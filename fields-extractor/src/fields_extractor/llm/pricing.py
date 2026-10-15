"""Token prices for the daily budget (see fields-extractor/specs/llm-providers.md, Prices)."""

from dataclasses import dataclass
from decimal import Decimal

_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Rate:
    input: Decimal  # US dollars per million tokens
    output: Decimal


# Allowed models only (config.ALLOWED_MODELS).
# model -> list of (max prompt tokens or None, rate); first matching row wins.
_TABLE: dict[str, list[tuple[int | None, Rate]]] = {
    "claude-haiku-5-5": [
        (100_000, Rate(Decimal("0.10"), Decimal("0.50"))),
        (None, Rate(Decimal("0.50"), Decimal("2.50"))),
    ],
}


class UnknownPrice(Exception):
    pass


class Pricing:
    def __init__(self, model: str, override: Rate | None = None):
        if override is None and model not in _TABLE:
            raise UnknownPrice(
                f"no price for model {model!r}: set LLM_PRICE_INPUT_PER_MTOK and LLM_PRICE_OUTPUT_PER_MTOK"
            )
        self.model = model
        self.override = override

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        """US dollars for one request. `model` is the one the API reports (may differ in form)."""
        rate = self.override or self._rate(model, input_tokens)
        return (Decimal(input_tokens) * rate.input + Decimal(output_tokens) * rate.output) / _MILLION

    def _rate(self, model: str, input_tokens: int) -> Rate:
        rows = _TABLE.get(model) or _TABLE[self.model]
        for max_prompt, rate in rows:
            if max_prompt is None or input_tokens <= max_prompt:
                return rate
        raise AssertionError("price table rows must end with an open row")
