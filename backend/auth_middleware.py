"""Firebase Auth Middleware – enforces tenant_id on every non-public route.

Extracts the Bearer token from the Authorization header, verifies it with Firebase Admin,
and sets `request.state.tenant_id` to the user's UID.

Public routes (like webhooks and health) are excluded.
"""

import os
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from firebase_admin import auth
from observability import log_event

_is_production = os.environ.get("ENVIRONMENT", "development").lower() == "production"

# Paths that never require authentication
_PUBLIC_PREFIXES = (
    "/api/webhook",
    "/webhook",
    "/api/health",
)

# Only allow docs access in non-production
if not _is_production:
    _PUBLIC_PREFIXES = _PUBLIC_PREFIXES + ("/docs", "/openapi.json", "/redoc")


class FirebaseAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Public routes whitelist (no auth needed)
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
            return await call_next(request)

        # 2. Extract Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "Missing or invalid Authorization header"}
            )

        token = auth_header.split(" ", 1)[1]

        # 3. Verify Firebase ID token
        try:
            decoded_token = auth.verify_id_token(token)
            tenant_id = decoded_token["uid"]
            request.state.tenant_id = tenant_id
            request.state.user = decoded_token
        except Exception:
            # Never log token or exception details that could leak secrets
            log_event("auth_failure", level="WARN", detail="Token verification failed")
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid or expired token"}
            )

        # 4. Proceed to route handler
        response = await call_next(request)
        return response
