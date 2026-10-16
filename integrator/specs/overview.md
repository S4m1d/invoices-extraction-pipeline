# Integrator: Overview

## Purpose

Consumes extracted invoice JSON and writes it into the procurement tables. Other tables may be added later.

## Stack

Go, PostgreSQL.

Go (same version and structure as `ingestor`), `pgx` v5 (`pgxpool`) for PostgreSQL, `amqp091-go`, `minio-go`, `log/slog`; queue/storage/correlation code copied from `ingestor` and adapted (as `fields-extractor` did from `text-extractor`).

## Responsibilities

- Consume messages from the `documents.fields_extracted` queue and read `extracted.json` from object storage.
- Validate the JSON against the extraction schema.
- Insert the invoice and its lines in one transaction.
- Report documents it gives up on to `documents.failed`.

## Interfaces

- Input: see `/specs/pipeline-contracts.md` and `fields-extractor/specs/extraction-schema.md`.
- Database: see [`data-model.md`](data-model.md).
- Output: none. `integrator` is the last stage; the tester finds results in PostgreSQL by `document_id`.

## Processing

For each message:

1. Validate the envelope: `stage` = `fields-extractor`, `object_key` = `documents/{document_id}/extracted.json`, other fields as in the contract. Invalid: reject without requeue (dead-lettered).
2. Read `extracted.json` (storage retries as in the other modules: 3 attempts, 1 s / 5 s, 10 s per call).
3. Decode it into the invoice and line structure. The content is not validated again: `fields-extractor` has validated it, and the database constraints catch what reaches the tables (decision record: `docs/decisions/0012-integrator-duplicates-and-outages.md`).
4. Write the invoice and its lines in one transaction, with `extraction_model` and `prompt_version` from the object metadata of `extracted.json` (see `data-model.md`), unless the `document_id` already exists (see Idempotency).
5. Ack.

One message at a time per process (prefetch 1).

## Idempotency

A `document_id` is written once. If it already exists in `invoices`, the document fails with `duplicate_document` and the stored row is kept unchanged; nothing is written. The check is the unique constraint on `document_id` itself (the insert runs first in the transaction), so two replicas writing the same document at the same moment also produce exactly one row and one `duplicate_document` failure.

This is a deliberate exception to the contract's rule that consumers are idempotent (`docs/decisions/0012-integrator-duplicates-and-outages.md`). Known consequence: when a message is redelivered after a successful write (for example the ack was lost when the RabbitMQ connection dropped, or `fields-extractor` published twice), the redelivery produces a `duplicate_document` failure for a document whose row is correct. The same invoice ingested again as a new document (new `document_id`, for example a repeated test run) is a separate row; invoice numbers are not unique.

## Errors

| Error code | Cause | Retried |
|---|---|---|
| `object_not_found` | `extracted.json` does not exist | no |
| `storage_unavailable` | MinIO failing | yes (3 attempts, 1 s / 5 s) |
| `invalid_extraction` | `extracted.json` cannot be decoded (not JSON, wrong types, missing fields) | no |
| `duplicate_document` | The `document_id` is already in `invoices` (see Idempotency) | no |
| `database_rejected` | PostgreSQL rejects the data (constraint violation, invalid decimal or date) | no |
| `internal_error` | Unexpected exception | no |

Failing a document: publish to `documents.failed` and ack (contract). No diagnostics bucket: the object to inspect (`extracted.json`) is already in storage, and the error message names the failing field or constraint.

### Database unavailable

PostgreSQL being unreachable (connection refused, timeouts, too many connections, serialization or deadlock errors after a retry) affects every document, like RabbitMQ being down. Unlike ADR 0011's retry-then-fail rule (exception recorded in ADR 0012), the worker does not fail documents for it: it returns the message to the queue (`basic.nack` with requeue), stops consuming, `/healthz` reports `postgres` as failing, and it checks the database every `INTEGRATOR_DB_CHECK_INTERVAL` seconds (default `10`). When the database answers, consuming resumes with the returned message. There is no stop after N checks: a database outage is an infrastructure problem that resolves without a human changing configuration.

Serialization failures and deadlocks are retried once in place before they count as unavailability.

## Schema migrations

- The integrator owns the database schema (contract: PostgreSQL is owned by `integrator`).
- Migrations are numbered SQL files in `integrator/src/migrations/` (`0001_create_invoices.sql`, ...), embedded in the binary with `go:embed` and applied at startup with `pressly/goose`, holding a PostgreSQL advisory lock so that several replicas do not migrate at the same time. Up migrations only.
- If migration fails, the service does not start.
- A migration grants `SELECT` on both tables to role `tester` if that role exists (created by `infra/`, see `data-model.md`, Access); without it the grant is skipped and logged.

## Health, logging, configuration

- `/healthz`: `rabbitmq`, `minio` (bucket `invoices`), `postgres` (`SELECT 1` with a 3 s timeout). Health failures are logged when the set of failing dependencies changes.
- Logs: JSON (`log/slog`) with `correlation_id` and `document_id` on every entry about a message; one summary entry per message (`result`, `lines`, `duration_ms`).
- `X-Correlation-ID` on MinIO requests (contract). PostgreSQL has no per-request header; the correlation ID is in the logs and in the `invoices` row (see `data-model.md`).

| Variable | Required | Default | Description |
|---|---|---|---|
| `AMQP_URL` | yes | | RabbitMQ URL |
| `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | yes | | |
| `MINIO_USE_SSL` | no | `false` | |
| `DATABASE_URL` | yes | | PostgreSQL URL, e.g. `postgres://integrator:...@postgres:5432/invoices` |
| `INTEGRATOR_HTTP_ADDR` | no | `:8080` | Health endpoint |
| `INTEGRATOR_DB_CHECK_INTERVAL` | no | `10` | Seconds between database checks while paused |
| `LOG_LEVEL` | no | `info` | |

## Open questions

None. Resolved:

- Idempotency: a `document_id` is written once; duplicates fail (see Idempotency).
- Migrations tooling: embedded SQL files applied with goose at startup (see Schema migrations).
