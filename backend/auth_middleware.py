"""Firebase Auth middleware for multi-tenant enforcement (Phase-3).

Verifies the Firebase ID token from the Authorization header and injects
`request.state.tenant_id` (= Firebase Auth UID) on every authenticated request.

Unauthenticated paths (webhook, health) are whitelisted.
"""

import firebase_admin.auth
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from db_layer.tenants import tenants as _db_tenants
from observability import log_event

# Paths that do NOT require authentication
_PUBLIC_PREFIXES = (
    "/api/webhook",
    "/api/health",
    "/docs",
    "/openapi.json",
)


class FirebaseAuthMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public endpoints through without auth
        if any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
            return await call_next(request)

        # Allow CORS preflight
        if request.method == "OPTIONS":
            return await call_next(request)

        # Extract Bearer token
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "Missing or invalid Authorization header"},
            )

        id_token = auth_header[7:].strip()
        if not id_token:
            return JSONResponse(
                status_code=401,
                content={"error": "Empty bearer token"},
            )

        # Verify Firebase ID token
        try:
            decoded = firebase_admin.auth.verify_id_token(id_token)
        except firebase_admin.auth.ExpiredIdTokenError:
            return JSONResponse(
                status_code=401,
                content={"error": "Token expired"},
            )
        except firebase_admin.auth.InvalidIdTokenError:
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid token"},
            )
        except Exception as e:
            log_event("auth_verify", level="WARN", detail=str(e))
            return JSONResponse(
                status_code=401,
                content={"error": "Authentication failed"},
            )

        tenant_id = decoded["uid"]
        request.state.tenant_id = tenant_id

        # Lazy tenant bootstrap: create tenant doc on first request
        _ensure_tenant_exists(tenant_id)

        return await call_next(request)


def _ensure_tenant_exists(tenant_id: str):
    """Create a minimal tenant document if it doesn't exist yet."""
    existing = _db_tenants.get(tenant_id)
    if existing:
        return
    import datetime
    _db_tenants.upsert(tenant_id, {
        "is_configured": False,
        "created_at": datetime.datetime.utcnow().isoformat(),
    })
    log_event("tenant_bootstrap", tenant_id=tenant_id)
