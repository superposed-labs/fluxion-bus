from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(level: int = logging.INFO, filename: str | Path | None = None) -> None:
    handlers: list[logging.Handler] = []

    if filename:
        log_path = Path(filename)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # Limit file size to 5MB, keep 3 backup files
            file_handler = RotatingFileHandler(
                log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
            )
            handlers.append(file_handler)
        except Exception:
            pass

    # If stderr is a terminal (tty) or we don't have a filename, log to console.
    # Otherwise, avoid console logs to prevent launchd redirect files from growing.
    if not filename or sys.stderr.isatty():
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        handlers.append(stream_handler)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates on re-config
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    for h in handlers:
        root_logger.addHandler(h)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
