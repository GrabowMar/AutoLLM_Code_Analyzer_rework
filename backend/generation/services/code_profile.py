"""Code profile of one generated app: what was stored, what counts as source, and who wrote it.

Everything here is derived on read from what the job already stores
(``result_data.files``, the agent's workspace manifest, the evaluation's
resolved packages and the stack scaffold), so it covers every finished job
without a backfill and never touches a measured value.

Numbers and their sources:

- ``loc`` is :func:`backend.reports.services.loc.loc_from_result_data`: the
  source LOC every study table, the model page and human evaluation use as the
  per-KLOC denominator (non-blank, non-comment lines of source files, test code
  apart, files inside :data:`backend.analysis.services.base.NON_SOURCE_DIRS`
  left out).
- ``all_lines`` counts every stored line the way ``metrics.lines_of_code`` does
  (lockfiles, docs and agent scratch folders included): shown for comparison
  only, never as LOC.
- Layers sort each stored file into exactly one of backend / frontend source,
  tests, config & manifests (lockfiles, docs, Dockerfiles, minified bundles) and
  excluded (dependency, cache and scratch directories), with the same
  classifiers as ``loc.py``; backend + frontend source LOC adds up to
  ``loc.total_loc`` and the tests layer to ``loc.test_loc``.
- Origins (scaffold / added / modified) come from the agent's workspace
  manifest; older agent runs and template-mode jobs have none (unknown, not 0).
- Structure counts (SQLAlchemy models, route decorators, component files and
  definitions) are static regex heuristics: approximate, never a functional
  measure (the conformance suite is that).
- Dependencies: declared specs from the app's manifests next to its stack
  scaffold's, with the helpers the dependency-ownership statistics use
  (:mod:`backend.statistics.services.dependencies`).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.services.base import NON_SOURCE_DIRS
from backend.analysis.services.base import is_non_source_path
from backend.reports.services.loc import _FRONTEND_EXTENSIONS
from backend.reports.services.loc import is_source_file
from backend.reports.services.loc import is_test_path
from backend.reports.services.loc import loc_from_files
from backend.reports.services.loc import loc_from_result_data
from backend.reports.services.loc import loc_of_test_files

if TYPE_CHECKING:
    from backend.generation.models import GenerationJob

PROFILE_VERSION = 1

LAYERS = ("backend", "frontend", "tests", "config", "excluded")
LAYER_LABELS = {
    "backend": "Backend source",
    "frontend": "Frontend source",
    "tests": "Tests",
    "config": "Config, manifests & docs",
    "excluded": "Excluded (dependency / cache / scratch dirs)",
}

_LANGUAGE_BY_EXT = {
    ".py": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JSX",
    ".ts": "TypeScript",
    ".tsx": "TSX",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sh": "Shell",
}
_LOCKFILES = frozenset(
    {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock", "uv.lock", "bun.lockb"},
)
_CONTAINER_FILES = frozenset({"dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore"})
_DOC_EXTS = frozenset({".md", ".rst", ".adoc"})

# Flask 1 ``@bp.route(...)`` and Flask 2 shortcut decorators ``@app.get(...)``.
_ROUTE_DECORATOR_RE = re.compile(r"^\s*@\w+(?:\.\w+)*\.(route|get|post|put|patch|delete)\s*\(", re.MULTILINE)
# A capitalised function or arrow-function binding: React's component naming rule.
_COMPONENT_DEF_RE = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?)?(?:function\s+([A-Z]\w*)\s*\(|(?:const|let)\s+([A-Z]\w*)\s*=\s*"
    r"(?:React\.)?(?:memo\(|forwardRef\()?\s*(?:async\s+)?(?:\([^)]*\)|\w+)\s*=>)",
    re.MULTILINE,
)
_COMPONENT_DIR_RE = re.compile(r"(^|/)components/")
_PAGE_DIR_RE = re.compile(r"(^|/)(pages|views|routes|screens)/")
_COMPONENT_EXTS = frozenset({".jsx", ".tsx", ".vue", ".svelte"})
# The package name of a requirement line ("Flask-Cors>=4.0" -> "Flask-Cors").
_REQ_NAME_RE = re.compile(r"[\s<>=!~;\[@]")

_MAX_LISTED_FILES = 60
_MAX_SAMPLE_PATHS = 5


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    return "." + name.rsplit(".", 1)[-1] if "." in name else ""


def _all_lines(text: str) -> int:
    """Lines as ``metrics.lines_of_code`` counts them (every line, blank or not)."""
    return text.count("\n") + 1


def file_layer(path: str) -> str:
    """The one layer *path* belongs to (``loc.py``'s classifiers, in its order of precedence)."""
    if is_non_source_path(path):
        return "excluded"
    if is_test_path(path):
        return "tests"
    if is_source_file(path):
        # loc_from_files' split: everything under frontend/ plus web assets anywhere else.
        return "frontend" if path.startswith("frontend/") or _ext(path) in _FRONTEND_EXTENSIONS else "backend"
    return "config"


def _non_source_kind(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    ext = _ext(path)
    if name in _LOCKFILES:
        return "lockfile"
    if (name.startswith("requirements") and ext == ".txt") or name in ("package.json", "pyproject.toml", "pipfile"):
        return "manifest"
    if name in _CONTAINER_FILES:
        return "container"
    if ext in _DOC_EXTS or name.startswith(("readme", "license", "changelog")):
        return "docs"
    if ".min." in name:
        return "minified"
    return "config"


def _excluded_root(path: str) -> str:
    """``frontend/node_modules`` for ``frontend/node_modules/x/y.js``: the path up to its first excluded directory."""
    parts = path.strip("/").split("/")
    for i, part in enumerate(parts[:-1]):
        if part in NON_SOURCE_DIRS:
            return "/".join(parts[: i + 1])
    return parts[0]


def _file_map(data: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """``(layout, {path: text})`` of a ``result_data``.

    ``files``: a per-file map (agent runs, newer template runs). ``stage_blobs``:
    only the backend/frontend stage outputs (older template runs), given their
    entry-point paths. ``single_blob``: one raw response without paths.
    """
    files = data.get("files")
    if isinstance(files, dict) and files:
        return "files", {str(k): v for k, v in files.items() if isinstance(v, str)}
    backend = data.get("backend_code") or data.get("backend") or ""
    frontend = data.get("frontend_code") or data.get("frontend") or ""
    if backend or frontend:
        blobs = {}
        if isinstance(backend, str) and backend:
            blobs[str(data.get("backend_entry") or "app.py")] = backend
        if isinstance(frontend, str) and frontend:
            blobs["frontend/src/App.jsx"] = frontend
        return "stage_blobs", blobs
    content = data.get("content") or data.get("raw_response") or ""
    if isinstance(content, str) and content:
        return "single_blob", {"(raw response)": content}
    return "none", {}


def _empty_layer(key: str) -> dict[str, Any]:
    return {"layer": key, "label": LAYER_LABELS[key], "files": 0, "all_lines": 0, "source_loc": 0}


def _layers(files: dict[str, str]) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, int]]:
    """Per-layer totals, each path's layer and each source/test file's LOC (``loc.py``'s count)."""
    layers = {key: _empty_layer(key) for key in LAYERS}
    layer_of: dict[str, str] = {}
    file_loc: dict[str, int] = {}
    for path, text in files.items():
        layer = file_layer(path)
        layer_of[path] = layer
        row = layers[layer]
        row["files"] += 1
        row["all_lines"] += _all_lines(text)
        if layer in ("backend", "frontend"):
            loc = loc_from_files({path: text})["total_loc"]
        elif layer == "tests" and is_source_file(path):
            loc = loc_of_test_files({path: text})["test_loc"]
        else:
            continue
        file_loc[path] = loc
        row["source_loc"] += loc
    for key in ("config", "excluded"):
        layers[key]["source_loc"] = None  # not source: no LOC, by definition
    return layers, layer_of, file_loc


def _languages(files: dict[str, str], layer_of: dict[str, str], file_loc: dict[str, int]) -> list[dict[str, Any]]:
    """Source files and LOC per language (backend and frontend source only; tests apart)."""
    rows: dict[str, dict[str, Any]] = {}
    for path in files:
        if layer_of.get(path) not in ("backend", "frontend"):
            continue
        lang = _LANGUAGE_BY_EXT.get(_ext(path), "Other")
        row = rows.setdefault(lang, {"language": lang, "files": 0, "source_loc": 0})
        row["files"] += 1
        row["source_loc"] += file_loc.get(path, 0)
    return sorted(rows.values(), key=lambda r: (-r["source_loc"], r["language"]))


def _excluded_groups(files: dict[str, str], layer_of: dict[str, str]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for path, text in sorted(files.items()):
        if layer_of.get(path) != "excluded":
            continue
        root = _excluded_root(path)
        group = groups.setdefault(root, {"root": root, "files": 0, "all_lines": 0, "sample_paths": []})
        group["files"] += 1
        group["all_lines"] += _all_lines(text)
        if len(group["sample_paths"]) < _MAX_SAMPLE_PATHS:
            group["sample_paths"].append(path)
    return sorted(groups.values(), key=lambda g: -g["all_lines"])


def _non_source_files(files: dict[str, str], layer_of: dict[str, str]) -> list[dict[str, Any]]:
    rows = [
        {"path": path, "kind": _non_source_kind(path), "all_lines": _all_lines(text)}
        for path, text in files.items()
        if layer_of.get(path) == "config"
    ]
    rows.sort(key=lambda r: (-r["all_lines"], r["path"]))
    return rows[:_MAX_LISTED_FILES]


def _origins(
    job: GenerationJob,
    files: dict[str, str],
    layer_of: dict[str, str],
    file_loc: dict[str, int],
) -> dict[str, Any]:
    """Scaffold / added / modified per file, from the agent's workspace manifest (``known`` False without one)."""
    data = job.result_data if isinstance(job.result_data, dict) else {}
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    workspace = agent.get("workspace") if isinstance(agent.get("workspace"), dict) else {}
    manifest = workspace.get("manifest") if isinstance(workspace.get("manifest"), dict) else {}
    if not manifest:
        reason = "no_manifest" if agent else "not_agent"
        return {"known": False, "reason": reason}

    per_file: dict[str, str] = {}
    for path, entry in manifest.items():
        origin = entry.get("origin") if isinstance(entry, dict) else None
        per_file[str(path)] = origin if isinstance(origin, str) and origin else "unknown"
    for path in files:
        per_file.setdefault(path, "unknown")

    by_origin: dict[str, dict[str, Any]] = {}
    for path in files:
        origin = per_file[path]
        row = by_origin.setdefault(
            origin,
            {"origin": origin, "files": 0, "source_files": 0, "source_loc": 0, "test_files": 0, "test_loc": 0},
        )
        row["files"] += 1
        layer = layer_of.get(path)
        if layer in ("backend", "frontend"):
            row["source_files"] += 1
            row["source_loc"] += file_loc.get(path, 0)
        elif layer == "tests" and path in file_loc:
            row["test_files"] += 1
            row["test_loc"] += file_loc[path]

    recorded = workspace.get("origins") if isinstance(workspace.get("origins"), dict) else None
    skipped = [
        {
            "path": s.get("path"),
            "reason": s.get("reason"),
            "size": s.get("size"),
            "origin": s.get("origin"),
        }
        for s in (workspace.get("skipped") or [])
        if isinstance(s, dict)
    ]
    return {
        "known": True,
        "counts": dict(recorded) if recorded else dict(Counter(per_file.values())),
        "by_origin": sorted(by_origin.values(), key=lambda r: r["origin"]),
        "per_file": per_file,
        "deleted_scaffold_files": [str(p) for p in workspace.get("deleted_scaffold_files") or []],
        "protected_modified": [str(p) for p in workspace.get("protected_modified") or []],
        "skipped": skipped,
    }


def _structure(files: dict[str, str], layer_of: dict[str, str]) -> dict[str, Any]:
    """Static heuristics over source files (approximate by design)."""
    from backend.generation.services.backend_scanner import scan_backend_code

    backend_py = {p: t for p, t in files.items() if layer_of.get(p) == "backend" and _ext(p) == ".py"}
    frontend = {p: t for p, t in files.items() if layer_of.get(p) == "frontend"}
    models: list[str] = []
    if backend_py:
        models = [m.name for m in scan_backend_code(backend_py).models]
    route_decorators = sum(len(_ROUTE_DECORATOR_RE.findall(t)) for t in backend_py.values())
    component_files = sum(1 for p in frontend if _COMPONENT_DIR_RE.search(p.lower()))
    page_files = sum(1 for p in frontend if _PAGE_DIR_RE.search(p.lower()))
    definitions = 0
    for path, text in frontend.items():
        ext = _ext(path)
        if ext in (".vue", ".svelte"):
            definitions += 1  # one component per single-file component
        elif ext in _COMPONENT_EXTS:
            definitions += len(_COMPONENT_DEF_RE.findall(text))
    return {
        "approximate": True,
        "method": "static regex over source files",
        "models": len(models),
        "model_names": models[:40],
        "route_decorators": route_decorators,
        "frontend_component_files": component_files,
        "frontend_page_files": page_files,
        "component_definitions": definitions,
    }


def _ecosystem(path: str) -> str | None:
    base = path.rsplit("/", 1)[-1].lower()
    if base.startswith("requirements") and base.endswith(".txt"):
        return "pypi"
    if base == "package.json":
        return "npm"
    return None


def _manifest_rows(files: dict[str, Any]) -> list[dict[str, Any]]:
    """``[{package, spec, ecosystem, manifest}]`` declared directly in the manifests of a ``{path: text}`` map."""
    from backend.statistics.services.dependencies import manifest_specs

    rows: list[dict[str, Any]] = []
    for path in sorted(files):
        text = files[path]
        eco = _ecosystem(path)
        if eco is None or not isinstance(text, str) or "node_modules/" in path:
            continue
        rows.extend(
            {"package": name, "spec": spec, "ecosystem": eco, "manifest": path}
            for name, spec in manifest_specs({path: text}).items()
        )
    return rows


def _scaffold_stack(job: GenerationJob) -> tuple[dict[str, Any] | None, Any]:
    """The stack row the scaffold specs come from, as :func:`scaffold_specs` picks it (pinned version, else latest)."""
    from backend.runtime.services.scaffolding import get_stack_row

    stack = (job.resolved_bundle or {}).get("stack") if isinstance(job.resolved_bundle, dict) else None
    stack = stack if isinstance(stack, dict) else {}
    slug = stack.get("slug") or job.stack_slug or ""
    pinned = stack.get("version")
    if not slug:
        return None, None
    row = get_stack_row(slug, pinned)
    source = "pinned"
    if row is None:
        row = get_stack_row(slug)
        source = "latest"
    if row is None:
        return {"slug": slug, "pinned_version": pinned, "version": None, "source": "missing"}, None
    return {"slug": slug, "pinned_version": pinned, "version": row.version, "source": source}, row


def _dependencies(job: GenerationJob) -> dict[str, Any]:
    from backend.statistics.services.dependencies import _same_spec
    from backend.statistics.services.dependencies import job_files
    from backend.statistics.services.dependencies import manifest_specs
    from backend.statistics.services.dependencies import model_added_packages
    from backend.statistics.services.dependencies import normalize_package
    from backend.statistics.services.dependencies import scaffold_specs

    data = job.result_data if isinstance(job.result_data, dict) else {}
    app_files = job_files(job)
    declared_rows = _manifest_rows(app_files)
    app_specs = manifest_specs(app_files)
    stack, row = _scaffold_stack(job)
    scaffold = scaffold_specs(job)
    scaffold_rows = _manifest_rows(row.files or {}) if row is not None else []
    scaffold_eco = {r["package"]: r["ecosystem"] for r in scaffold_rows}

    has_manifests = bool(declared_rows)
    added = sorted(model_added_packages(job)) if has_manifests else None
    repinned = (
        [
            {"package": p, "spec": app_specs[p], "scaffold_spec": scaffold[p], "ecosystem": scaffold_eco.get(p)}
            for p in sorted(app_specs)
            if p in scaffold and not _same_spec(app_specs[p], scaffold[p])
        ]
        if has_manifests
        else None
    )
    dropped = sorted(set(scaffold) - set(app_specs)) if has_manifests and scaffold else None

    added_set = set(added or ())
    repinned_set = {r["package"] for r in repinned or ()}
    for r in declared_rows:
        if r["package"] in added_set:
            r["status"] = "added"
        elif r["package"] in repinned_set:
            r["status"] = "repinned"
        elif r["package"] in scaffold:
            r["status"] = "scaffold"
        else:
            r["status"] = "unknown"
        r["scaffold_spec"] = scaffold.get(r["package"])

    evaluation = data.get("evaluation") if isinstance(data.get("evaluation"), dict) else {}
    build = evaluation.get("build") if isinstance(evaluation.get("build"), dict) else {}
    healing = build.get("healing") if isinstance(build.get("healing"), dict) else {}
    healing_added = {
        normalize_package(_REQ_NAME_RE.split(str(req).strip(), maxsplit=1)[0])
        for req in healing.get("added_requirements") or []
    }
    # The image installs the manifests it was built from: the app's own when it stores
    # them (agent runs), the stack scaffold's for template runs (merged in at build time).
    built_from = "app" if has_manifests else "scaffold"
    built_rows = declared_rows if has_manifests else scaffold_rows
    built_pypi = {r["package"] for r in built_rows if r["ecosystem"] == "pypi"}
    resolved_raw = evaluation.get("resolved_python_deps")
    resolved = None
    if isinstance(resolved_raw, list):
        resolved = []
        for item in resolved_raw:
            name, _, version = str(item).partition("==")
            norm = normalize_package(name)
            declared_by = built_from if norm in built_pypi else "healing" if norm in healing_added else None
            resolved.append(
                {
                    "package": name,
                    "version": version or None,
                    "declared": declared_by is not None,
                    "declared_by": declared_by,
                },
            )

    reported_raw = data.get("backend_dependencies") or data.get("dependencies")
    reported = [str(x) for x in reported_raw] if isinstance(reported_raw, list) else None

    return {
        "has_manifests": has_manifests,
        "declared": declared_rows,
        "scaffold": [{**r, "declared_by_app": r["package"] in app_specs} for r in scaffold_rows],
        "scaffold_stack": stack,
        "model_added": added,
        "repinned": repinned,
        "dropped_scaffold": dropped,
        "resolved_python": resolved,
        "reported_by_generator": reported,
        "counts": {
            "declared": len(app_specs) if has_manifests else None,
            "scaffold": len(scaffold) if row is not None else None,
            "model_added": len(added) if added is not None else None,
            "repinned": len(repinned) if repinned is not None else None,
            "dropped_scaffold": len(dropped) if dropped is not None else None,
            "resolved_python": len(resolved) if resolved is not None else None,
        },
    }


def code_profile(job: GenerationJob) -> dict[str, Any]:
    """The code profile of *job* (see the module docstring for every number's source)."""
    data = job.result_data if isinstance(job.result_data, dict) else {}
    layout, files = _file_map(data)
    notes: list[str] = []

    layers, layer_of, file_loc = _layers(files) if layout in ("files", "stage_blobs") else ({}, {}, {})
    loc: dict[str, int | None] = dict(loc_from_result_data(data))
    if layout in ("single_blob", "none"):
        # No paths: nothing can be told apart as source, so source LOC is unmeasured (not 0).
        loc = dict.fromkeys(loc)
        if layout == "single_blob":
            notes.append(
                "This run stored one raw response without file paths: source LOC is not measured "
                "(study tables count it as 0 and leave it out of per-KLOC rates).",
            )
    if layout == "stage_blobs":
        notes.append("Only the backend/frontend stage outputs were stored; they are shown under their entry paths.")
        layers["tests"]["source_loc"] = None  # loc.py: a stage blob cannot be told apart as test code

    all_lines = sum(_all_lines(t) for t in files.values()) if files else None
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    recorded_lines = metrics.get("lines_of_code")
    origins = _origins(job, files, layer_of, file_loc) if layout == "files" else {"known": False, "reason": layout}
    if not origins["known"] and origins["reason"] == "no_manifest":
        notes.append("This agent run predates the workspace manifest: file origins are unknown.")

    total_loc = loc.get("total_loc")
    return {
        "job_id": str(job.id),
        "version": PROFILE_VERSION,
        "mode": job.mode,
        "layout": layout,
        "loc": loc,
        "all_lines": all_lines,
        "all_files": len(files) if files else 0,
        "recorded_lines_of_code": recorded_lines if isinstance(recorded_lines, int) else None,
        "all_lines_ratio": (round(all_lines / total_loc, 2) if all_lines and total_loc else None),
        "layers": [layers[k] for k in LAYERS] if layers else [],
        "languages": _languages(files, layer_of, file_loc) if layers else [],
        "excluded": _excluded_groups(files, layer_of) if layers else [],
        "non_source_files": _non_source_files(files, layer_of) if layers else [],
        "origins": origins,
        "structure": _structure(files, layer_of) if layers else None,
        "dependencies": _dependencies(job),
        "definitions": {
            "source_loc": (
                "Non-blank, non-comment lines of source files (.py, .js/.jsx/.ts/.tsx, .html, .css, …), "
                "test code and dependency/cache/scratch directories left out: the per-KLOC denominator "
                "of the study tables (reports/services/loc.py)."
            ),
            "all_lines": (
                "Every stored line of every file, lockfiles, docs and scratch folders included "
                "(as metrics.lines_of_code counts it): for comparison only."
            ),
        },
        "notes": notes,
    }
