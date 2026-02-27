from __future__ import annotations

"""Firestore operations for the `campaigns` collection.

Doc ID: {campaign_id} (UUID, deterministic).
NO inline counters (sent_count, failed_count) — use campaign_counters instead.
Write frequency: Medium (status updates during send).
"""

import datetime
from firebase_config import get_db
from google.cloud.firestore_v1.base_query import FieldFilter


def _col():
    db = get_db()
    return db.collection("campaigns") if db else None


class _Campaigns:

    @staticmethod
    def create(campaign_id: str, tenant_id: str, data: dict):
        """Create a new campaign document."""
        col = _col()
        if not col:
            return
        try:
            data["campaign_id"] = campaign_id
            data["tenant_id"] = tenant_id
            data["created_at"] = datetime.datetime.utcnow().isoformat()
            data["updated_at"] = data["created_at"]
            col.document(campaign_id).set(data)
        except Exception as e:
            print(f"[db_layer.campaigns] create({campaign_id}) failed: {e}")

    @staticmethod
    def get(campaign_id: str) -> dict | None:
        col = _col()
        if not col:
            return None
        try:
            doc = col.document(campaign_id).get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            print(f"[db_layer.campaigns] get({campaign_id}) failed: {e}")
            return None

    @staticmethod
    def update_status(campaign_id: str, status: str, **extra):
        """Update campaign status and optional extra fields."""
        col = _col()
        if not col:
            return
        try:
            update = {"status": status, "updated_at": datetime.datetime.utcnow().isoformat()}
            update.update(extra)
            col.document(campaign_id).update(update)
        except Exception as e:
            print(f"[db_layer.campaigns] update_status({campaign_id}) failed: {e}")

    @staticmethod
    def update_last_processed(campaign_id: str, index: int):
        """Track resume point for interrupted campaigns."""
        col = _col()
        if not col:
            return
        try:
            col.document(campaign_id).update({
                "last_processed_index": index,
                "updated_at": datetime.datetime.utcnow().isoformat(),
            })
        except Exception as e:
            print(f"[db_layer.campaigns] update_last_processed failed: {e}")

    @staticmethod
    def update_heartbeat(campaign_id: str):
        """Update worker heartbeat timestamp. Called every batch."""
        col = _col()
        if not col:
            return
        try:
            col.document(campaign_id).update({
                "worker_heartbeat": datetime.datetime.utcnow().isoformat(),
            })
        except Exception as e:
            print(f"[db_layer.campaigns] update_heartbeat({campaign_id}) failed: {e}")

    HEARTBEAT_STALE_SECONDS = 120  # campaign considered stuck if no heartbeat for 2 min

    @staticmethod
    def get_stale_running(threshold_seconds: int = 0) -> list[dict]:
        """Find running campaigns whose heartbeat is older than threshold."""
        if not threshold_seconds:
            threshold_seconds = _Campaigns.HEARTBEAT_STALE_SECONDS
        col = _col()
        if not col:
            return []
        try:
            cutoff = (
                datetime.datetime.utcnow()
                - datetime.timedelta(seconds=threshold_seconds)
            ).isoformat()
            docs = (
                col.where(filter=FieldFilter("status", "==", "running"))
                .stream()
            )
            stale = []
            for doc in docs:
                d = doc.to_dict()
                hb = d.get("worker_heartbeat", "")
                if hb and hb < cutoff:
                    stale.append(d)
                elif not hb and d.get("created_at", "") < cutoff:
                    stale.append(d)
            return stale
        except Exception as e:
            print(f"[db_layer.campaigns] get_stale_running failed: {e}")
            return []

    @staticmethod
    def list(tenant_id: str, limit: int = 25, cursor: str = None) -> tuple[list[dict], str | None]:
        """List campaigns with cursor-based pagination. Returns (docs, next_cursor)."""
        limit = max(1, min(limit, 100))
        col = _col()
        if not col:
            return [], None
        try:
            query = (
                col.where(filter=FieldFilter("tenant_id", "==", tenant_id))
                .order_by("created_at", direction="DESCENDING")
            )
            if cursor:
                query = query.start_after({"created_at": cursor})
            raw = list(query.limit(limit + 1).stream())
            has_next = len(raw) > limit
            docs = [doc.to_dict() for doc in raw[:limit]]
            next_cursor = docs[-1]["created_at"] if has_next and docs else None
            return docs, next_cursor
        except Exception as e:
            print(f"[db_layer.campaigns] list({tenant_id}) failed: {e}")
            return [], None

    @staticmethod
    def list_running() -> list[dict]:
        """Find all campaigns with status 'running'."""
        col = _col()
        if not col:
            return []
        try:
            docs = col.where(filter=FieldFilter("status", "==", "running")).stream()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"[db_layer.campaigns] list_running failed: {e}")
            return []

    @staticmethod
    def get_due_scheduled() -> list[dict]:
        """Find campaigns with status 'scheduled' whose scheduled_at time has passed."""
        col = _col()
        if not col:
            return []
        try:
            now = datetime.datetime.utcnow().isoformat() + "Z"
            docs = (
                col.where(filter=FieldFilter("status", "==", "scheduled"))
                .where(filter=FieldFilter("scheduled_at", "<=", now))
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"[db_layer.campaigns] get_due_scheduled failed: {e}")
            return []

    @staticmethod
    def delete(campaign_id: str):
        col = _col()
        if not col:
            return
        try:
            col.document(campaign_id).delete()
        except Exception as e:
            print(f"[db_layer.campaigns] delete({campaign_id}) failed: {e}")


campaigns = _Campaigns()
