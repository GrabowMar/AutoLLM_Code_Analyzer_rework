"""Scaffolding helper: write generated code + Dockerfiles to a build directory."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_lab.generation.models import GenerationJob

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

    if template_slug in ("flask-react", "react-flask"):
        _scaffold_flask_react(dest_path, backend_code, frontend_code)
    else:
        _scaffold_generic_python(dest_path, backend_code)

    return dest_path


def _resolve_template(job: GenerationJob) -> str:
    """Determine scaffolding template from job or fall back to generic-python."""
    if job.scaffolding_template:
        slug = job.scaffolding_template.slug
        if slug in ("flask-react", "react-flask"):
            return slug
    return "generic-python"


def _scaffold_flask_react(
    dest: Path,
    backend_code: str,
    frontend_code: str,
) -> None:
    template_dir = _TEMPLATES_DIR / "flask-react"
    _copy_template_dir(template_dir, dest)
    (dest / "app.py").write_text(backend_code or _placeholder_backend())

    if frontend_code:
        src_dir = dest / "frontend" / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
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
        "import os\n"
        "from flask import Flask, jsonify, send_from_directory\n\n"
        "app = Flask(__name__, static_folder='static', static_url_path='')\n\n"
        "@app.route('/api/health')\n"
        "def health():\n"
        "    return jsonify({'status': 'ok'})\n\n"
        "@app.route('/', defaults={'path': ''})\n"
        "@app.route('/<path:path>')\n"
        "def _serve_spa(path):\n"
        "    import os as _os\n"
        "    if path and _os.path.exists(_os.path.join(app.static_folder, path)):\n"
        "        return send_from_directory(app.static_folder, path)\n"
        "    return send_from_directory(app.static_folder, 'index.html')\n\n"
        "if __name__ == '__main__':\n"
        "    port = int(os.environ.get('PORT', 8000))\n"
        "    app.run(host='0.0.0.0', port=port, debug=False)\n"
    )
