"""Source LOC: the one size measure (and test-code classifier) every study table uses.

* :func:`source_loc` is the LOC of one file: non-blank, non-comment lines of a source file, 0 for test
  code, manifests, lockfiles, docs and other non-source files. The collector stores it in
  ``RunFile.loc``.
* :func:`loc_from_run` / :func:`loc_by_run` / :func:`loc_for_runs` read a run's **model** scope through
  ``RunView`` (``authored``, ``modified`` and ``slot`` rows, excluded rows left out), so starter code,
  artifacts, vendored code and lockfiles never count. A run whose tree was never collected has ``None``
  everywhere: unknown, never a silent 0.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.services.base import is_generated_bundle
from backend.analysis.services.base import is_non_source_path

if TYPE_CHECKING:
    from collections.abc import Iterable

    from backend.generation.models import GenerationRun
    from backend.generation.runs.view import FileEntry
    from backend.generation.runs.view import RunView


_COMMENT_PREFIX_BY_EXT = {
    ".py": "#",
    ".jsx": "//",
    ".tsx": "//",
    ".js": "//",
    ".mjs": "//",
    ".cjs": "//",
    ".ts": "//",
    ".vue": "//",
    ".svelte": "//",
    ".css": "/*",
    ".scss": "//",
    ".html": "<!--",
    ".sh": "#",
}
# Source files: what the model wrote as program code. Manifests, lockfiles,
# docs and configs (.json, .lock, .md, .txt, .env, .yaml) are not, and a
# generated package-lock.json alone can outweigh the whole app, so counting
# them would make issue densities (per KLOC) incomparable between apps.
SOURCE_EXTENSIONS = frozenset(_COMMENT_PREFIX_BY_EXT)
_FRONTEND_EXTENSIONS = frozenset(
    {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".css", ".scss", ".html"},
)


# Test code the model wrote next to the app (tests/, test_*.py, *.spec.ts, smoke
# scripts). Its findings (test passwords, urlopen against localhost) and its
# lines are not the delivered app: study tables count them apart.
_TEST_DIR_RE = re.compile(r"(^|/)(tests?|__tests__|e2e|cypress|playwright)/")
_TEST_JS_RE = re.compile(r"\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs|mts|cts)$")


def is_test_path(path: str) -> bool:
    """Whether *path* is test code: a test directory, a test module, a JS/TS test file, a test setup or a smoke script.

    The one classifier every study table uses, for findings and for LOC alike.
    """
    p = (path or "").replace("\\", "/").lower()
    if not p:
        return False
    if _TEST_DIR_RE.search(p):
        return True
    name = p.rsplit("/", 1)[-1]
    return (
        (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.py")
        or name == "conftest.py"
        or bool(_TEST_JS_RE.search(name))
        or name.startswith("setuptests.")
        or "smoke" in name
    )


# Scratch and verification scripts an agent leaves next to the app: ``.tmp_test_api.py``,
# ``.scratch/edge.py``, ``check_api.py``, ``probe_xml.py``, ``npmprobe/package.json``. They are code the
# model wrote, but not the delivered app, and :func:`is_test_path` does not know their names. In the
# 2026-11 study they held about 10% of the findings counted as app code, between 0 and 235 per model.
# Hidden paths are the safe part of the rule; the names can misfire on a real module (a task about
# "checks"), so the two parts can be switched separately (:data:`CODE_SCOPES`).
_PROJECT_DOTFILES = (
    ".eslintrc",
    ".prettierrc",
    ".babelrc",
    ".stylelintrc",
    ".env",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
    ".npmrc",
    ".nvmrc",
    ".flake8",
    ".pylintrc",
    ".coveragerc",
    ".browserslistrc",
)
_PROJECT_DOTDIRS = frozenset({".github", ".husky", ".vscode", ".storybook", ".config"})
_AUX_STEM_RE = re.compile(
    r"^(check|verify|probe|debug|scratch|repro|tmp)[_\-]"
    r"|^(tmp|scratch)[a-z0-9_\-]*$"
    r"|^temp[_\-]"
    r"|[_\-](check|checks|probe|verify)\d*$"
    r"|^(probe|apitest|api_?test)\d*$",
)
_AUX_DIR_RE = re.compile(r"^(tmp|temp$|scratch|probe)|probe$")


def is_hidden_scratch_path(path: str) -> bool:
    """Whether *path* has a hidden component that is not a conventional project dotfile or directory."""
    for part in (path or "").replace("\\", "/").lower().strip("/").split("/"):
        if not part.startswith(".") or part in (".", ".."):
            continue
        if part in _PROJECT_DOTDIRS or part.startswith(_PROJECT_DOTFILES):
            continue
        return True
    return False


def is_named_scratch_path(path: str) -> bool:
    """Whether *path* is named like a throwaway check (``check_api.py``, ``probe_xml.py``, ``tmpjsdom/x.mjs``)."""
    parts = (path or "").replace("\\", "/").lower().strip("/").split("/")
    if not parts or not parts[-1]:
        return False
    if any(_AUX_DIR_RE.search(part) for part in parts[:-1]):
        return True
    stem = parts[-1].split(".", 1)[0] if not parts[-1].startswith(".") else parts[-1][1:].split(".", 1)[0]
    return bool(_AUX_STEM_RE.search(stem))


def is_auxiliary_path(path: str, *, names: bool = True) -> bool:
    """Whether *path* is an agent's scratch or verification script (and not test code by :func:`is_test_path`)."""
    if not path or is_test_path(path):
        return False
    return is_hidden_scratch_path(path) or (names and is_named_scratch_path(path))


# What the study tables count as the delivered app's code (findings and LOC alike):
#   app          tests and scratch scripts (hidden paths and check/probe/tmp names) are counted apart
#   app_named    tests and hidden scratch paths are counted apart; scripts matched by name only stay
#                app code (the sensitivity check for the name rule)
#   with_scratch only tests are counted apart (the rule before 2026-11-12)
#   all          everything the model wrote counts as app code, tests included
CODE_SCOPES = ("app", "app_named", "with_scratch", "all")
DEFAULT_CODE_SCOPE = "app"
_code_scope_override: ContextVar[str | None] = ContextVar("study_code_scope", default=None)


def code_scope() -> str:
    """The scope in force: a :func:`use_code_scope` override, else ``settings.STUDY_CODE_SCOPE``."""
    from django.conf import settings

    scope = _code_scope_override.get() or getattr(settings, "STUDY_CODE_SCOPE", DEFAULT_CODE_SCOPE)
    return scope if scope in CODE_SCOPES else DEFAULT_CODE_SCOPE


@contextmanager
def use_code_scope(scope: str | None):
    """Count with *scope* (one of :data:`CODE_SCOPES`; None keeps the configured one) inside the block."""
    if scope is not None and scope not in CODE_SCOPES:
        msg = f"unknown code scope {scope!r}; one of {', '.join(CODE_SCOPES)}"
        raise ValueError(msg)
    token = _code_scope_override.set(scope) if scope is not None else None
    try:
        yield code_scope()
    finally:
        if token is not None:
            _code_scope_override.reset(token)


def is_outside_app(path: str) -> bool:
    """Whether the study counts *path* apart from the delivered app under the scope in force.

    The classifier of the analysis and statistics tables. Generation-side code (the collector's
    ``RunFile.is_test``, slot context) keeps :func:`is_test_path`, which never changes with the scope.
    """
    scope = code_scope()
    if scope == "all":
        return False
    if is_test_path(path):
        return True
    if scope == "with_scratch":
        return False
    return is_auxiliary_path(path, names=scope == "app")


def _count_lines(text: str | None, comment_prefix: str = "#") -> int:
    if not text:
        return 0
    return sum(1 for line in str(text).splitlines() if line.strip() and not line.strip().startswith(comment_prefix))


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    return "." + name.rsplit(".", 1)[-1] if "." in name else ""


def is_source_file(path: str) -> bool:
    """Whether *path* is counted as source code (not a manifest, lockfile, doc or minified bundle)."""
    name = path.rsplit("/", 1)[-1].lower()
    return _ext(path) in SOURCE_EXTENSIONS and ".min." not in name and not is_non_source_path(path)


def loc_from_files(files: dict[str, Any]) -> dict[str, int]:
    """Source LOC (non-empty, non-comment lines of source files) of a ``{path: text}`` map.

    Frontend is everything under ``frontend/`` plus web-asset files elsewhere
    (``static/app.js``, ``templates/*.html``); backend is the rest. Test code
    (:func:`is_test_path`) is not counted here; :func:`loc_of_test_files`
    counts it.
    """
    backend_loc = frontend_loc = source_files = 0
    for rel_path, content in files.items():
        if not isinstance(content, str) or not is_source_file(rel_path) or is_outside_app(rel_path):
            continue
        if is_generated_bundle(rel_path, content):  # bundler output is not the app's source
            continue
        ext = _ext(rel_path)
        lines = _count_lines(content, _COMMENT_PREFIX_BY_EXT.get(ext, "#"))
        source_files += 1
        if rel_path.startswith("frontend/") or ext in _FRONTEND_EXTENSIONS:
            frontend_loc += lines
        else:
            backend_loc += lines
    return {
        "backend_loc": backend_loc,
        "frontend_loc": frontend_loc,
        "total_loc": backend_loc + frontend_loc,
        "source_files": source_files,
    }


def loc_of_test_files(files: dict[str, Any]) -> dict[str, int]:
    """LOC and file count of the test code (:func:`is_test_path`) in a ``{path: text}`` map."""
    test_loc = test_files = 0
    for rel_path, content in files.items():
        if (
            isinstance(content, str)
            and is_source_file(rel_path)
            and is_outside_app(rel_path)
            and not is_generated_bundle(rel_path, content)
        ):
            test_loc += _count_lines(content, _COMMENT_PREFIX_BY_EXT.get(_ext(rel_path), "#"))
            test_files += 1
    return {"test_loc": test_loc, "test_files": test_files}


def source_loc(path: str, text: str | None) -> int:
    """Source LOC of one file: non-blank, non-comment lines; 0 for tests and every non-source file.

    The value the collector stores in ``RunFile.loc`` and the unit of every per-kLOC rate.
    """
    if not isinstance(text, str) or not text or is_test_path(path) or not is_source_file(path):
        return 0
    return _count_lines(text, _COMMENT_PREFIX_BY_EXT.get(_ext(path), "#"))


def _is_layer_source(entry: FileEntry) -> bool:
    """A model-scope row whose stored LOC counts (the ``RunView.loc`` rules)."""
    return not entry.excluded and not entry.is_test and is_source_file(entry.path) and not is_outside_app(entry.path)


def _is_test_source(entry: FileEntry) -> bool:
    if code_scope() == "all":
        return False
    return not entry.excluded and is_source_file(entry.path) and (entry.is_test or is_outside_app(entry.path))


def _is_test_counted_as_app(entry: FileEntry) -> bool:
    """Scope ``all``: a test file counts as app code. Its stored LOC is 0, so its lines are read from the blob."""
    return (
        code_scope() == "all"
        and not entry.excluded
        and is_source_file(entry.path)
        and (entry.is_test or is_test_path(entry.path))
    )


_UNKNOWN: dict[str, int | None] = dict.fromkeys(
    ("backend_loc", "frontend_loc", "total_loc", "source_files", "test_loc", "test_files"),
)


def loc_from_view(view: RunView) -> dict[str, int | None]:
    """Source LOC of a run's **model** scope by layer, plus its test code counted apart.

    ``total_loc`` is the stored ``RunFile.loc`` of the source rows the scope in force counts as the app
    (:func:`is_outside_app`; under ``with_scratch`` it equals ``RunView.loc("model")``). Frontend
    is everything under ``frontend/`` plus web-asset files elsewhere; backend is the rest. ``test_loc``
    counts the test files' lines (read from their blobs). Every value is ``None`` when the run has no
    collected tree.
    """
    entries = view.files("model")
    # No model rows: a collected tree without model code is a measured 0, no tree at all is unknown.
    if not entries and view.loc("model") is None:
        return dict(_UNKNOWN)
    backend_loc = frontend_loc = source_files = 0
    tests = []
    tests_as_app = []
    for entry in entries:
        if _is_test_source(entry):
            tests.append(entry.path)
            continue
        lines = entry.loc
        if _is_test_counted_as_app(entry):
            tests_as_app.append(entry.path)
            lines = 0  # added below from the blob
        elif not _is_layer_source(entry):
            continue
        source_files += 1
        if entry.path.startswith("frontend/") or _ext(entry.path) in _FRONTEND_EXTENSIONS:
            frontend_loc += lines
        else:
            backend_loc += lines
    test = {"test_loc": 0, "test_files": 0}
    if tests or tests_as_app:
        texts = view.code_files("model")
        if tests:
            test = loc_of_test_files({path: texts.get(path) for path in tests})
        for path in tests_as_app:
            lines = _count_lines(texts.get(path), _COMMENT_PREFIX_BY_EXT.get(_ext(path), "#"))
            if path.startswith("frontend/") or _ext(path) in _FRONTEND_EXTENSIONS:
                frontend_loc += lines
            else:
                backend_loc += lines
    return {
        "backend_loc": backend_loc,
        "frontend_loc": frontend_loc,
        "total_loc": backend_loc + frontend_loc,
        "source_files": source_files,
        **test,
    }


def loc_from_run(run: GenerationRun) -> dict[str, int | None]:
    """:func:`loc_from_view` of one run (model scope; ``None`` values without a collected tree)."""
    from backend.generation.runs.view import run_view

    return loc_from_view(run_view(run))


def loc_by_run(runs: Iterable[GenerationRun] | Any) -> dict[str, dict[str, int | None]]:
    """``{run_id: loc_from_view(...)}`` for many runs (one view query; one file query per run)."""
    from backend.generation.runs.view import run_views

    return {view.id: loc_from_view(view) for view in run_views(runs)}


def loc_for_runs(runs: Iterable[GenerationRun] | Any) -> dict[str, Any]:
    """Aggregate LOC across runs; runs without a collected tree are listed but not summed.

    ``counted`` is the number of runs with a known LOC; totals are ``None`` when no run has one.
    """
    per_run: list[dict[str, Any]] = []
    totals: dict[str, Any] = {"backend_loc": None, "frontend_loc": None, "total_loc": None, "test_loc": None}
    counted = 0
    for run_id, loc in loc_by_run(runs).items():
        per_run.append({"run_id": run_id, **loc})
        if loc["total_loc"] is None:
            continue
        counted += 1
        for key, value in totals.items():
            totals[key] = (value or 0) + (loc[key] or 0)
    totals["counted"] = counted
    totals["per_run"] = per_run
    return totals
