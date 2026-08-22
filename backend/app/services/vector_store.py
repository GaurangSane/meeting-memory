"""
app/services/vector_store.py — Abstract VectorStore interface (Phase 5, Step 5.1).

Verbatim from PLAN_WEB_SAAS.md.
The pgvector implementation is the only concrete class today. Switching to
Qdrant or Pinecone means writing one new class and changing one line in the
DI wiring — no other module references pgvector directly.
"""

from abc import ABC, abstractmethod


class VectorStore(ABC):
    @abstractmethod
    async def upsert(
        self,
        org_id: str,
        meeting_id: str,
        content_type: str,
        source_text: str,
        embedding: list[float],
        metadata: dict,
    ) -> None: ...

    @abstractmethod
    async def query(
        self,
        org_id: str,
        embedding: list[float],
        top_k: int,
        content_types: list[str] | None = None,
    ) -> list[dict]: ...
