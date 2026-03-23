"""Logging setup — daily file rotation with 7-day retention."""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_CONSOLE_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_FILE_FMT = "%(asctime)s [%(levelname)-5s] %(name)s.%(funcName)s:%(lineno)d | %(message)s"


def setup_logging(log_dir: str | Path, console_level: str = "INFO") -> None:
    """Configure root logger with console + two file handlers (daily rotation, 7-day retention)."""
    log_path = Path(log_dir).expanduser()
    log_path.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Clear existing handlers to avoid duplicates on re-init
    root.handlers.clear()

    # --- Console handler ---
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, console_level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter(_CONSOLE_FMT))
    root.addHandler(console)

    # --- INFO file handler: tianshu.log ---
    info_handler = TimedRotatingFileHandler(
        filename=log_path / "tianshu.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    info_handler.setLevel(logging.INFO)
    info_handler.setFormatter(logging.Formatter(_FILE_FMT))
    info_handler.suffix = "%Y-%m-%d"
    root.addHandler(info_handler)

    # --- DEBUG file handler: debug.log ---
    debug_handler = TimedRotatingFileHandler(
        filename=log_path / "debug.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(logging.Formatter(_FILE_FMT))
    debug_handler.suffix = "%Y-%m-%d"
    root.addHandler(debug_handler)

    # Suppress noisy third-party loggers
    for noisy in ("httpx", "httpcore", "litellm", "uvicorn.access", "watchdog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
