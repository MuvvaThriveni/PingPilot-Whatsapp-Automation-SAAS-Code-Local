import os
import re
import logging
from bullmq import Queue
from rate_limit import get_redis_opts_for_bullmq
from utils.phone_utils import normalize_phone

logger = logging.getLogger("queue_manager")

redis_opts = get_redis_opts_for_bullmq()

# Queues
campaign_queue = Queue("campaign_queue", {"connection": redis_opts})
message_queue = Queue("message_queue", {"connection": redis_opts})
dead_letter_queue = Queue("dead_letter_queue", {"connection": redis_opts})

# Helper function to enqueue
async def enqueue_campaign(campaign_id: str, tenant_id: str):
    """Enqueue a job to process all contacts of a campaign and push them to message_queue."""
    await campaign_queue.add(
        "process_campaign",
        {
            "campaign_id": campaign_id,
            "tenant_id": tenant_id
        },
        opts={"jobId": f"campaign_launch_{campaign_id}"}
    )

async def enqueue_message(
    job_id: str,
    tenant_id: str,
    campaign_id: str,
    contact_id: str,
    phone_number: str,
    template_name: str = "",
    template_variables: dict = None,
    message_text: str = "",
    priority: int = 1
):
    """Enqueue individual message job with idempotent deduplication for campaigns.
    
    For campaign-based jobs (campaign_id != "webhook" or "file_forward"):
        - Overrides job_id with format: {campaign_id}:{normalized_phone}:{safe_template}
        - Normalizes phone numbers to ensure consistent format (prepends 91 for 10-digit numbers)
        - Sanitizes template_name to ensure safe, consistent jobId keys
        - Ensures deduplication of identical messages to the same recipient
        - Sets contact_id to normalized_phone for data consistency
    
    For webhook/file_forward jobs:
        - Uses the provided job_id as-is (preserves existing behavior)
    """
    
    # Override jobId for campaign-based message jobs to ensure idempotent deduplication
    if campaign_id not in ("webhook", "file_forward"):
        # Normalize phone number using shared utility
        # Correctly preserves international numbers while applying +91 fallback for Indian locals
        normalized_phone = normalize_phone(phone_number)

        if normalized_phone is None:
            logger.warning("Invalid phone number skipped: raw=%s campaign=%s", phone_number, campaign_id)
            return
        
        # Sanitize template_name for safe jobId generation
        # - Default to "text" if empty
        # - Convert to lowercase
        # - Replace spaces with underscores
        # - Remove any non-alphanumeric/underscore characters
        safe_template = re.sub(r'[^a-zA-Z0-9_]', '', (template_name or "text").lower().replace(" ", "_"))
        
        # Generate composite jobId: campaign_id:normalized_phone:safe_template
        # Including template_name ensures different templates for the same user are not blocked
        job_id = f"{campaign_id}:{normalized_phone}:{safe_template}"
        
        # Update contact_id to normalized_phone for data consistency
        contact_id = normalized_phone
    
    # For webhook/file_forward jobs, use the provided job_id as-is
    
    opts = {
        "jobId": job_id, 
        "attempts": int(os.environ.get("QUEUE_RETRY_ATTEMPTS", "3")),
        "backoff": {
            "type": "exponential",
            "delay": 5000  # Will start at 5s, then 10s, 20s, 40s, 80s
        },
        "removeOnComplete": True,
        "removeOnFail": True,
        "priority": priority
    }
    
    await message_queue.add(
        "send_message",
        {
            "tenant_id": tenant_id,
            "campaign_id": campaign_id,
            "contact_id": contact_id,
            "phone_number": phone_number,
            "template_name": template_name,
            "template_variables": template_variables or {},
            "message_text": message_text,
            "idempotency_key": job_id
        },
        opts
    )

async def enqueue_file_forward(
    job_id: str,
    tenant_id: str,
    phone_number: str,
    media_id: str,
    media_type: str = "document",
    filename: str = "file",
    caption: str = "",
    priority: int = 1,
):
    """Enqueue a file-forward message job (image or document via media_id)."""
    opts = {
        "jobId": job_id,
        "attempts": int(os.environ.get("QUEUE_RETRY_ATTEMPTS", "3")),
        "backoff": {
            "type": "exponential",
            "delay": 5000,
        },
        "removeOnComplete": True,
        "removeOnFail": True,
        "priority": priority,
    }

    await message_queue.add(
        "send_message",
        {
            "tenant_id": tenant_id,
            "campaign_id": "file_forward",
            "contact_id": phone_number,
            "phone_number": phone_number,
            "template_name": "",
            "template_variables": {},
            "message_text": "",
            "media_id": media_id,
            "media_type": media_type,
            "media_filename": filename,
            "media_caption": caption,
            "idempotency_key": job_id,
        },
        opts,
    )


async def enqueue_dead_letter(job_data: dict, reason: str):
    """Move failed job to dead letter queue."""
    # Naming the jobId explicitly means we prevent duplicate insertions in DLQ
    opts = {}
    if "idempotency_key" in job_data:
        opts["jobId"] = f"dlq_{job_data['idempotency_key']}"
        
    job_data["failure_reason"] = reason
    await dead_letter_queue.add("permanently_failed", job_data, opts)

