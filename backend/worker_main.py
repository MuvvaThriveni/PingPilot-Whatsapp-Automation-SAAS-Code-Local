import os
import asyncio
import time as _time
import logging
import uuid
import datetime
from bullmq import Worker, Job
from dotenv import load_dotenv

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

load_dotenv()

from database import init_db_pool, close_db_pool, transaction

from store import get_settings
from utils.time_utils import get_ist_now_iso
from services.queue_manager import enqueue_message, enqueue_dead_letter, redis_opts
from services.whatsapp import WhatsAppService
from services.template_builder import (
    ensure_cached as _ensure_template_cached,
    upload_header_media as _upload_header_media,
    build_components as _build_template_components,
    validate_components as _validate_template_components,
    get_template_keys_for_tenant as _get_template_keys_for_tenant,
    has_media_header as _has_media_header,
    invalidate_cached_media as _invalidate_cached_media,
)
from observability import log_event
from rate_limit import (
    tenant_token_bucket_consume,
    is_global_cooldown_active,
)
from db_layer.campaigns import campaigns as _db_campaigns
from db_layer.campaign_recipients import campaign_recipients as _db_recipients
from db_layer.campaign_counters import campaign_counters as _db_counters
from db_layer.messages import messages as _db_messages
from db_layer.tenants import tenants as _db_tenants
from db_layer.quota import get_quota_status, try_consume_quota

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("worker")


DELIVERY_CONFIRM_TIMEOUT_SECONDS = int(os.environ.get("DELIVERY_CONFIRM_TIMEOUT_SECONDS", "900"))

# ── In-worker rate throttling (replaces BullMQ limiter) ─────────────
# Instead of using BullMQ's Redis-heavy limiter scripts, we throttle
# between jobs with a simple asyncio.sleep.  This is safe because the
# worker processes jobs sequentially (concurrency=1).
_RATE_DELAY_SECONDS = float(os.environ.get("WORKER_RATE_DELAY", "0.2"))  # 200ms default = ~5 msg/sec


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(str(s))
        return True
    except Exception:
        return False


async def _maybe_finalize_campaign(tenant_id: str, campaign_id: str, max_attempts: int = 5):
    if not campaign_id or campaign_id in ("webhook", "file_forward"):
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

    # Use authoritative recipient table count — counters can drift.
    done = await _db_recipients.count_done(tenant_id, campaign_id, max_attempts=max_attempts)
    if done >= total:
        await _db_campaigns.update_status(tenant_id, campaign_id, "completed")


def _choose_template_key(keys: list[str]) -> str:
    if not keys:
        return ""
    for k in keys:
        if k.endswith("|en_US"):
            return k
    return keys[0]


async def _resolve_template_key(template_name: str, whatsapp: WhatsAppService, settings: dict, tenant_id: str) -> tuple[str, bool]:
    if not template_name:
        return "", False
    if "|" in template_name:
        ok = await _ensure_template_cached(template_name, whatsapp, settings, tenant_id=tenant_id)
        return template_name, bool(ok)

    preferred = f"{template_name}|en_US"
    ok = await _ensure_template_cached(preferred, whatsapp, settings, tenant_id=tenant_id)
    if ok:
        return preferred, True

    keys = _get_template_keys_for_tenant(tenant_id, template_name)
    chosen = _choose_template_key(keys)
    if chosen:
        return chosen, True
    return template_name, False

async def process_campaign_job(job: Job, token: str):
    """Worker logic for campaign_queue: fetch all pending recipients and distribute to message_queue."""
    campaign_id = job.data.get("campaign_id")
    tenant_id = job.data.get("tenant_id")
    epoch = job.data.get("epoch", "")
    
    logger.info(f"Processing campaign setup: {campaign_id} epoch={epoch}")
    
    campaign = await _db_campaigns.get(tenant_id, campaign_id)
    if not campaign:
        logger.warning(f"Campaign {campaign_id} not found in DB.")
        return
        
    if campaign.get("status") in ("stopped", "deleted"):
        logger.info(f"Campaign {campaign_id} was stopped. Skipping.")
        return
        
    template_name = campaign.get("template_name")
    header_image_url = campaign.get("header_image_url", "")
    
    await _db_campaigns.update_status(tenant_id, campaign_id, "running")
    
    # ── Quota-capped fan-out ────────────────────────────────────────
    tenant = await _db_tenants.get(tenant_id)
    bulk_quota_limit = int((tenant or {}).get("bulk_quota_limit", 100))
    quota = await get_quota_status(tenant_id, bulk_quota_limit)
    quota_remaining = quota.remaining

    if quota_remaining <= 0:
        # Entire campaign is over quota — mark all pending as quota_exceeded
        excess = await _db_recipients.mark_excess_recipients_quota_exceeded(tenant_id, campaign_id)
        log_event("quota_campaign_blocked", tenant_id=tenant_id, campaign_id=campaign_id,
                  detail=f"remaining=0 excess_marked={excess}")
        logger.info(f"Campaign {campaign_id}: quota exhausted, marked {excess} recipients as quota_exceeded.")
        await _maybe_finalize_campaign(tenant_id, campaign_id)
        return "quota_exhausted"

    # Process recipients in large batches
    total_enqueued = 0
    limit = 1000
    while True:
        if total_enqueued >= quota_remaining:
            break

        pending = await _db_recipients.get_pending(tenant_id, campaign_id, limit=limit)
        if not pending:
            break
            
        for contact in pending:
            if total_enqueued >= quota_remaining:
                break
            phone = contact.get("contact_phone")
            job_id = f"msg_{campaign_id}_{phone}" if not epoch else f"msg_{campaign_id}_{phone}_{epoch}"
            
            # Extract dynamically generated header URL if needed
            contact_data = contact.get("contact_data", {})
            
            template_variables = {
                "contact_data": contact_data,
                "header_image_url": header_image_url,
                "name": contact.get("contact_name", "")
            }
            
            # Enqueue to individual message job
            enqueued_ok = False
            try:
                await enqueue_message(
                    job_id=job_id,
                    tenant_id=tenant_id,
                    campaign_id=campaign_id,
                    contact_id=phone,
                    phone_number=phone,
                    template_name=template_name,
                    template_variables=template_variables,
                )
                enqueued_ok = True
            except Exception as e:
                # Most common failure: jobId already exists (restart / duplicate campaign job).
                # Do not abort the whole campaign — transition to queued so it won't be stuck in pending.
                msg = str(e)
                logger.warning(f"Failed to enqueue message job campaign={campaign_id} phone={phone} err={msg}")
                if "already exists" in msg.lower() or "job" in msg.lower() and "exists" in msg.lower():
                    enqueued_ok = True

            if enqueued_ok:
                await _db_recipients.transition_pending_to_queued(tenant_id, campaign_id, phone)
                total_enqueued += 1
            
    # Mark any remaining pending recipients as quota_exceeded
    excess = await _db_recipients.mark_excess_recipients_quota_exceeded(tenant_id, campaign_id)
    if excess > 0:
        log_event("quota_cap_applied", tenant_id=tenant_id, campaign_id=campaign_id,
                  detail=f"enqueued={total_enqueued} cap={quota_remaining} excess_marked={excess}")

    logger.info(f"Campaign {campaign_id}: enqueued {total_enqueued} distinct messages (quota_cap={quota_remaining}, excess={excess}).")
    
    # The campaign remains "running" until all messages finish. The API can check `campaign_counters` to determine completion.
    return "done"


async def process_message_job(job: Job, token: str):
    """Worker logic for message_queue: sends to WhatsApp API."""
    # ── In-worker throttle (replaces BullMQ limiter) ────────────────
    # Avoids Redis-heavy limiter Lua scripts; simple sleep is sufficient
    # for sequential (concurrency=1) processing.
    if _RATE_DELAY_SECONDS > 0:
        await asyncio.sleep(_RATE_DELAY_SECONDS)

    data = job.data
    campaign_id = data.get("campaign_id")
    tenant_id = data.get("tenant_id")
    phone_raw = data.get("phone_number")
    template_name = data.get("template_name", "")
    message_text = data.get("message_text", "")
    extra_vars = data.get("template_variables", {})
    media_id = data.get("media_id", "")
    media_type = data.get("media_type", "")
    media_filename = data.get("media_filename", "file")
    media_caption = data.get("media_caption", "")
    idempotency_key = data.get("idempotency_key")
    job_opts = getattr(job, "opts", None) or {}
    try:
        max_attempts = int(job_opts.get("attempts", 5))
    except Exception:
        max_attempts = 5

    # ── Phone normalization & observability ──────────────────────────
    from utils.phone_utils import normalize_phone
    phone = normalize_phone(phone_raw) if phone_raw else None
    logger.info("Phone normalization: raw=%s, normalized=%s", phone_raw, phone)

    if phone is None:
        logger.warning("Invalid phone number skipped in worker: raw=%s campaign=%s", phone_raw, campaign_id)
        if campaign_id and campaign_id not in ("webhook", "file_forward"):
            from db_layer.campaign_recipients import campaign_recipients as _db_recip
            from db_layer.campaign_counters import campaign_counters as _db_cnt
            async with transaction() as conn:
                await _db_recip.mark_failed(
                    tenant_id, campaign_id, phone_raw or "",
                    "Invalid phone number (failed E.164 validation)",
                    conn=conn,
                )
                await _db_cnt.increment(tenant_id, campaign_id, "failed", conn=conn)
            await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
        return "invalid_phone"

    logger.info(f"Picked job={job.id} tenant={tenant_id} campaign={campaign_id} phone={phone}")

    # Old/stale jobs may carry non-UUID campaign IDs (e.g. "camp1").
    # Avoid DB errors from UUID casts by treating such values as "no campaign".
    if campaign_id and campaign_id not in ("webhook", "file_forward") and not _is_uuid(campaign_id):
        campaign_id = ""
    
    if campaign_id and campaign_id not in ("webhook", "file_forward"):
        campaign = await _db_campaigns.get(tenant_id, campaign_id)
        if not campaign:
            logger.warning(f"Campaign not found: {campaign_id}")
            return "skipped"
        if campaign.get("status") in ("stopped", "deleted"):
            logger.info(f"Skip send; campaign status={campaign.get('status')} campaign={campaign_id}")
            return "skipped"

        existing = await _db_messages.get_sent_for_campaign_recipient(tenant_id, campaign_id, phone)
        if existing:
            logger.info(f"Skip send; already in messages sent campaign={campaign_id} phone={phone}")
            await _db_recipients.mark_sent(
                tenant_id,
                campaign_id,
                phone,
                existing.get("wa_message_id") or "",
            )
            await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
            return "already_sent"

        r = await _db_recipients.get_one(tenant_id, campaign_id, phone)
        if not r:
            logger.warning(f"Recipient row not found campaign={campaign_id} phone={phone}")
            return "skipped"
        if r.get("status") == "sent":
            logger.info(f"Skip send; recipient already sent campaign={campaign_id} phone={phone}")
            await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
            return "already_sent"

        if r.get("status") == "submitted":
            logger.info(f"Skip send; recipient already submitted campaign={campaign_id} phone={phone}")
            latest = await _db_messages.get_latest_outgoing_for_campaign_recipient(tenant_id, campaign_id, phone)
            if not latest:
                await _db_recipients.transition_submitted_to_queued(
                    tenant_id,
                    campaign_id,
                    phone,
                    "Submitted row had no matching outgoing message; re-queued",
                )
                await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
                return "requeued"

            # If delivery confirmation never arrives, re-queue after a timeout.
            try:
                last_attempt = r.get("last_attempt_at")
                if last_attempt and isinstance(last_attempt, datetime.datetime):
                    age = datetime.datetime.now(datetime.timezone.utc) - last_attempt
                    if age.total_seconds() > DELIVERY_CONFIRM_TIMEOUT_SECONDS:
                        await _db_recipients.transition_submitted_to_queued(
                            tenant_id,
                            campaign_id,
                            phone,
                            f"No delivery confirmation after {DELIVERY_CONFIRM_TIMEOUT_SECONDS}s; re-queued",
                        )
                        return "requeued"
            except Exception:
                pass

            await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
            return "skipped"
        if r.get("status") == "processing":
            logger.info(f"Skip send; recipient already processing campaign={campaign_id} phone={phone}")
            # Do not resend: if a sent message exists, finalize; else let it remain processing.
            existing2 = await _db_messages.get_sent_for_campaign_recipient(tenant_id, campaign_id, phone)
            if existing2:
                await _db_recipients.mark_sent(
                    tenant_id,
                    campaign_id,
                    phone,
                    existing2.get("wa_message_id") or "",
                )
                await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
                return "already_sent"
            try:
                last_attempt = r.get("last_attempt_at")
                if last_attempt and isinstance(last_attempt, datetime.datetime):
                    age = datetime.datetime.now(datetime.timezone.utc) - last_attempt
                    if age.total_seconds() > 300:
                        await _db_recipients.transition_processing_to_queued(
                            tenant_id,
                            campaign_id,
                            phone,
                            "Stale processing (>300s); re-queued",
                        )
                        return "requeued"
            except Exception:
                pass
            return "skipped"
        if r.get("status") not in ("queued", "failed"):
            logger.info(f"Skip send; recipient status={r.get('status')} campaign={campaign_id} phone={phone}")
            await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
            return "skipped"

        if r.get("status") == "failed" and int(r.get("attempt_count") or 0) >= max_attempts:
            logger.info(f"Skip send; max attempts reached attempts={r.get('attempt_count')} max={max_attempts} campaign={campaign_id} phone={phone}")
            await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
            return "skipped"

        # ── Atomic quota consume before sending ─────────────────────
        # Only consume quota on the FIRST attempt for this recipient.
        # Retries (attempt_count > 0) already had quota consumed on their
        # initial attempt — counting them again would inflate usage.
        current_attempts = int(r.get("attempt_count") or 0)
        if current_attempts == 0:
            tenant = await _db_tenants.get(tenant_id)
            bulk_quota_limit = int((tenant or {}).get("bulk_quota_limit", 100))
            consumed = await try_consume_quota(tenant_id, bulk_quota_limit)
            if not consumed:
                log_event("quota_exceeded_worker", tenant_id=tenant_id, campaign_id=campaign_id,
                          phone=phone, detail="atomic quota consume failed")
                await _db_recipients.mark_failed(
                    tenant_id, campaign_id, phone,
                    "Monthly bulk message quota exhausted",
                )
                await _db_counters.increment(tenant_id, campaign_id, "failed")
                await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
                return "quota_exceeded"
        else:
            log_event("quota_skip_retry", tenant_id=tenant_id, campaign_id=campaign_id,
                      phone=phone, detail=f"attempt={current_attempts}, quota already consumed")

        ok = await _db_recipients.transition_to_processing(tenant_id, campaign_id, phone)
        if not ok:
            r2 = await _db_recipients.get_one(tenant_id, campaign_id, phone)
            logger.info(f"Skip send; could not transition to processing status={r2.get('status') if r2 else None} campaign={campaign_id} phone={phone}")
            await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
            return "skipped"

    # ── Adaptive global cooldown check ──────────────────────────────
    # If WhatsApp has been returning 429s, all workers pause briefly.
    try:
        if await is_global_cooldown_active():
            log_event(
                "worker_global_cooldown",
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                phone=phone,
                detail="global cooldown active, delaying job",
                level="WARN",
            )
            raise RuntimeError("global_cooldown_active")
    except RuntimeError:
        raise
    except Exception:
        pass  # Redis failure — proceed without throttling

    # ── Tenant token bucket check ─────────────────────────────────────
    # Ensures per-tenant fairness (default 10 msg/sec, burst 20).
    try:
        allowed = await tenant_token_bucket_consume(tenant_id)
        if not allowed:
            log_event(
                "worker_tenant_throttled",
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                phone=phone,
                detail="tenant token bucket exhausted, delaying job",
                level="WARN",
            )
            # Raise to trigger BullMQ retry with backoff (500ms–1s)
            raise RuntimeError("tenant_rate_limited")
    except RuntimeError:
        raise
    except Exception:
        pass  # Redis failure — proceed without throttling

    settings = await get_settings(tenant_id)
    if not settings.get("phone_number_id") or not settings.get("access_token"):
        raise ValueError("WhatsApp not configured")
        
    whatsapp = WhatsAppService(settings["phone_number_id"], settings["access_token"])
    
    if campaign_id and campaign_id not in ("webhook", "file_forward"):
        log_event("worker_send_prepare", tenant_id=tenant_id, campaign_id=campaign_id, phone=phone)
    
    try:
        if media_id:
            # File-forward job: send pre-uploaded media (image or document)
            if media_type == "image":
                result = await whatsapp.send_image(phone, media_id, media_caption)
            else:
                result = await whatsapp.send_document(phone, media_id, media_filename, media_caption)
        elif template_name:
            resolved_template_key, found_tpl = await _resolve_template_key(template_name, whatsapp, settings, tenant_id)
            if not found_tpl:
                log_event(
                    "template_not_found",
                    tenant_id=tenant_id,
                    campaign_id=campaign_id,
                    phone=phone,
                    detail=f"template={template_name}",
                    level="WARN",
                )
                if campaign_id and campaign_id not in ("webhook", "file_forward"):
                    async with transaction() as conn:
                        failed_ok = await _db_recipients.transition_processing_to_failed(
                            tenant_id,
                            campaign_id,
                            phone,
                            f"Template not found: {template_name}",
                            conn=conn,
                        )
                        if failed_ok:
                            await _db_counters.increment(tenant_id, campaign_id, "failed", conn=conn)
                    await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
                return "template_missing"

            template_name = resolved_template_key

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

            # ── Validate components before sending ─────────────────────────
            # Catches parameter count mismatches and missing media headers
            # locally, preventing WhatsApp errors #132000 and #132012.
            valid, validation_err = _validate_template_components(
                template_name, components, tenant_id=tenant_id
            )
            if not valid:
                log_event(
                    "template_validation_failed",
                    tenant_id=tenant_id,
                    campaign_id=campaign_id,
                    phone=phone,
                    detail=validation_err,
                    level="ERROR",
                )
                if campaign_id and campaign_id not in ("webhook", "file_forward"):
                    async with transaction() as conn:
                        failed_ok = await _db_recipients.transition_processing_to_failed(
                            tenant_id, campaign_id, phone,
                            validation_err,
                            conn=conn,
                        )
                        if failed_ok:
                            await _db_counters.increment(tenant_id, campaign_id, "failed", conn=conn)
                    await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
                return "validation_failed"

            # ── Log final payload for production debugging ─────────────────
            log_event(
                "template_payload_built",
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                phone=phone,
                detail=f"template={template_name} components={components}",
            )

            result = await whatsapp.send_template_message(
                phone, template_name, components=components if components else None
            )
        else:
            result = await whatsapp.send_text_message(phone, message_text)
        
        now = get_ist_now_iso()
        if result["success"]:
            wa_message_id = result["messageId"]
            logger.info(f"Send success campaign={campaign_id} phone={phone} wa_id={wa_message_id}")
            
            if campaign_id and campaign_id not in ("webhook", "file_forward"):
                try:
                    async with transaction() as conn:
                        submitted_ok = await _db_recipients.transition_processing_to_submitted(
                            tenant_id,
                            campaign_id,
                            phone,
                            wa_message_id,
                            conn=conn,
                        )
                        if submitted_ok:
                            await _db_counters.increment(tenant_id, campaign_id, "sent", conn=conn)
                        await _db_messages.add_idempotent(
                            tenant_id,
                            {
                                "direction": "outgoing",
                                "product_type": "bulk_message" if campaign_id not in ("webhook", "file_forward") else "chatbot",
                                "contact_phone": phone,
                                "message_type": "template" if template_name else "text",
                                "wa_message_id": wa_message_id,
                                "campaign_id": campaign_id,
                                "status": "submitted",
                                "template_name": template_name,
                                "created_at": now,
                            },
                            conn=conn,
                        )
                    log_event(
                        "worker_finalize_sent",
                        tenant_id=tenant_id,
                        campaign_id=campaign_id,
                        phone=phone,
                        detail=f"wa_id={wa_message_id}",
                    )
                except Exception as finalize_exc:
                    # Critical: do NOT raise after a successful WhatsApp send.
                    # If we raise, BullMQ will retry and we may resend the same message.
                    log_event(
                        "worker_finalize_error",
                        tenant_id=tenant_id,
                        campaign_id=campaign_id,
                        phone=phone,
                        detail=f"wa_id={wa_message_id} err={finalize_exc}",
                        level="ERROR",
                    )

                    # Best-effort fallback: attempt to persist state without a transaction.
                    # Still do not raise.
                    try:
                        await _db_recipients.mark_submitted(tenant_id, campaign_id, phone, wa_message_id)
                    except Exception as e:
                        log_event(
                            "worker_finalize_error",
                            tenant_id=tenant_id,
                            campaign_id=campaign_id,
                            phone=phone,
                            detail=f"fallback mark_sent failed: {e}",
                            level="ERROR",
                        )
                    try:
                        await _db_counters.increment(tenant_id, campaign_id, "sent")
                    except Exception:
                        pass
                    try:
                        await _db_messages.add_idempotent(
                            tenant_id,
                            {
                                "direction": "outgoing",
                                "product_type": "bulk_message",
                                "contact_phone": phone,
                                "message_type": "template" if template_name else "text",
                                "wa_message_id": wa_message_id,
                                "campaign_id": campaign_id,
                                "status": "submitted",
                                "template_name": template_name,
                                "created_at": now,
                            },
                        )
                    except Exception as e:
                        log_event(
                            "worker_finalize_error",
                            tenant_id=tenant_id,
                            campaign_id=campaign_id,
                            phone=phone,
                            detail=f"fallback add_message failed: {e}",
                            level="ERROR",
                        )
            else:
                # Determine product_type for webhook, file_forward, or other non-campaign sends
                if campaign_id == "file_forward":
                    _product_type = "file_forward_bulk"
                    _msg_type = media_type if media_id else ("template" if template_name else "text")
                elif campaign_id == "webhook":
                    _product_type = "chatbot"
                    _msg_type = "template" if template_name else "text"
                else:
                    _product_type = "bulk_message"
                    _msg_type = "template" if template_name else "text"
                await _db_messages.add(tenant_id, {
                    "direction": "outgoing",
                    "product_type": _product_type,
                    "contact_phone": phone,
                    "message_type": _msg_type,
                    "wa_message_id": wa_message_id,
                    "media_id": media_id if media_id else None,
                    "campaign_id": campaign_id if campaign_id not in ("webhook", "file_forward") else "",
                    "status": "sent",
                    "template_name": template_name,
                    "created_at": now,
                })
                if campaign_id == "file_forward":
                    from db_layer.usage_events import usage_events as _db_usage_ff
                    await _db_usage_ff.record(tenant_id, "message_sent", "file_forward_bulk",
                                              contact_phone=phone)
            
            if campaign_id and campaign_id not in ("webhook", "file_forward"):
                await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)

            if campaign_id == "webhook":
                from db_layer.chat_messages import chat_messages as _db_chat_messages
                from db_layer.usage_events import usage_events as _db_usage
                final_msg_content = f"Template: {template_name}" if template_name else message_text
                await _db_chat_messages.add(tenant_id, {
                    "contact_phone": phone,
                    "contact_name": extra_vars.get("name", "Unknown"),
                    "message_text": final_msg_content,
                    "direction": "outgoing",
                })
                await _db_usage.record(tenant_id, "message_sent", "chatbot", contact_phone=phone)
            
            logger.info(f"Job complete job={job.id} campaign={campaign_id} phone={phone}")
            return wa_message_id
            
        else:
            err = result.get("error", "Unknown error")
            err_code = str(result.get("error_code", ""))
            logger.warning(f"Send failed campaign={campaign_id} phone={phone} err={err} code={err_code}")

            # Non-retryable error: template missing / translation missing.
            # Error 132000: parameter count mismatch (validation error).
            # Error 132001: template name missing.
            # Error 132012: parameter format mismatch (e.g. missing media header).
            # Error 100: invalid parameter (e.g. bad media upload).
            # None of these are transient — retrying will never fix them.
            _NON_RETRYABLE_CODES = {"132000", "132001", "132012", "100"}
            non_retryable = (
                err_code in _NON_RETRYABLE_CODES
                or "132000" in err
                or "132001" in err
                or "132012" in err
                or "(code: 100)" in err
                or "Template name does not exist" in err
            )
            # If #132012, the cached media_id is likely stale — invalidate it
            if "132012" in err and template_name:
                _invalidate_cached_media(template_name, tenant_id=tenant_id)

            if campaign_id and campaign_id not in ("webhook", "file_forward"):
                async with transaction() as conn:
                    await _db_recipients.transition_processing_to_queued(
                        tenant_id,
                        campaign_id,
                        phone,
                        err,
                        conn=conn,
                    )

            if non_retryable:
                log_event(
                    "wa_non_retryable",
                    tenant_id=tenant_id,
                    campaign_id=campaign_id,
                    phone=phone,
                    detail=err,
                    level="WARN",
                )
                if campaign_id and campaign_id not in ("webhook", "file_forward"):
                    async with transaction() as conn:
                        failed_ok = await _db_recipients.mark_failed(
                            tenant_id,
                            campaign_id,
                            phone,
                            err,
                            conn=conn,
                        )
                        if failed_ok:
                            await _db_counters.increment(tenant_id, campaign_id, "failed", conn=conn)
                    await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=max_attempts)
                return "non_retryable"

            raise RuntimeError(f"WhatsApp API Error: {err}")
            
    except Exception as e:
        if campaign_id and campaign_id not in ("webhook", "file_forward"):
            async with transaction() as conn:
                await _db_recipients.transition_processing_to_queued(
                    tenant_id,
                    campaign_id,
                    phone,
                    str(e),
                    conn=conn,
                )
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
        if campaign_id and campaign_id not in ("webhook", "file_forward"):
            tenant_id = job.data.get("tenant_id")
            async with transaction() as conn:
                failed_ok = await _db_recipients.mark_failed(
                    tenant_id,
                    campaign_id,
                    phone,
                    f"Exhausted retries: {str(error)}",
                    conn=conn,
                )
                if failed_ok:
                    await _db_counters.increment(tenant_id, campaign_id, "failed", conn=conn)
            await _maybe_finalize_campaign(tenant_id, campaign_id, max_attempts=int(job.opts.get("attempts", 3)))
        await enqueue_dead_letter(job.data, reason=str(error))


async def main():
    logger.info("Starting BullMQ Workers...")

    await init_db_pool()
    
    # ── Worker options ───────────────────────────────────────────────
    # CRITICAL: drainDelay, stalledInterval, lockDuration, maxStalledCount
    # MUST be top-level keys in the opts dict.  Nesting them inside a
    # "settings" sub-dict is silently ignored by bullmq-python.
    #
    # drainDelay:       Max 10s (capped by BullMQ's maximum_block_timeout).
    #                   The worker uses BZPOPMIN with this timeout when idle.
    # stalledInterval:  5 min — checks for stalled jobs much less often
    #                   (default 30s → ~17 Redis calls/min; 300s → ~0.2/min).
    # lockDuration:     5 min — matches stalledInterval, prevents premature
    #                   stall detection.  Worst-case job = ~125s, well within.
    # maxStalledCount:  1 — minimal stalled-check iterations.
    #
    # BullMQ limiter is intentionally REMOVED.  It injects Redis Lua scripts
    # into every moveToActive call (~6 extra Redis commands per job).
    # Rate control is now handled by a simple asyncio.sleep() inside the
    # message processor (see _RATE_DELAY_SECONDS above).

    campaign_worker_opts = {
        "connection": redis_opts,
        "drainDelay": 10,            # 10s idle poll (max allowed by BullMQ)
        "stalledInterval": 300000,   # 5 minutes
        "lockDuration": 300000,      # 5 minutes
        "maxStalledCount": 1,
    }

    message_worker_opts = {
        "connection": redis_opts,
        "drainDelay": 10,            # 10s idle poll (max allowed by BullMQ)
        "stalledInterval": 300000,   # 5 minutes
        "lockDuration": 300000,      # 5 minutes
        "maxStalledCount": 1,
    }

    # Worker for Campaign splits
    campaign_worker = Worker("campaign_queue", process_campaign_job, campaign_worker_opts)
    
    # Worker for concurrent messages (rate limiting via asyncio.sleep, NOT BullMQ limiter)
    message_worker = Worker("message_queue", process_message_job, message_worker_opts)
    
    # The current python bullmq API might attach event listeners slightly differently, but natively you can use the built-in events.
    def _on_failed(job: Job, error: Exception):
        asyncio.create_task(on_failed_message(job, error))

    message_worker.on("failed", _on_failed)
    
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
        await close_db_pool()


if __name__ == "__main__":
    asyncio.run(main())
