"""
audio/capture.py

Single-stream In-Person Physical Meeting Assistant audio capture.

Design:
  - Microphone is captured via sounddevice InputStream callback.
    Each callback block is converted to mono float32 and pushed to `mic_queue`.

  - All loopback / soundcard / dual-queue logic has been removed.
    This system is optimised for capturing physical room audio from a
    laptop or external microphone — not system audio playback.
"""

import queue
import logging
import numpy as np
import sounddevice as sd

from audio.mixer import to_mono
from config.settings import AUDIO_SAMPLE_RATE, AUDIO_CHANNELS

logger = logging.getLogger(__name__)


def get_mic_devices() -> list[str]:
    """Return a list of available microphone device names."""
    try:
        devices = sd.query_devices()
        return [d['name'] for d in devices if d.get('max_input_channels', 0) > 0]
    except Exception as e:
        logger.warning(f"[AudioCapture] Failed to list mic devices: {e}")
        return []


# Frame block size: ~20ms at 16 kHz
BLOCKSIZE = 320


class AudioCapture:
    """
    Manages single-stream microphone capture.

    Pushes mono float32 frames onto `mic_queue` for consumption by AudioChunker.
    """

    def __init__(self, mic_queue: queue.Queue):
        """
        Args:
            mic_queue: Thread-safe queue for microphone frames.
        """
        self._mic_queue = mic_queue
        self._mic_stream: sd.InputStream | None = None

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self, mic_device_name: str | None = None) -> None:
        """Start the microphone capture stream."""
        self._start_mic(mic_device_name)
        logger.info("[AudioCapture] Microphone stream started.")

    def stop(self) -> None:
        """Gracefully stop the microphone stream."""
        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()
            self._mic_stream = None
        logger.info("[AudioCapture] Microphone stream stopped.")

    # ── Internal: Mic ──────────────────────────────────────────────────────

    def _start_mic(self, device_name: str | None = None) -> None:
        """Open sounddevice InputStream and register callback."""
        device_kwarg = {}
        if device_name and device_name != "Default":
            device_kwarg["device"] = device_name

        self._mic_stream = sd.InputStream(
            samplerate=AUDIO_SAMPLE_RATE,
            channels=AUDIO_CHANNELS,
            dtype="float32",
            blocksize=BLOCKSIZE,
            callback=self._mic_callback,
            latency="low",
            **device_kwarg,
        )
        self._mic_stream.start()
        logger.debug("[Mic] InputStream started.")

    def _mic_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags,
    ) -> None:
        """
        Called by sounddevice on every audio block (PortAudio thread).

        Converts block to mono float32 and pushes onto mic_queue.
        """
        if status:
            logger.debug(f"[Mic] Callback status: {status}")

        mono = to_mono(indata.copy())
        try:
            self._mic_queue.put_nowait(mono)
        except queue.Full:
            logger.warning("[AudioCapture] Mic queue full — dropping frame.")
