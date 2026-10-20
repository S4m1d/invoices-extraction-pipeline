"""Object storage access (see /specs/pipeline-contracts.md, Object storage layout).

Adapted from text-extractor (fields-extractor/specs/overview.md, Code sharing)."""

import io
import json

import urllib3
from minio import Minio
from minio.error import S3Error

from . import context

BUCKET = "invoices"
FAILURES_BUCKET = "fields-extractor-failures"
USAGE_BUCKET = "fields-extractor-usage"


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

    def get_text(self, key: str) -> str:
        return self.get(key).decode("utf-8")

    def put_extracted(self, key: str, data: dict, metadata: dict[str, str]) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self._put(BUCKET, key, body, "application/json", metadata)

    def put_diagnostics(self, key: str, diagnostics: dict) -> None:
        body = json.dumps(diagnostics, ensure_ascii=False, indent=2).encode()
        self._put(FAILURES_BUCKET, key, body, "application/json")

    def list_usage(self, day: str) -> list[dict]:
        """All instances' spend counters of the day (spec overview, Daily budget)."""
        try:
            out = []
            for obj in self._client.list_objects(USAGE_BUCKET, prefix=f"{day}/"):
                resp = self._client.get_object(USAGE_BUCKET, obj.object_name)
                try:
                    out.append(json.loads(resp.read()))
                finally:
                    resp.close()
                    resp.release_conn()
            return out
        except Exception as e:
            raise StorageUnavailable(f"read usage {USAGE_BUCKET}/{day}/: {type(e).__name__}: {e}") from e

    def put_usage(self, day: str, instance: str, counter: dict) -> None:
        body = json.dumps(counter).encode()
        self._put(USAGE_BUCKET, f"{day}/{instance}.json", body, "application/json")

    def check(self) -> None:
        """Raise StorageUnavailable unless both buckets exist."""
        for bucket in (BUCKET, FAILURES_BUCKET, USAGE_BUCKET):
            try:
                ok = self._client.bucket_exists(bucket)
            except Exception as e:
                raise StorageUnavailable(f"bucket {bucket}: {type(e).__name__}: {e}") from e
            if not ok:
                raise StorageUnavailable(f"bucket {bucket} does not exist")

    def _put(self, bucket: str, key: str, data: bytes, content_type: str, metadata: dict | None = None) -> None:
        try:
            self._client.put_object(
                bucket, key, io.BytesIO(data), len(data), content_type=content_type, metadata=metadata
            )
        except Exception as e:
            raise StorageUnavailable(f"put {bucket}/{key}: {type(e).__name__}: {e}") from e
