"""Redis-backed rate limiting for WappFlow API (Phase-7).

Provides:
- FastAPI dependency-based rate limiters (per-IP and per-tenant)
- Tenant-aware token bucket for worker message sending
- Global cooldown check for adaptive 429 handling

All backed by the same Redis instance used by BullMQ queues.
"""

from __future__ import annotations

import os
import time
import math
from fastapi import Request
from fastapi.responses import JSONResponse
from observability import log_event

import redis.asyncio as aioredis
from redis.exceptions import NoScriptError
from urllib.parse import urlparse

# ── Redis connection (shared singleton) ─────────────────────────────
# Production: set REDIS_URL (e.g. rediss://default:password@host:port)
# Local dev:  set REDIS_HOST + REDIS_PORT (defaults to localhost:6379)

REDIS_URL = os.environ.get("REDIS_URL", "")
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

_redis_client: aioredis.Redis | None = None


def _build_redis_kwargs() -> dict:
    """Build connection kwargs from REDIS_URL or REDIS_HOST/PORT."""
    if REDIS_URL:
        parsed = urlparse(REDIS_URL)
        use_ssl = parsed.scheme in ("rediss", "redis+ssl")
        kwargs: dict = {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or (6380 if use_ssl else 6379),
            "decode_responses": True,
            "ssl": use_ssl,
        }
        if parsed.password:
            kwargs["password"] = parsed.password
        if parsed.username:
            kwargs["username"] = parsed.username
        if use_ssl:
            kwargs["ssl_cert_reqs"] = None  # Accept cloud provider certs
        return kwargs
    return {
        "host": REDIS_HOST,
        "port": REDIS_PORT,
        "decode_responses": True,
    }


async def get_redis() -> aioredis.Redis:
    """Return (and lazily create) the shared async Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.Redis(**_build_redis_kwargs())
    return _redis_client


async def redis_health_check() -> bool:
    """Ping Redis and return True if reachable."""
    try:
        r = await get_redis()
        return await r.ping()
    except Exception:
        return False


async def close_redis():
    """Close the shared Redis client. Call on app shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


def get_redis_opts_for_bullmq() -> dict:
    """Return connection dict compatible with BullMQ's connection option."""
    if REDIS_URL:
        parsed = urlparse(REDIS_URL)
        use_ssl = parsed.scheme in ("rediss", "redis+ssl")
        opts: dict = {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or (6380 if use_ssl else 6379),
        }
        if parsed.password:
            opts["password"] = parsed.password
        if parsed.username:
            opts["username"] = parsed.username
        if use_ssl:
            opts["ssl"] = True
            opts["ssl_cert_reqs"] = None
        return opts
    return {
        "host": REDIS_HOST,
        "port": REDIS_PORT,
    }


# ── Helper: extract client IP (X-Forwarded-For aware) ──────────────

def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


# ── Token Bucket Rate Limiter (Lua-based, single Redis command) ────
# Reduces Redis commands from ~5 to 1 per request using EVALSHA.

# Feature flag for gradual rollout
USE_TOKEN_BUCKET = os.getenv("USE_TOKEN_BUCKET", "true").lower() == "true"

# Lua script for atomic token bucket with refill logic
_TOKEN_BUCKET_RATE_LIMIT_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local data = redis.call("HMGET", key, "tokens", "last_refill")
local tokens = tonumber(data[1])
local last_refill = tonumber(data[2])

if tokens == nil then
    tokens = capacity
    last_refill = now
end

local delta = math.max(0, now - last_refill)
local refill = delta * refill_rate
tokens = math.min(capacity, tokens + refill)

if tokens < 1 then
    redis.call("HMSET", key, "tokens", tokens, "last_refill", now)
    redis.call("EXPIRE", key, ttl)
    return {0, tokens}
end

tokens = tokens - 1
redis.call("HMSET", key, "tokens", tokens, "last_refill", now)
redis.call("EXPIRE", key, ttl)

return {1, tokens}
"""

# SHA cache for EVALSHA (loaded lazily on first use)
_TOKEN_BUCKET_SHA: str | None = None


async def _get_token_bucket_sha(r: aioredis.Redis) -> str:
    """Get cached SHA or load Lua script into Redis.
    
    Uses module-level cache to avoid repeated SCRIPT LOAD calls.
    """
    global _TOKEN_BUCKET_SHA
    if _TOKEN_BUCKET_SHA is None:
        _TOKEN_BUCKET_SHA = await r.script_load(_TOKEN_BUCKET_RATE_LIMIT_LUA)
    return _TOKEN_BUCKET_SHA


async def check_rate_limit_token_bucket(
    key: str,
    capacity: int,
    refill_rate: float,
) -> tuple[bool, float]:
    """Token bucket rate limiter using single EVALSHA call.
    
    Args:
        key: Redis key for this rate limit bucket
        capacity: Maximum tokens (burst capacity)
        refill_rate: Tokens refilled per second
    
    Returns:
        (allowed: bool, retry_after_seconds: int)
    """
    r = await get_redis()
    now = time.time()
    ttl = math.ceil(capacity / max(refill_rate, 1e-6))
    
    try:
        # Try EVALSHA first (fast path)
        sha = await _get_token_bucket_sha(r)
        result = await r.evalsha(sha, 1, key, capacity, refill_rate, now, ttl)
    except NoScriptError:
        # Reset cached SHA
        global _TOKEN_BUCKET_SHA
        _TOKEN_BUCKET_SHA = None

        # Fallback to EVAL (guaranteed execution)
        result = await r.eval(
            _TOKEN_BUCKET_RATE_LIMIT_LUA,
            1,
            key,
            capacity,
            refill_rate,
            now,
            ttl,
        )

        # Re-cache SHA for future calls
        _TOKEN_BUCKET_SHA = await r.script_load(_TOKEN_BUCKET_RATE_LIMIT_LUA)
    
    allowed = bool(result[0])
    remaining_tokens = float(result[1])
    
    if not allowed:
        # Calculate retry_after based on tokens needed
        retry_after = math.ceil(max(0, (1 - remaining_tokens) / refill_rate))
        return False, retry_after
    
    return True, 0


# ── Sliding-window rate limiter (generic) ───────────────────────────
# Uses Redis sorted sets for precise sliding window counting.
# KEPT AS FALLBACK for safe rollout.

async def _check_rate_limit(
    key: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """Check and record a request against a sliding window.

    Returns (allowed: bool, retry_after_seconds: int).
    """
    r = await get_redis()
    now = time.time()
    window_start = now - window_seconds

    pipe = r.pipeline()
    # Remove expired entries
    pipe.zremrangebyscore(key, 0, window_start)
    # Count current entries
    pipe.zcard(key)
    # Add current request (score = timestamp, member = unique timestamp)
    pipe.zadd(key, {f"{now}": now})
    # Set TTL so keys auto-expire
    pipe.expire(key, window_seconds + 1)
    results = await pipe.execute()

    current_count = results[1]  # zcard result before adding

    if current_count >= max_requests:
        # Over limit — remove the entry we just added
        await r.zrem(key, f"{now}")
        # Calculate retry_after from oldest entry
        oldest = await r.zrange(key, 0, 0, withscores=True)
        if oldest:
            oldest_ts = oldest[0][1]
            retry_after = max(1, math.ceil((oldest_ts + window_seconds) - now))
        else:
            retry_after = window_seconds
        return False, retry_after

    return True, 0


# ── Starlette Middleware (runs inside the middleware stack) ──────────
# This must be placed AFTER FirebaseAuthMiddleware so tenant_id is available.

from starlette.middleware.base import BaseHTTPMiddleware

# Heavy endpoints: user-submit (write) actions only — stricter per-tenant limits.
# Tuple of (HTTP_METHOD, path_prefix).  Only POST/DELETE actions that trigger
# real work (campaign start, file send, contact parse, resend-failed, delete).
# Read-only polling endpoints (GET /status, GET /details) use the general tier.
_HEAVY_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST",   "/api/bulk-message/start"),
    ("POST",   "/api/bulk-message/parse"),
    ("POST",   "/api/bulk-message/stop/"),
    ("POST",   "/api/bulk-message/campaigns/"),   # resend-failed
    ("DELETE", "/api/bulk-message/campaigns/"),   # delete campaign
    ("POST",   "/api/file-forward/send"),
    ("POST",   "/api/file-forward/send-bulk"),
    ("POST",   "/api/file-forward/parse-contacts"),
    ("POST",   "/api/settings/whatsapp"),
    ("POST",   "/api/settings/whatsapp/test"),
)

# Paths that are never rate-limited
# /api/webhook is called by WhatsApp servers — rate limiting risks webhook deregistration
_SKIP_PATHS = ("/", "/api/health", "/docs", "/openapi.json", "/api/webhook", "/webhook")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-backed rate limiting middleware.

    - Heavy (write) endpoints: 10 req/min per tenant_id
    - General (read/poll) endpoints: 300 req/min per tenant_id
    - /api/health, /docs, /api/webhook: no limit (webhook is called by WhatsApp)
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip paths that should never be rate-limited
        if any(path.startswith(s) for s in _SKIP_PATHS):
            return await call_next(request)

        try:
            # Authenticated endpoints: rate limit by tenant_id
            tenant_id = getattr(request.state, "tenant_id", None)
            if tenant_id:
                method = request.method.upper()
                is_heavy = any(
                    method == m and path.startswith(p)
                    for m, p in _HEAVY_ROUTES
                )
                if is_heavy:
                    max_req, window = 10, 60
                    key = f"rl:tenant:{tenant_id}:heavy"
                else:
                    max_req, window = 300, 60
                    key = f"rl:tenant:{tenant_id}:general"

                if USE_TOKEN_BUCKET:
                    # Token bucket: convert max_req/window to capacity and refill_rate
                    capacity = max_req
                    refill_rate = max_req / window  # tokens per second
                    allowed, retry_after = await check_rate_limit_token_bucket(
                        key, capacity, refill_rate
                    )
                else:
                    # Fallback to sliding window
                    allowed, retry_after = await _check_rate_limit(key, max_req, window)
                if not allowed:
                    log_event(
                        "api_rate_limited",
                        tenant_id=tenant_id,
                        detail=f"endpoint={path} tier={'heavy' if is_heavy else 'general'} retry_after={retry_after}",
                        level="WARN",
                    )
                    return JSONResponse(
                        status_code=429,
                        content={"error": "rate_limited", "retry_after_seconds": retry_after},
                        headers={"Retry-After": str(retry_after)},
                    )
        except Exception:
            pass  # Never block requests due to rate-limiter errors

        return await call_next(request)


# ── FastAPI dependency-based rate limiters (optional per-route use) ──

def rate_limit_by_ip(max_requests: int = 60, window_seconds: int = 60):
    """Rate limit by client IP address. For public/unauthenticated endpoints."""
    async def dependency(request: Request):
        ip = _client_ip(request)
        key = f"rl:ip:{ip}:{request.url.path}"
        if USE_TOKEN_BUCKET:
            capacity = max_requests
            refill_rate = max_requests / window_seconds
            allowed, retry_after = await check_rate_limit_token_bucket(
                key, capacity, refill_rate
            )
        else:
            allowed, retry_after = await _check_rate_limit(key, max_requests, window_seconds)
        if not allowed:
            log_event(
                "api_rate_limited",
                detail=f"ip={ip} endpoint={request.url.path} retry_after={retry_after}",
                level="WARN",
            )
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited", "retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        return None
    return dependency


def rate_limit_by_tenant(max_requests: int = 300, window_seconds: int = 60):
    """Rate limit by tenant_id. For authenticated endpoints."""
    async def dependency(request: Request):
        tenant_id = getattr(request.state, "tenant_id", None)
        if not tenant_id:
            # Auth middleware hasn't run yet or public route — skip
            return None
        key = f"rl:tenant:{tenant_id}:{request.url.path}"
        if USE_TOKEN_BUCKET:
            capacity = max_requests
            refill_rate = max_requests / window_seconds
            allowed, retry_after = await check_rate_limit_token_bucket(
                key, capacity, refill_rate
            )
        else:
            allowed, retry_after = await _check_rate_limit(key, max_requests, window_seconds)
        if not allowed:
            log_event(
                "api_rate_limited",
                tenant_id=tenant_id,
                detail=f"endpoint={request.url.path} retry_after={retry_after}",
                level="WARN",
            )
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited", "retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        return None
    return dependency


# ── Tenant token bucket for worker-side rate limiting ───────────────
# Lua script for atomic token bucket: refill + consume.

_TOKEN_BUCKET_LUA = """
local key       = KEYS[1]
local max_tokens = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now        = tonumber(ARGV[3])

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens     = tonumber(data[1])
local last_refill = tonumber(data[2])

if tokens == nil then
    tokens = max_tokens
    last_refill = now
end

-- Refill tokens based on elapsed time
local elapsed = math.max(0, now - last_refill)
local new_tokens = math.min(max_tokens, tokens + elapsed * refill_rate)

if new_tokens >= 1 then
    -- Consume one token
    new_tokens = new_tokens - 1
    redis.call('HMSET', key, 'tokens', new_tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 60)
    return 1  -- allowed
else
    -- No tokens available
    redis.call('HMSET', key, 'tokens', new_tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 60)
    return 0  -- denied
end
"""

# Configuration from environment
TENANT_RATE_LIMIT = int(os.environ.get("TENANT_RATE_LIMIT", "10"))  # msgs/sec
TENANT_BURST = int(os.environ.get("TENANT_BURST", "20"))  # max burst

# In-memory cache for tenant token bucket results (Fix #5: Redis command optimization)
# Maps tenant_id → {"allowed": bool, "expires_at": float}
# When the bucket was checked recently and had tokens, skip the Redis EVAL.
_TENANT_BUCKET_CACHE: dict[str, dict] = {}
_TENANT_BUCKET_CACHE_TTL = 5.0  # seconds — safe because we already throttle via asyncio.sleep in workers


async def tenant_token_bucket_consume(tenant_id: str) -> bool:
    """Try to consume one token from the tenant's bucket.

    Returns True if allowed, False if the tenant should be throttled.
    Uses a short in-memory cache (5s) to reduce Redis EVAL calls during
    high-throughput campaign bursts.
    """
    now = time.time()
    cached = _TENANT_BUCKET_CACHE.get(tenant_id)
    if cached and now < cached["expires_at"]:
        return cached["allowed"]

    r = await get_redis()
    key = f"tb:tenant:{tenant_id}"
    result = await r.eval(
        _TOKEN_BUCKET_LUA,
        1,
        key,
        TENANT_BURST,          # max_tokens (burst capacity)
        TENANT_RATE_LIMIT,     # refill_rate (tokens per second)
        now,
    )
    allowed = bool(result)

    # Cache the result.  If denied, use a shorter TTL so we re-check sooner.
    _TENANT_BUCKET_CACHE[tenant_id] = {
        "allowed": allowed,
        "expires_at": now + (_TENANT_BUCKET_CACHE_TTL if allowed else 1.0),
    }
    return allowed


# ── Global cooldown for adaptive 429 handling ───────────────────────

GLOBAL_COOLDOWN_KEY = "wa:global_cooldown"
GLOBAL_COOLDOWN_TTL = int(os.environ.get("WA_COOLDOWN_TTL", "5"))  # seconds

# In-memory cache for cooldown status (Fix #3: Redis command optimization)
# Reduces Redis GET calls from ~1,000/campaign to ~10/campaign
_cooldown_cache = {"value": False, "expires_at": 0.0}


async def set_global_cooldown():
    """Activate global cooldown after repeated WhatsApp 429 responses."""
    r = await get_redis()
    await r.set(GLOBAL_COOLDOWN_KEY, "1", ex=GLOBAL_COOLDOWN_TTL)
    log_event("wa_global_cooldown_set", detail=f"ttl={GLOBAL_COOLDOWN_TTL}s", level="WARN")
    # Invalidate cache immediately when cooldown is set
    _cooldown_cache["expires_at"] = 0.0


async def is_global_cooldown_active() -> bool:
    """Check if the global cooldown is currently active.
    
    Uses a 2-second in-memory cache to reduce Redis GET commands during
    high-throughput message processing (e.g., 1,000-message campaigns).
    """
    now = time.time()
    if now < _cooldown_cache["expires_at"]:
        return _cooldown_cache["value"]
    
    # Cache expired — fetch from Redis
    r = await get_redis()
    val = await r.get(GLOBAL_COOLDOWN_KEY)
    active = val is not None
    
    # Update cache with 2-second TTL
    _cooldown_cache["value"] = active
    _cooldown_cache["expires_at"] = now + 2.0
    return active
