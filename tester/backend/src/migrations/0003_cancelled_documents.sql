-- No per-document timeout (ADR 0016): Stop cancels documents still in the pipeline. `timeout` stays
-- for runs from before. SQLite cannot change a CHECK constraint in place: rebuild the table.
CREATE TABLE run_documents_new (
    run_id              TEXT NOT NULL REFERENCES runs (id),
    invoice_id          TEXT NOT NULL,
    document_id         TEXT,
    correlation_id      TEXT,
    uploaded_at         TEXT,
    finished_at         TEXT,
    status              TEXT NOT NULL CHECK (status IN ('pending', 'uploaded', 'passed', 'failed', 'error', 'cancelled', 'timeout')),
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
INSERT INTO run_documents_new SELECT run_id, invoice_id, document_id, correlation_id, uploaded_at, finished_at, status, error_stage,
    error_code, error_message, active_wait_seconds, timeout_reason, extracted_json, extraction_model, prompt_version FROM run_documents;
DROP TABLE run_documents;
ALTER TABLE run_documents_new RENAME TO run_documents;
CREATE INDEX run_documents_document_id ON run_documents (document_id);
CREATE INDEX run_documents_status ON run_documents (status);
