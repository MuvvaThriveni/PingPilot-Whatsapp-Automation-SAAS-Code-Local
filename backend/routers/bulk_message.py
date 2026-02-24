"""Bulk WhatsApp Messaging routes (SQLite-backed).

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

from store import get_settings, active_campaigns, add_message
from services.whatsapp import WhatsAppService
from services.template_builder import (
    get_components as _get_template_components,
    build_components as _build_template_components,
    ensure_cached as _ensure_template_cached,
    upload_header_media as _upload_header_media,
    _template_components,
)
from observability import log_event
from db_layer.campaigns import campaigns as _db_campaigns
from db_layer.campaign_recipients import campaign_recipients as _db_recipients
from db_layer.campaign_counters import campaign_counters as _db_counters

router = APIRouter(prefix="/api/bulk-message", tags=["bulk-message"])

# NOTE: _template_components cache lives in services.template_builder (shared with webhook.py).
# It is imported above for the legacy cache-key check in _process_campaign.

# Process-local campaign cache for status polling when Firestore is unavailable.
_recent_campaign_status: dict = {}
_RECENT_CAMPAIGN_TTL_SECONDS = 60 * 10

# Process-local lock for campaign workers
_worker_locks: set[str] = set()

# Process-local lock for scheduler
_scheduler_running: bool = False

# Process-local campaign store (Deprecated for Firestore)
_local_campaigns: dict[str, dict] = {}
_local_campaign_counters: dict[str, dict[str, int]] = {}

# Background scheduler task handle
_scheduler_task: asyncio.Task | None = None


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

        header_format = ""
        requires_header_media = False
        has_example_header_media = False

        # Count parameters in HEADER and BODY components
        param_count = 0
        for comp in components:
            comp_type = comp.get("type", "")
            if comp_type == "HEADER":
                fmt = comp.get("format", "TEXT")
                header_format = fmt
                if fmt == "TEXT":
                    params = re.findall(r"\{\{\d+\}\}", comp.get("text", ""))
                    param_count += len(params)
                elif fmt in ("IMAGE", "VIDEO", "DOCUMENT"):
                    param_count += 1
                    requires_header_media = True
                    example = comp.get("example", {}) or {}
                    handle = (example.get("header_handle") or [""])[0]
                    has_example_header_media = bool((handle or "").strip())
            elif comp_type == "BODY":
                params = re.findall(r"\{\{\d+\}\}", comp.get("text", ""))
                param_count += len(params)

        templates.append({
            "name": name,
            "language": lang,
            "status": status,
            "display": cache_key,
            "param_count": param_count,
            "header_format": header_format,
            "requires_header_media": requires_header_media,
            "has_example_header_media": has_example_header_media,
        })

    approved = [t for t in templates if t["status"] == "APPROVED"]

    return {"templates": approved}


# _get_template_components and _build_template_components are now imported from
# services.template_builder as aliases above.


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
        # Excel often stores phone numbers as floats (e.g. 9346775705.0)
        raw = row[phone_col]
        if isinstance(raw, float) and raw == int(raw):
            phone_str = str(int(raw))
        else:
            phone_str = str(raw).strip()
            if phone_str.endswith('.0'):
                phone_str = phone_str[:-2]

        phone = "".join(filter(str.isdigit, phone_str))
        # Auto-add India country code for exactly 10-digit mobile numbers
        if len(phone) == 10 and phone[0] in ('6', '7', '8', '9'):
            phone = '91' + phone

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
    scheduledAt: str = Form(None), # Format: ISO8601 or similar from frontend
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
    now = datetime.datetime.now().isoformat()
    
    status = "scheduled" if scheduledAt else "running"
    
    # 1. Create campaign in Firestore
    campaign_data = {
        "campaign_id": campaign_id,
        "tenant_id": tenant_id,
        "name": campaignName or f"Campaign {datetime.datetime.now().strftime('%Y-%m-%d')}",
        "template_name": templateName,
        "header_image_url": headerImageUrl.strip() if headerImageUrl else "",
        "total_contacts": len(contacts),
        "status": status,
        "delay_ms": delayMs,
        "created_at": now,
    }
    if scheduledAt:
        # Ensure scheduledAt is in ISO format
        campaign_data["scheduled_at"] = scheduledAt

    _db_campaigns.create(campaign_id, tenant_id, campaign_data)
    
    # 2. Save contacts to Firestore (campaign_recipients)
    _db_recipients.create_batch(campaign_id, tenant_id, contacts)
    
    # 3. Initialize counter shards
    _db_counters.init_shards(campaign_id)
    
    # 4. Handle immediate vs scheduled
    if not scheduledAt:
        # For backward compat with local stop signals
        active_campaigns[campaign_id] = {
            "running": True,
            "contacts": contacts,
            "template": templateName,
            "delay": delayMs,
            "header_image_url": headerImageUrl.strip() if headerImageUrl else "",
            "tenant_id": tenant_id,
        }
        asyncio.create_task(_process_campaign(campaign_id))
        log_event("campaign_start", tenant_id=tenant_id, campaign_id=campaign_id,
                 detail=f"contacts={len(contacts)} template={templateName}")
    else:
        log_event("campaign_scheduled", tenant_id=tenant_id, campaign_id=campaign_id,
                 detail=f"scheduled_at={scheduledAt}")

    return {"success": True, "campaignId": campaign_id, "totalContacts": len(contacts), "status": status}


BATCH_SIZE = 10  # send up to 10 messages concurrently per batch


async def _send_one(whatsapp: WhatsAppService, contact: dict, template_str: str, campaign_id: str, tenant_id: str, header_image_url: str = "", header_media_id: str = ""):
    """Send a single template message and record the result in Firestore."""
    phone = contact.get("phone") or contact.get("contact_phone")
    now = datetime.datetime.now().isoformat()
    try:
        # Build components
        components = _build_template_components(template_str, contact, header_media_id=header_media_id)
        result = await whatsapp.send_template_message(
            phone, template_str, components=components if components else None
        )

        if result["success"]:
            # Update Firestore recipient status
            _db_recipients.update_status(campaign_id, phone, "sent", wa_message_id=result["messageId"])
            # Update Firestore campaign counter
            _db_counters.increment(campaign_id, "sent")
            
            # Legacy log for UI chat history
            add_message(tenant_id, {
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
        else:
            err = result.get("error", "Unknown error")
            _db_recipients.update_status(campaign_id, phone, "failed", error_message=err)
            _db_counters.increment(campaign_id, "failed")
            
            log_event("send_fail", tenant_id=tenant_id, campaign_id=campaign_id,
                      phone=phone, detail=err, level="WARN")
    except Exception as exc:
        err = str(exc)
        _db_recipients.update_status(campaign_id, phone, "failed", error_message=err)
        _db_counters.increment(campaign_id, "failed")
        log_event("send_fail", tenant_id=tenant_id, campaign_id=campaign_id,
                  phone=phone, detail=err, level="WARN")


async def _process_campaign(campaign_id: str):
    """Process a bulk campaign from Firestore records."""
    if campaign_id in _worker_locks:
        print(f"[BULK] Campaign {campaign_id} is already being processed. Skipping duplicate task.")
        return
    
    _worker_locks.add(campaign_id)
    try:
        # 1. Fetch campaign record from Firestore
        campaign = _db_campaigns.get(campaign_id)
        if not campaign:
            print(f"[BULK] ABORT: Campaign {campaign_id} not found in Firestore")
            return

        tenant_id = campaign.get("tenant_id")
        template_str = campaign.get("template_name")
        delay_ms = campaign.get("delay_ms", 1000)
        header_image_url = campaign.get("header_image_url", "")
        
        # 2. Update status to running
        _db_campaigns.update_status(campaign_id, "running")
        _db_campaigns.update_heartbeat(campaign_id)

        # 3. Ensure stop signal is initialized
        active_campaigns[campaign_id] = {"running": True}
        
        settings = get_settings(tenant_id)
        if not settings.get("phone_number_id") or not settings.get("access_token"):
            print(f"[BULK] ABORT: WhatsApp not configured for {tenant_id}")
            _db_campaigns.update_status(campaign_id, "failed", error_message="WhatsApp not configured")
            active_campaigns.pop(campaign_id, None)
            return

        whatsapp = WhatsAppService(settings["phone_number_id"], settings["access_token"])

        print(f"[BULK] Starting campaign={campaign_id} template='{template_str}' phone_number_id='{settings.get('phone_number_id')}' token_set={bool(settings.get('access_token'))}")

        # Ensure template metadata is cached (fetch from WhatsApp API if missing)
        await _ensure_template_cached(template_str, whatsapp, settings)

        # ── Auto-upload template header media via shared builder ─────────────────────
        header_media_id = await _upload_header_media(template_str, whatsapp)

        try:
            # Loop over pending recipients in Firestore
            while True:
                # Check stop signal
                if not active_campaigns.get(campaign_id, {}).get("running"):
                    _db_campaigns.update_status(campaign_id, "stopped")
                    break
                    
                _db_campaigns.update_heartbeat(campaign_id)
                
                pending = _db_recipients.get_pending(campaign_id, limit=BATCH_SIZE)
                if not pending:
                    _db_campaigns.update_status(campaign_id, "completed")
                    break
                    
                # Process batch
                tasks = [
                    _send_one(whatsapp, contact, template_str, campaign_id, tenant_id, 
                              header_image_url=header_image_url, 
                              header_media_id=header_media_id)
                    for contact in pending
                ]
                await asyncio.gather(*tasks, return_exceptions=True)
                
                # Respect delay between batches
                await asyncio.sleep(delay_ms / 1000)

            log_event("campaign_complete", tenant_id=tenant_id, campaign_id=campaign_id)

        except Exception as exc:
            print(f"[BULK] FATAL ERROR in campaign={campaign_id}: {exc}")
            log_event("campaign_error", tenant_id=tenant_id, campaign_id=campaign_id, detail=str(exc), level="ERROR")
            _db_campaigns.update_status(campaign_id, "failed", error_message=str(exc))

    finally:
        active_campaigns.pop(campaign_id, None)
        _worker_locks.discard(campaign_id)


async def periodical_scheduler():
    """Check for due scheduled campaigns every 60 seconds."""
    global _scheduler_running
    if _scheduler_running:
        print("[BULK] Scheduler already running. Skipping duplicate task.")
        return
    
    _scheduler_running = True
    try:
        while True:
            try:
                due = _db_campaigns.get_due_scheduled()
                for campaign in due:
                    cid = campaign["campaign_id"]
                    print(f"[BULK] Starting due scheduled campaign: {cid}")
                    asyncio.create_task(_process_campaign(cid))
            except Exception as e:
                print(f"[BULK] Scheduler error: {e}")
            
            await asyncio.sleep(60)
    finally:
        _scheduler_running = False


@router.post("/stop/{campaign_id}")
async def stop_campaign(campaign_id: str):
    # Signal current worker to stop
    if campaign_id in active_campaigns:
        active_campaigns[campaign_id]["running"] = False
    _db_campaigns.update_status(campaign_id, "stopped")
    return {"success": True, "message": "Campaign stop requested"}


@router.get("/status/{campaign_id}")
async def get_campaign_status(request: Request, campaign_id: str):
    campaign = _db_campaigns.get(campaign_id)
    if not campaign:
        return JSONResponse(status_code=404, content={"error": "Campaign not found"})

    # Fetch real-time counters from Firestore
    counters = _db_counters.get(campaign_id)
    return {
        "campaign": {
            "campaign_id": campaign_id,
            "name": campaign.get("name", ""),
            "template_name": campaign.get("template_name", ""),
            "total_contacts": campaign.get("total_contacts", 0),
            "sent_count": counters.get("sent", 0),
            "failed_count": counters.get("failed", 0),
            "status": campaign.get("status", ""),
            "created_at": campaign.get("created_at", ""),
            "scheduled_at": campaign.get("scheduled_at"),
        }
    }


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(request: Request, campaign_id: str):
    # Stop if running
    if campaign_id in active_campaigns:
        active_campaigns[campaign_id]["running"] = False
    
    _db_campaigns.delete(campaign_id)
    _db_recipients.delete_by_campaign(campaign_id)
    _db_counters.delete(campaign_id)
    
    return {"success": True, "message": "Campaign deleted"}


@router.get("/campaigns")
async def get_all_campaigns(request: Request, limit: int = 25, cursor: str = None):
    tenant_id = request.state.tenant_id
    campaigns_list, next_cursor = _db_campaigns.list(tenant_id, limit=limit, cursor=cursor)
    
    result = []
    for c in campaigns_list:
        cid = c.get("campaign_id", "")
        counters = _db_counters.get(cid)
        result.append({
            "campaign_id": cid,
            "name": c.get("name", ""),
            "template_name": c.get("template_name", ""),
            "total_contacts": c.get("total_contacts", 0),
            "sent_count": counters.get("sent", 0),
            "failed_count": counters.get("failed", 0),
            "status": c.get("status", ""),
            "created_at": c.get("created_at", ""),
            "scheduled_at": c.get("scheduled_at"),
        })
    return {
        "campaigns": result,
        "next_cursor": next_cursor,
    }
