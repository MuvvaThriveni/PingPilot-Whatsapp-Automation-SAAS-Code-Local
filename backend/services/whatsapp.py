from __future__ import annotations

"""WhatsApp Cloud API service (Phase-4: retry safety)."""

import asyncio
import time
import httpx

# Retry configuration
_MAX_RETRIES = 3
_BASE_DELAY_S = 1.0  # first retry after ~1 s, then 2 s, then 4 s


def _is_retryable(exc: Exception = None, status_code: int = 0) -> bool:
    """Return True for network errors and 5xx responses. Never retry 4xx."""
    if exc is not None:
        return True  # network / timeout
    return status_code >= 500


class WhatsAppService:
    def __init__(self, phone_number_id: str, access_token: str):
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.base_url = f"https://graph.facebook.com/v18.0/{phone_number_id}"

    # ── internal retry helper ──────────────────────────────────────────

    async def _request_with_retry(self, method: str, url: str, *,
                                  headers: dict = None, json: dict = None,
                                  data: dict = None, files: dict = None,
                                  params: dict = None,
                                  timeout: float = 30.0,
                                  label: str = "") -> httpx.Response:
        """Execute an HTTP request with exponential backoff on retryable errors.

        Raises the last exception or returns the response (even if non-200).
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(
                        method, url,
                        headers=headers, json=json, data=data,
                        files=files, params=params,
                    )
                if not _is_retryable(status_code=response.status_code):
                    return response
                # 5xx — worth retrying
                last_exc = None
                if attempt < _MAX_RETRIES - 1:
                    delay = _BASE_DELAY_S * (2 ** attempt)
                    print(f"[WA-RETRY] {label} attempt={attempt+1} status={response.status_code} retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                else:
                    return response  # final attempt — return whatever we got
            except Exception as e:
                last_exc = e
                if attempt < _MAX_RETRIES - 1:
                    delay = _BASE_DELAY_S * (2 ** attempt)
                    print(f"[WA-RETRY] {label} attempt={attempt+1} error={e} retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
        # All retries exhausted with exception
        raise last_exc  # type: ignore[misc]

    # ── public API methods ─────────────────────────────────────────────

    async def send_text_message(self, to: str, text: str):
        try:
            response = await self._request_with_retry(
                "POST", f"{self.base_url}/messages",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": text},
                },
                label=f"send_text to={to}",
            )
            data = response.json()
            if response.status_code == 200:
                return {"success": True, "messageId": data.get("messages", [{}])[0].get("id")}
            return {"success": False, "error": data.get("error", {}).get("message", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def send_template_message(self, to: str, template_name: str, language: str = "en_US", components: list = None):
        # Support n8n-style "template_name|language_code" format
        if "|" in template_name:
            parts = template_name.split("|", 1)
            template_name = parts[0].strip()
            language = parts[1].strip()

        try:
            payload = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": language},
                },
            }
            if components:
                payload["template"]["components"] = components
            response = await self._request_with_retry(
                "POST", f"{self.base_url}/messages",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json=payload,
                label=f"send_template to={to} tpl={template_name}",
            )
            data = response.json()
            if response.status_code == 200:
                return {"success": True, "messageId": data.get("messages", [{}])[0].get("id")}
            api_error = data.get("error", {})
            error_msg = api_error.get("message", "Unknown error")
            error_code = api_error.get("code", "")
            detail = error_msg
            if error_code:
                detail += f" (code: {error_code})"
            return {"success": False, "error": detail}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def upload_media(self, file_content: bytes, mime_type: str):
        try:
            response = await self._request_with_retry(
                "POST", f"{self.base_url}/media",
                headers={"Authorization": f"Bearer {self.access_token}"},
                files={"file": ("file", file_content, mime_type)},
                data={"messaging_product": "whatsapp"},
                label="upload_media",
            )
            data = response.json()
            if response.status_code == 200:
                return {"success": True, "mediaId": data.get("id")}
            return {"success": False, "error": data.get("error", {}).get("message", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def send_image(self, to: str, media_id: str, caption: str = ""):
        try:
            response = await self._request_with_retry(
                "POST", f"{self.base_url}/messages",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "image",
                    "image": {"id": media_id, "caption": caption},
                },
                label=f"send_image to={to}",
            )
            data = response.json()
            if response.status_code == 200:
                return {"success": True, "messageId": data.get("messages", [{}])[0].get("id")}
            return {"success": False, "error": data.get("error", {}).get("message", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def send_document(self, to: str, media_id: str, filename: str, caption: str = ""):
        try:
            response = await self._request_with_retry(
                "POST", f"{self.base_url}/messages",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "document",
                    "document": {"id": media_id, "filename": filename, "caption": caption},
                },
                label=f"send_document to={to}",
            )
            data = response.json()
            if response.status_code == 200:
                return {"success": True, "messageId": data.get("messages", [{}])[0].get("id")}
            return {"success": False, "error": data.get("error", {}).get("message", "Unknown error")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_templates(self, business_account_id: str):
        """Fetch all message templates from the WhatsApp Business Account."""
        try:
            response = await self._request_with_retry(
                "GET", f"https://graph.facebook.com/v18.0/{business_account_id}/message_templates",
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={"limit": 100},
                label=f"get_templates ba={business_account_id}",
            )
            data = response.json()
            if response.status_code == 200:
                return {"success": True, "templates": data.get("data", [])}
            return {"success": False, "error": data.get("error", {}).get("message", "Failed to fetch templates")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def test_connection(self):
        try:
            response = await self._request_with_retry(
                "GET", f"https://graph.facebook.com/v18.0/{self.phone_number_id}",
                headers={"Authorization": f"Bearer {self.access_token}"},
                label="test_connection",
            )
            data = response.json()
            if response.status_code == 200:
                return {
                    "success": True,
                    "data": data,
                    "phoneNumber": data.get("display_phone_number", ""),
                    "verifiedName": data.get("verified_name", ""),
                }
            # Surface the real error from the Graph API
            api_error = data.get("error", {})
            error_msg = api_error.get("message", "Invalid credentials")
            error_code = api_error.get("code", "")
            error_subcode = api_error.get("error_subcode", "")
            detail = f"{error_msg}"
            if error_code:
                detail += f" (code: {error_code})"
            if error_subcode:
                detail += f" (subcode: {error_subcode})"
            return {"success": False, "error": detail}
        except Exception as e:
            return {"success": False, "error": str(e)}
