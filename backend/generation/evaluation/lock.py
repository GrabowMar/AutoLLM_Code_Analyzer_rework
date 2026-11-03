"""Platform lock step: ``resolved_lock`` and constraint-pinned installs (spec section 6.10).

``resolve_lock(run)`` is the first stage of every evaluation and runs once per sample:

* A run that already has a lock (``resolved_lock_blob``, e.g. adopted through eval reuse) keeps it: a
  re-evaluation **never re-resolves**.
* If every phase-A manifest of the collected tree has a **complete** lock written by the model
  (``build.locks.check_tree_locks``: a starter lock next to a manifest the model changed is
  ``stale_starter_lock``, a lock missing a direct requirement is ``not_covered``), ``lock_source=model`` and
  the files are used as written (``ResolvedLock.files`` stays empty). A tree without manifests is
  vacuously complete (``model``, nothing to install).
* Otherwise the platform generates a lock for each manifest that lacks a complete one, in a container of the
  sandbox base image (``build.locks`` ecosystem scripts: ``uv pip compile --generate-hashes``, ``npm install
  --package-lock-only``), and stores the files in the blob store at the starter-convention paths
  (``<dir>/requirements.lock``, ``<dir>/package-lock.json``; WP-05) so the starter Dockerfiles install under
  them. ``lock_source=platform``, ``registry_snapshot_at`` = now.

Failures: a resolver that runs and reports an unresolvable requirement raises ``LockUnresolvableError``
(model-attributable: ``build_fail``, reason ``lock_unresolvable``); a registry/depcache/docker problem raises
``LockInfraError`` (``infra_error``). The runner is injectable (``runner=``) for tests.
"""

from __future__ import annotations

import json
import re
from datetime import UTC
from datetime import datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from backend.generation.contracts.evaluation import LockedPackage
from backend.generation.contracts.evaluation import ResolvedLock

if TYPE_CHECKING:
    from backend.generation.build.locks import LockRunner
    from backend.generation.models import GenerationRun

__all__ = [
    "LOCK_UNRESOLVABLE",
    "LockInfraError",
    "LockUnresolvableError",
    "load_lock",
    "lock_summary",
    "parse_lock_packages",
    "resolve_lock",
    "tree_files",
]

LOCK_UNRESOLVABLE: Final = "lock_unresolvable"
# Resolver output that means "this requirement cannot be satisfied" (the sample's fault), as opposed to a
# network, registry or docker failure.
_UNRESOLVABLE: Final = re.compile(
    r"No solution found|depends on .* (?:which|but)|no matching distribution|Could not find a version|"
    r"No matching version found|ETARGET|E404|is not in (?:the npm|this) registry|ERESOLVE|unsatisfiable|"
    r"Invalid requirement|Failed to parse|EJSONPARSE|not found in the package registry",
    re.IGNORECASE,
)
_KEY: Final = {"pypi": "pip", "npm": "npm", "go": "go", "maven": "maven"}


class LockUnresolvableError(RuntimeError):
    """The manifests cannot be resolved (model-attributable ``build_fail:lock_unresolvable``)."""

    reason = LOCK_UNRESOLVABLE

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


class LockInfraError(RuntimeError):
    """The lock step could not run (registry, depcache or docker outage): ``infra_error``."""

    reason = "lock_infra"


def tree_files(run: GenerationRun) -> tuple[dict[str, bytes], dict[str, str]]:
    """``({path: bytes}, {path: origin})`` of the run's app tree (``RunView``; slot variants overlaid)."""
    from backend.generation import blobs
    from backend.generation.runs.view import run_view

    entries = run_view(run).files("app")
    return {e.path: blobs.get_bytes(e.blob) for e in entries}, {e.path: e.origin for e in entries}


def load_lock(run: GenerationRun) -> ResolvedLock | None:
    """The run's stored ``ResolvedLock`` (``None`` when it has none)."""
    from backend.generation import blobs

    if not run.resolved_lock_blob:
        return None
    return ResolvedLock.model_validate(blobs.get_json(run.resolved_lock_blob))


def parse_lock_packages(ecosystem: str, data: bytes) -> list[LockedPackage]:
    """``[LockedPackage]`` of a pip constraints/requirements lock or an npm ``package-lock.json``."""
    text = data.decode("utf-8", errors="replace")
    out: list[LockedPackage] = []
    if ecosystem == "pypi":
        for raw in text.splitlines():
            line = raw.strip()
            m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;\\]+)", line)
            if m:
                out.append(LockedPackage(name=m.group(1).lower(), version=m.group(2)))
            h = re.search(r"--hash=(sha256:[0-9a-f]{64})", line)
            if h and out and out[-1].hash is None:
                out[-1] = out[-1].model_copy(update={"hash": h.group(1)})
    elif ecosystem == "npm":
        try:
            doc = json.loads(text)
        except ValueError:
            return out
        for path, row in sorted((doc.get("packages") or {}).items()):
            if path and isinstance(row, dict) and row.get("version"):
                name = path.rsplit("node_modules/", 1)[-1]
                out.append(LockedPackage(name=name, version=str(row["version"]), hash=row.get("integrity")))
    return out


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _platform_lock(
    run_files: dict[str, bytes],
    needs: list[Any],
    runner: LockRunner | None,
) -> tuple[dict[str, str], dict[str, list[LockedPackage]]]:
    from backend.generation import blobs
    from backend.generation.build import dockerfiles
    from backend.generation.build import locks as build_locks

    if runner is None:
        try:
            runner = build_locks.DockerLockRunner()
        except Exception as exc:
            msg = f"no docker for the lock step: {exc}"
            raise LockInfraError(msg) from exc
    try:
        images = dockerfiles.base_images()
        tools = dockerfiles.lock_tools()
    except Exception as exc:
        msg = f"base images unavailable: {exc}"
        raise LockInfraError(msg) from exc
    lock_files: dict[str, str] = {}
    ecosystems: dict[str, list[LockedPackage]] = {}
    for check in needs:
        eco = build_locks.ECOSYSTEMS[check.ecosystem]
        if eco.phase != "A":
            continue
        try:
            produced = runner.run(
                images[eco.image_key].pinned,
                {eco.manifest: run_files[check.manifest]},
                eco.script(tools),
                [eco.lock],
            )
        except build_locks.LockError as exc:
            text = str(exc)
            if _UNRESOLVABLE.search(text):
                detail = {"manifest": check.manifest, "error": text[-2000:]}
                msg = f"{check.manifest}: dependencies cannot be resolved"
                raise LockUnresolvableError(msg, detail) from exc
            msg = f"{check.manifest}: lock step failed: {text[-500:]}"
            raise LockInfraError(msg) from exc
        except (LockInfraError, LockUnresolvableError):
            raise
        except Exception as exc:
            msg = f"{check.manifest}: lock step failed: {exc}"
            raise LockInfraError(msg) from exc
        data = produced[eco.lock]
        if not build_locks.lock_covers(run_files[check.manifest], data, ecosystem=check.ecosystem):
            detail = {"manifest": check.manifest, "error": "the generated lock does not pin every requirement"}
            msg = f"{check.manifest}: the resolver did not pin every requirement"
            raise LockUnresolvableError(msg, detail)
        path = (PurePosixPath(check.manifest).parent / eco.lock).as_posix()
        lock_files[path] = blobs.put_bytes(data)
        ecosystems.setdefault(_KEY[check.ecosystem], []).extend(parse_lock_packages(check.ecosystem, data))
    return lock_files, ecosystems


def resolve_lock(run: GenerationRun, *, runner: LockRunner | None = None) -> ResolvedLock:
    """The lock every build and re-evaluation installs under; stored on the run (``resolved_lock_blob``)."""
    from backend.generation import blobs
    from backend.generation.build import locks as build_locks
    from backend.generation.models import GenerationRun

    existing = load_lock(run)
    if existing is not None:
        return existing  # re-evaluations never re-resolve
    files, origins = tree_files(run)
    checks = build_locks.check_tree_locks(files, origins=origins)
    ecosystems: dict[str, list[LockedPackage]] = {}
    for check in checks:
        if check.status == build_locks.LOCK_COMPLETE:
            ecosystems.setdefault(_KEY[check.ecosystem], []).extend(
                parse_lock_packages(check.ecosystem, files[check.lock]),
            )
    needs = [c for c in checks if c.status != build_locks.LOCK_COMPLETE]
    lock_files: dict[str, str] = {}
    source = "model"
    if needs:
        source = "platform"
        lock_files, platform = _platform_lock(files, needs, runner)
        for key, rows in platform.items():
            ecosystems.setdefault(key, []).extend(rows)
    now = _now()
    lock = ResolvedLock(ecosystems=ecosystems, files=lock_files, resolved_at=now.isoformat())
    sha = blobs.put_json(lock.model_dump(mode="json"))
    snapshot = now if source == "platform" else None
    updated = GenerationRun.objects.filter(pk=run.pk, resolved_lock_blob="").update(
        resolved_lock_blob=sha,
        lock_source=source,
        registry_snapshot_at=snapshot,
    )
    if updated:
        run.resolved_lock_blob, run.lock_source, run.registry_snapshot_at = sha, source, snapshot
        return lock
    # A concurrent evaluation stored one first: use that one (never two locks for one sample).
    run.refresh_from_db(fields=["resolved_lock_blob", "lock_source", "registry_snapshot_at"])
    return load_lock(run) or lock


def lock_summary(lock: ResolvedLock, source: str) -> dict[str, Any]:
    """``stages.lock.detail``: source, package counts per ecosystem and the platform lock files."""
    return {
        "source": source or "model",
        "packages": {eco: len(rows) for eco, rows in lock.ecosystems.items()},
        "files": sorted(lock.files),
        "resolved_at": lock.resolved_at,
    }
