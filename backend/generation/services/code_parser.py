"""Code Parser — extracts and organizes code blocks from LLM responses.

Parses annotated code blocks from markdown-formatted LLM output, merges
multi-file Python output into a single app.py, and infers dependencies.
Merges multi-file Python output into a single app.py and infers dependencies.
"""

import ast
import logging
import re

logger = logging.getLogger(__name__)

# Standard library modules that shouldn't be in requirements.txt
PYTHON_STDLIB = frozenset(
    {
        "abc",
        "argparse",
        "asyncio",
        "base64",
        "bisect",
        "calendar",
        "collections",
        "contextlib",
        "copy",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "difflib",
        "email",
        "enum",
        "functools",
        "glob",
        "hashlib",
        "heapq",
        "hmac",
        "html",
        "http",
        "importlib",
        "inspect",
        "io",
        "itertools",
        "json",
        "logging",
        "math",
        "mimetypes",
        "operator",
        "os",
        "pathlib",
        "pickle",
        "platform",
        "pprint",
        "queue",
        "random",
        "re",
        "secrets",
        "shutil",
        "signal",
        "socket",
        "sqlite3",
        "statistics",
        "string",
        "struct",
        "subprocess",
        "sys",
        "tempfile",
        "textwrap",
        "threading",
        "time",
        "traceback",
        "typing",
        "unittest",
        "urllib",
        "uuid",
        "warnings",
        "xml",
        "zipfile",
    },
)

# Common import → PyPI package mapping
IMPORT_TO_PACKAGE = {
    "flask": "flask",
    "flask_cors": "flask-cors",
    "flask_sqlalchemy": "flask-sqlalchemy",
    "flask_migrate": "flask-migrate",
    "flask_login": "flask-login",
    "flask_jwt_extended": "flask-jwt-extended",
    "flask_mail": "flask-mail",
    "flask_wtf": "flask-wtf",
    "sqlalchemy": "sqlalchemy",
    "werkzeug": "werkzeug",
    "jwt": "pyjwt",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "requests": "requests",
    "celery": "celery",
    "redis": "redis",
    "bcrypt": "bcrypt",
    "marshmallow": "marshmallow",
    "stripe": "stripe",
    "boto3": "boto3",
    "pandas": "pandas",
    "numpy": "numpy",
    "gunicorn": "gunicorn",
}


# Some models emit the filename as a bare ``:app.py`` marker on the first line
# INSIDE the block instead of on the fence (```python\n:app.py\n...).  Left in,
# it becomes line 1 of the source and breaks compilation, so we strip it.
_INLINE_FILENAME_RE = re.compile(r"^[ \t]*:[ \t]*(?P<filename>[\w./-]+\.[A-Za-z0-9]+)[ \t]*$")


def extract_code_blocks(content: str) -> list[dict[str, str]]:
    """Extract all annotated code blocks from LLM markdown output.

    Supports ```python:filename.py, ```jsx:App.jsx, ```javascript:api.js etc.,
    and a ``:filename`` marker on the first line inside the block.
    Returns list of dicts with 'language', 'filename', 'code' keys.
    """
    blocks = []
    # The closing fence is optional (``|\Z``): a response truncated at the
    # token limit ends mid-block, and dropping that block would make callers
    # fall back to the raw markdown — leaking the ```lang:file header into
    # the extracted source.
    pattern = re.compile(
        r"```(?P<lang>[a-zA-Z0-9_+.\-]+)?(?:[ \t]*[:  ]?[ \t]*(?P<filename>[^\n\r`]+))?\s*[\r\n]+(.*?)(?:```|\Z)",
        re.DOTALL,
    )
    for match in pattern.finditer(content or ""):
        lang = (match.group("lang") or "").strip().lower()
        filename = (match.group("filename") or "").strip()
        code = (match.group(3) or "").strip()
        # Pull a leading ``:filename`` marker out of the body so it can't end up
        # in the source; adopt it as the filename when the fence had none.
        first, _sep, rest = code.partition("\n")
        inline = _INLINE_FILENAME_RE.match(first)
        if inline:
            if not filename:
                filename = inline.group("filename")
            code = rest.strip()
        if code:
            blocks.append({"language": lang, "filename": filename, "code": code})
    return blocks


def extract_python_code(raw_content: str) -> str:
    """Extract and merge all Python code from LLM response into single module."""
    blocks = extract_code_blocks(raw_content)

    python_blocks = []
    requirements_blocks = []

    for block in blocks:
        lang = block["language"]
        filename = block["filename"]

        # Detect language from filename if needed
        if not lang and filename.lower().endswith(".py"):
            lang = "python"
        if lang.endswith(".py"):
            filename = lang
            lang = "python"

        if lang == "requirements" or (filename and "requirements" in filename.lower()):
            requirements_blocks.append(block["code"])
            continue

        if lang == "python":
            python_blocks.append(
                {"filename": filename or "app.py", "code": block["code"]},
            )

    # Fallback: if no code blocks found but content looks like Python
    if not python_blocks and _looks_like_python(raw_content):
        logger.info("No code blocks found; using raw content as Python")
        return raw_content.strip()

    if not python_blocks:
        return ""

    if len(python_blocks) == 1:
        return python_blocks[0]["code"]

    return _merge_python_files(python_blocks)


def extract_frontend_code(raw_content: str) -> str:
    """Extract frontend code (JSX/JS/HTML) from LLM response."""
    blocks = extract_code_blocks(raw_content)

    frontend_blocks = []
    for block in blocks:
        lang = block["language"]
        if lang in ("jsx", "tsx", "javascript", "js", "html", "css", "svelte") or (
            block["filename"]
            and any(
                block["filename"].lower().endswith(ext) for ext in (".jsx", ".tsx", ".js", ".html", ".css", ".svelte")
            )
        ):
            frontend_blocks.append(block)

    if not frontend_blocks:
        return raw_content.strip() if raw_content else ""

    if len(frontend_blocks) == 1:
        return frontend_blocks[0]["code"]

    # Return all blocks with file markers
    parts = []
    for block in frontend_blocks:
        header = block["filename"] or f"file.{block['language'] or 'jsx'}"
        parts.append(f"// === {header} ===\n{block['code']}")
    return "\n\n".join(parts)


# Packages required by a feature/type that has no dedicated import line.
# Pydantic's EmailStr, for example, needs the optional ``email-validator``
# extra at runtime even though nothing imports it directly.
_FEATURE_DEPENDENCIES: tuple[tuple[str, str], ...] = ((r"\bEmailStr\b", "email-validator"),)


def _add_feature_dependencies(code: str, packages: set[str]) -> None:
    for marker, package in _FEATURE_DEPENDENCIES:
        if re.search(marker, code):
            packages.add(package)


def infer_python_dependencies(code: str) -> list[str]:
    """Infer PyPI packages from Python import statements using AST."""
    packages: set[str] = set()

    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Fallback to regex if AST fails
        packages.update(_regex_infer_deps(code))
    else:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    _map_import(top, packages)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    _map_import(top, packages)

    _add_feature_dependencies(code, packages)
    return sorted(packages)


_FRONTEND_LANGS = frozenset({"jsx", "tsx", "javascript", "js", "ts", "typescript", "html", "css", "vue", "svelte"})
_FRONTEND_EXTS = (".jsx", ".tsx", ".js", ".ts", ".html", ".css", ".vue", ".svelte")
_MAX_PATH_DEPTH = 6


def sanitize_rel_path(name: str) -> str | None:
    """Normalize an LLM-supplied filename to a safe relative path.

    Filenames come straight from model output and are written to a Docker
    build directory later — absolute paths, ``..`` segments, and other
    escapes must never survive. Returns None when the name is unusable.
    """
    if not name:
        return None
    cleaned = name.strip().strip("`'\"").replace("\\", "/")
    if not cleaned or cleaned.startswith(("/", "~")) or ":" in cleaned:
        return None
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or len(parts) > _MAX_PATH_DEPTH:
        return None
    for part in parts:
        if part == ".." or part.startswith("..") or not re.fullmatch(r"[\w.@-]+", part):
            return None
    filename = parts[-1]
    if "." not in filename:
        return None
    return "/".join(parts)


def _is_frontend_block(block: dict[str, str]) -> bool:
    if block["language"] in _FRONTEND_LANGS:
        return True
    filename = (block["filename"] or "").lower()
    return filename.endswith(_FRONTEND_EXTS)


def _is_python_block(block: dict[str, str]) -> bool:
    lang = block["language"]
    filename = (block["filename"] or "").lower()
    if lang == "requirements" or "requirements" in filename:
        return False
    return lang in ("python", "py") or lang.endswith(".py") or filename.endswith(".py")


def _backend_entry_candidate(files: dict[str, str], entry_name: str) -> str | None:
    """Pick the file that should become the backend entry module."""
    if entry_name in files:
        return entry_name
    for common in ("app.py", "main.py"):
        if common in files:
            return common
    scored = [name for name, code in files.items() if "__main__" in code or "Flask(" in code or "FastAPI(" in code]
    if scored:
        return scored[0]
    return next(iter(files), None)


def _frontend_rel_path(filename: str) -> str | None:
    """Map an LLM frontend filename to its place under ``frontend/src/``."""
    safe = sanitize_rel_path(filename)
    if not safe:
        return None
    if safe.startswith("frontend/"):
        return safe
    if safe.startswith("src/"):
        return f"frontend/{safe}"
    if safe.startswith("public/"):
        return f"frontend/{safe}"
    if safe == "index.html":
        return "frontend/index.html"
    return f"frontend/src/{safe}"


def parse_to_files(
    backend_raw: str,
    frontend_raw: str | None = None,
    stack_config: dict | None = None,
) -> dict:
    """Parse raw LLM output into a per-file map, preserving multi-file output.

    Backend files land at the build-dir root; frontend files under
    ``frontend/src/`` (or their given ``frontend/``-relative path). The
    stack's entry module (``backend_filename``) and main component
    (``frontend_component``) are guaranteed to exist in the map so Docker
    builds always find them.
    """
    stack_config = stack_config or {}
    entry_name = stack_config.get("backend_filename", "app.py")
    component = stack_config.get("frontend_component", "App.jsx")
    component_path = f"frontend/src/{component}"

    files: dict[str, str] = {}

    # ── Backend ──
    backend_files: dict[str, str] = {}
    unnamed_python: list[dict[str, str]] = []
    for block in extract_code_blocks(backend_raw or ""):
        if not _is_python_block(block):
            continue
        filename = block["filename"]
        # Some models put the filename in the fence's language slot
        # (```app.py instead of ```python:app.py); recover it here.
        if not filename and block["language"].endswith(".py"):
            filename = block["language"]
        safe = sanitize_rel_path(filename) if filename else None
        if safe and safe.endswith(".py"):
            if safe in backend_files:
                backend_files[safe] += "\n\n" + block["code"]
            else:
                backend_files[safe] = block["code"]
        else:
            unnamed_python.append({"filename": "", "code": block["code"]})

    if unnamed_python:
        merged = unnamed_python[0]["code"] if len(unnamed_python) == 1 else _merge_python_files(unnamed_python)
        if entry_name in backend_files:
            backend_files[entry_name] += "\n\n" + merged
        else:
            backend_files[entry_name] = merged
    if not backend_files and backend_raw and _looks_like_python(backend_raw):
        backend_files[entry_name] = backend_raw.strip()

    entry = _backend_entry_candidate(backend_files, entry_name)
    if entry and entry != entry_name:
        backend_files[entry_name] = backend_files.pop(entry)
        entry = entry_name
    files.update(backend_files)

    # ── Frontend ──
    frontend_count = 0
    if frontend_raw:
        unnamed_frontend: list[dict[str, str]] = []
        for block in extract_code_blocks(frontend_raw):
            if not _is_frontend_block(block):
                continue
            rel = _frontend_rel_path(block["filename"]) if block["filename"] else None
            if rel:
                if rel in files:
                    files[rel] += "\n\n" + block["code"]
                else:
                    files[rel] = block["code"]
                frontend_count += 1
            else:
                unnamed_frontend.append(block)
        if unnamed_frontend:
            merged_front = "\n\n".join(b["code"] for b in unnamed_frontend)
            if component_path in files:
                files[component_path] += "\n\n" + merged_front
            else:
                files[component_path] = merged_front
            frontend_count += 1
        if frontend_count and component_path not in files:
            # Promote the closest match to the main component the scaffold imports.
            candidates = [
                p for p in files if p.startswith("frontend/") and p.rsplit("/", 1)[-1].lower() == component.lower()
            ]
            source = candidates[0] if candidates else next(p for p in files if p.startswith("frontend/"))
            files[component_path] = files.pop(source)

    all_python = "\n\n".join(backend_files.values())

    return {
        "files": files,
        "backend_entry": entry or "",
        "backend_dependencies": infer_python_dependencies(all_python) if all_python else [],
        "backend_files": len(backend_files),
        "frontend_files": frontend_count,
    }


def parse_result_to_structured(
    backend_raw: str,
    frontend_raw: str | None = None,
) -> dict:
    """Parse raw LLM outputs into structured result data."""
    result: dict = {}

    backend_code = extract_python_code(backend_raw)
    result["backend_code"] = backend_code
    result["backend_files"] = _count_code_blocks(backend_raw, "python")

    if backend_code:
        result["backend_dependencies"] = infer_python_dependencies(backend_code)

    if frontend_raw:
        frontend_code = extract_frontend_code(frontend_raw)
        result["frontend_code"] = frontend_code
        result["frontend_files"] = _count_code_blocks(frontend_raw, "jsx")

    return result


# ── Private helpers ──────────────────────────────────────────────


def _merge_python_files(blocks: list[dict[str, str]]) -> str:
    """Merge multiple Python code blocks into single module.

    Strategy: collect imports, categorize code, reassemble in order.
    """
    all_imports: set[str] = set()
    model_code: list[str] = []
    route_code: list[str] = []
    main_code: list[str] = []
    helper_code: list[str] = []

    for block in blocks:
        filename = block["filename"].lower()
        code = block["code"]

        lines = code.split("\n")
        non_import_lines = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and "=" not in stripped:
                all_imports.add(stripped)
            else:
                non_import_lines.append(line)

        body = "\n".join(non_import_lines).strip()
        if not body:
            continue

        if "model" in filename:
            model_code.append(body)
        elif "route" in filename or "view" in filename or "api" in filename:
            route_code.append(body)
        elif filename in ("app", "app.py", "main", "main.py", ""):
            main_code.append(body)
        else:
            helper_code.append(body)

    parts = []
    if all_imports:
        sorted_imports = sorted(all_imports)
        parts.append("\n".join(sorted_imports))
    if model_code:
        parts.append("\n\n# --- Models ---\n" + "\n\n".join(model_code))
    if helper_code:
        parts.append("\n\n# --- Helpers ---\n" + "\n\n".join(helper_code))
    if route_code:
        parts.append("\n\n# --- Routes ---\n" + "\n\n".join(route_code))
    if main_code:
        parts.append("\n\n".join(main_code))

    return "\n\n".join(parts)


def _looks_like_python(content: str) -> bool:
    """Heuristic check if content looks like Python code."""
    indicators = [
        r"^\s*(?:from|import)\s+\w+",
        r"^\s*def\s+\w+\s*\(",
        r"^\s*class\s+\w+",
        r"^\s*@\w+\.\w+",
        r"app\s*=\s*Flask\(",
    ]
    matches = sum(1 for pattern in indicators if re.search(pattern, content, re.MULTILINE))
    return matches >= 2


def _map_import(top_module: str, packages: set[str]) -> None:
    """Map a top-level import name to PyPI package name."""
    if top_module in PYTHON_STDLIB:
        return
    if top_module in IMPORT_TO_PACKAGE:
        packages.add(IMPORT_TO_PACKAGE[top_module])
    elif top_module.startswith(("app", "config", "models", "routes", "views")):
        return  # local project modules
    else:
        packages.add(top_module)


def _regex_infer_deps(code: str) -> list[str]:
    """Fallback regex-based dependency inference."""
    packages: set[str] = set()
    for match in re.finditer(
        r"^\s*(?:from|import)\s+(\w+)",
        code,
        re.MULTILINE,
    ):
        _map_import(match.group(1), packages)
    return sorted(packages)


def _count_code_blocks(content: str, language: str) -> int:
    """Count code blocks of a specific language in content."""
    blocks = extract_code_blocks(content)
    return sum(1 for b in blocks if b["language"].startswith(language))
