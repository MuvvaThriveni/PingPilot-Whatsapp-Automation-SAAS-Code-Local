"""Firebase Auth Middleware – enforces tenant_id on every non-public route.

Extracts the Bearer token from the Authorization header, verifies it with Firebase Admin,
and sets `request.state.tenant_id` to the user's UID.

Public routes (like webhooks) are excluded.
"""

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from firebase_admin import auth
from firebase_config import get_db

class FirebaseAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Public routes whitelist (no auth needed)
        # Webhooks must be public as they are called by Meta/WhatsApp
        if request.url.path.startswith("/api/webhook") or \
           request.url.path.startswith("/api/health") or \
           request.url.path.startswith("/docs") or \
           request.url.path.startswith("/openapi.json"):
            return await call_next(request)

        # 2. Extract Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "Missing or invalid Authorization header"}
            )

        token = auth_header.split(" ")[1]

        # 3. Verify Firebase ID token
        try:
            decoded_token = auth.verify_id_token(token)
            tenant_id = decoded_token["uid"]
            request.state.tenant_id = tenant_id
            request.state.user = decoded_token
        except Exception as e:
            print(f"[AUTH] Token verification failed: {e}")
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid or expired token"}
            )

        # 4. Proceed to route handler
        response = await call_next(request)
        return response
