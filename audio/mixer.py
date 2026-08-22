"""
audio/mixer.py
Mixes two numpy float32 PCM arrays (mic + loopback) into a single mono channel.
Called by AudioChunker at chunk-emission time, NOT inside the capture callback.

The length-mismatch handling (truncation to min_len) is intentional: it is the
correct way to absorb the small frame-count difference that accumulates over 30
seconds of independent hardware-clock drift.
"""

import numpy as np


def mix_streams(mic_pcm: np.ndarray, loopback_pcm: np.ndarray) -> np.ndarray:
    """
    Mix mic and loopback PCM float32 mono arrays.

    Args:
        mic_pcm: 1-D float32 array (already mono)
        loopback_pcm: 1-D float32 array (already mono)

    Returns:
        Mixed mono float32 array, values soft-clipped to [-1.0, 1.0] via tanh.
        Length = min(len(mic_pcm), len(loopback_pcm)).
    """
    min_len = min(len(mic_pcm), len(loopback_pcm))
    mixed = mic_pcm[:min_len] + loopback_pcm[:min_len]
    # tanh soft-clipping: prevents hard clipping distortion while preserving loudness
    return np.tanh(mixed).astype(np.float32)


def to_mono(frames: np.ndarray) -> np.ndarray:
    """
    Collapse a (N, channels) array to mono (N,) float32.
    No-op if already 1-D.
    """
    if frames.ndim > 1:
        return frames.mean(axis=1).astype(np.float32)
    return frames.astype(np.float32)
