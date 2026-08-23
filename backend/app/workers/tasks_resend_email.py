"""Resend-backed MOM email delivery task.

This task is intentionally isolated from the existing SMTP implementation so
we can switch production MOM email delivery to Resend over HTTPS without
changing WhatsApp notification behaviour or the rest of the MOM pipeline.
"""

import logging

import resend

from app.core.config import settings
from app.db import get_meeting_with_mom_sync, get_recipients_for_meeting_sync
from app.workers.celery_app import celery_app
from app.workers.tasks_notifications import _render_email_html

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(self, meeting_id: str) -> None:
    """Send a completed MOM through Resend's HTTPS email API."""
    try:
        if not settings.RESEND_API_KEY:
            raise RuntimeError("RESEND_API_KEY is not configured")
        if not settings.RESEND_FROM_EMAIL:
            raise RuntimeError("RESEND_FROM_EMAIL is not configured")

        meeting, mom = get_meeting_with_mom_sync(meeting_id)
        recipients = get_recipients_for_meeting_sync(meeting_id)

        email_recipients = [
            str(u.notify_email or u.email)
            for u in recipients
            if u.notify_email or u.email
        ]
        if not email_recipients:
            logger.info(
                "send_email_task: no email recipients for meeting=%s", meeting_id
            )
            return

        logger.info(
            "send_email_task: sending MOM via Resend for meeting=%s to %d recipients",
            meeting_id,
            len(email_recipients),
        )

        resend.api_key = settings.RESEND_API_KEY
        html_body = _render_email_html(meeting, mom)
        subject = f"MOM: {meeting.title or 'Meeting'}"
        from_address = (
            f"{settings.RESEND_FROM_NAME} <{settings.RESEND_FROM_EMAIL}>"
            if settings.RESEND_FROM_NAME
            else settings.RESEND_FROM_EMAIL
        )

        response = resend.Emails.send(
            {
                "from": from_address,
                "to": email_recipients,
                "subject": subject,
                "html": html_body,
            }
        )

        logger.info(
            "send_email_task: Resend accepted meeting=%s response=%s",
            meeting_id,
            response,
        )

    except Exception as exc:
        logger.error(
            "send_email_task failed for meeting=%s: %s",
            meeting_id,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc)
