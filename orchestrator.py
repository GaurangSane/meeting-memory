"""
orchestrator.py — Single-stream In-Person Physical Meeting Assistant.

Creates a single mic_queue and passes it to both AudioCapture and
AudioChunker. The dual-queue / loopback architecture has been removed.

Telemetry flow:
  - AudioChunker pushes {chunks_processed, silence_skipped} via telemetry_callback.
  - SarvamSTTClient pushes last API latency (ms) via latency_callback.
  - Both callbacks forward data to AppWindow.update_telemetry(), which is
    thread-safe via tkinter's after(0, ...) dispatch.

The stop() method may block for up to 120 seconds waiting for STT tasks.
This is safe because _handle_stop() in the UI calls stop() in a daemon
thread, so the UI main thread is never blocked.
"""

import asyncio
import threading
import logging
import queue
from typing import TYPE_CHECKING

from audio.capture import AudioCapture
from pipeline.chunker import AudioChunker
from pipeline.stt_client import SarvamSTTClient
from nlp.gemini_client import GeminiMOMClient
from output.html_formatter import HTMLFormatter
from output.email_sender import EmailSender
from notifications.whatsapp_alert import WhatsAppAlert
from config.settings import EMAIL_RECIPIENTS

if TYPE_CHECKING:
    from ui.app_window import AppWindow

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Lifecycle:
      1. __init__: instantiate all modules; start async event loop thread.
      2. start(context, recipients): begin audio capture + STT pipeline.
      3. stop(): stop capture; flush; run Gemini; format; dispatch.
    """

    def __init__(self, ui: "AppWindow"):
        self._ui = ui

        # ── Async event loop in a background daemon thread ─────────────────
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="AsyncEventLoopThread",
        )
        self._loop_thread.start()

        # ── Single mic queue (single-stream, in-person mode) ───────────────
        self._mic_queue = queue.Queue(maxsize=2000)

        # AudioCapture pushes mic frames to the single queue
        self._capture = AudioCapture(mic_queue=self._mic_queue)

        self._stt_client = SarvamSTTClient()
        self._stt_client.set_log_callback(self._ui.log)
        self._stt_client.set_latency_callback(self._on_latency_update)

        # AudioChunker drains mic_queue; VAD filters silence; telemetry forwarded to UI
        self._chunker = AudioChunker(
            mic_queue=self._mic_queue,
            async_loop=self._loop,
            on_chunk_ready=self._stt_client.transcribe_chunk,
            log_callback=self._ui.log,
            telemetry_callback=self._on_telemetry_update,
        )

        self._gemini    = GeminiMOMClient()
        self._formatter = HTMLFormatter()
        self._email     = EmailSender()
        self._whatsapp  = WhatsAppAlert()

        self._meeting_context: str = ""
        self._override_recipients: list[str] = []

        logger.info("[Orchestrator] Initialised (single-stream mic pipeline).")

    # ── Async event loop ───────────────────────────────────────────────────

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self, meeting_context: str, override_recipients: list[str], mic_device: str | None = None) -> None:
        """
        Begin recording session.
        Called from a daemon thread (never blocks the UI thread).
        """
        self._meeting_context     = meeting_context
        self._override_recipients = override_recipients
        self._stt_client.clear()

        asyncio.run_coroutine_threadsafe(
            self._stt_client.start(), self._loop
        ).result(timeout=10)

        self._capture.start(mic_device)
        self._chunker.start()

        logger.info(f"[Orchestrator] Session started. Context: '{meeting_context}'")
        self._ui.log("🎙 Single-stream mic capture active. Sarvam AI STT pipeline running.")

    def stop(self) -> None:
        """
        Stop recording and run the full MOM generation pipeline.

        This method BLOCKS (up to ~2 minutes waiting for STT tasks).
        It is always called from a daemon thread — never the UI main thread —
        so blocking here is safe.
        """
        logger.info("[Orchestrator] Stop signal received.")

        # 1. Stop audio capture
        self._capture.stop()
        self._ui.log("⏹ Audio capture stopped.")

        # 2. Stop chunker — flushes final partial chunk
        self._chunker.stop()
        self._ui.log("📦 Final audio chunk flushed.")

        # 3. Wait for all in-flight STT tasks
        self._ui.log("⏳ Waiting for all STT tasks to complete...")
        asyncio.run_coroutine_threadsafe(
            self._wait_for_stt(), self._loop
        ).result(timeout=120)

        # 4. Retrieve full ordered transcript
        transcript = self._stt_client.get_full_transcript()
        self._ui.log(f"📝 Transcript assembled ({len(transcript.split())} words).")
        logger.info(f"[Orchestrator] Transcript: {len(transcript)} chars")

        # 5. Close STT session
        asyncio.run_coroutine_threadsafe(
            self._stt_client.stop(), self._loop
        ).result(timeout=10)

        # 6. Gemini MOM extraction
        self._ui.log("🧠 Sending transcript to Gemini for MOM extraction...")
        self._ui.set_progress(0.4)
        try:
            mom_data = self._gemini.extract_mom(transcript, self._meeting_context)
            self._ui.log(
                f"✅ MOM extracted: "
                f"{len(mom_data.get('action_items', []))} action items, "
                f"{len(mom_data.get('key_decisions', []))} decisions."
            )
        except Exception as e:
            logger.error(f"[Orchestrator] Gemini error: {e}")
            self._ui.log(f"❌ Gemini error: {e}")
            self._ui.show_error(str(e))
            return

        # 7. HTML formatting
        self._ui.set_progress(0.65)
        self._ui.log("🎨 Rendering HTML email...")
        try:
            html_body = self._formatter.render(mom_data)
        except Exception as e:
            logger.error(f"[Orchestrator] Formatting error: {e}")
            self._ui.log(f"❌ Formatting error: {e}")
            self._ui.show_error(str(e))
            return

        # 8. Email dispatch
        self._ui.set_progress(0.80)
        recipients = self._override_recipients or EMAIL_RECIPIENTS
        self._ui.log(f"📧 Sending email to {len(recipients)} recipient(s)...")
        email_ok = self._email.send(mom_data, html_body)
        self._ui.log("✅ Email sent." if email_ok else "⚠️ Email failed — check SMTP config.")

        # 9. WhatsApp alert
        self._ui.set_progress(0.90)
        self._ui.log("📱 Sending WhatsApp alert via Twilio...")
        wa_results  = self._whatsapp.send_alert(mom_data)
        sent_count  = sum(wa_results)
        self._ui.log(f"✅ WhatsApp sent to {sent_count}/{len(wa_results)} recipient(s).")

        # 10. Done
        self._ui.set_progress(1.0)
        self._ui.log("🎉 MOM generation complete!")
        self._ui.show_success()
        logger.info("[Orchestrator] Session complete.")

    # ── Telemetry callbacks (called from worker threads) ───────────────────

    def _on_telemetry_update(self, data: dict) -> None:
        """Forwarded from AudioChunker. Thread-safe via AppWindow.update_telemetry()."""
        self._ui.update_telemetry(data)

    def _on_latency_update(self, latency_ms: float) -> None:
        """Forwarded from SarvamSTTClient. Thread-safe via AppWindow.update_telemetry()."""
        self._ui.update_telemetry({"last_api_latency_ms": latency_ms})

    # ── Async helpers ──────────────────────────────────────────────────────

    async def _wait_for_stt(self) -> None:
        """
        Wait for all STT coroutine tasks to finish.
        Polls the semaphore: when all slots are free, all tasks have resolved.
        Includes a 2-second grace period for late-dispatched chunks.
        """
        from pipeline.stt_client import MAX_CONCURRENT_REQUESTS
        await asyncio.sleep(2.0)
        max_wait = 90
        elapsed  = 0.0
        while elapsed < max_wait:
            if self._stt_client._semaphore._value == MAX_CONCURRENT_REQUESTS:
                logger.debug("[Orchestrator] All STT tasks resolved.")
                return
            await asyncio.sleep(0.5)
            elapsed += 0.5
        logger.warning("[Orchestrator] STT wait timed out — proceeding with available transcripts.")
