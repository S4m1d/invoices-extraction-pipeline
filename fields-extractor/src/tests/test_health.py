import json
import logging
import urllib.error
import urllib.request

import pytest

from fields_extractor import health


def down():
    raise ConnectionError("down")


def ok():
    pass


@pytest.fixture
def serve():
    servers = []

    def start(checks):
        server = health.make_server("127.0.0.1", 0, checks)
        health.serve_in_background(server)
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for s in servers:
        s.shutdown()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.mark.parametrize(
    ("rmq", "minio", "status", "failing"),
    [
        (ok, ok, 200, None),
        (down, ok, 503, ["rabbitmq"]),
        (ok, down, 503, ["minio"]),
        (down, down, 503, ["rabbitmq", "minio"]),
    ],
)
def test_healthz(serve, rmq, minio, status, failing):
    base = serve({"rabbitmq": rmq, "minio": minio})
    code, body = get(base + "/healthz")
    assert code == status
    expected = {"status": "unavailable", "failing": failing, "errors": {name: "down" for name in failing or []}}
    assert body == ({"status": "ok"} if failing is None else expected)


def test_unknown_path(serve):
    code, body = get(serve({"rabbitmq": ok}) + "/nope")
    assert code == 404 and body["error"]["code"] == "not_found"


def test_logs_only_on_change(serve, caplog):
    state = {"down": True}

    def llm():
        if state["down"]:
            raise RuntimeError("llm_usage_limit (paused): spend limit reached")

    base = serve({"llm": llm})
    with caplog.at_level(logging.INFO, logger="fields_extractor.health"):
        for _ in range(5):
            get(base + "/healthz")
        state["down"] = False
        for _ in range(3):
            get(base + "/healthz")
    msgs = [r.getMessage() for r in caplog.records if r.name == "fields_extractor.health"]
    assert msgs == ["health check failed", "health check ok again"]
    failed = [r for r in caplog.records if r.getMessage() == "health check failed"][0]
    assert failed.failing == ["llm"] and "spend limit" in failed.errors["llm"]
