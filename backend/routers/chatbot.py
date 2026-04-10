"""Chatbot routes – auto-reply settings, rules, button mappings, and conversations (Phase-8: fully dynamic).

All chatbot behavior is now DB-driven and tenant-specific:
- Settings with configurable fallback template + cooldown
- Keyword rules with response_type (text/template) and match_type (exact/contains/starts_with)
- Button→template mappings (replaces hardcoded defaults)
- Conversations and user list (cached)
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from store import get_chatbot_settings as _get_chatbot_settings, save_chatbot_settings as _save_chatbot_settings, get_chatbot_rules as _get_chatbot_rules
from db_layer.chatbot import chatbot_rules as _db_rules
from db_layer.chatbot_button_mappings import button_mappings as _db_button_mappings
from db_layer.chat_messages import chat_messages as _db_chat_messages
from cache import cache, chat_users_key, fetch_cached_async

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


# ── Pydantic Models ──────────────────────────────────────────────

class ChatbotSettingsModel(BaseModel):
    is_enabled: bool
    fallback_message: str
    fallback_template_name: Optional[str] = ""
    fallback_cooldown_hours: Optional[int] = 24
    use_ai: Optional[bool] = None
    ai_system_prompt: Optional[str] = None
    openai_api_key: Optional[str] = None


class ChatbotRuleModel(BaseModel):
    keyword: str
    response: str
    response_type: Optional[str] = "text"       # "text" or "template"
    match_type: Optional[str] = "contains"      # "exact", "contains", "starts_with"
    priority: Optional[int] = 0
    is_active: Optional[bool] = None


class ButtonMappingModel(BaseModel):
    button_text: str
    template_name: str
    is_active: Optional[bool] = True
    priority: Optional[int] = 0


# ── Settings Endpoints ───────────────────────────────────────────

@router.get("/settings")
async def get_chatbot_settings(request: Request):
    tenant_id = request.state.tenant_id
    return {"settings": await _get_chatbot_settings(tenant_id)}


@router.put("/settings")
async def update_chatbot_settings(request: Request, data: ChatbotSettingsModel):
    tenant_id = request.state.tenant_id
    current = await _get_chatbot_settings(tenant_id)
    current["is_enabled"] = data.is_enabled
    current["fallback_message"] = data.fallback_message
    
    # AI logic commented out to reflect the transition to a purely rule-based and triggered flow.
    # if data.use_ai is not None:
    #     current["use_ai"] = data.use_ai
    # if data.ai_system_prompt is not None:
    #     current["ai_system_prompt"] = data.ai_system_prompt
    # if data.openai_api_key is not None:
    #     current["openai_api_key"] = data.openai_api_key.strip()
    
    current["use_ai"] = False

    # Persist new fallback template settings
    if data.fallback_template_name is not None:
        current["fallback_template_name"] = data.fallback_template_name
    if data.fallback_cooldown_hours is not None:
        current["fallback_cooldown_hours"] = data.fallback_cooldown_hours
    
    await _save_chatbot_settings(tenant_id, current)
    return {"success": True, "message": "Settings updated"}


# ── Rules Endpoints ──────────────────────────────────────────────

@router.get("/rules")
async def get_chatbot_rules(request: Request):
    tenant_id = request.state.tenant_id
    rules = await _get_chatbot_rules(tenant_id)
    return {"rules": rules}


@router.post("/rules")
async def create_chatbot_rule(request: Request, rule: ChatbotRuleModel):
    tenant_id = request.state.tenant_id
    new_rule = {
        "keyword": rule.keyword.lower().strip(),
        "response": rule.response,
        "response_type": rule.response_type or "text",
        "match_type": rule.match_type or "contains",
        "priority": rule.priority if rule.priority is not None else 0,
        "is_active": True,  # FIX: must be Python bool, not int 1 — psycopg3 is strict about BOOLEAN columns
    }
    result = await _db_rules.create(tenant_id, new_rule)
    return {"rule": result}


@router.put("/rules/{rule_id}")
async def update_chatbot_rule(request: Request, rule_id: int, rule: ChatbotRuleModel):
    tenant_id = request.state.tenant_id
    existing_rules = await _get_chatbot_rules(tenant_id)
    existing = next((r for r in existing_rules if r.get("id") == rule_id), None)
    if not existing:
        return JSONResponse(status_code=404, content={"error": "Rule not found"})

    doc_id = existing.get("_doc_id")
    if not doc_id:
        return JSONResponse(status_code=404, content={"error": "Rule not found in database"})

    update_data = {
        "keyword": rule.keyword.lower().strip(),
        "response": rule.response,
        "response_type": rule.response_type or "text",
        "match_type": rule.match_type or "contains",
        "priority": rule.priority,
    }
    if rule.is_active is not None:
        update_data["is_active"] = 1 if rule.is_active else 0

    await _db_rules.update(tenant_id, doc_id, update_data)

    # Return the merged rule for API compat
    existing.update(update_data)
    return {"rule": existing}


@router.delete("/rules/{rule_id}")
async def delete_chatbot_rule(request: Request, rule_id: int):
    tenant_id = request.state.tenant_id
    existing_rules = await _get_chatbot_rules(tenant_id)
    existing = next((r for r in existing_rules if r.get("id") == rule_id), None)
    if not existing:
        return JSONResponse(status_code=404, content={"error": "Rule not found"})

    doc_id = existing.get("_doc_id")
    if doc_id:
        await _db_rules.delete(tenant_id, doc_id)

    return {"success": True, "message": "Rule deleted"}


# ── Button Mapping Endpoints (NEW — Phase-8) ─────────────────────

@router.get("/button-mappings")
async def list_button_mappings(request: Request):
    """List all button→template mappings for the tenant."""
    tenant_id = request.state.tenant_id
    mappings = await _db_button_mappings.list(tenant_id)
    return {"mappings": mappings}


@router.post("/button-mappings")
async def create_button_mapping(request: Request, data: ButtonMappingModel):
    """Create a new button→template mapping."""
    tenant_id = request.state.tenant_id
    bt = (data.button_text or "").strip()
    if not bt:
        return JSONResponse(
            status_code=400,
            content={"error": "button_text is required"},
        )
    result = await _db_button_mappings.create(tenant_id, {
        "button_text": bt,
        "template_name": data.template_name,
        "is_active": data.is_active if data.is_active is not None else True,
        "priority": data.priority or 0,
    })
    return {"mapping": result}


@router.put("/button-mappings/{mapping_id}")
async def update_button_mapping(request: Request, mapping_id: int, data: ButtonMappingModel):
    """Update an existing button→template mapping."""
    tenant_id = request.state.tenant_id
    update_data = {}
    if data.button_text is not None:
        update_data["button_text"] = (data.button_text or "").strip()
    if data.template_name:
        update_data["template_name"] = data.template_name
    if data.is_active is not None:
        update_data["is_active"] = data.is_active
    if data.priority is not None:
        update_data["priority"] = data.priority

    try:
        await _db_button_mappings.update(tenant_id, mapping_id, update_data)
    except Exception:
        return JSONResponse(status_code=500, content={"error": "Failed to update mapping"})

    return {"success": True, "message": "Mapping updated"}


@router.delete("/button-mappings/{mapping_id}")
async def delete_button_mapping(request: Request, mapping_id: int):
    """Delete a button→template mapping."""
    tenant_id = request.state.tenant_id
    try:
        await _db_button_mappings.delete(tenant_id, mapping_id)
    except Exception:
        return JSONResponse(status_code=500, content={"error": "Failed to delete mapping"})

    return {"success": True, "message": "Mapping deleted"}


# ── Conversation Endpoints ───────────────────────────────────────

@router.get("/conversations")
async def get_conversations(request: Request, limit: int = 50, cursor: str = None):
    tenant_id = request.state.tenant_id
    db_msgs, next_cursor = await _db_chat_messages.get_conversation_list(tenant_id, limit=limit, cursor=cursor)
    return {"conversations": [_remap_chat_msg(m) for m in db_msgs], "next_cursor": next_cursor}


@router.get("/users")
async def get_chat_users(request: Request):
    """Get list of unique users with their latest message. Cached for 15 s."""
    tenant_id = request.state.tenant_id

    async def _fetch_users():
        raw, _ = await _db_chat_messages.get_conversation_list(tenant_id, limit=200)
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

    users_list = await fetch_cached_async(chat_users_key(tenant_id), _fetch_users, ttl=15.0)
    return {"users": users_list}


@router.get("/conversations/{phone}")
async def get_user_conversations(request: Request, phone: str, limit: int = 50, cursor: str = None):
    """Get all conversations for a specific user."""
    tenant_id = request.state.tenant_id
    db_msgs, next_cursor = await _db_chat_messages.get_user_messages(tenant_id, phone, limit=limit, cursor=cursor)
    return {"conversations": [_remap_chat_msg(m) for m in db_msgs], "next_cursor": next_cursor}
