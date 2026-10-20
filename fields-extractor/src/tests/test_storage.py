"""Storage against a fake S3 endpoint (no MinIO needed)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fields_extractor import context
from fields_extractor.storage import ObjectNotFound, Storage, StorageUnavailable

CORR = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
NO_SUCH_KEY = (
    b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>NoSuchKey</Code>'
    b"<Message>The specified key does not exist.</Message><Key>k</Key><BucketName>invoices</BucketName>"
    b"<Resource>/invoices/k</Resource><RequestId>1</RequestId><HostId>1</HostId></Error>"
)


class FakeS3:
    def __init__(self):
        self.requests: list[tuple[str, str, str | None]] = []
        self.last_headers: dict[str, str] = {}
        self.respond = lambda method, path: (200, b"%PDF-1.4 data")
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                fake.requests.append((self.command, self.path, self.headers.get(context.HEADER)))
                fake.last_headers = {k.lower(): v for k, v in self.headers.items()}
                status, body = fake.respond(self.command, self.path)
                if status is None:
                    return  # hang: never answer
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("ETag", '"abc"')
                if body.startswith(b"<?xml"):
                    self.send_header("Content-Type", "application/xml")
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_PUT = do_HEAD = _handle

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def endpoint(self):
        return f"127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def s3():
    fake = FakeS3()
    yield fake
    fake.server.shutdown()


@pytest.fixture
def storage(s3):
    st = Storage(s3.endpoint, "key", "secret", secure=False)
    st._client._region_map["invoices"] = "us-east-1"  # skip the bucket location lookup
    for bucket in ("fields-extractor-failures", "fields-extractor-usage"):
        st._client._region_map[bucket] = "us-east-1"
    return st


def test_correlation_header_on_every_request(s3, storage):
    with context.ids(CORR, "doc"):
        storage.get_text("documents/d/layout.md")
        storage.put_extracted("documents/d/extracted.json", {"a": 1}, {"model": "m"})
        storage.put_diagnostics("d.json", {"a": 1})
        storage.put_usage("2026-10-15", "host", {"spent_usd": "0"})
    assert len(s3.requests) == 4
    assert all(h == CORR for _, _, h in s3.requests), s3.requests


def test_get_text(s3, storage):
    s3.respond = lambda m, p: (200, "## Page 1 — £".encode())
    assert storage.get_text("documents/d/layout.md") == "## Page 1 — £"
    assert s3.requests[0][:2] == ("GET", "/invoices/documents/d/layout.md")


def test_puts_go_to_the_right_buckets(s3, storage):
    storage.put_extracted("documents/d/extracted.json", {"a": 1}, {"model": "m"})
    storage.put_diagnostics("d.json", {"a": 1})
    storage.put_usage("2026-10-15", "host", {"spent_usd": "0"})
    assert [r[:2] for r in s3.requests] == [
        ("PUT", "/invoices/documents/d/extracted.json"),
        ("PUT", "/fields-extractor-failures/d.json"),
        ("PUT", "/fields-extractor-usage/2026-10-15/host.json"),
    ]


def test_extracted_metadata_header(s3, storage):
    storage.put_extracted("documents/d/extracted.json", {"a": 1}, {"model": "claude-haiku-5-5", "prompt-version": "v1"})
    assert s3.last_headers.get("x-amz-meta-model") == "claude-haiku-5-5"
    assert s3.last_headers.get("x-amz-meta-prompt-version") == "v1"
    assert s3.last_headers.get("content-type") == "application/json"


def test_no_such_key_is_object_not_found(s3, storage):
    s3.respond = lambda m, p: (404, NO_SUCH_KEY)
    with pytest.raises(ObjectNotFound):
        storage.get_text("documents/d/layout.md")


@pytest.mark.parametrize("op", ["get", "put"])
def test_server_error_is_storage_unavailable(s3, storage, op):
    s3.respond = lambda m, p: (500, b"")
    with pytest.raises(StorageUnavailable):
        storage.get_text("k") if op == "get" else storage.put_extracted("k", {}, {})
    assert len(s3.requests) == 1  # no hidden client-side retries


def test_list_usage(s3, storage):
    listing = (
        b'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult><Name>fields-extractor-usage</Name>'
        b"<Prefix>2026-10-15/</Prefix><KeyCount>2</KeyCount><MaxKeys>1000</MaxKeys><IsTruncated>false</IsTruncated>"
        b"<Contents><Key>2026-10-15/a.json</Key><Size>10</Size></Contents>"
        b"<Contents><Key>2026-10-15/b.json</Key><Size>10</Size></Contents></ListBucketResult>"
    )

    def respond(method, path):
        if "list-type" in path or path.rstrip("/").endswith("fields-extractor-usage") or "prefix=" in path:
            return 200, listing
        return 200, json.dumps({"instance": path.split("/")[-1][0]}).encode()

    s3.respond = respond
    assert storage.list_usage("2026-10-15") == [{"instance": "a"}, {"instance": "b"}]


def test_list_usage_failure(s3, storage):
    s3.respond = lambda m, p: (500, b"")
    with pytest.raises(StorageUnavailable):
        storage.list_usage("2026-10-15")


def test_connection_refused_is_storage_unavailable():
    st = Storage("127.0.0.1:1", "key", "secret", secure=False)
    st._client._region_map["invoices"] = "us-east-1"
    with pytest.raises(StorageUnavailable):
        st.get_text("k")


def test_check_covers_three_buckets(s3, storage):
    storage.check()
    assert [r[:2] for r in s3.requests] == [
        ("HEAD", "/invoices"),
        ("HEAD", "/fields-extractor-failures"),
        ("HEAD", "/fields-extractor-usage"),
    ]
    s3.respond = lambda m, p: (404, b"") if p.startswith("/fields-extractor-usage") else (200, b"")
    with pytest.raises(StorageUnavailable, match="fields-extractor-usage"):
        storage.check()
