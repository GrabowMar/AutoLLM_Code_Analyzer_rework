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


def extract_code_blocks(content: str) -> list[dict[str, str]]:
    """Extract all annotated code blocks from LLM markdown output.

    Supports ```python:filename.py, ```jsx:App.jsx, ```javascript:api.js etc.
    Returns list of dicts with 'language', 'filename', 'code' keys.
    """
    blocks = []
    pattern = re.compile(
        r"```(?P<lang>[a-zA-Z0-9_+.\-]+)?(?:[ \t]*[:  ]?[ \t]*(?P<filename>[^\n\r`]+))?\s*[\r\n]+(.*?)```",
        re.DOTALL,
    )
    for match in pattern.finditer(content or ""):
        lang = (match.group("lang") or "").strip().lower()
        filename = (match.group("filename") or "").strip()
        code = (match.group(3) or "").strip()
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


def infer_python_dependencies(code: str) -> list[str]:
    """Infer PyPI packages from Python import statements using AST."""
    packages: set[str] = set()

    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Fallback to regex if AST fails
        return _regex_infer_deps(code)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                _map_import(top, packages)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                _map_import(top, packages)

    return sorted(packages)


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
