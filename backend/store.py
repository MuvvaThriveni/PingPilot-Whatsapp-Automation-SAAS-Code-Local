"""Shared data store for WappFlow – settings persist to disk via JSON."""

import json
import os
from typing import List, Dict

_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

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
    "use_ai": True,  # Use ChatGPT for responses when enabled
    "ai_system_prompt": "You are a helpful customer service assistant. Be friendly, concise, and professional.",
    "openai_api_key": "",
}


def _load_settings() -> Dict:
    """Load settings from disk, falling back to defaults."""
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r") as f:
                data = json.load(f)
            merged = {**_DEFAULT_SETTINGS, **data.get("settings", {})}
            return merged
        except Exception:
            pass
    return dict(_DEFAULT_SETTINGS)


def _load_chatbot() -> Dict:
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r") as f:
                data = json.load(f)
            return {**_DEFAULT_CHATBOT, **data.get("chatbot", {})}
        except Exception:
            pass
    return dict(_DEFAULT_CHATBOT)


def _load_chatbot_rules() -> List[dict]:
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r") as f:
                data = json.load(f)
            return data.get("chatbot_rules", [])
        except Exception:
            pass
    return []


def save_to_disk():
    """Persist current settings, chatbot config, and rules to disk."""
    data = {
        "settings": {k: v for k, v in settings_store.items()},
        "chatbot": {k: v for k, v in chatbot_settings.items()},
        "chatbot_rules": chatbot_rules,
    }
    try:
        with open(_SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[WARN] Failed to save settings: {e}")


# Initialise from disk
settings_store: Dict = _load_settings()
chatbot_settings: Dict = _load_chatbot()
chatbot_rules: List[dict] = _load_chatbot_rules()

message_logs: List[dict] = []
bulk_campaigns: List[dict] = []
conversations: List[dict] = []
active_campaigns: Dict = {}
template_cache: Dict = {}  # key: "name|lang" -> raw template components from Meta API
