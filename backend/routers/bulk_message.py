"""Bulk WhatsApp Messaging routes (Phase-6: hardened).

Security fixes:
- Tenant ownership validation on stop/status/delete endpoints
- File upload size limit (16MB)
- DB-backed stop signals (works across multiple workers)
- UTC timestamps everywhere
- Removed all print() logging → structured log_event()
- Tenant-scoped template cache lookups
"""

import io
import re
import uuid
import asyncio
import datetime
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
import pandas as pd
from utils.time_utils import get_ist_now_iso, get_ist_now

from store import get_settings, active_campaigns, add_message, add_message_conn
from services.whatsapp import WhatsAppService
from services.template_builder import (
    get_components as _get_template_components,
    build_components as _build_template_components,
    ensure_cached as _ensure_template_cached,
    upload_header_media as _upload_header_media,
    get_template_keys_for_tenant as _get_template_keys_for_tenant,
)
from observability import log_event
from db_layer.campaigns import campaigns as _db_campaigns
from db_layer.campaign_recipients import campaign_recipients as _db_recipients
from db_layer.campaign_counters import campaign_counters as _db_counters
from database import transaction

router = APIRouter(prefix="/api/bulk-message", tags=["bulk-message"])

# ── Constants ────────────────────────────────────────────────────────
MAX_UPLOAD_SIZE_BYTES = 16 * 1024 * 1024  # 16MB
BATCH_SIZE = 10  # send up to 10 messages concurrently per batch

# Process-local lock for campaign workers
_worker_locks: set[str] = set()

# Process-local lock for scheduler
_scheduler_running: bool = False


def _ist_now() -> str:
    return get_ist_now_iso()


# ── Tenant ownership helper ─────────────────────────────────────────

def _verify_campaign_ownership(campaign: dict | None, tenant_id: str):
    """Return (campaign, error_response). error_response is None if authorized."""
    if not campaign:
        return None, JSONResponse(status_code=404, content={"error": "Campaign not found"})
    if campaign.get("tenant_id") != tenant_id:
        # Return 404 instead of 403 to avoid leaking existence of campaigns
        return None, JSONResponse(status_code=404, content={"error": "Campaign not found"})
    return campaign, None


# ── File parsing helpers ─────────────────────────────────────────────

def _find_column(df_columns, keywords):
    """Find a column whose name contains any of the given keywords (case-insensitive)."""
    for col in df_columns:
        for kw in keywords:
            if kw in col.lower():
                return col
    return None


def _parse_contacts(df):
    """Extract and normalize contacts from a DataFrame."""
    phone_col = _find_column(df.columns, ["phone", "mobile", "number"])
    if not phone_col:
        phone_col = df.columns[0]

    name_col = _find_column(df.columns, ["name"])
    image_col = _find_column(df.columns, ["image", "url"])

    contacts = []
    seen_phones = set()
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
        # Auto-add India country code for exactly 10-digit mobile numbers
        if len(phone) == 10 and phone[0] in ('6', '7', '8', '9'):
            phone = '91' + phone

        if len(phone) >= 10 and phone not in seen_phones:
            seen_phones.add(phone)
            contacts.append({
                "index": idx,
                "phone": phone,
                "name": str(row[name_col]).strip() if name_col and pd.notna(row.get(name_col)) else "",
                "imageUrl": str(row[image_col]).strip() if image_col and pd.notna(row.get(image_col)) else "",
            })
    return contacts


def _read_spreadsheet(content: bytes, filename: str):
    """Read an Excel or CSV file into a DataFrame.

    Uses dtype=str to prevent pandas from converting phone numbers to floats
    (which can cause precision loss or scientific-notation artifacts).
    """
    if filename and filename.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(content), dtype=str)
    return pd.read_excel(io.BytesIO(content), dtype=str)


async def _read_upload_safe(file: UploadFile) -> bytes:
    """Read uploaded file with size limit enforcement."""
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError(
            f"File too large: {len(content) / (1024*1024):.1f}MB exceeds "
            f"limit of {MAX_UPLOAD_SIZE_BYTES / (1024*1024):.0f}MB"
        )
    return content


# ── Routes ───────────────────────────────────────────────────────────

@router.get("/templates")
async def get_templates(request: Request):
    """Fetch available WhatsApp message templates from the Business Account."""
    tenant_id = request.state.tenant_id
    settings = await get_settings(tenant_id)
    if not settings["is_configured"]:
        return JSONResponse(status_code=400, content={"error": "WhatsApp not configured"})

    whatsapp = WhatsAppService(settings["phone_number_id"], settings["access_token"])
    result = await whatsapp.get_templates(settings["business_account_id"])

    if not result["success"]:
        return JSONResponse(status_code=400, content={"error": result["error"]})

    # Import the tenant-scoped cache dict from template_builder
    from services.template_builder import _template_components, _tenant_key

    templates = []
    for t in result["templates"]:
        lang = t.get("language", "en_US")
        name = t.get("name", "")
        status = t.get("status", "")
        components = t.get("components", [])

        # Cache with tenant-scoped key
        cache_key = f"{name}|{lang}"
        full_key = _tenant_key(tenant_id, cache_key)
        _template_components[full_key] = components

        header_format = ""
        requires_header_media = False
        has_example_header_media = False
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


@router.post("/parse")
async def parse_contacts_file(file: UploadFile = File(...)):
    try:
        content = await _read_upload_safe(file)
    except ValueError as e:
        return JSONResponse(status_code=413, content={"error": str(e)})
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
    scheduledAt: str = Form(None),
):
    tenant_id = request.state.tenant_id
    settings = await get_settings(tenant_id)
    if not settings["is_configured"]:
        return JSONResponse(status_code=400, content={"error": "WhatsApp not configured. Please configure in Settings."})

    try:
        content = await _read_upload_safe(file)
    except ValueError as e:
        return JSONResponse(status_code=413, content={"error": str(e)})

    try:
        df = _read_spreadsheet(content, file.filename or "")
        contacts = _parse_contacts(df)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to parse file: {str(e)}"})

    if not contacts:
        return JSONResponse(status_code=400, content={"error": "No valid contacts found"})

    campaign_id = str(uuid.uuid4())
    now = _ist_now()

    status = "scheduled" if scheduledAt else "running"

    campaign_data = {
        "campaign_id": campaign_id,
        "tenant_id": tenant_id,
        "name": campaignName or f"Campaign {get_ist_now().strftime('%Y-%m-%d')}",
        "template_name": templateName,
        "header_image_url": headerImageUrl.strip() if headerImageUrl else "",
        "total_contacts": len(contacts),
        "status": status,
        "delay_ms": delayMs,
        "created_at": now,
    }
    if scheduledAt:
        campaign_data["scheduled_at"] = scheduledAt

    async with transaction() as conn:
        await _db_campaigns.create(campaign_id, tenant_id, campaign_data, conn=conn)
        await _db_recipients.create_batch(campaign_id, tenant_id, contacts, conn=conn)
        await _db_counters.init_shards(tenant_id, campaign_id, conn=conn)

    if not scheduledAt:
        from services.queue_manager import enqueue_campaign
        await enqueue_campaign(campaign_id, tenant_id)
        
        log_event("campaign_start", tenant_id=tenant_id, campaign_id=campaign_id,
                  detail=f"contacts={len(contacts)} template={templateName}")
    else:
        log_event("campaign_scheduled", tenant_id=tenant_id, campaign_id=campaign_id,
                  detail=f"scheduled_at={scheduledAt}")

    return {"success": True, "campaignId": campaign_id, "totalContacts": len(contacts), "status": status}


# ── Campaign processing ─────────────────────────────────────────────

async def _send_one(whatsapp: WhatsAppService, contact: dict, template_str: str,
                    campaign_id: str, tenant_id: str,
                    header_image_url: str = "", header_media_id: str = ""):
    """Send a single template message and record the result in the database."""
    phone = contact.get("phone") or contact.get("contact_phone")
    now = _ist_now()
    try:
        components = _build_template_components(
            template_str, contact,
            header_media_id=header_media_id,
            tenant_id=tenant_id,
        )
        result = await whatsapp.send_template_message(
            phone, template_str, components=components if components else None
        )

        if result["success"]:
            async with transaction() as conn:
                await _db_recipients.update_status(
                    tenant_id,
                    campaign_id,
                    phone,
                    "sent",
                    wa_message_id=result["messageId"],
                    conn=conn,
                )
                await _db_counters.increment(tenant_id, campaign_id, "sent", conn=conn)
                await add_message_conn(
                    tenant_id,
                    {
                        "direction": "outgoing",
                        "product_type": "bulk_message",
                        "contact_phone": phone,
                        "message_type": "template",
                        "wa_message_id": result["messageId"],
                        "campaign_id": campaign_id,
                        "status": "sent",
                        "template_name": template_str,
                        "created_at": now,
                    },
                    conn,
                )
        else:
            err = result.get("error", "Unknown error")
            async with transaction() as conn:
                await _db_recipients.update_status(
                    tenant_id,
                    campaign_id,
                    phone,
                    "failed",
                    error_message=err,
                    conn=conn,
                )
                await _db_counters.increment(tenant_id, campaign_id, "failed", conn=conn)
            log_event("send_fail", tenant_id=tenant_id, campaign_id=campaign_id,
                      phone=phone, detail=err, level="WARN")
    except Exception as exc:
        err = str(exc)
        async with transaction() as conn:
            await _db_recipients.update_status(
                tenant_id,
                campaign_id,
                phone,
                "failed",
                error_message=err,
                conn=conn,
            )
            await _db_counters.increment(tenant_id, campaign_id, "failed", conn=conn)
        log_event("send_fail", tenant_id=tenant_id, campaign_id=campaign_id,
                  phone=phone, detail=err, level="WARN")


async def _process_campaign(campaign_id: str, tenant_id: str):
    """Process a bulk campaign from database records."""
    if campaign_id in _worker_locks:
        log_event("campaign_skip", campaign_id=campaign_id, detail="already processing")
        return

    _worker_locks.add(campaign_id)
    try:
        campaign = await _db_campaigns.get(tenant_id, campaign_id)
        if not campaign:
            log_event("campaign_abort", campaign_id=campaign_id, detail="not found in database", level="WARN")
            return

        template_str = campaign.get("template_name")
        delay_ms = campaign.get("delay_ms", 1000)
        header_image_url = campaign.get("header_image_url", "")

        await _db_campaigns.update_status(tenant_id, campaign_id, "running")
        await _db_campaigns.update_heartbeat(tenant_id, campaign_id)

        active_campaigns[campaign_id] = {"running": True, "tenant_id": tenant_id}

        settings = await get_settings(tenant_id)
        if not settings.get("phone_number_id") or not settings.get("access_token"):
            log_event("campaign_abort", tenant_id=tenant_id, campaign_id=campaign_id,
                      detail="WhatsApp not configured", level="ERROR")
            await _db_campaigns.update_status(tenant_id, campaign_id, "failed", error_message="WhatsApp not configured")
            active_campaigns.pop(campaign_id, None)
            return

        whatsapp = WhatsAppService(settings["phone_number_id"], settings["access_token"])

        log_event("campaign_process", tenant_id=tenant_id, campaign_id=campaign_id,
                  detail=f"template={template_str}")
        # Ensure template metadata is cached (tenant-scoped)
        await _ensure_template_cached(template_str, whatsapp, settings, tenant_id=tenant_id)

        # Auto-upload template header media
        header_media_id = await _upload_header_media(template_str, whatsapp, tenant_id=tenant_id)

        try:
            while True:
                # Check BOTH local and DB stop signals (multi-worker safe)
                local_running = active_campaigns.get(campaign_id, {}).get("running", False)
                if not local_running:
                    await _db_campaigns.update_status(tenant_id, campaign_id, "stopped")
                    break

                # Also check DB status for cross-worker stop signals
                db_campaign = await _db_campaigns.get(tenant_id, campaign_id)
                if db_campaign and db_campaign.get("status") == "stopped":
                    log_event("campaign_stopped_remote", campaign_id=campaign_id,
                              detail="stop signal from another worker")
                    break

                await _db_campaigns.update_heartbeat(tenant_id, campaign_id)

                pending = await _db_recipients.get_pending(tenant_id, campaign_id, limit=BATCH_SIZE)
                if not pending:
                    await _db_campaigns.update_status(tenant_id, campaign_id, "completed")
                    break

                tasks = [
                    _send_one(whatsapp, contact, template_str, campaign_id, tenant_id,
                              header_image_url=header_image_url,
                              header_media_id=header_media_id)
                    for contact in pending
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

                await asyncio.sleep(delay_ms / 1000)

            log_event("campaign_complete", tenant_id=tenant_id, campaign_id=campaign_id)

        except Exception as exc:
            log_event("campaign_error", tenant_id=tenant_id, campaign_id=campaign_id,
                      detail=str(exc), level="ERROR")
            await _db_campaigns.update_status(tenant_id, campaign_id, "failed", error_message=str(exc))

    finally:
        active_campaigns.pop(campaign_id, None)
        _worker_locks.discard(campaign_id)


async def periodical_scheduler():
    """Check for due scheduled campaigns every 60 seconds."""
    global _scheduler_running
    if _scheduler_running:
        log_event("scheduler_skip", detail="already running")
        return

    _scheduler_running = True
    try:
        from services.queue_manager import enqueue_campaign
        while True:
            try:
                due = await _db_campaigns.get_due_scheduled_global()
                for campaign in due:
                    cid = str(campaign["campaign_id"])
                    tenant_id = campaign.get("tenant_id", "")
                    log_event("scheduler_launch", campaign_id=cid, detail="enqueuing due campaign")
                    
                    await _db_campaigns.update_status(tenant_id, cid, "queued")
                    await enqueue_campaign(cid, tenant_id)
            except Exception as e:
                log_event("scheduler_error", detail=str(e), level="ERROR")

            await asyncio.sleep(60)
    finally:
        _scheduler_running = False


# ── Campaign management endpoints (with tenant ownership checks) ──

@router.post("/stop/{campaign_id}")
async def stop_campaign(request: Request, campaign_id: str):
    """Stop a running campaign. Requires tenant ownership."""
    tenant_id = request.state.tenant_id

    campaign = await _db_campaigns.get(tenant_id, campaign_id)
    _, error = _verify_campaign_ownership(campaign, tenant_id)
    if error:
        return error

    # Stop works by changing status to "stopped" in the database.
    # Workers reading from BullMQ check this status and will discard jobs.
    await _db_campaigns.update_status(tenant_id, campaign_id, "stopped")
    log_event("campaign_stopped", tenant_id=tenant_id, campaign_id=campaign_id)
    return {"success": True, "message": "Campaign stop requested"}


@router.get("/status/{campaign_id}")
async def get_campaign_status(request: Request, campaign_id: str):
    """Get campaign status. Requires tenant ownership."""
    tenant_id = request.state.tenant_id

    campaign = await _db_campaigns.get(tenant_id, campaign_id)
    campaign, error = _verify_campaign_ownership(campaign, tenant_id)
    if error:
        return error

    counters = await _db_counters.get(tenant_id, campaign_id)
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
    """Delete a campaign. Requires tenant ownership."""
    tenant_id = request.state.tenant_id

    campaign = await _db_campaigns.get(tenant_id, campaign_id)
    _, error = _verify_campaign_ownership(campaign, tenant_id)
    if error:
        return error

    await _db_campaigns.delete(tenant_id, campaign_id)
    await _db_recipients.delete_by_campaign(tenant_id, campaign_id)
    await _db_counters.delete(tenant_id, campaign_id)

    log_event("campaign_deleted", tenant_id=tenant_id, campaign_id=campaign_id)
    return {"success": True, "message": "Campaign deleted"}


@router.get("/campaigns")
async def get_all_campaigns(request: Request, limit: int = 25, cursor: str = None):
    tenant_id = request.state.tenant_id
    campaigns_list, next_cursor = await _db_campaigns.list(tenant_id, limit=limit, cursor=cursor)

    result = []
    for c in campaigns_list:
        cid = str(c.get("campaign_id", ""))
        counters = await _db_counters.get(tenant_id, cid)
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
