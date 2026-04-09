# WappFlow — Architecture Overview

> **Product:** Multi-tenant WhatsApp Business Automation SaaS  
> **Version:** 3.0.0 (Phase 16 — Contact Limits, Redis Optimization, Counter Accuracy & Quota Fix)  
> **Last Updated:** 2026-04-09

> **📖 How to Use This Document:** If you are new to the team, start with [Section 21 (Onboarding Guide)](#21-onboarding-guide-for-new-developers) for a structured path. Then dive into specific sections as needed. For API endpoint details, see [API_Developer_Doc.md](API_Developer_Doc.md).

---

## 1. What is WappFlow?

WappFlow is a **multi-tenant SaaS platform** that automates WhatsApp Business messaging. It provides three core products:

1. **Bulk Messaging** — Send template-based WhatsApp messages to thousands of contacts via Excel/CSV upload. Supports scheduled campaigns, real-time progress tracking, automatic retries, resend-failed workflows, and **per-tenant monthly message quotas** with atomic enforcement at both API and worker levels.
2. **File Forwarding** — Send documents, images, and PDFs to single or multiple recipients via the WhatsApp Cloud API.
3. **Auto-Reply Chatbot** — Keyword-based rule engine that automatically responds to incoming WhatsApp messages. Supports configurable button→template mappings and a first-trigger fallback system.

Every tenant is fully isolated: separate WhatsApp credentials, separate webhook URLs with per-tenant HMAC signature verification, and secrets encrypted at rest via Fernet.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            FRONTEND                                     │
│                     Next.js (React) + Tailwind                          │
│                     Firebase Auth (client-side)                          │
│                     Deployed on Vercel                                   │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ HTTPS (Bearer token)
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          FASTAPI SERVER                                  │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │ CORS         │  │ Firebase Auth    │  │ Rate Limit Middleware    │  │
│  │ Middleware    │→ │ Middleware       │→ │ (Redis sliding window)   │  │
│  └──────────────┘  └──────────────────┘  └──────────────────────────┘  │
│                                                                          │
│  ┌─────────────┐ ┌──────────────┐ ┌────────────┐ ┌──────────────────┐  │
│  │ /settings   │ │ /bulk-message│ │ /file-fwd  │ │ /chatbot         │  │
│  └─────────────┘ └──────────────┘ └────────────┘ └──────────────────┘  │
│  ┌─────────────┐ ┌──────────────────────────────────────────────────┐  │
│  │ /logs       │ │ /webhook/{tenant_id}  ← Meta WhatsApp            │  │
│  └─────────────┘ │   • HMAC-SHA256 signature verification            │  │
│                   │   • Per-tenant meta_app_secret (Fernet encrypted) │  │
│                   └──────────────────────────────────────────────────┘  │
│                                                                          │
│  Encryption Layer: db_layer/encryption.py (Fernet AES-128-CBC)          │
│  Background Tasks:                                                      │
│    TTL Cleanup (6h) · Campaign Scheduler (60s) · Retention Cron (24h)   │
└──────────┬──────────────────────────────┬───────────────────────────────┘
           │                              │
           ▼                              ▼
┌─────────────────────┐       ┌──────────────────────────────────────────┐
│    REDIS (TLS)      │       │          NEON POSTGRES                    │
│                     │       │                                           │
│  • BullMQ Queues    │       │  tenants · campaigns · campaign_recipients│
│    - campaign_queue │       │  messages · chat_messages · chatbot_rules │
│    - message_queue  │       │  webhook_events · usage_events            │
│    - dead_letter_q  │       │  template_cache · user_triggers           │
│  • Rate limit keys  │       │  chatbot_config · campaign_counters       │
│  • Token buckets    │       │  messages_archive · chat_messages_archive │
│  • Global cooldown  │       │  webhook_events_archive · usage_events_ar │
│                     │       │  daily_message_stats                      │
│                     │       │  tenant_quota_usage                       │
│                     │       │                                           │
│                     │       │  Encrypted columns: meta_app_secret       │
│                     │       └──────────────────────────────────────────┘
└──────────┬──────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       BULLMQ WORKER (worker_main.py)                     │
│                                                                          │
│  campaign_worker ──→ Reads pending recipients, fans out to message_queue│
│                      Caps fan-out to remaining monthly quota             │
│  message_worker  ──→ Sends via WhatsApp Cloud API (rate-limited)         │
│                      Atomically consumes quota before each send          │
│                                                                          │
│  Rate Controls:                                                          │
│    • In-worker asyncio.sleep throttle (WORKER_RATE_DELAY, default 200ms) │
│    • Tenant token bucket (10 msg/sec per tenant, burst 20)               │
│    • Global cooldown on repeated 429s from WhatsApp                      │
│    • Exponential backoff + jitter on retries                             │
│    • Monthly quota enforcement (atomic per-message consumption)           │
│    • Retry-aware quota: only first attempt consumes quota                │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
                  ┌───────────────────────┐
                  │  WhatsApp Cloud API   │
                  │  (Meta Graph API v18) │
                  └───────────────────────┘
```

---

## 3. Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Frontend** | Next.js 14, React, TypeScript | SPA with server-side rendering |
| **UI** | Tailwind CSS, shadcn/ui, Lucide icons | Component library & styling |
| **Auth** | Firebase Auth (client + server) | Google sign-in, email/password, ID token verification |
| **Backend** | FastAPI (Python 3.11+) | Async REST API with Pydantic validation |
| **Database** | Neon Postgres (serverless) | Multi-tenant data store via `psycopg` + connection pooling |
| **Queue** | BullMQ (Redis-backed) | Async job processing for message sending. Supports TLS via `rediss://` URL scheme for cloud Redis (e.g. Upstash) |
| **Cache** | In-memory (Python) + Redis | TTL-based settings/rules cache (6h) + rate-limit state |
| **External API** | WhatsApp Cloud API (Meta Graph v18.0) | Message sending, template management, media uploads |
| **Deployment** | Vercel (frontend), Render (backend), Docker Compose (local) | CI/CD and containerization |

---

## 4. Multi-Tenancy Model

- **Tenant = Firebase UID.** Each authenticated user gets a unique `tenant_id` derived from their Firebase Auth UID.
- **Row-level isolation.** Every database table includes a `tenant_id` column. All queries are scoped by tenant.
- **Middleware enforcement.** The `FirebaseAuthMiddleware` extracts and validates the token on every request, setting `request.state.tenant_id`. No route can access another tenant's data.
- **Cache isolation.** All cache keys are prefixed with `tenant_id`. Template component cache is tenant-scoped to prevent cross-tenant data leakage.
- **Per-tenant webhook URLs.** Each tenant receives a dedicated webhook endpoint (`/api/webhook/{tenant_id}`). Meta sends incoming messages and delivery status updates directly to this URL. The `meta_app_secret` used for HMAC signature verification is stored per-tenant (encrypted at rest).
- **Secrets encrypted at rest.** Sensitive fields like `meta_app_secret` are Fernet-encrypted before being stored in Postgres. The encryption layer (`db_layer/encryption.py`) uses an `"enc:"` prefix to distinguish encrypted values from legacy plain-text values, enabling zero-downtime migration.

---

## 5. Request Lifecycle

### Authenticated Request (e.g., Start Campaign)

```
1. Browser sends POST /api/bulk-message/start
   └─ Authorization: Bearer <firebase_id_token>
2. CORSMiddleware → validates origin
3. FirebaseAuthMiddleware → verifies token, sets tenant_id
4. RateLimitMiddleware → checks Redis sliding window (10 req/min for heavy endpoints)
5. Route handler:
   a. Reads tenant settings from cache/DB
   b. Contact limit check: rejects if > MAX_VALID_CONTACTS (default 500)
   c. Attempts to reuse cached contacts from /parse via upload_id (Redis)
   d. If cache miss: parses uploaded file → extracts contacts
   e. Quota pre-check: if contacts > remaining monthly quota → 429 rejected
   f. Creates campaign + recipients + counters in DB (single transaction)
   g. Enqueues campaign job to BullMQ campaign_queue
   h. Returns { campaignId, totalContacts, status }
6. Worker picks up campaign job:
   a. Reads remaining monthly quota for tenant
   b. Caps fan-out to min(pending_recipients, quota_remaining)
   c. Marks excess recipients as "quota_exceeded"
   d. Fans out capped message jobs to message_queue
7. Message worker processes each job:
   a. Atomically consumes 1 from monthly quota (first attempt only)
      └─ Retries (attempt_count > 0) skip quota — already consumed
      └─ If quota exhausted → mark recipient "quota_exceeded", skip send
   b. Checks global cooldown & tenant token bucket
   c. Resolves template, builds components, validates params
   d. Calls WhatsApp Cloud API
   e. Updates recipient status in DB (transaction)
   f. Status/list/details endpoints use count_by_status() for accurate counts
   g. On final recipient, marks campaign as "completed"
```

### Webhook (Incoming WhatsApp Message — Per-Tenant Route)

```
1. Meta sends POST /api/webhook/{tenant_id}
   └─ X-Hub-Signature-256: sha256=<hmac>
2. No Firebase auth middleware (public route)
3. Look up tenant from URL path {tenant_id}
4. Decrypt per-tenant meta_app_secret (Fernet)
5. Verify X-Hub-Signature-256 using HMAC-SHA256 (constant-time)
   └─ If invalid → 401 rejected, payload never parsed
6. Parse JSON body
7. Deduplicate via webhook_events table
8. Handle delivery status updates:
   a. Update message status in messages table
   b. Fallback lookup in messages_archive for recently-archived messages
   c. Trigger campaign finalization / retry logic
9. Handle incoming messages:
   a. Store in chat_messages + messages
   b. Chatbot decision engine:
      i.  Check button→template mappings (per-tenant, cached 1h)
      ii. Check keyword rules (DB-backed)
      iii. Fallback: first_trigger (24h rate-limited per sender)
   c. Enqueue reply to message_queue (priority 0 = highest)
10. Return { status: "ok", tenant_id: "..." }
```

> **Legacy route** (`POST /api/webhook`) is deprecated. It has no signature verification and resolves tenants from payload metadata. Will be removed in a future release.

---

## 6. Data Flow: Campaign Lifecycle

```
  [User uploads CSV + selects template]
               │
               ▼
  ┌─── API: /bulk-message/start ───┐
  │  Parse file → deduplicate       │
  │  Quota pre-check:               │
  │    contacts > remaining → 429   │
  │  Create campaign row (status:   │
  │    running or scheduled)        │
  │  Insert recipients (pending)    │
  │  Init counter shards            │
  │  Enqueue → campaign_queue       │
  └────────────┬───────────────────┘
               │
               ▼
  ┌─── campaign_worker ────────────┐
  │  Read quota remaining for tenant│
  │  Cap = min(pending, remaining)  │
  │  Excess → quota_exceeded        │
  │  For each (up to cap):          │
  │    enqueue → message_queue      │
  │  Transition: pending → queued   │
  └────────────┬───────────────────┘
               │
               ▼
  ┌─── message_worker ─────────────┐
  │  Atomic quota consume (1 unit)  │
  │    └─ Exhausted → quota_exceeded│
  │  Token bucket check             │
  │  Global cooldown check          │
  │  Transition: queued → processing│
  │  Build template components      │
  │  Call WhatsApp API              │
  │  On success:                    │
  │    processing → submitted       │
  │    Increment sent counter       │
  │    Record in messages table     │
  │  On failure:                    │
  │    processing → queued (retry)  │
  │    or → failed (max attempts)   │
  └────────────┬───────────────────┘
               │
               ▼
  ┌─── Webhook: delivery status ───┐
  │  delivered/read → mark sent     │
  │  failed → retry or mark failed  │
  │  Check if all done →            │
  │    campaign → completed         │
  └────────────────────────────────┘
```

### Recipient Status Machine

```
pending → queued → processing → submitted → sent (via webhook delivered/read)
    │                  │                        │
    │                  └──→ queued (retry) ◄────┘ (webhook failed, attempts < max)
    │                  │
    │                  └──→ failed (max attempts exhausted)
    │
    └──→ quota_exceeded (monthly tenant quota exhausted — terminal, no retry)
```

> **Note:** `quota_exceeded` is a terminal status. Recipients with this status are counted as "done" for campaign finalization purposes, ensuring campaigns complete cleanly even when quota is hit mid-run.

---

## 7. Backend Directory Structure

```
backend/
├── main.py                  # FastAPI app, lifespan, middleware, router registration
├── worker_main.py           # BullMQ workers (campaign + message processing)
├── run_server.py            # Uvicorn launcher
├── database.py              # Async Postgres pool (psycopg3), transaction helper
├── schema.sql               # Complete DDL for all live tables
├── retention_schema.sql     # DDL for archive tables + daily_message_stats
├── apply_schema.py          # Applies retention_schema.sql to database
├── retention.py             # Data retention engine (archive + purge) + CLI
│
├── auth_middleware.py        # Firebase Auth token verification middleware
├── rate_limit.py             # Redis rate limiter (API + worker token bucket)
├── cache.py                  # In-memory TTL cache (6h default)
├── store.py                  # Cached read/write layer for settings + chatbot config
├── observability.py          # Structured JSON logging (no sensitive data)
├── startup_checks.py         # Environment validation on boot
├── startup_cache.py          # Pre-warm caches on startup
├── firebase_config.py        # Firebase Admin SDK initialization
│
├── routers/
│   ├── settings.py           # WhatsApp API credentials CRUD
│   ├── bulk_message.py       # Campaign lifecycle (start/stop/status/delete)
│   ├── file_forward.py       # Single + bulk file sending
│   ├── chatbot.py            # Rules, settings, conversations
│   ├── logs.py               # Message log retrieval + CSV export
│   └── webhook.py            # Per-tenant + legacy webhook routes, HMAC sig verification
│
├── services/
│   ├── whatsapp.py           # WhatsApp Cloud API client (retry, rate-limit aware)
│   ├── queue_manager.py      # BullMQ queue helpers (campaign/message/file-forward/DLQ)
│   ├── template_builder.py   # Template component cache + parameter builder
│   └── chatgpt.py            # (Reserved) ChatGPT integration
│
├── db_layer/
│   ├── tenants.py            # Tenant CRUD + lookup by phone_number_id + webhook_verify_token
│   ├── campaigns.py          # Campaign CRUD + status transitions
│   ├── campaign_recipients.py# Recipient status machine (pending→queued→sent/failed/quota_exceeded)
│   ├── campaign_counters.py  # Sharded sent/failed counters
│   ├── messages.py           # Unified message log (all products) + archive fallback
│   ├── chat_messages.py      # Conversation history
│   ├── chatbot.py            # Chatbot config + rules + button mappings
│   ├── webhook_events.py     # Webhook deduplication
│   ├── usage_events.py       # Billable usage tracking
│   ├── template_cache.py     # Persistent template metadata
│   ├── secrets.py            # Runtime token resolution (DB → env fallback)
│   ├── encryption.py         # Fernet encrypt/decrypt for secrets at rest
│   ├── users.py              # User trigger rate limiting (24h)
│   └── quota.py              # Per-tenant monthly bulk message quota (read + atomic consume)
│
├── utils/
│   ├── phone_utils.py        # E.164 phone normalization (international + India fallback + scientific notation)
│   ├── image_utils.py        # Automatic image compression to ≤5 MB (Pillow: JPEG quality reduction + resize)
│   └── time_utils.py         # IST timestamp helpers
```

---

## 8. Frontend Structure

```
frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx                # Root layout with auth provider
│   │   ├── page.tsx                  # Landing / redirect
│   │   ├── login/page.tsx            # Firebase Auth login
│   │   ├── register/page.tsx         # Firebase Auth registration
│   │   └── dashboard/
│   │       ├── layout.tsx            # Dashboard shell (sidebar nav)
│   │       ├── page.tsx              # Dashboard home (product cards + usage stats)
│   │       ├── settings/             # WhatsApp API configuration
│   │       ├── bulk-message/         # Campaign management UI
│   │       ├── file-forward/         # File sending UI
│   │       ├── chatbot/              # Rules + conversation viewer
│   │       └── logs/                 # Message log viewer + export
│   ├── components/                   # Reusable UI components (shadcn/ui based)
│   ├── contexts/                     # React contexts (auth)
│   ├── hooks/                        # Custom React hooks
│   └── lib/
│       ├── api.ts                    # Axios client with Firebase auth interceptor
│       ├── firebase.ts               # Firebase client initialization
│       └── utils.ts                  # Shared utilities
├── next.config.js
├── tailwind.config.ts
└── package.json
```

---

## 9. Security Architecture

| Layer | Mechanism |
|-------|-----------|
| **Authentication** | Firebase Auth ID tokens verified server-side on every request |
| **Authorization** | Tenant isolation — all DB queries scoped by `tenant_id` from token |
| **Webhook integrity** | Per-tenant `X-Hub-Signature-256` HMAC-SHA256 verification using each tenant's own `meta_app_secret`. Constant-time comparison via `hmac.compare_digest()`. Raw body read **before** JSON parsing. |
| **Encryption at rest** | Sensitive secrets (`meta_app_secret`) encrypted via Fernet (AES-128-CBC) before storage in Postgres. `"enc:"` prefix distinguishes encrypted vs plain-text values. Backward-compatible with pre-encryption data. Requires `ENCRYPTION_KEY` env var. |
| **Token storage** | WhatsApp access tokens stored in Postgres, resolved at runtime via `db_layer/secrets.py`. Never returned to frontend, never logged — API returns only `has_access_token: true/false`. |
| **Rate limiting** | Redis sliding-window per tenant; heavy tier (write actions only) + general tier (reads/polls) + worker token bucket |
| **Input validation** | Pydantic models on all request bodies; alphanumeric validation on IDs |
| **CSV injection** | Export values sanitized against DDE formula injection |
| **File upload** | 16 MB hard limit enforced server-side |
| **CORS** | Explicit origin allowlist; `localhost:3000` only in non-production |
| **Docs** | OpenAPI/Swagger disabled in production |
| **Per-tenant webhooks** | Each tenant has a dedicated webhook URL (`/api/webhook/{tenant_id}`), eliminating the need for payload-based tenant resolution and enabling per-tenant signature verification. Legacy shared routes are deprecated. |

---

## 10. Queue Architecture (BullMQ)

Three Redis-backed queues handle all asynchronous work:

| Queue | Job Type | Purpose |
|-------|----------|---------|
| `campaign_queue` | `process_campaign` | Reads recipients from DB, fans out to `message_queue` |
| `message_queue` | `send_message` | Sends individual messages via WhatsApp API |
| `dead_letter_queue` | `permanently_failed` | Stores jobs that exhausted all retries |

### Rate Limiting Stack (layered)

```
API tier:
  Heavy  (write actions: POST /start, POST /send, DELETE, etc.) → 10 req/min per tenant
  General (read/poll: GET /status, GET /details, GET /logs)     → 300 req/min per tenant

Worker tier:
  Layer 1: BullMQ limiter         → 80 jobs/sec global across all tenants
  Layer 2: Tenant token bucket    → 10 msg/sec per tenant (burst 20)
  Layer 3: Global cooldown        → All workers pause on repeated WhatsApp 429s
  Layer 4: WhatsApp retry         → Exponential backoff + jitter + Retry-After support
```

The API heavy tier matches on **(HTTP method + path)**, not just path prefix. This ensures frontend polling (`GET` requests every 3–10s) never triggers 429 errors, while user-submit actions like starting campaigns are properly throttled.

### Retry Policy

- **Attempts:** Configurable via `QUEUE_RETRY_ATTEMPTS` (default 3)
- **Backoff:** Exponential starting at 5s (5s → 10s → 20s → 40s)
- **Non-retryable errors** (e.g., template not found): Immediately marked as failed
- **Terminal failure:** Moved to dead letter queue; recipient marked as `failed`

---

## 11. Caching Strategy

| Cache | Type | TTL | Purpose |
|-------|------|-----|---------|
| Tenant settings | In-memory | 6 hours | Avoid DB reads on every request |
| Chatbot config | In-memory | 6 hours | Chatbot enabled/disabled state |
| Chatbot rules | In-memory | 6 hours | Keyword matching rules |
| Chat users list | In-memory | 15 seconds | Reduce polling load on conversations page |
| Template components | In-memory + Postgres | Indefinite (memory) / persistent (DB) | Template parameter metadata (tenant-scoped key: `{tenant_id}:{name}|{lang}`) |
| Uploaded media IDs | In-memory | Process lifetime | Avoid re-uploading same template header image on every webhook trigger. Invalidated on `#132012` errors. |
| Button mappings | In-memory | 1 hour | Per-tenant button→template mappings |
| Rate limit counters | Redis | Sliding window | API and worker rate limiting |
| Token buckets | Redis | 60s auto-expire | Per-tenant message sending fairness |

Cache invalidation: Write operations explicitly call `cache.invalidate()` for affected keys.

---

## 12. Database Schema (ER Summary)

```
tenants (1)  ← includes bulk_quota_limit (default 100)
  ├──< chatbot_config (1:1)
  ├──< chatbot_rules (1:N)
  ├──< campaigns (1:N)
  │      └──< campaign_recipients (1:N)
  ├──< messages (1:N)  ───archive───>  messages_archive
  ├──< chat_messages (1:N)  ───────>  chat_messages_archive
  ├──< webhook_events (1:N)  ──────>  webhook_events_archive
  ├──< usage_events (1:N)  ────────>  usage_events_archive
  ├──< template_cache (1:N)
  ├──< user_triggers (1:N)
  └──< tenant_quota_usage (1:N per month_key)

daily_message_stats (pre-aggregated from messages before archival)
```

All tables use `tenant_id` as a foreign key to `tenants`. Campaigns use a composite primary key `(tenant_id, campaign_id)` for efficient tenant-scoped queries. The `tenant_quota_usage` table uses a composite primary key `(tenant_id, month_key)` to track per-month quota consumption.

Archive tables mirror the schema of their source tables with an additional `archived_at TIMESTAMPTZ` column. The `daily_message_stats` table stores pre-aggregated message counts per tenant/day/product/direction/status, ensuring dashboard analytics remain accurate after messages are archived.

---

## 13. Deployment Topology

### Production

```
Vercel ──────────────── Next.js frontend (CDN + SSR)
  │
  │  HTTPS
  ▼
Render ──────────────── FastAPI (api service, port 5000)
  │                     BullMQ Worker (worker_main.py)
  │
  ├──→ Neon Postgres ── Serverless Postgres (connection pooling)
  └──→ Redis (managed)─ BullMQ queues + rate limiting (TLS via `rediss://`)
```

### Local Development (Docker Compose)

```
docker-compose.yml defines:
  • redis (6.2-alpine, port 6379, AOF persistence, no TLS locally)
  • api (FastAPI, port 5000, depends on redis)
  • worker (worker_main.py, depends on redis)
```

Frontend runs separately via `npm run dev` on port 3000.

---

## 14. Background Tasks

| Task | Interval | Purpose |
|------|----------|---------|
| `periodical_cleanup` | Every 6 hours | Deletes transient data older than 30 days (chat_messages, messages, webhook_events, usage_events). Never touches tenant/config data. |
| `periodical_scheduler` | Every 60 seconds | Checks for scheduled campaigns past their `scheduled_at` time, transitions them to `queued`, and enqueues to `campaign_queue`. |
| `periodic_archive_runner` | Every 24 hours (configurable) | Runs the data retention pipeline: (1) pre-aggregate `daily_message_stats`, (2) archive old rows from live tables to `*_archive` tables, (3) optionally purge very old archive rows. Controlled by `RETENTION_ENABLED` and `PURGE_ENABLED` env vars. |

All three tasks run as `asyncio.Task` instances within the FastAPI lifespan and are gracefully cancelled on shutdown.

---

## 15. Graceful Shutdown Sequence

1. Signal all `active_campaigns` to stop (`running = False`)
2. Cancel background tasks (cleanup + scheduler + retention cron)
3. Mark any still-running campaigns as `interrupted` in the database
4. Close Redis connection
5. Close Postgres connection pool

The retention cron task handles `CancelledError` gracefully — if an archive batch is in-progress, the current transaction rolls back automatically (no partial data movement). This ensures no campaigns or data operations are silently lost during deployments or restarts.

---

## 16. Observability

- **Structured JSON logging** via `observability.log_event()` — every log line includes `timestamp`, `level`, `op`, and optional `tenant`, `campaign`, `phone`, `ms`, `detail`.
- **No sensitive data logged** — tokens, keys, and message bodies are never included.
- **Timed operations** — `timed_op()` context manager automatically logs duration for critical paths.
- **Log levels:** `INFO` (normal ops), `WARN` (rate limits, missing config), `ERROR` (failures).
- **Retention observability** — the archive and purge systems emit dedicated log events (`retention_start`, `retention_batch`, `retention_complete`, `purge_started`, `purge_batch`, `purge_completed`, etc.) with per-batch row counts, durations, and error details. See the [API Developer Doc Section 14.8](API_Developer_Doc.md#148-monitoring--log-events) for the full event catalog.
- **Webhook observability** — per-tenant webhook processing emits `webhook_per_tenant`, `webhook_sig_rejected`, `webhook_verify_tenant`, `button_match`, `button_id_match`, `fallback_trigger` events.
- **Phone normalization observability** — `worker_main.py` logs every normalization: `raw=<input> normalized=<output>` at `INFO`. Invalid numbers are logged at `WARN` and the recipient is immediately marked `failed`.
- **Image compression observability** — `utils/image_utils.py` emits structured events for every compression decision: `image_compression_skipped`, `image_compression_start`, `image_compressed` (with original/final size and quality), `image_compression_png_fallback`, `image_compression_resize`, `image_compression_failed`.
- **Template payload observability** — `template_payload_built` is logged (at `INFO`) with the full `components` array before every WhatsApp template send. `template_validation_failed` is logged (at `ERROR`) when pre-send validation detects parameter mismatches.
- **Media upload observability** — `upload_media_start`, `upload_media_response`, `upload_media_success`, `upload_media_failed` events are emitted with MIME type, file size, and API response body.

---

## 17. Data Retention Architecture

The data retention system is a critical part of WappFlow's production infrastructure. It manages the lifecycle of the four high-volume transient tables.

### 17.1 Data Flow

```
                    RETENTION_DAYS (default 2)
                           │
  Live Tables              │              Archive Tables          PURGE_RETENTION_DAYS (90)
  ┌──────────────┐         ▼              ┌────────────────────┐         │
  │ messages     │ ──── archive ────────> │ messages_archive   │ ── purge ──> Deleted
  │ chat_messages│ ──── archive ────────> │ chat_messages_arch │ ── purge ──> Deleted
  │ webhook_evts │ ──── archive ────────> │ webhook_evts_arch  │ ── purge ──> Deleted
  │ usage_events │ ──── archive ────────> │ usage_events_arch  │ ── purge ──> Deleted
  └──────────────┘                        └────────────────────┘
         │
         │ pre-aggregate (BEFORE archive)
         ▼
  ┌─────────────────────┐
  │ daily_message_stats │  ← permanent, powers GET /api/settings/usage
  └─────────────────────┘
```

### 17.2 Key Files

| File | Role |
|------|------|
| `retention_schema.sql` | DDL for archive tables + `daily_message_stats` |
| `retention.py` | Archive engine (`archive_old_data()`) + purge engine (`purge_old_archives()`) + CLI |
| `main.py` | `periodic_archive_runner()` background task + health check integration |
| `db_layer/messages.py` | `get_outgoing_by_wa_message_id_archived()` fallback + `get_usage()` reads from `daily_message_stats` |
| `routers/webhook.py` | Fallback archive lookup for delivery status updates on archived messages |

### 17.3 Safety Guarantees

| Property | How |
|----------|-----|
| **No data loss** | Archive uses INSERT → DELETE in a single transaction. Crash = rollback = rows stay in live table. |
| **Idempotent** | `ON CONFLICT DO NOTHING` on archive insert. Safe to re-run any number of times. |
| **No lock contention** | `FOR UPDATE SKIP LOCKED` — archive skips any rows currently locked by webhook handlers or workers. |
| **No API impact** | Runs as a background asyncio task with 50ms sleep between batches. Event loop is never starved. |
| **Dashboard accuracy** | `daily_message_stats` aggregated with `GREATEST()` BEFORE any deletes. Counts only go up. |
| **Webhook continuity** | `messages_archive` fallback lookup in webhook handler ensures delivery callbacks work for recently-archived messages. |
| **Controlled blast radius** | Max 1000 rows/batch × 100 batches = 100k rows/table/run. Configurable via env vars. |
| **Kill switch** | `RETENTION_ENABLED=false` and `PURGE_ENABLED=false` — instant disable, checked every cycle. |

### 17.4 Configuration Reference

**Archive (live → archive):**

| Variable | Default | Description |
|----------|---------|-------------|
| `RETENTION_ENABLED` | `false` | Master switch for background automation |
| `RETENTION_INTERVAL_HOURS` | `24` | Hours between runs |
| `RETENTION_TIMEOUT_HOURS` | `1` | Max run duration |
| `RETENTION_DAYS` | `2` | Archive rows older than N days |
| `RETENTION_BATCH_SIZE` | `1000` | Rows per batch |
| `RETENTION_MAX_BATCHES` | `100` | Max batches per table |

**Purge (archive → deleted):**

| Variable | Default | Description |
|----------|---------|-------------|
| `PURGE_ENABLED` | `false` | Master switch for purge |
| `PURGE_RETENTION_DAYS` | `90` | Delete archive rows older than N days |
| `PURGE_BATCH_SIZE` | `1000` | Rows per batch |
| `PURGE_MAX_BATCHES` | `50` | Max batches per table |

### 17.5 Operational Runbook

**Enable retention in production:**
```env
RETENTION_ENABLED=true
RETENTION_DAYS=2
RETENTION_INTERVAL_HOURS=24
```

**Enable purge (after retention has been running for 90+ days):**
```env
PURGE_ENABLED=true
PURGE_RETENTION_DAYS=90
```

**Run a manual archive (one-off, without enabling the cron):**
```bash
cd backend && python retention.py
```

**Run a manual purge:**
```bash
cd backend && python retention.py --purge-only
```

**Safe smoke test (10 rows only):**
```bash
RETENTION_BATCH_SIZE=10 RETENTION_MAX_BATCHES=1 python retention.py
```

**Check system status:**
```bash
curl http://localhost:5000/api/health | python -m json.tool
# Look at checks.retention and checks.purge
```

**Emergency disable:**
```env
RETENTION_ENABLED=false
PURGE_ENABLED=false
```
Restart the server. Next cycle logs `retention_skipped` / `purge_skipped`.

---

## 18. Encryption at Rest Architecture

WappFlow encrypts sensitive tenant secrets before storing them in Postgres. This prevents credential exposure if the database is compromised.

### 18.1 How It Works

```
  Save: plain text → encrypt_secret() → "enc:<Fernet ciphertext>" → Postgres
  Read: Postgres → "enc:<ciphertext>" → decrypt_secret() → plain text

  Legacy (pre-encryption): Postgres → "raw_plain_text" → returned as-is
```

### 18.2 Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Fernet (AES-128-CBC)** | Industry-standard symmetric encryption from Python's `cryptography` library. Single key, no key rotation complexity. |
| **`"enc:"` prefix** | Distinguishes encrypted values from pre-encryption plain text. Enables zero-downtime migration — old values work, new values are encrypted. |
| **Lazy initialization** | The Fernet cipher is created on first use, not at import time. If `ENCRYPTION_KEY` is missing, a warning is logged and values are stored in plain text. |
| **No key rotation (yet)** | Current design uses a single key. Future enhancement: support `ENCRYPTION_KEY_V2` with automatic re-encryption on read. |

### 18.3 Files

| File | Role |
|------|------|
| `db_layer/encryption.py` | `encrypt_secret(plain) → str`, `decrypt_secret(stored) → str` |
| `db_layer/secrets.py` | `secrets.resolve_wa_token(tenant_doc)` — resolves WhatsApp access token from DB or env fallback |
| `store.py` | Calls `encrypt_secret()` when saving `meta_app_secret` via `save_settings()` |
| `routers/webhook.py` | Calls `decrypt_secret()` when verifying webhook signatures |
| `routers/settings.py` | Returns `has_meta_app_secret: bool` — never exposes the actual secret |

---

## 19. Per-Tenant Webhook Architecture

Each tenant gets a dedicated webhook URL, replacing the legacy shared endpoint. This is a critical security improvement.

### 19.1 Route Mapping

| Route | Purpose | Security |
|-------|---------|----------|
| `GET /api/webhook/{tenant_id}` | Meta handshake verification | Compares `hub.verify_token` against tenant's stored `webhook_verify_token` (constant-time) |
| `POST /api/webhook/{tenant_id}` | Incoming messages + delivery status | Full HMAC-SHA256 signature verification using per-tenant encrypted `meta_app_secret` |
| `GET /api/webhook` | **Deprecated** legacy verification | Checks env var or scans all tenants |
| `POST /api/webhook` | **Deprecated** legacy incoming | No signature verification; resolves tenant from payload |

### 19.2 Signature Verification Flow

```
POST /api/webhook/{tenant_id}
  │
  ├─ 1. Read raw bytes (before any JSON parsing)
  ├─ 2. Look up tenant row from Postgres
  ├─ 3. Decrypt meta_app_secret: decrypt_secret("enc:...") → plain secret
  ├─ 4. Compute: HMAC-SHA256(secret, raw_body)
  ├─ 5. Compare with X-Hub-Signature-256 header (hmac.compare_digest)
  │     └─ Mismatch → 401 "Invalid signature" (body never parsed)
  └─ 6. Parse JSON and process payload
```

### 19.3 Chatbot Decision Engine

When an incoming message is received, the chatbot processes it through three layers:

```
Incoming message
  │
  ├─ Layer 1: Button→Template Mappings (per-tenant, cached 1h)
  │    Text match: e.g. "Sessions" → session_template
  │    Button ID match: e.g. "morning_session" → aruna_yoga
  │
  ├─ Layer 2: Keyword Rules (DB-backed, priority-ordered)
  │    Contains-match: e.g. "pricing" in message → custom response text
  │
  └─ Layer 3: First-Trigger Fallback (24h rate-limited per sender)
       If no rule matched and sender hasn't been triggered in 24h
       → Send "first_trigger" template
```

All replies are enqueued to `message_queue` with **priority 0** (highest), ensuring chatbot responses are never delayed by bulk campaign traffic.

---

## 20. Bulk Message Quota Architecture

WappFlow enforces a **per-tenant monthly quota** on bulk messages. This prevents runaway costs, abuse, and unbounded load. The quota system has three enforcement layers.

### 20.1 Data Model

**`tenants.bulk_quota_limit`** — integer column (default `100`). Configurable per tenant. Represents the maximum number of bulk messages a tenant can send per calendar month.

**`tenant_quota_usage`** — tracks actual consumption:

| Column | Type | Description |
|--------|------|-------------|
| `tenant_id` | TEXT (PK) | Foreign key to `tenants` |
| `month_key` | TEXT (PK) | Format `YYYY-MM` (e.g., `2026-03`) |
| `messages_sent` | INTEGER | Running count of messages consumed this month |
| `last_updated_at` | TIMESTAMPTZ | Last increment timestamp |

Composite primary key `(tenant_id, month_key)` means a new row is automatically created for each month. Old months are never deleted — they serve as an audit trail.

### 20.2 Three-Layer Enforcement

```
Layer 1: API Pre-Check (POST /api/bulk-message/start)
  │  Read quota → if contacts > remaining → 429 rejected (campaign never created)
  │
Layer 2: Campaign Worker (campaign fan-out)
  │  Read quota → cap = min(pending_recipients, remaining)
  │  Excess recipients → marked "quota_exceeded" immediately
  │  Only enqueue up to cap
  │
Layer 3: Message Worker (per-message send)
  │  Atomic consume: INSERT ... ON CONFLICT DO UPDATE ... WHERE sent < limit
  │  If RETURNING is empty → quota exhausted → mark "quota_exceeded", skip send
  │  This is the final authority — even if Layer 1 or 2 passed stale data
```

**Why three layers?**
- Layer 1 gives fast user feedback (no wasted processing)
- Layer 2 prevents enqueueing jobs that will definitely fail
- Layer 3 handles races between concurrent campaigns — it's the only layer that's truly atomic

### 20.3 Atomic Quota Consumption

The `try_consume_quota()` function uses a **conditional upsert** pattern that is race-safe under concurrent workers:

```sql
INSERT INTO tenant_quota_usage (tenant_id, month_key, messages_sent, last_updated_at)
VALUES ($1, $2, 1, now())
ON CONFLICT (tenant_id, month_key)
DO UPDATE SET
  messages_sent   = tenant_quota_usage.messages_sent + 1,
  last_updated_at = now()
WHERE
  tenant_quota_usage.messages_sent < $3   -- $3 = bulk_quota_limit
RETURNING messages_sent
```

- If `RETURNING` returns a row → quota consumed successfully
- If `RETURNING` returns nothing → quota is full, nothing was modified
- No gap between check and increment — single atomic statement

### 20.4 Frontend Integration

The frontend fetches quota status via `GET /api/bulk-message/quota` and displays:

- **Quota progress bar** (green/orange/red based on usage percentage)
- **Used / Limit / Remaining** counts
- **Reset date** (first of next month)
- **Start button disabled** when contacts exceed remaining quota
- **Inline warning** when a selected file has more contacts than remaining
- **Auto-refresh** after campaign completion and on a 10-second interval

Recipients with `quota_exceeded` status are shown with an orange badge and a dedicated filter tab on the campaign detail page.

### 20.5 Key Files

| File | Role |
|------|------|
| `schema.sql` | `tenants.bulk_quota_limit` column + `tenant_quota_usage` table DDL |
| `db_layer/quota.py` | `get_quota_status()`, `try_consume_quota()` — all quota DB operations |
| `routers/bulk_message.py` | `GET /quota` endpoint + pre-check enforcement on `POST /start` |
| `routers/settings.py` | `GET /usage` extended with `bulk_quota` info |
| `db_layer/campaign_recipients.py` | `quota_exceeded` terminal status + `mark_excess_recipients_quota_exceeded()` |
| `worker_main.py` | Capped fan-out in campaign worker + atomic consume in message worker |
| `frontend/src/lib/api.ts` | `bulkMessage.quota()` API method |
| `frontend/src/app/dashboard/bulk-message/page.tsx` | Quota bar, button guard, 429 error handling |
| `frontend/src/app/dashboard/bulk-message/[campaignId]/page.tsx` | `quota_exceeded` badge + filter tab |

---

## 21. Onboarding Guide for New Developers

This section provides a structured path for new team members to understand the entire WappFlow system.

### 21.1 The 30-Minute Mental Model

**What it does:** WappFlow lets businesses automate WhatsApp messaging. Each user (tenant) connects their WhatsApp Business Account and can send bulk campaigns, auto-reply to incoming messages, and forward files.

**How it's built:**
- **Frontend** (Next.js) handles UI + Firebase Auth login
- **Backend** (FastAPI) handles all business logic, DB operations, and webhook processing
- **Worker** (BullMQ) handles async message sending with rate limiting
- **Redis** powers the job queues + rate limiting
- **Postgres** (Neon) stores all tenant data, messages, and campaign state

**Key architectural principles:**
1. **Multi-tenant isolation** — every DB query is scoped by `tenant_id`
2. **Async-first** — all WhatsApp API calls go through BullMQ queues, never synchronously in API handlers
3. **Idempotent** — message sending uses idempotency keys to prevent duplicates on retries
4. **Fail-safe** — transactions ensure no partial state; crashes roll back cleanly

### 21.2 Reading Order for New Developers

| Day | Focus | Files to Read |
|-----|-------|---------------|
| **Day 1** | Data model + API structure | `schema.sql`, `retention_schema.sql`, `main.py`, `auth_middleware.py` |
| **Day 1** | How settings and config work | `store.py`, `cache.py`, `routers/settings.py` |
| **Day 2** | Campaign lifecycle (most complex flow) | `routers/bulk_message.py`, `worker_main.py`, `db_layer/campaigns.py`, `db_layer/campaign_recipients.py`, `db_layer/quota.py` |
| **Day 2** | Queue architecture + phone normalization | `services/queue_manager.py`, `rate_limit.py`, `utils/phone_utils.py` |
| **Day 2** | Template building + media pipeline | `services/template_builder.py`, `services/whatsapp.py`, `utils/image_utils.py` |
| **Day 3** | Webhook + chatbot | `routers/webhook.py`, `db_layer/encryption.py`, `db_layer/secrets.py` |
| **Day 3** | Data retention | `retention.py`, `retention_schema.sql` |
| **Day 4** | Frontend | `frontend/src/lib/api.ts`, `frontend/src/app/dashboard/`, `frontend/src/contexts/` |

### 21.3 How to Run Locally

```bash
# Prerequisites: Python 3.11+, Node.js 18+, Docker (for Redis)

# 1. Start Redis
docker-compose up redis -d

# 2. Backend
cd backend
pip install -r requirements.txt
cp .env.example .env    # Fill in DATABASE_URL, ENCRYPTION_KEY, WEBHOOK_VERIFY_TOKEN
python apply_schema.py  # Create all tables
python run_server.py    # Terminal 1: API on port 5000

# 3. Worker
cd backend
python worker_main.py   # Terminal 2: BullMQ worker

# 4. Frontend
cd frontend
npm install
cp .env.local.example .env.local  # Set NEXT_PUBLIC_API_URL=http://localhost:5000
npm run dev             # Terminal 3: Next.js on port 3000
```

### 21.4 Key Environment Variables (Quick Reference)

| Variable | Where | Purpose |
|----------|-------|---------|
| `DATABASE_URL` | Backend | Postgres connection string |
| `ENCRYPTION_KEY` | Backend | Fernet key for encrypting secrets at rest |
| `WEBHOOK_VERIFY_TOKEN` | Backend | Legacy webhook verification (per-tenant tokens preferred) |
| `REDIS_URL` | Backend | Full Redis URL. Use `rediss://` for TLS (required by Upstash). Overrides `REDIS_HOST`/`REDIS_PORT` |
| `REDIS_HOST` / `REDIS_PORT` | Backend | Redis host/port for local dev (only used if `REDIS_URL` is not set) |
| `NEXT_PUBLIC_API_URL` | Frontend | Backend URL (e.g., `http://localhost:5000`) |
| `NEXT_PUBLIC_FIREBASE_*` | Frontend | Firebase Auth configuration |

### 21.5 How to Test Key Flows

**Campaign flow (end-to-end):**
1. Login at `http://localhost:3000`
2. Go to Settings → configure WhatsApp API credentials
3. Go to Bulk Message → upload a CSV with test phone numbers
4. Select a template and start the campaign
5. Watch `worker_main.py` logs for `worker_send_prepare` and `worker_finalize_sent`
6. Check campaign status in the UI or via `GET /api/bulk-message/status/{id}`

**Webhook flow (requires ngrok):**
1. Run `ngrok http 5000` to get a public URL
2. In Meta App Dashboard → Webhooks, set callback URL to `https://<ngrok>/api/webhook/{tenant_id}`
3. Send a WhatsApp message to your business number
4. Watch backend logs for `webhook_per_tenant`, `button_match`, or `fallback_trigger`

### 21.6 Common Gotchas

| Gotcha | Explanation |
|--------|-------------|
| **Worker not running** | Campaigns will be created but messages won't send. Always run `python worker_main.py` in a separate terminal. |
| **Redis TLS (`Connection closed by server`)** | Cloud Redis providers (Upstash, Redis Cloud) require TLS. Use `rediss://` (double `s`) in `REDIS_URL`, not `redis://`. The `rate_limit.py` module auto-detects the scheme and passes `ssl=True` to both the `aioredis` client and BullMQ workers. |
| **Stale cache** | Settings and chatbot rules are cached for 6 hours. After manual DB changes, restart the server or wait for cache expiry. |
| **Webhook signature failures** | Ensure `meta_app_secret` in WappFlow matches the App Secret in Meta Dashboard. If you get 401s, check `webhook_sig_rejected` logs. |
| **Template not found** | Templates must be approved in Meta Business Manager first. WappFlow caches template metadata — if a new template isn't showing, wait for cache refresh or restart. |
| **Per-tenant webhook URL** | The `{tenant_id}` in the webhook URL is the Firebase UID, **not** the WhatsApp phone number ID. Find it in browser DevTools → Network → check the `Authorization` token payload. |
| **Quota not updating** | Quota is read from the `tenant_quota_usage` table using the current `YYYY-MM` month key. If you manually reset quota in the DB, make sure the `month_key` matches the current month. The frontend auto-refreshes quota every 10 seconds and on campaign completion. |
| **International numbers not delivering** | Ensure phone numbers in the CSV include the `+` prefix and full country code (e.g. `+14155552671` for US). Without `+`, 10-digit numbers are treated as Indian mobile numbers and `91` is prepended. See `utils/phone_utils.normalize_phone()`. |
| **Excel scientific notation phones** | Excel may store phone numbers as floats (e.g. `9.1995E+11`). The normalizer handles this. For reliability, format the phone column as **Text** in Excel and always include the country code. |
| **Image upload `#100` errors** | Images > 5 MB are auto-compressed by `utils/image_utils.compress_image()` before upload. If you still see `#100`, Pillow may not be installed or the image cannot be compressed below 5 MB. Check logs for `image_compression_failed`. |
| **Template `#132012` (media format mismatch)** | The cached media ID for a template header has expired. WappFlow auto-invalidates the cache on this error and re-uploads on the next send. If it persists, restart the worker to clear all in-memory media caches. |
| **Template `#132000` (parameter count mismatch)** | The number of variables in the template body doesn't match what the builder resolved. Check that your contact CSV has the correct columns (e.g. `name`, `phone`) matching the template's variable names. |

### 21.7 Architecture Evolution (Phase History)

| Phase | What Was Added |
|-------|---------------|
| **Phase 1–3** | Core platform: FastAPI, Firebase Auth, BullMQ queues, campaign management, chatbot rules |
| **Phase 4** | Startup checks, environment validation, startup cache pre-warming |
| **Phase 5** | Postgres migration (from Firestore), connection pooling, cursor pagination |
| **Phase 6** | Security hardening: removed token logging, Pydantic validation, CSV injection protection |
| **Phase 7** | Redis rate limiting: API middleware (heavy/general tiers), worker token bucket, global cooldown |
| **Phase 8** | Data retention: archive tables, daily_message_stats, background cron, purge system |
| **Phase 9** | Per-tenant webhooks, HMAC signature verification, Fernet encryption at rest, `meta_app_secret` per tenant |
| **Phase 10** | Per-tenant monthly bulk message quota: schema (`tenant_quota_usage`), atomic consumption (`db_layer/quota.py`), API pre-check, worker enforcement (capped fan-out + per-message consume), frontend quota bar + button guard |
| **Phase 11** | Comprehensive documentation refresh: README rewrite (Firestore→Postgres alignment), API doc glossary, cross-referencing between docs, fresher onboarding improvements |
| **Phase 12** | Phone normalization (`utils/phone_utils.py`): E.164-compliant, international support, India (+91) fallback, scientific notation (Excel float) handling, graceful `None` on invalid. Image compression (`utils/image_utils.py`): Pillow-based automatic compression to ≤5 MB before WhatsApp upload (JPEG quality reduction, PNG optimize, progressive resize). Media upload fix (`services/whatsapp.py`): correct multipart/form-data with MIME-derived filename. Template hardening: named + positional variable support, pre-send `validate_components()`, tenant-scoped template + media ID cache, CDN URL expiry detection, non-retryable error code classification (`#132000`, `#132001`, `#132012`, `#100`). |

---

## 22. Phone Normalization Architecture

WappFlow enforces **E.164-compliant phone normalization** at every entry point where phone numbers are accepted. This ensures numbers are never silently corrupted before reaching the WhatsApp Cloud API.

### 22.1 The Problem It Solves

Before Phase 12, phone numbers were processed with hardcoded India-specific logic that blindly stripped non-digits and prepended `91`. This caused:
- US numbers like `+14155552671` becoming `14155552671914155552671` (double-prepend on retry)
- UK numbers like `+447911123456` becoming `91447911123456` (invalid)
- Excel float notation like `9.1995E+11` becoming `91199` (corrupt)

### 22.2 Normalization Flow

```
Raw phone string from CSV / webhook / API
          │
          ├─ Starts with '+'? → set international=True
          │
          ├─ Parseable as float? (scientific notation, e.g. 9.1995E+11)
          │   └─ Convert to integer string; international=False
          │       (the '+' in '9.1995E+11' is exponent, not country code)
          │
          ├─ Strip all non-digit characters
          │
          ├─ Apply India (+91) fallback?
          │   Only when ALL conditions met:
          │   • international=False (no leading '+')
          │   • Exactly 10 digits
          │   • First digit is 6, 7, 8, or 9 (Indian mobile range)
          │   └─ Prepend '91'
          │
          ├─ Validate E.164 length: 10–15 digits
          │   └─ Outside range? → return None (caller skips)
          │
          └─ Return digits-only string (no '+', no spaces)
               WhatsApp API accepts: "to": "14155552671"
```

### 22.3 Where Normalization Happens

| Layer | File | Behavior on Invalid |
|-------|------|---------------------|
| **File parse (bulk)** | `routers/bulk_message.py` | Skip row silently; not counted in `validContacts` |
| **File parse (file-forward)** | `routers/file_forward.py` | Skip row silently |
| **Queue enqueue** | `services/queue_manager.py` | Skip enqueue; `WARNING` log; job never created |
| **Worker pre-send** | `worker_main.py` | Mark recipient `failed`; increment failed counter; log at `WARN`; finalize campaign if last recipient |

The worker re-normalizes the phone on arrival (not just at enqueue) to provide full observability via structured logging:
```
Phone normalization: raw=+14155552671, normalized=14155552671
```

### 22.4 Key Files

| File | Role |
|------|------|
| `utils/phone_utils.py` | Single source of truth for `normalize_phone()` |
| `routers/bulk_message.py` | Calls normalizer on every parsed CSV row |
| `routers/file_forward.py` | Calls normalizer on contact list rows |
| `services/queue_manager.py` | Normalizes in `enqueue_message()`; returns early if `None` |
| `worker_main.py` | Re-normalizes + logs for observability; graceful failure path |

---

## 23. Image & Media Upload Pipeline

WappFlow's media pipeline ensures images are reliably uploaded to the WhatsApp Cloud API — including automatic size enforcement, format detection, and CDN URL validation.

### 23.1 The Problem It Solves

WhatsApp Cloud API enforces a **5 MB hard limit** on image uploads. Attempts to upload larger images return `#100 Invalid parameter`. Additionally:
- CDN URLs embedded in WhatsApp template definitions expire over time — downloading them returns HTML error pages, not the actual image.
- The Graph API requires a specific `multipart/form-data` structure with a filename that has the correct extension — using `upload.bin` or omitting the extension causes silent `#100` failures.
- JPEG doesn't support transparency — RGBA/palette-mode PNGs must be composited before conversion.

### 23.2 Upload Flow (Template Header Images)

```
template_builder.upload_header_media(template_key, whatsapp, tenant_id)
          │
          ├─ Check in-memory media ID cache
          │   └─ Hit → return cached media_id (no download/upload)
          │
          ├─ Find HEADER component in cached template metadata
          │   └─ No media header → return "" immediately
          │
          ├─ Download image from CDN handle URL (httpx)
          │   ├─ status != 200 → WARN, abort
          │   ├─ Content-Type is text/html or application/json → WARN "CDN expired", abort
          │   ├─ Content length < 100 bytes → WARN "suspiciously small", abort
          │   └─ Content-Type is application/octet-stream → infer MIME from template format field
          │
          ├─ If format == IMAGE: compress_image(file_bytes)     ← utils/image_utils.py
          │   └─ Re-detect MIME from magic bytes after compression
          │       (PNG→JPEG conversion changes MIME)
          │
          ├─ whatsapp.upload_media(file_bytes, mime)            ← services/whatsapp.py
          │   ├─ Derive filename from MIME: "upload.jpg", "upload.png", etc.
          │   ├─ POST multipart/form-data:
          │   │   • file=(filename, bytes, mime)  [files= field]
          │   │   • messaging_product="whatsapp"  [data= field]
          │   │   • type=mime                     [data= field]
          │   └─ Returns {success: True, mediaId: "..."}
          │
          ├─ Cache media_id under tenant-scoped key
          └─ Return media_id
```

### 23.3 Image Compression Strategies

The `compress_image()` function in `utils/image_utils.py` applies strategies in order:

| Strategy | Condition | Output |
|----------|-----------|--------|
| **No-op** | `len(bytes) <= 5 MB` | Return as-is immediately |
| **EXIF fix** | Always (when opening with Pillow) | Apply `ImageOps.exif_transpose()` |
| **PNG optimize** | PNG with real transparency | `save(format=PNG, optimize=True)` |
| **PNG → JPEG fallback** | PNG optimize still > 5 MB | Fall through to JPEG path |
| **JPEG quality reduction** | All other cases | Quality 90 → 85 → 80 → ... → 40 (step 5); first ≤ 5 MB wins |
| **Progressive resize** | JPEG path exhausted | Halve resolution up to 5× + JPEG quality 70 |
| **Absolute fallback** | All strategies failed | Return original bytes; upload may fail — `image_compression_failed` logged |

### 23.4 Media ID Cache & Invalidation

Once a header image is successfully uploaded, the resulting `media_id` is cached:

```python
# Cache key format:
"{tenant_id}:{template_name}|{language_code}"

# Example:
"uid_abc123:promo_template|en_US"
```

- **Scope:** In-memory (`_uploaded_media_ids` dict), **per worker process**, for the process lifetime.
- **Benefit:** Chatbot flows that send the same template to hundreds of users per minute re-use the same `media_id` without re-uploading the image.
- **Invalidation trigger:** When the WhatsApp API returns `#132012 Parameter format does not match`, the worker calls `invalidate_cached_media(template_key, tenant_id)` so the next send triggers a fresh upload.

### 23.5 Key Files

| File | Role |
|------|------|
| `utils/image_utils.py` | `compress_image()` — all compression logic |
| `services/template_builder.py` | `upload_header_media()` — CDN download, compression, upload, caching; `invalidate_cached_media()` — cache invalidation |
| `services/whatsapp.py` | `upload_media()` — multipart/form-data upload to Graph API |
| `worker_main.py` | Calls `upload_header_media()` before every template send; calls `invalidate_cached_media()` on `#132012` |

---

## 24. Contact Limit Enforcement (Phase 13)

WappFlow enforces a **per-campaign contact limit** (default: 500) to prevent overly large campaigns from overwhelming the sending pipeline or hitting WhatsApp rate limits aggressively.

### 24.1 Architecture

```
User uploads CSV/Excel with 800 contacts
            │
            ▼
  POST /api/bulk-message/parse
            │
  _parse_contacts(df, max_contacts=500)
            │
  ┌─────────┴──────────┐
  │ Collect contacts   │
  │ Early-stop at 501  │ ← stops immediately, 299 rows never read
  └─────────┬──────────┘
            │ len(contacts) > 500
            ▼
  HTTP 400: "exceeds maximum of 500 per campaign"
```

### 24.2 Dual Enforcement

| Enforcement Point | Stage | Behavior |
|-------------------|-------|----------|
| `POST /parse` | File upload preview | Rejects with `400` + details. Uses early-stop optimization. |
| `POST /start` | Campaign creation | Second check (whether from cache or re-parsed). Rejects before DB writes. |

### 24.3 Parsed Contacts Redis Cache

To avoid parsing the same file twice, `/parse` caches results in Redis:

```
/parse flow:
  parse file → cache in Redis (key: parsed_contacts:{upload_id}, TTL: 10min)
              → return contacts[] + upload_id

/start flow:
  if upload_id present → try Redis GET parsed_contacts:{upload_id}
    → hit: use cached contacts, DELETE key (single-use token)
    → miss: fall back to file parsing
```

### 24.4 Frontend Sync

`GET /api/bulk-message/limits` returns `{ max_valid_contacts: 500 }` so the frontend can display the correct limit without hardcoding.

### 24.5 Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `MAX_VALID_CONTACTS` | `500` | Maximum valid contacts per campaign |

---

## 25. Redis Command Optimization (Phase 14)

This phase dramatically reduced Redis command consumption to minimize costs on managed Redis providers (e.g., Upstash).

### 25.1 BullMQ Limiter Removal

```
Before (Phase 12):                          After (Phase 14):
┌─────────────────────────────┐            ┌──────────────────────────────┐
│ BullMQ Worker               │            │ BullMQ Worker                │
│                             │            │                              │
│ limiter: {max:80, dur:1000} │            │ (no limiter)                 │
│   └─ 6 extra Redis Lua     │            │   └─ 0 extra Redis calls     │
│      calls per job          │            │                              │
│                             │            │ asyncio.sleep(0.2)           │
│                             │            │   └─ ~5 msg/sec, in-process  │
└─────────────────────────────┘            └──────────────────────────────┘
```

### 25.2 Worker Polling Tuning

| Setting | Before (Default) | After | Redis Impact |
|---------|-------------------|-------|--------------|
| `drainDelay` | 5s | 10s (max) | 50% fewer idle polls |
| `stalledInterval` | 30s (30,000ms) | 300s (300,000ms) | ~98% fewer stall checks |
| `lockDuration` | 30s | 300s | Matches stalledInterval |
| `maxStalledCount` | 2 | 1 | Minimal iterations |

### 25.3 In-Memory Caching

Two high-frequency Redis checks now use short-lived in-memory caches to avoid redundant calls during high-throughput campaign processing:

| Function | Cache TTL | Before | After |
|----------|-----------|--------|-------|
| `tenant_token_bucket_consume()` | 5s (allowed) / 1s (denied) | 1 Redis EVAL per message | ~1 per 5 seconds |
| `is_global_cooldown_active()` | 2s | 1 Redis GET per message | ~1 per 2 seconds |

### 25.4 Lua Token Bucket for API Rate Limiting

API rate limiting uses a single `EVALSHA` Lua script (with `EVAL` fallback) instead of the former sorted-set sliding window. This reduces per-request Redis overhead from ~5 commands to 1. Feature flag: `USE_TOKEN_BUCKET=true` (default).

### 25.5 Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `WORKER_RATE_DELAY` | `0.2` | Seconds between message jobs (in-worker throttle). Replaces BullMQ limiter. |
| `USE_TOKEN_BUCKET` | `true` | API rate limiter strategy. `true` = Lua token bucket; `false` = sorted-set sliding window |
| `QUEUE_RATE_LIMIT` | `80` | **Deprecated.** Has no effect. Retained for backward compatibility. |

---

## 26. Campaign Counter Accuracy (Phase 15)

### 26.1 Problem

Incremental counter shards (`sent_count`, `failed_count` on the `campaigns` row) could drift out of sync with actual recipient statuses when:
- Transactions partially committed
- Workers restarted mid-batch
- Retry logic updated recipient status without adjusting counters

### 26.2 Solution: Authoritative Recipient Counts

All campaign status/list/details API responses now derive counts from `campaign_recipients.count_by_status()` — a single SQL query that groups recipients by their current status:

```sql
SELECT
  COUNT(*) FILTER (WHERE status IN ('submitted','sent','delivered','read')) AS sent,
  COUNT(*) FILTER (WHERE status = 'failed') AS failed,
  COUNT(*) FILTER (WHERE status IN ('pending','queued','processing')) AS pending,
  COUNT(*) FILTER (WHERE status = 'quota_exceeded') AS quota_exceeded
FROM campaign_recipients
WHERE tenant_id = %s AND campaign_id = %s::uuid
```

### 26.3 Display Category Mapping

| API Field | Recipient Statuses Counted |
|-----------|---------------------------|
| `sent_count` | submitted, sent, delivered, read |
| `failed_count` | failed |
| `pending_count` | pending, queued, processing |
| `quota_exceeded_count` | quota_exceeded |

> **Note:** The `campaign_counters` shards still exist and are incremented for backward compatibility, but they are no longer the source of truth for API responses.

---

## 27. Quota Counting Fix (Phase 16)

### 27.1 Problem

The original quota system consumed 1 quota unit on every message send attempt, including retries. A message that failed and was retried 3 times consumed 3 units of quota, inflating the tenant's usage count.

### 27.2 Solution: First-Attempt-Only Consumption

The worker now checks `attempt_count` before consuming quota:

```
Message Worker receives job for phone=919876543210
  │
  ├─ attempt_count == 0 (first attempt)
  │   └─ try_consume_quota(tenant_id) → consumed? continue : mark quota_exceeded
  │
  ├─ attempt_count > 0 (retry)
  │   └─ skip quota consumption → log "quota_skip_retry" → proceed to send
  │
  └─ Send via WhatsApp API
```

This ensures each unique recipient consumes exactly **1 quota unit**, regardless of how many retries are needed.

### 27.3 Key Files

| File | Role |
|------|------|
| `worker_main.py` | `process_message_job()` — conditional quota consumption based on `attempt_count` |
| `db_layer/quota.py` | `try_consume_quota()` — atomic quota consumption logic |
| `db_layer/campaign_recipients.py` | `count_by_status()` — authoritative status counts |
| `routers/bulk_message.py` | `MAX_VALID_CONTACTS`, `/limits` endpoint, Redis contact caching |
| `rate_limit.py` | Lua token bucket, in-memory caches, `WORKER_RATE_DELAY` |

