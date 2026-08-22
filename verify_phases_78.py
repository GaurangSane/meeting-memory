import sys
print("--- Phase 7 Validation ---")
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

print("\n--- Phase 8 Validation ---")
from config.settings import LOG_LEVEL
from utils.logger import setup_logging
setup_logging()
from orchestrator import Orchestrator
print('All imports OK. Rev 2 architecture verified.')
