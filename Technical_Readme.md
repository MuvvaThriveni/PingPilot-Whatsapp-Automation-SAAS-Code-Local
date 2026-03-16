WappFlow Technical Architecture & System Audit

WappFlow — Technical Architecture & System Audit Documentation

Short Technical Summary
• WappFlow is a multi-tenant WhatsApp automation platform where tenants are identified by Firebase Auth UID (tenant_id).
• Frontend is built with Next.js 14 App Router and Firebase client authentication.
• Backend is FastAPI with Firebase Admin middleware validating all requests except public webhook and health endpoints.
• Primary database is Neon PostgreSQL accessed via psycopg AsyncConnectionPool.
• Asynchronous processing uses BullMQ with Redis queues.
• Core modules include Bulk Campaign Messaging, File Forwarding, Webhook Chatbot Automation, Settings, and Logs.
• External integrations include Meta WhatsApp Cloud API, Firebase Authentication, and optional OpenAI integration.
• Observability uses structured JSON logging with additional health checks.
• Configuration is environment-variable driven.

1. Project Structure Analysis
Top Level:
backend/ – FastAPI backend, workers, database layer, services
frontend/ – Next.js application and UI
docker-compose.yml – runtime stack for Redis, API, worker
architecture diagrams – architecture visualization
package.json – development orchestration

Backend Components
main.py – FastAPI entrypoint
worker_main.py – background workers
database.py – database connection pool
schema.sql – database schema
db_layer – database access modules
services – integrations and business logic
routers – API endpoints
auth_middleware – Firebase authentication middleware
cache.py – TTL in-memory caching
observability.py – structured logging

Frontend Components
src/app – Next.js App Router pages
src/lib/firebase.ts – Firebase client setup
src/lib/api.ts – Axios API client
src/contexts/AuthContext.tsx – authentication state management
src/components – UI components

2. System Architecture
Architecture pattern:
Next.js frontend + FastAPI backend + BullMQ worker tier + Redis + PostgreSQL.

Runtime components:
Frontend authenticates via Firebase and communicates through Axios.
Backend processes API requests and enqueues background tasks.
Worker processes queued jobs and interacts with WhatsApp API.
Redis provides queue infrastructure.
PostgreSQL stores tenants, campaigns, messages, and logs.

3. Core Product Modules
Bulk Campaign System
Uploads Excel/CSV contact lists and sends WhatsApp templates.
Worker processes recipients and sends messages asynchronously.

File Forwarding
Allows sending documents or images to individual or bulk recipients.

Webhook Chatbot
Processes inbound WhatsApp messages and triggers automated responses.

Settings
Stores tenant WhatsApp credentials and performs connectivity tests.

Logs
Provides message logs, analytics, and CSV exports.

4. API Layer Documentation
Public Endpoints
GET /api/health – system health status
GET /api/webhook – webhook verification
POST /api/webhook – receive inbound WhatsApp messages

Settings Endpoints
GET /api/settings/whatsapp
POST /api/settings/whatsapp
POST /api/settings/whatsapp/test
GET /api/settings/usage

File Forward Endpoints
POST /api/file-forward/parse-contacts
POST /api/file-forward/send
POST /api/file-forward/send-bulk

Bulk Messaging Endpoints
GET /api/bulk-message/templates
POST /api/bulk-message/parse
POST /api/bulk-message/start
POST /api/bulk-message/stop/{campaign_id}
GET /api/bulk-message/status/{campaign_id}
GET /api/bulk-message/campaigns
DELETE /api/bulk-message/campaigns/{campaign_id}

Chatbot Endpoints
GET /api/chatbot/settings
PUT /api/chatbot/settings
GET /api/chatbot/rules
POST /api/chatbot/rules
PUT /api/chatbot/rules/{rule_id}
DELETE /api/chatbot/rules/{rule_id}

Logs Endpoints
GET /api/logs
GET /api/logs/export
GET /api/logs/stats

5. Database Architecture
Primary database: PostgreSQL.

Key Tables:
tenants – stores tenant configuration
campaigns – campaign metadata
campaign_recipients – recipients list per campaign
messages – message history
chat_messages – chat history
webhook_events – inbound webhook deduplication
usage_events – analytics events
template_cache – WhatsApp template metadata
user_triggers – trigger cooldown tracking

Relationships:
tenants is root entity with cascade relationships to child tables.

6. Message & Job Processing
Queues:
campaign_queue – expands campaigns into message jobs
message_queue – sends WhatsApp messages
dead_letter_queue – stores failed jobs

Features:
Rate limiting
Retry with exponential backoff
Idempotent message recording

7. External Integrations
Meta WhatsApp Cloud API
Endpoints for sending messages, uploading media, fetching templates.

Firebase Authentication
Frontend login and backend token validation.

OpenAI
Optional AI chatbot integration (currently disabled).

8. Authentication & Multi-tenancy
Authentication uses Firebase ID tokens.
Backend middleware validates token and extracts tenant ID.
Every database query is tenant-scoped.

9. Configuration System
Backend Environment Variables:
DATABASE_URL
REDIS_HOST
REDIS_PORT
QUEUE_RATE_LIMIT
QUEUE_RETRY_ATTEMPTS
META_APP_SECRET
WEBHOOK_VERIFY_TOKEN

Frontend Variables:
NEXT_PUBLIC_FIREBASE_API_KEY
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN
NEXT_PUBLIC_FIREBASE_PROJECT_ID
NEXT_PUBLIC_API_URL

10. Frontend Architecture
Next.js 14 App Router application.
Firebase authentication.
Axios API client with token interceptor.
Dashboard pages for campaigns, chatbot, logs, settings.

11. Data Flow
Campaign Workflow:
Upload contacts → create campaign → enqueue jobs → worker sends messages → update counters.

Webhook Workflow:
Receive webhook → validate → store inbound message → determine response → enqueue reply job.

File Forward Workflow:
Upload file → send message(s) → log results.

12. Performance & Scalability
Strengths:
Asynchronous workers
Queue-based architecture
Cursor pagination

Potential Bottlenecks:
In-memory cache per instance
Webhook processing synchronous until enqueue

13. Error Handling & Reliability
Retry logic with backoff.
Dead-letter queue for permanent failures.
Transaction rollback on DB errors.

14. Security Analysis
Critical:
Secrets committed in repo.
Tenant tokens stored in plaintext.

Medium:
Webhook verification optional.

Low:
CSV injection protection and structured logging implemented.

15. Code Quality & Technical Debt
Architecture diagram outdated.
Duplicate campaign processing paths.
Pagination inconsistencies between frontend and backend.

16. Deployment Architecture
Docker services:
Redis
API server
Worker

Database hosted externally on Neon.

17. Observability
Structured JSON logs.
Health endpoint checks database connectivity.

18. Future Improvements
Security hardening.
Shared caching layer.
Better monitoring and metrics.
Queue monitoring dashboards.
Improved developer tooling.

