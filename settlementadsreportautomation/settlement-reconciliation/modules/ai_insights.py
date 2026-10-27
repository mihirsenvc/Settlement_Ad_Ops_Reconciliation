"""Deterministic, explainable insights for finance operations reporting."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from modules.dashboard import build_campaign_summary
from modules.reconciler import ReconciliationResult
from utils.constants import (
    STATUS_AMOUNT_MISMATCH,
    STATUS_CAMPAIGN_MISMATCH,
    STATUS_DATE_MISMATCH,
    STATUS_DUPLICATE,
    STATUS_SETTLEMENT_MISSING,
    STATUS_TRANSACTION_MISSING,
)


@dataclass(slots=True)
class ExecutiveSummary:
    """Business-readable summary text and prioritized observations."""

    headline: str
    narrative: str
    insights: pd.DataFrame
    campaign_summary: pd.DataFrame


def build_executive_summary(
    result: ReconciliationResult,
    campaign_summary: pd.DataFrame | None = None,
) -> ExecutiveSummary:
    """Generate actionable rule-based observations without external AI services."""
    records = result.records
    metrics = result.metrics
    campaigns = (
        campaign_summary
        if campaign_summary is not None
        else build_campaign_summary(records)
    )
    match_percent = metrics["match_percentage"]
    unmatched_count = metrics["unmatched_records"]
    variance = metrics["variance_amount"]
    quality_score = metrics["transaction_quality_score"]
    headline = (
        f"{match_percent:.1f}% of transactions matched successfully; "
        f"{unmatched_count:,} records need review."
    )
    campaign_name = "No campaign variance"
    campaign_share = 0.0
    if not campaigns.empty:
        top_campaign = campaigns.iloc[0]
        mapped_name = top_campaign["Campaign_Name"]
        campaign_name = (
            str(mapped_name)
            if pd.notna(mapped_name) and str(mapped_name).strip()
            else str(top_campaign["Campaign_ID"])
        )
        absolute_campaign_variances = campaigns["Variance_Amount"].abs()
        total_absolute_variance = float(absolute_campaign_variances.sum())
        if total_absolute_variance:
            campaign_share = (
                float(abs(top_campaign["Variance_Amount"])) / total_absolute_variance * 100
            )

    narrative = (
        f"{headline} Net source-total gap is ₹{variance:,.2f} "
        f"({metrics['variance_percentage']:.2f}% of the larger source total); this includes "
        f"transactions missing from one source. Paired transaction amount differences total "
        f"₹{metrics.get('paired_absolute_variance_amount', 0.0):,.2f}. "
        f"{campaign_name} has the highest campaign variance exposure"
        f"{f' and contributes {campaign_share:.1f}% of net campaign variance exposure' if campaign_share else ''}. "
        f"Transaction quality score: {quality_score:.1f}% "
        "(share of output rows not classified as duplicate)."
    )

    status_counts = records["Reconciliation_Status"].value_counts()
    insights: list[dict[str, str]] = []

    def add_insight(
        title: str,
        observation: str,
        recommendation: str,
        priority: str,
    ) -> None:
        insights.append(
            {
                "Priority": priority,
                "Observation": title,
                "Details": observation,
                "Recommended Action": recommendation,
            }
        )

    amount_mismatches = int(status_counts.get(STATUS_AMOUNT_MISMATCH, 0))
    if amount_mismatches:
        add_insight(
            "Amount inconsistencies",
            f"{amount_mismatches:,} matched transaction IDs have an amount difference above tolerance.",
            "Compare partner settlement values with wallet ledger entries and verify fee or adjustment treatment.",
            "High",
        )

    campaign_mismatches = int(status_counts.get(STATUS_CAMPAIGN_MISMATCH, 0))
    if campaign_mismatches:
        add_insight(
            "Campaign assignment inconsistencies",
            f"{campaign_mismatches:,} transaction IDs have different campaign IDs across sources.",
            "Verify campaign attribution in both source systems before allocating settlement amounts.",
            "Medium",
        )

    date_mismatches = int(status_counts.get(STATUS_DATE_MISMATCH, 0))
    if date_mismatches:
        add_insight(
            "Settlement date differences",
            f"{date_mismatches:,} transaction IDs have different dates across sources.",
            "Review settlement cut-off times and timezone or posting-date conventions.",
            "Medium",
        )

    duplicates = int(status_counts.get(STATUS_DUPLICATE, 0))
    if duplicates:
        add_insight(
            "Duplicate transaction activity",
            f"{duplicates:,} source rows belong to duplicated transaction IDs.",
            "Confirm source extract uniqueness and suppress repeated rows before settlement posting.",
            "High",
        )

    missing_wallet = int(status_counts.get(STATUS_TRANSACTION_MISSING, 0))
    if missing_wallet:
        add_insight(
            "Wallet transactions missing",
            f"{missing_wallet:,} settlement rows have no corresponding wallet transaction.",
            "Check wallet ingestion timing and partner settlement references for these transactions.",
            "High",
        )

    missing_settlement = int(status_counts.get(STATUS_SETTLEMENT_MISSING, 0))
    if missing_settlement:
        add_insight(
            "Settlement entries missing",
            f"{missing_settlement:,} wallet transactions have no corresponding settlement entry.",
            "Review settlement cut-off windows and raise unresolved items with the settlement partner.",
            "Medium",
        )

    if campaign_share >= 20 and campaign_name != "No campaign variance":
        add_insight(
            "Concentrated campaign variance",
            f"{campaign_name} contributes {campaign_share:.1f}% of absolute campaign variance.",
            "Prioritize a campaign-level reconciliation and review its settlement configuration.",
            "High",
        )

    if not insights:
        add_insight(
            "No material exceptions detected",
            "No duplicate, missing, or amount-mismatch classifications were identified.",
            "Retain the reconciliation output with the period-close audit evidence.",
            "Low",
        )

    return ExecutiveSummary(
        headline=headline,
        narrative=narrative,
        insights=pd.DataFrame(insights),
        campaign_summary=campaigns,
    )
