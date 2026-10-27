"""Tests for explainable executive insights."""

from __future__ import annotations

import pandas as pd

from modules.ai_insights import build_executive_summary
from modules.reconciler import ReconciliationEngine


def test_executive_summary_identifies_concentrated_variance_and_missing_rows() -> None:
    settlement = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2026-10-01", "2026-10-01"]),
            "Campaign_ID": ["C1", "C1"],
            "Transaction_ID": ["T1", "T2"],
            "Customer_ID": ["U1", "U2"],
            "Amount": [100.0, 50.0],
            "Settlement_Status": ["SETTLED", "SETTLED"],
        }
    )
    wallet = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2026-10-01"]),
            "Campaign_ID": ["C1"],
            "Transaction_ID": ["T1"],
            "Customer_ID": ["U1"],
            "Amount": [90.0],
        }
    )
    mapping = pd.DataFrame(
        {
            "Campaign_ID": ["C1"],
            "Campaign_Name": ["SARAL"],
            "Business_Unit": ["Consumer"],
        }
    )
    result = ReconciliationEngine().reconcile(settlement, wallet, mapping)

    executive = build_executive_summary(result)

    assert "0.0%" in executive.headline
    assert "SARAL" in executive.narrative
    assert "Wallet transactions missing" in executive.insights["Observation"].tolist()
    assert executive.campaign_summary.iloc[0]["Campaign_ID"] == "C1"
