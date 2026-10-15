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
- Thinking: adaptive (model default); depth set by `LLM_EFFORT` (`output_config.effort`), default `low`. No sampling parameters (`temperature`, `top_p`, `top_k`): Claude Haiku 5.5 rejects non-default values.
- `max_tokens`: 64000 (the largest run-001 invoice needs about 15k output tokens, plus thinking).
- Request timeout: 5 min. SDK retries for 429, 5xx, timeouts and connection errors, honoring `retry-after`: `LLM_MAX_RETRIES`, default 4.
- Per-document deadline: all LLM work for one document (both attempts including retries) must finish within 20 min, else `llm_unavailable`. This keeps every message well under RabbitMQ's default consumer acknowledgement timeout (30 min).
- Status mapping:

| Response | Result |
|---|---|
| 401, 403, 404 (key, permission, unknown model) | `llm_misconfigured` |
| 400, 413 (request rejected) | `llm_bad_request` |
| 429, 5xx, timeout, connection error after SDK retries | `llm_unavailable` |
| `stop_reason: "refusal"` | `llm_refused` (Claude Haiku 5.5 has no server-side refusal fallback) |
| `stop_reason: "max_tokens"` | `llm_output_truncated` |

- `X-Correlation-ID` is sent on every request (`/specs/pipeline-contracts.md`).
- Prompt caching: not used. The prompt prefix (instructions + schema) is below the minimum cacheable size.
- Cost: run-001 (200 invoices, about 0.5 M input and 0.6 M output tokens) is estimated at well under $1 per full run with `claude-haiku-5-5` (prompts stay under 100K tokens, so the lower rate card applies). `LLM_MODEL` can be switched to `claude-sonnet-5-5` or `claude-opus-5-5` for comparison.

## Configuration and API keys

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_PROVIDER` | no | `anthropic` | `anthropic`, `openai`, `local` |
| `LLM_MODEL` | no | `claude-haiku-5-5` | Model ID |
| `LLM_EFFORT` | no | `low` | `low`, `medium`, `high`, `xhigh`, `max` |
| `LLM_MAX_RETRIES` | no | `4` | SDK retries for transient errors |
| `ANTHROPIC_API_KEY` | yes, for `anthropic` | | API key |

The API key reaches the container as an environment variable from a gitignored `.env` file next to the compose file (`env_file:`); `.env.example` with empty values is tracked. The key is never logged.
