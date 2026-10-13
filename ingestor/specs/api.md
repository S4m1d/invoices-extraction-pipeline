# Ingestor: HTTP API

Status: stub, to be written.

## `POST /documents` (PROPOSED)

Accepts one invoice document.

- Request: `multipart/form-data`, field `file` (PDF). Optional `correlation_id`.
- Response `202 Accepted`: `{ "document_id": "<uuid>" }`.
- Errors: `400` (invalid file), `413` (too large), `503` (queue/storage unavailable).

## Other endpoints

- OPEN: health endpoint (`GET /healthz`).
- OPEN: status endpoint (`GET /documents/{id}`), only if needed by the testing client.
