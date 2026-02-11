"""File Forwarding routes – single and bulk."""

import io
import datetime
import asyncio
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import JSONResponse
import pandas as pd

from store import settings_store, message_logs
from services.whatsapp import WhatsAppService

router = APIRouter(prefix="/api/file-forward", tags=["file-forward"])


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
    for idx, row in df.iterrows():
        phone = str(row[phone_col]).strip()
        phone = "".join(filter(str.isdigit, phone))
        if len(phone) >= 10:
            contacts.append({
                "index": idx,
                "phone": phone,
                "name": str(row[name_col]).strip() if name_col and pd.notna(row.get(name_col)) else "",
            })
    return contacts


@router.post("/parse-contacts")
async def parse_contacts_file(contactsFile: UploadFile = File(...)):
    """Parse an Excel/CSV file to extract contacts for bulk file forwarding."""
    content = await contactsFile.read()
    try:
        filename = contactsFile.filename or ""
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        else:
            df = pd.read_excel(io.BytesIO(content))
        contacts = _parse_contacts_from_df(df)
        return {"contacts": contacts, "total": len(contacts)}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to parse file: {str(e)}"})


@router.post("/send")
async def send_file(
    file: UploadFile = File(...),
    recipient: str = Form(...),
    message: str = Form(""),
):
    if not settings_store["is_configured"]:
        return JSONResponse(
            status_code=400,
            content={"error": "WhatsApp not configured. Please configure in Settings."},
        )

    whatsapp = WhatsAppService(settings_store["phone_number_id"], settings_store["access_token"])
    file_content = await file.read()
    now = datetime.datetime.now().isoformat()

    # Step 1: Upload media to WhatsApp
    upload_result = await whatsapp.upload_media(file_content, file.content_type or "application/octet-stream")
    if not upload_result["success"]:
        message_logs.append({
            "product_type": "file_forward",
            "recipient": recipient,
            "status": "failed",
            "error_message": upload_result["error"],
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

    if send_result["success"]:
        message_logs.append({
            "product_type": "file_forward",
            "recipient": recipient,
            "message_id": send_result["messageId"],
            "status": "sent",
            "created_at": now,
        })
        return {
            "success": True,
            "message": "File sent successfully",
            "messageId": send_result["messageId"],
        }

    message_logs.append({
        "product_type": "file_forward",
        "recipient": recipient,
        "status": "failed",
        "error_message": send_result["error"],
        "created_at": now,
    })
    return JSONResponse(status_code=400, content={"error": send_result["error"]})


@router.post("/send-bulk")
async def send_file_bulk(
    file: UploadFile = File(...),
    contactsFile: UploadFile = File(...),
    message: str = Form(""),
):
    """Send a file to multiple recipients from an Excel/CSV contact list."""
    if not settings_store["is_configured"]:
        return JSONResponse(
            status_code=400,
            content={"error": "WhatsApp not configured. Please configure in Settings."},
        )

    # Parse contacts from the uploaded spreadsheet
    contacts_content = await contactsFile.read()
    try:
        filename = contactsFile.filename or ""
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contacts_content))
        else:
            df = pd.read_excel(io.BytesIO(contacts_content))
        contacts = _parse_contacts_from_df(df)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to parse contacts: {str(e)}"})

    if not contacts:
        return JSONResponse(status_code=400, content={"error": "No valid contacts found in file"})

    whatsapp = WhatsAppService(settings_store["phone_number_id"], settings_store["access_token"])
    file_content = await file.read()
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

    # Send to all contacts concurrently in batches
    async def send_to_contact(contact):
        nonlocal sent_count, failed_count
        phone = contact["phone"]
        now = datetime.datetime.now().isoformat()

        if is_image:
            result = await whatsapp.send_image(phone, media_id, message)
        else:
            result = await whatsapp.send_document(phone, media_id, file_name, message)

        if result["success"]:
            sent_count += 1
            message_logs.append({
                "product_type": "file_forward_bulk",
                "recipient": phone,
                "message_id": result["messageId"],
                "status": "sent",
                "created_at": now,
            })
        else:
            failed_count += 1
            message_logs.append({
                "product_type": "file_forward_bulk",
                "recipient": phone,
                "status": "failed",
                "error_message": result["error"],
                "created_at": now,
            })

    # Process in batches of 10
    batch_size = 10
    for i in range(0, len(contacts), batch_size):
        batch = contacts[i : i + batch_size]
        await asyncio.gather(*[send_to_contact(c) for c in batch])
        if i + batch_size < len(contacts):
            await asyncio.sleep(1)  # Rate limit delay

    return {
        "success": True,
        "message": f"File sent to {sent_count} recipients, {failed_count} failed",
        "sent_count": sent_count,
        "failed_count": failed_count,
        "total": len(contacts),
    }
