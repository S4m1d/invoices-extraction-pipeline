# Tester

Evaluation of the pipeline on configured data sources ([`backend/specs/data-sources.md`](backend/specs/data-sources.md)): synthetic datasets with golden sets, generated manually by docsynth ([`backend/specs/dataset-format.md`](backend/specs/dataset-format.md)), and plain PDFs such as real invoices, which have no golden set and are checked for required fields and consistent totals ([`backend/specs/validation.md`](backend/specs/validation.md)).

## Testing client

A client app for testing the pipeline:

- Can generate different load, with requests sent in parallel.
- Shows the history of test runs.
- The user chooses between synthetic and real-document datasets and can switch between all datasets of the configured sources.
- The user can open a failed run and inspect where things went wrong, including the file the pipeline failed to process or processed incorrectly, and the pipeline's intermediate files (layout markdown, extracted JSON, diagnostics).
- Runs can be paused, resumed and stopped; several can run at once.

## Parts

- [`backend/`](backend/): Rust; does all the work.
- [`frontend/`](frontend/): React web UI; only visualizes what the backend returns.
