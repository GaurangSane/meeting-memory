"""
app/workers/tasks_embeddings.py

embed_meeting_task — Phase 7, Step 7.2.

Two distinct entry points into the same task:
  1. Called from generate_mom_task with `mom` dict:
     Embeds the new MOM's summary, key decisions, and action items.
     These become the 'history' rows retrieved by future RAG queries.

  2. Called from PATCH /meetings/{id}/mom with `edit_history_ids`:
     Embeds correction sentences built from mom_edit_history rows.
     These become the 'correction' rows that feed back into the RAG
     prompt's "## Known corrections" section for future meetings.

Sync/async bridge
-----------------
Celery tasks are synchronous. The embedding service and vector store are
async. We use asyncio.run() to bridge sync → async for each individual
DB+network call. We do NOT create a single shared event loop across calls
because that would require careful lifecycle management in the worker process.
asyncio.run() creates and closes a fresh event loop per call, which is the
correct pattern for Celery workers that may process many tasks in sequence.

Correction sentence format
--------------------------
"In a past meeting, the AI set {field_path} to '{old_value}',
 but the correct value was '{new_value}'."

This is the canonical format documented in PLAN_WEB_SAAS.md §5.5. The
sentence must be human-readable prose (not JSON) because Gemini needs to
parse it as a behavioural instruction when retrieved at generation time.

Example:
  field_path = "action_items[<uuid>].assignee"
  old_value  = "Ankit"
  new_value  = "Priya"
  sentence   = "In a past meeting, the AI set action_items[<uuid>].assignee
                to 'Ankit', but the correct value was 'Priya'."
"""

import asyncio
import logging

from app.db import get_edit_history_rows_sync
from app.services.embedding_service import embed_text
from app.services.vector_store_pgvector import PgVectorStore
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

vector_store = PgVectorStore()


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=3, default_retry_delay=15)
def embed_meeting_task(
    self,
    meeting_id: str,
    org_id: str,
    mom: dict | None = None,
    edit_history_ids: list[str] | None = None,
) -> None:
    """
    Embed MOM content and/or correction sentences into meeting_embeddings.

    Both paths may be active in the same call (unlikely but safe).
    Each embedding operation is independent — a failure in one does not
    prevent the others from running (within the same task retry scope).

    Args:
        meeting_id:       The meeting UUID whose MOM or corrections to embed.
        org_id:           The organisation UUID (required for tenant-scoped upsert).
        mom:              Full MOM dict from generate_mom() (Phase 6). If provided,
                          embeds summary, key_decisions, and action_items.
        edit_history_ids: List of MomEditHistory UUIDs to convert to correction
                          sentences and embed. If provided, reads rows from DB.
    """
    try:
        if mom:
            logger.info(
                "embed_meeting_task: embedding MOM content for meeting=%s", meeting_id
            )
            _embed_mom_content(org_id, meeting_id, mom)

        if edit_history_ids:
            logger.info(
                "embed_meeting_task: embedding %d corrections for meeting=%s",
                len(edit_history_ids), meeting_id,
            )
            _embed_corrections(org_id, meeting_id, edit_history_ids)

    except Exception as exc:
        logger.error(
            "embed_meeting_task failed for meeting=%s: %s", meeting_id, exc, exc_info=True
        )
        raise self.retry(exc=exc)


# ── Private helpers ───────────────────────────────────────────────────────────

def _embed_mom_content(org_id: str, meeting_id: str, mom: dict) -> None:
    """
    Embed the three content types from a newly generated MOM.

    summary      → single row, content_type='summary'
    key_decisions → one row per decision, content_type='decision'
    action_items  → one row per item, content_type='action_item'
                    metadata carries item_id for future attribution

    Each embed+upsert pair is called separately so a single failure (e.g.
    rate limit on embedding request #3) does not skip all subsequent items.
    """
    # Summary
    summary = mom.get("summary")
    if summary:
        try:
            vec = asyncio.run(embed_text(summary))
            asyncio.run(
                vector_store.upsert(org_id, meeting_id, "summary", summary, vec, {})
            )
            logger.debug("Embedded summary for meeting=%s", meeting_id)
        except Exception as exc:
            logger.error("Failed to embed summary for meeting=%s: %s", meeting_id, exc)
            raise  # let Celery retry the whole task

    # Key decisions
    for decision in mom.get("key_decisions", []):
        text = decision["text"] if isinstance(decision, dict) else str(decision)
        if not text.strip():
            continue
        try:
            vec = asyncio.run(embed_text(text))
            asyncio.run(
                vector_store.upsert(org_id, meeting_id, "decision", text, vec, {})
            )
        except Exception as exc:
            logger.error(
                "Failed to embed decision for meeting=%s: %s", meeting_id, exc
            )
            raise

    # Action items
    for item in mom.get("action_items", []):
        if not isinstance(item, dict):
            continue
        item_text = (
            f"{item.get('task', '')} → {item.get('assignee', '')} "
            f"by {item.get('deadline', 'TBD')}"
        )
        item_id = item.get("id", "")
        try:
            vec = asyncio.run(embed_text(item_text))
            asyncio.run(
                vector_store.upsert(
                    org_id, meeting_id, "action_item", item_text, vec,
                    {"item_id": item_id},
                )
            )
        except Exception as exc:
            logger.error(
                "Failed to embed action_item %s for meeting=%s: %s",
                item_id, meeting_id, exc,
            )
            raise


def _embed_corrections(
    org_id: str, meeting_id: str, edit_history_ids: list[str]
) -> None:
    """
    Convert MomEditHistory rows into correction sentences and embed them.

    Correction sentence format (from PLAN_WEB_SAAS.md §5.5):
      "In a past meeting, the AI set {field_path} to '{old_value}',
       but the correct value was '{new_value}'."

    The sentence is stored as source_text alongside the vector and will
    be injected verbatim into the Gemini prompt under "## Known corrections"
    when the same org runs a future meeting with a semantically related topic.

    metadata carries field_path for future debugging/audit.
    """
    rows = get_edit_history_rows_sync(edit_history_ids)

    for row in rows:
        old_val = row.old_value or "(none)"
        new_val = row.new_value or "(none)"
        sentence = (
            f"In a past meeting, the AI set {row.field_path} to "
            f"'{old_val}', but the correct value was '{new_val}'."
        )
        try:
            vec = asyncio.run(embed_text(sentence))
            asyncio.run(
                vector_store.upsert(
                    org_id, meeting_id, "correction", sentence, vec,
                    {"field_path": row.field_path},
                )
            )
            logger.info(
                "Embedded correction: field=%s old=%r new=%r meeting=%s",
                row.field_path, old_val[:40], new_val[:40], meeting_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to embed correction row=%s: %s", row.id, exc
            )
            raise  # let Celery retry
