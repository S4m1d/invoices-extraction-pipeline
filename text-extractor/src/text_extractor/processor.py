"""Processing of one input message (see text-extractor/specs/overview.md, Processing)."""

import logging
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from . import context, envelope
from .convert import ConversionError, PdfInfo, convert
from .ocr import Ocr
from .storage import BUCKET, FAILURES_BUCKET, ObjectNotFound, StorageUnavailable

log = logging.getLogger(__name__)

# Transient storage errors: 3 attempts, waiting 1 s and then 5 s.
RETRY_WAITS = (1.0, 5.0)


class Outcome(Enum):
    ACK = "ack"
    REJECT = "reject"  # without requeue: dead-lettered


class Storage(Protocol):
    def get(self, key: str) -> bytes: ...
    def put_layout(self, key: str, markdown: str) -> None: ...
    def put_diagnostics(self, key: str, diagnostics: dict) -> None: ...


class Publisher(Protocol):
    def publish(self, queue: str, body: bytes, correlation_id: str) -> None:
        """Publish persistently and wait for the broker's confirm. Raises on failure."""


class Processor:
    def __init__(
        self,
        storage: Storage,
        publisher: Publisher,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        ocr: Ocr | None = None,
    ):
        self.storage = storage
        self.publisher = publisher
        self.sleep = sleep
        self.now = now
        self.ocr = ocr or Ocr()

    def handle(self, body: bytes) -> Outcome:
        """Process one message. RabbitMQ errors propagate: the caller must not ack then."""
        try:
            env = envelope.parse(body)
        except envelope.InvalidMessage as e:
            log.warning("invalid message, rejecting to DLQ", extra={"error": str(e)})
            return Outcome.REJECT

        with context.ids(env.correlation_id, env.document_id):
            start = time.monotonic()
            result = self._process(env)
            log.info("message processed", extra={**result, "duration_ms": round((time.monotonic() - start) * 1000)})
            return Outcome.ACK

    def _process(self, env: envelope.Envelope) -> dict:
        try:
            pdf = self._retry(lambda: self.storage.get(env.object_key))
            res = convert(pdf, env.document_id, self.ocr)
            self._retry(lambda: self.storage.put_layout(envelope.layout_key(env.document_id), res.markdown))
        except ObjectNotFound as e:
            return self._fail(env, "object_not_found", str(e))
        except ConversionError as e:
            return self._fail(env, e.code, str(e), info=e.info, pdf_bytes=len(pdf))
        except StorageUnavailable as e:
            return self._fail(env, "storage_unavailable", str(e))
        except Exception as e:
            log.exception("unexpected error")
            return self._fail(env, "internal_error", f"{type(e).__name__}: {e}")

        self.publisher.publish(envelope.OUTPUT_QUEUE, envelope.output(env, self.now()), env.correlation_id)
        return {
            "result": "ok",
            "pages": res.info.page_count,
            "ocr_pages": len(res.ocr_pages),
            "output_bytes": len(res.markdown.encode()),
            "ocr_duration_ms": res.ocr_duration_ms,
        }

    def _fail(
        self, env: envelope.Envelope, code: str, message: str, info: PdfInfo | None = None, pdf_bytes: int | None = None
    ) -> dict:
        if info is not None:
            diagnostics = {
                "schema_version": 1,
                "document_id": env.document_id,
                "correlation_id": env.correlation_id,
                "error_code": code,
                "error_message": message,
                "original": {"bucket": BUCKET, "key": env.object_key, "bytes": pdf_bytes},
                **asdict(info),
                "created_at": envelope.timestamp(self.now()),
            }
            key = f"{env.document_id}.json"
            try:
                self._retry(lambda: self.storage.put_diagnostics(key, diagnostics))
            except StorageUnavailable as e:
                log.error(
                    "cannot write diagnostics file",
                    extra={"error_code": code, "error": str(e), "bucket": FAILURES_BUCKET},
                )
                code, message = "storage_unavailable", f"writing diagnostics for {code}: {e}"

        log.warning("document failed", extra={"error_code": code, "error": message})
        self.publisher.publish(
            envelope.FAILED_QUEUE, envelope.failure(env, code, message, self.now()), env.correlation_id
        )
        return {"result": "failed", "error_code": code}

    def _retry(self, op):
        for attempt, wait in enumerate((*RETRY_WAITS, None), 1):
            try:
                return op()
            except StorageUnavailable as e:
                if wait is None:
                    raise
                log.warning(
                    "storage unavailable, retrying", extra={"attempt": attempt, "wait_s": wait, "error": str(e)}
                )
                self.sleep(wait)
