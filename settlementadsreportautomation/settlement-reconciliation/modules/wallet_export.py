"""Normalize wallet transaction exports that include metadata before the CSV header."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import BinaryIO

import numpy as np
import pandas as pd

from modules.ads_settlement import campaign_id_from_transaction
from modules.validator import FileValidationError

_REQUIRED_WALLET_HEADERS = {
    "transactionid",
    "campaignid",
    "wallettransactiondate",
    "grossamountincloftax",
}


def _canonical_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def load_wallet_export(file_obj: BinaryIO, filename: str) -> pd.DataFrame:
    """Load a wallet CSV with metadata preamble and normalize its transaction columns."""
    if Path(filename).suffix.lower() != ".csv":
        raise FileValidationError("The wallet export format requires a .csv file.")

    wallet_id: str | None = None
    header_row: int | None = None
    try:
        file_obj.seek(0)
        for row_number in range(20):
            line = file_obj.readline()
            if not line:
                break
            fields = next(csv.reader([line.decode("utf-8-sig")]))
            indexes = {
                _canonical_header(value): column_index
                for column_index, value in enumerate(fields)
                if str(value).strip()
            }
            if (
                len(fields) >= 2
                and _canonical_header(fields[0]) == "walletidselected"
            ):
                wallet_id = str(fields[1]).strip() or None
            if _REQUIRED_WALLET_HEADERS.issubset(indexes):
                header_row = row_number
                break
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise FileValidationError(f"{filename}: unable to read wallet export header.") from exc
    if header_row is None:
        raise FileValidationError(
            f"{filename}: wallet export header was not found. Expected Transaction Id, "
            "campaign_id, Gross Amount (Incl. of Tax), and Wallet Transaction Date."
        )

    try:
        file_obj.seek(0)
        rows = pd.read_csv(
            file_obj,
            skiprows=header_row,
            dtype="string",
            keep_default_na=False,
        )
    except (OSError, ValueError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise FileValidationError(f"{filename}: unable to parse wallet transaction rows.") from exc
    header_indexes = {
        _canonical_header(column): index for index, column in enumerate(rows.columns)
    }

    def column_values(header: str) -> pd.Series:
        return rows.iloc[:, header_indexes[header]].astype("string").str.strip()

    transaction_id = column_values("transactionid")
    amount_text = column_values("grossamountincloftax")
    date_text = column_values("wallettransactiondate")
    campaign_id = column_values("campaignid")
    keep = transaction_id.ne("") | amount_text.ne("") | date_text.ne("")
    transaction_id = transaction_id.loc[keep].reset_index(drop=True)
    amount_text = amount_text.loc[keep].reset_index(drop=True)
    date_text = date_text.loc[keep].reset_index(drop=True)
    campaign_id = campaign_id.loc[keep].reset_index(drop=True)
    if transaction_id.empty:
        raise FileValidationError(f"{filename}: wallet export contains no transaction records.")

    parsed_dates = pd.to_datetime(
        date_text.str.slice(0, 10), errors="coerce"
    ).dt.normalize()
    invalid_dates = parsed_dates.isna()
    if invalid_dates.any():
        raise FileValidationError(
            f"{filename}: {int(invalid_dates.sum())} wallet row(s) have invalid or blank "
            "Wallet Transaction Date values."
        )
    amounts = pd.to_numeric(amount_text.str.replace(",", "", regex=False), errors="coerce")
    invalid_amounts = amounts.isna() | ~np.isfinite(amounts)
    if invalid_amounts.any():
        raise FileValidationError(
            f"{filename}: {int(invalid_amounts.sum())} wallet row(s) have invalid or blank "
            "Gross Amount (Incl. of Tax) values."
        )

    transaction_campaigns = transaction_id.map(campaign_id_from_transaction)
    normalized_campaigns = transaction_campaigns.fillna(campaign_id)
    result = pd.DataFrame(
        {
            "Date": parsed_dates,
            "Campaign_ID": normalized_campaigns,
            "Transaction_ID": transaction_id,
            "Customer_ID": pd.Series(wallet_id, index=transaction_id.index, dtype="string"),
            "Amount": amounts.astype("float64"),
        }
    )
    source_rows = rows.loc[keep].reset_index(drop=True)
    for column in source_rows.columns:
        result[f"Wallet_{column}"] = source_rows[column].astype("string").str.strip()
    result.loc[result["Campaign_ID"].eq(""), "Campaign_ID"] = pd.NA
    result.loc[result["Transaction_ID"].eq(""), "Transaction_ID"] = pd.NA
    result.loc[result["Customer_ID"].eq(""), "Customer_ID"] = pd.NA
    result.attrs["import_warnings"] = [
        "Wallet export metadata was skipped; Gross Amount (Incl. of Tax) was used as Amount.",
        "SPWT transaction suffixes and ADS_GMV_SETTLE identifiers were used as campaign "
        "categories; original wallet columns were retained in the master report.",
    ]
    return result
