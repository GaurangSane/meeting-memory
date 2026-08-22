"""
app/models/embedding.py

ORM model for the `meeting_embeddings` table — the vector memory store.

IMPORTANT TENANT ISOLATION NOTE (from the plan):
  - RLS policy `tenant_isolation_embeddings` is enabled on this table.
  - Every query in vector_store_pgvector.py MUST include an explicit
    `WHERE org_id = :org_id` filter in addition to relying on RLS.
  - Defense in depth: the application layer must never silently depend
    on the database layer to catch a tenant-isolation bug.

Only a B-tree org index is created in the migration. pgvector HNSW cannot
index 3072-dimensional vectors.
The content_type column drives the two separate retrieval passes in
rag_service.py:
  Pass 1 — content_types=['summary', 'decision', 'action_item'] → history
  Pass 2 — content_types=['correction']                          → imperatives
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_session import Base

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    # Allow model import to succeed even before the pgvector package is
    # installed (e.g. during Alembic migration generation on a dev machine
    # without the full Docker environment). The actual column requires
    # pgvector to be installed at runtime.
    from sqlalchemy import LargeBinary as Vector  # type: ignore[assignment]


class MeetingEmbedding(Base):
    """
    Stores one embedding vector per content unit (summary, decision,
    action item, or correction sentence).

    The `metadata` JSONB field carries optional context:
      - For action_item rows: {"item_id": "<uuid>"}
      - For correction rows:  {"field_path": "action_items[<id>].assignee"}
      - For others:           {}
    """

    __tablename__ = "meeting_embeddings"
    __table_args__ = (
        Index("idx_embeddings_org", "org_id"),
        # No HNSW index: pgvector's HNSW dimension ceiling is below 3072.
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    #   'summary' | 'decision' | 'action_item' | 'correction'

    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    #   The human-readable text that was embedded (used in RAG prompt assembly)

    embedding: Mapped[list[float]] = mapped_column(Vector(3072), nullable=False)
    #   3072-dim vector from Gemini embedding model

    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(  # noqa: F821
        "Organization", back_populates="embeddings"
    )
    meeting: Mapped["Meeting | None"] = relationship(     # noqa: F821
        "Meeting", back_populates="embeddings"
    )

    def __repr__(self) -> str:
        return (
            f"<MeetingEmbedding id={self.id} type={self.content_type!r} "
            f"org={self.org_id}>"
        )
