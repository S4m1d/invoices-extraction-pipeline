# Tester Backend: HTTP API

Status: approved. JSON over HTTP, no authentication (bound to localhost). Errors: `{"error": {"code", "message"}}`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/datasets` | Datasets (subdirectories of the datasets root): name, `total_documents`, units, `created_at`, conditions, plus the cost estimate |
| GET | `/api/spend` | Today's LLM spend (UTC) and the fields-extractor daily budget |
| POST | `/api/runs` | Start a run: `{"dataset": "run-001", "parallelism": 4}` → `201` with the run. Several runs may be active |
| GET | `/api/runs` | Run history, newest first |
| GET | `/api/runs/{id}` | Run status, progress (counts by status), the pipeline's waiting state if any, and, when finished, the summary (see `overview.md`, Analytics) |
| POST | `/api/runs/{id}/pause` | Stop uploading until resumed; `409` unless `running` |
| POST | `/api/runs/{id}/resume` | Continue uploading; `409` unless `paused` |
| POST | `/api/runs/{id}/stop` | No further uploads, ever; `409` if already `stopped` or `completed` |
| GET | `/api/pipeline` | The pipeline's state as the tester sees it: each service's health and the waiting state used for timeouts |
| GET | `/api/runs/{id}/documents` | Per-document results; filters `status`, `template_id`, `currency`; paging |
| GET | `/api/runs/{id}/documents/{invoice_id}` | Details: status, pipeline error or timeout reason, field outcomes (expected vs actual), line comparison by position |
| GET | `/api/runs/{id}/documents/{invoice_id}/artifacts/{name}` | `original.pdf` (from the dataset), `layout.md`, `extracted.json`, `text-extractor-failure.json`, `fields-extractor-failure.json` (from MinIO, if they exist) |
| GET | `/healthz` | Health: SQLite, PostgreSQL, RabbitMQ, MinIO, datasets root readable |

Paths under `/api`, so the frontend's static files can be served from `/` by the same server.
