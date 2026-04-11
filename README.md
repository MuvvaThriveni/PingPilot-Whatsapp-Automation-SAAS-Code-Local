<div align="center">
  <h1>🚀 WappFlow</h1>
  <p><strong>A Production-Grade, Multi-Tenant WhatsApp Automation Platform</strong></p>
  <p>
    WappFlow enables businesses to run massive WhatsApp marketing campaigns, build self-serve chatbots, and manage customer communications—securely isolated within a serverless multi-tenant environment.
  </p>
</div>

---

## 📖 Project Overview

WappFlow is designed to handle thousands of WhatsApp messages per minute without dropping requests or hitting API rate limits. By combining **FastAPI**, **Next.js**, and **BullMQ/Redis**, it separates the fast user-facing API from the heavy, IO-bound message delivery pipeline.

Whether you are dispatching a 10,000-contact campaign or instantly responding to an incoming interactive webhook, the system handles retry logic, exponential backoff, deduplication, and dead-letter routing automatically.

### ✨ Key Features

- **Multi-Tenant Isolation**: Complete data separation at the API layer via Firebase Auth tokens, ensuring cross-tenant data leaks are impossible.
- **Robust Queuing Architecture**: Uses Redis + BullMQ for true asynchronous message processing with in-worker throttling (~5 msg/sec default, configurable via `WORKER_RATE_DELAY`).
- **Campaign State Management**: Chunk-based campaign processing with instant "Stop/Cancel" capabilities, per-tenant monthly bulk message quotas, and resend-failed workflows.
- **Resilient Webhook Handlers**: Per-tenant webhook URLs with `X-Hub-Signature-256` HMAC verification, deduplication, and priority chatbot response routing.
- **Intelligent Template Caching**: Tenant-scoped caching for approved WhatsApp templates to reduce round trips to Meta.
- **Time Standardization**: Enforces IST (Indian Standard Time) consistently across logging, database timestamps, and telemetry.
- **Encryption at Rest**: Sensitive secrets (e.g., `meta_app_secret`) are Fernet-encrypted before storage in Postgres.
- **Data Retention**: Automated archive + purge system keeps live tables fast while preserving historical data.
- **Per-Tenant Bulk Quotas**: Monthly message caps with atomic three-layer enforcement (API → worker fan-out → per-message). Retry-aware: only first attempts consume quota.
- **Contact Limit Enforcement**: Configurable per-campaign limit (default 500) with early-stop parsing and dual `/parse` + `/start` enforcement.
- **Redis Optimization**: Minimal Redis command footprint via in-worker throttling, Lua token buckets, in-memory caching, and tuned BullMQ polling intervals.
- **Dynamic Chatbot System** *(Phase 17)*: Fully database-driven button→template mappings, enhanced keyword rules with `match_type` + `response_type`, and per-tenant fallback template with configurable cooldown hours. No hardcoded defaults.

---

## 🏗 System Architecture

WappFlow separates concerns into three primary domains: **Frontend**, **API Server**, and **Background Workers**.

```text
┌─────────────────┐       ┌──────────────┐         ┌───────────────┐
│   Next.js 14    │       │   FastAPI    │         │   Worker(s)   │
│   (Frontend)    │ ────▶│  (Backend)   │ ────▶   │ BullMQ / Async│
└─────────────────┘       └──────┬───────┘         └──────┬────────┘
                                 │                        │
                      ┌──────────┴──────────┐             │
                      │                     │             │
               ┌──────▼──────┐     ┌────────▼──┐     ┌────▼──────────┐
               │ Neon Postgres│    │   Redis   │     │ WhatsApp API  │
               │ (Serverless) │    │ (BullMQ)  │     │ (Meta Cloud)  │
               └─────────────┘     └───────────┘     └───────────────┘
                       ▲                               ▲
                       │                               │
                       └───────────────────────────────┘
                           Webhook Callbacks
```

### Tech Stack

| Category | Technologies |
| --- | --- |
| **Frontend** | Next.js 14, React 18, Tailwind CSS, shadcn/ui, Radix UI, Framer Motion |
| **Backend API** | FastAPI (Python 3.11+), Uvicorn, Pydantic |
| **Queues / Caching** | Redis, BullMQ (Python Port) |
| **Database** | Neon Postgres (Serverless PostgreSQL) via `psycopg3` + connection pooling |
| **Authentication** | Firebase Auth (JWT verification middleware) |
| **Encryption** | Fernet (AES-128-CBC) for secrets at rest |
| **Deployment** | Vercel (frontend), Render (backend), Docker Compose (local) |

---

## 📬 Messaging Pipeline

The core value of WappFlow lies in its non-blocking message pipeline.

### 1. The Bulk Campaign Flow

When a user uploads a spreadsheet of contacts and starts a campaign, the system does not loop over contacts in memory. Instead, it relies on a multi-stage queue process:

```text
User 
 │ 1. Uploads CSV
 ▼
API (FastAPI) 
 │ 2. Validates user, checks monthly quota
 │ 3. Creates Campaign & Recipients in Postgres
 │ 4. Enqueues job to 'campaign_queue'
 ▼
Redis
 │ 5. Persists job
 ▼
Worker (campaign_queue)
 │ 6. Reads quota remaining, caps fan-out
 │ 7. Dispatches individual jobs to 'message_queue'
 │ 8. Marks excess recipients as 'quota_exceeded'
 ▼
Worker (message_queue)
 │ 9.  Atomic quota consume (first attempt only; retries skip)
 │ 10. In-worker throttle (~5 msg/sec via asyncio.sleep)
 │ 11. Calls Meta WhatsApp Cloud API
 │ 12. On success: Updates recipient status in Postgres
 │ 13. On failure: Auto-retries with exponential backoff
 ▼
WhatsApp Cloud API
```

### 2. The Webhook / Chatbot Flow

When a user replies to a WappFlow business number, Meta fires a webhook back to our system. Because response speed reduces Meta's pricing for conversational windows, webhook replies jump the queue:

```text
WhatsApp API
 │ 1. Sends payload to /api/webhook/{tenant_id}
 ▼
API (FastAPI)
 │ 2. Verifies HMAC-SHA256 signature (per-tenant encrypted secret)
 │ 3. Deduplicates by Message ID
 │ 4. Evaluates 3-layer Chatbot Engine:
 │      Layer 1 — Button→Template Mappings (DB-backed, cached 1h)
 │      Layer 2 — Keyword Rules (exact/contains/starts_with, cached 6h)
 │      Layer 3 — Fallback Template (configurable cooldown per tenant)
 │ 5. Enqueues a HIGH-PRIORITY (Priority: 0) job to 'message_queue'
 ▼
Worker (message_queue)
 │ 6. Bypasses bulk messages in queue
 │ 7. Instantly dispatches reply via WhatsApp API
 ▼
User receives instant reply
```

---

## 🤖 Chatbot System (Phase 17)

The chatbot has been completely redesigned in Phase 17 from hardcoded Python dicts to a fully dynamic, database-driven architecture.

### 3-Layer Decision Engine

| Priority | Layer | Source | Cache TTL |
|----------|-------|--------|-----------|
| **1 (Highest)** | Button→Template Mappings | `chatbot_button_mappings` table | 1 hour |
| **2** | Keyword Rules | `chatbot_rules` table | 6 hours |
| **3 (Fallback)** | Fallback Template | `chatbot_config.fallback_template_name` | 6 hours |

### Button Mappings

- Stored in the `chatbot_button_mappings` table — fully per-tenant, zero hardcoded defaults.
- Match is performed on `button_text` (exact string match, case-sensitive after `.strip()`).
- CRUD API: `GET/POST/PUT/DELETE /api/chatbot/button-mappings`
- Priority-ordered: higher `priority` value wins when multiple mappings exist.
- Cache is invalidated immediately on any create/update/delete.

### Keyword Rules

Enhanced in Phase 17 with two new fields:

| Field | Values | Description |
|-------|--------|-------------|
| `match_type` | `exact`, `contains`, `starts_with` | How to match the incoming message against the keyword |
| `response_type` | `text`, `template` | Whether to reply with raw text or a WhatsApp template |

### Fallback Template

Configured in `chatbot_config` per tenant:

| Field | Default | Description |
|-------|---------|-------------|
| `fallback_template_name` | `""` (disabled) | Template to send when no rule matches. Empty = no fallback. |
| `fallback_cooldown_hours` | `24` | Minimum hours between fallback sends to the same contact |

The fallback cooldown is enforced via the `user_triggers` table — each contact gets at most 1 fallback message per cooldown window.

---

## 🗄 Queue Architecture (Redis + BullMQ)

WappFlow embraces asynchronous job processing to overcome common Node/Python bottlenecks.

We use **3 primary queues**:

1. **`campaign_queue`**: A lightweight queue. A job here represents "Launch Campaign X". The worker picks this up, reads quota remaining, caps fan-out, and floods the message queue.
2. **`message_queue`**: The heavy-lifter. Processes individual API calls to Meta. Features:
   - **In-Worker Throttle**: `asyncio.sleep(WORKER_RATE_DELAY)` between jobs (~5 msg/sec default). Replaces the former BullMQ limiter for minimal Redis overhead.
   - **Retry & Backoff**: Configured for 3 attempts (default) with `exponential` delay starting at 5 seconds.
   - **Idempotency**: Utilizes calculated unique IDs to prevent duplicate sends if a worker crashes during execution.
   - **Priority Routing**: Chatbot webhook replies are queued as priority `0` (highest), ensuring customer support isn't delayed by a marketing blast.
   - **Per-Tenant Token Bucket**: 10 msg/sec per tenant (burst 20) for fair resource sharing. Uses in-memory cache (5s TTL) to reduce Redis calls.
   - **Retry-Aware Quota**: Only the first attempt for each recipient consumes quota. Retries skip quota consumption to prevent inflated usage.
3. **`dead_letter_queue`**: Messages that exhaust all retries are gracefully moved here with the `permanently_failed` event, ensuring the main queue isn't clogged by continuously failing payloads.

---

## 💻 Getting Started (Local Development)

You can spin up the full WappFlow environment within minutes.

### Prerequisites

- Python 3.11+
- Node.js 18+
- Docker (for Redis)
- Postgres connection string (Neon DB or local)
- Firebase project with Auth enabled

### 1. Clone & Install
```bash
git clone <your-repo>
cd wappflow-neon

# Using our custom concurrent install script:
npm run install:all
```

### 2. Configure Environment Variables

**Backend (`backend/.env`)**
```env
# Copy from the comprehensive example:
cp backend/.env.example backend/.env

# Required values to fill in:
DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require
WEBHOOK_VERIFY_TOKEN=your_custom_secure_token
ENCRYPTION_KEY=<generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

REDIS_HOST=localhost
REDIS_PORT=6379

# Optional tuning
MAX_VALID_CONTACTS=500        # Max contacts per campaign (default: 500)
WORKER_RATE_DELAY=0.2         # Seconds between messages in worker (default: 0.2)
RETENTION_ENABLED=true        # Enable data archival background job
PURGE_ENABLED=true            # Enable archive purge (run after 90+ days of retention)
```

**Frontend (`frontend/.env.local`)**
```env
NEXT_PUBLIC_API_URL=http://localhost:5000
NEXT_PUBLIC_FIREBASE_API_KEY=xxx
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=xxx
NEXT_PUBLIC_FIREBASE_PROJECT_ID=xxx
```

*(Place your Firebase Admin SDK credential file at `backend/firebase-service-account.json`)*

### 3. Apply Database Schema
```bash
cd backend

# Apply full schema (creates all tables including Phase 17)
python apply_schema.py

# Apply Phase 17 chatbot migration (safe to run on both fresh and existing DBs)
python run_migration.py
```

> **Note:** `run_migration.py` applies `migration_chatbot_redesign.sql` which:
> - Adds `fallback_template_name` + `fallback_cooldown_hours` to `chatbot_config`
> - Adds `response_type` + `match_type` to `chatbot_rules`
> - Creates the `chatbot_button_mappings` table
> - Creates the `chatbot_flows` table (future flow builder)
> - Migrates any existing JSONB button mappings from `chatbot_config` to the new table

### 4. Start Infrastructure

WappFlow requires Redis. We provide a docker-compose file for this:
```bash
docker-compose up redis -d
```

### 5. Start the Application

You need to run BOTH the auto-reloading API+Frontend and the worker script.

**Terminal 1: Start API**
```bash
cd backend
pip install -r requirements.txt
python run_server.py
```

**Terminal 2: Start the Background Worker**
```bash
# Workers are required; without them, queues back up and no WhatsApps are sent.
cd backend
python worker_main.py
```

**Terminal 3: Start Frontend**
```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000` → register/login → configure WhatsApp settings → you're ready to test.

---

## 🚢 Deployment Guide

WappFlow's separated architecture makes it highly scalable in production.

### Frontend
Deploy the `frontend/` directory directly to **Vercel**.
- Override root directory to `frontend`.
- Add all `NEXT_PUBLIC_` variables.

### Backend (API + Workers)
The backend is best deployed via **Docker** on a VPS (AWS EC2, DigitalOcean) or a PaaS like **Render** or **Railway**.

1. **Postgres**: Provision a serverless Postgres instance (e.g., Neon DB).
2. **Redis**: Provision a managed Redis instance with TLS (e.g., Upstash). Use `rediss://` (double `s`) URL scheme.
3. **API Service**: Run the Uvicorn web server.
   `uvicorn main:app --host 0.0.0.0 --port 5000`
4. **Worker Service**: Run the Python worker as an independent background process.
   `python worker_main.py`

> **Important:** Both the API service and the Worker service must have `MAX_VALID_CONTACTS` set to the same value for consistent contact limit enforcement.

### 📈 Scaling the System
- **Scale the API** horizontally to handle thousands of incoming Webhooks per second without sweating.
- **Scale Workers**: To increase campaign sending speed (assuming Meta lifts your rate limits), simply run multiple `python worker_main.py` containers. BullMQ safely distributes jobs across all active workers.

---

## 🐛 Troubleshooting

| Issue | Cause / Solution |
| --- | --- |
| **Campaign Stuck at 0 Sent** | Worker isn't running. Ensure you are running `worker_main.py` in a separate terminal. |
| **Webhooks not triggering** | Meta Cloud cannot reach localhost. Run `ngrok http 5000` and update your Meta App Webhook URL to `https://<ngrok>/api/webhook/{tenant_id}`. |
| **Invalid Signature (401)** | `meta_app_secret` does not match the Meta dashboard, or the trailing whitespace is polluting the `.env`. Check `webhook_sig_rejected` logs. |
| **Redis TLS Errors** | Cloud Redis (Upstash, Redis Cloud) requires TLS. Use `rediss://` (double `s`) in `REDIS_URL`, not `redis://`. |
| **Stale Settings/Rules** | Settings and chatbot rules are cached for 6 hours. After manual DB changes, restart the server or wait for cache expiry. Button mappings cache expires in 1 hour. |
| **Quota Not Updating** | Quota is tracked per `YYYY-MM` month key. Frontend auto-refreshes every 10 seconds. |
| **Button Mapping Not Triggering** | The match is exact and case-sensitive (after `.strip()`). Ensure the `button_text` in the DB exactly matches the text received in the WhatsApp payload. Check logs for `button_match` events. |
| **Migration Errors (`column does not exist`)** | Run `python run_migration.py` to apply the Phase 17 schema changes. If the error persists, check `information_schema.columns` for `chatbot_config` to verify the migration was applied. |
| **Fallback Template Sends Too Often** | Adjust `fallback_cooldown_hours` in chatbot settings (default: 24). Check the `user_triggers` table for existing cooldown records. |

---

## 📂 Project Structure

```text
├── backend/
│   ├── main.py                      # FastAPI entry point, CORS, Health Checks, Lifespan
│   ├── worker_main.py               # BullMQ Worker definition (campaign + message processing)
│   ├── database.py                  # Async Postgres pool (psycopg3), transaction helper
│   ├── schema.sql                   # Complete DDL for all live tables (Phase 17 schema)
│   ├── migration_chatbot_redesign.sql  # Phase 17 migration: new columns, tables, data migration
│   ├── run_migration.py             # Runs migration_chatbot_redesign.sql against DATABASE_URL
│   ├── retention_schema.sql         # DDL for archive tables + daily_message_stats
│   ├── apply_schema.py              # Applies schema.sql + retention_schema.sql to database
│   ├── retention.py                 # Data retention engine (archive + purge) + CLI
│   ├── auth_middleware.py           # Firebase Auth token verification middleware
│   ├── rate_limit.py                # Redis rate limiter (API + worker token bucket)
│   ├── cache.py                     # In-memory TTL cache (6h default); includes button_mappings_key
│   ├── store.py                     # Cached read/write layer for settings + chatbot config
│   ├── observability.py             # Structured JSON logging
│   ├── routers/                     # Controllers
│   │   ├── settings.py              # WhatsApp API credentials CRUD
│   │   ├── bulk_message.py          # Campaign lifecycle (start/stop/status/delete/quota)
│   │   ├── file_forward.py          # Single + bulk file sending
│   │   ├── chatbot.py               # Rules, settings, button mappings (Phase 17: fully dynamic)
│   │   ├── logs.py                  # Message log retrieval + CSV export
│   │   └── webhook.py               # Per-tenant + legacy webhook routes, 3-layer chatbot engine
│   ├── services/                    # Abstractions
│   │   ├── whatsapp.py              # WhatsApp Cloud API client (retry, rate-limit aware)
│   │   ├── queue_manager.py         # BullMQ queue helpers (campaign/message/file-forward/DLQ)
│   │   ├── template_builder.py      # Template component cache + parameter builder
│   │   └── chatgpt.py               # (Reserved) ChatGPT integration
│   ├── db_layer/                    # Postgres repository adapters
│   │   ├── tenants.py               # Tenant CRUD + webhook token lookup
│   │   ├── campaigns.py             # Campaign CRUD + status transitions
│   │   ├── campaign_recipients.py   # Recipient status machine + count_by_status()
│   │   ├── campaign_counters.py     # Sharded sent/failed counters (legacy — kept for compat)
│   │   ├── messages.py              # Unified message log + archive fallback
│   │   ├── chat_messages.py         # Conversation history
│   │   ├── chatbot.py               # Chatbot config + rules (response_type, match_type)
│   │   ├── chatbot_button_mappings.py  # Phase 17: CRUD + 1h cache for button→template mappings
│   │   ├── webhook_events.py        # Webhook deduplication
│   │   ├── usage_events.py          # Billable usage tracking
│   │   ├── template_cache.py        # Persistent template metadata
│   │   ├── secrets.py               # Runtime token resolution (DB → env fallback)
│   │   ├── encryption.py            # Fernet encrypt/decrypt for secrets at rest
│   │   ├── users.py                 # User trigger rate limiting (configurable cooldown)
│   │   └── quota.py                 # Per-tenant monthly bulk message quota
│   └── utils/
│       ├── phone_utils.py           # E.164 phone normalization (international + India fallback)
│       ├── image_utils.py           # Automatic image compression to ≤5 MB
│       └── time_utils.py            # IST timestamp helpers
├── frontend/                        # Next.js 14 Web Application
│   ├── src/app/                     # App Router pages (dashboard, login, register)
│   ├── src/components/              # Reusable UI components (shadcn/ui based)
│   ├── src/contexts/                # React contexts (auth)
│   ├── src/lib/                     # API client, Firebase config, utilities
│   ├── package.json
│   └── tailwind.config.ts
├── docker-compose.yml               # Local Redis + Full stack orchestration
├── API_Developer_Doc.md             # Complete API reference (all endpoints, schemas, errors)
├── Architecture_Overview.md         # Deep architecture documentation (for onboarding)
├── Technical_Readme.md              # Concise technical summary of every system component
└── package.json                     # Global dependency runner
```

---

## 📚 Documentation

| Document | Purpose | Audience |
|----------|---------|----------|
| **[API_Developer_Doc.md](API_Developer_Doc.md)** | Complete API reference with request/response schemas, error codes, rate limits, encryption details, data retention, quota system, and onboarding quick-start | Backend developers, frontend integrators |
| **[Architecture_Overview.md](Architecture_Overview.md)** | Deep system architecture — multi-tenancy model, request lifecycle, data flows, security layers, queue architecture, retention system, Phase 17 chatbot redesign, and phase history | New team members, architects |
| **[Technical_Readme.md](Technical_Readme.md)** | Concise technical summary of every system component | Quick reference |

---

## 🔒 Security Highlights

- **Firebase Auth** on every request (except webhooks/health)
- **Row-level tenant isolation** on all DB tables
- **Per-tenant HMAC webhook verification** (X-Hub-Signature-256)
- **Fernet encryption at rest** for sensitive secrets (`meta_app_secret`)
- **Redis token-bucket rate limiting** (Lua-based, 1 command per request) + in-worker throttle
- **Input validation** via Pydantic models
- **Contact limit enforcement** (configurable max per campaign via `MAX_VALID_CONTACTS`)
- **CSV injection protection** on exports
- **OpenAPI/Swagger disabled** in production
- **No hardcoded chatbot defaults** — all button mappings and rules are tenant-scoped and DB-driven

---

## 🔄 Phase History (Summary)

| Phase | Key Change |
|-------|------------|
| 1–3 | Core platform: FastAPI, Firebase Auth, BullMQ, chatbot rules |
| 4–6 | Startup validation, Postgres migration, security hardening |
| 7 | Redis rate limiting (sliding window + token bucket) |
| 8 | Data retention: archive tables, `daily_message_stats`, purge system |
| 9 | Per-tenant webhooks, HMAC verification, Fernet encryption |
| 10 | Per-tenant monthly bulk quota: 3-layer atomic enforcement |
| 11 | Documentation refresh |
| 12 | Phone normalization (E.164), image compression (Pillow ≤5 MB), template hardening |
| 13 | Contact limit enforcement: `MAX_VALID_CONTACTS`, Redis contact cache, `/limits` endpoint |
| 14 | Redis command optimization: BullMQ limiter→`asyncio.sleep`, Lua token bucket, in-memory caches |
| 15 | Campaign counter accuracy: `count_by_status()` replaces incremental shards |
| 16 | Retry-aware quota: only first attempt consumes quota |
| **17** | **Chatbot system redesign**: dynamic `chatbot_button_mappings` table, button-text-only matching, `response_type`/`match_type` on rules, per-tenant fallback template + cooldown, `migration_chatbot_redesign.sql`, `run_migration.py`, new button mapping API endpoints |
