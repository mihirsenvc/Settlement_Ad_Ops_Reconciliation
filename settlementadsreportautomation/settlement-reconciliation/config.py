"""Environment-backed application settings."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def _configured_path(name: str, default: Path) -> Path:
    """Resolve a configured path relative to the project unless it is absolute."""
    configured = Path(os.getenv(name, str(default))).expanduser()
    return configured if configured.is_absolute() else PROJECT_ROOT / configured


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration for the reconciliation application."""

    environment: str
    database_path: Path
    log_path: Path
    output_path: Path
    log_level: str
    log_max_bytes: int
    log_backup_count: int
    amount_tolerance: float
    max_upload_size_mb: int
    max_xlsx_expanded_size_mb: int
    table_page_size: int


def get_settings() -> Settings:
    """Build settings from environment variables and ensure runtime folders exist."""
    database_path = _configured_path(
        "DATABASE_PATH", PROJECT_ROOT / "database" / "sqlite.db"
    )
    log_path = _configured_path("LOG_PATH", PROJECT_ROOT / "logs" / "application.log")
    output_path = _configured_path("OUTPUT_PATH", PROJECT_ROOT / "outputs")
    for directory in (database_path.parent, log_path.parent, output_path):
        directory.mkdir(parents=True, exist_ok=True)
    tolerance = float(os.getenv("AMOUNT_TOLERANCE", "0.01"))
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("AMOUNT_TOLERANCE must be finite and zero or greater.")
    max_upload_size_mb = int(os.getenv("MAX_UPLOAD_SIZE_MB", "200"))
    if max_upload_size_mb < 1:
        raise ValueError("MAX_UPLOAD_SIZE_MB must be at least 1.")
    max_xlsx_expanded_size_mb = int(
        os.getenv("MAX_XLSX_EXPANDED_SIZE_MB", "1024")
    )
    if max_xlsx_expanded_size_mb < 1:
        raise ValueError("MAX_XLSX_EXPANDED_SIZE_MB must be at least 1.")
    table_page_size = int(os.getenv("TABLE_PAGE_SIZE", "500"))
    if table_page_size < 1:
        raise ValueError("TABLE_PAGE_SIZE must be at least 1.")
    log_max_bytes = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))
    log_backup_count = int(os.getenv("LOG_BACKUP_COUNT", "5"))
    if log_max_bytes < 1 or log_backup_count < 0:
        raise ValueError("LOG_MAX_BYTES must be positive and LOG_BACKUP_COUNT cannot be negative.")
    return Settings(
        environment=os.getenv("APP_ENV", "development"),
        database_path=database_path,
        log_path=log_path,
        output_path=output_path,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_max_bytes=log_max_bytes,
        log_backup_count=log_backup_count,
        amount_tolerance=tolerance,
        max_upload_size_mb=max_upload_size_mb,
        max_xlsx_expanded_size_mb=max_xlsx_expanded_size_mb,
        table_page_size=table_page_size,
    )
