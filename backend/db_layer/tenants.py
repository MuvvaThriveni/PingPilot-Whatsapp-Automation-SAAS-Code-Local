from __future__ import annotations

"""Firestore operations for the `tenants` collection.

Doc ID: {tenant_id} (= Firebase Auth UID, deterministic).
Secrets (access_token, openai_api_key) are NEVER stored here.
Only reference keys (token_ref, openai_key_ref) are persisted.
"""

from firebase_config import get_db


def _col():
    db = get_db()
    return db.collection("tenants") if db else None


class _Tenants:

    @staticmethod
    def get(tenant_id: str) -> dict | None:
        col = _col()
        if not col:
            return None
        try:
            doc = col.document(tenant_id).get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            print(f"[db_layer.tenants] get({tenant_id}) failed: {e}")
            return None

    @staticmethod
    def get_by_phone_number_id(phone_number_id: str) -> dict | None:
        """Lookup tenant by WhatsApp phone_number_id (webhook routing)."""
        col = _col()
        if not col:
            return None
        try:
            docs = col.where("phone_number_id", "==", phone_number_id).limit(1).stream()
            for doc in docs:
                return doc.to_dict()
            return None
        except Exception as e:
            print(f"[db_layer.tenants] get_by_phone_number_id failed: {e}")
            return None

    @staticmethod
    def upsert(tenant_id: str, data: dict):
        """Create or update tenant document.

        Caller must ensure `data` does NOT contain raw secrets.
        Use `token_ref` / `openai_key_ref` instead.
        """
        col = _col()
        if not col:
            return
        try:
            # Strip any raw secrets that may have leaked in
            safe = {k: v for k, v in data.items()
                    if k not in ("access_token", "openai_api_key")}
            safe["tenant_id"] = tenant_id
            col.document(tenant_id).set(safe, merge=True)
        except Exception as e:
            print(f"[db_layer.tenants] upsert({tenant_id}) failed: {e}")

    @staticmethod
    def delete(tenant_id: str):
        col = _col()
        if not col:
            return
        try:
            col.document(tenant_id).delete()
        except Exception as e:
            print(f"[db_layer.tenants] delete({tenant_id}) failed: {e}")


tenants = _Tenants()
