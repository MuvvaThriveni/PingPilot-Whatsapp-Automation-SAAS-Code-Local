from __future__ import annotations

"""Firestore operations for `chatbot_config` and `chatbot_rules` (Phase-5: cached).

chatbot_config — Doc ID: {tenant_id} (deterministic, 1:1 with tenant).
chatbot_rules  — Doc ID: auto-generated, linked by tenant_id field.

Secrets (openai_api_key) are NEVER stored. Only `openai_key_ref` is persisted.

Reads are cached via the centralized cache module.
"""

import datetime
from google.cloud.firestore_v1.base_query import FieldFilter
from firebase_config import get_db
from cache import fetch_cached, cache, chatbot_config_key, chatbot_rules_key, chatbot_active_rules_key
from observability import log_event
from utils.time_utils import get_ist_now_iso


def _config_col():
    db = get_db()
    return db.collection("chatbot_config") if db else None


def _rules_col():
    db = get_db()
    return db.collection("chatbot_rules") if db else None


# ---------------------------------------------------------------------------
# chatbot_config
# ---------------------------------------------------------------------------

class _ChatbotConfig:

    @staticmethod
    def get(tenant_id: str) -> dict | None:
        """Get chatbot config. Cached for 6 hours."""
        def _fetch():
            col = _config_col()
            if not col:
                return None
            try:
                # Direct document access (Requirement 2)
                doc = col.document(tenant_id).get()
                return doc.to_dict() if doc.exists else None
            except Exception as e:
                log_event("db_error", detail=f"chatbot_config.get({tenant_id}) failed: {e}", level="ERROR")
                return None

        return fetch_cached(chatbot_config_key(tenant_id), _fetch)

    @staticmethod
    def upsert(tenant_id: str, data: dict):
        """Merge-update chatbot config. Invalidate cache."""
        col = _config_col()
        if not col:
            return
        try:
            data["tenant_id"] = tenant_id
            data["updated_at"] = get_ist_now_iso()
            col.document(tenant_id).set(data, merge=True)
            # Invalidate cache
            cache.invalidate(chatbot_config_key(tenant_id))
        except Exception as e:
            log_event("db_error", detail=f"chatbot_config.upsert({tenant_id}) failed: {e}", level="ERROR")


chatbot_config = _ChatbotConfig()


# ---------------------------------------------------------------------------
# chatbot_rules
# ---------------------------------------------------------------------------

class _ChatbotRules:

    @staticmethod
    def list(tenant_id: str) -> list[dict]:
        """List all chatbot rules. Cached for 6 hours."""
        def _fetch():
            col = _rules_col()
            if not col:
                return []
            try:
                docs = (
                    col.where("tenant_id", "==", tenant_id)
                    .order_by("priority", direction="DESCENDING")
                    .stream()
                )
                results = []
                for doc in docs:
                    d = doc.to_dict()
                    d["_doc_id"] = doc.id
                    results.append(d)
                return results
            except Exception as e:
                log_event("db_error", detail=f"chatbot_rules.list({tenant_id}) failed: {e}", level="ERROR")
                return []

        return fetch_cached(chatbot_rules_key(tenant_id), _fetch)

    @staticmethod
    def create(tenant_id: str, rule: dict) -> dict:
        col = _rules_col()
        if not col:
            return rule
        try:
            rule["tenant_id"] = tenant_id
            rule["created_at"] = get_ist_now_iso()
            _, doc_ref = col.add(rule)
            rule["_doc_id"] = doc_ref.id
            # Invalidate cache
            cache.invalidate(chatbot_rules_key(tenant_id))
            cache.invalidate(chatbot_active_rules_key(tenant_id))
            return rule
        except Exception as e:
            log_event("db_error", detail=f"chatbot_rules.create failed: {e}", level="ERROR")
            return rule

    @staticmethod
    def update(doc_id: str, data: dict):
        col = _rules_col()
        if not col:
            return
        try:
            data["updated_at"] = get_ist_now_iso()
            col.document(doc_id).set(data, merge=True)
            # Invalidate all rules caches
            cache.invalidate_prefix("chatbot_rules:")
            cache.invalidate_prefix("chatbot_rules_active:")
        except Exception as e:
            log_event("db_error", detail=f"chatbot_rules.update({doc_id}) failed: {e}", level="ERROR")

    @staticmethod
    def delete(doc_id: str):
        col = _rules_col()
        if not col:
            return
        try:
            col.document(doc_id).delete()
            # Invalidate all rules caches
            cache.invalidate_prefix("chatbot_rules:")
            cache.invalidate_prefix("chatbot_rules_active:")
        except Exception as e:
            log_event("db_error", detail=f"chatbot_rules.delete({doc_id}) failed: {e}", level="ERROR")

    @staticmethod
    def get_active(tenant_id: str) -> list[dict]:
        """Return only active rules. Cached for 6 hours."""
        def _fetch():
            col = _rules_col()
            if not col:
                return []
            try:
                # Requirement: avoid .stream() inside handlers - caching solves this
                docs = (
                    col.where(filter=FieldFilter("tenant_id", "==", tenant_id))
                    .where(filter=FieldFilter("is_active", "==", 1))
                    .order_by("priority", direction="DESCENDING")
                    .stream()
                )
                return [doc.to_dict() for doc in docs]
            except Exception as e:
                log_event("db_error", detail=f"chatbot_rules.get_active({tenant_id}) failed: {e}", level="ERROR")
                return []

        return fetch_cached(chatbot_active_rules_key(tenant_id), _fetch)


chatbot_rules = _ChatbotRules()
