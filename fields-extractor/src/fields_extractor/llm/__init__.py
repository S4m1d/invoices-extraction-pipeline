"""LLM provider abstraction (see fields-extractor/specs/llm-providers.md)."""

from dataclasses import dataclass, field
from importlib import resources
from typing import Protocol

PROMPT_VERSION = "v4"


def load_prompt(version: str = PROMPT_VERSION) -> str:
    return resources.files("fields_extractor.prompts").joinpath(f"extract-{version}.md").read_text("utf-8")


@dataclass
class Attempt:
    """One call to the model: what it returned, before the module's validation."""

    output: str
    stop_reason: str | None
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    refusal_category: str | None = None
    validation_errors: list[str] = field(default_factory=list)


class LlmUnavailable(Exception):
    """Transient errors after retries, or the per-document deadline passed."""


class LlmBadRequest(Exception):
    """The API rejected this request (400, 413)."""


class LlmMisconfigured(Exception):
    """Authentication, permission or model errors: affects every document."""


class LlmPaused(Exception):
    """Not a document failure: the worker returns the message and pauses (spec: Usage limits)."""

    code = ""


class LlmUsageLimit(LlmPaused):
    """The LLM API reports a usage limit (spend cap, spend limit, credit)."""

    code = "llm_usage_limit"


class LlmDailyBudget(LlmPaused):
    """The module's own daily budget is used up."""

    code = "llm_daily_budget_reached"


class Provider(Protocol):
    model: str

    def extract(self, markdown: str, previous: Attempt | None = None, *, deadline: float) -> Attempt:
        """Run one extraction attempt.

        previous is the failed attempt to correct (its output and validation_errors are sent
        back to the model). deadline is a time.monotonic() value no retry may start after.
        Raises LlmUnavailable, LlmBadRequest, LlmMisconfigured or LlmUsageLimit.
        """

    def check_available(self) -> bool:
        """Minimal request: True if the API accepts requests, False while a usage limit holds.

        Raises LlmMisconfigured on configuration errors; other errors count as unavailable.
        """


class NotImplementedProvider(Exception):
    pass


def make_provider(name: str, **kwargs) -> Provider:
    """kwargs: model, effort, max_retries, on_usage."""
    """Build the configured provider. Stubs fail here, at startup (spec: Stubs)."""
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(**kwargs)
    if name in ("openai", "local"):
        raise NotImplementedProvider(f"LLM provider {name!r} is not implemented (only 'anthropic' is)")
    raise NotImplementedProvider(f"unknown LLM provider {name!r}")
