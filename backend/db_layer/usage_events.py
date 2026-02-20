from __future__ import annotations

"""Firestore operations for the `usage_events` collection (Phase-5: optimized).

Append-only raw usage metering for billing and analytics.
Doc ID: auto-generated.
Write frequency: HIGH.

Optimizations:
- get_monthly() limited to 500 docs max
- count_monthly() cached for 300 s (5 minutes)
"""

import datetime
from firebase_config import get_db
from cache import cache


def _col():
    db = get_db()
    return db.collection("usage_events") if db else None


class _UsageEvents:

    @staticmethod
    def record(tenant_id: str, event_type: str, product_type: str = "",
               campaign_id: str = "", contact_phone: str = "",
               billable: bool = True):
        """Record a single usage event. Minimal document size."""
        col = _col()
        if not col:
            return
        try:
            now = datetime.datetime.utcnow()
            doc = {
                "tenant_id": tenant_id,
                "event_type": event_type,
                "month_key": now.strftime("%Y-%m"),
                "created_at": now.isoformat(),
            }
            # Optional fields — only include if present
            if product_type:
                doc["product_type"] = product_type
            if campaign_id:
                doc["campaign_id"] = campaign_id
            if contact_phone:
                doc["contact_phone"] = contact_phone
            if not billable:
                doc["billable"] = False  # Default is billable, so only store False

            col.add(doc)
        except Exception as e:
            print(f"[db_layer.usage_events] record failed: {e}")

    @staticmethod
    def get_monthly(tenant_id: str, month_key: str = "", limit: int = 500) -> list[dict]:
        """Get usage events for a specific month. Hard-capped at 500."""
        limit = max(1, min(limit, 500))
        col = _col()
        if not col:
            return []
        if not month_key:
            month_key = datetime.datetime.utcnow().strftime("%Y-%m")
        try:
            docs = (
                col.where("tenant_id", "==", tenant_id)
                .where("month_key", "==", month_key)
                .limit(limit)
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"[db_layer.usage_events] get_monthly failed: {e}")
            return []

    @staticmethod
    def count_monthly(tenant_id: str, month_key: str = "") -> dict:
        """Count usage events by type for a month. Cached for 300 s."""
        if not month_key:
            month_key = datetime.datetime.utcnow().strftime("%Y-%m")
        cache_key = f"usage_count:{tenant_id}:{month_key}"

        def _fetch():
            events = _UsageEvents.get_monthly(tenant_id, month_key)
            counts = {}
            for e in events:
                et = e.get("event_type", "unknown")
                counts[et] = counts.get(et, 0) + 1
            return counts

        return cache.get_or_fetch(cache_key, _fetch, ttl=300.0)


usage_events = _UsageEvents()
