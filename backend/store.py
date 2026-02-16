"""Shared data store for WappFlow – Multi-tenant Firestore-only (Phase-3).

All reads and writes go exclusively through db_layer.
Every function requires an explicit `tenant_id` parameter derived from
the authenticated Firebase Auth UID (set by auth_middleware).

The only runtime state is `active_campaigns` — an ephemeral dict used to signal
stop requests to in-flight campaign tasks within a single process.  It is NOT
persisted; campaign resume after restart is handled via Firestore campaign status.
"""

import os
from typing import Dict

# db_layer imports — sole Firestore abstraction
from db_layer.tenants import tenants as _db_tenants
from db_layer.chatbot import chatbot_config as _db_chatbot_config, chatbot_rules as _db_chatbot_rules
from db_layer.chat_messages import chat_messages as _db_chat_messages
from db_layer.messages import messages as _db_messages
from db_layer.secrets import secrets as _secrets

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
    "use_ai": True,
    "ai_system_prompt": "You are a helpful customer service assistant. Be friendly, concise, and professional.",
    "openai_api_key": "",
}


# ── Accessor functions (Firestore-only, tenant-scoped) ──

def get_settings(tenant_id: str) -> Dict:
    """Read WhatsApp settings for a specific tenant from Firestore."""
    tenant = _db_tenants.get(tenant_id)
    if tenant:
        return {
            "business_account_id": tenant.get("business_account_id", ""),
            "phone_number_id": tenant.get("phone_number_id", ""),
            "access_token": _secrets.resolve_wa_token(tenant),
            "webhook_verify_token": tenant.get("webhook_verify_token", ""),
            "is_configured": tenant.get("is_configured", False),
        }
    return dict(_DEFAULT_SETTINGS)


def get_chatbot_settings(tenant_id: str) -> Dict:
    """Read chatbot config for a specific tenant from Firestore."""
    cfg = _db_chatbot_config.get(tenant_id)
    if cfg:
        return {
            "is_enabled": cfg.get("is_enabled", False),
            "fallback_message": cfg.get("fallback_message", _DEFAULT_CHATBOT["fallback_message"]),
            "use_ai": cfg.get("use_ai", True),
            "ai_system_prompt": cfg.get("ai_system_prompt", _DEFAULT_CHATBOT["ai_system_prompt"]),
            "openai_api_key": _secrets.resolve_openai_key(cfg),
        }
    return dict(_DEFAULT_CHATBOT)


def get_chatbot_rules(tenant_id: str) -> list:
    """Read chatbot rules for a specific tenant from Firestore."""
    return _db_chatbot_rules.list(tenant_id)


# ── Write functions (Firestore-only via db_layer, tenant-scoped) ──

def save_settings(tenant_id: str, settings_data: Dict):
    """Persist WhatsApp settings for a tenant. Secrets stored as refs."""
    access_token = settings_data.get("access_token", "")
    _db_tenants.upsert(tenant_id, {
        "business_account_id": settings_data.get("business_account_id", ""),
        "phone_number_id": settings_data.get("phone_number_id", ""),
        "webhook_verify_token": settings_data.get("webhook_verify_token", ""),
        "is_configured": settings_data.get("is_configured", False),
        "token_ref": _secrets.make_ref(f"WHATSAPP_ACCESS_TOKEN_{tenant_id}"),
    })
    # Set env var so resolve works immediately in this process
    if access_token:
        os.environ[f"WHATSAPP_ACCESS_TOKEN_{tenant_id}"] = access_token
    print(f"[STORE] save_settings tenant={tenant_id} → firestore")


def save_chatbot_settings(tenant_id: str, chatbot_data: Dict):
    """Persist chatbot config for a tenant."""
    openai_key = chatbot_data.get("openai_api_key", "")
    _db_chatbot_config.upsert(tenant_id, {
        "is_enabled": chatbot_data.get("is_enabled", False),
        "fallback_message": chatbot_data.get("fallback_message", ""),
        "use_ai": chatbot_data.get("use_ai", True),
        "ai_system_prompt": chatbot_data.get("ai_system_prompt", ""),
        "openai_key_ref": _secrets.make_ref(f"OPENAI_API_KEY_{tenant_id}"),
    })
    if openai_key:
        os.environ[f"OPENAI_API_KEY_{tenant_id}"] = openai_key
    print(f"[STORE] save_chatbot_settings tenant={tenant_id} → firestore")


# ── Ephemeral runtime state (NOT persisted — process-local only) ──
# Used to signal stop requests to in-flight campaign asyncio tasks.
# Campaign resume after restart is handled by querying Firestore for
# campaigns with status="running".
active_campaigns: Dict = {}
