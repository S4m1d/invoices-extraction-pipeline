# Ingestor: Overview

## Purpose

Entry point of the pipeline. A simple REST service that accepts invoice documents and puts them on the queue.

## Stack

Go.

## Responsibilities

- Accept documents through a `POST` request.
- Assign a `document_id`.
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

- OPEN: file validation rules (MIME type, max size).
- OPEN: is the response synchronous only (accepted + `document_id`) or is there a status endpoint?
