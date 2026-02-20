"""Centralized in-memory cache to prevent repeated Firestore reads.

All Firestore reads should go through this cache when possible.
Each cache entry has a TTL (time-to-live) after which it is considered stale.
Thread-safe via simple dict operations (GIL protected for single-process).

This is a process-local cache — safe for single-worker deployments.
For multi-worker, each worker gets its own cache which is acceptable
since TTLs are short (30-120 seconds).
"""

import time
from typing import Any, Optional, Callable


class _CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.expires_at = time.monotonic() + ttl


class InMemoryCache:
    """Simple TTL-based in-memory cache."""

    def __init__(self):
        self._store: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Optional[Any]:
        """Return cached value if present and not expired, else None."""
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: float = 60.0):
        """Store a value with the given TTL in seconds."""
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

    def get_or_fetch(self, key: str, fetcher: Callable, ttl: float = 60.0) -> Any:
        """Return cached value or call fetcher() to populate cache.
        
        This is the primary method for reducing Firestore reads.
        """
        val = self.get(key)
        if val is not None:
            return val
        val = fetcher()
        if val is not None:
            self.set(key, val, ttl)
        return val


# Singleton cache instance
cache = InMemoryCache()


# ── Cache key builders ──────────────────────────────────────────────

def tenant_key(tenant_id: str) -> str:
    return f"tenant:{tenant_id}"

def tenant_by_phone_key(phone_number_id: str) -> str:
    return f"tenant_phone:{phone_number_id}"

def chatbot_config_key(tenant_id: str) -> str:
    return f"chatbot_config:{tenant_id}"

def chatbot_rules_key(tenant_id: str) -> str:
    return f"chatbot_rules:{tenant_id}"

def chat_users_key(tenant_id: str) -> str:
    return f"chat_users:{tenant_id}"

def usage_key(tenant_id: str) -> str:
    return f"usage:{tenant_id}"

def settings_key(tenant_id: str) -> str:
    return f"settings:{tenant_id}"
