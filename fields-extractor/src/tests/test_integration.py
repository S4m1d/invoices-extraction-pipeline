"""Integration tests against real RabbitMQ and MinIO, with the fake LLM API (tests/fake_anthropic.py).

Each test gets its own RabbitMQ vhost (created through the management API), so real queue
names can be used and deleted without touching other environments. MinIO buckets `invoices`
`fields-extractor-failures` and `fields-extractor-usage` must exist.

    AMQP_URL=amqp://guest:guest@localhost:5672/ RABBITMQ_MANAGEMENT_URL=http://guest:guest@localhost:15672 \\
    MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=... uv run pytest -m integration
"""

import base64
import datetime as dt
import io
import json
import os
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import anthropic
import pika
import pytest

from fields_extractor import storage as storage_mod
from fields_extractor.budget import DailyBudget
from fields_extractor.llm import PROMPT_VERSION, LlmDailyBudget, LlmUsageLimit
from fields_extractor.llm.anthropic_provider import AnthropicProvider
from fields_extractor.llm.pricing import Pricing
from fields_extractor.processor import Processor
from fields_extractor.storage import ObjectNotFound, Storage, StorageUnavailable
from fields_extractor.worker import Worker
from tests.fake_anthropic import EXAMPLE_OUTPUT, FakeAnthropic, error, spend_limit, text
from tests.spec_examples import extraction_example

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
def fake():
    f = FakeAnthropic(default=text(EXAMPLE_OUTPUT)).start()
    yield f
    f.stop()


class Day:
    """A test-only UTC day, so budget objects never mix with real ones; removed afterwards."""

    def __init__(self, storage):
        self.d = dt.date(2000, 1, 1) + dt.timedelta(days=uuid.uuid4().int % 3000)
        self.storage = storage

    def __call__(self):
        return self.d

    def cleanup(self):
        c = self.storage._client
        for day in (self.d, self.d + dt.timedelta(days=1)):
            for o in c.list_objects("fields-extractor-usage", prefix=f"{day.isoformat()}/"):
                c.remove_object("fields-extractor-usage", o.object_name)


@pytest.fixture
def run_worker(storage, fake, monkeypatch):
    monkeypatch.setattr("fields_extractor.worker.RECONNECT_WAIT", 0.5)
    started = []

    def start(url, budget_usd="10", interval=0.3, max_checks=3):
        day = Day(storage)
        pricing = Pricing("claude-haiku-5-5")
        budget = DailyBudget(storage, Decimal(budget_usd), f"test-{uuid.uuid4().hex[:6]}", today=day)
        client = anthropic.Anthropic(api_key="test", base_url=fake.url, max_retries=0)
        provider = AnthropicProvider(
            "claude-haiku-5-5",
            "low",
            0,
            client=client,
            sleep=lambda s: None,
            on_usage=lambda m, i, o: budget.add(pricing.cost(m, i, o), i, o),
        )
        w = Worker(
            url,
            lambda pub, sleep: Processor(storage, pub, provider, budget=budget, sleep=sleep),
            checks={LlmUsageLimit.code: provider.check_available, LlmDailyBudget.code: budget.available},
            check_interval=interval,
            max_checks=max_checks,
        )
        w.day, w.budget = day, budget
        t = threading.Thread(target=w.run, daemon=True)
        t.start()
        assert w.connected.wait(10), "worker did not connect"
        started.append((w, t))
        return w

    yield start
    for w, t in started:
        w.stop()
        t.join(10)
        w.day.cleanup()


def channel(url):
    return pika.BlockingConnection(pika.URLParameters(url)).channel()


def submit(storage, ch) -> str:
    doc = str(uuid.uuid4())
    key = f"documents/{doc}/layout.md"
    data = b"## Page 1 of 1\n"
    storage._client.put_object("invoices", key, io.BytesIO(data), len(data))
    ch.basic_publish(
        "",
        "documents.text_extracted",
        json.dumps(
            {
                "schema_version": 1,
                "stage": "text-extractor",
                "document_id": doc,
                "correlation_id": str(uuid.uuid4()),
                "object_key": key,
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        ).encode(),
        pika.BasicProperties(delivery_mode=2),
    )
    return doc


def collect(ch, queue, n, timeout=10):
    """Up to n messages from queue (document ids, in order)."""
    out, deadline = [], time.monotonic() + timeout
    while len(out) < n and time.monotonic() < deadline:
        method, _, body = ch.basic_get(queue, auto_ack=True)
        if method:
            out.append(json.loads(body)["document_id"])
        else:
            time.sleep(0.1)
    return out


def counts(ch, queue):
    return ch.queue_declare(queue, passive=True).method.message_count


def wait_until(cond, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_processes_and_acks(vhost, storage, run_worker):
    url, _, _ = vhost
    w = run_worker(url)
    ch = channel(url)
    doc = submit(storage, ch)
    assert collect(ch, "documents.fields_extracted", 1) == [doc]
    key = f"documents/{doc}/extracted.json"
    assert json.loads(storage._client.get_object("invoices", key).read()) == extraction_example()
    meta = {k.lower(): v for k, v in storage._client.stat_object("invoices", key).metadata.items()}
    assert meta["x-amz-meta-model"] == "claude-haiku-5-5" and meta["x-amz-meta-prompt-version"] == PROMPT_VERSION
    assert counts(ch, "documents.text_extracted") == 0
    usage = storage.list_usage(w.day().isoformat())
    assert len(usage) == 1 and usage[0]["requests"] == 1 and Decimal(usage[0]["spent_usd"]) > 0


def test_misconfigured_returns_message_and_stops(vhost, storage, run_worker, fake):
    url, _, _ = vhost
    fake.default = error(401, "authentication_error", "invalid x-api-key")
    w = run_worker(url)
    ch = channel(url)
    submit(storage, ch)
    assert wait_until(lambda: w.misconfigured is not None)
    assert wait_until(lambda: counts(ch, "documents.text_extracted") == 1)
    assert counts(ch, "documents.failed") == 0


def test_usage_limit_raised_during_checks_resumes_in_order(vhost, storage, run_worker, fake):
    url, _, _ = vhost
    fake.responses = [spend_limit(), spend_limit()]  # the first document's request, then the first check
    w = run_worker(url)
    ch = channel(url)
    a, b = submit(storage, ch), submit(storage, ch)
    assert collect(ch, "documents.fields_extracted", 2) == [a, b]
    assert w.paused is None and w.stopped is None
    assert counts(ch, "documents.failed") == 0


def test_usage_limit_persists_stops_after_checks(vhost, storage, run_worker, fake):
    url, _, _ = vhost
    fake.default = spend_limit()
    w = run_worker(url)
    ch = channel(url)
    submit(storage, ch), submit(storage, ch)
    assert wait_until(lambda: w.stopped is not None)
    assert w.stopped.startswith("llm_usage_limit: stopped after 3 checks")
    assert len(fake.requests) == 4  # one extraction request, three checks, nothing after
    time.sleep(0.5)
    assert len(fake.requests) == 4
    assert counts(ch, "documents.text_extracted") == 2 and counts(ch, "documents.failed") == 0


def test_daily_budget_pauses_without_api_calls_and_stops(vhost, storage, run_worker, fake):
    url, _, _ = vhost
    w = run_worker(url, budget_usd="0.00005")  # one fake request costs about $0.00009
    ch = channel(url)
    a, _ = submit(storage, ch), submit(storage, ch)  # the second one stays queued
    assert collect(ch, "documents.fields_extracted", 1) == [a]
    assert wait_until(lambda: w.stopped is not None)
    assert w.stopped.startswith("llm_daily_budget_reached: stopped after 3 checks")
    assert len(fake.requests) == 1  # the budget checks make no API calls
    assert counts(ch, "documents.text_extracted") == 1 and counts(ch, "documents.failed") == 0


def test_daily_budget_resumes_next_day(vhost, storage, run_worker, fake):
    url, _, _ = vhost
    w = run_worker(url, budget_usd="0.00005", interval=0.5, max_checks=20)
    ch = channel(url)
    a, b = submit(storage, ch), submit(storage, ch)
    assert collect(ch, "documents.fields_extracted", 1) == [a]
    assert wait_until(lambda: w.paused is not None)
    w.day.d += dt.timedelta(days=1)  # midnight UTC
    assert collect(ch, "documents.fields_extracted", 1) == [b]
    assert w.stopped is None


def test_check_returning_401_switches_to_misconfigured(vhost, storage, run_worker, fake):
    url, _, _ = vhost
    fake.responses = [spend_limit()]
    fake.default = error(401, "authentication_error", "invalid x-api-key")
    w = run_worker(url)
    ch = channel(url)
    submit(storage, ch)
    assert wait_until(lambda: w.misconfigured is not None)
    assert w.paused is None and counts(ch, "documents.text_extracted") == 1


def test_unroutable_output_is_not_acked(vhost, storage, run_worker):
    url, _, _ = vhost
    run_worker(url)
    ch = channel(url)
    ch.queue_delete("documents.fields_extracted")
    doc = submit(storage, ch)
    time.sleep(2)
    assert counts(ch, "documents.text_extracted") == 1
    ch.queue_declare("documents.fields_extracted", durable=True)
    assert collect(ch, "documents.fields_extracted", 1, timeout=15) == [doc]


def test_reconnects_after_connection_loss(vhost, storage, run_worker):
    url, mgmt, name = vhost
    w = run_worker(url)
    conns = []
    assert wait_until(
        lambda: bool(conns.extend(c for c in mgmt.call("GET", "/connections") if c.get("vhost") == name) or conns)
    )
    for c in conns:
        mgmt.call("DELETE", "/connections/" + urllib.parse.quote(c["name"], safe=""))
    time.sleep(1)
    assert w.connected.wait(10)
    ch = channel(url)
    doc = submit(storage, ch)
    assert collect(ch, "documents.fields_extracted", 1, timeout=15) == [doc]


def test_invalid_message_is_dead_lettered(vhost, run_worker):
    url, _, _ = vhost
    run_worker(url)
    ch = channel(url)
    ch.basic_publish("", "documents.text_extracted", b'{"not": "an envelope"}')
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        method, _, body = ch.basic_get("documents.text_extracted.dlq", auto_ack=True)
        if method:
            assert json.loads(body) == {"not": "an envelope"}
            return
        time.sleep(0.1)
    pytest.fail("not dead-lettered")


def test_storage_roundtrip(storage):
    key = f"test/{uuid.uuid4()}/extracted.json"
    storage.put_extracted(key, {"a": 1}, {"model": "claude-haiku-5-5"})
    stat = storage._client.stat_object("invoices", key)
    assert stat.content_type == "application/json"
    assert {k.lower(): v for k, v in stat.metadata.items()}["x-amz-meta-model"] == "claude-haiku-5-5"
    day = f"1990-01-{uuid.uuid4().int % 28 + 1:02d}"
    storage.put_usage(day, "x", {"instance": "x", "spent_usd": "1"})
    assert {"instance": "x", "spent_usd": "1"} in storage.list_usage(day)
    storage._client.remove_object("fields-extractor-usage", f"{day}/x.json")
    storage.check()


def test_storage_missing_object(storage):
    with pytest.raises(ObjectNotFound):
        storage.get_text(f"documents/{uuid.uuid4()}/layout.md")


def test_storage_missing_bucket(storage, monkeypatch):
    monkeypatch.setattr(storage_mod, "USAGE_BUCKET", f"missing-{uuid.uuid4().hex[:8]}")
    with pytest.raises(StorageUnavailable, match="does not exist"):
        storage.check()
