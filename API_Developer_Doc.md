# WappFlow — Developer API Documentation

> **Version:** 1.1.0  
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
11. [Environment Variables](#11-environment-variables)
12. [Local Development Setup](#12-local-development-setup)

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
    "background_tasks": "2/2 alive"
  }
}
```

`status` can be `"ok"` or `"degraded"`.

---

## 3. Settings API

Prefix: `/api/settings`

### 3.1 Get WhatsApp Settings

`GET /api/settings/whatsapp`

Returns the tenant's WhatsApp Business API configuration. **The access token is never returned.**

**Response `200`:**
```json
{
  "settings": {
    "business_account_id": "123456789",
    "phone_number_id": "987654321",
    "webhook_verify_token": "my_verify_token",
    "is_configured": true,
    "has_access_token": true
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
  "webhook_verify_token": "my_verify_token"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `business_account_id` | string | Yes | Alphanumeric + underscores only |
| `phone_number_id` | string | Yes | Alphanumeric + underscores only |
| `access_token` | string | No | Omit to keep existing token. `Bearer ` prefix auto-stripped |
| `webhook_verify_token` | string | No | Token used for Meta webhook verification |

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

Returns message counts for today, this month, and by product type (last 30 days).

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

Prefix: `/api/webhook` — **No authentication required** (called by Meta servers).

### 8.1 Webhook Verification (Meta handshake)

`GET /api/webhook`

Called by Meta during webhook setup. Verifies the token matches either the environment variable `WEBHOOK_VERIFY_TOKEN` or a per-tenant token stored in the database.

**Query Params (set by Meta):**
- `hub.mode` = `subscribe`
- `hub.verify_token` = your verification token
- `hub.challenge` = challenge string

**Response:** Returns `hub.challenge` as plain text on success, `403` on failure.

---

### 8.2 Incoming Webhook

`POST /api/webhook`

Receives incoming messages and delivery status updates from WhatsApp.

- **Signature verification:** `X-Hub-Signature-256` header is validated using `META_APP_SECRET` (if configured).
- **Deduplication:** Uses `webhook_events` table to prevent duplicate processing.
- **Message routing:** Incoming text/button messages are matched against chatbot rules and button mappings. Replies are enqueued into the message queue.
- **Delivery status handling:** Updates message status, triggers campaign finalization, handles retries for failed deliveries.

**Response `200`:**
```json
{
  "status": "ok"
}
```

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

## 11. Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Postgres connection string (Neon DB) |
| `WEBHOOK_VERIFY_TOKEN` | Strong secret for Meta webhook verification |
| `REDIS_HOST` | Redis hostname (default: `localhost`) |
| `REDIS_PORT` | Redis port (default: `6379`) |

### Required Files

| File | Description |
|------|-------------|
| `backend/firebase-service-account.json` | Firebase Admin SDK service account key |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `CORS_ORIGINS` | Hardcoded list | Comma-separated allowed origins |
| `ENVIRONMENT` | `development` | Set to `production` to disable docs and restrict CORS |
| `META_APP_SECRET` | (empty) | WhatsApp webhook signature verification |
| `PG_POOL_MIN` | `1` | Min Postgres connection pool size |
| `PG_POOL_MAX` | `10` | Max Postgres connection pool size |
| `PG_STATEMENT_TIMEOUT_MS` | `30000` | Statement timeout |
| `PG_TIMEZONE` | `Asia/Kolkata` | DB session timezone |
| `TENANT_RATE_LIMIT` | `10` | Worker messages per second per tenant |
| `TENANT_BURST` | `20` | Worker max burst capacity |
| `WA_COOLDOWN_TTL` | `5` | Global 429 cooldown duration (seconds) |
| `QUEUE_RATE_LIMIT` | `80` | BullMQ message worker rate limit |
| `QUEUE_RETRY_ATTEMPTS` | `3` | Max retry attempts for message jobs |
| `DELIVERY_CONFIRM_TIMEOUT_SECONDS` | `900` | Requeue if no delivery confirmation |

---

## 12. Local Development Setup

### Prerequisites

- Python 3.11+
- Node.js 18+
- Redis 6+
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
