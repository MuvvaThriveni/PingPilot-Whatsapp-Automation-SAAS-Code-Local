from __future__ import annotations

"""Postgres operations for the `messages` table (Phase-5: optimized).

Single source of truth for ALL message history (chatbot, bulk, file-forward).
No secondary summary documents — query this collection directly.

Primary key: (tenant_id, id).
Write frequency: HIGH.

Optimizations:
- Reduced scan limits in get_usage() from 2000 → 500
- Reduced scan limits in get_stats() from 1000 → 500
- Capped all query limits strictly
- Minimized stored document size
"""

import datetime
from database import fetchrow, fetch, execute, fetchrow_conn
from cache import cache, usage_key, wa_message_mapping_key, fetch_cached_async
from observability import log_event
from utils.time_utils import get_ist_now_iso, get_ist_now


def _ist_now_iso() -> str:
    return get_ist_now_iso()


class _Messages:

    @staticmethod
    async def add(tenant_id: str, data: dict, conn=None) -> str | None:
        """Insert a single message row. Returns the inserted row ID as string."""
        try:
            created_at = data.get("created_at")
            q = """
                INSERT INTO messages (
                    tenant_id,
                    direction,
                    product_type,
                    contact_phone,
                    contact_name,
                    message_text,
                    message_type,
                    wa_message_id,
                    status,
                    template_name,
                    campaign_id,
                    media_id,
                    error_message,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    COALESCE(%s::timestamptz, now()),
                    now()
                )
                RETURNING id
                """
            args = (
                tenant_id,
                data.get("direction", ""),
                data.get("product_type", ""),
                data.get("contact_phone", ""),
                data.get("contact_name", ""),
                data.get("message_text", ""),
                data.get("message_type", "text"),
                data.get("wa_message_id", ""),
                data.get("status", ""),
                data.get("template_name", ""),
                data.get("campaign_id") or None,
                data.get("media_id", ""),
                data.get("error_message", ""),
                created_at if created_at else None,
            )
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)

            inserted_id = str(row["id"]) if row and row.get("id") is not None else None

            wa_id = data.get("wa_message_id")
            if wa_id and inserted_id:
                cache.set(wa_message_mapping_key(wa_id, tenant_id), inserted_id, ttl=3600 * 24)

            cache.invalidate(usage_key(tenant_id))
            return inserted_id
        except Exception as e:
            log_event("db_error", detail=f"messages.add failed: {e}", level="ERROR")
            return None

    @staticmethod
    async def get_latest_outgoing_for_campaign_recipient(
        tenant_id: str,
        campaign_id: str,
        contact_phone: str,
        conn=None,
    ) -> dict | None:
        try:
            q = """
                SELECT id, wa_message_id, status, created_at
                FROM messages
                WHERE tenant_id = %s
                  AND campaign_id = %s::uuid
                  AND contact_phone = %s
                  AND direction = 'outgoing'
                  AND status IN ('submitted', 'sent', 'delivered', 'read')
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            args = (tenant_id, campaign_id, contact_phone)
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)
            return dict(row) if row else None
        except Exception as e:
            log_event("db_error", detail=f"messages.get_latest_outgoing_for_campaign_recipient failed: {e}", level="ERROR")
            return None

    @staticmethod
    async def add_idempotent(tenant_id: str, data: dict, conn=None) -> str | None:
        """Insert a message row but ignore any unique conflicts.

        Intended for worker finalization where retries must not create duplicate rows.
        """
        try:
            created_at = data.get("created_at")
            q = """
                INSERT INTO messages (
                    tenant_id,
                    direction,
                    product_type,
                    contact_phone,
                    contact_name,
                    message_text,
                    message_type,
                    wa_message_id,
                    status,
                    template_name,
                    campaign_id,
                    media_id,
                    error_message,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    COALESCE(%s::timestamptz, now()),
                    now()
                )
                ON CONFLICT DO NOTHING
                RETURNING id
                """
            args = (
                tenant_id,
                data.get("direction", ""),
                data.get("product_type", ""),
                data.get("contact_phone", ""),
                data.get("contact_name", ""),
                data.get("message_text", ""),
                data.get("message_type", "text"),
                data.get("wa_message_id", ""),
                data.get("status", ""),
                data.get("template_name", ""),
                data.get("campaign_id") or None,
                data.get("media_id", ""),
                data.get("error_message", ""),
                created_at if created_at else None,
            )
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)

            inserted_id = str(row["id"]) if row and row.get("id") is not None else None

            wa_id = data.get("wa_message_id")
            if wa_id and inserted_id:
                cache.set(wa_message_mapping_key(wa_id, tenant_id), inserted_id, ttl=3600 * 24)

            cache.invalidate(usage_key(tenant_id))
            return inserted_id
        except Exception as e:
            log_event("db_error", detail=f"messages.add_idempotent failed: {e}", level="ERROR")
            return None

    @staticmethod
    async def get_sent_for_campaign_recipient(tenant_id: str, campaign_id: str, contact_phone: str, conn=None) -> dict | None:
        """Fetch an existing sent outgoing message for a campaign+recipient if present."""
        try:
            q = """
                SELECT id, wa_message_id, created_at
                FROM messages
                WHERE tenant_id = %s
                  AND campaign_id = %s::uuid
                  AND contact_phone = %s
                  AND direction = 'outgoing'
                  AND status IN ('submitted', 'sent', 'delivered', 'read')
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            if conn is not None:
                row = await fetchrow_conn(conn, q, tenant_id, campaign_id, contact_phone)
            else:
                row = await fetchrow(q, tenant_id, campaign_id, contact_phone)
            return dict(row) if row else None
        except Exception as e:
            log_event("db_error", detail=f"messages.get_sent_for_campaign_recipient failed: {e}", level="ERROR")
            return None

    @staticmethod
    async def get_outgoing_by_wa_message_id(tenant_id: str, wa_message_id: str, conn=None) -> dict | None:
        """Fetch outgoing message row by WhatsApp message ID."""
        try:
            q = """
                SELECT id, tenant_id, campaign_id, contact_phone, status, template_name, created_at
                FROM messages
                WHERE tenant_id = %s
                  AND wa_message_id = %s
                  AND direction = 'outgoing'
                LIMIT 1
                """
            args = (tenant_id, wa_message_id)
            if conn is not None:
                row = await fetchrow_conn(conn, q, *args)
            else:
                row = await fetchrow(q, *args)
            return dict(row) if row else None
        except Exception as e:
            log_event("db_error", detail=f"messages.get_outgoing_by_wa_message_id failed: {e}", level="ERROR")
            return None

    @staticmethod
    async def add_batch(tenant_id: str, items: list[dict]):
        """Insert multiple message rows."""
        try:
            for data in items:
                await _Messages.add(tenant_id, data)
            cache.invalidate(usage_key(tenant_id))
        except Exception as e:
            log_event("db_error", detail=f"messages.add_batch failed: {e}", level="ERROR")

    @staticmethod
    async def update_status(wa_message_id: str, status: str, tenant_id: str = ""):
        """Update message status by WhatsApp message ID (webhook callback). Cached-fallback."""
        try:
            if not tenant_id:
                return

            # Try to use memory mapping first (Requirement 2: .document() direct access)
            doc_id = None
            doc_id = cache.get(wa_message_mapping_key(wa_message_id, tenant_id))
            if not doc_id:
                # Backward compat (pre-tenant-scoped cache keys)
                doc_id = cache.get(wa_message_mapping_key(wa_message_id))
            if doc_id:
                await execute(
                    """
                    UPDATE messages
                    SET status = %s, updated_at = now()
                    WHERE tenant_id = %s AND id = %s
                    """,
                    status,
                    tenant_id,
                    int(doc_id),
                )
                return

            row = await fetchrow(
                """
                UPDATE messages
                SET status = %s, updated_at = now()
                WHERE tenant_id = %s AND wa_message_id = %s
                RETURNING id
                """,
                status,
                tenant_id,
                wa_message_id,
            )
            if row and row.get("id") is not None:
                cache.set(wa_message_mapping_key(wa_message_id, tenant_id), str(row["id"]), ttl=3600 * 24)
        except Exception as e:
            log_event("db_error", detail=f"messages.update_status({wa_message_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def list(tenant_id: str, product_type: str = None, status: str = None,
                   limit: int = 25, cursor: str = None) -> tuple[list[dict], str | None]:
        """Query messages with optional filters and cursor-based pagination.

        Returns (docs, next_cursor).  next_cursor is None when no more pages.
        limit is clamped to [1, 100].
        """
        limit = max(1, min(limit, 100))
        try:
            where = ["tenant_id = %s"]
            args: list = [tenant_id]
            if product_type:
                args.append(product_type)
                where.append("product_type = %s")
            if status:
                args.append(status)
                where.append("status = %s")

            cursor_created_at: str | None = None
            cursor_id: int | None = None
            if cursor and "::" in cursor:
                parts = cursor.split("::", 1)
                cursor_created_at = parts[0]
                try:
                    cursor_id = int(parts[1])
                except ValueError:
                    cursor_id = None
            elif cursor:
                cursor_created_at = cursor

            if cursor_created_at and cursor_id is not None:
                args.append(cursor_created_at)
                args.append(cursor_id)
                where.append("(created_at, id) < (%s::timestamptz, %s)")
            elif cursor_created_at:
                args.append(cursor_created_at)
                where.append("created_at < %s::timestamptz")

            q = (
                "SELECT * FROM messages WHERE "
                + " AND ".join(where)
                + " ORDER BY created_at DESC, id DESC LIMIT "
                + str(limit + 1)
            )
            raw = await fetch(q, *args)
            has_next = len(raw) > limit
            page = raw[:limit]
            docs = [dict(r) for r in page]
            next_cursor = None
            if has_next and page:
                last = page[-1]
                next_cursor = f"{last['created_at'].isoformat()}::{last['id']}"
            return docs, next_cursor
        except Exception as e:
            log_event("db_error", detail=f"messages.list failed: {e}", level="ERROR")
            return [], None

    @staticmethod
    async def list_by_campaign(tenant_id: str, campaign_id: str, limit: int = 500) -> list[dict]:
        """Get all messages for a specific campaign."""
        limit = max(1, min(limit, 500))
        try:
            rows = await fetch(
                """
                SELECT *
                FROM messages
                WHERE tenant_id = %s AND campaign_id = %s
                ORDER BY created_at ASC, id ASC
                LIMIT %s
                """,
                tenant_id,
                campaign_id,
                limit,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            log_event("db_error", detail=f"messages.list_by_campaign failed: {e}", level="ERROR")
            return []

    @staticmethod
    async def get_stats(tenant_id: str) -> dict:
        """Compute basic stats. Cached for 6 hours."""
        cache_key = f"msg_stats:{tenant_id}"

        async def _fetch():
            try:
                rows = await fetch(
                    """
                    SELECT product_type, status
                    FROM messages
                    WHERE tenant_id = %s
                    ORDER BY created_at DESC
                    LIMIT 500
                    """,
                    tenant_id,
                )
                stats = {}
                for r in rows:
                    d = dict(r)
                    key = f"{d.get('product_type', 'unknown')}_{d.get('status', 'unknown')}"
                    stats[key] = stats.get(key, 0) + 1
                return stats
            except Exception as e:
                log_event("db_error", detail=f"messages.get_stats failed: {e}", level="ERROR")
                return {}

        return await fetch_cached_async(cache_key, _fetch)

    @staticmethod
    async def get_usage(tenant_id: str) -> dict:
        """Get usage statistics for dashboard. Cached for 6 hours."""
        async def _fetch():
            try:
                now_ist = get_ist_now()
                today_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
                month_start_ist = now_ist.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

                today_row = await fetchrow(
                    """
                    SELECT
                      COUNT(*) AS total,
                      COUNT(*) FILTER (WHERE status IN ('sent','delivered','read')) AS successful,
                      COUNT(*) FILTER (WHERE status = 'failed') AS failed
                    FROM messages
                    WHERE tenant_id = %s AND created_at >= %s::timestamptz
                    """,
                    tenant_id,
                    today_start_ist.astimezone(datetime.timezone.utc).isoformat(),
                )
                month_row = await fetchrow(
                    """
                    SELECT
                      COUNT(*) AS total,
                      COUNT(*) FILTER (WHERE status IN ('sent','delivered','read')) AS successful,
                      COUNT(*) FILTER (WHERE status = 'failed') AS failed
                    FROM messages
                    WHERE tenant_id = %s AND created_at >= %s::timestamptz
                    """,
                    tenant_id,
                    month_start_ist.astimezone(datetime.timezone.utc).isoformat(),
                )
                return {
                    "today": {
                        "total": int((today_row or {}).get("total") or 0),
                        "successful": int((today_row or {}).get("successful") or 0),
                        "failed": int((today_row or {}).get("failed") or 0),
                    },
                    "month": {
                        "total": int((month_row or {}).get("total") or 0),
                        "successful": int((month_row or {}).get("successful") or 0),
                        "failed": int((month_row or {}).get("failed") or 0),
                    },
                    "byProduct": [],
                }
            except Exception as e:
                log_event("db_error", detail=f"messages.get_usage failed: {e}", level="ERROR")
                return {"today": {"total": 0, "successful": 0, "failed": 0},
                        "month": {"total": 0, "successful": 0, "failed": 0},
                        "byProduct": []}

        return await fetch_cached_async(usage_key(tenant_id), _fetch)


messages = _Messages()
