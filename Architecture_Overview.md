# WappFlow — Architecture Overview

> **Product:** Multi-tenant WhatsApp Business Automation SaaS  
> **Version:** 2.0.0 (Phase 9 — Per-Tenant Webhooks & Encryption at Rest)

---

## 1. What is WappFlow?

WappFlow is a **multi-tenant SaaS platform** that automates WhatsApp Business messaging. It provides three core products:

1. **Bulk Messaging** — Send template-based WhatsApp messages to thousands of contacts via Excel/CSV upload. Supports scheduled campaigns, real-time progress tracking, automatic retries, and resend-failed workflows.
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
│  message_worker  ──→ Sends via WhatsApp Cloud API (rate-limited)         │
│                                                                          │
│  Rate Controls:                                                          │
│    • BullMQ limiter (80 msg/sec global)                                  │
│    • Tenant token bucket (10 msg/sec per tenant, burst 20)               │
│    • Global cooldown on repeated 429s from WhatsApp                      │
│    • Exponential backoff + jitter on retries                             │
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
   b. Parses uploaded file → extracts contacts
   c. Creates campaign + recipients + counters in DB (single transaction)
   d. Enqueues campaign job to BullMQ campaign_queue
   e. Returns { campaignId, totalContacts, status }
6. Worker picks up campaign job:
   a. Reads all pending recipients from DB
   b. Fans out individual message jobs to message_queue
7. Message worker processes each job:
   a. Checks global cooldown & tenant token bucket
   b. Resolves template, builds components
   c. Calls WhatsApp Cloud API
   d. Updates recipient status + counters in DB (transaction)
   e. On final recipient, marks campaign as "completed"
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
  │  Create campaign row (status:   │
  │    running or scheduled)        │
  │  Insert recipients (pending)    │
  │  Init counter shards            │
  │  Enqueue → campaign_queue       │
  └────────────┬───────────────────┘
               │
               ▼
  ┌─── campaign_worker ────────────┐
  │  Read pending recipients        │
  │  For each: enqueue →            │
  │    message_queue                 │
  │  Transition: pending → queued   │
  └────────────┬───────────────────┘
               │
               ▼
  ┌─── message_worker ─────────────┐
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
                       │                        │
                       └──→ queued (retry) ◄────┘ (webhook failed, attempts < max)
                       │
                       └──→ failed (max attempts exhausted)
```

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
│   ├── campaign_recipients.py# Recipient status machine (pending→queued→sent/failed)
│   ├── campaign_counters.py  # Sharded sent/failed counters
│   ├── messages.py           # Unified message log (all products) + archive fallback
│   ├── chat_messages.py      # Conversation history
│   ├── chatbot.py            # Chatbot config + rules + button mappings
│   ├── webhook_events.py     # Webhook deduplication
│   ├── usage_events.py       # Billable usage tracking
│   ├── template_cache.py     # Persistent template metadata
│   ├── secrets.py            # Runtime token resolution (DB → env fallback)
│   ├── encryption.py         # Fernet encrypt/decrypt for secrets at rest
│   └── users.py              # User trigger rate limiting (24h)
│
└── utils/
    └── time_utils.py         # IST timestamp helpers
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
| Template components | In-memory + Postgres | Indefinite (memory) / persistent (DB) | Template parameter metadata |
| Uploaded media IDs | In-memory | Process lifetime | Avoid re-uploading same template header media |
| Button mappings | In-memory | 1 hour | Per-tenant button→template mappings |
| Rate limit counters | Redis | Sliding window | API and worker rate limiting |
| Token buckets | Redis | 60s auto-expire | Per-tenant message sending fairness |

Cache invalidation: Write operations explicitly call `cache.invalidate()` for affected keys.

---

## 12. Database Schema (ER Summary)

```
tenants (1)
  ├──< chatbot_config (1:1)
  ├──< chatbot_rules (1:N)
  ├──< campaigns (1:N)
  │      └──< campaign_recipients (1:N)
  ├──< messages (1:N)  ───archive───>  messages_archive
  ├──< chat_messages (1:N)  ───────>  chat_messages_archive
  ├──< webhook_events (1:N)  ──────>  webhook_events_archive
  ├──< usage_events (1:N)  ────────>  usage_events_archive
  ├──< template_cache (1:N)
  └──< user_triggers (1:N)

daily_message_stats (pre-aggregated from messages before archival)
```

All tables use `tenant_id` as a foreign key to `tenants`. Campaigns use a composite primary key `(tenant_id, campaign_id)` for efficient tenant-scoped queries.

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

## 20. Onboarding Guide for New Developers

This section provides a structured path for new team members to understand the entire WappFlow system.

### 20.1 The 30-Minute Mental Model

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

### 20.2 Reading Order for New Developers

| Day | Focus | Files to Read |
|-----|-------|---------------|
| **Day 1** | Data model + API structure | `schema.sql`, `retention_schema.sql`, `main.py`, `auth_middleware.py` |
| **Day 1** | How settings and config work | `store.py`, `cache.py`, `routers/settings.py` |
| **Day 2** | Campaign lifecycle (most complex flow) | `routers/bulk_message.py`, `worker_main.py`, `db_layer/campaigns.py`, `db_layer/campaign_recipients.py` |
| **Day 2** | Queue architecture | `services/queue_manager.py`, `rate_limit.py` |
| **Day 3** | Webhook + chatbot | `routers/webhook.py`, `db_layer/encryption.py`, `db_layer/secrets.py` |
| **Day 3** | Data retention | `retention.py`, `retention_schema.sql` |
| **Day 4** | Frontend | `frontend/src/lib/api.ts`, `frontend/src/app/dashboard/`, `frontend/src/contexts/` |

### 20.3 How to Run Locally

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

### 20.4 Key Environment Variables (Quick Reference)

| Variable | Where | Purpose |
|----------|-------|---------|
| `DATABASE_URL` | Backend | Postgres connection string |
| `ENCRYPTION_KEY` | Backend | Fernet key for encrypting secrets at rest |
| `WEBHOOK_VERIFY_TOKEN` | Backend | Legacy webhook verification (per-tenant tokens preferred) |
| `REDIS_URL` | Backend | Full Redis URL. Use `rediss://` for TLS (required by Upstash). Overrides `REDIS_HOST`/`REDIS_PORT` |
| `REDIS_HOST` / `REDIS_PORT` | Backend | Redis host/port for local dev (only used if `REDIS_URL` is not set) |
| `NEXT_PUBLIC_API_URL` | Frontend | Backend URL (e.g., `http://localhost:5000`) |
| `NEXT_PUBLIC_FIREBASE_*` | Frontend | Firebase Auth configuration |

### 20.5 How to Test Key Flows

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

### 20.6 Common Gotchas

| Gotcha | Explanation |
|--------|-------------|
| **Worker not running** | Campaigns will be created but messages won't send. Always run `python worker_main.py` in a separate terminal. |
| **Redis TLS (`Connection closed by server`)** | Cloud Redis providers (Upstash, Redis Cloud) require TLS. Use `rediss://` (double `s`) in `REDIS_URL`, not `redis://`. The `rate_limit.py` module auto-detects the scheme and passes `ssl=True` to both the `aioredis` client and BullMQ workers. |
| **Stale cache** | Settings and chatbot rules are cached for 6 hours. After manual DB changes, restart the server or wait for cache expiry. |
| **Webhook signature failures** | Ensure `meta_app_secret` in WappFlow matches the App Secret in Meta Dashboard. If you get 401s, check `webhook_sig_rejected` logs. |
| **Template not found** | Templates must be approved in Meta Business Manager first. WappFlow caches template metadata — if a new template isn't showing, wait for cache refresh or restart. |
| **Per-tenant webhook URL** | The `{tenant_id}` in the webhook URL is the Firebase UID, **not** the WhatsApp phone number ID. Find it in browser DevTools → Network → check the `Authorization` token payload. |

### 20.7 Architecture Evolution (Phase History)

| Phase | What Was Added |
|-------|---------------|
| **Phase 1–3** | Core platform: FastAPI, Firebase Auth, BullMQ queues, campaign management, chatbot rules |
| **Phase 4** | Startup checks, environment validation, startup cache pre-warming |
| **Phase 5** | Postgres migration (from Firestore), connection pooling, cursor pagination |
| **Phase 6** | Security hardening: removed token logging, Pydantic validation, CSV injection protection |
| **Phase 7** | Redis rate limiting: API middleware (heavy/general tiers), worker token bucket, global cooldown |
| **Phase 8** | Data retention: archive tables, daily_message_stats, background cron, purge system |
| **Phase 9** | Per-tenant webhooks, HMAC signature verification, Fernet encryption at rest, `meta_app_secret` per tenant |
