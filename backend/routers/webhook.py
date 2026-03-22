from __future__ import annotations

"""Webhook routes – WhatsApp incoming message handling (Phase-6: hardened).

Security improvements:
- X-Hub-Signature-256 verification for all POST webhooks (Meta App Secret)
- Tenant-scoped template cache lookups
- Configurable button→template mappings per tenant (no more hardcoded yoga logic)
- Removed all secret/token logging
- UTC timestamps everywhere
"""

import os
import hmac
import hashlib
import datetime
import uuid
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from utils.time_utils import get_ist_now_iso

from store import get_settings, get_chatbot_settings
from services.whatsapp import WhatsAppService
from services.template_builder import (
    ensure_cached as _ensure_template_cached,
    upload_header_media as _upload_header_media,
    build_components as _build_template_components,
    get_template_keys_for_tenant as _get_template_keys_for_tenant,
)
from db_layer.tenants import tenants as _db_tenants
from observability import log_event
from db_layer.webhook_events import webhook_events as _db_webhook
from db_layer.chat_messages import chat_messages as _db_chat_messages
from db_layer.messages import messages as _db_messages
from db_layer.usage_events import usage_events as _db_usage
from db_layer.chatbot import chatbot_rules as _db_chatbot_rules
from db_layer.users import users_db
from db_layer.campaigns import campaigns as _db_campaigns
from db_layer.campaign_recipients import campaign_recipients as _db_recipients
from db_layer.campaign_counters import campaign_counters as _db_counters
from database import transaction
from services.queue_manager import enqueue_message
from cache import cache, chat_users_key

router = APIRouter(prefix="/api/webhook", tags=["webhook"])


def _ist_now() -> str:
    """Return ISO-formatted IST timestamp."""
    return get_ist_now_iso()


def _resolve_tenant_from_payload(value: dict) -> str | None:
    """Extract phone_number_id from webhook payload and resolve to tenant_id."""
    phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
    if not phone_number_id:
        return None

    tenant_doc = _db_tenants.get_by_phone_number_id(phone_number_id)
    if tenant_doc:
        return tenant_doc.get("tenant_id")
    return None


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(str(s))
        return True
    except Exception:
        return False


async def _maybe_finalize_campaign(tenant_id: str, campaign_id: str, max_attempts: int = 3):
    if not campaign_id or campaign_id == "webhook":
        return
    if not _is_uuid(campaign_id):
        return
    campaign = await _db_campaigns.get(tenant_id, campaign_id)
    if not campaign:
        return

    status = (campaign.get("status") or "").strip().lower()
    if status not in ("running", "queued", "scheduled"):
        return

    total = int(campaign.get("total_contacts") or 0)
    if total <= 0:
        await _db_campaigns.update_status(tenant_id, campaign_id, "completed")
        return

    # Use authoritative recipient table — counters can drift due to race conditions.
    # Do NOT use sent_count + failed_count here; it caused premature completion
    # when webhook delivery callbacks double-incremented the counter.
    done = await _db_recipients.count_done(tenant_id, campaign_id, max_attempts=max_attempts)
    if done >= total:
        await _db_campaigns.update_status(tenant_id, campaign_id, "completed")


async def _resolve_tenant_from_payload_async(value: dict) -> str | None:
    """Async version of tenant resolution."""
    phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
    if not phone_number_id:
        return None
    tenant_doc = await _db_tenants.get_by_phone_number_id(phone_number_id)
    if tenant_doc:
        return tenant_doc.get("tenant_id")
    return None


# ── Webhook verify token ─────────────────────────────────────────────
_raw_verify_token: str = os.environ.get("WEBHOOK_VERIFY_TOKEN", "")
if not _raw_verify_token:
    raise RuntimeError(
        "[FATAL] WEBHOOK_VERIFY_TOKEN is not set in .env. "
        "Generate a strong secret and add it to backend/.env before starting the server."
    )
VERIFY_TOKEN: str = _raw_verify_token

# ── Meta App Secret for webhook signature verification ───────────────
META_APP_SECRET: str = os.environ.get("META_APP_SECRET", "")
if not META_APP_SECRET:
    log_event("startup_warn", level="WARN",
              detail="META_APP_SECRET not set — webhook signature verification DISABLED. "
                     "Set this in production to prevent forged webhooks.")


def _verify_webhook_signature(request_body: bytes, signature_header: str | None) -> bool:
    """Verify X-Hub-Signature-256 from Meta webhook payload.

    Returns True if signature is valid OR if META_APP_SECRET is not configured
    (for backward compatibility during migration).
    """
    if not META_APP_SECRET:
        # Not configured — skip verification (log warning at startup)
        return True

    if not signature_header:
        log_event("webhook_sig_missing", level="WARN", detail="No X-Hub-Signature-256 header")
        return False

    # Header format: "sha256=<hex_digest>"
    if not signature_header.startswith("sha256="):
        log_event("webhook_sig_invalid", level="WARN", detail="Signature header malformed")
        return False

    expected_sig = signature_header[7:]  # Strip "sha256=" prefix
    computed_sig = hmac.new(
        META_APP_SECRET.encode("utf-8"),
        request_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed_sig, expected_sig)


# ── Default button→template mappings (configurable per tenant in DB) ──
_DEFAULT_BUTTON_MAPPINGS = {
    "Sessions": "session_template",
    "Products": "products_template",
    "Morning": "aruna_yoga",
    "Afternoon": "afternoon_meet",
    "Evening": "meet3",
}
_DEFAULT_BUTTON_ID_MAPPINGS = {
    "morning_session": "aruna_yoga",
    "afternoon_session": "afternoon_meet",
    "evening_session": "meet3",
}


async def _get_button_mappings(tenant_id: str) -> tuple[dict, dict]:
    """Get per-tenant button→template mappings from DB (cached).

    Falls back to defaults if the tenant hasn't configured custom mappings.
    """
    cache_key = f"button_mappings:{tenant_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    text_map = dict(_DEFAULT_BUTTON_MAPPINGS)
    id_map = dict(_DEFAULT_BUTTON_ID_MAPPINGS)

    try:
        from db_layer.chatbot import chatbot_config as _db_chatbot_config
        cfg = await _db_chatbot_config.get(tenant_id)
        if cfg:
            custom_text = cfg.get("button_text_mappings")
            custom_id = cfg.get("button_id_mappings")
            if custom_text and isinstance(custom_text, dict):
                text_map = custom_text
            if custom_id and isinstance(custom_id, dict):
                id_map = custom_id
    except Exception:
        pass  # Use defaults on error

    result = (text_map, id_map)
    cache.set(cache_key, result, ttl=3600.0)  # Cache for 1 hour
    return result


# ── GET: Webhook verification ────────────────────────────────────────

@router.get("")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Verify webhook for Meta/WhatsApp.

    Accepts the token if:
      1. It matches WEBHOOK_VERIFY_TOKEN from .env, OR
      2. It matches a tenant's webhook_verify_token stored in the database.

    Returns the hub.challenge as plain text on success — required by Meta.
    """
    log_event("webhook_verify", detail=f"mode={hub_mode}")

    if hub_mode != "subscribe" or not hub_verify_token:
        log_event("webhook_verify", status="failed", level="WARN",
                  detail="Missing hub.mode or hub.verify_token")
        return JSONResponse(status_code=403, content={"error": "Verification failed"})

    # ── Check 1: env-configured token ───────────────────────────────
    if hmac.compare_digest(hub_verify_token, VERIFY_TOKEN):
        log_event("webhook_verify", status="success", detail="env token matched")
        return PlainTextResponse(content=hub_challenge or "")

    # ── Check 2: per-tenant token stored in Postgres ──────────────
    try:
        tenant = await _db_tenants.get_by_webhook_verify_token(hub_verify_token)
        if tenant:
            log_event("webhook_verify", tenant_id=tenant.get("tenant_id", ""), status="success")
            return PlainTextResponse(content=hub_challenge or "")
    except Exception as e:
        log_event("webhook_verify", level="WARN", detail=str(e))

    log_event("webhook_verify", status="failed", level="WARN",
              detail="token did not match any tenant")
    return JSONResponse(status_code=403, content={"error": "Verification failed"})


@router.get("/")
async def verify_webhook_slash(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    return await verify_webhook(hub_mode, hub_verify_token, hub_challenge)


# ── POST: Handle incoming webhook ────────────────────────────────────

@router.post("")
async def handle_webhook(request: Request):
    """Process incoming webhook from Meta/WhatsApp.

    Security: Verifies X-Hub-Signature-256 before processing.
    """
    # ── Step 0: Verify webhook signature ─────────────────────────────
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

    if not _verify_webhook_signature(raw_body, signature):
        log_event("webhook_sig_rejected", level="WARN",
                  detail="Invalid or missing webhook signature — request rejected")
        return JSONResponse(status_code=401, content={"error": "Invalid signature"})

    # Parse the body as JSON
    import json
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    log_event("webhook_receive")

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})

            # Resolve tenant from phone_number_id in payload
            tenant_id = await _resolve_tenant_from_payload_async(value)
            if not tenant_id:
                phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
                log_event("webhook_no_tenant", detail=f"phone_number_id={phone_number_id}", level="WARN")
                continue

            settings = await get_settings(tenant_id)
            chatbot = await get_chatbot_settings(tenant_id)

            # --- Handle status updates ---
            for status_obj in value.get("statuses", []):
                wa_msg_id = status_obj.get("id", "")
                new_status = status_obj.get("status", "")
                if wa_msg_id and new_status:
                    await _db_messages.update_status(wa_msg_id, new_status, tenant_id)

                    outgoing = await _db_messages.get_outgoing_by_wa_message_id(tenant_id, wa_msg_id)
                    if not outgoing:
                        # Fallback: message may have been archived
                        outgoing = await _db_messages.get_outgoing_by_wa_message_id_archived(tenant_id, wa_msg_id)
                    if not outgoing:
                        continue
                    campaign_id = outgoing.get("campaign_id")
                    phone = outgoing.get("contact_phone")
                    if not campaign_id or not phone:
                        continue

                    campaign_id_str = str(campaign_id)
                    if not _is_uuid(campaign_id_str):
                        continue

                    # Only reconcile for active campaigns
                    campaign = await _db_campaigns.get(tenant_id, campaign_id_str)
                    if not campaign:
                        continue
                    if (campaign.get("status") or "").strip().lower() not in ("running", "queued", "scheduled", "completed"):
                        continue

                    # NOTE: webhook status values are commonly: sent, delivered, read, failed
                    terminal_success = new_status in ("delivered", "read")
                    terminal_failure = new_status == "failed"

                    if terminal_success:
                        async with transaction() as conn:
                            await _db_recipients.mark_sent(
                                tenant_id,
                                campaign_id_str,
                                phone,
                                wa_msg_id,
                                conn=conn,
                            )
                            # NOTE: Do NOT increment sent_count here.
                            # The worker already incremented it when transitioning to 'submitted'.
                        await _maybe_finalize_campaign(tenant_id, campaign_id_str, max_attempts=3)
                        continue

                    if terminal_failure:
                        err_detail = ""
                        try:
                            errors = status_obj.get("errors") or []
                            if errors and isinstance(errors, list):
                                err_detail = str(errors[0].get("title") or errors[0].get("message") or "")
                        except Exception:
                            err_detail = ""
                        err_detail = err_detail or "Delivery failed"

                        r = await _db_recipients.get_one(tenant_id, campaign_id_str, phone)
                        attempts = int((r or {}).get("attempt_count") or 0)
                        max_attempts = 3
                        if attempts < max_attempts:
                            # Retry: put back to queued and enqueue a new send job.
                            # Only proceed if the recipient was actually in 'submitted' state;
                            # if not, this is a duplicate/stale webhook callback — ignore it.
                            async with transaction() as conn:
                                transitioned = await _db_recipients.transition_submitted_to_queued(
                                    tenant_id,
                                    campaign_id_str,
                                    phone,
                                    err_detail,
                                    wa_message_id=wa_msg_id,
                                    conn=conn,
                                )
                                if transitioned:
                                    await _db_counters.decrement_sent(tenant_id, campaign_id_str, conn=conn)

                            if transitioned:
                                template_name = campaign.get("template_name") or ""
                                header_image_url = campaign.get("header_image_url", "")
                                template_variables = {
                                    "contact_data": (r or {}).get("contact_data") or {},
                                    "header_image_url": header_image_url,
                                    "name": (r or {}).get("contact_name", ""),
                                }
                                job_id = f"msg_{campaign_id_str}_{phone}"
                                await enqueue_message(
                                    job_id=job_id,
                                    tenant_id=tenant_id,
                                    campaign_id=campaign_id_str,
                                    contact_id=phone,
                                    phone_number=phone,
                                    template_name=template_name,
                                    template_variables=template_variables,
                                )
                        else:
                            # Terminal failure — move from sent_count to failed_count
                            async with transaction() as conn:
                                failed_ok = await _db_recipients.mark_failed(
                                    tenant_id,
                                    campaign_id_str,
                                    phone,
                                    err_detail,
                                    conn=conn,
                                )
                                if failed_ok:
                                    await _db_counters.transfer_sent_to_failed(tenant_id, campaign_id_str, conn=conn)
                            await _maybe_finalize_campaign(tenant_id, campaign_id_str, max_attempts=max_attempts)

            for message in value.get("messages", []):
                wa_message_id = message.get("id", "")
                sender_phone = message.get("from", "")
                sender_name = (
                    value.get("contacts", [{}])[0]
                    .get("profile", {})
                    .get("name", "Unknown")
                )

                message_type = message.get("type", "text")
                message_text = ""
                interactive_button_id = ""

                if message_type == "text":
                    message_text = message.get("text", {}).get("body", "").strip()
                elif message_type == "button":
                    message_text = message.get("button", {}).get("text", "").strip()
                    if not message_text:
                        message_text = message.get("button", {}).get("payload", "").strip()
                elif message_type == "interactive":
                    interactive = message.get("interactive", {})
                    if interactive.get("type") == "button_reply":
                        button_reply_data = interactive.get("button_reply", {})
                        message_text = button_reply_data.get("title", "").strip()
                        interactive_button_id = button_reply_data.get("id", "").strip()

                now = _ist_now()

                # Skip if no content readable for routing
                if not message_text and message_type not in ["button", "interactive"]:
                    log_event("webhook_skip", tenant_id=tenant_id, detail="non-text/no-button or empty message")
                    continue

                # --- Deduplication check ---
                if wa_message_id and await _db_webhook.exists(tenant_id, wa_message_id):
                    log_event("webhook_dedup", tenant_id=tenant_id, phone=sender_phone, detail=wa_message_id)
                    continue

                # Record webhook event for deduplication
                if wa_message_id:
                    await _db_webhook.record(wa_message_id, tenant_id, {"event_type": "message", "created_at": now})

                # Save incoming message to chat_messages
                await _db_chat_messages.add(tenant_id, {
                    "contact_phone": sender_phone,
                    "contact_name": sender_name,
                    "message_text": message_text,
                    "direction": "incoming",
                    "created_at": now,
                })

                # Write to messages collection (single source of truth)
                await _db_messages.add(tenant_id, {
                    "direction": "incoming",
                    "product_type": "chatbot",
                    "contact_phone": sender_phone,
                    "contact_name": sender_name,
                    "message_text": message_text,
                    "message_type": message_type,
                    "wa_message_id": wa_message_id,
                    "status": "received",
                    "created_at": now,
                })

                # Record usage event
                await _db_usage.record(tenant_id, "message_received", "chatbot",
                                       contact_phone=sender_phone, billable=False)

                # Invalidate cached user list
                cache.invalidate(chat_users_key(tenant_id))

                # ── Chatbot Decision Logic ───────────────────────────────
                whatsapp = WhatsAppService(
                    settings["phone_number_id"],
                    settings["access_token"],
                )

                response_text = ""
                response_template = ""
                matched_rule = False

                # 1. Configurable button→template mappings (per-tenant)
                clean_text = message_text.strip()
                text_map, id_map = await _get_button_mappings(tenant_id)

                if not matched_rule:
                    # Check text-based button match
                    if clean_text in text_map:
                        response_template = text_map[clean_text]
                        matched_rule = True
                        log_event("button_match", tenant_id=tenant_id, phone=sender_phone,
                                  status="matched", detail=f"text={clean_text!r} → {response_template}")

                    # Check button ID match
                    elif interactive_button_id and interactive_button_id in id_map:
                        response_template = id_map[interactive_button_id]
                        matched_rule = True
                        log_event("button_id_match", tenant_id=tenant_id, phone=sender_phone,
                                  status="matched", detail=f"id={interactive_button_id!r} → {response_template}")

                # 2. Existing Chatbot Rules (DB-based)
                if not matched_rule and chatbot["is_enabled"]:
                    rules = await _db_chatbot_rules.get_active(tenant_id)
                    message_lower = message_text.lower().strip()
                    for rule in rules:
                        keyword = rule.get("keyword", "").strip().lower()
                        if keyword and keyword in message_lower:
                            response_text = rule.get("response", "")
                            if response_text:
                                matched_rule = True
                                break

                # 3. Fallback Trigger (First Trigger) - Rate limited to once every 24 hours
                if not matched_rule:
                    if await users_db.should_send_trigger(tenant_id, sender_phone):
                        await users_db.record_trigger(tenant_id, sender_phone)
                        response_template = "first_trigger"
                        matched_rule = True
                        log_event("fallback_trigger", tenant_id=tenant_id, phone=sender_phone,
                                  detail="first_trigger sent (24h lock)")

                # --- Execute Reply via Queue ---
                if matched_rule or response_text or response_template:
                    import uuid
                    uid = str(uuid.uuid4())
                    
                    if response_template:
                        await enqueue_message(
                            job_id=f"wh_{wa_message_id}_{uid}",
                            tenant_id=tenant_id,
                            campaign_id="webhook",
                            contact_id=sender_phone,
                            phone_number=sender_phone,
                            template_name=response_template,
                            template_variables={"name": sender_name},
                            priority=0  # Highest priority for interactive chat
                        )
                        log_event("webhook_queued", tenant_id=tenant_id, phone=sender_phone, detail=f"template {response_template}")
                    elif response_text:
                        await enqueue_message(
                            job_id=f"wh_{wa_message_id}_{uid}",
                            tenant_id=tenant_id,
                            campaign_id="webhook",
                            contact_id=sender_phone,
                            phone_number=sender_phone,
                            message_text=response_text,
                            priority=0 
                        )
                        log_event("webhook_queued", tenant_id=tenant_id, phone=sender_phone, detail="text message")

                # Mark webhook event as processed
                if wa_message_id:
                    await _db_webhook.mark_processed(tenant_id, wa_message_id)

    return {"status": "ok"}


@router.post("/")
async def handle_webhook_slash(request: Request):
    return await handle_webhook(request)
