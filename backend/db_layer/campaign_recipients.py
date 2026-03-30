from __future__ import annotations

"""Postgres operations for the `campaign_recipients` table.

Primary key: (tenant_id, campaign_id, contact_phone).
One row per recipient per campaign — supports parallel worker writes.
Write frequency: HIGH.
"""

import datetime
import json

from database import (
    execute,
    fetchrow,
    fetch,
    executemany,
    execute_conn,
    fetchrow_conn,
    fetch_conn,
    executemany_conn,
)
from observability import log_event
from utils.time_utils import get_ist_now_iso


def _ist_now_iso() -> str:
    return get_ist_now_iso()


class _CampaignRecipients:

    @staticmethod
    async def get_one(tenant_id: str, campaign_id: str, contact_phone: str, conn=None) -> dict | None:
        try:
            q = """
                SELECT tenant_id, campaign_id, contact_phone, contact_name, contact_data,
                       status, wa_message_id, error_message, attempt_count, last_attempt_at, updated_at
                FROM campaign_recipients
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s
                """
            if conn is not None:
                row = await fetchrow_conn(conn, q, tenant_id, campaign_id, contact_phone)
            else:
                row = await fetchrow(q, tenant_id, campaign_id, contact_phone)
            return dict(row) if row else None
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.get_one failed: {e}", level="ERROR")
            return None

    @staticmethod
    async def mark_submitted(
        tenant_id: str,
        campaign_id: str,
        contact_phone: str,
        wa_message_id: str,
        conn=None,
    ) -> bool:
        try:
            q = """
                UPDATE campaign_recipients
                SET
                    status = 'submitted',
                    wa_message_id = COALESCE(%s, ''),
                    error_message = '',
                    updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s
                  AND status <> 'sent'
                RETURNING 1 AS ok
                """
            args = (wa_message_id or "", tenant_id, campaign_id, contact_phone)
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)
            return bool(row)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.mark_submitted failed: {e}", level="ERROR")
            return False

    @staticmethod
    async def transition_pending_to_queued(tenant_id: str, campaign_id: str, contact_phone: str, conn=None) -> bool:
        """pending -> queued"""
        try:
            q = """
                UPDATE campaign_recipients
                SET status = 'queued', updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s AND status = 'pending'
                RETURNING 1 AS ok
                """
            if conn is not None:
                row = await fetchrow_conn(conn, q, tenant_id, campaign_id, contact_phone)
            else:
                row = await fetchrow(q, tenant_id, campaign_id, contact_phone)
            return bool(row)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.transition_pending_to_queued failed: {e}", level="ERROR")
            return False

    @staticmethod
    async def mark_sent(
        tenant_id: str,
        campaign_id: str,
        contact_phone: str,
        wa_message_id: str,
        conn=None,
    ) -> bool:
        """Set status=sent if not already sent.

        This is used for idempotent finalization after a successful WhatsApp send.
        """
        try:
            q = """
                UPDATE campaign_recipients
                SET
                    status = 'sent',
                    wa_message_id = CASE WHEN %s <> '' THEN %s ELSE wa_message_id END,
                    error_message = '',
                    updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s AND status <> 'sent'
                RETURNING 1 AS ok
                """
            args = (
                wa_message_id or "",
                wa_message_id or "",
                tenant_id,
                campaign_id,
                contact_phone,
            )
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)
            return bool(row)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.mark_sent failed: {e}", level="ERROR")
            return False

    @staticmethod
    async def mark_failed(
        tenant_id: str,
        campaign_id: str,
        contact_phone: str,
        error_message: str,
        conn=None,
    ) -> bool:
        """Set status=failed if not already failed/sent.

        We never overwrite 'sent'.
        """
        try:
            q = """
                UPDATE campaign_recipients
                SET
                    status = 'failed',
                    error_message = COALESCE(%s, ''),
                    updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s
                  AND status NOT IN ('sent', 'failed')
                RETURNING 1 AS ok
                """
            args = (error_message or "", tenant_id, campaign_id, contact_phone)
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)
            return bool(row)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.mark_failed failed: {e}", level="ERROR")
            return False

    @staticmethod
    async def transition_to_processing(tenant_id: str, campaign_id: str, contact_phone: str, conn=None) -> bool:
        """queued|failed -> processing (increments attempt_count exactly once per send-attempt)."""
        try:
            q = """
                UPDATE campaign_recipients
                SET
                    status = 'processing',
                    attempt_count = attempt_count + 1,
                    last_attempt_at = now(),
                    updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s
                  AND status IN ('queued', 'failed')
                RETURNING 1 AS ok
                """
            if conn is not None:
                row = await fetchrow_conn(conn, q, tenant_id, campaign_id, contact_phone)
            else:
                row = await fetchrow(q, tenant_id, campaign_id, contact_phone)
            return bool(row)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.transition_to_processing failed: {e}", level="ERROR")
            return False

    @staticmethod
    async def transition_processing_to_submitted(
        tenant_id: str,
        campaign_id: str,
        contact_phone: str,
        wa_message_id: str,
        conn=None,
    ) -> bool:
        try:
            q = """
                UPDATE campaign_recipients
                SET
                    status = 'submitted',
                    wa_message_id = COALESCE(%s, ''),
                    error_message = '',
                    updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s AND status = 'processing'
                RETURNING 1 AS ok
                """
            args = (wa_message_id or "", tenant_id, campaign_id, contact_phone)
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)
            return bool(row)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.transition_processing_to_submitted failed: {e}", level="ERROR")
            return False

    @staticmethod
    async def transition_processing_to_queued(
        tenant_id: str,
        campaign_id: str,
        contact_phone: str,
        error_message: str,
        conn=None,
    ) -> bool:
        try:
            q = """
                UPDATE campaign_recipients
                SET
                    status = 'queued',
                    error_message = COALESCE(%s, ''),
                    updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s AND status = 'processing'
                RETURNING 1 AS ok
                """
            args = (error_message or "", tenant_id, campaign_id, contact_phone)
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)
            return bool(row)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.transition_processing_to_queued failed: {e}", level="ERROR")
            return False

    @staticmethod
    async def transition_submitted_to_queued(
        tenant_id: str,
        campaign_id: str,
        contact_phone: str,
        error_message: str,
        wa_message_id: str = "",
        conn=None,
    ) -> bool:
        try:
            if wa_message_id:
                # Only transition if the current wa_message_id matches — prevents
                # stale webhook callbacks from reverting a successful retry.
                q = """
                    UPDATE campaign_recipients
                    SET
                        status = 'queued',
                        error_message = COALESCE(%s, ''),
                        updated_at = now()
                    WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s
                      AND status = 'submitted' AND wa_message_id = %s
                    RETURNING 1 AS ok
                    """
                args = (error_message or "", tenant_id, campaign_id, contact_phone, wa_message_id)
            else:
                q = """
                    UPDATE campaign_recipients
                    SET
                        status = 'queued',
                        error_message = COALESCE(%s, ''),
                        updated_at = now()
                    WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s AND status = 'submitted'
                    RETURNING 1 AS ok
                    """
                args = (error_message or "", tenant_id, campaign_id, contact_phone)
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)
            return bool(row)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.transition_submitted_to_queued failed: {e}", level="ERROR")
            return False

    @staticmethod
    async def transition_processing_to_sent(
        tenant_id: str,
        campaign_id: str,
        contact_phone: str,
        wa_message_id: str,
        conn=None,
    ) -> bool:
        """processing -> sent"""
        try:
            q = """
                UPDATE campaign_recipients
                SET
                    status = 'sent',
                    wa_message_id = COALESCE(%s, ''),
                    error_message = '',
                    updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s AND status = 'processing'
                RETURNING 1 AS ok
                """
            args = (wa_message_id or "", tenant_id, campaign_id, contact_phone)
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)
            return bool(row)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.transition_processing_to_sent failed: {e}", level="ERROR")
            return False

    @staticmethod
    async def transition_processing_to_failed(
        tenant_id: str,
        campaign_id: str,
        contact_phone: str,
        error_message: str,
        conn=None,
    ) -> bool:
        """processing -> failed"""
        try:
            q = """
                UPDATE campaign_recipients
                SET
                    status = 'failed',
                    error_message = COALESCE(%s, ''),
                    updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s AND status = 'processing'
                RETURNING 1 AS ok
                """
            args = (error_message or "", tenant_id, campaign_id, contact_phone)
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)
            return bool(row)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.transition_processing_to_failed failed: {e}", level="ERROR")
            return False

    @staticmethod
    async def create_batch(campaign_id: str, tenant_id: str, contacts: list[dict], conn=None):
        """Batch-create recipient documents for a campaign.

        Each contact dict should have: phone, name, index, and optional contact_data.
        """
        try:
            now = _ist_now_iso()
            args_list: list[tuple] = []
            for i, contact in enumerate(contacts):
                phone = str(contact.get("phone", "") or "")
                if not phone:
                    continue
                contact_data = {k: v for k, v in contact.items() if k not in ("phone", "name", "index")}
                args_list.append(
                    (
                        tenant_id,
                        campaign_id,
                        phone,
                        str(contact.get("name", "") or ""),
                        json.dumps(contact_data or {}),
                        "pending",
                        "",
                        "",
                        0,
                        int(contact.get("index", i)),
                        now,
                    )
                )

            if not args_list:
                return

            # Insert in chunks to avoid very large executemany payloads
            chunk_size = 1000
            q = (
                """
                INSERT INTO campaign_recipients (
                    tenant_id,
                    campaign_id,
                    contact_phone,
                    contact_name,
                    contact_data,
                    status,
                    wa_message_id,
                    error_message,
                    attempt_count,
                    recipient_index,
                    created_at
                )
                VALUES (%s, %s::uuid, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s::timestamptz)
                ON CONFLICT (tenant_id, campaign_id, contact_phone) DO NOTHING
                """
            )
            for start in range(0, len(args_list), chunk_size):
                batch = args_list[start : start + chunk_size]
                if conn is not None:
                    await executemany_conn(conn, q, batch)
                else:
                    await executemany(q, batch)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.create_batch failed: {e}", level="ERROR")

    @staticmethod
    async def update_status(
        tenant_id: str,
        campaign_id: str,
        contact_phone: str,
        status: str,
        wa_message_id: str = "",
        error_message: str = "",
        conn=None,
    ):
        """Update a single recipient's delivery status."""
        try:
            q = """
                UPDATE campaign_recipients
                SET
                    status = %s,
                    wa_message_id = CASE WHEN %s <> '' THEN %s ELSE wa_message_id END,
                    error_message = CASE WHEN %s <> '' THEN %s ELSE error_message END,
                    attempt_count = attempt_count + 1,
                    last_attempt_at = now(),
                    updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND contact_phone = %s
                """
            args = (
                status,
                wa_message_id or "",
                wa_message_id or "",
                error_message or "",
                error_message or "",
                tenant_id,
                campaign_id,
                contact_phone,
            )
            if conn is not None:
                await execute_conn(conn, q, *args)
            else:
                await execute(q, *args)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.update_status failed: {e}", level="ERROR")

    @staticmethod
    async def get_pending(tenant_id: str, campaign_id: str, limit: int = 10) -> list[dict]:
        """Get pending recipients for a campaign (for resume / distributed processing)."""
        try:
            rows = await fetch(
                """
                SELECT tenant_id, campaign_id, contact_phone, contact_name, contact_data, status, wa_message_id,
                       error_message, attempt_count, recipient_index, created_at, updated_at
                FROM campaign_recipients
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND status = 'pending'
                ORDER BY recipient_index ASC
                LIMIT %s
                """,
                tenant_id,
                campaign_id,
                max(1, min(limit, 1000)),
            )
            return [dict(r) for r in rows]
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.get_pending failed: {e}", level="ERROR")
            return []

    @staticmethod
    async def get_failed(tenant_id: str, campaign_id: str) -> list[dict]:
        """Get failed recipients for retry."""
        try:
            rows = await fetch(
                """
                SELECT tenant_id, campaign_id, contact_phone, contact_name, contact_data, status, wa_message_id,
                       error_message, attempt_count, recipient_index, created_at, updated_at
                FROM campaign_recipients
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND status = 'failed'
                ORDER BY recipient_index ASC
                """,
                tenant_id,
                campaign_id,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.get_failed failed: {e}", level="ERROR")
            return []

    @staticmethod
    async def list_by_campaign(tenant_id: str, campaign_id: str, limit: int = 500) -> list[dict]:
        """Get all recipients for a campaign, ordered by index."""
        try:
            rows = await fetch(
                """
                SELECT tenant_id, campaign_id, contact_phone, contact_name, contact_data, status, wa_message_id,
                       error_message, attempt_count, recipient_index, created_at, updated_at
                FROM campaign_recipients
                WHERE tenant_id = %s AND campaign_id = %s::uuid
                ORDER BY recipient_index ASC
                LIMIT %s
                """,
                tenant_id,
                campaign_id,
                max(1, min(limit, 5000)),
            )
            return [dict(r) for r in rows]
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.list_by_campaign failed: {e}", level="ERROR")
            return []

    @staticmethod
    async def count_done(tenant_id: str, campaign_id: str, max_attempts: int = 5, conn=None) -> int:
        try:
            q = """
                SELECT COUNT(*) AS cnt
                FROM campaign_recipients
                WHERE tenant_id = %s AND campaign_id = %s::uuid
                  AND (
                    status IN ('submitted', 'sent', 'quota_exceeded')
                    OR (status = 'failed' AND attempt_count >= %s)
                  )
                """
            args = (tenant_id, campaign_id, int(max_attempts))
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)
            if not row:
                return 0
            return int(row.get("cnt") or 0)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.count_done failed: {e}", level="ERROR")
            return 0

    @staticmethod
    async def mark_excess_recipients_quota_exceeded(
        tenant_id: str,
        campaign_id: str,
        conn=None,
    ) -> int:
        """Mark all remaining 'pending' recipients as 'quota_exceeded'.

        Called after the capped fan-out has enqueued the allowed batch.
        Returns the number of rows affected.
        """
        try:
            q = """
                UPDATE campaign_recipients
                SET
                    status = 'quota_exceeded',
                    error_message = 'Monthly bulk message quota exhausted',
                    updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid AND status = 'pending'
                """
            if conn is not None:
                result = await execute_conn(conn, q, tenant_id, campaign_id)
            else:
                result = await execute(q, tenant_id, campaign_id)
            # execute returns the command tag string like "UPDATE 5"
            if result and isinstance(result, str):
                parts = result.split()
                if len(parts) >= 2:
                    return int(parts[-1])
            return 0
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.mark_excess_recipients_quota_exceeded failed: {e}", level="ERROR")
            return 0

    @staticmethod
    async def delete_by_campaign(tenant_id: str, campaign_id: str, conn=None):
        """Delete all recipient documents for a campaign."""
        try:
            q = "DELETE FROM campaign_recipients WHERE tenant_id = %s AND campaign_id = %s::uuid"
            if conn is not None:
                await execute_conn(conn, q, tenant_id, campaign_id)
            else:
                await execute(q, tenant_id, campaign_id)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.delete_by_campaign failed: {e}", level="ERROR")


campaign_recipients = _CampaignRecipients()
