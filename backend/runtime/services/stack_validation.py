"""Validation for user-authored stack skeletons.

User stacks are materialized into Docker build directories, so the file map is
the security boundary: no path escapes, no Docker-control files (the
Dockerfile is generated server-side from a pinned template), and hard size
caps. Dependency manifests (requirements.txt / package.json) are deliberately
allowed — installing dependencies executes code at build time, but that is
the platform's existing risk class: its whole purpose is building and running
LLM-generated code in these same containers.
"""

from __future__ import annotations

from django.conf import settings

MAX_FILES = 64
MAX_TOTAL_BYTES = 512 * 1024
MAX_FILE_BYTES = 128 * 1024
MAX_PATH_LENGTH = 200

# Files that control the container build/runtime — server-generated or
# forbidden outright.
RESERVED_NAMES = frozenset(
    {
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        ".dockerignore",
    },
)

_PORT_MIN = 1024
_PORT_MAX = 65535


def validate_stack_files(files: object) -> list[str]:
    """Return every problem with a user-supplied ``{relpath: text}`` map."""
    if not isinstance(files, dict):
        return ["files must be an object mapping relative paths to text content"]

    errors: list[str] = []
    if len(files) > MAX_FILES:
        errors.append(f"too many files ({len(files)} > {MAX_FILES})")

    total = 0
    for path, content in files.items():
        if not isinstance(path, str) or not isinstance(content, str):
            errors.append("file paths and contents must be strings")
            break
        path_errors = _validate_path(path)
        errors.extend(path_errors)
        size = len(content.encode("utf-8"))
        total += size
        if size > MAX_FILE_BYTES:
            errors.append(f"{path}: file too large ({size} > {MAX_FILE_BYTES} bytes)")

    if total > MAX_TOTAL_BYTES:
        errors.append(f"skeleton too large ({total} > {MAX_TOTAL_BYTES} bytes)")
    return errors


def _validate_path(path: str) -> list[str]:
    errors: list[str] = []
    if not path or len(path) > MAX_PATH_LENGTH:
        errors.append(f"invalid path length: {path[:60]!r}")
        return errors
    if "\x00" in path or "\\" in path:
        errors.append(f"{path[:60]!r}: forbidden characters")
        return errors
    if path.startswith(("/", "~")):
        errors.append(f"{path}: absolute and home-relative paths are forbidden")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        errors.append(f"{path}: path traversal segments are forbidden")
    name = parts[-1].lower()
    if name in RESERVED_NAMES or name.startswith(".git"):
        errors.append(f"{path}: reserved file name (the Dockerfile is generated server-side)")
    return errors


def validate_stack_config(
    *,
    backend_base_image: str,
    frontend_base_image: str,
    has_frontend: bool,
    default_port: int,
    backend_filename: str,
) -> list[str]:
    """Validate the build-relevant config of a user stack."""
    errors: list[str] = []
    allowed = settings.STACK_ALLOWED_BASE_IMAGES

    if backend_base_image not in allowed["python"]:
        errors.append(
            f"backend_base_image must be one of: {', '.join(allowed['python'])}",
        )
    if has_frontend and frontend_base_image not in allowed["node"]:
        errors.append(
            f"frontend_base_image must be one of: {', '.join(allowed['node'])}",
        )
    if not _PORT_MIN <= default_port <= _PORT_MAX:
        errors.append(f"default_port must be between {_PORT_MIN} and {_PORT_MAX}")
    if (
        not backend_filename
        or "/" in backend_filename
        or not backend_filename.endswith(".py")
        or len(backend_filename) > 64
    ):
        errors.append("backend_filename must be a plain *.py file name")
    return errors


def stack_usable_by(stack, user) -> bool:
    """Whether *user* may run jobs on (or reference) this stack row."""
    if stack.is_archived:
        return False
    if stack.is_builtin or stack.is_approved:
        return True
    return stack.created_by_id == getattr(user, "id", None)
