"""
app/services/vad.py

Voice Activity Detection — ported directly from the desktop app's numpy
RMS-energy VAD filter (pipeline/chunker.py).

Now invoked per ~5-second WebM/Opus blob (decoded to float32 PCM before
this call) rather than per 30-second WAV chunk, which means silence is
detected and dropped at finer granularity — strictly better cost savings
than the original desktop implementation, which could waste one Sarvam AI
call per 30-second silence window.

Threshold tuning
----------------
SILENCE_RMS_THRESHOLD = 0.01 matches the desktop app's empirically derived
value. For very quiet rooms or headset-only scenarios, lowering to 0.005 may
be appropriate. For noisy open-office environments, raising to 0.015–0.02
prevents HVAC noise from triggering unnecessary STT calls.

The threshold is intentionally a module-level constant rather than a config
setting to keep the hot-path (called for every 5s chunk) free of attribute
lookups. If per-org tuning becomes a product feature, the function signature
accepts an optional override.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

SILENCE_RMS_THRESHOLD = 0.01  # tune empirically; matches desktop app's value


def is_silent(
    pcm: np.ndarray,
    threshold: float = SILENCE_RMS_THRESHOLD,
) -> bool:
    """
    Return True if the audio chunk contains only silence.

    Computes the Root Mean Square (RMS) energy of the float32 PCM array.
    Casting to float64 before squaring prevents overflow with large-amplitude
    int16-range samples that happen to be stored as float32.

    Args:
        pcm:       Mono float32 PCM array (any sample rate, any length).
        threshold: RMS below this value is classified as silence.

    Returns:
        True  → chunk is silent, discard it, skip the STT API call.
        False → chunk contains speech (or at minimum, non-trivial energy).
    """
    if pcm.size == 0:
        # Empty array after ffmpeg decode — treat as silent
        logger.debug("VAD received empty PCM array, treating as silent")
        return True

    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
    silent = rms < threshold
    logger.debug("VAD rms=%.5f threshold=%.5f silent=%s", rms, threshold, silent)
    return silent
