from decimal import Decimal

import pytest

from fields_extractor import config

REQUIRED = {
    "AMQP_URL": "amqp://guest:guest@localhost:5672/",
    "MINIO_ENDPOINT": "localhost:9000",
    "MINIO_ACCESS_KEY": "key",
    "MINIO_SECRET_KEY": "secret",
    "ANTHROPIC_API_KEY": "sk-test",
}


def test_defaults():
    c = config.load(dict(REQUIRED))
    assert (c.http_host, c.http_port, c.log_level) == ("", 8080, "info")
    assert (c.llm_provider, c.llm_model, c.llm_effort, c.llm_max_retries) == ("anthropic", "claude-haiku-5-5", "low", 4)
    assert (c.llm_limit_check_interval, c.llm_limit_max_checks) == (120.0, 3)
    assert c.llm_daily_budget_usd == Decimal("10")
    assert (c.llm_price_input, c.llm_price_output) == (None, None)


def test_overrides():
    c = config.load(
        {
            **REQUIRED,
            "FIELDS_EXTRACTOR_HTTP_ADDR": ":9000",
            "LLM_EFFORT": "medium",
            "LLM_MAX_RETRIES": "2",
            "LLM_LIMIT_CHECK_INTERVAL": "30",
            "LLM_LIMIT_MAX_CHECKS": "5",
            "LLM_DAILY_BUDGET_USD": "2.5",
            "LLM_PRICE_INPUT_PER_MTOK": "0.2",
            "LLM_PRICE_OUTPUT_PER_MTOK": "1",
        }
    )
    assert (c.http_port, c.llm_effort, c.llm_max_retries) == (9000, "medium", 2)
    assert (c.llm_limit_check_interval, c.llm_limit_max_checks, c.llm_daily_budget_usd) == (30.0, 5, Decimal("2.5"))
    assert (c.llm_price_input, c.llm_price_output) == (Decimal("0.2"), Decimal("1"))


def test_reports_all_problems():
    with pytest.raises(config.ConfigError) as exc:
        config.load(
            {
                "LLM_EFFORT": "huge",
                "LLM_MAX_RETRIES": "-1",
                "LLM_LIMIT_CHECK_INTERVAL": "0",
                "LLM_LIMIT_MAX_CHECKS": "0",
                "LLM_DAILY_BUDGET_USD": "abc",
                "LLM_PROVIDER": "x",
            }
        )
    msg = str(exc.value)
    for name in [
        "AMQP_URL",
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "LLM_EFFORT",
        "LLM_MAX_RETRIES",
        "LLM_LIMIT_CHECK_INTERVAL",
        "LLM_LIMIT_MAX_CHECKS",
        "LLM_DAILY_BUDGET_USD",
        "LLM_PROVIDER",
    ]:
        assert name in msg


@pytest.mark.parametrize("budget", ["0", "-1", "NaN", "inf"])
def test_budget_must_be_positive(budget):
    with pytest.raises(config.ConfigError, match="LLM_DAILY_BUDGET_USD"):
        config.load({**REQUIRED, "LLM_DAILY_BUDGET_USD": budget})


def test_price_override_needs_both():
    with pytest.raises(config.ConfigError, match="set both or neither"):
        config.load({**REQUIRED, "LLM_PRICE_INPUT_PER_MTOK": "1"})


def test_api_key_required_for_anthropic():
    env = dict(REQUIRED)
    del env["ANTHROPIC_API_KEY"]
    with pytest.raises(config.ConfigError, match="ANTHROPIC_API_KEY"):
        config.load(env)


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-5-5",
        "claude-fable-5-1",
        "claude-sonnet-5-5",
        "claude-haiku-4-5",
        "claude-mythos-5-1",
        "something-new",
    ],
)
def test_only_haiku_5_5_allowed(model):
    with pytest.raises(config.ConfigError) as exc:
        config.load({**REQUIRED, "LLM_MODEL": model})
    assert str(exc.value) == (
        f"LLM_MODEL {model!r} is not allowed: only claude-haiku-5-5 may be used (cost restriction). Other models "
        "must be added to the allowed list explicitly, see fields-extractor/specs/llm-providers.md"
    )


def test_price_override_does_not_lift_restriction():
    with pytest.raises(config.ConfigError, match="not allowed"):
        config.load(
            {
                **REQUIRED,
                "LLM_MODEL": "claude-opus-5-5",
                "LLM_PRICE_INPUT_PER_MTOK": "0.1",
                "LLM_PRICE_OUTPUT_PER_MTOK": "0.5",
            }
        )
