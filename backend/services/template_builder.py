"""Shared WhatsApp template component builder (Phase-6: tenant-isolated).

CRITICAL SECURITY FIX: Template component cache is now tenant-scoped.
Previously, `_template_components` was keyed by "name|language" only,
causing cross-tenant data leakage when two tenants had templates with
the same name but different content.

Now keyed by "{tenant_id}:{name}|{language}".

Also caches uploaded media IDs to avoid re-uploading on every webhook trigger.

Public API
----------
``ensure_cached(template_key, whatsapp, settings, tenant_id) -> bool``
``get_components(template_key, tenant_id) -> list``
``build_components(template_key, contact, header_media_id, tenant_id) -> list``
``validate_components(template_key, components, tenant_id) -> tuple[bool, str]``
``upload_header_media(template_key, whatsapp, tenant_id) -> str``
``get_template_keys_for_tenant(tenant_id, template_name) -> list[str]``
"""

from __future__ import annotations

import re
import httpx
from observability import log_event

# Regex patterns for template variable detection
_POSITIONAL_VAR_RE = re.compile(r"\{\{(\d+)\}\}")
_NAMED_VAR_RE = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")
_ANY_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

# ---------------------------------------------------------------------------
# Tenant-scoped template metadata cache
#
# Key format:  "{tenant_id}:{template_name}|{language_code}"
# Value:       raw ``components`` list from the WhatsApp API response
# ---------------------------------------------------------------------------
_template_components: dict[str, list] = {}

# ---------------------------------------------------------------------------
# Cached uploaded media IDs (avoids re-uploading on every webhook trigger)
#
# Key format:  "{tenant_id}:{template_name}|{language_code}"
# Value:       WhatsApp media_id string
# ---------------------------------------------------------------------------
_uploaded_media_ids: dict[str, str] = {}


def _tenant_key(tenant_id: str, template_key: str) -> str:
    """Build tenant-scoped cache key."""
    return f"{tenant_id}:{template_key}"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_components(template_key: str, tenant_id: str = "") -> list:
    """Return the raw cached component list for *template_key* (or empty list)."""
    if tenant_id:
        return _template_components.get(_tenant_key(tenant_id, template_key), [])
    # Backward compat: check without tenant prefix
    return _template_components.get(template_key, [])


def get_template_keys_for_tenant(tenant_id: str, template_name: str) -> list[str]:
    """Find all cached template keys for a tenant matching the given template name.

    Returns keys in "name|language" format (without tenant prefix).
    """
    prefix = f"{tenant_id}:{template_name}|"
    exact = f"{tenant_id}:{template_name}"
    result = []
    for k in _template_components:
        if k == exact or k.startswith(prefix):
            # Strip tenant prefix for caller
            result.append(k.split(":", 1)[1] if ":" in k else k)
    return result


def has_media_header(template_key: str, tenant_id: str = "") -> bool:
    """Return True if the cached template has a media (IMAGE/VIDEO/DOCUMENT) header."""
    full_key = _tenant_key(tenant_id, template_key) if tenant_id else template_key
    cached = _template_components.get(full_key, [])
    for comp in cached:
        if comp.get("type") == "HEADER" and comp.get("format") in ("IMAGE", "VIDEO", "DOCUMENT"):
            return True
    return False


def invalidate_cached_media(template_key: str, tenant_id: str = ""):
    """Remove a cached media_id so the next call re-uploads."""
    full_key = _tenant_key(tenant_id, template_key) if tenant_id else template_key
    _uploaded_media_ids.pop(full_key, None)


async def ensure_cached(template_key: str, whatsapp, settings: dict,
                        tenant_id: str = "") -> bool:
    """Ensure *template_key* is present in the tenant-scoped cache.

    If the key is already cached, returns True immediately (no network call).
    Otherwise fetches all templates from the WhatsApp Business API, populates
    the cache, and returns True if the key was found.
    """
    full_key = _tenant_key(tenant_id, template_key) if tenant_id else template_key

    if full_key in _template_components:
        return True

    # Try Postgres-backed persistent cache first (tenant-scoped only)
    if tenant_id and "|" in template_key:
        try:
            from db_layer.template_cache import template_cache_db

            name, lang = template_key.split("|", 1)
            cached = await template_cache_db.get(tenant_id, name.strip(), lang.strip())
            if cached and cached.get("components"):
                _template_components[full_key] = cached.get("components")
                return True
        except Exception:
            pass

    log_event("template_cache_miss", tenant_id=tenant_id, detail=f"key={template_key}")
    baid = settings.get("business_account_id", "")
    if not baid:
        log_event("template_cache_error", tenant_id=tenant_id, level="ERROR",
                  detail="business_account_id missing from settings")
        return False

    result = await whatsapp.get_templates(baid)
    if not result["success"]:
        log_event("template_fetch_failed", tenant_id=tenant_id, level="WARN",
                  detail=result.get("error", ""))
        return False

    for t in result["templates"]:
        key = f"{t['name']}|{t['language']}"
        cache_key = _tenant_key(tenant_id, key) if tenant_id else key
        _template_components[cache_key] = t.get("components", [])

    # Best-effort persist to Postgres so templates survive restarts
    if tenant_id:
        try:
            from db_layer.template_cache import template_cache_db

            await template_cache_db.upsert_batch(tenant_id, result.get("templates", []))
        except Exception:
            pass

    found = full_key in _template_components
    log_event("template_cache_populated", tenant_id=tenant_id,
              detail=f"cached={len(result['templates'])} found={found} key={template_key}")
    return found


async def upload_header_media(template_key: str, whatsapp,
                              tenant_id: str = "") -> str:
    """Upload the example header media embedded in the template definition.

    Caches the resulting media_id to avoid re-uploading on subsequent calls
    (e.g., every webhook trigger for the same template).

    Returns the WhatsApp media_id string on success, or empty string on failure.
    """
    full_key = _tenant_key(tenant_id, template_key) if tenant_id else template_key

    # Check media ID cache first
    cached_media_id = _uploaded_media_ids.get(full_key)
    if cached_media_id:
        return cached_media_id

    cached = _template_components.get(full_key, [])
    for comp in cached:
        if comp.get("type") != "HEADER":
            continue
        if comp.get("format") not in ("IMAGE", "VIDEO", "DOCUMENT"):
            continue

        handle_list = (comp.get("example") or {}).get("header_handle") or []
        handle_url = handle_list[0] if handle_list else ""
        if not handle_url:
            log_event("template_media_missing", tenant_id=tenant_id, level="WARN",
                      detail=f"'{template_key}' has media header but no example.header_handle")
            break

        log_event("template_media_upload", tenant_id=tenant_id,
                  detail=f"Uploading header media for '{template_key}'")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                dl = await client.get(handle_url)
            if dl.status_code != 200:
                log_event("template_media_download_failed", tenant_id=tenant_id, level="WARN",
                          detail=f"status={dl.status_code}")
                break

            mime = dl.headers.get("content-type", "image/jpeg").split(";")[0].strip().lower()

            # Validate: downloaded content must be actual media, not an error page
            # (expired scontent.whatsapp.net CDN URLs return HTML or JSON errors)
            if mime.startswith("text/") or mime == "application/json":
                log_event("template_media_invalid_content", tenant_id=tenant_id, level="WARN",
                          detail=f"CDN returned content-type '{mime}' — URL likely expired")
                break

            if len(dl.content) < 100:
                log_event("template_media_too_small", tenant_id=tenant_id, level="WARN",
                          detail=f"Downloaded content suspiciously small ({len(dl.content)} bytes)")
                break

            # Infer proper MIME when CDN returns a generic content-type
            if mime in ("application/octet-stream", "binary/octet-stream"):
                fmt = comp.get("format", "").upper()
                mime = {"IMAGE": "image/jpeg", "VIDEO": "video/mp4", "DOCUMENT": "application/pdf"}.get(fmt, mime)

            file_bytes = dl.content

            # ── Image compression (IMAGE headers only) ─────────────────
            # WhatsApp rejects images > 5 MB with (#100) Invalid parameter.
            # Compress before uploading; non-image formats pass through.
            if comp.get("format", "").upper() == "IMAGE":
                try:
                    from utils.image_utils import compress_image
                    file_bytes = compress_image(file_bytes)
                    # Update MIME if compression converted PNG → JPEG
                    if file_bytes[:3] == b'\xff\xd8\xff':
                        mime = "image/jpeg"
                    elif file_bytes[:8] == b'\x89PNG\r\n\x1a\n':
                        mime = "image/png"
                except Exception as exc:
                    log_event("image_compression_import_error",
                              tenant_id=tenant_id, level="WARN",
                              detail=f"Compression skipped: {str(exc)[:100]}")
                    # Fallback: upload original uncompressed bytes
                    file_bytes = dl.content

            upload_result = await whatsapp.upload_media(file_bytes, mime)
            if upload_result["success"]:
                media_id: str = upload_result["mediaId"]
                # Cache the media ID for reuse
                _uploaded_media_ids[full_key] = media_id
                log_event("template_media_uploaded", tenant_id=tenant_id,
                          detail=f"mediaId cached for '{template_key}'")
                return media_id
            else:
                log_event("template_media_upload_failed", tenant_id=tenant_id, level="WARN",
                          detail=upload_result.get("error", ""))
        except Exception as exc:
            log_event("template_media_error", tenant_id=tenant_id, level="WARN",
                      detail=str(exc)[:120])
        break  # only one HEADER component matters

    return ""


def _resolve_param_value(var_name: str, index: int, contact: dict,
                         example_texts: list, section: str = "body") -> str:
    """Resolve a single template parameter value from contact data or examples.

    Lookup order:
    1. Exact key match in contact (e.g. contact["name"] for {{name}})
    2. Positional fallback: contact["name"] for index 0, contact["phone"] for index 1
    3. Example text from the template definition
    4. Single space as last resort (keeps param count correct)
    """
    # 1. Named match — try exact key in contact
    if not var_name.isdigit() and contact.get(var_name):
        return str(contact[var_name])

    # 2. Positional fallback for well-known slots
    if section == "header":
        if index == 0 and contact.get("name"):
            return contact["name"]
    else:  # body
        if index == 0 and contact.get("name"):
            return contact["name"]
        if index == 1 and contact.get("phone"):
            return contact["phone"]

    # 3. Example text
    if index < len(example_texts):
        return example_texts[index]

    # 4. Last resort
    return " "


def _is_named_var(var_name: str) -> bool:
    """Return True if the variable name is alphabetic (named), not numeric (positional)."""
    return not var_name.isdigit()


def _build_param_entry(var_name: str, value: str) -> dict:
    """Build a single parameter dict for the WhatsApp API.

    Named variables ({{name}}) → include parameter_name field.
    Positional variables ({{1}}) → text only.
    """
    if _is_named_var(var_name):
        return {"type": "text", "parameter_name": var_name, "text": value}
    return {"type": "text", "text": value}


def build_components(
    template_key: str,
    contact: dict | None = None,
    header_media_id: str = "",
    tenant_id: str = "",
) -> list:
    """Build the runtime *components* payload for ``send_template_message()``.

    Handles:
    - Positional variables: ``{{1}}``, ``{{2}}``
    - Named variables: ``{{name}}``, ``{{phone}}``
    - IMAGE / VIDEO / DOCUMENT headers (media id or link)
    - TEXT headers with variables
    - BODY with variables

    Uses tenant-scoped cached template metadata.
    """
    full_key = _tenant_key(tenant_id, template_key) if tenant_id else template_key
    cached = _template_components.get(full_key, [])
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
                # Detect both positional {{1}} and named {{name}} variables
                var_matches = _ANY_VAR_RE.findall(text)
                if var_matches:
                    example_texts = example.get("header_text", [])
                    parameters = []
                    for i, var_name in enumerate(var_matches):
                        value = _resolve_param_value(
                            var_name, i, contact, example_texts, section="header"
                        )
                        parameters.append(_build_param_entry(var_name, value))
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

        # ── BODY ───────────────────────────────────────────────────────────
        elif comp_type == "BODY":
            text = comp.get("text", "")
            # Detect both positional {{1}} and named {{name}} variables
            var_matches = _ANY_VAR_RE.findall(text)
            if var_matches:
                example_texts = (
                    example.get("body_text", [[]])[0]
                    if example.get("body_text")
                    else []
                )
                parameters = []
                for i, var_name in enumerate(var_matches):
                    value = _resolve_param_value(
                        var_name, i, contact, example_texts, section="body"
                    )
                    parameters.append(_build_param_entry(var_name, value))
                components.append({"type": "body", "parameters": parameters})

    return components


def validate_components(
    template_key: str,
    components: list,
    tenant_id: str = "",
) -> tuple[bool, str]:
    """Validate that *components* match the cached template expectations.

    Returns ``(True, "")`` if valid, or ``(False, error_message)`` on mismatch.
    Checks:
    - Media headers have a corresponding header component
    - Body parameter count matches template variable count
    - Header text parameter count matches
    """
    full_key = _tenant_key(tenant_id, template_key) if tenant_id else template_key
    cached = _template_components.get(full_key, [])
    if not cached:
        return True, ""  # nothing to validate against

    built_by_type: dict[str, dict] = {}
    for c in (components or []):
        built_by_type[c.get("type", "")] = c

    for comp in cached:
        comp_type = comp.get("type", "")

        if comp_type == "HEADER":
            header_format = comp.get("format", "TEXT")

            # Media headers MUST have a header component
            if header_format in ("IMAGE", "VIDEO", "DOCUMENT"):
                if "header" not in built_by_type:
                    return False, (
                        f"Template '{template_key}' requires a {header_format} header "
                        f"but no header component was built (missing media_id or URL)"
                    )

            # Text headers with variables must match count
            if header_format == "TEXT":
                text = comp.get("text", "")
                expected_count = len(_ANY_VAR_RE.findall(text))
                if expected_count > 0:
                    header_comp = built_by_type.get("header")
                    actual_count = len((header_comp or {}).get("parameters", []))
                    if actual_count != expected_count:
                        return False, (
                            f"Header parameter count mismatch: template expects "
                            f"{expected_count} but built {actual_count}"
                        )

        elif comp_type == "BODY":
            text = comp.get("text", "")
            expected_count = len(_ANY_VAR_RE.findall(text))
            if expected_count > 0:
                body_comp = built_by_type.get("body")
                actual_count = len((body_comp or {}).get("parameters", []))
                if actual_count != expected_count:
                    return False, (
                        f"Body parameter count mismatch: template expects "
                        f"{expected_count} but built {actual_count}"
                    )

    return True, ""
