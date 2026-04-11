"""Shared data store for WappFlow – Multi-tenant Postgres-backed (Phase-6: hardened).

All reads and writes go exclusively through db_layer + cache.
Every function requires an explicit `tenant_id` parameter derived from
the authenticated Firebase Auth UID (set by auth_middleware).

Security fixes:
- REMOVED: Plaintext token logging (was leaking partial tokens to stdout)
- REMOVED: os.environ pollution with per-tenant access tokens
- Added structured logging via observability module
"""

from typing import Dict

# db_layer imports — sole DB abstraction
from db_layer.tenants import tenants as _db_tenants
from db_layer.chatbot import chatbot_config as _db_chatbot_config, chatbot_rules as _db_chatbot_rules
from db_layer.chat_messages import chat_messages as _db_chat_messages
from db_layer.messages import messages as _db_messages
from db_layer.secrets import secrets as _secrets
from db_layer.encryption import encrypt_secret

# Cache layer
from cache import cache, fetch_cached_async, tenant_key, chatbot_config_key, settings_key, button_mappings_key
from observability import log_event


_DEFAULT_SETTINGS: Dict = {
    "business_account_id": "",
    "phone_number_id": "",
    "access_token": "",
    "webhook_verify_token": "",
    "meta_app_secret": "",
    "is_configured": False,
}

_DEFAULT_CHATBOT: Dict = {
    "is_enabled": False,
    "fallback_message": "Thank you for your message. Our team will get back to you soon.",
    "use_ai": False,
    "fallback_template_name": "",
    "fallback_cooldown_hours": 24,
}


# ── Accessor functions (cached, tenant-scoped) ──

async def get_settings(tenant_id: str) -> Dict:
    """Read WhatsApp settings for a specific tenant. Cached for 6 hours."""
    async def _fetch():
        tenant = await _db_tenants.get(tenant_id)
        if tenant:
            token = _secrets.resolve_wa_token(tenant)
            # SECURITY: Never log tokens — not even partially
            has_token = bool(token)
            log_event("settings_loaded", tenant_id=tenant_id,
                      detail=f"has_token={has_token}")
            return {
                "business_account_id": tenant.get("business_account_id", ""),
                "phone_number_id": tenant.get("phone_number_id", ""),
                "access_token": token,
                "webhook_verify_token": tenant.get("webhook_verify_token", ""),
                "meta_app_secret": tenant.get("meta_app_secret", ""),
                "is_configured": tenant.get("is_configured", False),
            }
        return dict(_DEFAULT_SETTINGS)

    return await fetch_cached_async(settings_key(tenant_id), _fetch)


async def get_chatbot_settings(tenant_id: str) -> Dict:
    """Read chatbot config for a specific tenant. Cached for 6 hours."""
    async def _fetch():
        cfg = await _db_chatbot_config.get(tenant_id)
        if cfg:
            return {
                "is_enabled": cfg.get("is_enabled", False),
                "fallback_message": cfg.get("fallback_message", _DEFAULT_CHATBOT["fallback_message"]),
                "use_ai": False,
                "fallback_template_name": cfg.get("fallback_template_name", ""),
                "fallback_cooldown_hours": int(cfg.get("fallback_cooldown_hours", 24)),
            }
        return dict(_DEFAULT_CHATBOT)

    return await fetch_cached_async(chatbot_config_key(tenant_id), _fetch)


async def get_chatbot_rules(tenant_id: str) -> list:
    """Read chatbot rules for a specific tenant. Delegated to db_layer which handles caching."""
    return await _db_chatbot_rules.list(tenant_id)


# ── Write functions (DB via db_layer, invalidate cache) ──

async def save_settings(tenant_id: str, settings_data: Dict):
    """Persist WhatsApp settings for a tenant.

    The access token is stored in the database so it survives server restarts.
    If no new token is provided in settings_data, the existing token in the database
    is preserved (not overwritten with empty string).

    SECURITY NOTE: os.environ is no longer polluted with per-tenant tokens.
    Tokens are resolved at runtime via secrets.resolve_wa_token().
    """
    access_token = settings_data.get("access_token", "").strip()
    # Strip "Bearer " prefix if accidentally included
    if access_token.lower().startswith("bearer "):
        access_token = access_token[7:].strip()

    meta_app_secret = settings_data.get("meta_app_secret", "").strip()

    upsert_data = {
        "business_account_id": settings_data.get("business_account_id", ""),
        "phone_number_id": settings_data.get("phone_number_id", ""),
        "webhook_verify_token": settings_data.get("webhook_verify_token", ""),
        "is_configured": settings_data.get("is_configured", False),
    }
    # Only overwrite access_token if a new one was explicitly provided
    if access_token:
        upsert_data["access_token"] = access_token
        log_event("settings_saved", tenant_id=tenant_id, detail="token updated")
    else:
        log_event("settings_saved", tenant_id=tenant_id, detail="token preserved (no new value)")

    # Only overwrite meta_app_secret if a new one was explicitly provided
    if meta_app_secret and meta_app_secret.strip():
        if not isinstance(meta_app_secret, str):
            raise ValueError("meta_app_secret must be a string")
        if meta_app_secret.startswith("enc:"):
            # Already encrypted → keep as-is
            upsert_data["meta_app_secret"] = meta_app_secret
        else:
            # Plain text → encrypt
            upsert_data["meta_app_secret"] = encrypt_secret(meta_app_secret)
        log_event("settings_saved", tenant_id=tenant_id, detail="meta_app_secret updated")

    await _db_tenants.upsert(tenant_id, upsert_data)
    # Invalidate cache so next read picks up new values
    cache.invalidate(settings_key(tenant_id))
    cache.invalidate(tenant_key(tenant_id))



async def save_chatbot_settings(tenant_id: str, chatbot_data: Dict):
    """Persist chatbot config for a tenant."""
    upsert_data = {
        "is_enabled": chatbot_data.get("is_enabled", False),
        "fallback_message": chatbot_data.get("fallback_message", ""),
        "use_ai": False,
        "fallback_template_name": chatbot_data.get("fallback_template_name", ""),
        "fallback_cooldown_hours": int(chatbot_data.get("fallback_cooldown_hours", 24)),
    }

    await _db_chatbot_config.upsert(tenant_id, upsert_data)
    # Invalidate all related caches
    cache.invalidate(chatbot_config_key(tenant_id))
    cache.invalidate(button_mappings_key(tenant_id))
    log_event("chatbot_settings_saved", tenant_id=tenant_id)


# ── Ephemeral runtime state (NOT persisted — process-local only) ──
active_campaigns: Dict = {}


async def add_message(tenant_id: str, message_data: Dict):
    """Convenience wrapper for adding a message to the history."""
    await _db_messages.add(tenant_id, message_data)


async def add_message_conn(tenant_id: str, message_data: Dict, conn):
    await _db_messages.add(tenant_id, message_data, conn=conn)
