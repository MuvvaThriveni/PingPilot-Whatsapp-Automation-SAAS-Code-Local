from __future__ import annotations

"""Webhook routes – WhatsApp incoming message handling (Phase-5: cached).

Webhooks are unauthenticated (called by Meta). Tenant is resolved from the
phone_number_id in the payload via db_layer.tenants.get_by_phone_number_id().
"""

import os
import datetime
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from store import get_settings, get_chatbot_settings
from services.whatsapp import WhatsAppService
from services.chatgpt import get_chatgpt_service
from services.template_builder import (
    ensure_cached as _ensure_template_cached,
    upload_header_media as _upload_header_media,
    build_components as _build_template_components,
    _template_components,
)
from db_layer.tenants import tenants as _db_tenants
from observability import log_event
from db_layer.webhook_events import webhook_events as _db_webhook
from db_layer.chat_messages import chat_messages as _db_chat_messages
from db_layer.messages import messages as _db_messages
from db_layer.usage_events import usage_events as _db_usage
from db_layer.chatbot import chatbot_rules as _db_chatbot_rules
from db_layer.users import users_db
from cache import cache, chat_users_key

router = APIRouter(prefix="/api/webhook", tags=["webhook"])


def _resolve_tenant_from_payload(value: dict) -> str | None:
    """Extract phone_number_id from webhook payload and resolve to tenant_id."""
    phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
    if not phone_number_id:
        return None
    tenant_doc = _db_tenants.get_by_phone_number_id(phone_number_id)
    if tenant_doc:
        return tenant_doc.get("tenant_id")
    return None


# ── Webhook verify token ─────────────────────────────────────────────
# MUST be set in backend/.env as WEBHOOK_VERIFY_TOKEN=<your_secret>
# Use this same value in Meta App Dashboard → Webhooks → Verify Token.
_raw_verify_token: str = os.environ.get("WEBHOOK_VERIFY_TOKEN", "")
if not _raw_verify_token:
    raise RuntimeError(
        "[FATAL] WEBHOOK_VERIFY_TOKEN is not set in .env. "
        "Generate a strong secret and add it to backend/.env before starting the server."
    )
VERIFY_TOKEN: str = _raw_verify_token


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Verify webhook for Meta/WhatsApp.

    Accepts the token if:
      1. It matches WEBHOOK_VERIFY_TOKEN from .env, OR
      2. It matches a tenant's webhook_verify_token stored in Firestore.

    Returns the hub.challenge as plain text on success — required by Meta.
    """
    log_event("webhook_verify", detail=f"mode={hub_mode}")

    if hub_mode != "subscribe" or not hub_verify_token:
        log_event("webhook_verify", status="failed", level="WARN",
                  detail="Missing hub.mode or hub.verify_token")
        return JSONResponse(status_code=403, content={"error": "Verification failed"})

    # ── Check 1: env-configured token ───────────────────────────────
    if hub_verify_token == VERIFY_TOKEN:
        log_event("webhook_verify", status="success", detail="env token matched")
        return PlainTextResponse(content=hub_challenge or "")

    # ── Check 2: per-tenant token stored in Firestore ──────────────
    from firebase_config import get_db
    db = get_db()
    if db:
        try:
            docs = db.collection("tenants").where(
                "webhook_verify_token", "==", hub_verify_token
            ).limit(1).stream()
            for doc in docs:
                log_event("webhook_verify", tenant_id=doc.id, status="success")
                return PlainTextResponse(content=hub_challenge or "")
        except Exception as e:
            log_event("webhook_verify", level="WARN", detail=str(e))

    log_event("webhook_verify", status="failed", level="WARN",
              detail=f"token did not match any tenant")
    return JSONResponse(status_code=403, content={"error": "Verification failed"})



@router.get("/")
async def verify_webhook_slash(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    return await verify_webhook(hub_mode, hub_verify_token, hub_challenge)


@router.post("")
async def handle_webhook(body: dict):
    log_event("webhook_receive")

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})

            # Resolve tenant from phone_number_id in payload
            tenant_id = _resolve_tenant_from_payload(value)
            if not tenant_id:
                phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
                log_event("webhook_no_tenant", detail=f"phone_number_id={phone_number_id}", level="WARN")
                continue

            settings = get_settings(tenant_id)
            chatbot = get_chatbot_settings(tenant_id)

            # --- Handle status updates ---
            for status_obj in value.get("statuses", []):
                wa_msg_id = status_obj.get("id", "")
                new_status = status_obj.get("status", "")
                if wa_msg_id and new_status:
                    _db_messages.update_status(wa_msg_id, new_status, tenant_id)

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
                
                if message_type == "text":
                    message_text = message.get("text", {}).get("body", "").strip()
                elif message_type == "button":
                    # For template buttons, Meta sends text in 'text' or 'payload'
                    message_text = message.get("button", {}).get("text", "").strip()
                    if not message_text:
                        message_text = message.get("button", {}).get("payload", "").strip()
                elif message_type == "interactive":
                    # Handle interactive button replies
                    interactive = message.get("interactive", {})
                    if interactive.get("type") == "button_reply":
                        button_reply_data = interactive.get("button_reply", {})
                        message_text = button_reply_data.get("title", "").strip()
                        # Capture button ID for fallback matching (e.g. morning_session)
                        interactive_button_id = button_reply_data.get("id", "").strip()
                    else:
                        interactive_button_id = ""
                
                # For non-interactive messages there is no button_id
                if message_type != "interactive":
                    interactive_button_id = ""

                now = datetime.datetime.now().isoformat()

                # Skip if no content readable for routing
                if not message_text and message_type not in ["button", "interactive"]:
                    log_event("webhook_skip", tenant_id=tenant_id, detail="non-text/no-button or empty message")
                    continue

                # --- Deduplication check ---
                if wa_message_id and _db_webhook.exists(wa_message_id):
                    log_event("webhook_dedup", tenant_id=tenant_id, phone=sender_phone, detail=wa_message_id)
                    continue

                # Record webhook event for deduplication
                if wa_message_id:
                    _db_webhook.record(wa_message_id, tenant_id, {"event_type": "message"})

                # Save incoming message to chat_messages
                _db_chat_messages.add(tenant_id, {
                    "contact_phone": sender_phone,
                    "contact_name": sender_name,
                    "message_text": message_text,
                    "direction": "incoming",
                    "created_at": now,
                })

                # Write to messages collection (single source of truth)
                _db_messages.add(tenant_id, {
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
                _db_usage.record(tenant_id, "message_received", "chatbot",
                                 contact_phone=sender_phone, billable=False)

                # Invalidate cached user list
                cache.invalidate(chat_users_key(tenant_id))

                # ── Chatbot Decision Logic (Yoga Flow) ──────────────────
                whatsapp = WhatsAppService(
                    settings["phone_number_id"],
                    settings["access_token"],
                )
                
                response_text = ""
                response_template = ""
                matched_rule = False

                # 1. Button Handling (Direct matches for yoga plan)
                clean_text = message_text.strip()
                if not matched_rule:
                    if clean_text == "Sessions":
                        response_template = "session_template"
                        matched_rule = True
                    elif clean_text == "Products":
                        response_template = "products_template"
                        matched_rule = True
                    # ── Morning Session → aruna_yoga template ─────────────────────
                    # Triggered by interactive button_reply title OR button ID fallback
                    elif clean_text == "Morning" or interactive_button_id == "morning_session":
                        response_template = "aruna_yoga"
                        matched_rule = True
                        log_event(
                            "morning_session_trigger",
                            tenant_id=tenant_id,
                            phone=sender_phone,
                            status="matched",
                            detail=f"button_title={clean_text!r} button_id={interactive_button_id!r}",
                        )
                        print(f"[WEBHOOK] Morning button matched for {sender_phone} → sending aruna_yoga template")
                    
                        # ── Afternoon Session → afternoon_meet template ──────────────────────────────
                    # Triggered by interactive button_reply title OR button ID fallback.
                    # The shared template pipeline handles IMAGE header automatically.
                    elif clean_text == "Afternoon" or interactive_button_id == "afternoon_session":
                        response_template = "afternoon_meet"
                        matched_rule = True
                        log_event(
                            "afternoon_session_trigger",
                            tenant_id=tenant_id,
                            phone=sender_phone,
                            status="matched",
                            detail=f"button_title={clean_text!r} button_id={interactive_button_id!r}",
                        )
                        print(f"[WEBHOOK] Afternoon button matched for {sender_phone} → sending afternoon_meet template")
                    # ── Evening Session → meet3 template ──────────────────────────────
                    # Triggered by interactive button_reply title OR button ID fallback.
                    # The shared template pipeline handles IMAGE header automatically.
                    elif clean_text == "Evening" or interactive_button_id == "evening_session":
                        response_template = "meet3"
                        matched_rule = True
                        log_event(
                            "evening_session_trigger",
                            tenant_id=tenant_id,
                            phone=sender_phone,
                            status="matched",
                            detail=f"button_title={clean_text!r} button_id={interactive_button_id!r}",
                        )
                        print(f"[WEBHOOK] Evening button matched for {sender_phone} → sending meet3 template")

                # 2. Existing Chatbot Rules (DB-based)
                if not matched_rule and chatbot["is_enabled"]:
                    rules = _db_chatbot_rules.get_active(tenant_id)
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
                    if users_db.should_send_trigger(sender_phone):
                        users_db.record_trigger(sender_phone)
                        response_template = "first_trigger"
                        matched_rule = True
                        print(f"[WEBHOOK] Fallback matched for {sender_phone} → sending first_trigger (24h lock)")
                    else:
                        print(f"[WEBHOOK] No match for {sender_phone}, but 24h trigger lock active. Skipping response.")

                # --- Execute Reply ---
                if matched_rule or response_text or response_template:
                    result = {"success": False}
                    final_msg_content = response_text
                    
                    if response_template:
                        # ── Shared template builder (same as bulk campaigns) ──────────────
                        # Resolve the canonical "name|language" cache key so we can look up
                        # the template metadata and derive the correct language code.
                        tpl_name = response_template
                        tpl_language = "en_US"  # sensible default

                        if "|" in response_template:
                            # Caller already provided a fully-qualified key
                            tpl_name, tpl_language = response_template.split("|", 1)
                        else:
                            # Try to find the key in the cache (any language suffix)
                            matching_keys = [
                                k for k in _template_components
                                if k == response_template or k.startswith(response_template + "|")
                            ]
                            if matching_keys:
                                tpl_language = matching_keys[0].split("|", 1)[1]
                            # else: will fetch below; language resolved after cache hit

                        # Build the canonical key used by the builder
                        canonical_key = f"{tpl_name}|{tpl_language}"

                        # Ensure template metadata is cached (fetch from API if missing)
                        found = await _ensure_template_cached(canonical_key, whatsapp, settings)

                        # If still not found under the guessed language, try a wildcard
                        if not found:
                            matching_keys = [
                                k for k in _template_components
                                if k.startswith(tpl_name + "|")
                            ]
                            if matching_keys:
                                canonical_key = matching_keys[0]
                                tpl_language = canonical_key.split("|", 1)[1]
                                found = True

                        # Auto-upload header media (IMAGE/VIDEO/DOCUMENT) from example handle
                        header_media_id = ""
                        if found:
                            header_media_id = await _upload_header_media(canonical_key, whatsapp)

                        # Build components (handles IMAGE/VIDEO/DOCUMENT/TEXT headers + BODY params)
                        # contact dict carries the sender name for {{1}} BODY params
                        contact_ctx = {"name": sender_name, "phone": sender_phone}
                        components = _build_template_components(
                            canonical_key,
                            contact=contact_ctx,
                            header_media_id=header_media_id,
                        ) or None

                        # first_trigger body-only override (no header media needed)
                        if response_template == "first_trigger" and not components:
                            components = [{
                                "type": "body",
                                "parameters": [{"type": "text", "text": sender_name}],
                            }]

                        result = await whatsapp.send_template_message(
                            sender_phone,
                            tpl_name,
                            language=tpl_language,
                            components=components,
                        )
                        final_msg_content = f"Template: {response_template}"
                    elif response_text:
                        result = await whatsapp.send_text_message(sender_phone, response_text)

                    log_event("webhook_reply", tenant_id=tenant_id, phone=sender_phone,
                              status="sent" if result["success"] else "failed",
                              detail=result.get("error", ""))

                    out_now = datetime.datetime.now().isoformat()
                    # Save outgoing logs
                    _db_chat_messages.add(tenant_id, {
                        "contact_phone": sender_phone,
                        "contact_name": sender_name,
                        "message_text": final_msg_content,
                        "direction": "outgoing",
                        "created_at": out_now,
                    })
                    _db_messages.add(tenant_id, {
                        "direction": "outgoing",
                        "product_type": "chatbot",
                        "contact_phone": sender_phone,
                        "message_type": "template" if response_template else "text",
                        "wa_message_id": result.get("messageId", ""),
                        "status": "sent" if result["success"] else "failed",
                        "error_message": result.get("error", ""),
                        "created_at": out_now,
                    })
                    _db_usage.record(tenant_id, "message_sent", "chatbot", contact_phone=sender_phone)

                # Mark webhook event as processed
                if wa_message_id:
                    _db_webhook.mark_processed(wa_message_id)

    return {"status": "ok"}


@router.post("/")
async def handle_webhook_slash(body: dict):
    return await handle_webhook(body)
