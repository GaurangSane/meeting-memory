"""
app/models/user.py

ORM model for the `users` table.
Each user belongs to exactly one organization and has a role of
'admin' or 'member'. Notification preferences (email + WhatsApp) are
stored here and updated via PATCH /api/v1/users/me.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="member")
    #   'admin' | 'member'

    # Notification preferences — populated via PATCH /users/me
    whatsapp_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    #   e.g. "whatsapp:+91XXXXXXXXXX"
    notify_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    #   defaults to `email` if null; allows a separate notification address

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(   # noqa: F821
        "Organization", back_populates="users"
    )
    meetings_created: Mapped[list["Meeting"]] = relationship(  # noqa: F821
        "Meeting", back_populates="creator", foreign_keys="Meeting.created_by"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role!r}>"
