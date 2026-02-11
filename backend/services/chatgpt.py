"""ChatGPT service for AI-powered auto-replies."""

import os
import httpx
from typing import List, Dict

# In-memory conversation history per phone number (session memory)
_conversation_memory: Dict[str, List[Dict]] = {}
MAX_MEMORY_MESSAGES = 10  # Keep last 10 messages per conversation


class ChatGPTService:
    """Service to interact with OpenAI ChatGPT API."""

    def __init__(self, api_key: str = None, system_prompt: str = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = "https://api.openai.com/v1/chat/completions"
        self.system_prompt = system_prompt or (
            "You are a helpful customer service assistant for a business. "
            "Be friendly, concise, and helpful. Answer questions clearly and professionally. "
            "If you don't know something, politely say so and offer to connect them with a human agent."
        )

    def _get_conversation_history(self, phone: str) -> List[Dict]:
        """Get conversation history for a phone number."""
        return _conversation_memory.get(phone, [])

    def _add_to_history(self, phone: str, role: str, content: str):
        """Add a message to conversation history."""
        if phone not in _conversation_memory:
            _conversation_memory[phone] = []
        
        _conversation_memory[phone].append({"role": role, "content": content})
        
        # Keep only last N messages to avoid token limits
        if len(_conversation_memory[phone]) > MAX_MEMORY_MESSAGES:
            _conversation_memory[phone] = _conversation_memory[phone][-MAX_MEMORY_MESSAGES:]

    def clear_history(self, phone: str):
        """Clear conversation history for a phone number."""
        if phone in _conversation_memory:
            del _conversation_memory[phone]

    async def get_response(self, phone: str, message: str) -> Dict:
        """
        Get an AI response for the given message.
        Maintains conversation context per phone number.
        """
        if not self.api_key:
            return {
                "success": False,
                "error": "OpenAI API key not configured. Set OPENAI_API_KEY in environment.",
            }

        # Add user message to history
        self._add_to_history(phone, "user", message)

        # Build messages array with system prompt + conversation history
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self._get_conversation_history(phone))

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.base_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-3.5-turbo",
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
                
                # Add assistant response to history
                self._add_to_history(phone, "assistant", assistant_message)

                return {"success": True, "response": assistant_message}

        except httpx.TimeoutException:
            return {"success": False, "error": "OpenAI API request timed out"}
        except Exception as e:
            return {"success": False, "error": f"ChatGPT error: {str(e)}"}


# Singleton instance
_chatgpt_service: ChatGPTService = None


def get_chatgpt_service(api_key: str = None, system_prompt: str = None) -> ChatGPTService:
    """Get or create ChatGPT service instance."""
    global _chatgpt_service
    if _chatgpt_service is None or api_key:
        _chatgpt_service = ChatGPTService(api_key, system_prompt)
    return _chatgpt_service
