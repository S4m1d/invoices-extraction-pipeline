# Text Extractor: Overview

## Purpose

Takes an invoice PDF, understands its layout and extracts text while preserving the layout, producing a markdown file.

This module combines two logical pipeline steps, layout analysis and text extraction (ADR 0003).

## Stack

Python, pdfplumber.

Python 3.13, dependencies managed with `uv`; `pika` (RabbitMQ), `minio` (object storage), standard `logging` with a JSON formatter; `pytest` for tests.

## Responsibilities

- Consume messages from `documents.ingested`.
- Read `original.pdf` from object storage.
- Produce layout-preserving markdown (see [`output-format.md`](output-format.md)).
- Write `layout.md` to object storage and publish a message to `documents.text_extracted`.
- Report documents it gives up on to `documents.failed`, and write diagnostics for PDFs a human should look at.

## Internal design

For the MVP (clean text PDFs) both steps are done with pdfplumber: per page, watermark characters are filtered out and the page text is extracted in layout mode.

When scan processing is added, the module becomes an internal sub-pipeline (e.g. OCR -> layout analysis -> text assembly). The module's external contract (PDF in, markdown out) must not change.

## Bounds (MVP)

Clean, text-based PDFs only. No OCR.

## Processing

For each message:

1. Parse and validate the envelope (see Input validation). Invalid: reject without requeue, so it goes to `documents.ingested.dlq`.
2. Read `object_key` from object storage.
3. Open the PDF and convert it (see [`output-format.md`](output-format.md)).
4. Write `documents/{document_id}/layout.md`.
5. Publish the envelope to `documents.text_extracted` (persistent, mandatory, publisher confirms), with `stage` = `text-extractor`, `object_key` = the layout key, and `document_id` and `correlation_id` copied unchanged.
6. Ack the input message.

Reprocessing the same message (redelivery) overwrites `layout.md` and publishes again; downstream consumers are idempotent (see `/specs/pipeline-contracts.md`).

### Input validation

The message is JSON with `schema_version` = `1`, `stage` = `ingestor`, a UUID `document_id`, a UUID `correlation_id`, `object_key` = `documents/{document_id}/original.pdf`, and an RFC 3339 `created_at`. Anything else is invalid.

### Errors

Decision record for the general rules: `docs/decisions/0011-failure-handling.md`.

| Error code | Cause | Retried | Diagnostics file |
|---|---|---|---|
| `object_not_found` | `original.pdf` does not exist | no | no |
| `pdf_unreadable` | Not a PDF, corrupt, or encrypted | no | yes |
| `no_text_layer` | No page contains any text after watermark removal (scanned or image-only PDF) | no | yes |
| `storage_unavailable` | MinIO errors or timeouts on read or write | yes | no |
| `internal_error` | Unexpected exception | no | no |

- Permanent errors (not retried) publish a failure message to `documents.failed` at once and ack the input.
- The `no_text_layer` check counts characters left after watermark removal (see `output-format.md`), so a scanned PDF with a text watermark is still detected. Pages without text in an otherwise text-based PDF are not an error.
- `storage_unavailable` is retried in-process: 3 attempts in total, waiting 1 s and then 5 s between them. After the last attempt the document is failed with that code.
- If RabbitMQ itself fails (publish or ack), the process does not ack; it reconnects, and the broker redelivers the message.
- Invalid messages are rejected without requeue (dead-lettered, see `/specs/pipeline-contracts.md`); no failure message is published, since the message cannot be trusted to identify a document.

### Diagnostics file

For PDF reading problems (`pdf_unreadable`, `no_text_layer`) a person should look at the PDF. Before publishing the failure message, the module writes `text-extractor-failures/{document_id}.json` (bucket `text-extractor-failures`, see `/specs/pipeline-contracts.md`):

```json
{
  "schema_version": 1,
  "document_id": "<uuid>",
  "correlation_id": "<uuid>",
  "error_code": "no_text_layer",
  "error_message": "no page contains text",
  "original": { "bucket": "invoices", "key": "documents/<uuid>/original.pdf", "bytes": 124290 },
  "page_count": 3,
  "chars_per_page": [0, 0, 0],
  "images_per_page": [1, 1, 1],
  "pdf_metadata": { "Producer": "...", "Creator": "..." },
  "created_at": "2026-10-14T12:00:00Z"
}
```

- `page_count`, `chars_per_page`, `images_per_page` and `pdf_metadata` are `null` when the PDF cannot be opened (`pdf_unreadable`).
- `chars_per_page` counts characters after watermark removal.
- `pdf_metadata` is the PDF's document information dictionary as strings; empty object if there is none.
- If writing the diagnostics file fails, it is retried like `storage_unavailable`; if it still fails, the document is failed with `storage_unavailable`.

### Concurrency

One message at a time per process (prefetch 1). Throughput is scaled by running more containers.

## Health

HTTP `GET /healthz` on `TEXT_EXTRACTOR_HTTP_ADDR` (default `:8080`), same shape as the ingestor's: `200 {"status":"ok"}` when the RabbitMQ connection is open and the buckets `invoices` and `text-extractor-failures` exist, else `503 {"status":"unavailable","failing":[...]}`. Used by the Docker healthcheck.

## Logging and correlation

As required by `/specs/pipeline-contracts.md`: JSON log lines on stdout; every entry about a message has `correlation_id` and `document_id`; MinIO requests carry `X-Correlation-ID`; published messages carry it in the AMQP `correlation_id` property and the `X-Correlation-ID` header. Each processed message gets one summary entry (`document_id`, pages, output bytes, duration, result).

pdfminer's own warnings (for example `Could not get FontBBox`, emitted for most run-001 documents) are suppressed.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `AMQP_URL` | yes | | RabbitMQ URL |
| `MINIO_ENDPOINT` | yes | | `host:port` |
| `MINIO_ACCESS_KEY` | yes | | |
| `MINIO_SECRET_KEY` | yes | | |
| `MINIO_USE_SSL` | no | `false` | |
| `TEXT_EXTRACTOR_HTTP_ADDR` | no | `:8080` | Health endpoint address |
| `LOG_LEVEL` | no | `info` | |

## Interfaces

- Input: queue message referencing a PDF (see `/specs/pipeline-contracts.md`).
- Output: markdown object + queue message (see `/specs/pipeline-contracts.md`).
- Markdown format: see [`output-format.md`](output-format.md).

## Open questions

None. Resolved:

- Non-text PDFs: fail with `no_text_layer` and write a diagnostics file (see Errors).
- Multi-page invoices: all pages in order, one section per page (see `output-format.md`).
