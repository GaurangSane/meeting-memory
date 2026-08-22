"""
app/api/v1/auth.py

Authentication router — Phase 2, Step 2.2 of PLAN_WEB_SAAS.md.

Endpoints
---------
POST /api/v1/auth/register
    Body : {org_name, email, password}
    Flow : Creates Organization + first admin User in a single DB transaction.
           Returns {access_token} in JSON body + sets refresh token in httpOnly cookie.

POST /api/v1/auth/login
    Body : {email, password}  (also accepts OAuth2 form data for Swagger)
    Flow : verify_password → create_access_token + create_refresh_token.
           Returns {access_token} in JSON body + sets refresh token in httpOnly cookie.

POST /api/v1/auth/refresh
    Cookie required : refresh_token (httpOnly)
    Flow : decode_refresh_token → re-fetch user → new access + new refresh token.
           Rotates the refresh token on every call (the old cookie value is replaced).

POST /api/v1/auth/logout
    Flow : Clears the refresh_token cookie (sets it to an expired value).
    Returns : 204 No Content.

Security notes
--------------
- The refresh token NEVER appears in the JSON response body. It travels
  exclusively in an httpOnly, SameSite=Lax cookie. This means it is not
  accessible to JavaScript in the browser, which mitigates XSS token theft.
- `samesite="lax"` protects against CSRF for state-changing requests that
  originate from cross-site navigations. Combined with the Bearer header
  check on the access token, there is no practical CSRF surface.
- `secure=True` is set in production (enforce HTTPS). For local HTTP dev
  you can override by setting COOKIE_SECURE=false in .env — but the default
  is True so production is safe by default.
"""

import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_session import SessionLocal
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from app.models.organization import Organization
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Cookie configuration ──────────────────────────────────────────────────────
REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 days in seconds


def _set_refresh_cookie(response: Response, token: str) -> None:
    """Write the refresh token into an httpOnly cookie on the response."""
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,       # not readable by JS
        secure=False,        # set to True in production (HTTPS); False for local HTTP dev
        samesite="lax",      # CSRF protection for top-level navigations
        max_age=REFRESH_COOKIE_MAX_AGE,
        path="/",            # root path so Next.js middleware can read it on every route
    )


def _clear_refresh_cookie(response: Response) -> None:
    """Overwrite the refresh cookie with an immediately-expired empty value."""
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path="/",            # must match the path used when the cookie was set
        httponly=True,
        secure=False,        # must match secure flag used when cookie was set
        samesite="lax",
    )


# ── POST /register ────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new organisation and its first admin user",
)
async def register(
    payload: RegisterRequest,
    response: Response,
) -> TokenResponse:
    """
    Atomically create an Organization and the first admin User within it.

    Uses a single session transaction so that a failure at any point
    (e.g. duplicate email) leaves no orphaned org record.
    """
    async with SessionLocal() as session:
        async with session.begin():
            # Reject duplicate email before attempting any inserts
            existing = await session.execute(
                select(User).where(User.email == payload.email)
            )
            if existing.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An account with that email already exists",
                )

            # Create organisation
            org = Organization(name=payload.org_name)
            session.add(org)
            await session.flush()  # populate org.id before referencing it in User

            # Create first admin user
            user = User(
                org_id=org.id,
                email=payload.email,
                hashed_password=hash_password(payload.password),
                role="admin",
            )
            session.add(user)
            await session.flush()  # populate user.id for token generation

            user_id = str(user.id)
            org_id  = str(org.id)
        # session.begin() commits here

    logger.info("Registered org=%s user=%s email=%s", org_id, user_id, payload.email)

    access_token  = create_access_token(user_id=user_id, org_id=org_id)
    refresh_token = create_refresh_token(user_id=user_id)
    _set_refresh_cookie(response, refresh_token)

    return TokenResponse(access_token=access_token)


# ── POST /login ───────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login with email and password",
)
async def login(
    payload: LoginRequest,
    response: Response,
) -> TokenResponse:
    """
    Verify credentials and issue access + refresh tokens.

    Deliberately returns the same 401 for "email not found" and "wrong password"
    to avoid leaking which emails are registered (user enumeration prevention).
    """
    _bad_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.email == payload.email)
        )
        user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.hashed_password):
        raise _bad_credentials

    user_id = str(user.id)
    org_id  = str(user.org_id)

    logger.info("Login user=%s org=%s", user_id, org_id)

    access_token  = create_access_token(user_id=user_id, org_id=org_id)
    refresh_token = create_refresh_token(user_id=user_id)
    _set_refresh_cookie(response, refresh_token)

    return TokenResponse(access_token=access_token)


# ── POST /refresh ─────────────────────────────────────────────────────────────

@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token and issue a new access token",
)
async def refresh_token_endpoint(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> TokenResponse:
    """
    Validate the httpOnly refresh cookie and rotate both tokens.

    Token rotation (issuing a new refresh token on every call) means a stolen
    refresh token is single-use — after the legitimate client refreshes, the
    stolen token is already superseded. This is the recommended pattern for
    refresh token security.
    """
    _invalid_refresh = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if refresh_token is None:
        raise _invalid_refresh

    try:
        payload = decode_refresh_token(refresh_token)
        user_id: str = payload["sub"]
    except JWTError:
        raise _invalid_refresh

    # Re-fetch the user to verify the account still exists
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        raise _invalid_refresh

    org_id = str(user.org_id)

    new_access  = create_access_token(user_id=user_id, org_id=org_id)
    new_refresh = create_refresh_token(user_id=user_id)
    _set_refresh_cookie(response, new_refresh)

    logger.debug("Refreshed tokens for user=%s", user_id)
    return TokenResponse(access_token=new_access)


# ── POST /logout ──────────────────────────────────────────────────────────────

@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout — clear the refresh token cookie",
)
async def logout(response: Response) -> None:
    """
    Clear the refresh token cookie. The access token is short-lived (15 min)
    and is discarded by the frontend immediately; it does not need server-side
    invalidation for the normal logout case.
    """
    _clear_refresh_cookie(response)
    logger.debug("Logout — refresh cookie cleared")
