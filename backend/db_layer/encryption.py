"""Fernet-based encryption helpers for storing secrets at rest.

Usage:
    from db_layer.encryption import encrypt_secret, decrypt_secret

    encrypted = encrypt_secret("my_plain_secret")
    plain = decrypt_secret(encrypted)

Requires ENCRYPTION_KEY in .env (base64-encoded 32-byte Fernet key).
Generate one with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Backward compatibility:
    decrypt_secret() gracefully handles plain-text values that were stored
    before encryption was enabled — it returns them as-is instead of crashing.
"""

from __future__ import annotations

import os
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# ── Fernet cipher (lazy-initialised from env) ────────────────────────

_ENCRYPTION_PREFIX = "enc:"  # Marks a value as Fernet-encrypted

_fernet: Optional[Fernet] = None
_init_attempted = False


def _get_fernet() -> Optional[Fernet]:
    """Lazily initialise the Fernet cipher from ENCRYPTION_KEY env var."""
    global _fernet, _init_attempted
    if _init_attempted:
        return _fernet
    _init_attempted = True

    key = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not key:
        logger.warning(
            "ENCRYPTION_KEY not set — secrets will be stored in plain text. "
            "Generate one: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
        return None

    try:
        _fernet = Fernet(key.encode())
        logger.info("Encryption layer initialised successfully")
    except Exception as e:
        logger.error(f"Invalid ENCRYPTION_KEY — encryption disabled: {e}")
        _fernet = None

    return _fernet


# ── Public API ────────────────────────────────────────────────────────

def encrypt_secret(plain_text: str) -> str:
    """Encrypt a plain-text secret using Fernet.

    Returns prefixed encrypted string ("enc:<ciphertext>").
    If encryption is not configured, returns plain text unchanged.
    """
    if not plain_text:
        return plain_text

    f = _get_fernet()
    if f is None:
        # No encryption key configured — store as plain text
        return plain_text

    encrypted = f.encrypt(plain_text.encode()).decode()
    return f"{_ENCRYPTION_PREFIX}{encrypted}"


def decrypt_secret(stored_value: str) -> str:
    """Decrypt a stored secret.

    Handles three cases:
      1. Prefixed with "enc:" → decrypt with Fernet
      2. Plain text (no prefix) → return as-is (backward compatibility)
      3. Empty string → return empty
    """
    if not stored_value:
        return stored_value

    # Not encrypted — plain text from before encryption was enabled
    if not stored_value.startswith(_ENCRYPTION_PREFIX):
        return stored_value

    f = _get_fernet()
    if f is None:
        logger.error(
            "Encrypted value found but ENCRYPTION_KEY is not set — cannot decrypt"
        )
        return ""

    cipher_text = stored_value[len(_ENCRYPTION_PREFIX):]
    try:
        return f.decrypt(cipher_text.encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt secret — invalid token or wrong key")
        return ""
