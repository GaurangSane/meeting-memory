import logging
import json
import sys
logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

from nlp.gemini_client import GeminiMOMClient
from output.html_formatter import HTMLFormatter
from output.email_sender import EmailSender
from notifications.whatsapp_alert import WhatsAppAlert

TRANSCRIPT = """
Good morning. Today's agenda is the Q3 product launch timeline.
Priya confirmed the design mockups are ready. Ankit will own QA by July 10th.
Sunita will have staging deployment ready by July 12th.
We've decided to skip the beta phase. Final launch is July 15th.
AWS bill is at 80% of budget — Ramesh will escalate to finance by June 14th.
Marketing needs the press kit by July 8th. Divya, please own that.
Next meeting on June 25th to review QA plan.
"""

print("\n── Step 1: Gemini MOM extraction ──")
try:
    mom = GeminiMOMClient().extract_mom(TRANSCRIPT, "Q3 Product Launch Planning")
    print(json.dumps(mom, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"Gemini Exception (expected with dummy key): {e}")
    mom = {
        "meeting_title": "Mock Meeting",
        "date": "11-Jun-2025",
        "meeting_context": "Dry Run",
        "executive_summary": "Mock summary due to dummy Gemini key.",
        "key_decisions": ["Proceed with mock"],
        "action_items": [],
        "attendees_mentioned": ["Priya"],
        "risks_and_blockers": ["Dummy key"],
        "next_steps": "Get real key",
        "next_meeting_suggestion": "TBD"
    }

print("\n── Step 2: HTML rendering ──")
html = HTMLFormatter().render(mom)
import tempfile
import os
tmp_path = os.path.join(tempfile.gettempdir(), "mom_rev2_preview.html")
with open(tmp_path, "w", encoding='utf-8') as f: 
    f.write(html)
print(f"HTML: {len(html):,} chars → saved to {tmp_path}")

print("\n── Step 3: Email dispatch ──")
try:
    ok = EmailSender().send(mom, html)
    print(f"Email: {'sent' if ok else 'FAILED'}")
except Exception as e:
    print(f"Email Exception: {e}")

print("\n── Step 4: WhatsApp alert ──")
try:
    results = WhatsAppAlert().send_alert(mom)
    print(f"WhatsApp: {sum(results)}/{len(results)} sent")
except Exception as e:
    print(f"WhatsApp Exception (expected with dummy key): {e}")

print("\n✅ Full pipeline dry-run complete (Rev 2).")
