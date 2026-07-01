"""Unit tests for Aider helper functions."""

from backend.generation.services.aider_runner import to_aider_model_id


class TestToAiderModelId:
    def test_adds_prefix(self) -> None:
        assert to_aider_model_id("deepseek/deepseek-chat") == "openrouter/deepseek/deepseek-chat"

    def test_keeps_existing_prefix(self) -> None:
        assert to_aider_model_id("openrouter/anthropic/claude-3.5-sonnet") == (
            "openrouter/anthropic/claude-3.5-sonnet"
        )
