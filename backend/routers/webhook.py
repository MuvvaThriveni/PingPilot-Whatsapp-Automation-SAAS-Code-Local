from __future__ import annotations

"""Webhook routes – WhatsApp incoming message handling (Phase-3: multi-tenant).

Webhooks are unauthenticated (called by Meta). Tenant is resolved from the
phone_number_id in the payload via db_layer.tenants.get_by_phone_number_id().
"""

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


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Verify webhook for Meta/WhatsApp.

    Iterates all tenants to find one whose webhook_verify_token matches.
    """
    log_event("webhook_verify", detail=f"mode={hub_mode}")
    if hub_mode != "subscribe" or not hub_verify_token:
        return JSONResponse(status_code=403, content={"error": "Verification failed"})

    # Try to find a tenant whose verify token matches
    from db_layer.tenants import tenants as _t
    # For verification we need to check all tenants — this is a rare operation
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

    log_event("webhook_verify", status="failed", level="WARN")
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
                message_text = message.get("text", {}).get("body", "")
                now = datetime.datetime.now().isoformat()

                # --- Deduplication check ---
                if wa_message_id and _db_webhook.exists(wa_message_id, "message"):
                    log_event("webhook_dedup", tenant_id=tenant_id, phone=sender_phone, detail=wa_message_id)
                    continue

                # Record webhook event (mark not-yet-processed)
                if wa_message_id:
                    _db_webhook.record(
                        tenant_id=tenant_id,
                        wa_message_id=wa_message_id,
                        event_type="message",
                        sender_phone=sender_phone,
                        raw_payload=message,
                        processed=False,
                    )

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

                if chatbot["is_enabled"] and settings["is_configured"]:
                    response_text = chatbot["fallback_message"]
                    
                    # Use ChatGPT for AI-powered responses
                    api_key = chatbot.get("openai_api_key", "")
                    system_prompt = chatbot.get("ai_system_prompt", "")
                    if api_key:
                        chatgpt = get_chatgpt_service(api_key, system_prompt)
                        ai_result = await chatgpt.get_response(tenant_id, sender_phone, message_text)
                        if ai_result["success"]:
                            response_text = ai_result["response"]
                            _db_usage.record(tenant_id, "ai_reply", "chatbot",
                                             contact_phone=sender_phone)
                        else:
                            log_event("chatgpt_error", tenant_id=tenant_id, phone=sender_phone,
                                      detail=ai_result['error'], level="WARN")

                    whatsapp = WhatsAppService(
                        settings["phone_number_id"],
                        settings["access_token"],
                    )
                    result = await whatsapp.send_text_message(sender_phone, response_text)
                    log_event("webhook_reply", tenant_id=tenant_id, phone=sender_phone,
                              status="sent" if result["success"] else "failed")

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
                    _db_webhook.mark_processed(wa_message_id, "message")

    return {"status": "ok"}


@router.post("/")
async def handle_webhook_slash(body: dict):
    return await handle_webhook(body)
