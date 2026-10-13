# Tester

Evaluation of the pipeline against golden sets. Datasets (synthetic invoices and golden sets) are generated manually by docsynth and supplied by the author through the `tester/datasets` symlink. Format: [`backend/specs/dataset-format.md`](backend/specs/dataset-format.md).

## Testing client

A client app for testing the pipeline:

- Can generate different load, with requests sent in parallel.
- Shows the history of test runs.
- The user selects which dataset to use for a run and can switch between all datasets found under `tester/datasets` (currently `run-001`).
- The user can open a failed run and inspect where things went wrong, including the file the pipeline failed to process or processed incorrectly.

## Parts

- [`backend/`](backend/): Rust; does all the work.
- [`frontend/`](frontend/): React web UI; only visualizes what the backend returns.
