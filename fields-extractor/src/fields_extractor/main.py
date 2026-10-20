"""Entry point: `fields-extractor` runs the service, `fields-extractor healthcheck` probes it."""

import logging
import os
import signal
import socket
import sys
import threading
import urllib.request

from . import config, health
from .budget import DailyBudget
from .context import setup_logging
from .llm import LlmDailyBudget, LlmUsageLimit, NotImplementedProvider, make_provider
from .llm.pricing import Pricing, Rate, UnknownPrice
from .processor import Processor
from .storage import Storage
from .worker import Worker

log = logging.getLogger("fields_extractor")


def main() -> int:
    if sys.argv[1:] == ["healthcheck"]:
        return healthcheck()
    try:
        cfg = config.load()
        override = (
            Rate(cfg.llm_price_input, cfg.llm_price_output) if cfg.llm_price_input and cfg.llm_price_output else None
        )
        pricing = Pricing(cfg.llm_model, override)
    except (config.ConfigError, UnknownPrice) as e:
        print(e, file=sys.stderr)
        return 1
    setup_logging(cfg.log_level)

    storage = Storage(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key, cfg.minio_use_ssl)
    instance = socket.gethostname()
    budget = DailyBudget(storage, cfg.llm_daily_budget_usd, instance)

    def on_usage(model: str, input_tokens: int, output_tokens: int) -> None:
        budget.add(pricing.cost(model, input_tokens, output_tokens), input_tokens, output_tokens)

    try:
        provider = make_provider(
            cfg.llm_provider,
            model=cfg.llm_model,
            effort=cfg.llm_effort,
            max_retries=cfg.llm_max_retries,
            on_usage=on_usage,
        )
    except NotImplementedProvider as e:
        log.error("cannot start", extra={"error": str(e)})
        return 1

    worker = Worker(
        cfg.amqp_url,
        lambda publisher, sleep: Processor(storage, publisher, provider, budget=budget, sleep=sleep),
        checks={LlmUsageLimit.code: provider.check_available, LlmDailyBudget.code: budget.available},
        check_interval=cfg.llm_limit_check_interval,
        max_checks=cfg.llm_limit_max_checks,
    )
    shutdown = threading.Event()

    def rabbitmq_check():
        idle = worker.misconfigured or worker.paused or worker.stopped
        if not idle and not worker.connected.is_set():
            raise ConnectionError("not connected to rabbitmq")

    def llm_check():
        if worker.misconfigured is not None:
            raise RuntimeError(f"llm_misconfigured: {worker.misconfigured}")
        if worker.paused is not None:
            code, reason = worker.paused
            raise RuntimeError(f"{code} (paused): {reason}")
        if worker.stopped is not None:
            raise RuntimeError(worker.stopped)

    server = health.make_server(
        cfg.http_host,
        cfg.http_port,
        {"rabbitmq": rabbitmq_check, "minio": storage.check, "llm": llm_check},
    )
    health.serve_in_background(server)

    def on_signal(signum, _frame):
        log.info("shutting down", extra={"signal": signal.Signals(signum).name})
        shutdown.set()
        worker.stop()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    log.info(
        "fields-extractor started",
        extra={
            "model": cfg.llm_model,
            "effort": cfg.llm_effort,
            "daily_budget_usd": str(cfg.llm_daily_budget_usd),
            "instance": instance,
        },
    )
    worker.run()
    if worker.misconfigured is not None or worker.stopped is not None:
        # Stay up and unhealthy until stopped: no restart loop, no further API calls.
        shutdown.wait()
    server.shutdown()
    log.info("stopped")
    return 0


def healthcheck() -> int:
    """Probe the local /healthz; used by the Docker HEALTHCHECK."""
    addr = os.environ.get("FIELDS_EXTRACTOR_HTTP_ADDR") or ":8080"
    host, _, port = addr.rpartition(":")
    try:
        with urllib.request.urlopen(f"http://{host or 'localhost'}:{port}/healthz", timeout=5) as r:
            return 0 if r.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
