"""
config/settings.py
Central configuration loader. Raises descriptive errors on missing keys.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (parent of config/)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH,override=True)


def _require(key: str) -> str:
    """Fetch env var or raise with a clear message."""
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"[config] Required environment variable '{key}' is missing. "
            f"Check your .env file at {_ENV_PATH}"
        )
    return val


# ── Sarvam AI ──────────────────────────────────────────────────────────────
SARVAM_API_KEY: str        = _require("SARVAM_API_KEY")
SARVAM_STT_MODEL: str      = os.getenv("SARVAM_STT_MODEL", "saarika:v2")
SARVAM_LANGUAGE_CODE: str  = os.getenv("SARVAM_LANGUAGE_CODE", "hi-IN")
SARVAM_STT_URL: str        = "https://api.sarvam.ai/speech-to-text"

# ── Google Gemini ──────────────────────────────────────────────────────────
GEMINI_API_KEY: str        = _require("GEMINI_API_KEY")
GEMINI_MODEL: str          = os.getenv("GEMINI_MODEL", "gemini-1.5-pro-latest")

# ── Email ──────────────────────────────────────────────────────────────────
SMTP_HOST: str             = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int             = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str             = _require("SMTP_USER")
SMTP_PASSWORD: str         = _require("SMTP_PASSWORD")
EMAIL_FROM_NAME: str       = os.getenv("EMAIL_FROM_NAME", "MOM Generator Bot")
EMAIL_RECIPIENTS: list[str] = [
    e.strip() for e in _require("EMAIL_RECIPIENTS").split(",") if e.strip()
]

# ── Twilio ─────────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID: str    = _require("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN: str     = _require("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM: str  = _require("TWILIO_WHATSAPP_FROM")
WHATSAPP_RECIPIENTS: list[str] = [
    r.strip() for r in _require("WHATSAPP_RECIPIENTS").split(",") if r.strip()
]

# ── Audio ──────────────────────────────────────────────────────────────────
CHUNK_DURATION_SECONDS: int = int(os.getenv("CHUNK_DURATION_SECONDS", "30"))
AUDIO_SAMPLE_RATE: int      = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
AUDIO_CHANNELS: int         = int(os.getenv("AUDIO_CHANNELS", "1"))

# ── App ────────────────────────────────────────────────────────────────────
LOG_LEVEL: str              = os.getenv("LOG_LEVEL", "INFO")
