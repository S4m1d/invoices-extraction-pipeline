import json
import logging
from datetime import UTC, datetime

import pytest

from tests import pdfs
from text_extractor import context, envelope
from text_extractor.ocr import OcrError
from text_extractor.processor import Outcome, Processor
from text_extractor.storage import ObjectNotFound, StorageUnavailable

DOC = "0f8fad5b-d9cb-469f-a165-70867728950e"
CORR = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)


def message(**overrides) -> bytes:
    m = {
        "schema_version": 1,
        "stage": "ingestor",
        "document_id": DOC,
        "correlation_id": CORR,
        "object_key": f"documents/{DOC}/original.pdf",
        "created_at": "2026-10-14T11:59:00Z",
    }
    m.update(overrides)
    return json.dumps(m).encode()


class FakeStorage:
    def __init__(self, pdf=pdfs.INVOICE):
        self.pdf = pdf
        self.calls: list[str] = []
        self.objects: dict[str, object] = {}
        self.fail: dict[str, list[Exception]] = {}  # op -> errors to raise, one per call
        self.correlation_ids: list[str] = []

    def _op(self, op):
        self.calls.append(op)
        self.correlation_ids.append(context.correlation_id.get())
        if self.fail.get(op):
            raise self.fail[op].pop(0)

    def get(self, key):
        self._op("get")
        return self.pdf

    def put_layout(self, key, markdown):
        self._op("put_layout")
        self.objects[key] = markdown

    def put_diagnostics(self, key, diagnostics):
        self._op("put_diagnostics")
        self.objects["failures/" + key] = diagnostics


class FakePublisher:
    def __init__(self, error: Exception | None = None):
        self.published: list[tuple[str, dict, str]] = []
        self.error = error

    def publish(self, queue, body, correlation_id):
        if self.error:
            raise self.error
        self.published.append((queue, json.loads(body), correlation_id))


@pytest.fixture
def env():
    storage, publisher, sleeps = FakeStorage(), FakePublisher(), []
    p = Processor(storage, publisher, sleep=sleeps.append, now=lambda: NOW)
    return p, storage, publisher, sleeps


def only_failure(publisher) -> dict:
    assert len(publisher.published) == 1
    queue, body, corr = publisher.published[0]
    assert queue == "documents.failed" and corr == CORR
    assert body["stage"] == "text-extractor" and body["document_id"] == DOC and body["correlation_id"] == CORR
    assert body["created_at"] == "2026-10-14T12:00:00Z"
    return body


def test_happy_path(env):
    p, storage, publisher, sleeps = env
    assert p.handle(message()) is Outcome.ACK
    assert storage.calls == ["get", "put_layout"]
    assert "Invoice INV-001" in storage.objects[f"documents/{DOC}/layout.md"]
    assert publisher.published == [
        (
            "documents.text_extracted",
            {
                "schema_version": 1,
                "stage": "text-extractor",
                "document_id": DOC,
                "correlation_id": CORR,
                "object_key": f"documents/{DOC}/layout.md",
                "created_at": "2026-10-14T12:00:00Z",
            },
            CORR,
        )
    ]
    assert sleeps == []
    assert set(storage.correlation_ids) == {CORR}


def test_invalid_message_is_rejected(env):
    p, storage, publisher, _ = env
    assert p.handle(b"not json") is Outcome.REJECT
    assert storage.calls == [] and publisher.published == []


def test_object_not_found(env):
    p, storage, publisher, sleeps = env
    storage.fail["get"] = [ObjectNotFound("missing")]
    assert p.handle(message()) is Outcome.ACK
    assert only_failure(publisher)["error_code"] == "object_not_found"
    assert "put_diagnostics" not in storage.calls and sleeps == []


@pytest.mark.parametrize(
    ("pdf", "code", "chars"),
    [(pdfs.EMPTY, "no_text_layer", [0]), (b"%PDF-1.4 garbage", "pdf_unreadable", None)],
)
def test_pdf_problems_write_diagnostics(env, pdf, code, chars):
    p, storage, publisher, _ = env
    storage.pdf = pdf
    assert p.handle(message()) is Outcome.ACK
    assert storage.calls == ["get", "put_diagnostics"]
    failure = only_failure(publisher)
    assert failure["error_code"] == code

    diag = storage.objects[f"failures/{DOC}.json"]
    assert diag["schema_version"] == 1
    assert diag["document_id"] == DOC and diag["correlation_id"] == CORR
    assert diag["error_code"] == code and diag["error_message"] == failure["error_message"]
    assert diag["original"] == {"bucket": "invoices", "key": f"documents/{DOC}/original.pdf", "bytes": len(pdf)}
    assert diag["chars_per_page"] == chars
    assert diag["created_at"] == "2026-10-14T12:00:00Z"
    if code == "pdf_unreadable":
        assert diag["page_count"] is None and diag["images_per_page"] is None and diag["pdf_metadata"] is None
        assert diag["page_kinds"] is None and diag["ocr_chars_per_page"] is None
    else:
        assert diag["page_kinds"] == ["empty"] and diag["ocr_chars_per_page"] == [None]


def test_ocr_failure_writes_diagnostics():
    storage, publisher = FakeStorage(pdfs.scan(1)), FakePublisher()
    ocr = pdfs.FakeOcr(error=OcrError("tesseract failed: boom"))
    p = Processor(storage, publisher, sleep=lambda s: None, now=lambda: NOW, ocr=ocr)
    assert p.handle(message()) is Outcome.ACK
    assert storage.calls == ["get", "put_diagnostics"]
    failure = only_failure(publisher)
    assert failure["error_code"] == "ocr_failed" and "boom" in failure["error_message"]
    diag = storage.objects[f"failures/{DOC}.json"]
    assert diag["page_kinds"] == ["scan"] and diag["ocr_chars_per_page"] == [None]


def test_scanned_document_is_processed(caplog):
    storage, publisher = FakeStorage(pdfs.scan(1)), FakePublisher()
    p = Processor(storage, publisher, sleep=lambda s: None, now=lambda: NOW, ocr=pdfs.FakeOcr({0: "Total $9.50"}))
    with caplog.at_level(logging.INFO):
        assert p.handle(message()) is Outcome.ACK
    assert "## Page 1 of 1 (OCR)" in storage.objects[f"documents/{DOC}/layout.md"]
    assert publisher.published[0][0] == "documents.text_extracted"
    (s,) = [r for r in caplog.records if r.getMessage() == "message processed"]
    assert s.ocr_pages == 1 and s.ocr_duration_ms >= 0


def test_storage_retried_then_succeeds(env):
    p, storage, publisher, sleeps = env
    storage.fail["get"] = [StorageUnavailable("down"), StorageUnavailable("down")]
    assert p.handle(message()) is Outcome.ACK
    assert sleeps == [1.0, 5.0]
    assert storage.calls == ["get", "get", "get", "put_layout"]
    assert publisher.published[0][0] == "documents.text_extracted"


@pytest.mark.parametrize("op", ["get", "put_layout"])
def test_storage_fails_after_three_attempts(env, op):
    p, storage, publisher, sleeps = env
    storage.fail[op] = [StorageUnavailable("down")] * 3
    assert p.handle(message()) is Outcome.ACK
    assert sleeps == [1.0, 5.0]
    assert storage.calls.count(op) == 3
    assert only_failure(publisher)["error_code"] == "storage_unavailable"


def test_diagnostics_write_failure_becomes_storage_unavailable(env):
    p, storage, publisher, sleeps = env
    storage.pdf = pdfs.EMPTY
    storage.fail["put_diagnostics"] = [StorageUnavailable("down")] * 3
    assert p.handle(message()) is Outcome.ACK
    failure = only_failure(publisher)
    assert failure["error_code"] == "storage_unavailable"
    assert "no_text_layer" in failure["error_message"]
    assert sleeps == [1.0, 5.0]


def test_unexpected_error_is_internal_error(env):
    p, storage, publisher, sleeps = env
    storage.fail["put_layout"] = [RuntimeError("boom")]
    assert p.handle(message()) is Outcome.ACK
    failure = only_failure(publisher)
    assert failure["error_code"] == "internal_error" and "boom" in failure["error_message"]
    assert sleeps == []


@pytest.mark.parametrize("pdf", [pdfs.INVOICE, pdfs.EMPTY])
def test_publisher_errors_propagate(pdf):
    """RabbitMQ failures must reach the worker so it does not ack."""
    storage = FakeStorage(pdf)
    p = Processor(storage, FakePublisher(error=ConnectionError("broker gone")), sleep=lambda s: None)
    with pytest.raises(ConnectionError):
        p.handle(message())


def test_every_log_entry_has_ids(env, caplog):
    p, storage, _, _ = env
    storage.fail["get"] = [StorageUnavailable("down")]
    with caplog.at_level(logging.DEBUG):
        p.handle(message())
    records = [r for r in caplog.records if r.name.startswith("text_extractor")]
    assert records
    for r in records:
        line = json.loads(context.JsonFormatter().format(r))
        assert line["correlation_id"] == CORR and line["document_id"] == DOC


def test_summary_log(env, caplog):
    p, _, _, _ = env
    with caplog.at_level(logging.INFO):
        p.handle(message())
    summary = [r for r in caplog.records if r.getMessage() == "message processed"]
    assert len(summary) == 1
    s = summary[0]
    assert s.result == "ok" and s.pages == 1 and s.output_bytes > 0 and s.duration_ms >= 0
    assert s.ocr_pages == 0 and s.ocr_duration_ms >= 0


def test_layout_key_matches_contract():
    assert envelope.layout_key(DOC) == f"documents/{DOC}/layout.md"
