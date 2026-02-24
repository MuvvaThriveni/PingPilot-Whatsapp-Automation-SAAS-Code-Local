import logging
import os
from fastapi import APIRouter, Request, Query
from fastapi.responses import PlainTextResponse

from whatsapp_client import whatsapp_client
from db_layer.users import users_db
from db_layer.tenants import tenants as _db_tenants
from store import get_settings

# Setup logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chatbot")

VERIFY_TOKEN_FALLBACK = os.getenv("WEBHOOK_VERIFY_TOKEN", "verify123")

def _resolve_tenant_from_payload(value: dict) -> str | None:
    """Extract phone_number_id from webhook payload and resolve to tenant_id."""
    phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
    if not phone_number_id:
        return None
    tenant_doc = _db_tenants.get_by_phone_number_id(phone_number_id)
    if tenant_doc:
        return tenant_doc.get("tenant_id")
    return None

@router.get("/webhook")
async def verify_webhook(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge")
):
    """Verification endpoint for Meta Webhooks."""
    # Check fallback token
    if mode == "subscribe" and token == VERIFY_TOKEN_FALLBACK:
        logger.info("Webhook verified successfully using fallback token.")
        return PlainTextResponse(content=challenge)
    
    # Check per-tenant tokens in DB
    if mode == "subscribe" and token:
        from firebase_config import get_db
        db = get_db()
        if db:
            try:
                docs = db.collection("tenants").where(
                    "webhook_verify_token", "==", token
                ).limit(1).stream()
                for doc in docs:
                    logger.info(f"Webhook verified for tenant: {doc.id}")
                    return PlainTextResponse(content=challenge)
            except Exception as e:
                logger.warning(f"Error checking tenant tokens: {e}")

    return PlainTextResponse(content="Verification failed", status_code=403)

@router.post("/webhook")
async def handle_webhook(request: Request):
    """Handle incoming WhatsApp messages from Meta."""
    try:
        body = await request.json()
        logger.info(f"Incoming webhook payload: {body}")

        entries = body.get("entry", [])
        if not entries:
            return {"status": "ok", "message": "No entries found"}

        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                
                # Resolve tenant from phone_number_id in payload
                tenant_id = _resolve_tenant_from_payload(value)
                if not tenant_id:
                    logger.warning("Could not resolve tenant for incoming message")
                    continue

                # Get tenant settings (access token, phone number id)
                settings = get_settings(tenant_id)
                if not settings.get("is_configured"):
                    logger.warning(f"Tenant {tenant_id} is not configured yet")
                    continue
                
                access_token = settings.get("access_token")
                phone_number_id = settings.get("phone_number_id")

                messages = value.get("messages", [])
                for message in messages:
                    sender_phone = message.get("from")
                    message_type = message.get("type")
                    
                    if message_type == "text":
                        # 2. First Message Handling
                        if not users_db.is_user_seen(sender_phone):
                            users_db.mark_user_seen(sender_phone)
                            await whatsapp_client.send_template(
                                sender_phone, 
                                "first_trigger",
                                phone_number_id=phone_number_id,
                                access_token=access_token
                            )
                            logger.info(f"First message from {sender_phone} (Tenant: {tenant_id}), sent first_trigger")
                        else:
                            logger.info(f"User {sender_phone} already seen, no trigger sent")

                    elif message_type == "button":
                        # 3. Button Handling Logic
                        button_data = message.get("button", {})
                        button_text = button_data.get("text")
                        logger.info(f"Button message from {sender_phone}: {button_text}")

                        if button_text == "Sessions":
                            await whatsapp_client.send_template(
                                sender_phone, 
                                "session_template",
                                phone_number_id=phone_number_id,
                                access_token=access_token
                            )
                        
                        elif button_text == "Products":
                            await whatsapp_client.send_template(
                                sender_phone, 
                                "products_template",
                                phone_number_id=phone_number_id,
                                access_token=access_token
                            )
                        
                        elif button_text == "6:30 AM":
                            await whatsapp_client.send_text_message(
                                sender_phone, 
                                "🧘 Our 6:30 AM Yoga plan includes Surya Namaskar, Pranayama, and light meditation to start your day with energy!",
                                phone_number_id=phone_number_id,
                                access_token=access_token
                            )
                        
                        elif button_text == "7:30 Am":
                            await whatsapp_client.send_text_message(
                                sender_phone, 
                                "🧘 Our 7:30 AM Yoga plan focuses on flexibility and core strength, perfect for mid-morning refreshment.",
                                phone_number_id=phone_number_id,
                                access_token=access_token
                            )
                        
                        elif button_text == "10:30 AM":
                            await whatsapp_client.send_text_message(
                                sender_phone, 
                                "🧘 Our 10:30 AM Yoga plan is a gentle flow designed for stress relief and mindfulness.",
                                phone_number_id=phone_number_id,
                                access_token=access_token
                            )

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Critical error in webhook handler: {str(e)}", exc_info=True)
        return {"status": "error", "message": str(e)}
