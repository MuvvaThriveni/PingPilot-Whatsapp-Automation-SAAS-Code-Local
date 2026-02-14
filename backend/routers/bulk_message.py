"""Bulk WhatsApp Messaging routes (Firestore-first, Phase-2).

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
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
import pandas as pd

from store import get_settings, active_campaigns
from services.whatsapp import WhatsAppService
from observability import log_event
from db_layer.campaigns import campaigns as _db_campaigns
from db_layer.campaign_recipients import campaign_recipients as _db_recipients
from db_layer.campaign_counters import campaign_counters as _db_counters
from db_layer.messages import messages as _db_messages
from db_layer.template_cache import template_cache_db as _db_template_cache
from db_layer.usage_events import usage_events as _db_usage

router = APIRouter(prefix="/api/bulk-message", tags=["bulk-message"])

# Process-local template component cache (rebuilt from Firestore on miss)
_template_components: dict = {}


@router.get("/templates")
async def get_templates(request: Request):
    """Fetch available WhatsApp message templates from the Business Account."""
    tenant_id = request.state.tenant_id
    settings = get_settings(tenant_id)
    if not settings["is_configured"]:
        return JSONResponse(status_code=400, content={"error": "WhatsApp not configured"})

    whatsapp = WhatsAppService(settings["phone_number_id"], settings["access_token"])
    result = await whatsapp.get_templates(settings["business_account_id"])

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
        _template_components[cache_key] = components

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

    # Persist template metadata to Firestore
    try:
        _db_template_cache.upsert_batch(tenant_id, [
            {"name": t["name"], "language": t["language"], "status": t["status"],
             "components": _template_components.get(f"{t['name']}|{t['language']}", []),
             "param_count": t["param_count"]}
            for t in approved
        ])
    except Exception as e:
        print(f"[WARN] db_layer template_cache upsert_batch failed: {e}")

    return {"templates": approved}


def _get_template_components(template_key: str, tenant_id: str = "") -> list:
    """Get cached template components — process-local first, then Firestore."""
    if template_key in _template_components:
        return _template_components[template_key]
    # Firestore fallback
    if not tenant_id:
        return []
    comps = _db_template_cache.get_components(tenant_id, template_key)
    if comps:
        _template_components[template_key] = comps
        print(f"[BULK] Template cache rebuilt from Firestore for '{template_key}'")
    return comps


def _build_template_components(template_key: str, contact: dict = None) -> list:
    """Auto-build the components array for a template based on cached metadata.

    Inspects the cached template components and fills in parameter placeholders
    with contact data (name, phone) or the template's own example values as
    fallback so the user doesn't have to specify them manually.
    """
    cached = _get_template_components(template_key, contact.get("_tenant_id", "") if contact else "")
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
    request: Request,
    file: UploadFile = File(...),
    templateName: str = Form(...),
    campaignName: str = Form(""),
    delayMs: int = Form(1000),
    headerImageUrl: str = Form(""),
):
    tenant_id = request.state.tenant_id
    settings = get_settings(tenant_id)
    if not settings["is_configured"]:
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

    # Create campaign in Firestore (single source of truth)
    _db_campaigns.create(campaign_id, tenant_id, {
        "name": campaignName or f"Campaign {datetime.datetime.now().strftime('%Y-%m-%d')}",
        "campaign_type": "bulk_template",
        "template_name": templateName,
        "header_image_url": headerImageUrl.strip() if headerImageUrl else "",
        "total_contacts": len(contacts),
        "status": "running",
        "delay_ms": delayMs,
        "batch_size": BATCH_SIZE,
        "last_processed_index": 0,
    })
    _db_counters.init_shards(campaign_id)
    _db_campaigns.update_heartbeat(campaign_id)
    _db_recipients.create_batch(campaign_id, tenant_id, contacts)

    # Ephemeral process-local state for stop signaling + in-flight data
    active_campaigns[campaign_id] = {
        "running": True,
        "contacts": contacts,
        "template": templateName,
        "delay": delayMs,
        "header_image_url": headerImageUrl.strip() if headerImageUrl else "",
        "tenant_id": tenant_id,
    }

    log_event("campaign_start", tenant_id=tenant_id, campaign_id=campaign_id,
             detail=f"contacts={len(contacts)} template={templateName}")
    asyncio.create_task(_process_campaign(campaign_id))
    return {"success": True, "campaignId": campaign_id, "totalContacts": len(contacts)}


BATCH_SIZE = 10  # send up to 10 messages concurrently per batch


async def _send_one(whatsapp: WhatsAppService, contact: dict, template_str: str, campaign_id: str, tenant_id: str, header_image_url: str = ""):
    """Send a single template message and record the result (Firestore-only)."""
    if header_image_url and not contact.get("imageUrl"):
        contact = {**contact, "imageUrl": header_image_url}
    components = _build_template_components(template_str, contact)
    result = await whatsapp.send_template_message(
        contact["phone"], template_str, components=components if components else None
    )
    phone = contact["phone"]
    now = datetime.datetime.now().isoformat()

    if result["success"]:
        _db_messages.add(tenant_id, {
            "direction": "outgoing",
            "product_type": "bulk_message",
            "contact_phone": phone,
            "message_type": "template",
            "wa_message_id": result["messageId"],
            "campaign_id": campaign_id,
            "status": "sent",
            "template_name": template_str,
            "created_at": now,
        })
        _db_recipients.update_status(campaign_id, phone, "sent",
                                     wa_message_id=result["messageId"])
        _db_counters.increment_sent(campaign_id)
        _db_usage.record(tenant_id, "message_sent", "bulk_message",
                         campaign_id=campaign_id, contact_phone=phone)
    else:
        log_event("send_fail", tenant_id=tenant_id, campaign_id=campaign_id,
                  phone=phone, detail=result["error"], level="WARN")
        _db_messages.add(tenant_id, {
            "direction": "outgoing",
            "product_type": "bulk_message",
            "contact_phone": phone,
            "message_type": "template",
            "campaign_id": campaign_id,
            "status": "failed",
            "error_message": result["error"],
            "template_name": template_str,
            "created_at": now,
        })
        _db_recipients.update_status(campaign_id, phone, "failed",
                                     error_message=result["error"])
        _db_counters.increment_failed(campaign_id)


async def _process_campaign(campaign_id: str):
    """Process a bulk campaign – sends messages in concurrent batches (Firestore-first)."""
    state = active_campaigns.get(campaign_id)
    if not state:
        print(f"[BULK] Campaign {campaign_id} not found in active_campaigns, checking Firestore for resume")
        campaign_doc = _db_campaigns.get(campaign_id)
        if not campaign_doc or campaign_doc.get("status") != "running":
            return
        # Resume support: rebuild state from Firestore
        pending = _db_recipients.get_pending(campaign_id, limit=10000)
        state = {
            "running": True,
            "contacts": [{"phone": r["contact_phone"], "name": r.get("contact_name", ""),
                          "index": r.get("recipient_index", 0)} for r in pending],
            "template": campaign_doc.get("template_name", ""),
            "delay": campaign_doc.get("delay_ms", 1000),
            "header_image_url": campaign_doc.get("header_image_url", ""),
            "tenant_id": campaign_doc.get("tenant_id", ""),
        }
        active_campaigns[campaign_id] = state
        log_event("campaign_resume", tenant_id=state.get('tenant_id', ''),
                 campaign_id=campaign_id, detail=f"pending={len(state['contacts'])}")

    tenant_id = state.get("tenant_id", "")
    settings = get_settings(tenant_id)
    whatsapp = WhatsAppService(settings["phone_number_id"], settings["access_token"])
    template_str = state["template"]
    contacts = state["contacts"]

    # Ensure template metadata is cached (fetch from Firestore or API if missing)
    if not _get_template_components(template_str, tenant_id) and settings.get("business_account_id"):
        result = await whatsapp.get_templates(settings["business_account_id"])
        if result["success"]:
            for t in result["templates"]:
                key = f"{t['name']}|{t['language']}"
                _template_components[key] = t.get("components", [])

    # Process contacts in batches of BATCH_SIZE concurrently
    for i in range(0, len(contacts), BATCH_SIZE):
        if not active_campaigns.get(campaign_id, {}).get("running"):
            _db_campaigns.update_status(campaign_id, "stopped")
            log_event("campaign_stopped", tenant_id=tenant_id, campaign_id=campaign_id)
            break

        batch = contacts[i : i + BATCH_SIZE]
        header_image_url = state.get("header_image_url", "")
        tasks = [
            _send_one(whatsapp, contact, template_str, campaign_id, tenant_id, header_image_url)
            for contact in batch
        ]
        await asyncio.gather(*tasks)

        # Track resume point and heartbeat in Firestore
        _db_campaigns.update_last_processed(campaign_id, i + len(batch))
        _db_campaigns.update_heartbeat(campaign_id)

        # Small delay between batches to respect rate limits
        if i + BATCH_SIZE < len(contacts):
            await asyncio.sleep(state["delay"] / 1000)

    if active_campaigns.get(campaign_id, {}).get("running"):
        _db_campaigns.update_status(campaign_id, "completed",
                                    completed_at=datetime.datetime.now().isoformat())
        log_event("campaign_complete", tenant_id=tenant_id, campaign_id=campaign_id)
    active_campaigns.pop(campaign_id, None)


@router.post("/stop/{campaign_id}")
async def stop_campaign(campaign_id: str):
    # Signal in-flight task to stop
    if campaign_id in active_campaigns:
        active_campaigns[campaign_id]["running"] = False
    # Always update Firestore status (works even if campaign is on another worker)
    _db_campaigns.update_status(campaign_id, "stopped")
    return {"success": True, "message": "Campaign stop requested"}


@router.get("/status/{campaign_id}")
async def get_campaign_status(request: Request, campaign_id: str):
    campaign = _db_campaigns.get(campaign_id)
    if not campaign:
        return JSONResponse(status_code=404, content={"error": "Campaign not found"})
    # Enrich with distributed counter totals
    totals = _db_counters.get_totals(campaign_id)
    campaign["sent_count"] = totals.get("sent", 0)
    campaign["failed_count"] = totals.get("failed", 0)
    # Remap to legacy API format
    result = {
        "campaign_id": campaign.get("campaign_id", campaign_id),
        "name": campaign.get("name", ""),
        "template_name": campaign.get("template_name", ""),
        "total_contacts": campaign.get("total_contacts", 0),
        "sent_count": campaign["sent_count"],
        "failed_count": campaign["failed_count"],
        "status": campaign.get("status", ""),
        "created_at": campaign.get("created_at", ""),
    }
    return {"campaign": result}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(request: Request, campaign_id: str):
    campaign = _db_campaigns.get(campaign_id)
    if not campaign:
        return JSONResponse(status_code=404, content={"error": "Campaign not found"})
    # Stop in-flight task if running locally
    if campaign_id in active_campaigns:
        active_campaigns[campaign_id]["running"] = False
        active_campaigns.pop(campaign_id, None)
    # Firestore cleanup
    _db_campaigns.delete(campaign_id)
    _db_counters.delete(campaign_id)
    _db_recipients.delete_by_campaign(campaign_id)
    return {"success": True, "message": "Campaign deleted"}


@router.get("/campaigns")
async def get_all_campaigns(request: Request, limit: int = 25, cursor: str = None):
    tenant_id = request.state.tenant_id
    campaigns, next_cursor = _db_campaigns.list(tenant_id, limit=limit, cursor=cursor)
    # Enrich each campaign with counter totals and remap to legacy format
    result = []
    for c in campaigns:
        cid = c.get("campaign_id", "")
        totals = _db_counters.get_totals(cid) if cid else {"sent": 0, "failed": 0}
        result.append({
            "campaign_id": cid,
            "name": c.get("name", ""),
            "template_name": c.get("template_name", ""),
            "total_contacts": c.get("total_contacts", 0),
            "sent_count": totals.get("sent", 0),
            "failed_count": totals.get("failed", 0),
            "status": c.get("status", ""),
            "created_at": c.get("created_at", ""),
        })
    return {
        "campaigns": result,
        "next_cursor": next_cursor,
    }
