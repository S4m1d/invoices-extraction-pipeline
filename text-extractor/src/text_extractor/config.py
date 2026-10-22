"""Configuration from the environment (see text-extractor/specs/overview.md)."""

import os
import re
from dataclasses import dataclass

_LEVELS = {"debug", "info", "warn", "warning", "error"}


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
    ocr_lang: str
    ocr_page_timeout: float  # seconds


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
    addr = optional("TEXT_EXTRACTOR_HTTP_ADDR", ":8080")
    h, sep, p = addr.rpartition(":")
    if sep and p.isdigit() and 0 < int(p) < 65536:
        host, port = h, int(p)
    else:
        invalid.append("TEXT_EXTRACTOR_HTTP_ADDR")

    level = optional("LOG_LEVEL", "info").lower()
    if level not in _LEVELS:
        invalid.append("LOG_LEVEL")

    ocr_lang = optional("TEXT_EXTRACTOR_OCR_LANG", "eng")
    if not re.fullmatch(r"\w+(\+\w+)*", ocr_lang):
        invalid.append("TEXT_EXTRACTOR_OCR_LANG")

    timeout = 0.0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)s?", optional("TEXT_EXTRACTOR_OCR_PAGE_TIMEOUT", "60s"))
    if m and float(m[1]) > 0:
        timeout = float(m[1])
    else:
        invalid.append("TEXT_EXTRACTOR_OCR_PAGE_TIMEOUT")

    cfg = Config(
        amqp_url=required("AMQP_URL"),
        minio_endpoint=required("MINIO_ENDPOINT"),
        minio_access_key=required("MINIO_ACCESS_KEY"),
        minio_secret_key=required("MINIO_SECRET_KEY"),
        minio_use_ssl=ssl in ("true", "1"),
        http_host=host,
        http_port=port,
        log_level="warning" if level == "warn" else level,
        ocr_lang=ocr_lang,
        ocr_page_timeout=timeout,
    )
    if missing or invalid:
        raise ConfigError(f"config: missing [{', '.join(missing)}], invalid [{', '.join(invalid)}]")
    return cfg
