import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from text_extractor import envelope

DOC = "0f8fad5b-d9cb-469f-a165-70867728950e"
CORR = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def msg(**overrides) -> dict:
    m = {
        "schema_version": 1,
        "stage": "ingestor",
        "document_id": DOC,
        "correlation_id": CORR,
        "object_key": f"documents/{DOC}/original.pdf",
        "created_at": "2026-10-14T12:00:00.123456Z",
    }
    m.update(overrides)
    return m


def test_valid():
    env = envelope.parse(json.dumps(msg()).encode())
    assert env == envelope.Envelope(DOC, CORR, f"documents/{DOC}/original.pdf")


@pytest.mark.parametrize("created_at", ["2026-10-14T12:00:00Z", "2026-10-14T12:00:00+02:00", "2026-10-14T12:00:00.5Z"])
def test_valid_timestamps(created_at):
    envelope.parse(json.dumps(msg(created_at=created_at)).encode())


def without(field):
    m = msg()
    del m[field]
    return m


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"\xff\xfe",
        b"[1, 2]",
        json.dumps(without("correlation_id")).encode(),
        json.dumps(msg(extra="x")).encode(),
        json.dumps(msg(schema_version=2)).encode(),
        json.dumps(msg(schema_version="1")).encode(),
        json.dumps(msg(schema_version=True)).encode(),
        json.dumps(msg(stage="text-extractor")).encode(),
        json.dumps(msg(document_id=DOC.upper(), object_key=f"documents/{DOC.upper()}/original.pdf")).encode(),
        json.dumps(msg(document_id="run-42")).encode(),
        json.dumps(msg(correlation_id="")).encode(),
        json.dumps(msg(object_key=f"documents/{CORR}/original.pdf")).encode(),
        json.dumps(msg(object_key=f"documents/{DOC}/layout.md")).encode(),
        json.dumps(msg(created_at="2026-10-14")).encode(),
        json.dumps(msg(created_at="2026-13-14T12:00:00Z")).encode(),
        json.dumps(msg(created_at=123)).encode(),
    ],
    ids=[
        "not json",
        "not utf-8",
        "not an object",
        "missing field",
        "extra field",
        "schema_version 2",
        "schema_version string",
        "schema_version bool",
        "wrong stage",
        "uppercase uuid",
        "not a uuid",
        "empty correlation_id",
        "key of other document",
        "key of other artifact",
        "date only",
        "invalid month",
        "created_at number",
    ],
)
def test_invalid(body):
    with pytest.raises(envelope.InvalidMessage):
        envelope.parse(body)


NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)
ENV = envelope.Envelope(DOC, CORR, f"documents/{DOC}/original.pdf")


def test_output():
    assert json.loads(envelope.output(ENV, NOW)) == {
        "schema_version": 1,
        "stage": "text-extractor",
        "document_id": DOC,
        "correlation_id": CORR,
        "object_key": f"documents/{DOC}/layout.md",
        "created_at": "2026-10-14T12:00:00Z",
    }


def test_failure():
    assert json.loads(envelope.failure(ENV, "no_text_layer", "no page contains text", NOW)) == {
        "schema_version": 1,
        "stage": "text-extractor",
        "document_id": DOC,
        "correlation_id": CORR,
        "error_code": "no_text_layer",
        "error_message": "no page contains text",
        "created_at": "2026-10-14T12:00:00Z",
    }


def test_timestamp_is_utc():
    assert envelope.timestamp(NOW.astimezone(timezone(timedelta(hours=3)))) == "2026-10-14T12:00:00Z"
