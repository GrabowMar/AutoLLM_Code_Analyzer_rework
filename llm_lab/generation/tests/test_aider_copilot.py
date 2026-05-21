"""Tests for Aider-backed copilot orchestration."""

from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import uuid4

import pytest

from llm_lab.generation.models import CopilotIteration
from llm_lab.generation.models import GenerationJob
from llm_lab.generation.services.aider_runner import AiderIterationResult
from llm_lab.generation.services.generation_service import GenerationService
from llm_lab.generation.tests.factories import GenerationJobFactory


@pytest.mark.django_db
class TestRunCopilotWithAider:
    @patch("llm_lab.generation.services.copilot_results.CopilotResults.apply")
    @patch("llm_lab.generation.services.aider_runner.AiderRunner")
    @patch("llm_lab.generation.services.copilot_workspace.CopilotWorkspace.create")
    def test_run_copilot_uses_aider_pipeline(
        self,
        mock_workspace_create: MagicMock,
        mock_runner_cls: MagicMock,
        mock_apply: MagicMock,
    ) -> None:
        job = GenerationJobFactory(
            mode=GenerationJob.Mode.COPILOT,
            copilot_description="Build a todo API",
            copilot_max_iterations=3,
        )
        workspace = MagicMock()
        workspace.cleanup = MagicMock()
        mock_workspace_create.return_value = workspace

        iteration = AiderIterationResult(
            iteration=1,
            transcript="done",
            errors=[],
            build_output="",
            build_success=True,
        )
        mock_runner_cls.return_value.run_loop.return_value = [iteration]

        service = GenerationService()
        service._run_copilot(job)

        mock_workspace_create.assert_called_once_with(job)
        mock_runner_cls.assert_called_once_with(job, workspace)
        mock_runner_cls.return_value.run_loop.assert_called_once_with(3)
        mock_apply.assert_called_once()
        apply_args = mock_apply.call_args[0]
        assert apply_args[0].id == job.id
        assert apply_args[2] == [iteration]
        workspace.cleanup.assert_called_once()
        assert job.metrics["engine"] == "aider"

    @patch(
        "llm_lab.generation.services.aider_runner.AiderRunner._run_aider_message",
        return_value="ok",
    )
    @patch(
        "llm_lab.generation.services.aider_runner.AiderRunner._ensure_aider_available",
    )
    @patch(
        "llm_lab.generation.services.aider_runner.resolve_copilot_api_key",
        return_value="sk-or-v1-test",
    )
    def test_aider_runner_creates_iterations(
        self,
        _mock_key: MagicMock,
        _mock_ensure: MagicMock,
        _mock_run: MagicMock,
        tmp_path,
    ) -> None:
        from llm_lab.generation.services.aider_runner import AiderRunner
        from llm_lab.generation.services.copilot_workspace import CopilotWorkspace

        job = GenerationJobFactory(
            mode=GenerationJob.Mode.COPILOT,
            copilot_description="Build app",
            copilot_max_iterations=1,
        )
        root = tmp_path / str(uuid4())
        root.mkdir()
        (root / "app.py").write_text("x = 1\n" * 40, encoding="utf-8")

        workspace = CopilotWorkspace(root, job)
        with patch.object(workspace, "test_command", return_value=None):
            runner = AiderRunner(job, workspace)
            results = runner.run_loop(1)

        assert len(results) == 1
        assert CopilotIteration.objects.filter(job=job).count() == 1
