"""User trigger tracking (Phase-6: hardened).

Tracks the last time a "first trigger" template was sent to each phone number
to enforce a 24-hour cooldown period.

Fixes:
- Bare except: clause replaced with Exception
- Timestamps now persisted in Firestore (survive server restarts)
- Fallback to in-memory cache if Firestore is unavailable
"""

import datetime
from cache import cache
from observability import log_event
from utils.time_utils import get_ist_now, parse_iso_to_ist


def _ist_now() -> datetime.datetime:
    return get_ist_now()


class UsersDB:
    """Manages user trigger state with Firestore persistence and in-memory cache."""

    def _cache_key(self, phone_number: str) -> str:
        return f"user_trigger:{phone_number}"

    def should_send_trigger(self, phone_number: str) -> bool:
        """Check if we should send a trigger (once every 24 hours).

        Priority: in-memory cache → Firestore → default True.
        """
        # 1. Check in-memory cache first (fastest path)
        cache_key = self._cache_key(phone_number)
        cached = cache.get(cache_key)
        if cached is not None:
            try:
                last_dt = parse_iso_to_ist(cached)
                diff = _ist_now() - last_dt
                return diff.total_seconds() > 24 * 3600
            except (ValueError, TypeError) as e:
                log_event("trigger_check_error", detail=f"cache parse: {e}", level="WARN")
                return True

        # 2. Check Firestore (survives restarts)
        try:
            from firebase_config import get_db
            db = get_db()
            if db:
                doc = db.collection("user_triggers").document(phone_number).get()
                if doc.exists:
                    data = doc.to_dict()
                    last_at = data.get("last_trigger_at", "")
                    if last_at:
                        # Cache in memory for subsequent checks
                        cache.set(cache_key, last_at, ttl=3600.0 * 25)
                        last_dt = parse_iso_to_ist(last_at)
                        diff = _ist_now() - last_dt
                        return diff.total_seconds() > 24 * 3600
        except Exception as e:
            log_event("trigger_check_error", detail=f"firestore: {e}", level="WARN")

        return True

    def record_trigger(self, phone_number: str):
        """Record the time of the latest trigger. Persists to Firestore and cache."""
        now_str = _ist_now().isoformat()
        cache_key = self._cache_key(phone_number)

        # 1. Always update in-memory cache
        cache.set(cache_key, now_str, ttl=3600.0 * 25)

        # 2. Persist to Firestore (survives restarts)
        try:
            from firebase_config import get_db
            db = get_db()
            if db:
                db.collection("user_triggers").document(phone_number).set({
                    "phone_number": phone_number,
                    "last_trigger_at": now_str,
                }, merge=True)
        except Exception as e:
            log_event("trigger_record_error", detail=f"firestore: {e}", level="WARN")


users_db = UsersDB()
