"""
app/core/security.py

Two distinct token types, deliberately kept separate:

1. Access/refresh JWTs — standard REST API auth.
   - Access token: 15 min lifespan, carries `sub` (user_id) and `org_id`.
     Sent as a Bearer token in the Authorization header on every request.
   - Refresh token: 7 day lifespan, carries only `sub` (user_id).
     Stored in an httpOnly cookie — never readable by JS, mitigates XSS
     token theft. On every POST /auth/refresh the old token is rotated
     (new refresh token issued, old one replaced in the cookie).

2. WS tickets — single-use, 30-second-lived UUID tokens.
   Minted by an authenticated REST call (POST /meetings/{id}/ws-ticket)
   specifically to authorise one WebSocket handshake.

   WHY NOT just put the JWT in the query string?
   Browsers cannot attach custom headers to a WebSocket handshake, so
   *some* credential must travel in the URL — which means it will appear
   verbatim in proxy/nginx/CDN access logs for the lifetime of those log
   files. A 15-minute JWT in that position is a real exposure vector.
   A 30-second, single-use UUID ticket limits the blast radius of a
   logged URL to a 30-second window, and the ticket is consumed (deleted
   from Redis) on first use, so even a captured log line is useless the
   moment the WS handshake completes.

Token shape
-----------
Access JWT payload:
    {"sub": "<user_uuid>", "org_id": "<org_uuid>", "type": "access", "exp": ...}

Refresh JWT payload:
    {"sub": "<user_uuid>", "type": "refresh", "exp": ...}

Redis WS ticket:
    key   : "ws_ticket:<ticket_uuid>"
    value : "<user_id>:<org_id>:<meeting_id>"   (colon-delimited)
    TTL   : settings.WS_TICKET_EXPIRE_SECONDS (default 30)
"""

import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.redis_client import redis_client

# ── Password hashing ──────────────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Return a bcrypt hash of `password`."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True iff `plain` matches the bcrypt `hashed` digest."""
    return pwd_context.verify(plain, hashed)


# ── JWT helpers ───────────────────────────────────────────────────────────────

def create_access_token(user_id: str, org_id: str) -> str:
    """
    Mint a 15-minute access JWT.

    Payload includes both `sub` (user identity) and `org_id` (tenant) so
    that get_current_user in api/deps.py can set up the RLS context without
    a DB round-trip to discover the user's org.
    """
    payload = {
        "sub":    str(user_id),
        "org_id": str(org_id),
        "type":   "access",
        "exp":    datetime.now(timezone.utc)
                  + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """
    Mint a 7-day refresh JWT.

    Deliberately carries NO `org_id` — the refresh endpoint validates the
    token type and re-issues a full access token after re-fetching the user
    (which also implicitly checks the account still exists and is active).
    """
    payload = {
        "sub":  str(user_id),
        "type": "refresh",
        "exp":  datetime.now(timezone.utc)
                + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """
    Decode and verify a JWT. Raises `jose.JWTError` on invalid signature,
    expiry, or malformed input. Callers must check `payload["type"]`.
    """
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )


def decode_access_token(token: str) -> dict:
    """
    Decode a token and assert it is an access token.
    Raises JWTError if expired, invalid, or wrong type.
    Used exclusively by api/deps.py::get_current_user.
    """
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise JWTError("Expected access token, got: " + payload.get("type", "unknown"))
    if not payload.get("sub") or not payload.get("org_id"):
        raise JWTError("Access token missing required claims")
    return payload


def decode_refresh_token(token: str) -> dict:
    """
    Decode a token and assert it is a refresh token.
    Raises JWTError if expired, invalid, or wrong type.
    Used exclusively by POST /auth/refresh.
    """
    payload = decode_token(token)
    if payload.get("type") != "refresh":
        raise JWTError("Expected refresh token, got: " + payload.get("type", "unknown"))
    if not payload.get("sub"):
        raise JWTError("Refresh token missing 'sub' claim")
    return payload


# ── WebSocket ticket helpers ──────────────────────────────────────────────────

async def mint_ws_ticket(user_id: str, org_id: str, meeting_id: str) -> str:
    """
    Create a single-use, 30-second-lived ticket for one WebSocket handshake.

    The ticket (a random UUID) is stored in Redis as:
        key   = "ws_ticket:<ticket>"
        value = "<user_id>:<org_id>:<meeting_id>"
        TTL   = WS_TICKET_EXPIRE_SECONDS

    The ticket is consumed (deleted) by consume_ws_ticket() on the first
    WebSocket connection attempt. If the ticket is never used, Redis expires
    it automatically after the TTL.

    Args:
        user_id:    The authenticated user's UUID (string).
        org_id:     The user's organisation UUID (string).
        meeting_id: The meeting UUID this ticket authorises audio for.

    Returns:
        The opaque ticket string (UUID4) to be passed in the WS query string.
    """
    ticket = str(uuid.uuid4())
    await redis_client.set(
        f"ws_ticket:{ticket}",
        f"{user_id}:{org_id}:{meeting_id}",
        ex=settings.WS_TICKET_EXPIRE_SECONDS,
    )
    return ticket


async def consume_ws_ticket(ticket: str) -> tuple[str, str, str] | None:
    """
    Validate and immediately invalidate a WS ticket.

    This is an atomic GET + DELETE sequence on the Redis key. Because Redis
    is single-threaded per command, there is no race window between the GET
    and DELETE — a second concurrent call for the same ticket will receive
    None from the GET and correctly reject the connection.

    Args:
        ticket: The opaque ticket string received from the WS query string.

    Returns:
        (user_id, org_id, meeting_id) if the ticket is valid, else None.
        A return of None means the caller must close the WebSocket with
        code 4001 (Unauthorized).
    """
    key = f"ws_ticket:{ticket}"
    value = await redis_client.get(key)
    if value is None:
        return None

    # Burn it immediately — single-use guarantee
    await redis_client.delete(key)

    user_id, org_id, meeting_id = value.decode().split(":")
    return user_id, org_id, meeting_id
