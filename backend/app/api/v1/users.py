"""
app/api/v1/users.py

Users router — Phase 3, Step 3.1 of PLAN_WEB_SAAS.md.

Endpoints
---------
GET  /api/v1/users/me
    Returns the current authenticated user's profile.
    Used by the frontend to populate the dashboard settings page.

PATCH /api/v1/users/me
    Updates notification preferences: whatsapp_number and/or notify_email.
    All other fields (email, role, org_id, hashed_password) are immutable
    through this endpoint to keep the attack surface minimal.

Design note
-----------
All DB access goes through `get_db` (injected via Depends), which:
  1. Verifies the Bearer token (get_current_user)
  2. Yields a tenant-scoped session with RLS set to the user's org_id

This means even though the users table has no RLS policy, the session used
here is the correct per-tenant session for all other tables a future handler
on this router might touch (e.g. listing org members).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.user import UserResponse, UserUpdateRequest

logger = logging.getLogger(__name__)

router = APIRouter()


# ── GET /me ───────────────────────────────────────────────────────────────────

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the current user's profile",
)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Return the authenticated user's profile.

    Note: This endpoint deliberately does NOT inject `get_db` because it
    only needs the already-loaded User object from `get_current_user`.
    Injecting `get_db` would open an extra tenant-scoped session for no
    benefit here.
    """
    return current_user


# ── PATCH /me ─────────────────────────────────────────────────────────────────

@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Update notification preferences (whatsapp_number, notify_email)",
)
async def update_me(
    payload: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Update the authenticated user's notification preferences.

    Only `whatsapp_number` and `notify_email` are mutable here. Fields
    omitted from the payload (None) are left unchanged — this is a true
    partial update (PATCH semantics).

    The whatsapp_number is expected in E.164 format prefixed with
    "whatsapp:", e.g. "whatsapp:+919876543210". No normalisation is
    performed server-side; the client is responsible for format.
    """
    # Re-fetch the user within the RLS-scoped session to get a mutable instance
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if user is None:
        # Should never happen — the JWT was valid and the user existed at dep
        # resolution time. Guard defensively.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    changed = False
    if payload.whatsapp_number is not None:
        user.whatsapp_number = payload.whatsapp_number
        changed = True
    if payload.notify_email is not None:
        user.notify_email = str(payload.notify_email)
        changed = True

    if changed:
        await db.commit()
        await db.refresh(user)
        logger.info(
            "Updated preferences for user=%s: whatsapp=%s notify_email=%s",
            user.id, user.whatsapp_number, user.notify_email,
        )

    return user
