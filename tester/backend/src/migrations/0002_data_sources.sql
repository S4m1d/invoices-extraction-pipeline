-- Data sources (tester/backend/specs/data-sources.md, data-model.md): runs know their source and kind;
-- documents runs store required-field outcomes (present, missing) and check results.
ALTER TABLE runs ADD COLUMN source TEXT NOT NULL DEFAULT 'synthetic';
ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'synthetic' CHECK (kind IN ('synthetic', 'documents'));

-- SQLite cannot change a CHECK constraint in place: rebuild the table.
CREATE TABLE run_field_results_new (
    run_id     TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    position   INTEGER,
    field      TEXT NOT NULL,
    outcome    TEXT NOT NULL CHECK (outcome IN ('match', 'mismatch', 'missing', 'unexpected', 'present')),
    expected   TEXT,
    actual     TEXT
);
INSERT INTO run_field_results_new (run_id, invoice_id, position, field, outcome, expected, actual)
    SELECT run_id, invoice_id, position, field, outcome, expected, actual FROM run_field_results;
DROP TABLE run_field_results;
ALTER TABLE run_field_results_new RENAME TO run_field_results;
CREATE INDEX run_field_results_run_field ON run_field_results (run_id, field);
CREATE INDEX run_field_results_doc ON run_field_results (run_id, invoice_id);

CREATE TABLE run_check_results (
    run_id     TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    "check"    TEXT NOT NULL CHECK ("check" IN ('lines_sum', 'totals', 'line_amount')),
    position   INTEGER,
    outcome    TEXT NOT NULL CHECK (outcome IN ('pass', 'fail', 'not_applicable')),
    expected   TEXT,
    actual     TEXT
);
CREATE INDEX run_check_results_doc ON run_check_results (run_id, invoice_id);
