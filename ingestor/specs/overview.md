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
- No authentication or authorization in the MVP: the API is reachable only inside the Docker network.

## Scope (MVP)

Clean, text-based PDFs only.

## Interfaces

- HTTP API: see [`api.md`](api.md).
- Queue output: see `/specs/pipeline-contracts.md`.

## Behaviour

- The ingestor does not create the bucket or queues; `infra/` provisions them. A missing queue or bucket makes `/healthz` and `POST /documents` return `503`.
- Upload flow: validate the request, write `documents/{document_id}/original.pdf` (content type `application/pdf`), then publish the envelope to `documents.ingested` (persistent, mandatory, with publisher confirms). `202` is returned only after the broker confirms. If publishing fails after the object is written, the response is `503` and the object stays in storage (orphan; no cleanup in MVP).
- Storage write and publish each time out after 10 s.
- Published messages also carry the correlation ID in the AMQP `correlation_id` property and the `X-Correlation-ID` message header (the body field stays the source of truth).
- Logs are JSON lines on stdout (Go `log/slog`), one access entry per request plus entries for errors.

## Configuration

Environment variables:

| Variable | Required | Default | Description |
|---|---|---|---|
| `INGESTOR_HTTP_ADDR` | no | `:8080` | Listen address |
| `AMQP_URL` | yes | | RabbitMQ URL, e.g. `amqp://guest:guest@rabbitmq:5672/` |
| `MINIO_ENDPOINT` | yes | | `host:port` |
| `MINIO_ACCESS_KEY` | yes | | |
| `MINIO_SECRET_KEY` | yes | | |
| `MINIO_USE_SSL` | no | `false` | |
| `LOG_LEVEL` | no | `info` | `debug`, `info`, `warn`, `error` |
| `INGESTOR_MAX_CONCURRENT_UPLOADS` | no | `20` | Upload concurrency limit, at least `1`; see `api.md` |

## Open questions

None. Resolved:

- Request restrictions: see [`api.md`](api.md#request-restrictions).
- The API is synchronous only (`202` with `document_id` and `correlation_id`); there is no status endpoint.
