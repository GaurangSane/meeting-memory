"""
app/models/mom.py

ORM models for `mom_records` and `mom_edit_history`.

mom_records: The structured MOM output from Gemini. All list-type fields
(key_decisions, action_items, risks) use JSONB so the frontend can receive
them as native JSON without deserialization steps.

mom_edit_history: Every field-level user edit captured by PATCH /meetings/{id}/mom.
This is the raw material for the correction embeddings that close the RAG
learning loop (Phase 5 of the plan). Bypassing the PATCH endpoint and writing
directly to mom_records will silently break the learning loop.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_session import Base


class MomRecord(Base):
    __tablename__ = "mom_records"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_decisions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #   Each decision: {"id": "<uuid>", "text": "..."}
    action_items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #   Each item: {"id": "<uuid>", "task": "...", "assignee": "...", "deadline": "...", "priority": "..."}
    risks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    next_steps: Mapped[str | None] = mapped_column(Text, nullable=True)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_edited_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    last_edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──────────────────────────────────────────────────
    meeting: Mapped["Meeting"] = relationship(       # noqa: F821
        "Meeting", back_populates="mom_record"
    )
    edit_history: Mapped[list["MomEditHistory"]] = relationship(
        "MomEditHistory", back_populates="mom_record", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<MomRecord id={self.id} meeting={self.meeting_id} version={self.version}>"


class MomEditHistory(Base):
    """
    Immutable audit log of every field-level MOM edit.

    field_path uses dot/bracket notation matching `_diff_mom_fields()`:
        "summary"
        "action_items[<item-uuid>].assignee"
        "action_items[<item-uuid>].deadline"

    org_id is denormalised here so correction embeddings can be queried
    per-org without joining back through mom_records → meetings.
    """

    __tablename__ = "mom_edit_history"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    mom_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mom_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    field_path: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────
    mom_record: Mapped["MomRecord"] = relationship(
        "MomRecord", back_populates="edit_history"
    )

    def __repr__(self) -> str:
        return (
            f"<MomEditHistory id={self.id} field={self.field_path!r} "
            f"'{self.old_value}' → '{self.new_value}'>"
        )
