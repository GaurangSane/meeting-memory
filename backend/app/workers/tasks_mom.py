"""
app/workers/tasks_mom.py

generate_mom_task — Phase 6, Step 6.2.

Runs in the Celery worker process — NOT on the FastAPI WS handler thread.
The task is enqueued by websocket_audio.py when the client sends {"type":"stop"}
and all in-flight STT futures have been awaited.

Processing pipeline (within this task):
  1. Fetch the meeting from Postgres (raw_transcript must already be saved).
  2. Run two-pass RAG retrieval via rag_service.py (may return empty for
     the first meeting — handled gracefully).
  3. Build the RAG prompt block (plain text, empty string if no history).
  4. Call Gemini to generate the structured MOM JSON.
  5. Save the MomRecord to Postgres and mark meeting status='completed'.
  6. Enqueue three independent parallel follow-up tasks:
       a) embed_meeting_task — embeds summary/decisions/items for future RAG
       b) send_email_task    — emails the MOM to org members
       c) send_whatsapp_task — WhatsApp alert
     All three are in a Celery group so they run concurrently in their
     respective queues. None blocks the others.

Failure handling:
  - If any step raises, meeting status is set to 'failed' and the task is
    retried up to 3 times with 10-second delays.
  - The parallel follow-up tasks have their own retry posture (see tasks_*.py).
  - Celery result backend tracks each task's state independently.

Async/sync bridge:
  The Celery task body is synchronous. The async services (rag_service,
  gemini_service, db helpers) are called via asyncio.run() in the DB helpers
  module, and the services themselves run async inside the event loop created
  by asyncio.run(). This is the correct pattern for Celery workers — do NOT
  try to share a single event loop across tasks.
"""

import asyncio
import logging

from celery import group

from app.db import (
    get_meeting_sync,
    get_meeting_with_mom_sync,
    save_mom_sync,
    set_meeting_status_sync,
)
from app.services.gemini_service import generate_mom
from app.services.rag_service import build_rag_prompt_block, retrieve_historical_context
from app.workers.celery_app import celery_app
from app.workers.tasks_embeddings import embed_meeting_task
from app.workers.tasks_notifications import send_email_task, send_whatsapp_task

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def generate_mom_task(self, meeting_id: str, org_id: str) -> None:
    """
    RAG-augmented MOM generation pipeline.

    This is the Celery equivalent of the desktop app's Stop-button handler,
    fully decoupled from any HTTP/WS connection lifetime.

    Args:
        meeting_id: UUID of the meeting whose transcript to process.
        org_id:     UUID of the owning organisation (passed explicitly so
                    no DB call is needed to discover it; also used to scope
                    the RAG retrieval correctly).
    """
    try:
        # ── Step 1: Fetch the meeting ──────────────────────────────────────
        logger.info(
            "generate_mom_task started meeting=%s org=%s", meeting_id, org_id
        )
        meeting = get_meeting_sync(meeting_id)

        if not meeting.raw_transcript:
            logger.warning(
                "Meeting=%s has no transcript — skipping MOM generation", meeting_id
            )
            set_meeting_status_sync(meeting_id, "failed")
            return

        # ── Step 2: RAG retrieval ──────────────────────────────────────────
        # retrieve_historical_context is async; run it in a fresh event loop.
        retrieved = asyncio.run(
            retrieve_historical_context(
                org_id=org_id,
                meeting_context=meeting.meeting_context,
                transcript_excerpt=meeting.raw_transcript,
            )
        )

        # ── Step 3: Build RAG prompt block ────────────────────────────────
        rag_block = build_rag_prompt_block(retrieved)
        logger.info(
            "RAG block built: %d history rows, %d corrections, block_len=%d",
            len(retrieved.get("history", [])),
            len(retrieved.get("corrections", [])),
            len(rag_block),
        )

        # ── Step 4: Generate MOM with Gemini ──────────────────────────────
        mom = asyncio.run(
            generate_mom(
                meeting_context=meeting.meeting_context,
                transcript=meeting.raw_transcript,
                rag_context_block=rag_block,
            )
        )
        logger.info(
            "Gemini MOM generated for meeting=%s: %d action_items, %d decisions",
            meeting_id,
            len(mom.get("action_items", [])),
            len(mom.get("key_decisions", [])),
        )

        # ── Step 5: Persist MOM and mark meeting completed ─────────────────
        save_mom_sync(meeting_id, mom)
        set_meeting_status_sync(meeting_id, "completed")
        logger.info("MOM persisted and meeting=%s marked completed", meeting_id)

        # ── Step 6: Parallel follow-up tasks ──────────────────────────────
        # None of these tasks blocks the others — they run concurrently in
        # their respective queues ('embeddings' and 'notifications').
        group(
            embed_meeting_task.s(
                meeting_id=meeting_id,
                org_id=org_id,
                mom=mom,
            ),
            send_email_task.s(meeting_id=meeting_id),
            send_whatsapp_task.s(meeting_id=meeting_id),
        ).apply_async()

        logger.info(
            "Enqueued embed + notify follow-ups for meeting=%s", meeting_id
        )

    except Exception as exc:
        logger.error(
            "generate_mom_task failed for meeting=%s: %s",
            meeting_id, exc, exc_info=True,
        )
        # Mark as failed before retrying so the UI can show the error state
        try:
            set_meeting_status_sync(meeting_id, "failed")
        except Exception:
            pass  # don't mask the original exception

        raise self.retry(exc=exc)
