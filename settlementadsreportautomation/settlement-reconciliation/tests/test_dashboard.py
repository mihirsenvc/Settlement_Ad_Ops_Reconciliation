"""Tests for compact chart aggregates cached for each reconciliation run."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from modules.dashboard import (
    calculate_ads_campaign_summary,
    filter_transactions,
    prepare_dashboard_data,
)
from utils.constants import STATUS_AMOUNT_MISMATCH, STATUS_MATCH


def test_dashboard_aggregates_status_campaign_and_daily_values() -> None:
    records = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2026-10-01", "2026-10-01", "2026-10-02"]),
            "Campaign_ID": ["C1", "C1", "C2"],
            "Campaign_Name": ["Campaign One", "Campaign One", None],
            "Transaction_ID": ["T1", "T2", "T3"],
            "Customer_ID": ["U1", "U2", "U3"],
            "Amount_Variance": [0.0, 2.0, -3.0],
            "Reconciliation_Status": [
                STATUS_MATCH,
                STATUS_AMOUNT_MISMATCH,
                STATUS_AMOUNT_MISMATCH,
            ],
        }
    )

    dashboard = prepare_dashboard_data(records)

    assert dashboard.status_counts.set_index("Record_Type")["Records"].to_dict() == {
        "Matched": 1,
        "Exception": 2,
    }
    assert dashboard.campaign_variance.set_index("Campaign_Label")[
        "Amount_Variance"
    ].to_dict() == {"Campaign One": 2.0, "C2": -3.0}
    assert dashboard.daily_counts["Records"].sum() == 3
    assert dashboard.daily_amounts[
        ["Settlement_Amount", "Wallet_Amount"]
    ].to_numpy().sum() == 0
    assert dashboard.daily_amounts["Day"].tolist() == [
        date(2026, 10, 1),
        date(2026, 10, 2),
    ]


def test_advanced_transaction_filters_combine_without_mutating_input() -> None:
    records = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2026-10-01", "2026-10-01", "2026-10-02"]),
            "Campaign_ID": ["C1", "C1", "C2"],
            "Campaign_Name": ["Campaign One", "Campaign One", None],
            "Transaction_ID": ["T1", "T2", "T3"],
            "Customer_ID": ["U1", "U2", "U3"],
            "Amount_Variance": [0.0, 2.0, -3.0],
            "Reconciliation_Status": [
                STATUS_MATCH,
                STATUS_AMOUNT_MISMATCH,
                STATUS_AMOUNT_MISMATCH,
            ],
        }
    )

    filtered = filter_transactions(
        records.set_axis([10, 20, 30]),
        statuses=[STATUS_AMOUNT_MISMATCH],
        campaign_text="c1",
        search_text="u2",
        date_range=(date(2026, 10, 1), date(2026, 10, 1)),
    )

    assert filtered["Transaction_ID"].tolist() == ["T2"]
    assert filtered.index.tolist() == [20]
    assert len(records) == 3


def test_empty_status_filter_returns_no_transactions() -> None:
    records = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2026-10-01"]),
            "Campaign_ID": ["C1"],
            "Campaign_Name": ["Campaign One"],
            "Transaction_ID": ["T1"],
            "Customer_ID": ["U1"],
            "Amount_Variance": [0.0],
            "Reconciliation_Status": [STATUS_MATCH],
        }
    )

    filtered = filter_transactions(records, [], "", "", None)

    assert filtered.empty


def test_ads_campaign_summary_uses_payment_date_and_linked_wallet_rows() -> None:
    records = pd.DataFrame(
        {
            "Date": pd.to_datetime(
                ["2026-10-01", "2026-10-02", "2026-10-02", "2026-10-02", "2026-10-02"]
            ),
            "Ads_Payment Date": [
                "2026-10-03",
                "2026-10-04",
                "2026-10-04",
                "2026-10-04",
                "2026-10-04",
            ],
            "Campaign_ID": ["ANPL", "ANPL", "PC", "ANPL", "ANPL"],
            "Settlement_Campaign_ID": ["ANPL", "ANPL", "PC", "ANPL", "ANPL"],
            "Wallet_Campaign_ID": ["ANPL", pd.NA, "PC", "ANPL", "ANPL"],
            "Transaction_ID": ["T1", "T2", "T3", "T4", "T5"],
            "Wallet_Transaction Id": ["T1", pd.NA, "T3", "T4", "OTHER-T5"],
            "Wallet_campaign_id": [
                "WALLET-CAMPAIGN-1",
                pd.NA,
                "WALLET-CAMPAIGN-3",
                "WALLET-CAMPAIGN-4",
                "WALLET-CAMPAIGN-5",
            ],
            "Settlement_Amount": [100.0, 50.0, 500.0, 150.0, 80.0],
            "Wallet_Amount": [100.0, None, 500.0, 100.0, 80.0],
            "Amount_Variance": [0.0, 50.0, 0.0, 50.0, 0.0],
            "Reconciliation_Status": [
                STATUS_MATCH,
                "STATUS_TRANSACTION_MISSING",
                STATUS_MATCH,
                STATUS_AMOUNT_MISMATCH,
                STATUS_MATCH,
            ],
        }
    )

    summary, details = calculate_ads_campaign_summary(
        records,
        ("ANPL", "PC"),
        (date(2026, 10, 3), date(2026, 10, 4)),
    )

    assert summary == {
        "successful_lookup_rows": 3,
        "matched_rows": 2,
        "ads_settlement_total": 750.0,
        "linked_wallet_total": 700.0,
        "campaign_net_variance": 50.0,
        "paired_net_variance": 50.0,
        "paired_absolute_variance": 50.0,
        "paired_variance_percentage": pytest.approx(100 / 15),
    }
    assert details["Transaction_ID"].tolist() == ["T1", "T3", "T4"]
    assert details.loc[0, "Wallet_Transaction Id"] == "T1"
    assert details.loc[0, "Wallet_campaign_id"] == "WALLET-CAMPAIGN-1"
    assert details["Lookup_Result"].eq("SUCCESS").all()
    assert STATUS_AMOUNT_MISMATCH in details["Reconciliation_Status"].tolist()
    assert not {"T2", "T5"}.intersection(details["Transaction_ID"])


def test_ads_campaign_summary_returns_wallet_campaign_for_exact_id_lookup() -> None:
    records = pd.DataFrame(
        {
            "Ads_Payment Date": ["2026-10-03"],
            "Campaign_ID": ["ANPL"],
            "Wallet_Transaction Id": ["T1"],
            "Wallet_Campaign_ID": ["PC"],
            "Transaction_ID": ["T1"],
            "Settlement_Amount": [100.0],
            "Wallet_Amount": [100.0],
            "Amount_Variance": [0.0],
            "Reconciliation_Status": [STATUS_AMOUNT_MISMATCH],
        }
    )

    summary, details = calculate_ads_campaign_summary(
        records,
        "ANPL",
        (date(2026, 10, 3), date(2026, 10, 3)),
    )

    assert summary["successful_lookup_rows"] == 1
    assert summary["linked_wallet_total"] == 100
    assert len(details) == 1
    assert details.loc[0, "Wallet_Campaign_ID"] == "PC"


def test_ads_campaign_summary_calculates_saral_campaign() -> None:
    transaction_id = "ADS_GMV_SETTLE_26090600003893499075"
    records = pd.DataFrame(
        {
            "Ads_Payment Date": ["2026-09-10"],
            "Campaign_ID": ["SARAL"],
            "Wallet_Transaction Id": [transaction_id],
            "Wallet_Campaign_ID": ["SARAL"],
            "Transaction_ID": [transaction_id],
            "Settlement_Amount": [125.5],
            "Wallet_Amount": [120.0],
            "Amount_Variance": [5.5],
            "Reconciliation_Status": [STATUS_AMOUNT_MISMATCH],
        }
    )

    summary, details = calculate_ads_campaign_summary(
        records,
        "SARAL",
        (date(2026, 9, 10), date(2026, 9, 10)),
    )

    assert summary["successful_lookup_rows"] == 1
    assert summary["campaign_net_variance"] == 5.5
    assert details["Transaction_ID"].tolist() == [transaction_id]


def test_campaign_summary_uses_report_date_when_ads_payment_date_is_unavailable() -> None:
    records = pd.DataFrame(
        {
            "Date": ["2026-09-06"],
            "Campaign_ID": ["PC"],
            "Wallet_Transaction Id": ["SPWT260906-PC"],
            "Wallet_Campaign_ID": ["PC"],
            "Transaction_ID": ["SPWT260906-PC"],
            "Settlement_Amount": [125.5],
            "Wallet_Amount": [120.0],
            "Amount_Variance": [5.5],
            "Reconciliation_Status": [STATUS_AMOUNT_MISMATCH],
        }
    )

    summary, details = calculate_ads_campaign_summary(
        records,
        "PC",
        (date(2026, 9, 6), date(2026, 9, 6)),
    )

    assert summary["successful_lookup_rows"] == 1
    assert summary["campaign_net_variance"] == 5.5
    assert details["Transaction_ID"].tolist() == ["SPWT260906-PC"]


def test_ads_campaign_summary_matches_transaction_ids_by_row_with_duplicate_indexes() -> None:
    records = pd.DataFrame(
        {
            "Ads_Payment Date": ["2026-10-03", "2026-10-03"],
            "Campaign_ID": ["ANPL", "PC"],
            "Wallet_Transaction Id": ["T1", "T2"],
            "Wallet_Campaign_ID": ["ANPL", "PC"],
            "Transaction_ID": ["T1", "T2"],
            "Settlement_Amount": [100.0, 200.0],
            "Wallet_Amount": [100.0, 200.0],
            "Amount_Variance": [0.0, 0.0],
            "Reconciliation_Status": [STATUS_MATCH, STATUS_MATCH],
        },
        index=[7, 7],
    )

    summary, details = calculate_ads_campaign_summary(
        records,
        ("ANPL", "PC"),
        (date(2026, 10, 3), date(2026, 10, 3)),
    )

    assert summary["successful_lookup_rows"] == 2
    assert details["Transaction_ID"].tolist() == ["T1", "T2"]


def test_ads_campaign_summary_rejects_missing_source_date() -> None:
    records = pd.DataFrame({"Campaign_ID": ["ANPL"]})

    with pytest.raises(ValueError, match="Date"):
        calculate_ads_campaign_summary(records, "ANPL", (date(2026, 10, 1), date(2026, 10, 1)))
