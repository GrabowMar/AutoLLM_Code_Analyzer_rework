"""Scaffolding helper: write generated code + Dockerfiles to a build directory."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from llm_lab.generation.services.code_parser import infer_python_dependencies

if TYPE_CHECKING:
    from llm_lab.generation.models import GenerationJob

_TEMPLATES_DIR = Path(__file__).parent.parent / "scaffolding"
_LUCIDE_ICONS_PATH = _TEMPLATES_DIR / "lucide_icons.json"
_LUCIDE_FALLBACK = "AlertCircle"

# Loaded lazily on first use
_valid_lucide_icons: frozenset[str] | None = None


def _get_valid_lucide_icons() -> frozenset[str]:
    global _valid_lucide_icons  # noqa: PLW0603
    if _valid_lucide_icons is None:
        try:
            data = json.loads(_LUCIDE_ICONS_PATH.read_text())
            _valid_lucide_icons = frozenset(data)
        except Exception:  # noqa: BLE001
            _valid_lucide_icons = frozenset()
    return _valid_lucide_icons


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
    patched = _patch_backend_code(backend_code or _placeholder_backend())
    (dest / "app.py").write_text(patched)
    _patch_requirements(dest, backend_code)

    if frontend_code:
        src_dir = dest / "frontend" / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "App.jsx").write_text(_sanitize_lucide_imports(frontend_code))


def _scaffold_generic_python(dest: Path, backend_code: str) -> None:
    template_dir = _TEMPLATES_DIR / "generic-python"
    _copy_template_dir(template_dir, dest)
    patched = _patch_backend_code(backend_code or _placeholder_backend())
    (dest / "app.py").write_text(patched)
    _patch_requirements(dest, backend_code)


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
    # Build a set of package names already listed (lowercase, strip versions)
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
    code = _fix_db_create_all(code)
    return code


def _fix_sqlite_uri(code: str) -> str:
    """Replace relative sqlite:/// URIs with an absolute /app/data/ path.

    Flask-SQLAlchemy 3.x resolves relative SQLite paths relative to the Flask
    instance folder (/app/instance/), not CWD. The Dockerfile creates /app/data/,
    so we rewrite any sqlite:/// (3-slash relative) URI to sqlite:////app/data/app.db.
    Absolute paths (4 slashes) are left unchanged.
    """
    def _rewrite(m: re.Match) -> str:
        # Extract just the filename from whatever path the LLM used
        original_path = m.group(1)
        filename = Path(original_path).name or "app.db"
        return f"sqlite:////app/data/{filename}"

    return re.sub(
        r"sqlite:///([^'\"]+)",
        lambda m: _rewrite(m),
        code,
    )


def _fix_db_create_all(code: str) -> str:
    """Wrap the init block inside if __name__ in with app.app_context().

    Flask-SQLAlchemy 3.x requires all DB operations to run inside an app context.
    LLMs often call db.create_all() and seed functions outside a context. This
    patches the pattern by wrapping the full init block (from db.create_all up to
    but not including port assignment / app.run) in with app.app_context().
    """
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

        # Find the if __name__ == '__main__': block
        if re.match(r"if __name__\s*==\s*['\"]__main__['\"]:", stripped):
            block_indent = len(line) - len(line.lstrip())
            body_indent = block_indent + 4
            result.append(line)
            i += 1

            # Collect the body of the if block
            init_lines: list[str] = []
            run_lines: list[str] = []
            in_run_section = False
            while i < len(lines):
                body_line = lines[i]
                # Stop if we've de-indented back past the block
                if body_line.strip() and len(body_line) - len(body_line.lstrip()) <= block_indent:
                    break
                body_stripped = body_line.strip()
                # Once we see port= or app.run(), switch to run_lines
                if body_stripped.startswith("port ") or body_stripped.startswith("app.run("):
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
                    # Add 4 spaces of extra indent for each init line
                    result.append("    " + il if il.strip() else il)
            result.extend(run_lines)
            continue

        result.append(line)
        i += 1

    return "".join(result)


def _fix_flask_port(code: str) -> str:
    """Ensure Flask listens on the PORT env variable defaulting to 8000.

    The Dockerfile maps 8000/tcp. LLMs sometimes default to PORT=5000.
    """
    # Replace default port of 5000 with 8000 in os.environ.get('PORT', N)
    code = re.sub(
        r"os\.environ\.get\(['\"]PORT['\"],\s*5000\s*\)",
        "os.environ.get('PORT', 8000)",
        code,
    )
    # Replace bare app.run(..., port=5000, ...) with PORT-aware binding
    code = re.sub(
        r"app\.run\(([^)]*\b)port\s*=\s*5000([^)]*)\)",
        lambda m: f"app.run({m.group(1)}port=int(os.environ.get('PORT', 8000)){m.group(2)})",
        code,
    )
    return code


def _sanitize_lucide_imports(code: str) -> str:
    """Replace unknown lucide-react icon names with a safe fallback.

    LLMs occasionally hallucinate icon names that don't exist in the installed
    lucide-react version, which causes the npm build to fail.
    """
    valid = _get_valid_lucide_icons()
    if not valid:
        return code

    def _fix_import(match: re.Match) -> str:
        prefix, names_str, suffix = match.group(1), match.group(2), match.group(3)
        names = [n.strip() for n in names_str.split(",") if n.strip()]
        fixed: list[str] = []
        fallback_needed = False
        for name in names:
            # Handle "Name as Alias" patterns
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

    # Match single-line and multi-line lucide-react import statements
    pattern = re.compile(
        r"(import\s*\{)([^}]+)(\}\s*from\s*['\"]lucide-react['\"])",
        re.DOTALL,
    )
    return pattern.sub(_fix_import, code)


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
