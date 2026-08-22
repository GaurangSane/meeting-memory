"""
app/workers/tasks_notifications.py

Email + WhatsApp notification Celery tasks — Phase 7, Step 7.3.

Direct port of the desktop app's EmailSender + WhatsAppAlert into the
Celery task execution context. The notification logic itself (HTML
rendering, SMTP send, Twilio API call) is ported from the desktop modules;
only the entry point changes from a direct function call to a Celery task.

These tasks run in the 'notifications' queue, which can have higher
concurrency than 'mom_generation' because they are pure I/O-bound
(SMTP + Twilio HTTP calls), not Gemini rate-limit-sensitive.

Error handling:
  Both tasks retry up to 3 times with 30-second delays. This covers
  transient SMTP connection failures and Twilio 5xx errors without
  permanently failing the notification for a momentary outage.

Notification channels:
  - Email: sent to users with notify_email (or fallback to email)
  - WhatsApp: sent to users with whatsapp_number configured
  Both are filtered per-recipient — no channel is sent if no recipients
  are configured for it.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

from app.core.config import settings
from app.db import get_meeting_with_mom_sync, get_recipients_for_meeting_sync
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# ── Priority emoji map (ported from desktop whatsapp_alert.py) ────────────────
_PRIORITY_EMOJI = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}


# ─────────────────────────────────────────────────────────────────────────────
# HTML email renderer
# ─────────────────────────────────────────────────────────────────────────────

def _render_email_html(meeting, mom) -> str:
    """
    Render the MOM as a clean, professional HTML email.

    Ported from the desktop app's HTMLFormatter pattern. Uses f-strings
    rather than a Jinja2 template file to avoid a file-system dependency
    inside the Celery worker; the output is functionally identical.
    """
    title = meeting.title or "Meeting"

    # ── Summary ──────────────────────────────────────────────────────────────
    summary_html = f"<p>{mom.summary or 'No summary available.'}</p>"

    # ── Key decisions ─────────────────────────────────────────────────────────
    decisions = mom.key_decisions or []
    if decisions:
        items = ""
        for d in decisions:
            text = d["text"] if isinstance(d, dict) else str(d)
            items += f"<li>{text}</li>"
        decisions_html = f"<ul>{items}</ul>"
    else:
        decisions_html = "<p><em>No key decisions recorded.</em></p>"

    # ── Action items ──────────────────────────────────────────────────────────
    action_items = mom.action_items or []
    if action_items:
        priority_colors = {"High": "#ef4444", "Medium": "#f59e0b", "Low": "#10b981"}
        rows = ""
        for item in action_items:
            if not isinstance(item, dict):
                continue
            priority = item.get("priority", "Low")
            color = priority_colors.get(priority, "#6b7280")
            rows += f"""
            <tr>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">{item.get('task', '')}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">{item.get('assignee', '')}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">{item.get('deadline', '')}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">
                <span style="color:{color};font-weight:600;">{priority}</span>
              </td>
            </tr>"""
        actions_html = f"""
        <table style="width:100%;border-collapse:collapse;margin-top:8px;">
          <thead>
            <tr style="background:#f3f4f6;">
              <th style="padding:10px 12px;text-align:left;font-weight:600;border-bottom:2px solid #d1d5db;">Task</th>
              <th style="padding:10px 12px;text-align:left;font-weight:600;border-bottom:2px solid #d1d5db;">Assignee</th>
              <th style="padding:10px 12px;text-align:left;font-weight:600;border-bottom:2px solid #d1d5db;">Deadline</th>
              <th style="padding:10px 12px;text-align:left;font-weight:600;border-bottom:2px solid #d1d5db;">Priority</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>"""
    else:
        actions_html = "<p><em>No action items recorded.</em></p>"

    # ── Risks ─────────────────────────────────────────────────────────────────
    risks = mom.risks or []
    if risks:
        risk_items = "".join(f"<li>{r}</li>" for r in risks)
        risks_html = f"<ul>{risk_items}</ul>"
    else:
        risks_html = "<p><em>No risks identified.</em></p>"

    # ── Next steps ────────────────────────────────────────────────────────────
    next_steps_html = f"<p>{mom.next_steps or 'No next steps recorded.'}</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MOM: {title}</title>
</head>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f9fafb;color:#111827;">
  <div style="max-width:700px;margin:40px auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 6px -1px rgba(0,0,0,.07);">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#6366f1,#8b5cf6);padding:32px 40px;">
      <p style="margin:0;color:#c7d2fe;font-size:13px;letter-spacing:.08em;text-transform:uppercase;font-weight:600;">Minutes of Meeting</p>
      <h1 style="margin:8px 0 0;color:#ffffff;font-size:26px;font-weight:700;">{title}</h1>
    </div>

    <div style="padding:40px;">

      <!-- Summary -->
      <h2 style="margin:0 0 12px;color:#1f2937;font-size:18px;font-weight:700;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">📋 Summary</h2>
      {summary_html}

      <!-- Key Decisions -->
      <h2 style="margin:32px 0 12px;color:#1f2937;font-size:18px;font-weight:700;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">✅ Key Decisions</h2>
      {decisions_html}

      <!-- Action Items -->
      <h2 style="margin:32px 0 12px;color:#1f2937;font-size:18px;font-weight:700;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">⚡ Action Items</h2>
      {actions_html}

      <!-- Risks -->
      <h2 style="margin:32px 0 12px;color:#1f2937;font-size:18px;font-weight:700;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">⚠️ Risks</h2>
      {risks_html}

      <!-- Next Steps -->
      <h2 style="margin:32px 0 12px;color:#1f2937;font-size:18px;font-weight:700;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">🗓️ Next Steps</h2>
      {next_steps_html}

    </div>

    <!-- Footer -->
    <div style="background:#f3f4f6;padding:20px 40px;text-align:center;">
      <p style="margin:0;color:#6b7280;font-size:13px;">
        Generated by <strong>Meeting Memory</strong> &mdash; AI-powered MOM generation
      </p>
    </div>

  </div>
</body>
</html>"""


def _send_smtp_email(subject: str, html_body: str, recipients: list[str]) -> None:
    """
    Send an HTML email via SMTP/STARTTLS.

    Uses the configured SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD.
    Raises on any SMTP failure — the caller (Celery task) handles retries.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_USER
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(settings.SMTP_USER, recipients, msg.as_string())

    logger.info("SMTP email sent to %d recipients: %s", len(recipients), recipients)


# ─────────────────────────────────────────────────────────────────────────────
# WhatsApp message formatter (ported from desktop whatsapp_alert.py)
# ─────────────────────────────────────────────────────────────────────────────

def _format_whatsapp_message(meeting, mom) -> str:
    """
    Format a concise WhatsApp MOM summary message.

    Ported directly from the desktop app's WhatsAppAlert._format_message().
    Keeps brevity: top-3 decisions, all action items with priority emoji.
    """
    title = meeting.title or "Meeting"
    lines = [f"*MOM: {title}* 📋"]

    decisions = mom.key_decisions or []
    if decisions:
        lines.append("\n*Key Decisions:*")
        for d in decisions[:3]:  # WhatsApp brevity: top 3
            text = d["text"] if isinstance(d, dict) else str(d)
            lines.append(f"• {text}")

    action_items = mom.action_items or []
    if action_items:
        lines.append("\n*Action Items:*")
        for item in action_items:
            if not isinstance(item, dict):
                continue
            priority = item.get("priority", "Low")
            emoji = _PRIORITY_EMOJI.get(priority, "🟢")
            lines.append(f"{emoji} {item.get('task', '')} → *{item.get('assignee', '')}*")

    lines.append("\n_Check email for the full HTML report._")
    return "\n".join(lines)


def _send_whatsapp_single(client: TwilioClient, to: str, body: str) -> bool:
    """
    Send a single WhatsApp message. Returns True on success, False on failure.
    Ported from desktop WhatsAppAlert._send_single().
    """
    try:
        msg = client.messages.create(
            from_=settings.TWILIO_WHATSAPP_FROM,
            to=to,
            body=body,
        )
        logger.info("WhatsApp sent to %s — SID: %s", to, msg.sid)
        return True
    except TwilioRestException as e:
        logger.error("WhatsApp failed to send to %s: %s", to, e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Celery tasks
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(self, meeting_id: str) -> None:
    """
    Send the completed MOM as an HTML email to all org members who have
    a notify_email configured (falls back to their account email).

    Reads MOM from Postgres, renders HTML, sends via SMTP/STARTTLS.
    Retries up to 3 times on SMTP failures with 30-second delays.
    """
    try:
        meeting, mom = get_meeting_with_mom_sync(meeting_id)
        recipients = get_recipients_for_meeting_sync(meeting_id)

        email_recipients = [
            u.notify_email or u.email
            for u in recipients
            if u.notify_email or u.email
        ]
        if not email_recipients:
            logger.info(
                "send_email_task: no email recipients for meeting=%s", meeting_id
            )
            return

        logger.info(
            "send_email_task: sending MOM for meeting=%s to %d recipients",
            meeting_id, len(email_recipients),
        )

        html_body = _render_email_html(meeting, mom)
        subject = f"MOM: {meeting.title or 'Meeting'}"
        _send_smtp_email(subject, html_body, email_recipients)

        logger.info(
            "send_email_task: successfully sent meeting=%s", meeting_id
        )

    except Exception as exc:
        logger.error(
            "send_email_task failed for meeting=%s: %s", meeting_id, exc, exc_info=True
        )
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def send_whatsapp_task(self, meeting_id: str) -> None:
    """
    Send a WhatsApp MOM summary to all org members who have a
    whatsapp_number configured.

    Uses the Twilio WhatsApp API (sandbox or approved Business sender).
    Each recipient's number must be in E.164 format prefixed with
    'whatsapp:' (e.g. 'whatsapp:+91XXXXXXXXXX').

    Retries up to 3 times on failures with 30-second delays. Individual
    per-recipient send failures are logged but do not cause the task to
    retry — only a failure to reach Twilio at all triggers a retry.
    """
    try:
        meeting, mom = get_meeting_with_mom_sync(meeting_id)
        recipients = get_recipients_for_meeting_sync(meeting_id)

        wa_recipients = [
            u.whatsapp_number
            for u in recipients
            if u.whatsapp_number
        ]
        if not wa_recipients:
            logger.info(
                "send_whatsapp_task: no WhatsApp recipients for meeting=%s", meeting_id
            )
            return

        logger.info(
            "send_whatsapp_task: sending MOM for meeting=%s to %d recipients",
            meeting_id, len(wa_recipients),
        )

        client = TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        message_body = _format_whatsapp_message(meeting, mom)

        results = [
            _send_whatsapp_single(client, recipient, message_body)
            for recipient in wa_recipients
        ]
        success_count = sum(results)
        logger.info(
            "send_whatsapp_task: %d/%d messages sent for meeting=%s",
            success_count, len(wa_recipients), meeting_id,
        )

    except TwilioRestException as exc:
        # Twilio API-level failure (auth, rate limit, etc.) — retry
        logger.error(
            "send_whatsapp_task Twilio error for meeting=%s: %s",
            meeting_id, exc, exc_info=True,
        )
        raise self.retry(exc=exc)
    except Exception as exc:
        logger.error(
            "send_whatsapp_task failed for meeting=%s: %s", meeting_id, exc, exc_info=True
        )
        raise self.retry(exc=exc)
