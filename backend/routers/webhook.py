"""Webhook routes – WhatsApp incoming message handling."""

import datetime
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from store import settings_store, chatbot_settings, message_logs, conversations
from services.whatsapp import WhatsAppService
from services.chatgpt import get_chatgpt_service

router = APIRouter(prefix="/api/webhook", tags=["webhook"])


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Verify webhook for Meta/WhatsApp."""
    print(f"[WEBHOOK] Verification request: mode={hub_mode}, token={hub_verify_token}, challenge={hub_challenge}")
    stored_token = settings_store.get("webhook_verify_token")
    print(f"[WEBHOOK] Stored verify token: {stored_token}")
    if hub_mode == "subscribe" and hub_verify_token == stored_token:
        print(f"[WEBHOOK] Verification SUCCESS")
        return PlainTextResponse(content=hub_challenge or "")
    print(f"[WEBHOOK] Verification FAILED")
    return JSONResponse(status_code=403, content={"error": "Verification failed"})


@router.post("")
async def handle_webhook(body: dict):
    print(f"[WEBHOOK] Received POST: {body}")
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            for message in value.get("messages", []):
                sender_phone = message.get("from", "")
                sender_name = (
                    value.get("contacts", [{}])[0]
                    .get("profile", {})
                    .get("name", "Unknown")
                )
                message_text = message.get("text", {}).get("body", "")
                now = datetime.datetime.now().isoformat()

                conversations.append({
                    "sender_phone": sender_phone,
                    "sender_name": sender_name,
                    "message_text": message_text,
                    "direction": "incoming",
                    "created_at": now,
                })

                if chatbot_settings["is_enabled"] and settings_store["is_configured"]:
                    response_text = chatbot_settings["fallback_message"]
                    
                    # Use ChatGPT for AI-powered responses
                    api_key = chatbot_settings.get("openai_api_key", "")
                    system_prompt = chatbot_settings.get("ai_system_prompt", "")
                    if api_key:
                        chatgpt = get_chatgpt_service(api_key, system_prompt)
                        ai_result = await chatgpt.get_response(sender_phone, message_text)
                        if ai_result["success"]:
                            response_text = ai_result["response"]
                        else:
                            print(f"[WARN] ChatGPT error: {ai_result['error']}")

                    whatsapp = WhatsAppService(
                        settings_store["phone_number_id"],
                        settings_store["access_token"],
                    )
                    result = await whatsapp.send_text_message(sender_phone, response_text)

                    conversations.append({
                        "sender_phone": sender_phone,
                        "sender_name": sender_name,
                        "message_text": response_text,
                        "direction": "outgoing",
                        "created_at": datetime.datetime.now().isoformat(),
                    })
                    message_logs.append({
                        "product_type": "chatbot",
                        "recipient": sender_phone,
                        "message_id": result.get("messageId"),
                        "status": "sent" if result["success"] else "failed",
                        "error_message": result.get("error"),
                        "created_at": datetime.datetime.now().isoformat(),
                    })
    return {"status": "ok"}
