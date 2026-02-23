"""WappFlow Backend – FastAPI entry point (Phase-5: optimized).

Single app definition with CORS and Firebase Auth middleware.
Includes 30-day TTL cleanup task for transient collections.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from starlette.middleware import Middleware
import asyncio
import datetime
from firebase_config import get_db

load_dotenv()

# Phase-4: Fail fast on missing environment configuration
from startup_checks import validate_environment
validate_environment(strict=False)

# Initialize Firebase before creating the app (required for auth middleware)
from firebase_config import init_firebase
init_firebase()

# Firebase Auth middleware — enforces tenant_id on every non-public route
from auth_middleware import FirebaseAuthMiddleware

origins = [
    "https://wappflow-1.onrender.com",
    "http://localhost:3000",
]

app = FastAPI(
    title="WappFlow API",
    version="1.0.0",
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        ),
        Middleware(FirebaseAuthMiddleware),
    ],
)

# Register routers
from routers.settings import router as settings_router
from routers.file_forward import router as file_forward_router
from routers.bulk_message import router as bulk_message_router
from routers.chatbot import router as chatbot_router
from routers.logs import router as logs_router
from routers.webhook import router as webhook_router
from routers.webhooks import router as chatbot_webhooks_router

app.include_router(settings_router)
app.include_router(file_forward_router)
app.include_router(bulk_message_router)
app.include_router(chatbot_router)
app.include_router(logs_router)
app.include_router(webhook_router)
app.include_router(chatbot_webhooks_router)


# ── TTL Cleanup Background Task ─────────────────────────────────────

async def periodical_cleanup():
    """Delete data older than 30 days from transient collections.

    Runs every 6 hours. Processes in batches of 400 to stay well under
    Firestore's 500-write batch limit.

    NEVER touches tenant, chatbot_config, or chatbot_rules collections.
    """
    while True:
        # Wait 6 hours between cleanup runs
        await asyncio.sleep(6 * 3600)

        print("[CLEANUP] Starting 30-day TTL cleanup...")
        db = get_db()
        if not db:
            print("[CLEANUP] Firestore not available, skipping.")
            continue

        # Transient collections only — tenant/config data is preserved
        collections_to_clean = [
            "chat_messages",
            "messages",
            "webhook_events",
            "usage_events",
        ]

        # 30-day cutoff
        cutoff = (
            datetime.datetime.utcnow() - datetime.timedelta(days=30)
        ).isoformat()

        for col_name in collections_to_clean:
            try:
                col_ref = db.collection(col_name)
                docs = (
                    col_ref.where("created_at", "<", cutoff)
                    .limit(2000)  # Server-side limit to prevent unbounded scans
                    .stream()
                )

                batch = db.batch()
                count = 0
                deleted_total = 0

                for doc in docs:
                    batch.delete(doc.reference)
                    count += 1
                    deleted_total += 1
                    if count >= 400:
                        batch.commit()
                        batch = db.batch()
                        count = 0

                if count > 0:
                    batch.commit()

                if deleted_total > 0:
                    print(f"[CLEANUP] Deleted {deleted_total} docs older than 30d from: {col_name}")
                else:
                    print(f"[CLEANUP] {col_name}: no expired docs found.")
            except Exception as e:
                print(f"[CLEANUP] Error cleaning {col_name}: {e}")

        print("[CLEANUP] TTL cleanup complete.")


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(periodical_cleanup())


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "WappFlow API is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)