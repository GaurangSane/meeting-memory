"""
app/services/stt_service.py

Sarvam AI Speech-to-Text client — Phase 4, Step 4.3.

Direct port of the desktop SarvamSTTClient, updated to the `saaras:v3` model.
Key differences from the desktop version:
  - Uses `aiohttp.ClientSession` (async HTTP) instead of `requests.Session`.
  - Exceptions are RAISED, not swallowed. The caller (websocket_audio.py)
    attaches done-callbacks to tasks to surface errors to the client, following
    the same exception-surfacing discipline established in the desktop app's
    bug-fix history (the "Fix 3" pattern).
  - The FormData object is recreated on each retry to avoid aiohttp's
    single-use stream constraint (a payload generator can only be iterated once).

Retry policy
------------
  MAX_RETRIES = 4 attempts
  Retryable:     HTTP 429 (rate limit) and 5xx (server error) → exponential backoff
  Non-retryable: 4xx other than 429 (bad request, invalid API key) → raise immediately
  Timeout:       20 seconds per attempt

Exponential backoff schedule (BASE=1.5s):
  attempt 0 → 1.5s wait
  attempt 1 → 3.0s wait
  attempt 2 → 6.0s wait
  (attempt 3 is the last try, no sleep after)
"""

import asyncio
import logging
import struct
import wave
from io import BytesIO

import aiohttp
from aiohttp import FormData

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 4
BASE_BACKOFF_SECONDS = 1.5
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"


async def transcribe_chunk(
    session: aiohttp.ClientSession,
    wav_bytes: bytes,
    chunk_index: int,
) -> str:
    """
    Transcribe a WAV audio chunk using Sarvam AI saaras:v3.

    Args:
        session:     Shared aiohttp.ClientSession (one per WS connection).
        wav_bytes:   16kHz mono int16 WAV bytes (standard WAV container).
        chunk_index: Chunk sequence number — used only for logging/error messages.

    Returns:
        The transcript string. Empty string if Sarvam returned no text
        (e.g. chunk contained only unintelligible noise that passed VAD).

    Raises:
        RuntimeError: After MAX_RETRIES exhausted on retryable errors.
        ValueError:   Immediately on non-retryable 4xx errors.
    """
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES):
        # Recreate FormData on each attempt — aiohttp streams are single-use
        form = FormData()
        form.add_field(
            "file",
            wav_bytes,
            filename=f"chunk_{chunk_index}.wav",
            content_type="audio/wav",
        )
        form.add_field("model", settings.SARVAM_STT_MODEL)  # "saaras:v3"

        try:
            async with session.post(
                SARVAM_STT_URL,
                data=form,
                headers={"api-subscription-key": settings.SARVAM_API_KEY},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    transcript = data.get("transcript", "")
                    logger.debug(
                        "STT chunk=%d attempt=%d transcript=%r",
                        chunk_index, attempt, transcript[:80],
                    )
                    return transcript

                if resp.status == 429 or resp.status >= 500:
                    error_body = await resp.text()
                    exc = aiohttp.ClientError(
                        f"Retryable HTTP {resp.status} on chunk #{chunk_index}: {error_body[:200]}"
                    )
                    logger.warning(
                        "STT retryable error chunk=%d attempt=%d status=%d",
                        chunk_index, attempt, resp.status,
                    )
                    raise exc

                # Non-retryable: bad request, auth failure, etc.
                error_body = await resp.text()
                raise ValueError(
                    f"Non-retryable Sarvam STT error {resp.status} "
                    f"on chunk #{chunk_index}: {error_body[:400]}"
                )

        except ValueError:
            raise  # non-retryable — propagate immediately

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                backoff = BASE_BACKOFF_SECONDS * (2 ** attempt)
                logger.info(
                    "STT retry chunk=%d attempt=%d/%d backoff=%.1fs error=%s",
                    chunk_index, attempt + 1, MAX_RETRIES, backoff, exc,
                )
                await asyncio.sleep(backoff)

    raise RuntimeError(
        f"STT failed after {MAX_RETRIES} attempts on chunk #{chunk_index}: {last_exc}"
    )
