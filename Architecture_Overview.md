# WappFlow — Architecture Overview

> **Product:** Multi-tenant WhatsApp Business Automation SaaS  
> **Version:** 1.2.0 (Phase 8 — Data Retention)

---

## 1. What is WappFlow?

WappFlow is a **multi-tenant SaaS platform** that automates WhatsApp Business messaging. It provides three core products:

1. **Bulk Messaging** — Send template-based WhatsApp messages to thousands of contacts via Excel/CSV upload. Supports scheduled campaigns, real-time progress tracking, automatic retries, and resend-failed workflows.
2. **File Forwarding** — Send documents, images, and PDFs to single or multiple recipients via the WhatsApp Cloud API.
3. **Auto-Reply Chatbot** — Keyword-based rule engine that automatically responds to incoming WhatsApp messages. Supports configurable button→template mappings and a first-trigger fallback system.

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
│  ┌─────────────┐ ┌──────────────┐                                      │
│  │ /logs       │ │ /webhook     │ ← Meta WhatsApp (no auth)            │
│  └─────────────┘ └──────────────┘                                      │
│                                                                          │
│  Background Tasks:                                                      │
│    TTL Cleanup (6h) · Campaign Scheduler (60s) · Retention Cron (24h)   │
└──────────┬──────────────────────────────┬───────────────────────────────┘
           │                              │
           ▼                              ▼
┌─────────────────────┐       ┌──────────────────────────────────────────┐
│    REDIS 6.2        │       │          NEON POSTGRES                    │
│                     │       │                                           │
│  • BullMQ Queues    │       │  tenants · campaigns · campaign_recipients│
│    - campaign_queue │       │  messages · chat_messages · chatbot_rules │
│    - message_queue  │       │  webhook_events · usage_events            │
│    - dead_letter_q  │       │  template_cache · user_triggers           │
│  • Rate limit keys  │       │  chatbot_config · campaign_counters       │
│  • Token buckets    │       │  messages_archive · chat_messages_archive │
│  • Global cooldown  │       │  webhook_events_archive · usage_events_ar │
│                     │       │  daily_message_stats                      │
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
| **Queue** | BullMQ (Redis-backed) | Async job processing for message sending |
| **Cache** | In-memory (Python) + Redis | TTL-based settings/rules cache (6h) + rate-limit state |
| **External API** | WhatsApp Cloud API (Meta Graph v18.0) | Message sending, template management, media uploads |
| **Deployment** | Vercel (frontend), Render (backend), Docker Compose (local) | CI/CD and containerization |

---

## 4. Multi-Tenancy Model

- **Tenant = Firebase UID.** Each authenticated user gets a unique `tenant_id` derived from their Firebase Auth UID.
- **Row-level isolation.** Every database table includes a `tenant_id` column. All queries are scoped by tenant.
- **Middleware enforcement.** The `FirebaseAuthMiddleware` extracts and validates the token on every request, setting `request.state.tenant_id`. No route can access another tenant's data.
- **Cache isolation.** All cache keys are prefixed with `tenant_id`. Template component cache is tenant-scoped to prevent cross-tenant data leakage.

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

### Webhook (Incoming WhatsApp Message)

```
1. Meta sends POST /api/webhook
   └─ X-Hub-Signature-256: sha256=<hmac>
2. No auth middleware (public route)
3. Signature verification (META_APP_SECRET)
4. Resolve tenant from phone_number_id in payload
5. Deduplicate via webhook_events table
6. Store incoming message in chat_messages + messages
7. Chatbot decision engine:
   a. Check button→template mappings (per-tenant configurable)
   b. Check keyword rules (DB-backed)
   c. Fallback: first_trigger (24h rate-limited per user)
8. Enqueue reply to message_queue (priority 0 = highest)
9. Return { status: "ok" }
```

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
│   └── webhook.py            # Meta webhook verification + incoming message handling
│
├── services/
│   ├── whatsapp.py           # WhatsApp Cloud API client (retry, rate-limit aware)
│   ├── queue_manager.py      # BullMQ queue helpers (enqueue campaign/message/DLQ)
│   ├── template_builder.py   # Template component cache + parameter builder
│   └── chatgpt.py            # (Reserved) ChatGPT integration
│
├── db_layer/
│   ├── tenants.py            # Tenant CRUD + lookup by phone_number_id
│   ├── campaigns.py          # Campaign CRUD + status transitions
│   ├── campaign_recipients.py# Recipient status machine (pending→queued→sent/failed)
│   ├── campaign_counters.py  # Sharded sent/failed counters
│   ├── messages.py           # Unified message log (all products)
│   ├── chat_messages.py      # Conversation history
│   ├── chatbot.py            # Chatbot config + rules
│   ├── webhook_events.py     # Webhook deduplication
│   ├── usage_events.py       # Billable usage tracking
│   ├── template_cache.py     # Persistent template metadata
│   ├── secrets.py            # Token resolution layer
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
| **Webhook integrity** | `X-Hub-Signature-256` HMAC verification using `META_APP_SECRET` |
| **Token storage** | Access tokens stored in Postgres, never returned to frontend, never logged |
| **Rate limiting** | Redis sliding-window per tenant; heavy tier (write actions only) + general tier (reads/polls) + worker token bucket |
| **Input validation** | Pydantic models on all request bodies; alphanumeric validation on IDs |
| **CSV injection** | Export values sanitized against DDE formula injection |
| **File upload** | 16 MB hard limit enforced server-side |
| **CORS** | Explicit origin allowlist; `localhost:3000` only in non-production |
| **Docs** | OpenAPI/Swagger disabled in production |

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
  └──→ Redis (managed)─ BullMQ queues + rate limiting
```

### Local Development (Docker Compose)

```
docker-compose.yml defines:
  • redis (6.2-alpine, port 6379, AOF persistence)
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
- **Retention observability** — the archive and purge systems emit dedicated log events (`retention_start`, `retention_batch`, `retention_complete`, `purge_started`, `purge_batch`, `purge_completed`, etc.) with per-batch row counts, durations, and error details. See the [API Developer Doc Section 13.8](API_Developer_Doc.md#138-monitoring--log-events) for the full event catalog.

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
