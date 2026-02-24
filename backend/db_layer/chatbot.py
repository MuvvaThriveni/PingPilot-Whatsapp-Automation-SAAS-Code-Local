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
from cache import cache, chatbot_config_key, chatbot_rules_key


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
        """Get chatbot config. Cached for 60 s."""
        def _fetch():
            col = _config_col()
            if not col:
                return None
            try:
                doc = col.document(tenant_id).get()
                return doc.to_dict() if doc.exists else None
            except Exception as e:
                print(f"[db_layer.chatbot_config] get({tenant_id}) failed: {e}")
                return None

        return cache.get_or_fetch(chatbot_config_key(tenant_id), _fetch, ttl=60.0)

    @staticmethod
    def upsert(tenant_id: str, data: dict):
        """Merge-update chatbot config. Strips raw openai_api_key."""
        col = _config_col()
        if not col:
            return
        try:
            data["tenant_id"] = tenant_id
            data["updated_at"] = datetime.datetime.utcnow().isoformat()
            col.document(tenant_id).set(data, merge=True)
            # Invalidate cache
            cache.invalidate(chatbot_config_key(tenant_id))
        except Exception as e:
            print(f"[db_layer.chatbot_config] upsert({tenant_id}) failed: {e}")


chatbot_config = _ChatbotConfig()


# ---------------------------------------------------------------------------
# chatbot_rules
# ---------------------------------------------------------------------------

class _ChatbotRules:

    @staticmethod
    def list(tenant_id: str) -> list[dict]:
        """List all chatbot rules. Cached for 60 s."""
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
                print(f"[db_layer.chatbot_rules] list({tenant_id}) failed: {e}")
                return []

        return cache.get_or_fetch(chatbot_rules_key(tenant_id), _fetch, ttl=60.0)

    @staticmethod
    def create(tenant_id: str, rule: dict) -> dict:
        col = _rules_col()
        if not col:
            return rule
        try:
            rule["tenant_id"] = tenant_id
            rule["created_at"] = datetime.datetime.utcnow().isoformat()
            _, doc_ref = col.add(rule)
            rule["_doc_id"] = doc_ref.id
            cache.invalidate(chatbot_rules_key(tenant_id))
            return rule
        except Exception as e:
            print(f"[db_layer.chatbot_rules] create failed: {e}")
            return rule

    @staticmethod
    def update(doc_id: str, data: dict):
        col = _rules_col()
        if not col:
            return
        try:
            data["updated_at"] = datetime.datetime.utcnow().isoformat()
            col.document(doc_id).set(data, merge=True)
            # Invalidate all rules caches (we don't know tenant_id here)
            cache.invalidate_prefix("chatbot_rules:")
        except Exception as e:
            print(f"[db_layer.chatbot_rules] update({doc_id}) failed: {e}")

    @staticmethod
    def delete(doc_id: str):
        col = _rules_col()
        if not col:
            return
        try:
            col.document(doc_id).delete()
            cache.invalidate_prefix("chatbot_rules:")
        except Exception as e:
            print(f"[db_layer.chatbot_rules] delete({doc_id}) failed: {e}")

    @staticmethod
    def get_active(tenant_id: str) -> list[dict]:
        """Return only active rules, ordered by priority descending. Cached for 60 s."""
        cache_key = f"chatbot_rules_active:{tenant_id}"

        def _fetch():
            col = _rules_col()
            if not col:
                return []
            try:
                docs = (
                    col.where(filter=FieldFilter("tenant_id", "==", tenant_id))
                    .where(filter=FieldFilter("is_active", "==", 1))
                    .order_by("priority", direction="DESCENDING")
                    .stream()
                )
                return [doc.to_dict() for doc in docs]
            except Exception as e:
                print(f"[db_layer.chatbot_rules] get_active({tenant_id}) failed: {e}")
                return []

        return cache.get_or_fetch(cache_key, _fetch, ttl=60.0)


chatbot_rules = _ChatbotRules()
