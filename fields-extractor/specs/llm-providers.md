# Fields Extractor: LLM Providers

The LLM provider must be abstracted behind an interface so that a locally running LLM can be used later.

## Provider status

| Provider | Status |
|---|---|
| Anthropic (Python SDK) | Implemented (MVP) |
| OpenAI | Stub: selecting it returns an explicit "not implemented" error |
| Local LLM | Stub: selecting it returns an explicit "not implemented" error |

## Requirements

- Provider is selected by configuration (e.g. environment variable).
- Selecting a stubbed provider must fail clearly (at startup or at first call), not silently fall back.
- The interface accepts markdown + target Pydantic schema and returns a validated object.

## Open questions

- OPEN: configuration keys, model name, timeouts.
- OPEN: how API keys are supplied in Docker.
