"""Scaffolding: copy stack templates, patch generated code, prepare Docker build dirs."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tarfile
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.services.code_parser import infer_python_dependencies

if TYPE_CHECKING:
    from backend.generation.models import GenerationJob

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "scaffolding"
_MANIFEST_PATH = _TEMPLATES_DIR / "manifest.json"
_LUCIDE_ICONS_PATH = _TEMPLATES_DIR / "lucide_icons.json"
_LUCIDE_FALLBACK = "AlertCircle"

_SUBSTITUTION_PATTERN = re.compile(r"\{\{(\w+)(?:\|([^}]+))?\}\}")

# Loaded lazily on first use
_valid_lucide_icons: frozenset[str] | None = None
_manifest_cache: dict[str, Any] | None = None


class ScaffoldPhase(str, Enum):
    """seed: copilot workspace placeholders; build: job result code for Docker."""

    SEED = "seed"
    BUILD = "build"


def load_manifest() -> dict[str, Any]:
    """Load and cache ``runtime/scaffolding/manifest.json``."""
    global _manifest_cache  # noqa: PLW0603
    if _manifest_cache is None:
        _manifest_cache = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    return _manifest_cache


def _stack_entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map canonical slug and aliases to stack config (canonical slug included)."""
    entries: dict[str, dict[str, Any]] = {}
    for canonical, config in manifest.get("stacks", {}).items():
        entry = {**config, "canonical_slug": canonical}
        entries[canonical] = entry
        for alias in config.get("aliases", []):
            entries[alias] = entry
    return entries


def canonical_stack_slug(slug: str) -> str:
    """Map a raw scaffolding slug (canonical or alias) to its canonical stack slug."""
    manifest = load_manifest()
    entries = _stack_entries(manifest)
    default = manifest.get("default_stack", "generic-python")

    if slug in entries:
        return entries[slug]["canonical_slug"]
    logger.warning("Unknown scaffolding slug %r; using %s", slug, default)
    return default


def resolve_stack_slug(job: GenerationJob) -> str:
    """Resolve job scaffolding template to a canonical stack slug from the manifest."""
    manifest = load_manifest()
    default = manifest.get("default_stack", "generic-python")

    if not job.scaffolding_template:
        return default

    return canonical_stack_slug(job.scaffolding_template.slug)


def get_stack_config(stack_slug: str) -> dict[str, Any]:
    """Return manifest config for a canonical stack slug."""
    manifest = load_manifest()
    stacks = manifest.get("stacks", {})
    if stack_slug not in stacks:
        msg = f"Unknown stack slug: {stack_slug}"
        raise KeyError(msg)
    return {**stacks[stack_slug], "canonical_slug": stack_slug}


def stack_has_frontend(stack_slug: str) -> bool:
    return bool(get_stack_config(stack_slug).get("has_frontend"))


def apply_scaffold(
    job: GenerationJob,
    dest_path: Path,
    *,
    phase: ScaffoldPhase,
) -> Path:
    """Copy stack template (or archive) into *dest_path* and write application code.

    *phase* ``seed`` uses placeholders for copilot; ``build`` uses ``job.result_data``.
    """
    dest_path.mkdir(parents=True, exist_ok=True)
    stack_slug = resolve_stack_slug(job)
    stack = get_stack_config(stack_slug)

    if _apply_template_archive(job, dest_path):
        pass
    else:
        template_dir = _TEMPLATES_DIR / stack["directory"]
        _copy_template_dir(template_dir, dest_path)

    _apply_substitutions(dest_path, job, stack)

    result = job.result_data or {}
    backend_code = result.get("backend_code", "")
    frontend_code = result.get("frontend_code", "")

    backend_name = stack.get("backend_filename", "app.py")

    if phase == ScaffoldPhase.SEED:
        backend_text = _placeholder_backend_for_stack(stack)
        frontend_text = _placeholder_frontend() if stack.get("has_frontend") else ""
    else:
        backend_text = backend_code or _placeholder_backend_for_stack(stack)
        frontend_text = frontend_code

    if stack.get("patch_profile") == "flask":
        backend_text = _patch_backend_code(backend_text)

    (dest_path / backend_name).write_text(backend_text, encoding="utf-8")
    _patch_requirements(
        dest_path,
        backend_code if phase == ScaffoldPhase.BUILD else "",
    )

    if stack.get("has_frontend"):
        src_dir = dest_path / "frontend" / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        component = stack.get("frontend_component", "App.jsx")
        is_vue = component.endswith(".vue")
        front = frontend_text or (_placeholder_frontend_vue() if is_vue else _placeholder_frontend())
        if phase == ScaffoldPhase.BUILD and frontend_text and not is_vue:
            front = _sanitize_lucide_imports(front)
        (src_dir / component).write_text(front, encoding="utf-8")

    return dest_path


def prepare_build_dir(job: GenerationJob, dest_path: Path) -> Path:
    """Write generated code + scaffolding templates to *dest_path* for Docker build."""
    return apply_scaffold(job, dest_path, phase=ScaffoldPhase.BUILD)


def _apply_template_archive(job: GenerationJob, dest_path: Path) -> bool:
    """Extract ``template_archive`` when present. Returns True if archive was applied."""
    template = job.scaffolding_template
    if not template or not template.template_archive:
        return False
    archive_path = Path(template.template_archive.path)
    if not archive_path.is_file():
        return False
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(path=dest_path, filter="data")
    return True


def _apply_substitutions(dest: Path, job: GenerationJob, stack: dict[str, Any]) -> None:
    manifest = load_manifest()
    rel_paths = manifest.get("substitution_files", [".env.example"])
    context = _build_substitution_context(job, stack)

    for rel in rel_paths:
        path = dest / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "{{" not in text:
            continue
        path.write_text(_render_substitutions(text, context), encoding="utf-8")


def _build_substitution_context(job: GenerationJob, stack: dict[str, Any]) -> dict[str, str]:
    port = str(stack.get("default_port", 8000))
    if job.app_requirement_id and job.app_requirement:
        project_name = job.app_requirement.slug
    elif job.scaffolding_template:
        project_name = job.scaffolding_template.slug
    else:
        project_name = "app"
    return {
        "PROJECT_NAME": project_name,
        "app_port": port,
    }


def _render_substitutions(text: str, context: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        default = match.group(2)
        if key in context:
            return context[key]
        if default is not None:
            return default
        return match.group(0)

    return _SUBSTITUTION_PATTERN.sub(_replace, text)


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


def _patch_requirements(dest: Path, backend_code: str) -> None:
    """Merge inferred deps from generated code into the template requirements.txt."""
    req_path = dest / "requirements.txt"
    if not req_path.exists() or not backend_code:
        return

    existing_text = req_path.read_text()
    existing_pkgs = {
        line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip().lower()
        for line in existing_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    inferred = infer_python_dependencies(backend_code)
    new_pkgs = [pkg for pkg in inferred if pkg.lower() not in existing_pkgs]

    if new_pkgs:
        extra = "\n".join(new_pkgs)
        req_path.write_text(existing_text.rstrip() + "\n" + extra + "\n")


def _patch_backend_code(code: str) -> str:
    """Apply runtime-invariant fixes to generated Flask backend code."""
    code = _fix_sqlite_uri(code)
    code = _fix_flask_port(code)
    return _fix_db_create_all(code)


def _fix_sqlite_uri(code: str) -> str:
    """Replace relative sqlite:/// URIs with an absolute /app/data/ path."""

    def _rewrite(m: re.Match[str]) -> str:
        original_path = m.group(1)
        filename = Path(original_path).name or "app.db"
        return f"sqlite:////app/data/{filename}"

    return re.sub(
        r"sqlite:///([^'\"]+)",
        _rewrite,
        code,
    )


def _fix_db_create_all(code: str) -> str:
    """Wrap the init block inside if __name__ in with app.app_context()."""
    if "with app.app_context()" in code:
        return code
    if "db.create_all()" not in code:
        return code

    lines = code.splitlines(keepends=True)
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        if re.match(r"if __name__\s*==\s*['\"]__main__['\"]:", stripped):
            block_indent = len(line) - len(line.lstrip())
            body_indent = block_indent + 4
            result.append(line)
            i += 1

            init_lines: list[str] = []
            run_lines: list[str] = []
            in_run_section = False
            while i < len(lines):
                body_line = lines[i]
                if body_line.strip() and len(body_line) - len(body_line.lstrip()) <= block_indent:
                    break
                body_stripped = body_line.strip()
                if body_stripped.startswith(("port ", "app.run(")):
                    in_run_section = True
                if in_run_section:
                    run_lines.append(body_line)
                else:
                    init_lines.append(body_line)
                i += 1

            if init_lines:
                ctx_indent = " " * body_indent
                result.append(f"{ctx_indent}with app.app_context():\n")
                for il in init_lines:
                    result.append("    " + il if il.strip() else il)
            result.extend(run_lines)
            continue

        result.append(line)
        i += 1

    return "".join(result)


def _fix_flask_port(code: str) -> str:
    """Ensure Flask listens on PORT defaulting to 8000."""
    code = re.sub(
        r"os\.environ\.get\(['\"]PORT['\"],\s*5000\s*\)",
        "os.environ.get('PORT', 8000)",
        code,
    )
    return re.sub(
        r"app\.run\(([^)]*\b)port\s*=\s*5000([^)]*)\)",
        lambda m: f"app.run({m.group(1)}port=int(os.environ.get('PORT', 8000)){m.group(2)})",
        code,
    )


def _get_valid_lucide_icons() -> frozenset[str]:
    global _valid_lucide_icons  # noqa: PLW0603
    if _valid_lucide_icons is None:
        try:
            data = json.loads(_LUCIDE_ICONS_PATH.read_text(encoding="utf-8"))
            _valid_lucide_icons = frozenset(data)
        except Exception:  # noqa: BLE001
            _valid_lucide_icons = frozenset()
    return _valid_lucide_icons


def _sanitize_lucide_imports(code: str) -> str:
    """Replace unknown lucide-react icon names with a safe fallback."""
    valid = _get_valid_lucide_icons()
    if not valid:
        return code

    def _fix_import(match: re.Match[str]) -> str:
        prefix, names_str, suffix = match.group(1), match.group(2), match.group(3)
        names = [n.strip() for n in names_str.split(",") if n.strip()]
        fixed: list[str] = []
        fallback_needed = False
        for name in names:
            base = name.split(" as ")[0].strip() if " as " in name else name
            if base in valid:
                fixed.append(name)
            else:
                fallback_needed = True
        if fallback_needed and _LUCIDE_FALLBACK not in [n.split(" as ")[0].strip() for n in fixed]:
            fixed.append(_LUCIDE_FALLBACK)
        if not fixed:
            fixed = [_LUCIDE_FALLBACK]
        return f"{prefix}{', '.join(fixed)}{suffix}"

    pattern = re.compile(
        r"(import\s*\{)([^}]+)(\}\s*from\s*['\"]lucide-react['\"])",
        re.DOTALL,
    )
    return pattern.sub(_fix_import, code)


def _placeholder_backend_for_stack(stack: dict[str, Any]) -> str:
    slug = stack.get("canonical_slug", "")
    if slug == "fastapi-vue":
        return _placeholder_fastapi()
    return _placeholder_backend()


def _placeholder_fastapi() -> str:
    return (
        "import os\n"
        "from pathlib import Path\n"
        "from fastapi import FastAPI\n"
        "from fastapi.responses import FileResponse\n"
        "from fastapi.staticfiles import StaticFiles\n\n"
        "app = FastAPI(title='Generated App')\n"
        "_STATIC = Path(__file__).parent / 'static'\n"
        "if _STATIC.is_dir():\n"
        "    app.mount('/assets', StaticFiles(directory=_STATIC / 'assets'), name='assets')\n\n"
        "@app.get('/api/health')\n"
        "def health():\n"
        "    return {'status': 'ok'}\n\n"
        "@app.get('/{full_path:path}')\n"
        "def spa(full_path: str = ''):\n"
        "    index = _STATIC / 'index.html'\n"
        "    if index.is_file():\n"
        "        return FileResponse(index)\n"
        "    return {'detail': 'frontend not built'}\n"
    )


def _placeholder_backend() -> str:
    return (
        "import os\n"
        "from pathlib import Path\n"
        "from flask import Flask, jsonify, send_from_directory\n\n"
        "app = Flask(__name__)\n"
        "_STATIC = Path(__file__).parent / 'static'\n\n"
        "@app.route('/api/health')\n"
        "def health():\n"
        "    return jsonify({'status': 'ok'})\n\n"
        "@app.route('/', defaults={'path': ''})\n"
        "@app.route('/<path:path>')\n"
        "def _serve_spa(path):\n"
        "    target = _STATIC / path if path else None\n"
        "    if target and target.is_file():\n"
        "        return send_from_directory(str(_STATIC), path)\n"
        "    return send_from_directory(str(_STATIC), 'index.html')\n\n"
        "if __name__ == '__main__':\n"
        "    port = int(os.environ.get('PORT', 8000))\n"
        "    app.run(host='0.0.0.0', port=port, debug=False)\n"
    )


def _placeholder_frontend() -> str:
    return 'export default function App() {\n  return <div className="p-8">Hello</div>;\n}\n'


def _placeholder_frontend_vue() -> str:
    return (
        "<template>\n  <div class=\"p-8\">Hello</div>\n</template>\n\n<script setup></script>\n"
    )
