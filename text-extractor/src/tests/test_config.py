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
    assert (c.ocr_lang, c.ocr_page_timeout) == ("eng", 60.0)


@pytest.mark.parametrize(
    ("lang", "timeout", "want"), [("eng+deu", "90s", ("eng+deu", 90.0)), ("eng", "2.5", ("eng", 2.5))]
)
def test_ocr_settings(lang, timeout, want):
    c = config.load({**REQUIRED, "TEXT_EXTRACTOR_OCR_LANG": lang, "TEXT_EXTRACTOR_OCR_PAGE_TIMEOUT": timeout})
    assert (c.ocr_lang, c.ocr_page_timeout) == want


def test_overrides():
    c = config.load(
        {**REQUIRED, "TEXT_EXTRACTOR_HTTP_ADDR": "127.0.0.1:9999", "MINIO_USE_SSL": "true", "LOG_LEVEL": "warn"}
    )
    assert (c.http_host, c.http_port, c.minio_use_ssl, c.log_level) == ("127.0.0.1", 9999, True, "warning")


def test_reports_all_problems():
    with pytest.raises(config.ConfigError) as exc:
        config.load(
            {
                "MINIO_USE_SSL": "maybe",
                "LOG_LEVEL": "loud",
                "TEXT_EXTRACTOR_HTTP_ADDR": "nope",
                "TEXT_EXTRACTOR_OCR_LANG": "eng deu",
                "TEXT_EXTRACTOR_OCR_PAGE_TIMEOUT": "0s",
            }
        )
    names = [
        "MINIO_USE_SSL",
        "LOG_LEVEL",
        "TEXT_EXTRACTOR_HTTP_ADDR",
        "TEXT_EXTRACTOR_OCR_LANG",
        "TEXT_EXTRACTOR_OCR_PAGE_TIMEOUT",
    ]
    for name in [*REQUIRED, *names]:
        assert name in str(exc.value)
