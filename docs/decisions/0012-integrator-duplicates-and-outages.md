# 0012. Integrator: duplicates fail, database outages pause

- Status: accepted
- Date: 2026-10-15

## Context

The contract requires idempotent consumers (at-least-once delivery), and ADR 0011 retries transient errors in-process and then fails the document. The integrator is the last stage and writes to PostgreSQL, owned by it.

## Decision

- **Duplicates fail.** A `document_id` is written once. A second write fails with `duplicate_document` and leaves the stored row unchanged, so conflicting re-extractions are visible instead of silently replacing data. This is an exception to the contract's idempotency rule.
- **Database outages pause.** When PostgreSQL is unreachable, the integrator returns the message to the queue, stops consuming, reports `postgres` failing on `/healthz`, checks the database periodically and resumes with the returned message. No document fails because of an outage, and there is no stop after a number of checks. This replaces ADR 0011's retry-then-fail rule for database unavailability in this module only.
- **The input is trusted.** The integrator does not validate `extracted.json` again; `fields-extractor` validated it, and database constraints catch what reaches the tables.

## Consequences

- A redelivered message after a successful write (lost ack, or a duplicate publish upstream) produces a `duplicate_document` failure for a document whose row is correct. The tester must handle this case (`/specs/pipeline-contracts.md`, Failures).
- A database restart delays documents instead of failing them.
- Schema errors in `extracted.json` surface as database errors (`database_rejected`) rather than field-level validation messages.
