"""Generation-time validation for scaffolding/custom output.

Python is checked in-process (``compile()``/AST — cheap, no container).
Frontend files get a real syntax parse via esbuild in a throwaway container,
mirroring the analyzer workspace pattern in ``backend.analysis.services.
workspace_service``. Validation never raises: an unavailable Docker daemon or
missing image degrades to a skipped check with a warning, it does not fail
the generation job.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from backend.generation.services.copilot_validation import validate_python_code
from backend.runtime.services import docker_manager

logger = logging.getLogger(__name__)

VALIDATOR_IMAGE_TAG = "backend/frontend-validator:latest"
_IMAGE_DIR = Path(__file__).resolve().parent.parent / "images" / "frontend-validator"

_FRONTEND_EXTS = (".js", ".jsx", ".ts", ".tsx")
_LOADER_BY_EXT = {".js": "jsx", ".jsx": "jsx", ".ts": "tsx", ".tsx": "tsx"}
EXEC_TIMEOUT_SECONDS = 20


class ValidationResult:
    """Per-file validation outcome for one generation stage."""

    def __init__(self) -> None:
        self.errors: dict[str, list[str]] = {}
        self.skipped: list[str] = []

    def add(self, filename: str, errors: list[str]) -> None:
        if errors:
            self.errors[filename] = errors

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"passed": self.passed, "errors": self.errors, "skipped": self.skipped}


def validate_python_files(files: dict[str, str]) -> ValidationResult:
    """Validate every ``.py`` file with the existing copilot AST/stub checks."""
    result = ValidationResult()
    for name, code in files.items():
        if name.endswith(".py"):
            result.add(name, validate_python_code(code))
    return result


def ensure_validator_image() -> tuple[bool, str]:
    """Build the frontend-validator image if it does not already exist."""
    if docker_manager.image_exists(VALIDATOR_IMAGE_TAG):
        return True, "Image present"
    if not docker_manager.ping():
        return False, "Docker daemon unavailable"
    try:
        docker_manager.build_image(str(_IMAGE_DIR), VALIDATOR_IMAGE_TAG)
    except Exception as exc:  # noqa: BLE001
        detail = docker_manager.extract_build_error(exc) or str(exc)
        logger.warning("frontend-validator image build failed: %s", detail[-500:])
        return False, detail[-2000:]
    return True, "Image built"


def validate_frontend_files(files: dict[str, str]) -> ValidationResult:
    """Real syntax check of JS/JSX/TS/TSX files via esbuild.

    Files with an extension esbuild doesn't parse (``.css``, ``.html``,
    ``.vue``) are recorded as skipped, not failed — no syntax checker for
    those is wired up yet.
    """
    result = ValidationResult()
    checkable = {name: code for name, code in files.items() if name.endswith(_FRONTEND_EXTS)}
    for name in files:
        if name not in checkable and any(name.endswith(ext) for ext in (".css", ".html", ".vue", ".svelte")):
            result.skipped.append(name)
    if not checkable:
        return result

    ok, message = ensure_validator_image()
    if not ok:
        logger.warning("Skipping frontend syntax validation: %s", message)
        result.skipped.extend(checkable)
        return result

    for name, code in checkable.items():
        ext = Path(name).suffix
        loader = _LOADER_BY_EXT.get(ext, "jsx")
        safe_name = f"input{ext}"
        # esbuild's bare --loader=X only applies when reading from stdin;
        # for a file argument the loader must be bound to its extension.
        exec_result = docker_manager.run_once_with_files(
            VALIDATOR_IMAGE_TAG,
            {safe_name: code},
            ["esbuild", safe_name, f"--loader:{ext}={loader}", "--bundle=false"],
            timeout_s=EXEC_TIMEOUT_SECONDS,
        )
        if "error" in exec_result:
            logger.warning("Frontend validation errored for %s: %s", name, exec_result["error"])
            result.skipped.append(name)
            continue
        if exec_result["exit_code"] != 0:
            result.add(name, _parse_esbuild_errors(exec_result["output"]))
    return result


def _parse_esbuild_errors(output: str) -> list[str]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    # esbuild marks each error with a leading "✘ [ERROR]"; older/plain
    # formats use a bare "error:" prefix. Match either.
    errors = [line for line in lines if re.match(r"^(✘\s*)?\[?error\]?", line, re.IGNORECASE)]
    return errors or (lines[:5] or ["esbuild reported a non-zero exit with no parseable output"])


def build_repair_prompt(filename: str, code: str, errors: list[str]) -> str:
    """Render the repair round's user message from the ``repair-instructions`` block."""
    from backend.generation.services.profile_resolver import get_content_block
    from backend.generation.services.prompt_renderer import PromptRenderer

    block = get_content_block("repair-instructions", 1)
    return PromptRenderer().render_template(
        block.content,
        {"filename": filename, "code": code, "errors": errors},
    )


def extract_repaired_file(response_content: str, filename: str) -> str | None:
    """Pull the single code block a repair response should contain."""
    from backend.generation.services.code_parser import extract_code_blocks

    blocks = extract_code_blocks(response_content)
    if not blocks:
        return None
    # A repair response should be one file; if the model named it, prefer the
    # match, otherwise take the (only) block.
    for block in blocks:
        if block["filename"] and Path(block["filename"]).name == Path(filename).name:
            return block["code"]
    return blocks[0]["code"]
