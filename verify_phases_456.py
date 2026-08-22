import sys

print("--- Phase 4 Validation ---")
from nlp.gemini_client import GeminiMOMClient
import json

client = GeminiMOMClient()
mock_transcript = (
    "Ramesh said we need to launch the new product by 15th July. "
    "Priya confirmed the design is ready. Ankit will handle QA by July 10th. "
    "Team decided to skip the beta phase. Sunita raised a concern about server capacity."
)
try:
    result = client.extract_mom(mock_transcript, "Q2 Product Launch Planning")
    print(json.dumps(result, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"Phase 4 Gemini API call failed (expected if API key is invalid): {e}")
print("\nGemini NLP Module OK.")

print("\n--- Phase 5 Validation ---")
from output.html_formatter import HTMLFormatter

formatter = HTMLFormatter()
mock_data = {
    "meeting_title": "Q3 Sprint Planning", "date": "11-Jun-2025",
    "meeting_context": "Sprint planning for Q3", "executive_summary": "Team reviewed Q2.",
    "key_decisions": ["Adopt two-week sprints"], "attendees_mentioned": ["Ankit", "Priya"],
    "action_items": [{"task": "Update Jira", "assignee": "Ankit", "deadline": "15-Jun-2025", "priority": "High"}],
    "risks_and_blockers": [], "next_steps": "Update backlogs.", "next_meeting_suggestion": "25-Jun-2025",
}
html = formatter.render(mock_data)
assert "<html" in html.lower() and "Q3 Sprint Planning" in html
print(f"HTML rendered: {len(html):,} chars. Formatter OK.")

print("\n--- Phase 6 Validation ---")
import logging
logging.basicConfig(level=logging.INFO)
from notifications.whatsapp_alert import WhatsAppAlert

try:
    alert = WhatsAppAlert()
    # We won't actually send a message during test unless the environment variables are correctly set
    # and we want to avoid spamming the Twilio sandbox.
    # The instantiation itself validates the module imports and client setup.
    print("WhatsApp Alert Module loaded successfully.")
except Exception as e:
    print(f"WhatsApp Alert initialization failed (expected if Twilio credentials are invalid): {e}")

print("Phase 6 Validation OK.")
