# Tester Backend: Data Model

## SQLite (test run results)

| Table | Columns |
|---|---|
| `runs` | `id` (uuid), `dataset`, `parallelism`, `status` (`running`, `paused`, `stopped`, `completed`), `documents_total`, `started_at`, `finished_at`, `overlapped` (another run was active at the same time), `summary_json` (cached summary, computed at the end) |
| `run_documents` | `run_id`, `invoice_id`, `document_id`, `correlation_id`, `uploaded_at`, `finished_at`, `status` (`pending` = not uploaded yet, `uploaded`, `passed`, `failed`, `error`, `timeout`), `error_stage`, `error_code`, `error_message`, `active_wait_seconds` (time counted toward the timeout), `timeout_reason`, `extracted_json` (copy of the row and its lines), `extraction_model`, `prompt_version` |
| `run_field_results` | `run_id`, `invoice_id`, `position` (null for header fields; line position for line fields), `field`, `outcome` (`match`, `mismatch`, `missing`, `unexpected`), `expected`, `actual` |
| `run_line_results` | `run_id`, `invoice_id`, `position` (1-based), `golden_line_no` (null for extra), `extracted_line_no` (null for missing), `outcome` (`compared`, `missing`, `extra`) |

Keys: `runs.id`; `(run_id, invoice_id)` for `run_documents`; indexes on `run_documents(document_id)` (failure lookup) and `run_field_results(run_id, field)`. Schema created by embedded migrations at startup (`sqlx migrate`).

## Golden set (DuckDB, read-only)

Gzip JSON Lines shards, tables `invoices` and `line_items`. Full layout and field list: [`dataset-format.md`](dataset-format.md).

## Application database (PostgreSQL, read-only)

Procurement tables: see `integrator/specs/data-model.md`. Read with role `tester`.
