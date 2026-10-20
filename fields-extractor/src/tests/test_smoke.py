"""Smoke test against a running fields-extractor, RabbitMQ and MinIO.

The service calls whatever LLM it is configured with (the real API, or tests/fake_anthropic.py,
which returns the extraction-schema.md example). Queues and buckets must exist (infra). Run:
    AMQP_URL=... MINIO_ENDPOINT=... MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=... uv run pytest -m smoke
"""

import io
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pika
import pytest
from minio import Minio

from fields_extractor.llm import PROMPT_VERSION

pytestmark = pytest.mark.smoke

ROOT = Path(__file__).parents[3]
# The text-extractor output for inv_00000029, as documented in its spec.
LAYOUT = (ROOT / "text-extractor/specs/output-format.md").read_text().split("````markdown\n", 1)[1].split("\n````")[0]
# Expected values: the extraction-schema.md example, which matches the golden set.
EXPECTED = json.loads(
    (ROOT / "fields-extractor/specs/extraction-schema.md")
    .read_text()
    .split("## Example\n\n```json\n", 1)[1]
    .split("\n```")[0]
)


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


def publish(channel, doc, corr):
    env = {
        "schema_version": 1,
        "stage": "text-extractor",
        "document_id": doc,
        "correlation_id": corr,
        "object_key": f"documents/{doc}/layout.md",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    channel.basic_publish(
        "",
        "documents.text_extracted",
        json.dumps(env).encode(),
        pika.BasicProperties(delivery_mode=2, content_type="application/json"),
    )


def take(channel, queue, match, timeout=120):
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


def test_layout_is_extracted(minio, channel):
    doc, corr = str(uuid.uuid4()), str(uuid.uuid4())
    data = LAYOUT.replace("0f8fad5b-d9cb-469f-a165-70867728950e", doc).encode()
    minio.put_object("invoices", f"documents/{doc}/layout.md", io.BytesIO(data), len(data))
    publish(channel, doc, corr)

    out = take(channel, "documents.fields_extracted", lambda m: m["document_id"] == doc)
    assert out["stage"] == "fields-extractor" and out["correlation_id"] == corr
    assert out["object_key"] == f"documents/{doc}/extracted.json"

    stat = minio.stat_object("invoices", out["object_key"])
    meta = {k.lower(): v for k, v in stat.metadata.items()}
    assert meta.get("x-amz-meta-prompt-version") == PROMPT_VERSION
    assert meta.get("x-amz-meta-model")
    extracted = json.loads(minio.get_object("invoices", out["object_key"]).read())
    assert extracted == EXPECTED


def test_missing_layout_fails(channel):
    doc, corr = str(uuid.uuid4()), str(uuid.uuid4())
    publish(channel, doc, corr)
    fail = take(channel, "documents.failed", lambda m: m["document_id"] == doc)
    assert fail["stage"] == "fields-extractor" and fail["error_code"] == "object_not_found"


def test_invalid_message_is_dead_lettered(channel):
    marker = str(uuid.uuid4())
    channel.basic_publish("", "documents.text_extracted", json.dumps({"marker": marker}).encode())
    take(channel, "documents.text_extracted.dlq", lambda m: m.get("marker") == marker, timeout=15)
