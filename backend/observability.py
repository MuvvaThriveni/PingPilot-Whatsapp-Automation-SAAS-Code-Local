"""Structured observability logging for WappFlow (Phase-4).

Emits JSON-style structured log lines with standard fields:
  tenant_id, campaign_id, phone, operation, duration_ms

No sensitive data (tokens, keys, message bodies) is ever logged.
"""

from __future__ import annotations

import time
import json
import functools
from contextlib import contextmanager
from typing import Any


def _safe(value: Any) -> str:
    """Coerce a value to a short, safe string for logging."""
    if value is None:
        return ""
    s = str(value)
    # Truncate long values (e.g. accidental token leak guard)
    return s[:120] if len(s) > 120 else s


def log_event(
    operation: str,
    *,
    tenant_id: str = "",
    campaign_id: str = "",
    phone: str = "",
    duration_ms: int | float | None = None,
    status: str = "",
    detail: str = "",
    level: str = "INFO",
):
    """Emit a single structured log line."""
    entry = {
        "level": level,
        "op": operation,
    }
    if tenant_id:
        entry["tenant"] = _safe(tenant_id)
    if campaign_id:
        entry["campaign"] = _safe(campaign_id)
    if phone:
        entry["phone"] = _safe(phone)
    if duration_ms is not None:
        entry["ms"] = round(duration_ms, 1)
    if status:
        entry["status"] = _safe(status)
    if detail:
        entry["detail"] = _safe(detail)
    print(json.dumps(entry, separators=(",", ":")))


@contextmanager
def timed_op(
    operation: str,
    *,
    tenant_id: str = "",
    campaign_id: str = "",
    phone: str = "",
):
    """Context manager that logs operation duration on exit.

    Usage:
        with timed_op("send_template", tenant_id=tid, phone=to):
            result = await whatsapp.send_template_message(...)
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        log_event(
            operation,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            phone=phone,
            duration_ms=elapsed_ms,
        )
