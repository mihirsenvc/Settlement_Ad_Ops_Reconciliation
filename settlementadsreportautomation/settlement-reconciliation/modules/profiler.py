"""Data quality profiling utilities for tabular input reports."""

from __future__ import annotations

from typing import Any

import pandas as pd


def profile_data(frame: pd.DataFrame) -> dict[str, Any]:
    """Return compact quality indicators, null counts, and duplicate statistics."""
    null_counts = frame.isna().sum()
    duplicate_transaction_ids = 0
    if "Transaction_ID" in frame.columns:
        transaction_ids = frame["Transaction_ID"].dropna()
        duplicate_transaction_ids = int(
            transaction_ids.duplicated(keep=False).sum()
        )
    return {
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "memory_usage_bytes": int(frame.memory_usage(index=True, deep=True).sum()),
        "null_analysis": {
            str(column): {
                "count": int(count),
                "percentage": round(float(count / len(frame) * 100), 2) if len(frame) else 0.0,
            }
            for column, count in null_counts.items()
        },
        "duplicate_rows": int(frame.duplicated().sum()),
        "duplicate_transaction_ids": duplicate_transaction_ids,
        "columns": [str(column) for column in frame.columns],
    }
