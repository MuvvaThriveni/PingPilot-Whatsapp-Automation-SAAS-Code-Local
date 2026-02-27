"""Shared data store for WappFlow – Multi-tenant Firestore-only (Phase-5: cached).

All reads and writes go exclusively through db_layer + cache.
Every function requires an explicit `tenant_id` parameter derived from
the authenticated Firebase Auth UID (set by auth_middleware).

Reads are cached in-memory (30–120 s TTL) to drastically reduce Firestore
read operations.  Writes invalidate the relevant cache entries immediately.
"""

import os
from typing import Dict

# db_layer imports — sole Firestore abstraction
from db_layer.tenants import tenants as _db_tenants
from db_layer.chatbot import chatbot_config as _db_chatbot_config, chatbot_rules as _db_chatbot_rules
from db_layer.chat_messages import chat_messages as _db_chat_messages
from db_layer.messages import messages as _db_messages
from db_layer.secrets import secrets as _secrets

# Cache layer
from cache import cache, fetch_cached, tenant_key, chatbot_config_key, chatbot_rules_key, settings_key


_DEFAULT_SETTINGS: Dict = {
    "business_account_id": "",
    "phone_number_id": "",
    "access_token": "",
    "webhook_verify_token": "",
    "is_configured": False,
}

_DEFAULT_CHATBOT: Dict = {
    "is_enabled": False,
    "fallback_message": "Thank you for your message. Our team will get back to you soon.",
    "use_ai": False,  
}


# ── Accessor functions (cached, Firestore-only, tenant-scoped) ──

def get_settings(tenant_id: str) -> Dict:
    """Read WhatsApp settings for a specific tenant. Cached for 6 hours."""
    def _fetch():
        tenant = _db_tenants.get(tenant_id)
        if tenant:
            token = _secrets.resolve_wa_token(tenant)
            print(f"[STORE] Resolved token for {tenant_id}: {token[:5]}... (len={len(token)})")
            return {
                "business_account_id": tenant.get("business_account_id", ""),
                "phone_number_id": tenant.get("phone_number_id", ""),
                "access_token": token,
                "webhook_verify_token": tenant.get("webhook_verify_token", ""),
                "is_configured": tenant.get("is_configured", False),
            }
        return dict(_DEFAULT_SETTINGS)

    return fetch_cached(settings_key(tenant_id), _fetch)


def get_chatbot_settings(tenant_id: str) -> Dict:
    """Read chatbot config for a specific tenant. Cached for 6 hours."""
    def _fetch():
        cfg = _db_chatbot_config.get(tenant_id)
        if cfg:
            return {
                "is_enabled": cfg.get("is_enabled", False),
                "fallback_message": cfg.get("fallback_message", _DEFAULT_CHATBOT["fallback_message"]),
                "use_ai": False,
            }
        return dict(_DEFAULT_CHATBOT)

    return fetch_cached(chatbot_config_key(tenant_id), _fetch)


def get_chatbot_rules(tenant_id: str) -> list:
    """Read chatbot rules for a specific tenant. Cached for 6 hours."""
    return fetch_cached(
        chatbot_rules_key(tenant_id),
        lambda: _db_chatbot_rules.list(tenant_id),
    )


# ── Write functions (Firestore-only via db_layer, invalidate cache) ──

def save_settings(tenant_id: str, settings_data: Dict):
    """Persist WhatsApp settings for a tenant.

    The access token is stored directly in Firestore so it survives server restarts.
    If no new token is provided in settings_data, the existing token in Firestore
    is preserved (not overwritten with empty string).
    """
    access_token = settings_data.get("access_token", "").strip()
    # Strip "Bearer " prefix if accidentally included
    if access_token.lower().startswith("bearer "):
        access_token = access_token[7:].strip()

    upsert_data = {
        "business_account_id": settings_data.get("business_account_id", ""),
        "phone_number_id": settings_data.get("phone_number_id", ""),
        "webhook_verify_token": settings_data.get("webhook_verify_token", ""),
        "is_configured": settings_data.get("is_configured", False),
    }
    # Only overwrite access_token if a new one was explicitly provided
    if access_token:
        upsert_data["access_token"] = access_token
        os.environ[f"WHATSAPP_ACCESS_TOKEN_{tenant_id}"] = access_token
        print(f"[STORE] save_settings tenant={tenant_id} → token updated in Firestore")
    else:
        print(f"[STORE] save_settings tenant={tenant_id} → token NOT updated (kept existing)")

    _db_tenants.upsert(tenant_id, upsert_data)
    # Invalidate cache so next read picks up new values
    cache.invalidate(settings_key(tenant_id))
    cache.invalidate(tenant_key(tenant_id))



def save_chatbot_settings(tenant_id: str, chatbot_data: Dict):
    """Persist chatbot config for a tenant."""
    # openai_key = chatbot_data.get("openai_api_key", "").strip()
    upsert_data = {
        "is_enabled": chatbot_data.get("is_enabled", False),
        "fallback_message": chatbot_data.get("fallback_message", ""),
        "use_ai": False, # chatbot_data.get("use_ai", True),
        # "ai_system_prompt": chatbot_data.get("ai_system_prompt", ""),
    }
    # Store key directly in Firestore so it survives server restarts
    # if openai_key:
    #     upsert_data["openai_api_key"] = openai_key

    _db_chatbot_config.upsert(tenant_id, upsert_data)
    # if openai_key:
    #     os.environ[f"OPENAI_API_KEY_{tenant_id}"] = openai_key
    # Invalidate cache
    cache.invalidate(chatbot_config_key(tenant_id))
    print(f"[STORE] save_chatbot_settings tenant={tenant_id} → firestore (cache invalidated)")


# ── Ephemeral runtime state (NOT persisted — process-local only) ──
active_campaigns: Dict = {}


def add_message(tenant_id: str, message_data: Dict):
    """Convenience wrapper for adding a message to the history."""
    _db_messages.add(tenant_id, message_data)

