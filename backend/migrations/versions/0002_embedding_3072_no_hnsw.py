"""
Use Gemini embedding-001's 3072-dimensional vectors without HNSW.

pgvector's HNSW index has a 2000-dimension ceiling, so the old vector index
must not exist with a VECTOR(3072) column.
"""

from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_embeddings_vector;")
    op.execute("""
        ALTER TABLE meeting_embeddings
        ALTER COLUMN embedding TYPE VECTOR(3072)
        USING embedding::VECTOR(3072);
    """)


def downgrade() -> None:
    # Do not recreate the retired 768-dimensional/HNSW setup.
    pass
