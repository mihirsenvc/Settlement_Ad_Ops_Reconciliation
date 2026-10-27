"""Plotly dashboard visualizations and KPI cards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from modules.reconciler import ReconciliationResult
from utils.constants import STATUS_MATCH


@dataclass(slots=True)
class DashboardData:
    """Compact, reusable chart aggregates computed once per reconciliation run."""

    status_counts: pd.DataFrame
    campaign_variance: pd.DataFrame
    campaign_summary: pd.DataFrame
    daily_counts: pd.DataFrame
    daily_amounts: pd.DataFrame


def build_campaign_summary(records: pd.DataFrame) -> pd.DataFrame:
    """Build a campaign-level control summary with counts and financial exposure."""
    campaign_ids = records["Campaign_ID"].fillna("Unassigned")
    campaign_ids.name = "Campaign_ID"
    aggregations: dict[str, tuple[str, str]] = {
        "Total_Records": ("Transaction_ID", "size")
    }
    for output_column, source_column in (
        ("Campaign_Name", "Campaign_Name"),
        ("Business_Unit", "Business_Unit"),
    ):
        if source_column in records:
            aggregations[output_column] = (source_column, "first")
    for output_column, source_column in (
        ("Settlement_Amount", "Settlement_Amount"),
        ("Wallet_Amount", "Wallet_Amount"),
        ("Variance_Amount", "Amount_Variance"),
    ):
        if source_column in records:
            aggregations[output_column] = (source_column, "sum")
        else:
            continue
    summary = records.groupby(campaign_ids, dropna=False, sort=False).agg(**aggregations)
    for column in ("Campaign_Name", "Business_Unit"):
        if column not in summary:
            summary[column] = pd.NA
    for column in ("Settlement_Amount", "Wallet_Amount", "Variance_Amount"):
        if column not in summary:
            summary[column] = 0.0
        summary[column] = summary[column].fillna(0)
    if "Reconciliation_Status" in records:
        status_counts = (
            records.groupby(
                [campaign_ids, records["Reconciliation_Status"]],
                dropna=False,
                sort=False,
            )
            .size()
            .unstack(fill_value=0)
        )
        summary = summary.join(status_counts, how="left")
    summary = summary.reset_index()
    summary["Match_Percentage"] = (
        summary.get(STATUS_MATCH, pd.Series(0, index=summary.index))
        .div(summary["Total_Records"].where(summary["Total_Records"].ne(0), 1))
        .mul(100)
    )
    return summary.sort_values(
        "Variance_Amount", key=lambda values: values.abs(), ascending=False
    ).reset_index(drop=True)


def calculate_ads_campaign_summary(
    records: pd.DataFrame,
    campaign_ids: str | tuple[str, ...],
    date_range: tuple[date, date],
) -> tuple[dict[str, float | int], pd.DataFrame]:
    """Calculate exact transaction lookups for selected campaigns and report dates."""
    date_column = (
        "Ads_Payment Date" if "Ads_Payment Date" in records.columns else "Date"
    )
    required_columns = {
        date_column,
        "Campaign_ID",
        "Transaction_ID",
        "Settlement_Amount",
        "Wallet_Amount",
        "Amount_Variance",
        "Reconciliation_Status",
    }
    missing = required_columns.difference(records.columns)
    if missing:
        raise ValueError(
            "Campaign-period summary is missing required result columns: "
            f"{', '.join(sorted(missing))}."
        )

    payment_dates = pd.to_datetime(records[date_column], errors="coerce").dt.normalize()
    start_date, end_date = map(pd.Timestamp, date_range)
    selected_campaigns = (
        (campaign_ids,) if isinstance(campaign_ids, str) else campaign_ids
    )
    normalized_campaigns = {campaign.upper() for campaign in selected_campaigns}
    selected_campaign_ids = records["Campaign_ID"].astype("string").str.upper()
    candidate_mask = (
        selected_campaign_ids.isin(normalized_campaigns)
        & payment_dates.between(start_date, end_date)
        & records["Settlement_Amount"].notna()
    )
    selected = records.loc[candidate_mask].copy()
    settlement_present = selected["Settlement_Amount"].notna()
    wallet_transaction_ids = (
        selected["Wallet_Transaction Id"]
        if "Wallet_Transaction Id" in selected
        else selected["Transaction_ID"]
    )
    wallet_campaign = (
        selected["Wallet_Campaign_ID"]
        if "Wallet_Campaign_ID" in selected
        else selected["Campaign_ID"]
    )
    ads_transaction_ids = (
        selected["Transaction_ID"].astype("string").fillna("").to_numpy()
    )
    wallet_transaction_id_values = (
        wallet_transaction_ids.astype("string").fillna("").to_numpy()
    )
    if len(ads_transaction_ids) != len(wallet_transaction_id_values):
        raise ValueError("Ads and wallet transaction ID columns have different lengths.")
    exact_id_match = pd.Series(
        ads_transaction_ids == wallet_transaction_id_values,
        index=selected.index,
    )
    # VLOOKUP(..., 0) succeeds only when the full transaction ID exists in the wallet.
    lookup_succeeded = (
        settlement_present
        & selected["Wallet_Amount"].notna()
        & selected["Transaction_ID"].notna()
        & wallet_transaction_ids.notna()
        & exact_id_match
    )
    successful = selected.loc[lookup_succeeded].copy()
    successful_wallet_campaign = wallet_campaign.loc[lookup_succeeded]
    successful["Wallet_Campaign_ID"] = successful_wallet_campaign.to_numpy()
    successful["Lookup_Result"] = "SUCCESS"
    successful["Amount_Variance"] = (
        successful["Settlement_Amount"] - successful["Wallet_Amount"]
    )
    paired = successful["Settlement_Amount"].notna() & successful["Wallet_Amount"].notna()
    matched = selected["Reconciliation_Status"].eq(STATUS_MATCH)
    settlement_total = float(successful["Settlement_Amount"].sum())
    wallet_total = float(successful["Wallet_Amount"].sum())
    paired_variance = successful["Amount_Variance"]
    paired_count = int(paired.sum())
    denominator = max(
        abs(float(successful.loc[paired, "Settlement_Amount"].sum())),
        abs(float(successful.loc[paired, "Wallet_Amount"].sum())),
    )
    summary: dict[str, float | int] = {
        "successful_lookup_rows": paired_count,
        "matched_rows": int(
            successful["Reconciliation_Status"].eq(STATUS_MATCH).sum()
        ),
        "ads_settlement_total": settlement_total,
        "linked_wallet_total": wallet_total,
        "campaign_net_variance": settlement_total - wallet_total,
        "paired_net_variance": float(paired_variance.sum()),
        "paired_absolute_variance": float(paired_variance.abs().sum()),
        "paired_variance_percentage": (
            float(paired_variance.abs().sum()) / denominator * 100
            if denominator
            else 0.0
        ),
    }
    detail_columns = [
        column
        for column in (
            "Date",
            "Campaign_ID",
            "Wallet_Campaign_ID",
            "Lookup_Result",
            "Transaction_ID",
            "Wallet_Transaction Id",
            "Wallet_campaign_id",
            "Settlement_Amount",
            "Wallet_Amount",
            "Amount_Variance",
            "Reconciliation_Status",
        )
        if column in successful.columns
    ]
    details = successful.loc[:, detail_columns].reset_index(drop=True)
    return summary, details


def filter_transactions(
    records: pd.DataFrame,
    statuses: list[str],
    campaign_text: str,
    search_text: str,
    date_range: tuple[date, date] | None,
) -> pd.DataFrame:
    """Apply transaction-table filters without mutating the reconciliation result."""
    if not statuses:
        return records.iloc[0:0]
    positions = np.flatnonzero(records["Reconciliation_Status"].isin(statuses).to_numpy())

    if campaign_text.strip():
        campaign_matches = records["Campaign_ID"].iloc[positions].astype("string").str.contains(
            campaign_text.strip(), case=False, regex=False, na=False
        )
        positions = positions[campaign_matches.to_numpy(dtype=bool, na_value=False)]
    if date_range is not None and "Date" in records.columns:
        start, end = map(pd.Timestamp, date_range)
        transaction_dates = pd.to_datetime(
            records["Date"].iloc[positions], errors="coerce"
        )
        date_matches = transaction_dates.between(start, end)
        positions = positions[date_matches.to_numpy(dtype=bool, na_value=False)]
    if search_text.strip():
        query = search_text.strip().casefold()
        transaction_match = (
            records["Transaction_ID"].iloc[positions]
            .astype("string")
            .str.casefold()
            .str.contains(
                query, regex=False, na=False
            )
        )
        customer_match = (
            records["Customer_ID"].iloc[positions]
            .astype("string")
            .str.casefold()
            .str.contains(
                query, regex=False, na=False
            )
        )
        search_matches = transaction_match | customer_match
        positions = positions[search_matches.to_numpy(dtype=bool, na_value=False)]
    return records.iloc[positions]


def prepare_dashboard_data(records: pd.DataFrame) -> DashboardData:
    """Aggregate transaction-level results before rendering or rerunning the UI."""
    status_counts = (
        records["Reconciliation_Status"]
        .eq(STATUS_MATCH)
        .map({True: "Matched", False: "Exception"})
        .value_counts()
        .rename_axis("Record_Type")
        .reset_index(name="Records")
    )
    campaign_summary = build_campaign_summary(records)
    campaign_variance = campaign_summary[["Campaign_ID", "Campaign_Name", "Variance_Amount"]]
    campaign_variance = campaign_variance.rename(
        columns={"Variance_Amount": "Amount_Variance"}
    )
    campaign_variance["Campaign_Label"] = campaign_variance["Campaign_Name"].fillna(
        campaign_variance["Campaign_ID"]
    )
    campaign_variance["_absolute_variance"] = campaign_variance["Amount_Variance"].abs()
    campaign_variance = (
        campaign_variance.nlargest(12, "_absolute_variance")
        .drop(columns="_absolute_variance")
        .reset_index(drop=True)
    )
    days = pd.to_datetime(records["Date"], errors="coerce").dt.date
    record_types = records["Reconciliation_Status"].eq(STATUS_MATCH).map(
        {True: "Matched", False: "Exception"}
    )
    daily_counts = pd.DataFrame(
        {
            "Records": record_types.groupby([days, record_types], dropna=False).size()
        }
    ).reset_index()
    daily_counts.columns = ["Day", "Record_Type", "Records"]
    daily_amount_source = records.reindex(
        columns=["Settlement_Amount", "Wallet_Amount"], fill_value=0
    )
    daily_amounts = (
        daily_amount_source.groupby(days.rename("Day"), dropna=False, sort=False)
        .sum(min_count=1)
        .reset_index()
    )
    return DashboardData(
        status_counts=status_counts,
        campaign_variance=campaign_variance,
        campaign_summary=campaign_summary,
        daily_counts=daily_counts,
        daily_amounts=daily_amounts,
    )


def render_dashboard(
    result: ReconciliationResult,
    dashboard_data: DashboardData,
) -> None:
    """Render metrics and compact pre-aggregated charts."""
    metrics = result.metrics
    first_row = st.columns(3)
    first_row[0].metric("Total records", f"{metrics['total_records']:,}")
    first_row[1].metric("Matched records", f"{metrics['matched_records']:,}")
    first_row[2].metric("Match rate", f"{metrics['match_percentage']:.1f}%")
    second_row = st.columns(4)
    second_row[0].metric("Unmatched records", f"{metrics['unmatched_records']:,}")
    second_row[1].metric("Duplicate records", f"{metrics['duplicate_records']:,}")
    second_row[2].metric(
        "Paired transaction variance",
        f"{metrics.get('paired_absolute_variance_amount', 0.0):,.2f}",
        help="Sum of absolute settlement-versus-wallet differences for transaction IDs "
        "present in both reports.",
    )
    second_row[3].metric(
        "Paired variance rate",
        f"{metrics.get('paired_variance_percentage', 0.0):.2f}%",
        help="Paired transaction variance divided by the larger paired source total.",
    )
    source_gap = st.columns(2)
    source_gap[0].metric(
        "Net source-total gap",
        f"{metrics['variance_amount']:,.2f}",
        help="Ads settlement total less wallet total; includes transactions missing "
        "from either source.",
    )
    source_gap[1].metric(
        "Source-total gap rate",
        f"{metrics['variance_percentage']:.2f}%",
        help="Difference between absolute source totals divided by the larger source total. "
        "This includes source coverage gaps, not just matched transaction differences.",
    )
    st.caption(
        f"Transaction quality score: {metrics['transaction_quality_score']:.1f}% "
        "of output rows are not classified as duplicates."
    )
    st.caption(
        f"Source coverage: {metrics['settlement_records']:,} Ads settlement row(s) vs "
        f"{metrics['wallet_records']:,} wallet row(s). Net variance includes transactions "
        "missing from either source; check the same-period wallet export before treating it "
        "as a variance on matched transactions."
    )

    left, right = st.columns([1, 1.4])
    with left:
        with st.container(border=True):
            st.markdown("#### Reconciliation outcome")
            if dashboard_data.status_counts.empty:
                st.info("No records are available for the selected date range.")
            else:
                pie = px.pie(
                    dashboard_data.status_counts,
                    values="Records",
                    names="Record_Type",
                    hole=0.68,
                    color="Record_Type",
                    color_discrete_map={"Matched": "#00875a", "Exception": "#e5a000"},
                )
                pie.update_layout(
                    template="plotly_white",
                    margin=dict(l=8, r=8, t=8, b=8),
                    legend_title_text="",
                    font=dict(family="Segoe UI, Arial, sans-serif", color="#344054"),
                    legend=dict(orientation="h", y=-0.08),
                )
                st.plotly_chart(pie, width="stretch")
    with right:
        with st.container(border=True):
            st.markdown("#### Campaign variance")
            st.caption(
                "Campaign amount variance includes full values for transactions missing "
                "from either source; use Paired transaction variance for matched-ID "
                "amount differences."
            )
            if dashboard_data.campaign_variance.empty:
                st.info("Campaign variance will appear when transaction data is available.")
            else:
                chart_data = dashboard_data.campaign_variance.head(12).sort_values(
                    "Amount_Variance"
                )
                chart = px.bar(
                    chart_data,
                    x="Amount_Variance",
                    y="Campaign_Label",
                    orientation="h",
                    color="Amount_Variance",
                    color_continuous_scale=["#d64545", "#e9edf2", "#00875a"],
                    hover_data={"Amount_Variance": ":,.2f"},
                )
                chart.update_layout(
                    template="plotly_white",
                    margin=dict(l=8, r=8, t=8, b=8),
                    xaxis_title="Variance amount",
                    yaxis_title="",
                    coloraxis_showscale=False,
                    font=dict(family="Segoe UI, Arial, sans-serif", color="#344054"),
                )
                chart.update_xaxes(showgrid=True, gridcolor="#eef1f5", zeroline=True)
                st.plotly_chart(chart, width="stretch")

    lower_left, lower_right = st.columns([1.4, 1])
    with lower_left:
        with st.container(border=True):
            st.markdown("#### Daily reconciliation trend")
            if dashboard_data.daily_counts.empty:
                st.info("Daily trend will appear when transaction data is available.")
            else:
                line = px.line(
                    dashboard_data.daily_counts,
                    x="Day",
                    y="Records",
                    color="Record_Type",
                    markers=True,
                    color_discrete_map={"Matched": "#00875a", "Exception": "#e5a000"},
                )
                line.update_layout(
                    template="plotly_white",
                    margin=dict(l=8, r=8, t=8, b=8),
                    legend_title_text="",
                    font=dict(family="Segoe UI, Arial, sans-serif", color="#344054"),
                )
                line.update_xaxes(showgrid=False)
                line.update_yaxes(showgrid=True, gridcolor="#eef1f5")
                st.plotly_chart(line, width="stretch")
    with lower_right:
        with st.container(border=True):
            st.markdown("#### Highest variance campaigns")
            st.dataframe(
                dashboard_data.campaign_variance.head(5).rename(
                    columns={"Campaign_Label": "Campaign", "Amount_Variance": "Variance"}
                ),
                width="stretch",
                hide_index=True,
            )
    with st.container(border=True):
        st.markdown("#### Settlement and wallet amount trends")
        if dashboard_data.daily_amounts.empty:
            st.info("Amount trends will appear when transaction data is available.")
        else:
            amount_trend = dashboard_data.daily_amounts.melt(
                id_vars="Day",
                value_vars=["Settlement_Amount", "Wallet_Amount"],
                var_name="Source",
                value_name="Amount",
            )
            amount_trend["Source"] = amount_trend["Source"].map(
                {
                    "Settlement_Amount": "Settlement",
                    "Wallet_Amount": "Wallet",
                }
            )
            amount_chart = px.line(
                amount_trend,
                x="Day",
                y="Amount",
                color="Source",
                markers=True,
                color_discrete_map={"Settlement": "#087f8c", "Wallet": "#345995"},
            )
            amount_chart.update_layout(
                template="plotly_white",
                margin=dict(l=8, r=8, t=8, b=8),
                yaxis_title="Amount",
                legend_title_text="",
                font=dict(family="Segoe UI, Arial, sans-serif", color="#344054"),
            )
            amount_chart.update_yaxes(showgrid=True, gridcolor="#eef1f5")
            st.plotly_chart(amount_chart, width="stretch")
