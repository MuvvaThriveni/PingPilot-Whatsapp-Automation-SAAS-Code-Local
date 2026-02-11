"""Bulk WhatsApp Messaging routes.

Mirrors the n8n "WhatsApp Image Marketing Automation" workflow:
  1. Read contacts from uploaded Excel/CSV (columns: Name, Phone Number, ImageURL)
  2. Normalize phone numbers to digit-only strings
  3. Send WhatsApp template messages (supports "template_name|language" format)
  4. Configurable delay between sends to avoid rate-limiting
"""

import io
import re
import uuid
import asyncio
import datetime
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import JSONResponse
import pandas as pd

from store import settings_store, message_logs, bulk_campaigns, active_campaigns, template_cache
from services.whatsapp import WhatsAppService

router = APIRouter(prefix="/api/bulk-message", tags=["bulk-message"])


@router.get("/templates")
async def get_templates():
    """Fetch available WhatsApp message templates from the Business Account."""
    if not settings_store["is_configured"]:
        return JSONResponse(status_code=400, content={"error": "WhatsApp not configured"})

    whatsapp = WhatsAppService(settings_store["phone_number_id"], settings_store["access_token"])
    result = await whatsapp.get_templates(settings_store["business_account_id"])

    if not result["success"]:
        return JSONResponse(status_code=400, content={"error": result["error"]})

    templates = []
    for t in result["templates"]:
        lang = t.get("language", "en_US")
        name = t.get("name", "")
        status = t.get("status", "")
        components = t.get("components", [])

        # Cache full component metadata for building params at send time
        cache_key = f"{name}|{lang}"
        template_cache[cache_key] = components

        # Count parameters in HEADER and BODY components
        param_count = 0
        for comp in components:
            comp_type = comp.get("type", "")
            if comp_type == "HEADER":
                fmt = comp.get("format", "TEXT")
                if fmt == "TEXT":
                    params = re.findall(r"\{\{\d+\}\}", comp.get("text", ""))
                    param_count += len(params)
                elif fmt in ("IMAGE", "VIDEO", "DOCUMENT"):
                    param_count += 1
            elif comp_type == "BODY":
                params = re.findall(r"\{\{\d+\}\}", comp.get("text", ""))
                param_count += len(params)

        templates.append({
            "name": name,
            "language": lang,
            "status": status,
            "display": cache_key,
            "param_count": param_count,
        })

    approved = [t for t in templates if t["status"] == "APPROVED"]
    return {"templates": approved}


def _build_template_components(template_key: str, contact: dict = None) -> list:
    """Auto-build the components array for a template based on cached metadata.

    Inspects the cached template components and fills in parameter placeholders
    with contact data (name, phone) or the template's own example values as
    fallback so the user doesn't have to specify them manually.
    """
    cached = template_cache.get(template_key, [])
    if not cached:
        return []

    components = []
    for comp in cached:
        comp_type = comp.get("type", "")
        example = comp.get("example", {})

        # HEADER component
        if comp_type == "HEADER":
            header_format = comp.get("format", "TEXT")
            if header_format == "TEXT":
                text = comp.get("text", "")
                params = re.findall(r"\{\{\d+\}\}", text)
                if params:
                    example_texts = example.get("header_text", [])
                    parameters = []
                    for i, _ in enumerate(params):
                        if i == 0 and contact and contact.get("name"):
                            parameters.append({"type": "text", "text": contact["name"]})
                        elif i < len(example_texts):
                            parameters.append({"type": "text", "text": example_texts[i]})
                        else:
                            parameters.append({"type": "text", "text": " "})
                    components.append({
                        "type": "header",
                        "parameters": parameters,
                    })
            elif header_format == "IMAGE":
                image_link = (contact or {}).get("imageUrl", "") or (example.get("header_handle") or [""])[0]
                if image_link:
                    components.append({
                        "type": "header",
                        "parameters": [{"type": "image", "image": {"link": image_link}}],
                    })
            elif header_format == "VIDEO":
                video_link = (contact or {}).get("videoUrl", "") or (example.get("header_handle") or [""])[0]
                if video_link:
                    components.append({
                        "type": "header",
                        "parameters": [{"type": "video", "video": {"link": video_link}}],
                    })
            elif header_format == "DOCUMENT":
                doc_link = (contact or {}).get("documentUrl", "") or (example.get("header_handle") or [""])[0]
                if doc_link:
                    components.append({
                        "type": "header",
                        "parameters": [{"type": "document", "document": {"link": doc_link}}],
                    })

        # BODY component
        elif comp_type == "BODY":
            text = comp.get("text", "")
            params = re.findall(r"\{\{\d+\}\}", text)
            if params:
                example_texts = example.get("body_text", [[]])[0] if example.get("body_text") else []
                parameters = []
                for i, _ in enumerate(params):
                    if i == 0 and contact and contact.get("name"):
                        parameters.append({"type": "text", "text": contact["name"]})
                    elif i == 1 and contact and contact.get("phone"):
                        parameters.append({"type": "text", "text": contact["phone"]})
                    elif i < len(example_texts):
                        parameters.append({"type": "text", "text": example_texts[i]})
                    else:
                        parameters.append({"type": "text", "text": " "})
                components.append({
                    "type": "body",
                    "parameters": parameters,
                })

    return components


def _find_column(df_columns, keywords):
    """Find a column whose name contains any of the given keywords (case-insensitive)."""
    for col in df_columns:
        for kw in keywords:
            if kw in col.lower():
                return col
    return None


def _parse_contacts(df):
    """Extract and normalize contacts from a DataFrame (matches n8n AI Transform step)."""
    phone_col = _find_column(df.columns, ["phone", "mobile", "number"])
    if not phone_col:
        phone_col = df.columns[0]

    name_col = _find_column(df.columns, ["name"])
    image_col = _find_column(df.columns, ["image", "url"])

    contacts = []
    for idx, row in df.iterrows():
        # n8n AI Transform: convert phone number to string, strip non-digits
        phone = str(row[phone_col]).strip()
        phone = "".join(filter(str.isdigit, phone))
        if len(phone) >= 10:
            contacts.append({
                "index": idx,
                "phone": phone,
                "name": str(row[name_col]).strip() if name_col and pd.notna(row.get(name_col)) else "",
                "imageUrl": str(row[image_col]).strip() if image_col and pd.notna(row.get(image_col)) else "",
            })
    return contacts


def _read_spreadsheet(content: bytes, filename: str):
    """Read an Excel or CSV file into a DataFrame."""
    if filename and filename.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(content))
    return pd.read_excel(io.BytesIO(content))


@router.post("/parse")
async def parse_contacts_file(file: UploadFile = File(...)):
    content = await file.read()
    try:
        df = _read_spreadsheet(content, file.filename or "")
        contacts = _parse_contacts(df)
        return {"contacts": contacts, "total": len(contacts), "validContacts": len(contacts)}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to parse file: {str(e)}"})


@router.post("/start")
async def start_bulk_campaign(
    file: UploadFile = File(...),
    templateName: str = Form(...),
    campaignName: str = Form(""),
    delayMs: int = Form(1000),
    headerImageUrl: str = Form(""),
):
    if not settings_store["is_configured"]:
        return JSONResponse(status_code=400, content={"error": "WhatsApp not configured. Please configure in Settings."})

    content = await file.read()
    try:
        df = _read_spreadsheet(content, file.filename or "")
        contacts = _parse_contacts(df)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to parse file: {str(e)}"})

    if not contacts:
        return JSONResponse(status_code=400, content={"error": "No valid contacts found"})

    campaign_id = str(uuid.uuid4())
    campaign = {
        "campaign_id": campaign_id,
        "name": campaignName or f"Campaign {datetime.datetime.now().strftime('%Y-%m-%d')}",
        "template_name": templateName,
        "total_contacts": len(contacts),
        "sent_count": 0,
        "failed_count": 0,
        "status": "running",
        "created_at": datetime.datetime.now().isoformat(),
    }
    bulk_campaigns.append(campaign)
    active_campaigns[campaign_id] = {
        "running": True,
        "contacts": contacts,
        "template": templateName,
        "delay": delayMs,
        "header_image_url": headerImageUrl.strip() if headerImageUrl else "",
    }

    asyncio.create_task(_process_campaign(campaign_id))
    return {"success": True, "campaignId": campaign_id, "totalContacts": len(contacts)}


BATCH_SIZE = 10  # send up to 10 messages concurrently per batch


async def _send_one(whatsapp: WhatsAppService, contact: dict, template_str: str, campaign_id: str, campaign: dict, header_image_url: str = ""):
    """Send a single template message and record the result."""
    # If user provided a header image URL, inject it into contact data
    if header_image_url and not contact.get("imageUrl"):
        contact = {**contact, "imageUrl": header_image_url}
    components = _build_template_components(template_str, contact)
    # Log first message of each campaign for debugging
    if campaign["sent_count"] == 0 and campaign["failed_count"] == 0:
        print(f"[DEBUG] Template key: '{template_str}'")
        print(f"[DEBUG] Cache keys: {list(template_cache.keys())}")
        print(f"[DEBUG] Cached components: {template_cache.get(template_str, 'NOT FOUND')}")
        print(f"[DEBUG] Built components: {components}")
        print(f"[DEBUG] Contact: {contact}")
    result = await whatsapp.send_template_message(
        contact["phone"], template_str, components=components if components else None
    )
    if not result["success"] and campaign["failed_count"] == 0:
        print(f"[DEBUG] First failure error: {result['error']}")
    phone = contact["phone"]
    now = datetime.datetime.now().isoformat()

    if result["success"]:
        campaign["sent_count"] += 1
        message_logs.append({
            "product_type": "bulk_message",
            "recipient": phone,
            "message_id": result["messageId"],
            "template_name": template_str,
            "status": "sent",
            "campaign_id": campaign_id,
            "created_at": now,
        })
    else:
        campaign["failed_count"] += 1
        message_logs.append({
            "product_type": "bulk_message",
            "recipient": phone,
            "template_name": template_str,
            "status": "failed",
            "error_message": result["error"],
            "campaign_id": campaign_id,
            "created_at": now,
        })


async def _process_campaign(campaign_id: str):
    """Process a bulk campaign – sends messages in concurrent batches."""
    state = active_campaigns.get(campaign_id)
    campaign = next((c for c in bulk_campaigns if c["campaign_id"] == campaign_id), None)
    if not state or not campaign:
        return

    whatsapp = WhatsAppService(settings_store["phone_number_id"], settings_store["access_token"])
    template_str = state["template"]
    contacts = state["contacts"]

    # Ensure template metadata is cached (fetch if missing, e.g. after server restart)
    if template_str not in template_cache and settings_store.get("business_account_id"):
        result = await whatsapp.get_templates(settings_store["business_account_id"])
        if result["success"]:
            for t in result["templates"]:
                key = f"{t['name']}|{t['language']}"
                template_cache[key] = t.get("components", [])

    # Process contacts in batches of BATCH_SIZE concurrently
    for i in range(0, len(contacts), BATCH_SIZE):
        if not active_campaigns.get(campaign_id, {}).get("running"):
            campaign["status"] = "stopped"
            break

        batch = contacts[i : i + BATCH_SIZE]
        header_image_url = state.get("header_image_url", "")
        tasks = [
            _send_one(whatsapp, contact, template_str, campaign_id, campaign, header_image_url)
            for contact in batch
        ]
        await asyncio.gather(*tasks)

        # Small delay between batches to respect rate limits
        if i + BATCH_SIZE < len(contacts):
            await asyncio.sleep(state["delay"] / 1000)

    if active_campaigns.get(campaign_id, {}).get("running"):
        campaign["status"] = "completed"
    active_campaigns.pop(campaign_id, None)


@router.post("/stop/{campaign_id}")
async def stop_campaign(campaign_id: str):
    if campaign_id in active_campaigns:
        active_campaigns[campaign_id]["running"] = False
        return {"success": True, "message": "Campaign stop requested"}
    return JSONResponse(status_code=404, content={"error": "Campaign not found"})


@router.get("/status/{campaign_id}")
async def get_campaign_status(campaign_id: str):
    campaign = next((c for c in bulk_campaigns if c["campaign_id"] == campaign_id), None)
    if not campaign:
        return JSONResponse(status_code=404, content={"error": "Campaign not found"})
    return {"campaign": campaign}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str):
    idx = next((i for i, c in enumerate(bulk_campaigns) if c["campaign_id"] == campaign_id), None)
    if idx is None:
        return JSONResponse(status_code=404, content={"error": "Campaign not found"})
    if campaign_id in active_campaigns:
        active_campaigns[campaign_id]["running"] = False
        active_campaigns.pop(campaign_id, None)
    bulk_campaigns.pop(idx)
    return {"success": True, "message": "Campaign deleted"}


@router.get("/campaigns")
async def get_all_campaigns():
    return {
        "campaigns": sorted(
            bulk_campaigns, key=lambda x: x.get("created_at", ""), reverse=True
        )[:50]
    }
