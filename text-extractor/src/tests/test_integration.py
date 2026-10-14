"""Integration tests against real RabbitMQ and MinIO.

Each test gets its own RabbitMQ vhost (created through the management API), so real queue
names can be used and deleted without touching other environments. MinIO buckets `invoices`
and `text-extractor-failures` must exist.

    AMQP_URL=amqp://guest:guest@localhost:5672/ RABBITMQ_MANAGEMENT_URL=http://guest:guest@localhost:15672 \\
    MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=... uv run pytest -m integration
"""

import base64
import io
import json
import os
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime

import pika
import pytest

from tests import pdfs
from text_extractor import storage as storage_mod
from text_extractor import worker as worker_mod
from text_extractor.processor import Processor
from text_extractor.storage import ObjectNotFound, Storage, StorageUnavailable

pytestmark = pytest.mark.integration

QUEUES = ["documents.ingested", "documents.text_extracted", "documents.fields_extracted", "documents.failed"]


class Management:
    def __init__(self, url: str):
        u = urllib.parse.urlsplit(url)
        self.base = f"{u.scheme}://{u.hostname}:{u.port}/api"
        self.auth = "Basic " + base64.b64encode(f"{u.username}:{u.password}".encode()).decode()
        self.user = u.username

    def call(self, method: str, path: str, body=None):
        req = urllib.request.Request(
            self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None
        )
        req.add_header("Authorization", self.auth)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read()
            return json.loads(data) if data else None


@pytest.fixture
def vhost():
    """A fresh vhost with the contract's queues, dead-letter exchange and policy."""
    mgmt = Management(os.environ.get("RABBITMQ_MANAGEMENT_URL", "http://guest:guest@localhost:15672"))
    name = f"test-text-extractor-{uuid.uuid4().hex[:8]}"
    q = urllib.parse.quote(name, safe="")
    mgmt.call("PUT", f"/vhosts/{q}", {})
    mgmt.call("PUT", f"/permissions/{q}/{mgmt.user}", {"configure": ".*", "write": ".*", "read": ".*"})
    mgmt.call(
        "PUT",
        f"/policies/{q}/dead-letter",
        {
            "pattern": r"^documents\.(ingested|text_extracted|fields_extracted)$",
            "apply-to": "queues",
            "definition": {"dead-letter-exchange": "dlx"},
        },
    )

    base = urllib.parse.urlsplit(os.environ["AMQP_URL"])
    url = urllib.parse.urlunsplit(base._replace(path="/" + q))
    conn = pika.BlockingConnection(pika.URLParameters(url))
    ch = conn.channel()
    ch.exchange_declare("dlx", "direct", durable=True)
    for name_ in QUEUES:
        ch.queue_declare(name_, durable=True)
    for name_ in QUEUES[:3]:
        ch.queue_declare(f"{name_}.dlq", durable=True)
        ch.queue_bind(f"{name_}.dlq", "dlx", routing_key=name_)
    conn.close()

    yield url, mgmt, name
    mgmt.call("DELETE", f"/vhosts/{q}")


@pytest.fixture(scope="module")
def storage():
    return Storage(
        os.environ["MINIO_ENDPOINT"], os.environ["MINIO_ACCESS_KEY"], os.environ["MINIO_SECRET_KEY"], secure=False
    )


@pytest.fixture
def run_worker(storage, monkeypatch):
    monkeypatch.setattr(worker_mod, "RECONNECT_WAIT", 0.5)
    workers = []

    def start(url):
        w = worker_mod.Worker(url, lambda pub, sleep: Processor(storage, pub, sleep=sleep))
        t = threading.Thread(target=w.run, daemon=True)
        t.start()
        assert w.connected.wait(10), "worker did not connect"
        workers.append((w, t))
        return w

    yield start
    for w, t in workers:
        w.stop()
        t.join(10)


def channel(url):
    return pika.BlockingConnection(pika.URLParameters(url)).channel()


def submit(storage, ch, pdf=pdfs.INVOICE) -> str:
    doc = str(uuid.uuid4())
    key = f"documents/{doc}/original.pdf"
    storage._client.put_object("invoices", key, io.BytesIO(pdf), len(pdf))
    ch.basic_publish(
        "",
        "documents.ingested",
        json.dumps(
            {
                "schema_version": 1,
                "stage": "ingestor",
                "document_id": doc,
                "correlation_id": str(uuid.uuid4()),
                "object_key": key,
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        ).encode(),
        pika.BasicProperties(delivery_mode=2),
    )
    return doc


def wait_message(ch, queue, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        method, props, body = ch.basic_get(queue, auto_ack=True)
        if method:
            return props, body
        time.sleep(0.1)
    pytest.fail(f"no message on {queue}")


def counts(ch, queue):
    q = ch.queue_declare(queue, passive=True).method
    return q.message_count


def test_processes_and_acks(vhost, storage, run_worker):
    url, _, _ = vhost
    run_worker(url)
    ch = channel(url)
    doc = submit(storage, ch)
    props, body = wait_message(ch, "documents.text_extracted")
    msg = json.loads(body)
    assert msg["document_id"] == doc and msg["stage"] == "text-extractor"
    assert props.delivery_mode == 2 and props.content_type == "application/json"
    assert props.correlation_id == msg["correlation_id"]
    assert props.headers["X-Correlation-ID"] == msg["correlation_id"]
    time.sleep(0.5)
    assert counts(ch, "documents.ingested") == 0  # acked, not waiting for redelivery


def test_unroutable_output_is_not_acked(vhost, storage, run_worker):
    url, _, _ = vhost
    run_worker(url)
    ch = channel(url)
    ch.queue_delete("documents.text_extracted")
    doc = submit(storage, ch)
    time.sleep(3)  # worker fails to publish, does not ack, reconnects and waits for the queue
    assert counts(ch, "documents.ingested") == 1

    ch.queue_declare("documents.text_extracted", durable=True)
    _, body = wait_message(ch, "documents.text_extracted", timeout=15)
    assert json.loads(body)["document_id"] == doc


def test_reconnects_after_connection_loss(vhost, storage, run_worker):
    url, mgmt, name = vhost
    w = run_worker(url)
    deadline = time.monotonic() + 10
    conns = []
    while not conns and time.monotonic() < deadline:
        conns = [c for c in mgmt.call("GET", "/connections") if c.get("vhost") == name]
        time.sleep(0.2)
    for c in conns:
        mgmt.call("DELETE", "/connections/" + urllib.parse.quote(c["name"], safe=""))
    time.sleep(1)
    assert w.connected.wait(10)
    ch = channel(url)
    doc = submit(storage, ch)
    _, body = wait_message(ch, "documents.text_extracted", timeout=15)
    assert json.loads(body)["document_id"] == doc


def test_invalid_message_is_dead_lettered(vhost, run_worker):
    url, _, _ = vhost
    run_worker(url)
    ch = channel(url)
    ch.basic_publish("", "documents.ingested", b'{"not": "an envelope"}')
    _, body = wait_message(ch, "documents.ingested.dlq")
    assert json.loads(body) == {"not": "an envelope"}


def test_missing_queue_blocks_connect(vhost, storage, monkeypatch):
    url, _, _ = vhost
    ch = channel(url)
    ch.queue_delete("documents.failed")
    monkeypatch.setattr(worker_mod, "RECONNECT_WAIT", 0.2)
    w = worker_mod.Worker(url, lambda pub, sleep: Processor(storage, pub, sleep=sleep))
    t = threading.Thread(target=w.run, daemon=True)
    t.start()
    assert not w.connected.wait(2)
    w.stop()
    t.join(5)


def test_storage_roundtrip(storage):
    key = f"test/{uuid.uuid4()}/layout.md"
    storage.put_layout(key, "# hello")
    obj = storage._client.stat_object("invoices", key)
    assert obj.content_type == "text/markdown; charset=utf-8"
    assert storage._client.get_object("invoices", key).read() == b"# hello"
    storage.put_diagnostics(f"test-{uuid.uuid4()}.json", {"a": 1})
    storage.check()


def test_storage_missing_object(storage):
    with pytest.raises(ObjectNotFound):
        storage.get(f"documents/{uuid.uuid4()}/original.pdf")


def test_storage_missing_bucket(storage, monkeypatch):
    monkeypatch.setattr(storage_mod, "FAILURES_BUCKET", f"missing-{uuid.uuid4().hex[:8]}")
    with pytest.raises(StorageUnavailable, match="does not exist"):
        storage.check()
