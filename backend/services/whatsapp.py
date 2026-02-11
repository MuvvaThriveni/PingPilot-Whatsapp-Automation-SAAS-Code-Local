"""WhatsApp Cloud API service."""

import httpx


class WhatsAppService:
    def __init__(self, phone_number_id: str, access_token: str):
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.base_url = f"https://graph.facebook.com/v18.0/{phone_number_id}"

    async def send_text_message(self, to: str, text: str):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/messages",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    json={
                        "messaging_product": "whatsapp",
                        "to": to,
                        "type": "text",
                        "text": {"body": text},
                    },
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

        async with httpx.AsyncClient() as client:
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
                response = await client.post(
                    f"{self.base_url}/messages",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    json=payload,
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
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/media",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    files={"file": ("file", file_content, mime_type)},
                    data={"messaging_product": "whatsapp"},
                )
                data = response.json()
                if response.status_code == 200:
                    return {"success": True, "mediaId": data.get("id")}
                return {"success": False, "error": data.get("error", {}).get("message", "Unknown error")}
            except Exception as e:
                return {"success": False, "error": str(e)}

    async def send_image(self, to: str, media_id: str, caption: str = ""):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/messages",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    json={
                        "messaging_product": "whatsapp",
                        "to": to,
                        "type": "image",
                        "image": {"id": media_id, "caption": caption},
                    },
                )
                data = response.json()
                if response.status_code == 200:
                    return {"success": True, "messageId": data.get("messages", [{}])[0].get("id")}
                return {"success": False, "error": data.get("error", {}).get("message", "Unknown error")}
            except Exception as e:
                return {"success": False, "error": str(e)}

    async def send_document(self, to: str, media_id: str, filename: str, caption: str = ""):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/messages",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    json={
                        "messaging_product": "whatsapp",
                        "to": to,
                        "type": "document",
                        "document": {"id": media_id, "filename": filename, "caption": caption},
                    },
                )
                data = response.json()
                if response.status_code == 200:
                    return {"success": True, "messageId": data.get("messages", [{}])[0].get("id")}
                return {"success": False, "error": data.get("error", {}).get("message", "Unknown error")}
            except Exception as e:
                return {"success": False, "error": str(e)}

    async def get_templates(self, business_account_id: str):
        """Fetch all message templates from the WhatsApp Business Account."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"https://graph.facebook.com/v18.0/{business_account_id}/message_templates",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    params={"limit": 100},
                )
                data = response.json()
                if response.status_code == 200:
                    return {"success": True, "templates": data.get("data", [])}
                return {"success": False, "error": data.get("error", {}).get("message", "Failed to fetch templates")}
            except Exception as e:
                return {"success": False, "error": str(e)}

    async def test_connection(self):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"https://graph.facebook.com/v18.0/{self.phone_number_id}",
                    headers={"Authorization": f"Bearer {self.access_token}"},
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
