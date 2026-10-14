"""Queue message formats (see /specs/pipeline-contracts.md)."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

STAGE = "text-extractor"
INPUT_QUEUE = "documents.ingested"
OUTPUT_QUEUE = "documents.text_extracted"
FAILED_QUEUE = "documents.failed"

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_FIELDS = {"schema_version", "stage", "document_id", "correlation_id", "object_key", "created_at"}


class InvalidMessage(Exception):
    pass


@dataclass(frozen=True)
class Envelope:
    document_id: str
    correlation_id: str
    object_key: str


def parse(body: bytes) -> Envelope:
    """Validate an input message from the ingestor."""
    try:
        m = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise InvalidMessage(f"not JSON: {e}") from e
    if not isinstance(m, dict):
        raise InvalidMessage("not a JSON object")
    if set(m) != _FIELDS:
        raise InvalidMessage(f"fields {sorted(m)}, want {sorted(_FIELDS)}")
    if type(m["schema_version"]) is not int or m["schema_version"] != 1:
        raise InvalidMessage(f"schema_version {m['schema_version']!r}, want 1")
    if m["stage"] != "ingestor":
        raise InvalidMessage(f"stage {m['stage']!r}, want 'ingestor'")
    for k in ("document_id", "correlation_id"):
        if not isinstance(m[k], str) or not _UUID.match(m[k]):
            raise InvalidMessage(f"{k} {m[k]!r} is not a lowercase UUID")
    if m["object_key"] != original_key(m["document_id"]):
        raise InvalidMessage(f"object_key {m['object_key']!r} does not match document_id")
    if not isinstance(m["created_at"], str) or not _is_rfc3339(m["created_at"]):
        raise InvalidMessage(f"created_at {m['created_at']!r} is not RFC 3339")
    return Envelope(m["document_id"], m["correlation_id"], m["object_key"])


def original_key(document_id: str) -> str:
    return f"documents/{document_id}/original.pdf"


def layout_key(document_id: str) -> str:
    return f"documents/{document_id}/layout.md"


def output(env: Envelope, now: datetime) -> bytes:
    return _dump(
        {
            "schema_version": 1,
            "stage": STAGE,
            "document_id": env.document_id,
            "correlation_id": env.correlation_id,
            "object_key": layout_key(env.document_id),
            "created_at": timestamp(now),
        }
    )


def failure(env: Envelope, code: str, message: str, now: datetime) -> bytes:
    return _dump(
        {
            "schema_version": 1,
            "stage": STAGE,
            "document_id": env.document_id,
            "correlation_id": env.correlation_id,
            "error_code": code,
            "error_message": message,
            "created_at": timestamp(now),
        }
    )


def timestamp(now: datetime) -> str:
    return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _dump(m: dict) -> bytes:
    return json.dumps(m, ensure_ascii=False).encode()


def _is_rfc3339(s: str) -> bool:
    if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$", s):
        return False
    try:
        datetime.fromisoformat(s)
    except ValueError:
        return False
    return True
