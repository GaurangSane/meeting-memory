"""
app/core/config.py

Centralised settings via pydantic-settings. All values are read from
environment variables (or the .env file loaded by python-dotenv).
Raises a clear ValidationError at startup if any required variable is missing.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Database ──────────────────────────────────────────────────────
    DATABASE_URL: str

    # ── Redis / Celery ────────────────────────────────────────────────
    REDIS_URL: str
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str

    # ── Auth ──────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    WS_TICKET_EXPIRE_SECONDS: int = 30

    # ── Sarvam AI ─────────────────────────────────────────────────────
    SARVAM_API_KEY: str
    SARVAM_STT_MODEL: str = "saaras:v3"

    # ── Google Gemini ─────────────────────────────────────────────────
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "models/gemini-1.5-pro-latest"
    GEMINI_EMBEDDING_MODEL: str = "models/gemini-embedding-001"

    # ── Email ─────────────────────────────────────────────────────────
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str
    SMTP_PASSWORD: str

    # ── Twilio ────────────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_WHATSAPP_FROM: str

    # ── Frontend ──────────────────────────────────────────────────────
    NEXT_PUBLIC_API_URL: str = "http://localhost:3000"
    ALLOWED_ORIGINS: str = "http://localhost"


settings = Settings()
