"""LOC counting from generation job result_data text fields."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.services.base import is_non_source_path

if TYPE_CHECKING:
    from backend.generation.models import GenerationJob


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
        if not isinstance(content, str) or not is_source_file(rel_path) or is_test_path(rel_path):
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
        if isinstance(content, str) and is_source_file(rel_path) and is_test_path(rel_path):
            test_loc += _count_lines(content, _COMMENT_PREFIX_BY_EXT.get(_ext(rel_path), "#"))
            test_files += 1
    return {"test_loc": test_loc, "test_files": test_files}


def loc_from_job(job: GenerationJob) -> dict[str, int | None]:
    """Source LOC of a job's generated code — the one denominator every per-KLOC rate uses."""
    return loc_from_result_data(job.result_data)


def loc_from_result_data(data: Any) -> dict[str, int | None]:
    """:func:`loc_from_job` for a raw ``result_data`` value (e.g. from ``values_list``).

    ``total_loc`` (the per-kLOC denominator) leaves test code out; ``test_loc``
    and ``test_files`` count it, and are ``None`` for a single-blob result
    (no paths, so its test code cannot be told apart: unknown, not zero).
    """
    data = data if isinstance(data, dict) else {}
    files = data.get("files")
    if isinstance(files, dict) and files:
        return {**loc_from_files(files), **loc_of_test_files(files)}

    backend = data.get("backend_code") or data.get("backend") or ""
    frontend = data.get("frontend_code") or data.get("frontend") or ""
    backend_loc = _count_lines(backend, "#") if backend else 0
    frontend_loc = _count_lines(frontend, "//") if frontend else 0
    return {
        "backend_loc": backend_loc,
        "frontend_loc": frontend_loc,
        "total_loc": backend_loc + frontend_loc,
        "source_files": int(bool(backend)) + int(bool(frontend)),
        # Single-blob results carry no paths: no file can be told apart as a test.
        "test_loc": None,
        "test_files": None,
    }


def loc_for_jobs(jobs) -> dict[str, Any]:
    """Aggregate LOC across a queryset / iterable of GenerationJobs.

    ``test_loc`` sums the jobs whose test code is known; ``None`` when none is.
    """

    totals: dict[str, Any] = {"backend_loc": 0, "frontend_loc": 0, "total_loc": 0, "test_loc": None}
    per_job: list[dict[str, Any]] = []
    counted = 0
    for job in jobs:
        loc = loc_from_job(job)
        if loc["total_loc"] > 0:
            counted += 1
        for k in ("backend_loc", "frontend_loc", "total_loc"):
            totals[k] += loc[k]
        if loc["test_loc"] is not None:
            totals["test_loc"] = (totals["test_loc"] or 0) + loc["test_loc"]
        per_job.append({"job_id": str(job.id), **loc})
    totals["counted"] = counted
    totals["per_job"] = per_job
    return totals
