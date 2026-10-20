# Fields Extractor: LLM Providers

The LLM provider is abstracted behind an interface so that a locally running LLM can be used later (ADR 0004).

## Provider status

| Provider | `LLM_PROVIDER` | Status |
|---|---|---|
| Anthropic (Python SDK) | `anthropic` | Implemented (MVP) |
| OpenAI | `openai` | Stub |
| Local LLM | `local` | Stub |

## Interface

```python
class Provider(Protocol):
    def extract(self, markdown: str, feedback: list[str] | None = None) -> Attempt: ...
```

- Input: the `layout.md` content; on the retry, the validation errors of the previous attempt.
- Output: an `Attempt`: raw output text, stop reason, refusal category, token usage, model. Validation into the Pydantic model (`extraction-schema.md`) is done by the module, not the provider, so every provider is validated the same way.
- Errors: the provider raises the module's error types (`llm_unavailable`, `llm_bad_request`, `llm_misconfigured`; see `overview.md`), never SDK-specific exceptions. Refusals and truncation are reported through the stop reason.
- Prompt and output schema are owned by the module and passed to the provider, so every provider extracts the same thing.

## Stubs

Selecting `openai` or `local` fails at startup with a configuration error naming the provider as not implemented. The service does not start, so no message is consumed.

## Anthropic provider

- SDK: `anthropic` (Python, 1.x), streaming request with structured outputs (`output_config.format`, JSON schema generated from the Pydantic model). Content blocks are read by type: a response may start with `thinking` blocks.
- Model: `LLM_MODEL`, default `claude-haiku-5-5`.
- Allowed models: only `claude-haiku-5-5`, to keep LLM cost low. Any other value (Sonnet, Opus, Fable, Mythos, older Haiku versions, unknown names) is a configuration error: the service does not start and logs why, for example `LLM_MODEL 'claude-opus-5-5' is not allowed: only claude-haiku-5-5 may be used (cost restriction). Other models must be added to the allowed list explicitly, see fields-extractor/specs/llm-providers.md`. Allowing another model is a deliberate change: add it to the allowed list and the price table, in the spec and the code.
- Thinking: adaptive (model default); depth set by `LLM_EFFORT` (`output_config.effort`), default `low`. No sampling parameters (`temperature`, `top_p`, `top_k`): Claude Haiku 5.5 rejects non-default values.
- `max_tokens`: 64000 (the largest run-001 invoice needs about 15k output tokens, plus thinking).
- Request timeout: 5 min. Retries for 408, 409, 429, 5xx (including 529 overloaded), timeouts and connection errors, honoring `retry-after` (else exponential backoff, at most 60 s): `LLM_MAX_RETRIES`, default 4. The module does the retries with the SDK's own retries turned off, so that no retry starts after the per-document deadline.
- Per-document deadline: all LLM work for one document (both attempts including retries) must finish within 20 min, else `llm_unavailable`. This keeps every message well under RabbitMQ's default consumer acknowledgement timeout (30 min).
- Status mapping:

| Response | Result |
|---|---|
| 429 with `error.details.error_code` = `enforced_spend_limit_reached` (organization tier's monthly spend cap; no `retry-after`) | `llm_usage_limit` |
| 400 `invalid_request_error` whose message starts with `You have reached your specified API usage limits` or `You have reached your specified workspace API usage limits` (spend limit set on the organization or workspace) | `llm_usage_limit` |
| Any status whose message contains `credit balance is too low`, or 402 / `billing_error` (no credit left, billing problem) | `llm_usage_limit` |
| 401, 403, 404 (key, permission, unknown model) | `llm_misconfigured` |
| 400, 413 and other 4xx not listed here (request rejected) | `llm_bad_request` |
| 408, 409, 429, 5xx, timeout, connection error after retries | `llm_unavailable` |
| `stop_reason: "refusal"` | `llm_refused` (Claude Haiku 5.5 has no server-side refusal fallback) |
| `stop_reason: "max_tokens"` | `llm_output_truncated` |

- Usage limit responses are documented at https://platform.claude.com/docs/en/api/rate-limits (spend caps and spend limits) and https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers (credit balance). The organization/workspace spend limit has no error code, so it is matched on the documented message prefix; the status of the credit-balance error is not documented, so it is matched on its message on any status. These rules are checked before the retry rules: usage limit responses are never retried.
- Usage limit check (`check_available`, see `overview.md`): a non-streaming request with a one-sentence prompt and `max_tokens` 16 on the configured model. A usage-limit or transient error means the limit still holds; 401/403/404 means `llm_misconfigured`; a success or any other error ends the pause.
- `X-Correlation-ID` is sent on every request (`/specs/pipeline-contracts.md`).
- Prompt caching: not used. The prompt prefix (instructions + schema) is below the minimum cacheable size.
- Cost: run-001 (200 invoices, about 0.5 M input and 0.6 M output tokens) is estimated at well under $1 per full run with `claude-haiku-5-5` (prompts stay under 100K tokens, so the lower rate card applies).

## Prices

Used for the daily budget (`overview.md`). US dollars per million tokens, for the allowed models. Source: https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-10-15).

| Model | Input | Output |
|---|---|---|
| `claude-haiku-5-5`, prompt up to 100K tokens | 0.10 | 0.50 |
| `claude-haiku-5-5`, prompt above 100K tokens | 0.50 | 2.50 |

`LLM_PRICE_INPUT_PER_MTOK` and `LLM_PRICE_OUTPUT_PER_MTOK` replace the table (flat prices), for example after a price change; they do not make other models allowed. When prices change, the table is updated.

## Configuration and API keys

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_PROVIDER` | no | `anthropic` | `anthropic`, `openai`, `local` |
| `LLM_MODEL` | no | `claude-haiku-5-5` | Model ID; only `claude-haiku-5-5` is allowed (see Allowed models) |
| `LLM_EFFORT` | no | `low` | `low`, `medium`, `high`, `xhigh`, `max` |
| `LLM_MAX_RETRIES` | no | `4` | Retries for transient errors |
| `LLM_LIMIT_CHECK_INTERVAL` | no | `120` | Seconds between checks while paused on a usage limit or the daily budget (see `overview.md`) |
| `LLM_LIMIT_MAX_CHECKS` | no | `3` | Failed checks in a row after which the worker stops |
| `LLM_DAILY_BUDGET_USD` | no | `10` | LLM spend allowed per UTC day, in US dollars (see `overview.md`, Daily budget) |
| `LLM_PRICE_INPUT_PER_MTOK`, `LLM_PRICE_OUTPUT_PER_MTOK` | no | | US dollars per million tokens; override the price table (both or neither) |
| `ANTHROPIC_API_KEY` | yes, for `anthropic` | | API key |

The API key reaches the container as an environment variable from a gitignored `.env` file next to the compose file (`env_file:`); `.env.example` with empty values is tracked. The key is never logged.
