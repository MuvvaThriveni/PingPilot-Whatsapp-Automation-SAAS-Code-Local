"""ChatGPT service for AI-powered auto-replies (Phase-3: multi-tenant).

Conversation context is always loaded from the Firestore `chat_messages`
collection via db_layer.  No in-memory persistence — fully stateless,
safe for multi-worker deployments.
"""

import os
import httpx
from typing import List, Dict

from db_layer.chat_messages import chat_messages as _db_chat_messages

MAX_MEMORY_MESSAGES = 10  # Keep last 10 messages per conversation


class ChatGPTService:
    """Service to interact with OpenAI ChatGPT API."""

    def __init__(self, api_key: str = None, system_prompt: str = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = "https://api.openai.com/v1/chat/completions"
        self.system_prompt = system_prompt or (
            "You are a helpful customer service assistant for a business. "
            "Be friendly, concise, and helpful. Answer questions clearly and professionally. "
            "Please keep your response strictly under 1500 characters. "
            "If you don't know something, politely say so and offer to connect them with a human agent."
        )

    def _get_conversation_history(self, tenant_id: str, phone: str) -> List[Dict]:
        """Load conversation history from Firestore chat_messages."""
        try:
            context = _db_chat_messages.build_ai_context(
                tenant_id, phone, limit=MAX_MEMORY_MESSAGES
            )
            return context or []
        except Exception as e:
            print(f"[WARN] Failed to load AI context from Firestore: {e}")
            return []

    async def get_response(self, tenant_id: str, phone: str, message: str) -> Dict:
        """
        Get an AI response for the given message.
        Conversation context is loaded from Firestore on every call.
        """
        if not self.api_key:
            return {
                "success": False,
                "error": "OpenAI API key not configured. Set OPENAI_API_KEY in environment.",
            }

        # Build messages array with system prompt + conversation history
        # Note: We explicitly handle the current message to ensure it's present 
        # (even if DB is slow) and not duplicated (if DB is fast).
        history = self._get_conversation_history(tenant_id, phone)
        
        # Deduplicate: If the last message in history is the current user message, remove it.
        # We will append it freshly below.
        if history and history[-1]["role"] == "user" and history[-1]["content"] == message:
            history.pop()
            
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": message})

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.base_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4-turbo",
                        "messages": messages,
                        "max_tokens": 500,
                        "temperature": 0.7,
                    },
                )

                data = response.json()

                if response.status_code != 200:
                    error_msg = data.get("error", {}).get("message", "Unknown error")
                    return {"success": False, "error": f"OpenAI API error: {error_msg}"}

                assistant_message = data["choices"][0]["message"]["content"].strip()

                return {"success": True, "response": assistant_message}

        except httpx.TimeoutException:
            return {"success": False, "error": "OpenAI API request timed out"}
        except Exception as e:
            return {"success": False, "error": f"ChatGPT error: {str(e)}"}


# Singleton instance
_chatgpt_service: ChatGPTService = None


def get_chatgpt_service(api_key: str = None, system_prompt: str = None) -> ChatGPTService:
    """Get ChatGPT service instance.
    
    If api_key or system_prompt are provided, returns a NEW instance with those settings.
    If no args provided, returns the singleton instance (creating if needed).
    """
    global _chatgpt_service
    
    if api_key or system_prompt is not None:
        return ChatGPTService(api_key, system_prompt)
        
    if _chatgpt_service is None:
        _chatgpt_service = ChatGPTService()
    return _chatgpt_service
