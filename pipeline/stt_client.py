"""
pipeline/stt_client.py

Asynchronous Sarvam AI Speech-to-Text client.

Key design:
  - Uses aiohttp for non-blocking multipart/form-data POST.
  - Each chunk is dispatched as an independent asyncio Task.
  - Results are stored in a dict keyed by chunk_index to preserve ordering
    even if responses arrive out-of-order (network jitter).
  - A semaphore limits concurrent in-flight requests to avoid rate-limit 429s.
  - Retry logic: on 5xx HTTP errors or network timeouts, the request is
    retried up to MAX_RETRIES times with exponential backoff (1s, 2s).
  - API latency is tracked and surfaced via the latency_callback for telemetry.
  - Exception handling is in AudioChunker's Future callback.
    This module only raises; it does not swallow.
"""

import asyncio
import logging
import time
import aiohttp
from aiohttp import FormData

from config.settings import (
    SARVAM_API_KEY,
    SARVAM_STT_MODEL,
    SARVAM_LANGUAGE_CODE,
    SARVAM_STT_URL,
)

logger = logging.getLogger(__name__)

# Max concurrent in-flight STT requests (rate-limit guard)
MAX_CONCURRENT_REQUESTS = 3

# Retry configuration for transient 5xx / network errors
MAX_RETRIES = 2
BACKOFF_BASE_SECONDS = 1.0


class SarvamSTTClient:
    """
    Dispatches audio chunks to Sarvam AI STT asynchronously.
    Maintains an ordered transcript dict.
    Retries on 5xx errors or network timeouts with exponential backoff.
    """

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._transcripts: dict[int, str] = {}
        self._lock = asyncio.Lock()
        self._log_callback = None
        self._latency_callback = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Create persistent aiohttp session."""
        headers = {"api-subscription-key": SARVAM_API_KEY}
        connector = aiohttp.TCPConnector(ssl=True)
        self._session = aiohttp.ClientSession(
            headers=headers,
            connector=connector,
        )
        logger.info("[STT] aiohttp session opened.")

    async def stop(self) -> None:
        """Close session cleanly."""
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("[STT] aiohttp session closed.")

    # ── Public API ─────────────────────────────────────────────────────────

    def set_log_callback(self, callback) -> None:
        """Optional callback(str) to push status messages to the UI."""
        self._log_callback = callback

    def set_latency_callback(self, callback) -> None:
        """Optional callback(float) to push last API latency (ms) to the UI."""
        self._latency_callback = callback

    async def transcribe_chunk(self, chunk_index: int, wav_bytes: bytes) -> None:
        """
        Async entry point called by chunker for each WAV chunk.
        Acquires semaphore then fires the STT request with retry logic.

        NOTE: This coroutine intentionally re-raises exceptions rather than
        catching them. The caller (AudioChunker._on_chunk_future_done via
        the Future done-callback) is responsible for logging them.
        """
        async with self._semaphore:
            await self._post_with_retry(chunk_index, wav_bytes)

    def get_full_transcript(self) -> str:
        """
        Return all transcripts joined in chronological order.
        Called AFTER recording stops and all in-flight tasks complete.
        """
        ordered_keys = sorted(self._transcripts.keys())
        parts = [self._transcripts[k] for k in ordered_keys if self._transcripts[k]]
        return " ".join(parts)

    def clear(self) -> None:
        """Reset for a new meeting session."""
        self._transcripts.clear()

    # ── Internal ───────────────────────────────────────────────────────────

    async def _post_with_retry(self, chunk_index: int, wav_bytes: bytes) -> None:
        """
        Wraps _post_to_sarvam with exponential-backoff retry logic.

        Retries on:
          - HTTP 5xx server errors
          - aiohttp.ServerTimeoutError / asyncio.TimeoutError (network timeout)
          - aiohttp.ClientConnectionError (transient network blip)

        Does NOT retry on 4xx client errors (bad request, auth failure, etc.)
        — those indicate a configuration problem that retrying won't fix.

        After MAX_RETRIES exhausted, the final exception is re-raised so
        AudioChunker's done-callback can log it to the UI.
        """
        last_exc: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):  # attempt 0, 1, 2
            try:
                await self._post_to_sarvam(chunk_index, wav_bytes)
                return  # success — exit retry loop
            except ValueError as exc:
                # ValueError is raised for non-5xx HTTP errors; don't retry
                raise
            except (
                aiohttp.ServerTimeoutError,
                asyncio.TimeoutError,
                aiohttp.ClientConnectionError,
            ) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
                    logger.warning(
                        f"[STT] Chunk #{chunk_index} — network error on attempt "
                        f"{attempt + 1}/{MAX_RETRIES + 1}: {exc}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    self._log(
                        f"⚠️ STT network error (chunk #{chunk_index}), "
                        f"retrying in {delay:.0f}s... (attempt {attempt + 1}/{MAX_RETRIES + 1})"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"[STT] Chunk #{chunk_index} — all {MAX_RETRIES + 1} attempts failed."
                    )
            except _ServerError as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
                    logger.warning(
                        f"[STT] Chunk #{chunk_index} — HTTP 5xx on attempt "
                        f"{attempt + 1}/{MAX_RETRIES + 1}: {exc}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    self._log(
                        f"⚠️ STT server error (chunk #{chunk_index}), "
                        f"retrying in {delay:.0f}s... (attempt {attempt + 1}/{MAX_RETRIES + 1})"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"[STT] Chunk #{chunk_index} — all {MAX_RETRIES + 1} attempts failed."
                    )

        # All retries exhausted — re-raise the last exception
        if last_exc is not None:
            raise last_exc

    async def _post_to_sarvam(self, chunk_index: int, wav_bytes: bytes) -> None:
        """
        POST WAV bytes to Sarvam AI STT endpoint.

        Raises:
          _ServerError   on HTTP 5xx (retryable)
          ValueError     on HTTP 4xx (not retryable)
          aiohttp errors on network failure (retryable — handled by caller)
        """
        if not self._session:
            raise RuntimeError("[STT] Session not started. Call start() first.")

        form = FormData()
        form.add_field(
            name="file",
            value=wav_bytes,
            filename=f"chunk_{chunk_index:04d}.wav",
            content_type="audio/wav",
        )
        form.add_field("model", SARVAM_STT_MODEL)
        form.add_field("language_code", SARVAM_LANGUAGE_CODE)
        form.add_field("with_timestamps", "false")
        form.add_field("with_disfluencies", "false")

        self._log(f"⏳ Transcribing chunk #{chunk_index}...")

        t_start = time.perf_counter()
        async with self._session.post(SARVAM_STT_URL, data=form) as resp:
            latency_ms = (time.perf_counter() - t_start) * 1000

            if resp.status == 200:
                data = await resp.json()
                transcript = data.get("transcript", "")
                async with self._lock:
                    self._transcripts[chunk_index] = transcript
                self._log(f"✅ Chunk #{chunk_index}: \"{transcript[:60]}...\" ({latency_ms:.0f}ms)")
                logger.debug(f"[STT] Chunk #{chunk_index} transcript: {transcript}")

                # Push latency telemetry
                if self._latency_callback:
                    self._latency_callback(latency_ms)

            elif 500 <= resp.status < 600:
                error_text = await resp.text()
                raise _ServerError(
                    f"Sarvam AI HTTP {resp.status} on chunk #{chunk_index}: {error_text}"
                )
            else:
                error_text = await resp.text()
                raise ValueError(
                    f"Sarvam AI HTTP {resp.status} on chunk #{chunk_index}: {error_text}"
                )

    def _log(self, message: str) -> None:
        logger.info(f"[STT] {message}")
        if self._log_callback:
            self._log_callback(message)


class _ServerError(Exception):
    """Internal sentinel for retryable HTTP 5xx errors."""
    pass
