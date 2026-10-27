"""SQLite persistence for audit and user activity records."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Iterable
from uuid import uuid4

from utils.logger import get_logger

LOGGER = get_logger(__name__)


class DatabaseManager:
    """Encapsulate SQLite connection lifecycle and audit writes."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Open a configured connection and always close it after the transaction."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create audit tables and indexes if they do not exist."""
        schema_path = Path(__file__).resolve().parents[1] / "database" / "schema.sql"
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(schema_path.read_text(encoding="utf-8"))

    def record_run(
        self,
        summary: Any,
        metrics: dict[str, Any],
        profiles: dict[str, Any],
        source_files: list[str],
        actor: str = "streamlit_user",
        user_session: str = "unknown",
        upload_metadata: list[dict[str, Any]] | None = None,
        warnings: list[str] | None = None,
        started_at: datetime | None = None,
        campaign_mapping: Iterable[tuple[str, str, str]] | None = None,
    ) -> str:
        """Persist a reconciliation audit record and user activity event."""
        run_id = str(uuid4())
        completed = datetime.now(timezone.utc)
        executed_at = completed.isoformat()
        started = started_at or completed
        warning_list = warnings or []
        payload = json.dumps(
            {
                "summary": summary.to_dict(orient="records"),
                "metrics": metrics,
                "profiles": profiles,
                "source_files": source_files,
            },
            default=str,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO reconciliation_runs
                       (run_id, executed_at, actor, source_files, total_records, matched_records,
                        unmatched_records, variance_amount, processing_seconds, result_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        executed_at,
                        actor,
                        json.dumps(source_files),
                        metrics["total_records"],
                        metrics["matched_records"],
                        metrics["unmatched_records"],
                        metrics["variance_amount"],
                        metrics["processing_seconds"],
                        payload,
                    ),
                )
                connection.execute(
                    """INSERT INTO reconciliation_results
                       (run_id, total_settlement_amount, total_wallet_amount, matched_amount,
                        unmatched_amount, variance_amount, metrics_json, summary_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        metrics["total_settlement_amount"],
                        metrics["total_wallet_amount"],
                        metrics["matched_amount"],
                        metrics["unmatched_amount"],
                        metrics["variance_amount"],
                        json.dumps(metrics, default=str),
                        json.dumps(summary.to_dict(orient="records"), default=str),
                    ),
                )
                for upload in upload_metadata or []:
                    connection.execute(
                        """INSERT INTO uploads
                           (upload_id, run_id, file_role, file_name, file_size_bytes,
                            record_count, uploaded_at, content_sha256)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(uuid4()),
                            run_id,
                            upload["file_role"],
                            Path(upload["file_name"]).name,
                            upload["file_size_bytes"],
                            upload["record_count"],
                            upload["uploaded_at"],
                            upload["content_sha256"],
                        ),
                    )
                if campaign_mapping is not None:
                    connection.executemany(
                        """INSERT INTO campaign_master
                           (campaign_id, campaign_name, business_unit, updated_at)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(campaign_id) DO UPDATE SET
                             campaign_name = excluded.campaign_name,
                             business_unit = excluded.business_unit,
                             updated_at = excluded.updated_at""",
                        (
                            (*campaign, executed_at)
                            for campaign in campaign_mapping
                        ),
                    )
                connection.execute(
                    """INSERT INTO user_activity
                       (event_id, occurred_at, actor, action, run_id, details)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid4()),
                        executed_at,
                        actor,
                        "RECONCILIATION_COMPLETED",
                        run_id,
                        json.dumps({"source_files": source_files}),
                    ),
                )
                connection.execute(
                    """INSERT INTO audit_logs
                       (event_id, occurred_at, severity, event_type, user_session, run_id, details)
                       VALUES (?, ?, 'INFO', ?, ?, ?, ?)""",
                    (
                        str(uuid4()),
                        executed_at,
                        "RECONCILIATION_COMPLETED",
                        user_session,
                        run_id,
                        json.dumps(
                            {
                                "source_files": source_files,
                                "warnings": warning_list,
                                "record_count": metrics["total_records"],
                            }
                        ),
                    ),
                )
                connection.execute(
                    """INSERT INTO execution_history
                       (execution_id, run_id, user_session, started_at, completed_at, status,
                        duration_seconds, errors_json, warnings_json)
                       VALUES (?, ?, ?, ?, ?, 'COMPLETED', ?, '[]', ?)""",
                    (
                        str(uuid4()),
                        run_id,
                        user_session,
                        started.isoformat(),
                        executed_at,
                        max(0.0, (completed - started).total_seconds()),
                        json.dumps(warning_list),
                    ),
                )
        except sqlite3.Error:
            LOGGER.exception("Failed to persist reconciliation audit for run %s", run_id)
            raise
        return run_id

    def record_audit_event(
        self,
        severity: str,
        event_type: str,
        user_session: str,
        details: dict[str, Any],
    ) -> None:
        """Persist validation warnings or operational errors without source payloads."""
        normalized_severity = severity.upper()
        if normalized_severity not in {"INFO", "WARNING", "ERROR"}:
            raise ValueError(f"Unsupported audit severity: {severity}.")
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO audit_logs
                       (event_id, occurred_at, severity, event_type, user_session, run_id, details)
                       VALUES (?, ?, ?, ?, ?, NULL, ?)""",
                    (
                        str(uuid4()),
                        datetime.now(timezone.utc).isoformat(),
                        normalized_severity,
                        event_type,
                        user_session,
                        json.dumps(details, default=str),
                    ),
                )
        except sqlite3.Error:
            LOGGER.exception("Failed to persist audit event %s", event_type)
            raise

    def record_activity(self, actor: str, action: str, details: dict[str, Any]) -> None:
        """Persist a user activity event outside a reconciliation run."""
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO user_activity
                       (event_id, occurred_at, actor, action, run_id, details)
                       VALUES (?, ?, ?, ?, NULL, ?)""",
                    (
                        str(uuid4()),
                        datetime.now(timezone.utc).isoformat(),
                        actor,
                        action,
                        json.dumps(details, default=str),
                    ),
                )
        except sqlite3.Error:
            LOGGER.exception("Failed to persist activity event %s for actor %s", action, actor)
            raise
