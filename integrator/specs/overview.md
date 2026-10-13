# Integrator: Overview

## Purpose

Consumes extracted invoice JSON and writes it into the procurement tables. Other tables may be added later.

## Stack

Go, PostgreSQL.

## Responsibilities

- Consume messages from the `documents.fields_extracted` queue and read `extracted.json` from object storage.
- Validate the JSON against the extraction schema.
- Insert the invoice and its lines in one transaction.

## Interfaces

- Input: see `/specs/pipeline-contracts.md` and `fields-extractor/specs/extraction-schema.md`.
- Database: see [`data-model.md`](data-model.md).

## Open questions

- OPEN: idempotency (same `document_id` delivered twice).
- OPEN: migrations tooling.
