"""
nlp/gemini_client.py

Sends the full transcript + meeting context to Google Gemini.
Forces structured JSON output using response_mime_type='application/json'.

Output JSON schema:
{
    "meeting_title": str,
    "date": str (DD-MMM-YYYY),
    "meeting_context": str,
    "executive_summary": str (3-5 sentences),
    "key_decisions": [str, ...],
    "action_items": [
        {
            "task": str,
            "assignee": str,
            "deadline": str (DD-MMM-YYYY or "TBD"),
            "priority": "High" | "Medium" | "Low"
        },
        ...
    ],
    "attendees_mentioned": [str, ...],
    "risks_and_blockers": [str, ...],
    "next_steps": str,
    "next_meeting_suggestion": str
}
"""

import json
import logging
from datetime import date
import google.generativeai as genai
from google.generativeai.types import GenerationConfig

from config.settings import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """
You are an expert corporate secretary specialising in Indian business meetings.
Your task is to analyse a meeting transcript and extract structured information.

RULES:
1. Respond ONLY with a valid JSON object. No markdown, no preamble, no explanation.
2. Infer assignees from the transcript; use "Team" if unspecified.
3. Deadlines must be extracted from conversational context (e.g., "by next Friday",
   "end of month") and converted to DD-MMM-YYYY format. Use "TBD" if not mentioned.
4. Executive summary must be professional, concise, and written in formal English.
5. Priorities: "High" = mentioned as urgent/critical, "Medium" = standard,
   "Low" = nice-to-have or mentioned briefly.
6. The output must match the exact JSON schema provided.
7. Meeting context provided by the user must anchor interpretation of all items.
8. Capture risks, blockers, and dependencies as a separate list.
"""

_USER_PROMPT_TEMPLATE = """
MEETING DATE: {meeting_date}
MEETING CONTEXT / AGENDA: {meeting_context}

FULL TRANSCRIPT:
---
{transcript}
---

Extract the Minutes of Meeting as a JSON object with this EXACT schema:
{{
    "meeting_title": "<inferred from context and transcript>",
    "date": "<{meeting_date}>",
    "meeting_context": "<{meeting_context}>",
    "executive_summary": "<3-5 sentence professional summary>",
    "key_decisions": ["<decision 1>", "<decision 2>"],
    "action_items": [
        {{
            "task": "<specific task description>",
            "assignee": "<name or Team>",
            "deadline": "<DD-MMM-YYYY or TBD>",
            "priority": "<High|Medium|Low>"
        }}
    ],
    "attendees_mentioned": ["<name1>", "<name2>"],
    "risks_and_blockers": ["<risk/blocker 1>"],
    "next_steps": "<brief paragraph>",
    "next_meeting_suggestion": "<suggestion or TBD>"
}}
"""


class GeminiMOMClient:
    """Extracts structured MOM data from transcript using Gemini."""

    def __init__(self):
        genai.configure(api_key=GEMINI_API_KEY)
        self._model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=_SYSTEM_PROMPT,
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
                top_p=0.9,
                max_output_tokens=4096,
            ),
        )
        logger.info(f"[Gemini] Initialised with model: {GEMINI_MODEL}")

    def extract_mom(self, transcript: str, meeting_context: str) -> dict:
        """
        Send transcript to Gemini and return structured MOM dict.

        Args:
            transcript: Full assembled transcript from all STT chunks.
            meeting_context: User-supplied context/agenda string from UI.

        Returns:
            Validated Python dict matching the MOM JSON schema.

        Raises:
            ValueError: If Gemini returns invalid/empty JSON.
            RuntimeError: If the Gemini API call fails.
        """
        if not transcript.strip():
            logger.warning("[Gemini] Empty transcript provided.")
            transcript = "[No transcript available — audio may not have been recorded]"

        today = date.today().strftime("%d-%b-%Y")
        prompt = _USER_PROMPT_TEMPLATE.format(
            meeting_date=today,
            meeting_context=meeting_context or "General team meeting",
            transcript=transcript,
        )

        logger.info("[Gemini] Sending transcript for MOM extraction...")

        try:
            response = self._model.generate_content(prompt)
        except Exception as e:
            raise RuntimeError(f"[Gemini] API call failed: {e}") from e

        raw_text = response.text.strip()
        logger.debug(f"[Gemini] Raw response ({len(raw_text)} chars): {raw_text[:200]}...")

        # Defensive strip in case Gemini wraps in ```json despite instructions
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        try:
            mom_data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"[Gemini] Could not parse JSON response: {e}\n"
                f"Raw response: {raw_text[:500]}"
            ) from e

        # Basic schema validation with graceful defaults
        required_keys = [
            "meeting_title", "date", "executive_summary",
            "key_decisions", "action_items",
        ]
        for key in required_keys:
            if key not in mom_data:
                logger.warning(f"[Gemini] Missing key in response: '{key}'. Inserting default.")
                mom_data[key] = [] if key in ("key_decisions", "action_items") else "N/A"

        logger.info(
            f"[Gemini] MOM extracted: "
            f"{len(mom_data.get('action_items', []))} action items, "
            f"{len(mom_data.get('key_decisions', []))} decisions."
        )
        return mom_data
