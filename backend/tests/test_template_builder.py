"""
Unit tests for services/template_builder.py

Run with:
    cd backend
    python -m pytest tests/test_template_builder.py -v

These tests are fully isolated — no database, no network, no Firebase.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

import services.template_builder as tb


# ---------------------------------------------------------------------------
# Fixtures — reset shared cache before every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure a clean cache for every test."""
    tb._template_components.clear()
    tb._uploaded_media_ids.clear()
    yield
    tb._template_components.clear()
    tb._uploaded_media_ids.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IMAGE_TEMPLATE_COMPS = [
    {
        "type": "HEADER",
        "format": "IMAGE",
        "example": {"header_handle": ["https://cdn.example.com/yoga.jpg"]},
    },
    {
        "type": "BODY",
        "text": "Hello {{1}}, your session is confirmed.",
        "example": {"body_text": [["Friend"]]},
    },
]

TEXT_HEADER_COMPS = [
    {
        "type": "HEADER",
        "format": "TEXT",
        "text": "Hi {{1}}",
        "example": {"header_text": ["Valued Customer"]},
    },
    {
        "type": "BODY",
        "text": "Your plan is {{1}}.",
        "example": {"body_text": [["Premium"]]},
    },
]

VIDEO_TEMPLATE_COMPS = [
    {
        "type": "HEADER",
        "format": "VIDEO",
        "example": {"header_handle": ["https://cdn.example.com/intro.mp4"]},
    },
]

DOCUMENT_TEMPLATE_COMPS = [
    {
        "type": "HEADER",
        "format": "DOCUMENT",
        "example": {"header_handle": ["https://cdn.example.com/brochure.pdf"]},
    },
]

BODY_ONLY_COMPS = [
    {
        "type": "BODY",
        "text": "Welcome {{1}}!",
        "example": {"body_text": [["Guest"]]},
    }
]


# ---------------------------------------------------------------------------
# get_components
# ---------------------------------------------------------------------------


class TestGetComponents:
    def test_returns_empty_list_for_unknown_key(self):
        assert tb.get_components("nonexistent|en") == []

    def test_returns_cached_components(self):
        tb._template_components["aruna_yoga|en_US"] = IMAGE_TEMPLATE_COMPS
        result = tb.get_components("aruna_yoga|en_US")
        assert result is IMAGE_TEMPLATE_COMPS


# ---------------------------------------------------------------------------
# build_components — empty cache
# ---------------------------------------------------------------------------


class TestBuildComponentsEmptyCache:
    def test_returns_empty_list_when_not_cached(self):
        comps = tb.build_components("aruna_yoga|en_US")
        assert comps == []

    def test_contact_none_safe(self):
        # Should not raise even with contact=None
        comps = tb.build_components("aruna_yoga|en_US", contact=None)
        assert comps == []


# ---------------------------------------------------------------------------
# build_components — IMAGE header
# ---------------------------------------------------------------------------


class TestBuildComponentsImageHeader:
    def setup_method(self):
        tb._template_components["aruna_yoga|en_US"] = IMAGE_TEMPLATE_COMPS

    def test_image_header_with_media_id(self):
        comps = tb.build_components(
            "aruna_yoga|en_US",
            contact={"name": "Aruna", "phone": "919999"},
            header_media_id="MEDIA_ABC123",
        )
        assert comps[0] == {
            "type": "header",
            "parameters": [{"type": "image", "image": {"id": "MEDIA_ABC123"}}],
        }

    def test_image_header_falls_back_to_image_url(self):
        comps = tb.build_components(
            "aruna_yoga|en_US",
            contact={"name": "Aruna", "imageUrl": "https://cdn.example.com/yoga.jpg"},
            header_media_id="",
        )
        assert comps[0]["parameters"][0] == {
            "type": "image",
            "image": {"link": "https://cdn.example.com/yoga.jpg"},
        }

    def test_media_id_takes_priority_over_image_url(self):
        comps = tb.build_components(
            "aruna_yoga|en_US",
            contact={"name": "Aruna", "imageUrl": "https://cdn.example.com/yoga.jpg"},
            header_media_id="MEDIA_WINS",
        )
        # Must use id, not link
        assert "id" in comps[0]["parameters"][0]["image"]
        assert comps[0]["parameters"][0]["image"]["id"] == "MEDIA_WINS"

    def test_body_uses_contact_name_for_param1(self):
        comps = tb.build_components(
            "aruna_yoga|en_US",
            contact={"name": "Aruna"},
            header_media_id="MID",
        )
        body = next(c for c in comps if c["type"] == "body")
        assert body["parameters"][0]["text"] == "Aruna"

    def test_body_falls_back_to_example_text(self):
        comps = tb.build_components(
            "aruna_yoga|en_US",
            contact={},  # no name
            header_media_id="MID",
        )
        body = next(c for c in comps if c["type"] == "body")
        assert body["parameters"][0]["text"] == "Friend"

    def test_no_header_component_when_no_media_and_no_url(self, capsys):
        comps = tb.build_components(
            "aruna_yoga|en_US",
            contact={"name": "Aruna"},  # no imageUrl
            header_media_id="",
        )
        # Header should be silently skipped, body still present
        types = [c["type"] for c in comps]
        assert "header" not in types
        assert "body" in types
        capsys.readouterr()


# ---------------------------------------------------------------------------
# build_components — TEXT header
# ---------------------------------------------------------------------------


class TestBuildComponentsTextHeader:
    def setup_method(self):
        tb._template_components["promo|en_US"] = TEXT_HEADER_COMPS

    def test_text_header_uses_contact_name(self):
        comps = tb.build_components(
            "promo|en_US",
            contact={"name": "John", "phone": "919876"},
        )
        header = next(c for c in comps if c["type"] == "header")
        assert header["parameters"][0]["text"] == "John"

    def test_text_header_falls_back_to_example(self):
        comps = tb.build_components("promo|en_US", contact={})
        header = next(c for c in comps if c["type"] == "header")
        assert header["parameters"][0]["text"] == "Valued Customer"


# ---------------------------------------------------------------------------
# build_components — VIDEO header
# ---------------------------------------------------------------------------


class TestBuildComponentsVideoHeader:
    def setup_method(self):
        tb._template_components["intro_video|en_US"] = VIDEO_TEMPLATE_COMPS

    def test_video_with_media_id(self):
        comps = tb.build_components("intro_video|en_US", header_media_id="VID_123")
        assert comps[0]["parameters"][0] == {"type": "video", "video": {"id": "VID_123"}}

    def test_video_falls_back_to_url(self):
        comps = tb.build_components(
            "intro_video|en_US",
            contact={"videoUrl": "https://cdn.example.com/intro.mp4"},
        )
        assert comps[0]["parameters"][0] == {
            "type": "video",
            "video": {"link": "https://cdn.example.com/intro.mp4"},
        }


# ---------------------------------------------------------------------------
# build_components — DOCUMENT header
# ---------------------------------------------------------------------------


class TestBuildComponentsDocumentHeader:
    def setup_method(self):
        tb._template_components["brochure|en_US"] = DOCUMENT_TEMPLATE_COMPS

    def test_document_with_media_id(self):
        comps = tb.build_components("brochure|en_US", header_media_id="DOC_456")
        assert comps[0]["parameters"][0] == {
            "type": "document",
            "document": {"id": "DOC_456"},
        }

    def test_document_falls_back_to_url(self):
        comps = tb.build_components(
            "brochure|en_US",
            contact={"documentUrl": "https://cdn.example.com/brochure.pdf"},
        )
        assert comps[0]["parameters"][0] == {
            "type": "document",
            "document": {"link": "https://cdn.example.com/brochure.pdf"},
        }


# ---------------------------------------------------------------------------
# build_components — BODY only (text-only template)
# ---------------------------------------------------------------------------


class TestBuildComponentsBodyOnly:
    def setup_method(self):
        tb._template_components["first_trigger|en"] = BODY_ONLY_COMPS

    def test_body_only_template(self):
        comps = tb.build_components(
            "first_trigger|en",
            contact={"name": "Jane"},
        )
        assert len(comps) == 1
        assert comps[0]["type"] == "body"
        assert comps[0]["parameters"][0]["text"] == "Jane"

    def test_body_fallback_to_example_when_no_name(self):
        comps = tb.build_components("first_trigger|en", contact={})
        assert comps[0]["parameters"][0]["text"] == "Guest"


# ---------------------------------------------------------------------------
# ensure_cached
# ---------------------------------------------------------------------------


class TestEnsureCached:
    @pytest.mark.asyncio
    async def test_returns_true_if_already_cached(self):
        tb._template_components["aruna_yoga|en_US"] = IMAGE_TEMPLATE_COMPS
        mock_wa = MagicMock()
        result = await tb.ensure_cached("aruna_yoga|en_US", mock_wa, {})
        assert result is True
        mock_wa.get_templates.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetches_and_caches_on_miss(self):
        mock_wa = MagicMock()
        mock_wa.get_templates = AsyncMock(return_value={
            "success": True,
            "templates": [
                {"name": "aruna_yoga", "language": "en_US", "components": IMAGE_TEMPLATE_COMPS}
            ],
        })
        settings = {"business_account_id": "BA_123"}
        result = await tb.ensure_cached("aruna_yoga|en_US", mock_wa, settings)
        assert result is True
        assert "aruna_yoga|en_US" in tb._template_components
        mock_wa.get_templates.assert_called_once_with("BA_123")

    @pytest.mark.asyncio
    async def test_returns_false_when_api_fails(self):
        mock_wa = MagicMock()
        mock_wa.get_templates = AsyncMock(return_value={"success": False, "error": "Auth failed"})
        result = await tb.ensure_cached("aruna_yoga|en_US", mock_wa, {"business_account_id": "BA"})
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_business_account_id(self):
        mock_wa = MagicMock()
        result = await tb.ensure_cached("aruna_yoga|en_US", mock_wa, {})
        assert result is False
        mock_wa.get_templates.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_when_template_not_in_api_response(self):
        mock_wa = MagicMock()
        mock_wa.get_templates = AsyncMock(return_value={
            "success": True,
            "templates": [
                {"name": "other_template", "language": "en_US", "components": []}
            ],
        })
        result = await tb.ensure_cached("aruna_yoga|en_US", mock_wa, {"business_account_id": "BA"})
        assert result is False


# ---------------------------------------------------------------------------
# upload_header_media
# ---------------------------------------------------------------------------


class TestUploadHeaderMedia:
    @pytest.mark.asyncio
    async def test_returns_empty_string_when_not_cached(self):
        mock_wa = MagicMock()
        result = await tb.upload_header_media("nonexistent|en", mock_wa)
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_handle_url(self):
        tb._template_components["no_handle|en"] = [
            {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": []}}
        ]
        mock_wa = MagicMock()
        result = await tb.upload_header_media("no_handle|en", mock_wa)
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_media_id_on_success(self):
        tb._template_components["aruna_yoga|en_US"] = IMAGE_TEMPLATE_COMPS

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"fake_image_bytes"
        mock_response.headers = {"content-type": "image/jpeg"}

        mock_wa = MagicMock()
        mock_wa.upload_media = AsyncMock(return_value={"success": True, "mediaId": "MEDIA_XYZ"})

        # Patch httpx to avoid real network calls
        import unittest.mock as um
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with um.patch("services.template_builder.httpx.AsyncClient", return_value=mock_client):
            result = await tb.upload_header_media("aruna_yoga|en_US", mock_wa)

        assert result == "MEDIA_XYZ"
        mock_wa.upload_media.assert_called_once_with(b"fake_image_bytes", "image/jpeg")

    @pytest.mark.asyncio
    async def test_returns_empty_on_download_failure(self):
        tb._template_components["aruna_yoga|en_US"] = IMAGE_TEMPLATE_COMPS

        mock_response = MagicMock()
        mock_response.status_code = 403  # forbidden

        mock_wa = MagicMock()

        import unittest.mock as um
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with um.patch("services.template_builder.httpx.AsyncClient", return_value=mock_client):
            result = await tb.upload_header_media("aruna_yoga|en_US", mock_wa)

        assert result == ""
        mock_wa.upload_media.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_empty_on_upload_failure(self):
        tb._template_components["aruna_yoga|en_US"] = IMAGE_TEMPLATE_COMPS

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"bytes"
        mock_response.headers = {"content-type": "image/png"}

        mock_wa = MagicMock()
        mock_wa.upload_media = AsyncMock(return_value={"success": False, "error": "Upload failed"})

        import unittest.mock as um
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with um.patch("services.template_builder.httpx.AsyncClient", return_value=mock_client):
            result = await tb.upload_header_media("aruna_yoga|en_US", mock_wa)

        assert result == ""
