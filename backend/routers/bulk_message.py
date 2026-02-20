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
import httpx
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
import pandas as pd

from store import get_settings, active_campaigns, add_message
from services.whatsapp import WhatsAppService
from observability import log_event

router = APIRouter(prefix="/api/bulk-message", tags=["bulk-message"])

# Process-local template component cache (rebuilt on miss from WhatsApp API)
_template_components: dict = {}

# Process-local campaign cache for status polling when Firestore is unavailable.
# Stores recently finished campaigns for a short period to avoid 404s in the UI.
_recent_campaign_status: dict = {}
_RECENT_CAMPAIGN_TTL_SECONDS = 60 * 10

# Process-local campaign store (Firestore disabled)
_local_campaigns: dict[str, dict] = {}
_local_campaign_counters: dict[str, dict[str, int]] = {}


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


def _get_template_components(template_key: str, tenant_id: str = "") -> list:
    """Get cached template components (process-local cache)."""
    if template_key in _template_components:
        return _template_components[template_key]
    return []


def _build_template_components(template_key: str, contact: dict = None, header_media_id: str = "") -> list:
    """Auto-build the components array for a template based on cached metadata.

    Priority for media headers:
      1. header_media_id     — pre-uploaded permanent WhatsApp media ID (no URL needed)
      2. contact imageUrl    — per-contact URL from spreadsheet or headerImageUrl field
      3. Nothing             — header component is omitted (text-only templates)
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
                if header_media_id:
                    # Use pre-uploaded permanent media ID (preferred — no URL expiry issues)
                    components.append({
                        "type": "header",
                        "parameters": [{"type": "image", "image": {"id": header_media_id}}],
                    })
                else:
                    image_link = (contact or {}).get("imageUrl", "").strip()
                    if image_link:
                        components.append({
                            "type": "header",
                            "parameters": [{"type": "image", "image": {"link": image_link}}],
                        })
                    else:
                        print(f"[BULK] WARNING: Template has IMAGE header but no mediaId or imageUrl. Skipping header component.")
            elif header_format == "VIDEO":
                if header_media_id:
                    components.append({
                        "type": "header",
                        "parameters": [{"type": "video", "video": {"id": header_media_id}}],
                    })
                else:
                    video_link = (contact or {}).get("videoUrl", "").strip()
                    if video_link:
                        components.append({
                            "type": "header",
                            "parameters": [{"type": "video", "video": {"link": video_link}}],
                        })
                    else:
                        print(f"[BULK] WARNING: Template has VIDEO header but no mediaId or videoUrl. Skipping header component.")
            elif header_format == "DOCUMENT":
                if header_media_id:
                    components.append({
                        "type": "header",
                        "parameters": [{"type": "document", "document": {"id": header_media_id}}],
                    })
                else:
                    doc_link = (contact or {}).get("documentUrl", "").strip()
                    if doc_link:
                        components.append({
                            "type": "header",
                            "parameters": [{"type": "document", "document": {"link": doc_link}}],
                        })
                    else:
                        print(f"[BULK] WARNING: Template has DOCUMENT header but no mediaId or documentUrl. Skipping header component.")

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
    _local_campaigns[campaign_id] = {
        "campaign_id": campaign_id,
        "tenant_id": tenant_id,
        "name": campaignName or f"Campaign {datetime.datetime.now().strftime('%Y-%m-%d')}",
        "template_name": templateName,
        "header_image_url": headerImageUrl.strip() if headerImageUrl else "",
        "total_contacts": len(contacts),
        "status": "running",
        "created_at": now,
    }
    _local_campaign_counters[campaign_id] = {"sent": 0, "failed": 0}

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


async def _send_one(whatsapp: WhatsAppService, contact: dict, template_str: str, campaign_id: str, tenant_id: str, header_image_url: str = "", header_media_id: str = ""):
    """Send a single template message and record the result (local store only)."""
    phone = contact["phone"]
    now = datetime.datetime.now().isoformat()
    try:
        # Priority: per-contact imageUrl > global headerImageUrl field (both as links)
        if header_image_url and not contact.get("imageUrl"):
            contact = {**contact, "imageUrl": header_image_url}
        # Build components — header_media_id (permanent WhatsApp media ID) takes top priority
        components = _build_template_components(template_str, contact, header_media_id=header_media_id)
        print(f"[BULK] Sending template='{template_str}' to={phone} components={components}")
        result = await whatsapp.send_template_message(
            phone, template_str, components=components if components else None
        )
        print(f"[BULK] Result for {phone}: success={result.get('success')} messageId={result.get('messageId')} error={result.get('error')}")

        if result["success"]:
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
            _local_campaign_counters.setdefault(campaign_id, {"sent": 0, "failed": 0})
            _local_campaign_counters[campaign_id]["sent"] += 1
        else:
            err = result.get("error", "Unknown error")
            print(f"[BULK] FAILED to send to {phone}: {err}")
            log_event("send_fail", tenant_id=tenant_id, campaign_id=campaign_id,
                      phone=phone, detail=err, level="WARN")
            add_message(tenant_id, {
                "direction": "outgoing",
                "product_type": "bulk_message",
                "contact_phone": phone,
                "message_type": "template",
                "campaign_id": campaign_id,
                "status": "failed",
                "error_message": err,
                "template_name": template_str,
                "created_at": now,
            })
            _local_campaign_counters.setdefault(campaign_id, {"sent": 0, "failed": 0})
            _local_campaign_counters[campaign_id]["failed"] += 1
    except Exception as exc:
        print(f"[BULK] EXCEPTION while sending to {phone}: {exc}")
        log_event("send_fail", tenant_id=tenant_id, campaign_id=campaign_id,
                  phone=phone, detail=str(exc), level="WARN")
        add_message(tenant_id, {
            "direction": "outgoing",
            "product_type": "bulk_message",
            "contact_phone": phone,
            "message_type": "template",
            "campaign_id": campaign_id,
            "status": "failed",
            "error_message": str(exc),
            "template_name": template_str,
            "created_at": now,
        })
        _local_campaign_counters.setdefault(campaign_id, {"sent": 0, "failed": 0})
        _local_campaign_counters[campaign_id]["failed"] += 1


async def _process_campaign(campaign_id: str):
    """Process a bulk campaign – sends messages in concurrent batches (Firestore disabled)."""
    state = active_campaigns.get(campaign_id)
    if not state:
        return

    tenant_id = state.get("tenant_id", "")
    settings = get_settings(tenant_id)
    template_str = state["template"]
    contacts = state["contacts"]

    print(f"[BULK] Starting campaign={campaign_id} template='{template_str}' contacts={len(contacts)} phone_number_id='{settings.get('phone_number_id')}' token_set={bool(settings.get('access_token'))}")

    if not settings.get("phone_number_id") or not settings.get("access_token"):
        print(f"[BULK] ABORT campaign={campaign_id}: WhatsApp not configured (phone_number_id or access_token missing)")
        if campaign_id in _local_campaigns:
            _local_campaigns[campaign_id]["status"] = "failed"
        active_campaigns.pop(campaign_id, None)
        return

    whatsapp = WhatsAppService(settings["phone_number_id"], settings["access_token"])

    # Ensure template metadata is cached (fetch from WhatsApp API if missing)
    if template_str not in _template_components:
        print(f"[BULK] Template '{template_str}' not in cache — fetching from WhatsApp API...")
        tmpl_result = await whatsapp.get_templates(settings["business_account_id"])
        if tmpl_result["success"]:
            for t in tmpl_result["templates"]:
                key = f"{t['name']}|{t['language']}"
                _template_components[key] = t.get("components", [])
            print(f"[BULK] Cached {len(tmpl_result['templates'])} templates. Target key '{template_str}' found={template_str in _template_components}")
        else:
            print(f"[BULK] WARNING: Could not fetch templates: {tmpl_result.get('error')} — sending without components")
    else:
        print(f"[BULK] Template '{template_str}' found in cache.")

    # ── Auto-upload template header media ────────────────────────────────────────
    # If the template has an IMAGE/VIDEO/DOCUMENT header with an example image (set
    # in Meta), we download it once and re-upload via WhatsApp media API to get a
    # permanent media_id. This is used for ALL sends in this campaign so the user
    # never needs to provide a URL manually.
    header_media_id = ""  # will be populated by auto-upload below if template has example media
    if template_str in _template_components:
        for comp in _template_components[template_str]:
            if comp.get("type") == "HEADER" and comp.get("format") in ("IMAGE", "VIDEO", "DOCUMENT"):
                handle_list = (comp.get("example") or {}).get("header_handle") or []
                handle_url = handle_list[0] if handle_list else ""
                if handle_url:
                    print(f"[BULK] Auto-uploading template header media from example handle...")
                    try:
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            dl = await client.get(handle_url)
                        if dl.status_code == 200:
                            mime = dl.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                            upload_result = await whatsapp.upload_media(dl.content, mime)
                            if upload_result["success"]:
                                header_media_id = upload_result["mediaId"]
                                print(f"[BULK] ✅ Template header media uploaded. mediaId={header_media_id} mime={mime}")
                            else:
                                print(f"[BULK] WARNING: Media upload failed: {upload_result.get('error')} — will try without header")
                        else:
                            print(f"[BULK] WARNING: Could not download example media (status={dl.status_code}) — will try without header")
                    except Exception as exc:
                        print(f"[BULK] WARNING: Exception auto-uploading header media: {exc} — will try without header")
                break  # only process the first HEADER component

    try:
        # Process contacts in batches of BATCH_SIZE concurrently
        for i in range(0, len(contacts), BATCH_SIZE):
            if not active_campaigns.get(campaign_id, {}).get("running"):
                log_event("campaign_stopped", tenant_id=tenant_id, campaign_id=campaign_id)
                if campaign_id in _local_campaigns:
                    _local_campaigns[campaign_id]["status"] = "stopped"
                break

            batch = contacts[i : i + BATCH_SIZE]
            header_image_url = state.get("header_image_url", "")
            tasks = [
                _send_one(whatsapp, contact, template_str, campaign_id, tenant_id,
                          header_image_url=header_image_url, header_media_id=header_media_id)
                for contact in batch
            ]
            # return_exceptions=True prevents one failed send from aborting the entire batch
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for exc in results:
                if isinstance(exc, Exception):
                    print(f"[BULK] Unhandled exception in batch gather: {exc}")

            # Small delay between batches to respect rate limits
            if i + BATCH_SIZE < len(contacts):
                await asyncio.sleep(state["delay"] / 1000)


        if active_campaigns.get(campaign_id, {}).get("running"):
            if campaign_id in _local_campaigns:
                _local_campaigns[campaign_id]["status"] = "completed"
            log_event("campaign_complete", tenant_id=tenant_id, campaign_id=campaign_id)
            counters = _local_campaign_counters.get(campaign_id, {"sent": 0, "failed": 0})
            print(f"[BULK] Campaign={campaign_id} completed. sent={counters.get('sent', 0)} failed={counters.get('failed', 0)}")

    except Exception as exc:
        print(f"[BULK] FATAL ERROR in campaign={campaign_id}: {exc}")
        log_event("campaign_error", tenant_id=tenant_id, campaign_id=campaign_id, detail=str(exc), level="ERROR")
        if campaign_id in _local_campaigns:
            _local_campaigns[campaign_id]["status"] = "failed"

    finally:
        finished = active_campaigns.pop(campaign_id, None) or {}
        _recent_campaign_status[campaign_id] = {
            "campaign_id": campaign_id,
            "tenant_id": finished.get("tenant_id", ""),
            "template_name": finished.get("template", ""),
            "total_contacts": len(finished.get("contacts", []) or []),
            "status": _local_campaigns.get(campaign_id, {}).get("status", "completed"),
            "created_at": datetime.datetime.now().isoformat(),
            "cached_at": datetime.datetime.now().timestamp(),
        }


@router.post("/stop/{campaign_id}")
async def stop_campaign(campaign_id: str):
    # Signal in-flight task to stop
    if campaign_id in active_campaigns:
        active_campaigns[campaign_id]["running"] = False
    if campaign_id in _local_campaigns:
        _local_campaigns[campaign_id]["status"] = "stopped"
    return {"success": True, "message": "Campaign stop requested"}


@router.get("/status/{campaign_id}")
async def get_campaign_status(request: Request, campaign_id: str):
    campaign = _local_campaigns.get(campaign_id)
    if not campaign:
        cached = _recent_campaign_status.get(campaign_id)
        if cached:
            cached_at = float(cached.get("cached_at", 0) or 0)
            if (datetime.datetime.now().timestamp() - cached_at) <= _RECENT_CAMPAIGN_TTL_SECONDS:
                counters = _local_campaign_counters.get(campaign_id, {"sent": 0, "failed": 0})
                return {
                    "campaign": {
                        "campaign_id": campaign_id,
                        "name": cached.get("name", ""),
                        "template_name": cached.get("template_name", ""),
                        "total_contacts": cached.get("total_contacts", 0),
                        "sent_count": counters.get("sent", 0),
                        "failed_count": counters.get("failed", 0),
                        "status": cached.get("status", "completed"),
                        "created_at": cached.get("created_at", ""),
                    }
                }
            _recent_campaign_status.pop(campaign_id, None)
        return JSONResponse(status_code=404, content={"error": "Campaign not found"})

    counters = _local_campaign_counters.get(campaign_id, {"sent": 0, "failed": 0})
    return {
        "campaign": {
            "campaign_id": campaign.get("campaign_id", campaign_id),
            "name": campaign.get("name", ""),
            "template_name": campaign.get("template_name", ""),
            "total_contacts": campaign.get("total_contacts", 0),
            "sent_count": counters.get("sent", 0),
            "failed_count": counters.get("failed", 0),
            "status": campaign.get("status", ""),
            "created_at": campaign.get("created_at", ""),
        }
    }


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(request: Request, campaign_id: str):
    campaign = _local_campaigns.get(campaign_id)
    if not campaign:
        return JSONResponse(status_code=404, content={"error": "Campaign not found"})
    # Stop in-flight task if running locally
    if campaign_id in active_campaigns:
        active_campaigns[campaign_id]["running"] = False
        active_campaigns.pop(campaign_id, None)
    _local_campaigns.pop(campaign_id, None)
    _local_campaign_counters.pop(campaign_id, None)
    _recent_campaign_status.pop(campaign_id, None)
    return {"success": True, "message": "Campaign deleted"}


@router.get("/campaigns")
async def get_all_campaigns(request: Request, limit: int = 25, cursor: str = None):
    tenant_id = request.state.tenant_id
    campaigns = [c for c in _local_campaigns.values() if c.get("tenant_id") == tenant_id]
    campaigns = sorted(campaigns, key=lambda x: x.get("created_at", ""), reverse=True)
    campaigns = campaigns[: max(0, int(limit or 25))]

    result = []
    for c in campaigns:
        cid = c.get("campaign_id", "")
        totals = _local_campaign_counters.get(cid, {"sent": 0, "failed": 0})
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
        "next_cursor": None,
    }
