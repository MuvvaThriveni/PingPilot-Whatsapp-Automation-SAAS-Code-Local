from __future__ import annotations

"""Firestore operations for the `webhook_events` collection.

Doc ID: deterministic hash for deduplication.
Stores raw webhook payloads for audit and idempotent processing.
Write frequency: HIGH.
"""

import hashlib
import datetime
from firebase_config import get_db


def _col():
    db = get_db()
    return db.collection("webhook_events") if db else None


def _make_doc_id(wa_message_id: str, event_type: str) -> str:
    """Deterministic doc ID for deduplication."""
    raw = f"{wa_message_id}:{event_type}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class _WebhookEvents:

    @staticmethod
    def exists(wa_message_id: str, event_type: str = "message") -> bool:
        """Check if this webhook event was already processed (idempotency)."""
        col = _col()
        if not col:
            return False
        try:
            doc_id = _make_doc_id(wa_message_id, event_type)
            doc = col.document(doc_id).get()
            if doc.exists:
                data = doc.to_dict()
                return data.get("processed", False)
            return False
        except Exception as e:
            print(f"[db_layer.webhook_events] exists check failed: {e}")
            return False

    @staticmethod
    def record(tenant_id: str, wa_message_id: str, event_type: str,
               sender_phone: str = "", status: str = "",
               raw_payload: dict = None, processed: bool = False):
        """Record a webhook event. Uses deterministic ID for dedup."""
        col = _col()
        if not col:
            return
        try:
            doc_id = _make_doc_id(wa_message_id, event_type)
            data = {
                "tenant_id": tenant_id,
                "event_type": event_type,
                "wa_message_id": wa_message_id,
                "sender_phone": sender_phone,
                "status": status,
                "processed": processed,
                "created_at": datetime.datetime.utcnow().isoformat(),
            }
            if raw_payload:
                # Truncate to avoid exceeding 1MB doc limit
                import json
                payload_str = json.dumps(raw_payload)
                if len(payload_str) > 50000:
                    data["raw_payload_truncated"] = True
                    data["raw_payload"] = json.loads(payload_str[:50000])
                else:
                    data["raw_payload"] = raw_payload
            col.document(doc_id).set(data)
        except Exception as e:
            print(f"[db_layer.webhook_events] record failed: {e}")

    @staticmethod
    def mark_processed(wa_message_id: str, event_type: str = "message"):
        """Mark a webhook event as processed."""
        col = _col()
        if not col:
            return
        try:
            doc_id = _make_doc_id(wa_message_id, event_type)
            col.document(doc_id).update({
                "processed": True,
                "processed_at": datetime.datetime.utcnow().isoformat(),
            })
        except Exception as e:
            print(f"[db_layer.webhook_events] mark_processed failed: {e}")

    @staticmethod
    def get_unprocessed(tenant_id: str, limit: int = 100) -> list[dict]:
        """Get unprocessed events for reprocessing."""
        col = _col()
        if not col:
            return []
        try:
            docs = (
                col.where("tenant_id", "==", tenant_id)
                .where("processed", "==", False)
                .limit(limit)
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"[db_layer.webhook_events] get_unprocessed failed: {e}")
            return []


webhook_events = _WebhookEvents()
