"""Versioned review prompt + findings JSON schema (``data/review/``).

The prompt and the schema are the reviewer *instrument*: their versions and
sha256 (of the exact file bytes — wording, whitespace and property order all
change model behaviour) are frozen into every plan, and ``run_review``
refuses to run when the files on disk no longer match the frozen hashes.

This module also decides what the single-shot reviewer is shown
(:func:`select_files`) and how code is rendered for it: every line prefixed
with its 1-based number (``  12| code``) so findings can cite lines, which
:mod:`.grounding` then verifies against the same line split
(:func:`split_lines`, which is ``sloc.split_lines``: the numbering of the
static tools, normalization and SLOC).
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import re
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.engine import sloc

if TYPE_CHECKING:
    from backend.analysis.engine.types import Sample
    from backend.analysis.engine.types import SampleFile

PROMPT_VERSION = "review-v1"
SCHEMA_VERSION = "findings-v1"

REVIEW_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "review"
PROMPT_FILES = {"review-v1": "prompt-v1.md"}
SCHEMA_FILES = {"findings-v1": "findings-schema-v1.json"}

# Name of the structured-output schema sent to OpenRouter (^[A-Za-z0-9_-]+$).
RESPONSE_FORMAT_NAME = "code_review_findings"

# Finding vocabulary of findings-v1 (must match the schema file; tested).
CATEGORIES = (
    "security",
    "secrets",
    "bug",
    "type",
    "maintainability",
    "dead_code",
    "duplication",
    "complexity",
    "style",
    "other",
)
FINDING_SEVERITIES = ("critical", "high", "medium", "low", "info")

# What the reviewer is shown by default: code the model wrote (the measured
# origins) in the file kinds that can hold reviewable defects. Docs,
# lockfiles, build output and assets are never sent.
DEFAULT_INCLUDE_ORIGINS = ("authored", "added", "modified")
ORIGIN_CHOICES = ("authored", "added", "modified", "scaffold", "platform", "unknown")
DEFAULT_INCLUDE_KINDS = ("source", "config", "manifest", "env")
KIND_CHOICES = ("source", "config", "manifest", "env", "doc", "other")

_SECTION_RE = re.compile(r"^<!-- @section (\w+) -->\n", re.MULTILINE)
# Width of the right-aligned line-number column (wider for >9999-line files).
_MIN_NUMBER_WIDTH = 4


class UnknownVersionError(ValueError):
    """A prompt or schema version without a (well-formed) file under ``data/review/``."""


# --------------------------------------------------------------------------
# Versioned files
# --------------------------------------------------------------------------


def _prompt_path(version: str) -> Path:
    try:
        return REVIEW_DIR / PROMPT_FILES[version]
    except KeyError:
        msg = f"unknown review prompt version {version!r} (known: {', '.join(PROMPT_FILES)})"
        raise UnknownVersionError(msg) from None


def _schema_path(version: str) -> Path:
    try:
        return REVIEW_DIR / SCHEMA_FILES[version]
    except KeyError:
        msg = f"unknown findings schema version {version!r} (known: {', '.join(SCHEMA_FILES)})"
        raise UnknownVersionError(msg) from None


@functools.cache
def _prompt_bytes(version: str) -> bytes:
    return _prompt_path(version).read_bytes()


@functools.cache
def _schema_bytes(version: str) -> bytes:
    return _schema_path(version).read_bytes()


@functools.cache
def _schema_json(version: str) -> dict[str, Any]:
    return json.loads(_schema_bytes(version))


@functools.cache
def _sections(version: str) -> dict[str, str]:
    """``{section: template text}`` of a prompt file (the header comment is dropped)."""
    text = _prompt_bytes(version).decode("utf-8")
    # split() keeps the captured names: header, then alternating section name and body.
    parts = _SECTION_RE.split(text)
    sections = {parts[i]: parts[i + 1].rstrip("\n") for i in range(1, len(parts) - 1, 2)}
    missing = {"system", "user", "repair"} - set(sections)
    if missing:
        msg = f"prompt {version} lacks section(s) {', '.join(sorted(missing))}"
        raise UnknownVersionError(msg)
    return sections


def system_prompt(version: str = PROMPT_VERSION) -> str:
    return Template(_sections(version)["system"]).substitute()


def user_prompt(
    *,
    files_block: str,
    file_count: int,
    chunk_index: int,
    chunk_count: int,
    version: str = PROMPT_VERSION,
) -> str:
    """One chunk's user message (*chunk_index* is 1-based)."""
    if chunk_count > 1:
        note = (
            f"This is part {chunk_index} of {chunk_count} of the application's reviewed files; "
            "the other parts are reviewed separately, so only report issues visible in the files below."
        )
    else:
        note = "These are all of the application's reviewed files."
    return Template(_sections(version)["user"]).substitute(chunk_note=note, file_count=file_count, files=files_block)


def repair_prompt(errors: list[str], version: str = PROMPT_VERSION) -> str:
    lines = "\n".join(f"- {e}" for e in errors) or "- the answer could not be parsed"
    return Template(_sections(version)["repair"]).substitute(errors=lines)


def findings_schema(allowed_files: list[str] | None = None, version: str = SCHEMA_VERSION) -> dict[str, Any]:
    """The findings JSON schema; with *allowed_files* the ``file`` property becomes an enum.

    The enum variant is what the model is constrained with (strict structured
    output). Local validation uses the plain schema, so a cited path outside
    the shown files is kept and graded ``invalid_file`` by grounding (a
    measurable reviewer error) instead of failing the whole answer.
    """
    schema = copy.deepcopy(_schema_json(version))
    if allowed_files is not None:
        item = schema["properties"]["findings"]["items"]["properties"]
        item["file"]["enum"] = sorted(set(allowed_files))
    return schema


def response_format(
    mode: str,
    allowed_files: list[str] | None,
    version: str = SCHEMA_VERSION,
) -> dict[str, Any] | None:
    """OpenRouter ``response_format`` for an output mode (``json_schema`` | ``json_object`` | ``prompt``)."""
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": RESPONSE_FORMAT_NAME,
                "strict": True,
                "schema": findings_schema(allowed_files, version),
            },
        }
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def prompt_sha256(version: str = PROMPT_VERSION) -> str:
    return hashlib.sha256(_prompt_bytes(version)).hexdigest()


def schema_sha256(version: str = SCHEMA_VERSION) -> str:
    return hashlib.sha256(_schema_bytes(version)).hexdigest()


def prompt_versions() -> list[dict[str, Any]]:
    """Known prompt versions with their hashes (for the UI and protocols)."""
    return [{"version": v, "sha256": prompt_sha256(v), "file": f} for v, f in PROMPT_FILES.items()]


def schema_versions() -> list[dict[str, Any]]:
    return [{"version": v, "sha256": schema_sha256(v), "file": f} for v, f in SCHEMA_FILES.items()]


def overhead_chars(version: str = PROMPT_VERSION, schema_version: str = SCHEMA_VERSION) -> int:
    """Characters every call carries besides the code: system + user template + schema."""
    sections = _sections(version)
    return len(sections["system"]) + len(sections["user"]) + len(_schema_bytes(schema_version))


# --------------------------------------------------------------------------
# What is shown, and how
# --------------------------------------------------------------------------


# File lines as editors and the static tools number them: the platform's one
# line numbering, :func:`backend.analysis.engine.sloc.split_lines` (``\n``
# only, a trailing ``\r`` dropped). Re-exported, not reimplemented: the
# numbered listing, grounding, normalization and SLOC must cut lines alike or
# AI and static findings would cite different lines for the same code.
split_lines = sloc.split_lines


def file_text(f: SampleFile) -> str | None:
    """Strict UTF-8 text of a file, or None for binary/undecodable content."""
    try:
        return f.content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def render_lines(path: str, lines: list[str], *, start: int = 1, end: int | None = None, language: str = "") -> str:
    """One file (or a line range of it) as the model sees it: header, numbered lines, footer."""
    total = len(lines)
    end = total if end is None else min(end, total)
    width = max(_MIN_NUMBER_WIDTH, len(str(total)))
    lang = f"{language}, " if language else ""
    span = f"{total} lines" if start == 1 and end == total else f"lines {start}-{end} of {total}"
    body = "\n".join(f"{n:>{width}}| {lines[n - 1]}" for n in range(start, end + 1))
    return f"=== FILE: {path} ({lang}{span}) ===\n{body}\n=== END FILE: {path} ==="


def select_files(
    sample: Sample,
    *,
    include_origins: list[str] | tuple[str, ...] = DEFAULT_INCLUDE_ORIGINS,
    include_kinds: list[str] | tuple[str, ...] = DEFAULT_INCLUDE_KINDS,
) -> tuple[list[SampleFile], dict[str, int]]:
    """Files the reviewer is shown (sorted by path) and counts of the others by reason.

    A file is shown when the sample includes it (scope rules already dropped
    lockfiles, build output, binaries and oversized files), an included
    origin wrote it, and its kind can hold reviewable defects. A ``modified``
    file is shown whole: its scaffold lines are context the model needs, and
    normalization attributes each finding to the real origin of its line.
    """
    origins = set(include_origins)
    kinds = set(include_kinds)
    selected: list[SampleFile] = []
    skipped: dict[str, int] = {}
    for f in sorted(sample.files, key=lambda x: x.path):
        if not f.included:
            reason = f"excluded:{f.exclude_reason or 'scope'}"
        elif f.origin not in origins:
            reason = f"origin:{f.origin}"
        elif f.kind not in kinds:
            reason = f"kind:{f.kind}"
        else:
            selected.append(f)
            continue
        skipped[reason] = skipped.get(reason, 0) + 1
    return selected, skipped
