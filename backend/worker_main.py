import os
import asyncio
import logging
from bullmq import Worker, Job
from dotenv import load_dotenv

load_dotenv()

# We perform the same startup checks and firebase initialization
from firebase_config import init_firebase, get_db
init_firebase()

from store import get_settings
from utils.time_utils import get_ist_now_iso
from services.queue_manager import enqueue_message, enqueue_dead_letter, redis_opts
from services.whatsapp import WhatsAppService
from services.template_builder import (
    ensure_cached as _ensure_template_cached,
    upload_header_media as _upload_header_media,
    build_components as _build_template_components,
)
from db_layer.campaigns import campaigns as _db_campaigns
from db_layer.campaign_recipients import campaign_recipients as _db_recipients
from db_layer.campaign_counters import campaign_counters as _db_counters
from db_layer.messages import messages as _db_messages

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("worker")

async def process_campaign_job(job: Job, token: str):
    """Worker logic for campaign_queue: fetch all pending recipients and distribute to message_queue."""
    campaign_id = job.data.get("campaign_id")
    tenant_id = job.data.get("tenant_id")
    
    logger.info(f"Processing campaign setup: {campaign_id}")
    
    campaign = _db_campaigns.get(campaign_id)
    if not campaign:
        logger.warning(f"Campaign {campaign_id} not found in DB.")
        return
        
    if campaign.get("status") in ("stopped", "deleted"):
        logger.info(f"Campaign {campaign_id} was stopped. Skipping.")
        return
        
    template_name = campaign.get("template_name")
    header_image_url = campaign.get("header_image_url", "")
    
    _db_campaigns.update_status(campaign_id, "running")
    
    # Process recipients in large batches
    total_enqueued = 0
    limit = 1000
    while True:
        pending = _db_recipients.get_pending(campaign_id, limit=limit)
        if not pending:
            break
            
        for contact in pending:
            phone = contact.get("contact_phone")
            job_id = f"msg_{campaign_id}_{phone}"
            
            # Extract dynamically generated header URL if needed
            contact_data = contact.get("contact_data", {})
            
            template_variables = {
                "contact_data": contact_data,
                "header_image_url": header_image_url,
                "name": contact.get("contact_name", "")
            }
            
            # Enqueue to individual message job
            await enqueue_message(
                job_id=job_id,
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                contact_id=phone,
                phone_number=phone,
                template_name=template_name,
                template_variables=template_variables
            )
            
            # Status update
            _db_recipients.update_status(campaign_id, phone, "queued")
            total_enqueued += 1
            
    logger.info(f"Campaign {campaign_id}: enqueued {total_enqueued} distinct messages.")
    
    # The campaign remains "running" until all messages finish. The API can check `campaign_counters` to determine completion.
    return "done"


async def process_message_job(job: Job, token: str):
    """Worker logic for message_queue: sends to WhatsApp API."""
    data = job.data
    campaign_id = data.get("campaign_id")
    tenant_id = data.get("tenant_id")
    phone = data.get("phone_number")
    template_name = data.get("template_name", "")
    message_text = data.get("message_text", "")
    extra_vars = data.get("template_variables", {})
    idempotency_key = data.get("idempotency_key")
    
    # Check if the campaign was stopped dynamically (only if it belongs to a campaign)
    if campaign_id and campaign_id != "webhook":
        campaign = _db_campaigns.get(campaign_id)
        if campaign and campaign.get("status") in ("stopped", "deleted"):
            _db_recipients.update_status(campaign_id, phone, "failed", error_message="Campaign was stopped")
            _db_counters.increment(campaign_id, "failed")
            return "skipped"

    settings = get_settings(tenant_id)
    if not settings.get("phone_number_id") or not settings.get("access_token"):
        raise ValueError("WhatsApp not configured")
        
    whatsapp = WhatsAppService(settings["phone_number_id"], settings["access_token"])
    
    if campaign_id and campaign_id != "webhook":
        _db_recipients.update_status(campaign_id, phone, "processing")
    
    try:
        if template_name:
            # Cache template on tenant
            await _ensure_template_cached(template_name, whatsapp, settings, tenant_id=tenant_id)
            
            # Determine media overrides
            header_media_id = await _upload_header_media(template_name, whatsapp, tenant_id=tenant_id)
            
            # Build template parameters
            contact_stub = {
                "phone": phone, 
                "name": extra_vars.get("name", ""),
                "imageUrl": extra_vars.get("header_image_url", ""),
                **extra_vars.get("contact_data", {})
            }
            
            components = _build_template_components(
                template_name, 
                contact_stub,
                header_media_id=header_media_id,
                tenant_id=tenant_id
            )
            
            result = await whatsapp.send_template_message(
                phone, template_name, components=components if components else None
            )
        else:
            result = await whatsapp.send_text_message(phone, message_text)
        
        now = get_ist_now_iso()
        if result["success"]:
            wa_message_id = result["messageId"]
            
            if campaign_id and campaign_id != "webhook":
                _db_recipients.update_status(campaign_id, phone, "sent", wa_message_id=wa_message_id)
                _db_counters.increment(campaign_id, "sent")
            
            _db_messages.add(tenant_id, {
                "direction": "outgoing",
                "product_type": "bulk_message" if campaign_id != "webhook" else "chatbot",
                "contact_phone": phone,
                "message_type": "template" if template_name else "text",
                "wa_message_id": wa_message_id,
                "campaign_id": campaign_id if campaign_id != "webhook" else "",
                "status": "sent",
                "template_name": template_name,
                "created_at": now,
            })
            
            if campaign_id == "webhook":
                from db_layer.chat_messages import chat_messages as _db_chat_messages
                from db_layer.usage_events import usage_events as _db_usage
                final_msg_content = f"Template: {template_name}" if template_name else message_text
                _db_chat_messages.add(tenant_id, {
                    "contact_phone": phone,
                    "contact_name": extra_vars.get("name", "Unknown"),
                    "message_text": final_msg_content,
                    "direction": "outgoing",
                    "created_at": now,
                })
                _db_usage.record(tenant_id, "message_sent", "chatbot", contact_phone=phone)
            
            return wa_message_id
            
        else:
            err = result.get("error", "Unknown error")
            if campaign_id and campaign_id != "webhook":
                _db_recipients.update_status(campaign_id, phone, "failed", error_message=err)
            raise RuntimeError(f"WhatsApp API Error: {err}")
            
    except Exception as e:
        if campaign_id and campaign_id != "webhook":
            _db_recipients.update_status(campaign_id, phone, "retrying", error_message=str(e))
        raise e  # Throw to BullMQ to handle retry/backoff


# BullMQ Failed Job Listener
async def on_failed_message(job: Job, error: Exception):
    """Moved to dead letter queue if exhausted."""
    if not job:
        return
        
    data = job.data
    campaign_id = data.get("campaign_id")
    phone = data.get("phone_number")
    
    logger.error(f"Job {job.id} failed. Attempt {job.attemptsMade}/{job.opts.get('attempts')}. Error: {error}")
    
    if job.attemptsMade >= job.opts.get("attempts", 5):
        # Final failure
        logger.error(f"Job {job.id} reached max retries. Moving to Dead Letter Queue.")
        if campaign_id and campaign_id != "webhook":
            _db_recipients.update_status(campaign_id, phone, "failed", error_message=f"Exhausted retries: {str(error)}")
            _db_counters.increment(campaign_id, "failed")
        await enqueue_dead_letter(job.data, reason=str(error))


async def main():
    logger.info("Starting BullMQ Workers...")
    
    # Limiter setup: max 80 requests per second (1000ms)
    rate_limit = int(os.environ.get("QUEUE_RATE_LIMIT", "80"))
    
    worker_options = {
        "limiter": {
            "max": rate_limit, 
            "duration": 1000 
        },
        "connection": redis_opts
    }

    # Worker for Campaign splits
    campaign_worker = Worker("campaign_queue", process_campaign_job, {"connection": redis_opts})
    
    # Worker for concurrent messages
    message_worker = Worker("message_queue", process_message_job, worker_options)
    
    # The current python bullmq API might attach event listeners slightly differently, but natively you can use the built-in events.
    message_worker.on("failed", on_failed_message)
    
    logger.info("Workers are listening to Redis queues. Press Ctrl+C to exit.")
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Shutting down workers...")
    finally:
        await campaign_worker.close()
        await message_worker.close()


if __name__ == "__main__":
    asyncio.run(main())
