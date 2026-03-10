from __future__ import annotations

"""Firestore operations for the `template_cache` collection.

Caches WhatsApp template metadata per tenant so templates survive restarts.
Doc ID: {tenant_id}_{template_name}_{language} (deterministic).
Write frequency: Low.
"""

import datetime
from firebase_config import get_db
from observability import log_event
from utils.time_utils import get_ist_now_iso


def _ist_now_iso() -> str:
    return get_ist_now_iso()


def _col():
    db = get_db()
    return db.collection("template_cache") if db else None


def _doc_id(tenant_id: str, template_name: str, language: str) -> str:
    return f"{tenant_id}_{template_name}_{language}"


class _TemplateCache:

    @staticmethod
    def upsert(tenant_id: str, template_name: str, language: str,
               components: list, status: str = "", param_count: int = 0):
        """Cache a single template's metadata."""
        col = _col()
        if not col:
            return
        try:
            doc_id = _doc_id(tenant_id, template_name, language)
            col.document(doc_id).set({
                "tenant_id": tenant_id,
                "template_name": template_name,
                "language": language,
                "status": status,
                "components": components,
                "param_count": param_count,
                "fetched_at": _ist_now_iso(),
            })
        except Exception as e:
            log_event("db_error", detail=f"template_cache.upsert failed: {e}", level="ERROR")

    @staticmethod
    def upsert_batch(tenant_id: str, templates: list[dict]):
        """Batch-cache multiple templates."""
        db = get_db()
        if not db:
            return
        col = db.collection("template_cache")
        try:
            batch = db.batch()
            now = _ist_now_iso()
            for i, t in enumerate(templates):
                name = t.get("template_name", t.get("name", ""))
                lang = t.get("language", "en_US")
                doc_id = _doc_id(tenant_id, name, lang)
                ref = col.document(doc_id)
                batch.set(ref, {
                    "tenant_id": tenant_id,
                    "template_name": name,
                    "language": lang,
                    "status": t.get("status", ""),
                    "components": t.get("components", []),
                    "param_count": t.get("param_count", 0),
                    "fetched_at": now,
                })
                if (i + 1) % 500 == 0:
                    batch.commit()
                    batch = db.batch()
            batch.commit()
        except Exception as e:
            log_event("db_error", detail=f"template_cache.upsert_batch failed: {e}", level="ERROR")

    @staticmethod
    def get(tenant_id: str, template_name: str, language: str) -> dict | None:
        """Get a single cached template."""
        col = _col()
        if not col:
            return None
        try:
            doc_id = _doc_id(tenant_id, template_name, language)
            doc = col.document(doc_id).get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            log_event("db_error", detail=f"template_cache.get failed: {e}", level="ERROR")
            return None

    @staticmethod
    def get_components(tenant_id: str, template_key: str) -> list:
        """Get cached components by template_key ('name|lang' format)."""
        if "|" not in template_key:
            return []
        parts = template_key.split("|", 1)
        name = parts[0].strip()
        lang = parts[1].strip()
        cached = _TemplateCache.get(tenant_id, name, lang)
        if cached:
            return cached.get("components", [])
        return []

    @staticmethod
    def list_approved(tenant_id: str) -> list[dict]:
        """Get all approved templates for a tenant."""
        col = _col()
        if not col:
            return []
        try:
            docs = (
                col.where("tenant_id", "==", tenant_id)
                .where("status", "==", "APPROVED")
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            log_event("db_error", detail=f"template_cache.list_approved failed: {e}", level="ERROR")
            return []

    @staticmethod
    def load_all_as_dict(tenant_id: str) -> dict:
        """Load all cached templates as a dict keyed by 'name|lang'.

        Used to populate the in-memory template_cache during migration.
        """
        col = _col()
        if not col:
            return {}
        try:
            docs = col.where("tenant_id", "==", tenant_id).stream()
            result = {}
            for doc in docs:
                d = doc.to_dict()
                key = f"{d.get('template_name', '')}|{d.get('language', '')}"
                result[key] = d.get("components", [])
            return result
        except Exception as e:
            log_event("db_error", detail=f"template_cache.load_all_as_dict failed: {e}", level="ERROR")
            return {}


template_cache_db = _TemplateCache()
