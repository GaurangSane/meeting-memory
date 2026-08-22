"""
app/schemas/auth.py

Pydantic request/response models for the auth router (Phase 2).

RegisterRequest  — POST /auth/register
LoginRequest     — POST /auth/login  (also used as OAuth2 form data shape)
TokenResponse    — returned in the JSON body of register + login + refresh
"""

from pydantic import BaseModel, EmailStr, field_validator


class RegisterRequest(BaseModel):
    """
    Body for POST /auth/register.
    Creates an Organization + first admin User in a single transaction.
    """
    org_name: str
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("org_name")
    @classmethod
    def org_name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("org_name must not be blank")
        return v.strip()


class LoginRequest(BaseModel):
    """Body for POST /auth/login."""
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """
    JSON body returned by register, login, and refresh.
    The refresh token travels in an httpOnly cookie set via Set-Cookie header —
    it is NEVER included in this response body so it cannot be read by JS.
    """
    access_token: str
    token_type: str = "bearer"
