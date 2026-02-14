from __future__ import annotations

"""Firestore operations for the `messages` collection.

Single source of truth for ALL message history (chatbot, bulk, file-forward).
No secondary summary documents — query this collection directly.

Doc ID: auto-generated.
Write frequency: HIGH.
"""

import datetime
from firebase_config import get_db


def _col():
    db = get_db()
    return db.collection("messages") if db else None


class _Messages:

    @staticmethod
    def add(tenant_id: str, data: dict) -> str | None:
        """Insert a single message document. Returns the auto-generated doc ID."""
        col = _col()
        if not col:
            return None
        try:
            data["tenant_id"] = tenant_id
            if "created_at" not in data:
                data["created_at"] = datetime.datetime.utcnow().isoformat()
            _, doc_ref = col.add(data)
            return doc_ref.id
        except Exception as e:
            print(f"[db_layer.messages] add failed: {e}")
            return None

    @staticmethod
    def add_batch(tenant_id: str, items: list[dict]):
        """Batch-write multiple message documents (up to 500 per batch)."""
        db = get_db()
        if not db:
            return
        col = db.collection("messages")
        try:
            batch = db.batch()
            now = datetime.datetime.utcnow().isoformat()
            for i, data in enumerate(items):
                data["tenant_id"] = tenant_id
                if "created_at" not in data:
                    data["created_at"] = now
                ref = col.document()  # auto-ID
                batch.set(ref, data)
                # Firestore batch limit is 500
                if (i + 1) % 500 == 0:
                    batch.commit()
                    batch = db.batch()
            batch.commit()
        except Exception as e:
            print(f"[db_layer.messages] add_batch failed: {e}")

    @staticmethod
    def update_status(wa_message_id: str, status: str, tenant_id: str = ""):
        """Update message status by WhatsApp message ID (webhook status callback)."""
        col = _col()
        if not col:
            return
        try:
            query = col.where("wa_message_id", "==", wa_message_id)
            if tenant_id:
                query = query.where("tenant_id", "==", tenant_id)
            query = query.limit(1)
            for doc in query.stream():
                doc.reference.update({
                    "status": status,
                    "updated_at": datetime.datetime.utcnow().isoformat(),
                })
                return
        except Exception as e:
            print(f"[db_layer.messages] update_status({wa_message_id}) failed: {e}")

    @staticmethod
    def list(tenant_id: str, product_type: str = None, status: str = None,
             limit: int = 25, cursor: str = None) -> tuple[list[dict], str | None]:
        """Query messages with optional filters and cursor-based pagination.

        Returns (docs, next_cursor).  next_cursor is None when no more pages.
        limit is clamped to [1, 100].
        """
        limit = max(1, min(limit, 100))
        col = _col()
        if not col:
            return [], None
        try:
            query = col.where("tenant_id", "==", tenant_id)
            if product_type:
                query = query.where("product_type", "==", product_type)
            if status:
                query = query.where("status", "==", status)
            query = query.order_by("created_at", direction="DESCENDING")

            if cursor:
                # cursor is the created_at value of the last doc in previous page
                query = query.start_after({"created_at": cursor})

            # Fetch limit+1 to detect if there's a next page
            raw = list(query.limit(limit + 1).stream())
            has_next = len(raw) > limit
            docs = [doc.to_dict() for doc in raw[:limit]]
            next_cursor = docs[-1]["created_at"] if has_next and docs else None
            return docs, next_cursor
        except Exception as e:
            print(f"[db_layer.messages] list failed: {e}")
            return [], None

    @staticmethod
    def list_by_campaign(tenant_id: str, campaign_id: str, limit: int = 500) -> list[dict]:
        """Get all messages for a specific campaign."""
        col = _col()
        if not col:
            return []
        try:
            docs = (
                col.where("tenant_id", "==", tenant_id)
                .where("campaign_id", "==", campaign_id)
                .order_by("created_at", direction="ASCENDING")
                .limit(limit)
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"[db_layer.messages] list_by_campaign failed: {e}")
            return []

    @staticmethod
    def get_stats(tenant_id: str) -> dict:
        """Compute basic stats by scanning recent messages.

        For production scale, replace with usage_events aggregation.
        """
        col = _col()
        if not col:
            return {}
        try:
            today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            docs = (
                col.where("tenant_id", "==", tenant_id)
                .order_by("created_at", direction="DESCENDING")
                .limit(1000)
                .stream()
            )
            stats = {}
            for doc in docs:
                d = doc.to_dict()
                key = f"{d.get('product_type', 'unknown')}_{d.get('status', 'unknown')}"
                stats[key] = stats.get(key, 0) + 1
            return stats
        except Exception as e:
            print(f"[db_layer.messages] get_stats failed: {e}")
            return {}

    @staticmethod
    def get_usage(tenant_id: str) -> dict:
        """Get usage statistics for dashboard (today + month)."""
        col = _col()
        if not col:
            return {"today": {"total": 0, "successful": 0, "failed": 0},
                    "month": {"total": 0, "successful": 0, "failed": 0},
                    "byProduct": []}
        try:
            today = datetime.datetime.utcnow().strftime("%Y-%m-%dT")
            docs = (
                col.where("tenant_id", "==", tenant_id)
                .order_by("created_at", direction="DESCENDING")
                .limit(2000)
                .stream()
            )
            today_total = today_ok = today_fail = 0
            month_total = month_ok = month_fail = 0
            for doc in docs:
                d = doc.to_dict()
                month_total += 1
                s = d.get("status", "")
                if s in ("sent", "delivered", "read"):
                    month_ok += 1
                elif s == "failed":
                    month_fail += 1
                if d.get("created_at", "").startswith(today):
                    today_total += 1
                    if s in ("sent", "delivered", "read"):
                        today_ok += 1
                    elif s == "failed":
                        today_fail += 1
            return {
                "today": {"total": today_total, "successful": today_ok, "failed": today_fail},
                "month": {"total": month_total, "successful": month_ok, "failed": month_fail},
                "byProduct": [],
            }
        except Exception as e:
            print(f"[db_layer.messages] get_usage failed: {e}")
            return {"today": {"total": 0, "successful": 0, "failed": 0},
                    "month": {"total": 0, "successful": 0, "failed": 0},
                    "byProduct": []}


messages = _Messages()
