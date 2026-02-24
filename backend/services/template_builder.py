"""Shared WhatsApp template component builder.

This module owns the process-local template metadata cache and provides the
functions needed to construct the ``components`` array accepted by
``WhatsAppService.send_template_message()``.

Design goals
------------
* Single source of truth for ``_template_components`` cache.
* Automatic cache population (fetch from WhatsApp API on miss).
* Automatic header-media upload using the template's own ``example.header_handle``
  — no hardcoded URLs, no .env image hacks.
* Works identically for bulk campaigns AND webhook-triggered templates.
* Multi-tenant safe (each WhatsAppService instance carries its own credentials).

Public API
----------
``ensure_cached(template_key, whatsapp, settings) -> bool``
    Guarantee that the template metadata for *template_key* is in the cache.
    Fetches from the WhatsApp API when missing.  Returns True on success.

``get_components(template_key) -> list``
    Return the raw cached component metadata for a template key.

``build_components(template_key, contact, header_media_id) -> list``
    Build the runtime ``components`` payload from cached metadata.

``upload_header_media(template_key, whatsapp) -> str``
    Download the example handle URL embedded in a template's IMAGE/VIDEO/DOCUMENT
    HEADER component and upload it to the WhatsApp media endpoint.
    Returns the resulting ``media_id`` string (or empty string on failure).
"""

from __future__ import annotations

import re
import httpx

# ---------------------------------------------------------------------------
# Process-local template metadata cache
#
# Key format:  "{template_name}|{language_code}"   e.g. "aruna_yoga|en_US"
# Value:       raw ``components`` list from the WhatsApp API response
# ---------------------------------------------------------------------------
_template_components: dict[str, list] = {}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_components(template_key: str) -> list:
    """Return the raw cached component list for *template_key* (or empty list)."""
    return _template_components.get(template_key, [])


async def ensure_cached(template_key: str, whatsapp, settings: dict) -> bool:
    """Ensure *template_key* is present in the in-process cache.

    If the key is already cached, returns True immediately (no network call).
    Otherwise fetches all templates from the WhatsApp Business API, populates
    the cache, and returns True if the key was found.

    Parameters
    ----------
    template_key:
        ``"name|language"`` string, e.g. ``"aruna_yoga|en_US"``.
    whatsapp:
        An initialised ``WhatsAppService`` instance for the correct tenant.
    settings:
        The tenant settings dict (must contain ``business_account_id``).

    Returns
    -------
    bool
        True  — template is now in cache (either was already, or just fetched).
        False — fetch failed or template not found in the API response.
    """
    if template_key in _template_components:
        return True

    print(f"[TEMPLATE_BUILDER] '{template_key}' not in cache — fetching from WhatsApp API…")
    baid = settings.get("business_account_id", "")
    if not baid:
        print("[TEMPLATE_BUILDER] ERROR: business_account_id missing from settings — cannot fetch templates.")
        return False

    result = await whatsapp.get_templates(baid)
    if not result["success"]:
        print(f"[TEMPLATE_BUILDER] WARNING: Could not fetch templates: {result.get('error')}")
        return False

    for t in result["templates"]:
        key = f"{t['name']}|{t['language']}"
        _template_components[key] = t.get("components", [])

    found = template_key in _template_components
    print(
        f"[TEMPLATE_BUILDER] Cached {len(result['templates'])} templates. "
        f"'{template_key}' found={found}"
    )
    return found


async def upload_header_media(template_key: str, whatsapp) -> str:
    """Upload the example header media embedded in the template definition.

    Reads the ``example.header_handle`` URL from the first HEADER component
    that has format IMAGE | VIDEO | DOCUMENT, downloads the bytes, then uploads
    them via ``whatsapp.upload_media()``.

    Returns the WhatsApp ``media_id`` string on success, or empty string on any
    failure.  All errors are non-fatal (logged via ``print`` only).
    """
    cached = _template_components.get(template_key, [])
    for comp in cached:
        if comp.get("type") != "HEADER":
            continue
        if comp.get("format") not in ("IMAGE", "VIDEO", "DOCUMENT"):
            continue

        handle_list = (comp.get("example") or {}).get("header_handle") or []
        handle_url = handle_list[0] if handle_list else ""
        if not handle_url:
            print(
                f"[TEMPLATE_BUILDER] Template '{template_key}' has media header but no "
                f"example.header_handle — cannot auto-upload."
            )
            break  # only one HEADER per template

        print(f"[TEMPLATE_BUILDER] Auto-uploading header media from example handle for '{template_key}'…")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                dl = await client.get(handle_url)
            if dl.status_code != 200:
                print(
                    f"[TEMPLATE_BUILDER] WARNING: Could not download example media "
                    f"(status={dl.status_code}) — will try sending without header."
                )
                break

            mime = dl.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            upload_result = await whatsapp.upload_media(dl.content, mime)
            if upload_result["success"]:
                media_id: str = upload_result["mediaId"]
                print(
                    f"[TEMPLATE_BUILDER] ✅ Header media uploaded. "
                    f"mediaId={media_id} mime={mime}"
                )
                return media_id
            else:
                print(
                    f"[TEMPLATE_BUILDER] WARNING: Media upload failed: "
                    f"{upload_result.get('error')} — will try sending without header."
                )
        except Exception as exc:
            print(
                f"[TEMPLATE_BUILDER] WARNING: Exception auto-uploading header media: "
                f"{exc} — will try without header."
            )
        break  # only one HEADER component matters

    return ""


def build_components(
    template_key: str,
    contact: dict | None = None,
    header_media_id: str = "",
) -> list:
    """Build the runtime *components* payload for ``send_template_message()``.

    Uses cached template metadata to generate the correct ``components`` array,
    handling all header types (IMAGE, VIDEO, DOCUMENT, TEXT) as well as
    parameterised BODY components.

    Priority for media headers
    --------------------------
    1. *header_media_id* — pre-uploaded permanent WhatsApp media ID (preferred).
    2. ``contact["imageUrl"]`` / ``contact["videoUrl"]`` / ``contact["documentUrl"]``
       — per-contact URL from a spreadsheet column.
    3. Nothing — header component is silently omitted (text-only templates still work).

    Parameters
    ----------
    template_key:
        ``"name|language"`` string, e.g. ``"aruna_yoga|en_US"``.
    contact:
        Optional contact dict with keys: ``name``, ``phone``, ``imageUrl``, etc.
        Pass ``None`` (or ``{}``) for webhook-triggered sends where no spreadsheet
        row is available.
    header_media_id:
        Pre-uploaded WhatsApp media ID returned by ``upload_header_media()``.

    Returns
    -------
    list
        Ready-to-use components array, or ``[]`` if the template is not cached.
    """
    cached = _template_components.get(template_key, [])
    if not cached:
        return []

    contact = contact or {}
    components: list[dict] = []

    for comp in cached:
        comp_type = comp.get("type", "")
        example = comp.get("example", {}) or {}

        # ── HEADER ─────────────────────────────────────────────────────────
        if comp_type == "HEADER":
            header_format = comp.get("format", "TEXT")

            if header_format == "TEXT":
                text = comp.get("text", "")
                params = re.findall(r"\{\{\d+\}\}", text)
                if params:
                    example_texts = example.get("header_text", [])
                    parameters = []
                    for i, _ in enumerate(params):
                        if i == 0 and contact.get("name"):
                            parameters.append({"type": "text", "text": contact["name"]})
                        elif i < len(example_texts):
                            parameters.append({"type": "text", "text": example_texts[i]})
                        else:
                            parameters.append({"type": "text", "text": " "})
                    components.append({"type": "header", "parameters": parameters})

            elif header_format == "IMAGE":
                if header_media_id:
                    components.append({
                        "type": "header",
                        "parameters": [{"type": "image", "image": {"id": header_media_id}}],
                    })
                else:
                    image_link = contact.get("imageUrl", "").strip()
                    if image_link:
                        components.append({
                            "type": "header",
                            "parameters": [{"type": "image", "image": {"link": image_link}}],
                        })
                    else:
                        print(
                            f"[TEMPLATE_BUILDER] WARNING: '{template_key}' has IMAGE header "
                            f"but no media_id or imageUrl — skipping header component."
                        )

            elif header_format == "VIDEO":
                if header_media_id:
                    components.append({
                        "type": "header",
                        "parameters": [{"type": "video", "video": {"id": header_media_id}}],
                    })
                else:
                    video_link = contact.get("videoUrl", "").strip()
                    if video_link:
                        components.append({
                            "type": "header",
                            "parameters": [{"type": "video", "video": {"link": video_link}}],
                        })
                    else:
                        print(
                            f"[TEMPLATE_BUILDER] WARNING: '{template_key}' has VIDEO header "
                            f"but no media_id or videoUrl — skipping header component."
                        )

            elif header_format == "DOCUMENT":
                if header_media_id:
                    components.append({
                        "type": "header",
                        "parameters": [{"type": "document", "document": {"id": header_media_id}}],
                    })
                else:
                    doc_link = contact.get("documentUrl", "").strip()
                    if doc_link:
                        components.append({
                            "type": "header",
                            "parameters": [{"type": "document", "document": {"link": doc_link}}],
                        })
                    else:
                        print(
                            f"[TEMPLATE_BUILDER] WARNING: '{template_key}' has DOCUMENT header "
                            f"but no media_id or documentUrl — skipping header component."
                        )

        # ── BODY ───────────────────────────────────────────────────────────
        elif comp_type == "BODY":
            text = comp.get("text", "")
            params = re.findall(r"\{\{\d+\}\}", text)
            if params:
                example_texts = (
                    example.get("body_text", [[]])[0]
                    if example.get("body_text")
                    else []
                )
                parameters = []
                for i, _ in enumerate(params):
                    if i == 0 and contact.get("name"):
                        parameters.append({"type": "text", "text": contact["name"]})
                    elif i == 1 and contact.get("phone"):
                        parameters.append({"type": "text", "text": contact["phone"]})
                    elif i < len(example_texts):
                        parameters.append({"type": "text", "text": example_texts[i]})
                    else:
                        parameters.append({"type": "text", "text": " "})
                components.append({"type": "body", "parameters": parameters})

    return components
