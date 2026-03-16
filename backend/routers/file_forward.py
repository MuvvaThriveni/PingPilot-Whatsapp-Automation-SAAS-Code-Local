"""File Forwarding routes – single and bulk (Phase-6: hardened).

Fixes:
- File upload size limit (16MB)
- Contact deduplication before bulk send
- UTC timestamps
- Phone number normalization with country code support
"""

import io
import datetime
import asyncio
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from utils.time_utils import get_ist_now_iso
import pandas as pd

from store import get_settings
from services.whatsapp import WhatsAppService
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

    contacts = []
    seen_phones = set()  # Deduplication
    for idx, row in df.iterrows():
        raw = row[phone_col]
        # Handle numeric values (int, float, or scientific-notation strings like "9.1995E+11")
        try:
            numeric = float(raw)
            phone_str = f"{numeric:.0f}"
        except (ValueError, TypeError):
            phone_str = str(raw).strip()
            if phone_str.endswith('.0'):
                phone_str = phone_str[:-2]

        phone = "".join(filter(str.isdigit, phone_str))
        # Auto-add India country code for 10-digit mobile numbers
        if len(phone) == 10 and phone[0] in ('6', '7', '8', '9'):
            phone = '91' + phone

        if len(phone) >= 10 and phone not in seen_phones:
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
    """Send a file to multiple recipients from an Excel/CSV contact list."""
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

    sent_count = 0
    failed_count = 0

    async def send_to_contact(contact):
        nonlocal sent_count, failed_count
        phone = contact["phone"]
        now = _ist_now()

        if is_image:
            result = await whatsapp.send_image(phone, media_id, message)
        else:
            result = await whatsapp.send_document(phone, media_id, file_name, message)

        msg_type = "image" if is_image else "document"
        if result["success"]:
            sent_count += 1
            await _db_messages.add(tenant_id, {
                "direction": "outgoing", "product_type": "file_forward_bulk",
                "contact_phone": phone, "message_type": msg_type,
                "wa_message_id": result["messageId"],
                "media_id": media_id, "status": "sent", "created_at": now,
            })
            await _db_usage.record(tenant_id, "message_sent", "file_forward_bulk",
                                   contact_phone=phone)
        else:
            failed_count += 1
            await _db_messages.add(tenant_id, {
                "direction": "outgoing", "product_type": "file_forward_bulk",
                "contact_phone": phone, "message_type": msg_type,
                "media_id": media_id, "status": "failed",
                "error_message": result["error"], "created_at": now,
            })

    # Process in batches of 10
    batch_size = 10
    for i in range(0, len(contacts), batch_size):
        batch = contacts[i : i + batch_size]
        await asyncio.gather(*[send_to_contact(c) for c in batch])
        if i + batch_size < len(contacts):
            await asyncio.sleep(1)  # Rate limit delay

    log_event("file_forward_bulk_done", tenant_id=tenant_id,
              detail=f"sent={sent_count} failed={failed_count} total={len(contacts)}")

    return {
        "success": True,
        "message": f"File sent to {sent_count} recipients, {failed_count} failed",
        "sent_count": sent_count,
        "failed_count": failed_count,
        "total": len(contacts),
    }
