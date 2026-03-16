from __future__ import annotations

"""Postgres operations for the `usage_events` table (Phase-5: optimized).

Append-only raw usage metering for billing and analytics.
Write frequency: HIGH.

Optimizations:
- get_monthly() limited to 500 rows max
- count_monthly() cached for 300 s (5 minutes)
"""

import datetime
from database import fetch, fetchrow
from cache import cache, fetch_cached_async
from observability import log_event
from utils.time_utils import get_ist_now

class _UsageEvents:

    @staticmethod
    async def record(tenant_id: str, event_type: str, product_type: str = "",
                     campaign_id: str = "", contact_phone: str = "",
                     billable: bool = True):
        """Record a single usage event. Minimal document size."""
        try:
            now = get_ist_now()
            month_key = now.strftime("%Y-%m")
            await fetchrow(
                """
                INSERT INTO usage_events (
                    tenant_id,
                    event_type,
                    product_type,
                    campaign_id,
                    contact_phone,
                    billable,
                    month_key,
                    created_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s, COALESCE(%s::timestamptz, now()))
                RETURNING id
                """,
                tenant_id,
                event_type,
                product_type or "",
                campaign_id or None,
                contact_phone or "",
                bool(billable),
                month_key,
                now.astimezone(datetime.timezone.utc).isoformat(),
            )
        except Exception as e:
            log_event("db_error", detail=f"usage_events.record failed: {e}", level="ERROR")

    @staticmethod
    async def get_monthly(tenant_id: str, month_key: str = "", limit: int = 500) -> list[dict]:
        """Get usage events for a specific month. Hard-capped at 500."""
        limit = max(1, min(limit, 500))
        if not month_key:
            month_key = get_ist_now().strftime("%Y-%m")
        try:
            rows = await fetch(
                """
                SELECT tenant_id, event_type, product_type, campaign_id, contact_phone, billable, month_key, created_at
                FROM usage_events
                WHERE tenant_id = %s AND month_key = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                tenant_id,
                month_key,
                limit,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            log_event("db_error", detail=f"usage_events.get_monthly failed: {e}", level="ERROR")
            return []

    @staticmethod
    async def count_monthly(tenant_id: str, month_key: str = "") -> dict:
        """Count usage events by type for a month. Cached for 300 s."""
        if not month_key:
            month_key = get_ist_now().strftime("%Y-%m")
        cache_key = f"usage_count:{tenant_id}:{month_key}"

        async def _fetch():
            rows = await fetch(
                """
                SELECT event_type, COUNT(*) AS c
                FROM usage_events
                WHERE tenant_id = %s AND month_key = %s
                GROUP BY event_type
                """,
                tenant_id,
                month_key,
            )
            counts: dict = {}
            for r in rows:
                d = dict(r)
                counts[d.get("event_type") or "unknown"] = int(d.get("c") or 0)
            return counts

        return await fetch_cached_async(cache_key, _fetch, ttl=300.0)


usage_events = _UsageEvents()
