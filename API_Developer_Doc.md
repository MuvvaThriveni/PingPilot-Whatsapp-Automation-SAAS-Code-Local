# WappFlow — Developer API Documentation

> **Version:** 2.0.0 (Phase 9 — Per-Tenant Webhooks & Encryption at Rest)  
> **Base URL:** `http://localhost:5000/api` (dev) or `https://<your-deployment>/api` (prod)  
> **Auth:** Firebase ID Token — `Authorization: Bearer <firebase_id_token>`

---

## Table of Contents

1. [Authentication](#1-authentication)
2. [Health Check](#2-health-check)
3. [Settings API](#3-settings-api)
4. [Bulk Messaging API](#4-bulk-messaging-api)
5. [File Forwarding API](#5-file-forwarding-api)
6. [Chatbot API](#6-chatbot-api)
7. [Logs API](#7-logs-api)
8. [Webhook API](#8-webhook-api)
9. [Rate Limiting](#9-rate-limiting)
10. [Error Handling](#10-error-handling)
11. [Encryption at Rest](#11-encryption-at-rest)
12. [Environment Variables](#12-environment-variables)
13. [Local Development Setup](#13-local-development-setup)
14. [Data Retention & Archive System](#14-data-retention--archive-system)
15. [Onboarding Quick-Start Guide](#15-onboarding-quick-start-guide)

---

## 1. Authentication

All endpoints (except webhooks and health) require a valid **Firebase Auth ID token** in the `Authorization` header.

```
Authorization: Bearer <firebase_id_token>
```

- The middleware extracts the Firebase UID and sets it as `tenant_id`.
- Every DB operation is scoped to this `tenant_id` — complete multi-tenant isolation.
- **Public routes** (no auth): `/api/webhook`, `/api/health`, `/docs`, `/openapi.json`

### Error Responses

| Status | Body | Meaning |
|--------|------|---------|
| `401` | `{"error": "Missing or invalid Authorization header"}` | No `Bearer` token provided |
| `401` | `{"error": "Invalid or expired token"}` | Firebase token verification failed |

---

## 2. Health Check

### `GET /api/health`

Deep health check — verifies Postgres connectivity and background task liveness. **No auth required.**

**Response `200`:**
```json
{
  "status": "ok",
  "checks": {
    "api": "ok",
    "postgres": "ok",
    "redis": "ok",
    "background_tasks": "3/3 alive",
    "retention": {
      "enabled": true,
      "running": false,
      "last_run": "2026-03-22T13:20:00+00:00",
      "last_duration_ms": 4521.3,
      "last_status": "success"
    },
    "purge": {
      "enabled": false,
      "running": false,
      "last_run": null,
      "last_duration_ms": null,
      "last_deleted_rows": null,
      "last_status": null
    }
  }
}
```

`status` can be `"ok"` or `"degraded"`. The `retention` and `purge` objects show the current state of the background data lifecycle jobs (see [Section 13](#13-data-retention--archive-system)).

---

## 3. Settings API

Prefix: `/api/settings`

### 3.1 Get WhatsApp Settings

`GET /api/settings/whatsapp`

Returns the tenant's WhatsApp Business API configuration. **Secrets (access token, Meta App Secret) are never returned** — only boolean flags indicating whether they are set.

**Response `200`:**
```json
{
  "settings": {
    "business_account_id": "123456789",
    "phone_number_id": "987654321",
    "webhook_verify_token": "my_verify_token",
    "is_configured": true,
    "has_access_token": true,
    "has_meta_app_secret": true
  }
}
```

---

### 3.2 Save WhatsApp Settings

`POST /api/settings/whatsapp`

**Request Body (JSON):**
```json
{
  "business_account_id": "123456789",
  "phone_number_id": "987654321",
  "access_token": "EAAxxxxxxx",
  "webhook_verify_token": "my_verify_token",
  "meta_app_secret": "abc123def456"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `business_account_id` | string | Yes | Alphanumeric + underscores only |
| `phone_number_id` | string | Yes | Alphanumeric + underscores only |
| `access_token` | string | No | Omit to keep existing token. `Bearer ` prefix auto-stripped |
| `webhook_verify_token` | string | No | Token used for Meta webhook verification |
| `meta_app_secret` | string | No | Per-tenant Meta App Secret for webhook signature verification. Encrypted at rest via Fernet (see [Section 11](#11-encryption-at-rest)). Omit to keep existing value |

**Response `200`:**
```json
{
  "message": "Settings saved successfully"
}
```

---

### 3.3 Test WhatsApp Connection

`POST /api/settings/whatsapp/test`

Tests connectivity to the WhatsApp Cloud API using stored credentials.

**Response `200`:**
```json
{
  "success": true,
  "message": "Connection successful",
  "phoneNumber": "+91 98765 43210",
  "verifiedName": "My Business",
  "data": { ... }
}
```

**Error `400`:**
```json
{
  "error": "Invalid credentials (code: 190)"
}
```

---

### 3.4 Get Usage Stats

`GET /api/settings/usage`

Returns message counts for today, this month, and by product type (last 30 days). Counts are sourced from a combination of the live `messages` table (recent data) and the pre-aggregated `daily_message_stats` table (historical data preserved before archival), ensuring numbers remain accurate even after old messages have been archived.

**Response `200`:**
```json
{
  "today": { "total": 42, "successful": 40, "failed": 2 },
  "month": { "total": 1200, "successful": 1150, "failed": 50 },
  "byProduct": [
    { "product_type": "bulk_message", "total": 800 },
    { "product_type": "chatbot", "total": 350 },
    { "product_type": "file_forward", "total": 50 }
  ]
}
```

---

## 4. Bulk Messaging API

Prefix: `/api/bulk-message`

### 4.1 Get Templates

`GET /api/bulk-message/templates`

Fetches **approved** WhatsApp message templates from the Business Account.

**Response `200`:**
```json
{
  "templates": [
    {
      "name": "order_confirmation",
      "language": "en_US",
      "status": "APPROVED",
      "display": "order_confirmation|en_US",
      "param_count": 2,
      "header_format": "IMAGE",
      "requires_header_media": true,
      "has_example_header_media": true
    }
  ]
}
```

---

### 4.2 Parse Contacts File

`POST /api/bulk-message/parse`

Parses an Excel/CSV file to extract and validate phone numbers. Used for preview before starting a campaign.

**Request:** `multipart/form-data`

| Field | Type | Required |
|-------|------|----------|
| `file` | File (xlsx/csv) | Yes |

**Response `200`:**
```json
{
  "contacts": [
    { "index": 0, "phone": "919876543210", "name": "John", "imageUrl": "" }
  ],
  "total": 150,
  "validContacts": 150
}
```

**Error `413`:** File exceeds 16 MB limit.

---

### 4.3 Start Campaign

`POST /api/bulk-message/start`

Creates a new bulk messaging campaign and enqueues it for processing.

**Request:** `multipart/form-data`

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `file` | File (xlsx/csv) | Yes | — | Contact list |
| `templateName` | string | Yes | — | Template `name\|language` or just `name` |
| `campaignName` | string | No | Auto-generated | Human-readable name |
| `delayMs` | int | No | `1000` | Delay between messages (ms) |
| `headerImageUrl` | string | No | `""` | Override image URL for header |
| `scheduledAt` | string (ISO 8601) | No | `null` | Schedule for later; omit for immediate |

**Response `200`:**
```json
{
  "success": true,
  "campaignId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "totalContacts": 150,
  "status": "running"
}
```

If `scheduledAt` is provided, `status` will be `"scheduled"`.

---

### 4.4 Get All Campaigns

`GET /api/bulk-message/campaigns`

Paginated list of all campaigns for the tenant.

**Query Params:**

| Param | Type | Default |
|-------|------|---------|
| `limit` | int | `25` |
| `cursor` | string | `null` |

**Response `200`:**
```json
{
  "campaigns": [
    {
      "campaign_id": "uuid",
      "name": "March Campaign",
      "template_name": "promo|en_US",
      "total_contacts": 500,
      "sent_count": 480,
      "failed_count": 20,
      "status": "completed",
      "created_at": "2026-03-15T10:30:00+05:30",
      "scheduled_at": null
    }
  ],
  "next_cursor": "2026-03-14T..."
}
```

---

### 4.5 Get Campaign Status

`GET /api/bulk-message/status/{campaign_id}`

**Response `200`:**
```json
{
  "campaign": {
    "campaign_id": "uuid",
    "name": "March Campaign",
    "template_name": "promo|en_US",
    "total_contacts": 500,
    "sent_count": 350,
    "failed_count": 10,
    "status": "running",
    "created_at": "...",
    "scheduled_at": null
  }
}
```

**Campaign Statuses:** `scheduled`, `queued`, `running`, `completed`, `stopped`, `interrupted`, `deleted`

---

### 4.6 Get Campaign Details

`GET /api/bulk-message/campaigns/{campaign_id}/details`

Returns full campaign info including all recipients (up to 5000).

**Response `200`:**
```json
{
  "campaign": {
    "campaign_id": "uuid",
    "name": "...",
    "template_name": "...",
    "header_image_url": "",
    "total_contacts": 500,
    "sent_count": 480,
    "failed_count": 20,
    "status": "completed",
    "delay_ms": 1000,
    "created_at": "...",
    "scheduled_at": null
  },
  "recipients": [
    {
      "contact_phone": "919876543210",
      "contact_name": "John",
      "status": "sent",
      "error_message": "",
      "attempt_count": 1,
      "updated_at": "..."
    }
  ]
}
```

---

### 4.7 Stop Campaign

`POST /api/bulk-message/stop/{campaign_id}`

Stops a running campaign. Workers check the DB status and discard further jobs.

**Response `200`:**
```json
{
  "success": true,
  "message": "Campaign stop requested"
}
```

---

### 4.8 Delete Campaign

`DELETE /api/bulk-message/campaigns/{campaign_id}`

Deletes the campaign, its recipients, and counters.

**Response `200`:**
```json
{
  "success": true,
  "message": "Campaign deleted"
}
```

---

### 4.9 Resend Failed Recipients

`POST /api/bulk-message/campaigns/{campaign_id}/resend-failed`

Re-queues all failed recipients in a completed/stopped campaign.

**Response `200`:**
```json
{
  "success": true,
  "resend_count": 15,
  "message": "Re-queued 15 failed recipients"
}
```

**Error `400`:** Campaign is still running, or no failed recipients exist.

---

## 5. File Forwarding API

Prefix: `/api/file-forward`

### 5.1 Parse Contacts

`POST /api/file-forward/parse-contacts`

Parses an Excel/CSV to extract phone numbers for bulk file forwarding.

**Request:** `multipart/form-data`

| Field | Type | Required |
|-------|------|----------|
| `contactsFile` | File (xlsx/csv) | Yes |

**Response `200`:**
```json
{
  "contacts": [
    { "index": 0, "phone": "919876543210", "name": "John" }
  ],
  "total": 50
}
```

---

### 5.2 Send Single File

`POST /api/file-forward/send`

Sends a file (image/document) to a single recipient.

**Request:** `multipart/form-data`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `file` | File | Yes | Max 16 MB |
| `recipient` | string | Yes | Phone number (e.g., `919876543210`) |
| `message` | string | No | Caption text |

**Response `200`:**
```json
{
  "success": true,
  "message": "File sent successfully",
  "messageId": "wamid.xxxx"
}
```

---

### 5.3 Send Bulk File

`POST /api/file-forward/send-bulk`

Uploads a file once, then enqueues individual send jobs to all contacts.

**Request:** `multipart/form-data`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `file` | File | Yes | The file to send (max 16 MB) |
| `contactsFile` | File (xlsx/csv) | Yes | Recipient list |
| `message` | string | No | Caption text |

**Response `200`:**
```json
{
  "success": true,
  "message": "Queued 48 of 50 recipients for delivery",
  "queued_count": 48,
  "total": 50
}
```

Delivery is **asynchronous** — returns immediately after enqueuing.

---

## 6. Chatbot API

Prefix: `/api/chatbot`

### 6.1 Get Chatbot Settings

`GET /api/chatbot/settings`

**Response `200`:**
```json
{
  "settings": {
    "is_enabled": true,
    "fallback_message": "Thank you for your message. Our team will get back to you soon.",
    "use_ai": false
  }
}
```

---

### 6.2 Update Chatbot Settings

`PUT /api/chatbot/settings`

**Request Body (JSON):**
```json
{
  "is_enabled": true,
  "fallback_message": "We'll get back to you shortly."
}
```

**Response `200`:**
```json
{
  "success": true,
  "message": "Settings updated"
}
```

---

### 6.3 Get Chatbot Rules

`GET /api/chatbot/rules`

**Response `200`:**
```json
{
  "rules": [
    {
      "id": 1,
      "keyword": "hello",
      "response": "Hi there! How can I help?",
      "priority": 0,
      "is_active": true,
      "created_at": "..."
    }
  ]
}
```

---

### 6.4 Create Rule

`POST /api/chatbot/rules`

**Request Body (JSON):**
```json
{
  "keyword": "pricing",
  "response": "Check out our pricing at https://example.com/pricing",
  "priority": 1
}
```

| Field | Type | Required |
|-------|------|----------|
| `keyword` | string | Yes |
| `response` | string | Yes |
| `priority` | int | No (default `0`) |

**Response `200`:**
```json
{
  "rule": {
    "id": 2,
    "keyword": "pricing",
    "response": "Check out our pricing at ...",
    "priority": 1,
    "is_active": 1,
    "created_at": "..."
  }
}
```

---

### 6.5 Update Rule

`PUT /api/chatbot/rules/{rule_id}`

**Request Body (JSON):**
```json
{
  "keyword": "pricing",
  "response": "Updated response text",
  "priority": 2,
  "is_active": true
}
```

---

### 6.6 Delete Rule

`DELETE /api/chatbot/rules/{rule_id}`

**Response `200`:**
```json
{
  "success": true,
  "message": "Rule deleted"
}
```

---

### 6.7 Get Chat Users

`GET /api/chatbot/users`

Returns unique contacts with their latest message. Cached for 15 seconds.

**Response `200`:**
```json
{
  "users": [
    {
      "phone": "919876543210",
      "name": "John Doe",
      "last_message": "Hello",
      "last_message_at": "2026-03-18T10:00:00+05:30",
      "direction": "incoming"
    }
  ]
}
```

---

### 6.8 Get Conversations (All)

`GET /api/chatbot/conversations`

**Query Params:** `limit` (default 50), `cursor`

**Response `200`:**
```json
{
  "conversations": [
    {
      "sender_phone": "919876543210",
      "sender_name": "John",
      "message_text": "Hello",
      "direction": "incoming",
      "created_at": "..."
    }
  ],
  "next_cursor": "..."
}
```

---

### 6.9 Get User Conversations

`GET /api/chatbot/conversations/{phone}`

**Query Params:** `limit` (default 50), `cursor`

Returns conversation history for a specific phone number. Same response shape as 6.8.

---

## 7. Logs API

Prefix: `/api/logs`

### 7.1 Get Message Logs

`GET /api/logs`

**Query Params:**

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `product_type` | string | `null` | Filter: `bulk_message`, `chatbot`, `file_forward` |
| `status` | string | `null` | Filter: `sent`, `delivered`, `failed`, etc. |
| `limit` | int | `25` | Max records per page |
| `cursor` | string | `null` | Pagination cursor |

**Response `200`:**
```json
{
  "logs": [
    {
      "product_type": "bulk_message",
      "recipient": "919876543210",
      "message_id": "wamid.xxxx",
      "template_name": "promo|en_US",
      "status": "delivered",
      "error_message": "",
      "campaign_id": "uuid",
      "created_at": "..."
    }
  ],
  "total": 25,
  "limit": 25,
  "next_cursor": "..."
}
```

---

### 7.2 Export Logs (CSV)

`GET /api/logs/export`

**Query Params:** Same as `GET /api/logs` (without pagination). Exports up to 5000 records.

**Response:** `text/csv` file download (`message_logs.csv`). CSV values are sanitized against formula injection.

---

### 7.3 Get Log Stats

`GET /api/logs/stats`

**Response `200`:**
```json
{
  "stats": {
    "total": 1200,
    "sent": 1000,
    "delivered": 950,
    "failed": 50,
    "read": 700
  },
  "dailyStats": []
}
```

---

## 8. Webhook API

Prefix: `/api/webhook` — **No Firebase authentication required** (called by Meta servers).

WappFlow supports two webhook modes: **per-tenant** (recommended, secure) and **legacy** (deprecated). New deployments should always use per-tenant routes.

### 8.1 Per-Tenant Webhook Verification (Recommended)

`GET /api/webhook/{tenant_id}`

Called by Meta during webhook setup. Verifies the token matches the tenant's stored `webhook_verify_token` from the database.

**Query Params (set by Meta):**
- `hub.mode` = `subscribe`
- `hub.verify_token` = your verification token
- `hub.challenge` = challenge string

**Response:** Returns `hub.challenge` as plain text on success, `403` on failure, `404` if tenant not found.

---

### 8.2 Per-Tenant Incoming Webhook (Recommended)

`POST /api/webhook/{tenant_id}`

Receives incoming messages and delivery status updates from WhatsApp. This is the **secure** endpoint with full signature verification.

**Security flow (in order):**
1. Read raw body **before** JSON parsing
2. Look up tenant from the URL path `{tenant_id}`
3. Decrypt the tenant's `meta_app_secret` (stored encrypted via Fernet — see [Section 11](#11-encryption-at-rest))
4. Verify `X-Hub-Signature-256` header using HMAC-SHA256 with the decrypted secret (constant-time comparison)
5. Only then parse JSON and process the payload

**If signature verification fails:** Returns `401 Invalid signature` — the payload is never processed.

**Processing pipeline:**
- **Deduplication:** Uses `webhook_events` table to prevent duplicate processing
- **Message routing:** Incoming text/button/interactive messages matched against:
  1. Per-tenant button→template mappings (configurable in DB, cached 1h)
  2. Keyword-based chatbot rules (DB-backed)
  3. First-trigger fallback (24h rate-limited per sender)
- **Delivery status handling:** Updates message status in `messages` table, triggers campaign finalization, handles retries for failed deliveries with automatic re-enqueue
- **Archive fallback:** If a message was recently archived, the webhook handler looks up `messages_archive` so delivery callbacks still work

**Response `200`:**
```json
{
  "status": "ok",
  "tenant_id": "firebase_uid_here"
}
```

**Error `401`:**
```json
{
  "error": "Invalid signature"
}
```

---

### 8.3 Legacy Webhook Routes (Deprecated)

> ⚠️ **Deprecated** — These routes will be removed in a future release. Migrate to `/api/webhook/{tenant_id}`.

`GET /api/webhook` — Legacy verification. Checks token against `WEBHOOK_VERIFY_TOKEN` env var or any tenant's stored token.

`POST /api/webhook` — Legacy incoming webhook. **No signature verification.** Tenant is resolved from the `phone_number_id` in the payload metadata (requires a lookup against all tenants).

**Response `200`:**
```json
{
  "status": "ok"
}
```

### 8.4 Webhook Setup Guide (for new tenants)

1. In the WappFlow Settings page, save your `webhook_verify_token` and `meta_app_secret` (from Meta App Dashboard)
2. In Meta App Dashboard → Webhooks, set the callback URL to:
   ```
   https://<your-deployment>/api/webhook/<your_tenant_id>
   ```
3. Set the Verify Token to the same value you saved in step 1
4. Meta will call `GET /api/webhook/{tenant_id}?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...`
5. WappFlow verifies the token and returns the challenge — webhook is now active
6. All incoming messages and delivery updates will be POSTed to `POST /api/webhook/{tenant_id}` with `X-Hub-Signature-256` verification

---

## 9. Rate Limiting

### API Rate Limits (Redis-backed sliding window)

Rate limits are applied **per tenant** and are based on both the **HTTP method** and the **path**. Only user-initiated write actions (campaign start, file send, etc.) hit the strict "heavy" tier. Read-only/polling endpoints use the generous general tier, so frontend polling will not trigger 429s.

**Heavy tier (10 req/min per tenant) — write actions only:**

| Method | Path Prefix | Action |
|--------|-------------|--------|
| `POST` | `/api/bulk-message/start` | Start campaign |
| `POST` | `/api/bulk-message/parse` | Parse contacts file |
| `POST` | `/api/bulk-message/stop/` | Stop campaign |
| `POST` | `/api/bulk-message/campaigns/` | Resend failed |
| `DELETE` | `/api/bulk-message/campaigns/` | Delete campaign |
| `POST` | `/api/file-forward/send` | Send single file |
| `POST` | `/api/file-forward/send-bulk` | Send bulk file |
| `POST` | `/api/file-forward/parse-contacts` | Parse contacts |
| `POST` | `/api/settings/whatsapp` | Save settings |
| `POST` | `/api/settings/whatsapp/test` | Test connection |

**General tier (300 req/min per tenant) — everything else:**

All `GET` requests (campaign status, details, logs, templates, conversations, etc.) and any authenticated endpoint not listed above.

**No limit:**

`/api/webhook`, `/api/health`, `/docs`, `/openapi.json`

**Rate limit response `429`:**
```json
{
  "error": "rate_limited",
  "retry_after_seconds": 12
}
```

Includes `Retry-After` header.

### Worker-Side Rate Limiting

- **Tenant token bucket:** 10 msg/sec, burst capacity of 20 (configurable via `TENANT_RATE_LIMIT` / `TENANT_BURST`).
- **Global cooldown:** If WhatsApp returns repeated 429s, all workers pause for `WA_COOLDOWN_TTL` seconds (default 5).

---

## 10. Error Handling

All errors follow a consistent structure:

```json
{
  "error": "Human-readable error message"
}
```

| Status | Meaning |
|--------|---------|
| `400` | Bad request / validation error |
| `401` | Authentication failed |
| `404` | Resource not found (also used to avoid leaking existence) |
| `413` | File too large (>16 MB) |
| `429` | Rate limited |

---

## 11. Encryption at Rest

WappFlow encrypts sensitive secrets (e.g., `meta_app_secret`) before storing them in Postgres using **Fernet symmetric encryption** (AES-128-CBC via the `cryptography` Python library).

### 11.1 How It Works

```
Tenant saves meta_app_secret via POST /api/settings/whatsapp
  │
  ▼
store.py → encrypt_secret(plain_text)
  │
  ├── ENCRYPTION_KEY set? → Fernet.encrypt() → stored as "enc:<ciphertext>"
  └── ENCRYPTION_KEY not set? → stored as plain text (backward compat)

Webhook receives POST /api/webhook/{tenant_id}
  │
  ▼
webhook.py → decrypt_secret(stored_value)
  │
  ├── Starts with "enc:" → Fernet.decrypt() → plain text for HMAC verification
  ├── No prefix → returned as-is (pre-encryption migration data)
  └── Empty → returned empty
```

### 11.2 Key Files

| File | Role |
|------|------|
| `db_layer/encryption.py` | `encrypt_secret()` / `decrypt_secret()` — Fernet wrapper with backward compatibility |
| `db_layer/secrets.py` | `secrets.resolve_wa_token()` — runtime token resolution (DB → env fallback) |
| `store.py` | Calls `encrypt_secret()` when saving `meta_app_secret` |
| `routers/webhook.py` | Calls `decrypt_secret()` to verify webhook signatures |

### 11.3 Generating an Encryption Key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the output as `ENCRYPTION_KEY` in your `.env` file. **Keep this key safe** — if lost, encrypted secrets cannot be decrypted.

### 11.4 Backward Compatibility

- Values stored **before** encryption was enabled (plain text) are returned as-is by `decrypt_secret()` — no migration required.
- If `ENCRYPTION_KEY` is not set, `encrypt_secret()` stores values in plain text and logs a warning.
- The `"enc:"` prefix distinguishes encrypted values from plain text.

---

## 12. Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Postgres connection string (Neon DB) |
| `WEBHOOK_VERIFY_TOKEN` | Strong secret for Meta webhook verification (legacy routes; per-tenant tokens are preferred) |
| `REDIS_HOST` | Redis hostname (default: `localhost`). Only used if `REDIS_URL` is not set |
| `REDIS_PORT` | Redis port (default: `6379`). Only used if `REDIS_URL` is not set |
| `ENCRYPTION_KEY` | Fernet encryption key for secrets at rest. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

### Required Files

| File | Description |
|------|-------------|
| `backend/firebase-service-account.json` | Firebase Admin SDK service account key |

### Optional — General

| Variable | Default | Description |
|----------|---------|-------------|
| `CORS_ORIGINS` | Hardcoded list | Comma-separated allowed origins |
| `ENVIRONMENT` | `development` | Set to `production` to disable docs and restrict CORS |
| `META_APP_SECRET` | (empty) | WhatsApp webhook signature verification |
| `HOST` | `127.0.0.1` | Server bind address |
| `PORT` | `5000` | Server bind port |

### Optional — Postgres

| Variable | Default | Description |
|----------|---------|-------------|
| `PG_POOL_MIN` | `1` | Min Postgres connection pool size |
| `PG_POOL_MAX` | `10` | Max Postgres connection pool size |
| `PG_STATEMENT_TIMEOUT_MS` | `30000` | Statement timeout (ms) |
| `PG_TIMEZONE` | `Asia/Kolkata` | DB session timezone |
| `PG_CONNECT_RETRIES` | `8` | Connection retry attempts on startup |
| `PG_CONNECT_RETRY_DELAY_S` | `0.5` | Delay between connection retries (seconds) |
| `PG_COMMAND_TIMEOUT` | `30` | Pool-level command timeout (seconds) |

### Optional — Redis & Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | (empty) | Full Redis URL (overrides REDIS_HOST/PORT if set). **Use `rediss://` (double `s`) for TLS** — required by cloud providers like Upstash. Example: `rediss://default:password@host:6379` |
| `TENANT_RATE_LIMIT` | `10` | Worker messages per second per tenant |
| `TENANT_BURST` | `20` | Worker max burst capacity |
| `WA_COOLDOWN_TTL` | `5` | Global 429 cooldown duration (seconds) |
| `QUEUE_RATE_LIMIT` | `80` | BullMQ message worker rate limit |
| `QUEUE_RETRY_ATTEMPTS` | `3` | Max retry attempts for message jobs |
| `DELIVERY_CONFIRM_TIMEOUT_SECONDS` | `900` | Requeue if no delivery confirmation |

### Optional — Data Retention (Archive)

| Variable | Default | Description |
|----------|---------|-------------|
| `RETENTION_ENABLED` | `false` | Enable automated background archiving (`true`/`false`) |
| `RETENTION_INTERVAL_HOURS` | `24` | Hours between automated archive runs |
| `RETENTION_TIMEOUT_HOURS` | `1` | Max hours per archive run before forced timeout |
| `RETENTION_DAYS` | `2` | Archive rows older than N days from live tables |
| `RETENTION_BATCH_SIZE` | `1000` | Rows per archive batch |
| `RETENTION_MAX_BATCHES` | `100` | Max batches per table per archive run |
| `RETENTION_BATCH_SLEEP` | `0.05` | Seconds between archive batches (backpressure) |
| `RETENTION_STATEMENT_TIMEOUT_MS` | `120000` | SQL statement timeout for archive queries (ms) |

### Optional — Data Retention (Purge)

| Variable | Default | Description |
|----------|---------|-------------|
| `PURGE_ENABLED` | `false` | Enable automated archive purge (`true`/`false`) |
| `PURGE_RETENTION_DAYS` | `90` | Delete archive rows older than N days |
| `PURGE_BATCH_SIZE` | `1000` | Rows per purge batch |
| `PURGE_MAX_BATCHES` | `50` | Max batches per table per purge run |
| `PURGE_BATCH_SLEEP` | `0.05` | Seconds between purge batches |

---

## 13. Local Development Setup

### Prerequisites

- Python 3.11+
- Node.js 18+
- Redis 6+ (local Docker) or a managed Redis with TLS (e.g. Upstash — use `rediss://` URL scheme)
- Postgres (or Neon DB connection string)
- Firebase project with Auth enabled

### Backend

```bash
cd backend
pip install -r requirements.txt
# Copy .env and fill in values
cp .env.example .env
# Apply DB schema
python apply_schema.py
# Start API server
python run_server.py
# In a separate terminal — start the BullMQ worker
python worker_main.py
```

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
# Set NEXT_PUBLIC_API_URL=http://localhost:5000
npm run dev
```

### Docker (Redis + full stack)

```bash
docker-compose up
```

This starts Redis, the API server (port 5000), and the worker.

### Database Schema

Apply via:
```bash
python backend/apply_schema.py
```

Or manually run `backend/schema.sql` against your Postgres database.

### Key Tables

| Table | Purpose |
|-------|---------|
| `tenants` | WhatsApp API credentials per tenant |
| `chatbot_config` | Chatbot on/off, fallback message |
| `chatbot_rules` | Keyword → response auto-reply rules |
| `campaigns` | Bulk message campaign metadata |
| `campaign_recipients` | Per-recipient delivery status |
| `campaign_counters` | Sharded sent/failed counters |
| `messages` | Unified message log (all products) |
| `chat_messages` | Conversation history (incoming + outgoing) |
| `webhook_events` | Deduplication for incoming webhooks |
| `usage_events` | Billable usage tracking |
| `template_cache` | Persistent WhatsApp template metadata |
| `user_triggers` | 24-hour rate limit for first-trigger messages |
| `messages_archive` | Archived messages (older than RETENTION_DAYS) |
| `chat_messages_archive` | Archived chat messages |
| `webhook_events_archive` | Archived webhook events |
| `usage_events_archive` | Archived usage events |
| `daily_message_stats` | Pre-aggregated daily message counts (preserves dashboard accuracy after archival) |

---

## 14. Data Retention & Archive System

WappFlow includes a **4-phase data lifecycle management system** that keeps live tables small and fast while preserving historical data for compliance and analytics. This section explains the full system so a new developer can understand, operate, and debug it.

### 14.1 Overview — Why This Exists

The four transient tables (`messages`, `chat_messages`, `webhook_events`, `usage_events`) grow continuously as users send messages. Without management, these tables would grow unbounded, slowing down queries, increasing index size, and inflating backup costs. The retention system solves this by:

1. **Archiving** old rows from live tables into `*_archive` tables (fast live queries)
2. **Purging** very old archive rows after a configurable retention period (controlled storage costs)
3. **Pre-aggregating** message stats so dashboard analytics remain accurate after archival

### 14.2 Architecture Diagram

```
 Live Tables                    Archive Tables              Deleted
 ┌──────────┐    archive (2d)   ┌──────────────────┐  purge (90d)  
 │ messages │ ──────────────→  │ messages_archive  │ ──────────→  Gone
 │ chat_msg │ ──────────────→  │ chat_msg_archive  │ ──────────→  Gone
 │ webhook  │ ──────────────→  │ webhook_archive   │ ──────────→  Gone
 │ usage    │ ──────────────→  │ usage_archive     │ ──────────→  Gone
 └──────────┘                   └──────────────────┘
       │
       │  pre-aggregate (before archive)
       ▼
 ┌─────────────────────┐
 │ daily_message_stats │  (permanent — powers dashboard)
 └─────────────────────┘
```

### 14.3 The Four Phases

| Phase | What | File | Status |
|-------|------|------|--------|
| **Phase 1** | Schema creation — archive tables + safety indexes + webhook fallback | `retention_schema.sql`, `schema.sql`, `db_layer/messages.py`, `routers/webhook.py` | Complete |
| **Phase 2** | Archive engine — batched data movement from live → archive | `retention.py` | Complete |
| **Phase 3** | Controlled automation — background cron + health monitoring | `main.py` | Complete |
| **Phase 4** | Archive cleanup — batched purge of old archive rows | `retention.py`, `main.py` | Complete |

### 14.4 How Archiving Works (Phase 2)

The `archive_old_data()` function in `retention.py` processes each table in batches:

```
For each table (messages, chat_messages, usage_events, webhook_events):
  Loop (max 100 batches):
    BEGIN TRANSACTION
      1. SELECT id FROM {table} WHERE created_at < cutoff
         ORDER BY id ASC LIMIT 1000
         FOR UPDATE SKIP LOCKED
      2. INSERT INTO {archive} (..., archived_at)
         SELECT ..., now() FROM {table} WHERE id = ANY($1)
         ON CONFLICT DO NOTHING
      3. DELETE FROM {table} WHERE id = ANY($1)
    COMMIT
    sleep 50ms
```

**Key safety properties:**

- **Batched**: Max 1000 rows per transaction — no long locks
- **Transactional**: All 3 steps atomic — crash = full rollback, zero data loss
- **Idempotent**: `ON CONFLICT DO NOTHING` — safe to re-run at any time
- **Concurrency-safe**: `FOR UPDATE SKIP LOCKED` — skips rows locked by live webhooks/workers
- **Backpressure**: 50ms sleep between batches yields control to the event loop

**Special handling:**

- `messages` table: `daily_message_stats` is pre-aggregated BEFORE any messages are deleted, using `GREATEST()` in `ON CONFLICT` so counts never decrease on re-runs
- `webhook_events`: Uses composite PK `(tenant_id, event_id)` instead of `id` — handled by a separate function with `unnest()` array matching
- Webhook status updates for archived messages: A fallback lookup in `messages_archive` was added to `routers/webhook.py` (Phase 1) so delivery callbacks for recently-archived messages still work

### 14.5 How Purging Works (Phase 4)

The `purge_old_archives()` function deletes old rows from archive tables using the same batched pattern:

```
For each archive table:
  Loop (max 50 batches):
    BEGIN TRANSACTION
      1. SELECT id FROM {archive} WHERE archived_at < cutoff
         ORDER BY id ASC LIMIT 1000
         FOR UPDATE SKIP LOCKED
      2. DELETE FROM {archive} WHERE id = ANY($1)
    COMMIT
    sleep 50ms
```

- Only targets `*_archive` tables — **never touches live tables**
- Uses `archived_at` (not `created_at`) as the cutoff — only deletes data that has been in the archive for the full retention period
- Disabled by default (`PURGE_ENABLED=false`)

### 14.6 Background Automation (Phase 3)

The `periodic_archive_runner()` in `main.py` runs as a background `asyncio.Task`:

```
Startup → 60s warmup delay → then every RETENTION_INTERVAL_HOURS:
  1. If RETENTION_ENABLED=false → log "retention_skipped", sleep, loop
  2. Acquire asyncio.Lock (prevent overlap)
  3. await asyncio.wait_for(archive_old_data(), timeout=1h)
  4. If PURGE_ENABLED=true:
       await asyncio.wait_for(purge_old_archives(), timeout=1h)
  5. Update health state dict → visible via GET /api/health
  6. Sleep → loop
```

**Safety features:**

| Feature | Mechanism |
|---------|----------|
| No overlap | `asyncio.Lock` — second cycle skips if first is still running |
| Timeout | `asyncio.wait_for()` — kills runaway jobs after 1h (configurable) |
| No crash | `except Exception` catches all errors, logs them, continues loop |
| Graceful shutdown | `CancelledError` re-raised — lifespan cancels the task cleanly |
| No startup blocking | 60s initial warmup delay — app fully serves requests first |
| Kill switch | `RETENTION_ENABLED=false` / `PURGE_ENABLED=false` — checked every cycle |

### 14.7 Manual CLI Usage

For one-off runs or debugging, the retention system can be triggered manually:

```bash
cd backend

# Archive only (move old rows to archive tables)
python retention.py

# Archive + Purge (archive then delete old archive rows)
python retention.py --purge

# Purge only (delete old archive rows without archiving)
python retention.py --purge-only

# Small test (10 rows, 1 batch — safe for production verification)
RETENTION_BATCH_SIZE=10 RETENTION_MAX_BATCHES=1 python retention.py

# Small purge test
PURGE_BATCH_SIZE=10 PURGE_MAX_BATCHES=1 python retention.py --purge-only
```

### 14.8 Monitoring & Log Events

All retention and purge operations emit structured JSON logs via `observability.log_event()`:

**Archive events:**

| Event | When |
|-------|------|
| `retention_start` | Archive run begins (includes cutoff timestamp, config) |
| `retention_aggregate` | `daily_message_stats` pre-aggregation complete |
| `retention_batch` | Each batch completes (includes table, batch#, rows, total, duration) |
| `retention_complete` | Archive run finished (includes full summary) |
| `retention_cron_started` | Background automation cycle begins |
| `retention_cron_completed` | Background automation cycle finished |
| `retention_cron_failed` | Background automation cycle errored/timed out |
| `retention_skipped` | Automation disabled or overlapping run |

**Purge events:**

| Event | When |
|-------|------|
| `purge_started` | Purge run begins (includes cutoff, config) |
| `purge_batch` | Each purge batch completes (table, batch#, rows, total, duration) |
| `purge_completed` | Purge run finished (includes full summary) |
| `purge_failed` | Purge run errored |
| `purge_cron_started` | Background purge cycle begins |
| `purge_cron_completed` | Background purge cycle finished |
| `purge_cron_failed` | Background purge cycle errored/timed out |
| `purge_skipped` | Purge disabled |

### 14.9 Verification Queries

**Check what would be archived (before running):**
```sql
SELECT 'messages' AS tbl, COUNT(*) FROM messages WHERE created_at < now() - interval '2 days'
UNION ALL
SELECT 'chat_messages', COUNT(*) FROM chat_messages WHERE created_at < now() - interval '2 days'
UNION ALL
SELECT 'usage_events', COUNT(*) FROM usage_events WHERE created_at < now() - interval '2 days'
UNION ALL
SELECT 'webhook_events', COUNT(*) FROM webhook_events WHERE created_at < now() - interval '2 days';
```

**Check archive table sizes:**
```sql
SELECT 'messages_archive' AS tbl, COUNT(*) FROM messages_archive
UNION ALL
SELECT 'chat_messages_archive', COUNT(*) FROM chat_messages_archive
UNION ALL
SELECT 'usage_events_archive', COUNT(*) FROM usage_events_archive
UNION ALL
SELECT 'webhook_events_archive', COUNT(*) FROM webhook_events_archive;
```

**Verify no data loss (live + archive = original total):**
```sql
SELECT
  (SELECT COUNT(*) FROM messages) AS live,
  (SELECT COUNT(*) FROM messages_archive) AS archived,
  (SELECT COUNT(*) FROM messages) + (SELECT COUNT(*) FROM messages_archive) AS combined;
```

**Check daily_message_stats populated:**
```sql
SELECT tenant_id, stat_date, SUM(message_count) AS total
FROM daily_message_stats
GROUP BY tenant_id, stat_date
ORDER BY stat_date DESC
LIMIT 10;
```

**Confirm live tables untouched after purge:**
```sql
SELECT 'messages' AS tbl, COUNT(*) FROM messages
UNION ALL
SELECT 'chat_messages', COUNT(*) FROM chat_messages
UNION ALL
SELECT 'usage_events', COUNT(*) FROM usage_events
UNION ALL
SELECT 'webhook_events', COUNT(*) FROM webhook_events;
```

---

## 15. Onboarding Quick-Start Guide

This section is for **new developers joining the team**. It walks you through the entire system so you can understand, run, and contribute to WappFlow within your first day.

### 15.1 What Does WappFlow Do?

WappFlow is a **multi-tenant WhatsApp automation SaaS**. Each user (tenant) connects their own WhatsApp Business Account and can:

1. **Bulk Messaging** — Upload a CSV of contacts, pick a pre-approved WhatsApp template, and send thousands of personalized messages. Campaigns can be scheduled, paused, and retried.
2. **File Forwarding** — Send documents/images to one or many recipients via the WhatsApp Cloud API.
3. **Auto-Reply Chatbot** — Configure keyword-based rules that automatically reply to incoming WhatsApp messages. Supports interactive button flows and a 24h rate-limited first-trigger fallback.

### 15.2 How the Pieces Fit Together

```
Browser (Next.js)
  │  Firebase Auth login → gets ID token
  │  Every API call includes: Authorization: Bearer <token>
  ▼
FastAPI Backend (Python)
  │  Middleware stack: CORS → Firebase Auth → Rate Limit
  │  Routes: /settings, /bulk-message, /file-forward, /chatbot, /logs, /webhook
  │  Background tasks: TTL cleanup, campaign scheduler, retention cron
  ▼
Redis                          Neon Postgres
  │  BullMQ queues               │  All tenant data, messages, campaigns
  │  Rate limit counters         │  Archive tables + daily_message_stats
  ▼                              │
BullMQ Worker (worker_main.py)  │
  │  Picks jobs from queues      │
  │  Sends via WhatsApp API  ────┘ (updates DB with results)
  ▼
WhatsApp Cloud API (Meta)
  │  Sends messages to end users
  │  Sends webhooks back to our server
  ▼
POST /api/webhook/{tenant_id}
  │  Signature verified → message processed → chatbot reply enqueued
```

### 15.3 Key Concepts to Understand

| Concept | Explanation |
|---------|-------------|
| **Tenant** | A single user identified by their Firebase Auth UID. All data is isolated per tenant via `tenant_id` columns. |
| **Campaign** | A bulk message job. Contains a template, a list of recipients, and counters tracking progress. |
| **Recipient status machine** | `pending → queued → processing → submitted → sent` (via webhook). Failed recipients can be retried. |
| **Template** | A pre-approved WhatsApp message format (created in Meta Business Manager). WappFlow caches template metadata locally. |
| **BullMQ** | A Redis-backed job queue. `campaign_queue` fans out recipients; `message_queue` sends individual messages with rate limiting. |
| **Per-tenant webhook** | Each tenant gets their own webhook URL (`/api/webhook/{tenant_id}`) with HMAC signature verification using their encrypted `meta_app_secret`. |
| **Encryption at rest** | Sensitive values like `meta_app_secret` are Fernet-encrypted before storage. See [Section 11](#11-encryption-at-rest). |
| **Data retention** | Old rows are archived from live tables → `*_archive` tables, keeping queries fast. See [Section 14](#14-data-retention--archive-system). |

### 15.4 Your First Local Setup

```bash
# 1. Clone the repo
git clone <repo-url> && cd SaaS-Product-

# 2. Start Redis (required for queues + rate limiting)
docker-compose up redis -d

# 3. Backend setup
cd backend
pip install -r requirements.txt
cp .env.example .env
# Fill in: DATABASE_URL, WEBHOOK_VERIFY_TOKEN, ENCRYPTION_KEY
# Place firebase-service-account.json in backend/

# 4. Apply database schema
python apply_schema.py

# 5. Start the API server (Terminal 1)
python run_server.py

# 6. Start the BullMQ worker (Terminal 2)
cd backend && python worker_main.py

# 7. Frontend setup (Terminal 3)
cd frontend
npm install
cp .env.local.example .env.local
# Set NEXT_PUBLIC_API_URL=http://localhost:5000
npm run dev
```

Open `http://localhost:3000` → register/login → configure WhatsApp settings → you're ready to test.

### 15.5 Important Files to Read First

As a new developer, read these files in order to build a mental model:

| Order | File | Why |
|-------|------|-----|
| 1 | `schema.sql` + `retention_schema.sql` | Understand the data model — every table, column, and relationship |
| 2 | `main.py` | See how the app starts, middleware stack, background tasks, health check |
| 3 | `auth_middleware.py` | Understand how every request gets a `tenant_id` |
| 4 | `store.py` | The cached read/write layer — how settings and chatbot config are loaded |
| 5 | `routers/bulk_message.py` | The most complex product — campaign lifecycle from start to completion |
| 6 | `worker_main.py` | How jobs are picked up, rate-limited, and sent via WhatsApp API |
| 7 | `routers/webhook.py` | How incoming WhatsApp messages flow through the system |
| 8 | `services/queue_manager.py` | How jobs are enqueued (campaign, message, file-forward, dead-letter) |
| 9 | `rate_limit.py` | API rate limiting (middleware) + worker token bucket (Lua script) |
| 10 | `retention.py` | Data lifecycle — archiving + purging |

### 15.6 Common Development Tasks

**Add a new API endpoint:**
1. Create or edit a file in `routers/`
2. Add the DB query in `db_layer/`
3. Register the router in `main.py` (if new file)
4. Update `frontend/src/lib/api.ts` with the new API call

**Add a new database table:**
1. Add the `CREATE TABLE` to `schema.sql`
2. Run `python apply_schema.py`
3. Create a new file in `db_layer/` for the CRUD operations

**Debug a campaign that's stuck:**
1. Check campaign status: `GET /api/bulk-message/status/{id}`
2. Check recipient details: `GET /api/bulk-message/campaigns/{id}/details`
3. Verify the worker is running (`python worker_main.py`)
4. Check logs for `worker_send_prepare`, `worker_finalize_sent`, or `worker_finalize_error`

**Test webhooks locally:**
1. Use `ngrok http 5000` to get a public URL
2. Set the webhook URL in Meta App Dashboard to `https://<ngrok-url>/api/webhook/{tenant_id}`
3. Send a message to your WhatsApp Business number — watch the logs
