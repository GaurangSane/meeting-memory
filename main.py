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
        on_start=lambda ctx, recs, mic_dev: _start_ref[0](ctx, recs, mic_dev),
        on_stop =lambda:                    _stop_ref[0](),
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
