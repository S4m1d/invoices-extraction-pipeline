"""RabbitMQ consumer loop: one message at a time, publisher confirms, reconnect.

Adapted from text-extractor: stops consuming on LlmMisconfigured; pauses on LlmPaused
(usage limit, daily budget), checks every interval and stops after N failed checks
(spec overview, Usage limits)."""

import logging
import threading
from datetime import UTC, datetime

import pika
import pika.exceptions

from . import context, envelope
from .llm import LlmMisconfigured, LlmPaused
from .processor import Outcome, Processor

log = logging.getLogger(__name__)

RECONNECT_WAIT = 5.0


class ChannelPublisher:
    """Publishes on the consuming channel, which is in confirm mode."""

    def __init__(self, channel):
        self.channel = channel

    def publish(self, queue: str, body: bytes, correlation_id: str) -> None:
        # Raises UnroutableError (queue missing) or NackError; the message is then not acked.
        self.channel.basic_publish(
            exchange="",
            routing_key=queue,
            body=body,
            mandatory=True,
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=pika.DeliveryMode.Persistent,
                correlation_id=correlation_id,
                headers={context.HEADER: correlation_id},
                timestamp=int(datetime.now(UTC).timestamp()),
            ),
        )


class Worker:
    def __init__(
        self,
        amqp_url: str,
        make_processor,
        checks: dict | None = None,
        check_interval: float = 120.0,
        max_checks: int = 3,
    ):
        """make_processor(publisher, sleep) builds a Processor for a channel.

        checks maps a pause code (LlmPaused.code) to a function that returns True when the
        limit no longer holds; it may raise LlmMisconfigured.
        """
        self.params = pika.URLParameters(amqp_url)
        self.make_processor = make_processor
        self.stopping = threading.Event()
        self.connected = threading.Event()  # read by the health endpoint
        self.misconfigured: str | None = None  # set when the LLM configuration is broken
        self.paused: tuple[str, str] | None = None  # (code, reason) while paused on a limit
        self.stopped: str | None = None  # set when the worker stopped after failed checks
        self.checks = checks or {}
        self.check_interval = check_interval
        self.max_checks = max_checks
        self._failed_checks = 0

    def stop(self) -> None:
        self.stopping.set()

    def run(self) -> None:
        while not self.stopping.is_set():
            if self.paused is not None:
                self._wait_for_limit()
                continue
            try:
                self._consume()
            except (pika.exceptions.AMQPError, OSError) as e:
                self.connected.clear()
                log.error(
                    "rabbitmq connection lost, reconnecting",
                    extra={"error": f"{type(e).__name__}: {e}", "wait_s": RECONNECT_WAIT},
                )
                self.stopping.wait(RECONNECT_WAIT)
        self.connected.clear()

    def _wait_for_limit(self) -> None:
        """Check every interval whether the limit still holds; stop after max_checks failures."""
        if self.stopping.wait(self.check_interval):
            return
        code, reason = self.paused
        check = self.checks.get(code)
        try:
            passed = check() if check else True
        except LlmMisconfigured as e:
            self.misconfigured = str(e)
            self.paused = None
            log.error("llm misconfigured, stopped consuming", extra={"error": str(e)})
            self.stopping.set()
            return
        except Exception as e:  # e.g. storage unavailable while reading the budget: a failed check
            log.warning("limit check failed", extra={"error_code": code, "error": str(e)})
            passed = False
        if passed:
            log.info("llm limit lifted, resuming", extra={"error_code": code})
            self.paused = None
            return
        self._failed_checks += 1
        log.debug("llm limit still reached", extra={"error_code": code, "check": self._failed_checks})
        if self._failed_checks >= self.max_checks:
            self.stopped = f"{code}: stopped after {self._failed_checks} checks: {reason}"
            self.paused = None
            log.error("llm limit still reached, worker stopped", extra={"error_code": code, "error": reason})
            self.stopping.set()

    def _consume(self) -> None:
        conn = pika.BlockingConnection(self.params)
        try:
            ch = conn.channel()
            for q in (envelope.INPUT_QUEUE, envelope.OUTPUT_QUEUE, envelope.FAILED_QUEUE):
                ch.queue_declare(q, passive=True)  # created by infra; fails if missing
            ch.confirm_delivery()
            ch.basic_qos(prefetch_count=1)
            processor: Processor = self.make_processor(ChannelPublisher(ch), conn.sleep)
            self.connected.set()
            log.info("consuming", extra={"queue": envelope.INPUT_QUEUE})

            for method, _props, body in ch.consume(envelope.INPUT_QUEUE, inactivity_timeout=1):
                if self.stopping.is_set():
                    break
                if method is None:
                    continue  # inactivity tick
                try:
                    outcome = processor.handle(body)
                except LlmPaused as e:
                    # Not a document failure: return the message (it keeps its place) and pause.
                    ch.basic_nack(method.delivery_tag, requeue=True)
                    self.paused = (e.code, str(e))
                    self._failed_checks = 0
                    log.error("llm limit reached, paused", extra={"error_code": e.code, "error": str(e)})
                    break
                except LlmMisconfigured as e:
                    # Not a document failure: return the message and stop consuming.
                    ch.basic_nack(method.delivery_tag, requeue=True)
                    self.misconfigured = str(e)
                    log.error("llm misconfigured, stopped consuming", extra={"error": str(e)})
                    self.stopping.set()
                    break
                if outcome is Outcome.ACK:
                    ch.basic_ack(method.delivery_tag)
                else:
                    ch.basic_reject(method.delivery_tag, requeue=False)
            ch.cancel()
        finally:
            self.connected.clear()
            if conn.is_open:
                try:
                    conn.close()
                except pika.exceptions.AMQPError:
                    pass
