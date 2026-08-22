"""
app/schemas/meeting.py

Pydantic request/response models for the meetings router (Phase 3).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.mom import MomResponse


class MeetingCreateRequest(BaseModel):
    """Body for POST /meetings."""
    title: str | None = None
    meeting_context: str


class MeetingResponse(BaseModel):
    """
    Flat meeting record — returned by POST /meetings and GET /meetings list.
    Does not include the nested MOM to keep list responses lightweight.
    """
    id: uuid.UUID
    org_id: uuid.UUID
    created_by: uuid.UUID
    title: str | None
    meeting_context: str
    status: str
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}  # Pydantic v2 ORM mode


class MeetingDetailResponse(MeetingResponse):
    """
    Extended meeting record — returned by GET /meetings/{id}.
    Includes the embedded MOM if one exists (status == 'completed').
    The `mom` field is None when status is 'recording' or 'processing'.
    """
    mom: MomResponse | None = None


class WsTicketResponse(BaseModel):
    """Response for POST /meetings/{id}/ws-ticket."""
    ticket: str


class MomPatchResponse(BaseModel):
    """
    Response for PATCH /meetings/{id}/mom.

    `corrections_captured` is the count of field-level diffs that were logged
    to mom_edit_history and enqueued for embedding. The frontend uses this to
    surface the "system is learning from this edit" toast (Phase 10 contract).

    A value of 0 means the payload matched the stored MOM exactly — no diffs,
    no learning signal, but the MOM is still saved (e.g. to persist a cosmetic
    whitespace change that shouldn't influence future generation).
    """
    status: str
    version: int
    corrections_captured: int
