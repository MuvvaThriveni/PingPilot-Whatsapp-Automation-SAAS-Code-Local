from __future__ import annotations

"""Firestore operations for the `webhook_events` collection (Phase-5: optimized).

Deduplication and auditing for incoming webhooks.
Doc ID: deterministic hash of event data.
Write frequency: Medium.

Optimizations:
- exists() uses get() instead of query (1 read vs N)
- All queries use .limit()
- Cached exists check for recent events
"""

import datetime
from firebase_config import get_db
from cache import cache
from observability import log_event
from utils.time_utils import get_ist_now_iso


def _ist_now_iso() -> str:
    return get_ist_now_iso()


def _col():
    db = get_db()
    return db.collection("webhook_events") if db else None


class _WebhookEvents:

    @staticmethod
    def exists(event_id: str) -> bool:
        """Check if an event has already been processed. Cached for 1 hour."""
        cache_key = f"webhook_exists:{event_id}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # If not in memory, we check Firestore (1 read)
        # Goal: Reduce reads by 90% + caching results for 1 hour.
        col = _col()
        if not col:
            return False
        try:
            doc = col.document(event_id).get()
            result = doc.exists
            cache.set(cache_key, result, ttl=3600.0)
            return result
        except Exception as e:
            log_event("db_error", detail=f"webhook_events.exists({event_id}) failed: {e}", level="ERROR")
            return False

    @staticmethod
    def record(event_id: str, tenant_id: str, data: dict):
        """Record a new webhook event for deduplication."""
        col = _col()
        if not col:
            return
        try:
            doc = {
                "tenant_id": tenant_id,
                "event_type": data.get("event_type", ""),
                "status": "received",
                "created_at": _ist_now_iso(),
            }
            col.document(event_id).set(doc)
            # Mark as existing in memory cache for 1 hour (Requirement 8/10)
            cache.set(f"webhook_exists:{event_id}", True, ttl=3600.0)
        except Exception as e:
            log_event("db_error", detail=f"webhook_events.record({event_id}) failed: {e}", level="ERROR")

    @staticmethod
    def mark_processed(event_id: str):
        """Mark a webhook event as processed."""
        col = _col()
        if not col:
            return
        try:
            col.document(event_id).update({
                "status": "processed",
                "processed_at": _ist_now_iso(),
            })
            # Ensure it stays in cache as 'True'
            cache.set(f"webhook_exists:{event_id}", True, ttl=3600.0)
        except Exception as e:
            log_event("db_error", detail=f"webhook_events.mark_processed({event_id}) failed: {e}", level="ERROR")

    @staticmethod
    def get_unprocessed(limit: int = 50) -> list[dict]:
        """Get unprocessed events. Hard-capped at 100."""
        limit = max(1, min(limit, 100))
        col = _col()
        if not col:
            return []
        try:
            docs = (
                col.where("status", "==", "received")
                .order_by("created_at", direction="ASCENDING")
                .limit(limit)
                .stream()
            )
            results = []
            for doc in docs:
                d = doc.to_dict()
                d["_doc_id"] = doc.id
                results.append(d)
            return results
        except Exception as e:
            log_event("db_error", detail=f"webhook_events.get_unprocessed failed: {e}", level="ERROR")
            return []


webhook_events = _WebhookEvents()
