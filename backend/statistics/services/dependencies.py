"""Who chose a dependency: the stack scaffold or the model.

Dependency advisories (osv-scanner, pip-audit, npm-audit, trivy) describe
packages, not lines the model wrote. A scaffold that pins an old Flask-CORS
gives every app built on it the same advisories, so counting them against
the model measures the template. These helpers split an app's dependency
advisories into packages the model added itself (declared in the app's
manifests but not in its stack's scaffold) and everything else: packages
the scaffold declares and transitive packages nobody declared directly.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import GenerationJob

_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_NPM_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


def normalize_package(name: str) -> str:
    """One spelling per package: PEP 503 for PyPI names (``Flask_Cors`` == ``flask-cors``), lower case for npm."""
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


def _requirements_packages(text: str) -> set[str]:
    names = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        if m := _REQ_NAME_RE.match(line):
            names.add(normalize_package(m.group(1)))
    return names


def _package_json_packages(text: str) -> set[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return set()
    if not isinstance(data, dict):
        return set()
    names = set()
    for section in _NPM_SECTIONS:
        deps = data.get(section)
        if isinstance(deps, dict):
            names.update(normalize_package(n) for n in deps)
    return names


def manifest_packages(files: dict[str, Any]) -> set[str]:
    """Packages declared directly in the ``requirements*.txt`` and ``package.json`` files of a ``{path: text}`` map."""
    names: set[str] = set()
    for path, text in files.items():
        if not isinstance(text, str) or "node_modules/" in path:
            continue
        base = path.rsplit("/", 1)[-1].lower()
        if base.startswith("requirements") and base.endswith(".txt"):
            names |= _requirements_packages(text)
        elif base == "package.json":
            names |= _package_json_packages(text)
    return names


def _scaffold_packages(slug: str, version: int | None) -> frozenset[str]:
    from backend.runtime.services.scaffolding import get_stack_row

    row = get_stack_row(slug, version) if slug else None
    if row is None and slug:
        row = get_stack_row(slug)
    return frozenset(manifest_packages(row.files or {})) if row is not None else frozenset()


def scaffold_packages(job: GenerationJob, memo: dict | None = None) -> frozenset[str]:
    """Packages the job's stack scaffold declares (the stack version the job pinned, else the latest).

    *memo* caches stack lookups across the jobs of one call.
    """
    stack = (job.resolved_bundle or {}).get("stack") or {}
    key = (stack.get("slug") or job.stack_slug or "", stack.get("version"))
    if memo is None:
        return _scaffold_packages(*key)
    if key not in memo:
        memo[key] = _scaffold_packages(*key)
    return memo[key]


def job_files(job: GenerationJob) -> dict[str, str]:
    """The job's generated files as analyzed (agent/copilot jobs keep a per-file map)."""
    data = job.result_data if isinstance(job.result_data, dict) else {}
    files = data.get("files")
    return {k: v for k, v in files.items() if isinstance(v, str)} if isinstance(files, dict) else {}


def model_added_packages(job: GenerationJob, memo: dict | None = None) -> set[str]:
    """Packages the app declares that its scaffold does not: the model's own dependency choices."""
    return manifest_packages(job_files(job)) - scaffold_packages(job, memo)


def cluster_package(cluster: Any) -> str:
    """The (normalized) package a dependency issue cluster is about, or ""."""
    for m in cluster.members:
        if m.get("package"):
            return normalize_package(m["package"])
    return ""
