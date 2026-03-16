from __future__ import annotations

"""Postgres operations for the `webhook_events` table (Phase-5: optimized).

Deduplication and auditing for incoming webhooks.
Primary key: (tenant_id, event_id).
Write frequency: Medium.

Optimizations:
- Cached exists check for recent events
"""

import datetime
from database import fetchrow, fetch, execute
from cache import cache
from observability import log_event
from utils.time_utils import get_ist_now_iso


def _ist_now_iso() -> str:
    return get_ist_now_iso()


class _WebhookEvents:

    @staticmethod
    async def exists(tenant_id: str, event_id: str) -> bool:
        """Check if an event has already been processed. Cached for 1 hour."""
        cache_key = f"webhook_exists:{tenant_id}:{event_id}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            row = await fetchrow(
                "SELECT 1 AS ok FROM webhook_events WHERE tenant_id = %s AND event_id = %s",
                tenant_id,
                event_id,
            )
            result = bool(row)
            cache.set(cache_key, result, ttl=3600.0)
            return result
        except Exception as e:
            log_event("db_error", detail=f"webhook_events.exists({event_id}) failed: {e}", level="ERROR")
            return False

    @staticmethod
    async def record(event_id: str, tenant_id: str, data: dict):
        """Record a new webhook event for deduplication."""
        try:
            await execute(
                """
                INSERT INTO webhook_events (tenant_id, event_id, event_type, status, created_at, processed_at)
                VALUES (%s, %s, %s, 'received', COALESCE(%s::timestamptz, now()), NULL)
                ON CONFLICT (tenant_id, event_id) DO NOTHING
                """,
                tenant_id,
                event_id,
                data.get("event_type", ""),
                data.get("created_at") or None,
            )
            # Mark as existing in memory cache for 1 hour (Requirement 8/10)
            cache.set(f"webhook_exists:{tenant_id}:{event_id}", True, ttl=3600.0)
        except Exception as e:
            log_event("db_error", detail=f"webhook_events.record({event_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def mark_processed(tenant_id: str, event_id: str):
        """Mark a webhook event as processed."""
        try:
            await execute(
                """
                UPDATE webhook_events
                SET status = 'processed', processed_at = COALESCE(%s::timestamptz, now())
                WHERE tenant_id = %s AND event_id = %s
                """,
                _ist_now_iso(),
                tenant_id,
                event_id,
            )
            # Ensure it stays in cache as 'True'
            cache.set(f"webhook_exists:{tenant_id}:{event_id}", True, ttl=3600.0)
        except Exception as e:
            log_event("db_error", detail=f"webhook_events.mark_processed({event_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def get_unprocessed(tenant_id: str, limit: int = 50) -> list[dict]:
        """Get unprocessed events. Hard-capped at 100."""
        limit = max(1, min(limit, 100))
        try:
            rows = await fetch(
                """
                SELECT tenant_id, event_id, event_type, status, created_at, processed_at
                FROM webhook_events
                WHERE tenant_id = %s AND status = 'received'
                ORDER BY created_at ASC
                LIMIT %s
                """,
                tenant_id,
                limit,
            )
            results: list[dict] = []
            for r in rows:
                d = dict(r)
                d["_doc_id"] = d.get("event_id")
                results.append(d)
            return results
        except Exception as e:
            log_event("db_error", detail=f"webhook_events.get_unprocessed failed: {e}", level="ERROR")
            return []


webhook_events = _WebhookEvents()
