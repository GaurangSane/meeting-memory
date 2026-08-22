"""
app/api/v1/meetings.py

Meetings router — Phase 3, Step 3.2 of PLAN_WEB_SAAS.md.

Endpoints
---------
POST   /api/v1/meetings
    Create a new Meeting record in 'recording' status.
    Called by the frontend immediately before opening the WebSocket audio
    stream, so the meeting ID exists in the DB before audio arrives.

GET    /api/v1/meetings
    Paginated list of meetings for the authenticated org.
    Only returns meetings belonging to the caller's org (enforced by the
    RLS policy + explicit WHERE org_id = :org_id).

GET    /api/v1/meetings/{meeting_id}
    Single meeting detail, including the embedded MomRecord if it exists.

DELETE /api/v1/meetings/{meeting_id}
    Hard-delete a meeting owned by the authenticated org. Related MOM records
    and embeddings are removed by ON DELETE CASCADE.

POST   /api/v1/meetings/{meeting_id}/retry
    Retry MOM generation for a failed meeting that has a persisted transcript.

POST   /api/v1/meetings/{meeting_id}/ws-ticket
    Mint a 30-second single-use WebSocket ticket for this meeting.
    The caller must hold a valid Bearer access token. The ticket is then
    passed in the WS query string (see api/v1/websocket_audio.py).
    The reason for this indirection is documented in core/security.py.

PATCH  /api/v1/meetings/{meeting_id}/mom  ← THE LEARNING LOOP CAPTURE POINT
    Described in full below. This endpoint is the ONLY place corrections
    are captured. Bypassing it (writing directly to mom_records) silently
    breaks the RAG learning loop.

─────────────────────────────────────────────────────────────────────────────
PATCH /meetings/{id}/mom — how the learning loop works
─────────────────────────────────────────────────────────────────────────────

When a user edits an AI-generated MOM in the Past Meetings view:

  1. _diff_mom_fields(old, new) computes field-level diffs.
     action_items and key_decisions are matched by their stable `id`
     (assigned server-side at Gemini generation time in gemini_service.py),
     so an edit to "item 3's assignee" is captured as exactly that — not
     as a wholesale list replacement that loses which specific field changed.

  2. One MomEditHistory row is written per changed field (audit trail).
     Each row records: field_path, old_value, new_value, edited_by, org_id.

  3. The MomRecord is updated and its version counter incremented.

  4. If any diffs were found, embed_meeting_task.delay() is enqueued.
     That Celery task (Phase 7) converts each diff into a human-readable
     "correction sentence" and embeds it as content_type='correction' in
     meeting_embeddings, where rag_service.py will find it on future meetings.

  5. The response includes `corrections_captured` so the frontend can
     show "X correction(s) recorded — future meetings will use this".
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.security import mint_ws_ticket
from app.models.meeting import Meeting
from app.models.mom import MomEditHistory, MomRecord
from app.models.user import User
from app.schemas.meeting import (
    MeetingCreateRequest,
    MeetingDetailResponse,
    MeetingResponse,
    MomPatchResponse,
    WsTicketResponse,
)
from app.schemas.mom import MomResponse, MomUpdateRequest
from app.workers.tasks_mom import generate_mom_task
from app.workers.tasks_embeddings import embed_meeting_task

logger = logging.getLogger(__name__)

router = APIRouter()


# ── POST /meetings ────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=MeetingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new meeting (call before opening the WebSocket audio stream)",
)
async def create_meeting(
    payload: MeetingCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Meeting:
    """
    Create a meeting record in 'recording' status.

    The frontend should call this endpoint first, receive the meeting ID,
    then immediately call POST /meetings/{id}/ws-ticket to get a WS ticket,
    and then open the WebSocket audio stream.
    """
    meeting = Meeting(
        org_id=current_user.org_id,
        created_by=current_user.id,
        title=payload.title,
        meeting_context=payload.meeting_context,
        status="recording",
    )
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)

    logger.info(
        "Created meeting=%s org=%s by user=%s",
        meeting.id, meeting.org_id, current_user.id,
    )
    return meeting


# ── GET /meetings ─────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[MeetingResponse],
    summary="List all meetings for the authenticated org (newest first)",
)
async def list_meetings(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Meeting]:
    """
    Return meetings belonging to the caller's organisation, newest first.

    The WHERE org_id filter is defence-in-depth alongside the RLS policy —
    both layers must be present (never rely on RLS alone).
    """
    result = await db.execute(
        select(Meeting)
        .where(Meeting.org_id == current_user.org_id)
        .order_by(Meeting.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


# ── GET /meetings/{meeting_id} ────────────────────────────────────────────────

@router.get(
    "/{meeting_id}",
    response_model=MeetingDetailResponse,
    summary="Get a single meeting and its MOM (if generated)",
)
async def get_meeting(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MeetingDetailResponse:
    """
    Return a meeting by ID, including its MomRecord if available.

    Both the meeting's org_id and the RLS policy ensure this endpoint
    cannot return a meeting belonging to a different tenant.
    """
    result = await db.execute(
        select(Meeting)
        .where(
            Meeting.id == meeting_id,
            Meeting.org_id == current_user.org_id,  # explicit defence-in-depth
        )
    )
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    # Load MOM if it exists (LEFT JOIN style via the ORM relationship)
    mom_result = await db.execute(
        select(MomRecord).where(MomRecord.meeting_id == meeting.id)
    )
    mom_record = mom_result.scalar_one_or_none()

    mom_response: MomResponse | None = None
    if mom_record is not None:
        mom_response = MomResponse.model_validate(mom_record)

    return MeetingDetailResponse(
        id=meeting.id,
        org_id=meeting.org_id,
        created_by=meeting.created_by,
        title=meeting.title,
        meeting_context=meeting.meeting_context,
        status=meeting.status,
        created_at=meeting.created_at,
        completed_at=meeting.completed_at,
        mom=mom_response,
    )


# ── DELETE /meetings/{meeting_id} ─────────────────────────────────────────────

@router.delete(
    "/{meeting_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a meeting and its generated data",
)
async def delete_meeting(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Hard-delete a meeting owned by the caller's organisation.

    The database schema enforces ON DELETE CASCADE from meetings to mom_records
    and meeting_embeddings, so this endpoint only deletes the parent meeting
    row after verifying tenant ownership.
    """
    result = await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.org_id == current_user.org_id,
        )
    )
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    await db.delete(meeting)
    await db.commit()

    logger.info(
        "Deleted meeting=%s org=%s by user=%s",
        meeting_id, current_user.org_id, current_user.id,
    )


# ── POST /meetings/{meeting_id}/retry ─────────────────────────────────────────

@router.post(
    "/{meeting_id}/retry",
    response_model=MeetingResponse,
    summary="Retry MOM generation for a failed meeting",
)
async def retry_meeting_mom(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Meeting:
    """
    Re-enqueue MOM generation for a failed meeting that already has audio text.

    If no transcript was captured, retrying Gemini cannot help, so the API
    returns 409 and the user should record again.
    """
    result = await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.org_id == current_user.org_id,
        )
    )
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    if meeting.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry a meeting with status '{meeting.status}'",
        )

    if not meeting.raw_transcript:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No transcript was captured for this meeting. Please record again.",
        )

    meeting.status = "processing"
    meeting.completed_at = None
    await db.commit()
    await db.refresh(meeting)

    generate_mom_task.delay(meeting_id=meeting_id, org_id=str(current_user.org_id))
    logger.info(
        "Retried MOM generation for meeting=%s org=%s by user=%s",
        meeting_id, current_user.org_id, current_user.id,
    )

    return meeting


# ── POST /meetings/{meeting_id}/ws-ticket ─────────────────────────────────────

@router.post(
    "/{meeting_id}/ws-ticket",
    response_model=WsTicketResponse,
    summary="Mint a 30-second single-use WebSocket ticket for this meeting",
)
async def create_ws_ticket(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WsTicketResponse:
    """
    Issue a short-lived, single-use WS ticket.

    The ticket is stored in Redis with a 30-second TTL and is consumed
    (deleted) by the WebSocket handler on first connection — see
    core/security.py::consume_ws_ticket for the atomicity guarantee.

    Why not just use the JWT in the WS query string?
    See PLAN_WEB_SAAS.md §0 architectural decisions for the full rationale.
    Short version: a 30-second ticket that appears in access logs is far
    less dangerous than a 15-minute JWT.
    """
    # Verify the meeting exists and belongs to this org
    result = await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.org_id == current_user.org_id,
        )
    )
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    if meeting.status not in ("recording", "processing"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot open audio stream for a meeting with status '{meeting.status}'",
        )

    ticket = await mint_ws_ticket(
        user_id=str(current_user.id),
        org_id=str(current_user.org_id),
        meeting_id=meeting_id,
    )
    logger.info("Minted WS ticket for meeting=%s user=%s", meeting_id, current_user.id)
    return WsTicketResponse(ticket=ticket)


# ── PATCH /meetings/{meeting_id}/mom ─────────────────────────────────────────
#
# This is the learning loop's capture point.
# Complete implementation per PLAN_WEB_SAAS.md Phase 3, Step 3.2.
#
# ─────────────────────────────────────────────────────────────────────────────

@router.patch(
    "/{meeting_id}/mom",
    response_model=MomPatchResponse,
    summary="Edit a MOM — captures field-level corrections for the RAG learning loop",
)
async def update_mom(
    meeting_id: str,
    payload: MomUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MomPatchResponse:
    """
    Apply a user edit to an AI-generated MOM.

    Every changed field is diffed and logged to mom_edit_history BEFORE the
    update is applied. This audit log is the raw material for correction
    embeddings (embed_meeting_task, Phase 7), which feed back into the RAG
    retrieval for future meetings (rag_service.py, Phase 5).

    CRITICAL: This is the ONLY place in the system where corrections are
    captured. Any future bulk-edit path that writes directly to mom_records
    will silently stop the learning loop.
    """
    # ── 1. Verify meeting ownership ────────────────────────────────────
    meeting_result = await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.org_id == current_user.org_id,
        )
    )
    meeting = meeting_result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    # ── 2. Load the existing MOM record ───────────────────────────────
    mom_result = await db.execute(
        select(MomRecord).where(MomRecord.meeting_id == meeting.id)
    )
    mom = mom_result.scalar_one_or_none()
    if mom is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MOM not yet generated for this meeting",
        )

    # ── 3. Compute field-level diffs ──────────────────────────────────
    diffs = _diff_mom_fields(old=mom, new=payload)

    # ── 4. Write one MomEditHistory row per changed field ─────────────
    edit_history_ids: list[str] = []
    for field_path, old_value, new_value in diffs:
        history_row = MomEditHistory(
            mom_record_id=mom.id,
            org_id=current_user.org_id,
            field_path=field_path,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            edited_by=current_user.id,
        )
        db.add(history_row)
        await db.flush()  # populate history_row.id before appending
        edit_history_ids.append(str(history_row.id))

    # ── 5. Apply the update to the MOM record ─────────────────────────
    mom.summary       = payload.summary
    mom.key_decisions = [kd.model_dump() for kd in payload.key_decisions]
    mom.action_items  = [ai.model_dump() for ai in payload.action_items]
    mom.risks         = list(payload.risks)
    mom.next_steps    = payload.next_steps
    mom.version       += 1
    mom.last_edited_by = current_user.id
    mom.last_edited_at = datetime.now(timezone.utc)

    await db.commit()

    logger.info(
        "PATCH mom meeting=%s user=%s corrections=%d new_version=%d",
        meeting_id, current_user.id, len(diffs), mom.version,
    )

    # ── 6. Enqueue correction embedding (fire-and-forget) ─────────────
    # Must NOT block the API response — embedding generation takes 1–3s.
    # The Celery task runs in a separate process; it reads the history rows
    # we just committed from Postgres.
    if diffs:
        embed_meeting_task.delay(
            meeting_id=meeting_id,
            org_id=str(current_user.org_id),
            edit_history_ids=edit_history_ids,
        )
        logger.debug("Enqueued embed_meeting_task for %d correction(s)", len(diffs))

    return MomPatchResponse(
        status="updated",
        version=mom.version,
        corrections_captured=len(diffs),
    )


# ── Diff engine ───────────────────────────────────────────────────────────────

def _diff_mom_fields(
    old: MomRecord,
    new: MomUpdateRequest,
) -> list[tuple[str, str | None, str | None]]:
    """
    Compute a field-level diff between the stored MOM and the incoming payload.

    Returns a list of (field_path, old_value, new_value) tuples, one per
    changed field. An empty list means nothing changed.

    Diff strategy
    -------------
    Scalar fields (summary, next_steps):
        Simple equality check. None == None is not a diff.

    key_decisions (list of {id, text}):
        Matched by stable `id`. An item present in new but not in old is a
        NEW addition (not a correction — it was not AI-generated, so there is
        nothing to learn from). An item in old but missing from new is a
        deletion — also not a correction of an AI value (captured implicitly
        by the absence from the updated list). Only CHANGED text on an
        existing item is a correction.

    action_items (list of {id, task, assignee, deadline, priority}):
        Matched by stable `id`. Changed field values on an existing item are
        corrections. The `id` field itself is immutable and never compared.

    risks (list of str):
        Not diffed per-item because risk strings have no stable ID. A change
        to the risks list is captured as a single "risks" field diff if the
        serialised list differs.

    WHY stable IDs matter
    ----------------------
    Without stable IDs, an edit to "action_items[2].assignee" would be
    indistinguishable from a wholesale list replacement. The stable UUID
    assigned by gemini_service.py at generation time is what makes it possible
    to record exactly "the AI said Ankit, the user corrected it to Priya" —
    which becomes the correction sentence that feeds the RAG loop.
    """
    diffs: list[tuple[str, str | None, str | None]] = []

    # ── summary ───────────────────────────────────────────────────────
    if old.summary != new.summary:
        diffs.append(("summary", old.summary, new.summary))

    # ── next_steps ────────────────────────────────────────────────────
    if old.next_steps != new.next_steps:
        diffs.append(("next_steps", old.next_steps, new.next_steps))

    # ── risks (serialise to string for comparison) ─────────────────────
    old_risks_str = str(old.risks)
    new_risks_str = str(list(new.risks))
    if old_risks_str != new_risks_str:
        diffs.append(("risks", old_risks_str, new_risks_str))

    # ── key_decisions — matched by stable id ──────────────────────────
    old_decisions: dict[str, dict] = {
        d["id"]: d
        for d in (old.key_decisions or [])
        if isinstance(d, dict) and "id" in d
    }
    for new_decision in new.key_decisions:
        old_decision = old_decisions.get(new_decision.id)
        if old_decision is None:
            continue  # newly added decision, not an AI correction
        if old_decision.get("text") != new_decision.text:
            diffs.append(
                (
                    f"key_decisions[{new_decision.id}].text",
                    old_decision.get("text"),
                    new_decision.text,
                )
            )

    # ── action_items — matched by stable id, diffed per sub-field ─────
    old_items: dict[str, dict] = {
        item["id"]: item
        for item in (old.action_items or [])
        if isinstance(item, dict) and "id" in item
    }
    for new_item in new.action_items:
        old_item = old_items.get(new_item.id)
        if old_item is None:
            continue  # newly added item, not a correction of an AI value
        for field in ("task", "assignee", "deadline", "priority"):
            old_val = old_item.get(field)
            new_val = getattr(new_item, field, None)
            if old_val != new_val:
                diffs.append(
                    (
                        f"action_items[{new_item.id}].{field}",
                        old_val,
                        new_val,
                    )
                )

    return diffs
