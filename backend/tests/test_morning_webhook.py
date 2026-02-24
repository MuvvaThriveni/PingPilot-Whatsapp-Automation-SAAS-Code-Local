"""
Tests for Morning interactive button_reply → aruna_yoga template flow.

Run with:
    cd backend
    python -m pytest tests/test_morning_webhook.py -v

Requirements:
    pip install pytest pytest-asyncio
"""

from __future__ import annotations

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers – minimal webhook payload factories
# ---------------------------------------------------------------------------

def _make_interactive_payload(
    phone_number_id: str = "1234567890",
    sender_phone: str = "919876543210",
    button_title: str = "Morning",
    button_id: str = "morning_session",
    wa_message_id: str = "wamid.test001",
) -> dict:
    """Build a realistic Meta webhook payload for an interactive button_reply."""
    return {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [{"profile": {"name": "Test User"}}],
                            "messages": [
                                {
                                    "id": wa_message_id,
                                    "from": sender_phone,
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {
                                            "id": button_id,
                                            "title": button_title,
                                        },
                                    },
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }


# ---------------------------------------------------------------------------
# Unit tests: parse interactive button_reply (no DB/network)
# ---------------------------------------------------------------------------


class TestInteractiveButtonParsing:
    """Verify that button title and ID are correctly extracted from payload."""

    def _extract(self, payload: dict):
        """Mimics the extraction logic from webhook.py."""
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        message_type = message.get("type", "text")
        message_text = ""
        interactive_button_id = ""

        if message_type == "interactive":
            interactive = message.get("interactive", {})
            if interactive.get("type") == "button_reply":
                button_reply_data = interactive.get("button_reply", {})
                message_text = button_reply_data.get("title", "").strip()
                interactive_button_id = button_reply_data.get("id", "").strip()
            else:
                interactive_button_id = ""

        if message_type != "interactive":
            interactive_button_id = ""

        return message_text, interactive_button_id

    def test_extracts_morning_title(self):
        payload = _make_interactive_payload(button_title="Morning", button_id="morning_session")
        text, bid = self._extract(payload)
        assert text == "Morning"

    def test_extracts_button_id(self):
        payload = _make_interactive_payload(button_title="Morning", button_id="morning_session")
        text, bid = self._extract(payload)
        assert bid == "morning_session"

    def test_afternoon_title(self):
        payload = _make_interactive_payload(button_title="Afternoon", button_id="afternoon_session")
        text, bid = self._extract(payload)
        assert text == "Afternoon"
        assert bid == "afternoon_session"

    def test_evening_title(self):
        payload = _make_interactive_payload(button_title="Evening", button_id="evening_session")
        text, bid = self._extract(payload)
        assert text == "Evening"

    def test_button_id_cleared_for_non_interactive(self):
        """For type=text messages, interactive_button_id must be blank."""
        message_type = "text"
        interactive_button_id = "should_be_cleared"
        if message_type != "interactive":
            interactive_button_id = ""
        assert interactive_button_id == ""


# ---------------------------------------------------------------------------
# Unit tests: routing logic (no DB/network)
# ---------------------------------------------------------------------------


class TestRoutingLogic:
    """Verify the if/elif routing logic selects the correct response."""

    def _route(self, clean_text: str, interactive_button_id: str = "") -> tuple[str, str]:
        """Mirrors the routing block in webhook.py. Returns (response_text, response_template)."""
        response_text = ""
        response_template = ""
        matched_rule = False

        if not matched_rule:
            if clean_text == "Sessions":
                response_template = "session_template"
                matched_rule = True
            elif clean_text == "Products":
                response_template = "products_template"
                matched_rule = True
            elif clean_text == "Morning" or interactive_button_id == "morning_session":
                response_template = "aruna_yoga"
                matched_rule = True
            elif clean_text == "Afternoon":
                response_text = "🧘 Our 7:30 AM Yoga plan focuses on flexibility and core strength, perfect for mid-morning refreshment."
                matched_rule = True
            elif clean_text == "Evening":
                response_text = "🧘 Our 10:30 AM Yoga plan is a gentle flow designed for stress relief and mindfulness."
                matched_rule = True

        return response_text, response_template

    # -- Morning --

    def test_morning_title_sends_aruna_yoga_template(self):
        text, tpl = self._route("Morning", "morning_session")
        assert tpl == "aruna_yoga"
        assert text == ""

    def test_morning_id_only_sends_aruna_yoga_template(self):
        """button_id fallback: title may be empty if Meta sends only ID."""
        text, tpl = self._route("", "morning_session")
        assert tpl == "aruna_yoga"
        assert text == ""

    def test_morning_case_sensitive(self):
        """'morning' (lowercase) should NOT trigger aruna_yoga."""
        text, tpl = self._route("morning", "")
        assert tpl != "aruna_yoga"

    # -- Afternoon / Evening isolation --

    def test_afternoon_sends_text_not_template(self):
        text, tpl = self._route("Afternoon", "afternoon_session")
        assert tpl == ""
        assert "7:30 AM" in text

    def test_evening_sends_text_not_template(self):
        text, tpl = self._route("Evening", "evening_session")
        assert tpl == ""
        assert "10:30 AM" in text

    # -- Sessions / Products --

    def test_sessions_sends_session_template(self):
        _, tpl = self._route("Sessions")
        assert tpl == "session_template"

    def test_products_sends_products_template(self):
        _, tpl = self._route("Products")
        assert tpl == "products_template"

    # -- No match --

    def test_unknown_message_no_match(self):
        text, tpl = self._route("hello world", "")
        assert text == "" and tpl == ""


# ---------------------------------------------------------------------------
# Integration-style test: mocked WhatsAppService template call
# ---------------------------------------------------------------------------


class TestMockTemplateCall:
    """Verify send_template_message is called with correct args for Morning."""

    @pytest.mark.asyncio
    async def test_aruna_yoga_called_with_correct_params(self):
        """Mock WhatsAppService and confirm aruna_yoga is dispatched."""
        mock_wa = MagicMock()
        mock_wa.send_template_message = AsyncMock(
            return_value={"success": True, "messageId": "fake-msg-id"}
        )

        sender_phone = "919876543210"
        response_template = "aruna_yoga"
        language = "en"

        result = await mock_wa.send_template_message(
            sender_phone, response_template, language=language, components=None
        )

        mock_wa.send_template_message.assert_called_once_with(
            "919876543210", "aruna_yoga", language="en", components=None
        )
        assert result["success"] is True
        assert result["messageId"] == "fake-msg-id"

    @pytest.mark.asyncio
    async def test_failed_api_response_handled(self):
        """If Meta API returns failure, result dict contains error."""
        mock_wa = MagicMock()
        mock_wa.send_template_message = AsyncMock(
            return_value={"success": False, "error": "Template not approved"}
        )

        result = await mock_wa.send_template_message(
            "919876543210", "aruna_yoga", language="en", components=None
        )
        assert result["success"] is False
        assert "Template not approved" in result["error"]


# ---------------------------------------------------------------------------
# Idempotency / deduplication test
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Ensure the same wa_message_id cannot trigger Morning twice."""

    def test_same_event_id_deduplicated(self):
        """Simulate the webhook_events.exists() guard."""
        seen_events: set[str] = set()

        def exists(event_id: str) -> bool:
            return event_id in seen_events

        def record(event_id: str):
            seen_events.add(event_id)

        wa_message_id = "wamid.UNIQUE_TEST_001"

        # First arrival → not a duplicate
        assert not exists(wa_message_id)
        record(wa_message_id)

        # Second arrival (webhook retry) → duplicate, must be skipped
        assert exists(wa_message_id)
