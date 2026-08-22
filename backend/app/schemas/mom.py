"""
app/schemas/mom.py

Pydantic request/response models for MOM endpoints (Phase 3).

MomUpdateRequest is the body for PATCH /meetings/{id}/mom — the learning
loop's capture point. All list items must carry a stable `id` (UUID assigned
server-side by gemini_service.py at generation time) so _diff_mom_fields()
can match edits precisely rather than treating the whole list as replaced.

MomResponse is the read schema for GET /meetings/{id} — embedded inside
MeetingDetailResponse.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel


# ── Sub-models ────────────────────────────────────────────────────────────────

class ActionItem(BaseModel):
    """
    One action item in a MOM.

    `id` is the stable UUID assigned by gemini_service.py at generation time.
    It MUST be preserved by the frontend and echoed back unchanged on PATCH
    so that _diff_mom_fields() can match edits to the right original field.
    """
    id: str
    task: str
    assignee: str
    deadline: str
    priority: str  # "High" | "Medium" | "Low"


class KeyDecision(BaseModel):
    """
    One key decision in a MOM.
    Same stable-ID contract as ActionItem.
    """
    id: str
    text: str


# ── Request model ─────────────────────────────────────────────────────────────

class MomUpdateRequest(BaseModel):
    """
    Body for PATCH /meetings/{id}/mom.

    All fields are optional at the schema level — PATCH semantics.
    However, the diff engine in meetings.py compares every field against the
    stored record, so an omitted field that defaults to [] or None will
    generate a diff if the original had a value. The frontend should always
    send the full current MOM state, not just the changed fields.
    """
    summary: str | None = None
    key_decisions: list[KeyDecision] = []
    action_items: list[ActionItem] = []
    risks: list[str] = []
    next_steps: str | None = None


# ── Response model ────────────────────────────────────────────────────────────

class MomResponse(BaseModel):
    """
    Read schema for a MomRecord.
    Used in GET /meetings/{id} (nested inside MeetingDetailResponse).
    """
    id: uuid.UUID
    meeting_id: uuid.UUID
    summary: str | None
    key_decisions: list       # list[{id, text}] — raw JSONB
    action_items: list        # list[{id, task, assignee, deadline, priority}]
    risks: list               # list[str]
    next_steps: str | None
    version: int
    last_edited_by: uuid.UUID | None
    last_edited_at: datetime | None

    model_config = {"from_attributes": True}  # Pydantic v2 ORM mode
