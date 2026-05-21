"""Tests for copilot result collection."""

from pathlib import Path

import pytest

from llm_lab.generation.services.copilot_results import CopilotResults
from llm_lab.generation.tests.factories import GenerationJobFactory


@pytest.mark.django_db
class TestCopilotResultsCollect:
    def test_collect_files_from_workspace(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / "backend").mkdir()
        (root / "backend" / "app.py").write_text("x = 1\n" * 40, encoding="utf-8")
        (root / "frontend" / "src").mkdir(parents=True)
        (root / "frontend" / "src" / "App.jsx").write_text(
            "export default function App() { return null; }\n",
            encoding="utf-8",
        )

        job = GenerationJobFactory(mode="copilot")

        class FakeWorkspace:
            def __init__(self, path: Path) -> None:
                self.root = path

        CopilotResults.apply(job, FakeWorkspace(root), [])

        assert job.result_data["engine"] == "aider"
        assert "backend/app.py" in job.result_data["files"]
        assert job.result_data["content"]
        assert job.result_data["backend_code"]
