from __future__ import annotations

"""Firestore operations for the `tenants` collection (Phase-5: cached).

Doc ID: {tenant_id} (= Firebase Auth UID, deterministic).
Secrets (access_token, openai_api_key) are NEVER stored here.
Only reference keys (token_ref, openai_key_ref) are persisted.

get_by_phone_number_id() is cached because it's called on EVERY webhook.
"""

from firebase_config import get_db
from cache import fetch_cached, cache, tenant_key, tenant_by_phone_key


def _col():
    db = get_db()
    return db.collection("tenants") if db else None


class _Tenants:

    @staticmethod
    def get(tenant_id: str) -> dict | None:
        """Get tenant document. Cached for 6 hours."""
        def _fetch():
            col = _col()
            if not col:
                return None
            try:
                # Direct document access (Requirement 2)
                doc = col.document(tenant_id).get()
                return doc.to_dict() if doc.exists else None
            except Exception as e:
                print(f"[db_layer.tenants] get({tenant_id}) failed: {e}")
                return None

        return fetch_cached(tenant_key(tenant_id), _fetch)

    @staticmethod
    def get_by_phone_number_id(phone_number_id: str) -> dict | None:
        """Lookup tenant by WhatsApp phone_number_id (webhook routing). Cached for 6 hours."""
        def _fetch():
            col = _col()
            if not col:
                return None
            try:
                # Still using where for initial lookup, but once cached it won't be called (Requirement 1/8)
                docs = col.where("phone_number_id", "==", phone_number_id).limit(1).stream()
                for doc in docs:
                    return doc.to_dict()
                return None
            except Exception as e:
                print(f"[db_layer.tenants] get_by_phone_number_id failed: {e}")
                return None

        return fetch_cached(tenant_by_phone_key(phone_number_id), _fetch)

    @staticmethod
    def upsert(tenant_id: str, data: dict):
        """Create or update tenant document. Invalidate cache."""
        col = _col()
        if not col:
            return
        try:
            data["tenant_id"] = tenant_id
            col.document(tenant_id).set(data, merge=True)
            # Invalidate cache
            cache.invalidate(tenant_key(tenant_id))
            cache.invalidate_prefix("tenant_phone:")
        except Exception as e:
            print(f"[db_layer.tenants] upsert({tenant_id}) failed: {e}")

    @staticmethod
    def delete(tenant_id: str):
        col = _col()
        if not col:
            return
        try:
            col.document(tenant_id).delete()
            cache.invalidate(tenant_key(tenant_id))
            cache.invalidate_prefix("tenant_phone:")
        except Exception as e:
            print(f"[db_layer.tenants] delete({tenant_id}) failed: {e}")


tenants = _Tenants()
