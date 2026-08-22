"""
app/api/v1/websocket_audio.py

WebSocket audio ingestion pipeline — Phase 4, Step 4.4.

Full data flow (verbatim from PLAN_WEB_SAAS.md with production hardening):

  1. Client calls POST /api/v1/meetings/{id}/ws-ticket (authenticated REST,
     Bearer token) → receives a 30-second single-use ticket.
  2. Client opens  wss://.../ws/meetings/{id}/audio?ticket=<ticket>
  3. Server calls consume_ws_ticket() — validates + immediately burns it.
     If invalid/expired/already used → close(code=4001, Unauthorized).
     If meeting_id in ticket ≠ meeting_id in path → close(code=4001).
  4. Client's MediaRecorder fires `ondataavailable` every ~5s, sending each
     WebM/Opus Blob as a binary WS frame.
  5. Server decodes WebM/Opus → mono 16kHz float32 PCM (via ffmpeg subprocess).
     ffmpeg must be installed in the backend Docker image (apt-get install ffmpeg).
  6. VAD: if RMS energy below threshold → chunk is silent, skip STT, continue.
  7. Non-silent: convert float32 PCM → int16 WAV bytes, dispatch as an
     independent asyncio.Task with a done-callback that:
       (a) appends the transcription to the Redis buffer in order
       (b) sends a partial_transcript push to the client for live display
       (c) surfaces any exception via an stt_error push (never silent)
     The Semaphore (MAX_CONCURRENT_STT=5) bounds the number of in-flight STT
     calls to prevent runaway aiohttp connection exhaustion under burst load.
  8. Client sends text frame {"type":"stop"} when the user clicks Stop.
  9. Server awaits all in-flight STT futures (bounded 30s wait) to ensure
     no transcription is lost before handing off to Celery.
 10. Raw transcript assembled from Redis → persisted to DB (status=processing).
 11. generate_mom_task.delay(meeting_id, org_id) enqueued → Celery picks it up.
 12. Redis transcript buffer is cleaned up.
 13. WebSocket is closed cleanly.

Exception discipline
--------------------
  The on_chunk_done() callback pattern was introduced in the desktop app's
  "Fix 3" to prevent asyncio.Task exceptions from being silently swallowed.
  A Task whose result is never retrieved silently drops its exception — the
  done-callback forces retrieval and surfaces it to the client.
"""

import asyncio
import json
import logging
import struct
import subprocess
import wave
from io import BytesIO

import aiohttp
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import consume_ws_ticket
from app.db import set_meeting_status
from app.services.stt_service import transcribe_chunk
from app.services.vad import is_silent
from app.workers.tasks_mom import generate_mom_task
from app.ws_manager import ConnectionContext, ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_CONCURRENT_STT = 5
SAMPLE_RATE = 16_000  # Hz — mono 16kHz expected from ffmpeg decode


# ── Audio decoding helpers ─────────────────────────────────────────────────────

def _decode_webm_opus_to_pcm(blob: bytes, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """
    Decode a WebM/Opus blob to mono float32 PCM via an ffmpeg subprocess.

    ffmpeg reads from stdin (pipe:0) and writes raw float32-LE PCM to stdout
    (pipe:1). Using pipe:0/pipe:1 avoids writing temp files and is safe for
    concurrent requests.

    Raises subprocess.CalledProcessError if ffmpeg exits non-zero (e.g.
    corrupted or empty blob). The caller should treat this as a non-fatal
    chunk error and log it — not crash the WS connection.

    Args:
        blob:        Raw WebM/Opus bytes from the browser's MediaRecorder.
        sample_rate: Target sample rate for downsampling. Default 16kHz
                     (required by Sarvam AI saaras:v3).

    Returns:
        mono float32 numpy array at `sample_rate` Hz.
    """
    logger.debug("Decoding chunk: len=%d header=%s", len(blob), blob[:16].hex())
    proc = subprocess.run(
        [
            "ffmpeg",
            "-i", "pipe:0",          # input from stdin
            "-f", "f32le",           # output format: raw float32 little-endian
            "-ac", "1",              # downmix to mono
            "-ar", str(sample_rate), # resample to target rate
            "pipe:1",                # output to stdout
            "-loglevel", "error",    # suppress ffmpeg's verbose output
        ],
        input=blob,
        capture_output=True,
        check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _pcm_to_wav_bytes(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """
    Encode a mono float32 PCM array into a standard 16-bit WAV container.

    Sarvam AI's STT API expects WAV format. We convert float32 → int16 to
    produce a compact, standard WAV file. The conversion clamps to [-1.0, 1.0]
    before scaling to prevent int16 overflow from clipped audio.

    Args:
        pcm:         Mono float32 PCM array.
        sample_rate: Sample rate in Hz (must match the Sarvam AI expectation).

    Returns:
        WAV-encoded bytes ready to POST to the STT endpoint.
    """
    # Clamp then convert: float32 [-1,1] → int16 [-32768, 32767]
    pcm_clamped = np.clip(pcm, -1.0, 1.0)
    pcm_int16 = (pcm_clamped * 32767).astype(np.int16)

    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)               # mono
        wf.setsampwidth(2)               # 16-bit = 2 bytes per sample
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_int16.tobytes())
    return buf.getvalue()


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

@router.websocket("/ws/meetings/{meeting_id}/audio")
async def audio_stream(websocket: WebSocket, meeting_id: str, ticket: str) -> None:
    """
    Accept, process, and finalise an audio stream for a meeting.

    The entire handler is one long try/finally — the finally block
    runs unconditionally whether the client sent 'stop', disconnected
    abruptly, or a server error occurred. This guarantees:
      - All in-flight STT tasks are waited for (within a 30s timeout)
      - The DB status is updated to 'processing' (or 'failed')
      - Celery is always notified (if we have any transcript at all)
      - The WebSocket is always closed cleanly
    """
    # ── Step 3: ticket validation ────────────────────────────────────────────
    auth = await consume_ws_ticket(ticket)
    if auth is None:
        logger.warning("WS rejected: invalid/expired ticket for meeting=%s", meeting_id)
        await websocket.close(code=4001)
        return

    user_id, org_id, ticket_meeting_id = auth
    if ticket_meeting_id != meeting_id:
        logger.warning(
            "WS rejected: ticket meeting_id=%s ≠ path meeting_id=%s",
            ticket_meeting_id, meeting_id,
        )
        await websocket.close(code=4001)
        return

    await websocket.accept()
    logger.info(
        "WS accepted meeting=%s org=%s user=%s", meeting_id, org_id, user_id
    )

    ctx = ConnectionContext(
        meeting_id=meeting_id,
        org_id=org_id,
        user_id=user_id,
    )
    ws_manager.register(ctx)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_STT)
    http_session = aiohttp.ClientSession()

    # ── Inner helpers (closures over local variables) ──────────────────────

    async def handle_chunk(pcm: np.ndarray, idx: int) -> None:
        """
        STT dispatch: convert PCM → WAV → Sarvam AI → Redis buffer → push to client.
        Runs as an independent asyncio.Task so multiple chunks can be in-flight
        simultaneously (up to MAX_CONCURRENT_STT).
        """
        async with semaphore:
            wav_bytes = _pcm_to_wav_bytes(pcm)
            text = await transcribe_chunk(http_session, wav_bytes, idx)
            await ws_manager.append_transcript_chunk(meeting_id, idx, text)
            # Push partial transcript to frontend for live display
            if text.strip():
                try:
                    await websocket.send_json({
                        "type": "partial_transcript",
                        "chunk": idx,
                        "text": text,
                    })
                except Exception:
                    # WS may already be closing; don't let this crash the task
                    pass

    def on_chunk_done(future: asyncio.Task) -> None:
        """
        Done-callback attached to every STT task.

        A Task whose result is never retrieved silently drops its exception.
        This callback forces retrieval and surfaces any STT error to the client
        via a 'stt_error' push message — continuing the exception-surfacing
        discipline from the desktop app's Fix 3.
        """
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            logger.error(
                "STT task failed for meeting=%s: %s", meeting_id, exc
            )
            try:
                asyncio.create_task(
                    websocket.send_json({
                        "type": "stt_error",
                        "message": str(exc),
                    })
                )
            except Exception:
                pass  # WS already closed

    # ── Steps 4–8: main receive loop ──────────────────────────────────────

    stop_received = False
    try:
        while True:
            message = await websocket.receive()

            # ── Binary frame: audio chunk ────────────────────────────────
            if message.get("bytes") is not None:
                blob = message["bytes"]
                try:
                    pcm = _decode_webm_opus_to_pcm(blob)
                except subprocess.CalledProcessError as exc:
                    stderr_text = exc.stderr.decode(errors="replace") if exc.stderr else "(no stderr captured)"
                    logger.warning(
                        "ffmpeg decode failed for meeting=%s chunk=%d: %s",
                        meeting_id, ctx.chunk_index, stderr_text,
                    )
                    continue  # skip this chunk, keep the connection alive

                # VAD: drop silent sub-chunks to save STT API calls
                if is_silent(pcm):
                    logger.debug(
                        "VAD: silent chunk dropped meeting=%s idx=%d",
                        meeting_id, ctx.chunk_index,
                    )
                    ctx.chunk_index += 1  # keep counter consistent for ordering
                    continue

                idx = ctx.chunk_index
                ctx.chunk_index += 1
                task = asyncio.create_task(handle_chunk(pcm, idx))
                task.add_done_callback(on_chunk_done)
                ctx.in_flight_futures.append(task)

            # ── Text frame: control message ──────────────────────────────
            elif message.get("text") is not None:
                try:
                    control = json.loads(message["text"])
                except json.JSONDecodeError:
                    logger.warning("Non-JSON text frame received for meeting=%s", meeting_id)
                    continue
                if control.get("type") == "stop":
                    logger.info("Stop signal received for meeting=%s", meeting_id)
                    stop_received = True
                    break

    except WebSocketDisconnect:
        logger.info("WS disconnected (client side) for meeting=%s", meeting_id)

    except Exception as exc:
        logger.error("Unexpected WS error for meeting=%s: %s", meeting_id, exc, exc_info=True)

    finally:
        # ── Steps 9–13: flush, persist, enqueue, clean up ─────────────────

        # Step 9: bounded wait for all in-flight STT tasks
        if ctx.in_flight_futures:
            logger.info(
                "Waiting for %d in-flight STT tasks for meeting=%s",
                len(ctx.in_flight_futures), meeting_id,
            )
            done, pending = await asyncio.wait(
                ctx.in_flight_futures, timeout=30
            )
            if pending:
                logger.warning(
                    "%d STT tasks did not finish within 30s for meeting=%s — cancelling",
                    len(pending), meeting_id,
                )
                for task in pending:
                    task.cancel()

        await http_session.close()

        # Step 10: assemble transcript and persist to DB
        transcript = await ws_manager.get_ordered_transcript(meeting_id)
        logger.info(
            "Meeting=%s transcript assembled: %d chars, %d chunks",
            meeting_id, len(transcript), ctx.chunk_index,
        )
        logger.info("Meeting=%s transcript text: %r", meeting_id, transcript)

        try:
            await set_meeting_status(
                meeting_id,
                status="processing",
                raw_transcript=transcript,
            )
        except Exception as exc:
            logger.error(
                "Failed to persist transcript for meeting=%s: %s",
                meeting_id, exc, exc_info=True,
            )

        # Step 11: enqueue MOM generation in Celery
        try:
            generate_mom_task.delay(meeting_id=meeting_id, org_id=org_id)
            logger.info(
                "Enqueued generate_mom_task for meeting=%s org=%s",
                meeting_id, org_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to enqueue generate_mom_task for meeting=%s: %s",
                meeting_id, exc, exc_info=True,
            )

        # Step 12: clean up the Redis transcript buffer
        try:
            await ws_manager.cleanup_transcript_buffer(meeting_id)
        except Exception:
            pass  # non-critical; 24h TTL will clean it up regardless

        # Step 13: unregister and close
        ws_manager.unregister(meeting_id)
        try:
            await websocket.close()
        except Exception:
            pass  # already closed by the client
