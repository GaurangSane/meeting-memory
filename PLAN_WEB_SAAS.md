# PLAN_WEB_SAAS.md — Intelligent Meeting Memory
## Desktop → Multi-Tenant Web SaaS Migration Blueprint

> **Audience:** Autonomous AI coding agent executing this migration.
> **Document Scope:** This is an architecture blueprint, not a line-by-line script.
> Architecturally critical, easy-to-get-wrong modules (WebSocket audio ingestion,
> RAG retrieval, the correction-learning loop, JWT/WS auth) are given complete
> code. Standard CRUD boilerplate (registration forms, list views, Pydantic
> schemas) is given as a precise contract — table schema, endpoint signature,
> request/response shape — for the agent to implement following the patterns
> established in the critical modules.

---

## 0. ARCHITECTURAL DECISIONS & RATIONALE

The original requirements offered several "or" choices. An autonomous agent
cannot execute against ambiguity, so these are resolved here, with rationale,
before any code is written.

| Decision Point | Choice | Rationale |
|---|---|---|
| Frontend framework | **Next.js (App Router) + TypeScript** | Streamlit has no first-class access to `MediaRecorder`/raw `WebSocket` browser APIs and no native session/route-protection model. Both are hard requirements here. |
| Vector store | **pgvector (inside the existing PostgreSQL instance)** | One transactional boundary. Deleting a meeting cascades to its embeddings automatically (`ON DELETE CASCADE`) — no orphaned vectors in a second system, no second auth/network surface to secure. Tenant isolation reuses Postgres Row-Level Security instead of a second per-vendor isolation model (Pinecone namespaces, Qdrant collections). A `VectorStore` interface (Phase 5) isolates this choice so Qdrant/Pinecone can be swapped in later purely as a config change if vector volume outgrows pgvector (roughly >5–10M vectors). |
| MOM generation timing | **Asynchronous, via Celery — not inline on the WebSocket handler** | RAG retrieval + Gemini generation takes 5–15s. Blocking a held-open WebSocket connection for that, multiplied across hundreds of concurrent tenants, is a connection-pool/timeout risk. The WS handler persists the transcript and returns immediately; a Celery task does the heavy lifting. |
| Embedding model | **Google `text-embedding-004`** (768-dim) | Same vendor/API key as Gemini generation — no second embedding provider account, consistent latency/cost profile. |
| WebSocket authentication | **Short-lived, single-use WS ticket** (not a raw JWT in the query string) | Browsers cannot set custom headers on a WS handshake, so the token must travel in the URL — which means it lands in proxy/server access logs. A 30-second, single-use ticket minted via a separate authenticated REST call limits the blast radius of a leaked log line to near-zero. |
| Multi-tenancy boundary | **Organization (`org_id`), not per-user** | "Multi-tenant SaaS" implies teams sharing a meeting history and a learning memory — not isolated individuals. A user belongs to one org; meetings, MOMs, and embeddings are scoped to `org_id`. |

---

## 1. MIGRATION MAP — Desktop Module → SaaS Service

This maps every existing component from the local app to its new home, so the
agent understands what is being **ported with modification** vs. **net new**.

| Desktop (As-Is) | SaaS (To-Be) | Change |
|---|---|---|
| `sounddevice` single-stream capture | Browser `MediaRecorder` API (Phase 9) | **Capability change, flagged below.** |
| `numpy` RMS-energy VAD filter | `services/vad.py` — ported almost verbatim, run server-side per ~5s sub-chunk instead of per 30s chunk | Finer-grained silence dropping → *better* cost savings than the desktop version |
| `aiohttp` → Sarvam AI with backoff | `services/stt_service.py` — same retry/backoff logic, invoked from the WS audio handler | Ported directly |
| Gemini 1.5 Pro, strict JSON schema | `services/gemini_service.py` — same schema, **plus** a "Historical Context" block injected by the RAG engine | Extended, not replaced |
| Jinja2 HTML email + SMTP | `workers/tasks_notifications.py` — same template, now a Celery task | Ported, moved off the main thread |
| Twilio WhatsApp | `workers/tasks_notifications.py` — same client, now a Celery task | Ported, moved off the main thread |
| CustomTkinter GUI | Next.js frontend (Phases 8–10) | Replaced |
| *(none — new capability)* | pgvector RAG memory loop (Phase 5) | **Net new — the core SaaS value proposition** |

### ⚠️ Capability gap the agent must flag to the user: system/loopback audio

The desktop app captured **room audio** directly from the OS (`sounddevice`
loopback or physical mic picking up speakers). A browser cannot do this by
default — `getUserMedia` only grants microphone access. If the user is running
their meeting (Zoom/Meet/Teams) in another browser tab on the same machine,
their own mic still captures everyone in the room, but **remote participants
joining by phone or a separate room mic will not be captured** the way the
desktop app's loopback did.

**Mitigation built into Phase 9:** offer a "Capture this browser tab's audio"
toggle using `navigator.mediaDevices.getDisplayMedia({ video: true, audio: true })`
(video track is discarded client-side, audio track is mixed with the mic track
via `AudioContext`). This approximates the old dual-stream behavior when the
meeting platform itself is running in a browser tab. It cannot capture a
phone-line participant or a physical conference-room speaker; that limitation
should be stated in the UI, not silently swallowed.

---

## 2. SYSTEM ARCHITECTURE OVERVIEW

```
┌────────────────────────────────────────────────────────────────────┐
│                         BROWSER (Next.js)                          │
│  Login/Dashboard │ Settings │ Live Meeting (MediaRecorder) │ MOM   │
│                              │ Editor (Past Meetings)              │
└──────────────┬───────────────────────────────┬─────────────────────┘
               │ REST (JWT)                    │ WSS (ticket-authed, binary audio)
               ▼                               ▼
┌────────────────────────────────────────────────────────────────────┐
│                       FASTAPI APPLICATION                          │
│  ┌────────────┐  ┌──────────────┐  ┌───────────────────────────┐  │
│  │ REST API   │  │ WS Manager   │  │ VAD → STT dispatch         │  │
│  │ auth/users │  │ per-meeting  │  │ (Sarvam saaras:v3, async,  │  │
│  │ meetings   │  │ connection   │  │  exponential backoff)      │  │
│  └─────┬──────┘  └──────┬───────┘  └─────────────┬───────────────┘  │
│        │                │  transcript chunks      │                │
│        ▼                ▼  → Redis (durable buf.) ▼                │
└────────┼────────────────────────────────────────────────────────────┘
         │                                          │ on "stop"
         ▼                                          ▼
┌──────────────────┐                    ┌──────────────────────────┐
│   PostgreSQL      │◄───────────────── │  Celery Worker(s)         │
│  + pgvector ext.  │   read/write       │  generate_mom_task        │
│  users, orgs,      │                   │  (RAG retrieve→Gemini)    │
│  meetings, moms,   │                   │  embed_meeting_task        │
│  edit_history,     │                   │  send_email_task           │
│  meeting_embeddings│                   │  send_whatsapp_task        │
└──────────────────┘                    └──────────┬───────────────┘
                                                     │ broker/result
                                                     ▼
                                              ┌────────────┐
                                              │   Redis    │
                                              └────────────┘
```

---

## 3. MONOREPO FOLDER STRUCTURE

```
meeting-saas/
├── PLAN_WEB_SAAS.md
├── docker-compose.yml
├── .env.example
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── app/
│   │   ├── main.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── security.py          ← JWT + WS ticket logic
│   │   │   └── db_session.py        ← async SQLAlchemy session + RLS tenant scoping
│   │   ├── models/                  ← SQLAlchemy ORM
│   │   │   ├── organization.py
│   │   │   ├── user.py
│   │   │   ├── meeting.py
│   │   │   ├── mom.py
│   │   │   └── embedding.py
│   │   ├── schemas/                 ← Pydantic request/response models
│   │   │   ├── auth.py
│   │   │   ├── user.py
│   │   │   ├── meeting.py
│   │   │   └── mom.py
│   │   ├── api/
│   │   │   ├── deps.py              ← get_current_user, get_db, tenant scoping
│   │   │   └── v1/
│   │   │       ├── auth.py
│   │   │       ├── users.py         ← settings: whatsapp/email prefs
│   │   │       ├── meetings.py      ← CRUD + PATCH mom (correction capture)
│   │   │       └── websocket_audio.py
│   │   ├── services/
│   │   │   ├── vad.py               ← ported RMS-energy filter
│   │   │   ├── stt_service.py       ← Sarvam AI saaras:v3, async + backoff
│   │   │   ├── vector_store.py      ← abstract interface
│   │   │   ├── vector_store_pgvector.py
│   │   │   ├── embedding_service.py ← Gemini text-embedding-004
│   │   │   ├── rag_service.py       ← retrieval + prompt assembly (THE LOOP)
│   │   │   └── gemini_service.py    ← RAG-augmented MOM generation
│   │   ├── workers/
│   │   │   ├── celery_app.py
│   │   │   ├── tasks_mom.py         ← generate_mom_task
│   │   │   ├── tasks_embeddings.py  ← embed_meeting_task
│   │   │   └── tasks_notifications.py ← email + WhatsApp
│   │   └── ws_manager.py            ← per-meeting connection + buffer registry
│   └── migrations/                  ← Alembic versions
│
└── frontend/
    ├── Dockerfile
    ├── package.json
    ├── next.config.js
    ├── middleware.ts                ← route protection (auth cookie check)
    ├── app/
    │   ├── login/page.tsx
    │   ├── register/page.tsx
    │   ├── (dashboard)/
    │   │   ├── layout.tsx
    │   │   ├── settings/page.tsx    ← WhatsApp + email prefs form
    │   │   ├── meetings/
    │   │   │   ├── page.tsx         ← Past Meetings list
    │   │   │   ├── [id]/page.tsx    ← MOM viewer/editor
    │   │   │   └── live/page.tsx    ← Live capture interface
    ├── components/
    │   ├── AudioRecorder.tsx        ← MediaRecorder + WS client (critical)
    │   ├── MomEditor.tsx            ← edit form → PATCH (feeds the learning loop)
    │   └── LiveTranscriptPanel.tsx
    └── lib/
        ├── api-client.ts
        └── auth.ts
```

---

## PHASE 0 — INFRASTRUCTURE SCAFFOLDING

### Step 0.1 — `docker-compose.yml`

```yaml
version: "3.9"

services:
  postgres:
    image: pgvector/pgvector:pg16     # Postgres 16 with pgvector pre-installed
    environment:
      POSTGRES_DB: meeting_saas
      POSTGRES_USER: saas_admin
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pg_data:/var/lib/postgresql/data
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U saas_admin"]
      interval: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  backend:
    build: ./backend
    env_file: .env
    depends_on: [postgres, redis]
    ports: ["8000:8000"]
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

  celery_worker:
    build: ./backend
    env_file: .env
    depends_on: [postgres, redis]
    command: celery -A app.workers.celery_app worker --loglevel=info --concurrency=4

  frontend:
    build: ./frontend
    env_file: .env
    depends_on: [backend]
    ports: ["3000:3000"]

volumes:
  pg_data:
```

### Step 0.2 — `.env.example`

```ini
# ── Database ───────────────────────────────────────────────
POSTGRES_PASSWORD=change_me
DATABASE_URL=postgresql+asyncpg://saas_admin:change_me@postgres:5432/meeting_saas

# ── Redis / Celery ─────────────────────────────────────────
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

# ── Auth ───────────────────────────────────────────────────
JWT_SECRET_KEY=change_me_to_a_long_random_string
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7
WS_TICKET_EXPIRE_SECONDS=30

# ── Sarvam AI ──────────────────────────────────────────────
SARVAM_API_KEY=your_key
SARVAM_STT_MODEL=saaras:v3

# ── Google Gemini ──────────────────────────────────────────
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-1.5-pro-latest
GEMINI_EMBEDDING_MODEL=text-embedding-004

# ── Email ──────────────────────────────────────────────────
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_gmail@gmail.com
SMTP_PASSWORD=app_password

# ── Twilio ─────────────────────────────────────────────────
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=your_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886

# ── Frontend ───────────────────────────────────────────────
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

---

## PHASE 1 — DATABASE SCHEMA (PostgreSQL + pgvector)

### Step 1.1 — Enable the extension and write the core migration

**File:** `backend/migrations/versions/0001_init.py` (Alembic — body shown as raw SQL for clarity; agent should wrap in `op.execute()`)

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Tenancy ──────────────────────────────────────────────────────────
CREATE TABLE organizations (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email           TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'member',  -- 'admin' | 'member'
    whatsapp_number TEXT,                            -- e.g. whatsapp:+91XXXXXXXXXX
    notify_email    TEXT,                            -- defaults to `email` if null
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Meetings & MOMs ──────────────────────────────────────────────────
CREATE TABLE meetings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    created_by      UUID NOT NULL REFERENCES users(id),
    title           TEXT,
    meeting_context TEXT NOT NULL,        -- the "anchor" field from the UI
    raw_transcript  TEXT,
    status          TEXT NOT NULL DEFAULT 'recording',
                    -- 'recording' | 'processing' | 'completed' | 'failed'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);
CREATE INDEX idx_meetings_org ON meetings(org_id);

CREATE TABLE mom_records (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    meeting_id      UUID UNIQUE NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    summary         TEXT,
    key_decisions   JSONB NOT NULL DEFAULT '[]',
    -- each action item: {id, task, assignee, deadline, priority}
    action_items    JSONB NOT NULL DEFAULT '[]',
    risks           JSONB NOT NULL DEFAULT '[]',
    next_steps      TEXT,
    version         INT NOT NULL DEFAULT 1,
    last_edited_by  UUID REFERENCES users(id),
    last_edited_at  TIMESTAMPTZ
);

-- Every manual edit is captured here. This table is the raw material
-- for the "correction" embeddings that close the learning loop (Phase 5).
CREATE TABLE mom_edit_history (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    mom_record_id   UUID NOT NULL REFERENCES mom_records(id) ON DELETE CASCADE,
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    field_path      TEXT NOT NULL,   -- e.g. "action_items[2].assignee"
    old_value       TEXT,
    new_value       TEXT,
    edited_by       UUID NOT NULL REFERENCES users(id),
    edited_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── The Memory: meeting + correction embeddings ─────────────────────
CREATE TABLE meeting_embeddings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    meeting_id      UUID REFERENCES meetings(id) ON DELETE CASCADE,
    content_type    TEXT NOT NULL,
                    -- 'summary' | 'decision' | 'action_item' | 'correction'
    source_text     TEXT NOT NULL,     -- the human-readable text that was embedded
    embedding       VECTOR(768) NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW index for fast cosine-similarity search, scoped by org via the WHERE
-- clause at query time (see rag_service.py) — never trust the index alone
-- for tenant isolation.
CREATE INDEX idx_embeddings_org ON meeting_embeddings(org_id);
CREATE INDEX idx_embeddings_vector ON meeting_embeddings
    USING hnsw (embedding vector_cosine_ops);

-- ── Defense-in-depth: Postgres Row-Level Security ───────────────────
-- The app sets `app.current_org` per-request (see core/db_session.py).
-- Even if an application-layer WHERE clause is forgotten in some future
-- query, RLS prevents cross-tenant reads/writes at the database layer.
ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;
ALTER TABLE mom_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE meeting_embeddings ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_meetings ON meetings
    USING (org_id = current_setting('app.current_org')::uuid);
CREATE POLICY tenant_isolation_embeddings ON meeting_embeddings
    USING (org_id = current_setting('app.current_org')::uuid);
```

### Step 1.2 — `core/db_session.py` (tenant-scoped session)

```python
"""
app/core/db_session.py

Every request sets `app.current_org` as a Postgres session variable before
any query runs. This activates the RLS policies defined in the migration —
a second, database-layer guarantee that one org can never read another's
data, even if an application-layer filter is accidentally omitted somewhere.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, pool_size=20, max_overflow=10)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_tenant_session(org_id: str) -> AsyncSession:
    """Yield a session with RLS tenant context set for this request."""
    async with SessionLocal() as session:
        await session.execute(
            "SET LOCAL app.current_org = :org_id", {"org_id": org_id}
        )
        yield session
```

---

## PHASE 2 — AUTH CORE (JWT + WS Tickets)

### Step 2.1 — `core/security.py`

```python
"""
app/core/security.py

Two distinct token types, deliberately kept separate:

1. Access/refresh JWTs — standard REST API auth. Access token: 15 min,
   sent as a Bearer header. Refresh token: 7 days, stored in an httpOnly
   cookie (never readable by JS, mitigates XSS token theft).

2. WS tickets — single-use, 30-second-lived tokens minted by an authenticated
   REST call specifically to authorize one WebSocket handshake. Browsers
   cannot attach custom headers to a WS handshake, so *some* token must go
   in the URL query string — which means it will appear in access logs and
   proxy logs. A long-lived JWT in that position is a real leak vector; a
   30-second single-use ticket reduces the exposure window to near-zero and
   is consumed (deleted from Redis) on first use, so even a logged ticket is
   useless after the handshake completes.
"""

import uuid
from datetime import datetime, timedelta, timezone
from jose import jwt
from passlib.context import CryptContext
from app.core.config import settings
from app.core.redis_client import redis_client

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str, org_id: str) -> str:
    payload = {
        "sub": user_id,
        "org_id": org_id,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


async def mint_ws_ticket(user_id: str, org_id: str, meeting_id: str) -> str:
    """
    Create a single-use ticket for one WebSocket handshake.
    Stored in Redis with a short TTL; deleted on first consumption
    in websocket_audio.py.
    """
    ticket = str(uuid.uuid4())
    key = f"ws_ticket:{ticket}"
    await redis_client.set(
        key,
        f"{user_id}:{org_id}:{meeting_id}",
        ex=settings.WS_TICKET_EXPIRE_SECONDS,
    )
    return ticket


async def consume_ws_ticket(ticket: str) -> tuple[str, str, str] | None:
    """Validate and immediately invalidate a WS ticket. Returns (user_id, org_id, meeting_id) or None."""
    key = f"ws_ticket:{ticket}"
    value = await redis_client.get(key)
    if value is None:
        return None
    await redis_client.delete(key)  # single-use: burn it immediately
    user_id, org_id, meeting_id = value.decode().split(":")
    return user_id, org_id, meeting_id
```

### Step 2.2 — `api/v1/auth.py` (contract)

| Endpoint | Method | Body | Response | Notes |
|---|---|---|---|---|
| `/api/v1/auth/register` | POST | `{org_name, email, password}` | `{access_token}` + sets refresh cookie | Creates org + first admin user atomically |
| `/api/v1/auth/login` | POST | `{email, password}` | `{access_token}` + sets refresh cookie | `verify_password` against `hashed_password` |
| `/api/v1/auth/refresh` | POST | *(refresh cookie)* | `{access_token}` | Rotates refresh token |
| `/api/v1/auth/logout` | POST | — | 204 | Clears refresh cookie |

### Step 2.3 — `api/deps.py` (contract)

```python
"""
app/api/deps.py

get_current_user: decodes the Bearer access token, loads the user.
get_db: yields a tenant-scoped session via get_tenant_session(user.org_id) —
        every request handler that touches the DB gets RLS automatically.
"""
# Standard FastAPI OAuth2PasswordBearer + decode_token() + DB lookup.
# Raises 401 on expired/invalid token. Implement following security.py above.
```

---

## PHASE 3 — REST API: USERS & MEETINGS

### Step 3.1 — `api/v1/users.py` (contract)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/users/me` | GET | Current user profile |
| `/api/v1/users/me` | PATCH | Update `whatsapp_number`, `notify_email` — the dashboard settings requirement |

### Step 3.2 — `api/v1/meetings.py` — full code for the critical PATCH endpoint

This is the endpoint that **closes the learning loop**. Every other CRUD
endpoint on this router (`POST /meetings`, `GET /meetings`, `GET /meetings/{id}`)
follows standard FastAPI/SQLAlchemy patterns and is not detailed here — but
this one is the mechanism by which a human correction becomes training signal,
so it is given in full.

```python
"""
app/api/v1/meetings.py — PATCH /meetings/{id}/mom

When a user edits an AI-generated MOM in the Past Meetings view, this endpoint:
  1. Diffs the incoming payload against the stored mom_record, field by field.
  2. Writes one mom_edit_history row per changed field (audit trail).
  3. Enqueues embed_meeting_task as a Celery task, which turns each diff into
     a human-readable "correction sentence" and embeds it (Phase 5).

This is the ONLY place in the system where corrections are captured. If this
endpoint is bypassed (e.g. a future bulk-edit feature writes directly to the
mom_records table), the system will silently stop learning from edits.
"""

from fastapi import APIRouter, Depends, HTTPException
from app.schemas.mom import MomUpdateRequest
from app.workers.tasks_embeddings import embed_meeting_task
from app.api.deps import get_current_user, get_db

router = APIRouter()


@router.patch("/meetings/{meeting_id}/mom")
async def update_mom(
    meeting_id: str,
    payload: MomUpdateRequest,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    mom = await db.get(MomRecord, meeting_id_to_mom_id(meeting_id))  # implement lookup
    if mom is None:
        raise HTTPException(404, "MOM not found")

    diffs = _diff_mom_fields(old=mom, new=payload)
    for field_path, old_value, new_value in diffs:
        db.add(MomEditHistory(
            mom_record_id=mom.id,
            org_id=current_user.org_id,
            field_path=field_path,
            old_value=str(old_value),
            new_value=str(new_value),
            edited_by=current_user.id,
        ))

    # Apply the update
    mom.summary = payload.summary
    mom.key_decisions = payload.key_decisions
    mom.action_items = payload.action_items
    mom.risks = payload.risks
    mom.next_steps = payload.next_steps
    mom.version += 1
    mom.last_edited_by = current_user.id
    mom.last_edited_at = func.now()

    await db.commit()

    if diffs:
        # Fire-and-forget: embedding generation must not block the API response.
        embed_meeting_task.delay(meeting_id=meeting_id, edit_history_ids=[d[0] for d in diffs])

    return {"status": "updated", "version": mom.version, "corrections_captured": len(diffs)}


def _diff_mom_fields(old, new) -> list[tuple[str, str, str]]:
    """
    Field-by-field diff. action_items and key_decisions are matched by their
    stable `id` (assigned server-side at generation time — see gemini_service.py)
    so an edit to "item 3's assignee" is captured precisely, not as a wholesale
    list replacement that loses which specific correction was made.
    """
    diffs = []
    if old.summary != new.summary:
        diffs.append(("summary", old.summary, new.summary))

    old_items = {item["id"]: item for item in old.action_items}
    for new_item in new.action_items:
        old_item = old_items.get(new_item["id"])
        if old_item is None:
            continue  # newly added item, not a correction
        for key in ("task", "assignee", "deadline", "priority"):
            if old_item.get(key) != new_item.get(key):
                diffs.append((f"action_items[{new_item['id']}].{key}", old_item.get(key), new_item.get(key)))
    return diffs
```

---

## PHASE 4 — WEBSOCKET AUDIO INGESTION PIPELINE

This is the highest-risk module in the system — it is the direct evolution of
the desktop app's `AudioCapture` + `AudioChunker` + `SarvamSTTClient`, now
running per-tenant over an untrusted network connection.

### Step 4.1 — `ws_manager.py`

```python
"""
app/ws_manager.py

One ConnectionContext per active WebSocket. Holds the VAD/chunk-aggregation
state for that single meeting's audio stream.

CRITICAL PRODUCTION DETAIL: transcript chunks are written to Redis
(`meeting:{id}:transcript_chunks`, a Redis LIST) on every successful STT
result — NOT kept only in an in-process Python list. Reasons:
  1. The Celery worker that eventually generates the MOM runs in a
     *different process* than the FastAPI WS handler; it cannot read
     the WS handler's in-memory state.
  2. If the WS connection drops and the client reconnects mid-meeting,
     the durable Redis buffer survives; an in-memory list would not.
"""

import asyncio
from dataclasses import dataclass, field
import numpy as np
from app.core.redis_client import redis_client


@dataclass
class ConnectionContext:
    meeting_id: str
    org_id: str
    user_id: str
    chunk_index: int = 0
    pcm_buffer: list = field(default_factory=list)
    buffered_samples: int = 0
    in_flight_futures: list = field(default_factory=list)


class WSConnectionManager:
    def __init__(self):
        self._connections: dict[str, ConnectionContext] = {}

    def register(self, ctx: ConnectionContext) -> None:
        self._connections[ctx.meeting_id] = ctx

    def get(self, meeting_id: str) -> ConnectionContext | None:
        return self._connections.get(meeting_id)

    def unregister(self, meeting_id: str) -> None:
        self._connections.pop(meeting_id, None)

    @staticmethod
    async def append_transcript_chunk(meeting_id: str, chunk_index: int, text: str) -> None:
        """Durable, cross-process transcript buffer (Redis, not memory)."""
        await redis_client.rpush(
            f"meeting:{meeting_id}:transcript_chunks",
            f"{chunk_index}|{text}",
        )

    @staticmethod
    async def get_ordered_transcript(meeting_id: str) -> str:
        raw_chunks = await redis_client.lrange(f"meeting:{meeting_id}:transcript_chunks", 0, -1)
        parsed = sorted(
            (int(c.split("|", 1)[0]), c.split("|", 1)[1]) for c in (r.decode() for r in raw_chunks)
        )
        return " ".join(text for _, text in parsed if text)


ws_manager = WSConnectionManager()
```

### Step 4.2 — `services/vad.py` (ported from desktop, unchanged logic)

```python
"""
app/services/vad.py
Ported directly from the desktop app's numpy RMS-energy VAD filter.
Now invoked per ~5-second sub-chunk (instead of per 30s), which means
silence is detected and dropped at finer granularity — strictly better
cost savings than the original.
"""

import numpy as np

SILENCE_RMS_THRESHOLD = 0.01  # tune empirically; matches desktop app's value


def is_silent(pcm: np.ndarray) -> bool:
    rms = np.sqrt(np.mean(pcm.astype(np.float64) ** 2))
    return rms < SILENCE_RMS_THRESHOLD
```

### Step 4.3 — `services/stt_service.py` (ported, model updated to `saaras:v3`)

```python
"""
app/services/stt_service.py
Direct port of the desktop SarvamSTTClient, updated to the saaras:v3 model
and the exponential-backoff retry logic from the desktop app's STT pipeline.
Exceptions are RAISED, not swallowed — the caller (websocket_audio.py)
attaches done-callbacks to surface them, continuing the pattern established
in the desktop app's bug-fix history.
"""

import asyncio
import aiohttp
from aiohttp import FormData
from app.core.config import settings

MAX_RETRIES = 4
BASE_BACKOFF_SECONDS = 1.5


async def transcribe_chunk(session: aiohttp.ClientSession, wav_bytes: bytes, chunk_index: int) -> str:
    form = FormData()
    form.add_field("file", wav_bytes, filename=f"chunk_{chunk_index}.wav", content_type="audio/wav")
    form.add_field("model", settings.SARVAM_STT_MODEL)  # "saaras:v3"

    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            async with session.post(
                "https://api.sarvam.ai/speech-to-text",
                data=form,
                headers={"api-subscription-key": settings.SARVAM_API_KEY},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("transcript", "")
                if resp.status == 429 or resp.status >= 500:
                    raise aiohttp.ClientError(f"Retryable HTTP {resp.status}")
                error_text = await resp.text()
                raise ValueError(f"Non-retryable Sarvam error {resp.status}: {error_text}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_exc = e
            backoff = BASE_BACKOFF_SECONDS * (2 ** attempt)
            await asyncio.sleep(backoff)

    raise RuntimeError(f"STT failed after {MAX_RETRIES} attempts on chunk #{chunk_index}: {last_exc}")
```

### Step 4.4 — `api/v1/websocket_audio.py` — the full pipeline

```python
"""
app/api/v1/websocket_audio.py

WebSocket data flow (binary frames = audio, text frames = control messages):

  1. Client calls POST /api/v1/meetings/{id}/ws-ticket (authenticated REST,
     Bearer token) → receives a 30-second single-use ticket.
  2. Client opens  wss://.../ws/meetings/{id}/audio?ticket=<ticket>
  3. Server calls consume_ws_ticket() — validates + immediately burns it.
     If invalid/expired/already used → close(code=4001).
  4. Client's MediaRecorder fires `ondataavailable` every ~5s, sending each
     WebM/Opus Blob as a binary WS frame.
  5. Server decodes WebM/Opus → PCM (via ffmpeg subprocess), runs VAD; if
     non-silent, dispatches to Sarvam AI as an independent asyncio Task with
     a done-callback that (a) appends the result to the Redis transcript
     buffer in order, and (b) logs+surfaces any exception — never silently
     dropped.
  6. Client sends a text control frame `{"type": "stop"}` when the user
     clicks Stop.
  7. Server awaits all in-flight STT futures (bounded wait), marks the
     meeting status='processing' in Postgres, and enqueues
     generate_mom_task.delay(meeting_id) — then closes the socket.
     The heavy RAG+Gemini work happens in Celery, NOT on this connection.
"""

import asyncio
import json
import subprocess
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import aiohttp

from app.core.security import consume_ws_ticket
from app.services.vad import is_silent
from app.services.stt_service import transcribe_chunk
from app.ws_manager import ws_manager, ConnectionContext
from app.workers.tasks_mom import generate_mom_task
from app.db import set_meeting_status  # thin helper, implement per ORM conventions

router = APIRouter()

MAX_CONCURRENT_STT = 5


def _decode_webm_opus_to_pcm(blob: bytes, sample_rate: int = 16000) -> np.ndarray:
    """
    Decode a WebM/Opus blob to mono 16kHz float32 PCM via an ffmpeg subprocess.
    ffmpeg must be present in the backend Docker image (apt-get install ffmpeg).
    """
    proc = subprocess.run(
        [
            "ffmpeg", "-i", "pipe:0",
            "-f", "f32le", "-ac", "1", "-ar", str(sample_rate),
            "pipe:1", "-loglevel", "error",
        ],
        input=blob,
        capture_output=True,
        check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32)


@router.websocket("/ws/meetings/{meeting_id}/audio")
async def audio_stream(websocket: WebSocket, meeting_id: str, ticket: str):
    auth = await consume_ws_ticket(ticket)
    if auth is None:
        await websocket.close(code=4001)
        return
    user_id, org_id, ticket_meeting_id = auth
    if ticket_meeting_id != meeting_id:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    ctx = ConnectionContext(meeting_id=meeting_id, org_id=org_id, user_id=user_id)
    ws_manager.register(ctx)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_STT)
    http_session = aiohttp.ClientSession()

    async def handle_chunk(pcm: np.ndarray, idx: int) -> None:
        async with semaphore:
            wav_bytes = _pcm_to_wav_bytes(pcm)  # int16 WAV encoding, same as desktop chunker
            text = await transcribe_chunk(http_session, wav_bytes, idx)
            await ws_manager.append_transcript_chunk(meeting_id, idx, text)
            # Optional: push partial transcript back for live display
            await websocket.send_json({"type": "partial_transcript", "chunk": idx, "text": text})

    def on_chunk_done(future: asyncio.Task) -> None:
        # Same exception-surfacing discipline as the desktop app's Fix 3:
        # a Future/Task whose result is never retrieved silently swallows
        # any exception raised inside it.
        exc = future.exception()
        if exc is not None:
            asyncio.create_task(
                websocket.send_json({"type": "stt_error", "message": str(exc)})
            )

    try:
        while True:
            message = await websocket.receive()

            if message.get("bytes") is not None:
                pcm = _decode_webm_opus_to_pcm(message["bytes"])
                if is_silent(pcm):
                    continue  # VAD: drop silent sub-chunk, save the STT call
                idx = ctx.chunk_index
                ctx.chunk_index += 1
                task = asyncio.create_task(handle_chunk(pcm, idx))
                task.add_done_callback(on_chunk_done)
                ctx.in_flight_futures.append(task)

            elif message.get("text") is not None:
                control = json.loads(message["text"])
                if control.get("type") == "stop":
                    break

    except WebSocketDisconnect:
        pass
    finally:
        # Bounded wait for in-flight STT tasks before handing off to Celery
        await asyncio.wait(ctx.in_flight_futures, timeout=30)
        await http_session.close()

        transcript = await ws_manager.get_ordered_transcript(meeting_id)
        await set_meeting_status(meeting_id, status="processing", raw_transcript=transcript)

        generate_mom_task.delay(meeting_id=meeting_id, org_id=org_id)

        ws_manager.unregister(meeting_id)
        await websocket.close()
```

---

## PHASE 5 — THE RAG MEMORY ENGINE (Core IP)

This is the mechanism that makes the answer to *"how does the system get
smarter over time"* concrete rather than aspirational. It has three moving
parts that together form a closed loop: **Ingest → Retrieve → Generate**, with
**Edit** feeding back into Ingest.

```
        ┌─────────────────────────────────────────────────────────┐
        │                                                          │
        ▼                                                          │
 [1] MEETING ENDS                                                  │
   transcript persisted → generate_mom_task runs                   │
        │                                                          │
        ▼                                                          │
 [2] RETRIEVE  (rag_service.py)                                    │
   embed(meeting_context + transcript)                              │
   → cosine search meeting_embeddings WHERE org_id = :org           │
   → top-k past summaries/decisions + any 'correction' rows         │
        │                                                          │
        ▼                                                          │
 [3] GENERATE  (gemini_service.py)                                 │
   prompt = system_instructions                                    │
           + "## Relevant history from past meetings:" + retrieved  │
           + "## Known corrections to apply:" + retrieved corrections│
           + transcript                                            │
   → Gemini → structured MOM JSON                                  │
        │                                                          │
        ▼                                                          │
 [4] INGEST  (embed_meeting_task)                                  │
   embed(summary, each decision, each action item)                  │
   upsert into meeting_embeddings, content_type ∈                   │
   {summary, decision, action_item}                                 │
        │                                                          │
        ▼                                                          │
 [5] USER EDITS MOM  (PATCH /meetings/{id}/mom, Phase 3)            │
   diff captured → mom_edit_history rows                            │
        │                                                          │
        ▼                                                          │
 [6] CORRECTION EMBEDDED  (embed_meeting_task, triggered by PATCH) │
   diff → human-readable correction sentence → embedded,            │
   content_type='correction'                                        │
        │                                                          │
        └──────────────────► feeds back into step [2] for the ─────┘
                              NEXT meeting this org runs
```

### Step 5.1 — `services/vector_store.py` (interface — swap point for Qdrant/Pinecone later)

```python
"""
app/services/vector_store.py

Abstract interface. The pgvector implementation below is the only concrete
class today. Switching to Qdrant or Pinecone later means writing one new
class that satisfies this interface and changing one line in the DI wiring —
no other module in the codebase references pgvector directly.
"""

from abc import ABC, abstractmethod


class VectorStore(ABC):
    @abstractmethod
    async def upsert(self, org_id: str, meeting_id: str, content_type: str,
                      source_text: str, embedding: list[float], metadata: dict) -> None:
        ...

    @abstractmethod
    async def query(self, org_id: str, embedding: list[float], top_k: int,
                     content_types: list[str] | None = None) -> list[dict]:
        ...
```

### Step 5.2 — `services/vector_store_pgvector.py`

```python
"""
app/services/vector_store_pgvector.py

CRITICAL: every query filters by org_id in the WHERE clause AND relies on the
RLS policy from Phase 1 as a second layer. Never construct a query for this
table without an explicit org_id filter, even though RLS exists — defense in
depth means the application layer should never depend solely on the database
layer to catch a tenant-isolation bug.
"""

from sqlalchemy import text
from app.services.vector_store import VectorStore
from app.core.db_session import SessionLocal


class PgVectorStore(VectorStore):
    async def upsert(self, org_id, meeting_id, content_type, source_text, embedding, metadata):
        async with SessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO meeting_embeddings
                        (org_id, meeting_id, content_type, source_text, embedding, metadata)
                    VALUES (:org_id, :meeting_id, :content_type, :source_text, :embedding, :metadata)
                """),
                {
                    "org_id": org_id, "meeting_id": meeting_id, "content_type": content_type,
                    "source_text": source_text, "embedding": embedding, "metadata": metadata,
                },
            )
            await session.commit()

    async def query(self, org_id, embedding, top_k=5, content_types=None):
        async with SessionLocal() as session:
            type_filter = ""
            params = {"org_id": org_id, "embedding": embedding, "top_k": top_k}
            if content_types:
                type_filter = "AND content_type = ANY(:content_types)"
                params["content_types"] = content_types

            result = await session.execute(
                text(f"""
                    SELECT source_text, content_type, metadata,
                           1 - (embedding <=> :embedding) AS similarity
                    FROM meeting_embeddings
                    WHERE org_id = :org_id {type_filter}
                    ORDER BY embedding <=> :embedding
                    LIMIT :top_k
                """),
                params,
            )
            return [dict(row._mapping) for row in result]
```

### Step 5.3 — `services/embedding_service.py`

```python
"""app/services/embedding_service.py — Gemini text-embedding-004 wrapper."""

import google.generativeai as genai
from app.core.config import settings

genai.configure(api_key=settings.GEMINI_API_KEY)


async def embed_text(text: str) -> list[float]:
    result = genai.embed_content(model=settings.GEMINI_EMBEDDING_MODEL, content=text)
    return result["embedding"]
```

### Step 5.4 — `services/rag_service.py` — the retrieval + prompt assembly

```python
"""
app/services/rag_service.py

THE core of "Intelligent Meeting Memory." Called once per meeting, just
before Gemini generation, by generate_mom_task (Phase 6).

Two retrieval passes, deliberately separate:
  1. General history: past summaries/decisions/action_items, to give Gemini
     situational continuity ("this is the same project as last Tuesday").
  2. Corrections: a SEPARATE, smaller retrieval specifically over
     content_type='correction', because corrections are imperative
     instructions ("stop doing X"), not narrative context, and deserve
     their own clearly labeled section in the prompt rather than being
     diluted inside general history.
"""

from app.services.embedding_service import embed_text
from app.services.vector_store_pgvector import PgVectorStore

vector_store = PgVectorStore()


async def retrieve_historical_context(org_id: str, meeting_context: str, transcript_excerpt: str) -> dict:
    query_text = f"{meeting_context}\n{transcript_excerpt[:2000]}"
    query_embedding = await embed_text(query_text)

    history = await vector_store.query(
        org_id=org_id, embedding=query_embedding, top_k=5,
        content_types=["summary", "decision", "action_item"],
    )
    corrections = await vector_store.query(
        org_id=org_id, embedding=query_embedding, top_k=3,
        content_types=["correction"],
    )
    return {"history": history, "corrections": corrections}


def build_rag_prompt_block(retrieved: dict) -> str:
    """
    Renders retrieved rows into the prompt block injected into gemini_service.py.
    Kept as plain text, not JSON — Gemini follows prose instructions about
    *applying* corrections more reliably than it parses structured correction
    objects as imperatives.
    """
    lines = []

    if retrieved["history"]:
        lines.append("## Relevant context from this team's past meetings:")
        for row in retrieved["history"]:
            lines.append(f"- [{row['content_type']}] {row['source_text']}")

    if retrieved["corrections"]:
        lines.append("\n## Known corrections — apply these patterns, do not repeat past mistakes:")
        for row in retrieved["corrections"]:
            lines.append(f"- {row['source_text']}")

    return "\n".join(lines) if lines else ""
```

### Step 5.5 — Worked example: how a correction actually changes the next output

This is the concrete mechanism behind "the system gets smarter."

**Meeting 1.** Gemini generates an action item:
`{"task": "Update staging config", "assignee": "Ankit", ...}`
The user knows Ankit moved teams last month; the real owner is Priya. They
edit the MOM, changing `assignee` from `"Ankit"` to `"Priya"`.

**What `_diff_mom_fields` captures** (Phase 3):
`("action_items[<id>].assignee", "Ankit", "Priya")`

**What `embed_meeting_task` turns that into** (Phase 6) — a correction sentence:
> "In a past meeting, the AI assigned the task 'Update staging config' to
> Ankit, but the correct assignee was Priya."

This sentence is embedded and stored with `content_type='correction'`.

**Meeting 5, weeks later.** A new transcript mentions "staging config" again.
`retrieve_historical_context` finds that correction sentence by semantic
similarity (the embedding space relates "staging config" across meetings even
without exact word match) and injects it into the prompt under
`## Known corrections`. Gemini now has explicit, retrieved evidence that
Ankit is the wrong default for staging-related tasks — and the new MOM is
measurably more likely to get the assignee right the first time, with no
code change and no retraining. **This is the entire mechanism** — there is no
fine-tuning step; the "learning" is retrieval-time context injection, which
is why it compounds for free as more meetings and more corrections accumulate.

---

## PHASE 6 — RAG-AUGMENTED MOM GENERATION

### Step 6.1 — `services/gemini_service.py`

```python
"""
app/services/gemini_service.py

Same strict-JSON-schema discipline as the desktop app's GeminiMOMClient,
extended with one addition: the rag_context_block is inserted between the
system instructions and the transcript. Action items and key decisions are
assigned a stable UUID server-side immediately after parsing — this is what
makes precise field-level diffing possible in Phase 3's _diff_mom_fields.
"""

import json
import uuid
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from app.core.config import settings

genai.configure(api_key=settings.GEMINI_API_KEY)

_SYSTEM_PROMPT = """You are an expert corporate secretary for Indian business
meetings. Respond ONLY with valid JSON matching the given schema. Apply any
"Known corrections" provided as binding instructions, not suggestions."""


def _build_prompt(meeting_context: str, transcript: str, rag_context_block: str) -> str:
    return f"""
MEETING CONTEXT: {meeting_context}

{rag_context_block}

FULL TRANSCRIPT:
---
{transcript}
---

Extract the Minutes of Meeting as JSON:
{{
  "meeting_title": "...", "summary": "...",
  "key_decisions": ["..."],
  "action_items": [{{"task": "...", "assignee": "...", "deadline": "...", "priority": "High|Medium|Low"}}],
  "risks": ["..."], "next_steps": "..."
}}
"""


async def generate_mom(meeting_context: str, transcript: str, rag_context_block: str) -> dict:
    model = genai.GenerativeModel(
        model_name=settings.GEMINI_MODEL,
        system_instruction=_SYSTEM_PROMPT,
        generation_config=GenerationConfig(response_mime_type="application/json", temperature=0.1),
    )
    response = model.generate_content(_build_prompt(meeting_context, transcript, rag_context_block))
    mom = json.loads(response.text)

    # Assign stable IDs — required for precise correction diffing later (Phase 3)
    for item in mom.get("action_items", []):
        item["id"] = str(uuid.uuid4())
    for i, decision in enumerate(mom.get("key_decisions", [])):
        if isinstance(decision, str):
            mom["key_decisions"][i] = {"id": str(uuid.uuid4()), "text": decision}

    return mom
```

### Step 6.2 — `workers/tasks_mom.py`

```python
"""
app/workers/tasks_mom.py
Runs in the Celery worker process — NOT on the FastAPI request/WS thread.
Chains into embeddings + notifications as independent parallel tasks once
the MOM is persisted, mirroring the desktop app's Stop-button sequence but
fully decoupled from any single HTTP/WS connection's lifetime.
"""

from celery import group
from app.workers.celery_app import celery_app
from app.services.rag_service import retrieve_historical_context, build_rag_prompt_block
from app.services.gemini_service import generate_mom
from app.workers.tasks_embeddings import embed_meeting_task
from app.workers.tasks_notifications import send_email_task, send_whatsapp_task
from app.db import get_meeting_sync, save_mom_sync, set_meeting_status_sync


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def generate_mom_task(self, meeting_id: str, org_id: str):
    try:
        meeting = get_meeting_sync(meeting_id)

        retrieved = retrieve_historical_context(
            org_id=org_id,
            meeting_context=meeting.meeting_context,
            transcript_excerpt=meeting.raw_transcript,
        )
        rag_block = build_rag_prompt_block(retrieved)

        mom = generate_mom(meeting.meeting_context, meeting.raw_transcript, rag_block)
        save_mom_sync(meeting_id, mom)
        set_meeting_status_sync(meeting_id, "completed")

        # Independent, parallel follow-ups — none blocks the others
        group(
            embed_meeting_task.s(meeting_id=meeting_id, org_id=org_id, mom=mom),
            send_email_task.s(meeting_id=meeting_id),
            send_whatsapp_task.s(meeting_id=meeting_id),
        ).apply_async()

    except Exception as exc:
        set_meeting_status_sync(meeting_id, "failed")
        raise self.retry(exc=exc)
```

---

## PHASE 7 — ASYNC DISTRIBUTION (Celery + Redis)

### Step 7.1 — `workers/celery_app.py`

```python
from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "meeting_saas",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)
celery_app.conf.task_routes = {
    "app.workers.tasks_mom.*": {"queue": "mom_generation"},
    "app.workers.tasks_embeddings.*": {"queue": "embeddings"},
    "app.workers.tasks_notifications.*": {"queue": "notifications"},
}
```

### Step 7.2 — `workers/tasks_embeddings.py`

```python
"""
app/workers/tasks_embeddings.py
Two distinct entry points into the same task:
  - Called from generate_mom_task: embeds the new MOM's summary/decisions/items.
  - Called from the PATCH /meetings/{id}/mom endpoint: embeds correction
    sentences built from mom_edit_history rows.
"""

from app.workers.celery_app import celery_app
from app.services.embedding_service import embed_text
from app.services.vector_store_pgvector import PgVectorStore
from app.db import get_edit_history_rows_sync

vector_store = PgVectorStore()


@celery_app.task
def embed_meeting_task(meeting_id: str, org_id: str, mom: dict | None = None,
                        edit_history_ids: list[str] | None = None):
    if mom:
        _embed_mom_content(org_id, meeting_id, mom)
    if edit_history_ids:
        _embed_corrections(org_id, meeting_id, edit_history_ids)


def _embed_mom_content(org_id, meeting_id, mom):
    if mom.get("summary"):
        vec = embed_text(mom["summary"])
        vector_store.upsert(org_id, meeting_id, "summary", mom["summary"], vec, {})
    for decision in mom.get("key_decisions", []):
        text = decision["text"] if isinstance(decision, dict) else decision
        vector_store.upsert(org_id, meeting_id, "decision", text, embed_text(text), {})
    for item in mom.get("action_items", []):
        text = f"{item['task']} → {item['assignee']} by {item['deadline']}"
        vector_store.upsert(org_id, meeting_id, "action_item", text, embed_text(text), {"item_id": item["id"]})


def _embed_corrections(org_id, meeting_id, edit_history_ids):
    rows = get_edit_history_rows_sync(edit_history_ids)
    for row in rows:
        sentence = (
            f"In a past meeting, the AI set {row.field_path} to "
            f"'{row.old_value}', but the correct value was '{row.new_value}'."
        )
        vec = embed_text(sentence)
        vector_store.upsert(org_id, meeting_id, "correction", sentence, vec, {"field_path": row.field_path})
```

### Step 7.3 — `workers/tasks_notifications.py` (ported from desktop, unchanged content logic)

```python
"""
app/workers/tasks_notifications.py
Direct Celery-task port of the desktop app's EmailSender + WhatsAppAlert.
Same Jinja2 template, same Twilio client, same retry posture — only the
execution context (Celery task vs. direct call after Stop) has changed.
"""

from app.workers.celery_app import celery_app
from app.db import get_meeting_with_mom_sync, get_recipients_for_meeting_sync
# Reuse the desktop app's html_formatter.py and email_sender.py / whatsapp_alert.py
# logic unchanged — only the entry point becomes a Celery task signature.


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(self, meeting_id: str):
    meeting, mom = get_meeting_with_mom_sync(meeting_id)
    recipients = get_recipients_for_meeting_sync(meeting_id)  # users' notify_email
    # html = HTMLFormatter().render(mom); EmailSender().send(mom, html, recipients)
    # On SMTP failure: raise self.retry(exc=e)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def send_whatsapp_task(self, meeting_id: str):
    meeting, mom = get_meeting_with_mom_sync(meeting_id)
    recipients = get_recipients_for_meeting_sync(meeting_id)  # users' whatsapp_number
    # WhatsAppAlert().send_alert(mom, recipients)
```

---

## PHASE 8 — FRONTEND: AUTH & DASHBOARD

### Step 8.1 — Auth flow contract

- `middleware.ts` checks for a valid session cookie on every `(dashboard)/*` route; redirects to `/login` if absent.
- Access token is held in memory (a React context), **not** localStorage (XSS exposure). Refresh token lives in the httpOnly cookie set by the backend.
- `lib/api-client.ts` wraps `fetch`, auto-retries once on a 401 by calling `/auth/refresh`, then re-issues the original request.

### Step 8.2 — `app/(dashboard)/settings/page.tsx` (contract)

Simple form bound to `PATCH /api/v1/users/me`:

| Field | Maps to |
|---|---|
| WhatsApp Number (with country code) | `whatsapp_number` |
| Notification Email | `notify_email` |

Standard React Hook Form + Zod validation; no novel logic — implement
following the `MomEditor.tsx` pattern in Phase 10 for the PATCH-and-toast flow.

---

## PHASE 9 — FRONTEND: LIVE MEETING CAPTURE (Critical)

### Step 9.1 — `components/AudioRecorder.tsx` — full code

```tsx
/**
 * components/AudioRecorder.tsx
 *
 * Flow:
 *   1. On mount: POST /meetings/{id}/ws-ticket (authenticated REST call)
 *      to get a short-lived ticket.
 *   2. Open WebSocket with that ticket in the query string.
 *   3. getUserMedia (mic) + optionally getDisplayMedia (tab audio, see
 *      Phase 1's capability-gap note) → merge via AudioContext if both
 *      are enabled.
 *   4. MediaRecorder on the merged stream, timeslice=5000ms, sends each
 *      Blob as a binary WS frame as soon as it's available.
 *   5. On Stop click: send {"type":"stop"} text frame, then close.
 */

"use client";
import { useRef, useState, useCallback } from "react";
import { apiClient } from "@/lib/api-client";

interface Props {
  meetingId: string;
  captureTabAudio: boolean;
  onPartialTranscript: (text: string) => void;
  onError: (message: string) => void;
}

export function AudioRecorder({ meetingId, captureTabAudio, onPartialTranscript, onError }: Props) {
  const [isRecording, setIsRecording] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamsRef = useRef<MediaStream[]>([]);

  const start = useCallback(async () => {
    // 1. Mint a single-use WS ticket via authenticated REST
    const { ticket } = await apiClient.post(`/meetings/${meetingId}/ws-ticket`);

    // 2. Open the WS using the ticket (NOT the JWT) in the query string
    const ws = new WebSocket(`${process.env.NEXT_PUBLIC_WS_URL}/ws/meetings/${meetingId}/audio?ticket=${ticket}`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "partial_transcript") onPartialTranscript(msg.text);
      if (msg.type === "stt_error") onError(msg.message);
    };

    await new Promise<void>((resolve) => { ws.onopen = () => resolve(); });

    // 3. Acquire mic, optionally merge tab audio
    const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    streamsRef.current.push(micStream);
    let finalStream = micStream;

    if (captureTabAudio) {
      const tabStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
      streamsRef.current.push(tabStream);
      const audioContext = new AudioContext();
      const dest = audioContext.createMediaStreamDestination();
      audioContext.createMediaStreamSource(micStream).connect(dest);
      const tabAudioTrack = tabStream.getAudioTracks()[0];
      if (tabAudioTrack) {
        audioContext.createMediaStreamSource(new MediaStream([tabAudioTrack])).connect(dest);
      }
      tabStream.getVideoTracks().forEach((t) => t.stop()); // discard video, audio only
      finalStream = dest.stream;
    }

    // 4. MediaRecorder, 5s timeslice, stream each Blob as binary WS frame
    const recorder = new MediaRecorder(finalStream, { mimeType: "audio/webm;codecs=opus" });
    recorder.ondataavailable = async (e) => {
      if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
        ws.send(await e.data.arrayBuffer());
      }
    };
    recorder.start(5000);
    recorderRef.current = recorder;
    setIsRecording(true);
  }, [meetingId, captureTabAudio, onPartialTranscript, onError]);

  const stop = useCallback(() => {
    recorderRef.current?.stop();
    streamsRef.current.forEach((s) => s.getTracks().forEach((t) => t.stop()));
    wsRef.current?.send(JSON.stringify({ type: "stop" }));
    wsRef.current?.close();
    setIsRecording(false);
  }, []);

  return (
    <div className="flex gap-3">
      <button onClick={start} disabled={isRecording} className="btn-primary">▶ Start Recording</button>
      <button onClick={stop} disabled={!isRecording} className="btn-danger">■ Stop & Generate MOM</button>
    </div>
  );
}
```

---

## PHASE 10 — FRONTEND: PAST MEETINGS & MOM EDITOR

### Step 10.1 — `components/MomEditor.tsx` (contract + key submit logic)

The list view (`meetings/page.tsx`) and detail fetch are standard
`GET`-and-render — not detailed here. The submit handler is the frontend half
of the learning loop and is given in full:

```tsx
async function handleSave(meetingId: string, updatedMom: MomData) {
  const res = await apiClient.patch(`/meetings/${meetingId}/mom`, updatedMom);
  // Backend returns { status, version, corrections_captured }
  if (res.corrections_captured > 0) {
    toast.success(`Saved. ${res.corrections_captured} correction(s) recorded — future meetings will use this.`);
  } else {
    toast.success("Saved.");
  }
}
```

Surfacing `corrections_captured` to the user is a deliberate product choice:
it makes the "the system is learning from this edit" promise visible and
verifiable, rather than an invisible backend detail.

---

## PHASE 11 — DEPLOYMENT & PRODUCTION HARDENING

### Step 11.1 — nginx WebSocket proxy config (common production gotcha)

```nginx
location /ws/ {
    proxy_pass http://backend:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;   # WS connections are long-lived; default 60s will kill them
}

location /api/ {
    proxy_pass http://backend:8000;
    proxy_set_header Host $host;
}
```

### Step 11.2 — Production checklist

- [ ] `ffmpeg` installed in the backend Docker image (`apt-get install -y ffmpeg`)
- [ ] Alembic migrations run on deploy, before the backend container starts serving traffic
- [ ] `pgvector` HNSW index built (`CREATE INDEX ... USING hnsw`) before any meaningful embedding volume accumulates — building it after the fact on a large table is a long-running, lock-relevant operation
- [ ] Celery worker `--concurrency` tuned separately for the `notifications` queue (I/O-bound, can be higher) vs. `mom_generation` queue (Gemini-call-bound, rate-limit aware)
- [ ] `WS_TICKET_EXPIRE_SECONDS` and Redis ticket storage confirmed working under load (a ticket minted but not consumed within 30s must fail closed, not silently extend)

---

## PHASE 12 — VALIDATION PLAN

| Test | Validates |
|---|---|
| Register org → login → PATCH `/users/me` with WhatsApp number | Auth + dashboard settings |
| Open WS with an expired/already-used ticket | Ticket single-use + expiry enforcement (security-critical) |
| Stream 3×5s silent chunks + 2×5s spoken chunks | VAD correctly drops silence, only spoken chunks reach Sarvam AI |
| Kill the FastAPI process mid-meeting, restart, reconnect WS for same meeting_id | Redis-backed transcript buffer survives process restart |
| Run `generate_mom_task` for an org with zero prior meetings | RAG retrieval returns empty gracefully; Gemini prompt has no "Relevant context" section, generation still succeeds |
| Edit an action item's assignee in the MOM editor, then run a second unrelated meeting mentioning the same task | The correction surfaces in `## Known corrections` for the second meeting's generation — **this is the end-to-end proof the learning loop works** |
| Attempt to query `meeting_embeddings` for Org A while authenticated as Org B | RLS policy blocks the read even if an application WHERE clause were hypothetically missing |

---

*End of PLAN_WEB_SAAS.md — Critical-path modules fully specified; standard CRUD scaffolding specified by contract for agent implementation following established patterns above.*
