from __future__ import annotations

"""Postgres operations for `chatbot_config` and `chatbot_rules` (Phase-5: cached).

chatbot_config — Primary key: tenant_id (1:1 with tenant).
chatbot_rules  — Primary key: (tenant_id, id).

Secrets (openai_api_key) are NEVER stored. Only `openai_key_ref` is persisted.

Reads are cached via the centralized cache module.
"""

from database import fetchrow, fetch, execute
from cache import fetch_cached_async, cache, chatbot_config_key, chatbot_rules_key, chatbot_active_rules_key
from observability import log_event
from utils.time_utils import get_ist_now_iso


# ---------------------------------------------------------------------------
# chatbot_config
# ---------------------------------------------------------------------------

class _ChatbotConfig:

    @staticmethod
    async def get(tenant_id: str) -> dict | None:
        """Get chatbot config. Cached for 6 hours."""
        async def _fetch():
            try:
                row = await fetchrow(
                    "SELECT * FROM chatbot_config WHERE tenant_id = %s",
                    tenant_id,
                )
                return dict(row) if row else None
            except Exception as e:
                log_event("db_error", detail=f"chatbot_config.get({tenant_id}) failed: {e}", level="ERROR")
                return None

        return await fetch_cached_async(chatbot_config_key(tenant_id), _fetch)

    @staticmethod
    async def upsert(tenant_id: str, data: dict):
        """Merge-update chatbot config. Invalidate cache."""
        try:
            await execute(
                """
                INSERT INTO chatbot_config (
                    tenant_id,
                    is_enabled,
                    fallback_message,
                    use_ai,
                    fallback_template_name,
                    fallback_cooldown_hours,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    COALESCE(%s, FALSE),
                    COALESCE(%s, ''),
                    COALESCE(%s, FALSE),
                    COALESCE(%s, ''),
                    COALESCE(%s, 24),
                    now(),
                    now()
                )
                ON CONFLICT (tenant_id) DO UPDATE SET
                    is_enabled = COALESCE(%s, chatbot_config.is_enabled),
                    fallback_message = COALESCE(%s, chatbot_config.fallback_message),
                    use_ai = COALESCE(%s, chatbot_config.use_ai),
                    fallback_template_name = COALESCE(%s, chatbot_config.fallback_template_name),
                    fallback_cooldown_hours = COALESCE(%s, chatbot_config.fallback_cooldown_hours),
                    updated_at = now()
                """,
                tenant_id,
                data.get("is_enabled"),
                data.get("fallback_message"),
                data.get("use_ai"),
                data.get("fallback_template_name"),
                data.get("fallback_cooldown_hours"),
                data.get("is_enabled"),
                data.get("fallback_message"),
                data.get("use_ai"),
                data.get("fallback_template_name"),
                data.get("fallback_cooldown_hours"),
            )
            # Invalidate chatbot_config cache and button_mappings cache
            cache.invalidate(chatbot_config_key(tenant_id))
            cache.invalidate(f"button_mappings:{tenant_id}")
        except Exception as e:
            log_event("db_error", detail=f"chatbot_config.upsert({tenant_id}) failed: {e}", level="ERROR")


chatbot_config = _ChatbotConfig()


# ---------------------------------------------------------------------------
# chatbot_rules
# ---------------------------------------------------------------------------

class _ChatbotRules:

    @staticmethod
    async def list(tenant_id: str) -> list[dict]:
        """List all chatbot rules. Cached for 6 hours."""
        async def _fetch():
            try:
                rows = await fetch(
                    """
                    SELECT id, tenant_id, keyword, response, response_type, match_type,
                           priority, is_active, created_at, updated_at
                    FROM chatbot_rules
                    WHERE tenant_id = %s
                    ORDER BY priority DESC, id DESC
                    """,
                    tenant_id,
                )
                results: list[dict] = []
                for r in rows:
                    d = dict(r)
                    d["_doc_id"] = str(d.get("id"))
                    results.append(d)
                return results
            except Exception as e:
                log_event("db_error", detail=f"chatbot_rules.list({tenant_id}) failed: {e}", level="ERROR")
                return []

        return await fetch_cached_async(chatbot_rules_key(tenant_id), _fetch)

    @staticmethod
    async def create(tenant_id: str, rule: dict) -> dict:
        try:
            row = await fetchrow(
                """
                INSERT INTO chatbot_rules (
                    tenant_id, keyword, response, response_type, match_type,
                    priority, is_active, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, TRUE), now(), NULL)
                RETURNING id
                """,
                tenant_id,
                (rule.get("keyword") or "").strip(),
                (rule.get("response") or ""),
                (rule.get("response_type") or "text"),
                (rule.get("match_type") or "contains"),
                int(rule.get("priority") or 0),
                (rule.get("is_active") if rule.get("is_active") is not None else True),
            )
            if row and row.get("id") is not None:
                rule["id"] = int(row["id"])
                rule["_doc_id"] = str(row["id"])
            # Invalidate cache
            cache.invalidate(chatbot_rules_key(tenant_id))
            cache.invalidate(chatbot_active_rules_key(tenant_id))
            return rule
        except Exception as e:
            log_event("db_error", detail=f"chatbot_rules.create failed: {e}", level="ERROR")
            return rule

    @staticmethod
    async def update(tenant_id: str, doc_id: str, data: dict):
        try:
            await execute(
                """
                UPDATE chatbot_rules
                SET
                    keyword = COALESCE(%s, keyword),
                    response = COALESCE(%s, response),
                    response_type = COALESCE(%s, response_type),
                    match_type = COALESCE(%s, match_type),
                    priority = COALESCE(%s, priority),
                    is_active = COALESCE(%s, is_active),
                    updated_at = now()
                WHERE tenant_id = %s AND id = %s
                """,
                data.get("keyword"),
                data.get("response"),
                data.get("response_type"),
                data.get("match_type"),
                data.get("priority"),
                (True if data.get("is_active") in (True, 1, "1") else False) if data.get("is_active") is not None else None,
                tenant_id,
                int(doc_id),
            )
            # Invalidate all rules caches
            cache.invalidate_prefix("chatbot_rules:")
            cache.invalidate_prefix("chatbot_rules_active:")
        except Exception as e:
            log_event("db_error", detail=f"chatbot_rules.update({doc_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def delete(tenant_id: str, doc_id: str):
        try:
            await execute(
                "DELETE FROM chatbot_rules WHERE tenant_id = %s AND id = %s",
                tenant_id,
                int(doc_id),
            )
            # Invalidate all rules caches
            cache.invalidate_prefix("chatbot_rules:")
            cache.invalidate_prefix("chatbot_rules_active:")
        except Exception as e:
            log_event("db_error", detail=f"chatbot_rules.delete({doc_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def get_active(tenant_id: str) -> list[dict]:
        """Return only active rules. Cached for 6 hours."""
        async def _fetch():
            try:
                rows = await fetch(
                    """
                    SELECT keyword, response, response_type, match_type, priority, is_active
                    FROM chatbot_rules
                    WHERE tenant_id = %s AND is_active = TRUE
                    ORDER BY priority DESC, id DESC
                    """,
                    tenant_id,
                )
                return [dict(r) for r in rows]
            except Exception as e:
                log_event("db_error", detail=f"chatbot_rules.get_active({tenant_id}) failed: {e}", level="ERROR")
                return []

        return await fetch_cached_async(chatbot_active_rules_key(tenant_id), _fetch)


chatbot_rules = _ChatbotRules()
