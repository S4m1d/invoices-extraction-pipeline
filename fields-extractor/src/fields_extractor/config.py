"""Configuration from the environment (see fields-extractor/specs/overview.md and llm-providers.md).

Adapted from text-extractor."""

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_LEVELS = {"debug", "info", "warn", "warning", "error"}
_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
_PROVIDERS = {"anthropic", "openai", "local"}

# Cost restriction (spec: llm-providers.md, Allowed models). Adding a model is a deliberate change:
# allowed list and price table, in the spec and in llm/pricing.py.
ALLOWED_MODELS = ("claude-haiku-5-5",)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    amqp_url: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_use_ssl: bool
    http_host: str
    http_port: int
    log_level: str
    llm_provider: str
    llm_model: str
    llm_effort: str
    llm_max_retries: int
    llm_limit_check_interval: float
    llm_limit_max_checks: int
    llm_daily_budget_usd: Decimal
    llm_price_input: Decimal | None
    llm_price_output: Decimal | None


def load(env: dict[str, str] | None = None) -> Config:
    """Read the configuration, reporting all missing or invalid variables at once."""
    env = os.environ if env is None else env
    missing, invalid = [], []

    def required(name: str) -> str:
        v = env.get(name, "")
        if not v:
            missing.append(name)
        return v

    def optional(name: str, default: str) -> str:
        return env.get(name) or default

    ssl = optional("MINIO_USE_SSL", "false").lower()
    if ssl not in ("true", "false", "1", "0"):
        invalid.append("MINIO_USE_SSL")

    host, port = "", 0
    addr = optional("FIELDS_EXTRACTOR_HTTP_ADDR", ":8080")
    h, sep, p = addr.rpartition(":")
    if sep and p.isdigit() and 0 < int(p) < 65536:
        host, port = h, int(p)
    else:
        invalid.append("FIELDS_EXTRACTOR_HTTP_ADDR")

    level = optional("LOG_LEVEL", "info").lower()
    if level not in _LEVELS:
        invalid.append("LOG_LEVEL")

    provider = optional("LLM_PROVIDER", "anthropic").lower()
    if provider not in _PROVIDERS:
        invalid.append("LLM_PROVIDER")
    effort = optional("LLM_EFFORT", "low").lower()
    if effort not in _EFFORTS:
        invalid.append("LLM_EFFORT")
    retries = optional("LLM_MAX_RETRIES", "4")
    if not retries.isdigit():
        invalid.append("LLM_MAX_RETRIES")
    interval = optional("LLM_LIMIT_CHECK_INTERVAL", "120")
    try:
        interval_s = float(interval)
        if interval_s <= 0:
            raise ValueError
    except ValueError:
        invalid.append("LLM_LIMIT_CHECK_INTERVAL")
        interval_s = 0.0
    max_checks = optional("LLM_LIMIT_MAX_CHECKS", "3")
    if not max_checks.isdigit() or int(max_checks) < 1:
        invalid.append("LLM_LIMIT_MAX_CHECKS")

    def positive_decimal(name: str, value: str | None) -> Decimal | None:
        if value is None:
            return None
        try:
            d = Decimal(value)
            if not d.is_finite() or d <= 0:
                raise InvalidOperation
            return d
        except InvalidOperation:
            invalid.append(name)
            return None

    budget = positive_decimal("LLM_DAILY_BUDGET_USD", optional("LLM_DAILY_BUDGET_USD", "10"))
    price_in = positive_decimal("LLM_PRICE_INPUT_PER_MTOK", env.get("LLM_PRICE_INPUT_PER_MTOK") or None)
    price_out = positive_decimal("LLM_PRICE_OUTPUT_PER_MTOK", env.get("LLM_PRICE_OUTPUT_PER_MTOK") or None)
    if bool(env.get("LLM_PRICE_INPUT_PER_MTOK")) != bool(env.get("LLM_PRICE_OUTPUT_PER_MTOK")):
        invalid.append("LLM_PRICE_INPUT_PER_MTOK/LLM_PRICE_OUTPUT_PER_MTOK (set both or neither)")
    if provider == "anthropic" and not (env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN")):
        missing.append("ANTHROPIC_API_KEY")

    cfg = Config(
        amqp_url=required("AMQP_URL"),
        minio_endpoint=required("MINIO_ENDPOINT"),
        minio_access_key=required("MINIO_ACCESS_KEY"),
        minio_secret_key=required("MINIO_SECRET_KEY"),
        minio_use_ssl=ssl in ("true", "1"),
        http_host=host,
        http_port=port,
        log_level="warning" if level == "warn" else level,
        llm_provider=provider,
        llm_model=optional("LLM_MODEL", "claude-haiku-5-5"),
        llm_effort=effort,
        llm_max_retries=int(retries) if retries.isdigit() else 0,
        llm_limit_check_interval=interval_s,
        llm_limit_max_checks=int(max_checks) if max_checks.isdigit() else 0,
        llm_daily_budget_usd=budget or Decimal(0),
        llm_price_input=price_in,
        llm_price_output=price_out,
    )
    if cfg.llm_provider == "anthropic" and cfg.llm_model not in ALLOWED_MODELS:
        raise ConfigError(
            f"LLM_MODEL {cfg.llm_model!r} is not allowed: only {', '.join(ALLOWED_MODELS)} may be used "
            "(cost restriction). Other models must be added to the allowed list explicitly, "
            "see fields-extractor/specs/llm-providers.md"
        )
    if missing or invalid:
        raise ConfigError(f"config: missing [{', '.join(missing)}], invalid [{', '.join(invalid)}]")
    return cfg
