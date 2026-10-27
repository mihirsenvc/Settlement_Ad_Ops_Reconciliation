PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    run_id TEXT PRIMARY KEY,
    executed_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    source_files TEXT NOT NULL,
    total_records INTEGER NOT NULL CHECK (total_records >= 0),
    matched_records INTEGER NOT NULL CHECK (matched_records >= 0),
    unmatched_records INTEGER NOT NULL CHECK (unmatched_records >= 0),
    variance_amount REAL NOT NULL,
    processing_seconds REAL NOT NULL CHECK (processing_seconds >= 0),
    result_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_runs_executed_at
    ON reconciliation_runs(executed_at);

CREATE TABLE IF NOT EXISTS user_activity (
    event_id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    run_id TEXT,
    details TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES reconciliation_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_user_activity_occurred_at
    ON user_activity(occurred_at);

CREATE TABLE IF NOT EXISTS audit_logs (
    event_id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'ERROR')),
    event_type TEXT NOT NULL,
    user_session TEXT NOT NULL,
    run_id TEXT,
    details TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES reconciliation_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_occurred_at
    ON audit_logs(occurred_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_session
    ON audit_logs(user_session, occurred_at);

CREATE TABLE IF NOT EXISTS uploads (
    upload_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    file_role TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL CHECK (file_size_bytes >= 0),
    record_count INTEGER NOT NULL CHECK (record_count >= 0),
    uploaded_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES reconciliation_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_uploads_run_id ON uploads(run_id);

CREATE TABLE IF NOT EXISTS reconciliation_results (
    run_id TEXT PRIMARY KEY,
    total_settlement_amount REAL NOT NULL,
    total_wallet_amount REAL NOT NULL,
    matched_amount REAL NOT NULL,
    unmatched_amount REAL NOT NULL,
    variance_amount REAL NOT NULL,
    metrics_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES reconciliation_runs(run_id)
);

CREATE TABLE IF NOT EXISTS execution_history (
    execution_id TEXT PRIMARY KEY,
    run_id TEXT,
    user_session TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('COMPLETED', 'FAILED')),
    duration_seconds REAL NOT NULL CHECK (duration_seconds >= 0),
    errors_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES reconciliation_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_execution_history_session
    ON execution_history(user_session, started_at);

CREATE TABLE IF NOT EXISTS campaign_master (
    campaign_id TEXT PRIMARY KEY,
    campaign_name TEXT NOT NULL,
    business_unit TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
