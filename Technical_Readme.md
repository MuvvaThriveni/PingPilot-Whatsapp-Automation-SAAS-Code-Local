# WappFlow — Technical Architecture & System Summary

> **Version:** 4.0.0 | **Last Updated:** 2026-04-10  
> **Full Details:** [Architecture_Overview.md](Architecture_Overview.md) | **API Reference:** [API_Developer_Doc.md](API_Developer_Doc.md)

---

## Short Technical Summary

- WappFlow is a **multi-tenant WhatsApp automation platform** where tenants are identified by Firebase Auth UID (`tenant_id`).
- Frontend is built with **Next.js 14** App Router and Firebase client authentication.
- Backend is **FastAPI** with Firebase Admin middleware validating all requests except public webhook and health endpoints.
- Primary database is **Neon PostgreSQL** accessed via `psycopg3` AsyncConnectionPool with connection pooling.
- Asynchronous processing uses **BullMQ** with Redis queues (supports TLS via `rediss://` for cloud providers).
- Core modules: Bulk Campaign Messaging (with monthly quotas), File Forwarding, Webhook Chatbot Automation, Settings, and Logs.
- **Encryption at rest** via Fernet (AES-128-CBC) for sensitive secrets like `meta_app_secret`.
- **Per-tenant webhooks** with HMAC-SHA256 signature verification.
- **Data retention** with automated archive + purge pipeline.
- External integrations: Meta WhatsApp Cloud API, Firebase Authentication, optional OpenAI integration.
- Observability uses structured JSON logging with deep health checks (Postgres, Redis, background tasks, retention status).
- Configuration is environment-variable driven with comprehensive `.env.example`.

---

## 1. Project Structure

### Top Level
| Path | Purpose |
|------|---------|
| `backend/` | FastAPI backend, workers, database layer, services |
| `frontend/` | Next.js 14 application and UI |
| `docker-compose.yml` | Runtime stack for Redis, API, worker |
| `API_Developer_Doc.md` | Complete API reference (1700+ lines) |
| `Architecture_Overview.md` | Deep architecture documentation (900+ lines) |
| `package.json` | Development orchestration |

### Backend Components
| File/Dir | Purpose |
|----------|---------|
| `main.py` | FastAPI entrypoint, middleware stack, lifespan, health check |
| `worker_main.py` | BullMQ workers (campaign + message processing, quota enforcement) |
| `database.py` | Async Postgres pool (psycopg3), transaction helper |
| `schema.sql` | Complete database DDL (20+ tables, Phase 17 schema included) |
| `migration_chatbot_redesign.sql` | **Phase 17 — NEW:** Idempotent migration. Adds columns, creates `chatbot_button_mappings` + `chatbot_flows`, migrates JSONB data. |
| `run_migration.py` | **Phase 17 — NEW:** Runs `migration_chatbot_redesign.sql` via `DATABASE_URL`. |
| `retention_schema.sql` | Archive tables + `daily_message_stats` DDL |
| `retention.py` | Data retention engine (archive + purge) + CLI |
| `auth_middleware.py` | Firebase Auth token verification middleware |
| `rate_limit.py` | Redis rate limiter (API sliding window + worker token bucket + global cooldown) |
| `cache.py` | In-memory TTL caching (6h default). Keys: `chatbot_config`, `chatbot_rules`, `button_mappings` |
| `store.py` | Cached read/write layer for settings + chatbot config (`fallback_template_name`, `fallback_cooldown_hours`) |
| `observability.py` | Structured JSON logging |
| `startup_checks.py` | Environment validation on boot |
| `startup_cache.py` | Pre-warm caches on startup |
| `db_layer/` | 16 Postgres repository adapter modules (incl. new `chatbot_button_mappings.py`) |
| `services/` | Business logic (queue_manager, whatsapp, template_builder, chatgpt) |
| `routers/` | 6 API endpoint modules (settings, bulk_message, file_forward, chatbot, logs, webhook) |
| `utils/` | Shared utilities: phone normalization (E.164), image compression (≤5 MB), IST time helpers |

### Frontend Components
| File/Dir | Purpose |
|----------|---------|
| `src/app/` | Next.js App Router pages (dashboard, login, register) |
| `src/lib/firebase.ts` | Firebase client setup |
| `src/lib/api.ts` | Axios API client with Firebase token interceptor |
| `src/contexts/AuthContext.tsx` | Authentication state management |
| `src/components/` | UI components (shadcn/ui based) |

---

## 2. System Architecture

**Pattern:** Next.js frontend → FastAPI backend → BullMQ worker tier → Redis + PostgreSQL

**Runtime flow:**
1. Frontend authenticates via Firebase and communicates through Axios with Bearer token
2. Backend processes API requests through middleware (CORS → Firebase Auth → Rate Limit)
3. Write operations enqueue background tasks to Redis (BullMQ)
4. Worker processes queued jobs and interacts with WhatsApp Cloud API
5. Redis provides queue infrastructure + rate limiting state
6. PostgreSQL (Neon) stores all tenant data, campaigns, messages, logs, and quotas

---

## 3. Core Product Modules

### Bulk Campaign System
- Uploads Excel/CSV contact lists and sends WhatsApp templates
- Worker processes recipients asynchronously with rate limiting
- **Monthly quota enforcement** at three levels (API → campaign worker → per-message). Retry-aware: only first attempts consume quota.
- Supports scheduling, pause/resume, resend-failed workflows
- **Contact limit enforcement** (default 500, configurable via `MAX_VALID_CONTACTS`) with early-stop parsing
- **Parsed contacts Redis caching** (avoids re-parsing same file between `/parse` and `/start`)
- Recipient status machine: `pending → queued → processing → submitted → sent/failed/quota_exceeded`
- **Authoritative counter accuracy**: API responses derive counts from `count_by_status()` instead of incremental counters

### File Forwarding
- Send documents, images, and PDFs to individual or bulk recipients
- Files uploaded once, then URL shared with all recipients via queue

### Webhook Chatbot (Phase 17 Redesign)
- Per-tenant webhook URLs with HMAC-SHA256 signature verification
- **Fully dynamic, DB-driven** three-layer response engine (no hardcoded defaults):
  1. **Button→Template Mappings** — exact text match against `chatbot_button_mappings` table (per-tenant, 1h cache)
  2. **Keyword Rules** — configurable `match_type` (`exact`/`contains`/`starts_with`) + `response_type` (`text`/`template`)
  3. **Fallback Template** — tenant-configurable `fallback_template_name` with `fallback_cooldown_hours` (default 24h)
- Priority queue routing (chatbot replies bypass bulk campaign traffic)
- Button ID matching **removed** — text-only matching

### Settings
- Tenant WhatsApp credentials CRUD with connectivity testing
- Sensitive secrets (meta_app_secret) encrypted at rest via Fernet
- Usage statistics with quota status

### Logs
- Unified message logs across all products with filtering and cursor pagination
- CSV export with formula injection protection
- Log statistics (total/sent/delivered/failed/read)

---

## 4. API Endpoints Summary

### Public Endpoints (No Auth)
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Deep health check (Postgres, Redis, background tasks, retention/purge status) |
| GET | `/api/webhook/{tenant_id}` | Per-tenant Meta webhook verification |
| POST | `/api/webhook/{tenant_id}` | Per-tenant incoming webhooks (HMAC verified) |

### Settings Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/settings/whatsapp` | Get WhatsApp configuration |
| POST | `/api/settings/whatsapp` | Save WhatsApp credentials |
| POST | `/api/settings/whatsapp/test` | Test WhatsApp connectivity |
| GET | `/api/settings/usage` | Usage stats + quota status |

### Bulk Messaging Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/bulk-message/templates` | Fetch approved templates |
| GET | `/api/bulk-message/quota` | Get quota status |
| POST | `/api/bulk-message/parse` | Parse contacts file |
| POST | `/api/bulk-message/start` | Start campaign (quota-enforced) |
| POST | `/api/bulk-message/stop/{id}` | Stop running campaign |
| GET | `/api/bulk-message/status/{id}` | Get campaign status |
| GET | `/api/bulk-message/campaigns` | List all campaigns |
| GET | `/api/bulk-message/campaigns/{id}/details` | Get campaign + recipients |
| POST | `/api/bulk-message/campaigns/{id}/resend-failed` | Resend failed recipients |
| DELETE | `/api/bulk-message/campaigns/{id}` | Delete campaign |
| GET | `/api/bulk-message/limits` | Get backend-configured limits (max contacts) |

### File Forward Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/file-forward/parse-contacts` | Parse contacts file |
| POST | `/api/file-forward/send` | Send single file |
| POST | `/api/file-forward/send-bulk` | Send bulk file |

### Chatbot Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/chatbot/settings` | Get chatbot config (incl. `fallback_template_name`, `fallback_cooldown_hours`) |
| PUT | `/api/chatbot/settings` | Update chatbot config |
| GET | `/api/chatbot/rules` | Get all rules (incl. `response_type`, `match_type`) |
| POST | `/api/chatbot/rules` | Create rule |
| PUT | `/api/chatbot/rules/{id}` | Update rule |
| DELETE | `/api/chatbot/rules/{id}` | Delete rule |
| **GET** | **`/api/chatbot/button-mappings`** | **List button→template mappings (Phase 17)** |
| **POST** | **`/api/chatbot/button-mappings`** | **Create button mapping (Phase 17)** |
| **PUT** | **`/api/chatbot/button-mappings/{id}`** | **Update button mapping (Phase 17)** |
| **DELETE** | **`/api/chatbot/button-mappings/{id}`** | **Delete button mapping (Phase 17)** |
| GET | `/api/chatbot/users` | List chat users (cached 15s) |
| GET | `/api/chatbot/conversations` | All conversations |
| GET | `/api/chatbot/conversations/{phone}` | User conversations |

### Logs Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/logs` | Message logs (filtered, paginated) |
| GET | `/api/logs/export` | CSV export (max 5000 records) |
| GET | `/api/logs/stats` | Message statistics |

---

## 5. Database Architecture

**Engine:** Neon Postgres (serverless) via `psycopg3` + async connection pooling

### Key Tables
| Table | Purpose | Key Design |
|-------|---------|------------|
| `tenants` | Tenant config + credentials | PK: `tenant_id`, includes `bulk_quota_limit` |
| `chatbot_config` | Per-tenant chatbot settings | 1:1 with `tenants`. Columns: `fallback_template_name`, `fallback_cooldown_hours` |
| `chatbot_rules` | Keyword auto-reply rules | Columns: `response_type` (`text`/`template`), `match_type` (`exact`/`contains`/`starts_with`) |
| `chatbot_button_mappings` | **NEW (Phase 17)** Button→template mappings | Unique index on `(tenant_id, button_text)`. Replaces JSONB columns. |
| `chatbot_flows` | **NEW (Phase 17, future)** Visual flow builder | `flow_data` JSONB. Not yet exposed via API. |
| `campaigns` | Campaign metadata | Composite PK: `(tenant_id, campaign_id)` |
| `campaign_recipients` | Per-recipient status | Composite PK: `(tenant_id, campaign_id, contact_phone)` |
| `messages` | Unified message log | Unique index on `(tenant_id, wa_message_id)` |
| `chat_messages` | Conversation history | Indexed by phone + timestamp |
| `webhook_events` | Deduplication | Composite PK: `(tenant_id, event_id)` |
| `user_triggers` | Fallback cooldown tracking | One row per `(tenant_id, phone)`. TTL controlled by `fallback_cooldown_hours`. |
| `tenant_quota_usage` | Monthly quota tracking | Composite PK: `(tenant_id, month_key)` |
| `daily_message_stats` | Pre-aggregated analytics | Permanent, powers dashboard after archival |

### Archive Tables (Data Retention)
`messages_archive`, `chat_messages_archive`, `webhook_events_archive`, `usage_events_archive` — mirror live table schemas with additional `archived_at` column.

---

## 6. Message & Job Processing

### Queues
| Queue | Purpose |
|-------|---------|
| `campaign_queue` | Expands campaigns into message jobs (quota-capped) |
| `message_queue` | Sends WhatsApp messages (rate-limited, retries, idempotent) |
| `dead_letter_queue` | Stores jobs that exhausted all retries |

### Rate Limiting Stack
| Layer | Mechanism | Scope |
|-------|-----------|-------|
| API Heavy | Redis Lua token bucket (10 req/min) | Per-tenant, write actions only |
| API General | Redis Lua token bucket (300 req/min) | Per-tenant, reads |
| Worker In-Process | `asyncio.sleep(WORKER_RATE_DELAY)` (default 200ms = ~5 msg/sec) | Per-worker. Replaces former BullMQ limiter (removed in Phase 14). |
| Worker Tenant | Token bucket (10 msg/sec, burst 20) + in-memory cache (5s TTL) | Per-tenant |
| Worker Cooldown | Global pause on repeated 429s + in-memory cache (2s TTL) | All workers |

> **Note:** The BullMQ `limiter` has been intentionally removed (Phase 14). It injected ~6 extra Redis Lua commands per job, causing excessive Redis command consumption on cloud providers. The in-worker `asyncio.sleep` achieves the same rate control with zero Redis overhead.

### Retry Policy
- Default 3 attempts with exponential backoff (5s → 10s → 20s)
- Non-retryable errors (template not found) → immediate fail
- Terminal failure → dead letter queue

---

## 7. Security Architecture

| Layer | Mechanism |
|-------|-----------|
| **Authentication** | Firebase Auth ID tokens verified server-side on every request |
| **Authorization** | Row-level tenant isolation — all DB queries scoped by `tenant_id` |
| **Webhook integrity** | Per-tenant HMAC-SHA256 verification with encrypted `meta_app_secret` |
| **Encryption at rest** | Fernet (AES-128-CBC) for sensitive secrets. `"enc:"` prefix for encrypted values |
| **Token storage** | Stored in Postgres, resolved at runtime. Never returned to frontend or logged |
| **Rate limiting** | Multi-tier: API (Lua token bucket) + worker (in-process throttle) + global cooldown. In-memory caches reduce Redis overhead. |
| **Input validation** | Pydantic models on all request bodies |
| **Contact limits** | Configurable per-campaign max (default 500) enforced at `/parse` and `/start` |
| **CSV injection** | Export values sanitized against DDE formula injection |
| **File upload** | 16 MB hard limit enforced server-side |
| **CORS** | Explicit origin allowlist; `localhost:3000` only in non-production |
| **API docs** | OpenAPI/Swagger disabled in production |

---

## 8. Background Tasks

| Task | Interval | Purpose |
|------|----------|---------|
| `periodical_cleanup` | Every 6 hours | Deletes transient data older than 30 days |
| `periodical_scheduler` | Every 60 seconds | Processes scheduled campaigns |
| `periodic_archive_runner` | Every 24 hours | Archive old data + optional purge |

All three run as `asyncio.Task` instances within the FastAPI lifespan and are gracefully cancelled on shutdown.

---

## 9. External Integrations

| Integration | Purpose |
|-------------|---------|
| **Meta WhatsApp Cloud API** | Message sending, template management, media uploads |
| **Firebase Authentication** | Frontend login + backend token validation |
| **OpenAI** | Optional AI chatbot integration (currently reserved) |

---

## 10. Configuration System

### Required Backend Environment Variables
| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Postgres connection string (Neon DB) |
| `WEBHOOK_VERIFY_TOKEN` | Webhook verification token |
| `ENCRYPTION_KEY` | Fernet key for secrets at rest |
| `REDIS_HOST` / `REDIS_PORT` | Redis connection (or use `REDIS_URL`) |
| Firebase service account JSON | `backend/firebase-service-account.json` |

### Required Frontend Variables
| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_URL` | Backend URL |
| `NEXT_PUBLIC_FIREBASE_API_KEY` | Firebase config |
| `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN` | Firebase config |
| `NEXT_PUBLIC_FIREBASE_PROJECT_ID` | Firebase config |

See `backend/.env.example` for the complete 160-line reference with all optional variables and descriptions.

---

## 11. Deployment Architecture

```
Vercel ──────── Next.js frontend (CDN + SSR)
  │  HTTPS
  ▼
Render ──────── FastAPI (port 5000) + BullMQ Worker
  ├──→ Neon Postgres (serverless, connection pooling)
  └──→ Redis (managed, TLS via rediss://)
```

Docker Compose available for local development (Redis + API + Worker).

---

## 12. Observability

- Structured JSON logging via `observability.log_event()`
- Log fields: `timestamp`, `level`, `op`, `tenant`, `campaign`, `phone`, `ms`, `detail`
- No sensitive data logged (tokens, keys, message bodies)
- Timed operations via `timed_op()` context manager
- Dedicated retention/purge log events with per-batch metrics
- Deep health check endpoint with component-level status

---

## 13. Phase History

| Phase | What Was Added |
|-------|----------------|
| **1–3** | Core platform: FastAPI, Firebase Auth, BullMQ queues, campaign management, chatbot rules |
| **4** | Startup checks, environment validation, startup cache pre-warming |
| **5** | Postgres migration (from Firestore), connection pooling, cursor pagination |
| **6** | Security hardening: removed token logging, Pydantic validation, CSV injection protection |
| **7** | Redis rate limiting: API middleware (heavy/general tiers), worker token bucket, global cooldown |
| **8** | Data retention: archive tables, daily_message_stats, background cron, purge system |
| **9** | Per-tenant webhooks, HMAC signature verification, Fernet encryption at rest |
| **10** | Per-tenant monthly bulk message quota with atomic three-layer enforcement |
| **11** | Comprehensive documentation refresh (README, API docs, architecture docs) |
| **12** | Phone normalization (E.164), image compression (≤5 MB), template hardening (validation + CDN checks), media upload pipeline |
| **13** | Contact limit enforcement (500 max/campaign), early-stop parsing, parsed contacts Redis caching, `GET /limits` endpoint |
| **14** | Redis command optimization: BullMQ limiter removal, in-worker sleep throttle, Lua token bucket for API, in-memory caching for rate limiters, BullMQ polling tuning |
| **15** | Campaign counter accuracy: `count_by_status()` replaces incremental counters, new `pending_count` + `quota_exceeded_count` API fields |
| **16** | Quota counting fix: only first attempt consumes quota (retries skip), eliminates inflated usage counts |
| **17** | **Chatbot system redesign:** `chatbot_button_mappings` table replaces JSONB columns + hardcoded Python dicts. Button ID matching removed. Keyword rules enhanced with `response_type` + `match_type`. Fallback template + cooldown configurable per tenant. New files: `db_layer/chatbot_button_mappings.py`, `migration_chatbot_redesign.sql`, `run_migration.py`. 4 new API endpoints for button mappings. |
