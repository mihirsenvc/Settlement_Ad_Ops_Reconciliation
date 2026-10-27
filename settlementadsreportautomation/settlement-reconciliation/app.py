"""Streamlit entry point for the settlement reconciliation platform."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal, Protocol
from uuid import uuid4

import pandas as pd
import streamlit as st

from config import Settings, get_settings
from modules.ads_settlement import (
    load_ads_settlement,
    transaction_date_from_transaction_id,
)
from modules.dashboard import calculate_ads_campaign_summary
from modules.database_manager import DatabaseManager
from modules.reconciler import ReconciliationEngine
from modules.wallet_export import load_wallet_export
from modules.validator import (
    FileValidationError,
    load_report,
    validate_report,
)
from utils.logger import configure_logging

LOGGER = logging.getLogger(__name__)
UploadRole = Literal["settlement", "wallet"]


class UploadedReport(Protocol):
    """Minimal interface consumed from Streamlit's upload control."""

    name: str
    size: int

    def seek(self, pos: int, whence: int = 0, /) -> int:
        """Rewind the uploaded file before parsing."""

    def read(self, size: int = -1, /) -> bytes:
        """Read uploaded bytes for XLSX archive inspection."""

    def readline(self, size: int = -1, /) -> bytes:
        """Read one CSV line while locating a metadata-prefixed header."""

    def tell(self) -> int:
        """Return the upload stream position."""

    def getbuffer(self) -> memoryview[int]:
        """Expose a read-only view for hashing without copying the upload."""


def _read_upload(
    upload: UploadedReport, role: UploadRole, settings: Settings
) -> pd.DataFrame:
    """Read and validate an uploaded report without relying on its filename."""
    content = upload.getbuffer()
    validate_report(
        upload.name,
        content,
        role,
        max_xlsx_expanded_size_bytes=(
            settings.max_xlsx_expanded_size_mb * 1024 * 1024
        ),
        file_stream=upload,
    )
    upload.seek(0)
    if role == "settlement" and upload.name.lower().endswith(".xlsx"):
        frame = load_ads_settlement(upload, upload.name)
    else:
        try:
            frame = load_report(upload, upload.name, role)
        except FileValidationError as exc:
            missing_required_columns = "missing required column" in str(exc).lower()
            unable_to_parse = "unable to parse report" in str(exc).lower()
            if (
                role == "wallet"
                and upload.name.lower().endswith(".csv")
                and (missing_required_columns or unable_to_parse)
            ):
                frame = load_wallet_export(upload, upload.name)
            else:
                raise
    return frame


def _execute_reconciliation(
    settlement_file: UploadedReport,
    wallet_file: UploadedReport,
) -> None:
    st.session_state.pop("reconciliation", None)
    started_at = datetime.now(timezone.utc)
    uploaded_at = started_at.isoformat()
    user_session = st.session_state.setdefault("user_session_id", str(uuid4()))
    settings: Settings | None = None
    try:
        settings = get_settings()
        uploads = [settlement_file, wallet_file]
        oversized = [
            item.name
            for item in uploads
            if item.size > settings.max_upload_size_mb * 1024 * 1024
        ]
        if oversized:
            raise FileValidationError(
                f"Upload exceeds the configured {settings.max_upload_size_mb} MB limit: "
                f"{', '.join(oversized)}."
            )
        upload_buffers = [item.getbuffer() for item in uploads]
        oversized = [
            item.name
            for item, content in zip(uploads, upload_buffers, strict=True)
            if len(content) > settings.max_upload_size_mb * 1024 * 1024
        ]
        if oversized:
            raise FileValidationError(
                f"Upload exceeds the configured {settings.max_upload_size_mb} MB limit: "
                f"{', '.join(oversized)}."
            )
        fingerprints = [sha256(content).hexdigest() for content in upload_buffers]
        if len(fingerprints) != len(set(fingerprints)):
            raise FileValidationError("The same file was uploaded more than once.")

        with st.spinner("Validating and loading reports..."):
            settlement = _read_upload(settlement_file, "settlement", settings)
            wallet = _read_upload(wallet_file, "wallet", settings)
        import_warnings = list(settlement.attrs.get("import_warnings", []))
        import_warnings.extend(wallet.attrs.get("import_warnings", []))
        upload_row_counts = [len(settlement), len(wallet)]

        with st.status("Reconciling settlement and wallet activity", expanded=True) as progress:
            engine = ReconciliationEngine(amount_tolerance=settings.amount_tolerance)
            progress.write("Comparing transaction IDs, campaign IDs, amounts, and dates")
            result = engine.reconcile(settlement, wallet)
            database = DatabaseManager(settings.database_path)
            progress.write("Recording the audit event")
            database.initialize()
            warnings: list[str] = []
            if len(settlement) != len(wallet):
                warnings.append(
                    f"Source coverage differs: {len(settlement):,} Ads settlement row(s) "
                    f"and {len(wallet):,} wallet row(s). Total variance includes "
                    "transactions missing from one source; confirm the wallet export covers "
                    "the same period and transactions before interpreting it as a matched-amount difference."
                )
            warnings.extend(import_warnings)
            run_id = database.record_run(
                summary=result.summary,
                metrics=result.metrics,
                profiles={},
                source_files=[item.name for item in uploads],
                user_session=user_session,
                upload_metadata=[
                    {
                        "file_role": role,
                        "file_name": upload.name,
                        "file_size_bytes": len(content),
                        "record_count": row_count,
                        "uploaded_at": uploaded_at,
                        "content_sha256": fingerprint,
                    }
                    for role, upload, content, fingerprint, row_count in zip(
                        ("settlement", "wallet"),
                        uploads,
                        upload_buffers,
                        fingerprints,
                        upload_row_counts,
                        strict=True,
                    )
                ],
                warnings=warnings,
                started_at=started_at,
            )
            st.session_state["reconciliation"] = {
                "result": result,
                "run_id": run_id,
            }
            progress.update(
                label="Reconciliation completed",
                state="complete",
                expanded=False,
            )
        st.success(f"Reconciliation completed successfully · Run {run_id[:8]}")
    except FileValidationError as exc:
        _record_failure(
            settings,
            user_session,
            "INPUT_VALIDATION_FAILED",
            "Input validation failed.",
        )
        st.error(f"Input validation failed: {exc}")
    except Exception as exc:
        reference = uuid4().hex[:10]
        LOGGER.error(
            "Reconciliation failed (%s); reference=%s",
            type(exc).__name__,
            reference,
        )
        _record_failure(
            settings,
            user_session,
            "RECONCILIATION_FAILED",
            f"Support reference: {reference}",
        )
        st.error(f"Reconciliation failed. Share reference `{reference}` with support.")


def _record_failure(
    settings: Settings | None,
    user_session: str,
    event_type: str,
    message: str,
) -> None:
    """Persist safe failure details when database configuration is available."""
    if settings is None:
        return
    try:
        database = DatabaseManager(settings.database_path)
        database.initialize()
        database.record_audit_event(
            "ERROR",
            event_type,
            user_session,
            {"message": message},
        )
    except Exception:
        LOGGER.exception("Could not persist reconciliation failure audit")


@st.dialog("SARAL campaign not available")
def _show_saral_unavailable_dialog() -> None:
    st.write(
        "No SARAL transactions were found in the uploaded settlement report. "
        "Check that its transaction IDs use the ADS_GMV_SETTLE_<digits> format."
    )
    if st.button("Close"):
        st.rerun()


def main() -> None:
    """Render the upload workflow and reconciliation results."""
    st.set_page_config(
        page_title="Flipkart Search Ad Ops Settlement Reconciliation Software",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    try:
        settings = get_settings()
        configure_logging(
            settings.log_path,
            settings.log_level,
            settings.log_max_bytes,
            settings.log_backup_count,
        )
    except (OSError, ValueError):
        LOGGER.exception("Application startup configuration failed")
        st.error("Application configuration is invalid. Contact your administrator.")
        st.stop()
    st.markdown(
        """
        <style>
        :root {
          --ink:#172b4d; --muted:#667085; --teal:#087f8c; --green:#00875a;
          --line:#e4e9f0; --canvas:#f3f5f8; --surface:#ffffff;
        }
        html, body, [class*="css"] { font-family:'Segoe UI',Arial,sans-serif; color:var(--ink); }
        .stApp { background:var(--canvas); }
        [data-testid="stMainBlockContainer"] { max-width:1560px; padding-top:1.6rem; padding-bottom:3rem; }
        [data-testid="stSidebar"] { background:#14243a; border-right:1px solid #293950; }
        [data-testid="stSidebar"] * { color:#e9eff5; }
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color:#afbdcc; }
        .brand { font-size:21px; font-weight:750; letter-spacing:-.4px; color:white; }
        .brand-mark { color:#65d5b0; padding-right:8px; }
        .sidebar-kicker { color:#95a6b9; font-size:10px; font-weight:700; letter-spacing:1.4px; }
        .hero {
          position:relative; overflow:hidden;
          background:linear-gradient(112deg,#14243a 0%,#1d3e57 70%,#087f8c 150%);
          border:1px solid #29485d; border-radius:18px; padding:28px 32px;
          margin:2px 0 22px; color:white; box-shadow:0 10px 24px #172b4d12;
        }
        .hero-eyebrow { color:#7cddc0; font-size:11px; font-weight:750; letter-spacing:1.5px; margin-bottom:8px; }
        .hero h1 { font-size:29px; line-height:1.2; margin:0 0 7px; color:white; letter-spacing:-.6px; }
        .hero p { color:#c8d4df; margin:0; font-size:14px; }
        .hero-chip { display:inline-block; margin-top:16px; padding:5px 10px; border-radius:20px;
          background:#ffffff14; color:#d6e5ed; font-size:11px; border:1px solid #ffffff20; }
        h2, h3, h4 { color:var(--ink); letter-spacing:-.25px; }
        [data-testid="stVerticalBlockBorderWrapper"] {
          background:var(--surface); border-color:var(--line); border-radius:14px;
          box-shadow:0 2px 8px #172b4d06;
        }
        div[data-testid="stMetric"] {
          min-height:108px; background:var(--surface); border:1px solid var(--line);
          border-radius:13px; padding:17px 19px; box-shadow:0 2px 8px #172b4d07;
        }
        div[data-testid="stMetricLabel"] { color:var(--muted); font-size:12px; font-weight:650; }
        div[data-testid="stMetricValue"] { color:var(--ink); font-size:25px; font-weight:750; }
        [data-testid="stFileUploader"] section {
          background:#f8fafc; border:1px dashed #b8c4d2; border-radius:11px;
        }
        [data-testid="stFileUploader"] section:hover { border-color:var(--teal); background:#f1fbfa; }
        .stButton>button, .stDownloadButton>button {
          min-height:42px; border-radius:9px; font-weight:700;
          transition:all .15s ease; border-color:#d0d8e2;
        }
        .stButton>button[kind="primary"] {
          background:#087f8c; color:white; border-color:#087f8c;
          box-shadow:0 3px 8px #087f8c24;
        }
        .stButton>button[kind="primary"]:hover {
          background:#066d78; border-color:#066d78; color:white;
        }
        [data-testid="stAlert"] { border-radius:10px; }
        [data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:10px; }
        [data-testid="stTabs"] button { font-weight:650; }
        @media (max-width: 760px) {
          [data-testid="stMainBlockContainer"] { padding:1rem .8rem 2rem; }
          .hero { padding:22px 20px; border-radius:14px; }
          .hero h1 { font-size:23px; }
          div[data-testid="stMetric"] { min-height:92px; padding:13px; }
          div[data-testid="stMetricValue"] { font-size:20px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown(
            '<div class="brand"><span class="brand-mark">◈</span>Flipkart Search Ad Ops</div>'
            '<div class="sidebar-kicker">SETTLEMENT RECONCILIATION</div>',
            unsafe_allow_html=True,
        )
        st.divider()
        st.markdown("### Reconciliation")
        st.caption("Settlement control center")
        st.markdown("**Workspace**")
        st.caption("Compare settlement and wallet transactions, then calculate campaign totals.")
        st.markdown("---")
        st.caption("SUPPORTED FILES")
        st.caption("CSV · XLSX")
        st.caption(f"AMOUNT TOLERANCE · {settings.amount_tolerance:.2f}")
        st.markdown("---")
        st.caption(f"Environment · {settings.environment.title()}")

    st.markdown(
        '<section class="hero"><div class="hero-eyebrow">FINANCE OPERATIONS · CONTROL CENTER</div>'
        '<h1>Flipkart Search Ad Ops Settlement Reconciliation</h1>'
        '<p>Upload the settlement report and wallet download, then process using dates detected in the reports.</p>'
        '<span class="hero-chip">Secure report processing&nbsp;&nbsp;·&nbsp;&nbsp;CSV and Excel</span></section>',
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.markdown("**01 · Settlement report**")
        settlement_file = st.file_uploader(
            "Settlement report file",
            type=["csv", "xlsx"],
            key="settlement",
            help=(
                "Required: standard settlement columns, or an Excel workbook with an Ads "
                "sheet containing Payment Date, Campaign / Transaction ID, and Settlement Value."
            ),
            label_visibility="collapsed",
        )
        if settlement_file is not None:
            st.caption(f"Ready · {settlement_file.name} · {settlement_file.size / 1024:,.0f} KB")
            if settlement_file.name.lower().endswith(".xlsx"):
                st.caption(
                    "Ads-format workbooks: SPWT endings map to PC, ANPL, or SARAL; "
                    "ADS_GMV_SETTLE IDs map to SARAL."
                )
        else:
            st.caption("Required · Standard CSV/XLSX or Ads settlement workbook")

        if settlement_file is not None:
            st.markdown("**02 · Settlement date range**")
        if (
            settlement_file is not None
            and settlement_file.size > settings.max_upload_size_mb * 1024 * 1024
        ):
            st.error(
                f"Settlement report exceeds the configured "
                f"{settings.max_upload_size_mb} MB upload limit."
            )
        elif settlement_file is not None:
            try:
                settlement_preview = _read_upload(
                    settlement_file, "settlement", settings
                )
                if "Transaction_ID" not in settlement_preview:
                    st.caption("No Campaign / Transaction ID column was found.")
                else:
                    encoded_dates = (
                        settlement_preview["Transaction_ID"]
                        .map(transaction_date_from_transaction_id)
                        .dropna()
                    )
                    if encoded_dates.empty:
                        st.caption(
                            "No dates in YYMMDD format were found after SPWT "
                            "in the Campaign / Transaction ID column."
                        )
                    else:
                        encoded_dates = pd.to_datetime(encoded_dates).dt.normalize()
                        st.caption(
                            "Dates detected from Campaign / Transaction ID: "
                            f"{encoded_dates.min():%d %b %Y} to "
                            f"{encoded_dates.max():%d %b %Y}"
                        )
            except FileValidationError as exc:
                st.error(f"Settlement date range unavailable: {exc}")

        st.markdown("**03 · Wallet transactions**")
        wallet_file = st.file_uploader(
            "Wallet transaction report",
            type=["csv", "xlsx"],
            key="wallet",
            help=(
                "Required: Date, Campaign_ID, Transaction_ID, Customer_ID, Amount. "
                "Wallet exports with a metadata preamble are supported; Wallet Id Selected "
                "is used as Customer_ID."
            ),
            label_visibility="collapsed",
        )
        if wallet_file is not None:
            st.caption(f"Ready · {wallet_file.name} · {wallet_file.size / 1024:,.0f} KB")
        else:
            st.caption("Required · CSV or XLSX")
        st.caption("Reports are processed in memory. Duplicate file contents are rejected.")
        ready = settlement_file is not None and wallet_file is not None
        if ready and st.button(
            "04 · Process reports",
            type="primary",
            width="stretch",
            icon=":material/compare_arrows:",
        ):
            _execute_reconciliation(settlement_file, wallet_file)

    saved = st.session_state.get("reconciliation")
    if saved:
        result = saved["result"]
        st.markdown("")
        st.subheader("Reconciliation results")
        st.caption(
            f"Run {saved['run_id'][:8]} · Completed "
            f"{result.execution_timestamp.astimezone().strftime('%d %b %Y, %H:%M %Z')}"
        )
        with st.container(border=True):
            st.markdown("#### Settlement-date campaign calculation")
            st.caption(
                "The report date range is detected automatically. Select a campaign group "
                "to calculate totals using exact Ads-to-wallet transaction ID matching."
            )
            report_date_column = (
                "Ads_Payment Date"
                if "Ads_Payment Date" in result.records
                else "Date"
            )
            report_dates = pd.to_datetime(
                result.records[report_date_column], errors="coerce"
            ).dt.normalize()
            dated_rows = result.records.loc[
                result.records["Settlement_Amount"].notna() & report_dates.notna()
            ]
            if dated_rows.empty:
                st.info("No usable dates were found in the uploaded settlement report.")
            else:
                available_dates = report_dates.loc[dated_rows.index]
                calculation_range = (
                    available_dates.min().date(),
                    available_dates.max().date(),
                )
                st.caption(
                    f"Dates detected in report: {calculation_range[0]:%d %b %Y} "
                    f"to {calculation_range[1]:%d %b %Y}"
                )
                supported_campaigns = ("PC", "ANPL", "SARAL")
                available_campaigns = set(
                    dated_rows["Campaign_ID"]
                    .dropna()
                    .astype("string")
                    .str.upper()
                    .unique()
                ).intersection(supported_campaigns)
                campaign_groups: dict[str, tuple[str, ...]] = {}
                if available_campaigns.intersection(("PC", "ANPL")):
                    campaign_groups["PC + ANPL"] = ("PC", "ANPL")
                campaign_groups["SARAL"] = ("SARAL",)
                selected_campaign_group = st.selectbox(
                    "Campaign group",
                    options=list(campaign_groups),
                    key=f"ads_campaign_{saved['run_id']}",
                )
                if st.button(
                    "Calculate campaign summary",
                    type="primary",
                    key=f"calculate_ads_campaign_{saved['run_id']}",
                ):
                    if (
                        selected_campaign_group == "SARAL"
                        and "SARAL" not in available_campaigns
                    ):
                        _show_saral_unavailable_dialog()
                    else:
                        calculation, calculation_details = calculate_ads_campaign_summary(
                            result.records,
                            campaign_groups[selected_campaign_group],
                            calculation_range,
                        )
                        st.markdown(
                            f"**{selected_campaign_group} · "
                            f"{calculation_range[0]:%d %b %Y} to "
                            f"{calculation_range[1]:%d %b %Y}**"
                        )
                        summary_rows = [
                            {"Calculation": label, "Value": value}
                            for label, value in (
                                (
                                    "Successful exact transaction lookups",
                                    calculation["successful_lookup_rows"],
                                ),
                                ("Fully reconciled rows", calculation["matched_rows"]),
                                ("Ads settlement total", calculation["ads_settlement_total"]),
                                ("Linked wallet gross total", calculation["linked_wallet_total"]),
                                ("Campaign net variance", calculation["campaign_net_variance"]),
                                ("Paired net variance", calculation["paired_net_variance"]),
                                (
                                    "Paired absolute variance",
                                    calculation["paired_absolute_variance"],
                                ),
                                (
                                    "Paired variance rate (%)",
                                    calculation["paired_variance_percentage"],
                                ),
                            )
                        ]
                        st.dataframe(
                            pd.DataFrame(summary_rows),
                            width="stretch",
                            hide_index=True,
                        )
                        st.caption(
                            "Only successful exact transaction-ID lookups are listed below. "
                            "Missing wallet transactions are excluded. Value variation is "
                            "Ads settlement amount minus matched wallet gross amount."
                        )
                        st.markdown("#### Transaction calculation detail")
                        display_columns = calculation_details.rename(
                            columns={
                                "Transaction_ID": "Ads Transaction ID (lookup value)",
                                "Wallet_Transaction Id": "Wallet Transaction ID (lookup key)",
                                "Wallet_campaign_id": "Wallet Campaign ID (lookup result)",
                                "Wallet_Campaign_ID": "Wallet Campaign Category",
                            }
                        )
                        st.dataframe(
                            display_columns,
                            width="stretch",
                            hide_index=True,
                            height=360,
                        )
    else:
        st.info("Upload both source reports to begin. Sample CSV files are available in the `sample_data` folder.")


if __name__ == "__main__":
    main()
