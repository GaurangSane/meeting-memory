"""
app/schemas/user.py

Pydantic request/response models for user endpoints (Phase 3, Step 3.1).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr


class UserResponse(BaseModel):
    """
    Profile response for GET /users/me.
    hashed_password is deliberately excluded — never serialise it.
    """
    id: uuid.UUID
    org_id: uuid.UUID
    email: EmailStr
    role: str
    whatsapp_number: str | None
    notify_email: str | None
    created_at: datetime

    model_config = {"from_attributes": True}  # Pydantic v2 ORM mode


class UserUpdateRequest(BaseModel):
    """
    Body for PATCH /users/me.

    Both fields are optional — send only the fields you want to update.
    A null value (explicit JSON null) clears the preference.
    An omitted field leaves the stored value unchanged.
    """
    whatsapp_number: str | None = None
    notify_email: EmailStr | None = None
