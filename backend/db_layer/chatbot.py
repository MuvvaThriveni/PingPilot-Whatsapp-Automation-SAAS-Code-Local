from __future__ import annotations

"""Firestore operations for `chatbot_config` and `chatbot_rules` collections.

chatbot_config — Doc ID: {tenant_id} (deterministic, 1:1 with tenant).
chatbot_rules  — Doc ID: auto-generated, linked by tenant_id field.

Secrets (openai_api_key) are NEVER stored. Only `openai_key_ref` is persisted.
"""

import datetime
from firebase_config import get_db


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
        col = _config_col()
        if not col:
            return None
        try:
            doc = col.document(tenant_id).get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            print(f"[db_layer.chatbot_config] get({tenant_id}) failed: {e}")
            return None

    @staticmethod
    def upsert(tenant_id: str, data: dict):
        """Merge-update chatbot config. Strips raw openai_api_key."""
        col = _config_col()
        if not col:
            return
        try:
            safe = {k: v for k, v in data.items() if k != "openai_api_key"}
            safe["tenant_id"] = tenant_id
            safe["updated_at"] = datetime.datetime.utcnow().isoformat()
            col.document(tenant_id).set(safe, merge=True)
        except Exception as e:
            print(f"[db_layer.chatbot_config] upsert({tenant_id}) failed: {e}")


chatbot_config = _ChatbotConfig()


# ---------------------------------------------------------------------------
# chatbot_rules
# ---------------------------------------------------------------------------

class _ChatbotRules:

    @staticmethod
    def list(tenant_id: str) -> list[dict]:
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
        except Exception as e:
            print(f"[db_layer.chatbot_rules] update({doc_id}) failed: {e}")

    @staticmethod
    def delete(doc_id: str):
        col = _rules_col()
        if not col:
            return
        try:
            col.document(doc_id).delete()
        except Exception as e:
            print(f"[db_layer.chatbot_rules] delete({doc_id}) failed: {e}")

    @staticmethod
    def get_active(tenant_id: str) -> list[dict]:
        """Return only active rules, ordered by priority descending."""
        col = _rules_col()
        if not col:
            return []
        try:
            docs = (
                col.where("tenant_id", "==", tenant_id)
                .where("is_active", "==", 1)
                .order_by("priority", direction="DESCENDING")
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"[db_layer.chatbot_rules] get_active({tenant_id}) failed: {e}")
            return []


chatbot_rules = _ChatbotRules()
