"""Storage against a fake S3 endpoint (no MinIO needed)."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from text_extractor import context
from text_extractor.storage import ObjectNotFound, Storage, StorageUnavailable

CORR = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
NO_SUCH_KEY = (
    b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>NoSuchKey</Code>'
    b"<Message>The specified key does not exist.</Message><Key>k</Key><BucketName>invoices</BucketName>"
    b"<Resource>/invoices/k</Resource><RequestId>1</RequestId><HostId>1</HostId></Error>"
)


class FakeS3:
    def __init__(self):
        self.requests: list[tuple[str, str, str | None]] = []
        self.respond = lambda method, path: (200, b"%PDF-1.4 data")
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                fake.requests.append((self.command, self.path, self.headers.get(context.HEADER)))
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
    st._client._region_map["text-extractor-failures"] = "us-east-1"
    return st


def test_correlation_header_on_every_request(s3, storage):
    with context.ids(CORR, "doc"):
        storage.get("documents/d/original.pdf")
        storage.put_layout("documents/d/layout.md", "# md")
        storage.put_diagnostics("d.json", {"a": 1})
    assert len(s3.requests) == 3
    assert all(h == CORR for _, _, h in s3.requests), s3.requests


def test_get_returns_body(s3, storage):
    assert storage.get("documents/d/original.pdf") == b"%PDF-1.4 data"
    assert s3.requests[0][:2] == ("GET", "/invoices/documents/d/original.pdf")


def test_puts_go_to_the_right_buckets(s3, storage):
    storage.put_layout("documents/d/layout.md", "# md")
    storage.put_diagnostics("d.json", {"a": 1})
    assert [r[:2] for r in s3.requests] == [
        ("PUT", "/invoices/documents/d/layout.md"),
        ("PUT", "/text-extractor-failures/d.json"),
    ]


def test_no_such_key_is_object_not_found(s3, storage):
    s3.respond = lambda m, p: (404, NO_SUCH_KEY)
    with pytest.raises(ObjectNotFound):
        storage.get("documents/d/original.pdf")


@pytest.mark.parametrize("op", ["get", "put"])
def test_server_error_is_storage_unavailable(s3, storage, op):
    s3.respond = lambda m, p: (500, b"")
    with pytest.raises(StorageUnavailable):
        storage.get("k") if op == "get" else storage.put_layout("k", "x")
    assert len(s3.requests) == 1  # no hidden client-side retries


def test_connection_refused_is_storage_unavailable():
    st = Storage("127.0.0.1:1", "key", "secret", secure=False)
    st._client._region_map["invoices"] = "us-east-1"
    with pytest.raises(StorageUnavailable):
        st.get("k")


def test_check(s3, storage):
    storage.check()  # HEAD on both buckets -> 200
    assert [r[:2] for r in s3.requests] == [("HEAD", "/invoices"), ("HEAD", "/text-extractor-failures")]
    s3.respond = lambda m, p: (404, b"") if p.startswith("/text-extractor-failures") else (200, b"")
    with pytest.raises(StorageUnavailable, match="text-extractor-failures"):
        storage.check()
