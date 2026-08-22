"""
app/services/rag_service.py

THE core of "Intelligent Meeting Memory" — Phase 5, Step 5.4.

Called once per meeting, just before Gemini generation, by generate_mom_task.
Returns historical context (narrative) and correction imperatives (behavioural)
as separate prompt blocks.

Two retrieval passes — deliberately separate
--------------------------------------------
Pass 1 — General history (top-5):
  content_types = ['summary', 'decision', 'action_item']
  Purpose: give Gemini situational continuity about this team's past work.
  Injected under "## Relevant context from this team's past meetings:"

Pass 2 — Corrections (top-3):
  content_types = ['correction']
  Purpose: surface past human corrections as binding imperatives.
  Injected under "## Known corrections — apply these patterns, do not repeat past mistakes:"

Why separate passes?
  Corrections are imperative instructions ("stop doing X"), not narrative
  context. If diluted inside the general history block, Gemini treats them
  as weak suggestions. A dedicated, clearly-labelled section makes them
  syntactically equivalent to hard rules in the prompt, which measurably
  improves compliance (empirical observation from the desktop prototype).

Why plain text, not JSON?
  Gemini follows prose instructions about applying corrections more reliably
  than it follows correction objects structured as JSON. The plain-text
  format was deliberately chosen after testing both approaches.

Zero-result handling
--------------------
  Both query calls return empty lists if the org has no prior meetings
  (i.e. this is the first meeting ever). In that case build_rag_prompt_block()
  returns an empty string, and the Gemini prompt simply omits the RAG section —
  generation still succeeds with no fallback logic needed. This is validated
  by Phase 12's test case: "Run generate_mom_task for an org with zero prior
  meetings → RAG retrieval returns empty gracefully."
"""

import logging

from app.services.embedding_service import embed_text
from app.services.vector_store_pgvector import PgVectorStore

logger = logging.getLogger(__name__)

# Module-level singleton — one instance shared across all requests/tasks.
# PgVectorStore is stateless (opens a new session per call), so this is safe.
vector_store = PgVectorStore()

# Retrieval configuration — these match the plan's specified values.
_HISTORY_TOP_K = 5
_CORRECTION_TOP_K = 3
_TRANSCRIPT_EXCERPT_CHARS = 2000  # keep query embedding focused on the opening


async def retrieve_historical_context(
    org_id: str,
    meeting_context: str,
    transcript_excerpt: str,
) -> dict:
    """
    Run two parallel-style vector searches and return the combined results.

    The query embedding is constructed from the meeting_context (e.g.
    "Q3 Sprint Planning — Infrastructure") concatenated with the first
    2000 characters of the transcript. This gives the embedding model enough
    signal to retrieve semantically related past meetings without being
    diluted by the full transcript length.

    Args:
        org_id:             Tenant UUID string.
        meeting_context:    The 'anchor' field the user entered before the meeting.
        transcript_excerpt: The raw transcript from this meeting (will be truncated
                            to TRANSCRIPT_EXCERPT_CHARS before embedding).

    Returns:
        dict with keys:
          "history":     list[dict] — past summaries/decisions/action_items
          "corrections": list[dict] — past human corrections
        Both lists may be empty (first meeting, or no semantically similar past).
    """
    query_text = f"{meeting_context}\n{transcript_excerpt[:_TRANSCRIPT_EXCERPT_CHARS]}"
    logger.info(
        "RAG retrieval org=%s query_len=%d", org_id, len(query_text)
    )

    query_embedding = await embed_text(query_text)

    history = await vector_store.query(
        org_id=org_id,
        embedding=query_embedding,
        top_k=_HISTORY_TOP_K,
        content_types=["summary", "decision", "action_item"],
    )
    corrections = await vector_store.query(
        org_id=org_id,
        embedding=query_embedding,
        top_k=_CORRECTION_TOP_K,
        content_types=["correction"],
    )

    logger.info(
        "RAG retrieved %d history rows + %d correction rows for org=%s",
        len(history), len(corrections), org_id,
    )
    return {"history": history, "corrections": corrections}


def build_rag_prompt_block(retrieved: dict) -> str:
    """
    Render retrieved rows into the plain-text prompt block for Gemini.

    The output is inserted between the system instructions and the transcript
    in gemini_service.py's _build_prompt(). If both lists are empty, returns
    an empty string so the prompt contains no RAG section at all (cleaner
    than an empty ## header).

    Format:
        ## Relevant context from this team's past meetings:
        - [summary] The Q2 planning meeting agreed to prioritise the API gateway.
        - [decision] Decision: Use FastAPI for the backend service.
        - [action_item] Complete onboarding docs → Rahul by 2024-03-15

        ## Known corrections — apply these patterns, do not repeat past mistakes:
        - In a past meeting, the AI assigned the task 'Update staging config'
          to Ankit, but the correct assignee was Priya.

    Args:
        retrieved: Dict with 'history' and 'corrections' lists from
                   retrieve_historical_context().

    Returns:
        Multi-line string ready for f-string injection into the prompt.
        Empty string if both lists are empty.
    """
    lines: list[str] = []

    if retrieved.get("history"):
        lines.append("## Relevant context from this team's past meetings:")
        for row in retrieved["history"]:
            lines.append(f"- [{row['content_type']}] {row['source_text']}")

    if retrieved.get("corrections"):
        lines.append(
            "\n## Known corrections — apply these patterns, do not repeat past mistakes:"
        )
        for row in retrieved["corrections"]:
            lines.append(f"- {row['source_text']}")

    return "\n".join(lines) if lines else ""
