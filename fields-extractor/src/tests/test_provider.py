"""Anthropic provider against tests/fake_anthropic.py (real SDK, fake HTTP API)."""

import json
import time

import anthropic
import pytest

from fields_extractor import context
from fields_extractor.llm import Attempt, LlmBadRequest, LlmMisconfigured, LlmUnavailable, LlmUsageLimit, load_prompt
from fields_extractor.llm import anthropic_provider as ap
from fields_extractor.schema import OUTPUT_SCHEMA
from tests.fake_anthropic import EXAMPLE_OUTPUT, FakeAnthropic, error, no_credit, refusal, spend_cap, spend_limit, text

CORR = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


@pytest.fixture
def fake():
    f = FakeAnthropic().start()
    yield f
    f.stop()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def make(fake):
    def build(max_retries=4, **kw):
        sleeps, usage = [], []
        clock = kw.pop("clock", None) or Clock()

        def sleep(s):
            sleeps.append(s)
            clock.t += s

        client = anthropic.Anthropic(api_key="test", base_url=fake.url, max_retries=0)
        p = ap.AnthropicProvider(
            "claude-haiku-5-5",
            "low",
            max_retries,
            client=client,
            sleep=sleep,
            clock=clock,
            on_usage=lambda *a: usage.append(a),
        )
        p.sleeps, p.usage, p.clock_ = sleeps, usage, clock
        return p

    return build


def deadline(p, seconds=1200):
    return p.clock_.t + seconds


def test_request_shape(fake, make):
    p = make()
    fake.responses = [text(EXAMPLE_OUTPUT)]
    with context.ids(CORR, "doc"):
        p.extract("# md", deadline=deadline(p))
    req = fake.requests[0]
    body = req["body"]
    assert body["model"] == "claude-haiku-5-5"
    assert body["max_tokens"] == 64000 and body["stream"] is True
    assert body["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
    assert body["system"] == load_prompt()
    assert body["messages"] == [{"role": "user", "content": "<invoice>\n# md\n</invoice>"}]
    assert not {"temperature", "top_p", "top_k"} & set(body)
    assert req["headers"]["x-correlation-id"] == CORR


def test_feedback_request_contains_previous_output_and_errors(fake, make):
    p = make()
    fake.responses = [text(EXAMPLE_OUTPUT)]
    prev = Attempt(
        output='{"bad": 1}', stop_reason="end_turn", model="m", validation_errors=["currency: x", "lines: y"]
    )
    p.extract("# md", prev, deadline=deadline(p))
    msgs = fake.requests[0]["body"]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"] == '{"bad": 1}'
    assert "- currency: x\n- lines: y" in msgs[2]["content"]


def test_reads_attempt(fake, make):
    p = make()
    fake.responses = [text(EXAMPLE_OUTPUT)]
    a = p.extract("md", deadline=deadline(p))
    assert json.loads(a.output) == EXAMPLE_OUTPUT
    assert (a.stop_reason, a.model, a.input_tokens) == ("end_turn", "claude-haiku-5-5", 100)
    assert a.output_tokens > 0
    assert p.usage == [("claude-haiku-5-5", 100, a.output_tokens)]


def test_text_after_thinking_block(fake, make, monkeypatch):
    p = make()
    fake.responses = [text(EXAMPLE_OUTPUT)]

    class Block:
        def __init__(self, type_, text=""):
            self.type, self.text = type_, text

    real_call = p.client.messages.stream

    def stream(**kw):
        cm = real_call(**kw)
        msg_enter = cm.__enter__

        def enter():
            s = msg_enter()
            final = s.get_final_message()
            final.content = [Block("thinking"), *final.content]
            s.get_final_message = lambda: final
            return s

        cm.__enter__ = enter
        return cm

    monkeypatch.setattr(p.client.messages, "stream", stream)
    assert json.loads(p.extract("md", deadline=deadline(p)).output) == EXAMPLE_OUTPUT


@pytest.mark.parametrize(
    "resp,stop,category",
    [(refusal("cyber"), "refusal", "cyber"), (text("{", stop_reason="max_tokens"), "max_tokens", None)],
)
def test_stop_reasons(fake, make, resp, stop, category):
    p = make()
    fake.responses = [resp]
    a = p.extract("md", deadline=deadline(p))
    assert (a.stop_reason, a.refusal_category) == (stop, category)


@pytest.mark.parametrize(
    "status,exc",
    [
        (401, LlmMisconfigured),
        (403, LlmMisconfigured),
        (404, LlmMisconfigured),
        (400, LlmBadRequest),
        (413, LlmBadRequest),
        (422, LlmBadRequest),
    ],
)
def test_non_transient_status(fake, make, status, exc):
    p = make()
    fake.responses = [error(status, message="nope")]
    with pytest.raises(exc):
        p.extract("md", deadline=deadline(p))
    assert len(fake.requests) == 1 and p.sleeps == []


@pytest.mark.parametrize("status", [408, 409, 429, 500, 503, 529])
def test_transient_status_is_retried(fake, make, status):
    p = make()
    fake.responses = [error(status, "overloaded_error" if status == 529 else "api_error"), text(EXAMPLE_OUTPUT)]
    assert p.extract("md", deadline=deadline(p)).stop_reason == "end_turn"
    assert len(fake.requests) == 2


def test_retry_after_is_honoured(fake, make):
    p = make()
    fake.responses = [error(429, "rate_limit_error", headers={"retry-after": "7"}), text(EXAMPLE_OUTPUT)]
    p.extract("md", deadline=deadline(p))
    assert p.sleeps == [7.0]


def test_exponential_backoff_capped(fake, make, monkeypatch):
    monkeypatch.setattr(ap.random, "uniform", lambda a, b: 0.0)
    p = make(max_retries=8)
    fake.responses = [error(500)] * 8 + [text(EXAMPLE_OUTPUT)]
    p.extract("md", deadline=deadline(p, 10_000))
    assert p.sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


def test_retry_limit(fake, make):
    p = make(max_retries=2)
    fake.responses = [error(500)] * 5
    with pytest.raises(LlmUnavailable, match="after 3 attempts"):
        p.extract("md", deadline=deadline(p))
    assert len(fake.requests) == 3


def test_no_retry_past_deadline(fake, make):
    p = make()
    fake.responses = [error(429, "rate_limit_error", headers={"retry-after": "30"})]
    with pytest.raises(LlmUnavailable, match="deadline"):
        p.extract("md", deadline=deadline(p, 20))
    assert len(fake.requests) == 1 and p.sleeps == []


def test_expired_deadline(fake, make):
    p = make()
    with pytest.raises(LlmUnavailable, match="deadline passed"):
        p.extract("md", deadline=p.clock_.t)
    assert fake.requests == []


def test_connection_refused_is_retried_then_unavailable():
    clock = Clock()
    client = anthropic.Anthropic(api_key="t", base_url="http://127.0.0.1:1", max_retries=0)
    p = ap.AnthropicProvider("claude-haiku-5-5", "low", 1, client=client, sleep=lambda s: None, clock=clock)
    with pytest.raises(LlmUnavailable, match="APIConnectionError"):
        p.extract("md", deadline=clock.t + 60)


@pytest.mark.parametrize(
    "resp",
    [spend_cap(), spend_limit(), spend_limit(workspace=True), no_credit(400), no_credit(402)],
    ids=["tier cap", "org limit", "workspace limit", "credit 400", "credit 402"],
)
def test_usage_limit_after_one_request(fake, make, resp):
    p = make()
    fake.responses = [resp]
    with pytest.raises(LlmUsageLimit):
        p.extract("md", deadline=deadline(p))
    assert len(fake.requests) == 1 and p.sleeps == []


def test_ordinary_errors_are_not_usage_limits(fake, make):
    p = make()
    fake.responses = [error(400, "invalid_request_error", "messages: field required")]
    with pytest.raises(LlmBadRequest):
        p.extract("md", deadline=deadline(p))
    fake.responses = [
        error(
            429,
            "rate_limit_error",
            "Number of request tokens has exceeded your per-minute rate limit",
            headers={"retry-after": "1"},
        ),
        text(EXAMPLE_OUTPUT),
    ]
    assert p.extract("md", deadline=deadline(p)).stop_reason == "end_turn"


def test_check_available(fake, make):
    p = make()
    fake.responses = [spend_limit()]
    assert p.check_available() is False
    fake.responses = [error(529, "overloaded_error")]
    assert p.check_available() is False
    fake.responses = [error(401)]
    with pytest.raises(LlmMisconfigured):
        p.check_available()
    fake.default = text(EXAMPLE_OUTPUT)
    fake.responses = []
    n = len(p.usage)
    assert p.check_available() is True
    body = fake.requests[-1]["body"]
    assert body["max_tokens"] == 16 and not body.get("stream")
    assert len(p.usage) == n + 1  # the check's tokens count toward the daily budget


def test_check_unexpected_error_resumes(fake, make):
    p = make()
    fake.responses = [error(400, "invalid_request_error", "something else")]
    assert p.check_available() is True


def test_no_sdk_retries_by_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert ap.AnthropicProvider("claude-haiku-5-5", "low", 4).client.max_retries == 0


def test_time_module_untouched():
    assert ap.time.monotonic is time.monotonic  # provider uses an injectable clock, not patched globals
