"""Bulk WhatsApp Messaging routes (Phase-7: queue-unified).

Security fixes:
- Tenant ownership validation on stop/status/delete endpoints
- File upload size limit (16MB)
- DB-backed stop signals (works across multiple workers)
- UTC timestamps everywhere
- Removed all print() logging → structured log_event()
- Tenant-scoped template cache lookups
- All campaign processing delegated to BullMQ workers (no direct sends)
"""

import io
import re
import time
import uuid
import asyncio
import datetime
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
import pandas as pd
from utils.time_utils import get_ist_now_iso, get_ist_now

from store import get_settings
from services.whatsapp import WhatsAppService
from observability import log_event
from db_layer.campaigns import campaigns as _db_campaigns
from db_layer.campaign_recipients import campaign_recipients as _db_recipients
from db_layer.campaign_counters import campaign_counters as _db_counters
from database import transaction, execute
from services.queue_manager import campaign_queue

router = APIRouter(prefix="/api/bulk-message", tags=["bulk-message"])

# ── Constants ────────────────────────────────────────────────────────
MAX_UPLOAD_SIZE_BYTES = 16 * 1024 * 1024  # 16MB

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


@router.get("/campaigns/{campaign_id}/details")
async def get_campaign_details(request: Request, campaign_id: str):
    """Get full campaign details including all recipients. Requires tenant ownership."""
    tenant_id = request.state.tenant_id

    campaign = await _db_campaigns.get(tenant_id, campaign_id)
    campaign, error = _verify_campaign_ownership(campaign, tenant_id)
    if error:
        return error

    counters = await _db_counters.get(tenant_id, campaign_id)
    recipients = await _db_recipients.list_by_campaign(tenant_id, campaign_id, limit=5000)

    recipient_list = []
    for r in recipients:
        recipient_list.append({
            "contact_phone": r.get("contact_phone", ""),
            "contact_name": r.get("contact_name", ""),
            "status": r.get("status", ""),
            "error_message": r.get("error_message", ""),
            "attempt_count": r.get("attempt_count", 0),
            "updated_at": str(r.get("updated_at", "")),
        })

    return {
        "campaign": {
            "campaign_id": campaign_id,
            "name": campaign.get("name", ""),
            "template_name": campaign.get("template_name", ""),
            "header_image_url": campaign.get("header_image_url", ""),
            "total_contacts": campaign.get("total_contacts", 0),
            "sent_count": counters.get("sent", 0),
            "failed_count": counters.get("failed", 0),
            "status": campaign.get("status", ""),
            "delay_ms": campaign.get("delay_ms", 1000),
            "created_at": str(campaign.get("created_at", "")),
            "scheduled_at": str(campaign.get("scheduled_at", "")) if campaign.get("scheduled_at") else None,
        },
        "recipients": recipient_list,
    }


@router.post("/campaigns/{campaign_id}/resend-failed")
async def resend_failed_recipients(request: Request, campaign_id: str):
    """Re-queue all failed recipients for a campaign. Requires tenant ownership."""
    tenant_id = request.state.tenant_id

    campaign = await _db_campaigns.get(tenant_id, campaign_id)
    campaign, error = _verify_campaign_ownership(campaign, tenant_id)
    if error:
        return error

    if campaign.get("status") == "running":
        return JSONResponse(status_code=400, content={"error": "Campaign is still running"})

    failed = await _db_recipients.get_failed(tenant_id, campaign_id)
    if not failed:
        return JSONResponse(status_code=400, content={"error": "No failed recipients to resend"})

    template_name = campaign.get("template_name") or ""
    header_image_url = campaign.get("header_image_url", "")

    # Reset failed recipients to pending and reset their attempt counters
    await execute(
        """
        UPDATE campaign_recipients
        SET status = 'pending', error_message = '', attempt_count = 0, updated_at = now()
        WHERE tenant_id = %s AND campaign_id = %s::uuid AND status = 'failed'
        """,
        tenant_id,
        campaign_id,
    )

    # Adjust counters: move failed_count back to 0 for the re-queued ones
    resend_count = len(failed)
    await execute(
        """
        UPDATE campaigns
        SET failed_count = GREATEST(failed_count - %s, 0),
            status = 'running',
            updated_at = now()
        WHERE tenant_id = %s AND campaign_id = %s::uuid
        """,
        resend_count,
        tenant_id,
        campaign_id,
    )

    # Enqueue the campaign for processing (worker will pick up pending recipients).
    # Use a unique epoch so message jobIds don't collide with previous sends,
    # and a unique campaign jobId so BullMQ doesn't deduplicate against the original launch.
    epoch = str(int(time.time()))
    await campaign_queue.add(
        "process_campaign",
        {"campaign_id": campaign_id, "tenant_id": tenant_id, "epoch": epoch},
        opts={"jobId": f"campaign_resend_{campaign_id}_{epoch}"},
    )

    log_event("campaign_resend_failed", tenant_id=tenant_id, campaign_id=campaign_id,
              detail=f"re-queued {resend_count} failed recipients")

    return {
        "success": True,
        "resend_count": resend_count,
        "message": f"Re-queued {resend_count} failed recipients",
    }
