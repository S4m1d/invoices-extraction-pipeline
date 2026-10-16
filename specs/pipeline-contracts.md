# Pipeline Contracts

Cross-module contracts. Each module's own spec must stay consistent with this file. Decision record: `docs/decisions/0009-pipeline-contracts.md`.

## Infrastructure

- Message broker: RabbitMQ.
- Object storage: MinIO (S3-compatible).
- Database: PostgreSQL (owned by `integrator`).
- Everything runs in Docker containers (see `infra/`).

## Queues

Direct durable queues on the default exchange, one per stage. Messages are persistent (delivery mode 2). Consumers use manual acknowledgement and ack only after the work is done and the output is published (at-least-once delivery; consumers must be idempotent, with one exception: `integrator` fails a document whose `document_id` is already stored, see `docs/decisions/0012-integrator-duplicates-and-outages.md`).

| # | Producer | Queue | Consumer | Message references |
|---|---|---|---|---|
| 1 | `ingestor` | `documents.ingested` | `text-extractor` | `original.pdf` |
| 2 | `text-extractor` | `documents.text_extracted` | `fields-extractor` | `layout.md` |
| 3 | `fields-extractor` | `documents.fields_extracted` | `integrator` | `extracted.json` |
| F | any service | `documents.failed` | `tester` backend | stage + error |

Each of queues 1 to 3 has a dead-letter queue named `<queue>.dlq`, used for messages that cannot be parsed or are rejected.

### Dead-lettering

Decision record: `docs/decisions/0011-failure-handling.md`.

- Stage queues are declared plain: durable, no `x-` arguments. Any party may declare them (or check them passively) without conflicts.
- Dead-lettering is configured on the broker by `infra/` with a policy, not with queue arguments:
  - Policy `dead-letter`, pattern `^documents\.(ingested|text_extracted|fields_extracted)$`, apply to queues, definition `{"dead-letter-exchange": "dlx"}`.
  - Exchange `dlx` (direct, durable). Each `<queue>.dlq` (durable) is bound to `dlx` with routing key `<queue>`. A dead-lettered message keeps its original routing key (the queue name, since producers publish on the default exchange), so it lands in the matching `.dlq`.
- A consumer sends a message to the DLQ by rejecting it without requeue (`basic.reject` / `basic.nack` with `requeue=false`). Consumers never publish to `.dlq` queues directly.
- `documents.failed` and the `.dlq` queues are not dead-lettered.

## Object storage layout

### Bucket `invoices`: pipeline data

Keys by document ID:

```
documents/{document_id}/original.pdf       written by ingestor
documents/{document_id}/layout.md          written by text-extractor
documents/{document_id}/extracted.json     written by fields-extractor
```

Objects are written before the message that references them is published. Re-processing a document overwrites its objects.

### Failure buckets: diagnostics for manual checks

Decision record: `docs/decisions/0011-failure-handling.md`.

- Each stage that can fail a document for a reason a human should look into has its own bucket, named `{stage}-failures` (for example `text-extractor-failures`). A stage writes only to its own failure bucket.
- Key: `{document_id}.json`. The content is defined in the stage's spec; it always contains `document_id`, `correlation_id`, `error_code`, `error_message` and `created_at`.
- Which error codes produce a diagnostics file is defined in the stage's spec.
- The diagnostics file is written before the failure message is published. Re-processing overwrites it.
- Buckets are created by `infra/`. Current failure buckets: `text-extractor-failures`, `fields-extractor-failures`.

Module-private buckets (not part of the pipeline data flow): `fields-extractor-usage` (daily LLM spend, see `fields-extractor/specs/overview.md`).

## Message envelope

Every message on queues 1 to 3 is JSON with:

| Field | Type | Description |
|---|---|---|
| `schema_version` | int | Envelope version, starts at `1` |
| `stage` | string | Producer module: `ingestor`, `text-extractor`, `fields-extractor` |
| `document_id` | string (UUID) | Assigned by `ingestor` |
| `correlation_id` | string (UUID) | Pass-through tracing ID, see [Correlation ID](#correlation-id); never empty |
| `object_key` | string | Key of the artifact the consumer must read |
| `created_at` | string (RFC 3339) | Message creation time |

## Correlation ID

Decision record: `docs/decisions/0010-correlation-id.md`.

- `correlation_id` is a UUID in canonical lowercase form (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`).
- It is assigned once, at the entry point: `ingestor` takes it from the client request or generates one (UUIDv4) if the client did not send it. See `ingestor/specs/api.md`.
- It is a pass-through identifier: once assigned, it sticks to everything done downstream on behalf of the original request and is never replaced or regenerated.
  - Every stage copies it unchanged from the incoming message into every message it publishes, including failure messages.
  - Every outgoing call a service makes while handling the request or message carries it where the protocol allows, in the HTTP header `X-Correlation-ID` for HTTP calls. This applies to all systems, internal and external alike (for example MinIO, LLM provider APIs, a future self-hosted model), so the ID can show up in their debug logs. Systems that ignore the header are not a problem.
- Purpose: debugging and tracing. It lets one request be followed through all services and logs.
- Expected use: one `correlation_id` per request, unique among all requests within one day. Clients are strongly recommended to generate a new UUID for every request (or omit it and let the ingestor generate one).
- Uniqueness is not enforced. If a client accidentally reuses a value, the pipeline processes the documents normally; only tracing becomes ambiguous (log searches return entries for several requests, which `document_id` tells apart).
- `correlation_id` is not a grouping key (for example, for test runs) and not a document identifier; `document_id` identifies a single document.
- A message on queues 1 to 3 without a valid `correlation_id` is malformed and goes to the DLQ.

### Logging rule

Every service includes `correlation_id` in every log entry written while handling a request or a message, as a field named `correlation_id`. Once `document_id` is known, it is included too (field `document_id`). This covers errors, retries and rejected requests.

## Failures

Decision record: `docs/decisions/0011-failure-handling.md`.

- Errors are permanent (retrying cannot help, for example a corrupt PDF) or transient (a dependency is temporarily unavailable). Each module's spec classifies its errors.
- Permanent errors fail the document at once.
- Transient errors are retried in-process: the consumer waits and retries within the same delivery, then fails the document after the last attempt. Attempts and waits are set per module in its spec.
- Failing a document means publishing a failure message to `documents.failed`, then acking the original message.
- If RabbitMQ itself is unavailable (publish or ack fails), the consumer does not ack; the broker redelivers the message later.
- Because of at-least-once delivery, the same document can produce more than one failure message (for example, when the failure is published but the ack is lost). Consumers of `documents.failed` must tolerate duplicates. A `duplicate_document` failure from `integrator` can arrive for a document whose row is stored and correct (a redelivered message after a successful write).

Failure message (JSON):

| Field | Type | Description |
|---|---|---|
| `schema_version` | int | `1` |
| `stage` | string | Module that gave up |
| `document_id` | string | |
| `correlation_id` | string (UUID) | Copied from the failed message |
| `error_code` | string | Short machine-readable code |
| `error_message` | string | Human-readable details |
| `created_at` | string (RFC 3339) | |

The tester finds the file to inspect via `documents/{document_id}/original.pdf`, plus any intermediate objects that exist, and the stage's diagnostics file `{stage}-failures/{document_id}.json` if that stage writes one.

### Error codes

- Each module lists its error codes in its own spec: `text-extractor/specs/overview.md`, `fields-extractor/specs/overview.md`, `integrator/specs/overview.md` (Errors).
- Codes are `snake_case`, stable once published, and unique within a module.
- Generic codes have the same meaning in every module: `storage_unavailable` (object storage failing after retries), `internal_error` (unexpected exception).
- A failure is identified by the pair (`stage`, `error_code`).

## Extracted JSON schema

Owned by `fields-extractor` (see `fields-extractor/specs/extraction-schema.md`). Stored as `extracted.json`, consumed by `integrator`.
