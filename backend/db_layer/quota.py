from __future__ import annotations

"""Postgres operations for per-tenant monthly bulk message quota.

Uses `tenant_quota_usage` table with (tenant_id, month_key) primary key.
month_key format: 'YYYY-MM' (matches usage_events convention).

Key design:
- try_consume_quota uses a conditional atomic upsert (INSERT ... ON CONFLICT
  DO UPDATE ... WHERE messages_sent < limit RETURNING ...) so quota is never
  incremented past the limit — no increment-then-undo pattern.
- get_quota_status is a pure read for UI display / pre-checks.
"""

import datetime
from dataclasses import dataclass

from database import fetchrow
from observability import log_event


def get_current_month_key() -> str:
    """Return current UTC month as 'YYYY-MM' string."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


def next_month_start() -> str:
    """Return ISO 8601 string of the first second of next calendar month in UTC."""
    now = datetime.datetime.now(datetime.timezone.utc)
    if now.month == 12:
        first = datetime.datetime(now.year + 1, 1, 1, tzinfo=datetime.timezone.utc)
    else:
        first = datetime.datetime(now.year, now.month + 1, 1, tzinfo=datetime.timezone.utc)
    return first.isoformat()


@dataclass
class QuotaStatus:
    used: int
    limit: int
    remaining: int
    month_key: str
    resets_at: str


async def get_quota_status(tenant_id: str, limit: int) -> QuotaStatus:
    """Read current quota usage for the current month. Pure read — no side effects."""
    month_key = get_current_month_key()
    resets_at = next_month_start()
    used = 0
    try:
        row = await fetchrow(
            """
            SELECT messages_sent
            FROM tenant_quota_usage
            WHERE tenant_id = %s AND month_key = %s
            """,
            tenant_id,
            month_key,
        )
        if row:
            used = int(row.get("messages_sent") or 0)
    except Exception as e:
        log_event("db_error", detail=f"quota.get_quota_status failed: {e}", level="ERROR")

    remaining = max(limit - used, 0)
    return QuotaStatus(
        used=used,
        limit=limit,
        remaining=remaining,
        month_key=month_key,
        resets_at=resets_at,
    )


async def try_consume_quota(tenant_id: str, limit: int) -> bool:
    """Atomically consume 1 from the tenant's monthly quota.

    Returns True if the increment succeeded (quota not yet full).
    Returns False if quota was already at the limit — nothing is modified.

    Uses a single conditional upsert so there is no race between check and increment.
    """
    month_key = get_current_month_key()
    try:
        row = await fetchrow(
            """
            INSERT INTO tenant_quota_usage (tenant_id, month_key, messages_sent, last_updated_at)
            VALUES (%s, %s, 1, now())
            ON CONFLICT (tenant_id, month_key)
            DO UPDATE SET
              messages_sent   = tenant_quota_usage.messages_sent + 1,
              last_updated_at = now()
            WHERE
              tenant_quota_usage.messages_sent < %s
            RETURNING messages_sent
            """,
            tenant_id,
            month_key,
            limit,
        )
        return row is not None
    except Exception as e:
        log_event("db_error", detail=f"quota.try_consume_quota failed: {e}", level="ERROR")
        return False
