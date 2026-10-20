"""Smoke test against a running text-extractor, RabbitMQ and MinIO.

Queues and buckets must exist (infra). Run:
    AMQP_URL=... MINIO_ENDPOINT=... MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=... uv run pytest -m smoke
"""

import io
import json
import os
import time
import uuid
from datetime import UTC, datetime

import pika
import pytest
from minio import Minio

from tests import pdfs

pytestmark = pytest.mark.smoke


@pytest.fixture(scope="module")
def minio():
    return Minio(
        os.environ["MINIO_ENDPOINT"],
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=False,
    )


@pytest.fixture
def channel():
    conn = pika.BlockingConnection(pika.URLParameters(os.environ["AMQP_URL"]))
    yield conn.channel()
    conn.close()


def submit(minio, channel, pdf: bytes) -> dict:
    doc, corr = str(uuid.uuid4()), str(uuid.uuid4())
    key = f"documents/{doc}/original.pdf"
    minio.put_object("invoices", key, io.BytesIO(pdf), len(pdf), content_type="application/pdf")
    env = {
        "schema_version": 1,
        "stage": "ingestor",
        "document_id": doc,
        "correlation_id": corr,
        "object_key": key,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    channel.basic_publish(
        "",
        "documents.ingested",
        json.dumps(env).encode(),
        pika.BasicProperties(delivery_mode=2, content_type="application/json"),
    )
    return env


def take(channel, queue: str, match, timeout: float = 15) -> dict:
    """Get messages from queue until match(message) is true; others are requeued."""
    deadline, skipped = time.monotonic() + timeout, []
    try:
        while time.monotonic() < deadline:
            method, _props, body = channel.basic_get(queue)
            if method is None:
                time.sleep(0.2)
                continue
            m = json.loads(body)
            if match(m):
                channel.basic_ack(method.delivery_tag)
                return m
            skipped.append(method.delivery_tag)
        pytest.fail(f"no matching message on {queue} within {timeout}s")
    finally:
        for tag in skipped:
            channel.basic_nack(tag, requeue=True)


def test_invoice_is_extracted(minio, channel):
    env = submit(minio, channel, pdfs.INVOICE)
    out = take(channel, "documents.text_extracted", lambda m: m["document_id"] == env["document_id"])
    assert out["stage"] == "text-extractor"
    assert out["correlation_id"] == env["correlation_id"]
    assert out["object_key"] == f"documents/{env['document_id']}/layout.md"

    md = minio.get_object("invoices", out["object_key"]).read().decode()
    assert "## Page 1 of 1" in md
    assert "Invoice INV-001" in md and "$20.00" in md


def test_pdf_without_text_fails_with_diagnostics(minio, channel):
    env = submit(minio, channel, pdfs.EMPTY)
    fail = take(channel, "documents.failed", lambda m: m["document_id"] == env["document_id"])
    assert fail["stage"] == "text-extractor"
    assert fail["error_code"] == "no_text_layer"
    assert fail["correlation_id"] == env["correlation_id"]

    diag = json.loads(minio.get_object("text-extractor-failures", f"{env['document_id']}.json").read())
    assert diag["error_code"] == "no_text_layer"
    assert diag["page_count"] == 1 and diag["chars_per_page"] == [0]


def test_invalid_message_is_dead_lettered(channel):
    try:
        channel.queue_declare("documents.ingested.dlq", passive=True)
    except pika.exceptions.ChannelClosedByBroker:
        pytest.skip("documents.ingested.dlq does not exist (dead-lettering not set up)")
    marker = str(uuid.uuid4())
    channel.basic_publish("", "documents.ingested", json.dumps({"marker": marker}).encode())
    take(channel, "documents.ingested.dlq", lambda m: m.get("marker") == marker)
