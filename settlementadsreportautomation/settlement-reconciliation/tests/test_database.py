"""Tests for persisted run, upload, campaign, and audit history."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from modules.database_manager import DatabaseManager
from modules.reconciler import ReconciliationEngine


def test_run_persists_uploads_results_campaigns_and_execution_history(
    tmp_path: Path,
) -> None:
    settlement = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2026-10-01"]),
            "Campaign_ID": ["C1"],
            "Transaction_ID": ["T1"],
            "Customer_ID": ["U1"],
            "Amount": [12.0],
            "Settlement_Status": ["SETTLED"],
        }
    )
    wallet = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2026-10-01"]),
            "Campaign_ID": ["C1"],
            "Transaction_ID": ["T1"],
            "Customer_ID": ["U1"],
            "Amount": [12.0],
        }
    )
    result = ReconciliationEngine().reconcile(settlement, wallet)
    database_path = tmp_path / "audit.sqlite"
    manager = DatabaseManager(database_path)
    manager.initialize()
    run_id = manager.record_run(
        result.summary,
        result.metrics,
        {},
        ["settlement.csv"],
        user_session="session-123",
        upload_metadata=[
            {
                "file_role": "settlement",
                "file_name": "..\\private\\settlement.csv",
                "file_size_bytes": 200,
                "record_count": 1,
                "uploaded_at": result.execution_timestamp.isoformat(),
                "content_sha256": "a" * 64,
            }
        ],
        campaign_mapping=[("C1", "Campaign One", "Consumer")],
        warnings=["example warning"],
    )
    manager.record_audit_event(
        "ERROR",
        "INPUT_VALIDATION_FAILED",
        "session-123",
        {"message": "Invalid test file"},
    )

    with sqlite3.connect(database_path) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "uploads",
                "reconciliation_results",
                "execution_history",
                "campaign_master",
                "audit_logs",
                "user_activity",
            )
        }
        upload_name = connection.execute(
            "SELECT file_name FROM uploads WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        execution = connection.execute(
            "SELECT status, warnings_json FROM execution_history WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    assert all(counts[table] == 1 for table in counts if table != "audit_logs")
    assert counts["audit_logs"] == 2
    assert upload_name == "settlement.csv"
    assert execution[0] == "COMPLETED"
    assert "example warning" in execution[1]
