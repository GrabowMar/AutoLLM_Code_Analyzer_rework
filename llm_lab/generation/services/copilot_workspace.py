"""Ephemeral git workspace for copilot (Aider) generation jobs."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from llm_lab.runtime.services.scaffolding import ScaffoldPhase
from llm_lab.runtime.services.scaffolding import apply_scaffold
from llm_lab.runtime.services.scaffolding import resolve_stack_slug
from llm_lab.runtime.services.scaffolding import stack_has_frontend

if TYPE_CHECKING:
    from llm_lab.generation.models import GenerationJob

logger = logging.getLogger(__name__)

_SKIP_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
}


class CopilotWorkspace:
    """Per-job directory with git history for Aider."""

    def __init__(self, root: Path, job: GenerationJob) -> None:
        self.root = root
        self.job = job

    @classmethod
    def create(cls, job: GenerationJob) -> CopilotWorkspace:
        """Create workspace, seed scaffold, and initial commit."""
        base = Path(settings.GENERATION_WORKSPACE_ROOT)
        base.mkdir(parents=True, exist_ok=True)
        root = base / str(job.id)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)

        workspace = cls(root, job)
        workspace._seed_scaffold()
        workspace._git_init_and_commit()
        return workspace

    def cleanup(self) -> None:
        """Remove workspace directory after job completes."""
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def template_slug(self) -> str:
        return resolve_stack_slug(self.job)

    def is_flask_react(self) -> bool:
        return stack_has_frontend(self.template_slug())

    def tracked_files(self) -> list[str]:
        """Relative paths of editable source files for Aider."""
        paths: list[str] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() in {
                ".py",
                ".jsx",
                ".js",
                ".ts",
                ".tsx",
                ".html",
                ".css",
                ".json",
                ".md",
            }:
                paths.append(rel)
        return paths or ["app.py"]

    def primary_python_path(self) -> Path:
        """Best-effort main Python file for validation."""
        candidates = [
            self.root / "app.py",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        py_files = sorted(self.root.rglob("*.py"), key=lambda p: -p.stat().st_size)
        if py_files:
            return py_files[0]
        return self.root / "app.py"

    def test_command(self) -> str | None:
        """Shell command for compile/test validation after Aider edits."""
        rel = self.primary_python_path().relative_to(self.root).as_posix()
        if (self.root / "app.py").is_file():
            return f"python -m py_compile {rel}"
        return None

    def _seed_scaffold(self) -> None:
        apply_scaffold(self.job, self.root, phase=ScaffoldPhase.SEED)
        readme = self.root / "README.md"
        readme.write_text(
            f"# Copilot project\n\n{self.job.copilot_description}\n",
            encoding="utf-8",
        )

    def _git_init_and_commit(self) -> None:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "llm-lab",
            "GIT_AUTHOR_EMAIL": "copilot@llm-lab.local",
            "GIT_COMMITTER_NAME": "llm-lab",
            "GIT_COMMITTER_EMAIL": "copilot@llm-lab.local",
        }
        git_cfg = ["-c", "user.name=llm-lab", "-c", "user.email=copilot@llm-lab.local"]
        subprocess.run(
            ["git", *git_cfg, "init"],
            cwd=self.root,
            check=True,
            capture_output=True,
            env=env,
        )
        subprocess.run(
            ["git", *git_cfg, "add", "-A"],
            cwd=self.root,
            check=True,
            capture_output=True,
            env=env,
        )
        result = subprocess.run(
            ["git", *git_cfg, "commit", "-m", "Initial scaffold"],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0 and "nothing to commit" not in (result.stderr or ""):
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                result.stdout,
                result.stderr,
            )
