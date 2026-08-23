"""
app/workers/celery_app.py — Celery application factory (Phase 7, Step 7.1).

Verbatim from PLAN_WEB_SAAS.md.
Three distinct queues keep resource-intensive tasks (Gemini generation) from
starving fast I/O tasks (notifications).
"""

from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "meeting_saas",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.tasks_mom",
        "app.workers.tasks_embeddings",
        "app.workers.tasks_notifications",
        "app.workers.tasks_resend_email",
    ],
)
celery_app.conf.task_routes = {
    "app.workers.tasks_mom.*":           {"queue": "mom_generation"},
    "app.workers.tasks_embeddings.*":    {"queue": "embeddings"},
    "app.workers.tasks_notifications.*": {"queue": "notifications"},
    "app.workers.tasks_resend_email.*":  {"queue": "notifications"},
}
