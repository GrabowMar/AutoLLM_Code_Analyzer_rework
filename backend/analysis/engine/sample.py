"""Sample resolution: a target -> the exact files analysed (owner: sample/measure stage).

One versioned resolver (``sample-v1``) for every job mode. It never invents
files: unknown result shapes raise :class:`SampleUnavailable`, extensions are
never appended and arbitrary ``result_data`` keys never become files. Every
file gets a language, a kind (source/config/manifest/lockfile/doc/asset/build/
env/other), an origin (who wrote it; line-level for agent-modified files via
the stack scaffold baseline), SLOC and a scope decision.

Sources per job mode:

* **agent** — ``result_data["files"]`` + ``agent.workspace.manifest`` (origin
  added/modified/scaffold, sha256 verified). ``modified`` files are diffed
  (``difflib``, no autojunk) against the scaffold the agent started from —
  ``generation.services.agent.workspace.seed_files(job)``, i.e. the job's
  pinned Stack row — so unchanged lines are attributed to ``scaffold``. The
  baseline is trusted only when it reproduces every ``scaffold`` file of the
  manifest byte for byte; otherwise modified files are attributed whole.
* **scaffolding** — ``result_data["files"]`` (the LLM's code blocks, ``authored``).
* **custom** — ``code_parser.extract_code_blocks(result_data["content"])``
  (``authored``; a block without a file name is skipped with a warning).
* **legacy** — ``backend_code`` / ``frontend_code`` only -> the stack's backend
  file (``app.py``) / ``frontend/src/<component>`` (``App.jsx``).
* **inline** — files as given (``authored``).

Platform-owned build files (Dockerfile, .dockerignore, docker-compose.yml) get
origin ``platform``: tools may scan them, but they never count for the model.

**Measured scope is per line.** A file's measured SLOC (the density
denominator) and suppression markers cover exactly the lines whose origin
(``SampleFile.line_origin``: ``modified`` for changed lines of an attributed
modified file, ``scaffold`` for its unchanged lines, else the file origin) is
in the plan's ``measured_origins`` - the rule ``normalize`` applies to every
finding - so numerator and denominator cover the same lines for any origin
set. The resolver's files and samples (:class:`ResolvedFile`,
:class:`ResolvedSample`) also record that attribution in the stored manifest
(changed-line ranges, SLOC by origin, the scaffold baseline digest), so the
measurement can verify the findings against the denominator it was given.

**Truncation vs run limits.** ``truncated`` means the generation *output* was
cut off (responses that hit the output cap: ``finish_reason=length``, the
backend/frontend truncation flags). An agent run that ended at a run-level
limit (wall clock, request/token/cost budget, memory cap) is a valid sample
by the generator's own rule - dropping those would bias comparisons toward
models that finish quickly - so it is reported separately as ``run_stopped``
(``run_stop_reasons``) and excluded only when a study opts in
(``exclude_run_limits``); the generator's validity notes rate each limit.
"""

from __future__ import annotations

import copy
import difflib
import functools
import gzip
import hashlib
import json
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from backend.analysis.engine import sloc
from backend.analysis.engine.types import Sample
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import SampleUnavailable

logger = logging.getLogger(__name__)

DEFAULT_SCOPE: dict[str, Any] = {
    "measured_origins": ["authored", "added", "modified"],
    # File kinds whose lines enter measured SLOC and counted findings. Config,
    # docs, manifests and env files are reported but not measured by default;
    # a study may opt in (e.g. to count secrets in a model-written .env).
    "measured_kinds": ["source"],
    "line_attribution": True,
    "exclude_globs": [
        "**/node_modules/**",
        "**/dist/**",
        "**/build/**",
        "**/.venv/**",
        "**/__pycache__/**",
        "**/*.min.js",
        "**/*.min.css",
        "static/assets/**",
    ],
    "exclude_kinds": ["lockfile", "build", "asset"],
    "max_file_bytes": 524288,
}

DEFAULT_EXCLUSION_RULES: dict[str, Any] = {
    "exclude_invalid": True,  # agent validity == exclude
    "exclude_empty_output": True,  # nothing authored/added/modified
    "exclude_truncated": True,  # generation output cut off (finish_reason=length, truncation flags)
    # Agent runs that ended at a run-level limit (timeout, budget, memory cap)
    # are valid samples (generation's runner keeps them so that slow or
    # thorough models are not dropped); a study may exclude them, e.g. for a
    # sensitivity analysis.
    "exclude_run_limits": False,
    "min_measured_sloc": 0,
    "require_functional": False,  # only apps whose conformance status is known
}

# Origins that mean "the model wrote this" (fixed; independent of the plan's
# measured_origins, which only decides what the outcomes count).
MODEL_ORIGINS = ("authored", "added", "modified")
# The default measured kind (scope ``measured_kinds``), and the kind that
# decides ``empty_output`` (did the model write any source code at all),
# whatever a plan measures.
MEASURED_KIND = "source"
PROTECTED_PLATFORM_FILES = frozenset({"Dockerfile", ".dockerignore", "docker-compose.yml"})
LOCKFILE_NAMES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
        "poetry.lock",
        "Pipfile.lock",
        "uv.lock",
        "pdm.lock",
    },
)
MANIFEST_NAMES = frozenset({"package.json", "pyproject.toml", "Pipfile", "setup.cfg"})
VENDORED_DIRS = frozenset({"node_modules", ".venv", "venv", "site-packages", "bower_components", "vendor"})
BUILD_DIRS = frozenset({"dist", "build", "__pycache__", ".next", ".nuxt", ".svelte-kit", ".vite", ".output"})
# Agent terminations at a run-level limit: the run stopped, its output was
# not truncated (reported as ``run_stopped``, never as ``truncated``).
RUN_LIMIT_TERMINATIONS = frozenset({"timeout", "budget_exhausted", "resource_limit"})
# Prefixes of the run-limit codes (``termination:<t>``, ``budget_exhausted:<limit>``);
# target_meta rows written before the split listed them in ``truncation_reasons``.
RUN_STOP_PREFIXES = ("termination:", "budget_exhausted:")

_EXTENSION_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".vue": "vue",
    ".svelte": "svelte",
    ".html": "html",
    ".htm": "html",
    ".jinja": "html",
    ".jinja2": "html",
    ".j2": "html",
    ".css": "css",
    ".scss": "css",
    ".sass": "css",
    ".less": "css",
    ".json": "json",
    ".jsonc": "json",
    ".json5": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".txt": "text",
    ".rst": "text",
}
_ASSET_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".avif",
        ".tif",
        ".tiff",
        ".svg",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".mp3",
        ".mp4",
        ".wav",
        ".ogg",
        ".webm",
        ".mov",
        ".pdf",
        ".zip",
        ".gz",
        ".tgz",
        ".tar",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".wasm",
        ".bin",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".jar",
        ".class",
        ".db",
        ".sqlite",
        ".sqlite3",
    },
)
_BUILD_SUFFIXES = (".min.js", ".min.mjs", ".min.css", ".map", ".pyc", ".pyo")
_CONFIG_DOTFILES = frozenset(
    {
        ".gitignore",
        ".dockerignore",
        ".npmrc",
        ".nvmrc",
        ".editorconfig",
        ".prettierrc",
        ".eslintrc",
        ".babelrc",
        ".flake8",
        ".pylintrc",
        ".python-version",
        ".gitattributes",
    },
)
# JS/TS files that configure tooling (vite.config.js, .eslintrc.cjs, ...):
# code, but not application source.
_JS_CONFIG_RE = re.compile(r"^[\w-]+\.(config|conf)\.(c|m)?[jt]s$|^\.[\w-]+rc\.(c|m)?[jt]s$")
# Inline markers by which code silences or configures its own scanners
# (suppression_markers_per_kloc: how often the model tried). One match per
# directive: a region's closing marker (eslint-enable, jscpd:ignore-end) ends
# a suppression and is not one. Most are ignored or neutralized at scan time
# (``# mypy:`` inline configuration, ``#lizard forgive``, ``jscpd:ignore-start``
# and ``codespell:ignore`` on staged copies), so counting them is what keeps
# the attempt visible.
_SUPPRESSION_RE = re.compile(
    r"#\s*(?:(?:ruff|flake8)\s*:\s*)?noqa\b"
    r"|#\s*nosec\b"
    r"|#\s*type:\s*ignore\b"
    r"|#\s*pylint:\s*(?:disable|skip-file)\b"
    r"|#\s*mypy:"
    r"|eslint-disable"
    r"|nosemgrep"
    r"|gitleaks:allow"
    r"|pragma:\s*allowlist\s+(?:nextline\s+)?secret"
    r"|@ts-(?:ignore|nocheck|expect-error)\b"
    r"|lizard\s*forgives?"
    r"|jscpd:ignore-start"
    r"|codespell:ignore",
)
_FILENAME_ATTR_RE = re.compile(r"""(?:file(?:name)?|title|path)\s*=\s*["']?([^"'\s]+)""")
# A line this long in a js/css file, with a high average, means minified output.
_MINIFIED_LINE = 1000
_MINIFIED_AVG = 200


# --------------------------------------------------------------------------
# Language / kind
# --------------------------------------------------------------------------


def detect_language(path: str) -> str:
    """Language of a file from its name (never from content: deterministic)."""
    name = PurePosixPath(path).name
    lower = name.lower()
    if lower in ("dockerfile", "containerfile") or lower.startswith("dockerfile.") or lower.endswith(".dockerfile"):
        return "dockerfile"
    if lower == ".env" or lower.startswith(".env."):
        return "env"
    if lower == "pipfile":
        return "toml"
    if lower in ("readme", "license", "licence", "changelog", "authors", "notice", "copying"):
        return "text"
    if name in _CONFIG_DOTFILES:
        return "text"
    return _EXTENSION_LANGUAGES.get(PurePosixPath(lower).suffix, "other")


def detect_kind(path: str, language: str, *, content: bytes | None = None) -> str:  # noqa: PLR0911 - ordered rules
    """Kind of a file: source | config | manifest | lockfile | doc | asset | build | env | other.

    *content* (optional) enables the minified-output check for js/css files.
    """
    p = PurePosixPath(path)
    name = p.name
    lower = name.lower()
    parts = p.parts[:-1]
    if name in LOCKFILE_NAMES:
        return "lockfile"
    if lower.endswith(_BUILD_SUFFIXES) or any(part in BUILD_DIRS for part in parts):
        return "build"
    if p.suffix.lower() in _ASSET_EXTENSIONS:
        return "asset"
    if language == "env":
        return "env"
    if name in MANIFEST_NAMES or re.fullmatch(r"(requirements|constraints)([-_.][\w.-]+)?\.(txt|in)", lower):
        return "manifest"
    if language in ("javascript", "typescript") and _JS_CONFIG_RE.search(lower):
        return "config"
    if content is not None and language in ("javascript", "css") and _looks_minified(content):
        return "build"
    if language in sloc.CODE_LANGUAGES and language not in ("yaml", "toml", "dockerfile"):
        return "source"
    if language in ("json", "yaml", "toml", "dockerfile") or name in _CONFIG_DOTFILES:
        return "config"
    if lower.endswith((".ini", ".cfg", ".conf", ".properties")):
        return "config"
    if language in ("markdown", "text"):
        return "doc"
    return "other"


def _looks_minified(content: bytes) -> bool:
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return False
    longest = max(len(line) for line in lines)
    return longest >= _MINIFIED_LINE and sum(len(line) for line in lines) / len(lines) >= _MINIFIED_AVG


# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------


def effective_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    """DEFAULT_SCOPE overlaid with the plan's scope (unknown keys are kept)."""
    out = copy.deepcopy(DEFAULT_SCOPE)
    for key, value in (scope or {}).items():
        if value is not None:
            out[key] = copy.deepcopy(value)
    return out


@functools.lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """``**``-aware glob over posix paths (``**/x`` also matches ``x`` at the root)."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("(?:/.*)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(path: str, pattern: str) -> bool:
    return bool(_glob_regex(pattern).match(path))


def _exclusion(path: str, kind: str, size: int, binary: bool, scope: dict[str, Any]) -> str:  # noqa: PLR0911
    """The exclude_reason for a file, or "" when it is handed to tools."""
    if binary:
        return "binary"
    if kind in (scope.get("exclude_kinds") or ()):
        if kind == "lockfile":
            return "lockfile"
        if kind == "build":
            return "minified" if path.lower().endswith((".min.js", ".min.mjs", ".min.css")) else "build_artifact"
        if kind == "asset":
            return "asset"
        return f"kind:{kind}"
    for pattern in scope.get("exclude_globs") or ():
        if glob_match(path, pattern):
            parts = PurePosixPath(path).parts[:-1]
            if any(part in VENDORED_DIRS for part in parts):
                return "vendored"
            if any(part in BUILD_DIRS for part in parts):
                return "build_artifact"
            if path.lower().endswith((".min.js", ".min.css")):
                return "minified"
            return "glob"
    max_bytes = scope.get("max_file_bytes")
    if isinstance(max_bytes, int) and max_bytes > 0 and size > max_bytes:
        return "too_large"
    return ""


# --------------------------------------------------------------------------
# File construction
# --------------------------------------------------------------------------


@dataclass
class _Spec:
    path: str
    content: bytes
    origin: str


def clean_path(path: Any) -> str | None:
    """A safe relative posix path, or None (absolute, ``..``, empty, NUL)."""
    if not isinstance(path, str):
        return None
    p = path.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or "\x00" in p:
        return None
    parts = [part for part in p.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def count_suppressions(text: str, lines: set[int] | None = None) -> int:
    """Suppression markers in *text* (restricted to *lines* when given; ``sloc.split_lines`` numbering)."""
    total = 0
    for i, line in enumerate(sloc.split_lines(text), 1):
        if lines is not None and i not in lines:
            continue
        total += len(_SUPPRESSION_RE.findall(line))
    return total


def changed_lines(baseline: str, final: str) -> list[int]:
    """1-based lines of *final* that are not unchanged lines of *baseline*.

    Lines are numbered as the tools number them (``sloc.split_lines``: ``\\n``
    only), so a form feed or U+2028 inside a line shifts nothing.
    ``autojunk`` is off: with it, difflib treats frequent lines of files over
    200 lines as junk and produces unstable, inflated diffs.
    """
    a = sloc.split_lines(baseline)
    b = sloc.split_lines(final)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out: list[int] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            out.extend(range(j1 + 1, j2 + 1))
    return out


def _decode(content: bytes) -> str | None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


@dataclass
class ResolvedFile(SampleFile):
    """A SampleFile as the resolver builds it: its manifest entry also records
    how the file's lines were attributed, so a stored manifest can be checked
    against the findings normalized at execution (``measurement``).

    ``attribution`` (modified files only): ``line`` (diffed against a verified
    scaffold baseline), ``whole`` (no usable baseline: every line is the
    model's) or ``disabled`` (the plan's ``line_attribution`` is off).
    """

    attribution: str | None = None
    baseline_sha256: str | None = None  # the scaffold version a modified file was diffed against
    sloc_by_origin: dict[str, int] = field(default_factory=dict)

    def manifest_entry(self) -> dict[str, Any]:
        entry = super().manifest_entry()
        entry["attribution"] = self.attribution
        entry["changed_line_ranges"] = line_ranges(self.changed_lines) if self.changed_lines is not None else None
        entry["sloc_by_origin"] = dict(self.sloc_by_origin)
        entry["baseline_sha256"] = self.baseline_sha256
        return entry


def line_ranges(lines: list[int] | set[int]) -> list[list[int]]:
    """Sorted line numbers compressed to inclusive ``[start, end]`` ranges."""
    out: list[list[int]] = []
    for n in sorted(set(lines)):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out


def expand_ranges(ranges: Any) -> set[int] | None:
    """Inverse of :func:`line_ranges` (None for anything that is not a range list)."""
    if not isinstance(ranges, list):
        return None
    out: set[int] = set()
    for item in ranges:
        if not (isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(v, int) for v in item)):
            return None
        out.update(range(item[0], item[1] + 1))
    return out


def _build_file(
    spec: _Spec,
    scope: dict[str, Any],
    baseline: dict[str, bytes] | None,
) -> SampleFile:
    content = spec.content
    text = _decode(content)
    binary = text is None
    language = detect_language(spec.path)
    kind = detect_kind(spec.path, language, content=None if binary else content)
    origin = "platform" if spec.path in PROTECTED_PLATFORM_FILES else spec.origin
    reason = _exclusion(spec.path, kind, len(content), binary, scope)
    file = ResolvedFile(
        path=spec.path,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        language=language,
        kind=kind,
        origin=origin,
        included=not reason,
        exclude_reason=reason,
    )
    if origin == "modified":
        file.attribution = "whole" if scope.get("line_attribution") else "disabled"
    if binary:
        return file
    code = sloc.code_lines(text, language)
    file.sloc = len(code)
    if origin == "modified" and scope.get("line_attribution") and baseline is not None and spec.path in baseline:
        base_text = _decode(baseline[spec.path])
        if base_text is not None:
            file.changed_lines = changed_lines(base_text, text)
            file.attribution = "line"
            file.baseline_sha256 = hashlib.sha256(baseline[spec.path]).hexdigest()
    measured_origins = set(scope.get("measured_origins") or ())
    measured_kinds = scope.get("measured_kinds") or (MEASURED_KIND,)
    # Per line, exactly as normalize scopes findings (SampleFile.line_origin):
    # changed lines of an attributed modified file are "modified", the others
    # "scaffold"; every other file has one origin for all its lines.
    if file.changed_lines is not None:
        changed = set(file.changed_lines)
        file.sloc_by_origin = {"modified": len(code & changed), "scaffold": len(code - changed)}
        line_origins = {"modified", "scaffold"}
        scoped_lines: set[int] | None = {
            i
            for i in range(1, len(sloc.split_lines(text)) + 1)
            if ("modified" if i in changed else "scaffold") in measured_origins
        }
    else:
        file.sloc_by_origin = {origin: len(code)} if code else {}
        line_origins = {origin}
        scoped_lines = None if origin in measured_origins else set()
    file.measured = file.included and kind in measured_kinds and bool(line_origins & measured_origins)
    if file.measured:
        file.measured_sloc = len(code) if scoped_lines is None else len(code & scoped_lines)
        file.suppression_markers = count_suppressions(text, scoped_lines)
    return file


def sample_hash(files: list[SampleFile]) -> str:
    """sha256 over sorted ``path\\0sha256\\n`` of the included files."""
    digest = hashlib.sha256()
    for f in sorted((f for f in files if f.included), key=lambda f: f.path):
        digest.update(f"{f.path}\0{f.sha256}\n".encode())
    return digest.hexdigest()


@dataclass
class ResolvedSample(Sample):
    """A Sample as the resolver builds it: its manifest also carries the
    line-attribution summary (:func:`attribution_summary`)."""

    baseline_sha256: str | None = None  # digest of the scaffold seed the modified files were diffed against
    line_attribution: bool = True  # the plan's scope.line_attribution

    def manifest(self) -> dict[str, Any]:
        out = super().manifest()
        out["attribution"] = attribution_summary(self)
        return out


def baseline_digest(baseline: dict[str, bytes] | None) -> str | None:
    """sha256 over sorted ``path\\0sha256\\n`` of a scaffold seed (None without one)."""
    if baseline is None:
        return None
    digest = hashlib.sha256()
    for path in sorted(baseline):
        digest.update(f"{path}\0{hashlib.sha256(baseline[path]).hexdigest()}\n".encode())
    return digest.hexdigest()


def attribution_summary(sample: Sample) -> dict[str, Any]:
    """How the sample's lines were attributed (manifest ``attribution`` and target_meta).

    ``mode``: ``line_level`` (every included modified file diffed against a
    verified scaffold baseline), ``whole_file`` (at least one attributed
    whole: its scaffold lines count as the model's), ``disabled`` (the plan
    turned line attribution off), ``not_applicable`` (no modified file).
    ``digest`` covers every included file's origin and changed lines, so two
    resolutions that attribute differently never share it (``sample_hash``
    covers content only); ``baseline_sha256`` identifies the scaffold seed.
    """
    modified = [f for f in sample.files if f.included and f.origin == "modified"]
    whole = sorted(f.path for f in modified if f.changed_lines is None)
    if not modified:
        mode = "not_applicable"
    elif not getattr(sample, "line_attribution", True):
        mode = "disabled"
    elif whole:
        mode = "whole_file"
    else:
        mode = "line_level"
    digest = hashlib.sha256()
    for f in sorted((f for f in sample.files if f.included), key=lambda f: f.path):
        ranges = line_ranges(f.changed_lines) if f.changed_lines is not None else None
        digest.update(f"{f.path}\0{f.origin}\0{json.dumps(ranges, separators=(',', ':'))}\n".encode())
    return {
        "mode": mode,
        "whole_file": whole,
        "digest": digest.hexdigest(),
        "baseline_sha256": getattr(sample, "baseline_sha256", None),
    }


def attribution_changed(stored_manifest: dict[str, Any] | None, sample: Sample) -> str:
    """Why *sample* is attributed differently from a stored ``sample_manifest`` ("" if it is not).

    ``sample_hash`` covers content only: a re-resolution of the same code
    (the executor's ``load_sample``) can lose or change its scaffold baseline
    and attribute lines differently, so findings normalized against it would
    not match the stored denominator. "" when the stored manifest predates
    the attribution record.
    """
    stored = (stored_manifest or {}).get("attribution")
    if not isinstance(stored, dict) or not stored.get("digest"):
        return ""
    current = attribution_summary(sample)
    if current["digest"] == stored["digest"]:
        return ""
    return (
        f"line attribution changed since submit ({stored.get('mode')} -> {current['mode']}"
        + (f"; attributed whole: {', '.join(current['whole_file'][:5])}" if current["whole_file"] else "")
        + ")"
    )


def _assemble(
    specs: list[_Spec],
    *,
    source: str,
    scope: dict[str, Any] | None,
    warnings: list[str],
    baseline: dict[str, bytes] | None = None,
) -> Sample:
    eff = effective_scope(scope)
    by_path: dict[str, _Spec] = {}
    for spec in specs:
        if spec.path in by_path:
            warnings.append(f"duplicate file {spec.path}: the later block wins")
        by_path[spec.path] = spec
    files = [_build_file(by_path[p], eff, baseline) for p in sorted(by_path)]
    return ResolvedSample(
        files=files,
        source=source,
        sample_hash=sample_hash(files),
        warnings=warnings,
        baseline_sha256=baseline_digest(baseline),
        line_attribution=bool(eff.get("line_attribution")),
    )


def model_files(sample: Sample) -> list[SampleFile]:
    """Included source files the model wrote (the basis of ``empty_output``)."""
    return [f for f in sample.files if f.included and f.origin in MODEL_ORIGINS and f.kind == MEASURED_KIND]


# --------------------------------------------------------------------------
# Inline
# --------------------------------------------------------------------------


def resolve_inline(files: dict[str, str], scope: dict[str, Any] | None = None, *, source: str = "inline") -> Sample:
    """Resolve pasted/fixture files (origin ``authored``)."""
    if not isinstance(files, dict):
        msg = "Inline source must be a {path: text} map"
        raise SampleUnavailable(msg, code="unknown_shape")
    warnings: list[str] = []
    specs: list[_Spec] = []
    for raw_path, text in files.items():
        path = clean_path(raw_path)
        if path is None:
            warnings.append(f"skipped file with an unsafe path: {str(raw_path)[:120]!r}")
            continue
        if isinstance(text, bytes):
            data = text
        elif isinstance(text, str):
            data = text.encode("utf-8")
        else:
            warnings.append(f"skipped {path}: content is not text")
            continue
        specs.append(_Spec(path, data, "authored"))
    if not specs:
        msg = "No files to analyse"
        raise SampleUnavailable(msg, code="no_code")
    sample = _assemble(specs, source=source, scope=scope, warnings=warnings)
    sample.meta = inline_meta(sample)
    return sample


def inline_meta(sample: Sample) -> dict[str, Any]:
    """target_meta for an inline sample: no job identity, quality flags from the files."""
    return {
        "job_id": None,
        "mode": "inline",
        "status": "completed",
        "termination": None,
        "validity": {"valid": True, "exclude": False, "notes": [], "source": "not_applicable"},
        "truncated": False,
        "truncation_reasons": [],
        "truncation": {"truncated_calls": None, "evidence": "not_applicable"},
        "run_stopped": False,
        "run_stop_reasons": [],
        "empty_output": not model_files(sample),
        "attribution": attribution_summary(sample),
        "functional": _functional_none(),
        "generation": {"cost": None, "total_tokens": None, "duration_seconds": None},
    }


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


# Seeds per (stack slug, version, content hash, substitution inputs); a seed
# is a few dozen small files, so the bound keeps memory trivial.
BASELINE_CACHE_SIZE = 32
_baseline_cache: OrderedDict[tuple[str, ...], dict[str, bytes]] = OrderedDict()
_baseline_lock = threading.Lock()


def _baseline_key(job) -> tuple[str, ...] | None:
    """What a job's seed depends on, or None when it must not be cached.

    ``apply_scaffold`` seeds the job's pinned Stack row (immutable per slug +
    version; its content hash guards against a re-seeded row) and substitutes
    the project name (app requirement slug, else the job's stack slug). A
    job without an exact pin seeds the *latest* row, which moves, so it is
    never cached.
    """
    try:
        bundle = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
        pinned = bundle.get("stack") if isinstance(bundle.get("stack"), dict) else {}
        slug, version = pinned.get("slug"), pinned.get("version")
        if not slug or isinstance(version, bool) or not isinstance(version, int):
            return None
        from backend.runtime.models import Stack

        content_hash = Stack.objects.filter(slug=slug, version=version).values_list("content_hash", flat=True).first()
        if content_hash is None:
            return None
        requirement = job.app_requirement if job.app_requirement_id else None
        return (str(slug), str(version), str(content_hash), job.stack_slug or "", getattr(requirement, "slug", ""))
    except Exception:  # noqa: BLE001 - no key means "seed without the cache", never a crash
        logger.debug("No scaffold baseline cache key for job %s", getattr(job, "pk", None), exc_info=True)
        return None


def clear_baseline_cache() -> None:
    """Forget every cached scaffold seed (tests; a re-seeded stack is already keyed by content hash)."""
    with _baseline_lock:
        _baseline_cache.clear()


def scaffold_baseline(job) -> dict[str, bytes] | None:
    """The skeleton an agent job started from (its pinned Stack row), or None.

    Reuses generation's own seeding so the baseline is byte-identical to the
    agent's seed; the caller verifies it against the workspace manifest.
    Seeding materialises the stack in a temp dir (tens of ms), and preflight
    / launch resolve every target of a study, so seeds are cached in-process
    (bounded LRU) under everything they depend on (:func:`_baseline_key`).
    """
    key = _baseline_key(job)
    if key is not None:
        with _baseline_lock:
            cached = _baseline_cache.get(key)
            if cached is not None:
                _baseline_cache.move_to_end(key)
                return dict(cached)
    try:
        from backend.generation.services.agent.workspace import seed_files

        seed = seed_files(job)
    except Exception:  # noqa: BLE001 - any failure means "no baseline", never a crash
        logger.warning("Scaffold baseline unavailable for job %s", getattr(job, "pk", None), exc_info=True)
        return None
    if key is not None:
        with _baseline_lock:
            _baseline_cache[key] = dict(seed)
            _baseline_cache.move_to_end(key)
            while len(_baseline_cache) > BASELINE_CACHE_SIZE:
                _baseline_cache.popitem(last=False)
    return seed


def resolve_job(job, scope: dict[str, Any] | None = None) -> Sample:
    """Resolve a GenerationJob. Raises SampleUnavailable (failed job, no code, unknown shape)."""
    status = getattr(job, "status", "")
    if status != "completed":
        msg = f"Generation job is {status or 'not finished'}: only completed jobs can be analysed"
        raise SampleUnavailable(msg, code="not_completed")
    result = job.result_data if isinstance(job.result_data, dict) else {}
    mode = getattr(job, "mode", "")
    eff = effective_scope(scope)
    if mode == "agent" or isinstance(result.get("agent"), dict):
        sample = _resolve_agent(job, result, eff)
    elif mode == "custom":
        sample = _resolve_custom(result, eff)
    elif mode == "copilot" and isinstance(result.get("files"), dict) and result.get("files"):
        sample = _resolve_copilot(job, result, eff)
    elif isinstance(result.get("files"), dict) and result.get("files"):
        warnings: list[str] = []
        specs = _specs_from_map(result["files"], "authored", warnings)
        source = "scaffolding" if mode == "scaffolding" else "legacy"
        sample = _assemble(specs, source=source, scope=eff, warnings=warnings)
    elif isinstance(result.get("backend_code"), str) or isinstance(result.get("frontend_code"), str):
        sample = _resolve_legacy(job, result, eff)
    else:
        msg = "The job's result has no code this resolver understands"
        raise SampleUnavailable(msg, code="unknown_shape" if result else "no_code")
    if not sample.files:
        msg = "The job produced no files"
        raise SampleUnavailable(msg, code="no_code")
    sample.meta = target_meta(job, sample=sample)
    return sample


def _specs_from_map(files: dict[str, Any], origin: str, warnings: list[str]) -> list[_Spec]:
    specs: list[_Spec] = []
    for raw_path, text in files.items():
        path = clean_path(raw_path)
        if path is None:
            warnings.append(f"skipped file with an unsafe path: {str(raw_path)[:120]!r}")
            continue
        if not isinstance(text, str):
            warnings.append(f"skipped {path}: content is not text")
            continue
        specs.append(_Spec(path, text.encode("utf-8"), origin))
    return specs


def _resolve_agent(job, result: dict[str, Any], scope: dict[str, Any]) -> Sample:
    files = result.get("files")
    if not isinstance(files, dict) or not files:
        msg = "The agent run left no collected files"
        raise SampleUnavailable(msg, code="no_code")
    agent = result.get("agent") if isinstance(result.get("agent"), dict) else {}
    workspace = agent.get("workspace") if isinstance(agent.get("workspace"), dict) else {}
    manifest = _clean_manifest(workspace.get("manifest"))
    warnings: list[str] = []
    specs = _specs_from_map(files, "unknown", warnings)

    needs_baseline = manifest is None or (
        scope.get("line_attribution") and any(_manifest_origin(manifest, s.path) == "modified" for s in specs)
    )
    baseline = scaffold_baseline(job) if needs_baseline else None

    if manifest is None:
        warnings.append("agent run has no workspace manifest: origins derived from the scaffold baseline")
        _classify_against_baseline(specs, baseline, warnings)
    else:
        for spec in specs:
            entry = manifest.get(spec.path)
            origin = _manifest_origin(manifest, spec.path)
            if origin is None:
                spec.origin = "unknown"
                warnings.append(f"{spec.path}: not in the workspace manifest (origin unknown)")
                continue
            spec.origin = origin
            expected = entry.get("sha256") if isinstance(entry, dict) else None
            if expected and hashlib.sha256(spec.content).hexdigest() != expected:
                warnings.append(f"{spec.path}: content sha256 differs from the workspace manifest")
        paths = {s.path for s in specs}
        missing = sum(1 for p in manifest if clean_path(p) not in paths)
        if missing:
            warnings.append(f"{missing} manifest file(s) missing from result files")
        if baseline is not None:
            problems = _baseline_problems(manifest, baseline)
            if problems:
                warnings.append(
                    "scaffold baseline does not reproduce the agent's seed ("
                    + "; ".join(problems[:5])
                    + "): modified files are attributed whole",
                )
                baseline = None
        elif needs_baseline and scope.get("line_attribution"):
            warnings.append("scaffold baseline unavailable: modified files are attributed whole")
    for skipped in workspace.get("skipped") or []:
        if isinstance(skipped, dict):
            warnings.append(
                f"{skipped.get('path')}: not collected by the generator ({skipped.get('reason')}), not analysed",
            )
    return _assemble(specs, source="agent", scope=scope, warnings=warnings, baseline=baseline)


def _clean_manifest(raw: Any) -> dict[str, Any] | None:
    """The workspace manifest keyed by cleaned paths (None when absent)."""
    if not isinstance(raw, dict):
        return None
    return {path: entry for key, entry in raw.items() if (path := clean_path(key)) is not None}


def _classify_against_baseline(specs: list[_Spec], baseline: dict[str, bytes] | None, warnings: list[str]) -> None:
    """Origins by comparison with the seed, exactly as ``agent.workspace.collect`` does."""
    if baseline is None:
        warnings.append("no scaffold baseline: every file has origin unknown (not measured)")
    for spec in specs:
        if baseline is None:
            spec.origin = "unknown"
        elif spec.path not in baseline:
            spec.origin = "added"
        else:
            spec.origin = "scaffold" if baseline[spec.path] == spec.content else "modified"


def _resolve_copilot(job, result: dict[str, Any], scope: dict[str, Any]) -> Sample:
    """Legacy copilot jobs: the workspace map, classified against the placeholder seed."""
    warnings: list[str] = ["copilot job: origins derived from the scaffold baseline (no manifest)"]
    specs = _specs_from_map(result["files"], "unknown", warnings)
    baseline = scaffold_baseline(job)
    _classify_against_baseline(specs, baseline, warnings)
    return _assemble(specs, source="legacy", scope=scope, warnings=warnings, baseline=baseline)


def _manifest_origin(manifest: dict[str, Any], path: str) -> str | None:
    entry = manifest.get(path)
    origin = entry.get("origin") if isinstance(entry, dict) else None
    return origin if origin in ("added", "modified", "scaffold") else None


def _baseline_problems(manifest: dict[str, Any], baseline: dict[str, bytes]) -> list[str]:
    """Inconsistencies between the reproduced seed and the generation-time manifest."""
    problems: list[str] = []
    for path, entry in sorted(manifest.items()):
        if not isinstance(entry, dict):
            continue
        origin = entry.get("origin")
        seeded = baseline.get(path)
        digest = hashlib.sha256(seeded).hexdigest() if seeded is not None else None
        if origin == "scaffold" and digest != entry.get("sha256"):
            problems.append(f"{path} differs from the scaffold")
        elif origin == "modified" and (seeded is None or digest == entry.get("sha256")):
            problems.append(f"{path} has no distinct scaffold version")
        elif origin == "added" and seeded is not None:
            problems.append(f"{path} exists in the scaffold")
    return problems


def _resolve_custom(result: dict[str, Any], scope: dict[str, Any]) -> Sample:
    content = result.get("content")
    if not isinstance(content, str) or not content.strip():
        msg = "The custom job has no response content"
        raise SampleUnavailable(msg, code="no_code")
    from backend.generation.services.code_parser import extract_code_blocks

    warnings: list[str] = []
    specs: list[_Spec] = []
    for index, block in enumerate(extract_code_blocks(content), 1):
        name = _block_filename(block)
        if not name:
            warnings.append(f"code block {index} ({block.get('language') or 'no language'}) has no file name: skipped")
            continue
        path = clean_path(name)
        if path is None:
            warnings.append(f"code block {index} has an unsafe file name {name[:120]!r}: skipped")
            continue
        specs.append(_Spec(path, (block.get("code") or "").encode("utf-8"), "authored"))
    if not specs:
        msg = "The response has no code block with a file name"
        raise SampleUnavailable(msg, code="no_code")
    return _assemble(specs, source="custom", scope=scope, warnings=warnings)


def _block_filename(block: dict[str, Any]) -> str:
    """The file name of a fenced block: ``lang:path``, ``title="path"``, or ``path`` as the info string."""
    raw = (block.get("filename") or "").strip().strip("`'\"")
    if raw:
        attr = _FILENAME_ATTR_RE.search(raw)
        if attr:
            return attr.group(1)
        return "" if any(ch.isspace() for ch in raw) else raw
    lang = (block.get("language") or "").strip()
    # ```app.py — the info string itself is a file name with a known extension.
    if "." in lang and detect_language(lang) != "other":
        return lang
    return ""


def _resolve_legacy(job, result: dict[str, Any], scope: dict[str, Any]) -> Sample:
    backend_name, component = _legacy_names(job)
    warnings: list[str] = []
    specs: list[_Spec] = []
    backend = result.get("backend_code")
    if isinstance(backend, str) and backend.strip():
        specs.append(_Spec(backend_name, backend.encode("utf-8"), "authored"))
    frontend = result.get("frontend_code")
    if isinstance(frontend, str) and frontend.strip():
        if "```" in frontend:
            warnings.append("frontend_code contains markdown fences (raw LLM output): analysed as-is")
        specs.append(_Spec(f"frontend/src/{component}", frontend.encode("utf-8"), "authored"))
    if not specs:
        msg = "The job has neither backend nor frontend code"
        raise SampleUnavailable(msg, code="no_code")
    return _assemble(specs, source="legacy", scope=scope, warnings=warnings)


def _legacy_names(job) -> tuple[str, str]:
    """(backend file, frontend component) of the job's stack; app.py / App.jsx by default."""
    try:
        from backend.runtime.services.scaffolding import get_stack_config
        from backend.runtime.services.scaffolding import resolve_stack_slug

        stack = get_stack_config(resolve_stack_slug(job))
    except Exception:  # noqa: BLE001 - an unknown stack keeps the documented defaults
        return "app.py", "App.jsx"
    backend = clean_path(stack.get("backend_filename") or "") or "app.py"
    component = clean_path(stack.get("frontend_component") or "") or "App.jsx"
    return backend, component


# --------------------------------------------------------------------------
# target_meta
# --------------------------------------------------------------------------


def target_meta(job, *, sample: Sample | None = None) -> dict[str, Any]:
    """Identity + quality snapshot of a job (stored on AnalysisRun.target_meta).

    Keys: job_id, mode, status, model_id, model_slug, model_name, model_provider,
    generator_family, harness, harness_version, stack_slug, app_slug, app_name,
    prompt_hash, bundle_key, config_fingerprint, experiment_id, condition_id,
    condition_label, batch_id, repeat_index, seed, created_at, termination,
    validity {valid, exclude, notes}, truncated (bool), truncation_reasons [..],
    empty_output (bool), functional {status, pass_rate|None, source, evaluator_version},
    generation {cost, total_tokens, duration_seconds}.

    Extra keys: model_canonical_slug, stack_version, method_key, truncation
    {truncated_calls|None, evidence}, validity.source (recomputed|stored|none|
    not_applicable), run_stopped (bool) + run_stop_reasons [..] (an agent run
    that ended at a run-level limit: ``termination:<t>``,
    ``budget_exhausted:<limit>``; never part of ``truncated``), attribution
    (:func:`attribution_summary`; None without a *sample*). ``truncated`` /
    ``truncation_reasons`` are output truncation only. Never raises for odd
    shapes: missing data is None. Pass the resolved *sample* to compute
    ``empty_output`` over its scope.
    """
    result = job.result_data if isinstance(job.result_data, dict) else {}
    bundle = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    agent = result.get("agent") if isinstance(result.get("agent"), dict) else None
    llm = bundle.get("llm") if isinstance(bundle.get("llm"), dict) else {}
    harness = bundle.get("harness") if isinstance(bundle.get("harness"), dict) else {}
    stack = bundle.get("stack") if isinstance(bundle.get("stack"), dict) else {}
    app = bundle.get("app_requirement") if isinstance(bundle.get("app_requirement"), dict) else {}
    model = getattr(job, "model", None)
    app_row = getattr(job, "app_requirement", None)
    condition = getattr(job, "condition", None)

    model_id = getattr(model, "model_id", None) or llm.get("model_slug")
    provider = getattr(model, "provider", None)
    if not provider and model_id and "/" in model_id:
        provider = model_id.split("/", 1)[0]

    truncation_reasons, truncation, run_stop_reasons = _truncation(job, result, agent)
    validity = _validity(job, bundle, result, agent, truncation.get("truncated_calls"))
    meta: dict[str, Any] = {
        "job_id": str(job.pk),
        "mode": job.mode,
        "status": job.status,
        "model_id": model_id,
        "model_slug": llm.get("model_slug") or model_id,
        "model_canonical_slug": llm.get("model_canonical_slug") or getattr(model, "canonical_slug", None),
        "model_name": getattr(model, "model_name", None),
        "model_provider": provider,
        # Reviewer self-preference (same family judging its own code) is a
        # known LLM-judge bias; the family is recorded to stratify on it.
        "generator_family": (provider or "").lower() or None,
        "harness": (agent or {}).get("harness") or harness.get("name") or None,
        "harness_version": (agent or {}).get("harness_version") or harness.get("version") or None,
        "stack_slug": stack.get("slug") or job.stack_slug or bundle.get("scaffolding_slug") or None,
        "stack_version": stack.get("version"),
        "app_slug": getattr(app_row, "slug", None) or app.get("slug"),
        "app_name": getattr(app_row, "name", None) or app.get("name"),
        "prompt_hash": job.prompt_hash or bundle.get("prompt_hash") or None,
        "bundle_key": job.bundle_key or None,
        "method_key": bundle.get("method_key"),
        "config_fingerprint": bundle.get("config_fingerprint"),
        "experiment_id": _str_or_none(job.experiment_id),
        "condition_id": _str_or_none(job.condition_id),
        "condition_label": getattr(condition, "label", None) or None,
        "batch_id": _str_or_none(job.batch_id),
        "repeat_index": job.repeat_index,
        "seed": job.experiment_seed if job.experiment_seed is not None else bundle.get("seed"),
        "created_at": job.created_at.isoformat() if getattr(job, "created_at", None) else None,
        "termination": (agent or {}).get("termination"),
        "validity": validity,
        "truncated": bool(truncation_reasons),
        "truncation_reasons": truncation_reasons,
        "truncation": truncation,
        "run_stopped": bool(run_stop_reasons),
        "run_stop_reasons": run_stop_reasons,
        "empty_output": _empty_output(job, result, agent, sample),
        "attribution": attribution_summary(sample) if sample is not None else None,
        "functional": functional_from_job(job),
        "generation": {
            "cost": _num(metrics.get("cost")),
            "total_tokens": _num(metrics.get("total_tokens")),
            "duration_seconds": _num(job.duration_seconds)
            if job.duration_seconds is not None
            else _num(metrics.get("duration_seconds")),
        },
    }
    return meta


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _num(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _functional_none() -> dict[str, Any]:
    return {"status": "not_evaluated", "pass_rate": None, "source": None, "evaluator_version": None}


def functional_from_job(job) -> dict[str, Any]:
    """None-safe conformance snapshot: ``pass_rate`` is None unless it is a real rate.

    Only the conformance evaluator's own record counts: ``result_data
    ["evaluation"]`` (status, ``summary.pass_rate``, evaluator version), else
    a ``metrics["functional"]`` whose ``source`` is ``conformance`` (both are
    written together by ``evaluation.runner.evaluate_job``). The runtime smoke
    probe (``runtime.services.smoke``) overwrites ``metrics["functional"]``
    with a source-less GET probe whenever an app is launched: a different,
    more lenient instrument whose rate must never stand in for, or be mixed
    with, the conformance pass rate. Status, rate and provenance always come
    from the same record. The generator stores ``pass_rate=None`` for infra
    errors and ``0.0`` for jobs without an app spec (``not_applicable``);
    both are missing data.
    """
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    result = job.result_data if isinstance(job.result_data, dict) else {}
    functional = metrics.get("functional") if isinstance(metrics.get("functional"), dict) else {}
    conformance = functional if functional.get("source") == "conformance" else {}
    evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), dict) else {}
    if evaluation:
        summary = evaluation.get("summary") if isinstance(evaluation.get("summary"), dict) else {}
        status = evaluation.get("status") or "not_evaluated"
        rate = summary.get("pass_rate")
        version = evaluation.get("evaluator_version")
        stated = evaluation.get("contract_stated_in_prompt")
        if stated is None and conformance.get("evaluator_version") == version:
            stated = conformance.get("contract_stated_in_prompt")
    elif conformance:
        status = conformance.get("status") or "not_evaluated"
        rate = conformance.get("pass_rate")
        version = conformance.get("evaluator_version")
        stated = conformance.get("contract_stated_in_prompt")
    else:
        return _functional_none()
    pass_rate = None
    if status not in ("infra_error", "not_applicable", "not_evaluated") and _num(rate) is not None:
        value = float(rate)
        pass_rate = value if 0.0 <= value <= 1.0 else None
    return {
        "status": status,
        "pass_rate": pass_rate,
        "source": "conformance",
        "evaluator_version": version,
        "contract_stated_in_prompt": stated,
    }


def _truncation(
    job,
    result: dict[str, Any],
    agent: dict[str, Any] | None,
) -> tuple[list[str], dict[str, Any], list[str]]:
    """(output-truncation codes, {truncated_calls, evidence}, run-stop codes) for every job mode.

    Output truncation is a defect of individual responses (cut at the output
    cap). A run-level stop (wall clock, budget, memory cap) ends a run whose
    sample the generator keeps as valid: it is reported apart, never as
    truncation (see the module docstring).
    """
    reasons: list[str] = []
    stops: list[str] = []
    info: dict[str, Any] = {"truncated_calls": None, "evidence": "not_applicable"}
    if agent is not None:
        calls, evidence = agent_truncated_calls(job, agent)
        info = {"truncated_calls": calls, "evidence": evidence}
        if calls:
            reasons.append("truncated_calls")
        termination = agent.get("termination")
        if termination in RUN_LIMIT_TERMINATIONS:
            stops.append(f"termination:{termination}")
        exhausted = agent.get("budget_exhausted")
        if exhausted:
            stops.append(f"budget_exhausted:{exhausted}")
        return reasons, info, stops
    if result.get("backend_truncated") is True:
        reasons.append("backend_truncated")
    if result.get("frontend_truncated") is True:
        reasons.append("frontend_truncated")
    if result.get("truncated") is True:
        reasons.append("output_truncated")
    if result.get("finish_reason") == "length" and "output_truncated" not in reasons:
        reasons.append("finish_reason:length")
    known = any(k in result for k in ("backend_truncated", "frontend_truncated", "truncated", "finish_reason"))
    info["evidence"] = "result_data" if known else "unavailable"
    return reasons, info, stops


def truncation_flags(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Output truncation and run-level stops of a target_meta, old rows included.

    ``{"truncated", "truncation_reasons", "run_stopped", "run_stop_reasons"}``.
    target_meta rows written before run stops were split out listed
    ``termination:*`` / ``budget_exhausted:*`` in ``truncation_reasons`` (and
    set ``truncated`` for them); those codes are moved to ``run_stop_reasons``
    here, so every consumer applies one rule to old and new rows.
    """
    meta = meta if isinstance(meta, dict) else {}
    reasons = [str(r) for r in meta.get("truncation_reasons") or []]
    output = [r for r in reasons if not r.startswith(RUN_STOP_PREFIXES)]
    stops = [r for r in reasons if r.startswith(RUN_STOP_PREFIXES)]
    stops += [str(r) for r in meta.get("run_stop_reasons") or [] if str(r) not in stops]
    # A truncated flag without any reason code is kept (unknown cause, not "clean").
    truncated = bool(output) or (bool(meta.get("truncated")) and not reasons)
    return {
        "truncated": truncated,
        "truncation_reasons": output,
        "run_stopped": bool(stops) or bool(meta.get("run_stopped")),
        "run_stop_reasons": stops,
    }


def agent_truncated_calls(job, agent: dict[str, Any]) -> tuple[int | None, str]:
    """Responses cut at the output cap: (count | None, evidence source).

    Newer runs store ``truncated_calls``; older ones are backfilled from the
    sha256-verified ``llm_calls.jsonl.gz`` run record (same definition as the
    generator: successful calls whose ``finish_reason`` is ``length``), then
    from the harness's own finish-reason tally. None = unknown.
    """
    stored = agent.get("truncated_calls")
    if isinstance(stored, int) and not isinstance(stored, bool):
        return stored, "result_data"
    counted = _run_record_truncated_calls(job, agent)
    if counted is not None:
        return counted, "run_record"
    report = agent.get("harness_report") if isinstance(agent.get("harness_report"), dict) else {}
    reasons = report.get("finish_reasons") if isinstance(report.get("finish_reasons"), dict) else {}
    if reasons:
        return int(reasons.get("length") or 0), "harness_report"
    return None, "unavailable"


_MAX_CALL_LOG_BYTES = 256 * 1024 * 1024


def _run_record_truncated_calls(job, agent: dict[str, Any]) -> int | None:
    record = agent.get("run_record") if isinstance(agent.get("run_record"), dict) else {}
    entry = record.get("llm_calls.jsonl.gz") if isinstance(record.get("llm_calls.jsonl.gz"), dict) else {}
    expected = entry.get("sha256")
    if not expected or not isinstance(expected, str):
        return None
    from django.conf import settings

    path = str(Path(settings.GENERATION_RUNS_ROOT) / str(job.pk) / "llm_calls.jsonl.gz")
    key = (path, expected)
    if key in _TRUNCATION_CACHE:
        return _TRUNCATION_CACHE[key]
    counted = _count_truncated_calls(path, expected)
    if counted is not None:
        # Run records are immutable evidence and reports resolve target_meta
        # for many jobs at once; only verified counts are cached.
        if len(_TRUNCATION_CACHE) >= _TRUNCATION_CACHE_SIZE:
            _TRUNCATION_CACHE.clear()
        _TRUNCATION_CACHE[key] = counted
    return counted


_TRUNCATION_CACHE: dict[tuple[str, str], int] = {}
_TRUNCATION_CACHE_SIZE = 1024


def _count_truncated_calls(path: str, expected_sha256: str) -> int | None:
    """Length-truncated successful calls in a call log, None unless its sha256 verifies."""
    try:
        file = Path(path)
        if not file.is_file() or file.stat().st_size > _MAX_CALL_LOG_BYTES:
            return None
        raw = file.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            logger.warning("Run record %s fails its sha256", path)
            return None
        truncated = 0
        for line in gzip.decompress(raw).splitlines():
            if not line.strip():
                continue
            call = json.loads(line)
            response = call.get("response") if isinstance(call.get("response"), dict) else {}
            if call.get("status") == 200 and not response.get("error") and response.get("finish_reason") == "length":
                truncated += 1
        return truncated
    except (OSError, ValueError, EOFError, AttributeError):
        logger.warning("Could not read the LLM call log %s", path, exc_info=True)
        return None


def _validity(
    job,
    bundle: dict[str, Any],
    result: dict[str, Any],
    agent: dict[str, Any] | None,
    truncated_calls: int | None,
) -> dict[str, Any]:
    """Agent sample validity, recomputed with the generator's current rules.

    Recomputing (``agent.runner.assess_validity``, a pure function) applies
    one rule set to every job of a study, including rows written before a
    rule existed (e.g. ``truncated_calls`` backfilled from the run record).
    """
    if agent is None:
        return {"valid": True, "exclude": False, "notes": [], "source": "not_applicable"}
    stored = agent.get("validity") if isinstance(agent.get("validity"), dict) else None
    assessed: dict[str, Any] | None = None
    source = "none"
    try:
        from backend.generation.services.agent.runner import assess_validity

        patched_agent = dict(agent)
        if truncated_calls is not None and "truncated_calls" not in agent:
            patched_agent["truncated_calls"] = truncated_calls
        assessed = assess_validity(bundle, {**result, "agent": patched_agent})
        source = "recomputed"
    except Exception:  # noqa: BLE001 - fall back to the stored verdict
        logger.warning("assess_validity failed for job %s", getattr(job, "pk", None), exc_info=True)
        if stored is not None:
            assessed = stored
            source = "stored"
    if assessed is None:
        return {"valid": None, "exclude": False, "notes": [], "source": source}
    notes = [n for n in assessed.get("notes") or [] if isinstance(n, dict)]
    exclude = any(n.get("severity") == "exclude" for n in notes) or assessed.get("valid") is False
    out = {"valid": not exclude, "exclude": exclude, "notes": notes, "source": source}
    if stored is not None and source == "recomputed":
        out["stored_valid"] = stored.get("valid")
    return out


def _empty_output(job, result: dict[str, Any], agent: dict[str, Any] | None, sample: Sample | None) -> bool:
    """True when the job left no source code the model wrote."""
    if sample is not None:
        return not model_files(sample)
    for path, origin in _job_file_origins(job, result, agent):
        language = detect_language(path)
        if origin in MODEL_ORIGINS and detect_kind(path, language) == MEASURED_KIND:
            return False
    return True


def _job_file_origins(job, result: dict[str, Any], agent: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(path, origin) per file without resolving content (never raises)."""
    files = result.get("files") if isinstance(result.get("files"), dict) else {}
    if agent is not None:
        workspace = agent.get("workspace") if isinstance(agent.get("workspace"), dict) else {}
        manifest = _clean_manifest(workspace.get("manifest"))
        if manifest is None:
            return [(p, "unknown") for p in files]
        return [(p, _manifest_origin(manifest, p) or "unknown") for p in files]
    if job.mode == "custom":
        content = result.get("content")
        if not isinstance(content, str):
            return []
        from backend.generation.services.code_parser import extract_code_blocks

        return [(name, "authored") for b in extract_code_blocks(content) if (name := _block_filename(b))]
    if files:
        return [(p, "authored") for p, text in files.items() if isinstance(text, str)]
    backend_name, component = _legacy_names(job)
    out = []
    if isinstance(result.get("backend_code"), str) and result["backend_code"].strip():
        out.append((backend_name, "authored"))
    if isinstance(result.get("frontend_code"), str) and result["frontend_code"].strip():
        out.append((f"frontend/src/{component}", "authored"))
    return out


# --------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------


def check_eligibility(
    meta: dict[str, Any],
    sample: Sample | None,
    rules: dict[str, Any] | None = None,
    *,
    manifest: dict[str, Any] | None = None,
) -> dict:
    """``{"eligible": bool, "reasons": [{"code", "severity": "exclude"|"warn"|"info", "detail"}]}``.

    A rule that is switched off downgrades its reason to ``warn``: the flag is
    still reported, it just does not exclude. *manifest* (a stored
    ``sample_manifest``) stands in for *sample* when only the run row exists.
    """
    eff = {**DEFAULT_EXCLUSION_RULES, **(rules or {})}
    reasons: list[dict[str, str]] = []

    def add(code: str, severity: str, detail: str) -> None:
        reasons.append({"code": code, "severity": severity, "detail": detail})

    meta = meta or {}
    status = meta.get("status")
    if status not in (None, "completed"):
        add("not_completed", "exclude", f"generation job is {status}")

    validity = meta.get("validity") if isinstance(meta.get("validity"), dict) else {}
    notes = [n for n in validity.get("notes") or [] if isinstance(n, dict)]
    if validity.get("exclude"):
        codes = ", ".join(n.get("code", "") for n in notes if n.get("severity") == "exclude") or "invalid"
        add("invalid", "exclude" if eff["exclude_invalid"] else "warn", f"sample validity: {codes}")
    for note in notes:
        if note.get("severity") == "warn":
            add(f"validity:{note.get('code')}", "warn", str(note.get("detail") or ""))

    if meta.get("empty_output"):
        add(
            "empty_output",
            "exclude" if eff["exclude_empty_output"] else "warn",
            "the model wrote no source code (every source file is scaffold/platform or none exists)",
        )
    truncation = truncation_flags(meta)
    if truncation["truncated"]:
        add(
            "truncated",
            "exclude" if eff["exclude_truncated"] else "warn",
            "generation output was cut off: " + (", ".join(truncation["truncation_reasons"]) or "truncated"),
        )
    elif (meta.get("truncation") or {}).get("evidence") == "unavailable":
        add("truncation_unknown", "warn", "whether the generation output was truncated is unknown")
    if truncation["run_stopped"]:
        # A valid sample (the generator keeps it; its validity notes rate the
        # limit). Excluding it would favour models that finish quickly.
        add(
            "run_stopped",
            "exclude" if eff.get("exclude_run_limits") else "info",
            "the agent run ended at a run-level limit: " + (", ".join(truncation["run_stop_reasons"]) or "stopped"),
        )

    attribution = _attribution_of(meta, sample, manifest)
    if attribution.get("mode") == "whole_file":
        paths = ", ".join(attribution.get("whole_file") or []) or "modified files"
        add(
            "whole_file_attribution",
            "warn",
            f"no verified scaffold baseline for {paths}: their unchanged scaffold lines count as the model's",
        )

    measured = _measured_sloc(sample, manifest)
    minimum = eff.get("min_measured_sloc") or 0
    if measured is not None and minimum and measured < minimum:
        add("below_min_sloc", "exclude", f"measured SLOC {measured} < {minimum}")

    functional = meta.get("functional") if isinstance(meta.get("functional"), dict) else {}
    if functional.get("pass_rate") is None:
        add(
            "functional_unknown",
            "exclude" if eff["require_functional"] else "info",
            f"no functional pass rate (status {functional.get('status') or 'not_evaluated'})",
        )
    return {"eligible": not any(r["severity"] == "exclude" for r in reasons), "reasons": reasons}


def _attribution_of(meta: dict[str, Any], sample: Sample | None, manifest: dict[str, Any] | None) -> dict[str, Any]:
    """The attribution summary from the sample, else the stored manifest, else target_meta."""
    if sample is not None:
        return attribution_summary(sample)
    for source in ((manifest or {}).get("attribution"), meta.get("attribution")):
        if isinstance(source, dict):
            return source
    return {}


def _measured_sloc(sample: Sample | None, manifest: dict[str, Any] | None) -> int | None:
    if sample is not None:
        return sum(f.measured_sloc for f in sample.files if f.included and f.measured)
    totals = (manifest or {}).get("totals")
    if isinstance(totals, dict) and isinstance(totals.get("measured_sloc"), int):
        return totals["measured_sloc"]
    return None
