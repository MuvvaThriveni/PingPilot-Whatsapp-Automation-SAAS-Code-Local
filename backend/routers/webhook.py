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
from db_layer.tenants import tenants as _db_tenants
from observability import log_event
from db_layer.webhook_events import webhook_events as _db_webhook
from db_layer.chat_messages import chat_messages as _db_chat_messages
from db_layer.messages import messages as _db_messages
from db_layer.usage_events import usage_events as _db_usage
from db_layer.chatbot import chatbot_rules as _db_chatbot_rules
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
# Set WEBHOOK_VERIFY_TOKEN in your backend .env file.
# Use the same value in Meta App Dashboard → Webhooks → Verify Token.
VERIFY_TOKEN: str = os.environ.get("WEBHOOK_VERIFY_TOKEN", "verify123")


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Verify webhook for Meta/WhatsApp.

    Accepts the token if:
      1. It matches the hardcoded VERIFY_TOKEN (verify123), OR
      2. It matches a tenant's webhook_verify_token stored in Firestore.

    Returns the hub.challenge as plain text on success — required by Meta.
    """
    log_event("webhook_verify", detail=f"mode={hub_mode}")

    if hub_mode != "subscribe" or not hub_verify_token:
        log_event("webhook_verify", status="failed", level="WARN",
                  detail="Missing hub.mode or hub.verify_token")
        return JSONResponse(status_code=403, content={"error": "Verification failed"})

    # ── Check 1: hardcoded fallback token (always works) ───────────
    if hub_verify_token == VERIFY_TOKEN:
        log_event("webhook_verify", status="success", detail="hardcoded token matched")
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
                message_text = message.get("text", {}).get("body", "").strip()
                now = datetime.datetime.now().isoformat()

                # Skip non-text messages (images, audio, etc.) with no text body
                if not message_text:
                    log_event("webhook_skip", tenant_id=tenant_id, detail="non-text or empty message")
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
                    "message_type": "text",
                    "wa_message_id": wa_message_id,
                    "status": "received",
                    "created_at": now,
                })

                # Record usage event
                _db_usage.record(tenant_id, "message_received", "chatbot",
                                 contact_phone=sender_phone, billable=False)

                # Invalidate cached user list so frontend picks up new conversations
                cache.invalidate(chat_users_key(tenant_id))

                # ── Debug: show decision context ──────────────────────────
                print(f"[WEBHOOK] chatbot_enabled={chatbot.get('is_enabled')} "
                      f"is_configured={settings.get('is_configured')} "
                      f"phone_number_id={settings.get('phone_number_id')!r} "
                      f"has_token={bool(settings.get('access_token'))}")

                if chatbot["is_enabled"] and settings["is_configured"]:
                    response_text = chatbot["fallback_message"]
                    matched_rule = False
                    
                    # Step 1: Check rule-based keyword matches (fast, no API cost)
                    rules = _db_chatbot_rules.get_active(tenant_id)
                    message_lower = message_text.lower().strip()
                    print(f"[WEBHOOK] {len(rules)} active rules, checking: {message_lower!r}")
                    for rule in rules:
                        # Field is "keyword" (singular) as stored by chatbot rules
                        keyword = rule.get("keyword", "").strip().lower()
                        if keyword and keyword in message_lower:
                            response_text = rule.get("response", chatbot["fallback_message"])
                            matched_rule = True
                            print(f"[WEBHOOK] keyword rule matched: {keyword!r}")
                            break
                    
                    # Step 2: Use AI only if no rule matched and use_ai is enabled
                    if not matched_rule and chatbot.get("use_ai", True):
                        api_key = chatbot.get("openai_api_key", "")
                        system_prompt = chatbot.get("ai_system_prompt", "")
                        print(f"[WEBHOOK] no rule matched → use_ai={chatbot.get('use_ai')} has_key={bool(api_key)}")
                        if api_key:
                            chatgpt = get_chatgpt_service(api_key, system_prompt)
                            ai_result = await chatgpt.get_response(tenant_id, sender_phone, message_text)
                            if ai_result["success"]:
                                response_text = ai_result["response"]
                                _db_usage.record(tenant_id, "ai_reply", "chatbot",
                                                 contact_phone=sender_phone)
                            else:
                                print(f"[WEBHOOK] AI error: {ai_result['error']}")
                                log_event("chatgpt_error", tenant_id=tenant_id, phone=sender_phone,
                                          detail=ai_result['error'], level="WARN")

                    print(f"[WEBHOOK] → sending to {sender_phone}: {response_text!r}")
                    whatsapp = WhatsAppService(
                        settings["phone_number_id"],
                        settings["access_token"],
                    )
                    result = await whatsapp.send_text_message(sender_phone, response_text)
                    print(f"[WEBHOOK] WA API result: {result}")
                    log_event("webhook_reply", tenant_id=tenant_id, phone=sender_phone,
                              status="sent" if result["success"] else "failed",
                              detail=result.get("error", ""))

                    out_now = datetime.datetime.now().isoformat()

                    # Save outgoing chat message
                    _db_chat_messages.add(tenant_id, {
                        "contact_phone": sender_phone,
                        "contact_name": sender_name,
                        "message_text": response_text,
                        "direction": "outgoing",
                        "created_at": out_now,
                    })

                    # Save to messages (single source of truth)
                    _db_messages.add(tenant_id, {
                        "direction": "outgoing",
                        "product_type": "chatbot",
                        "contact_phone": sender_phone,
                        "message_type": "text",
                        "wa_message_id": result.get("messageId", ""),
                        "status": "sent" if result["success"] else "failed",
                        "error_message": result.get("error", ""),
                        "created_at": out_now,
                    })

                    _db_usage.record(tenant_id, "message_sent", "chatbot",
                                     contact_phone=sender_phone)

                # Mark webhook event as processed
                if wa_message_id:
                    _db_webhook.mark_processed(wa_message_id)

    return {"status": "ok"}


@router.post("/")
async def handle_webhook_slash(body: dict):
    return await handle_webhook(body)
