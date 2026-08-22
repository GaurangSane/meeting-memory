"""
app/db.py — Synchronous DB helper functions for Celery workers.

Celery tasks run in a synchronous process context (no running asyncio event
loop). The async SQLAlchemy engine and all async session methods cannot be
used directly inside a Celery task body. This module provides thin sync
wrappers using asyncio.run() to bridge into the async ORM layer.

DESIGN RULE:
  - These helpers are exclusively for Celery worker code.
  - FastAPI route handlers MUST use the async `get_db` dependency instead.
  - Never import these in api/v1/* — that direction is a code-smell.

Each helper opens its own engine connection, runs the query, and closes. This
is intentionally simple and suitable for Celery's one-task-at-a-time execution
model. For high-throughput workers, a dedicated sync engine with a connection
pool would be preferable, but that optimisation belongs in a later phase.

DB helpers provided
-------------------
get_meeting_sync(meeting_id)          → Meeting ORM object or raises
save_mom_sync(meeting_id, mom_dict)   → creates or updates MomRecord
set_meeting_status_sync(id, status)   → updates meetings.status
get_edit_history_rows_sync(ids)       → list[MomEditHistory] for embedding
get_meeting_with_mom_sync(meeting_id) → (Meeting, MomRecord) or raises
get_recipients_for_meeting_sync(id)   → list[User] with notification prefs
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.meeting import Meeting
from app.models.mom import MomEditHistory, MomRecord
from app.models.user import User

logger = logging.getLogger(__name__)

# ── Celery-dedicated DB engine ────────────────────────────────────────────────
# NullPool disables connection pooling so each asyncio.run() call gets a fresh
# connection that is fully closed when the coroutine exits. This prevents the
# asyncpg InterfaceError that occurs when a pooled connection is shared across
# preforked worker processes or separate event loops.
celery_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
CelerySessionLocal = async_sessionmaker(celery_engine, expire_on_commit=False)


# ── Internal async implementations ────────────────────────────────────────────
# All actual DB work is async; sync wrappers call asyncio.run() on these.

async def _get_meeting(meeting_id: str) -> Meeting:
    async with CelerySessionLocal() as session:
        result = await session.execute(
            select(Meeting).where(Meeting.id == meeting_id)
        )
        meeting = result.scalar_one_or_none()
        if meeting is None:
            raise ValueError(f"Meeting {meeting_id} not found")
        return meeting


async def _save_mom(meeting_id: str, mom_dict: dict) -> None:
    async with CelerySessionLocal() as session:
        async with session.begin():
            # Check if a MomRecord already exists for this meeting
            result = await session.execute(
                select(MomRecord).where(MomRecord.meeting_id == meeting_id)
            )
            existing = result.scalar_one_or_none()

            if existing is not None:
                # Update in place (should not normally happen in the generation path,
                # but guard against duplicate Celery task execution)
                existing.summary       = mom_dict.get("summary")
                existing.key_decisions = mom_dict.get("key_decisions", [])
                existing.action_items  = mom_dict.get("action_items", [])
                existing.risks         = mom_dict.get("risks", [])
                existing.next_steps    = mom_dict.get("next_steps")
                logger.warning("save_mom called on meeting with existing MOM: %s", meeting_id)
            else:
                mom_record = MomRecord(
                    meeting_id=meeting_id,
                    summary=mom_dict.get("summary"),
                    key_decisions=mom_dict.get("key_decisions", []),
                    action_items=mom_dict.get("action_items", []),
                    risks=mom_dict.get("risks", []),
                    next_steps=mom_dict.get("next_steps"),
                )
                session.add(mom_record)


async def _set_meeting_status(
    meeting_id: str,
    status: str,
    raw_transcript: str | None = None,
) -> None:
    async with CelerySessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                select(Meeting).where(Meeting.id == meeting_id)
            )
            meeting = result.scalar_one_or_none()
            if meeting is None:
                logger.error("set_meeting_status: meeting %s not found", meeting_id)
                return
            meeting.status = status
            if raw_transcript is not None:
                meeting.raw_transcript = raw_transcript
            if status == "completed":
                meeting.completed_at = datetime.now(timezone.utc)


async def _get_edit_history_rows(ids: list[str]) -> list[MomEditHistory]:
    async with CelerySessionLocal() as session:
        result = await session.execute(
            select(MomEditHistory).where(MomEditHistory.id.in_(ids))
        )
        return list(result.scalars().all())


async def _get_meeting_with_mom(meeting_id: str) -> tuple[Meeting, MomRecord]:
    async with CelerySessionLocal() as session:
        meeting_result = await session.execute(
            select(Meeting).where(Meeting.id == meeting_id)
        )
        meeting = meeting_result.scalar_one_or_none()
        if meeting is None:
            raise ValueError(f"Meeting {meeting_id} not found")

        mom_result = await session.execute(
            select(MomRecord).where(MomRecord.meeting_id == meeting_id)
        )
        mom = mom_result.scalar_one_or_none()
        if mom is None:
            raise ValueError(f"MomRecord for meeting {meeting_id} not found")

        return meeting, mom


async def _get_recipients_for_meeting(meeting_id: str) -> list[User]:
    async with CelerySessionLocal() as session:
        # Fetch the meeting to get org_id
        m_result = await session.execute(
            select(Meeting).where(Meeting.id == meeting_id)
        )
        meeting = m_result.scalar_one_or_none()
        if meeting is None:
            return []

        # Return all org users who have at least one notification channel configured
        u_result = await session.execute(
            select(User).where(
                User.org_id == meeting.org_id,
            )
        )
        return list(u_result.scalars().all())


# ── Public sync API (used by Celery workers only) ─────────────────────────────

def get_meeting_sync(meeting_id: str) -> Meeting:
    """Fetch a Meeting by ID. Raises ValueError if not found."""
    return asyncio.run(_get_meeting(meeting_id))


def save_mom_sync(meeting_id: str, mom_dict: dict) -> None:
    """Create or update the MomRecord for a given meeting."""
    asyncio.run(_save_mom(meeting_id, mom_dict))


def set_meeting_status_sync(
    meeting_id: str,
    status: str,
    raw_transcript: str | None = None,
) -> None:
    """Update the status (and optionally raw_transcript) of a Meeting."""
    asyncio.run(_set_meeting_status(meeting_id, status, raw_transcript))


def get_edit_history_rows_sync(ids: list[str]) -> list[MomEditHistory]:
    """Fetch MomEditHistory rows by their UUIDs (for correction embedding)."""
    return asyncio.run(_get_edit_history_rows(ids))


def get_meeting_with_mom_sync(meeting_id: str) -> tuple[Meeting, MomRecord]:
    """Fetch a Meeting and its MomRecord. Raises ValueError if either is missing."""
    return asyncio.run(_get_meeting_with_mom(meeting_id))


def get_recipients_for_meeting_sync(meeting_id: str) -> list[User]:
    """Return all users in the org that owns this meeting."""
    return asyncio.run(_get_recipients_for_meeting(meeting_id))


# ── WebSocket helper (used by websocket_audio.py) ─────────────────────────────
# This one is async because it is called from an async WS handler context.

async def set_meeting_status(
    meeting_id: str,
    status: str,
    raw_transcript: str | None = None,
) -> None:
    """
    Async version of set_meeting_status_sync — used by the WS handler
    (which already runs inside an asyncio event loop).
    """
    await _set_meeting_status(meeting_id, status, raw_transcript)
