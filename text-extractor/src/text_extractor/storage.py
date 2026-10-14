"""Object storage access (see /specs/pipeline-contracts.md, Object storage layout)."""

import io
import json

import urllib3
from minio import Minio
from minio.error import S3Error

from . import context

BUCKET = "invoices"
FAILURES_BUCKET = "text-extractor-failures"


class ObjectNotFound(Exception):
    pass


class StorageUnavailable(Exception):
    pass


class _CorrelationPool(urllib3.PoolManager):
    """Adds X-Correlation-ID from the current message to every request."""

    def urlopen(self, method, url, redirect=True, **kw):
        if cid := context.correlation_id.get():
            headers = kw.get("headers")
            headers = urllib3.HTTPHeaderDict(headers or {})
            headers[context.HEADER] = cid
            kw["headers"] = headers
        return super().urlopen(method, url, redirect=redirect, **kw)


def _http_client() -> urllib3.PoolManager:
    # No retries here: transient failures are retried by the processor (spec, Errors).
    return _CorrelationPool(timeout=urllib3.Timeout(connect=5, read=10), retries=False)


class Storage:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, secure: bool):
        self._client = Minio(
            endpoint, access_key=access_key, secret_key=secret_key, secure=secure, http_client=_http_client()
        )

    def get(self, key: str) -> bytes:
        try:
            resp = self._client.get_object(BUCKET, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()
        except S3Error as e:
            if e.code == "NoSuchKey":
                raise ObjectNotFound(f"{BUCKET}/{key} does not exist") from e
            raise StorageUnavailable(f"get {BUCKET}/{key}: {e.code}: {e.message}") from e
        except Exception as e:
            raise StorageUnavailable(f"get {BUCKET}/{key}: {type(e).__name__}: {e}") from e

    def put_layout(self, key: str, markdown: str) -> None:
        self._put(BUCKET, key, markdown.encode(), "text/markdown; charset=utf-8")

    def put_diagnostics(self, key: str, diagnostics: dict) -> None:
        body = json.dumps(diagnostics, ensure_ascii=False, indent=2).encode()
        self._put(FAILURES_BUCKET, key, body, "application/json")

    def check(self) -> None:
        """Raise StorageUnavailable unless both buckets exist."""
        for bucket in (BUCKET, FAILURES_BUCKET):
            try:
                ok = self._client.bucket_exists(bucket)
            except Exception as e:
                raise StorageUnavailable(f"bucket {bucket}: {type(e).__name__}: {e}") from e
            if not ok:
                raise StorageUnavailable(f"bucket {bucket} does not exist")

    def _put(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        try:
            self._client.put_object(bucket, key, io.BytesIO(data), len(data), content_type=content_type)
        except Exception as e:
            raise StorageUnavailable(f"put {bucket}/{key}: {type(e).__name__}: {e}") from e
