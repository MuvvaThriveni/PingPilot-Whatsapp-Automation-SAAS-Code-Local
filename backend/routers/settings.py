"""Settings routes – WhatsApp Business API configuration (Phase-3: multi-tenant)."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from store import get_settings, save_settings
from services.whatsapp import WhatsAppService
from db_layer.messages import messages as _db_messages

router = APIRouter(prefix="/api/settings", tags=["settings"])


class WhatsAppSettings(BaseModel):
    business_account_id: str
    phone_number_id: str
    access_token: Optional[str] = ""   # optional — omit to keep existing token
    webhook_verify_token: Optional[str] = ""


@router.get("/whatsapp")
async def get_whatsapp_settings(request: Request):
    tenant_id = request.state.tenant_id
    settings = get_settings(tenant_id)
    return {
        "settings": {
            "business_account_id": settings["business_account_id"],
            "phone_number_id": settings["phone_number_id"],
            "webhook_verify_token": settings["webhook_verify_token"],
            "is_configured": settings["is_configured"],
        }
    }


@router.post("/whatsapp")
async def save_whatsapp_settings(request: Request, data: WhatsAppSettings):
    tenant_id = request.state.tenant_id
    access_token = (data.access_token or "").strip()
    if access_token.lower().startswith("bearer "):
        access_token = access_token[7:].strip()

    settings_data = {
        "business_account_id": (data.business_account_id or "").strip(),
        "phone_number_id": (data.phone_number_id or "").strip(),
        "access_token": access_token,
        "webhook_verify_token": (data.webhook_verify_token or "").strip(),
        "is_configured": True,
    }
    save_settings(tenant_id, settings_data)
    return {"message": "Settings saved successfully"}


@router.post("/whatsapp/test")
async def test_whatsapp_connection(request: Request):
    tenant_id = request.state.tenant_id
    settings = get_settings(tenant_id)
    if not settings["is_configured"]:
        return JSONResponse(status_code=400, content={"error": "WhatsApp settings not configured"})

    whatsapp = WhatsAppService(settings["phone_number_id"], settings["access_token"])
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
async def get_usage_stats(request: Request):
    tenant_id = request.state.tenant_id
    return _db_messages.get_usage(tenant_id)
