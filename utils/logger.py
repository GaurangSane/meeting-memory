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
