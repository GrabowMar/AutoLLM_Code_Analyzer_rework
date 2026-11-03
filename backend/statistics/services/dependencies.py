"""Who chose a dependency: the starter or the model.

Dependency advisories (osv-scanner, pip-audit, npm-audit, trivy) describe
packages, not lines the model wrote. A starter that pins an old Flask-CORS
gives every app built on it the same advisories, so counting them against
the model measures the starter. These helpers split an app's dependency
advisories into the model's (:meth:`DependencyOwnership.is_models`) and the
rest: what the starter's own version specs install, and transitive packages
nobody declared directly.

An advisory is the model's when it is in a package the model added (declared
in the app's ``requirements*.txt``/``package.json`` but not in its
starter), in a transitive package that the app's lockfile shows only
model-added packages pull in, or in a starter package whose version spec the
model changed, when the starter's own spec would not install a vulnerable
version (``axios`` pinned to ``1.6.8`` while the starter's ``^1.6.0``
installs a fixed 1.x).

What the starter's spec installs is taken from the scope's evidence
(:class:`DependencyScope`): an exact starter pin installs that version; a
range installs what the scope's apps that kept that range installed (their
lockfiles, the versions the scanners found), else the newest version in the
range other apps in scope installed. So two apps on the same installed
version get the same owner whatever their pin style. A transitive package
under a starter package the model re-pinned is judged the same way, from
what the apps that kept the starter's spec installed. With no evidence in
scope (a single app) a range is assumed to install its newest version, which
is fixed when a fix lies within the range.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from dataclasses import field
from itertools import pairwise
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.services.base import is_non_source_path

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import Starter

logger = logging.getLogger(__name__)

_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_NPM_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
_LOCK_SECTIONS = ("dependencies", "optionalDependencies", "peerDependencies")
_ROOT = ""


def normalize_package(name: str) -> str:
    """One spelling per package: PEP 503 for PyPI names (``Flask_Cors`` == ``flask-cors``), lower case for npm."""
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


def _normalize_spec(spec: str) -> str:
    """One spelling per spec: ``== 4.0`` -> ``==4.0``, ``>=2, <3`` -> ``>=2,<3``, whitespace runs as one space.

    The spaces between npm comparators (``>=1.2.3 <2.0.0``) and around
    hyphen ranges (``1.2.3 - 2.3.4``) carry meaning and stay.
    """
    text = _OP_SPACE_RE.sub(r"\1", str(spec or "").strip())
    return re.sub(r"\s+", " ", re.sub(r"\s*,\s*", ",", text))


def _requirements_specs(text: str) -> dict[str, str]:
    specs: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        if m := _REQ_NAME_RE.match(line):
            spec = m.group(3).split(";", 1)[0]  # drop environment markers
            specs.setdefault(normalize_package(m.group(1)), _normalize_spec(spec))
    return specs


def _package_json_specs(text: str) -> dict[str, str]:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    specs: dict[str, str] = {}
    for section in _NPM_SECTIONS:
        deps = data.get(section)
        if isinstance(deps, dict):
            for name, spec in deps.items():
                specs.setdefault(normalize_package(name), _normalize_spec(spec if isinstance(spec, str) else ""))
    return specs


def _manifests(files: dict[str, Any]):
    for path, text in files.items():
        if not isinstance(text, str) or "node_modules/" in path:
            continue
        yield path.rsplit("/", 1)[-1].lower(), text


def manifest_specs(files: dict[str, Any]) -> dict[str, str]:
    """``{package: version spec}`` declared directly in the ``requirements*.txt`` and ``package.json`` files of a map.

    Specs are normalized (``==4.0.0``, ``^1.6.0``, ``>=1.2.3 <2.0.0``, ``""``
    for none; :func:`_normalize_spec`); a package declared twice keeps its
    first spec.
    """
    specs: dict[str, str] = {}
    for base, text in _manifests(files):
        if base.startswith("requirements") and base.endswith(".txt"):
            found = _requirements_specs(text)
        elif base == "package.json":
            found = _package_json_specs(text)
        else:
            continue
        for name, spec in found.items():
            specs.setdefault(name, spec)
    return specs


def manifest_packages(files: dict[str, Any]) -> set[str]:
    """Packages declared directly in the ``requirements*.txt`` and ``package.json`` files of a ``{path: text}`` map."""
    return set(manifest_specs(files))


def _lock_graph(files: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """:func:`lock_requirers` and :func:`lock_versions` in one pass over the lockfiles."""
    requirers: dict[str, set[str]] = {}
    versions: dict[str, set[str]] = {}

    def add(parent: str, deps: Any) -> None:
        if isinstance(deps, dict):
            for name in deps:
                requirers.setdefault(normalize_package(name), set()).add(parent)

    def installed(name: str, entry: dict[str, Any]) -> None:
        if isinstance(entry.get("version"), str) and entry["version"]:
            versions.setdefault(normalize_package(name), set()).add(entry["version"])

    def walk_v1(deps: Any) -> None:
        if not isinstance(deps, dict):
            return
        for name, entry in deps.items():
            if isinstance(entry, dict):
                installed(name, entry)
                add(normalize_package(name), entry.get("requires"))
                walk_v1(entry.get("dependencies"))

    for base, text in _manifests(files):
        if base != "package-lock.json":
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        packages = data.get("packages")
        if isinstance(packages, dict):  # lockfileVersion 2 and 3
            for path, entry in packages.items():
                if not isinstance(entry, dict):
                    continue
                name = path.rsplit("node_modules/", 1)[-1]
                parent = normalize_package(name) if path else _ROOT
                if path:
                    installed(name, entry)
                for section in (*_LOCK_SECTIONS, "devDependencies") if not path else _LOCK_SECTIONS:
                    add(parent, entry.get(section))
        else:  # lockfileVersion 1: requires per entry; direct deps come from package.json
            walk_v1(data.get("dependencies"))
    return requirers, versions


def lock_requirers(files: dict[str, Any]) -> dict[str, set[str]]:
    """``{package: packages that require it}`` from the ``package-lock.json`` files of a map (``""`` is the project).

    Names only, every installed copy merged: a package reachable from one
    parent through any copy counts as reachable from it.
    """
    return _lock_graph(files)[0]


def lock_versions(files: dict[str, Any]) -> dict[str, set[str]]:
    """``{package: versions installed}`` from the ``package-lock.json`` files of a map (every copy)."""
    return _lock_graph(files)[1]


# ── versions ─────────────────────────────────────────────────────────────

Version = tuple[int, int, int]
_ZERO: Version = (0, 0, 0)
_INF: Version = (10**9, 0, 0)
_VERSION_RE = re.compile(r"^[v=\s]*(\d+(?:\.\d+)*)")
_COMPARATOR_RE = re.compile(r"^(===|==|~=|>=|<=|!=|\^|~|>|<|=)?v?(.+)$")
_OP_SPACE_RE = re.compile(r"(===|==|~=|>=|<=|!=|>|<|=|\^|~)\s+")


def _version(text: Any) -> Version | None:
    """The release numbers of a version string, padded to three (``"2.0.0rc1"`` -> ``(2, 0, 0)``)."""
    m = _VERSION_RE.match(str(text or ""))
    if not m:
        return None
    major, minor, patch = [*(int(p) for p in m.group(1).split(".")[:3]), 0, 0][:3]
    return (major, minor, patch)


def _bump(v: Version, level: int) -> Version:
    """The first version after *v*'s release line at *level* (0 major, 1 minor, 2 patch)."""
    if level == 0:
        return (v[0] + 1, 0, 0)
    if level == 1:
        return (v[0], v[1] + 1, 0)
    return (v[0], v[1], v[2] + 1)


def _release(text: str) -> tuple[list[int], bool] | None:
    """The release numbers a spec version gives, and whether it ends in a wildcard (``1.2.*``, ``1.x``)."""
    nums: list[int] = []
    for seg in text.split("+", 1)[0].split("."):
        if seg in ("*", "x", "X"):
            return nums, True
        m = re.match(r"\d+", seg)
        if not m:
            return (nums, False) if nums else None
        nums.append(int(m.group(0)))
        if m.group(0) != seg or len(nums) == 3:  # a pre-release suffix ends the release part
            return nums, False
    return nums, False


def _comparator(op: str, text: str) -> tuple[Version, Version] | None:  # noqa: PLR0911
    """``[low, high)`` one comparator allows; ``low == high`` is an exact version."""
    parsed = _release(text)
    if parsed is None:
        return None
    nums, wildcard = parsed
    if not nums:
        return (_ZERO, _INF)
    v: Version = (*nums, 0, 0)[:3]  # type: ignore[assignment]
    depth = len(nums)
    if op in ("==", "==="):  # PEP 440: "==2.0" is 2.0.0, "==2.*" a wildcard
        return (v, _bump(v, depth - 1)) if wildcard else (v, v)
    if op in ("", "="):  # npm: "1.2" and "1.2.x" are ranges
        return (v, _bump(v, depth - 1)) if wildcard or depth < 3 else (v, v)
    if op == "^":
        level = 0 if v[0] or depth == 1 else 1 if v[1] or depth == 2 else 2
        return (v, _bump(v, level))
    if op == "~":
        return (v, _bump(v, 0 if depth == 1 else 1))
    if op == "~=":
        return (v, _bump(v, max(depth - 2, 0)))
    if op in (">=", ">"):
        return (v, _INF)
    if op == "<":
        return (_ZERO, v)
    if op == "<=":
        return (_ZERO, _bump(v, 2))
    return None


def spec_range(spec: str) -> tuple[Version, Version] | None:
    """``(low, high)`` versions a declared spec allows: ``high`` exclusive, ``low == high`` for an exact pin.

    Handles PEP 440 (``==``, ``~=``, ``>=``, comma lists) and npm (bare and
    ``=`` pins, ``^``, ``~``, ``x`` wildcards, space-separated comparators).
    Alternatives (``||``), hyphen ranges, tags, URLs and paths give ``None``.
    """
    spec = (spec or "").strip()
    if not spec or spec in ("*", "x", "X", "latest"):
        return (_ZERO, _INF)
    if "||" in spec or " - " in spec or ":" in spec or "/" in spec:
        return None
    low, high = _ZERO, _INF
    exact: Version | None = None
    for token in re.split(r"[,\s]+", _OP_SPACE_RE.sub(r"\1", spec)):
        if not token:
            continue
        m = _COMPARATOR_RE.match(token)
        op = (m.group(1) or "") if m else ""
        if op == "!=":
            continue
        bounds = _comparator(op, m.group(2)) if m else None
        if bounds is None:
            return None
        if bounds[0] == bounds[1]:
            if exact is not None and exact != bounds[0]:
                return None
            exact = bounds[0]
        else:
            low, high = max(low, bounds[0]), min(high, bounds[1])
    if exact is not None:
        return (exact, exact) if low <= exact < high else None
    return (low, high) if low < high else None


def _range_start(prev: Version, fix: Version) -> Version:
    """Where the affected range closed by *fix* starts: after *prev*'s release line (a backport's line)."""
    if prev[0] != fix[0]:
        return _bump(prev, 0)
    if prev[1] != fix[1]:
        return _bump(prev, 1)
    return _bump(prev, 2)


def _affected(v: Version, fixes: list[Version]) -> bool:
    """Whether version *v* is vulnerable, given the advisory's sorted fixed versions.

    Affected ranges are taken as ``[0, first fix)`` and, for each later fix,
    from the end of the previous fix's release line up to it (OSV's usual
    shape for backports: 4.5.14 and 6.1.7 leave 5.x affected).
    """
    if not fixes or v < fixes[0]:
        return True
    return any(_range_start(prev, fix) <= v < fix for prev, fix in pairwise(fixes))


def starter_spec_affected(spec: str, fixed_versions: list[str]) -> bool | None:
    """Whether a fresh install of *spec* gets a vulnerable version (``None``: the spec cannot be read).

    An exact pin installs that version; a range installs its newest version,
    which is fixed when a fix lies within the range.
    """
    bounds = spec_range(spec)
    if bounds is None:
        return None
    fixes = sorted(v for v in (_version(f) for f in fixed_versions) if v is not None)
    low, high = bounds
    if low == high:
        return _affected(low, fixes)
    if not fixes:
        return True
    if high == _INF:
        return False
    # The newest version below ``high``: still before the first fix, or inside a later affected range.
    return fixes[0] >= high or any(_range_start(prev, fix) < high <= fix for prev, fix in pairwise(fixes))


# ── ownership ────────────────────────────────────────────────────────────


def _cluster_fixes(cluster: Any) -> list[str] | None:
    """The fixed versions the cluster's members name (``None``: no member says)."""
    fixes: list[str] | None = None
    for m in cluster.members:
        found = m.get("fixed_versions")
        if found is not None:
            fixes = [*(fixes or []), *found]
    return fixes


def _versions(texts: Any) -> set[Version]:
    return {v for v in (_version(t) for t in texts or ()) if v is not None}


def _cluster_installed(cluster: Any) -> set[Version]:
    """The installed versions the cluster's members name (vulnerability scanners report them)."""
    return _versions(m.get("installed_version") for m in cluster.members)


def _same_spec(declared: str, starter: str) -> bool:
    """Whether a declared spec is the starter's own (``^1.6`` is ``^1.6.0``)."""
    if declared == starter:
        return True
    bounds = spec_range(declared)
    return bounds is not None and bounds == spec_range(starter)


def _all_affected(versions: set[Version], fixes: list[str]) -> bool | None:
    """Whether any of *versions* is affected (``None``: no version to judge)."""
    if not versions:
        return None
    fixed = sorted(_versions(fixes))
    return any(_affected(v, fixed) for v in versions)


@dataclass
class DependencyOwnership:
    """One app's declared dependencies next to its starter's, for attributing advisories.

    ``installed`` is what the app has installed (its lockfile, its exact pins
    and the versions the scanners found); ``scope`` links the other apps of
    the same study scope (:func:`dependency_scope`).
    """

    app: dict[str, str]
    starter: dict[str, str]
    requirers: dict[str, set[str]] = field(default_factory=dict)
    installed: dict[str, set[Version]] = field(default_factory=dict)
    scope: DependencyScope | None = field(default=None, repr=False, compare=False)

    def model_added(self) -> set[str]:
        """Packages the app declares that its starter does not."""
        return set(self.app) - set(self.starter)

    def _roots(self, package: str) -> set[str]:
        """The declared packages the lockfile shows pulling *package* in (directly or through others)."""
        seen: set[str] = set()
        roots: set[str] = set()
        stack = [package]
        while stack:
            for parent in self.requirers.get(stack.pop(), ()):
                if parent in seen:
                    continue
                seen.add(parent)
                if parent == _ROOT:
                    continue
                if parent in self.app:
                    roots.add(parent)
                stack.append(parent)
        return roots

    def _transitive_is_models(self, package: str, cluster: Any) -> bool:
        roots = self._roots(package)
        if not roots:
            return False  # no lockfile shows who pulls it in
        starter_roots = {r: self.starter[r] for r in roots if r in self.starter}
        if not starter_roots:
            return True  # only model-added packages pull it in
        changed = [r for r, spec in starter_roots.items() if not _same_spec(self.app[r], spec)]
        fixes = _cluster_fixes(cluster)
        if not changed or self.scope is None or fixes is None:
            return False  # the starter's own packages pull it in
        # The model re-pinned a starter package that pulls it in: the model's
        # only if the apps that kept the starter's specs installed a fixed version.
        return _all_affected(self.scope.resolved_transitive(package, starter_roots), fixes) is False

    def is_models(self, cluster: Any) -> bool:
        """Whether a dependency advisory cluster is in a version of a package the model chose."""
        package = cluster_package(cluster)
        if not package:
            return False
        if package not in self.app:
            return self._transitive_is_models(package, cluster)
        if package not in self.starter:
            return True
        if _same_spec(self.app[package], self.starter[package]):
            return False  # the starter's own spec
        return self._changed_spec_is_models(package, cluster)

    def _changed_spec_is_models(self, package: str, cluster: Any) -> bool:
        """The model changed the starter's spec: the advisory is the starter's if its own spec installs it too."""
        starter = self.starter[package]
        fixes = _cluster_fixes(cluster)
        if fixes is None:
            return False
        if self.scope is None:
            return starter_spec_affected(starter, fixes) is False
        affected = _all_affected(self.scope.resolved(package, starter), fixes)
        if affected is not None:
            return not affected
        # Nothing in scope shows what the starter's range installs. A version
        # the range allows is the model's only when a newer one in the range,
        # installed by other apps in scope, is fixed: pinning what the range
        # installed at the time is the starter's choice, not the model's.
        installed = _cluster_installed(cluster) or self.installed.get(package, set())
        bounds = spec_range(starter)
        if installed and bounds is not None and all(bounds[0] <= v < bounds[1] for v in installed):
            in_range = {v for v in self.scope.seen(package, besides=self) if bounds[0] <= v < bounds[1]}
            if in_range:
                newer = {v for v in in_range if v > max(installed)}
                return bool(newer) and _all_affected({max(newer)}, fixes) is False
        # No evidence at all: the range is taken to install its newest version.
        return starter_spec_affected(starter, fixes) is False


@dataclass
class DependencyScope:
    """What the apps of one study scope declare and install: the evidence of what a starter spec installs."""

    apps: list[DependencyOwnership]

    def resolved(self, package: str, spec: str) -> set[Version]:
        """Versions *spec* installed for *package*: an exact pin's version, else what scope apps declaring it got."""
        bounds = spec_range(spec)
        if bounds is None:
            return set()
        if bounds[0] == bounds[1]:
            return {bounds[0]}
        # Only copies inside the range: a nested copy outside it (pulled in by
        # another package) is not what this spec installed.
        return {
            v
            for a in self.apps
            if package in a.app and spec_range(a.app[package]) == bounds
            for v in a.installed.get(package, ())
            if bounds[0] <= v < bounds[1]
        }

    def resolved_transitive(self, package: str, roots: dict[str, str]) -> set[Version]:
        """Versions of *package* installed in the scope apps that declare every root with the given spec."""
        return {
            v
            for a in self.apps
            if all(r in a.app and _same_spec(a.app[r], spec) for r, spec in roots.items())
            for v in a.installed.get(package, ())
        }

    def seen(self, package: str, *, besides: DependencyOwnership | None = None) -> set[Version]:
        """Every version of *package* installed in scope (by apps other than *besides*)."""
        return {v for a in self.apps if a is not besides for v in a.installed.get(package, ())}


def dependency_scope(ownerships: dict[str, DependencyOwnership], clusters: Any = ()) -> DependencyScope:
    """Link the ownerships of one scope (``{run id: ownership}``) so changed specs are judged on the scope's evidence.

    *clusters* (the scope's advisory clusters, ``run_id`` = the GenerationRun id) add the
    installed versions the scanners found to their app's ``installed``. Pass
    every app of the compared scope at once, as for the issue tables.
    """
    for c in clusters:
        own = ownerships.get(str(c.run_id))
        package = cluster_package(c)
        if own is not None and package:
            own.installed.setdefault(package, set()).update(_cluster_installed(c))
    scope = DependencyScope(list(ownerships.values()))
    for own in ownerships.values():
        own.scope = scope
    return scope


def _starter_of(run: GenerationRun) -> Starter | None:
    """The starter the run's tree grew from (a slot variant's is its parent's)."""
    if run.starter_id is not None:
        return run.starter
    parent = run.parent_run if run.parent_run_id is not None else None
    return parent.starter if parent is not None and parent.starter_id is not None else None


def _is_manifest(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].lower()
    return base == "package.json" or (base.startswith("requirements") and base.endswith(".txt"))


def _starter_manifest_specs(starter: Starter | None) -> dict[str, str]:
    """``{package: spec}`` the starter's own ``requirements*.txt``/``package.json`` declare (its pinned version)."""
    if starter is None:
        return {}
    from backend.generation import blobs

    files: dict[str, str] = {}
    entries = (starter.content or {}).get("files") if isinstance(starter.content, dict) else None
    for path, entry in (entries or {}).items():
        if not _is_manifest(path) or "node_modules/" in path or not isinstance(entry, dict) or not entry.get("blob"):
            continue
        try:
            files[path] = blobs.get_text(entry["blob"])
        except (OSError, LookupError, ValueError):
            logger.warning("starter %s: manifest blob of %s is missing", starter.pk, path)
    return manifest_specs(files)


def starter_specs(run: GenerationRun, memo: dict | None = None) -> dict[str, str]:
    """``{package: spec}`` the run's starter declares (the starter version pinned in the run).

    *memo* caches starter reads across the runs of one call.
    """
    starter = _starter_of(run)
    key = getattr(starter, "pk", None)
    if memo is None:
        return _starter_manifest_specs(starter)
    if key not in memo:
        memo[key] = _starter_manifest_specs(starter)
    return memo[key]


def starter_packages(run: GenerationRun, memo: dict | None = None) -> frozenset[str]:
    """Packages the run's starter declares."""
    return frozenset(starter_specs(run, memo))


def run_files(run: GenerationRun) -> dict[str, str]:
    """The run's app tree as analyzed (``RunView.code_files("app")``), without dependency or cache dirs.

    Files inside dependency, cache or scratch directories are left out, as the analysis leaves them out: a
    package an agent unpacked to read its docs does not declare the app's dependencies.
    """
    from backend.generation.runs.view import run_view

    return _analyzed(run_view(run).code_files("app"))


def _analyzed(files: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in files.items() if isinstance(v, str) and not is_non_source_path(k)}


def dependency_ownership(
    run: GenerationRun,
    memo: dict | None = None,
    *,
    files: dict[str, str] | None = None,
) -> DependencyOwnership:
    """The run's declared dependencies, its starter's, its lockfile graph and what it has installed.

    *files* is the run's app-scope ``{path: text}`` when the caller read it in bulk
    (``run_views_code(runs, "app")``); else it is read here. Link the ownerships of one scope with
    :func:`dependency_scope` so a changed starter spec is judged on what the scope's apps installed.
    """
    files = _analyzed(files) if files is not None else run_files(run)
    app = manifest_specs(files)
    requirers, lock = _lock_graph(files)
    installed = {name: _versions(found) for name, found in lock.items()}
    for name, spec in app.items():
        bounds = spec_range(spec)
        if bounds is not None and bounds[0] == bounds[1]:  # an exact pin installs that version
            installed.setdefault(name, set()).add(bounds[0])
    return DependencyOwnership(
        app=app,
        starter=starter_specs(run, memo),
        requirers=requirers,
        installed=installed,
    )


def model_added_packages(run: GenerationRun, memo: dict | None = None) -> set[str]:
    """Packages the app declares that its starter does not: the model's own dependency choices."""
    return manifest_packages(run_files(run)) - starter_packages(run, memo)


def cluster_package(cluster: Any) -> str:
    """The (normalized) package a dependency issue cluster is about, or ""."""
    for m in cluster.members:
        if m.get("package"):
            return normalize_package(m["package"])
    return ""
