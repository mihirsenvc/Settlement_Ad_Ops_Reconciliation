# Flipkart Search Ad Ops Settlement Reconciliation Software

An enterprise-oriented Streamlit application for comparing settlement reports with wallet transaction exports. Upload both reports, reconcile using the dates in the files, and calculate campaign-period totals.

## Features

- CSV and XLSX ingestion with normalized headers, required-column checks, numeric amount/date validation, and duplicate file detection. Uploads are hashed through memory views and parsed from their existing stream to avoid redundant full-file copies.
- Flipkart Ads settlement workbook ingestion always reads the `Ads` worksheet (never another worksheet) for `.xlsx` settlement uploads. `Campaign / Transaction ID` becomes `Transaction_ID`; SPWT IDs ending in `PC`, `ANPL`, or `SARAL` map to those campaign IDs, and `ADS_GMV_SETTLE_<digits>` IDs map to SARAL. When available, the transaction date is read from the `YYMMDD` portion of either ID format while the original payment date remains in the source columns. Recognized campaign settlement amounts are compared by absolute value against positive wallet gross amounts; signed source values are preserved in `Ads_Settlement_Amount_Raw` and the original Ads columns. Uncached row-level `SUM(G:K)` settlement formulas are calculated from their component cells; unsupported uncached formulas fail with an actionable validation message. Blank settlement values are treated as zero and explicitly reported; rows with both a blank payment date and settlement value are excluded with a warning.
- Wallet CSV exports with leading metadata rows are supported. The loader detects the transaction header, maps `Wallet Transaction Date` and `Transaction Id`, uses `Wallet Id Selected` as `Customer_ID`, and maps `Gross Amount (Incl. of Tax)` to the reconciliation amount. SPWT suffixes and `ADS_GMV_SETTLE_<digits>` IDs supply campaign categories used to align with Ads; all original wallet columns, including the original `campaign_id`, are retained in the master output.
- One-to-one transaction-ID reconciliation with campaign, amount, and date comparisons; duplicate IDs are isolated to avoid many-to-many joins.
- Match, amount mismatch, campaign mismatch, date mismatch, missing-wallet, missing-settlement, and duplicate classifications.
- Interactive campaign calculation combines PC and ANPL into one campaign group and provides SARAL separately. It detects the report date range automatically, performs an exact Ads-to-wallet transaction ID lookup, and displays successful lookups with their amount variance and returned wallet campaign ID. A dialog explains when SARAL is not present.
- SQLite execution history, upload metadata (including SHA-256 checksums and row counts), reconciliation results, campaign master data, audit events, and user activity events. Uploaded report contents are not persisted.
- Configurable amount tolerance, database and log paths, rotating log retention, and runtime environment.
- Staged loading feedback and run status notifications.
- The post-run interface focuses on the campaign calculator using dates detected from the uploaded reports; executive insights, transaction browsing, data-quality profiling, and report downloads are not shown.
- Reconciliation uses vectorized Pandas operations and only the required source columns. Campaign enrichment maps columns instead of merging/copying the full result frame.

## Project structure

```text
settlement-reconciliation/
├── app.py
├── config.py
├── requirements.txt
├── README.md
├── database/
│   └── schema.sql
├── .streamlit/
│   └── config.toml
├── logs/
├── outputs/
├── sample_data/
│   ├── settlement_report.csv
│   └── wallet_transactions.csv
├── modules/
│   ├── ai_insights.py
│   ├── dashboard.py
│   ├── database_manager.py
│   ├── profiler.py
│   ├── reconciler.py
│   ├── report_generator.py
│   └── validator.py
├── tests/
│   ├── test_config.py
│   ├── test_ai_insights.py
│   ├── test_database.py
│   ├── test_dashboard.py
│   ├── test_reconciler.py
│   ├── test_report_generator.py
│   └── test_validator.py
├── utils/
│   ├── constants.py
│   └── logger.py
└── uploads/  # Reserved for controlled deployments; uploads are processed in memory by default.
```

## Input schemas

Settlement CSV/XLSX requires `Date`, `Campaign_ID`, `Transaction_ID`, `Customer_ID`, `Amount`, and `Settlement_Status`. For settlement `.xlsx` uploads, the application requires an `Ads` sheet with `Payment Date`, `Campaign / Transaction ID`, and `Settlement Value (Rs.) = SUM(G:K)` and ignores all other sheets. Uncached row-level formulas of the form `=SUM(Gn:Kn)` are calculated from columns G through K; if a component itself contains an uncached formula, upload is stopped with an error rather than assuming a zero amount. Recognized SPWT transaction IDs must start with `SPWT`, contain a digit sequence, and end with `PC`, `ANPL`, or `SARAL` to be assigned to those campaign IDs. `ADS_GMV_SETTLE_<digits>` identifiers map to SARAL.
Wallet CSV/XLSX requires `Date`, `Campaign_ID`, `Transaction_ID`, `Customer_ID`, and `Amount`. For wallet exports with recognized SPWT or `ADS_GMV_SETTLE_<digits>` transaction IDs, the ID supplies `Campaign_ID` for reconciliation; the source `campaign_id` and remaining wallet columns are preserved as wallet source fields in the complete outcome.
Headers are matched without case or punctuation sensitivity (for example, `campaign id` maps to `Campaign_ID`). Dates must parse and amounts must be numeric. The calculator uses the Ads `Payment Date` when the Ads worksheet is available, otherwise it uses the settlement report `Date`; the available date span is displayed after processing and applied automatically.

## Reconciliation rules

1. Duplicate transaction IDs within either source are reported as `STATUS_DUPLICATE` before joining. One output row is emitted per duplicate source row.
2. Remaining rows are joined on `Transaction_ID`; blank IDs are kept source-specific and never matched to one another.
3. A source-only transaction is `STATUS_TRANSACTION_MISSING` when settlement exists without wallet, or `STATUS_SETTLEMENT_MISSING` when wallet exists without settlement.
4. For IDs found on both sides, campaign mismatch is classified first, followed by amount differences above `AMOUNT_TOLERANCE`, then date mismatch. Only rows equal across all criteria receive `STATUS_MATCH`.
5. Variance is `Settlement_Amount - Wallet_Amount`. Recognized SPWT and `ADS_GMV_SETTLE_<digits>` settlement values are normalized to absolute values for comparison with wallet gross amounts; their original signs remain in `Ads_Settlement_Amount_Raw`. The Ads payout date is retained, but the `YYMMDD` embedded in a dated recognized transaction ID is used as the transaction date for comparison with the wallet transaction date. The campaign calculation uses the Ads sheet's `Payment Date` when available, otherwise the settlement report `Date`, then links wallet rows to selected Ads rows by transaction ID and campaign ID. Overall source-total gap includes the full value of source-only transactions, so it measures source coverage exposure as well as transaction differences. Paired transaction variance/rate measures amount differences only for IDs present in both sources. Wallet reconciliation amount is `Gross Amount (Incl. of Tax)`.
6. Match percentage is matched output rows divided by all output rows.

## Run locally

Requires Python 3.12.

```powershell
cd settlement-reconciliation
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

Use the settlement and wallet CSV files under `sample_data/` to try the workflow. The calculator detects the available date span from the uploaded reports. The database and log folders are created automatically at startup.

## Configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | Environment label shown in the sidebar |
| `DATABASE_PATH` | `database/sqlite.db` | SQLite audit database path |
| `LOG_PATH` | `logs/application.log` | Application log path |
| `OUTPUT_PATH` | `outputs/` | Reserved report output directory |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `LOG_MAX_BYTES` | `10485760` | Maximum size of one log file before rotation |
| `LOG_BACKUP_COUNT` | `5` | Number of rotated log files retained |
| `AMOUNT_TOLERANCE` | `0.01` | Maximum absolute amount difference considered equal |
| `MAX_UPLOAD_SIZE_MB` | `200` | Deployment upload-size setting; set Streamlit's `server.maxUploadSize` to the same value |
| `MAX_XLSX_EXPANDED_SIZE_MB` | `1024` | Maximum uncompressed size of an XLSX archive before workbook parsing |
| `TABLE_PAGE_SIZE` | `500` | Transaction rows rendered per table page |

For Streamlit upload sizing, add `.streamlit/config.toml`:

```toml
[server]
maxUploadSize = 200
```

The app does not persist uploaded source files. It prepares one requested workbook at a time, retains only that workbook in the user's Streamlit session, and creates the file in write-only mode. XLSX cell values that look like formulas are emitted as text.

`Summary_Report.xlsx` contains reconciliation metrics and status totals. The campaign report includes campaign-level record counts, match rate, source amounts, and variance. The executive report includes a headline, narrative, KPIs, prioritized rule-based observations, and campaign summary. SQLite audit records retain file names, sizes, row counts, checksums, run metadata, and safe event details—not uploaded file contents.

## Tests

```powershell
pip install -r requirements-dev.txt
pytest -q
```

## Deployment

1. Provision Python 3.12 and install compatible runtime dependencies from `requirements.txt`; use `requirements-dev.txt` for test tooling.
2. Set `APP_ENV=production`, `DATABASE_PATH` to a persistent, access-controlled volume, `LOG_PATH` to the approved log destination, and `AMOUNT_TOLERANCE` to the finance-approved value.
3. Run behind an authenticated reverse proxy with TLS, request-size limits, and restricted network access. Configure Streamlit's `server.maxUploadSize` for the environment.
4. Persist the SQLite database and logs on encrypted storage, apply backups and retention policy, and grant access only to authorized Finance Operations users.
5. Run the container as its non-root `app` user. Mount the database and log paths on persistent, access-controlled storage. The app has no built-in authentication: keep it behind an authenticated reverse proxy or an organization-approved identity layer and do not expose port 8501 directly to an untrusted network.
6. Configure the compressed upload size and XLSX expanded-size limits for host capacity. XLSX archives exceeding `MAX_XLSX_EXPANDED_SIZE_MB` are rejected before workbook parsing to reduce decompression-bomb risk.
7. For concurrent or sustained high-volume production workloads, replace SQLite with a managed relational database and process very large inputs in a dedicated batch worker. CSV is preferred for million-row inputs; XLSX parsing has higher transient memory cost. Pandas reconciliation, post-run results, and each user's Streamlit session still hold data in memory. Validate peak resident memory and throughput with representative 1M+ row files on the intended host and at expected concurrency; this application is not an out-of-core processor and cannot guarantee a row ceiling independent of available RAM.

The reconciliation join and aggregation are memory-bandwidth-bound and require shared duplicate detection across both sources. Multiprocessing is intentionally not enabled: on Windows, transferring million-row DataFrames to child processes adds serialization cost and multiplies peak memory rather than improving the core operation. Use a distributed or database-backed batch architecture if the measured workload exceeds a single process.

## Data and security notes

The default interface has no identity provider integration. The audit actor is `streamlit_user`; deployments should add organization-approved authentication and inject a verified actor identity before production use. Avoid storing raw financial input files in shared directories. Protect database/log backups as sensitive financial data.
