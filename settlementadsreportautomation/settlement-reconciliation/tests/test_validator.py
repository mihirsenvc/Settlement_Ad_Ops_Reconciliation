"""Unit tests for report parsing and schema validation."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import pytest

from modules.profiler import profile_data
from modules.validator import (
    FileValidationError,
    load_report,
    validate_campaign_mapping,
    validate_report,
)


def test_load_report_normalizes_headers_and_preserves_transaction_ids() -> None:
    content = (
        b"date,campaign id,transaction id,customer_id,amount,settlement status\n"
        b"2026-10-01,C1,000012,U1,12.5,SETTLED\n"
    )
    frame = load_report(BytesIO(content), "settlement.csv", "settlement")

    assert frame.loc[0, "Transaction_ID"] == "000012"
    assert frame.loc[0, "Amount"] == 12.5


def test_load_report_reads_xlsx_from_existing_upload_stream() -> None:
    source = pd.DataFrame(
        {
            "Date": ["2026-10-01"],
            "Campaign_ID": ["C1"],
            "Transaction_ID": ["000012"],
            "Customer_ID": ["U1"],
            "Amount": ["12.5"],
            "Settlement_Status": ["SETTLED"],
        }
    )
    content = BytesIO()
    source.to_excel(content, index=False)
    payload = content.getvalue()
    stream = BytesIO(payload)

    validate_report(
        "settlement.xlsx",
        payload,
        "settlement",
        max_xlsx_expanded_size_bytes=1024 * 1024,
        file_stream=stream,
    )
    frame = load_report(stream, "settlement.xlsx", "settlement")

    assert stream.tell() > 0
    assert frame.loc[0, "Transaction_ID"] == "000012"


def test_load_report_preserves_reserved_na_transaction_id() -> None:
    content = (
        b"Date,Campaign_ID,Transaction_ID,Customer_ID,Amount,Settlement_Status\n"
        b"2026-10-01,C1,NA,U1,12.5,SETTLED\n"
    )
    frame = load_report(BytesIO(content), "settlement.csv", "settlement")

    assert frame.loc[0, "Transaction_ID"] == "NA"


def test_validate_report_rejects_unsupported_extensions() -> None:
    with pytest.raises(FileValidationError, match="only .csv and .xlsx"):
        validate_report("report.xls", b"content", "wallet")


def test_validate_report_rejects_xlsx_expanding_past_configured_limit() -> None:
    compressed = BytesIO()
    with ZipFile(compressed, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", "x" * 100)
    compressed.seek(0)

    with pytest.raises(FileValidationError, match="expands beyond"):
        validate_report(
            "report.xlsx",
            compressed.getvalue(),
            "wallet",
            max_xlsx_expanded_size_bytes=50,
        )


def test_campaign_mapping_does_not_require_a_date_column() -> None:
    frame = load_report(
        BytesIO(b"Campaign_ID,Campaign_Name,Business_Unit\nC1,Rewards,Consumer\n"),
        "campaign.csv",
        "campaign",
    )
    validate_campaign_mapping(frame)


def test_campaign_mapping_rejects_duplicate_ids() -> None:
    frame = pd.DataFrame(
        {
            "Campaign_ID": ["C1", "C1"],
            "Campaign_Name": ["One", "One"],
            "Business_Unit": ["Consumer", "Consumer"],
        }
    )
    with pytest.raises(FileValidationError, match="must be unique"):
        validate_campaign_mapping(frame)


def test_load_report_rejects_duplicate_normalized_headers() -> None:
    content = (
        b"Date,date,Campaign_ID,Transaction_ID,Customer_ID,Amount,Settlement_Status\n"
        b"2026-10-01,2026-10-01,C1,T1,U1,10,SETTLED\n"
    )
    with pytest.raises(FileValidationError, match="duplicate column"):
        load_report(BytesIO(content), "settlement.csv", "settlement")


def test_load_report_rejects_non_finite_amounts() -> None:
    content = (
        b"Date,Campaign_ID,Transaction_ID,Customer_ID,Amount,Settlement_Status\n"
        b"2026-10-01,C1,T1,U1,inf,SETTLED\n"
    )
    with pytest.raises(FileValidationError, match="non-finite"):
        load_report(BytesIO(content), "settlement.csv", "settlement")


def test_null_transaction_ids_are_not_profiled_as_duplicates() -> None:
    frame = pd.DataFrame({"Transaction_ID": [pd.NA, pd.NA, "T1", "T1"]})

    profile = profile_data(frame)

    assert profile["duplicate_transaction_ids"] == 2
