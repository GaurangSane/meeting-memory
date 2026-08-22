"""
app/models/organization.py

ORM model for the `organizations` table.
The root of the multi-tenancy hierarchy — every user, meeting, and embedding
belongs to exactly one organization.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_session import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────
    users: Mapped[list["User"]] = relationship(          # noqa: F821
        "User", back_populates="organization", cascade="all, delete-orphan"
    )
    meetings: Mapped[list["Meeting"]] = relationship(    # noqa: F821
        "Meeting", back_populates="organization", cascade="all, delete-orphan"
    )
    embeddings: Mapped[list["MeetingEmbedding"]] = relationship(  # noqa: F821
        "MeetingEmbedding", back_populates="organization", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id} name={self.name!r}>"
