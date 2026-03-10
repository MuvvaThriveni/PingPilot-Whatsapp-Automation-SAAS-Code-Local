from __future__ import annotations

"""Firestore operations for the `campaign_recipients` collection.

Doc ID: {campaign_id}_{contact_phone} (deterministic).
One document per recipient per campaign — supports parallel worker writes.
Write frequency: HIGH.
"""

import datetime
from google.cloud.firestore_v1 import Increment
from firebase_config import get_db
from observability import log_event
from utils.time_utils import get_ist_now_iso


def _ist_now_iso() -> str:
    return get_ist_now_iso()


def _col():
    db = get_db()
    return db.collection("campaign_recipients") if db else None


def _doc_id(campaign_id: str, contact_phone: str) -> str:
    return f"{campaign_id}_{contact_phone}"


class _CampaignRecipients:

    @staticmethod
    def create_batch(campaign_id: str, tenant_id: str, contacts: list[dict]):
        """Batch-create recipient documents for a campaign.

        Each contact dict should have: phone, name, index, and optional contact_data.
        """
        db = get_db()
        if not db:
            return
        col = db.collection("campaign_recipients")
        try:
            batch = db.batch()
            now = _ist_now_iso()
            for i, contact in enumerate(contacts):
                phone = contact.get("phone", "")
                doc_id = _doc_id(campaign_id, phone)
                doc_data = {
                    "campaign_id": campaign_id,
                    "tenant_id": tenant_id,
                    "contact_phone": phone,
                    "contact_name": contact.get("name", ""),
                    "contact_data": {k: v for k, v in contact.items()
                                     if k not in ("phone", "name", "index")},
                    "status": "pending",
                    "wa_message_id": "",
                    "error_message": "",
                    "attempt_count": 0,
                    "recipient_index": contact.get("index", i),
                    "created_at": now,
                }
                ref = col.document(doc_id)
                batch.set(ref, doc_data)
                # Firestore batch limit is 500
                if (i + 1) % 500 == 0:
                    batch.commit()
                    batch = db.batch()
            batch.commit()
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.create_batch failed: {e}", level="ERROR")

    @staticmethod
    def update_status(campaign_id: str, contact_phone: str, status: str,
                      wa_message_id: str = "", error_message: str = ""):
        """Update a single recipient's delivery status."""
        col = _col()
        if not col:
            return
        try:
            doc_id = _doc_id(campaign_id, contact_phone)
            update = {
                "status": status,
                "updated_at": _ist_now_iso(),
                "last_attempt_at": _ist_now_iso(),
            }
            if wa_message_id:
                update["wa_message_id"] = wa_message_id
            if error_message:
                update["error_message"] = error_message
            update["attempt_count"] = Increment(1)
            col.document(doc_id).update(update)
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.update_status failed: {e}", level="ERROR")

    @staticmethod
    def get_pending(campaign_id: str, limit: int = 10) -> list[dict]:
        """Get pending recipients for a campaign (for resume / distributed processing)."""
        col = _col()
        if not col:
            return []
        try:
            docs = (
                col.where("campaign_id", "==", campaign_id)
                .where("status", "==", "pending")
                .order_by("recipient_index")
                .limit(limit)
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.get_pending failed: {e}", level="ERROR")
            return []

    @staticmethod
    def get_failed(campaign_id: str) -> list[dict]:
        """Get failed recipients for retry."""
        col = _col()
        if not col:
            return []
        try:
            docs = (
                col.where("campaign_id", "==", campaign_id)
                .where("status", "==", "failed")
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.get_failed failed: {e}", level="ERROR")
            return []

    @staticmethod
    def list_by_campaign(campaign_id: str, limit: int = 500) -> list[dict]:
        """Get all recipients for a campaign, ordered by index."""
        col = _col()
        if not col:
            return []
        try:
            docs = (
                col.where("campaign_id", "==", campaign_id)
                .order_by("recipient_index")
                .limit(limit)
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.list_by_campaign failed: {e}", level="ERROR")
            return []

    @staticmethod
    def delete_by_campaign(campaign_id: str):
        """Delete all recipient documents for a campaign."""
        db = get_db()
        if not db:
            return
        col = db.collection("campaign_recipients")
        try:
            docs = col.where("campaign_id", "==", campaign_id).stream()
            batch = db.batch()
            count = 0
            for doc in docs:
                batch.delete(doc.reference)
                count += 1
                if count % 500 == 0:
                    batch.commit()
                    batch = db.batch()
            if count % 500 != 0:
                batch.commit()
        except Exception as e:
            log_event("db_error", detail=f"campaign_recipients.delete_by_campaign failed: {e}", level="ERROR")


campaign_recipients = _CampaignRecipients()
