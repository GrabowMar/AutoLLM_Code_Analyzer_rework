"""Tests that copilot resolves API keys from stored user credentials."""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from llm_lab.generation.services.aider_runner import resolve_copilot_api_key
from llm_lab.generation.tests.factories import GenerationJobFactory


@pytest.mark.django_db
class TestResolveCopilotApiKey:
    @patch("llm_lab.generation.services.aider_runner.get_openrouter_key", return_value="sk-or-stored")
    def test_uses_job_owner(self, mock_get_key: MagicMock) -> None:
        job = GenerationJobFactory(mode="copilot")
        assert resolve_copilot_api_key(job) == "sk-or-stored"
        mock_get_key.assert_called_once_with(job.created_by)

    @patch("llm_lab.generation.services.aider_runner.get_openrouter_key", return_value="sk-or-stored")
    def test_loads_user_when_not_prefetched(self, mock_get_key: MagicMock) -> None:
        job = GenerationJobFactory(mode="copilot")
        user = job.created_by
        job.created_by = None
        job.created_by_id = user.id
        assert resolve_copilot_api_key(job) == "sk-or-stored"
        mock_get_key.assert_called_once()
        assert mock_get_key.call_args[0][0].id == user.id
