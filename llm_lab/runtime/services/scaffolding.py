"""Scaffolding helper: write generated code + Dockerfiles to a build directory."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_lab.generation.models import GenerationJob

# Base directory where static template files live
_TEMPLATES_DIR = Path(__file__).parent.parent / "scaffolding"


def prepare_build_dir(job: GenerationJob, dest_path: Path) -> Path:
    """Write generated code + scaffolding templates to *dest_path*.

    Returns the build directory path (same as *dest_path*).
    """
    dest_path.mkdir(parents=True, exist_ok=True)

    result = job.result_data or {}
    backend_code: str = result.get("backend_code", "")
    frontend_code: str = result.get("frontend_code", "")

    template_slug = _resolve_template(job)

    if template_slug == "react-flask":
        _scaffold_react_flask(dest_path, backend_code, frontend_code)
    else:
        _scaffold_generic_python(dest_path, backend_code)

    return dest_path


def _resolve_template(job: GenerationJob) -> str:
    """Determine scaffolding template from job or fall back to generic-python."""
    if job.scaffolding_template and job.scaffolding_template.slug == "react-flask":
        return "react-flask"
    return "generic-python"


def _scaffold_react_flask(
    dest: Path,
    backend_code: str,
    frontend_code: str,
) -> None:
    template_dir = _TEMPLATES_DIR / "react-flask"

    backend_dest = dest / "backend"
    backend_dest.mkdir(exist_ok=True)
    _copy_template_dir(template_dir / "backend", backend_dest)
    (backend_dest / "app.py").write_text(backend_code or _placeholder_backend())

    frontend_dest = dest / "frontend"
    frontend_dest.mkdir(exist_ok=True)
    _copy_template_dir(template_dir / "frontend", frontend_dest)
    if frontend_code:
        src_dir = frontend_dest / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "App.jsx").write_text(frontend_code)


def _scaffold_generic_python(dest: Path, backend_code: str) -> None:
    template_dir = _TEMPLATES_DIR / "generic-python"
    _copy_template_dir(template_dir, dest)
    (dest / "app.py").write_text(backend_code or _placeholder_backend())


def _copy_template_dir(src: Path, dst: Path) -> None:
    """Copy template files to dst, skipping if src does not exist."""
    if not src.exists():
        return
    for item in src.iterdir():
        dest_item = dst / item.name
        if item.is_dir():
            shutil.copytree(str(item), str(dest_item), dirs_exist_ok=True)
        else:
            shutil.copy2(str(item), str(dest_item))


def _placeholder_backend() -> str:
    return (
        "from flask import Flask, jsonify\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/api/health')\n"
        "def health():\n"
        "    return jsonify({'status': 'ok'})\n\n"
        "if __name__ == '__main__':\n"
        "    app.run(host='0.0.0.0', port=5000)\n"
    )
