"""
app/services/vector_store_pgvector.py

pgvector concrete implementation of the VectorStore interface — Phase 5, Step 5.2.

CRITICAL tenant-isolation rule (from PLAN_WEB_SAAS.md):
  Every query MUST include an explicit `WHERE org_id = :org_id` filter AND
  rely on the RLS policy from the Phase 1 migration as a second layer.
  Defense in depth: the application layer must NEVER depend solely on the
  database layer to catch a tenant-isolation bug. If both layers agree, the
  system is safe; if either is accidentally omitted, the other still blocks
  the cross-tenant read.

Vector search operator
----------------------
  `<=>` is the pgvector cosine distance operator.
  `1 - (embedding <=> :embedding)` converts distance → similarity score
  in range [0, 1] where 1 = identical vector.

  The ORDER BY uses `<=>` directly (not the derived similarity column).
  No HNSW index is created because pgvector's HNSW dimension ceiling is below
  Gemini embedding-001's 3072-dimensional output.

UPSERT vs INSERT
----------------
  The plan specifies INSERT. We intentionally do not use ON CONFLICT DO UPDATE
  to preserve the audit trail — each embedding is a distinct content unit.
  Duplicate embeddings for the same source_text are harmless (the ANN search
  returns whichever is most similar; retrieval quality is not degraded by
  having two identical vectors for the same correction sentence).

Session scope — WHY NullPool, NOT SessionLocal
-----------------------------------------------
  upsert() and query() are called exclusively from Celery worker processes,
  bridged via asyncio.run() in rag_service.py and tasks_embeddings.py.

  The FastAPI engine in db_session.py uses a persistent connection pool
  (pool_size=20, max_overflow=10). Each connection in that pool is a live
  asyncpg Protocol object whose I/O handles are registered with uvicorn's
  event loop (call it loop A). When Celery forks a worker, it copies the
  parent's memory — including the engine — into the child process. The child
  then calls asyncio.run(), which creates a brand-new loop B. Asking the
  copied pool for a connection inside loop B is illegal: asyncpg tries to
  schedule I/O on loop B using file descriptors that are registered only
  with loop A, raising:

      asyncpg.exceptions._base.InterfaceError:
          cannot perform operation: another operation is in progress

  Each upsert()/query() call creates a fresh NullPool engine inside the
  current event loop and disposes it before returning. There is no cross-loop
  engine or socket reuse.

  This engine is NOT used by FastAPI route handlers — they continue to use
  the pooled SessionLocal from db_session.py via the get_db dependency.
"""

import logging
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

# ── Celery-safe engine for vector store calls ─────────────────────────────────
# NullPool: no persistent connections; every session opens + closes a fresh
# TCP connection. Required because this module is called from Celery workers
# (via asyncio.run) which run in a different process than FastAPI and therefore
# on a different event loop. See module docstring for full explanation.
def _make_celery_session_factory():
    """
    Creates a brand-new engine + sessionmaker on every call.

    Why not a module-level singleton: this module is imported ONCE when the
    Celery worker process boots, but the worker handles MANY tasks over its
    lifetime, each calling asyncio.run() independently — and each asyncio.run()
    creates a fresh event loop. A single long-lived AsyncEngine object carries
    internal loop-bound state from whichever loop first used it; reusing it
    across a later, different asyncio.run() loop raises
    "Future attached to a different loop", even with NullPool eliminating
    connection pooling. The fix: create and dispose the engine fresh within
    the single loop lifetime of one call.
    """
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


class PgVectorStore(VectorStore):
    """
    pgvector implementation of VectorStore.

    To swap to Qdrant or Pinecone: write a new class that inherits VectorStore,
    implement upsert() and query(), then update the single instantiation in
    rag_service.py. No other module references PgVectorStore directly.
    """

    async def upsert(
        self,
        org_id: str,
        meeting_id: str | None,
        content_type: str,
        source_text: str,
        embedding: list[float],
        metadata: dict,
    ) -> None:
        """
        Insert one embedding row into meeting_embeddings.

        Args:
            org_id:        Tenant UUID (string form).
            meeting_id:    Meeting UUID the embedding belongs to (or None for
                           org-level embeddings — currently unused but reserved).
            content_type:  'summary' | 'decision' | 'action_item' | 'correction'
            source_text:   The human-readable text that was embedded.
                           Stored alongside the vector and returned in RAG results
                           for direct injection into the Gemini prompt.
            embedding:     3072-dimensional float list from Gemini embedding-001.
            metadata:      Arbitrary JSON dict. Conventions:
                             action_item rows: {"item_id": "<uuid>"}
                             correction rows:  {"field_path": "action_items[<id>].assignee"}
        """
        engine, session_factory = _make_celery_session_factory()
        try: 

            async with session_factory() as session:
                await session.execute(
                    text("""
                        INSERT INTO meeting_embeddings
                            (org_id, meeting_id, content_type, source_text, embedding, metadata)
                        VALUES
                            (:org_id, :meeting_id, :content_type, :source_text,
                            CAST(:embedding AS vector), CAST(:metadata AS jsonb))
                    """),
                    {
                        "org_id":       org_id,
                        "meeting_id":   meeting_id,
                        "content_type": content_type,
                        "source_text":  source_text,
                        "embedding":    str(embedding),   # pgvector accepts "[0.1,0.2,...]" string
                        "metadata":     json.dumps(metadata),
                    },
                )
                await session.commit()
                logger.debug(
                    "Upserted embedding org=%s type=%s text=%r...",
                    org_id, content_type, source_text[:60],
                )
        finally:
            await engine.dispose()
    async def query(
        self,
        org_id: str,
        embedding: list[float],
        top_k: int = 5,
        content_types: list[str] | None = None,
    ) -> list[dict]:
        """
        Retrieve the top-k most similar embeddings for the given org.

        Uses cosine distance search ordered by ascending cosine distance
        (i.e. most similar first).

        Args:
            org_id:        Tenant UUID — ALWAYS included in WHERE clause.
            embedding:     Query vector (3072 dims).
            top_k:         Maximum number of results to return.
            content_types: Optional filter list. If provided, only rows with
                           content_type IN (...) are considered.
                           None → all content types.

        Returns:
            List of dicts with keys: source_text, content_type, metadata, similarity.
        """
        engine, session_factory = _make_celery_session_factory()
        try:
            async with session_factory() as session:
                    type_filter = ""
                    params: dict = {
                        "org_id":    org_id,
                        "embedding": str(embedding),  # pgvector accepts "[...]" string literal
                        "top_k":     top_k,
                    }

                    if content_types:
                        type_filter = "AND content_type = ANY(:content_types)"
                        params["content_types"] = content_types

                    result = await session.execute(
                        text(f"""
                            SELECT
                                source_text,
                                content_type,
                                metadata,
                                1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                            FROM meeting_embeddings
                            WHERE org_id = :org_id {type_filter}
                            ORDER BY embedding <=> CAST(:embedding AS vector)
                            LIMIT :top_k
                        """),
                        params,
                    )
                    rows = [dict(row._mapping) for row in result]
                    logger.debug(
                        "Vector query org=%s types=%s top_k=%d → %d results",
                        org_id, content_types, top_k, len(rows),
                    )
                    return rows
        finally:
            await engine.dispose()
