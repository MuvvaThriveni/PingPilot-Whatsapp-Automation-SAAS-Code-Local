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
        """Resolve the WhatsApp access token for a tenant.

        Priority:
          1. 'access_token' stored directly in Firestore (survives restarts)
          2. 'token_ref' env-var reference (legacy / env-based deployments)
        """
        def _strip_bearer(val: str) -> str:
            if val and val.lower().startswith("bearer "):
                return val[7:].strip()
            return val

        # Primary: direct field in Firestore document
        direct = tenant_doc.get("access_token", "")
        if direct:
            return _strip_bearer(direct)

        # Fallback: env-var reference
        ref = tenant_doc.get("token_ref", "")
        resolved = resolve(ref)
        if resolved:
            return _strip_bearer(resolved)

        return ""

    # @staticmethod
    # def resolve_openai_key(chatbot_doc: dict) -> str:
    #     """Resolve the OpenAI API key for a tenant's chatbot config.
    #
    #     Priority:
    #       1. 'openai_api_key' stored directly in Firestore (survives restarts)
    #       2. 'openai_key_ref' env-var reference (legacy / env-based deployments)
    #     """
    #     # Primary: direct field in Firestore document
    #     direct = chatbot_doc.get("openai_api_key", "")
    #     if direct:
    #         return direct
    #
    #     # Fallback: env-var reference
    #     ref = chatbot_doc.get("openai_key_ref", "")
    #     resolved = resolve(ref)
    #     return resolved if resolved else ""


secrets = _Secrets()
