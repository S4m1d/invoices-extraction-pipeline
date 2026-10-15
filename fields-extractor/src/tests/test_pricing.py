from decimal import Decimal

import pytest

from fields_extractor.llm.pricing import Pricing, Rate, UnknownPrice


def test_haiku_lower_rate_card_up_to_100k():
    p = Pricing("claude-haiku-5-5")
    assert p.cost("claude-haiku-5-5", 100_000, 0) == Decimal("0.01")  # exactly 100K: still 0.10/MTok
    assert p.cost("claude-haiku-5-5", 10_000, 2_000) == Decimal("0.002")


def test_haiku_higher_rate_card_above_100k():
    assert Pricing("claude-haiku-5-5").cost("claude-haiku-5-5", 100_001, 1_000) == Decimal(
        "0.0525005"
    )  # 100001 x 0.50 + 1000 x 2.50, per million


def test_reported_model_variant_falls_back_to_configured():
    assert Pricing("claude-haiku-5-5").cost("claude-haiku-5-5-20261001", 10_000, 0) == Decimal("0.001")


def test_override():
    p = Pricing("claude-haiku-5-5", Rate(Decimal("1"), Decimal("2")))
    assert p.cost("claude-haiku-5-5", 1_000_000, 1_000_000) == Decimal("3")


def test_unknown_model_without_override():
    with pytest.raises(UnknownPrice):
        Pricing("claude-opus-5-5")


def test_cost_is_exact_decimal():
    c = Pricing("claude-haiku-5-5").cost("claude-haiku-5-5", 1, 1)
    assert isinstance(c, Decimal) and c == Decimal("0.0000006")
