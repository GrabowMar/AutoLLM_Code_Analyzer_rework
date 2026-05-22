"""Ephemeral git workspace for copilot (Aider) generation jobs."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tarfile
from pathlib import Path

from django.conf import settings

from llm_lab.generation.models import GenerationJob
from llm_lab.runtime.services.scaffolding import _copy_template_dir
from llm_lab.runtime.services.scaffolding import _placeholder_backend

logger = logging.getLogger(__name__)

_RUNTIME_SCAFFOLDING = Path(__file__).resolve().parents[2] / "runtime" / "scaffolding"

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
        if job_template := self.job.scaffolding_template:
            return job_template.slug
        return "generic-python"

    def is_flask_react(self) -> bool:
        return self.template_slug() in ("flask-react", "react-flask")

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
            self.root / "backend" / "app.py",
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
        if self.is_flask_react():
            return f"python -m py_compile {self.primary_python_path().relative_to(self.root).as_posix()}"
        if (self.root / "app.py").is_file():
            return f"python -m py_compile {self.primary_python_path().relative_to(self.root).as_posix()}"
        return None

    def _seed_scaffold(self) -> None:
        template = self.job.scaffolding_template
        if template and template.template_archive:
            archive_path = Path(template.template_archive.path)
            if archive_path.is_file():
                self._extract_archive(archive_path)
                return

        slug = self.template_slug()
        if slug in ("flask-react", "react-flask"):
            self._seed_flask_react_static()
        else:
            self._seed_minimal()

    def _extract_archive(self, archive_path: Path) -> None:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=self.root, filter="data")

    def _seed_flask_react_static(self) -> None:
        template_dir = _RUNTIME_SCAFFOLDING / "flask-react"
        _copy_template_dir(template_dir, self.root)
        (self.root / "app.py").write_text(_placeholder_backend(), encoding="utf-8")

        src_dir = self.root / "frontend" / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        if not (src_dir / "App.jsx").is_file():
            (src_dir / "App.jsx").write_text(
                "export default function App() {\n"
                "  return <div className=\"p-8\">Hello</div>;\n"
                "}\n",
                encoding="utf-8",
            )

        readme = self.root / "README.md"
        readme.write_text(
            f"# Copilot project\n\n{self.job.copilot_description}\n",
            encoding="utf-8",
        )

    def _seed_minimal(self) -> None:
        generic = _RUNTIME_SCAFFOLDING / "generic-python"
        if generic.exists():
            _copy_template_dir(generic, self.root)
        (self.root / "app.py").write_text(_placeholder_backend(), encoding="utf-8")
        (self.root / "README.md").write_text(
            f"# Copilot project\n\n{self.job.copilot_description}\n",
            encoding="utf-8",
        )

    def _git_init_and_commit(self) -> None:
        import os

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
