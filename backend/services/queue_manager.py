import os
from bullmq import Queue
from rate_limit import get_redis_opts_for_bullmq

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
    """Enqueue individual message job."""
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

