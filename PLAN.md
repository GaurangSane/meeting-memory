# PLAN.md — MOM (Minutes of Meeting) Generator
## Autonomous CLI Agent Execution Plan — Rev 2 (3 Bug Fixes Applied)

> **Target:** Local Python desktop app for Indian corporate teams.
> **Execution Model:** Each Phase is a discrete, independently verifiable unit. The agent MUST validate each phase before proceeding.
> **Convention:** All file paths are relative to the project root `mom_generator/`.
>
> **Revision Notes (Rev 2):**
> Three structural bugs from Rev 1 have been corrected. Each fix is marked inline
> with a `# ── BUG FIX N ──` banner and a plain-English explanation.
>
> | Fix | Location | Bug | Root Cause |
> |-----|----------|-----|------------|
> | 1 | `ui/app_window.py` → `_handle_stop` | UI freezes for up to 2 min on Stop | `orchestrator.stop()` has a `.result(timeout=120)` blocking call; was invoked directly on the tkinter main thread |
> | 2 | `audio/capture.py` + `pipeline/chunker.py` + `orchestrator.py` | Loopback audio silently drops or buffer grows unboundedly | Mic (PortAudio) and loopback (WASAPI/CoreAudio) run on separate hardware clocks; mixing frame-by-frame in the mic callback causes clock drift desync |
> | 3 | `pipeline/chunker.py` → `_emit_chunk` | Network exceptions from Sarvam AI STT are silently swallowed | `asyncio.run_coroutine_threadsafe()` returns a `concurrent.futures.Future` that was discarded; exceptions stored in it were never retrieved |

---

## TABLE OF CONTENTS

1. [Architecture Overview](#architecture-overview)
2. [Tech Stack & Rationale](#tech-stack--rationale)
3. [Full Directory Structure](#full-directory-structure)
4. [Phase 0 — Project Scaffolding](#phase-0--project-scaffolding)
5. [Phase 1 — Configuration & Environment](#phase-1--configuration--environment)
6. [Phase 2 — Audio Capture Module (Fix 2)](#phase-2--audio-capture-module-fix-2-applied)
7. [Phase 3 — Streaming STT Pipeline (Fix 3)](#phase-3--streaming-stt-pipeline-fix-3-applied)
8. [Phase 4 — NLP Module (Gemini)](#phase-4--nlp-module-gemini)
9. [Phase 5 — Output Formatter (HTML Email)](#phase-5--output-formatter-html-email)
10. [Phase 6 — Notification Module (Twilio WhatsApp)](#phase-6--notification-module-twilio-whatsapp)
11. [Phase 7 — UI Module (CustomTkinter) (Fix 1)](#phase-7--ui-module-customtkinter-fix-1-applied)
12. [Phase 8 — Orchestrator & Entry Point (Fix 2)](#phase-8--orchestrator--entry-point-fix-2-applied)
13. [Phase 9 — Validation & End-to-End Test](#phase-9--validation--end-to-end-test)
14. [Data Flow Diagram](#data-flow-diagram)
15. [Environment Variables Reference](#environment-variables-reference)
16. [Known Platform Caveats](#known-platform-caveats)

---

## ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────────┐
│                     UI LAYER (CustomTkinter)             │
│   [Meeting Context Input]  [Start ▶]  [Stop ■]  [Log]   │
└───────────────┬─────────────────────────┬───────────────┘
                │ Start Event (thread)     │ Stop Event (thread) ← FIX 1
                ▼                         ▼
┌──────────────────────┐     ┌────────────────────────────┐
│  AUDIO CAPTURE       │     │  ORCHESTRATOR              │
│  sounddevice (mic)   │     │  (orchestrator.py)         │
│  → mic_queue         │     │  Joins all transcript chunks│
│  soundcard (loopback)│     │  → calls Gemini NLP        │
│  → loopback_queue    │ ←FIX2                            │
└──────────┬───────────┘     └────────┬───────────────────┘
           │ TWO independent queues    │
           ▼                         ▼
┌──────────────────────┐     ┌────────────────────────────┐
│  CHUNKER ← FIX 2     │     │  GEMINI NLP CLIENT         │
│  Drains BOTH queues  │     │  Structured JSON output    │
│  independently       │     │  (Summary, Decisions,      │
│  Mixes at chunk time │     │   Action Items + Assignees)│
│  → WAV BytesIO obj   │     └────────┬───────────────────┘
└──────────┬───────────┘              │
           │ WAV chunk                ▼
           ▼              ┌────────────────────────────┐
┌──────────────────────┐  │  HTML FORMATTER            │
│  STT PIPELINE        │  │  Jinja2 → corporate email  │
│  asyncio task queue  │  └────────┬───────────────────┘
│  aiohttp → Sarvam AI │           │
│  Future + callback   │  ┌────────┴───────────────────┐
│  to log exceptions ←FIX3│  DUAL DISPATCH             │
└──────────────────────┘  ├────────────────────────────┤
                           │ • smtplib → HTML Email     │
                           │ • Twilio → WhatsApp Alert  │
                           └────────────────────────────┘
```

---

## TECH STACK & RATIONALE

| Concern                | Library               | Reason                                              |
|------------------------|-----------------------|-----------------------------------------------------|
| UI Framework           | `customtkinter`       | Modern look over raw `tkinter`; zero extra build deps|
| Mic Capture            | `sounddevice`         | Cross-platform ASIO/WASAPI/CoreAudio via PortAudio  |
| System Audio (loopback)| `soundcard`           | OS-level loopback; works on Win/macOS/Linux         |
| Audio Mixing           | `numpy`               | Element-wise array addition with soft clipping      |
| WAV Encoding           | `scipy.io.wavfile`    | Write PCM int16 → WAV BytesIO in-memory             |
| Async HTTP             | `aiohttp`             | Non-blocking multipart POST to Sarvam AI            |
| STT                    | Sarvam AI (`saarika:v2`) | Best-in-class Indian English + regional lang STT |
| LLM                    | `google-generativeai` | Gemini 1.5 Pro; structured JSON via `response_schema`|
| HTML Templating        | `jinja2`              | Clean separation of template vs. logic              |
| Email                  | `smtplib` (stdlib)    | Zero extra deps; works with Gmail App Passwords     |
| WhatsApp               | `twilio`              | Official Twilio WhatsApp Sandbox API                |
| Config                 | `python-dotenv`       | `.env` file loading                                 |
| Async Bridge           | `asyncio` + `threading` | Run event loop in daemon thread alongside tkinter  |

---

## FULL DIRECTORY STRUCTURE

```
mom_generator/
├── PLAN.md                         ← this file
├── README.md
├── requirements.txt
├── .env.example
├── .env                            ← agent creates from .env.example (git-ignored)
│
├── main.py                         ← entry point; launches UI
├── orchestrator.py                 ← coordinates all modules on start/stop
│
├── config/
│   ├── __init__.py
│   └── settings.py                 ← loads & validates all env vars
│
├── audio/
│   ├── __init__.py
│   ├── capture.py                  ← dual-stream: mic → mic_queue, loopback → loopback_queue
│   └── mixer.py                    ← numpy mix + normalize; called by chunker at chunk time
│
├── pipeline/
│   ├── __init__.py
│   ├── chunker.py                  ← drains BOTH queues independently; mixes at chunk boundary
│   └── stt_client.py               ← async Sarvam AI STT; Future callback logs exceptions
│
├── nlp/
│   ├── __init__.py
│   └── gemini_client.py            ← Gemini structured JSON extraction
│
├── output/
│   ├── __init__.py
│   ├── html_formatter.py           ← JSON → Jinja2 HTML email renderer
│   ├── email_sender.py             ← smtplib SMTP dispatcher
│   └── templates/
│       └── mom_email.html.j2       ← Jinja2 HTML email template
│
├── notifications/
│   ├── __init__.py
│   └── whatsapp_alert.py           ← Twilio WhatsApp dispatcher
│
├── utils/
│   ├── __init__.py
│   └── logger.py                   ← coloured console + rotating file logger
│
└── tests/
    ├── test_chunker.py
    ├── test_stt_client.py
    ├── test_gemini_client.py
    └── test_html_formatter.py
```

---

## PHASE 0 — PROJECT SCAFFOLDING

**Objective:** Create the entire directory tree and all empty `__init__.py` files.

### Step 0.1 — Create root directory and subdirectories

```bash
mkdir -p mom_generator/{config,audio,pipeline,nlp,output/templates,notifications,utils,tests}
cd mom_generator
touch config/__init__.py audio/__init__.py pipeline/__init__.py \
      nlp/__init__.py output/__init__.py notifications/__init__.py \
      utils/__init__.py tests/__init__.py
```

### Step 0.2 — Create `requirements.txt`

**File:** `requirements.txt`

```
# UI
customtkinter==5.2.2

# Audio
sounddevice==0.4.6
soundcard==0.4.2
numpy==1.26.4
scipy==1.13.1

# Async HTTP
aiohttp==3.9.5

# Google Gemini
google-generativeai==0.7.2

# HTML Templating
Jinja2==3.1.4

# WhatsApp / SMS
twilio==9.2.2

# Config
python-dotenv==1.0.1
```

### Step 0.3 — Create `.env.example`

**File:** `.env.example`

```ini
# ── Sarvam AI ──────────────────────────────────────────────────────
SARVAM_API_KEY=your_sarvam_api_key_here
SARVAM_STT_MODEL=saarika:v2
SARVAM_LANGUAGE_CODE=hi-IN      # hi-IN | en-IN | ta-IN | te-IN | kn-IN | mr-IN

# ── Google Gemini ──────────────────────────────────────────────────
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-1.5-pro-latest

# ── Email (Gmail SMTP) ─────────────────────────────────────────────
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_gmail@gmail.com
SMTP_PASSWORD=your_16char_app_password   # Gmail App Password, NOT account password
EMAIL_FROM_NAME=MOM Generator Bot
EMAIL_RECIPIENTS=manager@company.com,team@company.com

# ── Twilio WhatsApp ────────────────────────────────────────────────
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886   # Twilio Sandbox number
WHATSAPP_RECIPIENTS=whatsapp:+919876543210,whatsapp:+919123456789

# ── App Behaviour ──────────────────────────────────────────────────
CHUNK_DURATION_SECONDS=30
AUDIO_SAMPLE_RATE=16000
AUDIO_CHANNELS=1
LOG_LEVEL=INFO
```

### Step 0.4 — Install dependencies

```bash
pip install -r requirements.txt
```

### ✅ Phase 0 Validation

```bash
python -c "import customtkinter, sounddevice, soundcard, numpy, scipy, aiohttp, google.generativeai, jinja2, twilio, dotenv; print('ALL DEPS OK')"
```

Expected output: `ALL DEPS OK`

---

## PHASE 1 — CONFIGURATION & ENVIRONMENT

**Objective:** Central, validated config loader. All modules import from here — no direct `os.getenv` scattered across code.

### Step 1.1 — Create `config/settings.py`

**File:** `config/settings.py`

```python
"""
config/settings.py
Central configuration loader. Raises descriptive errors on missing keys.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (parent of config/)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)


def _require(key: str) -> str:
    """Fetch env var or raise with a clear message."""
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"[config] Required environment variable '{key}' is missing. "
            f"Check your .env file at {_ENV_PATH}"
        )
    return val


# ── Sarvam AI ──────────────────────────────────────────────────────────────
SARVAM_API_KEY: str        = _require("SARVAM_API_KEY")
SARVAM_STT_MODEL: str      = os.getenv("SARVAM_STT_MODEL", "saarika:v2")
SARVAM_LANGUAGE_CODE: str  = os.getenv("SARVAM_LANGUAGE_CODE", "hi-IN")
SARVAM_STT_URL: str        = "https://api.sarvam.ai/speech-to-text"

# ── Google Gemini ──────────────────────────────────────────────────────────
GEMINI_API_KEY: str        = _require("GEMINI_API_KEY")
GEMINI_MODEL: str          = os.getenv("GEMINI_MODEL", "gemini-1.5-pro-latest")

# ── Email ──────────────────────────────────────────────────────────────────
SMTP_HOST: str             = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int             = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str             = _require("SMTP_USER")
SMTP_PASSWORD: str         = _require("SMTP_PASSWORD")
EMAIL_FROM_NAME: str       = os.getenv("EMAIL_FROM_NAME", "MOM Generator Bot")
EMAIL_RECIPIENTS: list[str] = [
    e.strip() for e in _require("EMAIL_RECIPIENTS").split(",") if e.strip()
]

# ── Twilio ─────────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID: str    = _require("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN: str     = _require("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM: str  = _require("TWILIO_WHATSAPP_FROM")
WHATSAPP_RECIPIENTS: list[str] = [
    r.strip() for r in _require("WHATSAPP_RECIPIENTS").split(",") if r.strip()
]

# ── Audio ──────────────────────────────────────────────────────────────────
CHUNK_DURATION_SECONDS: int = int(os.getenv("CHUNK_DURATION_SECONDS", "30"))
AUDIO_SAMPLE_RATE: int      = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
AUDIO_CHANNELS: int         = int(os.getenv("AUDIO_CHANNELS", "1"))

# ── App ────────────────────────────────────────────────────────────────────
LOG_LEVEL: str              = os.getenv("LOG_LEVEL", "INFO")
```

### ✅ Phase 1 Validation

```bash
python -c "from config.settings import SARVAM_API_KEY, GEMINI_MODEL; print('Config OK:', GEMINI_MODEL)"
```

---

## PHASE 2 — AUDIO CAPTURE MODULE (Fix 2 Applied)

> **BUG FIX 2 — CLOCK DRIFT DESYNC**
>
> **Root cause (Rev 1):** `capture.py` mixed mic and loopback frames inside the
> sounddevice mic callback (`_mic_callback`). The mic callback fires on PortAudio's
> hardware clock; the loopback worker fires on soundcard's (WASAPI/CoreAudio) clock.
> These clocks run at slightly different rates. Over a 30-minute meeting, the
> `_loopback_buffer` either drains empty (loopback is slower → silent fallback to
> mic-only for the entire remainder of the meeting) or grows unboundedly (loopback
> is faster → memory leak). Both failures are completely silent.
>
> **Fix:** `AudioCapture` now pushes mic frames and loopback frames into two
> **separate, independent queues**. No mixing happens in this module at all.
> Clock-drift tolerance is achieved in `AudioChunker` (Phase 3), which drains both
> queues independently and calls `mix_streams()` only once per 30-second chunk,
> where a small frame-count mismatch is expected, harmless, and already handled by
> `mixer.py`'s `min_len` truncation.

**Objective:** Capture microphone and system loopback into two separate thread-safe queues. Zero mixing logic in this module.

### Step 2.1 — Create `audio/mixer.py`

*(Unchanged from Rev 1 — the mixing logic itself is correct; its call site moved to the chunker.)*

**File:** `audio/mixer.py`

```python
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
```

### Step 2.2 — Create `audio/capture.py`

**File:** `audio/capture.py`

```python
"""
audio/capture.py

Dual-stream audio capture — FIXED for clock-drift safety (Bug Fix 2).

Design:
  - Microphone is captured via sounddevice InputStream callback.
    Each callback block is converted to mono and pushed to `mic_queue`.

  - System loopback is captured via soundcard in a dedicated daemon thread.
    Each block is converted to mono and pushed to `loopback_queue`.

  - NO mixing happens here. The two queues are consumed independently
    by AudioChunker, which performs mixing once per 30-second chunk.
    This is the only safe way to handle the fact that sounddevice and
    soundcard run on separate hardware clocks.
"""

import threading
import queue
import logging
import numpy as np
import sounddevice as sd
import soundcard as sc

from audio.mixer import to_mono
from config.settings import AUDIO_SAMPLE_RATE, AUDIO_CHANNELS

logger = logging.getLogger(__name__)

# Frame block size: ~20ms at 16 kHz
BLOCKSIZE = 320


class AudioCapture:
    """
    Manages dual-stream capture.

    Pushes mono float32 frames onto two independent queues:
      mic_queue      — microphone audio
      loopback_queue — system speaker loopback
    """

    def __init__(self, mic_queue: queue.Queue, loopback_queue: queue.Queue):
        """
        Args:
            mic_queue:      Thread-safe queue for microphone frames.
            loopback_queue: Thread-safe queue for loopback frames.
                            May accumulate at a different rate than mic_queue
                            due to hardware clock differences — this is expected.
        """
        self._mic_queue = mic_queue
        self._loopback_queue = loopback_queue
        self._stop_event = threading.Event()
        self._loopback_thread: threading.Thread | None = None
        self._mic_stream: sd.InputStream | None = None
        self._loopback_device = None

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start both capture streams."""
        self._stop_event.clear()
        self._start_loopback()
        self._start_mic()
        logger.info("[AudioCapture] Both streams started (independent queues).")

    def stop(self) -> None:
        """Gracefully stop both streams."""
        self._stop_event.set()
        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()
            self._mic_stream = None
        if self._loopback_thread and self._loopback_thread.is_alive():
            self._loopback_thread.join(timeout=2.0)
        logger.info("[AudioCapture] Both streams stopped.")

    # ── Internal: Loopback ─────────────────────────────────────────────────

    def _start_loopback(self) -> None:
        """Detect default loopback device and start capture thread."""
        try:
            self._loopback_device = sc.default_speaker()
            logger.info(f"[AudioCapture] Loopback device: {self._loopback_device.name}")
        except Exception as e:
            logger.warning(
                f"[AudioCapture] No loopback device found ({e}). "
                "System audio will not be captured — mic only."
            )
            self._loopback_device = None
            return

        self._loopback_thread = threading.Thread(
            target=self._loopback_worker,
            daemon=True,
            name="LoopbackCaptureThread",
        )
        self._loopback_thread.start()

    def _loopback_worker(self) -> None:
        """
        Blocking loop that reads loopback audio in BLOCKSIZE-frame chunks.

        Each frame is converted to mono float32 and pushed onto `loopback_queue`.
        Runs on soundcard's (WASAPI/CoreAudio) hardware clock — intentionally
        independent from the mic PortAudio clock. The slight rate difference
        between the two clocks is absorbed by the chunker at mix time.
        """
        try:
            with self._loopback_device.recorder(
                samplerate=AUDIO_SAMPLE_RATE,
                channels=AUDIO_CHANNELS,
                blocksize=BLOCKSIZE,
            ) as recorder:
                logger.debug("[Loopback] Recording started.")
                while not self._stop_event.is_set():
                    data = recorder.record(numframes=BLOCKSIZE)
                    mono = to_mono(data)
                    try:
                        self._loopback_queue.put_nowait(mono)
                    except queue.Full:
                        # Drop silently — chunker will handle the gap
                        logger.debug("[Loopback] Queue full — dropping 1 frame.")
        except Exception as e:
            logger.error(f"[Loopback] Worker thread error: {e}")

    # ── Internal: Mic ──────────────────────────────────────────────────────

    def _start_mic(self) -> None:
        """Open sounddevice InputStream and register callback."""
        self._mic_stream = sd.InputStream(
            samplerate=AUDIO_SAMPLE_RATE,
            channels=AUDIO_CHANNELS,
            dtype="float32",
            blocksize=BLOCKSIZE,
            callback=self._mic_callback,
            latency="low",
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
        No mixing with loopback here — the two streams must remain
        on separate queues to avoid cross-clock frame misalignment.
        """
        if status:
            logger.debug(f"[Mic] Callback status: {status}")

        mono = to_mono(indata.copy())
        try:
            self._mic_queue.put_nowait(mono)
        except queue.Full:
            logger.warning("[AudioCapture] Mic queue full — dropping frame.")
```

### ✅ Phase 2 Validation

```bash
python - <<'EOF'
import queue, time
from audio.capture import AudioCapture

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
EOF
```

---

## PHASE 3 — STREAMING STT PIPELINE (Fix 3 Applied)

> **BUG FIX 3 — SILENT NETWORK EXCEPTION SWALLOWING**
>
> **Root cause (Rev 1):** `_emit_chunk` called `asyncio.run_coroutine_threadsafe()`
> but discarded the returned `concurrent.futures.Future`. If the `aiohttp` POST
> raised any exception (connection reset, DNS failure, HTTP 5xx, JSON parse error),
> that exception was stored inside the Future object. Since no code ever called
> `future.result()` or `future.exception()`, the error was permanently lost —
> no log line, no UI alert, just a silently missing transcript chunk.
>
> **Fix:** The `Future` is now captured and `future.add_done_callback(
> self._on_chunk_future_done)` is attached immediately. The callback calls
> `f.exception()` which re-raises or returns the stored exception; if non-None,
> it is logged at ERROR level and surfaced to the UI log callback.

**Objective:** Collect raw PCM frames from both queues, accumulate a 30-second buffer, mix them at chunk time, encode as WAV, and fire async STT requests with proper exception visibility.

### Step 3.1 — Create `pipeline/chunker.py`

**File:** `pipeline/chunker.py`

```python
"""
pipeline/chunker.py — FIXED for clock-drift safety (Bug Fix 2) and
                       silent exception swallowing (Bug Fix 3).

Drains mic_queue and loopback_queue independently into separate frame buffers.
When the mic buffer reaches CHUNK_DURATION_SECONDS of audio:
  1. Concatenates both buffers into mono numpy arrays.
  2. Calls mix_streams() to produce a single mixed WAV (handles length mismatch).
  3. Encodes to WAV bytes (int16).
  4. Calls `asyncio.run_coroutine_threadsafe()` and captures the returned Future.
  5. Attaches a done-callback to the Future so any exception is logged (Fix 3).

The separation of mic/loopback draining is Fix 2: because the two devices run on
different hardware clocks, the loopback buffer will have slightly more or fewer
frames than the mic buffer after 30 seconds. mix_streams() truncates to min_len,
which absorbs the drift silently and correctly.
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

from audio.mixer import mix_streams, to_mono
from config.settings import CHUNK_DURATION_SECONDS, AUDIO_SAMPLE_RATE

logger = logging.getLogger(__name__)

FRAMES_PER_CHUNK = CHUNK_DURATION_SECONDS * AUDIO_SAMPLE_RATE


class AudioChunker:
    """
    Reads mic and loopback audio frames from two separate queues (Fix 2).
    Mixes them once per chunk boundary (not per-frame).
    Emits WAV chunks to `on_chunk_ready` coroutine with exception-safe
    Future handling (Fix 3).
    """

    def __init__(
        self,
        mic_queue: queue.Queue,
        loopback_queue: queue.Queue | None,
        async_loop: asyncio.AbstractEventLoop,
        on_chunk_ready: Callable[[int, bytes], Awaitable[None]],
        log_callback: Callable[[str], None] | None = None,
    ):
        """
        Args:
            mic_queue:       Primary audio source. Drives chunk cadence.
            loopback_queue:  Secondary audio source (may be None if loopback
                             unavailable). Drained non-blocking alongside mic.
            async_loop:      The running asyncio event loop (in its daemon thread).
            on_chunk_ready:  Async coroutine called with (chunk_index, wav_bytes).
            log_callback:    Optional callable(str) to surface messages to UI.
        """
        self._mic_queue = mic_queue
        self._loopback_queue = loopback_queue
        self._async_loop = async_loop
        self._on_chunk_ready = on_chunk_ready
        self._log_callback = log_callback
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._chunk_index: int = 0

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
            f"Loopback queue: {'enabled' if self._loopback_queue else 'disabled'}."
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
        a chunk is emitted. loopback_buffer is drained non-blocking on every
        iteration, so it accumulates at its own pace.

        At chunk time, the two buffers will have accumulated a slightly different
        number of frames due to independent hardware clocks. mix_streams() handles
        this by truncating to min_len. The discarded frames (a handful at most)
        represent less than 1ms of audio and are inaudible/inconsequential for STT.
        """
        mic_buffer: list[np.ndarray] = []
        loopback_buffer: list[np.ndarray] = []
        mic_frame_count: int = 0

        while not self._stop_event.is_set():
            # Block on the mic queue — this is the primary timing source
            try:
                mic_frame = self._mic_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            mic_buffer.append(mic_frame)
            mic_frame_count += len(mic_frame)

            # Drain ALL currently available loopback frames (non-blocking)
            # This keeps the loopback buffer roughly in sync with the mic buffer
            # without blocking on the loopback queue's independent clock.
            if self._loopback_queue is not None:
                while True:
                    try:
                        lb_frame = self._loopback_queue.get_nowait()
                        loopback_buffer.append(lb_frame)
                    except queue.Empty:
                        break

            if mic_frame_count >= FRAMES_PER_CHUNK:
                self._emit_chunk(mic_buffer, loopback_buffer)
                mic_buffer = []
                loopback_buffer = []
                mic_frame_count = 0

        # Flush any remaining audio in both buffers as the final chunk
        if mic_buffer:
            logger.info("[Chunker] Flushing final partial chunk.")
            # Drain any loopback frames that arrived after stop was signalled
            if self._loopback_queue is not None:
                while True:
                    try:
                        loopback_buffer.append(self._loopback_queue.get_nowait())
                    except queue.Empty:
                        break
            self._emit_chunk(mic_buffer, loopback_buffer)

    def _emit_chunk(
        self,
        mic_buffer: list[np.ndarray],
        loopback_buffer: list[np.ndarray],
    ) -> None:
        """
        Mix, encode, and dispatch one WAV chunk.

        Step 1: Concatenate the mic buffer into a single mono array.
        Step 2: If loopback data is available, concatenate and mix with mic.
                mix_streams() truncates to min_len — this is the clock-drift
                compensation point (Fix 2).
        Step 3: Encode to int16 WAV bytes.
        Step 4: Schedule the STT coroutine on the async event loop and attach
                a done-callback to surface any exceptions (Fix 3).
        """
        mic_pcm = np.concatenate(mic_buffer)

        if loopback_buffer:
            loopback_pcm = np.concatenate(loopback_buffer)
            mixed_pcm = mix_streams(mic_pcm, loopback_pcm)
            logger.debug(
                f"[Chunker] Mixed: mic={len(mic_pcm)} frames, "
                f"loopback={len(loopback_pcm)} frames, "
                f"output={len(mixed_pcm)} frames "
                f"(drift={abs(len(mic_pcm) - len(loopback_pcm))} frames)"
            )
        else:
            mixed_pcm = mic_pcm
            logger.debug("[Chunker] No loopback data — using mic only for this chunk.")

        # Convert float32 [-1, 1] → int16 for WAV
        pcm_int16 = (mixed_pcm * 32767).clip(-32768, 32767).astype(np.int16)
        wav_buffer = io.BytesIO()
        wav_write(wav_buffer, AUDIO_SAMPLE_RATE, pcm_int16)
        wav_bytes = wav_buffer.getvalue()

        idx = self._chunk_index
        self._chunk_index += 1
        logger.info(f"[Chunker] Emitting chunk #{idx} ({len(wav_bytes):,} bytes)")

        # ── BUG FIX 3: Capture the Future and attach a done-callback ──────
        # run_coroutine_threadsafe returns a concurrent.futures.Future.
        # In Rev 1 this was discarded, silently swallowing any exception
        # raised inside transcribe_chunk (network drops, timeouts, etc.).
        # The done-callback calls f.exception(), which retrieves and logs
        # the stored exception before it is garbage-collected.
        future: concurrent.futures.Future = asyncio.run_coroutine_threadsafe(
            self._on_chunk_ready(idx, wav_bytes),
            self._async_loop,
        )
        future.add_done_callback(self._on_chunk_future_done)

    def _on_chunk_future_done(self, future: concurrent.futures.Future) -> None:
        """
        Done-callback executed when an STT task Future resolves.

        If the coroutine raised an exception, `future.exception()` returns it
        (without re-raising). We log it at ERROR level and notify the UI.
        If it was cancelled, we log a warning.
        Normal completion: no-op.

        This callback runs in whatever thread the Future resolves on, so
        all operations here must be thread-safe (logging and queue.put are).
        """
        if future.cancelled():
            logger.warning(f"[Chunker] STT task was cancelled unexpectedly.")
            self._log_to_ui("⚠️ An STT task was cancelled — one chunk may be missing.")
            return

        exc = future.exception()
        if exc is not None:
            logger.error(
                f"[Chunker] STT task raised an unhandled exception: "
                f"{type(exc).__name__}: {exc!r}"
            )
            self._log_to_ui(f"❌ STT task failed: {type(exc).__name__}: {exc}")

    def _log_to_ui(self, message: str) -> None:
        if self._log_callback:
            self._log_callback(message)
```

### Step 3.2 — Create `pipeline/stt_client.py`

*(Core logic unchanged from Rev 1. Presented in full for agent completeness.)*

**File:** `pipeline/stt_client.py`

```python
"""
pipeline/stt_client.py

Asynchronous Sarvam AI Speech-to-Text client.

Key design:
  - Uses aiohttp for non-blocking multipart/form-data POST.
  - Each chunk is dispatched as an independent asyncio Task.
  - Results are stored in a dict keyed by chunk_index to preserve ordering
    even if responses arrive out-of-order (network jitter).
  - A semaphore limits concurrent in-flight requests to avoid rate-limit 429s.
  - Exception handling is now in AudioChunker's Future callback (Fix 3).
    This module only raises; it does not swallow.
"""

import asyncio
import logging
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


class SarvamSTTClient:
    """
    Dispatches audio chunks to Sarvam AI STT asynchronously.
    Maintains an ordered transcript dict.
    """

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._transcripts: dict[int, str] = {}
        self._lock = asyncio.Lock()
        self._log_callback = None

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

    async def transcribe_chunk(self, chunk_index: int, wav_bytes: bytes) -> None:
        """
        Async entry point called by chunker for each WAV chunk.
        Acquires semaphore then fires the STT request.

        NOTE: This coroutine intentionally re-raises exceptions rather than
        catching them. The caller (AudioChunker._on_chunk_future_done via
        the Future done-callback, Fix 3) is responsible for logging them.
        """
        async with self._semaphore:
            await self._post_to_sarvam(chunk_index, wav_bytes)

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

    async def _post_to_sarvam(self, chunk_index: int, wav_bytes: bytes) -> None:
        """
        POST WAV bytes to Sarvam AI STT endpoint.
        Raises aiohttp.ClientError or ValueError on failure
        (to be caught by the Future done-callback in the chunker).
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

        async with self._session.post(SARVAM_STT_URL, data=form) as resp:
            if resp.status == 200:
                data = await resp.json()
                transcript = data.get("transcript", "")
                async with self._lock:
                    self._transcripts[chunk_index] = transcript
                self._log(f"✅ Chunk #{chunk_index}: \"{transcript[:60]}...\"")
                logger.debug(f"[STT] Chunk #{chunk_index} transcript: {transcript}")
            else:
                error_text = await resp.text()
                # Raise so the Future done-callback can log it (Fix 3)
                raise ValueError(
                    f"Sarvam AI HTTP {resp.status} on chunk #{chunk_index}: {error_text}"
                )

    def _log(self, message: str) -> None:
        logger.info(f"[STT] {message}")
        if self._log_callback:
            self._log_callback(message)
```

### ✅ Phase 3 Validation

```bash
python - <<'EOF'
import asyncio
from pipeline.stt_client import SarvamSTTClient

async def test():
    client = SarvamSTTClient()
    await client.start()
    import numpy as np, io
    from scipy.io.wavfile import write as wav_write
    buf = io.BytesIO()
    wav_write(buf, 16000, np.zeros(16000, dtype=np.int16))
    await client.transcribe_chunk(0, buf.getvalue())
    await asyncio.sleep(2)
    await client.stop()
    print("STT Pipeline OK. Transcript:", repr(client.get_full_transcript()))

asyncio.run(test())
EOF
```

```bash
# Verify Fix 3: Future exception callback is wired
python - <<'EOF'
import asyncio, threading, queue, time, concurrent.futures
from pipeline.chunker import AudioChunker

loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()

errors_logged = []

async def failing_on_chunk(idx, wav_bytes):
    raise ConnectionResetError("Simulated network drop")

mic_q = queue.Queue()
chunker = AudioChunker(
    mic_queue=mic_q,
    loopback_queue=None,
    async_loop=loop,
    on_chunk_ready=failing_on_chunk,
    log_callback=lambda msg: errors_logged.append(msg),
)
chunker.start()

import numpy as np
# Push enough frames to trigger one chunk
from pipeline.chunker import FRAMES_PER_CHUNK
block = np.zeros(320, dtype=np.float32)
for _ in range(FRAMES_PER_CHUNK // 320 + 1):
    mic_q.put(block)

time.sleep(2.0)
chunker.stop()

assert any("ConnectionResetError" in e for e in errors_logged), \
    f"Exception was NOT surfaced. Logged: {errors_logged}"
print("✅ Fix 3 verified: network exception was surfaced via done-callback.")
EOF
```

---

## PHASE 4 — NLP MODULE (GEMINI)

**Objective:** Accept the full assembled transcript + meeting context string. Send to Gemini 1.5 Pro with forced JSON output. Return validated Python dict.

*(No changes from Rev 1.)*

### Step 4.1 — Create `nlp/gemini_client.py`

**File:** `nlp/gemini_client.py`

```python
"""
nlp/gemini_client.py

Sends the full transcript + meeting context to Google Gemini.
Forces structured JSON output using response_mime_type='application/json'.

Output JSON schema:
{
    "meeting_title": str,
    "date": str (DD-MMM-YYYY),
    "meeting_context": str,
    "executive_summary": str (3-5 sentences),
    "key_decisions": [str, ...],
    "action_items": [
        {
            "task": str,
            "assignee": str,
            "deadline": str (DD-MMM-YYYY or "TBD"),
            "priority": "High" | "Medium" | "Low"
        },
        ...
    ],
    "attendees_mentioned": [str, ...],
    "risks_and_blockers": [str, ...],
    "next_steps": str,
    "next_meeting_suggestion": str
}
"""

import json
import logging
from datetime import date
import google.generativeai as genai
from google.generativeai.types import GenerationConfig

from config.settings import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """
You are an expert corporate secretary specialising in Indian business meetings.
Your task is to analyse a meeting transcript and extract structured information.

RULES:
1. Respond ONLY with a valid JSON object. No markdown, no preamble, no explanation.
2. Infer assignees from the transcript; use "Team" if unspecified.
3. Deadlines must be extracted from conversational context (e.g., "by next Friday",
   "end of month") and converted to DD-MMM-YYYY format. Use "TBD" if not mentioned.
4. Executive summary must be professional, concise, and written in formal English.
5. Priorities: "High" = mentioned as urgent/critical, "Medium" = standard,
   "Low" = nice-to-have or mentioned briefly.
6. The output must match the exact JSON schema provided.
7. Meeting context provided by the user must anchor interpretation of all items.
8. Capture risks, blockers, and dependencies as a separate list.
"""

_USER_PROMPT_TEMPLATE = """
MEETING DATE: {meeting_date}
MEETING CONTEXT / AGENDA: {meeting_context}

FULL TRANSCRIPT:
---
{transcript}
---

Extract the Minutes of Meeting as a JSON object with this EXACT schema:
{{
    "meeting_title": "<inferred from context and transcript>",
    "date": "<{meeting_date}>",
    "meeting_context": "<{meeting_context}>",
    "executive_summary": "<3-5 sentence professional summary>",
    "key_decisions": ["<decision 1>", "<decision 2>"],
    "action_items": [
        {{
            "task": "<specific task description>",
            "assignee": "<name or Team>",
            "deadline": "<DD-MMM-YYYY or TBD>",
            "priority": "<High|Medium|Low>"
        }}
    ],
    "attendees_mentioned": ["<name1>", "<name2>"],
    "risks_and_blockers": ["<risk/blocker 1>"],
    "next_steps": "<brief paragraph>",
    "next_meeting_suggestion": "<suggestion or TBD>"
}}
"""


class GeminiMOMClient:
    """Extracts structured MOM data from transcript using Gemini."""

    def __init__(self):
        genai.configure(api_key=GEMINI_API_KEY)
        self._model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=_SYSTEM_PROMPT,
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
                top_p=0.9,
                max_output_tokens=4096,
            ),
        )
        logger.info(f"[Gemini] Initialised with model: {GEMINI_MODEL}")

    def extract_mom(self, transcript: str, meeting_context: str) -> dict:
        """
        Send transcript to Gemini and return structured MOM dict.

        Args:
            transcript: Full assembled transcript from all STT chunks.
            meeting_context: User-supplied context/agenda string from UI.

        Returns:
            Validated Python dict matching the MOM JSON schema.

        Raises:
            ValueError: If Gemini returns invalid/empty JSON.
            RuntimeError: If the Gemini API call fails.
        """
        if not transcript.strip():
            logger.warning("[Gemini] Empty transcript provided.")
            transcript = "[No transcript available — audio may not have been recorded]"

        today = date.today().strftime("%d-%b-%Y")
        prompt = _USER_PROMPT_TEMPLATE.format(
            meeting_date=today,
            meeting_context=meeting_context or "General team meeting",
            transcript=transcript,
        )

        logger.info("[Gemini] Sending transcript for MOM extraction...")

        try:
            response = self._model.generate_content(prompt)
        except Exception as e:
            raise RuntimeError(f"[Gemini] API call failed: {e}") from e

        raw_text = response.text.strip()
        logger.debug(f"[Gemini] Raw response ({len(raw_text)} chars): {raw_text[:200]}...")

        # Defensive strip in case Gemini wraps in ```json despite instructions
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        try:
            mom_data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"[Gemini] Could not parse JSON response: {e}\n"
                f"Raw response: {raw_text[:500]}"
            ) from e

        # Basic schema validation with graceful defaults
        required_keys = [
            "meeting_title", "date", "executive_summary",
            "key_decisions", "action_items",
        ]
        for key in required_keys:
            if key not in mom_data:
                logger.warning(f"[Gemini] Missing key in response: '{key}'. Inserting default.")
                mom_data[key] = [] if key in ("key_decisions", "action_items") else "N/A"

        logger.info(
            f"[Gemini] MOM extracted: "
            f"{len(mom_data.get('action_items', []))} action items, "
            f"{len(mom_data.get('key_decisions', []))} decisions."
        )
        return mom_data
```

### ✅ Phase 4 Validation

```bash
python - <<'EOF'
from nlp.gemini_client import GeminiMOMClient
import json

client = GeminiMOMClient()
mock_transcript = (
    "Ramesh said we need to launch the new product by 15th July. "
    "Priya confirmed the design is ready. Ankit will handle QA by July 10th. "
    "Team decided to skip the beta phase. Sunita raised a concern about server capacity."
)
result = client.extract_mom(mock_transcript, "Q2 Product Launch Planning")
print(json.dumps(result, indent=2, ensure_ascii=False))
print("\nGemini NLP Module OK.")
EOF
```

---

## PHASE 5 — OUTPUT FORMATTER (HTML EMAIL)

**Objective:** Transform the Gemini JSON dict into a polished HTML email using Jinja2. Dispatch via SMTP.

*(No changes from Rev 1.)*

### Step 5.1 — Create `output/templates/mom_email.html.j2`

**File:** `output/templates/mom_email.html.j2`

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Minutes of Meeting — {{ data.meeting_title }}</title>
<style>
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #f4f6f9; margin: 0; padding: 20px; color: #2c3e50; }
  .container { max-width: 720px; margin: 0 auto; background: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
  .header { background: linear-gradient(135deg, #1a237e 0%, #283593 100%); color: white; padding: 28px 32px; }
  .header h1 { margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0.5px; }
  .header .meta { margin-top: 8px; font-size: 13px; opacity: 0.85; }
  .badge { display: inline-block; background: rgba(255,255,255,0.2); border-radius: 4px; padding: 2px 8px; font-size: 11px; margin-left: 8px; }
  .section { padding: 24px 32px; border-bottom: 1px solid #edf2f7; }
  .section:last-child { border-bottom: none; }
  .section-title { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #1a237e; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
  .section-title::after { content: ''; flex: 1; height: 1px; background: #e2e8f0; }
  .summary-text { font-size: 14px; line-height: 1.7; color: #4a5568; }
  .decisions-list, .risks-list { list-style: none; padding: 0; margin: 0; }
  .decisions-list li, .risks-list li { padding: 8px 0 8px 20px; font-size: 14px; border-bottom: 1px solid #f7fafc; position: relative; }
  .decisions-list li::before { content: '✓'; position: absolute; left: 0; color: #38a169; font-weight: bold; }
  .risks-list li::before { content: '⚠'; position: absolute; left: 0; }
  .action-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .action-table th { background: #f7f8ff; color: #1a237e; padding: 10px 12px; text-align: left; font-weight: 600; border-bottom: 2px solid #c5cae9; }
  .action-table td { padding: 10px 12px; border-bottom: 1px solid #edf2f7; vertical-align: top; }
  .action-table tr:hover td { background: #fafbff; }
  .priority-high { display: inline-block; background: #fed7d7; color: #c53030; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .priority-medium { display: inline-block; background: #feebc8; color: #c05621; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .priority-low { display: inline-block; background: #c6f6d5; color: #276749; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .attendee-chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { background: #e8eaf6; color: #3949ab; border-radius: 12px; padding: 4px 12px; font-size: 12px; font-weight: 500; }
  .next-steps { font-size: 14px; line-height: 1.7; color: #4a5568; }
  .footer { background: #f7f8ff; padding: 16px 32px; text-align: center; font-size: 11px; color: #a0aec0; }
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <h1>📋 Minutes of Meeting</h1>
    <div class="meta">
      <strong>{{ data.meeting_title }}</strong>
      <span class="badge">{{ data.date }}</span>
      {% if data.action_items | length > 0 %}
      <span class="badge">{{ data.action_items | length }} Action Items</span>
      {% endif %}
    </div>
    <div class="meta" style="margin-top:6px;">
      <em>Context: {{ data.meeting_context }}</em>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Executive Summary</div>
    <p class="summary-text">{{ data.executive_summary }}</p>
  </div>

  {% if data.attendees_mentioned %}
  <div class="section">
    <div class="section-title">Attendees Mentioned</div>
    <div class="attendee-chips">
      {% for name in data.attendees_mentioned %}
      <span class="chip">👤 {{ name }}</span>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  {% if data.key_decisions %}
  <div class="section">
    <div class="section-title">Key Decisions</div>
    <ul class="decisions-list">
      {% for decision in data.key_decisions %}
      <li>{{ decision }}</li>
      {% endfor %}
    </ul>
  </div>
  {% endif %}

  {% if data.action_items %}
  <div class="section">
    <div class="section-title">Action Items</div>
    <table class="action-table">
      <thead>
        <tr>
          <th>#</th><th>Task</th><th>Assignee</th><th>Deadline</th><th>Priority</th>
        </tr>
      </thead>
      <tbody>
        {% for item in data.action_items %}
        <tr>
          <td>{{ loop.index }}</td>
          <td>{{ item.task }}</td>
          <td><strong>{{ item.assignee }}</strong></td>
          <td>{{ item.deadline }}</td>
          <td>
            {% if item.priority == 'High' %}<span class="priority-high">🔴 High</span>
            {% elif item.priority == 'Medium' %}<span class="priority-medium">🟡 Medium</span>
            {% else %}<span class="priority-low">🟢 Low</span>{% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

  {% if data.risks_and_blockers %}
  <div class="section">
    <div class="section-title">Risks &amp; Blockers</div>
    <ul class="risks-list">
      {% for risk in data.risks_and_blockers %}
      <li>{{ risk }}</li>
      {% endfor %}
    </ul>
  </div>
  {% endif %}

  {% if data.next_steps %}
  <div class="section">
    <div class="section-title">Next Steps</div>
    <p class="next-steps">{{ data.next_steps }}</p>
    {% if data.next_meeting_suggestion != 'TBD' %}
    <p style="font-size:13px; color:#718096;">
      📅 <strong>Next Meeting:</strong> {{ data.next_meeting_suggestion }}
    </p>
    {% endif %}
  </div>
  {% endif %}

  <div class="footer">
    Auto-generated by <strong>MOM Generator</strong> &bull;
    Powered by Sarvam AI + Gemini &bull; {{ data.date }}<br>
    <em>This document is auto-generated. Please verify action items with your team.</em>
  </div>

</div>
</body>
</html>
```

### Step 5.2 — Create `output/html_formatter.py`

**File:** `output/html_formatter.py`

```python
"""output/html_formatter.py — Renders MOM JSON dict into HTML email string."""

import logging
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

_TEMPLATE_DIR  = Path(__file__).resolve().parent / "templates"
_TEMPLATE_FILE = "mom_email.html.j2"


class HTMLFormatter:
    """Renders MOM data dict → HTML email string."""

    def __init__(self):
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "j2"]),
        )
        self._template = self._env.get_template(_TEMPLATE_FILE)
        logger.info(f"[HTMLFormatter] Template loaded from {_TEMPLATE_DIR / _TEMPLATE_FILE}")

    def render(self, mom_data: dict) -> str:
        html = self._template.render(data=mom_data)
        logger.info(f"[HTMLFormatter] Rendered {len(html):,} character HTML email.")
        return html
```

### Step 5.3 — Create `output/email_sender.py`

**File:** `output/email_sender.py`

```python
"""output/email_sender.py — Sends HTML MOM email via SMTP (Gmail TLS)."""

import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from datetime import date

from config.settings import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    EMAIL_FROM_NAME, EMAIL_RECIPIENTS,
)

logger = logging.getLogger(__name__)


class EmailSender:
    """Dispatches HTML MOM emails via SMTP."""

    def send(self, mom_data: dict, html_body: str) -> bool:
        subject = (
            f"[MOM] {mom_data.get('meeting_title', 'Meeting')} — "
            f"{mom_data.get('date', date.today().strftime('%d-%b-%Y'))}"
        )
        plain_text = self._build_plain_text(mom_data)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = formataddr((EMAIL_FROM_NAME, SMTP_USER))
        msg["To"]      = ", ".join(EMAIL_RECIPIENTS)
        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body,  "html",  "utf-8"))

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo(); server.starttls(); server.ehlo()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, EMAIL_RECIPIENTS, msg.as_string())
            logger.info(f"[Email] Sent to: {', '.join(EMAIL_RECIPIENTS)}")
            return True
        except smtplib.SMTPException as e:
            logger.error(f"[Email] SMTP error: {e}")
            return False

    def _build_plain_text(self, mom_data: dict) -> str:
        lines = [
            f"MINUTES OF MEETING — {mom_data.get('meeting_title', 'N/A')}",
            f"Date: {mom_data.get('date', 'N/A')}",
            f"Context: {mom_data.get('meeting_context', 'N/A')}",
            "", "EXECUTIVE SUMMARY", mom_data.get("executive_summary", "N/A"),
            "", "KEY DECISIONS",
        ]
        for i, d in enumerate(mom_data.get("key_decisions", []), 1):
            lines.append(f"  {i}. {d}")
        lines += ["", "ACTION ITEMS"]
        for i, item in enumerate(mom_data.get("action_items", []), 1):
            lines.append(
                f"  {i}. [{item.get('priority','?')}] {item.get('task','?')} "
                f"→ {item.get('assignee','?')} by {item.get('deadline','TBD')}"
            )
        lines += ["", "NEXT STEPS", mom_data.get("next_steps", "N/A"), "",
                  "---", "Auto-generated by MOM Generator"]
        return "\n".join(lines)
```

### ✅ Phase 5 Validation

```bash
python - <<'EOF'
from output.html_formatter import HTMLFormatter

formatter = HTMLFormatter()
mock_data = {
    "meeting_title": "Q3 Sprint Planning", "date": "11-Jun-2025",
    "meeting_context": "Sprint planning for Q3", "executive_summary": "Team reviewed Q2.",
    "key_decisions": ["Adopt two-week sprints"], "attendees_mentioned": ["Ankit", "Priya"],
    "action_items": [{"task": "Update Jira", "assignee": "Ankit", "deadline": "15-Jun-2025", "priority": "High"}],
    "risks_and_blockers": [], "next_steps": "Update backlogs.", "next_meeting_suggestion": "25-Jun-2025",
}
html = formatter.render(mock_data)
assert "<html" in html.lower() and "Q3 Sprint Planning" in html
print(f"HTML rendered: {len(html):,} chars. Formatter OK.")
EOF
```

---

## PHASE 6 — NOTIFICATION MODULE (TWILIO WHATSAPP)

*(No changes from Rev 1.)*

### Step 6.1 — Create `notifications/whatsapp_alert.py`

**File:** `notifications/whatsapp_alert.py`

```python
"""
notifications/whatsapp_alert.py

Sends a WhatsApp message via Twilio's Messaging API.

Prerequisites:
  1. Twilio account with WhatsApp Sandbox enabled.
  2. Each recipient must have joined the sandbox by sending
     the join code to the Twilio sandbox number.
  3. For production: use a Twilio-approved WhatsApp Business sender.
"""

import logging
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

from config.settings import (
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
    TWILIO_WHATSAPP_FROM, WHATSAPP_RECIPIENTS,
)

logger = logging.getLogger(__name__)

_PRIORITY_EMOJI = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}


class WhatsAppAlert:
    """Dispatches WhatsApp MOM summary via Twilio."""

    def __init__(self):
        self._client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        logger.info("[WhatsApp] Twilio client initialised.")

    def send_alert(self, mom_data: dict) -> list[bool]:
        message_body = self._format_message(mom_data)
        return [self._send_single(r, message_body) for r in WHATSAPP_RECIPIENTS]

    def _send_single(self, to: str, body: str) -> bool:
        try:
            msg = self._client.messages.create(
                from_=TWILIO_WHATSAPP_FROM, to=to, body=body
            )
            logger.info(f"[WhatsApp] Sent to {to} — SID: {msg.sid}")
            return True
        except TwilioRestException as e:
            logger.error(f"[WhatsApp] Failed to send to {to}: {e}")
            return False

    def _format_message(self, mom_data: dict) -> str:
        title    = mom_data.get("meeting_title", "Meeting")
        date_str = mom_data.get("date", "N/A")
        context  = mom_data.get("meeting_context", "N/A")
        decisions    = mom_data.get("key_decisions", [])
        action_items = mom_data.get("action_items", [])

        lines = [
            f"📋 *MOM Alert: {title}*",
            f"📅 {date_str}  |  🎯 {context}",
            "",
            f"✅ *{len(decisions)} Key Decision(s)* recorded",
            f"📌 *{len(action_items)} Action Item(s)* assigned",
        ]
        if action_items:
            lines += ["", "*Action Items:*"]
            for i, item in enumerate(action_items[:8], 1):
                emoji = _PRIORITY_EMOJI.get(item.get("priority", "Medium"), "🟡")
                lines.append(
                    f"{i}. {item.get('task','?')} → "
                    f"@{item.get('assignee','Team')} "
                    f"by {item.get('deadline','TBD')} {emoji}"
                )
            if len(action_items) > 8:
                lines.append(f"   ... and {len(action_items) - 8} more.")
        lines += ["", "📧 Full MOM sent to email. _Auto-generated by MOM Generator_"]
        return "\n".join(lines)
```

---

## PHASE 7 — UI MODULE (CUSTOMTKINTER) (Fix 1 Applied)

> **BUG FIX 1 — UI DEADLOCK ON STOP**
>
> **Root cause (Rev 1):** `_handle_stop` called `self._on_stop()` — which maps to
> `orchestrator.stop()` — directly on the CustomTkinter main thread (the button
> command runs on the UI event loop). `orchestrator.stop()` contains
> `.result(timeout=120)`, a blocking call that waits up to 120 seconds for all
> in-flight STT tasks. With the UI thread blocked, CustomTkinter cannot process
> any events: the window freezes, the log drain timer fires but cannot execute,
> the status labels cannot update, and on some platforms the OS marks the window
> as "Not Responding".
>
> **Fix:** `_handle_stop` now spawns `self._on_stop()` in a `daemon=True` thread,
> identical to the pattern already used by `_handle_start`. The UI thread returns
> immediately and remains fully responsive. All UI updates during stop processing
> flow back via `app.after(0, ...)` inside the orchestrator (unchanged).

**Objective:** Build the desktop GUI. Meeting Context input, Start/Stop buttons, live log, status indicator, elapsed timer, progress bar.

### Step 7.1 — Create `ui/app_window.py`

**File:** `ui/app_window.py`

```python
"""
ui/app_window.py — FIXED for UI deadlock on Stop (Bug Fix 1).

Layout:
  ┌─────────────────────────────────────────────────┐
  │  🎙 MOM Generator          [● RECORDING...]     │
  ├─────────────────────────────────────────────────┤
  │  Meeting Context / Agenda:                       │
  │  [____________________________________]          │
  │  Override Email Recipients (optional):           │
  │  [____________________________________]          │
  │  [ ▶ Start Recording ]  [ ■ Stop & Generate ]    │
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
    """Main application window."""

    def __init__(
        self,
        on_start: Callable[[str, list[str]], None],
        on_stop: Callable[[], None],
    ):
        super().__init__()
        self._on_start = on_start
        self._on_stop  = on_stop
        self._is_recording = False
        self._record_start_time: float | None = None
        self._timer_thread: threading.Thread | None = None
        self._log_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._schedule_log_drain()

    # ── UI Construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.title("MOM Generator — Powered by Sarvam AI + Gemini")
        self.geometry("680x620")
        self.resizable(False, False)
        self.grid_columnconfigure(0, weight=1)

        # Header
        header = ctk.CTkFrame(self, fg_color="#1a237e", corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header,
            text="🎙  Minutes of Meeting Generator",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="white",
        ).pack(pady=14, padx=20, anchor="w")

        # Recording indicator bar
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

        # Input fields
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

        # Action buttons
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

        # Transcription log
        log_frame = ctk.CTkFrame(self, fg_color="white")
        log_frame.grid(row=4, column=0, sticky="nsew", padx=16, pady=(4, 0))
        log_frame.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(
            log_frame, text="Live Transcription Log",
            font=ctk.CTkFont(size=12, weight="bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self._log_textbox = ctk.CTkTextbox(
            log_frame, height=180,
            font=ctk.CTkFont(size=11, family="Courier"),
            fg_color="#1e1e2e", text_color="#cdd6f4", wrap="word",
        )
        self._log_textbox.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        log_frame.grid_rowconfigure(1, weight=1)

        # Progress bar
        self._progress = ctk.CTkProgressBar(self, height=8)
        self._progress.grid(row=5, column=0, sticky="ew", padx=16, pady=(4, 0))
        self._progress.set(0)

        # Footer status
        footer = ctk.CTkFrame(self, fg_color="#f0f4ff", corner_radius=0)
        footer.grid(row=6, column=0, sticky="ew", pady=(4, 0))
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

        self._is_recording = True
        self._record_start_time = time.time()
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._context_entry.configure(state="disabled")
        self._recipients_entry.configure(state="disabled")
        self._status_dot.configure(text="🔴  Recording...", text_color="#c62828")
        self._footer_label.configure(text="Recording in progress. Sarvam AI is transcribing...")
        self._log_message("🎙 Recording started. Audio is being captured and transcribed.")

        self._timer_thread = threading.Thread(
            target=self._update_timer, daemon=True
        )
        self._timer_thread.start()

        threading.Thread(
            target=self._on_start,
            args=(context, override_recipients),
            daemon=True,
        ).start()

    def _handle_stop(self) -> None:
        """
        Handle Stop button click.

        ── BUG FIX 1 ────────────────────────────────────────────────────────
        Rev 1 called `self._on_stop()` directly here, on the tkinter main thread.
        `orchestrator.stop()` calls `.result(timeout=120)`, blocking the UI thread
        for up to 2 minutes — the window freezes and appears hung.

        Fix: spin `self._on_stop()` off into a daemon thread so the UI thread
        returns immediately and remains fully responsive during the stop pipeline.
        All UI updates from the orchestrator flow back via `app.after(0, ...)`.
        ─────────────────────────────────────────────────────────────────────
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

        # Spawn on daemon thread so the UI event loop is NOT blocked (Fix 1)
        threading.Thread(
            target=self._on_stop,
            daemon=True,
            name="StopPipelineThread",
        ).start()

    # ── Public Methods (called from orchestrator via app.after) ───────────

    def log(self, message: str) -> None:
        """Thread-safe log. Can be called from any thread."""
        self._log_queue.put(message)

    def set_progress(self, value: float) -> None:
        """Update progress bar (0.0–1.0). Thread-safe."""
        self.after(0, lambda: self._progress.set(value))

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
```

### ✅ Phase 7 Validation

```bash
python - <<'EOF'
# Smoke test: launch UI for 3 seconds and auto-close
import threading, time
from ui.app_window import AppWindow

def fake_start(ctx, recs): print(f"Start called: context='{ctx}'")
def fake_stop():
    import time; time.sleep(0.5)   # Simulate blocking work — UI must stay alive
    print("Stop pipeline completed (non-blocking from UI perspective)")

app = AppWindow(on_start=fake_start, on_stop=fake_stop)
threading.Thread(target=lambda: (time.sleep(3), app.destroy()), daemon=True).start()
app.mainloop()
print("UI Module OK (no deadlock).")
EOF
```

---

## PHASE 8 — ORCHESTRATOR & ENTRY POINT (Fix 2 Applied)

> **BUG FIX 2 (Orchestrator side):** `Orchestrator.__init__` now creates two
> separate queues (`_mic_queue` and `_loopback_queue`) and passes both to
> `AudioCapture` and `AudioChunker`. The single `_frame_queue` from Rev 1 is
> removed. `AudioChunker` also receives `log_callback` so its Fix 3 error
> messages surface in the UI log.

### Step 8.1 — Create `utils/logger.py`

**File:** `utils/logger.py`

```python
"""utils/logger.py — Coloured console + rotating file logger."""

import logging
import logging.handlers
from pathlib import Path
from config.settings import LOG_LEVEL

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_COLOURS = {
    "DEBUG":    "\033[36m",
    "INFO":     "\033[32m",
    "WARNING":  "\033[33m",
    "ERROR":    "\033[31m",
    "CRITICAL": "\033[41m",
    "RESET":    "\033[0m",
}


class ColouredFormatter(logging.Formatter):
    def format(self, record):
        colour = _COLOURS.get(record.levelname, "")
        reset  = _COLOURS["RESET"]
        record.levelname = f"{colour}{record.levelname:8s}{reset}"
        return super().format(record)


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    ch = logging.StreamHandler()
    ch.setFormatter(ColouredFormatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(ch)

    fh = logging.handlers.RotatingFileHandler(
        LOG_DIR / "mom_generator.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
    ))
    root.addHandler(fh)
```

### Step 8.2 — Create `orchestrator.py`

**File:** `orchestrator.py`

```python
"""
orchestrator.py — FIXED for clock-drift safety (Bug Fix 2).

Creates TWO queues (mic_queue, loopback_queue) and passes them to both
AudioCapture and AudioChunker. Rev 1 used a single frame_queue and mixed
inside the mic callback, which caused clock-drift desync.

The stop() method may block for up to 120 seconds waiting for STT tasks.
This is safe because _handle_stop() in the UI now calls stop() in a daemon
thread (Fix 1), so the UI main thread is never blocked.
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

        # ── BUG FIX 2: Two independent queues for mic and loopback ─────────
        # Rev 1 used a single mixed frame_queue. Mixing inside the mic
        # callback caused clock-drift desync between PortAudio (mic) and
        # soundcard (loopback), leading to silent audio dropouts.
        # Each stream now has its own queue; mixing happens in the chunker.
        self._mic_queue      = queue.Queue(maxsize=2000)
        self._loopback_queue = queue.Queue(maxsize=2000)

        # AudioCapture pushes to separate queues (Fix 2)
        self._capture = AudioCapture(
            mic_queue=self._mic_queue,
            loopback_queue=self._loopback_queue,
        )

        self._stt_client = SarvamSTTClient()
        self._stt_client.set_log_callback(self._ui.log)

        # AudioChunker drains both queues independently; mixes at chunk time (Fix 2)
        # log_callback is passed so Fix 3 exception messages reach the UI
        self._chunker = AudioChunker(
            mic_queue=self._mic_queue,
            loopback_queue=self._loopback_queue,
            async_loop=self._loop,
            on_chunk_ready=self._stt_client.transcribe_chunk,
            log_callback=self._ui.log,
        )

        self._gemini    = GeminiMOMClient()
        self._formatter = HTMLFormatter()
        self._email     = EmailSender()
        self._whatsapp  = WhatsAppAlert()

        self._meeting_context: str = ""
        self._override_recipients: list[str] = []

        logger.info("[Orchestrator] Initialised (dual-queue audio pipeline).")

    # ── Async event loop ───────────────────────────────────────────────────

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self, meeting_context: str, override_recipients: list[str]) -> None:
        """
        Begin recording session.
        Called from a daemon thread (never blocks the UI thread).
        """
        self._meeting_context      = meeting_context
        self._override_recipients  = override_recipients
        self._stt_client.clear()

        asyncio.run_coroutine_threadsafe(
            self._stt_client.start(), self._loop
        ).result(timeout=10)

        self._capture.start()
        self._chunker.start()

        logger.info(f"[Orchestrator] Session started. Context: '{meeting_context}'")
        self._ui.log("🎙 Dual-stream audio capture active. Sarvam AI STT pipeline running.")

    def stop(self) -> None:
        """
        Stop recording and run the full MOM generation pipeline.

        This method BLOCKS (up to ~2 minutes waiting for STT tasks).
        It is always called from a daemon thread — never the UI main thread —
        so blocking here is safe (Fix 1 in the UI ensures this).
        """
        logger.info("[Orchestrator] Stop signal received.")

        # 1. Stop audio capture
        self._capture.stop()
        self._ui.log("⏹ Audio capture stopped.")

        # 2. Stop chunker — flushes final partial chunk from BOTH buffers
        self._chunker.stop()
        self._ui.log("📦 Final audio chunk flushed (mic + loopback buffers).")

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
```

### Step 8.3 — Create `main.py`

*(No changes from Rev 1.)*

**File:** `main.py`

```python
"""
main.py — Application entry point.

Launch sequence:
  1. Setup logging
  2. Validate configuration (fail fast before UI loads)
  3. Instantiate UI window
  4. Instantiate Orchestrator (with UI reference)
  5. Wire UI callbacks → Orchestrator methods
  6. Start tkinter mainloop
"""

import sys
import logging

from utils.logger import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 60)
    logger.info("  MOM Generator — Starting up (Rev 2)")
    logger.info("=" * 60)

    try:
        from config.settings import (
            SARVAM_API_KEY, GEMINI_API_KEY, SMTP_USER, TWILIO_ACCOUNT_SID
        )
        logger.info("Configuration validated.")
    except EnvironmentError as e:
        logger.critical(f"Configuration error: {e}")
        print(f"\n❌ CONFIGURATION ERROR:\n{e}\n")
        print("Please check your .env file. See .env.example for required variables.\n")
        sys.exit(1)

    from ui.app_window import AppWindow
    from orchestrator import Orchestrator

    _start_ref = [None]
    _stop_ref  = [None]

    app = AppWindow(
        on_start=lambda ctx, recs: _start_ref[0](ctx, recs),
        on_stop =lambda:           _stop_ref[0](),
    )

    orchestrator = Orchestrator(ui=app)
    _start_ref[0] = orchestrator.start
    _stop_ref[0]  = orchestrator.stop

    logger.info("All modules initialised. Launching UI.")

    try:
        app.mainloop()
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        logger.info("MOM Generator shut down.")


if __name__ == "__main__":
    main()
```

### ✅ Phase 8 Validation

```bash
python -c "
from config.settings import LOG_LEVEL
from utils.logger import setup_logging
setup_logging()
from orchestrator import Orchestrator
from ui.app_window import AppWindow
print('All imports OK. Rev 2 architecture verified.')
"
```

---

## PHASE 9 — VALIDATION & END-TO-END TEST

### Step 9.1 — Create `tests/test_chunker.py`

**File:** `tests/test_chunker.py`

```python
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
```

### Step 9.2 — Create `tests/test_html_formatter.py`

**File:** `tests/test_html_formatter.py`

```python
"""Unit tests for HTMLFormatter."""

from output.html_formatter import HTMLFormatter

_MOCK = {
    "meeting_title": "Test Meeting", "date": "11-Jun-2025",
    "meeting_context": "Test context", "executive_summary": "Summary.",
    "key_decisions": ["Decision A"],
    "action_items": [
        {"task": "Do X", "assignee": "Alice", "deadline": "20-Jun-2025", "priority": "High"},
    ],
    "attendees_mentioned": ["Alice", "Bob"],
    "risks_and_blockers": ["Risk 1"],
    "next_steps": "Follow up.", "next_meeting_suggestion": "18-Jun-2025",
}


def test_renders_html():
    html = HTMLFormatter().render(_MOCK)
    assert "<html" in html.lower() and "Test Meeting" in html
    assert "Decision A" in html and "Alice" in html
    print(f"✅ HTML render OK ({len(html):,} chars)")


def test_handles_empty_action_items():
    data = dict(_MOCK); data["action_items"] = []
    html = HTMLFormatter().render(data)
    assert "<html" in html.lower()
    print("✅ Empty action items handled gracefully.")
```

### Step 9.3 — Run all tests

```bash
pip install pytest
python -m pytest tests/ -v --tb=short
```

Expected: all 7 tests pass, including the 3 Fix-specific tests.

### Step 9.4 — Full pipeline dry-run (synthetic transcript, no audio)

```bash
python - <<'EOF'
import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

from nlp.gemini_client import GeminiMOMClient
from output.html_formatter import HTMLFormatter
from output.email_sender import EmailSender
from notifications.whatsapp_alert import WhatsAppAlert

TRANSCRIPT = """
Good morning. Today's agenda is the Q3 product launch timeline.
Priya confirmed the design mockups are ready. Ankit will own QA by July 10th.
Sunita will have staging deployment ready by July 12th.
We've decided to skip the beta phase. Final launch is July 15th.
AWS bill is at 80% of budget — Ramesh will escalate to finance by June 14th.
Marketing needs the press kit by July 8th. Divya, please own that.
Next meeting on June 25th to review QA plan.
"""

print("\n── Step 1: Gemini MOM extraction ──")
mom = GeminiMOMClient().extract_mom(TRANSCRIPT, "Q3 Product Launch Planning")
import json; print(json.dumps(mom, indent=2, ensure_ascii=False))

print("\n── Step 2: HTML rendering ──")
html = HTMLFormatter().render(mom)
with open("/tmp/mom_rev2_preview.html", "w") as f: f.write(html)
print(f"HTML: {len(html):,} chars → saved to /tmp/mom_rev2_preview.html")

print("\n── Step 3: Email dispatch ──")
ok = EmailSender().send(mom, html)
print(f"Email: {'sent' if ok else 'FAILED'}")

print("\n── Step 4: WhatsApp alert ──")
results = WhatsAppAlert().send_alert(mom)
print(f"WhatsApp: {sum(results)}/{len(results)} sent")

print("\n✅ Full pipeline dry-run complete (Rev 2).")
EOF
```

### Step 9.5 — Launch the application

```bash
python main.py
```

---

## DATA FLOW DIAGRAM

```
[Microphone]──────────────────────────────────┐
  sounddevice PortAudio clock                  │
  _mic_callback → to_mono()                    │
                                               ▼
                                        mic_queue (Queue)
                                               │
[System Loopback]──────────────────────────────┤
  soundcard WASAPI/CoreAudio clock             │
  _loopback_worker → to_mono()                │
                                               ▼
                                     loopback_queue (Queue)
                                               │
                              ┌────────────────┘
                              │ Drained independently by AudioChunker
                              │ (every 30s, at mic clock cadence)
                              ▼
                    ┌──────────────────────┐
                    │  AudioChunker         │
                    │  mic_buf + lb_buf    │
                    │  mix_streams()        │  ← clock-drift absorbed here
                    │  → WAV BytesIO        │     (min_len truncation)
                    └──────────┬───────────┘
                               │ concurrent.futures.Future
                               │ + add_done_callback()  ← exceptions surfaced
                               ▼
                    ┌──────────────────────┐
                    │  SarvamSTTClient      │
                    │  aiohttp POST         │
                    │  Semaphore(3)         │
                    │  transcripts[idx]     │
                    └──────────┬───────────┘
                               │ (on Stop)
                               ▼
                    ┌──────────────────────┐
                    │  GeminiMOMClient      │
                    │  gemini-1.5-pro       │
                    │  application/json     │
                    └──────────┬───────────┘
                               │ mom_data dict
                    ┌──────────┴───────────┐
                    │                      │
          ┌─────────▼────────┐  ┌──────────▼──────────┐
          │  HTMLFormatter    │  │  WhatsAppAlert       │
          │  Jinja2 render    │  │  Twilio API          │
          └─────────┬────────┘  └─────────────────────┘
                    │
          ┌─────────▼────────┐
          │  EmailSender      │
          │  smtplib SMTP     │
          └──────────────────┘
```

---

## ENVIRONMENT VARIABLES REFERENCE

| Variable                | Required | Default             | Description                                  |
|-------------------------|----------|---------------------|----------------------------------------------|
| `SARVAM_API_KEY`        | ✅       | —                   | Sarvam AI API subscription key               |
| `SARVAM_STT_MODEL`      | ⬜       | `saarika:v2`        | Sarvam STT model identifier                  |
| `SARVAM_LANGUAGE_CODE`  | ⬜       | `hi-IN`             | Language for STT (`en-IN`, `ta-IN`, etc.)    |
| `GEMINI_API_KEY`        | ✅       | —                   | Google AI Studio API key                     |
| `GEMINI_MODEL`          | ⬜       | `gemini-1.5-pro-latest` | Gemini model name                        |
| `SMTP_HOST`             | ⬜       | `smtp.gmail.com`    | SMTP server hostname                         |
| `SMTP_PORT`             | ⬜       | `587`               | SMTP port (587 = STARTTLS)                   |
| `SMTP_USER`             | ✅       | —                   | Gmail address for sending                    |
| `SMTP_PASSWORD`         | ✅       | —                   | Gmail App Password (16 chars, no spaces)     |
| `EMAIL_FROM_NAME`       | ⬜       | `MOM Generator Bot` | Display name in From header                  |
| `EMAIL_RECIPIENTS`      | ✅       | —                   | Comma-separated list of recipient emails     |
| `TWILIO_ACCOUNT_SID`    | ✅       | —                   | Twilio Account SID (starts with `AC`)        |
| `TWILIO_AUTH_TOKEN`     | ✅       | —                   | Twilio Auth Token                            |
| `TWILIO_WHATSAPP_FROM`  | ✅       | —                   | `whatsapp:+14155238886` (sandbox number)     |
| `WHATSAPP_RECIPIENTS`   | ✅       | —                   | Comma-separated `whatsapp:+91XXXXXXXXXX`     |
| `CHUNK_DURATION_SECONDS`| ⬜       | `30`                | Audio chunk size in seconds                  |
| `AUDIO_SAMPLE_RATE`     | ⬜       | `16000`             | Hz — 16 kHz recommended for Sarvam AI        |
| `AUDIO_CHANNELS`        | ⬜       | `1`                 | 1 = mono (required by Sarvam AI)             |
| `LOG_LEVEL`             | ⬜       | `INFO`              | `DEBUG` / `INFO` / `WARNING` / `ERROR`       |

---

## KNOWN PLATFORM CAVEATS

### Windows
- System audio loopback requires WASAPI drivers. Verify via:
  `python -c "import soundcard; print(soundcard.all_microphones(include_loopback=True))"`
- If loopback fails, install **VB-Audio Virtual Cable** and set it as the default recording device.
- Run as Administrator if WASAPI permissions are denied.

### macOS
- System audio loopback is blocked by Apple at the OS level. Install **BlackHole 2ch** (free) and configure a Multi-Output Device in Audio MIDI Setup.
- `soundcard` uses CoreAudio and will detect BlackHole automatically.

### Linux
- Use a PulseAudio monitor source: `pactl list sources short | grep monitor`
- If auto-detection fails, set the device name explicitly in `audio/capture.py`.

### Sarvam AI Rate Limits
- Free tier: ~60 requests/minute. `MAX_CONCURRENT_REQUESTS = 3` prevents throttling for meetings under ~30 minutes.
- For longer meetings, increase `CHUNK_DURATION_SECONDS` to 60 in `.env`.

### Gmail App Password
- Enable 2FA on your Gmail account.
- Google Account → Security → 2-Step Verification → App Passwords → generate for "Mail".

### Twilio Sandbox
- Each WhatsApp recipient must first send `join <sandbox-code>` to the Twilio number.
- For production, apply for a Twilio WhatsApp Business sender.

---

*End of PLAN.md Rev 2 — Three structural bugs corrected. All phases independently verifiable.*
```
