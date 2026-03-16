from __future__ import annotations

"""Postgres operations for the `tenants` table (Phase-5: cached).

Primary key: {tenant_id} (= Firebase Auth UID, deterministic).
Secrets (access_token, openai_api_key) are NEVER stored here.
Only reference keys (token_ref, openai_key_ref) are persisted.

get_by_phone_number_id() is cached because it's called on EVERY webhook.
"""

from database import fetchrow, execute
from cache import fetch_cached_async, cache, tenant_key, tenant_by_phone_key
from observability import log_event


class _Tenants:

    @staticmethod
    async def get(tenant_id: str) -> dict | None:
        """Get tenant document. Cached for 6 hours."""
        async def _fetch():
            try:
                row = await fetchrow(
                    "SELECT * FROM tenants WHERE tenant_id = %s",
                    tenant_id,
                )
                return dict(row) if row else None
            except Exception as e:
                log_event("db_error", detail=f"tenants.get({tenant_id}) failed: {e}", level="ERROR")
                return None

        return await fetch_cached_async(tenant_key(tenant_id), _fetch)

    @staticmethod
    async def get_by_phone_number_id(phone_number_id: str) -> dict | None:
        """Lookup tenant by WhatsApp phone_number_id (webhook routing). Cached for 6 hours."""
        async def _fetch():
            try:
                row = await fetchrow(
                    "SELECT * FROM tenants WHERE phone_number_id = %s LIMIT 1",
                    phone_number_id,
                )
                return dict(row) if row else None
            except Exception as e:
                log_event("db_error", detail=f"tenants.get_by_phone_number_id failed: {e}", level="ERROR")
                return None

        return await fetch_cached_async(tenant_by_phone_key(phone_number_id), _fetch)

    @staticmethod
    async def get_by_webhook_verify_token(webhook_verify_token: str) -> dict | None:
        """Lookup tenant by webhook_verify_token (Meta webhook verification fallback)."""
        if not webhook_verify_token:
            return None
        try:
            row = await fetchrow(
                "SELECT * FROM tenants WHERE webhook_verify_token = %s LIMIT 1",
                webhook_verify_token,
            )
            return dict(row) if row else None
        except Exception as e:
            log_event("db_error", detail=f"tenants.get_by_webhook_verify_token failed: {e}", level="ERROR")
            return None

    @staticmethod
    async def upsert(tenant_id: str, data: dict):
        """Create or update tenant document. Invalidate cache."""
        try:
            await execute(
                """
                INSERT INTO tenants (
                    tenant_id,
                    business_account_id,
                    phone_number_id,
                    access_token,
                    token_ref,
                    webhook_verify_token,
                    is_configured,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    COALESCE(%s, ''),
                    COALESCE(%s, ''),
                    COALESCE(%s, ''),
                    COALESCE(%s, ''),
                    COALESCE(%s, ''),
                    COALESCE(%s, FALSE),
                    now(),
                    now()
                )
                ON CONFLICT (tenant_id) DO UPDATE SET
                    business_account_id = COALESCE(%s, tenants.business_account_id),
                    phone_number_id = COALESCE(%s, tenants.phone_number_id),
                    access_token = COALESCE(%s, tenants.access_token),
                    token_ref = COALESCE(%s, tenants.token_ref),
                    webhook_verify_token = COALESCE(%s, tenants.webhook_verify_token),
                    is_configured = COALESCE(%s, tenants.is_configured),
                    updated_at = now()
                """,
                tenant_id,
                data.get("business_account_id"),
                data.get("phone_number_id"),
                data.get("access_token"),
                data.get("token_ref"),
                data.get("webhook_verify_token"),
                data.get("is_configured"),
                data.get("business_account_id"),
                data.get("phone_number_id"),
                data.get("access_token"),
                data.get("token_ref"),
                data.get("webhook_verify_token"),
                data.get("is_configured"),
            )
            # Invalidate cache
            cache.invalidate(tenant_key(tenant_id))
            cache.invalidate_prefix("tenant_phone:")
        except Exception as e:
            log_event("db_error", detail=f"tenants.upsert({tenant_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def delete(tenant_id: str):
        try:
            await execute(
                "DELETE FROM tenants WHERE tenant_id = %s",
                tenant_id,
            )
            cache.invalidate(tenant_key(tenant_id))
            cache.invalidate_prefix("tenant_phone:")
        except Exception as e:
            log_event("db_error", detail=f"tenants.delete({tenant_id}) failed: {e}", level="ERROR")


tenants = _Tenants()
