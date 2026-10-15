# Fields Extractor: Overview

## Purpose

Takes markdown with extracted layout and text and extracts concrete pieces of information into a JSON schema, then passes the JSON to the next pipeline stage (`integrator`).

## Stack

Python, Pydantic, LLM via a provider abstraction (see [`llm-providers.md`](llm-providers.md)).

Python 3.13, `uv`, `anthropic` SDK 1.x, Pydantic v2, `pika`, `minio`, `pytest`; same service structure as `text-extractor` (worker, storage, health, JSON logging), with its code copied rather than shared (see Code sharing).

## Responsibilities

- Consume messages from the `documents.text_extracted` queue.
- Read markdown from object storage.
- Call the configured LLM provider and obtain structured output.
- Validate output with Pydantic against the extraction schema.
- Write the validated JSON to object storage (`extracted.json`) and publish a message for `integrator`.
- Report documents it gives up on to `documents.failed`, with diagnostics for outputs a human should look at.

## Interfaces

- Input: queue message referencing markdown (see `/specs/pipeline-contracts.md`).
- Output: extracted JSON, see [`extraction-schema.md`](extraction-schema.md).
- LLM providers: see [`llm-providers.md`](llm-providers.md).

## Processing

For each message:

1. Validate the envelope: as in `text-extractor`, with `stage` = `text-extractor` and `object_key` = `documents/{document_id}/layout.md`. Invalid: reject without requeue (dead-lettered).
2. Read `layout.md`.
3. Call the provider (prompt + schema), then validate (see Validation).
4. Write `documents/{document_id}/extracted.json` (`application/json`), with object metadata `model`, `prompt-version`, `input-tokens`, `output-tokens`.
5. Publish the envelope to `documents.fields_extracted` (`stage` = `fields-extractor`, `object_key` = the extracted key), persistent, mandatory, with publisher confirms.
6. Ack.

One message at a time per process (prefetch 1). Reprocessing overwrites `extracted.json`.

## Validation

Structured outputs guarantee the JSON shape. On top of that, the Pydantic model checks:

- Required fields present and non-empty.
- Decimal strings parse as decimals; dates are valid calendar dates.
- `currency` is 3 uppercase letters.
- `lines` has at least one item and `line_no` runs 1..N in order.

If validation fails, the provider is called once more with the same input plus the validation errors ("your previous output failed these checks: ..."). If the second output also fails, the document fails with `llm_invalid_output`.

Plausibility checks that do not fail the document, only log a warning (`check` field) and set object metadata `checks-failed`:

- Sum of line `amount`s equals `subtotal` (holds for all 200 run-001 invoices).
- `subtotal` + `tax_total` equals `grand_total` (does not hold when discounts, shipping or deposits apply).

## Errors

| Error code | Cause | Retried | Diagnostics file |
|---|---|---|---|
| `object_not_found` | `layout.md` does not exist | no | no |
| `storage_unavailable` | MinIO failing (as in `text-extractor`: 3 attempts, 1 s / 5 s, 10 s per call) | yes | no |
| `llm_unavailable` | Rate limits, 5xx, timeouts, connection errors after the SDK's retries, or the 20 min per-document deadline passed | by the SDK | no |
| `llm_bad_request` | The API rejected the request (400, 413) | no | yes |
| `llm_refused` | `stop_reason` is `refusal` | no | yes |
| `llm_output_truncated` | `stop_reason` is `max_tokens` | no | yes |
| `llm_invalid_output` | Output fails validation twice | once, with feedback | yes |
| `internal_error` | Unexpected exception | no | no |

`llm_misconfigured` (401, 403, 404: authentication failed, permission denied, unknown model) is not a document failure: the problem affects every document. The worker stops consuming and does not ack (the message returns to the queue), logs one error, and stays running with `/healthz` reporting `llm` as failing, so `docker ps` shows the container as unhealthy. There is no restart loop and no further API calls. After fixing the configuration, the container is restarted.

Provider details (timeouts, retries, status mapping): [`llm-providers.md`](llm-providers.md).

### Diagnostics file

`fields-extractor-failures/{document_id}.json` (contract: failure buckets) for `llm_refused`, `llm_output_truncated`, `llm_invalid_output` and `llm_bad_request`:

```json
{
  "schema_version": 1,
  "document_id": "<uuid>",
  "correlation_id": "<uuid>",
  "error_code": "llm_invalid_output",
  "error_message": "...",
  "input": { "bucket": "invoices", "key": "documents/<uuid>/layout.md", "bytes": 7074 },
  "model": "claude-haiku-5-5",
  "prompt_version": "v1",
  "attempts": [
    { "stop_reason": "end_turn", "refusal_category": null, "output": "<raw model text>", "validation_errors": ["..."], "input_tokens": 0, "output_tokens": 0 }
  ],
  "created_at": "2026-10-15T12:00:00Z"
}
```

## Prompt

- The prompt lives in the module as a versioned file, `prompts/extract-v1.md`; the version in use is a code constant. A changed prompt is a new file (`extract-v2.md`), so results can be traced to the exact prompt.
- The prompt contains the extraction rules from `extraction-schema.md` (top-level lines only, no item codes in descriptions, decimal format, currency inference including USD vs CAD, `null` for missing optional fields) and says that the input is layout text, one fenced block per page.
- The model and prompt version are logged per document and stored as object metadata on `extracted.json`.

## Health, logging, configuration

- `/healthz` as in `text-extractor` (`rabbitmq`, `minio` with buckets `invoices` and `fields-extractor-failures`), plus `llm`: failing after `llm_misconfigured`. No API call is made by the health check (it would cost money every 10 s).
- Logs as in `text-extractor`; the per-message summary adds `model`, `prompt_version`, `input_tokens`, `output_tokens`, `lines`, `attempts`. The document content and LLM output are not logged (they are in the diagnostics file when needed).
- Configuration: `AMQP_URL`, `MINIO_*`, `FIELDS_EXTRACTOR_HTTP_ADDR` (default `:8080`), `LOG_LEVEL`, plus the LLM variables in `llm-providers.md`.

## Code sharing

The queue, storage, health and logging code is copied from `text-extractor` and adapted, not extracted into a shared library. Each module stays independently buildable and deployable; a shared package can be extracted later if a third Python module appears.

## Open questions

None. Resolved:

- Validation failure behaviour: one retry with feedback, then `llm_invalid_output` (see Validation).
- Prompt storage and versioning: versioned prompt files (see Prompt).
