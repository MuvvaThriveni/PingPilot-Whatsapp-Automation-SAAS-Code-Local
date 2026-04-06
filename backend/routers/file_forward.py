"""File Forwarding routes – single and bulk (Phase-7: queue-unified).

Fixes:
- File upload size limit (16MB)
- Contact deduplication before bulk send
- UTC timestamps
- Phone number normalization with country code support
- Bulk sends routed through message_queue for unified rate limiting
"""

import io
import uuid
import logging
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from utils.time_utils import get_ist_now_iso
from utils.phone_utils import normalize_phone
import pandas as pd

from store import get_settings
from services.whatsapp import WhatsAppService
from services.queue_manager import enqueue_file_forward
from db_layer.messages import messages as _db_messages
from db_layer.usage_events import usage_events as _db_usage
from observability import log_event

router = APIRouter(prefix="/api/file-forward", tags=["file-forward"])

MAX_UPLOAD_SIZE_BYTES = 16 * 1024 * 1024  # 16MB


def _ist_now() -> str:
    return get_ist_now_iso()


def _find_column(df_columns, keywords):
    """Find a column whose name contains any of the given keywords (case-insensitive)."""
    for col in df_columns:
        for kw in keywords:
            if kw in col.lower():
                return col
    return None


def _parse_contacts_from_df(df):
    """Extract and normalize contacts from a DataFrame."""
    phone_col = _find_column(df.columns, ["phone", "mobile", "number"])
    if not phone_col:
        phone_col = df.columns[0]

    name_col = _find_column(df.columns, ["name"])

    _logger = logging.getLogger("file_forward")
    contacts = []
    seen_phones = set()  # Deduplication
    for idx, row in df.iterrows():
        raw = row[phone_col]
        phone = normalize_phone(raw)

        if phone is None:
            _logger.warning("Invalid phone number skipped: raw=%s", raw)
            continue

        if phone not in seen_phones:
            seen_phones.add(phone)
            contacts.append({
                "index": idx,
                "phone": phone,
                "name": str(row[name_col]).strip() if name_col and pd.notna(row.get(name_col)) else "",
            })
    return contacts


async def _read_upload_safe(file: UploadFile) -> bytes:
    """Read uploaded file with size limit enforcement."""
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError(
            f"File too large: {len(content) / (1024*1024):.1f}MB exceeds "
            f"limit of {MAX_UPLOAD_SIZE_BYTES / (1024*1024):.0f}MB"
        )
    return content


@router.post("/parse-contacts")
async def parse_contacts_file(contactsFile: UploadFile = File(...)):
    """Parse an Excel/CSV file to extract contacts for bulk file forwarding."""
    try:
        content = await _read_upload_safe(contactsFile)
    except ValueError as e:
        return JSONResponse(status_code=413, content={"error": str(e)})

    try:
        filename = contactsFile.filename or ""
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(content), dtype=str)
        contacts = _parse_contacts_from_df(df)
        return {"contacts": contacts, "total": len(contacts)}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to parse file: {str(e)}"})


@router.post("/send")
async def send_file(
    request: Request,
    file: UploadFile = File(...),
    recipient: str = Form(...),
    message: str = Form(""),
):
    tenant_id = request.state.tenant_id
    settings = await get_settings(tenant_id)
    if not settings["is_configured"]:
        return JSONResponse(
            status_code=400,
            content={"error": "WhatsApp not configured. Please configure in Settings."},
        )

    try:
        file_content = await _read_upload_safe(file)
    except ValueError as e:
        return JSONResponse(status_code=413, content={"error": str(e)})

    whatsapp = WhatsAppService(settings["phone_number_id"], settings["access_token"])
    now = _ist_now()

    # Step 1: Upload media to WhatsApp
    upload_result = await whatsapp.upload_media(file_content, file.content_type or "application/octet-stream")
    if not upload_result["success"]:
        await _db_messages.add(tenant_id, {
            "direction": "outgoing", "product_type": "file_forward",
            "contact_phone": recipient, "message_type": "document",
            "status": "failed", "error_message": upload_result["error"],
            "created_at": now,
        })
        return JSONResponse(status_code=400, content={"error": upload_result["error"]})

    # Step 2: Send as image or document based on content type
    content_type = file.content_type or ""
    if content_type.startswith("image/"):
        send_result = await whatsapp.send_image(recipient, upload_result["mediaId"], message)
    else:
        send_result = await whatsapp.send_document(
            recipient, upload_result["mediaId"], file.filename or "file", message
        )

    msg_type = "image" if content_type.startswith("image/") else "document"

    if send_result["success"]:
        await _db_messages.add(tenant_id, {
            "direction": "outgoing", "product_type": "file_forward",
            "contact_phone": recipient, "message_type": msg_type,
            "wa_message_id": send_result["messageId"],
            "media_id": upload_result["mediaId"],
            "status": "sent", "created_at": now,
        })
        await _db_usage.record(tenant_id, "message_sent", "file_forward",
                               contact_phone=recipient)
        return {
            "success": True,
            "message": "File sent successfully",
            "messageId": send_result["messageId"],
        }

    await _db_messages.add(tenant_id, {
        "direction": "outgoing", "product_type": "file_forward",
        "contact_phone": recipient, "message_type": msg_type,
        "media_id": upload_result["mediaId"],
        "status": "failed", "error_message": send_result["error"],
        "created_at": now,
    })
    return JSONResponse(status_code=400, content={"error": send_result["error"]})


@router.post("/send-bulk")
async def send_file_bulk(
    request: Request,
    file: UploadFile = File(...),
    contactsFile: UploadFile = File(...),
    message: str = Form(""),
):
    """Send a file to multiple recipients from an Excel/CSV contact list.

    Media is uploaded once, then individual send jobs are enqueued into
    the message_queue for unified rate limiting, retry, and observability.
    Returns immediately — delivery is asynchronous.
    """
    tenant_id = request.state.tenant_id
    settings = await get_settings(tenant_id)
    if not settings["is_configured"]:
        return JSONResponse(
            status_code=400,
            content={"error": "WhatsApp not configured. Please configure in Settings."},
        )

    # Read and validate both files
    try:
        file_content = await _read_upload_safe(file)
        contacts_content = await _read_upload_safe(contactsFile)
    except ValueError as e:
        return JSONResponse(status_code=413, content={"error": str(e)})

    try:
        filename = contactsFile.filename or ""
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contacts_content))
        else:
            df = pd.read_excel(io.BytesIO(contacts_content))
        contacts = _parse_contacts_from_df(df)  # Already deduplicated
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to parse contacts: {str(e)}"})

    if not contacts:
        return JSONResponse(status_code=400, content={"error": "No valid contacts found in file"})

    whatsapp = WhatsAppService(settings["phone_number_id"], settings["access_token"])
    content_type = file.content_type or ""
    file_name = file.filename or "file"

    # Upload media once (reuse for all recipients)
    upload_result = await whatsapp.upload_media(file_content, content_type or "application/octet-stream")
    if not upload_result["success"]:
        return JSONResponse(status_code=400, content={"error": f"Media upload failed: {upload_result['error']}"})

    media_id = upload_result["mediaId"]
    is_image = content_type.startswith("image/")
    media_type = "image" if is_image else "document"

    # Enqueue individual send jobs through the unified message_queue
    enqueued_count = 0
    batch_id = str(uuid.uuid4())[:8]
    for contact in contacts:
        phone = contact["phone"]
        job_id = f"ff_{batch_id}_{phone}"
        try:
            await enqueue_file_forward(
                job_id=job_id,
                tenant_id=tenant_id,
                phone_number=phone,
                media_id=media_id,
                media_type=media_type,
                filename=file_name,
                caption=message,
            )
            enqueued_count += 1
        except Exception as e:
            log_event("file_forward_enqueue_error", tenant_id=tenant_id,
                      phone=phone, detail=str(e), level="WARN")

    log_event("file_forward_bulk_queued", tenant_id=tenant_id,
              detail=f"queued={enqueued_count} total={len(contacts)}")

    return {
        "success": True,
        "message": f"Queued {enqueued_count} of {len(contacts)} recipients for delivery",
        "queued_count": enqueued_count,
        "total": len(contacts),
    }
