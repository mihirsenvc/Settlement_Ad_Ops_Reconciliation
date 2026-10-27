"""Vectorized settlement-to-wallet reconciliation services."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

from utils.constants import (
    REQUIRED_COLUMNS,
    STATUS_AMOUNT_MISMATCH,
    STATUS_CAMPAIGN_MISMATCH,
    STATUS_DATE_MISMATCH,
    STATUS_DUPLICATE,
    STATUS_MATCH,
    STATUS_SETTLEMENT_MISSING,
    STATUS_TRANSACTION_MISSING,
)
from utils.logger import get_logger

LOGGER = get_logger(__name__)


@dataclass(slots=True)
class ReconciliationResult:
    """Reconciled rows, aggregate metrics, and execution metadata."""

    records: pd.DataFrame
    summary: pd.DataFrame
    metrics: dict[str, Any]
    execution_timestamp: datetime


class ReconciliationEngine:
    """Compare settlement and wallet rows using a transaction-ID keyed outer join."""

    def __init__(self, amount_tolerance: float = 0.01) -> None:
        if not math.isfinite(amount_tolerance) or amount_tolerance < 0:
            raise ValueError("amount_tolerance must be finite and zero or greater.")
        self.amount_tolerance = amount_tolerance

    def reconcile(
        self,
        settlement: pd.DataFrame,
        wallet: pd.DataFrame,
        campaign_mapping: pd.DataFrame | None = None,
    ) -> ReconciliationResult:
        """Reconcile reports, preserving duplicate source rows without many-to-many joins."""
        started = perf_counter()
        settlement_columns = [
            *REQUIRED_COLUMNS["settlement"],
            *[column for column in settlement if column not in REQUIRED_COLUMNS["settlement"]],
        ]
        wallet_columns = [
            *REQUIRED_COLUMNS["wallet"],
            *[column for column in wallet if column not in REQUIRED_COLUMNS["wallet"]],
        ]
        settlement = settlement.loc[:, settlement_columns]
        wallet = wallet.loc[:, wallet_columns]
        settlement_keys = settlement["Transaction_ID"].astype("string").str.strip()
        wallet_keys = wallet["Transaction_ID"].astype("string").str.strip()
        settlement_duplicate_mask = self._duplicate_mask(settlement_keys)
        wallet_duplicate_mask = self._duplicate_mask(wallet_keys)
        duplicate_keys = pd.Index(
            pd.concat(
                [
                    settlement_keys.loc[settlement_duplicate_mask],
                    wallet_keys.loc[wallet_duplicate_mask],
                ],
                ignore_index=True,
            ).unique()
        )
        settlement_rows = settlement.assign(Transaction_ID=settlement_keys)
        wallet_rows = wallet.assign(Transaction_ID=wallet_keys)
        settlement_duplicate_mask |= settlement_keys.isin(duplicate_keys)
        wallet_duplicate_mask |= wallet_keys.isin(duplicate_keys)
        duplicate_records = self._duplicate_records(
            settlement_rows,
            wallet_rows,
            settlement_duplicate_mask,
            wallet_duplicate_mask,
        )
        settlement_unique = settlement_rows.loc[~settlement_duplicate_mask].copy()
        wallet_unique = wallet_rows.loc[~wallet_duplicate_mask].copy()

        # Null IDs are deliberately source-qualified so unrelated blank-key rows never match.
        settlement_unique["_join_id"] = (
            "id:" + settlement_unique["Transaction_ID"].astype("string")
        )
        wallet_unique["_join_id"] = "id:" + wallet_unique["Transaction_ID"].astype("string")
        settlement_null = settlement_unique["Transaction_ID"].isna()
        wallet_null = wallet_unique["Transaction_ID"].isna()
        settlement_unique.loc[settlement_null, "_join_id"] = [
            f"settlement-null:{index}" for index in settlement_unique.index[settlement_null]
        ]
        wallet_unique.loc[wallet_null, "_join_id"] = [
            f"wallet-null:{index}" for index in wallet_unique.index[wallet_null]
        ]

        joined = settlement_unique.merge(
            wallet_unique,
            on="_join_id",
            how="outer",
            suffixes=("_Settlement", "_Wallet"),
            indicator=True,
            validate="one_to_one",
        )
        records = self._format_records(joined)
        if not duplicate_records.empty:
            records = pd.concat([records, duplicate_records], ignore_index=True, sort=False)
        records = self._add_campaign_metadata(records, campaign_mapping)
        records = records.reindex(columns=self._output_columns(records))
        summary = self._build_summary(records)
        metrics = self._build_metrics(records, settlement_rows, wallet_rows, started)
        metrics["amount_tolerance"] = self.amount_tolerance
        result = ReconciliationResult(
            records=records,
            summary=summary,
            metrics=metrics,
            execution_timestamp=datetime.now(timezone.utc),
        )
        LOGGER.info(
            "Reconciliation completed: %s rows, %s matched, %s exceptions in %.3fs",
            len(records),
            metrics["matched_records"],
            metrics["unmatched_records"],
            metrics["processing_seconds"],
        )
        return result

    @staticmethod
    def _duplicate_mask(transaction_ids: pd.Series) -> pd.Series:
        """Mark repeated non-blank IDs within a source."""
        return transaction_ids.notna() & transaction_ids.duplicated(keep=False)

    @staticmethod
    def _duplicate_records(
        settlement: pd.DataFrame,
        wallet: pd.DataFrame,
        settlement_mask: pd.Series,
        wallet_mask: pd.Series,
    ) -> pd.DataFrame:
        """Create one exception row per source row belonging to a duplicate transaction ID."""
        settlement_duplicates = settlement.loc[settlement_mask]
        wallet_duplicates = wallet.loc[wallet_mask]
        settlement_records = pd.DataFrame(
            {
                "Date": settlement_duplicates["Date"].to_numpy(),
                "Campaign_ID": settlement_duplicates["Campaign_ID"].to_numpy(),
                "Settlement_Campaign_ID": settlement_duplicates["Campaign_ID"].to_numpy(),
                "Wallet_Campaign_ID": pd.NA,
                "Transaction_ID": settlement_duplicates["Transaction_ID"].to_numpy(),
                "Customer_ID": settlement_duplicates["Customer_ID"].to_numpy(),
                "Settlement_Date": settlement_duplicates["Date"].to_numpy(),
                "Wallet_Date": pd.NaT,
                "Settlement_Amount": settlement_duplicates["Amount"].to_numpy(),
                "Wallet_Amount": np.nan,
                "Settlement_Status": settlement_duplicates["Settlement_Status"].to_numpy(),
                "Amount_Variance": settlement_duplicates["Amount"].to_numpy(),
                "Reconciliation_Status": STATUS_DUPLICATE,
                "Duplicate_Source": "settlement",
                "Other_Source": "wallet",
            }
        )
        wallet_records = pd.DataFrame(
            {
                "Date": wallet_duplicates["Date"].to_numpy(),
                "Campaign_ID": wallet_duplicates["Campaign_ID"].to_numpy(),
                "Settlement_Campaign_ID": pd.NA,
                "Wallet_Campaign_ID": wallet_duplicates["Campaign_ID"].to_numpy(),
                "Transaction_ID": wallet_duplicates["Transaction_ID"].to_numpy(),
                "Customer_ID": wallet_duplicates["Customer_ID"].to_numpy(),
                "Settlement_Date": pd.NaT,
                "Wallet_Date": wallet_duplicates["Date"].to_numpy(),
                "Settlement_Amount": np.nan,
                "Wallet_Amount": wallet_duplicates["Amount"].to_numpy(),
                "Settlement_Status": pd.NA,
                "Amount_Variance": -wallet_duplicates["Amount"].to_numpy(),
                "Reconciliation_Status": STATUS_DUPLICATE,
                "Duplicate_Source": "wallet",
                "Other_Source": "settlement",
            }
        )
        for column in settlement.columns:
            if column.startswith("Ads_"):
                settlement_records[column] = settlement_duplicates[column].to_numpy()
        for column in wallet.columns:
            if column.startswith("Wallet_"):
                wallet_records[column] = wallet_duplicates[column].to_numpy()
        return pd.concat([settlement_records, wallet_records], ignore_index=True)

    def _format_records(self, joined: pd.DataFrame) -> pd.DataFrame:
        """Convert outer-joined source rows into the reporting schema and assign status."""
        left = joined["_merge"].ne("right_only")
        right = joined["_merge"].ne("left_only")
        settlement_amount = joined["Amount_Settlement"].where(left)
        wallet_amount = joined["Amount_Wallet"].where(right)
        campaign_settlement = joined["Campaign_ID_Settlement"].where(left)
        campaign_wallet = joined["Campaign_ID_Wallet"].where(right)
        date_settlement = joined["Date_Settlement"].where(left)
        date_wallet = joined["Date_Wallet"].where(right)
        both = joined["_merge"].eq("both")
        amount_diff = (settlement_amount - wallet_amount).abs()
        campaign_diff = campaign_settlement.isna().ne(campaign_wallet.isna()) | (
            campaign_settlement.astype("string")
            .ne(campaign_wallet.astype("string"))
            .fillna(False)
        )
        date_diff = (
            pd.to_datetime(date_settlement)
            .ne(pd.to_datetime(date_wallet))
            .fillna(False)
        )

        statuses = np.select(
            [
                joined["_merge"].eq("left_only").to_numpy(dtype=bool),
                joined["_merge"].eq("right_only").to_numpy(dtype=bool),
                (both & campaign_diff).to_numpy(dtype=bool),
                (both & amount_diff.gt(self.amount_tolerance)).to_numpy(dtype=bool),
                (both & date_diff).to_numpy(dtype=bool),
            ],
            [
                STATUS_TRANSACTION_MISSING,
                STATUS_SETTLEMENT_MISSING,
                STATUS_CAMPAIGN_MISMATCH,
                STATUS_AMOUNT_MISMATCH,
                STATUS_DATE_MISMATCH,
            ],
            default=STATUS_MATCH,
        )
        records = pd.DataFrame(
            {
                "Date": date_settlement.combine_first(date_wallet),
                "Campaign_ID": campaign_settlement.combine_first(campaign_wallet),
                "Settlement_Campaign_ID": campaign_settlement,
                "Wallet_Campaign_ID": campaign_wallet,
                "Transaction_ID": joined["Transaction_ID_Settlement"].combine_first(
                    joined["Transaction_ID_Wallet"]
                ),
                "Customer_ID": joined["Customer_ID_Settlement"].combine_first(
                    joined["Customer_ID_Wallet"]
                ),
                "Settlement_Date": date_settlement,
                "Wallet_Date": date_wallet,
                "Settlement_Amount": settlement_amount,
                "Wallet_Amount": wallet_amount,
                "Settlement_Status": joined.get("Settlement_Status"),
                "Amount_Variance": settlement_amount.fillna(0) - wallet_amount.fillna(0),
                "Reconciliation_Status": statuses,
            }
        )
        for column in joined.columns:
            if column.startswith(("Ads_", "Wallet_")):
                records[column] = joined[column]
        return records

    @staticmethod
    def _add_campaign_metadata(
        records: pd.DataFrame, mapping: pd.DataFrame | None
    ) -> pd.DataFrame:
        """Attach optional campaign name and business unit without duplicating transactions."""
        if mapping is None or mapping.empty:
            records["Campaign_Name"] = pd.NA
            records["Business_Unit"] = pd.NA
            return records
        required = {"Campaign_ID", "Campaign_Name", "Business_Unit"}
        missing = required.difference(mapping.columns)
        if missing:
            raise ValueError(
                f"Campaign mapping is missing required columns: {', '.join(sorted(missing))}."
            )
        if mapping["Campaign_ID"].isna().any() or mapping["Campaign_ID"].duplicated().any():
            raise ValueError("Campaign mapping Campaign_ID values must be present and unique.")

        metadata = mapping.set_index("Campaign_ID")
        records["Campaign_Name"] = records["Campaign_ID"].map(metadata["Campaign_Name"])
        records["Business_Unit"] = records["Campaign_ID"].map(metadata["Business_Unit"])
        return records

    @staticmethod
    def _output_columns(records: pd.DataFrame) -> list[str]:
        """Return the preferred stable output column order."""
        preferred = [
            "Date", "Campaign_ID", "Campaign_Name", "Business_Unit", "Transaction_ID",
            "Settlement_Campaign_ID", "Wallet_Campaign_ID", "Customer_ID",
            "Settlement_Date", "Wallet_Date", "Settlement_Amount", "Wallet_Amount",
            "Amount_Variance", "Settlement_Status",
            "Reconciliation_Status", "Duplicate_Source", "Other_Source",
        ]
        preferred_columns = [column for column in preferred if column in records.columns]
        extra_columns = [
            column for column in records.columns if column not in preferred_columns
        ]
        return [*preferred_columns, *extra_columns]

    @staticmethod
    def _build_summary(records: pd.DataFrame) -> pd.DataFrame:
        """Build status-level counts and settlement/wallet exposure."""
        summary = (
            records.groupby("Reconciliation_Status", dropna=False)
            .agg(
                Records=("Transaction_ID", "size"),
                Settlement_Amount=("Settlement_Amount", "sum"),
                Wallet_Amount=("Wallet_Amount", "sum"),
            )
            .reset_index()
        )
        summary["Variance_Amount"] = summary["Settlement_Amount"] - summary["Wallet_Amount"]
        return summary

    @staticmethod
    def _build_metrics(
        records: pd.DataFrame,
        settlement: pd.DataFrame,
        wallet: pd.DataFrame,
        started: float,
    ) -> dict[str, Any]:
        """Calculate run-level counts and monetary totals."""
        matched = records["Reconciliation_Status"].eq(STATUS_MATCH)
        matched_count = int(matched.sum())
        record_count = len(records)
        paired = records["Settlement_Amount"].notna() & records["Wallet_Amount"].notna()
        total_settlement = float(settlement["Amount"].sum())
        total_wallet = float(wallet["Amount"].sum())
        paired_settlement = records.loc[paired, "Settlement_Amount"]
        paired_wallet = records.loc[paired, "Wallet_Amount"]
        paired_variances = paired_settlement - paired_wallet
        paired_variance_amount = float(paired_variances.sum())
        paired_absolute_variance_amount = float(paired_variances.abs().sum())
        paired_denominator = max(
            abs(float(paired_settlement.sum())),
            abs(float(paired_wallet.sum())),
        )
        paired_variance_percentage = (
            paired_absolute_variance_amount / paired_denominator * 100.0
            if paired_denominator
            else 0.0
        )
        matched_amount = float(records.loc[matched, "Settlement_Amount"].sum())
        match_percent = (matched_count / record_count * 100.0) if record_count else 0.0
        variance_amount = total_settlement - total_wallet
        variance_denominator = max(abs(total_settlement), abs(total_wallet))
        variance_percent = (
            abs(abs(total_settlement) - abs(total_wallet))
            / variance_denominator
            * 100.0
            if variance_denominator
            else 0.0
        )
        duplicate_count = int(records["Reconciliation_Status"].eq(STATUS_DUPLICATE).sum())
        quality_score = (
            max(0.0, (1 - duplicate_count / record_count) * 100.0)
            if record_count
            else 100.0
        )
        unmatched_amount = (
            records.loc[~matched, "Settlement_Amount"].abs().sum(skipna=True)
            + records.loc[~matched, "Wallet_Amount"].abs().sum(skipna=True)
        )
        return {
            "total_records": int(record_count),
            "settlement_records": int(len(settlement)),
            "wallet_records": int(len(wallet)),
            "matched_records": matched_count,
            "unmatched_records": int(record_count - matched_count),
            "duplicate_records": duplicate_count,
            "total_settlement_amount": total_settlement,
            "total_wallet_amount": total_wallet,
            "matched_amount": matched_amount,
            "unmatched_amount": float(unmatched_amount),
            "variance_amount": variance_amount,
            "variance_percentage": variance_percent,
            "paired_records": int(paired.sum()),
            "paired_variance_amount": paired_variance_amount,
            "paired_absolute_variance_amount": paired_absolute_variance_amount,
            "paired_variance_percentage": paired_variance_percentage,
            "transaction_quality_score": quality_score,
            "match_percentage": match_percent,
            "processing_seconds": round(perf_counter() - started, 4),
        }
