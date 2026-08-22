"""
ui/app_window.py — In-Person Physical Meeting Assistant GUI.

Layout:
  ┌─────────────────────────────────────────────────┐
  │  🎙 MOM Generator          [● RECORDING...]     │
  ├─────────────────────────────────────────────────┤
  │  Meeting Context / Agenda:                       │
  │  [____________________________________]          │
  │  Override Email Recipients (optional):           │
  │  [____________________________________]          │
  │  Microphone Device:                              │
  │  [____________________________________]          │
  │  [ ▶ Start Recording ]  [ ■ Stop & Generate ]   │
  ├─────────────────────────────────────────────────┤
  │  📊 Live Telemetry                               │
  │  Chunks Processed: 0 | Silence Skipped: 0       │
  │  Last API Latency: — ms                          │
  ├─────────────────────────────────────────────────┤
  │  Live Transcription Log:                         │
  │  ┌──────────────────────────────────────────┐   │
  │  │ [00:00:30] Chunk #0 transcribed           │   │
  │  └──────────────────────────────────────────┘   │
  │  [████████░░░░░░░░] 45s                          │
  ├─────────────────────────────────────────────────┤
  │  Status: ● Idle                                  │
  └─────────────────────────────────────────────────┘
"""

import threading
import queue
import time
import logging
import customtkinter as ctk
from typing import Callable

logger = logging.getLogger(__name__)

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class AppWindow(ctk.CTk):
    """Main application window — In-Person Physical Meeting Assistant."""

    def __init__(
        self,
        on_start: Callable[[str, list[str], str], None],
        on_stop: Callable[[], None],
    ):
        super().__init__()
        self._on_start = on_start
        self._on_stop  = on_stop
        self._is_recording = False
        self._record_start_time: float | None = None
        self._timer_thread: threading.Thread | None = None
        self._log_queue: queue.Queue = queue.Queue()

        # Telemetry state
        self._chunks_processed: int = 0
        self._silence_skipped: int = 0
        self._last_latency_ms: float | None = None

        self._build_ui()
        self._schedule_log_drain()

    # ── UI Construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.title("MOM Generator — In-Person Meeting Assistant")
        self.geometry("700x740")
        self.resizable(False, False)
        self.grid_columnconfigure(0, weight=1)

        # ── Header ────────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="#1a237e", corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header,
            text="🎙  Minutes of Meeting Generator",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="white",
        ).pack(pady=14, padx=20, anchor="w")

        # ── Recording indicator bar ────────────────────────────────────────
        indicator = ctk.CTkFrame(self, fg_color="#f0f4ff")
        indicator.grid(row=1, column=0, sticky="ew")
        self._status_dot = ctk.CTkLabel(
            indicator, text="⚪  Idle — Ready to record",
            font=ctk.CTkFont(size=12), text_color="#4a5568",
        )
        self._status_dot.pack(side="left", padx=20, pady=6)
        self._timer_label = ctk.CTkLabel(
            indicator, text="00:00",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#1a237e",
        )
        self._timer_label.pack(side="right", padx=20)

        # ── Input fields ───────────────────────────────────────────────────
        inputs = ctk.CTkFrame(self, fg_color="white")
        inputs.grid(row=2, column=0, sticky="ew", padx=16, pady=(12, 0))
        inputs.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            inputs, text="Meeting Context / Agenda*",
            font=ctk.CTkFont(size=12, weight="bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 2))
        self._context_entry = ctk.CTkEntry(
            inputs,
            placeholder_text="e.g. Q3 Sprint Planning — Backend Team",
            height=38, font=ctk.CTkFont(size=13),
        )
        self._context_entry.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))

        ctk.CTkLabel(
            inputs, text="Override Email Recipients (optional, comma-separated)",
            font=ctk.CTkFont(size=12, weight="bold"), anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=16, pady=(4, 2))
        self._recipients_entry = ctk.CTkEntry(
            inputs,
            placeholder_text="Leave blank to use .env recipients",
            height=38, font=ctk.CTkFont(size=13),
        )
        self._recipients_entry.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))

        # Microphone selector (loopback dropdown removed)
        from audio.capture import get_mic_devices
        mic_devices = get_mic_devices()

        ctk.CTkLabel(
            inputs, text="Microphone Device",
            font=ctk.CTkFont(size=12, weight="bold"), anchor="w",
        ).grid(row=4, column=0, sticky="w", padx=16, pady=(4, 2))

        self._mic_combo = ctk.CTkComboBox(
            inputs,
            values=mic_devices if mic_devices else ["Default"],
            height=38, font=ctk.CTkFont(size=13),
        )
        self._mic_combo.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 12))
        if mic_devices:
            self._mic_combo.set(mic_devices[0])
        else:
            self._mic_combo.set("Default")

        # ── Action buttons ─────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="#f7f8ff")
        btn_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=10)
        btn_frame.grid_columnconfigure((0, 1), weight=1)

        self._start_btn = ctk.CTkButton(
            btn_frame, text="▶  Start Recording", height=44,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#2e7d32", hover_color="#1b5e20",
            command=self._handle_start,
        )
        self._start_btn.grid(row=0, column=0, padx=(12, 6), pady=10, sticky="ew")

        self._stop_btn = ctk.CTkButton(
            btn_frame, text="■  Stop & Generate MOM", height=44,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#c62828", hover_color="#b71c1c",
            state="disabled",
            command=self._handle_stop,
        )
        self._stop_btn.grid(row=0, column=1, padx=(6, 12), pady=10, sticky="ew")

        # ── Live Telemetry frame ───────────────────────────────────────────
        telemetry_frame = ctk.CTkFrame(
            self, fg_color="#eef2ff", corner_radius=10,
            border_width=1, border_color="#c7d2fe",
        )
        telemetry_frame.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 8))
        telemetry_frame.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkLabel(
            telemetry_frame,
            text="📊  Live Telemetry",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#3730a3",
            anchor="w",
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=14, pady=(10, 4))

        # Chunks Processed
        ctk.CTkLabel(
            telemetry_frame,
            text="Chunks Processed",
            font=ctk.CTkFont(size=11),
            text_color="#6b7280",
            anchor="center",
        ).grid(row=1, column=0, padx=10, pady=(0, 2), sticky="ew")

        self._chunks_label = ctk.CTkLabel(
            telemetry_frame,
            text="0",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#1e40af",
            anchor="center",
        )
        self._chunks_label.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")

        # Divider
        ctk.CTkFrame(
            telemetry_frame, fg_color="#c7d2fe", width=1,
        ).grid(row=1, column=1, rowspan=2, sticky="ns", pady=6)

        # Silence Skipped
        ctk.CTkLabel(
            telemetry_frame,
            text="Silence Skipped",
            font=ctk.CTkFont(size=11),
            text_color="#6b7280",
            anchor="center",
        ).grid(row=1, column=1, padx=10, pady=(0, 2), sticky="ew")

        self._silence_label = ctk.CTkLabel(
            telemetry_frame,
            text="0",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#d97706",
            anchor="center",
        )
        self._silence_label.grid(row=2, column=1, padx=10, pady=(0, 10), sticky="ew")

        # Last API Latency
        ctk.CTkLabel(
            telemetry_frame,
            text="Last API Latency",
            font=ctk.CTkFont(size=11),
            text_color="#6b7280",
            anchor="center",
        ).grid(row=1, column=2, padx=10, pady=(0, 2), sticky="ew")

        self._latency_label = ctk.CTkLabel(
            telemetry_frame,
            text="— ms",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#065f46",
            anchor="center",
        )
        self._latency_label.grid(row=2, column=2, padx=10, pady=(0, 10), sticky="ew")

        # ── Transcription log ──────────────────────────────────────────────
        log_frame = ctk.CTkFrame(self, fg_color="white")
        log_frame.grid(row=5, column=0, sticky="nsew", padx=16, pady=(4, 0))
        log_frame.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(
            log_frame, text="Live Transcription Log",
            font=ctk.CTkFont(size=12, weight="bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self._log_textbox = ctk.CTkTextbox(
            log_frame, height=160,
            font=ctk.CTkFont(size=11, family="Courier"),
            fg_color="#1e1e2e", text_color="#cdd6f4", wrap="word",
        )
        self._log_textbox.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        log_frame.grid_rowconfigure(1, weight=1)

        # ── Progress bar ───────────────────────────────────────────────────
        self._progress = ctk.CTkProgressBar(self, height=8)
        self._progress.grid(row=6, column=0, sticky="ew", padx=16, pady=(4, 0))
        self._progress.set(0)

        # ── Footer status ──────────────────────────────────────────────────
        footer = ctk.CTkFrame(self, fg_color="#f0f4ff", corner_radius=0)
        footer.grid(row=7, column=0, sticky="ew", pady=(4, 0))
        self._footer_label = ctk.CTkLabel(
            footer, text="Ready. Enter meeting context and press Start.",
            font=ctk.CTkFont(size=11), text_color="#718096",
        )
        self._footer_label.pack(pady=6, padx=16, anchor="w")

    # ── Button Handlers ────────────────────────────────────────────────────

    def _handle_start(self) -> None:
        context = self._context_entry.get().strip()
        if not context:
            self._log_message("⚠️  Please enter a Meeting Context before starting.")
            self._context_entry.focus()
            return

        recipients_raw = self._recipients_entry.get().strip()
        override_recipients = (
            [r.strip() for r in recipients_raw.split(",") if r.strip()]
            if recipients_raw else []
        )

        # Reset telemetry counters for the new session
        self._chunks_processed = 0
        self._silence_skipped  = 0
        self._last_latency_ms  = None
        self._refresh_telemetry_labels()

        self._is_recording = True
        self._record_start_time = time.time()
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._context_entry.configure(state="disabled")
        self._recipients_entry.configure(state="disabled")
        self._status_dot.configure(text="🔴  Recording...", text_color="#c62828")
        self._footer_label.configure(text="Recording in progress. Sarvam AI is transcribing...")
        self._log_message("🎙 Recording started. Physical room audio is being captured.")

        self._timer_thread = threading.Thread(
            target=self._update_timer, daemon=True
        )
        self._timer_thread.start()

        mic_device = self._mic_combo.get()

        threading.Thread(
            target=self._on_start,
            args=(context, override_recipients, mic_device),
            daemon=True,
        ).start()

    def _handle_stop(self) -> None:
        """
        Handle Stop button click.

        Spins self._on_stop() off into a daemon thread so the UI thread
        returns immediately and remains fully responsive during the stop pipeline.
        All UI updates from the orchestrator flow back via app.after(0, ...).
        """
        if not self._is_recording:
            return

        self._is_recording = False
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="disabled")
        self._status_dot.configure(text="⏳  Processing...", text_color="#d97706")
        self._footer_label.configure(
            text="Compiling transcript → Gemini → Formatting → Dispatching..."
        )
        self._log_message("■ Stop requested. Compiling transcript and generating MOM...")

        threading.Thread(
            target=self._on_stop,
            daemon=True,
            name="StopPipelineThread",
        ).start()

    # ── Public Methods (called from orchestrator via thread-safe dispatch) ─

    def log(self, message: str) -> None:
        """Thread-safe log. Can be called from any thread."""
        self._log_queue.put(message)

    def set_progress(self, value: float) -> None:
        """Update progress bar (0.0–1.0). Thread-safe."""
        self.after(0, lambda: self._progress.set(value))

    def update_telemetry(self, data: dict) -> None:
        """
        Thread-safe telemetry update. Called from orchestrator/worker threads.

        Accepted keys:
          - chunks_processed  (int)
          - silence_skipped   (int)
          - last_api_latency_ms (float)
        """
        if "chunks_processed" in data:
            self._chunks_processed = data["chunks_processed"]
        if "silence_skipped" in data:
            self._silence_skipped = data["silence_skipped"]
        if "last_api_latency_ms" in data:
            self._last_latency_ms = data["last_api_latency_ms"]
        # Schedule label refresh on the main thread
        self.after(0, self._refresh_telemetry_labels)

    def show_success(self) -> None:
        """Update UI to reflect successful MOM dispatch."""
        def _update():
            self._status_dot.configure(text="✅  MOM Sent!", text_color="#2e7d32")
            self._footer_label.configure(
                text="MOM generated and dispatched successfully! You may start a new recording."
            )
            self._start_btn.configure(state="normal")
            self._context_entry.configure(state="normal")
            self._recipients_entry.configure(state="normal")
            self._progress.set(1.0)
        self.after(0, _update)

    def show_error(self, message: str) -> None:
        """Update UI to reflect an error state."""
        def _update():
            self._status_dot.configure(text="❌  Error", text_color="#c62828")
            self._footer_label.configure(text=f"Error: {message}")
            self._start_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")
            self._context_entry.configure(state="normal")
            self._recipients_entry.configure(state="normal")
        self.after(0, _update)

    # ── Internal Helpers ───────────────────────────────────────────────────

    def _refresh_telemetry_labels(self) -> None:
        """Update the Live Telemetry frame labels. Must run on the main thread."""
        self._chunks_label.configure(text=str(self._chunks_processed))
        self._silence_label.configure(text=str(self._silence_skipped))
        if self._last_latency_ms is not None:
            self._latency_label.configure(text=f"{self._last_latency_ms:.0f} ms")
        else:
            self._latency_label.configure(text="— ms")

    def _log_message(self, message: str) -> None:
        """Write to log textbox. Must be called from the main thread."""
        timestamp = time.strftime("%H:%M:%S")
        self._log_textbox.insert("end", f"[{timestamp}] {message}\n")
        self._log_textbox.see("end")

    def _schedule_log_drain(self) -> None:
        """Drain thread-safe log queue into the textbox every 100ms."""
        while not self._log_queue.empty():
            try:
                self._log_message(self._log_queue.get_nowait())
            except queue.Empty:
                break
        self.after(100, self._schedule_log_drain)

    def _update_timer(self) -> None:
        """Update elapsed time label every second while recording."""
        while self._is_recording and self._record_start_time:
            elapsed = int(time.time() - self._record_start_time)
            mm, ss  = divmod(elapsed, 60)
            self.after(0, lambda t=f"{mm:02d}:{ss:02d}": self._timer_label.configure(text=t))
            self.set_progress(min(0.95, (elapsed % 60) / 60))
            time.sleep(1)
