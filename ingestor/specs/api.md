# Ingestor: HTTP API

Status: approved.

## `POST /documents`

Accepts one invoice document.

- Request: `multipart/form-data`, field `file` (PDF). Optional field `correlation_id` (UUID).
- Response `202 Accepted`: `{ "document_id": "<uuid>", "correlation_id": "<uuid>" }`. `correlation_id` is the client's value, or the generated one if the client did not send it.
- Errors: `400` (invalid request or file), `408` (upload too slow), `413` (too large), `415` (not multipart or not a PDF), `503` (queue/storage unavailable), `500` (unexpected error). Body: see [Error response body](#error-response-body).

## Request restrictions

Checked before anything is written to object storage or published to the queue. A rejected request leaves no trace in MinIO or RabbitMQ.

### Size

- The whole request body must be at most **5 MiB** (5 242 880 bytes). This includes multipart framing, so the usable file size is slightly smaller.
- A request whose `Content-Length` exceeds the limit is rejected before the body is read.
- A request without `Content-Length` (chunked) is read up to the limit; reading stops and the request is rejected as soon as the limit is exceeded.
- Response: `413 Payload Too Large`.

### Other restrictions

| # | Restriction | On violation | Status |
|---|---|---|---|
| R1 | `Content-Type` of the request is `multipart/form-data` | `415` | confirmed |
| R2 | Exactly one `file` part; no other file parts | `400` | confirmed |
| R3 | The file is non-empty | `400` | confirmed |
| R4 | The file starts with the PDF signature `%PDF-` (magic bytes; the part's `Content-Type` and filename extension are ignored) | `415` | confirmed |
| R5 | `correlation_id`, if present, is a valid UUID in canonical hyphenated form (case-insensitive; stored lowercase) | `400` | confirmed |
| R6 | Unknown form fields are rejected | `400` | confirmed |
| R7 | The upload must finish within 30 s (server read timeout) | connection closed / `408` | confirmed |

Out of scope (content is not parsed, see [`overview.md`](overview.md) non-goals): page count, encryption, PDF structural validity, scanned vs text PDF.

## Correlation ID

Rules are in `/specs/pipeline-contracts.md` (Correlation ID). Ingestor-specific behaviour:

- The ingestor is the only place a `correlation_id` is created.
- If the client sends a valid `correlation_id`, it is used as is (normalized to lowercase). Clients should send a new one for every request (see the expected use in `/specs/pipeline-contracts.md`).
- The ingestor does not check for duplicates; a reused `correlation_id` is accepted.
- If the client sends none, or an empty value, the ingestor generates a UUIDv4.
- The value is determined at the start of request handling, so log entries for rejected requests carry it too. If the client's value is invalid (R5), the request is rejected and its log entries use a freshly generated ID.

### Error response body

Every `4xx` and `5xx` response (where a response can still be sent) has a JSON body:

```json
{
  "error": { "code": "file_too_large", "message": "request body exceeds 5242880 bytes" },
  "correlation_id": "<uuid>"
}
```

- `code`: machine-readable, from the table below. `message`: human-readable details, not to be parsed.
- `correlation_id`: the ID used in this request's log entries (see [Correlation ID](#correlation-id)).
- These codes are local to the ingestor HTTP API and are separate from the `documents.failed` error codes in `/specs/pipeline-contracts.md`.

| Code | HTTP | Cause |
|---|---|---|
| `invalid_content_type` | `415` | R1 |
| `invalid_file_part` | `400` | R2 |
| `empty_file` | `400` | R3 |
| `not_pdf` | `415` | R4 |
| `invalid_correlation_id` | `400` | R5 |
| `unknown_field` | `400` | R6 |
| `request_timeout` | `408` | R7 |
| `file_too_large` | `413` | Size limit |
| `service_unavailable` | `503` | RabbitMQ or MinIO unreachable |
| `internal_error` | `500` | Unexpected error |

## `GET /healthz`

Used by the Docker healthcheck.

- `200 OK` with `{ "status": "ok" }` when the process is up and both RabbitMQ and MinIO are reachable.
- `503 Service Unavailable` with `{ "status": "unavailable", "failing": ["rabbitmq" | "minio", ...] }` otherwise.

## Not provided

- No status endpoint (`GET /documents/{id}`). The ingestor has no state beyond the request; document status is tracked by the tester (via `documents.failed`) and the integrator.
