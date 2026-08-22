"""
app/api/deps.py

FastAPI dependency injection helpers shared across all routers.

Dependencies
------------
get_current_user(token)
    Decodes the Bearer access token (via decode_access_token), fetches the
    User row from Postgres, and returns the ORM object. Raises HTTP 401 on
    an invalid, expired, or wrong-type token, and on a missing user record.

get_db(current_user)
    Yields an AsyncSession with `app.current_org` pre-set for this request.
    Every handler that injects `db=Depends(get_db)` gets RLS-scoped queries
    automatically — no handler ever needs to call SET LOCAL itself.

get_current_admin(current_user)
    Same as get_current_user but additionally enforces role == 'admin'.
    Raises HTTP 403 if the authenticated user is not an admin.

Usage
-----
    @router.get("/example")
    async def example(
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ):
        ...

    @router.post("/admin-only")
    async def admin_only(
        current_user: User = Depends(get_current_admin),
    ):
        ...
"""

import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_session import SessionLocal, get_tenant_session
from app.core.security import decode_access_token
from app.models.user import User

logger = logging.getLogger(__name__)

# tokenUrl tells Swagger/OpenAPI where users POST credentials to get a token.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """
    Decode Bearer JWT, verify it is an access token, and return the User ORM object.

    This dependency intentionally uses an UNSCOPED session (no RLS) for the
    user lookup because at this point we don't yet know the org_id to scope
    to — we're trying to find the user *to* learn the org_id. The user table
    has no RLS policy, so this is safe.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id: str = payload["sub"]
    except JWTError as exc:
        logger.debug("JWT validation failed: %s", exc)
        raise credentials_exc

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

    if user is None:
        logger.debug("User %s from valid JWT no longer exists in DB", user_id)
        raise credentials_exc

    return user


async def get_db(
    current_user: User = Depends(get_current_user),
) -> AsyncSession:
    """
    Yield a tenant-scoped AsyncSession.

    Sets `app.current_org = current_user.org_id` via SET LOCAL before the
    first query, activating the RLS policies defined in the Phase 1 migration.
    The session is automatically closed and the SET LOCAL is rolled back when
    the request completes.
    """
    async for session in get_tenant_session(str(current_user.org_id)):
        yield session


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Verify the authenticated user has role == 'admin'.
    Raises HTTP 403 Forbidden for non-admin users.
    Used on endpoints that should only be accessible to org admins
    (e.g. inviting new users, changing org-level settings).
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return current_user
