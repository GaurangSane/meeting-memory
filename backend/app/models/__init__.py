"""
app/models/__init__.py

Importing all models here ensures SQLAlchemy's mapper registry is populated
before Alembic autogenerate or any ORM operation runs.
"""

from app.models.organization import Organization
from app.models.user import User
from app.models.meeting import Meeting
from app.models.mom import MomRecord, MomEditHistory
from app.models.embedding import MeetingEmbedding

__all__ = [
    "Organization",
    "User",
    "Meeting",
    "MomRecord",
    "MomEditHistory",
    "MeetingEmbedding",
]
