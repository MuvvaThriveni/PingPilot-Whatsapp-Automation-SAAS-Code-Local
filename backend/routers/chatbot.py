"""Chatbot routes – auto-reply settings, rules, and conversations (Phase-5: optimized).

Caches expensive user-list endpoint. Reduces read-heavy polling impact.
"""

import datetime
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from store import get_chatbot_settings as _get_chatbot_settings, save_chatbot_settings as _save_chatbot_settings, get_chatbot_rules as _get_chatbot_rules
from db_layer.chatbot import chatbot_rules as _db_rules
from db_layer.chat_messages import chat_messages as _db_chat_messages
from cache import cache, chat_users_key

router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])


def _remap_chat_msg(m: dict) -> dict:
    """Remap db_layer chat_messages fields to legacy API format."""
    return {
        "sender_phone": m.get("contact_phone", ""),
        "sender_name": m.get("contact_name", "Unknown"),
        "message_text": m.get("message_text", ""),
        "direction": m.get("direction", ""),
        "created_at": m.get("created_at", ""),
    }


class ChatbotSettingsModel(BaseModel):
    is_enabled: bool
    fallback_message: str
    use_ai: Optional[bool] = None
    ai_system_prompt: Optional[str] = None
    openai_api_key: Optional[str] = None


class ChatbotRuleModel(BaseModel):
    keyword: str
    response: str
    priority: Optional[int] = 0
    is_active: Optional[bool] = None


@router.get("/settings")
async def get_chatbot_settings(request: Request):
    tenant_id = request.state.tenant_id
    return {"settings": _get_chatbot_settings(tenant_id)}


@router.put("/settings")
async def update_chatbot_settings(request: Request, data: ChatbotSettingsModel):
    tenant_id = request.state.tenant_id
    current = _get_chatbot_settings(tenant_id)
    current["is_enabled"] = data.is_enabled
    current["fallback_message"] = data.fallback_message
    if data.use_ai is not None:
        current["use_ai"] = data.use_ai
    if data.ai_system_prompt is not None:
        current["ai_system_prompt"] = data.ai_system_prompt
    if data.openai_api_key is not None:
        current["openai_api_key"] = data.openai_api_key.strip()
    _save_chatbot_settings(tenant_id, current)
    return {"success": True, "message": "Settings updated"}


@router.get("/rules")
async def get_chatbot_rules(request: Request):
    tenant_id = request.state.tenant_id
    rules = _get_chatbot_rules(tenant_id)
    return {"rules": rules}


@router.post("/rules")
async def create_chatbot_rule(request: Request, rule: ChatbotRuleModel):
    tenant_id = request.state.tenant_id
    existing_rules = _get_chatbot_rules(tenant_id)
    max_id = max((r.get("id", 0) for r in existing_rules), default=0)
    new_rule = {
        "id": max_id + 1,
        "keyword": rule.keyword.lower().strip(),
        "response": rule.response,
        "priority": rule.priority,
        "is_active": 1,
        "created_at": datetime.datetime.now().isoformat(),
    }
    result = _db_rules.create(tenant_id, new_rule)
    return {"rule": result}


@router.put("/rules/{rule_id}")
async def update_chatbot_rule(request: Request, rule_id: int, rule: ChatbotRuleModel):
    tenant_id = request.state.tenant_id
    existing_rules = _get_chatbot_rules(tenant_id)
    existing = next((r for r in existing_rules if r.get("id") == rule_id), None)
    if not existing:
        return JSONResponse(status_code=404, content={"error": "Rule not found"})

    doc_id = existing.get("_doc_id")
    if not doc_id:
        return JSONResponse(status_code=404, content={"error": "Rule not found in Firestore"})

    update_data = {
        "keyword": rule.keyword.lower().strip(),
        "response": rule.response,
        "priority": rule.priority,
    }
    if rule.is_active is not None:
        update_data["is_active"] = 1 if rule.is_active else 0

    _db_rules.update(doc_id, update_data)

    # Return the merged rule for API compat
    existing.update(update_data)
    return {"rule": existing}


@router.delete("/rules/{rule_id}")
async def delete_chatbot_rule(request: Request, rule_id: int):
    tenant_id = request.state.tenant_id
    existing_rules = _get_chatbot_rules(tenant_id)
    existing = next((r for r in existing_rules if r.get("id") == rule_id), None)
    if not existing:
        return JSONResponse(status_code=404, content={"error": "Rule not found"})

    doc_id = existing.get("_doc_id")
    if doc_id:
        _db_rules.delete(doc_id)

    return {"success": True, "message": "Rule deleted"}


@router.get("/conversations")
async def get_conversations(request: Request, limit: int = 50, cursor: str = None):
    tenant_id = request.state.tenant_id
    db_msgs, next_cursor = _db_chat_messages.get_conversation_list(tenant_id, limit=limit, cursor=cursor)
    return {"conversations": [_remap_chat_msg(m) for m in db_msgs], "next_cursor": next_cursor}


@router.get("/users")
async def get_chat_users(request: Request):
    """Get list of unique users with their latest message. Cached for 15 s."""
    tenant_id = request.state.tenant_id

    def _fetch_users():
        raw, _ = _db_chat_messages.get_conversation_list(tenant_id, limit=200)
        source = [_remap_chat_msg(m) for m in raw]

        users_map = {}
        for conv in source:
            phone = conv.get("sender_phone", "")
            if not phone:
                continue
            if phone not in users_map:
                users_map[phone] = {
                    "phone": phone,
                    "name": conv.get("sender_name", "Unknown"),
                    "last_message": conv.get("message_text", ""),
                    "last_message_at": conv.get("created_at", ""),
                    "direction": conv.get("direction", "incoming"),
                }
            else:
                if conv.get("created_at", "") > users_map[phone]["last_message_at"]:
                    users_map[phone]["last_message"] = conv.get("message_text", "")
                    users_map[phone]["last_message_at"] = conv.get("created_at", "")
                    users_map[phone]["direction"] = conv.get("direction", "incoming")
                if conv.get("sender_name") and conv.get("sender_name") != "Unknown":
                    users_map[phone]["name"] = conv.get("sender_name")

        return sorted(users_map.values(), key=lambda x: x["last_message_at"], reverse=True)

    users_list = cache.get_or_fetch(chat_users_key(tenant_id), _fetch_users, ttl=15.0)
    return {"users": users_list}


@router.get("/conversations/{phone}")
async def get_user_conversations(request: Request, phone: str, limit: int = 50, cursor: str = None):
    """Get all conversations for a specific user."""
    tenant_id = request.state.tenant_id
    db_msgs, next_cursor = _db_chat_messages.get_user_messages(tenant_id, phone, limit=limit, cursor=cursor)
    return {"conversations": [_remap_chat_msg(m) for m in db_msgs], "next_cursor": next_cursor}
