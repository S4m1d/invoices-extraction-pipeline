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

Crates: `axum` + `tokio` (HTTP), `sqlx` (SQLite and PostgreSQL), `duckdb` (bundled), `lapin` (RabbitMQ), `reqwest` (ingestor uploads), `aws-sdk-s3` (MinIO artifacts), `rust_decimal`, `tracing` with JSON output.

## Responsibilities

- Discover datasets: every subdirectory of the datasets root (`tester/datasets`, a symlink). See [`dataset-format.md`](dataset-format.md).
- Let the user switch between datasets (e.g. `run-001`) and start a run on the selected one.
- Start a test run: send the dataset's invoices to `ingestor` with a configurable parallel load.
- Compare resulting procurement rows with the golden set and record the result per document. Rules: [`comparison.md`](comparison.md).
- Provide run history and per-document failure details (including access to the source file and the pipeline's intermediate artifacts).
- Expose all of the above to the frontend over HTTP.

## Interfaces

- Dataset input: see [`dataset-format.md`](dataset-format.md).
- HTTP API for the frontend: see [`api.md`](api.md).
- Result storage: see [`data-model.md`](data-model.md).
- Pipeline: `ingestor` HTTP API (uploads), `documents.failed` queue (failures, contract), PostgreSQL (results, read-only role `tester`), MinIO (artifacts, read-only).

## Runs

### Starting

- Several runs can be active at once; each tracks its own documents (by `document_id`). Runs that overlap in time slow each other down, so the summary marks a run whose time overlapped with another run's (durations not comparable).
- A run uploads every document of the dataset (all units) to `POST /documents` of the ingestor, at most `parallelism` uploads in flight (default 4, 1..32). Each upload gets a new `correlation_id` (UUIDv4, contract: one per request). The run records `invoice_id` → `document_id` → `correlation_id`.
- Upload errors: `503` (`server_busy`, `service_unavailable`) and connection errors are retried with backoff (1, 2, 4, 8 s; 5 attempts); then the document is `error` with `upload_failed`. Other `4xx` are `error` at once.

### Completion

A document is finished when one of these happens first:

- Its `invoices` row appears in PostgreSQL (polled every 2 s for all unfinished documents, by `document_id`).
- A failure message for it arrives on `documents.failed` (the tester consumes this queue, contract): status `error` with the failure's `stage`, `error_code` and `error_message`. Exception: `duplicate_document` (integrator, ADR 0012) is ignored when the row exists, since it comes from a redelivery after a successful write.
- No row and no failure within `TESTER_DOCUMENT_TIMEOUT` (default 10 min) of active pipeline time after its upload: `timeout`.

Failure messages for documents that belong to no known run (manual uploads, earlier runs) are acked and dropped.

**Pause-aware timeout.** Workers do not fail documents while they wait (LLM usage limit or daily budget, LLM misconfiguration, PostgreSQL down: see the services' specs); the documents stay queued and continue later. So that such waits do not turn into timeouts, the backend polls every service's `/healthz` (`ingestor`, `text-extractor`, `fields-extractor`, `integrator`) every 5 s. While any of them reports failing, the timeout clocks of all unfinished documents stop, and every active run shows the pipeline as waiting, with the services and failing checks (for example `fields-extractor: llm`). A document that times out later carries the last waiting state as its timeout reason.

The services' `/healthz` responses carry an `errors` object with the reason per failing check (`/specs/pipeline-contracts.md`, Health endpoint), so the waiting state names the cause, for example `fields-extractor: llm_daily_budget_reached (paused): spent $10.0012 of $10.00`.

### Comparing

When the row is there, the backend reads the invoice and its lines (role `tester`), compares them with the golden set ([`comparison.md`](comparison.md)) and stores the outcome and a copy of the extracted values in SQLite, so results stay inspectable after the pipeline database is reset or a document is reprocessed.

### Stop, pause and resume

- **Stop** (`POST /runs/{id}/stop`): no further uploads, ever. Documents already uploaded are tracked until they finish or time out; the run ends as `stopped`.
- **Pause** (`POST /runs/{id}/pause`): no further uploads until resumed. Documents already uploaded keep being tracked. The run is `paused`.
- **Resume** (`POST /runs/{id}/resume`): uploading continues with the documents not uploaded yet.
- A run is `completed` when every document was uploaded and is finished.
- On a backend restart, every `running` run becomes `paused`: the upload state is in SQLite, so it can be resumed; documents already uploaded keep being tracked after the restart.

### Cost

Every document passes through the real LLM unless the stack runs in fake mode (`make up-fake`). Before a run starts, the UI shows the number of documents and an estimate (documents × `TESTER_COST_PER_DOCUMENT_USD`, default $0.0026, the measured Haiku 5.5 average), and today's LLM spend read from the `fields-extractor-usage` bucket (sum of today's counters; see `fields-extractor/specs/overview.md`, Daily budget), next to the daily budget.

## Analytics

The run summary contains, per run:

- Document counts by status (`passed`, `failed`, `error`, `timeout`), and whether the run overlapped with another run.
- Field accuracy per compared field (match / mismatch / missing / unexpected counts), and line accuracy (matched, missing, extra).
- The same accuracy broken down by golden metadata: `template_id`, `currency`, `language`.
- Pipeline errors by `stage` and `error_code`.
- The `extraction_model` and `prompt_version` seen in the run's rows (to compare prompt versions across runs).
- Durations: run wall-clock time, per-document time from upload to completion (median, p95, max).

Computed with DuckDB over the run's results (SQLite, attached) joined with the golden metadata.

## Golden set access

DuckDB reads the gzip JSON Lines shards directly (`read_json_auto` on the `.jsonl.gz` files of all units) when a run starts and when a summary is computed; nothing is imported. run-001 (200 invoices, 14,392 lines) reads in well under a second, and datasets change only when the author adds new ones.

## Deployment and configuration

- A container in the `infra` Compose project (`tester`), built from `tester/` (backend and frontend in one image: the backend serves the frontend's built files, see `tester/frontend/specs/overview.md`). Published on `127.0.0.1:8090`.
- Datasets: Docker does not resolve the `tester/datasets` symlink inside bind mounts, so Compose mounts the symlink's target (`DATASETS_DIR` in `infra/.env`) read-only at `/datasets`.
- SQLite file in the named volume `tester-data`.

| Variable | Default | Description |
|---|---|---|
| `TESTER_HTTP_ADDR` | `:8090` | HTTP API and frontend |
| `TESTER_DATASETS_DIR` | `/datasets` | Datasets root |
| `TESTER_DB_PATH` | `/data/tester.sqlite` | SQLite file |
| `INGESTOR_URL` | `http://ingestor:8080` | |
| `AMQP_URL` | | RabbitMQ (consumes `documents.failed`) |
| `DATABASE_URL` | | PostgreSQL, role `tester` |
| `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | | Artifacts and today's LLM spend |
| `TESTER_DOCUMENT_TIMEOUT` | `600` | Seconds until an unfinished document is `timeout` |
| `TESTER_COST_PER_DOCUMENT_USD` | `0.0026` | For the cost estimate |
| `TESTER_SERVICE_HEALTH_URLS` | the four services' `/healthz` in the Compose network | Polled for the pause-aware timeout |

## Open questions

None. Resolved: completion detection (see Runs, Completion), comparison rules ([`comparison.md`](comparison.md)), golden set access (read directly), health reasons (contract, Health endpoint), backend restart (runs become paused).
