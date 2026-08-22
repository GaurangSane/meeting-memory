"""
app/services/gemini_service.py

RAG-augmented MOM generation — Phase 6, Step 6.1.

Same strict-JSON-schema discipline as the desktop app's GeminiMOMClient,
with one critical addition: the rag_context_block is inserted between the
system instructions and the transcript, so past context and corrections are
visible to Gemini before it reads the new transcript.

Stable ID assignment
--------------------
Immediately after parsing Gemini's JSON output, we assign a stable UUID to
every action item and key decision. This UUID:
  - Is stored in mom_records.action_items / key_decisions (JSONB)
  - Echoed back unchanged by the frontend on PATCH /meetings/{id}/mom
  - Used by _diff_mom_fields() to match which specific item was edited
  - Persisted in mom_edit_history.field_path (e.g. "action_items[<uuid>].assignee")
  - Referenced in correction embedding metadata for attribution

Without stable IDs, a user edit to "item 3's assignee" is indistinguishable
from a wholesale list replacement, and the learning loop cannot produce a
precise correction sentence.

JSON reliability
----------------
response_mime_type="application/json" instructs Gemini to output valid JSON
only. Combined with temperature=0.1 (near-deterministic), this makes the
output extremely consistent. The json.loads() call will raise on malformed
output — this is intentional; Celery will retry the task.

Async via executor
------------------
The Gemini SDK's generate_content() is synchronous. We run it in a thread
pool executor (same pattern as embedding_service.py) to avoid blocking the
asyncio event loop. This is especially important when generate_mom() is called
from rag_service.py during the Celery task, which uses asyncio.run() — the
executor approach is the correct way to call sync I/O from async code.
"""

import asyncio
import json
import logging
import uuid
from functools import partial

import google.generativeai as genai
from google.generativeai.types import GenerationConfig

from app.core.config import settings

logger = logging.getLogger(__name__)

genai.configure(api_key=settings.GEMINI_API_KEY)

_SYSTEM_PROMPT = (
    "You are an expert corporate secretary for Indian business meetings. "
    "Respond ONLY with valid JSON matching the given schema. "
    'Apply any "Known corrections" provided as binding instructions, not suggestions. '
    "Do not invent calendar dates; preserve relative deadlines when the transcript "
    "does not provide an exact date. "
    "Do not include any text outside the JSON object."
)

_JSON_SCHEMA_EXAMPLE = """{
  "meeting_title": "string",
  "summary": "string",
  "key_decisions": ["string or {id, text}"],
  "action_items": [{"task": "string", "assignee": "string", "deadline": "string", "priority": "High|Medium|Low"}],
  "risks": ["string"],
  "next_steps": "string"
}"""


def _build_prompt(
    meeting_context: str,
    transcript: str,
    rag_context_block: str,
) -> str:
    """
    Assemble the full Gemini prompt.

    Structure:
      MEETING CONTEXT  ← anchor field from the UI
      [RAG BLOCK]      ← injected by rag_service.py (empty on first meeting)
      FULL TRANSCRIPT  ← the assembled raw transcript
      SCHEMA           ← strict JSON extraction instructions

    The rag_context_block is placed before the transcript so Gemini reads
    the "Known corrections" imperatives before encountering the new content
    — ordering matters for instruction following.
    """
    rag_section = f"\n{rag_context_block}\n" if rag_context_block else ""
    return f"""MEETING CONTEXT: {meeting_context}
{rag_section}
FULL TRANSCRIPT:
---
{transcript}
---

Extract the Minutes of Meeting as JSON matching exactly this schema:
{_JSON_SCHEMA_EXAMPLE}

Deadline rule: if the transcript says a relative deadline such as "Thursday"
or "next week" and no exact calendar date is provided, keep that exact relative
phrase in action_items[].deadline. Do not guess a YYYY-MM-DD date.
"""


async def generate_mom(
    meeting_context: str,
    transcript: str,
    rag_context_block: str,
) -> dict:
    """
    Generate a structured MOM from a meeting transcript using Gemini.

    After generation, assigns stable UUIDs to every action item and key
    decision so the diff engine in PATCH /meetings/{id}/mom can track
    field-level edits precisely.

    Args:
        meeting_context:   Short description of the meeting (e.g. "Q3 Planning").
        transcript:        Full raw transcript assembled from STT chunks.
        rag_context_block: Pre-rendered prompt block from rag_service.py.
                           Pass empty string for the first meeting.

    Returns:
        Parsed MOM dict with stable IDs injected. Keys:
          meeting_title, summary, key_decisions (list[{id, text}]),
          action_items (list[{id, task, assignee, deadline, priority}]),
          risks (list[str]), next_steps.

    Raises:
        json.JSONDecodeError: If Gemini returns invalid JSON (rare with
            response_mime_type="application/json" but possible on error).
        Exception:            Any Gemini SDK or network error propagates
            to the caller (Celery task) for retry.
    """
    model = genai.GenerativeModel(
        model_name=settings.GEMINI_MODEL,
        system_instruction=_SYSTEM_PROMPT,
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    prompt = _build_prompt(meeting_context, transcript, rag_context_block)
    logger.info(
        "Generating MOM — prompt_len=%d rag_block_len=%d",
        len(prompt), len(rag_context_block),
    )

    # Run sync SDK call in thread pool to avoid blocking the event loop
    loop = asyncio.get_running_loop()
    sync_call = partial(model.generate_content, prompt)
    response = await loop.run_in_executor(None, sync_call)

    mom: dict = json.loads(response.text)
    logger.info("Gemini MOM generated — keys: %s", list(mom.keys()))

    # ── Assign stable IDs ──────────────────────────────────────────────────
    # action_items: Gemini returns list of dicts without id — inject one
    for item in mom.get("action_items", []):
        if not isinstance(item, dict):
            continue
        item.setdefault("id", str(uuid.uuid4()))

    # key_decisions: Gemini may return list of strings or list of dicts
    for i, decision in enumerate(mom.get("key_decisions", [])):
        if isinstance(decision, str):
            mom["key_decisions"][i] = {
                "id": str(uuid.uuid4()),
                "text": decision,
            }
        elif isinstance(decision, dict) and "id" not in decision:
            decision["id"] = str(uuid.uuid4())

    logger.debug(
        "Stable IDs assigned: %d action_items, %d key_decisions",
        len(mom.get("action_items", [])),
        len(mom.get("key_decisions", [])),
    )
    return mom
