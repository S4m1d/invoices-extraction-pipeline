# 0011. Failure handling: in-process retries, dead-lettering by policy, per-stage failure buckets

- Status: accepted
- Date: 2026-10-14

## Context

ADR 0009 introduced `documents.failed` and per-queue DLQs but left open the retry mechanism, the error code list and how DLQs are wired. `text-extractor` is the first consumer and needs these settled. It also introduces documents that a human should inspect (unreadable PDFs, PDFs without a text layer).

## Decision

- **Retries**: errors are permanent or transient. Permanent errors fail the document at once. Transient errors are retried in-process (wait and retry within the same delivery); attempts and waits are set per module. Delayed retry queues were rejected for now as extra infra and code that one-message-per-process workers do not need.
- **Error codes**: listed in each module's spec; `snake_case`, unique within a module; generic codes (`storage_unavailable`, `internal_error`) mean the same everywhere; a failure is identified by (`stage`, `error_code`).
- **Dead-lettering**: stage queues are declared without arguments; `infra/` applies a broker policy that dead-letters them to exchange `dlx`, where each `<queue>.dlq` is bound with routing key `<queue>`. Queue arguments were rejected because they are fixed at declaration and conflicting declarations fail with `PRECONDITION_FAILED`.
- **Failure diagnostics**: each stage that fails documents for reasons a human should inspect writes a diagnostics file to its own bucket `{stage}-failures`, key `{document_id}.json`, before publishing the failure message.

## Consequences

- Workers block while waiting between retries; acceptable at one message per process and short waits. Long waits (for example LLM rate limits) may need a new ADR for retry queues.
- If the policy is missing, rejected messages are dropped silently; `infra/` must apply it.
- Consumers of `documents.failed` must tolerate duplicate failure messages.
- `infra/` creates one extra bucket per stage that writes diagnostics.
