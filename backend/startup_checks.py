"""Startup environment validation (Phase-4).

Fail fast if critical configuration is missing or invalid.
Called once from main.py before the app starts serving requests.
"""

from __future__ import annotations

import os
import sys

from observability import log_event


def _check_firebase() -> list[str]:
    """Verify Firebase Admin SDK can initialise."""
    errors: list[str] = []
    service_account_path = os.path.join(
        os.path.dirname(__file__),
        "firebase-service-account.json",
    )
    if not os.path.exists(service_account_path):
        errors.append(
            f"Firebase service account file not found at {service_account_path}. "
            "Set GOOGLE_APPLICATION_CREDENTIALS or place the file in the backend directory."
        )
    # Also accept the standard env var
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS") and not os.path.exists(service_account_path):
        alt = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        if not os.path.exists(alt):
            errors.append(
                f"GOOGLE_APPLICATION_CREDENTIALS points to {alt} which does not exist."
            )
    return errors


def _check_whatsapp_api_version() -> list[str]:
    """Warn if WHATSAPP_API_VERSION is not set (defaults to v18.0 in code)."""
    errors: list[str] = []
    version = os.getenv("WHATSAPP_API_VERSION", "")
    if not version:
        log_event("startup_warn", detail="WHATSAPP_API_VERSION not set, defaulting to v18.0", level="WARN")
    return errors


def validate_environment(*, strict: bool = True) -> None:
    """Run all startup checks. If strict=True, exit on fatal errors."""
    log_event("startup_validate", detail="running environment checks")

    all_errors: list[str] = []
    all_errors.extend(_check_firebase())
    all_errors.extend(_check_whatsapp_api_version())

    for err in all_errors:
        log_event("startup_error", detail=err, level="ERROR")

    if all_errors and strict:
        print(f"\n[FATAL] {len(all_errors)} startup check(s) failed. Aborting.\n")
        sys.exit(1)

    log_event("startup_validate", status="ok", detail=f"checks_passed={len(all_errors) == 0}")
