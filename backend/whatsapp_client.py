import os
import httpx
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Meta API configuration
# (Note: Credentials are now fetched from Firestore per tenant)

class WhatsAppClient:
    def __init__(self):
        self.api_version = os.getenv("WHATSAPP_API_VERSION", "v18.0")

    def _get_base_url(self, phone_number_id: str):
        return f"https://graph.facebook.com/{self.api_version}/{phone_number_id}"

    async def send_template(self, user_number: str, template_name: str, phone_number_id: str, access_token: str, language_code: str = "en_US"):
        """Calls Meta Graph API to send a template message."""
        url = f"{self._get_base_url(phone_number_id)}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": user_number,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": language_code
                }
            }
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload)
                response_data = response.json()
                
                if response.status_code == 200:
                    logger.info(f"Template {template_name} sent successfully to {user_number}")
                    return response_data
                else:
                    logger.error(f"Failed to send template. Status: {response.status_code}, Error: {response_data}")
                    return None
        except Exception as e:
            logger.error(f"Error calling Meta API for template: {str(e)}")
            return None

    async def send_text_message(self, user_number: str, text: str, phone_number_id: str, access_token: str):
        """Sends normal WhatsApp text message."""
        url = f"{self._get_base_url(phone_number_id)}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": user_number,
            "type": "text",
            "text": {
                "body": text
            }
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload)
                response_data = response.json()
                
                if response.status_code == 200:
                    logger.info(f"Text message sent successfully to {user_number}")
                    return response_data
                else:
                    logger.error(f"Failed to send text message. Status: {response.status_code}, Error: {response_data}")
                    return None
        except Exception as e:
            logger.error(f"Error calling Meta API for text message: {str(e)}")
            return None

whatsapp_client = WhatsAppClient()
