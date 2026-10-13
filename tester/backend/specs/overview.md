# Tester Backend: Overview

## Purpose

Does all the work of the testing client: talks to databases, sends requests to the pipeline in parallel, runs analytical queries.

## Stack

Rust. Data stores:

| Store | Role |
|---|---|
| DuckDB | Reads the golden set (gzip JSON Lines shards produced by docsynth) via its JSON readers |
| SQLite | Stores test run results and history |
| PostgreSQL | Connects to the real application database to read the procurement table |

## Responsibilities

- Discover datasets: every subdirectory of the datasets root (`tester/datasets`, a symlink). See [`dataset-format.md`](dataset-format.md).
- Let the user switch between datasets (e.g. `run-001`) and start a run on the selected one.
- Start a test run: send the dataset's invoices to `ingestor` with a configurable parallel load.
- Compare resulting procurement rows with the golden set and record the result per document.
- Provide run history and per-document failure details (including access to the source file).
- Expose all of the above to the frontend over HTTP.

## Interfaces

- Dataset input: see [`dataset-format.md`](dataset-format.md).
- HTTP API for the frontend: see [`api.md`](api.md).
- Result storage: see [`data-model.md`](data-model.md).

## Open questions

- OPEN: how a run detects pipeline completion for a document (polling PostgreSQL by `document_id`, timeout).
- OPEN: comparison rules (exact match, numeric tolerance, string normalisation). The compared field subset is defined by the extraction schema (ADR 0008).
- OPEN: whether DuckDB reads the shards directly on every run or they are imported into SQLite/DuckDB once per dataset.
