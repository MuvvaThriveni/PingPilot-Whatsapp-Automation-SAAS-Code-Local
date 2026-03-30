"""Settings routes – WhatsApp Business API configuration (Phase-6: hardened).

Security fix: Never return access_token in GET responses.
Input validation via Pydantic.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from typing import Optional
import re

from store import get_settings, save_settings
from services.whatsapp import WhatsAppService
from db_layer.messages import messages as _db_messages
from db_layer.tenants import tenants as _db_tenants
from db_layer.quota import get_quota_status
from observability import log_event

router = APIRouter(prefix="/api/settings", tags=["settings"])


class WhatsAppSettings(BaseModel):
    business_account_id: str
    phone_number_id: str
    access_token: Optional[str] = ""   # optional — omit to keep existing token
    webhook_verify_token: Optional[str] = ""
    meta_app_secret: Optional[str] = ""  # optional — omit to keep existing secret

    @field_validator("business_account_id", "phone_number_id")
    @classmethod
    def must_be_alphanumeric(cls, v: str) -> str:
        v = v.strip()
        if v and not re.match(r'^[a-zA-Z0-9_]+$', v):
            raise ValueError("Must contain only alphanumeric characters and underscores")
        return v


@router.get("/whatsapp")
async def get_whatsapp_settings(request: Request):
    tenant_id = request.state.tenant_id
    settings = await get_settings(tenant_id)
    # SECURITY: Never return access_token or meta_app_secret to the frontend
    return {
        "settings": {
            "business_account_id": settings["business_account_id"],
            "phone_number_id": settings["phone_number_id"],
            "webhook_verify_token": settings["webhook_verify_token"],
            "is_configured": settings["is_configured"],
            "has_access_token": bool(settings.get("access_token")),
            "has_meta_app_secret": bool(settings.get("meta_app_secret")),
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
        "meta_app_secret": (data.meta_app_secret or "").strip(),
        "is_configured": True,
    }
    await save_settings(tenant_id, settings_data)
    log_event("settings_updated", tenant_id=tenant_id)
    return {"message": "Settings saved successfully"}


@router.post("/whatsapp/test")
async def test_whatsapp_connection(request: Request):
    tenant_id = request.state.tenant_id
    settings = await get_settings(tenant_id)
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
    usage = await _db_messages.get_usage(tenant_id)

    tenant = await _db_tenants.get(tenant_id)
    bulk_quota_limit = int((tenant or {}).get("bulk_quota_limit", 100))
    quota = await get_quota_status(tenant_id, bulk_quota_limit)

    if isinstance(usage, dict):
        usage["bulk_quota"] = {
            "used":         quota.used,
            "limit":        quota.limit,
            "remaining":    quota.remaining,
            "resets_at":    quota.resets_at,
            "percent_used": round(quota.used / quota.limit * 100, 1) if quota.limit > 0 else 100.0,
        }
        return usage

    return {
        "bulk_quota": {
            "used":         quota.used,
            "limit":        quota.limit,
            "remaining":    quota.remaining,
            "resets_at":    quota.resets_at,
            "percent_used": round(quota.used / quota.limit * 100, 1) if quota.limit > 0 else 100.0,
        }
    }
