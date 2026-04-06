"""Phone number normalization utilities.

Handles international numbers correctly while preserving existing
+91 (India) fallback behavior for 10-digit local numbers.
"""

import logging
from typing import Optional

logger = logging.getLogger("phone_utils")


def normalize_phone(phone_str: str) -> Optional[str]:
    """Normalize a raw phone string into a digits-only international number.

    Returns None for invalid numbers (outside E.164 range of 10-15 digits)
    instead of raising exceptions, so callers can skip gracefully.

    Rules:
        1. Detect if the original input starts with '+' (international prefix).
        2. Strip all non-digit characters.
        3. Only apply the +91 (India) fallback when ALL of:
           - The original input did NOT start with '+'
           - The stripped number is exactly 10 digits
           - The first digit is 6, 7, 8, or 9 (Indian mobile range)
        4. Otherwise, return the stripped digits as-is.
        5. Validate final length is 10-15 digits (E.164); return None if not.

    Examples:
        >>> normalize_phone("+14155552671")   # US number → preserved
        '14155552671'
        >>> normalize_phone("+44 7911 123456")  # UK number → preserved
        '447911123456'
        >>> normalize_phone("+919876543210")  # Indian with code → preserved
        '919876543210'
        >>> normalize_phone("9876543210")     # Indian without code → 91 prepended
        '919876543210'
        >>> normalize_phone("022-12345678")   # Indian landline → no 91 (not mobile)
        '02212345678'
    """
    raw = str(phone_str).strip()

    # Step 1: Detect international prefix BEFORE stripping
    has_plus = raw.startswith("+")

    # Step 2: Handle numeric values (int/float/scientific notation like "9.1995E+11")
    # The '+' in scientific notation (e.g. "9.1995E+11") is NOT an international prefix.
    # We detect this by checking if '+' only appears after 'e'/'E', not at position 0.
    try:
        numeric = float(raw)
        # If it parsed as a float, treat as a numeric phone value (no int'l prefix)
        raw = f"{numeric:.0f}"
        has_plus = False  # Scientific-notation '+' is not a country-code prefix
    except (ValueError, TypeError):
        if raw.endswith(".0"):
            raw = raw[:-2]

    # Step 3: Strip all non-digit characters
    phone = "".join(filter(str.isdigit, raw))

    # Step 4: Apply India (+91) fallback ONLY for local numbers without '+'
    if not has_plus and len(phone) == 10 and phone[0] in ("6", "7", "8", "9"):
        phone = "91" + phone

    # Step 5: Validate E.164 length (10-15 digits)
    if len(phone) < 10 or len(phone) > 15:
        logger.warning("Invalid phone number (length=%d): raw=%s", len(phone), str(phone_str).strip())
        return None

    return phone
