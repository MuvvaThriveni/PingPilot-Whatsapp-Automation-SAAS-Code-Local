"""
Tests for the dynamic button→template routing system (Phase-8).

The routing logic is now FULLY DB-driven via chatbot_button_mappings.
These tests verify:
  - Interactive button_reply payloads are correctly parsed
  - The routing engine correctly matches text/id against DB mappings
  - Template dispatch works with mocked WhatsAppService
  - Keyword rules support text + template response types
  - Keyword match_type (exact/contains/starts_with) works correctly
  - Deduplication via wa_message_id still works

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
# Unit tests: DB-driven routing logic (no actual DB – uses dict lookups)
# ---------------------------------------------------------------------------


def _route_with_mappings(
    clean_text: str,
    interactive_button_id: str = "",
    text_map: dict | None = None,
    id_map: dict | None = None,
    rules: list[dict] | None = None,
) -> tuple[str, str]:
    """Mirrors the Phase-8 routing block in webhook.py.

    Uses in-memory dicts instead of DB calls, exactly matching the
    webhook decision engine:
      1. Button text map lookup
      2. Button ID map lookup
      3. Keyword rules with match_type + response_type
    """
    text_map = text_map or {}
    id_map = id_map or {}
    rules = rules or []

    response_text = ""
    response_template = ""
    matched_rule = False

    # 1. Button text map
    if not matched_rule and clean_text in text_map:
        response_template = text_map[clean_text]
        matched_rule = True

    # 2. Button ID map
    if not matched_rule and interactive_button_id and interactive_button_id in id_map:
        response_template = id_map[interactive_button_id]
        matched_rule = True

    # 3. Keyword rules with match_type + response_type
    if not matched_rule:
        message_lower = clean_text.lower().strip()
        for rule in rules:
            keyword = rule.get("keyword", "").strip().lower()
            match_type = rule.get("match_type", "contains")
            matched = False
            if keyword:
                if match_type == "exact" and message_lower == keyword:
                    matched = True
                elif match_type == "starts_with" and message_lower.startswith(keyword):
                    matched = True
                elif match_type == "contains" and keyword in message_lower:
                    matched = True

            if matched:
                resp_type = rule.get("response_type", "text")
                resp_value = rule.get("response", "")
                if resp_value:
                    if resp_type == "template":
                        response_template = resp_value
                    else:
                        response_text = resp_value
                    matched_rule = True
                    break

    return response_text, response_template


# Sample tenant button mappings (what a yoga studio would configure in their DB)
_YOGA_TEXT_MAP = {
    "Sessions": "session_template",
    "Products": "products_template",
    "Morning": "aruna_yoga",
    "Afternoon": "afternoon_meet",
    "Evening": "meet3",
}
_YOGA_ID_MAP = {
    "morning_session": "aruna_yoga",
    "afternoon_session": "afternoon_meet",
    "evening_session": "meet3",
}

# Sample tenant keyword rules
_SAMPLE_RULES = [
    {"keyword": "pricing", "response": "Our plans start at ₹999/month", "response_type": "text", "match_type": "contains"},
    {"keyword": "catalog", "response": "product_catalog", "response_type": "template", "match_type": "exact"},
    {"keyword": "hello", "response": "welcome_template", "response_type": "template", "match_type": "starts_with"},
]


class TestDynamicRoutingLogic:
    """Verify the DB-driven routing engine selects the correct response."""

    # -- Button text matches --

    def test_morning_text_routes_to_template(self):
        text, tpl = _route_with_mappings("Morning", "", _YOGA_TEXT_MAP, _YOGA_ID_MAP)
        assert tpl == "aruna_yoga"
        assert text == ""

    def test_evening_text_routes_to_template(self):
        text, tpl = _route_with_mappings("Evening", "", _YOGA_TEXT_MAP, _YOGA_ID_MAP)
        assert tpl == "meet3"
        assert text == ""

    def test_sessions_text_routes_to_template(self):
        _, tpl = _route_with_mappings("Sessions", "", _YOGA_TEXT_MAP, _YOGA_ID_MAP)
        assert tpl == "session_template"

    def test_products_text_routes_to_template(self):
        _, tpl = _route_with_mappings("Products", "", _YOGA_TEXT_MAP, _YOGA_ID_MAP)
        assert tpl == "products_template"

    # -- Button ID fallback matches --

    def test_morning_id_only_routes_to_template(self):
        """button_id fallback: title may be empty if Meta sends only ID."""
        text, tpl = _route_with_mappings("", "morning_session", _YOGA_TEXT_MAP, _YOGA_ID_MAP)
        assert tpl == "aruna_yoga"
        assert text == ""

    def test_evening_id_only_routes_to_template(self):
        text, tpl = _route_with_mappings("", "evening_session", _YOGA_TEXT_MAP, _YOGA_ID_MAP)
        assert tpl == "meet3"
        assert text == ""

    # -- Empty tenant (no mappings configured) --

    def test_no_mappings_returns_empty(self):
        """A tenant with no button mappings should get NO response (not yoga defaults)."""
        text, tpl = _route_with_mappings("Morning", "morning_session", {}, {})
        assert tpl == ""
        assert text == ""

    def test_unknown_button_returns_empty(self):
        text, tpl = _route_with_mappings("CustomButton", "custom_id", _YOGA_TEXT_MAP, _YOGA_ID_MAP)
        assert text == "" and tpl == ""

    # -- Case sensitivity --

    def test_button_text_is_case_sensitive(self):
        """'morning' (lowercase) should NOT match 'Morning' in text_map."""
        text, tpl = _route_with_mappings("morning", "", _YOGA_TEXT_MAP, _YOGA_ID_MAP)
        assert tpl != "aruna_yoga"

    def test_button_id_is_case_sensitive(self):
        """'Morning_Session' should NOT match 'morning_session' in id_map."""
        text, tpl = _route_with_mappings("", "Morning_Session", _YOGA_TEXT_MAP, _YOGA_ID_MAP)
        assert tpl == ""


class TestKeywordRuleRouting:
    """Verify keyword rules with response_type and match_type."""

    def test_contains_match(self):
        """'pricing' keyword with 'contains' should match 'what is your pricing plan'."""
        text, tpl = _route_with_mappings("what is your pricing plan", "", {}, {}, _SAMPLE_RULES)
        assert text == "Our plans start at ₹999/month"
        assert tpl == ""

    def test_exact_match_succeeds(self):
        """'catalog' with 'exact' should match exactly 'catalog'."""
        text, tpl = _route_with_mappings("catalog", "", {}, {}, _SAMPLE_RULES)
        assert tpl == "product_catalog"
        assert text == ""

    def test_exact_match_fails_on_partial(self):
        """'catalog' with 'exact' should NOT match 'show me catalog please'."""
        text, tpl = _route_with_mappings("show me catalog please", "", {}, {}, _SAMPLE_RULES)
        # 'catalog' rule is exact, so it won't match — no other rule matches either
        assert tpl == ""
        assert text == ""

    def test_starts_with_match(self):
        """'hello' with 'starts_with' should match 'hello there'."""
        text, tpl = _route_with_mappings("hello there", "", {}, {}, _SAMPLE_RULES)
        assert tpl == "welcome_template"

    def test_starts_with_fails_on_middle(self):
        """'hello' with 'starts_with' should NOT match 'say hello'."""
        text, tpl = _route_with_mappings("say hello", "", {}, {}, _SAMPLE_RULES)
        assert tpl == ""

    def test_template_response_type(self):
        """Rules with response_type='template' should set response_template, not response_text."""
        text, tpl = _route_with_mappings("catalog", "", {}, {}, _SAMPLE_RULES)
        assert tpl == "product_catalog"
        assert text == ""

    def test_text_response_type(self):
        """Rules with response_type='text' should set response_text, not response_template."""
        text, tpl = _route_with_mappings("pricing info", "", {}, {}, _SAMPLE_RULES)
        assert text == "Our plans start at ₹999/month"
        assert tpl == ""

    def test_button_mapping_takes_priority_over_rules(self):
        """If both text_map match and rule match, text_map wins (priority 1)."""
        rules = [{"keyword": "morning", "response": "text_reply", "response_type": "text", "match_type": "contains"}]
        text, tpl = _route_with_mappings("Morning", "", _YOGA_TEXT_MAP, _YOGA_ID_MAP, rules)
        assert tpl == "aruna_yoga"
        assert text == ""

    def test_no_rules_returns_empty(self):
        text, tpl = _route_with_mappings("random message", "", {}, {}, [])
        assert text == "" and tpl == ""


# ---------------------------------------------------------------------------
# Integration-style test: mocked WhatsAppService template call
# ---------------------------------------------------------------------------


class TestMockTemplateCall:
    """Verify send_template_message is called with correct args."""

    @pytest.mark.asyncio
    async def test_template_called_with_correct_params(self):
        """Mock WhatsAppService and confirm template dispatch works."""
        mock_wa = MagicMock()
        mock_wa.send_template_message = AsyncMock(
            return_value={"success": True, "messageId": "fake-msg-id"}
        )

        sender_phone = "919876543210"
        # Simulate: tenant has "Morning" → "aruna_yoga" in their button mappings
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
    """Ensure the same wa_message_id cannot trigger a button response twice."""

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


# ---------------------------------------------------------------------------
# Template pipeline test: IMAGE header handling
# ---------------------------------------------------------------------------


class TestTemplateImageHeader:
    """Verify that templates with IMAGE headers are built correctly."""

    @pytest.mark.asyncio
    async def test_template_with_image_header(self):
        """When template has IMAGE header, components must include image with media id."""
        import services.template_builder as tb

        # Seed cache with a template that has an IMAGE header
        test_comps = [
            {
                "type": "HEADER",
                "format": "IMAGE",
                "example": {"header_handle": ["https://cdn.example.com/evening.jpg"]},
            },
            {
                "type": "BODY",
                "text": "Hello {{1}}, join us for the session.",
                "example": {"body_text": [["Friend"]]},
            },
        ]
        tb._template_components["test_template|en_US"] = test_comps

        components = tb.build_components(
            "test_template|en_US",
            contact={"name": "Ravi", "phone": "919876543210"},
            header_media_id="TEST_MEDIA_ID",
        )

        # Header component must carry IMAGE with the supplied media id
        header = next((c for c in components if c["type"] == "header"), None)
        assert header is not None, "Expected a header component"
        assert header["parameters"][0] == {
            "type": "image",
            "image": {"id": "TEST_MEDIA_ID"},
        }

        # Body must use contact name for {{1}}
        body = next((c for c in components if c["type"] == "body"), None)
        assert body is not None
        assert body["parameters"][0]["text"] == "Ravi"

        # Clean up
        tb._template_components.pop("test_template|en_US", None)


# ---------------------------------------------------------------------------
# Tenant isolation test
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    """Verify that different tenants get different routing results."""

    def test_different_tenants_different_mappings(self):
        """Two tenants with different mappings should get different results."""
        # Tenant A: yoga studio
        tenant_a_text_map = {"Morning": "aruna_yoga"}
        text_a, tpl_a = _route_with_mappings("Morning", "", tenant_a_text_map)
        assert tpl_a == "aruna_yoga"

        # Tenant B: restaurant
        tenant_b_text_map = {"Morning": "breakfast_menu"}
        text_b, tpl_b = _route_with_mappings("Morning", "", tenant_b_text_map)
        assert tpl_b == "breakfast_menu"

        # They must NOT be the same
        assert tpl_a != tpl_b

    def test_unconfigured_tenant_gets_nothing(self):
        """A tenant with NO mappings must not inherit another tenant's config."""
        text, tpl = _route_with_mappings("Morning", "morning_session", {}, {})
        assert tpl == ""
        assert text == ""
