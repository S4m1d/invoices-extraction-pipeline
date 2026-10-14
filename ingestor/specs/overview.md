# Ingestor: Overview

## Purpose

Entry point of the pipeline. A simple REST service that accepts invoice documents and puts them on the queue.

## Stack

Go.

## Responsibilities

- Accept documents through a `POST` request.
- Assign a `document_id`.
- Take the client's `correlation_id` or generate one (see `/specs/pipeline-contracts.md`).
- Store the original file in object storage.
- Publish a message to the `text-extractor` queue (see `/specs/pipeline-contracts.md`).

## Non-goals

- No parsing or validation of document content.
- No OCR.

## Scope (MVP)

Clean, text-based PDFs only.

## Interfaces

- HTTP API: see [`api.md`](api.md).
- Queue output: see `/specs/pipeline-contracts.md`.

## Open questions

None. Resolved:

- Request restrictions: see [`api.md`](api.md#request-restrictions).
- The API is synchronous only (`202` with `document_id` and `correlation_id`); there is no status endpoint.
