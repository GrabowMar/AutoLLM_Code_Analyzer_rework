"""Collect copilot workspace files into GenerationJob.result_data."""

from __future__ import annotations

import logging
from pathlib import Path

from llm_lab.generation.models import GenerationJob
from llm_lab.generation.services.aider_runner import AiderIterationResult
from llm_lab.generation.services.code_parser import infer_python_dependencies
from llm_lab.generation.services.copilot_workspace import CopilotWorkspace

logger = logging.getLogger(__name__)

_TEXT_EXTENSIONS = {
    ".py",
    ".jsx",
    ".js",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".json",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".toml",
    ".env.example",
}

_SKIP_PARTS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


class CopilotResults:
    """Aggregate workspace state after Aider completes."""

    @staticmethod
    def apply(
        job: GenerationJob,
        workspace: CopilotWorkspace,
        iterations: list[AiderIterationResult],
    ) -> None:
        files = CopilotResults._collect_files(workspace.root)
        backend_code = files.get("backend/app.py", files.get("app.py", ""))
        frontend_code = files.get(
            "frontend/src/App.jsx",
            files.get("frontend/App.jsx", ""),
        )
        primary = backend_code or CopilotResults._largest_python(files) or ""
        last = iterations[-1] if iterations else None
        final_errors = last.errors if last else []

        job.result_data = {
            "content": primary,
            "files": files,
            "backend_code": backend_code,
            "frontend_code": frontend_code,
            "iterations_completed": len(iterations),
            "final_errors": final_errors,
            "dependencies": infer_python_dependencies(primary),
            "engine": "aider",
        }

    @staticmethod
    def _collect_files(root: Path) -> dict[str, str]:
        collected: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in _SKIP_PARTS for part in path.parts):
                continue
            if path.suffix.lower() not in _TEXT_EXTENSIONS and path.name not in {
                "Dockerfile",
                "requirements.txt",
            }:
                continue
            rel = path.relative_to(root).as_posix()
            try:
                collected[rel] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("Skip %s: %s", rel, exc)
        return collected

    @staticmethod
    def _largest_python(files: dict[str, str]) -> str:
        py_paths = [p for p in files if p.endswith(".py")]
        if not py_paths:
            return ""
        best = max(py_paths, key=lambda p: len(files[p]))
        return files[best]
