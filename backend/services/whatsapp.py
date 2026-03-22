from __future__ import annotations

"""WhatsApp Cloud API service (Phase-7: jitter + adaptive cooldown).

Improvements:
- Connection pooling (reusable httpx.AsyncClient per instance)
- 429 rate-limit handling with Retry-After header support
- Jitter on all retry delays to prevent thundering herd
- Adaptive global cooldown via Redis on repeated 429s
- No secrets/payloads logged
- Consistent error handling
"""

import asyncio
import random
import httpx
from observability import log_event
from rate_limit import set_global_cooldown

# Retry configuration
_MAX_RETRIES = 3
_BASE_DELAY_S = 1.0  # first retry after ~1 s, then 2 s, then 4 s
_CONSECUTIVE_429_THRESHOLD = 2  # trigger global cooldown after this many 429s


def _is_retryable(exc: Exception = None, status_code: int = 0) -> bool:
    """Return True for network errors, 5xx, and 429 Too Many Requests."""
    if exc is not None:
        return True  # network / timeout
    return status_code >= 500 or status_code == 429


class WhatsAppService:
    def __init__(self, phone_number_id: str, access_token: str):
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.base_url = f"https://graph.facebook.com/v18.0/{phone_number_id}"
        # Reusable HTTP client with connection pooling
        self._client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def close(self):
        """Close the underlying HTTP client. Call on shutdown if needed."""
        await self._client.aclose()

    # ── internal retry helper ──────────────────────────────────────────

    async def _request_with_retry(self, method: str, url: str, *,
                                  headers: dict = None, json: dict = None,
                                  data: dict = None, files: dict = None,
                                  params: dict = None,
                                  label: str = "") -> httpx.Response:
        """Execute an HTTP request with exponential backoff + jitter on retryable errors.

        Handles 429 Too Many Requests with Retry-After header support.
        Triggers a Redis-backed global cooldown after repeated 429s so all
        workers back off simultaneously.
        """
        last_exc: Exception | None = None
        consecutive_429 = 0
        for attempt in range(_MAX_RETRIES):
            try:
                response = await self._client.request(
                    method, url,
                    headers=headers, json=json, data=data,
                    files=files, params=params,
                )
                if not _is_retryable(status_code=response.status_code):
                    return response

                # Retryable status (5xx or 429)
                if attempt < _MAX_RETRIES - 1:
                    if response.status_code == 429:
                        consecutive_429 += 1
                        # Respect Retry-After header if present
                        retry_after = response.headers.get("Retry-After")
                        base_delay = float(retry_after) if retry_after else _BASE_DELAY_S * (2 ** attempt)
                        # Add jitter: delay + random(0, 0.5 * delay)
                        jitter = random.uniform(0, 0.5 * base_delay)
                        delay = min(base_delay + jitter, 30.0)
                        log_event("wa_rate_limited", detail=f"{label} 429 #{consecutive_429}, retry in {delay:.1f}s",
                                  level="WARN")
                        # Trigger global cooldown after repeated 429s
                        if consecutive_429 >= _CONSECUTIVE_429_THRESHOLD:
                            try:
                                await set_global_cooldown()
                            except Exception:
                                pass  # Redis failure — don't block the retry
                    else:
                        base_delay = _BASE_DELAY_S * (2 ** attempt)
                        jitter = random.uniform(0, 0.5 * base_delay)
                        delay = base_delay + jitter
                        log_event("wa_retry", detail=f"{label} status={response.status_code} retry in {delay:.1f}s")
                    await asyncio.sleep(delay)
                else:
                    # Final attempt — trigger cooldown if we got 429
                    if response.status_code == 429:
                        try:
                            await set_global_cooldown()
                        except Exception:
                            pass
                    return response  # final attempt — return whatever we got
                last_exc = None
            except Exception as e:
                last_exc = e
                if attempt < _MAX_RETRIES - 1:
                    base_delay = _BASE_DELAY_S * (2 ** attempt)
                    jitter = random.uniform(0, 0.5 * base_delay)
                    delay = base_delay + jitter
                    log_event("wa_retry", detail=f"{label} error, retry in {delay:.1f}s", level="WARN")
                    await asyncio.sleep(delay)
        # All retries exhausted with exception
        raise last_exc  # type: ignore[misc]

    # ── Auth headers helper ─────────────────────────────────────────

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}"}

    # ── public API methods ─────────────────────────────────────────────

    async def send_text_message(self, to: str, text: str):
        try:
            response = await self._request_with_retry(
                "POST", f"{self.base_url}/messages",
                headers=self._auth_headers(),
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
        # Support "template_name|language_code" format
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
                headers=self._auth_headers(),
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
                headers=self._auth_headers(),
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
                headers=self._auth_headers(),
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
                headers=self._auth_headers(),
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
                headers=self._auth_headers(),
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
                headers=self._auth_headers(),
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
