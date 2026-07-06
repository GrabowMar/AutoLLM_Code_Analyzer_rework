"""Tests for the AI reviewer runner's response parsing."""

from unittest import mock

import pytest

from backend.analysis.services import ai_runner
from backend.analysis.tests.factories import AnalyzerToolFactory


class TestExtractJsonArray:
    def test_plain_array(self) -> None:
        content = '[{"severity": "high", "title": "x"}]'
        assert ai_runner._extract_json_array(content) == [{"severity": "high", "title": "x"}]

    def test_fenced_array(self) -> None:
        content = '```json\n[{"severity": "low", "title": "y"}]\n```'
        assert ai_runner._extract_json_array(content) == [{"severity": "low", "title": "y"}]

    def test_empty_array(self) -> None:
        assert ai_runner._extract_json_array("[]") == []

    def test_no_array(self) -> None:
        assert ai_runner._extract_json_array("The code looks fine to me.") is None

    def test_truncated_array_salvages_complete_objects(self) -> None:
        # Cut off mid-object, as happens when the response hits max_tokens.
        content = (
            "```json\n[\n"
            '  {"severity": "critical", "title": "a", "description": "uses {braces}"},\n'
            '  {"severity": "low", "title": "b"},\n'
            '  {"severity": "medium", "title": "c", "descri'
        )
        data = ai_runner._extract_json_array(content)
        assert data is not None
        assert [d["title"] for d in data] == ["a", "b"]

    def test_truncated_with_nested_object(self) -> None:
        content = '[{"title": "a", "meta": {"k": "v"}}, {"title": "b", "meta": {"k'
        data = ai_runner._extract_json_array(content)
        assert data == [{"title": "a", "meta": {"k": "v"}}]


@pytest.mark.django_db
class TestRun:
    def _run_with_response(self, content: str, finish_reason: str = "stop"):
        tool = AnalyzerToolFactory(slug="llm_review", kind="ai", parser_key="")
        response = {
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        }
        with (
            mock.patch.object(ai_runner, "DEFAULT_MODEL", "test/model"),
            mock.patch(
                "backend.credentials.services.resolver.get_openrouter_key",
                return_value="sk-test",
            ),
            mock.patch(
                "backend.generation.services.openrouter_client.OpenRouterClient.chat_completion",
                return_value=response,
            ),
        ):
            return ai_runner.run(tool, {"backend": "print('x')"}, {}, None)

    def test_valid_review_produces_findings(self) -> None:
        output = self._run_with_response('[{"severity": "high", "title": "Bug"}]')
        assert not output.has_error
        assert len(output.findings) == 1
        assert output.summary["truncated"] is False

    def test_clean_empty_review_is_success(self) -> None:
        output = self._run_with_response("[]")
        assert not output.has_error
        assert output.findings == []

    def test_unparseable_response_is_error_not_zero_findings(self) -> None:
        output = self._run_with_response("I could not review this code.")
        assert output.has_error
        assert "unparseable" in output.error

    def test_truncated_unparseable_response_mentions_token_limit(self) -> None:
        output = self._run_with_response('```json\n[{"severity": "hi', finish_reason="length")
        assert output.has_error
        assert "max_tokens" in output.error

    def test_truncated_but_salvageable_response_keeps_findings(self) -> None:
        content = '```json\n[{"severity": "high", "title": "a"}, {"severity": "lo'
        output = self._run_with_response(content, finish_reason="length")
        assert not output.has_error
        assert len(output.findings) == 1
        assert output.summary["truncated"] is True
