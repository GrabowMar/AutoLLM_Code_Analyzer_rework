from __future__ import annotations

from unittest.mock import Mock
from unittest.mock import patch

from django.test import override_settings

from backend.llm_models.services.sync import fetch_openrouter_models


@override_settings(
    OPENROUTER_API_KEY="global-key",
    OPENROUTER_API_URL="https://example.test/openrouter/models",
)
@patch("backend.llm_models.services.sync.requests.get")
def test_fetch_openrouter_models_uses_explicit_api_key(mock_get):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"data": [{"id": "openai/gpt-4o"}]}
    mock_get.return_value = response

    models = fetch_openrouter_models("user-key")

    assert models == [{"id": "openai/gpt-4o"}]
    headers = mock_get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer user-key"
