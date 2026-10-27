"""Application logging configuration and logger factory."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(
    log_path: Path,
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """Configure console logging and a size-limited rotating file handler."""
    if max_bytes < 1 or backup_count < 0:
        raise ValueError("max_bytes must be positive and backup_count cannot be negative.")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if getattr(root, "_settlement_configured", False):
        return
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Unsupported log level: {level}.")
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.setLevel(numeric_level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    setattr(root, "_settlement_configured", True)


def get_logger(name: str) -> logging.Logger:
    """Return a named application logger."""
    return logging.getLogger(name)
