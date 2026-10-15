"""Per-message identifiers for logs and outgoing calls (see /specs/pipeline-contracts.md).

Copied from text-extractor (fields-extractor/specs/overview.md, Code sharing)."""

import contextvars
import json
import logging
from contextlib import contextmanager
from datetime import UTC, datetime

HEADER = "X-Correlation-ID"

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")
document_id: contextvars.ContextVar[str] = contextvars.ContextVar("document_id", default="")


@contextmanager
def ids(corr: str, doc: str):
    t1, t2 = correlation_id.set(corr), document_id.set(doc)
    try:
        yield
    finally:
        correlation_id.reset(t1)
        document_id.reset(t2)


_base_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs) -> logging.LogRecord:
    # Capture the ids when the record is created, not when it is formatted,
    # so buffered or queued handlers still see the right values.
    record = _base_factory(*args, **kwargs)
    record.correlation_id = correlation_id.get()
    record.document_id = document_id.get()
    return record


logging.setLogRecordFactory(_record_factory)

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line; adds correlation_id/document_id and `extra` fields."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        if cid := getattr(record, "correlation_id", ""):
            entry["correlation_id"] = cid
        if did := getattr(record, "document_id", ""):
            entry["document_id"] = did
        for k, v in record.__dict__.items():
            if k not in _STANDARD and k not in entry and k not in ("correlation_id", "document_id"):
                entry[k] = v
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    logging.getLogger("pika").setLevel(logging.WARNING)
    for name in ("httpx", "httpx2", "httpcore", "anthropic"):  # request lines and SDK chatter
        logging.getLogger(name).setLevel(logging.WARNING)
