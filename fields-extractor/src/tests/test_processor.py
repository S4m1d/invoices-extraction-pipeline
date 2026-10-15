import json
import logging
from datetime import UTC, datetime

import pytest

from fields_extractor import context
from fields_extractor.llm import (
    PROMPT_VERSION,
    Attempt,
    LlmBadRequest,
    LlmDailyBudget,
    LlmMisconfigured,
    LlmUnavailable,
    LlmUsageLimit,
)
from fields_extractor.processor import Outcome, Processor
from fields_extractor.storage import ObjectNotFound, StorageUnavailable
from tests.spec_examples import extraction_example

DOC = "0f8fad5b-d9cb-469f-a165-70867728950e"
CORR = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
EXAMPLE = extraction_example()
GOOD = json.dumps(EXAMPLE)
MARKDOWN = "## Page 1 of 1\n\n```text\nsecret invoice content\n```\n"


def message(**overrides) -> bytes:
    m = {
        "schema_version": 1,
        "stage": "text-extractor",
        "document_id": DOC,
        "correlation_id": CORR,
        "object_key": f"documents/{DOC}/layout.md",
        "created_at": "2026-10-15T11:59:00Z",
    }
    m.update(overrides)
    return json.dumps(m).encode()


def attempt(output=GOOD, stop_reason="end_turn", **kw) -> Attempt:
    return Attempt(
        output=output, stop_reason=stop_reason, model="claude-haiku-5-5", input_tokens=1000, output_tokens=300, **kw
    )


class FakeStorage:
    def __init__(self, calls):
        self.calls = calls
        self.objects, self.metadata = {}, {}
        self.fail: dict[str, list[Exception]] = {}

    def _op(self, op):
        self.calls.append(op)
        if self.fail.get(op):
            raise self.fail[op].pop(0)

    def get_text(self, key):
        self._op("get")
        return MARKDOWN

    def put_extracted(self, key, data, metadata):
        self._op("put_extracted")
        self.objects[key], self.metadata[key] = data, metadata

    def put_diagnostics(self, key, diagnostics):
        self._op("put_diagnostics")
        self.objects["failures/" + key] = diagnostics


class FakeProvider:
    model = "claude-haiku-5-5"

    def __init__(self, calls):
        self.calls = calls
        self.results: list = []  # Attempt or Exception, one per call
        self.previous: list = []

    def extract(self, markdown, previous=None, *, deadline):
        self.calls.append("llm")
        self.previous.append(previous)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeBudget:
    def __init__(self, calls):
        self.calls = calls
        self.fail: list[Exception] = []

    def ensure_available(self):
        self.calls.append("budget")
        if self.fail:
            raise self.fail.pop(0)


class FakePublisher:
    def __init__(self):
        self.published = []
        self.error = None

    def publish(self, queue, body, correlation_id):
        if self.error:
            raise self.error
        self.published.append((queue, json.loads(body), correlation_id))


@pytest.fixture
def env():
    calls = []
    st, prov, bud, pub, sleeps = FakeStorage(calls), FakeProvider(calls), FakeBudget(calls), FakePublisher(), []
    p = Processor(st, pub, prov, budget=bud, sleep=sleeps.append, now=lambda: NOW)
    return p, st, prov, bud, pub, calls, sleeps


def only_failure(pub) -> dict:
    assert len(pub.published) == 1
    queue, body, corr = pub.published[0]
    assert queue == "documents.failed" and corr == CORR
    assert body["stage"] == "fields-extractor" and body["document_id"] == DOC and body["correlation_id"] == CORR
    return body


def test_happy_path(env):
    p, st, prov, _, pub, calls, _ = env
    prov.results = [attempt()]
    assert p.handle(message()) is Outcome.ACK
    assert calls == ["get", "budget", "llm", "put_extracted"]
    key = f"documents/{DOC}/extracted.json"
    assert st.objects[key] == EXAMPLE
    assert st.metadata[key] == {
        "model": "claude-haiku-5-5",
        "prompt-version": PROMPT_VERSION,
        "input-tokens": "1000",
        "output-tokens": "300",
    }
    assert pub.published == [
        (
            "documents.fields_extracted",
            {
                "schema_version": 1,
                "stage": "fields-extractor",
                "document_id": DOC,
                "correlation_id": CORR,
                "object_key": key,
                "created_at": "2026-10-15T12:00:00Z",
            },
            CORR,
        )
    ]


MISMATCHED = json.dumps(
    {
        **EXAMPLE,
        "lines": [
            *EXAMPLE["lines"],
            {
                "line_no": 4,
                "description": "Shipping & Handling",
                "quantity": None,
                "unit_price": None,
                "amount": "34.94",
            },
        ],
    }
)


def test_line_sum_mismatch_retries_and_uses_fixed_output(env):
    p, st, prov, _, pub, calls, _ = env
    prov.results = [attempt(MISMATCHED), attempt()]
    assert p.handle(message()) is Outcome.ACK
    assert calls == ["get", "budget", "llm", "budget", "llm", "put_extracted"]
    feedback = prov.previous[1].validation_errors
    assert feedback == [
        "the line amounts sum to 808.99 but the printed subtotal is 774.05; check for totals-area "
        "rows (discount, shipping, tax, deposit) listed as line items and for missing line items"
    ]
    assert st.objects[f"documents/{DOC}/extracted.json"] == EXAMPLE
    assert "checks-failed" not in st.metadata[f"documents/{DOC}/extracted.json"]
    assert pub.published[0][0] == "documents.fields_extracted"


def test_line_sum_mismatch_twice_is_kept_with_warning(env, caplog):
    p, st, prov, _, pub, _, _ = env
    prov.results = [attempt(MISMATCHED), attempt(MISMATCHED)]
    with caplog.at_level(logging.WARNING):
        assert p.handle(message()) is Outcome.ACK
    key = f"documents/{DOC}/extracted.json"
    assert len(st.objects[key]["lines"]) == 4
    assert st.metadata[key]["checks-failed"] == "lines_sum_to_subtotal"  # subtotal + tax = total still holds
    assert pub.published[0][0] == "documents.fields_extracted"  # never failed for this
    assert "lines_sum_to_subtotal" in [getattr(r, "check", None) for r in caplog.records]


def test_line_sum_retry_with_invalid_output_keeps_first(env):
    p, st, prov, _, pub, _, _ = env
    prov.results = [attempt(MISMATCHED), attempt("not json")]
    assert p.handle(message()) is Outcome.ACK
    assert len(st.objects[f"documents/{DOC}/extracted.json"]["lines"]) == 4
    assert pub.published[0][0] == "documents.fields_extracted"


def test_at_most_one_retry(env):
    p, st, prov, _, pub, calls, _ = env
    prov.results = [attempt("not json"), attempt(MISMATCHED)]  # invalid, then valid but not adding up
    assert p.handle(message()) is Outcome.ACK
    assert calls.count("llm") == 2
    assert len(st.objects[f"documents/{DOC}/extracted.json"]["lines"]) == 4


def test_no_line_sum_check_without_subtotal(env):
    p, st, prov, _, pub, calls, _ = env
    prov.results = [attempt(json.dumps({**json.loads(MISMATCHED), "subtotal": None}))]
    assert p.handle(message()) is Outcome.ACK
    assert calls.count("llm") == 1


def test_plausibility_total_check_is_metadata_only(env, caplog):
    p, st, prov, _, pub, calls, _ = env
    prov.results = [attempt(json.dumps({**EXAMPLE, "grand_total": "900.00"}))]
    with caplog.at_level(logging.WARNING):
        assert p.handle(message()) is Outcome.ACK
    assert calls.count("llm") == 1
    assert st.metadata[f"documents/{DOC}/extracted.json"]["checks-failed"] == "subtotal_plus_tax_is_total"
    assert pub.published[0][0] == "documents.fields_extracted"


def test_feedback_retry_succeeds(env):
    p, st, prov, _, pub, calls, _ = env
    prov.results = [attempt('{"currency": "gbp"}'), attempt()]
    assert p.handle(message()) is Outcome.ACK
    assert calls == ["get", "budget", "llm", "budget", "llm", "put_extracted"]
    assert prov.previous[0] is None and prov.previous[1].validation_errors
    meta = st.metadata[f"documents/{DOC}/extracted.json"]
    assert meta["input-tokens"] == "2000" and meta["output-tokens"] == "600"
    assert pub.published[0][0] == "documents.fields_extracted"


def test_invalid_twice(env):
    p, st, prov, _, pub, calls, _ = env
    prov.results = [attempt("nope"), attempt('{"currency": "gbp"}')]
    assert p.handle(message()) is Outcome.ACK
    assert only_failure(pub)["error_code"] == "llm_invalid_output"
    diag = st.objects[f"failures/{DOC}.json"]
    assert diag["error_code"] == "llm_invalid_output" and diag["model"] == "claude-haiku-5-5"
    assert diag["prompt_version"] == PROMPT_VERSION and diag["input"] == {
        "bucket": "invoices",
        "key": f"documents/{DOC}/layout.md",
        "bytes": len(MARKDOWN.encode()),
    }
    assert [a["output"] for a in diag["attempts"]] == ["nope", '{"currency": "gbp"}']
    assert all(a["validation_errors"] for a in diag["attempts"])
    assert diag["attempts"][0]["input_tokens"] == 1000 and diag["created_at"] == "2026-10-15T12:00:00Z"


@pytest.mark.parametrize(
    "result,code",
    [
        (attempt("", "refusal", refusal_category="cyber"), "llm_refused"),
        (attempt("{", "max_tokens"), "llm_output_truncated"),
        (LlmBadRequest("400 rejected"), "llm_bad_request"),
    ],
)
def test_llm_output_failures_write_diagnostics(env, result, code):
    p, st, prov, _, pub, _, _ = env
    prov.results = [result]
    assert p.handle(message()) is Outcome.ACK
    assert only_failure(pub)["error_code"] == code
    assert st.objects[f"failures/{DOC}.json"]["error_code"] == code


@pytest.mark.parametrize(
    "setup,code",
    [
        (lambda st, prov: prov.results.append(LlmUnavailable("down")), "llm_unavailable"),
        (lambda st, prov: st.fail.setdefault("get", []).append(ObjectNotFound("missing")), "object_not_found"),
        (lambda st, prov: st.fail.setdefault("get", []).extend([StorageUnavailable("x")] * 3), "storage_unavailable"),
        (lambda st, prov: prov.results.append(RuntimeError("boom")), "internal_error"),
    ],
)
def test_other_failures_have_no_diagnostics(env, setup, code):
    p, st, prov, _, pub, _, _ = env
    setup(st, prov)
    assert p.handle(message()) is Outcome.ACK
    assert only_failure(pub)["error_code"] == code
    assert f"failures/{DOC}.json" not in st.objects


def test_diagnostics_write_failure(env):
    p, st, prov, _, pub, _, sleeps = env
    prov.results = [attempt("", "refusal")]
    st.fail["put_diagnostics"] = [StorageUnavailable("down")] * 3
    p.handle(message())
    failure = only_failure(pub)
    assert failure["error_code"] == "storage_unavailable" and "llm_refused" in failure["error_message"]
    assert sleeps == [1.0, 5.0]


def test_storage_retries(env):
    p, st, prov, _, pub, calls, sleeps = env
    prov.results = [attempt()]
    st.fail["get"] = [StorageUnavailable("x"), StorageUnavailable("x")]
    p.handle(message())
    assert sleeps == [1.0, 5.0] and calls[:3] == ["get", "get", "get"]
    assert pub.published[0][0] == "documents.fields_extracted"


@pytest.mark.parametrize("exc", [LlmMisconfigured("401"), LlmUsageLimit("limit")])
def test_worker_level_errors_propagate(env, exc):
    p, st, prov, _, pub, _, _ = env
    prov.results = [exc]
    with pytest.raises(type(exc)):
        p.handle(message())
    assert pub.published == [] and not st.objects


def test_budget_checked_before_each_attempt(env):
    p, _, prov, bud, pub, calls, _ = env
    prov.results = [attempt("nope")]
    calls_before = list(calls)
    bud_calls = []

    def ensure():
        bud_calls.append(1)
        calls.append("budget")
        if len(bud_calls) == 2:
            raise LlmDailyBudget("daily budget reached")

    bud.ensure_available = ensure
    with pytest.raises(LlmDailyBudget):
        p.handle(message())
    assert calls[len(calls_before) :] == ["get", "budget", "llm", "budget"]
    assert pub.published == []


def test_unreadable_budget_fails_as_storage(env):
    p, _, _, bud, pub, _, _ = env
    bud.fail = [StorageUnavailable("x")] * 3
    p.handle(message())
    assert only_failure(pub)["error_code"] == "storage_unavailable"


def test_publisher_error_propagates(env):
    p, _, prov, _, pub, _, _ = env
    prov.results = [attempt()]
    pub.error = ConnectionError("broker gone")
    with pytest.raises(ConnectionError):
        p.handle(message())


def test_invalid_envelope(env):
    p, _, _, _, pub, calls, _ = env
    assert p.handle(message(stage="ingestor")) is Outcome.REJECT
    assert p.handle(b"{}") is Outcome.REJECT
    assert calls == [] and pub.published == []


def test_logs(env, caplog):
    p, _, prov, _, _, _, _ = env
    prov.results = [attempt('{"currency": "gbp"}'), attempt()]
    with caplog.at_level(logging.DEBUG):
        p.handle(message())
    records = [r for r in caplog.records if r.name.startswith("fields_extractor")]
    for r in records:
        line = context.JsonFormatter().format(r)
        entry = json.loads(line)
        assert entry["correlation_id"] == CORR and entry["document_id"] == DOC
        assert "secret invoice content" not in line and "Willowmere" not in line
    summary = [r for r in records if r.getMessage() == "message processed"][0]
    assert (summary.result, summary.model, summary.prompt_version) == ("ok", "claude-haiku-5-5", PROMPT_VERSION)
    assert (summary.input_tokens, summary.output_tokens, summary.lines, summary.attempts) == (2000, 600, 3, 2)
