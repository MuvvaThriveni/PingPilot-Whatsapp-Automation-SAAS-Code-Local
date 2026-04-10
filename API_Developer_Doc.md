# WappFlow — Developer API Documentation

> **Version:** 4.0.0 (Phase 17 — Dynamic Chatbot System, Button Mappings, Fallback Templates)  
> **Base URL:** `http://localhost:5000/api` (dev) or `https://<your-deployment>/api` (prod)  
> **Auth:** Firebase ID Token — `Authorization: Bearer <firebase_id_token>`  
> **Database:** Neon Postgres (serverless) — see [Architecture_Overview.md](Architecture_Overview.md) for full schema  
> **Last Updated:** 2026-04-10

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
15. [Bulk Message Quota System](#15-bulk-message-quota-system)
16. [Onboarding Quick-Start Guide](#16-onboarding-quick-start-guide)
17. [Glossary](#17-glossary)
18. [Further Reading](#18-further-reading)
19. [Utility Modules Reference](#19-utility-modules-reference)
20. [Contact Limit Enforcement](#20-contact-limit-enforcement)
21. [Redis Command Optimization](#21-redis-command-optimization)
22. [Chatbot System Redesign (Phase 17)](#22-chatbot-system-redesign-phase-17)

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
  ],
  "bulk_quota": {
    "used": 42,
    "limit": 100,
    "remaining": 58,
    "month_key": "2026-03",
    "resets_at": "2026-04-01T00:00:00+00:00",
    "percent_used": 42
  }
}
```

The `bulk_quota` object is always present and reflects the tenant's current monthly bulk message quota status. See [Section 15](#15-bulk-message-quota-system) for details.

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

### 4.2 Get Quota Status

`GET /api/bulk-message/quota`

Returns the tenant's current monthly bulk message quota usage. The quota resets automatically on the first day of each calendar month (UTC).

**Response `200`:**
```json
{
  "used": 42,
  "limit": 100,
  "remaining": 58,
  "month_key": "2026-03",
  "resets_at": "2026-04-01T00:00:00+00:00",
  "percent_used": 42
}
```

| Field | Type | Description |
|-------|------|-------------|
| `used` | int | Messages consumed this month |
| `limit` | int | Maximum allowed messages per month (from `tenants.bulk_quota_limit`) |
| `remaining` | int | `limit - used` (floored at 0) |
| `month_key` | string | Current month in `YYYY-MM` format |
| `resets_at` | string (ISO 8601) | First second of the next calendar month (UTC) |
| `percent_used` | int | `(used / limit) * 100` rounded down |

---

### 4.3 Parse Contacts File

`POST /api/bulk-message/parse`

Parses an Excel/CSV file to extract and validate phone numbers. Used for preview before starting a campaign.

**Request:** `multipart/form-data`

| Field | Type | Required |
|-------|------|----------|
| `file` | File (xlsx/csv) | Yes |

**Phone Number Format:**

Phone numbers are normalized using `utils/phone_utils.normalize_phone()`. The following formats are accepted:

| Input Format | Example | Output |
|---|---|---|
| E.164 with `+` | `+14155552671` | `14155552671` |
| E.164 with `+` (Indian) | `+919876543210` | `919876543210` |
| 10-digit Indian mobile (no `+`) | `9876543210` | `919876543210` |
| International without `+` | `447911123456` | `447911123456` |
| Scientific notation (Excel) | `9.1995E+11` | `919950000000` |

Numbers that fail E.164 length validation (< 10 or > 15 digits after normalization) are **silently skipped** — they do not appear in `contacts` and are not counted in `validContacts`.

**Response `200`:**
```json
{
  "contacts": [
    { "index": 0, "phone": "919876543210", "name": "John", "imageUrl": "" }
  ],
  "total": 150,
  "validContacts": 148,
  "upload_id": "a1b2c3d4-e5f6-..."
}
```

> `total` counts all rows in the file; `validContacts` counts rows that passed phone normalization. `upload_id` is a one-time token for the `/start` endpoint to reuse parsed contacts without re-parsing (cached in Redis for 10 minutes). If Redis is unavailable, `upload_id` is omitted.

**Error `400` — Contact Limit Exceeded:**
```json
{
  "error": "File contains 600 valid contacts, which exceeds the maximum of 500 per campaign. Please reduce the number of contacts and try again.",
  "valid_contacts": 600,
  "max_allowed": 500
}
```

> The limit defaults to **500** and is configurable via the `MAX_VALID_CONTACTS` env var. See [Section 20](#20-contact-limit-enforcement) for details.

**Error `413`:** File exceeds 16 MB limit.

---

### 4.4 Start Campaign

`POST /api/bulk-message/start`

Creates a new bulk messaging campaign and enqueues it for processing. **Enforces monthly quota** — the request is rejected if the number of contacts would exceed the tenant's remaining quota for the month.

**Request:** `multipart/form-data`

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `file` | File (xlsx/csv) | Yes | — | Contact list |
| `templateName` | string | Yes | — | Template `name\|language` or just `name` |
| `campaignName` | string | No | Auto-generated | Human-readable name |
| `delayMs` | int | No | `1000` | Delay between messages (ms) |
| `headerImageUrl` | string | No | `""` | Override image URL for header |
| `scheduledAt` | string (ISO 8601) | No | `null` | Schedule for later; omit for immediate |
| `upload_id` | string | No | `null` | One-time token from `/parse` response to reuse cached contacts (avoids re-parsing). If missing or expired, the uploaded file is parsed normally |

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

**Quota Error `429` — Quota Exhausted:**
```json
{
  "detail": {
    "error": "quota_exceeded",
    "message": "Monthly bulk message quota exhausted",
    "used": 100,
    "limit": 100,
    "remaining": 0,
    "resets_at": "2026-04-01T00:00:00+00:00"
  }
}
```

**Quota Error `429` — Would Exceed Quota:**
```json
{
  "detail": {
    "error": "quota_would_exceed",
    "message": "Campaign would exceed monthly quota. 150 requested but only 58 remaining.",
    "requested": 150,
    "remaining": 58,
    "limit": 100,
    "used": 42,
    "resets_at": "2026-04-01T00:00:00+00:00"
  }
}
```

---

### 4.5 Get All Campaigns

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

### 4.6 Get Campaign Status

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

> **Note:** The `sent_count` and `failed_count` on campaign status/list/details responses are now sourced from **authoritative recipient table counts** (via `campaign_recipients.count_by_status()`) rather than incremental counter shards. This eliminates counter drift and ensures the frontend always shows accurate numbers. Additional fields `pending_count` and `quota_exceeded_count` are also returned.

---

### 4.7 Get Campaign Details

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

**Recipient Statuses:** `pending`, `queued`, `processing`, `submitted`, `sent`, `delivered`, `read`, `failed`, `quota_exceeded`

> Recipients with `quota_exceeded` status were not sent because the tenant's monthly quota was exhausted. This is a terminal status — these recipients will not be retried automatically.

---

### 4.8 Stop Campaign

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

### 4.9 Delete Campaign

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

### 4.10 Resend Failed Recipients

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

### 4.11 Get Campaign Limits

`GET /api/bulk-message/limits`

Returns backend-configured limits so the frontend can stay in sync without hardcoding values.

**Response `200`:**
```json
{
  "max_valid_contacts": 500
}
```

| Field | Type | Description |
|-------|------|-------------|
| `max_valid_contacts` | int | Maximum number of valid contacts allowed per bulk campaign. Configurable via `MAX_VALID_CONTACTS` env var (default 500) |

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

Phone numbers are normalized by `utils/phone_utils.normalize_phone()` — see [Section 4.3](#43-parse-contacts-file) for accepted formats. Invalid numbers are skipped.

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

The chatbot system is **fully dynamic and DB-driven** as of Phase 17. All behavior — settings, keyword rules, and button→template mappings — is stored in Postgres and cached in-memory. There are **no hardcoded defaults**.

### Chatbot Automation Priority Order

When an incoming WhatsApp message arrives and the chatbot is enabled, the system checks in this order:

| Priority | Layer | What it does |
|----------|-------|--------------|
| **1 (Highest)** | Button→Template Mappings | Exact text match against `chatbot_button_mappings` table (cached 1h) |
| **2** | Keyword Rules | Matches `chatbot_rules` by `match_type` (exact/contains/starts_with), ordered by `priority DESC` |
| **3 (Lowest)** | Fallback Template | Sends `fallback_template_name` if no rule matched and sender hasn't been triggered in `fallback_cooldown_hours` |

First match wins. Once a layer matches, lower layers are skipped entirely.

---

### 6.1 Get Chatbot Settings

`GET /api/chatbot/settings`

**Response `200`:**
```json
{
  "settings": {
    "is_enabled": true,
    "fallback_message": "Thank you for your message. Our team will get back to you soon.",
    "fallback_template_name": "first_trigger",
    "fallback_cooldown_hours": 24,
    "use_ai": false
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `is_enabled` | bool | Master switch — no auto-replies fire when `false` |
| `fallback_message` | string | Text reply sent when no rule matches (if `fallback_template_name` is empty) |
| `fallback_template_name` | string | WhatsApp template to send as fallback when no rule matches. Empty string = no fallback. |
| `fallback_cooldown_hours` | int | Hours between fallback sends to the same contact (default 24). Prevents spam. |
| `use_ai` | bool | Always `false` — AI mode is reserved for a future release. |

---

### 6.2 Update Chatbot Settings

`PUT /api/chatbot/settings`

**Request Body (JSON):**
```json
{
  "is_enabled": true,
  "fallback_message": "We'll get back to you shortly.",
  "fallback_template_name": "welcome_template",
  "fallback_cooldown_hours": 48
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `is_enabled` | bool | Yes | Enable/disable all chatbot auto-replies |
| `fallback_message` | string | Yes | Fallback text (used if no template is configured) |
| `fallback_template_name` | string | No | Template name for fallback trigger. Set to `""` or `null` to disable. |
| `fallback_cooldown_hours` | int | No | Default 24. Min recommended: 1. |

> **Note:** Saving settings invalidates both the chatbot config cache and the button mappings cache for the tenant.

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

Returns all keyword rules for the tenant, ordered by `priority DESC`. Cached for 6 hours; invalidated on any create/update/delete.

**Response `200`:**
```json
{
  "rules": [
    {
      "id": 1,
      "keyword": "hello",
      "response": "Hi there! How can I help?",
      "response_type": "text",
      "match_type": "contains",
      "priority": 0,
      "is_active": true,
      "created_at": "2026-04-01T10:00:00+05:30",
      "updated_at": null
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Auto-increment database ID |
| `keyword` | string | Keyword/phrase to match (always stored lowercase) |
| `response` | string | The reply — either a text string or a WhatsApp template name |
| `response_type` | string | `"text"` or `"template"` |
| `match_type` | string | `"exact"`, `"contains"`, or `"starts_with"` |
| `priority` | int | Higher = checked first. Rules with the same priority are ordered by `id DESC` |
| `is_active` | bool | Inactive rules are stored but never matched |

---

### 6.4 Create Rule

`POST /api/chatbot/rules`

**Request Body (JSON):**
```json
{
  "keyword": "pricing",
  "response": "Check out our pricing at https://example.com/pricing",
  "response_type": "text",
  "match_type": "contains",
  "priority": 1
}
```

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `keyword` | string | Yes | — | Stored as lowercase |
| `response` | string | Yes | — | Text reply or template name |
| `response_type` | string | No | `"text"` | `"text"` or `"template"` |
| `match_type` | string | No | `"contains"` | `"exact"`, `"contains"`, `"starts_with"` |
| `priority` | int | No | `0` | |

**Match Type Behavior:**

| `match_type` | Example keyword | Matches |
|---|---|---|
| `exact` | `"hello"` | Only the exact message `"hello"` |
| `contains` | `"price"` | Any message containing the word `"price"` |
| `starts_with` | `"hi"` | Messages starting with `"hi"` (e.g. `"Hi there"`) |

**Template Response:** When `response_type` is `"template"`, the `response` field must contain the exact approved WhatsApp template name (e.g. `"welcome_message"`). The webhook engine will send this template via the message queue.

**Response `200`:**
```json
{
  "rule": {
    "id": 2,
    "keyword": "pricing",
    "response": "Check out our pricing at ...",
    "response_type": "text",
    "match_type": "contains",
    "priority": 1,
    "is_active": true
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
  "response": "promo_template",
  "response_type": "template",
  "match_type": "exact",
  "priority": 2,
  "is_active": true
}
```

**Response `200`:** Returns the updated rule object.

**Error `404`:** `{ "error": "Rule not found" }`

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

### 6.7 Get Button Mappings

`GET /api/chatbot/button-mappings`

Lists all button→template mappings for the tenant. Unlike keyword rules, button mappings only support **exact text match** (used for WhatsApp interactive button replies). Cached in-memory for 1 hour; invalidated on any write.

**Response `200`:**
```json
{
  "mappings": [
    {
      "id": 1,
      "tenant_id": "firebase_uid",
      "button_text": "Book Session",
      "template_name": "session_booking_template",
      "is_active": true,
      "priority": 0,
      "created_at": "2026-04-10T10:00:00+05:30",
      "updated_at": "2026-04-10T10:00:00+05:30"
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Auto-increment database ID |
| `button_text` | string | Exact button text to match (case-sensitive, stripped of whitespace) |
| `template_name` | string | WhatsApp template name to send when the button is pressed |
| `is_active` | bool | Only active mappings are used during webhook processing |
| `priority` | int | Higher-priority mappings are checked first |

> **How matching works:** When a user taps a WhatsApp interactive button, the `button.text` field from the webhook payload is compared against all active `button_text` values for the tenant. First exact match (by priority) triggers the corresponding template send.

---

### 6.8 Create Button Mapping

`POST /api/chatbot/button-mappings`

**Request Body (JSON):**
```json
{
  "button_text": "Book Session",
  "template_name": "session_booking_template",
  "is_active": true,
  "priority": 0
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `button_text` | string | **Yes** | The exact text of the WhatsApp button. Must be non-empty. Whitespace stripped. |
| `template_name` | string | **Yes** | Approved WhatsApp template name to send as response. |
| `is_active` | bool | No | Default `true` |
| `priority` | int | No | Default `0`. Higher = checked first when multiple mappings exist. |

**Uniqueness:** Only one mapping per `button_text` per tenant is allowed. Attempting to create a duplicate returns a `500` database error.

**Error `400` — Missing button_text:**
```json
{
  "error": "button_text is required"
}
```

**Response `200`:**
```json
{
  "mapping": {
    "id": 2,
    "button_text": "Book Session",
    "template_name": "session_booking_template",
    "is_active": true,
    "priority": 0,
    "created_at": "2026-04-10T10:05:00+05:30",
    "updated_at": "2026-04-10T10:05:00+05:30"
  }
}
```

---

### 6.9 Update Button Mapping

`PUT /api/chatbot/button-mappings/{mapping_id}`

**Request Body (JSON):** Same fields as create. All fields are optional for partial update.

```json
{
  "template_name": "updated_template",
  "is_active": false
}
```

**Response `200`:**
```json
{
  "success": true,
  "message": "Mapping updated"
}
```

**Error `500`:** `{ "error": "Failed to update mapping" }`

---

### 6.10 Delete Button Mapping

`DELETE /api/chatbot/button-mappings/{mapping_id}`

**Response `200`:**
```json
{
  "success": true,
  "message": "Mapping deleted"
}
```

**Error `500`:** `{ "error": "Failed to delete mapping" }`

---

### 6.11 Get Chat Users

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

### 6.12 Get Conversations (All)

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

### 6.13 Get User Conversations

`GET /api/chatbot/conversations/{phone}`

**Query Params:** `limit` (default 50), `cursor`

Returns conversation history for a specific phone number. Same response shape as 6.12.

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
- **Message routing:** Incoming text/button/interactive messages matched against three layers in order:
  1. **Button→Template Mappings** — exact match against `chatbot_button_mappings` table (per-tenant, cached 1h). Handles WhatsApp interactive button replies (`button.text`) and plain message text.
  2. **Keyword Rules** — DB-backed `chatbot_rules` with configurable `match_type` (`exact`/`contains`/`starts_with`) and `response_type` (`text`/`template`). Ordered by `priority DESC`.
  3. **Fallback Template** — if no rule matched and the sender hasn't been triggered within `fallback_cooldown_hours` (default 24h), the `fallback_template_name` template is sent. Controlled by per-tenant cooldown in `user_triggers` table.
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
| `429` | Rate limited — or bulk message quota exceeded (see [Section 15](#15-bulk-message-quota-system)). Quota errors include structured `detail` with `error`, `remaining`, `limit`, `resets_at`. |

### 10.1 WhatsApp API Non-Retryable Error Codes

The message worker classifies certain WhatsApp Cloud API error codes as **non-retryable** — these are immediately marked as `failed` without consuming additional retry attempts, since re-sending will never succeed:

| Error Code | Meaning | Action |
|------------|---------|--------|
| `#132000` | Template parameter count mismatch | Mark failed immediately; check template variable mapping |
| `#132001` | Template name does not exist in approved templates | Mark failed immediately; the template must be approved first |
| `#132012` | Media parameter format mismatch (header media missing/expired) | Mark failed; cached media ID is **invalidated** so next run re-uploads |
| `#100` | Invalid parameter (e.g. image > 5 MB after compression) | Mark failed immediately |

All other errors (5xx, network failures, `#429`) are retried with exponential backoff up to `QUEUE_RETRY_ATTEMPTS` (default 3).

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
| `QUEUE_RATE_LIMIT` | `80` | **Deprecated (Phase 14).** BullMQ's built-in limiter has been removed to reduce Redis command overhead. Rate control is now handled by `WORKER_RATE_DELAY`. This variable is retained for backward compatibility but has no effect. |
| `WORKER_RATE_DELAY` | `0.2` | Seconds to sleep between message jobs in the worker (in-worker throttle). `0.2` = ~5 msg/sec. Set to `0` to disable throttling. Replaces the former BullMQ limiter. See [Section 21](#21-redis-command-optimization). |
| `USE_TOKEN_BUCKET` | `true` | Feature flag for API rate limiting strategy. `true` = Lua-based token bucket (1 Redis call per request). `false` = sliding window (fallback, ~5 Redis calls per request). |
| `QUEUE_RETRY_ATTEMPTS` | `3` | Max retry attempts for message jobs |
| `DELIVERY_CONFIRM_TIMEOUT_SECONDS` | `900` | Requeue if no delivery confirmation |

### Optional — Bulk Campaign Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_VALID_CONTACTS` | `500` | Maximum valid contacts allowed per bulk campaign. Enforced at both `/parse` and `/start` endpoints. See [Section 20](#20-contact-limit-enforcement). |

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
| `tenants` | WhatsApp API credentials per tenant + `bulk_quota_limit` (default 100) |
| `chatbot_config` | Chatbot on/off, `fallback_message`, `fallback_template_name`, `fallback_cooldown_hours` |
| `chatbot_rules` | Keyword → response rules with `response_type` (`text`/`template`) and `match_type` (`exact`/`contains`/`starts_with`) |
| `chatbot_button_mappings` | **NEW (Phase 17)** — Per-tenant button text → template mappings. Replaces JSONB columns on `chatbot_config`. Each row: `button_text`, `template_name`, `is_active`, `priority` |
| `chatbot_flows` | **NEW (Phase 17, future)** — Visual flow builder storage. `flow_data` JSONB with nodes/edges/variables. Not yet exposed via API. |
| `campaigns` | Bulk message campaign metadata (composite PK: `tenant_id, campaign_id`) |
| `campaign_recipients` | Per-recipient delivery status machine (`pending → queued → processing → submitted → sent`) |
| `campaign_counters` | Sharded sent/failed counters (atomic increment via DB transactions) |
| `messages` | Unified message log (all products: bulk_message, chatbot, file_forward) |
| `chat_messages` | Conversation history (incoming + outgoing WhatsApp messages) |
| `webhook_events` | Deduplication for incoming webhooks (composite PK: `tenant_id, event_id`) |
| `usage_events` | Billable usage tracking (per event, per month) |
| `template_cache` | Persistent WhatsApp template metadata (synced from Meta API) |
| `user_triggers` | Configurable-cooldown rate limit for fallback chatbot messages (default 24h) |
| `tenant_quota_usage` | Per-tenant monthly bulk message quota consumption (composite PK: `tenant_id, month_key`) |
| `messages_archive` | Archived messages (older than RETENTION_DAYS) |
| `chat_messages_archive` | Archived chat messages |
| `webhook_events_archive` | Archived webhook events |
| `usage_events_archive` | Archived usage events |
| `daily_message_stats` | Pre-aggregated daily message counts (preserves dashboard accuracy after archival) |

### Applying Migrations

When adding new features that require schema changes, use the migration script pattern:

```bash
# Apply the base schema (idempotent — safe to re-run)
python backend/apply_schema.py

# Apply Phase 17 chatbot redesign migration
python backend/run_migration.py
```

`run_migration.py` applies `migration_chatbot_redesign.sql` which:
1. Adds `fallback_template_name` + `fallback_cooldown_hours` to `chatbot_config`
2. Adds `response_type` + `match_type` to `chatbot_rules`
3. Creates `chatbot_button_mappings` table
4. Creates `chatbot_flows` table (future)
5. Migrates existing JSONB button mappings to the new table
6. Sets default `fallback_template_name = 'first_trigger'` for tenants with chatbot enabled

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

## 15. Bulk Message Quota System

WappFlow enforces a **per-tenant monthly quota** on bulk WhatsApp messages. This section explains how the quota system works end-to-end for developers.

### 15.1 Overview

Each tenant has a configurable `bulk_quota_limit` (default: **100 messages/month**) stored in the `tenants` table. Actual usage is tracked in the `tenant_quota_usage` table, keyed by `(tenant_id, month_key)`. The quota resets automatically each calendar month — no cron job needed, just a new `month_key`.

### 15.2 Data Model

**`tenants` table** — new column:

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `bulk_quota_limit` | INTEGER | `100` | Max bulk messages per calendar month |

**`tenant_quota_usage` table:**

| Column | Type | Description |
|--------|------|-------------|
| `tenant_id` | TEXT (PK) | Foreign key to `tenants` |
| `month_key` | TEXT (PK) | `YYYY-MM` format (e.g., `2026-03`) |
| `messages_sent` | INTEGER | Running count of consumed messages |
| `last_updated_at` | TIMESTAMPTZ | Last increment timestamp |

### 15.3 Enforcement Layers

The quota is enforced at three levels:

**Layer 1 — API Pre-Check** (`POST /api/bulk-message/start`)
- After parsing the uploaded file, the API reads the tenant's quota status
- If `contacts > remaining` → returns **HTTP 429** with `quota_would_exceed` or `quota_exceeded`
- Campaign is never created if quota would be exceeded

**Layer 2 — Campaign Worker** (fan-out capping)
- When a campaign job is picked up, the worker reads quota remaining
- Caps fan-out to `min(pending_recipients, quota_remaining)`
- Excess recipients are immediately marked `quota_exceeded`

**Layer 3 — Message Worker** (atomic per-message consume)
- Before sending each message, the worker calls `try_consume_quota()`
- Uses a conditional upsert that atomically increments `messages_sent` only if `< limit`
- If the upsert returns no row → quota is full → recipient marked `quota_exceeded`, message not sent
- This is the **final authority** and is race-safe under concurrent workers

### 15.4 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/bulk-message/quota` | GET | Returns current quota status (used, limit, remaining, resets_at, percent_used) |
| `/api/bulk-message/start` | POST | Enforces quota pre-check; returns 429 if quota would be exceeded |
| `/api/settings/usage` | GET | Includes `bulk_quota` object in response |

See [Section 4.2](#42-get-quota-status) and [Section 4.4](#44-start-campaign) for full request/response documentation.

### 15.5 Recipient Status: `quota_exceeded`

When a recipient cannot be sent due to quota exhaustion, their status is set to `quota_exceeded`. This is a **terminal status** — it counts as "done" for campaign finalization, so campaigns complete cleanly even when quota is hit mid-run.

The frontend displays these recipients with an orange "Quota Exceeded" badge and a dedicated filter tab on the campaign detail page.

### 15.6 Frontend Behavior

The bulk message page (`/dashboard/bulk-message`) includes:

- **Quota progress bar** at the top (green → orange → red as usage increases)
- **Used / Limit / Remaining** text + reset date
- **"Exhausted" badge** when remaining = 0
- **Inline warning** when selected contacts exceed remaining quota
- **Start button disabled** when contacts > remaining or quota = 0
- **Friendly toast messages** for 429 quota errors
- **Auto-refresh** of quota every 10 seconds and on campaign completion

### 15.7 Key Files

| File | Role |
|------|------|
| `backend/schema.sql` | `tenants.bulk_quota_limit` + `tenant_quota_usage` DDL |
| `backend/db_layer/quota.py` | `get_quota_status()`, `try_consume_quota()` |
| `backend/routers/bulk_message.py` | `GET /quota` + pre-check on `POST /start` |
| `backend/routers/settings.py` | `bulk_quota` in `GET /usage` response |
| `backend/db_layer/campaign_recipients.py` | `quota_exceeded` status + `mark_excess_recipients_quota_exceeded()` |
| `backend/worker_main.py` | Capped fan-out + atomic consume |
| `frontend/src/lib/api.ts` | `bulkMessage.quota()` |
| `frontend/src/app/dashboard/bulk-message/page.tsx` | Quota bar + button guard |
| `frontend/src/app/dashboard/bulk-message/[campaignId]/page.tsx` | `quota_exceeded` badge + filter |

### 15.8 Manual Quota Management (SQL)

**Check current quota for a tenant:**
```sql
SELECT t.bulk_quota_limit, q.messages_sent, q.month_key
FROM tenants t
LEFT JOIN tenant_quota_usage q ON q.tenant_id = t.tenant_id
  AND q.month_key = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM')
WHERE t.tenant_id = '<tenant_id>';
```

**Reset quota for testing:**
```sql
DELETE FROM tenant_quota_usage
WHERE tenant_id = '<tenant_id>'
  AND month_key = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM');
```

**Increase a tenant's quota limit:**
```sql
UPDATE tenants SET bulk_quota_limit = 500 WHERE tenant_id = '<tenant_id>';
```

---

## 16. Onboarding Quick-Start Guide

This section is for **new developers joining the team**. It walks you through the entire system so you can understand, run, and contribute to WappFlow within your first day.

### 16.1 What Does WappFlow Do?

WappFlow is a **multi-tenant WhatsApp automation SaaS**. Each user (tenant) connects their own WhatsApp Business Account and can:

1. **Bulk Messaging** — Upload a CSV of contacts, pick a pre-approved WhatsApp template, and send thousands of personalized messages. Campaigns can be scheduled, paused, and retried.
2. **File Forwarding** — Send documents/images to one or many recipients via the WhatsApp Cloud API.
3. **Auto-Reply Chatbot** — Configure keyword-based rules that automatically reply to incoming WhatsApp messages. Supports interactive button flows and a 24h rate-limited first-trigger fallback.

### 16.2 How the Pieces Fit Together

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

### 16.3 Key Concepts to Understand

| Concept | Explanation |
|---------|-------------|
| **Tenant** | A single user identified by their Firebase Auth UID. All data is isolated per tenant via `tenant_id` columns. |
| **Campaign** | A bulk message job. Contains a template, a list of recipients, and counters tracking progress. |
| **Recipient status machine** | `pending → queued → processing → submitted → sent` (via webhook). Failed recipients can be retried. `quota_exceeded` is a terminal status for recipients blocked by quota. |
| **Template** | A pre-approved WhatsApp message format (created in Meta Business Manager). WappFlow caches template metadata locally. |
| **BullMQ** | A Redis-backed job queue. `campaign_queue` fans out recipients; `message_queue` sends individual messages with rate limiting. |
| **Per-tenant webhook** | Each tenant gets their own webhook URL (`/api/webhook/{tenant_id}`) with HMAC signature verification using their encrypted `meta_app_secret`. |
| **Encryption at rest** | Sensitive values like `meta_app_secret` are Fernet-encrypted before storage. See [Section 11](#11-encryption-at-rest). |
| **Data retention** | Old rows are archived from live tables → `*_archive` tables, keeping queries fast. See [Section 14](#14-data-retention--archive-system). |
| **Bulk message quota** | Per-tenant monthly message cap enforced at API, campaign worker, and message worker levels. Atomic consumption prevents overshoot. See [Section 15](#15-bulk-message-quota-system). |

### 16.4 Your First Local Setup

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

### 16.5 Important Files to Read First

As a new developer, read these files in order to build a mental model:

| Order | File | Why |
|-------|------|-----|
| 1 | `schema.sql` + `retention_schema.sql` | Understand the data model — every table, column, and relationship |
| 2 | `main.py` | See how the app starts, middleware stack, background tasks, health check |
| 3 | `auth_middleware.py` | Understand how every request gets a `tenant_id` |
| 4 | `store.py` | The cached read/write layer — how settings and chatbot config are loaded |
| 5 | `routers/bulk_message.py` | The most complex product — campaign lifecycle, contact limits, parsed contacts caching |
| 6 | `worker_main.py` | How jobs are picked up, throttled (in-worker sleep), and sent via WhatsApp API. Retry-aware quota logic. |
| 7 | `routers/webhook.py` | How incoming WhatsApp messages flow through the system |
| 8 | `services/queue_manager.py` | How jobs are enqueued (campaign, message, file-forward, dead-letter) |
| 9 | `rate_limit.py` | API rate limiting (Lua token bucket), worker token bucket, in-memory caches, global cooldown |
| 10 | `retention.py` | Data lifecycle — archiving + purging |
| 11 | `db_layer/quota.py` | Per-tenant monthly bulk message quota — reads + atomic consumption |
| 12 | `utils/phone_utils.py` | E.164 phone normalization — used everywhere contacts are parsed or sent |
| 13 | `utils/image_utils.py` | Image compression (≤5 MB) for WhatsApp media uploads |
| 14 | `db_layer/campaign_recipients.py` | Recipient status transitions + `count_by_status()` authoritative counts |

### 16.6 Common Development Tasks

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

---

## 17. Glossary

A quick reference for terminology used throughout this documentation. Essential for new developers joining the team.

| Term | Definition |
|------|------------|
| **Tenant** | A single user/business identified by their Firebase Auth UID. All data, keys, and operations are scoped to a `tenant_id`. |
| **Campaign** | A bulk messaging job containing a template, a list of recipients parsed from CSV/Excel, and counters tracking progress. |
| **Recipient** | A single contact within a campaign. Each recipient has a status that progresses through a state machine: `pending → queued → processing → submitted → sent/failed/quota_exceeded`. |
| **Template** | A pre-approved WhatsApp message format created in Meta Business Manager. Must be `APPROVED` status before WappFlow can use it. Templates can have parameters (e.g., `{{1}}` for name). |
| **BullMQ** | A Redis-backed job queue library (Python port). Used for all asynchronous work: campaign fan-out, message sending, and dead letter handling. |
| **Campaign Queue** | The lightweight BullMQ queue that receives "process campaign X" jobs. The worker reads pending recipients and fans them out to the message queue. |
| **Message Queue** | The heavy-lifter BullMQ queue that processes individual WhatsApp API calls with rate limiting, retry logic, and idempotency. |
| **Dead Letter Queue (DLQ)** | A BullMQ queue where jobs that have exhausted all retry attempts are moved. These are investigated manually. |
| **Token Bucket** | A rate-limiting algorithm used per-tenant in the worker. Each tenant gets 10 messages/second with a burst of 20, preventing any single tenant from monopolizing the sending capacity. |
| **Global Cooldown** | When WhatsApp returns repeated `429 Too Many Requests` errors, all workers pause for a configurable duration (default 5s) to avoid API bans. |
| **Webhook** | An HTTP callback from Meta's servers to our system. Triggered when messages are delivered, read, or when users reply. Each tenant has their own webhook URL for HMAC signature verification. |
| **HMAC-SHA256** | Hash-based Message Authentication Code used to verify that webhook payloads genuinely originate from Meta, not from attackers. Uses each tenant's `meta_app_secret`. |
| **Fernet Encryption** | Symmetric encryption (AES-128-CBC) from Python's `cryptography` library. Used to encrypt `meta_app_secret` before storing in Postgres. Values are prefixed with `"enc:"`. |
| **Idempotency** | The property that performing an operation multiple times produces the same result. In WappFlow, message sends use calculated job IDs to prevent duplicate sends on worker restarts. |
| **Data Retention** | The automated system that moves old rows from live tables to archive tables, keeping queries fast. Controlled by `RETENTION_ENABLED` and related env vars. |
| **Purge** | The second stage of data retention that permanently deletes very old rows from archive tables after a configurable period (default 90 days). |
| **Quota** | A per-tenant monthly cap on bulk messages (default 100/month). Enforced atomically at three levels: API pre-check, campaign worker cap, and per-message consumption. |
| **`tenant_id`** | The Firebase Auth UID that uniquely identifies each tenant. Every database table and cache key is scoped by this value. |
| **`wa_message_id`** | The unique identifier returned by WhatsApp's Cloud API after successfully accepting a message. Used for delivery status tracking. |
| **`month_key`** | A string in `YYYY-MM` format (e.g., `2026-03`) used to partition quota usage and usage events by calendar month. |
| **Button Mapping** | A tenant-configured rule stored in `chatbot_button_mappings` that maps an exact WhatsApp button text (e.g. `"Book Session"`) to a WhatsApp template name. Checked at highest priority by the chatbot engine. |
| **Match Type** | Per-rule setting on `chatbot_rules` controlling how keyword matching works: `exact` (full message match), `contains` (substring), or `starts_with` (prefix). Added in Phase 17. |
| **Response Type** | Per-rule setting on `chatbot_rules` controlling what is sent on a match: `text` (plain text) or `template` (WhatsApp template by name). Added in Phase 17. |
| **Fallback Template** | A tenant-configurable WhatsApp template (`fallback_template_name`) sent when no button or keyword rule matches an incoming message. Rate-limited by `fallback_cooldown_hours` (default 24h) per sender. Replaced the hardcoded `first_trigger` approach in Phase 17. |
| **Fallback Cooldown** | The number of hours (`fallback_cooldown_hours`) between fallback template sends to the same contact. Tracked in the `user_triggers` table per `(tenant_id, phone)`. |
| **First-Trigger Fallback** | Legacy term for the Fallback Template system (pre-Phase 17). Now configurable per tenant as `fallback_template_name` + `fallback_cooldown_hours`. |
| **Neon Postgres** | A serverless PostgreSQL provider. WappFlow uses it as the primary database with `psycopg3` async driver and connection pooling. |
| **IST (Indian Standard Time)** | UTC+05:30. All timestamps in WappFlow are standardized to IST for consistency across logging, database records, and API responses. |

---

## 18. Further Reading

| Document | Description |
|----------|-------------|
| **[Architecture_Overview.md](Architecture_Overview.md)** | Deep system architecture: multi-tenancy model, request lifecycle, data flows, security layers, queue architecture, retention system, encryption at rest, and full phase history. Start here for the big picture. |
| **[Technical_Readme.md](Technical_Readme.md)** | Concise technical summary of every system component. Good for quick reference. |
| **[README.md](README.md)** | Project overview, getting started guide, deployment instructions, and troubleshooting. |
| **[backend/.env.example](backend/.env.example)** | Comprehensive environment variable reference with descriptions and defaults. |
| **[backend/schema.sql](backend/schema.sql)** | Complete DDL for all live database tables. Read this to understand the data model. |
| **[backend/retention_schema.sql](backend/retention_schema.sql)** | DDL for archive tables and `daily_message_stats`. |
| **[backend/retention.py](backend/retention.py)** | Data retention engine source. Contains `archive_old_data()` and `purge_old_archives()` with detailed comments. |

---

## 19. Utility Modules Reference

These shared utilities in `backend/utils/` are used across routers, services, and the BullMQ worker. Understanding them is key to correctly handling phone numbers and media uploads.

---

### 19.1 `utils/phone_utils.py` — E.164 Phone Normalization

**Function:** `normalize_phone(phone_str: str) -> Optional[str]`

Normalizes any raw phone string into a **digits-only** international number compatible with the WhatsApp Cloud API. Returns `None` for invalid numbers instead of raising exceptions, so all callers can skip gracefully.

#### Normalization Rules (applied in order)

| Step | Rule |
|------|------|
| 1 | Detect if the raw input starts with `+` (international prefix flag) |
| 2 | Handle scientific notation (e.g. Excel's `9.1995E+11`) — parsed as float, `+` is not treated as a country-code prefix |
| 3 | Strip all non-digit characters |
| 4 | Apply `+91` (India) fallback **only if**: original had no `+`, stripped length is exactly 10, and first digit is 6/7/8/9 (Indian mobile range) |
| 5 | Validate final length: 10–15 digits (E.164 range). Return `None` if outside range |

#### Examples

```python
normalize_phone("+14155552671")   # US: '14155552671'
normalize_phone("+44 7911 123456") # UK: '447911123456'
normalize_phone("+919876543210")  # Indian with code: '919876543210'
normalize_phone("9876543210")     # Indian local 10-digit: '919876543210'
normalize_phone("9.1995E+11")     # Excel scientific: '919950000000'
normalize_phone("022-12345678")   # Indian landline (11 digits, no +91 fallback): '02212345678'
normalize_phone("12345")          # Too short → None
normalize_phone("+1234567890123456")  # Too long → None
```

#### Where It Is Used

| File | Usage |
|------|-------|
| `routers/bulk_message.py` | Normalize phones from uploaded CSV/Excel before inserting recipients |
| `routers/file_forward.py` | Normalize recipient phone before sending single file |
| `services/queue_manager.py` | Normalize phone in `enqueue_message()` for idempotent job ID generation; skip if `None` |
| `worker_main.py` | Re-normalize in worker for observability; mark recipient `failed` if `None` |

> **Important for Excel users:** Numbers stored as floats in Excel may be read as scientific notation (e.g. `9.1995E+11`). The normalizer handles this automatically. To be safe, format the phone column as **Text** in Excel before exporting, and include the `+` country code prefix.

---

### 19.2 `utils/image_utils.py` — Automatic Image Compression

**Function:** `compress_image(file_bytes: bytes) -> bytes`

Automatically compresses image bytes to fit within WhatsApp's **5 MB media upload limit**. Called by `services/template_builder.upload_header_media()` before uploading template header images. Non-image files pass through untouched.

WhatsApp Cloud API returns `#100 Invalid parameter` when an image exceeds 5 MB. This module prevents that silently.

#### Compression Strategy

```
Input image bytes
      │
      ├─ Already ≤ 5 MB? → return as-is (no Pillow required)
      │
      ├─ Open with Pillow → fix EXIF orientation (handle phone rotations)
      │
      ├─ PNG with real transparency?
      │   ├─ Optimize PNG (lossless)
      │   │   └─ ≤ 5 MB? → return PNG
      │   └─ Still > 5 MB → fall through to JPEG
      │
      ├─ Iterative JPEG quality reduction (90 → 85 → ... → 40, step 5)
      │   └─ First quality where output ≤ 5 MB → return JPEG
      │
      ├─ Last resort: halve resolution up to 5× (LANCZOS) + JPEG quality 70
      │   └─ First size where output ≤ 5 MB → return JPEG
      │
      └─ Absolute fallback: return original bytes (upload may fail naturally)
```

#### RGBA / Transparency Handling

JPEG does not support transparency. For RGBA/LA/palette-mode images, the module composites onto a **white background** before converting to RGB. This preserves visual fidelity for logos and PNGs with semi-transparent elements.

#### MIME Type Update After Compression

After compression, `upload_header_media()` inspects the magic bytes of the returned value to set the correct MIME type for the upload:

```python
if file_bytes[:3] == b'\xff\xd8\xff':  # JPEG magic bytes
    mime = "image/jpeg"
elif file_bytes[:8] == b'\x89PNG\r\n\x1a\n':  # PNG magic bytes
    mime = "image/png"
```

This ensures the Graph API receives a consistent `Content-Type`.

#### Log Events Emitted

| Event | Meaning |
|-------|---------|
| `image_compression_skipped` | Image already ≤ 5 MB, no processing needed |
| `image_compression_start` | Compression starting (logs format + original size) |
| `image_compressed` | Compression succeeded (logs original size, final size, format, quality if JPEG) |
| `image_compression_png_fallback` | Optimized PNG still too large, falling back to JPEG |
| `image_compression_resize` | Quality reduction insufficient, entering resize phase |
| `image_compression_resize_error` | Resize step raised an exception |
| `image_compression_failed` | All strategies exhausted; original bytes returned as fallback |
| `image_compression_import_error` | Pillow not installed or import error; original bytes used |
| `image_compression_error` | Pillow could not decode the image (corrupt/unsupported format) |

#### Dependencies

Requires **Pillow** (`pip install Pillow`). Already in `backend/requirements.txt`. If Pillow is unavailable at runtime, the image is uploaded uncompressed with a `WARN` log.

---

### 19.3 Media Upload Internals (`services/whatsapp.py`)

The `WhatsAppService.upload_media()` method sends files to the Graph API using **multipart/form-data** with exactly three fields required by Meta:

| Multipart field | Value | Sent via |
|-----------------|-------|----------|
| `file` | Binary content with filename | `files=` (httpx) |
| `messaging_product` | `"whatsapp"` | `data=` (httpx) |
| `type` | MIME type string | `data=` (httpx) |

The filename is derived from the MIME type (e.g. `upload.jpg` for `image/jpeg`) so the Graph API can detect the file format. Using a generic name like `upload.bin` or omitting the extension causes `#100` errors.

**MIME → Extension mapping:**

| MIME | Extension |
|------|-----------|
| `image/jpeg` | `.jpg` |
| `image/png` | `.png` |
| `image/webp` | `.webp` |
| `video/mp4` | `.mp4` |
| `audio/mpeg` | `.mp3` |
| `audio/ogg` | `.ogg` |
| `application/pdf` | `.pdf` |
| `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `.docx` |
| `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | `.xlsx` |
| `application/vnd.openxmlformats-officedocument.presentationml.presentation` | `.pptx` |

---

### 19.4 Template Builder — Variable Support & Validation

The `services/template_builder.py` module handles building the `components` payload for `send_template_message()`. As of Phase 12, it supports both variable styles and performs pre-send validation.

#### Variable Types

| Type | Syntax | Example |
|------|--------|---------|
| **Positional** | `{{1}}`, `{{2}}` | `Hello {{1}}, your order {{2}} is ready` |
| **Named** | `{{name}}`, `{{phone}}` | `Hi {{name}}, your code is {{code}}` |

Named variables produce API parameter objects with `"parameter_name"` field; positional variables produce plain `"text"` entries.

#### Parameter Resolution Order

For each template variable, the builder resolves the value in this priority order:

1. Exact key match in contact dict (e.g. `contact["name"]` for `{{name}}`)
2. Positional fallbacks: slot 0 → `contact["name"]`, slot 1 → `contact["phone"]`
3. Example text from the template definition (stored in `example.body_text`)
4. Single space `" "` (preserves parameter count — WhatsApp rejects missing params)

#### Pre-Send Validation (`validate_components`)

Before calling the WhatsApp API, the worker validates built components against cached template metadata:

- **Media headers:** If the template has an `IMAGE`/`VIDEO`/`DOCUMENT` header, a `header` component with a media parameter **must** be present. Missing → immediate `failed` (no retry).
- **Parameter count match:** Body and header text parameter counts are compared against the template definition. Mismatch → immediate `failed` (no retry, logs `template_validation_failed`).

#### Media ID Caching

Uploaded header media IDs are cached in-memory for the process lifetime under a **tenant-scoped key** (`{tenant_id}:{template_name}|{language}`). This avoids re-uploading the same image on every webhook trigger. The cache is invalidated when a `#132012` error is received, forcing a fresh upload on the next send.

#### CDN URL Validation

Before uploading, `upload_header_media()` validates the downloaded content:
- Rejects responses with `Content-Type: text/html` or `application/json` (expired CDN URLs return error pages)
- Rejects downloads < 100 bytes (suspiciously small — likely an error response)
- Infers MIME from template `format` field when the CDN returns `application/octet-stream`

---

## 20. Contact Limit Enforcement

WappFlow enforces a **per-campaign contact limit** to prevent excessively large campaigns from overwhelming the system or hitting WhatsApp rate limits.

### 20.1 How It Works

The limit is configured via the `MAX_VALID_CONTACTS` env var (default: **500**). It is enforced at two points:

1. **`POST /api/bulk-message/parse`** — The parser uses early-stop optimization: once `MAX_VALID_CONTACTS + 1` valid contacts are collected, parsing stops immediately. If the count exceeds the limit, the endpoint returns HTTP `400` with details.
2. **`POST /api/bulk-message/start`** — A second enforcement check runs on the contacts (whether from cache or freshly parsed). If the count exceeds the limit, the request is rejected before the campaign is created.

### 20.2 Early-Stop Optimization

The `_parse_contacts()` function accepts a `max_contacts` parameter. When set, parsing stops after collecting `max_contacts + 1` rows. This means a 100,000-row file rejects in milliseconds instead of parsing every row:

```
File has 100,000 rows, limit = 500
  → Parser collects 501 valid contacts → stops immediately
  → Returns 400: "exceeds the maximum of 500 per campaign"
  → 99,499 rows never read
```

### 20.3 Parsed Contacts Redis Caching

To avoid parsing the same file twice (once on `/parse`, again on `/start`), the system caches parsed contacts in Redis:

1. `/parse` returns an `upload_id` token (UUID)
2. Contacts are stored in Redis under `parsed_contacts:{upload_id}` with a 10-minute TTL
3. `/start` accepts `upload_id` as a form field — if the cache hit succeeds, parsing is skipped
4. The cache entry is deleted after use (single-use token)
5. If Redis is unavailable or the cache expires, the system falls back to file parsing

### 20.4 Frontend Integration

The frontend calls `GET /api/bulk-message/limits` on page load to fetch the current `max_valid_contacts` value, ensuring the UI stays in sync with the backend configuration without hardcoding limits.

### 20.5 Key Files

| File | Role |
|------|------|
| `routers/bulk_message.py` | `MAX_VALID_CONTACTS` constant, enforcement on `/parse` and `/start`, Redis caching logic |
| `frontend/src/lib/api.ts` | `bulkMessage.limits()` API method |
| `.env.example` | `MAX_VALID_CONTACTS` configuration reference |

---

## 21. Redis Command Optimization

Phase 14 introduced a comprehensive Redis command reduction strategy to minimize costs on managed Redis providers (e.g., Upstash) while maintaining system reliability.

### 21.1 Problem

The original BullMQ worker configuration caused aggressive Redis polling and Lua script execution:
- BullMQ's built-in `limiter` injected ~6 extra Redis Lua commands per job (`moveToActive` path)
- Default `stalledInterval` (30s) caused frequent stalled-job checks (~17 Redis calls/min per worker)
- Default `drainDelay` (5s) caused rapid idle polling
- Redis-backed rate limiters for every worker message check added significant overhead

### 21.2 Changes Made

#### BullMQ Limiter Removal
The BullMQ `limiter` configuration has been **completely removed** from worker options. Rate control is now handled by a simple `asyncio.sleep(_RATE_DELAY_SECONDS)` at the start of each message job. This is safe because the worker processes jobs sequentially (`concurrency=1`).

```python
# Before (Phase 12): BullMQ limiter — ~6 extra Redis Lua calls per job
worker_opts = {
    "limiter": {"max": 80, "duration": 1000},
    ...
}

# After (Phase 14): In-worker sleep — 0 extra Redis calls
_RATE_DELAY_SECONDS = 0.2  # ~5 msg/sec, configurable via WORKER_RATE_DELAY
if _RATE_DELAY_SECONDS > 0:
    await asyncio.sleep(_RATE_DELAY_SECONDS)
```

#### Worker Polling Optimization

| Setting | Before | After | Impact |
|---------|--------|-------|--------|
| `drainDelay` | 5s (default) | 10s (max) | 50% fewer idle polls |
| `stalledInterval` | 30s (default) | 300s (5 min) | ~98% fewer stall checks |
| `lockDuration` | 30s (default) | 300s (5 min) | Matches stalledInterval |
| `maxStalledCount` | 2 (default) | 1 | Minimal stall iterations |

#### In-Memory Caching for Rate Limiters

Two high-frequency Redis checks now use short-lived in-memory caches:

| Check | Cache TTL | Before | After |
|-------|-----------|--------|-------|
| `tenant_token_bucket_consume()` | 5s (allowed) / 1s (denied) | 1 Redis EVAL per message | ~1 per 5 seconds |
| `is_global_cooldown_active()` | 2s | 1 Redis GET per message | ~1 per 2 seconds |

#### Lua-Based Token Bucket for API Rate Limiting

API rate limiting now uses a Lua-based token bucket that executes in a single `EVALSHA` call (with `EVAL` fallback), reducing the per-request Redis overhead from ~5 commands (sorted-set sliding window) to 1 command. Controlled by `USE_TOKEN_BUCKET=true` (default).

### 21.3 Quota Counting Fix (Phase 16)

The quota system now correctly handles retries. Only the **first attempt** for each recipient consumes quota. Retry attempts (`attempt_count > 0`) skip quota consumption entirely, preventing inflated usage counts:

```python
# Worker logic (simplified):
if current_attempts == 0:
    consumed = await try_consume_quota(tenant_id, bulk_quota_limit)
    if not consumed:
        # Mark recipient as quota_exceeded
else:
    # Skip — quota already consumed on first attempt
    log_event("quota_skip_retry", ...)
```

### 21.4 Campaign Counter Accuracy (Phase 15)

Campaign `sent_count` and `failed_count` are now derived from **authoritative recipient table counts** rather than incremental counter shards. The `count_by_status()` method groups recipients into display categories:

| Category | Recipient Statuses |
|----------|--------------------|
| `sent` | submitted, sent, delivered, read |
| `failed` | failed |
| `pending` | pending, queued, processing |
| `quota_exceeded` | quota_exceeded |

This eliminates counter drift issues where incremental counters could fall out of sync with actual recipient statuses.

### 21.5 Key Files

| File | Role |
|------|------|
| `worker_main.py` | `_RATE_DELAY_SECONDS`, removed BullMQ limiter, tuned worker opts, retry-aware quota consume |
| `rate_limit.py` | Lua token bucket, in-memory caches for cooldown and tenant bucket |
| `db_layer/campaign_recipients.py` | `count_by_status()` authoritative counter method |
| `routers/bulk_message.py` | Uses `count_by_status()` for all campaign response payloads |

---

## 22. Chatbot System Redesign (Phase 17)

Phase 17 is a complete redesign of the chatbot automation system, replacing hardcoded static mappings and JSONB columns with a fully dynamic, DB-driven, multi-tenant architecture.

### 22.1 What Changed

| Feature | Before (Pre-Phase 17) | After (Phase 17) |
|---------|----------------------|------------------|
| Button?template mappings | Hardcoded in `webhook.py` Python dicts | DB table `chatbot_button_mappings`, fully per-tenant CRUD |
| Button ID matching | Supported via `button_id_mappings` JSONB | **Removed** � matching is by button text only |
| Keyword rule response type | Text only | `text` or `template` (configurable per rule) |
| Keyword rule match type | Contains only | `exact`, `contains`, or `starts_with` (configurable per rule) |
| Fallback trigger template | Hardcoded `"first_trigger"` string | Configurable `fallback_template_name` per tenant |
| Fallback cooldown | Fixed 24h | Configurable `fallback_cooldown_hours` per tenant (default 24) |
| AI mode | Partially scaffolded | Fully disabled (`use_ai` always `false`); reserved for future |

### 22.2 New Database Tables

#### `chatbot_button_mappings`

Replaces the legacy `button_text_mappings` JSONB column on `chatbot_config`. Each row maps one button text to one template, scoped to a tenant:

```sql
CREATE TABLE chatbot_button_mappings (
  id            BIGSERIAL PRIMARY KEY,
  tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  button_text   TEXT NOT NULL DEFAULT '',
  template_name TEXT NOT NULL,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  priority      INTEGER NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chk_button_mapping_has_trigger CHECK (button_text <> '')
);
-- Unique: one button_text per tenant
CREATE UNIQUE INDEX idx_button_mappings_unique_text
  ON chatbot_button_mappings (tenant_id, button_text) WHERE button_text <> '';
```

#### `chatbot_flows` (Future � schema exists, no API yet)

Placeholder table for a future visual flow builder. Stored in the schema now for forward compatibility. `trigger_type` can be `keyword`, `button`, `first_message`, or `manual`. Not yet exposed via any API endpoint.

### 22.3 Updated Columns on Existing Tables

**`chatbot_config` � two new columns:**

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `fallback_template_name` | TEXT | `''` | Template name to send when no rule matches. Empty string disables the fallback. |
| `fallback_cooldown_hours` | INTEGER | `24` | Hours between fallback sends to the same contact. Prevents spam. |

**`chatbot_rules` � two new columns:**

| Column | Type | Default | Values | Description |
|--------|------|---------|--------|-------------|
| `response_type` | TEXT | `'text'` | `'text'`, `'template'` | Whether to send a plain text reply or a WhatsApp template |
| `match_type` | TEXT | `'contains'` | `'exact'`, `'contains'`, `'starts_with'` | How to match the incoming message against the keyword |

### 22.4 New and Updated Backend Files

| File | Status | Purpose |
|------|--------|---------|
| `backend/db_layer/chatbot_button_mappings.py` | **NEW** | Full CRUD + 1h in-memory cache for `chatbot_button_mappings`. Methods: `list()`, `get_active_maps()`, `create()`, `update()`, `delete()`. Cache key: `button_mappings:{tenant_id}`. |
| `backend/migration_chatbot_redesign.sql` | **NEW** | Idempotent SQL migration for Phase 17 changes. Adds columns, creates tables, migrates JSONB data, sets defaults. |
| `backend/run_migration.py` | **NEW** | Connects via `DATABASE_URL` and runs `migration_chatbot_redesign.sql` transactionally. |
| `backend/routers/chatbot.py` | **UPDATED** | New `ButtonMappingModel` Pydantic model. 4 new button-mapping endpoints. Settings + rules models extended. AI fields disabled. |
| `backend/routers/webhook.py` | **UPDATED** | All three chatbot decision layers now fully dynamic: DB-backed button maps, per-rule `match_type`/`response_type`, configurable fallback cooldown. |
| `backend/db_layer/chatbot.py` | **UPDATED** | `upsert()` handles `fallback_template_name` + `fallback_cooldown_hours`. Rules `create()`/`update()` handle `response_type` + `match_type`. |
| `backend/store.py` | **UPDATED** | Default chatbot dict updated. `save_chatbot_settings()` invalidates button mappings cache. |
| `backend/cache.py` | **UPDATED** | `button_mappings_key()` helper exported. |
| `frontend/src/app/dashboard/chatbot/page.tsx` | **UPDATED** | Button mappings management panel. Fallback template searchable dropdown. |
| `frontend/src/lib/api.ts` | **UPDATED** | `chatbot.buttonMappings.list/create/update/delete()` methods added. |

### 22.5 Webhook Decision Engine (Complete Flow)

```
Incoming WhatsApp message (chatbot.is_enabled = true)
  �
  +-- Layer 1: Button?Template Mappings (highest priority)
  �     text_map = get_active_maps(tenant_id)    # DB query, cached 1h
  �     if message_text.strip() in text_map:
  �         ? enqueue template: text_map[message_text]
  �         ? DONE (layers 2 & 3 skipped)
  �
  +-- Layer 2: Keyword Rules
  �     rules = get_active(tenant_id)            # DB query, cached 6h, priority DESC
  �     for each rule:
  �         match by rule.match_type:
  �           'exact'       ? msg.lower() == keyword
  �           'contains'    ? keyword in msg.lower()
  �           'starts_with' ? msg.lower().startswith(keyword)
  �         if matched:
  �           response_type == 'template' ? enqueue template
  �           response_type == 'text'     ? enqueue text message
  �           ? DONE (layer 3 skipped)
  �
  +-- Layer 3: Fallback Template (lowest priority)
        fallback_tpl   = settings['fallback_template_name']
        cooldown_hours = settings['fallback_cooldown_hours']
        if fallback_tpl is not empty:
            if should_send_trigger(tenant_id, phone, cooldown_hours):
                record_trigger(tenant_id, phone)   # updates user_triggers table
                ? enqueue template: fallback_tpl
```

### 22.6 Cache Invalidation Map

| Write Operation | Caches Invalidated |
|----------------|-------------------|
| `PUT /api/chatbot/settings` | `chatbot_config:{tenant_id}`, `button_mappings:{tenant_id}` |
| `POST /api/chatbot/rules` | `chatbot_rules:{tenant_id}`, `chatbot_rules_active:{tenant_id}` |
| `PUT /api/chatbot/rules/{id}` | All `chatbot_rules:*` and `chatbot_rules_active:*` keys (prefix invalidation) |
| `DELETE /api/chatbot/rules/{id}` | All `chatbot_rules:*` and `chatbot_rules_active:*` keys (prefix invalidation) |
| `POST /api/chatbot/button-mappings` | `button_mappings:{tenant_id}` |
| `PUT /api/chatbot/button-mappings/{id}` | `button_mappings:{tenant_id}` |
| `DELETE /api/chatbot/button-mappings/{id}` | `button_mappings:{tenant_id}` |

### 22.7 Migration Runbook

**Fresh installation (new database):**
```bash
# schema.sql already includes all Phase 17 tables:
python backend/apply_schema.py
```

**Existing deployment (upgrading from pre-Phase 17):**
```bash
# Step 1: Apply base schema (idempotent)
python backend/apply_schema.py

# Step 2: Run Phase 17 migration
python backend/run_migration.py
```

The migration uses `ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`, and `ON CONFLICT DO NOTHING` throughout � safe to run multiple times.

### 22.8 Developer Gotchas

| Gotcha | Explanation |
|--------|-------------|
| **`column does not exist` on startup** | Phase 17 migration not applied. Run `python backend/run_migration.py`. |
| **Button text is case-sensitive** | `"Book Session"` ? `"book session"`. Match the exact text WhatsApp sends in the button payload. |
| **Button ID no longer works** | `button_id` matching was removed entirely. Only `button_text` exact match is supported. |
| **`use_ai=true` has no effect** | AI mode is disabled. `true` is accepted but stored as `false`. |
| **Fallback only fires once per cooldown window** | Each phone number has its own cooldown timer in `user_triggers`. One fallback per contact per N hours. |
| **Button mappings are tenant-scoped** | Each tenant manages their own set of button?template mappings independently. |
| **Stale button map cache after direct DB edit** | API writes invalidate the cache immediately. Direct DB edits require a server restart or waiting up to 1 hour for cache expiry. |