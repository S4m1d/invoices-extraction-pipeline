# 0002. Documents travel through MinIO object storage

- Status: accepted
- Date: 2026-10-13

## Context

PDFs and extracted markdown must move between services connected by RabbitMQ. Options: shared volume, object storage, bytes inside messages.

## Decision

Use object storage (MinIO, S3-compatible). Queue messages carry object keys and IDs, not file bytes.

## Consequences

- One more infrastructure container.
- Closer to a production setup; no shared filesystem coupling between services.
- Small queue messages.
- The testing client can fetch the source file of a failed document by key.
