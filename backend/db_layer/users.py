"""User trigger tracking (Phase-6: hardened).

Tracks the last time a "first trigger" template was sent to each phone number
to enforce a 24-hour cooldown period.

Fixes:
- Bare except: clause replaced with Exception
- Timestamps are persisted in Postgres (survive server restarts)
- Fallback to in-memory cache on DB errors
"""

import datetime
from cache import cache
from observability import log_event
from utils.time_utils import get_ist_now, parse_iso_to_ist

from database import fetchrow, execute


def _ist_now() -> datetime.datetime:
    return get_ist_now()


class UsersDB:
    """Manages user trigger state with Postgres persistence and in-memory cache."""

    def _cache_key(self, tenant_id: str, phone_number: str) -> str:
        return f"user_trigger:{tenant_id}:{phone_number}"

    async def should_send_trigger(self, tenant_id: str, phone_number: str) -> bool:
        """Check if we should send a trigger (once every 24 hours).

        Priority: in-memory cache → database → default True.
        """
        # 1. Check in-memory cache first (fastest path)
        cache_key = self._cache_key(tenant_id, phone_number)
        cached = cache.get(cache_key)
        if cached is not None:
            try:
                last_dt = parse_iso_to_ist(cached)
                diff = _ist_now() - last_dt
                return diff.total_seconds() > 24 * 3600
            except (ValueError, TypeError) as e:
                log_event("trigger_check_error", detail=f"cache parse: {e}", level="WARN")
                return True

        # 2. Check Postgres (survives restarts)
        try:
            row = await fetchrow(
                """
                SELECT last_trigger_at
                FROM user_triggers
                WHERE tenant_id = %s AND phone_number = %s
                """,
                tenant_id,
                phone_number,
            )
            if row and row.get("last_trigger_at"):
                last_dt = row["last_trigger_at"].astimezone(get_ist_now().tzinfo)
                cache.set(cache_key, last_dt.isoformat(), ttl=3600.0 * 25)
                diff = _ist_now() - last_dt
                return diff.total_seconds() > 24 * 3600
        except Exception as e:
            log_event("trigger_check_error", detail=f"postgres: {e}", level="WARN")

        return True

    async def record_trigger(self, tenant_id: str, phone_number: str):
        """Record the time of the latest trigger. Persists to database and cache."""
        now_str = _ist_now().isoformat()
        cache_key = self._cache_key(tenant_id, phone_number)

        # 1. Always update in-memory cache
        cache.set(cache_key, now_str, ttl=3600.0 * 25)

        # 2. Persist to Postgres (survives restarts)
        try:
            await execute(
                """
                INSERT INTO user_triggers (tenant_id, phone_number, last_trigger_at, created_at, updated_at)
                VALUES (%s, %s, %s::timestamptz, now(), now())
                ON CONFLICT (tenant_id, phone_number) DO UPDATE SET
                    last_trigger_at = EXCLUDED.last_trigger_at,
                    updated_at = now()
                """,
                tenant_id,
                phone_number,
                now_str,
            )
        except Exception as e:
            log_event("trigger_record_error", detail=f"postgres: {e}", level="WARN")


users_db = UsersDB()
