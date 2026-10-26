"""LOC counting from generation job result_data text fields."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

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
    return _ext(path) in SOURCE_EXTENSIONS and ".min." not in name and "node_modules/" not in path


def loc_from_files(files: dict[str, Any]) -> dict[str, int]:
    """Source LOC (non-empty, non-comment lines of source files) of a ``{path: text}`` map.

    Frontend is everything under ``frontend/`` plus web-asset files elsewhere
    (``static/app.js``, ``templates/*.html``); backend is the rest.
    """
    backend_loc = frontend_loc = source_files = 0
    for rel_path, content in files.items():
        if not isinstance(content, str) or not is_source_file(rel_path):
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


def loc_from_job(job: GenerationJob) -> dict[str, int]:
    """Source LOC of a job's generated code — the one denominator every per-KLOC rate uses."""
    return loc_from_result_data(job.result_data)


def loc_from_result_data(data: Any) -> dict[str, int]:
    """:func:`loc_from_job` for a raw ``result_data`` value (e.g. from ``values_list``)."""
    data = data if isinstance(data, dict) else {}
    files = data.get("files")
    if isinstance(files, dict) and files:
        return loc_from_files(files)

    backend = data.get("backend_code") or data.get("backend") or ""
    frontend = data.get("frontend_code") or data.get("frontend") or ""
    backend_loc = _count_lines(backend, "#") if backend else 0
    frontend_loc = _count_lines(frontend, "//") if frontend else 0
    return {
        "backend_loc": backend_loc,
        "frontend_loc": frontend_loc,
        "total_loc": backend_loc + frontend_loc,
        "source_files": int(bool(backend)) + int(bool(frontend)),
    }


def loc_for_jobs(jobs) -> dict[str, Any]:
    """Aggregate LOC across a queryset / iterable of GenerationJobs."""

    totals = {"backend_loc": 0, "frontend_loc": 0, "total_loc": 0}
    per_job: list[dict[str, Any]] = []
    counted = 0
    for job in jobs:
        loc = loc_from_job(job)
        if loc["total_loc"] > 0:
            counted += 1
        for k in totals:
            totals[k] += loc[k]
        per_job.append({"job_id": str(job.id), **loc})
    totals["counted"] = counted
    totals["per_job"] = per_job
    return totals
