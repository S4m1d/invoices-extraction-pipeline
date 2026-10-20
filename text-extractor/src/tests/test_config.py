import pytest

from text_extractor import config

REQUIRED = {
    "AMQP_URL": "amqp://guest:guest@localhost:5672/",
    "MINIO_ENDPOINT": "localhost:9000",
    "MINIO_ACCESS_KEY": "key",
    "MINIO_SECRET_KEY": "secret",
}


def test_defaults():
    c = config.load(dict(REQUIRED))
    assert (c.http_host, c.http_port, c.minio_use_ssl, c.log_level) == ("", 8080, False, "info")
    assert c.amqp_url == REQUIRED["AMQP_URL"] and c.minio_endpoint == "localhost:9000"


def test_overrides():
    c = config.load(
        {**REQUIRED, "TEXT_EXTRACTOR_HTTP_ADDR": "127.0.0.1:9999", "MINIO_USE_SSL": "true", "LOG_LEVEL": "warn"}
    )
    assert (c.http_host, c.http_port, c.minio_use_ssl, c.log_level) == ("127.0.0.1", 9999, True, "warning")


def test_reports_all_problems():
    with pytest.raises(config.ConfigError) as exc:
        config.load({"MINIO_USE_SSL": "maybe", "LOG_LEVEL": "loud", "TEXT_EXTRACTOR_HTTP_ADDR": "nope"})
    for name in [*REQUIRED, "MINIO_USE_SSL", "LOG_LEVEL", "TEXT_EXTRACTOR_HTTP_ADDR"]:
        assert name in str(exc.value)
