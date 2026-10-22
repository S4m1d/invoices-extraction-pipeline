import pytest

from fields_extractor.llm import PROMPT_VERSION, NotImplementedProvider, load_prompt, make_provider


@pytest.mark.parametrize("name", ["openai", "local"])
def test_stub_providers_fail(name):
    with pytest.raises(NotImplementedProvider, match=f"{name!r} is not implemented"):
        make_provider(name, model="m", effort="low", max_retries=0)


def test_unknown_provider_fails():
    with pytest.raises(NotImplementedProvider, match="unknown"):
        make_provider("other")


@pytest.mark.parametrize("version", ["v1", "v2", "v3", "v4"])
def test_prompt_versions_load(version):
    prompt = load_prompt(version)
    assert "top-level line items" in prompt and "CAD" in prompt


def test_prompt_in_use_is_v4():
    assert PROMPT_VERSION == "v4"
    prompt = load_prompt()
    assert "amount due" in prompt and "totals area are never line items" in prompt
    assert "number only, without its unit" in prompt
    assert "## Page i of N (OCR)" in prompt


def test_anthropic_provider_builds(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    p = make_provider("anthropic", model="claude-haiku-5-5", effort="low", max_retries=1)
    assert p.model == "claude-haiku-5-5" and p.client.max_retries == 0  # retries are ours, not the SDK's
