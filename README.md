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
- **Robust Queuing Architecture**: Uses Redis + BullMQ for true asynchronous message processing, preventing WhatsApp rate-limit bans (max 80 req/sec configuration).
- **Campaign State Management**: Chunk-based campaign processing with instant "Stop/Cancel" capabilities via cross-worker Firestore signals.
- **Resilient Webhook Handlers**: Processes incoming WhatsApp webhooks, validates `X-Hub-Signature-256`, deduplicates payloads, and routes them to a priority chatbot responder.
- **Intelligent Template Caching**: Tenant-scoped caching for approved WhatsApp templates to reduce round trips to Meta.
- **Time Standardization**: Enforces IST (Indian Standard Time) consistently across logging, database timestamps, and telemetry.

---

## 🏗 System Architecture

WappFlow separates concerns into three primary domains: **Frontend**, **API Server**, and **Background Workers**.

```text
┌─────────────────┐      ┌─────────────┐       ┌───────────────┐
│ Next.js Next.js │      │   FastAPI   │       │   Worker(s)   │
│   (Frontend)    │ ────▶│  (Backend)  │ ────▶ │ BullMQ / Async│
└─────────────────┘      └──────┬──────┘       └──────┬────────┘
                                │                     │
                                │                     │
                         ┌──────▼──────┐       ┌──────▼────────┐
                         │  Firestore  │       │ WhatsApp API  │
                         │ (Serverless)│       │ (Meta Cloud)  │
                         └─────────────┘       └───────────────┘
                                ▲                     ▲
                                │                     │
                                └─────────────────────┘
                                  Webhook Callbacks
```

### Tech Stack

| Category | Technologies |
| --- | --- |
| **Frontend** | Next.js 14, React 18, Tailwind CSS, Radix UI, Framer Motion |
| **Backend API** | FastAPI (Python), Uvicorn |
| **Queues / caching**| Redis, BullMQ (Python Port) |
| **Database** | Firebase / Firestore (NoSQL) |
| **Authentication** | Firebase Auth (JWT verification middleware) |

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
 │ 2. Validates user, creates Campaign & Recipients in Firestore
 │ 3. Enqueues job to 'campaign_queue'
 ▼
Redis
 │ 4. Persists job
 ▼
Worker (campaign_queue)
 │ 5. Locks campaign, fetches recipients in batches
 │ 6. Dispatches individual jobs to 'message_queue'
 ▼
Worker (message_queue)
 │ 7. Rate-limited execution (80 req/sec)
 │ 8. Calls Meta WhatsApp Cloud API
 │ 9. On success: Updates Firestore counters
 │ 10. On failure: Auto-retries with exponential backoff
 ▼
WhatsApp Cloud API
```

### 2. The Webhook / Chatbot Flow

When a user replies to a WappFlow business number, Meta fires a webhook back to our system. Because response speed reduces Meta's pricing for conversational windows, webhook replies jump the queue:

```text
WhatsApp API
 │ 1. Sends payload to /api/webhook
 ▼
API (FastAPI)
 │ 2. Verifies HMAC signature, deduplicates by Message ID
 │ 3. Evaluates Chatbot Rules (DB lookup) or Default Buttons
 │ 4. Enqueues a HIGH-PRIORITY (Priority: 0) job to 'message_queue'
 ▼
Worker (message_queue)
 │ 5. Bypasses bulk messages in queue
 │ 6. Instantly dispatches reply via WhatsApp API
 ▼
User receives instant reply
```

---

## 🗄 Queue Architecture (Redis + BullMQ)

WappFlow embraces asynchronous job processing to overcome common Node/Python bottlenecks. 

We use **3 primary queues**:

1. **`campaign_queue`**: A lightweight queue. A job here represents "Launch Campaign X". The worker picks this up, connects to Firestore, slices the audience into chunks, and floods the message queue. 
2. **`message_queue`**: The heavy-lifter. Processes individual API calls to Meta. Features:
   - **Rate Limiting**: Strictly capped at 80 messages per second to avoid Meta API bans.
   - **Retry & Backoff**: Configured for 5 attempts with `exponential` delay starting at 5 seconds (5s → 10s → 20s → 40s).
   - **Idempotency**: Utilizes calculated unique IDs to prevent duplicate sends if a worker crashes during execution.
   - **Priority Routing**: Chatbot webhook replies are queued as priority `0` (highest), ensuring customer support isn't delayed by a marketing blast.
3. **`dead_letter_queue`**: Messages that exhaust all 5 retries are gracefully moved here with the `permanently_failed` event, ensuring the main queue isn't clogged by continuously failing payloads.

---

## 💻 Getting Started (Local Development)

You can spin up the full WappFlow environment natively within minutes.

### 1. Clone & Install
```bash
git clone <your-repo>
cd SaaS-Product-

# Using our custom concurrent install script:
npm run install:all
```

### 2. Configure Environment Variables

**Backend (`backend/.env`)**
```env
ENVIRONMENT=development
CORS_ORIGINS=http://localhost:3000
FIREBASE_CREDENTIALS_PATH=./firebase-service-account.json

REDIS_HOST=localhost
REDIS_PORT=6379

WEBHOOK_VERIFY_TOKEN=your_custom_secure_token
META_APP_SECRET=your_whatsapp_app_secret
```

**Frontend (`frontend/.env.local`)**
```env
NEXT_PUBLIC_API_URL=http://localhost:5000
NEXT_PUBLIC_FIREBASE_API_KEY=xxx
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=xxx
NEXT_PUBLIC_FIREBASE_PROJECT_ID=xxx
```

*(Place your Firebase Admin SDK credential file at `backend/firebase-service-account.json`)*

### 3. Start Infrastructure

WappFlow requires Redis. We provide a docker-compose file for this:
```bash
docker-compose up redis -d
```

### 4. Start the Application

You need to run BOTH the auto-reloading API+Frontend and the worker script.

**Terminal 1: Start API & Frontend**
```bash
# This uses concurrently to spin up Next.js (port 3000) and FastAPI (port 5000)
npm run dev
```

**Terminal 2: Start the Background Worker**
```bash
# Workers are required; without them, queues back up and no WhatsApps are sent.
cd backend
python worker_main.py
```

---

## 🚢 Deployment Guide

WappFlow's separated architecture makes it highly scalable in production.

### Frontend
Deploy the `frontend/` directory directly to **Vercel**.
- Override root directory to `frontend`.
- Add all `NEXT_PUBLIC_` variables.

### Backend (API + Workers)
The backend is best deployed via **Docker** on a VPS (AWS EC2, DigitalOcean) or a PaaS like **Render** or **Railway**.

1. **Redis**: Provision a managed Redis instance (e.g., Upstash).
2. **API Service**: Run the Uvicorn web server.
   `uvicorn main:app --host 0.0.0.0 --port 5000`
3. **Worker Service**: Run the Python worker as an independent background process.
   `python worker_main.py`

### 📈 Scaling the System
- **Scale the API** horizontally to handle thousands of incoming Webhooks per second without sweating.
- **Scale Workers**: To increase campaign sending speed (assuming Meta lifts your rate limits), simply run multiple `python worker_main.py` containers. BullMQ safely distributes jobs across all active workers.

---

## 🐛 Troubleshooting

| Issue | Cause / Solution |
| --- | --- |
| **Campaign Stuck at 0 Sent** | Worker isn't running. Ensure you are running `worker_main.py` in a separate terminal. |
| **Webhooks not triggering** | Meta Cloud cannot reach localhost. Run `ngrok http 5000` and update your Meta App Webhook URL. |
| **Invalid Signature (401)** | `META_APP_SECRET` does not match the Meta dashboard, or the trailing whitespace is polluting the `.env`. |

---

## 📂 Project Structure

```text
├── backend/
│   ├── main.py               # FastAPI entry point, CORS, Health Checks, Lifespan
│   ├── worker_main.py        # BullMQ Worker definition
│   ├── routers/              # Controllers (bulk_message, webhook, chatbot)
│   ├── services/             # Abstractions (queue_manager, whatsapp)
│   ├── db_layer/             # Firestore repository adapters
│   └── requirements.txt      
├── frontend/                 # Next.js 14 Web Application
│   ├── src/app/              
│   ├── package.json          
│   └── tailwind.config.ts    
├── docker-compose.yml        # Local Redis orchestration
└── package.json              # Global dependency runner
```
