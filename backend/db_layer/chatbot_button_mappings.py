"""Postgres operations for chatbot_button_mappings (Phase-8: dynamic mappings).

Replaces hardcoded _DEFAULT_BUTTON_MAPPINGS / _DEFAULT_BUTTON_ID_MAPPINGS
in webhook.py.  All button→template mappings are now fully tenant-specific.

Every query is scoped to tenant_id.  No cross-tenant access is possible.
"""

from __future__ import annotations

from database import fetchrow, fetch, execute
from cache import fetch_cached_async, cache
from observability import log_event


# ── Cache key builder ─────────────────────────────────────────────

def _button_mappings_cache_key(tenant_id: str) -> str:
    return f"button_mappings:{tenant_id}"


# ── DB Operations ─────────────────────────────────────────────────

class _ChatbotButtonMappings:

    @staticmethod
    async def list(tenant_id: str) -> list[dict]:
        """List all button mappings for a tenant (admin view)."""
        try:
            rows = await fetch(
                """
                SELECT id, tenant_id, button_text,
                       template_name, is_active, priority,
                       created_at, updated_at
                FROM chatbot_button_mappings
                WHERE tenant_id = %s
                ORDER BY priority DESC, id DESC
                """,
                tenant_id,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            log_event("db_error",
                      detail=f"button_mappings.list({tenant_id}) failed: {e}",
                      level="ERROR")
            return []

    @staticmethod
    async def get_active_maps(tenant_id: str) -> dict:
        """Return text_map for active button mappings.  Cached 1 h.

        Returns empty dict if no mappings configured — NO hardcoded defaults.
        """
        async def _fetch():
            try:
                rows = await fetch(
                    """
                    SELECT button_text, template_name
                    FROM chatbot_button_mappings
                    WHERE tenant_id = %s AND is_active = TRUE
                    ORDER BY priority DESC
                    """,
                    tenant_id,
                )
                text_map: dict[str, str] = {}
                for r in rows:
                    bt = (r.get("button_text") or "").strip()
                    tpl = r.get("template_name", "")
                    if bt and bt not in text_map:
                        text_map[bt] = tpl
                return text_map
            except Exception as e:
                log_event(
                    "db_error",
                    detail=f"button_mappings.get_active_maps({tenant_id}) failed: {e}",
                    level="ERROR",
                )
                return {}  # Empty — NOT hardcoded defaults

        return await fetch_cached_async(
            _button_mappings_cache_key(tenant_id), _fetch, ttl=3600.0,
        )

    @staticmethod
    async def create(tenant_id: str, data: dict) -> dict:
        """Insert a new button mapping.  Invalidates cache."""
        try:
            row = await fetchrow(
                """
                INSERT INTO chatbot_button_mappings
                    (tenant_id, button_text, template_name,
                     is_active, priority, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, now(), now())
                RETURNING id, created_at, updated_at
                """,
                tenant_id,
                (data.get("button_text") or "").strip(),
                data["template_name"],
                data.get("is_active", True),
                int(data.get("priority") or 0),
            )
            if row:
                data["id"] = row["id"]
                data["created_at"] = str(row.get("created_at", ""))
                data["updated_at"] = str(row.get("updated_at", ""))
            cache.invalidate(_button_mappings_cache_key(tenant_id))
            return data
        except Exception as e:
            log_event("db_error",
                      detail=f"button_mappings.create({tenant_id}) failed: {e}",
                      level="ERROR")
            raise

    @staticmethod
    async def update(tenant_id: str, mapping_id: int, data: dict):
        """Update a button mapping.  Invalidates cache."""
        try:
            await execute(
                """
                UPDATE chatbot_button_mappings
                SET button_text    = COALESCE(%s, button_text),
                    template_name  = COALESCE(%s, template_name),
                    is_active      = COALESCE(%s, is_active),
                    priority       = COALESCE(%s, priority),
                    updated_at     = now()
                WHERE tenant_id = %s AND id = %s
                """,
                data.get("button_text"),
                data.get("template_name"),
                data.get("is_active"),
                data.get("priority"),
                tenant_id,
                mapping_id,
            )
            cache.invalidate(_button_mappings_cache_key(tenant_id))
        except Exception as e:
            log_event("db_error",
                      detail=f"button_mappings.update({tenant_id}, {mapping_id}) failed: {e}",
                      level="ERROR")
            raise

    @staticmethod
    async def delete(tenant_id: str, mapping_id: int):
        """Delete a button mapping.  Invalidates cache."""
        try:
            await execute(
                "DELETE FROM chatbot_button_mappings WHERE tenant_id = %s AND id = %s",
                tenant_id,
                mapping_id,
            )
            cache.invalidate(_button_mappings_cache_key(tenant_id))
        except Exception as e:
            log_event("db_error",
                      detail=f"button_mappings.delete({tenant_id}, {mapping_id}) failed: {e}",
                      level="ERROR")
            raise


button_mappings = _ChatbotButtonMappings()
