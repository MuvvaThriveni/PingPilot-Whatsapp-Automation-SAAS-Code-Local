"""Centralized in-memory cache to prevent repeated Firestore reads.

Required for reducing Firestore quota usage.
Default TTL is now 6 hours (21600s).
"""

import time
import logging
from typing import Any, Optional, Callable

logger = logging.getLogger(__name__)

class _CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.expires_at = time.monotonic() + ttl


class InMemoryCache:
    """Simple TTL-based in-memory cache with an additional persistent session store."""

    def __init__(self):
        self._store: dict[str, _CacheEntry] = {}
        # session_store does not use TTL (or use very long one)
        self._session_store: dict[str, Any] = {}

    def get(self, key: str) -> Optional[Any]:
        """Return cached value if present and not expired, else None."""
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: float = 21600.0):
        """Store a value with the given TTL in seconds. Default 6 hours."""
        self._store[key] = _CacheEntry(value, ttl)

    def invalidate(self, key: str):
        """Remove a specific key from cache."""
        self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str):
        """Remove all keys starting with the given prefix."""
        keys_to_remove = [k for k in self._store if k.startswith(prefix)]
        for k in keys_to_remove:
            del self._store[k]

    def clear(self):
        """Remove all cached entries."""
        self._store.clear()

    def get_or_fetch(self, key: str, fetcher: Callable, ttl: float = 21600.0) -> Any:
        """Return cached value or call fetcher() to populate cache.
        
        Primary method for reducing Firestore reads. Default 6h TTL.
        """
        val = self.get(key)
        if val is not None:
            return val
        
        logger.info(f"[CACHE] Miss for key: {key}. Fetching from origin...")
        val = fetcher()
        if val is not None:
            self.set(key, val, ttl)
        return val

    # ── Session Storage (In-memory state) ───────────────────────────
    
    def get_session(self, user_id: str) -> Optional[Any]:
        return self._session_store.get(user_id)

    def set_session(self, user_id: str, value: Any):
        self._session_store[user_id] = value


# Singleton cache instance
cache = InMemoryCache()

# ── Global Helper Functions (Requested) ─────────────────────────────

def get_cached(key: str) -> Optional[Any]:
    return cache.get(key)

def set_cached(key: str, value: Any, ttl: float = 21600.0):
    cache.set(key, value, ttl)

def fetch_cached(key: str, fetcher: Callable, ttl: float = 21600.0) -> Any:
    return cache.get_or_fetch(key, fetcher, ttl)


# ── Cache key builders ──────────────────────────────────────────────

def tenant_key(tenant_id: str) -> str:
    return f"tenant:{tenant_id}"

def tenant_by_phone_key(phone_number_id: str) -> str:
    return f"tenant_phone:{phone_number_id}"

def chatbot_config_key(tenant_id: str) -> str:
    return f"chatbot_config:{tenant_id}"

def chatbot_rules_key(tenant_id: str) -> str:
    return f"chatbot_rules:{tenant_id}"

def chatbot_active_rules_key(tenant_id: str) -> str:
    return f"chatbot_rules_active:{tenant_id}"

def chat_users_key(tenant_id: str) -> str:
    return f"chat_users:{tenant_id}"

def usage_key(tenant_id: str) -> str:
    return f"usage:{tenant_id}"

def settings_key(tenant_id: str) -> str:
    return f"settings:{tenant_id}"

def wa_message_mapping_key(wa_message_id: str) -> str:
    return f"wa_map:{wa_message_id}"
