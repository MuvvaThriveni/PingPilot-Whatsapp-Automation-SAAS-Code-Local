from __future__ import annotations

"""Firestore operations for the `usage_events` collection.

Append-only raw usage metering for billing and analytics.
Doc ID: auto-generated.
Write frequency: HIGH.
"""

import datetime
from firebase_config import get_db


def _col():
    db = get_db()
    return db.collection("usage_events") if db else None


class _UsageEvents:

    @staticmethod
    def record(tenant_id: str, event_type: str, product_type: str = "",
               campaign_id: str = "", contact_phone: str = "",
               billable: bool = True):
        """Record a single usage event."""
        col = _col()
        if not col:
            return
        try:
            now = datetime.datetime.utcnow()
            col.add({
                "tenant_id": tenant_id,
                "event_type": event_type,
                "product_type": product_type,
                "campaign_id": campaign_id,
                "contact_phone": contact_phone,
                "billable": billable,
                "month_key": now.strftime("%Y-%m"),
                "created_at": now.isoformat(),
            })
        except Exception as e:
            print(f"[db_layer.usage_events] record failed: {e}")

    @staticmethod
    def get_monthly(tenant_id: str, month_key: str = "") -> list[dict]:
        """Get usage events for a specific month."""
        col = _col()
        if not col:
            return []
        if not month_key:
            month_key = datetime.datetime.utcnow().strftime("%Y-%m")
        try:
            docs = (
                col.where("tenant_id", "==", tenant_id)
                .where("month_key", "==", month_key)
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"[db_layer.usage_events] get_monthly failed: {e}")
            return []

    @staticmethod
    def count_monthly(tenant_id: str, month_key: str = "") -> dict:
        """Count usage events by type for a month."""
        events = _UsageEvents.get_monthly(tenant_id, month_key)
        counts = {}
        for e in events:
            et = e.get("event_type", "unknown")
            counts[et] = counts.get(et, 0) + 1
        return counts


usage_events = _UsageEvents()
