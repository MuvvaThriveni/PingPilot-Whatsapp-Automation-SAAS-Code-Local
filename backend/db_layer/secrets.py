"""Runtime secret resolution — never stores secrets in Firestore.

Secrets are resolved from environment variables using a reference key.
Tenant documents store only the *ref* (e.g. "env:WHATSAPP_ACCESS_TOKEN")
and this module resolves the actual value at runtime.
"""

import os
from typing import Optional


# Supported ref prefixes
_ENV_PREFIX = "env:"


def resolve(ref: str) -> Optional[str]:
    """Resolve a secret reference to its actual value.

    Supported formats:
        "env:VAR_NAME"  — reads os.environ["VAR_NAME"]
        plain string    — returned as-is (backward compat during migration)
    """
    if not ref:
        return None
    if ref.startswith(_ENV_PREFIX):
        var_name = ref[len(_ENV_PREFIX):]
        return os.environ.get(var_name, "")
    # Backward compat: treat raw non-empty string as the value itself.
    # This allows the migration period where callers still pass raw tokens.
    return ref


def make_ref(env_var_name: str) -> str:
    """Create an env-backed secret reference string."""
    return f"{_ENV_PREFIX}{env_var_name}"


class _Secrets:
    """Namespace for secret-resolution helpers."""

    resolve = staticmethod(resolve)
    make_ref = staticmethod(make_ref)

    @staticmethod
    def resolve_wa_token(tenant_doc: dict) -> str:
        """Resolve the WhatsApp access token for a tenant."""
        ref = tenant_doc.get("token_ref", "")
        resolved = resolve(ref)
        if resolved:
            # Strip "Bearer " prefix if present (safety for tokens saved before prefix stripping)
            if resolved.lower().startswith("bearer "):
                resolved = resolved[7:].strip()
            return resolved
        # Fallback: check legacy field still in memory during migration
        token = tenant_doc.get("access_token", "")
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token

    @staticmethod
    def resolve_openai_key(chatbot_doc: dict) -> str:
        """Resolve the OpenAI API key for a tenant's chatbot config."""
        ref = chatbot_doc.get("openai_key_ref", "")
        resolved = resolve(ref)
        if resolved:
            return resolved
        # Fallback: check legacy field still in memory during migration
        return chatbot_doc.get("openai_api_key", "")


secrets = _Secrets()
