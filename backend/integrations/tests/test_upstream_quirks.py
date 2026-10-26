"""Provider quirks applied by the direct-endpoint caller (backend.inference.upstream)."""

from __future__ import annotations

from backend.inference import upstream


def test_anthropic_gets_version_header_and_no_sampling_params():
    headers: dict[str, str] = {}
    body = {"model": "claude-haiku-5-5", "temperature": 0.3, "top_p": 0.9, "top_k": 5, "max_tokens": 10}
    out = upstream._provider_quirks("api.anthropic.com", body, headers)
    assert headers["anthropic-version"] == upstream.ANTHROPIC_VERSION
    assert out == {"model": "claude-haiku-5-5", "max_tokens": 10}
    assert body["temperature"] == 0.3


def test_anthropic_get_without_body():
    headers: dict[str, str] = {}
    assert upstream._provider_quirks("api.anthropic.com", None, headers) is None
    assert "anthropic-version" in headers


def test_other_hosts_untouched():
    headers: dict[str, str] = {}
    body = {"temperature": 0.3}
    assert upstream._provider_quirks("api.openai.com", body, headers) is body
    assert headers == {}
