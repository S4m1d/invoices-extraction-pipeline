# Tester Backend: HTTP API

Status: approved. JSON over HTTP, no authentication (bound to localhost). Errors: `{"error": {"code", "message"}}`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/sources` | Configured data sources: `name`, `kind` (`synthetic`, `documents`), connection type, `available` and the reason if not, and for `documents` sources the effective `required_fields` |
| GET | `/api/datasets` | Datasets of all available sources: `source`, `kind`, `name`, `total_documents`, plus the cost estimate; `synthetic` datasets also units, `created_at`, conditions; a dataset that cannot be run carries an `error`. Filter `?source=` |
| GET | `/api/spend` | Today's LLM spend (UTC) and the fields-extractor daily budget |
| POST | `/api/runs` | Start a run: `{"source": "synthetic", "dataset": "run-001", "parallelism": 4}` → `201` with the run (including `source`, `kind`). Unknown source or dataset: `404`. Several runs may be active |
| GET | `/api/runs` | Run history, newest first; each run has `source`, `dataset`, `kind`. Filter `?kind=` |
| GET | `/api/runs/{id}` | Run status, progress (counts by status), the pipeline's waiting state if any, and, when finished, the summary (see `overview.md`, Analytics; for `documents` runs [`validation.md`](validation.md), Summary) |
| POST | `/api/runs/{id}/pause` | Stop uploading until resumed; `409` unless `running` |
| POST | `/api/runs/{id}/resume` | Continue uploading; `409` unless `paused` |
| POST | `/api/runs/{id}/stop` | No further uploads, ever; `409` if already `stopped` or `completed` |
| GET | `/api/pipeline` | The pipeline's state as the tester sees it: each service's health and the waiting state used for timeouts |
| GET | `/api/runs/{id}/documents` | Per-document results; filters `status`, `currency` (golden for `synthetic`, extracted for `documents`), `template_id` (`synthetic` only); paging |
| GET | `/api/runs/{id}/documents/{invoice_id}` | Details: status, pipeline error or timeout reason; `synthetic`: field outcomes (expected vs actual), line comparison by position; `documents`: extracted header and lines, required-field outcomes, check results (expected vs actual) |
| GET | `/api/runs/{id}/documents/{invoice_id}/artifacts/{name}` | `original.pdf` (from the dataset's source), `layout.md`, `extracted.json`, `text-extractor-failure.json`, `fields-extractor-failure.json` (from MinIO, if they exist) |
| GET | `/healthz` | Health: SQLite, PostgreSQL, RabbitMQ, MinIO. An unavailable data source is not a health failure; it is shown in `/api/sources` |

Paths under `/api`, so the frontend's static files can be served from `/` by the same server.
