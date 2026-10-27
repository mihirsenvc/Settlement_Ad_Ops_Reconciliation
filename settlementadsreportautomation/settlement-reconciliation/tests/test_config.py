"""Tests for environment-backed runtime configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

import config


def test_relative_paths_resolve_from_project_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("DATABASE_PATH", "database/audit.sqlite")
    monkeypatch.setenv("LOG_PATH", "logs/app.log")
    monkeypatch.setenv("OUTPUT_PATH", "outputs")
    monkeypatch.delenv("AMOUNT_TOLERANCE", raising=False)
    monkeypatch.delenv("MAX_UPLOAD_SIZE_MB", raising=False)
    monkeypatch.delenv("MAX_XLSX_EXPANDED_SIZE_MB", raising=False)
    monkeypatch.delenv("LOG_MAX_BYTES", raising=False)
    monkeypatch.delenv("LOG_BACKUP_COUNT", raising=False)
    monkeypatch.delenv("TABLE_PAGE_SIZE", raising=False)

    settings = config.get_settings()

    assert settings.database_path == tmp_path / "database" / "audit.sqlite"
    assert settings.log_path == tmp_path / "logs" / "app.log"
    assert settings.output_path == tmp_path / "outputs"


def test_non_finite_amount_tolerance_is_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("DATABASE_PATH", "database/audit.sqlite")
    monkeypatch.setenv("LOG_PATH", "logs/app.log")
    monkeypatch.setenv("OUTPUT_PATH", "outputs")
    monkeypatch.setenv("AMOUNT_TOLERANCE", "nan")
    monkeypatch.delenv("MAX_UPLOAD_SIZE_MB", raising=False)
    monkeypatch.delenv("MAX_XLSX_EXPANDED_SIZE_MB", raising=False)
    monkeypatch.delenv("LOG_MAX_BYTES", raising=False)
    monkeypatch.delenv("LOG_BACKUP_COUNT", raising=False)
    monkeypatch.delenv("TABLE_PAGE_SIZE", raising=False)

    with pytest.raises(ValueError, match="must be finite"):
        config.get_settings()


def test_non_positive_xlsx_expansion_limit_is_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("DATABASE_PATH", "database/audit.sqlite")
    monkeypatch.setenv("LOG_PATH", "logs/app.log")
    monkeypatch.setenv("OUTPUT_PATH", "outputs")
    monkeypatch.setenv("MAX_XLSX_EXPANDED_SIZE_MB", "0")

    with pytest.raises(ValueError, match="MAX_XLSX_EXPANDED_SIZE_MB"):
        config.get_settings()
