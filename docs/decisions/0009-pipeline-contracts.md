# 0009. Pipeline contracts: queues, envelope, failures

- Status: accepted
- Date: 2026-10-13

## Context

Services communicate only via RabbitMQ and MinIO (ADR 0001, 0002). Queue topology, message format, JSON transport and failure surfacing were open.

## Decision

- Direct durable queues on the default exchange, one per stage: `documents.ingested`, `documents.text_extracted`, `documents.fields_extracted`. Persistent messages, manual ack, at-least-once.
- Extracted JSON is stored in MinIO as `extracted.json`; the message carries the key.
- Message envelope: `schema_version`, `stage`, `document_id`, `correlation_id`, `object_key`, `created_at`.
- Object keys: `documents/{document_id}/{original.pdf,layout.md,extracted.json}`.
- Failures: retry up to a limit, then publish to `documents.failed` and ack; unparseable or rejected messages go to `<queue>.dlq`.

## Consequences

- Consumers must be idempotent (duplicates are possible).
- The tester consumes `documents.failed` to mark documents failed without waiting for timeouts, and can open the original file by key.
- Extracted results are kept for every document, including those that later fail in the integrator.
- Retry limit, backoff and error codes are still open.
