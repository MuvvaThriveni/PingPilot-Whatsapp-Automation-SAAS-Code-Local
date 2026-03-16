from __future__ import annotations

"""Postgres operations for the `campaigns` table.

Primary key: (tenant_id, campaign_id).
NO inline counters (sent_count, failed_count) — use campaign_counters instead.
Write frequency: Medium (status updates during send).
"""

import datetime
import uuid
from database import (
    fetchrow,
    fetch,
    execute,
    fetchrow_conn,
    fetch_conn,
    execute_conn,
)
from observability import log_event
from utils.time_utils import get_ist_now_iso, get_ist_now


def _ist_now_iso() -> str:
    return get_ist_now_iso()


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(str(s))
        return True
    except Exception:
        return False


class _Campaigns:

    @staticmethod
    async def create(campaign_id: str, tenant_id: str, data: dict, conn=None):
        """Create a new campaign document."""
        try:
            q = """
                INSERT INTO campaigns (
                    tenant_id,
                    campaign_id,
                    name,
                    template_name,
                    header_image_url,
                    total_contacts,
                    status,
                    delay_ms,
                    scheduled_at,
                    last_processed_index,
                    worker_heartbeat,
                    error_message,
                    sent_count,
                    failed_count,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s::uuid,
                    COALESCE(%s, ''),
                    COALESCE(%s, ''),
                    COALESCE(%s, ''),
                    COALESCE(%s, 0),
                    COALESCE(%s, ''),
                    COALESCE(%s, 1000),
                    %s::timestamptz,
                    %s,
                    %s::timestamptz,
                    COALESCE(%s, ''),
                    0,
                    0,
                    COALESCE(%s::timestamptz, now()),
                    now()
                )
                """
            args = (
                tenant_id,
                campaign_id,
                data.get("name"),
                data.get("template_name"),
                data.get("header_image_url"),
                int(data.get("total_contacts") or 0),
                data.get("status"),
                int(data.get("delay_ms") or 1000),
                data.get("scheduled_at"),
                data.get("last_processed_index"),
                data.get("worker_heartbeat"),
                data.get("error_message"),
                data.get("created_at"),
            )

            if conn is not None:
                await execute_conn(conn, q, *args)
            else:
                await execute(q, *args)
        except Exception as e:
            log_event("db_error", detail=f"campaigns.create({campaign_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def get(tenant_id: str, campaign_id: str, conn=None) -> dict | None:
        try:
            if not _is_uuid(campaign_id):
                return None
            q = "SELECT * FROM campaigns WHERE tenant_id = %s AND campaign_id = %s::uuid"
            if conn is not None:
                row = await fetchrow_conn(conn, q, tenant_id, campaign_id)
            else:
                row = await fetchrow(q, tenant_id, campaign_id)
            return dict(row) if row else None
        except Exception as e:
            log_event("db_error", detail=f"campaigns.get({campaign_id}) failed: {e}", level="ERROR")
            return None

    @staticmethod
    async def update_status(tenant_id: str, campaign_id: str, status: str, conn=None, **extra):
        """Update campaign status and optional extra fields."""
        try:
            q = """
                UPDATE campaigns
                SET
                    status = %s,
                    error_message = COALESCE(%s, error_message),
                    scheduled_at = COALESCE(%s::timestamptz, scheduled_at),
                    updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid
                """
            args = (
                status,
                extra.get("error_message"),
                extra.get("scheduled_at"),
                tenant_id,
                campaign_id,
            )
            if conn is not None:
                await execute_conn(conn, q, *args)
            else:
                await execute(q, *args)
        except Exception as e:
            log_event("db_error", detail=f"campaigns.update_status({campaign_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def update_last_processed(tenant_id: str, campaign_id: str, index: int, conn=None):
        """Track resume point for interrupted campaigns."""
        try:
            q = """
                UPDATE campaigns
                SET last_processed_index = %s, updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid
                """
            args = (int(index), tenant_id, campaign_id)
            if conn is not None:
                await execute_conn(conn, q, *args)
            else:
                await execute(q, *args)
        except Exception as e:
            log_event("db_error", detail=f"campaigns.update_last_processed failed: {e}", level="ERROR")

    @staticmethod
    async def update_heartbeat(tenant_id: str, campaign_id: str, conn=None):
        """Update worker heartbeat timestamp. Called every batch."""
        try:
            q = """
                UPDATE campaigns
                SET worker_heartbeat = now(), updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid
                """
            args = (tenant_id, campaign_id)
            if conn is not None:
                await execute_conn(conn, q, *args)
            else:
                await execute(q, *args)
        except Exception as e:
            log_event("db_error", detail=f"campaigns.update_heartbeat({campaign_id}) failed: {e}", level="ERROR")

    HEARTBEAT_STALE_SECONDS = 120  # campaign considered stuck if no heartbeat for 2 min

    @staticmethod
    def get_stale_running(threshold_seconds: int = 0) -> list[dict]:
        """Find running campaigns whose heartbeat is older than threshold."""
        raise RuntimeError(
            "campaigns.get_stale_running is async after Postgres migration; "
            "use: await campaigns.get_stale_running_async(...)"
        )

    @staticmethod
    async def get_stale_running_async(threshold_seconds: int = 0) -> list[dict]:
        if not threshold_seconds:
            threshold_seconds = _Campaigns.HEARTBEAT_STALE_SECONDS
        try:
            cutoff = get_ist_now() - datetime.timedelta(seconds=threshold_seconds)
            rows = await fetch(
                """
                SELECT *
                FROM campaigns
                WHERE status = 'running' AND (
                    (worker_heartbeat IS NOT NULL AND worker_heartbeat < %s::timestamptz)
                    OR (worker_heartbeat IS NULL AND created_at < %s::timestamptz)
                )
                """,
                cutoff.astimezone(datetime.timezone.utc).isoformat(),
                cutoff.astimezone(datetime.timezone.utc).isoformat(),
            )
            return [dict(r) for r in rows]
        except Exception as e:
            log_event("db_error", detail=f"campaigns.get_stale_running failed: {e}", level="ERROR")
            return []

    @staticmethod
    async def list(tenant_id: str, limit: int = 25, cursor: str = None) -> tuple[list[dict], str | None]:
        """List campaigns with cursor-based pagination. Returns (docs, next_cursor)."""
        limit = max(1, min(limit, 100))
        try:
            where = ["tenant_id = %s"]
            args: list = [tenant_id]

            cursor_created_at: str | None = None
            cursor_campaign_id: str | None = None
            if cursor and "::" in cursor:
                parts = cursor.split("::", 1)
                cursor_created_at = parts[0]
                cursor_campaign_id = parts[1]
            elif cursor:
                cursor_created_at = cursor

            if cursor_created_at and cursor_campaign_id:
                where.append("(created_at, campaign_id) < (%s::timestamptz, %s::uuid)")
                args.append(cursor_created_at)
                args.append(cursor_campaign_id)
            elif cursor_created_at:
                where.append("created_at < %s::timestamptz")
                args.append(cursor_created_at)

            q = (
                "SELECT * FROM campaigns WHERE "
                + " AND ".join(where)
                + " ORDER BY created_at DESC, campaign_id DESC LIMIT "
                + str(limit + 1)
            )
            raw = await fetch(q, *args)
            has_next = len(raw) > limit
            page = raw[:limit]
            docs = [dict(r) for r in page]
            next_cursor = None
            if has_next and page:
                last = page[-1]
                next_cursor = f"{last['created_at'].isoformat()}::{last['campaign_id']}"
            return docs, next_cursor
        except Exception as e:
            log_event("db_error", detail=f"campaigns.list({tenant_id}) failed: {e}", level="ERROR")
            return [], None

    @staticmethod
    async def list_running(tenant_id: str) -> list[dict]:
        """Find campaigns with status 'running' for a tenant."""
        try:
            rows = await fetch(
                "SELECT * FROM campaigns WHERE tenant_id = %s AND status = 'running'",
                tenant_id,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            log_event("db_error", detail=f"campaigns.list_running failed: {e}", level="ERROR")
            return []

    @staticmethod
    async def list_running_global() -> list[dict]:
        """Find all campaigns with status 'running' across all tenants."""
        try:
            rows = await fetch("SELECT * FROM campaigns WHERE status = 'running'")
            return [dict(r) for r in rows]
        except Exception as e:
            log_event("db_error", detail=f"campaigns.list_running_global failed: {e}", level="ERROR")
            return []

    @staticmethod
    async def get_due_scheduled(tenant_id: str) -> list[dict]:
        """Find scheduled campaigns for a tenant whose scheduled_at time has passed."""
        try:
            rows = await fetch(
                """
                SELECT *
                FROM campaigns
                WHERE tenant_id = %s AND status = 'scheduled' AND scheduled_at IS NOT NULL AND scheduled_at <= now()
                ORDER BY scheduled_at ASC
                """
                ,
                tenant_id,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            log_event("db_error", detail=f"campaigns.get_due_scheduled failed: {e}", level="ERROR")
            return []

    @staticmethod
    async def get_due_scheduled_global() -> list[dict]:
        """Find campaigns with status 'scheduled' whose scheduled_at time has passed across all tenants."""
        try:
            rows = await fetch(
                """
                SELECT *
                FROM campaigns
                WHERE status = 'scheduled' AND scheduled_at IS NOT NULL AND scheduled_at <= now()
                ORDER BY scheduled_at ASC
                """
            )
            return [dict(r) for r in rows]
        except Exception as e:
            log_event("db_error", detail=f"campaigns.get_due_scheduled_global failed: {e}", level="ERROR")
            return []

    @staticmethod
    async def delete(tenant_id: str, campaign_id: str):
        try:
            await execute(
                "DELETE FROM campaigns WHERE tenant_id = %s AND campaign_id = %s::uuid",
                tenant_id,
                campaign_id,
            )
        except Exception as e:
            log_event("db_error", detail=f"campaigns.delete({campaign_id}) failed: {e}", level="ERROR")


campaigns = _Campaigns()
