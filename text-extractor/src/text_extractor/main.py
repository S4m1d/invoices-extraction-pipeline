"""Entry point: `text-extractor` runs the service, `text-extractor healthcheck` probes it."""

import logging
import signal
import sys
import urllib.request

from . import config, health
from .ocr import Ocr, OcrError
from .processor import Processor
from .storage import Storage
from .worker import Worker

log = logging.getLogger("text_extractor")


def main() -> int:
    if sys.argv[1:] == ["healthcheck"]:
        return healthcheck()
    try:
        cfg = config.load()
    except config.ConfigError as e:
        print(e, file=sys.stderr)
        return 1
    from .context import setup_logging

    setup_logging(cfg.log_level)

    ocr = Ocr(cfg.ocr_lang, cfg.ocr_page_timeout)
    try:
        ocr.check()
    except OcrError as e:
        log.error("cannot start", extra={"error": str(e)})
        return 1

    storage = Storage(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key, cfg.minio_use_ssl)
    worker = Worker(cfg.amqp_url, lambda publisher, sleep: Processor(storage, publisher, sleep=sleep, ocr=ocr))

    def rabbitmq_check():
        if not worker.connected.is_set():
            raise ConnectionError("not connected to rabbitmq")

    def minio_check():
        storage.check()

    server = health.make_server(cfg.http_host, cfg.http_port, {"rabbitmq": rabbitmq_check, "minio": minio_check})
    health.serve_in_background(server)

    def on_signal(signum, _frame):
        log.info("shutting down", extra={"signal": signal.Signals(signum).name})
        worker.stop()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    log.info("text-extractor started", extra={"health_port": cfg.http_port})
    worker.run()
    server.shutdown()
    log.info("stopped")
    return 0


def healthcheck() -> int:
    """Probe the local /healthz; used by the Docker HEALTHCHECK."""
    import os

    addr = os.environ.get("TEXT_EXTRACTOR_HTTP_ADDR") or ":8080"
    host, _, port = addr.rpartition(":")
    try:
        with urllib.request.urlopen(f"http://{host or 'localhost'}:{port}/healthz", timeout=5) as r:
            return 0 if r.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
