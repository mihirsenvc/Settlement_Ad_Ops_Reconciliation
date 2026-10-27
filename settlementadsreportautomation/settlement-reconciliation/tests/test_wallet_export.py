"""Tests for wallet CSV exports with metadata before the transaction header."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pandas as pd
import pytest

from app import _read_upload
from modules.reconciler import ReconciliationEngine
from modules.validator import FileValidationError
from modules.wallet_export import load_wallet_export


class _UploadedCsv(BytesIO):
    def __init__(self, content: bytes, name: str = "wallet-export.csv") -> None:
        super().__init__(content)
        self.name = name
        self.size = len(content)

    def getbuffer(self) -> memoryview:
        return super().getbuffer()


@pytest.fixture
def wallet_csv() -> bytes:
    return (
        b"Start Time,2025-12-17 00:00:00\n"
        b"End Time,2025-12-17 23:59:59\n"
        b"Time of Download,2026-10-23 04:24:14\n"
        b"Operation Selected,[ANPL_SETTLEMENT]\n"
        b"Wallet Id Selected,03a231b8ec164622\n"
        b"Transaction Id,Gross Amount (Incl. of Tax),campaign_id,Campaign,Operation,"
        b"Operation Sub Type,status,Net Amount (Excl. of Tax),Wallet Transaction Date,"
        b"Ads Service Date,Check\n"
        b"SPWT251217110353667749627ANPL,1853.33,4HUIDXXM7030,Keyword Planner,"
        b"ANPL_SETTLEMENT,,SUCCESS,1853.33,2025-12-17T16:33:53.761+05:30,"
        b"2025-12-15T05:30:00.000+05:30,#N/A\n"
        b"SPWT251217110352152675941ANPL,1990.52,4JI0SD81T7FJ,,ANPL_SETTLEMENT,,"
        b"SUCCESS,1990.52,2025-12-17T00:15:00.000+05:30,"
        b"2025-12-15T05:30:00.000+05:30,#N/A\n"
    )


def test_load_wallet_export_skips_metadata_and_maps_wallet_fields(wallet_csv: bytes) -> None:
    frame = load_wallet_export(BytesIO(wallet_csv), "wallet-export.csv")

    assert list(frame.columns[:5]) == [
        "Date",
        "Campaign_ID",
        "Transaction_ID",
        "Customer_ID",
        "Amount",
    ]
    assert len(frame) == 2
    assert frame.loc[0, "Date"] == pd.Timestamp("2025-12-17")
    assert frame.loc[0, "Campaign_ID"] == "ANPL"
    assert frame.loc[0, "Transaction_ID"] == "SPWT251217110353667749627ANPL"
    assert frame.loc[0, "Customer_ID"] == "03a231b8ec164622"
    assert frame.loc[0, "Amount"] == 1853.33
    assert frame.loc[0, "Wallet_campaign_id"] == "4HUIDXXM7030"
    assert frame.loc[0, "Wallet_Operation"] == "ANPL_SETTLEMENT"
    assert frame.attrs["import_warnings"]


def test_upload_reader_accepts_wallet_export_with_metadata(wallet_csv: bytes) -> None:
    frame = _read_upload(
        _UploadedCsv(wallet_csv),
        "wallet",
        SimpleNamespace(max_xlsx_expanded_size_mb=1024),
    )

    assert len(frame) == 2
    assert frame.loc[1, "Amount"] == 1990.52


def test_wallet_spwt_suffix_reconciles_to_ads_campaign_category(wallet_csv: bytes) -> None:
    wallet = load_wallet_export(BytesIO(wallet_csv), "wallet-export.csv")
    settlement = pd.DataFrame(
        {
            "Date": [pd.Timestamp("2025-12-17"), pd.Timestamp("2025-12-17")],
            "Campaign_ID": ["ANPL", "ANPL"],
            "Transaction_ID": [
                "SPWT251217110353667749627ANPL",
                "SPWT251217110352152675941ANPL",
            ],
            "Customer_ID": ["03a231b8ec164622", "03a231b8ec164622"],
            "Amount": [1853.33, 1990.52],
            "Settlement_Status": ["SETTLED", "SETTLED"],
            "Ads_Neft_ID": ["NEFT-1", "NEFT-1"],
        }
    )

    result = ReconciliationEngine().reconcile(settlement, wallet)

    assert result.records["Reconciliation_Status"].eq("STATUS_MATCH").all()
    assert result.records["Campaign_ID"].eq("ANPL").all()
    assert result.records["Ads_Neft_ID"].eq("NEFT-1").all()
    assert result.records["Wallet_Operation"].eq("ANPL_SETTLEMENT").all()


def test_wallet_ads_gmv_settle_identifier_reconciles_as_saral() -> None:
    transaction_id = "ADS_GMV_SETTLE_26090600003893499075"
    wallet_csv = (
        b"Wallet Id Selected,account-1\n"
        b"Transaction Id,Gross Amount (Incl. of Tax),campaign_id,Campaign,Operation,"
        b"Operation Sub Type,status,Net Amount (Excl. of Tax),Wallet Transaction Date\n"
        + (
            f"{transaction_id},125.50,source-campaign,Saral,GMV_SETTLEMENT,,SUCCESS,"
            "125.50,2026-09-06\n"
        ).encode()
    )
    wallet = load_wallet_export(BytesIO(wallet_csv), "wallet-export.csv")
    settlement = pd.DataFrame(
        {
            "Date": [pd.Timestamp("2026-09-06")],
            "Campaign_ID": ["SARAL"],
            "Transaction_ID": [transaction_id],
            "Customer_ID": ["account-1"],
            "Amount": [125.50],
            "Settlement_Status": ["SETTLED"],
        }
    )

    result = ReconciliationEngine().reconcile(settlement, wallet)

    assert wallet.loc[0, "Campaign_ID"] == "SARAL"
    assert result.records.loc[0, "Campaign_ID"] == "SARAL"
    assert result.records.loc[0, "Reconciliation_Status"] == "STATUS_MATCH"
    assert result.records.loc[0, "Settlement_Amount"] == 125.50


def test_wallet_export_rejects_missing_transaction_header() -> None:
    with pytest.raises(FileValidationError, match="wallet export header was not found"):
        load_wallet_export(BytesIO(b"Start Time,2025-12-17\nTransaction Id,Amount\n"), "wallet.csv")
