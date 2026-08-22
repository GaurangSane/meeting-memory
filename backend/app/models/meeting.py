"""
app/models/meeting.py

ORM model for the `meetings` table.
Status lifecycle: 'recording' → 'processing' → 'completed' | 'failed'

RLS policy `tenant_isolation_meetings` is enabled on this table in the
migration — the engine-level policy provides defence-in-depth alongside the
application-layer org_id filter in every query.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_session import Base


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        Index("idx_meetings_org", "org_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    meeting_context: Mapped[str] = mapped_column(Text, nullable=False)
    #   The "anchor" field from the UI (e.g. "Q3 Sprint Planning")

    raw_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    #   Assembled by WSConnectionManager from Redis after recording stops

    status: Mapped[str] = mapped_column(Text, nullable=False, default="recording")
    #   'recording' | 'processing' | 'completed' | 'failed'

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(  # noqa: F821
        "Organization", back_populates="meetings"
    )
    creator: Mapped["User"] = relationship(              # noqa: F821
        "User", back_populates="meetings_created", foreign_keys=[created_by]
    )
    mom_record: Mapped["MomRecord | None"] = relationship(  # noqa: F821
        "MomRecord", back_populates="meeting", uselist=False,
        cascade="all, delete-orphan"
    )
    embeddings: Mapped[list["MeetingEmbedding"]] = relationship(  # noqa: F821
        "MeetingEmbedding", back_populates="meeting", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Meeting id={self.id} status={self.status!r} context={self.meeting_context!r}>"
