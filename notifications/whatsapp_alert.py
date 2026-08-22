"""
notifications/whatsapp_alert.py

Sends a WhatsApp message via Twilio's Messaging API.

Prerequisites:
  1. Twilio account with WhatsApp Sandbox enabled.
  2. Each recipient must have joined the sandbox by sending
     the join code to the Twilio sandbox number.
  3. For production: use a Twilio-approved WhatsApp Business sender.
"""

import logging
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

from config.settings import (
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
    TWILIO_WHATSAPP_FROM, WHATSAPP_RECIPIENTS,
)

logger = logging.getLogger(__name__)

_PRIORITY_EMOJI = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}


class WhatsAppAlert:
    """Dispatches WhatsApp MOM summary via Twilio."""

    def __init__(self):
        self._client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        logger.info("[WhatsApp] Twilio client initialised.")

    def send_alert(self, mom_data: dict) -> list[bool]:
        message_body = self._format_message(mom_data)
        return [self._send_single(r, message_body) for r in WHATSAPP_RECIPIENTS]

    def _send_single(self, to: str, body: str) -> bool:
        try:
            msg = self._client.messages.create(
                from_=TWILIO_WHATSAPP_FROM, to=to, body=body
            )
            logger.info(f"[WhatsApp] Sent to {to} — SID: {msg.sid}")
            return True
        except TwilioRestException as e:
            logger.error(f"[WhatsApp] Failed to send to {to}: {e}")
            return False

    def _format_message(self, mom_data: dict) -> str:
        title = mom_data.get("meeting_title", "Meeting")
        lines = [f"*MOM: {title}* 📋"]

        decisions = mom_data.get("key_decisions", [])
        if decisions:
            lines.append("\n*Key Decisions:*")
            for d in decisions[:3]:  # WhatsApp brevity: top 3
                lines.append(f"• {d}")

        actions = mom_data.get("action_items", [])
        if actions:
            lines.append("\n*Action Items:*")
            for a in actions:
                prio = _PRIORITY_EMOJI.get(a.get("priority", "Low"), "🟢")
                lines.append(f"{prio} {a.get('task')} → *{a.get('assignee')}*")

        lines.append("\n_Check email for full HTML details._")
        return "\n".join(lines)
