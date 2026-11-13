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
* npm: the tree's own ``package-lock.json`` (the model's, or the starter's), when it is a v2+ lock, goes in
  next to ``package.json`` as the **seed** of ``npm install --package-lock-only``: npm keeps every locked
  version the manifest still accepts and only re-resolves what changed (usually just the root specs). Without
  the seed a fresh resolve takes whatever the registry serves today -- that is how a ``~1.2.14`` range picked
  up ``@rolldown/binding-linux-x64-gnu@1.2.18`` (built for x86-64-v3, ``SIGILL`` on the evaluation host) while
  the model had built with 1.2.16. A seed npm cannot use (the resolver fails, or the result does not cover the
  manifest) falls back to the manifest-only resolve, so a broken model lock is never ``lock_unresolvable`` by
  itself.
* Go (phase B): an incomplete or missing ``go.sum`` is completed by ``go mod download all`` in the pinned
  ``base_images.yaml`` ``go`` image (``GOTOOLCHAIN=local``; the tree's ``go.sum`` goes in too) and stored at
  ``<dir>/go.sum``; ``go.mod`` must come out unchanged. A ``go.mod`` without ``require``/``replace`` needs no
  ``go.sum``. Maven never reads its lock record (``dependency-list.txt``), so it is never platform-locked.

Failures: a resolver that runs and reports an unresolvable requirement raises ``LockUnresolvableError``
(model-attributable: ``build_fail``, reason ``lock_unresolvable``); a registry/depcache/docker problem raises
``LockInfraError`` (``infra_error``). The runner is injectable (``runner=``) for tests.
"""

from __future__ import annotations

import contextlib
import json
import posixpath
import re
import shlex
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
    r"Invalid requirement|Failed to parse|EJSONPARSE|not found in the package registry|"
    # A requirements.txt the resolver cannot read: broken syntax (a stray backslash, a hand-written hash
    # block) or an ``-r``/``-c`` include, which the platform never accepts (``lock_covers``: every line must
    # be a named requirement). Both runs of the 2026-11 study that hit these were labelled infra_error.
    r"expected '-c', '-e', '-r' or the start of a requirement|Error parsing included file|"
    r"Couldn't parse requirement|Unexpected '.{1,20}', expected",
    re.IGNORECASE,
)
# Go (``go mod download``) output that is the module's fault: a version or module that does not exist, a
# go.mod the pinned toolchain refuses (``go >= newer``), a go.sum whose recorded checksum is wrong.
_GO_UNRESOLVABLE: Final = re.compile(
    r"unknown revision|invalid version|no matching versions|malformed module path|invalid module path|"
    r"requires go >= |errors parsing go\.mod|go\.mod:\d+:|updates to go\.mod needed|checksum mismatch|"
    r"SECURITY ERROR|: (?:404 Not Found|410 Gone)|module .* found, but does not contain",
    re.IGNORECASE,
)
_GO_REPLACE: Final = re.compile(r"^\s*replace\b", re.MULTILINE)
_GO_REQUIRE: Final = re.compile(r"^\s*require\b", re.MULTILINE)
_GO_REPLACE_LINE: Final = re.compile(r"^(?:replace\s+)?\S+(?:\s+\S+)?\s*=>\s*(\S+)")
_KEY: Final = {"pypi": "pip", "npm": "npm", "go": "go", "maven": "maven"}


def _go_lock_script(_tools: Any) -> str:
    """The platform Go lock: complete ``go.sum`` from ``go.mod`` (+ the tree's ``go.sum``), never edit ``go.mod``.

    ``go mod download all`` adds the module and ``/go.mod`` sums of the whole build list; the pinned toolchain
    only (``GOTOOLCHAIN=local``), and ``go.mod`` must come out byte-identical.
    """
    return (
        "touch go.sum && cp go.mod /tmp/go.mod.in && GOTOOLCHAIN=local go mod download all"
        " && if ! cmp -s go.mod /tmp/go.mod.in; then echo 'go: updates to go.mod needed' >&2; exit 1; fi"
    )


def _go_local_replaces(text: str) -> list[str]:
    """The local-path targets (``./x``, ``../x``, ``/x``) of a ``go.mod``'s ``replace`` directives."""
    out: list[str] = []
    block = False
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if block:
            if line == ")":
                block = False
                continue
        elif re.match(r"replace\s*\($", line):
            block = True
            continue
        elif not line.startswith("replace"):
            continue
        match = _GO_REPLACE_LINE.match(line)
        target = match.group(1) if match else ""
        if target in {".", ".."} or target.startswith(("./", "../", "/")):
            out.append(target)
    return out


def _go_module_inputs(check: Any, run_files: dict[str, bytes]) -> tuple[str, dict[str, bytes]]:
    """``(module dir relative to the input root, inputs)`` for the Go lock of ``check``.

    A local-path ``replace`` makes ``go mod download all`` read the replacement's ``go.mod`` (only that: checked
    against ``golang:1.25`` offline). Those directories go in too, recursively, under the common ancestor of all
    of them, so the replacement resolves as it does in the build. A target missing from the tree, absolute or
    outside it is the sample's fault: ``LockUnresolvableError``, never an infra error.
    """
    module_dir = posixpath.dirname(check.manifest)
    dirs: list[str] = []
    todo = [module_dir]
    while todo:
        current = todo.pop()
        if current in dirs:
            continue
        dirs.append(current)
        source = posixpath.join(current, "go.mod")
        for target in _go_local_replaces(run_files[source].decode("utf-8", errors="replace")):
            resolved = posixpath.normpath(posixpath.join(current, target))
            resolved = "" if resolved == "." else resolved
            inside = not target.startswith("/") and resolved != ".." and not resolved.startswith("../")
            if not inside or posixpath.join(resolved, "go.mod") not in run_files:
                detail = {"manifest": check.manifest, "error": f"{source}: replace target {target} has no go.mod"}
                msg = f"{check.manifest}: local replace target {target} is missing from the tree"
                raise LockUnresolvableError(msg, detail)
            todo.append(resolved)
    common: list[str] = []
    for column in zip(*[d.split("/") if d else [] for d in dirs], strict=False):
        if len(set(column)) != 1:
            break
        common.append(column[0])
    root = "/".join(common)

    def rel(path: str) -> str:
        return posixpath.relpath(path, root) if root else path

    inputs: dict[str, bytes] = {}
    for directory in dirs:
        for name in ("go.mod", "go.sum"):
            path = posixpath.join(directory, name)
            if path in run_files:
                inputs[rel(path)] = run_files[path]
    subdir = rel(module_dir) if module_dir else ""
    return ("" if subdir == "." else subdir), inputs


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
    """``[LockedPackage]`` of a pip constraints/requirements lock, an npm ``package-lock.json`` or a ``go.sum``."""
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
    elif ecosystem == "go":
        # go.sum: "<module> <version> h1:<hash>"; the "<version>/go.mod" lines only pin a go.mod file.
        seen: set[tuple[str, str]] = set()
        for raw in text.splitlines():
            parts = raw.split()
            if len(parts) == 3 and not parts[1].endswith("/go.mod") and (parts[0], parts[1]) not in seen:
                seen.add((parts[0], parts[1]))
                out.append(LockedPackage(name=parts[0], version=parts[1], hash=parts[2]))
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


def _npm_seed(check: Any, run_files: dict[str, bytes]) -> bytes | None:
    """The tree's ``package-lock.json`` beside ``check.manifest`` when npm can seed a re-lock from it (v2+)."""
    data = run_files.get(check.lock)
    if not data:
        return None
    try:
        doc = json.loads(data)
    except ValueError:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("packages"), dict):
        return None
    version = doc.get("lockfileVersion")
    # v1 (npm 6) has no "packages" map to seed from.
    if isinstance(version, bool) or not isinstance(version, int) or version < 2:
        return None
    return data


def _platform_lock(
    run_files: dict[str, bytes],
    needs: list[Any],
    runner: LockRunner | None,
) -> tuple[dict[str, str], dict[str, list[LockedPackage]]]:
    from backend.generation import blobs
    from backend.generation.build import dockerfiles
    from backend.generation.build import locks as build_locks

    # Sample-attributable Go failures (a local replace target missing from the tree) before any docker work.
    go_inputs = {c.manifest: _go_module_inputs(c, run_files) for c in needs if c.ecosystem == "go"}
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
        if not eco.enforced:
            continue  # Maven never reads its lock record: nothing to complete
        inputs = {eco.manifest: run_files[check.manifest]}
        seed = _npm_seed(check, run_files) if check.ecosystem == "npm" else None
        script = eco.script(tools)
        output = eco.lock
        manifest_text = run_files[check.manifest].decode("utf-8", errors="replace")
        if check.ecosystem == "go":
            # go.mod, the tree's go.sum and the go.mod/go.sum of every local replace target, under their
            # common ancestor; the script runs in the module's directory.
            subdir, inputs = go_inputs[check.manifest]
            script = _go_lock_script(tools)
            if subdir:
                script = f"cd {shlex.quote(subdir)} && {script}"
                output = f"{subdir}/{eco.lock}"
        if seed is not None:
            # Seeded first; any failure or an uncovered result falls back to the manifest-only resolve below.
            seeded: bytes | None = None
            with contextlib.suppress(Exception):
                seeded = runner.run(images[eco.image_key].pinned, {**inputs, eco.lock: seed}, script, [output])[output]
            if seeded is not None and build_locks.lock_covers(run_files[check.manifest], seeded, ecosystem="npm"):
                path = (PurePosixPath(check.manifest).parent / eco.lock).as_posix()
                lock_files[path] = blobs.put_bytes(seeded)
                ecosystems.setdefault(_KEY[check.ecosystem], []).extend(parse_lock_packages("npm", seeded))
                continue
        try:
            produced = runner.run(images[eco.image_key].pinned, inputs, script, [output])
        except build_locks.LockError as exc:
            from backend.generation.evaluation.suite import platform_cpu_fault

            text = str(exc)
            pattern = _GO_UNRESOLVABLE if check.ecosystem == "go" else _UNRESOLVABLE
            # A resolver (or a build backend it ran) killed by SIGILL is the host CPU's, never the sample's
            # "unresolvable" -- even when its output also says "No solution found" (the suite labels it
            # infra_error:platform_cpu from this message).
            if pattern.search(text) and not platform_cpu_fault(text):
                detail = {"manifest": check.manifest, "error": text[-2000:]}
                msg = f"{check.manifest}: dependencies cannot be resolved"
                raise LockUnresolvableError(msg, detail) from exc
            died = " (killed by SIGILL: platform CPU)" if platform_cpu_fault(text) else ""
            msg = f"{check.manifest}: lock step failed{died}: {text[-500:]}"
            raise LockInfraError(msg) from exc
        except (LockInfraError, LockUnresolvableError):
            raise
        except Exception as exc:
            msg = f"{check.manifest}: lock step failed: {exc}"
            raise LockInfraError(msg) from exc
        data = produced[output]
        # A go.mod with ``replace`` directives is never "covered" offline (the sums belong to the replacement);
        # the go tool itself verified the download, so its go.sum is used as produced.
        replaced = check.ecosystem == "go" and bool(_GO_REPLACE.search(manifest_text))
        if not replaced and not build_locks.lock_covers(run_files[check.manifest], data, ecosystem=check.ecosystem):
            detail = {"manifest": check.manifest, "error": "the generated lock does not pin every requirement"}
            msg = f"{check.manifest}: the resolver did not pin every requirement"
            raise LockUnresolvableError(msg, detail)
        path = (PurePosixPath(check.manifest).parent / eco.lock).as_posix()
        lock_files[path] = blobs.put_bytes(data)
        ecosystems.setdefault(_KEY[check.ecosystem], []).extend(parse_lock_packages(check.ecosystem, data))
    return lock_files, ecosystems


def _lockable(check: Any, files: dict[str, bytes]) -> bool:
    """Whether a manifest without a complete lock needs one: a ``go.mod`` without ``require``/``replace``
    (standard library only) builds with no ``go.sum`` at all."""
    if check.ecosystem != "go":
        return True
    text = files[check.manifest].decode("utf-8", errors="replace")
    return bool(_GO_REQUIRE.search(text) or _GO_REPLACE.search(text))


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
    needs = [c for c in checks if c.status != build_locks.LOCK_COMPLETE and _lockable(c, files)]
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
