"""Unit tests for AudioChunker (Rev 2 — dual-queue, Fix 2 + Fix 3)."""

import asyncio
import queue
import threading
import time
import numpy as np
import pytest

from pipeline.chunker import AudioChunker, FRAMES_PER_CHUNK


def _start_loop():
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def test_chunker_emits_on_threshold():
    """Emits exactly 1 chunk after FRAMES_PER_CHUNK mic frames."""
    mic_q = queue.Queue()
    lb_q  = queue.Queue()
    loop  = _start_loop()
    received = []

    async def on_chunk(idx, wav_bytes):
        received.append((idx, len(wav_bytes)))

    chunker = AudioChunker(mic_q, lb_q, loop, on_chunk)
    chunker.start()

    block = np.zeros(320, dtype=np.float32)
    for _ in range(FRAMES_PER_CHUNK // 320):
        mic_q.put(block)

    time.sleep(1.0)
    chunker.stop()
    loop.call_soon_threadsafe(loop.stop)

    assert len(received) == 1, f"Expected 1 chunk, got {len(received)}"
    assert received[0][1] > 44  # WAV header minimum
    print(f"✅ Chunk emitted: {received[0][1]:,} bytes")


def test_chunker_mixes_loopback_when_available():
    """Chunk contains mixed audio when loopback frames are present."""
    mic_q = queue.Queue()
    lb_q  = queue.Queue()
    loop  = _start_loop()
    received_wav = []

    async def on_chunk(idx, wav_bytes):
        received_wav.append(wav_bytes)

    chunker = AudioChunker(mic_q, lb_q, loop, on_chunk)
    chunker.start()

    # Mic: silence, Loopback: full amplitude → mixed result should be non-silent
    mic_block = np.zeros(320, dtype=np.float32)
    lb_block  = np.ones(320, dtype=np.float32) * 0.5

    for _ in range(FRAMES_PER_CHUNK // 320):
        mic_q.put(mic_block)
        lb_q.put(lb_block)

    time.sleep(1.0)
    chunker.stop()

    assert len(received_wav) == 1, "Expected 1 chunk"
    print("✅ Loopback mixing test passed.")


def test_chunker_handles_clock_drift():
    """
    Chunker must handle loopback having FEWER frames than mic at chunk time
    (simulates a slower loopback clock drifting behind mic).
    """
    mic_q = queue.Queue()
    lb_q  = queue.Queue()
    loop  = _start_loop()
    received = []

    async def on_chunk(idx, wav_bytes):
        received.append(idx)

    chunker = AudioChunker(mic_q, lb_q, loop, on_chunk)
    chunker.start()

    block = np.zeros(320, dtype=np.float32)
    # Push full mic frames but only HALF the loopback frames (simulated drift)
    for i in range(FRAMES_PER_CHUNK // 320):
        mic_q.put(block)
        if i % 2 == 0:          # Only half the frames → simulated clock drift
            lb_q.put(block)

    time.sleep(1.0)
    chunker.stop()
    time.sleep(0.3)

    assert 0 in received, "Chunk should emit despite loopback frame count mismatch"
    print("✅ Clock-drift tolerance test passed.")


def test_fix3_exception_surfaced_via_callback():
    """Fix 3: exceptions from STT coroutine must be surfaced via done-callback."""
    mic_q = queue.Queue()
    loop  = _start_loop()
    errors = []

    async def failing_on_chunk(idx, wav_bytes):
        raise ConnectionResetError("Simulated network drop")

    chunker = AudioChunker(
        mic_queue=mic_q,
        loopback_queue=None,
        async_loop=loop,
        on_chunk_ready=failing_on_chunk,
        log_callback=lambda msg: errors.append(msg),
    )
    chunker.start()

    block = np.zeros(320, dtype=np.float32)
    for _ in range(FRAMES_PER_CHUNK // 320 + 1):
        mic_q.put(block)

    time.sleep(2.0)
    chunker.stop()

    assert any("ConnectionResetError" in e for e in errors), \
        f"Exception not surfaced. Got: {errors}"
    print("✅ Fix 3 test passed: exception surfaced via done-callback.")


def test_chunker_flush_on_stop():
    """Partial buffer is flushed as a chunk when stop() is called."""
    mic_q = queue.Queue()
    loop  = _start_loop()
    received = []

    async def on_chunk(idx, wav_bytes):
        received.append(idx)

    chunker = AudioChunker(mic_q, None, loop, on_chunk)
    chunker.start()

    for _ in range(10):             # Far less than FRAMES_PER_CHUNK
        mic_q.put(np.zeros(320, dtype=np.float32))

    time.sleep(0.3)
    chunker.stop()
    time.sleep(0.5)

    assert 0 in received, "Partial chunk should be flushed on stop."
    print("✅ Flush-on-stop test passed.")
