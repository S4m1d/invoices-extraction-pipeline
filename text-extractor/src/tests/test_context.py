import json
import logging

from text_extractor import context


def record(msg="hello", **extra):
    r = logging.getLogRecordFactory()("t", logging.INFO, __file__, 1, msg, None, None)
    for k, v in extra.items():
        setattr(r, k, v)
    return r


def test_json_line_with_ids_and_extras():
    with context.ids("corr-1", "doc-1"):
        line = context.JsonFormatter().format(record(pages=3))
    entry = json.loads(line)
    assert entry["msg"] == "hello" and entry["level"] == "INFO"
    assert entry["correlation_id"] == "corr-1" and entry["document_id"] == "doc-1"
    assert entry["pages"] == 3
    assert entry["time"].endswith("Z")


def test_ids_are_captured_when_the_record_is_created():
    with context.ids("corr-1", "doc-1"):
        r = record()
    entry = json.loads(context.JsonFormatter().format(r))  # formatted after the message is done
    assert entry["correlation_id"] == "corr-1" and entry["document_id"] == "doc-1"


def test_ids_are_reset_after_message():
    with context.ids("corr-1", "doc-1"):
        pass
    entry = json.loads(context.JsonFormatter().format(record()))
    assert "correlation_id" not in entry and "document_id" not in entry
