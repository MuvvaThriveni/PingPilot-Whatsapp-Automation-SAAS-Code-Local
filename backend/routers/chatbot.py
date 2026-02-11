"""Chatbot routes – auto-reply settings, rules, and conversations."""

import datetime
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from store import chatbot_settings, chatbot_rules, conversations, save_to_disk

router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])


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
async def get_chatbot_settings():
    return {"settings": chatbot_settings}


@router.put("/settings")
async def update_chatbot_settings(data: ChatbotSettingsModel):
    chatbot_settings["is_enabled"] = data.is_enabled
    chatbot_settings["fallback_message"] = data.fallback_message
    if data.use_ai is not None:
        chatbot_settings["use_ai"] = data.use_ai
    if data.ai_system_prompt is not None:
        chatbot_settings["ai_system_prompt"] = data.ai_system_prompt
    if data.openai_api_key is not None:
        chatbot_settings["openai_api_key"] = data.openai_api_key.strip()
    save_to_disk()
    return {"success": True, "message": "Settings updated"}


@router.get("/rules")
async def get_chatbot_rules():
    return {"rules": chatbot_rules}


@router.post("/rules")
async def create_chatbot_rule(rule: ChatbotRuleModel):
    new_rule = {
        "id": (max((r["id"] for r in chatbot_rules), default=0)) + 1,
        "keyword": rule.keyword.lower().strip(),
        "response": rule.response,
        "priority": rule.priority,
        "is_active": 1,
        "created_at": datetime.datetime.now().isoformat(),
    }
    chatbot_rules.append(new_rule)
    save_to_disk()
    return {"rule": new_rule}


@router.put("/rules/{rule_id}")
async def update_chatbot_rule(rule_id: int, rule: ChatbotRuleModel):
    existing = next((r for r in chatbot_rules if r["id"] == rule_id), None)
    if not existing:
        return JSONResponse(status_code=404, content={"error": "Rule not found"})

    existing["keyword"] = rule.keyword.lower().strip()
    existing["response"] = rule.response
    existing["priority"] = rule.priority
    if rule.is_active is not None:
        existing["is_active"] = 1 if rule.is_active else 0
    save_to_disk()
    return {"rule": existing}


@router.delete("/rules/{rule_id}")
async def delete_chatbot_rule(rule_id: int):
    global chatbot_rules
    idx = next((i for i, r in enumerate(chatbot_rules) if r["id"] == rule_id), None)
    if idx is None:
        return JSONResponse(status_code=404, content={"error": "Rule not found"})
    from store import chatbot_rules as cr
    cr.pop(idx)
    save_to_disk()
    return {"success": True, "message": "Rule deleted"}


@router.get("/conversations")
async def get_conversations():
    return {
        "conversations": sorted(
            conversations, key=lambda x: x.get("created_at", ""), reverse=True
        )[:100]
    }
