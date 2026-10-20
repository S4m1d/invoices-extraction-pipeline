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

Line-sum check (retry, never fail): if `subtotal` is printed and the line `amount`s do not add up to it (true for all 200 run-001 invoices), the same feedback retry is used, with the message "the line amounts sum to X but the printed subtotal is Y; check for totals-area rows (discount, shipping, tax, deposit) listed as line items and for missing line items". This check never fails a document:

- If the second output passes, it is used.
- If the second output is valid but still does not add up, it is used with a warning.
- If the second output is invalid, the first output (valid, but not adding up) is used with a warning.
- There is at most one retry per document: when the first output failed validation, the second is not retried again for the line sum.

In 4 sample runs, a totals-area row (`Shipping & Handling`, `Discount`) was listed as a line in 3 of them, despite the prompt rule; this check catches it.

Plausibility checks that only log a warning (`check` field) and set object metadata `checks-failed`:

- Sum of line `amount`s equals `subtotal` (after the retry above).
- `subtotal` + `tax_total` equals `grand_total` (does not hold when discounts, shipping or deposits apply, since `grand_total` is the amount due).

## Errors

| Error code | Cause | Retried | Diagnostics file |
|---|---|---|---|
| `object_not_found` | `layout.md` does not exist | no | no |
| `storage_unavailable` | MinIO failing (as in `text-extractor`: 3 attempts, 1 s / 5 s, 10 s per call) | yes | no |
| `llm_unavailable` | Rate limits, 5xx, timeouts, connection errors after retries, or the 20 min per-document deadline passed | yes (`LLM_MAX_RETRIES`) | no |
| `llm_bad_request` | The API rejected the request (400, 413, other 4xx) | no | yes |
| `llm_refused` | `stop_reason` is `refusal` | no | yes |
| `llm_output_truncated` | `stop_reason` is `max_tokens` | no | yes |
| `llm_invalid_output` | Output fails validation twice | once, with feedback | yes |
| `internal_error` | Unexpected exception | no | no |

`llm_misconfigured` (401, 403, 404: authentication failed, permission denied, unknown model) is not a document failure: the problem affects every document. The worker stops consuming and does not ack (the message returns to the queue), logs one error, and stays running with `/healthz` reporting `llm` as failing, so `docker ps` shows the container as unhealthy. There is no restart loop and no further API calls. After fixing the configuration, the container is restarted.

### Usage limits: pause, check, stop

Two kinds of limit pause the worker instead of failing documents. Neither is retried as a request.

| Code | Cause | Check |
|---|---|---|
| `llm_usage_limit` | The LLM API reports a usage limit: the organization tier's spend cap, a spend limit set on the organization or workspace, or no credit left (exact responses in [`llm-providers.md`](llm-providers.md)) | Minimal API request (a few input tokens, small `max_tokens`); passes when it is accepted |
| `llm_daily_budget_reached` | The module's own daily budget is used up (see Daily budget) | Recompute the day's spend; passes when it is below the budget (for example after 00:00 UTC). No API call |

Behaviour, the same for both:

1. The message is returned to the queue (`basic.nack` with requeue), so it keeps its place at the front, and the worker stops consuming. Work already done for the document (for example a first attempt before the feedback retry) is discarded.
2. `/healthz` reports `llm` as failing, with the code and reason in its `errors` object (`/specs/pipeline-contracts.md`, Health endpoint); one `ERROR` log entry is written.
3. Every `LLM_LIMIT_CHECK_INTERVAL` seconds (default `120`) the check runs; failed checks are logged at `DEBUG`.
4. If a check passes, an `INFO` entry is logged, `/healthz` is healthy again and consuming resumes. The returned message is delivered first, so processing continues with the document that hit the limit.
5. If `LLM_LIMIT_MAX_CHECKS` checks (default `3`) fail in a row, the worker stops for good: no more checks or API calls, the message stays first in the queue, the process keeps running with `/healthz` failing (`stopped after 3 checks`), one `ERROR` entry is logged. A person raises the limit (in the Console, or `LLM_DAILY_BUDGET_USD`) and restarts the container.

If an API check returns a configuration error (401, 403, 404), the worker switches to `llm_misconfigured` handling. Each replica pauses, checks and stops on its own.

### Daily budget

The module limits its own LLM spend per UTC day: `LLM_DAILY_BUDGET_USD`, default `10`.

- Cost of a request = input tokens × input price + output tokens × output price, from the token usage the API returns (output includes thinking tokens, which are billed as output). Every request counts: extraction attempts, feedback retries and usage-limit checks. Prices: [`llm-providers.md`](llm-providers.md) (Prices).
- Before each extraction attempt, the day's spend is compared with the budget; if it is at or above the budget, the worker pauses with `llm_daily_budget_reached`. An attempt that has started is finished, so the budget can be exceeded by at most one request per replica.
- Spend is stored in bucket `fields-extractor-usage`, one object per replica and UTC day: `{YYYY-MM-DD}/{instance}.json`, where `instance` is the container hostname. A replica writes only its own object, so replicas never overwrite each other; the day's spend is the sum of that day's objects. A restarted container continues its own object (same hostname) or starts a new one; earlier objects still count.

```json
{ "date": "2026-10-15", "instance": "3f2a9c1b7d40", "spent_usd": "3.417200", "input_tokens": 812345, "output_tokens": 5120044, "requests": 431 }
```

- The counter is written after every request. If the write fails (MinIO unavailable after retries), the amount is kept in memory and added on the next successful write, and an `ERROR` entry is logged. If the day's spend cannot be read before an attempt, the document fails with `storage_unavailable`.

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

- The prompt lives in the module as a versioned file, `prompts/extract-v{N}.md`; the version in use is a code constant. A changed prompt is a new file, so results can be traced to the exact prompt. Earlier versions stay in the repository.
- Versions:

| Version | Change |
|---|---|
| `v1` | Initial prompt |
| `v2` | `grand_total` is the amount due after deposits; totals-area rows are not lines; on grouped (telecom) invoices each usage record is a line, group subtotals are not. Based on a 10-invoice run-001 sample with v1. |
| `v3` (in use) | `quantity` is the number without its unit (telecom prints `214.6 megabytes`); with v2 this failed validation on every telecom invoice and cost a feedback retry. |
- The prompt contains the extraction rules from `extraction-schema.md` (top-level lines only, no item codes in descriptions, decimal format, currency inference including USD vs CAD, `null` for missing optional fields) and says that the input is layout text, one fenced block per page.
- The model and prompt version are logged per document and stored as object metadata on `extracted.json`.

## Health, logging, configuration

- `/healthz` as in `text-extractor` (`rabbitmq`, `minio` with buckets `invoices`, `fields-extractor-failures` and `fields-extractor-usage`), plus `llm`: failing after `llm_misconfigured`, while paused on `llm_usage_limit` or `llm_daily_budget_reached`, and after the worker stopped on them. A failing health check is logged when the set of failing dependencies changes, not on every probe, so a long pause does not flood the log. No API call is made by the health check (it would cost money every 10 s).
- Logs as in `text-extractor`; the per-message summary adds `model`, `prompt_version`, `input_tokens`, `output_tokens`, `lines`, `attempts`. The document content and LLM output are not logged (they are in the diagnostics file when needed).
- Configuration: `AMQP_URL`, `MINIO_*`, `FIELDS_EXTRACTOR_HTTP_ADDR` (default `:8080`), `LOG_LEVEL`, plus the LLM variables in `llm-providers.md` (including `LLM_LIMIT_CHECK_INTERVAL`, `LLM_LIMIT_MAX_CHECKS`, `LLM_DAILY_BUDGET_USD`).

## Code sharing

The queue, storage, health and logging code is copied from `text-extractor` and adapted, not extracted into a shared library. Each module stays independently buildable and deployable; a shared package can be extracted later if a third Python module appears.

## Open questions

None. Resolved:

- Validation failure behaviour: one retry with feedback, then `llm_invalid_output` (see Validation).
- Prompt storage and versioning: versioned prompt files (see Prompt).
