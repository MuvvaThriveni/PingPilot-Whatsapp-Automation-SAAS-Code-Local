"""Shared data store for WappFlow – settings persist to Firebase Firestore."""

import json
import os
from typing import List, Dict
from firebase_config import get_db

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
    "use_ai": True,
    "ai_system_prompt": "You are a helpful customer service assistant. Be friendly, concise, and professional.",
    "openai_api_key": "",
}


def _load_from_firestore(collection: str, doc_id: str, defaults: Dict) -> Dict:
    """Load document from Firestore, falling back to defaults."""
    db = get_db()
    if db:
        try:
            doc = db.collection(collection).document(doc_id).get()
            if doc.exists:
                return {**defaults, **doc.to_dict()}
        except Exception as e:
            print(f"[WARN] Failed to load {collection}/{doc_id} from Firestore: {e}")
    return dict(defaults)


def _load_list_from_firestore(collection: str) -> List[dict]:
    """Load all documents from a Firestore collection as a list."""
    db = get_db()
    if db:
        try:
            docs = db.collection(collection).stream()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"[WARN] Failed to load {collection} from Firestore: {e}")
    return []


def _load_settings() -> Dict:
    """Load settings from Firestore, falling back to local file then defaults."""
    # Try Firestore first
    result = _load_from_firestore("config", "settings", _DEFAULT_SETTINGS)
    if result != _DEFAULT_SETTINGS:
        return result
    
    # Fallback to local file
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
    """Load chatbot settings from Firestore, falling back to local file then defaults."""
    result = _load_from_firestore("config", "chatbot", _DEFAULT_CHATBOT)
    if result != _DEFAULT_CHATBOT:
        return result
    
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r") as f:
                data = json.load(f)
            return {**_DEFAULT_CHATBOT, **data.get("chatbot", {})}
        except Exception:
            pass
    return dict(_DEFAULT_CHATBOT)


def _load_chatbot_rules() -> List[dict]:
    """Load chatbot rules from Firestore."""
    rules = _load_list_from_firestore("chatbot_rules")
    if rules:
        return rules
    
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r") as f:
                data = json.load(f)
            return data.get("chatbot_rules", [])
        except Exception:
            pass
    return []


def _load_conversations() -> List[dict]:
    """Load conversations from Firestore."""
    db = get_db()
    if db:
        try:
            docs = db.collection("conversations").order_by("created_at").limit(500).stream()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"[WARN] Failed to load conversations from Firestore: {e}")
    return []


def save_to_disk():
    """Persist current settings, chatbot config to Firestore (and local backup)."""
    db = get_db()
    
    # Save to Firestore
    if db:
        try:
            db.collection("config").document("settings").set(dict(settings_store))
            db.collection("config").document("chatbot").set(dict(chatbot_settings))
            print("[INFO] Saved to Firestore")
        except Exception as e:
            print(f"[WARN] Failed to save to Firestore: {e}")
    
    # Also save local backup
    data = {
        "settings": {k: v for k, v in settings_store.items()},
        "chatbot": {k: v for k, v in chatbot_settings.items()},
        "chatbot_rules": chatbot_rules,
    }
    try:
        with open(_SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[WARN] Failed to save local backup: {e}")


def save_conversation(conv: dict):
    """Save a single conversation to Firestore."""
    db = get_db()
    if db:
        try:
            db.collection("conversations").add(conv)
        except Exception as e:
            print(f"[WARN] Failed to save conversation to Firestore: {e}")
    # Also keep in memory
    conversations.append(conv)


def save_message_log(log: dict):
    """Save a message log to Firestore."""
    db = get_db()
    if db:
        try:
            db.collection("message_logs").add(log)
        except Exception as e:
            print(f"[WARN] Failed to save message log to Firestore: {e}")
    message_logs.append(log)


# Initialize from Firestore (with local fallback)
settings_store: Dict = _load_settings()
chatbot_settings: Dict = _load_chatbot()
chatbot_rules: List[dict] = _load_chatbot_rules()
conversations: List[dict] = _load_conversations()

message_logs: List[dict] = []
bulk_campaigns: List[dict] = []
active_campaigns: Dict = {}
template_cache: Dict = {}  # key: "name|lang" -> raw template components from Meta API
