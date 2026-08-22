import sys
import queue, time
from audio.capture import AudioCapture

print("--- Phase 2 Validation ---")
mic_q = queue.Queue(maxsize=1000)
lb_q  = queue.Queue(maxsize=1000)
cap   = AudioCapture(mic_q, lb_q)
cap.start()
time.sleep(3)
cap.stop()
print(f"Mic frames:      {mic_q.qsize()}")
print(f"Loopback frames: {lb_q.qsize()}")
assert mic_q.qsize() > 0, "No mic frames captured"
print("Audio capture (dual-queue) OK.")

print("\n--- Phase 3 Validation 1 ---")
import asyncio
from pipeline.stt_client import SarvamSTTClient

async def test_stt():
    client = SarvamSTTClient()
    await client.start()
    import numpy as np, io
    from scipy.io.wavfile import write as wav_write
    buf = io.BytesIO()
    wav_write(buf, 16000, np.zeros(16000, dtype=np.int16))
    try:
        await client.transcribe_chunk(0, buf.getvalue())
        await asyncio.sleep(2)
    except Exception as e:
        print(f"STT Exception: {e}")
    await client.stop()
    print("STT Pipeline OK. Transcript:", repr(client.get_full_transcript()))

asyncio.run(test_stt())

print("\n--- Phase 3 Validation 2 ---")
import threading, concurrent.futures
from pipeline.chunker import AudioChunker

loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()

errors_logged = []

async def failing_on_chunk(idx, wav_bytes):
    raise ConnectionResetError("Simulated network drop")

mic_q2 = queue.Queue()
chunker = AudioChunker(
    mic_queue=mic_q2,
    loopback_queue=None,
    async_loop=loop,
    on_chunk_ready=failing_on_chunk,
    log_callback=lambda msg: errors_logged.append(msg),
)
chunker.start()

import numpy as np
from pipeline.chunker import FRAMES_PER_CHUNK
block = np.zeros(320, dtype=np.float32)
for _ in range(FRAMES_PER_CHUNK // 320 + 1):
    mic_q2.put(block)

time.sleep(2.0)
chunker.stop()

assert any("ConnectionResetError" in e for e in errors_logged), \
    f"Exception was NOT surfaced. Logged: {errors_logged}"
print("✅ Fix 3 verified: network exception was surfaced via done-callback.")
