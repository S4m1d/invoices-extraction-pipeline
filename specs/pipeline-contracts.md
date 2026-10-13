# Pipeline Contracts

Cross-module contracts. Each module's own spec must stay consistent with this file. Decision record: `docs/decisions/0009-pipeline-contracts.md`.

## Infrastructure

- Message broker: RabbitMQ.
- Object storage: MinIO (S3-compatible).
- Database: PostgreSQL (owned by `integrator`).
- Everything runs in Docker containers (see `infra/`).

## Queues

Direct durable queues on the default exchange, one per stage. Messages are persistent (delivery mode 2). Consumers use manual acknowledgement and ack only after the work is done and the output is published (at-least-once delivery; consumers must be idempotent).

| # | Producer | Queue | Consumer | Message references |
|---|---|---|---|---|
| 1 | `ingestor` | `documents.ingested` | `text-extractor` | `original.pdf` |
| 2 | `text-extractor` | `documents.text_extracted` | `fields-extractor` | `layout.md` |
| 3 | `fields-extractor` | `documents.fields_extracted` | `integrator` | `extracted.json` |
| F | any service | `documents.failed` | `tester` backend | stage + error |

Each of queues 1 to 3 has a dead-letter queue named `<queue>.dlq`, used for messages that cannot be parsed or are rejected.

## Object storage layout

One bucket. Keys by document ID:

```
documents/{document_id}/original.pdf       written by ingestor
documents/{document_id}/layout.md          written by text-extractor
documents/{document_id}/extracted.json     written by fields-extractor
```

Objects are written before the message that references them is published. Re-processing a document overwrites its objects.

## Message envelope

Every message on queues 1 to 3 is JSON with:

| Field | Type | Description |
|---|---|---|
| `schema_version` | int | Envelope version, starts at `1` |
| `stage` | string | Producer module: `ingestor`, `text-extractor`, `fields-extractor` |
| `document_id` | string (UUID) | Assigned by `ingestor` |
| `correlation_id` | string | Ties a message to a test run or request; may be empty |
| `object_key` | string | Key of the artifact the consumer must read |
| `created_at` | string (RFC 3339) | Message creation time |

## Failures

- A consumer retries a failing document up to the retry limit (OPEN: value, backoff; per-module limits in module specs).
- After that it publishes a failure message to `documents.failed` and acks the original message.

Failure message (JSON):

| Field | Type | Description |
|---|---|---|
| `schema_version` | int | `1` |
| `stage` | string | Module that gave up |
| `document_id` | string | |
| `correlation_id` | string | |
| `error_code` | string | Short machine-readable code |
| `error_message` | string | Human-readable details |
| `created_at` | string (RFC 3339) | |

The tester finds the file to inspect via `documents/{document_id}/original.pdf`, plus any intermediate objects that exist.

OPEN: error code list.

## Extracted JSON schema

Owned by `fields-extractor` (see `fields-extractor/specs/extraction-schema.md`). Stored as `extracted.json`, consumed by `integrator`.
