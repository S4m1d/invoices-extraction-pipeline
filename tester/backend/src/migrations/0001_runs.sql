-- Run results (tester/backend/specs/data-model.md).
CREATE TABLE runs (
    id              TEXT PRIMARY KEY,
    dataset         TEXT NOT NULL,
    parallelism     INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('running', 'paused', 'stopped', 'completed')),
    documents_total INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    overlapped      INTEGER NOT NULL DEFAULT 0,
    summary_json    TEXT
);

CREATE TABLE run_documents (
    run_id              TEXT NOT NULL REFERENCES runs (id),
    invoice_id          TEXT NOT NULL,
    document_id         TEXT,
    correlation_id      TEXT,
    uploaded_at         TEXT,
    finished_at         TEXT,
    status              TEXT NOT NULL CHECK (status IN ('pending', 'uploaded', 'passed', 'failed', 'error', 'timeout')),
    error_stage         TEXT,
    error_code          TEXT,
    error_message       TEXT,
    active_wait_seconds REAL NOT NULL DEFAULT 0,
    timeout_reason      TEXT,
    extracted_json      TEXT,
    extraction_model    TEXT,
    prompt_version      TEXT,
    PRIMARY KEY (run_id, invoice_id)
);
CREATE INDEX run_documents_document_id ON run_documents (document_id);
CREATE INDEX run_documents_status ON run_documents (status);

CREATE TABLE run_field_results (
    run_id     TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    position   INTEGER,
    field      TEXT NOT NULL,
    outcome    TEXT NOT NULL CHECK (outcome IN ('match', 'mismatch', 'missing', 'unexpected')),
    expected   TEXT,
    actual     TEXT
);
CREATE INDEX run_field_results_run_field ON run_field_results (run_id, field);
CREATE INDEX run_field_results_doc ON run_field_results (run_id, invoice_id);

CREATE TABLE run_line_results (
    run_id            TEXT NOT NULL,
    invoice_id        TEXT NOT NULL,
    position          INTEGER NOT NULL,
    golden_line_no    INTEGER,
    extracted_line_no INTEGER,
    outcome           TEXT NOT NULL CHECK (outcome IN ('compared', 'missing', 'extra'))
);
CREATE INDEX run_line_results_doc ON run_line_results (run_id, invoice_id);
