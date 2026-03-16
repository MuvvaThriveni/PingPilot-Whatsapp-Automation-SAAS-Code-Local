from __future__ import annotations

"""Postgres operations for the `template_cache` table.

Caches WhatsApp template metadata per tenant so templates survive restarts.
Primary key: doc_id = {tenant_id}_{template_name}_{language} (deterministic).
Write frequency: Low.
"""

import datetime
import json

from database import fetchrow, fetch, execute, executemany
from observability import log_event
from utils.time_utils import get_ist_now_iso


def _ist_now_iso() -> str:
    return get_ist_now_iso()

def _doc_id(tenant_id: str, template_name: str, language: str) -> str:
    return f"{tenant_id}_{template_name}_{language}"


class _TemplateCache:

    @staticmethod
    async def upsert(tenant_id: str, template_name: str, language: str,
                     components: list, status: str = "", param_count: int = 0):
        """Cache a single template's metadata."""
        try:
            doc_id = _doc_id(tenant_id, template_name, language)
            await execute(
                """
                INSERT INTO template_cache (
                    doc_id, tenant_id, template_name, language, status, components, param_count, fetched_at
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, COALESCE(%s::timestamptz, now()))
                ON CONFLICT (doc_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    components = EXCLUDED.components,
                    param_count = EXCLUDED.param_count,
                    fetched_at = EXCLUDED.fetched_at
                """,
                doc_id,
                tenant_id,
                template_name,
                language,
                status or "",
                json.dumps(components or []),
                int(param_count or 0),
                _ist_now_iso(),
            )
        except Exception as e:
            log_event("db_error", detail=f"template_cache.upsert failed: {e}", level="ERROR")

    @staticmethod
    async def upsert_batch(tenant_id: str, templates: list[dict]):
        """Batch-cache multiple templates."""
        try:
            now = _ist_now_iso()
            args_list: list[tuple] = []
            for i, t in enumerate(templates):
                name = t.get("template_name", t.get("name", ""))
                lang = t.get("language", "en_US")
                doc_id = _doc_id(tenant_id, name, lang)
                args_list.append(
                    (
                        doc_id,
                        tenant_id,
                        name,
                        lang,
                        t.get("status", "") or "",
                        json.dumps(t.get("components", []) or []),
                        int(t.get("param_count", 0) or 0),
                        now,
                    )
                )

            if not args_list:
                return

            q = (
                """
                INSERT INTO template_cache (
                    doc_id, tenant_id, template_name, language, status, components, param_count, fetched_at
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::timestamptz)
                ON CONFLICT (doc_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    components = EXCLUDED.components,
                    param_count = EXCLUDED.param_count,
                    fetched_at = EXCLUDED.fetched_at
                """
            )

            chunk_size = 1000
            for start in range(0, len(args_list), chunk_size):
                await executemany(q, args_list[start : start + chunk_size])
        except Exception as e:
            log_event("db_error", detail=f"template_cache.upsert_batch failed: {e}", level="ERROR")

    @staticmethod
    async def get(tenant_id: str, template_name: str, language: str) -> dict | None:
        """Get a single cached template."""
        try:
            doc_id = _doc_id(tenant_id, template_name, language)
            row = await fetchrow(
                "SELECT * FROM template_cache WHERE doc_id = %s AND tenant_id = %s",
                doc_id,
                tenant_id,
            )
            return dict(row) if row else None
        except Exception as e:
            log_event("db_error", detail=f"template_cache.get failed: {e}", level="ERROR")
            return None

    @staticmethod
    async def get_components(tenant_id: str, template_key: str) -> list:
        """Get cached components by template_key ('name|lang' format)."""
        if "|" not in template_key:
            return []
        parts = template_key.split("|", 1)
        name = parts[0].strip()
        lang = parts[1].strip()
        cached = await _TemplateCache.get(tenant_id, name, lang)
        if cached:
            return cached.get("components", [])
        return []

    @staticmethod
    async def list_approved(tenant_id: str) -> list[dict]:
        """Get all approved templates for a tenant."""
        try:
            rows = await fetch(
                """
                SELECT *
                FROM template_cache
                WHERE tenant_id = %s AND status = 'APPROVED'
                ORDER BY template_name ASC
                """,
                tenant_id,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            log_event("db_error", detail=f"template_cache.list_approved failed: {e}", level="ERROR")
            return []

    @staticmethod
    async def load_all_as_dict(tenant_id: str) -> dict:
        """Load all cached templates as a dict keyed by 'name|lang'.

        Used to populate the in-memory template_cache during migration.
        """
        try:
            result = {}
            rows = await fetch(
                "SELECT template_name, language, components FROM template_cache WHERE tenant_id = %s",
                tenant_id,
            )
            for r in rows:
                d = dict(r)
                key = f"{d.get('template_name', '')}|{d.get('language', '')}"
                result[key] = d.get("components", [])
            return result
        except Exception as e:
            log_event("db_error", detail=f"template_cache.load_all_as_dict failed: {e}", level="ERROR")
            return {}


template_cache_db = _TemplateCache()
