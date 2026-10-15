"""Processing of one input message (see fields-extractor/specs/overview.md, Processing)."""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from . import context, envelope
from .llm import PROMPT_VERSION, Attempt, LlmBadRequest, LlmMisconfigured, LlmPaused, LlmUnavailable, Provider
from .schema import Extraction, InvalidOutput, line_sum_mismatch, plausibility, validate
from .storage import BUCKET, ObjectNotFound, StorageUnavailable

log = logging.getLogger(__name__)

# Transient storage errors: 3 attempts, waiting 1 s and then 5 s.
RETRY_WAITS = (1.0, 5.0)
# All LLM work for one document must finish within this (spec: llm-providers.md).
LLM_DEADLINE = 20 * 60.0


class Outcome(Enum):
    ACK = "ack"
    REJECT = "reject"  # without requeue: dead-lettered


class Storage(Protocol):
    def get_text(self, key: str) -> str: ...
    def put_extracted(self, key: str, data: dict, metadata: dict[str, str]) -> None: ...
    def put_diagnostics(self, key: str, diagnostics: dict) -> None: ...


class Budget(Protocol):
    def ensure_available(self) -> None:
        """Raise LlmDailyBudget when today's budget is used up; StorageUnavailable if unreadable."""


class Publisher(Protocol):
    def publish(self, queue: str, body: bytes, correlation_id: str) -> None:
        """Publish persistently and wait for the broker's confirm. Raises on failure."""


class _Failed(Exception):
    """A document failure with an error code; attempts are written to the diagnostics file."""

    def __init__(self, code: str, message: str, attempts: list[Attempt] | None = None):
        super().__init__(message)
        self.code = code
        self.attempts = attempts


class Processor:
    def __init__(
        self,
        storage: Storage,
        publisher: Publisher,
        provider: Provider,
        budget: Budget | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = time.monotonic,
    ):
        self.storage = storage
        self.publisher = publisher
        self.provider = provider
        self.budget = budget
        self.sleep = sleep
        self.now = now
        self.clock = clock

    def handle(self, body: bytes) -> Outcome:
        """Process one message.

        RabbitMQ errors, LlmMisconfigured and LlmPaused propagate: the caller must not ack then.
        """
        try:
            env = envelope.parse(body)
        except envelope.InvalidMessage as e:
            log.warning("invalid message, rejecting to DLQ", extra={"error": str(e)})
            return Outcome.REJECT

        with context.ids(env.correlation_id, env.document_id):
            start = time.monotonic()
            result = self._process(env)
            log.info(
                "message processed",
                extra={**result, "duration_ms": round((time.monotonic() - start) * 1000)},
            )
            return Outcome.ACK

    def _process(self, env: envelope.Envelope) -> dict:
        markdown = ""
        try:
            markdown = self._retry(lambda: self.storage.get_text(env.object_key))
            extraction, attempts = self._extract(markdown)
            checks = plausibility(extraction)
            for check in checks:
                log.warning("plausibility check failed", extra={"check": check})
            metadata = {
                "model": attempts[-1].model,
                "prompt-version": PROMPT_VERSION,
                "input-tokens": str(sum(a.input_tokens for a in attempts)),
                "output-tokens": str(sum(a.output_tokens for a in attempts)),
            }
            if checks:
                metadata["checks-failed"] = ",".join(checks)
            key = envelope.extracted_key(env.document_id)
            data = extraction.model_dump(mode="json")
            self._retry(lambda: self.storage.put_extracted(key, data, metadata))
        except ObjectNotFound as e:
            return self._fail(env, "object_not_found", str(e))
        except StorageUnavailable as e:
            return self._fail(env, "storage_unavailable", str(e))
        except _Failed as e:
            return self._fail(env, e.code, str(e), e.attempts, markdown)
        except LlmUnavailable as e:
            return self._fail(env, "llm_unavailable", str(e))
        except LlmBadRequest as e:
            return self._fail(env, "llm_bad_request", str(e), [], markdown)
        except (LlmMisconfigured, LlmPaused):
            raise  # not document failures: the worker stops or pauses (spec: Errors)
        except Exception as e:
            log.exception("unexpected error")
            return self._fail(env, "internal_error", f"{type(e).__name__}: {e}")

        self.publisher.publish(envelope.OUTPUT_QUEUE, envelope.output(env, self.now()), env.correlation_id)
        return {
            "result": "ok",
            "model": metadata["model"],
            "prompt_version": PROMPT_VERSION,
            "input_tokens": int(metadata["input-tokens"]),
            "output_tokens": int(metadata["output-tokens"]),
            "lines": len(extraction.lines),
            "attempts": len(attempts),
        }

    def _extract(self, markdown: str) -> tuple[Extraction, list[Attempt]]:
        """Call the model; one feedback retry for invalid output or lines that don't add up (spec: Validation)."""
        deadline = self.clock() + LLM_DEADLINE
        attempts: list[Attempt] = []
        fallback: Extraction | None = None  # valid output whose lines don't add up
        previous = None
        for _ in range(2):
            if self.budget is not None:
                self._retry(self.budget.ensure_available)  # spec: Daily budget, before each attempt
            attempt = self.provider.extract(markdown, previous, deadline=deadline)
            attempts.append(attempt)
            if attempt.stop_reason == "refusal":
                raise _Failed("llm_refused", f"model refused (category: {attempt.refusal_category})", attempts)
            if attempt.stop_reason == "max_tokens":
                raise _Failed("llm_output_truncated", "output hit max_tokens", attempts)
            try:
                extraction = validate(attempt.output)
            except InvalidOutput as e:
                attempt.validation_errors = e.errors
                log.warning("invalid model output", extra={"attempt": len(attempts), "errors": e.errors[:10]})
                if fallback is not None:
                    return fallback, attempts  # line-sum retry produced invalid output: keep the first
                previous = attempt
                continue
            mismatch = line_sum_mismatch(extraction)
            if mismatch is None or len(attempts) == 2:
                return extraction, attempts
            attempt.validation_errors = [mismatch]
            log.warning("lines do not add up to subtotal, retrying", extra={"attempt": 1, "errors": [mismatch]})
            fallback, previous = extraction, attempt
        raise _Failed(
            "llm_invalid_output",
            f"output failed validation twice: {'; '.join(attempts[-1].validation_errors)[:500]}",
            attempts,
        )

    def _fail(
        self,
        env: envelope.Envelope,
        code: str,
        message: str,
        attempts: list[Attempt] | None = None,
        markdown: str = "",
    ) -> dict:
        if attempts is not None:
            diagnostics = {
                "schema_version": 1,
                "document_id": env.document_id,
                "correlation_id": env.correlation_id,
                "error_code": code,
                "error_message": message,
                "input": {"bucket": BUCKET, "key": env.object_key, "bytes": len(markdown.encode())},
                "model": attempts[-1].model if attempts else self.provider.model,
                "prompt_version": PROMPT_VERSION,
                "attempts": [
                    {
                        "stop_reason": a.stop_reason,
                        "refusal_category": a.refusal_category,
                        "output": a.output,
                        "validation_errors": a.validation_errors,
                        "input_tokens": a.input_tokens,
                        "output_tokens": a.output_tokens,
                    }
                    for a in attempts
                ],
                "created_at": envelope.timestamp(self.now()),
            }
            try:
                self._retry(lambda: self.storage.put_diagnostics(f"{env.document_id}.json", diagnostics))
            except StorageUnavailable as e:
                log.error("cannot write diagnostics file", extra={"error_code": code, "error": str(e)})
                code, message = "storage_unavailable", f"writing diagnostics for {code}: {e}"

        log.warning("document failed", extra={"error_code": code, "error": message})
        self.publisher.publish(
            envelope.FAILED_QUEUE, envelope.failure(env, code, message, self.now()), env.correlation_id
        )
        return {"result": "failed", "error_code": code}

    def _retry(self, op):
        for attempt, wait in enumerate((*RETRY_WAITS, None), 1):
            try:
                return op()
            except StorageUnavailable as e:
                if wait is None:
                    raise
                log.warning(
                    "storage unavailable, retrying",
                    extra={"attempt": attempt, "wait_s": wait, "error": str(e)},
                )
                self.sleep(wait)
