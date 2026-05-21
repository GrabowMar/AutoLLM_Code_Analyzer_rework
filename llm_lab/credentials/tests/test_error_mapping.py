"""Tests for the OpenRouterError friendly 401 mapping."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from llm_lab.generation.services.openrouter_client import OpenRouterClient
from llm_lab.generation.services.openrouter_client import OpenRouterError


def _fake_response(status_code: int, body: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    return resp


def test_401_user_not_found_maps_to_friendly_error():
    client = OpenRouterClient(api_key="bad")
    with patch(
        "llm_lab.generation.services.openrouter_client.requests.post",
        return_value=_fake_response(401, '{"error":{"message":"User not found.","code":401}}'),
    ), pytest.raises(OpenRouterError) as exc_info:
        client.chat_completion(model="x", messages=[{"role": "user", "content": "hi"}])

    err = exc_info.value
    assert err.status_code == 401
    assert err.user_facing_message
    assert "OpenRouter" in err.user_facing_message
    assert err.remediation
    assert "Settings" in err.remediation
    assert "Settings" in err.display()


def test_non_auth_error_is_not_remapped():
    client = OpenRouterClient(api_key="ok")
    with patch(
        "llm_lab.generation.services.openrouter_client.requests.post",
        return_value=_fake_response(400, '{"error":"bad request"}'),
    ), pytest.raises(OpenRouterError) as exc_info:
        client.chat_completion(model="x", messages=[{"role": "user", "content": "hi"}])

    err = exc_info.value
    assert err.status_code == 400
    assert err.user_facing_message is None
    assert err.remediation is None
