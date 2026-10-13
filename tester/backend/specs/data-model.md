# Tester Backend: Data Model

Status: stub, to be written.

## SQLite (test run results)

PROPOSED tables:

- `runs`: id, dataset, parallelism, started_at, finished_at, status.
- `run_documents`: run_id, document_id, source file, status (`passed` / `failed` / `error` / `timeout`), failure details.
- `run_field_diffs`: per-field expected vs. actual values.

## Golden set (DuckDB, read-only)

Gzip JSON Lines shards, tables `invoices` and `line_items`. Full layout and field list: [`dataset-format.md`](dataset-format.md).

## Application database (PostgreSQL, read-only)

Procurement tables: see `integrator/specs/data-model.md`.
