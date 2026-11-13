"""Code profile of one generated app: what was collected, what counts as source, and who wrote it.

Everything here is derived on read from the run's collected files (``RunFile`` rows read through
:func:`backend.generation.runs.view.run_view`), its starter and its resolved lock, so it never touches a
measured value.

Numbers and their sources:

- ``loc`` counts the **model** scope (authored, modified and slot files, excluded rows left out): the
  per-KLOC denominator of the study tables. ``loc_app`` counts the whole app tree (starter included) the
  same way. Both use ``reports/services/loc.py`` (non-blank, non-comment lines of source files, test code
  apart, files inside :data:`backend.analysis.services.base.NON_SOURCE_DIRS` left out). A run without
  collected files has ``None`` everywhere (not measured, never 0).
- ``all_lines`` counts every collected line (lockfiles, docs and scratch folders included): shown for
  comparison only, never as LOC.
- Layers sort each app file into exactly one of backend / frontend source, tests, config & manifests
  (lockfiles, docs, Dockerfiles, minified bundles) and excluded (dependency, cache and scratch
  directories), with the same classifiers as ``loc.py``.
- Origins (starter / authored / modified / deleted / slot) are the collector's ``RunFile.origin``.
- Structure counts (ORM model classes, route decorators, component files and definitions) are static
  regex heuristics: approximate, never a functional measure (the evaluation suite is that).
- Dependencies: the specs declared in the app's manifests next to its starter's, and the versions the
  platform lock step resolved (``GenerationRun.resolved_lock_blob``).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.services.base import NON_SOURCE_DIRS
from backend.analysis.services.base import is_non_source_path
from backend.generation.models.choices import FileOrigin
from backend.reports.services.loc import _FRONTEND_EXTENSIONS
from backend.reports.services.loc import is_source_file
from backend.reports.services.loc import is_test_path
from backend.reports.services.loc import loc_from_files
from backend.reports.services.loc import loc_of_test_files

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.runs.view import RunView

PROFILE_VERSION = 2

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
# ORM model classes: Flask-SQLAlchemy ``db.Model``, SQLAlchemy declarative ``Base``, SQLModel, Django.
_MODEL_CLASS_RE = re.compile(
    r"^\s*class\s+(\w+)\s*\([^)]*\b(?:db\.Model|Base|DeclarativeBase|SQLModel|models\.Model)\b",
    re.MULTILINE,
)
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
    """Every line, blank or not."""
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
    rows: list[dict[str, Any]],
    deleted: list[str],
    layer_of: dict[str, str],
    file_loc: dict[str, int],
) -> dict[str, Any]:
    """Files and LOC per collector origin (``RunFile.origin``; ``parent`` for a slot variant's parent rows)."""
    per_file = {row["path"]: row["origin"] for row in rows}
    by_origin: dict[str, dict[str, Any]] = {}
    for row in rows:
        origin = row["origin"]
        entry = by_origin.setdefault(
            origin,
            {"origin": origin, "files": 0, "source_files": 0, "source_loc": 0, "test_files": 0, "test_loc": 0},
        )
        entry["files"] += 1
        path = row["path"]
        layer = layer_of.get(path)
        if layer in ("backend", "frontend"):
            entry["source_files"] += 1
            entry["source_loc"] += file_loc.get(path, 0)
        elif layer == "tests" and path in file_loc:
            entry["test_files"] += 1
            entry["test_loc"] += file_loc[path]
    excluded = Counter(row["excluded"] for row in rows if row["excluded"])
    return {
        "known": bool(rows),
        "counts": dict(Counter(per_file.values())),
        "by_origin": sorted(by_origin.values(), key=lambda r: r["origin"]),
        "per_file": per_file,
        "deleted_starter_files": sorted(deleted),
        "excluded_by_reason": dict(sorted(excluded.items())),
    }


def _structure(files: dict[str, str], layer_of: dict[str, str]) -> dict[str, Any]:
    """Static heuristics over source files (approximate by design)."""
    backend_py = {p: t for p, t in files.items() if layer_of.get(p) == "backend" and _ext(p) == ".py"}
    frontend = {p: t for p, t in files.items() if layer_of.get(p) == "frontend"}
    models = [name for text in backend_py.values() for name in _MODEL_CLASS_RE.findall(text)]
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


def _starter_files(run: GenerationRun) -> dict[str, str] | None:
    """The starter's own manifest texts ``{path: text}`` (``None`` when the run has no starter)."""
    from backend.generation import blobs

    starter = run.starter
    if starter is None:
        return None
    files: dict[str, str] = {}
    for path, entry in ((starter.content or {}).get("files") or {}).items():
        if _ecosystem(path) is None or not isinstance(entry, dict) or not entry.get("blob"):
            continue
        try:
            files[path] = blobs.get_text(entry["blob"])
        except (OSError, LookupError, ValueError):
            continue  # a missing blob leaves that manifest out; the status says "unknown", never "starter"
    return files


def _resolved_lock(run: GenerationRun) -> list[dict[str, Any]] | None:
    """The pip packages the platform lock step resolved (``None`` before the lock step ran)."""
    from backend.generation import blobs

    if not run.resolved_lock_blob:
        return None
    try:
        lock = blobs.get_json(run.resolved_lock_blob)
    except (OSError, LookupError, ValueError):
        return None
    pip = ((lock or {}).get("ecosystems") or {}).get("pip") if isinstance(lock, dict) else None
    if not isinstance(pip, list):
        return None
    return [p for p in pip if isinstance(p, dict) and p.get("name")]


def _dependencies(run: GenerationRun, app_files: dict[str, str]) -> dict[str, Any]:
    from backend.statistics.services.dependencies import _same_spec
    from backend.statistics.services.dependencies import manifest_specs
    from backend.statistics.services.dependencies import normalize_package

    declared_rows = _manifest_rows(app_files)
    app_specs = manifest_specs(app_files)
    starter_files = _starter_files(run)
    starter_rows = _manifest_rows(starter_files) if starter_files is not None else []
    starter = manifest_specs(starter_files) if starter_files is not None else {}
    starter_eco = {r["package"]: r["ecosystem"] for r in starter_rows}
    has_manifests = bool(declared_rows)
    known_starter = starter_files is not None

    added = sorted(set(app_specs) - set(starter)) if has_manifests and known_starter else None
    repinned = (
        [
            {"package": p, "spec": app_specs[p], "starter_spec": starter[p], "ecosystem": starter_eco.get(p)}
            for p in sorted(app_specs)
            if p in starter and not _same_spec(app_specs[p], starter[p])
        ]
        if has_manifests and known_starter
        else None
    )
    dropped = sorted(set(starter) - set(app_specs)) if has_manifests and starter else None

    added_set = set(added or ())
    repinned_set = {r["package"] for r in repinned or ()}
    for r in declared_rows:
        if not known_starter:
            r["status"] = "unknown"
        elif r["package"] in added_set:
            r["status"] = "added"
        elif r["package"] in repinned_set:
            r["status"] = "repinned"
        elif r["package"] in starter:
            r["status"] = "starter"
        else:
            r["status"] = "unknown"
        r["starter_spec"] = starter.get(r["package"])

    declared_pypi = {normalize_package(r["package"]) for r in declared_rows if r["ecosystem"] == "pypi"}
    locked = _resolved_lock(run)
    resolved = (
        [
            {
                "package": p["name"],
                "version": p.get("version") or None,
                "declared": normalize_package(p["name"]) in declared_pypi,
            }
            for p in locked
        ]
        if locked is not None
        else None
    )
    return {
        "has_manifests": has_manifests,
        "declared": declared_rows,
        "starter": [{**r, "declared_by_app": r["package"] in app_specs} for r in starter_rows],
        "starter_ref": f"{run.starter.slug}@{run.starter.version}" if run.starter is not None else None,
        "model_added": added,
        "repinned": repinned,
        "dropped_starter": dropped,
        "resolved_python": resolved,
        "counts": {
            "declared": len(app_specs) if has_manifests else None,
            "starter": len(starter) if known_starter else None,
            "model_added": len(added) if added is not None else None,
            "repinned": len(repinned) if repinned is not None else None,
            "dropped_starter": len(dropped) if dropped is not None else None,
            "resolved_python": len(resolved) if resolved is not None else None,
        },
    }


_LOC_KEYS = ("total_loc", "backend_loc", "frontend_loc", "test_loc")


def _loc(files: dict[str, str]) -> dict[str, int | None]:
    if not files:
        return dict.fromkeys(_LOC_KEYS)  # nothing collected: not measured, never 0
    return {**loc_from_files(files), **loc_of_test_files(files)}


def code_profile(run: GenerationRun, view: RunView | None = None) -> dict[str, Any]:
    """The code profile of *run* (see the module docstring for every number's source)."""
    from backend.generation.runs.view import run_view

    view = view or run_view(run)
    files = view.code_files("app")
    model_files = view.code_files("model")
    rows = [
        {"path": row["path"], "origin": row["view_origin"], "excluded": row["excluded"]}
        for row in view.file_rows("app").values("path", "view_origin", "excluded")
    ]
    deleted = list(run.files.filter(origin=FileOrigin.DELETED).values_list("path", flat=True))
    notes: list[str] = []
    if not rows:
        notes.append("No files were collected for this run (yet): every count is not measured.")

    layers, layer_of, file_loc = _layers(files) if files else ({}, {}, {})
    loc = _loc(model_files)
    total_loc = loc.get("total_loc")
    all_lines = sum(_all_lines(t) for t in files.values()) if files else None
    return {
        "run_id": str(run.id),
        "version": PROFILE_VERSION,
        "kind": run.kind,
        "loc": loc,
        "loc_app": _loc(files),
        "all_lines": all_lines,
        "all_files": len(files),
        "all_lines_ratio": (round(all_lines / total_loc, 2) if all_lines and total_loc else None),
        "layers": [layers[k] for k in LAYERS] if layers else [],
        "languages": _languages(files, layer_of, file_loc) if layers else [],
        "excluded": _excluded_groups(files, layer_of) if layers else [],
        "non_source_files": _non_source_files(files, layer_of) if layers else [],
        "origins": _origins(rows, deleted, layer_of, file_loc),
        "structure": _structure(files, layer_of) if layers else None,
        "dependencies": _dependencies(run, files),
        "definitions": {
            "source_loc": (
                "Non-blank, non-comment lines of source files (.py, .js/.jsx/.ts/.tsx, .html, .css, …) the model "
                "wrote or changed, test code and dependency/cache/scratch directories left out: the per-KLOC "
                "denominator of the study tables (reports/services/loc.py)."
            ),
            "all_lines": "Every collected line of every app file, lockfiles, docs and scratch folders included.",
        },
        "notes": notes,
    }
