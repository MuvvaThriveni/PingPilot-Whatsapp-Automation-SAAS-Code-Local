"""WappFlow Backend – FastAPI entry point (Phase-7: rate-limited).

Redis-backed API rate limiting, CORS from environment,
lifespan-based startup/shutdown, deep health checks,
and graceful campaign shutdown.
"""

import os
import asyncio
import datetime
from contextlib import asynccontextmanager
from utils.time_utils import get_ist_now

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware import Middleware

load_dotenv()

# Phase-4: Fail fast on missing environment configuration
from startup_checks import validate_environment
validate_environment(strict=False)

# Initialize Firebase before creating the app (required for auth middleware)
from firebase_config import init_firebase
init_firebase()

from database import init_db_pool, close_db_pool, ping

# Firebase Auth middleware — enforces tenant_id on every non-public route
from auth_middleware import FirebaseAuthMiddleware
from observability import log_event
from rate_limit import get_redis, close_redis, redis_health_check, RateLimitMiddleware

# ── Helpers ──────────────────────────────────────────────────────────

def _ist_now() -> datetime.datetime:
    """Return timezone-aware IST now."""
    return get_ist_now()


# ── CORS origins from environment (fallback to hardcoded dev list) ───
_default_origins = "https://whatsapp-automation-swart.vercel.app,https://wappflow-1-h55h.onrender.com,https://wappflow-1.onrender.com"
_env_origins = os.environ.get("CORS_ORIGINS", _default_origins)
origins = [o.strip() for o in _env_origins.split(",") if o.strip()]

# Only include localhost in non-production environments
_is_production = os.environ.get("ENVIRONMENT", "development").lower() == "production"
if not _is_production:
    origins.append("http://localhost:3000")


# ── TTL Cleanup Background Task ─────────────────────────────────────

async def periodical_cleanup():
    """Delete data older than 30 days from transient collections.

    Runs every 6 hours. Processes in batches of 400 to stay well under
    conservative statement timeouts.

    NEVER touches tenant, chatbot_config, or chatbot_rules tables.
    """
    while True:
        # Wait 6 hours between cleanup runs
        await asyncio.sleep(6 * 3600)

        log_event("cleanup_start", detail="30-day TTL cleanup")

        from database import execute

        # Transient collections only — tenant/config data is preserved
        collections_to_clean = [
            "chat_messages",
            "messages",
            "webhook_events",
            "usage_events",
        ]

        # 30-day cutoff (timezone-aware UTC)
        cutoff_dt = (_ist_now() - datetime.timedelta(days=30)).astimezone(datetime.timezone.utc)
        cutoff = cutoff_dt.isoformat()

        for col_name in collections_to_clean:
            try:
                sql = f"DELETE FROM {col_name} WHERE created_at < %s::timestamptz"
                result = await execute(sql, cutoff)
                log_event("cleanup_done", detail=f"{col_name}: {result}")
            except Exception as e:
                log_event("cleanup_error", detail=f"{col_name}: {e}", level="ERROR")

        log_event("cleanup_complete")


# ── Lifespan (replaces deprecated @app.on_event) ────────────────────

# Track background tasks for graceful shutdown
_background_tasks: list[asyncio.Task] = []

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    # ── STARTUP ──
    await init_db_pool()

    # Initialize shared Redis connection for rate limiting
    await get_redis()
    log_event("redis_rate_limiter", status="connected")

    from startup_cache import prewarm_cache
    await prewarm_cache()

    t1 = asyncio.create_task(periodical_cleanup())
    _background_tasks.append(t1)

    from routers.bulk_message import periodical_scheduler
    t2 = asyncio.create_task(periodical_scheduler())
    _background_tasks.append(t2)

    log_event("app_startup", status="ok")

    yield

    # ── SHUTDOWN (graceful) ──
    log_event("app_shutdown", detail="graceful shutdown initiated")

    # Signal all active campaigns to stop
    from store import active_campaigns
    for cid in list(active_campaigns.keys()):
        active_campaigns[cid]["running"] = False
        log_event("campaign_shutdown", campaign_id=cid, detail="stop signal sent")

    # Cancel background tasks
    for task in _background_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Mark any still-running campaigns as interrupted in the database
    try:
        from db_layer.campaigns import campaigns as _db_campaigns
        running = await _db_campaigns.list_running_global()
        for c in running:
            await _db_campaigns.update_status(
                c["tenant_id"], str(c["campaign_id"]), "interrupted",
                error_message="Server shutdown during campaign processing",
            )
            log_event("campaign_interrupted", campaign_id=str(c["campaign_id"]))
    except Exception as e:
        log_event("shutdown_error", detail=str(e), level="ERROR")

    await close_redis()
    await close_db_pool()

    log_event("app_shutdown", status="complete")


# ── FastAPI app ──────────────────────────────────────────────────────

# Disable docs in production
_docs_url = None if _is_production else "/docs"
_openapi_url = None if _is_production else "/openapi.json"

app = FastAPI(
    title="WappFlow API",
    version="1.1.0",
    lifespan=lifespan,
    docs_url=_docs_url,
    openapi_url=_openapi_url,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        ),
        Middleware(FirebaseAuthMiddleware),
        Middleware(RateLimitMiddleware),
    ],
)

# Register routers
from routers.settings import router as settings_router
from routers.file_forward import router as file_forward_router
from routers.bulk_message import router as bulk_message_router
from routers.chatbot import router as chatbot_router
from routers.logs import router as logs_router
from routers.webhook import router as webhook_router

app.include_router(settings_router)
app.include_router(file_forward_router)
app.include_router(bulk_message_router)
app.include_router(chatbot_router)
app.include_router(logs_router)
app.include_router(webhook_router)


# ── Deep Health Check ────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """Deep health check: verifies Postgres connectivity and background task liveness."""
    checks = {"api": "ok"}
    overall = "ok"

    # Check Postgres
    try:
        ok = await ping()
        if ok:
            checks["postgres"] = "ok"
        else:
            checks["postgres"] = "unavailable"
            overall = "degraded"
    except Exception as e:
        checks["postgres"] = f"error: {str(e)[:80]}"
        overall = "degraded"

    # Check Redis
    try:
        redis_ok = await redis_health_check()
        if redis_ok:
            checks["redis"] = "ok"
        else:
            checks["redis"] = "unavailable"
            overall = "degraded"
    except Exception as e:
        checks["redis"] = f"error: {str(e)[:80]}"
        overall = "degraded"

    # Check background tasks
    alive_tasks = sum(1 for t in _background_tasks if not t.done())
    checks["background_tasks"] = f"{alive_tasks}/{len(_background_tasks)} alive"
    if alive_tasks < len(_background_tasks):
        overall = "degraded"

    return {"status": overall, "checks": checks}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
