"""RabbitMQ consumer loop: one message at a time, publisher confirms, reconnect."""

import logging
import threading
from datetime import UTC, datetime

import pika
import pika.exceptions

from . import context, envelope
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
    def __init__(self, amqp_url: str, make_processor):
        """make_processor(publisher, sleep) builds a Processor for a channel."""
        self.params = pika.URLParameters(amqp_url)
        self.make_processor = make_processor
        self.stopping = threading.Event()
        self.connected = threading.Event()  # read by the health endpoint

    def stop(self) -> None:
        self.stopping.set()

    def run(self) -> None:
        while not self.stopping.is_set():
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
                outcome = processor.handle(body)
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
