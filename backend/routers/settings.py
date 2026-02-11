"""Settings routes – WhatsApp Business API configuration."""

import datetime
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from store import settings_store, message_logs, save_to_disk
from services.whatsapp import WhatsAppService

router = APIRouter(prefix="/api/settings", tags=["settings"])


class WhatsAppSettings(BaseModel):
    business_account_id: str
    phone_number_id: str
    access_token: str
    webhook_verify_token: Optional[str] = ""


@router.get("/whatsapp")
async def get_whatsapp_settings():
    return {
        "settings": {
            "business_account_id": settings_store["business_account_id"],
            "phone_number_id": settings_store["phone_number_id"],
            "webhook_verify_token": settings_store["webhook_verify_token"],
            "is_configured": settings_store["is_configured"],
        }
    }


@router.post("/whatsapp")
async def save_whatsapp_settings(data: WhatsAppSettings):
    access_token = (data.access_token or "").strip()
    if access_token.lower().startswith("bearer "):
        access_token = access_token[7:].strip()

    settings_store["business_account_id"] = (data.business_account_id or "").strip()
    settings_store["phone_number_id"] = (data.phone_number_id or "").strip()
    settings_store["access_token"] = access_token
    settings_store["webhook_verify_token"] = (data.webhook_verify_token or "").strip()
    settings_store["is_configured"] = True
    save_to_disk()
    return {"message": "Settings saved successfully"}


@router.post("/whatsapp/test")
async def test_whatsapp_connection():
    if not settings_store["is_configured"]:
        return JSONResponse(status_code=400, content={"error": "WhatsApp settings not configured"})

    whatsapp = WhatsAppService(settings_store["phone_number_id"], settings_store["access_token"])
    result = await whatsapp.test_connection()

    if result["success"]:
        return {
            "success": True,
            "message": "Connection successful",
            "phoneNumber": result.get("phoneNumber"),
            "verifiedName": result.get("verifiedName"),
            "data": result.get("data"),
        }
    return JSONResponse(status_code=400, content={"error": result["error"]})


@router.get("/usage")
async def get_usage_stats():
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    today_logs = [l for l in message_logs if l.get("created_at", "").startswith(today)]
    return {
        "today": {
            "total": len(today_logs),
            "successful": len([l for l in today_logs if l.get("status") in ["sent", "delivered"]]),
            "failed": len([l for l in today_logs if l.get("status") == "failed"]),
        },
        "month": {
            "total": len(message_logs),
            "successful": len([l for l in message_logs if l.get("status") in ["sent", "delivered"]]),
            "failed": len([l for l in message_logs if l.get("status") == "failed"]),
        },
        "byProduct": [],
    }
