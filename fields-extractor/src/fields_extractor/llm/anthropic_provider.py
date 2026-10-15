"""Anthropic provider (see fields-extractor/specs/llm-providers.md, Anthropic provider)."""

import logging
import random
import time
from collections.abc import Callable

import anthropic

from .. import context
from ..schema import OUTPUT_SCHEMA
from . import Attempt, LlmBadRequest, LlmMisconfigured, LlmUnavailable, LlmUsageLimit, load_prompt

log = logging.getLogger(__name__)

MAX_TOKENS = 64000
REQUEST_TIMEOUT = 300.0  # 5 min
MAX_BACKOFF = 60.0

# Classified by status code, not exception class: e.g. 529 (OverloadedError) is not an
# InternalServerError subclass but must be retried like any 5xx.
_MISCONFIGURED_STATUS = {401, 403, 404}
_TRANSIENT_STATUS = {408, 409, 429}


# Usage limits (spec: llm-providers.md, status mapping; https://platform.claude.com/docs/en/api/rate-limits).
_SPEND_CAP_CODE = "enforced_spend_limit_reached"  # 429, error.details.error_code: tier spend cap
_SPEND_LIMIT_PREFIXES = (  # 400 invalid_request_error: a limit set on the organization or workspace
    "You have reached your specified API usage limits",
    "You have reached your specified workspace API usage limits",
)
_NO_CREDIT = "credit balance is too low"  # status not documented; matched on any status


def _usage_limit(e: Exception) -> str | None:
    """The reason if e says a usage limit is reached, else None."""
    status = getattr(e, "status_code", None)
    body = getattr(e, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    message = str(error.get("message") or "")
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    if status == 429 and details.get("error_code") == _SPEND_CAP_CODE:
        return "organization tier spend cap reached"
    if status == 400 and error.get("type") == "invalid_request_error" and message.startswith(_SPEND_LIMIT_PREFIXES):
        return "spend limit reached"
    if _NO_CREDIT in message.lower():
        return "credit balance too low"
    if status == 402 or error.get("type") == "billing_error":
        return "billing problem"
    return None


def _is_transient(e: Exception) -> bool:
    if isinstance(e, anthropic.APIConnectionError):  # includes APITimeoutError
        return True
    status = getattr(e, "status_code", None)
    return status is not None and (status in _TRANSIENT_STATUS or status >= 500)


class AnthropicProvider:
    def __init__(
        self,
        model: str,
        effort: str,
        max_retries: int,
        client: anthropic.Anthropic | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        on_usage: Callable[[str, int, int], None] | None = None,
    ):
        self.model = model
        self.effort = effort
        self.max_retries = max_retries
        # SDK retries off: retries are done here so they respect the per-document deadline.
        self.client = client or anthropic.Anthropic(max_retries=0, timeout=REQUEST_TIMEOUT)
        self.system = load_prompt()
        self.sleep = sleep
        self.clock = clock
        self.on_usage = on_usage  # (model, input_tokens, output_tokens) for every request (daily budget)

    def extract(self, markdown: str, previous: Attempt | None = None, *, deadline: float) -> Attempt:
        messages = [{"role": "user", "content": f"<invoice>\n{markdown}\n</invoice>"}]
        if previous is not None:
            messages += [
                {"role": "assistant", "content": previous.output or "(empty response)"},
                {
                    "role": "user",
                    "content": "Your previous output failed these checks:\n"
                    + "\n".join(f"- {e}" for e in previous.validation_errors)
                    + "\nReturn the corrected JSON for the whole invoice.",
                },
            ]

        for retry in range(self.max_retries + 1):
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise LlmUnavailable("per-document deadline passed")
            try:
                return self._call(messages, timeout=min(REQUEST_TIMEOUT, remaining))
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                if reason := _usage_limit(e):
                    raise LlmUsageLimit(f"{reason}: {_describe(e)}") from e
                if not _is_transient(e):
                    status = e.status_code
                    error = LlmMisconfigured if status in _MISCONFIGURED_STATUS else LlmBadRequest
                    raise error(f"{type(e).__name__} ({status}): {e.message}") from e
                if retry == self.max_retries:
                    raise LlmUnavailable(f"{_describe(e)} after {retry + 1} attempts") from e
                wait = _backoff(e, retry)
                if self.clock() + wait >= deadline:
                    raise LlmUnavailable(f"{_describe(e)}; per-document deadline reached") from e
                log.warning(
                    "llm unavailable, retrying",
                    extra={"retry": retry + 1, "wait_s": wait, "error": _describe(e)},
                )
                self.sleep(wait)
        raise AssertionError("unreachable")

    def check_available(self) -> bool:
        """Minimal request to see whether a usage limit still holds (spec: Usage limit reached)."""
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=16,
                messages=[{"role": "user", "content": "Reply with OK."}],
                output_config={"effort": "low"},
                timeout=30.0,
            )
            self._report(msg)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            if _usage_limit(e) or _is_transient(e):
                return False
            if e.status_code in _MISCONFIGURED_STATUS:
                raise LlmMisconfigured(f"{type(e).__name__} ({e.status_code}): {e.message}") from e
            # Any other rejection is not the usage limit: resume, and let processing report it.
            log.warning("usage limit check got an unexpected error, resuming", extra={"error": _describe(e)})
        return True

    def _call(self, messages: list, timeout: float) -> Attempt:
        with self.client.messages.stream(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=self.system,
            messages=messages,
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            extra_headers={context.HEADER: context.correlation_id.get()} if context.correlation_id.get() else None,
            timeout=timeout,
        ) as stream:
            msg = stream.get_final_message()
        self._report(msg)
        text = "".join(b.text for b in msg.content if b.type == "text")
        details = getattr(msg, "stop_details", None)
        return Attempt(
            output=text,
            stop_reason=msg.stop_reason,
            model=msg.model,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            refusal_category=getattr(details, "category", None) if details else None,
        )

    def _report(self, msg) -> None:
        if self.on_usage is not None:
            self.on_usage(msg.model, msg.usage.input_tokens, msg.usage.output_tokens)


def _backoff(e: Exception, retry: int) -> float:
    """retry-after when the API sends it, else exponential backoff with jitter."""
    response = getattr(e, "response", None)
    if response is not None:
        value = response.headers.get("retry-after")
        try:
            if value is not None:
                return min(max(float(value), 0.0), MAX_BACKOFF)
        except ValueError:
            pass
    return min(2.0**retry + random.uniform(0, 1), MAX_BACKOFF)


def _describe(e: Exception) -> str:
    status = getattr(e, "status_code", None)
    return (f"{type(e).__name__}" + (f" ({status})" if status else "") + f": {e}")[:300]
