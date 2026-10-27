"""Shared schemas and reconciliation status labels."""

from __future__ import annotations

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "settlement": [
        "Date",
        "Campaign_ID",
        "Transaction_ID",
        "Customer_ID",
        "Amount",
        "Settlement_Status",
    ],
    "wallet": ["Date", "Campaign_ID", "Transaction_ID", "Customer_ID", "Amount"],
    "campaign": ["Campaign_ID", "Campaign_Name", "Business_Unit"],
}

STATUS_MATCH = "STATUS_MATCH"
STATUS_AMOUNT_MISMATCH = "STATUS_AMOUNT_MISMATCH"
STATUS_TRANSACTION_MISSING = "STATUS_TRANSACTION_MISSING"
STATUS_SETTLEMENT_MISSING = "STATUS_SETTLEMENT_MISSING"
STATUS_DUPLICATE = "STATUS_DUPLICATE"
STATUS_CAMPAIGN_MISMATCH = "STATUS_CAMPAIGN_MISMATCH"
STATUS_DATE_MISMATCH = "STATUS_DATE_MISMATCH"
