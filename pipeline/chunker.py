"""
pipeline/chunker.py — Single-stream with Voice Activity Detection (VAD).

Drains mic_queue into a frame buffer. When the buffer reaches
CHUNK_DURATION_SECONDS of audio:
  1. Calculates the RMS (Root Mean Square) energy of the PCM array.
  2. If RMS < SILENCE_RMS_THRESHOLD, drops the chunk and logs a message
     to save API quota (no speech detected).
  3. Otherwise, encodes to WAV bytes (int16) and dispatches to the STT client
     via asyncio.run_coroutine_threadsafe().
  4. Attaches a done-callback to the returned Future so any exception is
     logged to the UI.

Telemetry counters (chunks_processed, silence_skipped) are updated and
pushed to the optional telemetry_callback so the UI can display live stats.
"""

import io
import queue
import threading
import logging
import concurrent.futures
import numpy as np
from scipy.io.wavfile import write as wav_write
from typing import Callable, Awaitable
import asyncio

from audio.mixer import to_mono
from config.settings import CHUNK_DURATION_SECONDS, AUDIO_SAMPLE_RATE

logger = logging.getLogger(__name__)

FRAMES_PER_CHUNK = CHUNK_DURATION_SECONDS * AUDIO_SAMPLE_RATE

# RMS energy below this threshold means nobody was speaking.
# float32 PCM is in [-1.0, 1.0]; typical room noise floor is ~0.002–0.005.
SILENCE_RMS_THRESHOLD = 0.01


class AudioChunker:
    """
    Reads mic audio frames from a single queue.
    Applies VAD energy gate before emitting WAV chunks to the STT client.
    Reports telemetry via an optional callback.
    """

    def __init__(
        self,
        mic_queue: queue.Queue,
        async_loop: asyncio.AbstractEventLoop,
        on_chunk_ready: Callable[[int, bytes], Awaitable[None]],
        log_callback: Callable[[str], None] | None = None,
        telemetry_callback: Callable[[dict], None] | None = None,
    ):
        """
        Args:
            mic_queue:           Primary (and only) audio source.
            async_loop:          The running asyncio event loop (daemon thread).
            on_chunk_ready:      Async coroutine called with (chunk_index, wav_bytes).
            log_callback:        Optional callable(str) to surface messages to UI.
            telemetry_callback:  Optional callable(dict) called after each chunk with
                                 keys: chunks_processed, silence_skipped.
        """
        self._mic_queue = mic_queue
        self._async_loop = async_loop
        self._on_chunk_ready = on_chunk_ready
        self._log_callback = log_callback
        self._telemetry_callback = telemetry_callback
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._chunk_index: int = 0

        # Telemetry counters
        self._chunks_processed: int = 0
        self._silence_skipped: int = 0

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="AudioChunkerThread",
        )
        self._thread.start()
        logger.info(
            f"[Chunker] Started. Chunk size: {CHUNK_DURATION_SECONDS}s "
            f"({FRAMES_PER_CHUNK} frames at {AUDIO_SAMPLE_RATE} Hz). "
            f"VAD silence threshold: RMS < {SILENCE_RMS_THRESHOLD}."
        )

    def stop(self) -> None:
        """Stop the chunker. Partial buffer is flushed as the final chunk."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("[Chunker] Stopped.")

    # ── Internal: Worker ───────────────────────────────────────────────────

    def _worker(self) -> None:
        """
        Main chunker loop.

        mic_buffer drives timing: when it accumulates FRAMES_PER_CHUNK frames,
        a chunk is evaluated (VAD check) and optionally emitted.
        """
        mic_buffer: list[np.ndarray] = []
        mic_frame_count: int = 0

        while not self._stop_event.is_set():
            try:
                mic_frame = self._mic_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            mic_buffer.append(mic_frame)
            mic_frame_count += len(mic_frame)

            if mic_frame_count >= FRAMES_PER_CHUNK:
                self._emit_chunk(mic_buffer)
                mic_buffer = []
                mic_frame_count = 0

        # Flush any remaining audio as the final chunk
        if mic_buffer:
            logger.info("[Chunker] Flushing final partial chunk.")
            self._emit_chunk(mic_buffer)

    def _emit_chunk(self, mic_buffer: list[np.ndarray]) -> None:
        """
        VAD gate, encode, and dispatch one WAV chunk.

        Step 1: Concatenate the mic buffer into a single mono array.
        Step 2: Calculate RMS energy. If below threshold, drop and log.
        Step 3: Encode to int16 WAV bytes.
        Step 4: Schedule the STT coroutine on the async event loop with a
                done-callback to surface any exceptions.
        """
        mic_pcm = np.concatenate(mic_buffer)

        # ── Voice Activity Detection ───────────────────────────────────────
        rms = float(np.sqrt(np.mean(mic_pcm ** 2)))
        if rms < SILENCE_RMS_THRESHOLD:
            self._silence_skipped += 1
            logger.info(
                f"[Chunker] Silence detected (RMS={rms:.5f} < {SILENCE_RMS_THRESHOLD}), "
                f"skipping API call. Total skipped: {self._silence_skipped}"
            )
            self._log_to_ui(
                f"🔇 Silence detected, skipping API call "
                f"(RMS={rms:.4f}). Skipped total: {self._silence_skipped}"
            )
            self._push_telemetry()
            return

        # Convert float32 [-1, 1] → int16 for WAV
        pcm_int16 = (mic_pcm * 32767).clip(-32768, 32767).astype(np.int16)
        wav_buffer = io.BytesIO()
        wav_write(wav_buffer, AUDIO_SAMPLE_RATE, pcm_int16)
        wav_bytes = wav_buffer.getvalue()

        idx = self._chunk_index
        self._chunk_index += 1
        self._chunks_processed += 1
        logger.info(
            f"[Chunker] Emitting chunk #{idx} (RMS={rms:.5f}, {len(wav_bytes):,} bytes)"
        )

        self._push_telemetry()

        # Capture the Future and attach a done-callback to surface exceptions
        future: concurrent.futures.Future = asyncio.run_coroutine_threadsafe(
            self._on_chunk_ready(idx, wav_bytes),
            self._async_loop,
        )
        future.add_done_callback(self._on_chunk_future_done)

    def _on_chunk_future_done(self, future: concurrent.futures.Future) -> None:
        """
        Done-callback executed when an STT task Future resolves.

        If the coroutine raised an exception, logs it at ERROR level and
        notifies the UI. Normal completion is a no-op.
        """
        if future.cancelled():
            logger.warning("[Chunker] STT task was cancelled unexpectedly.")
            self._log_to_ui("⚠️ An STT task was cancelled — one chunk may be missing.")
            return

        exc = future.exception()
        if exc is not None:
            logger.error(
                f"[Chunker] STT task raised an unhandled exception: "
                f"{type(exc).__name__}: {exc!r}"
            )
            self._log_to_ui(f"❌ STT task failed: {type(exc).__name__}: {exc}")

    def _push_telemetry(self) -> None:
        """Push current counters to the telemetry callback (if registered)."""
        if self._telemetry_callback:
            self._telemetry_callback({
                "chunks_processed": self._chunks_processed,
                "silence_skipped": self._silence_skipped,
            })

    def _log_to_ui(self, message: str) -> None:
        if self._log_callback:
            self._log_callback(message)
