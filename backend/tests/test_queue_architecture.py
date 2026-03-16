import os
import pytest
import asyncio
import types
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Must mock firebase and dot env before importing our modules
_fake_database = types.ModuleType("database")
_fake_database.init_db_pool = AsyncMock()
_fake_database.close_db_pool = AsyncMock()
_fake_database.ping = AsyncMock(return_value=True)
_fake_database.fetch = AsyncMock(return_value=[])
_fake_database.fetchrow = AsyncMock(return_value=None)
_fake_database.execute = AsyncMock(return_value="")
_fake_database.executemany = AsyncMock(return_value=None)

sys.modules["database"] = _fake_database

with patch("firebase_config.init_firebase"):
    from services.queue_manager import enqueue_message, enqueue_campaign, message_queue
    from worker_main import process_message_job, on_failed_message
    from bullmq import Job

@pytest.fixture
def mock_job():
    job = MagicMock(spec=Job)
    job.id = "msg_test_campaign_919876543210"
    job.data = {
        "tenant_id": "tenant1",
        "campaign_id": "camp1",
        "phone_number": "919876543210",
        "template_name": "hello_world",
        "template_variables": {"name": "Test User"},
        "idempotency_key": "msg_camp1_919876543210"
    }
    job.attemptsMade = 1
    job.opts = {"attempts": 5}
    return job

@pytest.mark.asyncio
async def test_queue_job_creation():
    """Test that jobs are correctly configured with idempotency and retry limit."""
    with patch("bullmq.Queue.add", new_callable=AsyncMock) as mock_add:
        # Action
        await enqueue_message(
            job_id="msg_test_919876",
            tenant_id="tenant1",
            campaign_id="camp1",
            contact_id="contact1",
            phone_number="919876543210",
            template_name="test_tpl",
            priority=1
        )
        
        # Assert
        mock_add.assert_called_once()
        args, kwargs = mock_add.call_args
        assert args[0] == "send_message"
        
        job_data = args[1]
        assert job_data["phone_number"] == "919876543210"
        assert job_data["idempotency_key"] == "msg_test_919876"
        
        job_opts = args[2]
        assert job_opts["jobId"] == "msg_test_919876"  # Idempotency relies on jobId in BullMQ
        assert job_opts["attempts"] == 5
        assert job_opts["backoff"]["type"] == "exponential"

@pytest.mark.asyncio
async def test_worker_processing_success(mock_job):
    """Simulate worker successfully processing a job and verifying API."""
    with patch("worker_main.WhatsAppService.send_template_message", new_callable=AsyncMock) as mock_wa, \
         patch("worker_main._db_campaigns.get", new_callable=AsyncMock, return_value={"status": "running"}), \
         patch("worker_main._db_recipients.update_status", new_callable=AsyncMock) as mock_db, \
         patch("worker_main._db_counters.increment", new_callable=AsyncMock), \
         patch("worker_main._ensure_template_cached", new_callable=AsyncMock), \
         patch("worker_main._upload_header_media", new_callable=AsyncMock, return_value="123"), \
         patch("worker_main.get_settings", new_callable=AsyncMock, return_value={"phone_number_id": "1", "access_token": "2"}), \
         patch("worker_main._db_messages.add", new_callable=AsyncMock):
             
        mock_wa.return_value = {"success": True, "messageId": "wamid_123"}
        
        # Action
        result = await process_message_job(mock_job, "token")
        
        # Assert
        assert result == "wamid_123"
        mock_db.assert_any_call("tenant1", "camp1", "919876543210", "processing")
        mock_db.assert_any_call("tenant1", "camp1", "919876543210", "sent", wa_message_id="wamid_123")


@pytest.mark.asyncio
async def test_retry_mechanism_and_failure(mock_job):
    """Simulate API rate limits/errors triggering retries."""
    with patch("worker_main.WhatsAppService.send_template_message", new_callable=AsyncMock) as mock_wa, \
         patch("worker_main._db_campaigns.get", new_callable=AsyncMock, return_value={"status": "running"}), \
         patch("worker_main._db_recipients.update_status", new_callable=AsyncMock) as mock_db, \
         patch("worker_main._db_counters.increment", new_callable=AsyncMock), \
         patch("worker_main._ensure_template_cached", new_callable=AsyncMock), \
         patch("worker_main._upload_header_media", new_callable=AsyncMock), \
         patch("worker_main.get_settings", new_callable=AsyncMock, return_value={"phone_number_id": "1", "access_token": "2"}):
        
        mock_wa.return_value = {"success": False, "error": "(#131056) Rate limit hit"}
        
        # Action
        with pytest.raises(RuntimeError, match="WhatsApp API Error"):
            await process_message_job(mock_job, "token")
            
        # Assert
        # The worker will update status to "retrying" before bubbling the exception to BullMQ
        mock_db.assert_any_call(
            "tenant1",
            "camp1",
            "919876543210",
            "retrying",
            error_message="WhatsApp API Error: (#131056) Rate limit hit",
        )


@pytest.mark.asyncio
async def test_failure_handling_dead_letter(mock_job):
    """Simulate moving a job to DLQ when exhaust retries."""
    mock_job.attemptsMade = 5  # Reached max
    mock_job.opts = {"attempts": 5}
    
    with patch("worker_main.enqueue_dead_letter", new_callable=AsyncMock) as mock_dlq, \
         patch("worker_main._db_recipients.update_status", new_callable=AsyncMock) as mock_db, \
         patch("worker_main._db_counters.increment", new_callable=AsyncMock):
        
        await on_failed_message(mock_job, Exception("Timeout Error"))
        
        mock_db.assert_any_call(
            "tenant1",
            "camp1",
            "919876543210",
            "failed",
            error_message="Exhausted retries: Timeout Error",
        )
        
        mock_dlq.assert_called_once()
        args, kwargs = mock_dlq.call_args
        assert args[0]["campaign_id"] == "camp1"
        assert kwargs["reason"] == "Timeout Error"
        
@pytest.mark.asyncio
async def test_network_failures_recovery(mock_job):
    """Simulate a network exception throwing."""
    with patch("worker_main._db_campaigns.get", new_callable=AsyncMock, return_value={"status": "running"}), \
         patch("worker_main.get_settings", new_callable=AsyncMock, return_value={"phone_number_id": "1", "access_token": "2"}), \
         patch("worker_main._ensure_template_cached", side_effect=Exception("Network Unreachable")), \
         patch("worker_main._db_recipients.update_status", new_callable=AsyncMock) as mock_db:
             
        with pytest.raises(Exception, match="Network Unreachable"):
            await process_message_job(mock_job, "token")
            
        mock_db.assert_any_call(
            "tenant1",
            "camp1",
            "919876543210",
            "retrying",
            error_message="Network Unreachable",
        )
