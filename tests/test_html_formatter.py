"""Unit tests for HTMLFormatter."""

from output.html_formatter import HTMLFormatter

_MOCK = {
    "meeting_title": "Test Meeting", "date": "11-Jun-2025",
    "meeting_context": "Test context", "executive_summary": "Summary.",
    "key_decisions": ["Decision A"],
    "action_items": [
        {"task": "Do X", "assignee": "Alice", "deadline": "20-Jun-2025", "priority": "High"},
    ],
    "attendees_mentioned": ["Alice", "Bob"],
    "risks_and_blockers": ["Risk 1"],
    "next_steps": "Follow up.", "next_meeting_suggestion": "18-Jun-2025",
}


def test_renders_html():
    html = HTMLFormatter().render(_MOCK)
    assert "<html" in html.lower() and "Test Meeting" in html
    assert "Decision A" in html and "Alice" in html
    print(f"✅ HTML render OK ({len(html):,} chars)")


def test_handles_empty_action_items():
    data = dict(_MOCK); data["action_items"] = []
    html = HTMLFormatter().render(data)
    assert "<html" in html.lower()
    print("✅ Empty action items handled gracefully.")
