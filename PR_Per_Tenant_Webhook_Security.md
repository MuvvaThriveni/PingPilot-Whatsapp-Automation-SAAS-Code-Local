# Pull Request

## Title

**feat(security): Per-Tenant Webhook HMAC Verification & Fernet Encryption at Rest (Phase 9)**

---

## Description

### Summary

This PR introduces **per-tenant webhook routing with HMAC-SHA256 signature verification** and **Fernet-based encryption at rest** for sensitive credentials stored in the database. The `meta_app_secret` and `webhook_verify_token` have been migrated from global backend environment variables to per-tenant database-backed configuration, enabling full multi-tenant isolation of webhook security credentials.

This constitutes **Phase 9** of the WappFlow architecture evolution.

---

### Motivation

Previously, webhook signature verification relied on a single global `META_APP_SECRET` environment variable shared across all tenants. This posed a fundamental multi-tenancy limitation:

- All tenants shared the same webhook verification secret.
- Compromising one tenant's credentials effectively compromised all tenants.
- Scaling onboarding required manual backend environment changes per tenant.

This PR eliminates these constraints by moving to a **per-tenant secret model** where each tenant stores their own `meta_app_secret` (encrypted at rest) and `webhook_verify_token`, verified at the webhook endpoint level.

---

### Changes

#### Backend

- **`db_layer/encryption.py`** *(new)* — Fernet-based encryption module with `encrypt_secret()` / `decrypt_secret()` helpers. Supports lazy initialization from `ENCRYPTION_KEY` env var, backward-compatible with pre-existing plain-text values, and gracefully degrades when no key is configured.

- **`routers/webhook.py`** — Introduced per-tenant webhook endpoints:
  - `GET /api/webhook/{tenant_id}` — Meta webhook verification (subscribe handshake) with constant-time token comparison via `hmac.compare_digest`.
  - `POST /api/webhook/{tenant_id}` — Inbound webhook handler with full HMAC-SHA256 signature verification against the tenant's decrypted `meta_app_secret`.
  - Existing `POST /api/webhook` route marked as **DEPRECATED** (no signature validation; retained for backward compatibility).
  - Refactored shared processing logic into `_process_webhook_body()` with optional `tenant_id_override`.
  - Added `_verify_per_tenant_signature()` helper using `hmac.new(SHA256)` for `X-Hub-Signature-256` validation.

- **`store.py`** — Extended `save_settings()` to handle `meta_app_secret`:
  - Encrypts plain-text secrets before storage.
  - Preserves already-encrypted values (idempotent re-saves).
  - Includes type validation and explicit logging on secret update.

- **`routers/settings.py`** — Settings response now includes `has_meta_app_secret` boolean flag (never exposes the actual secret to the frontend).

- **`schema.sql`** — Added `meta_app_secret TEXT NOT NULL DEFAULT ''` column to the `tenants` table.

- **`db_layer/tenants.py`** — Tenant upsert and retrieval now includes `meta_app_secret` field.

- **`db_layer/messages.py`** — Minor adjustments for consistency.

- **`auth_middleware.py`** — Added `logger.info` import.

- **`rate_limit.py`** — Minor adjustment.

- **`requirements.txt`** — Added `cryptography` dependency for Fernet encryption.

- **`.env.example`** — Updated to reflect new `ENCRYPTION_KEY` variable; removed global `META_APP_SECRET` and `WEBHOOK_VERIFY_TOKEN` (now per-tenant).

#### Frontend

- **`src/app/dashboard/settings/page.tsx`** — Settings form updated with:
  - **Meta App Secret** input field (password-masked, toggle visibility, same UX pattern as access token).
  - **Per-Tenant Webhook URL** display with one-click copy-to-clipboard, dynamically constructed from `NEXT_PUBLIC_API_BASE_URL` + user's Firebase UID.
  - Appropriate placeholder text and helper copy distinguishing new vs. existing secrets.

- **`src/lib/api.ts`** — Added `meta_app_secret` to the settings payload type definition.

- **`.env.local`** — Added `NEXT_PUBLIC_API_BASE_URL` for dynamic webhook URL construction.

- **`package-lock.json`** — Dependency tree update.

#### Documentation

- **`Architecture_Overview.md`** — Comprehensive additions:
  - Per-tenant webhook architecture documentation.
  - Chatbot decision engine flowchart.
  - Developer onboarding guide (Sections 20.1–20.7): 30-minute mental model, reading order, local setup, key env vars, test flows, common gotchas, and architecture evolution phase history.

- **`API_Developer_Doc.md`** — Updated API documentation to reflect per-tenant webhook endpoints, encryption layer, and new settings fields.

---

### Security Considerations

| Aspect | Implementation |
|--------|---------------|
| **Encryption at rest** | Fernet symmetric encryption (`cryptography` library) with `enc:` prefix convention |
| **Key management** | `ENCRYPTION_KEY` sourced from environment; never logged or exposed |
| **Signature verification** | HMAC-SHA256 against `X-Hub-Signature-256` header per Meta's specification |
| **Token comparison** | Constant-time via `hmac.compare_digest` to prevent timing attacks |
| **Secret exposure** | `meta_app_secret` is never returned to the frontend; only a `has_meta_app_secret` boolean is exposed |
| **Backward compatibility** | `decrypt_secret()` gracefully handles pre-encryption plain-text values |
| **Graceful degradation** | If `ENCRYPTION_KEY` is unset, secrets are stored as plain text with a logged warning |

---

### Database Migration

The following DDL must be applied to existing deployments:

```sql
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS meta_app_secret TEXT NOT NULL DEFAULT '';
```

---

### Environment Variables

| Variable | Location | Status | Purpose |
|----------|----------|--------|---------|
| `ENCRYPTION_KEY` | Backend `.env` | **New (required)** | Fernet key for encrypting secrets at rest |
| `META_APP_SECRET` | Backend `.env` | **Removed** | Migrated to per-tenant DB storage |
| `WEBHOOK_VERIFY_TOKEN` | Backend `.env` | **Removed** | Migrated to per-tenant DB storage |
| `NEXT_PUBLIC_API_BASE_URL` | Frontend `.env.local` | **New** | Backend URL for webhook URL construction |

---

### Breaking Changes

- **Webhook URL format change**: Tenants must update their Meta App Dashboard webhook callback URL from `/api/webhook` to `/api/webhook/{tenant_id}`.
- **Global `META_APP_SECRET`** and **`WEBHOOK_VERIFY_TOKEN`** environment variables are no longer used. Existing tenants must re-enter these values via the Settings UI, where they will be encrypted and stored per-tenant.

> **Note**: The legacy `POST /api/webhook` route is preserved (deprecated) to allow a grace period for migration.

---

### Testing Checklist

- [ ] Verify `ENCRYPTION_KEY` generation and Fernet initialization on server startup
- [ ] Confirm `encrypt_secret()` / `decrypt_secret()` round-trip integrity
- [ ] Validate backward compatibility with pre-existing plain-text secrets in DB
- [ ] Test per-tenant webhook verification handshake (`GET /api/webhook/{tenant_id}`)
- [ ] Test per-tenant HMAC signature validation on inbound webhooks (`POST /api/webhook/{tenant_id}`)
- [ ] Verify 401 response on invalid/missing `X-Hub-Signature-256`
- [ ] Verify 403 response on `webhook_verify_token` mismatch
- [ ] Verify 404 response for non-existent `tenant_id`
- [ ] Confirm `meta_app_secret` is never exposed in API responses
- [ ] Test Settings UI: save, mask, toggle visibility, and copy webhook URL
- [ ] Confirm legacy `POST /api/webhook` still functions (deprecated path)
- [ ] Validate schema migration on fresh and existing databases

---

### Files Changed

```
17 files changed, 931 insertions(+), 144 deletions(-)
```

| File | Change Type |
|------|-------------|
| `backend/db_layer/encryption.py` | **Added** |
| `backend/routers/webhook.py` | Modified |
| `backend/store.py` | Modified |
| `backend/routers/settings.py` | Modified |
| `backend/schema.sql` | Modified |
| `backend/db_layer/tenants.py` | Modified |
| `backend/db_layer/messages.py` | Modified |
| `backend/auth_middleware.py` | Modified |
| `backend/rate_limit.py` | Modified |
| `backend/requirements.txt` | Modified |
| `backend/.env.example` | Modified |
| `frontend/src/app/dashboard/settings/page.tsx` | Modified |
| `frontend/src/lib/api.ts` | Modified |
| `frontend/.env.local` | Modified |
| `frontend/package-lock.json` | Modified |
| `Architecture_Overview.md` | Modified |
| `API_Developer_Doc.md` | Modified |

---

### Related

- **Branch**: `Final-Fix`
- **Base**: `Cron-Job` (`b0296b7`)
- **Commit**: `7684705` — *meta_app_secret and webhook_verify_token moved to Frontend and changed the security layer appropriately for these*
