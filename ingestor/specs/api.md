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

### Concurrency

- At most `INGESTOR_MAX_CONCURRENT_UPLOADS` `POST /documents` requests are handled at once (default `20`). Each one holds up to 5 MiB in memory until it is answered.
- A request over the limit is rejected immediately, before its body is read: `503` with code `server_busy`. Clients may retry later.
- `GET /healthz` is not limited.

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

- `code`: machine-readable, from the table below. `message`: human-readable, not to be parsed.
- `4xx` messages explain what is wrong with the request. `5xx` messages are generic and never reveal internal details (hosts, ports, dependency errors): `service_unavailable` returns `service temporarily unavailable, retry later`, `internal_error` returns `internal error`. The cause is written to the log entry for the request (field `error`), found by `correlation_id`.
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
| `server_busy` | `503` | Concurrent upload limit reached |
| `internal_error` | `500` | Unexpected error |
| `malformed_request` | `400` | Body is not valid multipart |
| `not_found` | `404` | Unknown path |
| `method_not_allowed` | `405` | Wrong method on a known path; `Allow` header is set |

Processing order: parts are validated in the order they arrive and the first violation is returned. A `correlation_id` part that arrives after the failing part is not read, so the response and logs use the generated ID.

Whenever the generated ID is used even though the client may have sent its own, the ingestor writes a `WARN` log entry saying so, with the generated `correlation_id` and the reason:

- `client correlation_id invalid, using generated correlation_id` (R5 violation; the entry also contains the rejected value, truncated to 64 characters, as `client_correlation_id`).
- `request rejected before correlation_id was read, using generated correlation_id` (a `POST /documents` rejection that happens before the whole body was read and before any `correlation_id` part was processed; not logged when the body was fully read without a `correlation_id` part, since then the client sent none).

## `GET /healthz`

Used by the Docker healthcheck. RabbitMQ is reachable when the ingestor can connect and the queue `documents.ingested` exists; MinIO is reachable when the bucket `invoices` exists. Each check times out after 3 s.

- `200 OK` with `{ "status": "ok" }` when the process is up and both RabbitMQ and MinIO are reachable.
- `503 Service Unavailable` with `{ "status": "unavailable", "failing": ["rabbitmq" | "minio", ...], "errors": { "<check>": "<reason>" } }` otherwise (shape: `/specs/pipeline-contracts.md`, Health endpoint).

## Not provided

- No status endpoint (`GET /documents/{id}`). The ingestor has no state beyond the request; document status is tracked by the tester (via `documents.failed`) and the integrator.
