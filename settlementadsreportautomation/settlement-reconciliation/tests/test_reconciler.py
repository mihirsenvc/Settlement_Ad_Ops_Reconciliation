"""Unit tests for reconciliation classifications and metrics."""

from __future__ import annotations

import pandas as pd
import pytest

from modules.reconciler import ReconciliationEngine
from utils.constants import (
    STATUS_AMOUNT_MISMATCH,
    STATUS_CAMPAIGN_MISMATCH,
    STATUS_DATE_MISMATCH,
    STATUS_DUPLICATE,
    STATUS_MATCH,
    STATUS_SETTLEMENT_MISSING,
    STATUS_TRANSACTION_MISSING,
)


def _date(values: list[str]) -> pd.Series:
    return pd.to_datetime(values)


def test_reconciles_matched_mismatched_and_missing_transactions() -> None:
    settlement = pd.DataFrame(
        {
            "Date": _date(["2026-10-01", "2026-10-01", "2026-10-01"]),
            "Campaign_ID": ["C1", "C1", "C1"],
            "Transaction_ID": ["T1", "T2", "T3"],
            "Customer_ID": ["U1", "U2", "U3"],
            "Amount": [10.0, 20.0, 30.0],
            "Settlement_Status": ["SETTLED"] * 3,
        }
    )
    wallet = pd.DataFrame(
        {
            "Date": _date(["2026-10-01", "2026-10-01", "2026-10-01"]),
            "Campaign_ID": ["C1", "C1", "C1"],
            "Transaction_ID": ["T1", "T2", "T4"],
            "Customer_ID": ["U1", "U2", "U4"],
            "Amount": [10.0, 21.0, 40.0],
        }
    )

    result = ReconciliationEngine().reconcile(settlement, wallet)
    statuses = result.records.set_index("Transaction_ID")["Reconciliation_Status"].to_dict()

    assert statuses == {
        "T1": STATUS_MATCH,
        "T2": STATUS_AMOUNT_MISMATCH,
        "T3": STATUS_TRANSACTION_MISSING,
        "T4": STATUS_SETTLEMENT_MISSING,
    }
    assert result.metrics["matched_records"] == 1
    assert result.metrics["variance_amount"] == -11.0
    assert result.metrics["paired_records"] == 2
    assert result.metrics["paired_variance_amount"] == -1.0
    assert result.metrics["paired_absolute_variance_amount"] == 1.0
    assert result.metrics["paired_variance_percentage"] == pytest.approx(100 / 31)


def test_duplicate_ids_are_reported_without_cartesian_join() -> None:
    settlement = pd.DataFrame(
        {
            "Date": _date(["2026-10-01", "2026-10-01"]),
            "Campaign_ID": ["C1", "C1"],
            "Transaction_ID": ["T1", "T1"],
            "Customer_ID": ["U1", "U1"],
            "Amount": [10.0, 10.0],
            "Settlement_Status": ["SETTLED", "SETTLED"],
        }
    )
    wallet = pd.DataFrame(
        {
            "Date": _date(["2026-10-01"]),
            "Campaign_ID": ["C1"],
            "Transaction_ID": ["T1"],
            "Customer_ID": ["U1"],
            "Amount": [10.0],
        }
    )

    result = ReconciliationEngine().reconcile(settlement, wallet)

    assert len(result.records) == 3
    assert result.records["Reconciliation_Status"].eq(STATUS_DUPLICATE).all()
    assert result.records["Amount_Variance"].sum() == result.metrics["variance_amount"]


def test_campaign_and_date_mismatches_are_classified() -> None:
    settlement = pd.DataFrame(
        {
            "Date": _date(["2026-10-01", "2026-10-01"]),
            "Campaign_ID": ["C1", "C1"],
            "Transaction_ID": ["T1", "T2"],
            "Customer_ID": ["U1", "U2"],
            "Amount": [10.0, 20.0],
            "Settlement_Status": ["SETTLED", "SETTLED"],
        }
    )
    wallet = pd.DataFrame(
        {
            "Date": _date(["2026-10-01", "2026-10-02"]),
            "Campaign_ID": ["C2", "C1"],
            "Transaction_ID": ["T1", "T2"],
            "Customer_ID": ["U1", "U2"],
            "Amount": [11.0, 20.0],
        }
    )

    result = ReconciliationEngine().reconcile(settlement, wallet)

    statuses = result.records.set_index("Transaction_ID")["Reconciliation_Status"]
    assert statuses["T1"] == STATUS_CAMPAIGN_MISMATCH
    assert statuses["T2"] == STATUS_DATE_MISMATCH


def test_missing_side_variance_is_signed_and_included() -> None:
    settlement = pd.DataFrame(
        {
            "Date": _date(["2026-10-01"]),
            "Campaign_ID": ["C1"],
            "Transaction_ID": ["T1"],
            "Customer_ID": ["U1"],
            "Amount": [12.0],
            "Settlement_Status": ["SETTLED"],
        }
    )
    wallet = pd.DataFrame(
        {
            "Date": _date(["2026-10-01"]),
            "Campaign_ID": ["C1"],
            "Transaction_ID": ["T2"],
            "Customer_ID": ["U2"],
            "Amount": [7.0],
        }
    )

    result = ReconciliationEngine().reconcile(settlement, wallet)
    variances = result.records.set_index("Transaction_ID")["Amount_Variance"].to_dict()

    assert variances == {"T1": 12.0, "T2": -7.0}
    assert sum(variances.values()) == result.metrics["variance_amount"]


def test_one_sided_null_campaign_is_a_campaign_mismatch() -> None:
    settlement = pd.DataFrame(
        {
            "Date": _date(["2026-10-01"]),
            "Campaign_ID": [pd.NA],
            "Transaction_ID": ["T1"],
            "Customer_ID": ["U1"],
            "Amount": [10.0],
            "Settlement_Status": ["SETTLED"],
        }
    )
    wallet = pd.DataFrame(
        {
            "Date": _date(["2026-10-01"]),
            "Campaign_ID": ["C1"],
            "Transaction_ID": ["T1"],
            "Customer_ID": ["U1"],
            "Amount": [10.0],
        }
    )

    result = ReconciliationEngine().reconcile(settlement, wallet)

    assert result.records.iloc[0]["Reconciliation_Status"] == STATUS_CAMPAIGN_MISMATCH


def test_duplicate_campaign_mapping_keys_are_rejected() -> None:
    settlement = pd.DataFrame(
        {
            "Date": _date(["2026-10-01"]),
            "Campaign_ID": ["C1"],
            "Transaction_ID": ["T1"],
            "Customer_ID": ["U1"],
            "Amount": [10.0],
            "Settlement_Status": ["SETTLED"],
        }
    )
    mapping = pd.DataFrame(
        {
            "Campaign_ID": ["C1", "C1"],
            "Campaign_Name": ["One", "Conflicting"],
            "Business_Unit": ["Consumer", "Consumer"],
        }
    )

    with pytest.raises(ValueError, match="must be present and unique"):
        ReconciliationEngine().reconcile(settlement, settlement.drop(columns="Settlement_Status"), mapping)
