"""Starter lockfiles: generation (``manage.py build_starters --lock``) and the checked-in lock index.

A ``minimal`` starter ships a manifest **and** a lockfile for each ecosystem (spec section 11.4). The
manifests live in the fragments (``data/starters/{backend,frontend}/<framework>/<level>/``); the lockfiles
are generated inside the matching digest-pinned base image and checked in under ``data/starters/lock/``,
mirroring the fragment path:

    lock/backend/flask/minimal/backend/requirements.lock      (for backend/flask/minimal/backend/requirements.txt)
    lock/frontend/react/minimal/frontend/package-lock.json    (for frontend/react/minimal/frontend/package.json)

``lock/index.yaml`` records, per lock, the manifest's sha, the lock's sha, the image and the tool that
produced it. Composition (``build.starters``) refuses a lock whose recorded manifest sha differs from the
manifest on disk (``StaleLockError``): editing a manifest without re-locking cannot go unnoticed.

Lock commands (one container per manifest, files copied in and out through the Docker API, so the daemon may
be remote, e.g. apps-dind):

* PyPI: ``uv pip compile --generate-hashes`` of ``requirements.txt`` into ``requirements.lock``. The starter
  Dockerfiles install ``pip install -r requirements.txt -c requirements.lock`` (the lock is a constraints file).
* npm: ``npm install --package-lock-only --ignore-scripts`` writes ``package-lock.json`` (``npm ci``).
* Go (``go mod tidy`` -> ``go.sum``) and Maven (``dependency:resolve`` + a recorded ``dependency:list``) are
  phase B (wave 3): their specs are listed in ``ECOSYSTEMS`` so the seam is explicit, but no phase-A fragment
  has such a manifest.

Lock completeness on the build seam (``check_tree_locks``, ``lock_covers``): a starter lock is generated for
the starter's manifest only. When the model adds a dependency, the hashed ``requirements.lock`` (pip
``--require-hashes`` mode) or ``package-lock.json`` (``npm ci``) next to the edited manifest makes the build
fail for a reason the model did not cause. ``prepare_build_dir`` therefore records every manifest/lock pair
of the build context (``BuildContextReport.locks``, note ``lock_incomplete``): a starter-origin lock next to
a manifest it was not generated from is ``stale_starter_lock``, and a lock that does not pin every direct
requirement is ``not_covered``. Such a lock is the platform lock step's job (``ResolvedLock``), never a
model-attributed ``build_fail``.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shlex
import tarfile
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from typing import Final
from typing import Protocol

import yaml

from backend.generation import identity
from backend.generation.build import dockerfiles

__all__ = [
    "ECOSYSTEMS",
    "LOCK_COMPLETE",
    "LOCK_INCOMPLETE",
    "LOCK_INDEX_PATH",
    "LOCK_ROOT",
    "DockerLockRunner",
    "LockCheck",
    "LockError",
    "LockRunner",
    "LockSpec",
    "MissingLockError",
    "StaleLockError",
    "check_tree_locks",
    "generate_locks",
    "lock_covers",
    "lock_index",
    "lock_specs",
    "locks_for_fragment",
]

logger = logging.getLogger(__name__)

LOCK_ROOT: Final = dockerfiles.STARTERS_ROOT / "lock"
LOCK_INDEX_PATH: Final = LOCK_ROOT / "index.yaml"
_WORKDIR: Final = "/work"


class LockError(RuntimeError):
    """A lockfile could not be produced or used."""


class StaleLockError(LockError):
    """The checked-in lock was generated from other manifest bytes than the fragment holds now."""


class MissingLockError(LockError):
    """A ``minimal`` fragment manifest has no checked-in lock."""


@dataclass(frozen=True)
class Ecosystem:
    """How one ecosystem's manifest is locked."""

    name: str
    manifest: str  # file name of the manifest
    lock: str  # file name of the lock written next to it
    image_key: str  # base_images.yaml key of the image the lock runs in
    script: Callable[[Mapping[str, str]], str]  # tools -> shell script run in the manifest's directory
    phase: str = "A"


def _pypi_script(tools: Mapping[str, str]) -> str:
    uv = shlex.quote(f"uv=={tools.get('uv', '0.13.7')}")
    return (
        f"pip install --no-cache-dir --disable-pip-version-check -q {uv} >/dev/null"
        " && uv pip compile --quiet --no-header --generate-hashes --python-version 3.13"
        " requirements.txt -o requirements.lock"
    )


def _npm_script(_tools: Mapping[str, str]) -> str:
    return "npm install --package-lock-only --ignore-scripts --no-audit --no-fund --loglevel=error"


def _go_script(_tools: Mapping[str, str]) -> str:
    return "go mod tidy"


def _maven_script(_tools: Mapping[str, str]) -> str:
    return (
        "mvn -q -B dependency:resolve"
        " && mvn -q -B dependency:list -DoutputFile=dependency-list.txt -DincludeScope=runtime"
    )


ECOSYSTEMS: Final = {
    "pypi": Ecosystem("pypi", "requirements.txt", "requirements.lock", "python", _pypi_script),
    "npm": Ecosystem("npm", "package.json", "package-lock.json", "node", _npm_script),
    "go": Ecosystem("go", "go.mod", "go.sum", "go", _go_script, phase="B"),
    "maven": Ecosystem("maven", "pom.xml", "dependency-list.txt", "maven", _maven_script, phase="B"),
}
_BY_MANIFEST: Final = {eco.manifest: eco for eco in ECOSYSTEMS.values()}


@dataclass(frozen=True)
class LockSpec:
    """One manifest to lock: ``fragment`` (``backend/flask/minimal``) and the manifest path inside it."""

    fragment: str
    manifest: str  # path inside the fragment, e.g. backend/requirements.txt
    ecosystem: Ecosystem

    @property
    def lock(self) -> str:
        """The lock's path inside the fragment tree (and inside the starter)."""
        return (PurePosixPath(self.manifest).parent / self.ecosystem.lock).as_posix()

    @property
    def key(self) -> str:
        return f"{self.fragment}:{self.manifest}"

    def manifest_bytes(self, root: Path | None = None) -> bytes:
        return ((root or dockerfiles.STARTERS_ROOT) / self.fragment / self.manifest).read_bytes()


def lock_specs(fragments: Iterable[str], *, root: Path | None = None) -> list[LockSpec]:
    """Every lockable manifest (plain files, not templates) inside the given fragment directories."""
    base = root or dockerfiles.STARTERS_ROOT
    specs: list[LockSpec] = []
    for fragment in sorted(set(fragments)):
        directory = base / fragment
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            ecosystem = _BY_MANIFEST.get(path.name)
            if ecosystem is not None and path.is_file() and "node_modules" not in path.parts:
                specs.append(LockSpec(fragment, path.relative_to(directory).as_posix(), ecosystem))
    return specs


# =====================================================================================
# The index
# =====================================================================================


def lock_index(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """``{"<fragment>:<manifest>": {manifest_sha, lock, lock_sha, image, tool}}`` (empty without an index)."""
    path = path or LOCK_INDEX_PATH
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(key): dict(value) for key, value in (data.get("locks") or {}).items()}


def _write_index(entries: Mapping[str, Mapping[str, Any]], path: Path) -> None:
    header = (
        "# Generated by `manage.py build_starters --lock` (backend/generation/build/locks.py). Do not edit:\n"
        "# composition refuses a lock whose manifest_sha differs from the fragment's manifest.\n"
    )
    body = yaml.safe_dump({"version": 1, "locks": dict(sorted(entries.items()))}, sort_keys=True, width=120)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + body, encoding="utf-8")


def locks_for_fragment(
    fragment: str,
    *,
    root: Path | None = None,
    lock_root: Path | None = None,
    index: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, bytes]:
    """``{path inside the starter: lock bytes}`` for every manifest of ``fragment``.

    Raises ``MissingLockError`` when a manifest has no index entry or lock file, and ``StaleLockError`` when
    the manifest or the lock file changed since the lock was generated.
    """
    lock_root = lock_root or LOCK_ROOT
    index = index if index is not None else lock_index(lock_root / "index.yaml")
    out: dict[str, bytes] = {}
    for spec in lock_specs([fragment], root=root):
        entry = index.get(spec.key)
        lock_path = lock_root / spec.fragment / spec.lock
        if entry is None or not lock_path.is_file():
            msg = f"{spec.key} has no lock; run `manage.py build_starters --lock`"
            raise MissingLockError(msg)
        if entry.get("manifest_sha") != identity.bytes_sha(spec.manifest_bytes(root)):
            msg = f"{spec.key} changed since it was locked; run `manage.py build_starters --lock`"
            raise StaleLockError(msg)
        data = lock_path.read_bytes()
        if entry.get("lock_sha") != identity.bytes_sha(data):
            msg = f"{lock_path} differs from the lock recorded in the index; regenerate it"
            raise StaleLockError(msg)
        out[spec.lock] = data
    return out


# =====================================================================================
# Completeness of a manifest/lock pair in a build context
# =====================================================================================

LOCK_COMPLETE: Final = "complete"
LOCK_INCOMPLETE: Final = "incomplete"
_PEP503: Final = re.compile(r"[-_.]+")
_PINNED: Final = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*===?\s*([^\s;\\]+)")
_NPM_FIELDS: Final = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")


def _canonical(name: str) -> str:
    return _PEP503.sub("-", name).lower()


def _requirement_lines(text: str) -> list[str]:
    """Logical requirement lines (continuations joined, comments and blank lines dropped)."""
    out: list[str] = []
    buffer = ""
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        line = (buffer + line).strip()
        buffer = ""
        if line:
            out.append(line)
    if buffer.strip():
        out.append(buffer.strip())
    return out


def _lock_pins(lock: bytes) -> dict[str, str] | None:
    """``{canonical name: version}`` of a pip lock; ``None`` when an entry is not pinned with ``==``."""
    pinned: dict[str, str] = {}
    for line in _requirement_lines(lock.decode("utf-8", errors="replace")):
        if line.startswith("-"):
            continue  # options such as --index-url (hash continuations are joined to their entry)
        match = _PINNED.match(line)
        if match is None:
            return None  # an unpinned or URL entry: not a lock
        pinned[_canonical(match.group(1))] = match.group(2)
    return pinned


def _requirement_pinned(line: str, pinned: Mapping[str, str]) -> bool:
    """Whether one ``requirements.txt`` line is satisfied by a pin of the lock."""
    from packaging.requirements import InvalidRequirement
    from packaging.requirements import Requirement
    from packaging.version import InvalidVersion
    from packaging.version import Version

    try:
        # -r / -c / -e / --index-url and URL requirements cannot be verified against the lock.
        requirement = None if line.startswith("-") else Requirement(line.split(" --hash", 1)[0].strip())
    except InvalidRequirement:
        requirement = None
    if requirement is None or requirement.url:
        return False
    if requirement.marker is not None and not requirement.marker.evaluate({"python_version": "3.13"}):
        return True  # not installed on the image's Python
    version = pinned.get(_canonical(requirement.name))
    if version is None:
        return False
    try:
        return requirement.specifier.contains(Version(version), prereleases=True)
    except InvalidVersion:
        return False


def _pypi_covers(manifest: bytes, lock: bytes) -> bool:
    pinned = _lock_pins(lock)
    if pinned is None:
        return False
    lines = _requirement_lines(manifest.decode("utf-8", errors="replace"))
    return all(_requirement_pinned(line, pinned) for line in lines)


def _npm_covers(manifest: bytes, lock: bytes) -> bool:
    try:
        package = json.loads(manifest)
        locked = json.loads(lock)
    except ValueError:
        return False
    if not isinstance(package, dict) or not isinstance(locked, dict):
        return False
    root = (locked.get("packages") or {}).get("")
    if not isinstance(root, dict):
        return False  # lockfileVersion 1 or no root entry: npm ci cannot verify it either
    return all((package.get(key) or {}) == (root.get(key) or {}) for key in _NPM_FIELDS)


def lock_covers(manifest_bytes: bytes, lock_bytes: bytes, *, ecosystem: str = "pypi") -> bool:
    """Whether the lock pins every direct requirement of the manifest (necessary for a usable lock).

    * ``pypi``: every requirement line of ``requirements.txt`` is a named requirement (no ``-r``/``-e``/URL)
      whose name is pinned ``==`` in the lock with a version its specifier accepts (markers evaluated for
      Python 3.13).
    * ``npm``: ``package-lock.json``'s root package declares exactly the manifest's dependency maps (what
      ``npm ci`` checks).

    Transitive closure cannot be checked offline; a starter lock next to an edited manifest is caught by
    ``check_tree_locks`` (``stale_starter_lock``) even when it happens to cover. Other ecosystems: ``False``.
    """
    if ecosystem == "pypi":
        return _pypi_covers(manifest_bytes, lock_bytes)
    if ecosystem == "npm":
        return _npm_covers(manifest_bytes, lock_bytes)
    return False


@dataclass(frozen=True)
class LockCheck:
    """One manifest/lock pair of a build context (``BuildContextReport.locks``)."""

    manifest: str
    lock: str
    ecosystem: str
    status: str  # complete | incomplete | absent
    reason: str | None = None  # stale_starter_lock | not_covered (incomplete only)
    source: str = "tree"  # tree | starter | platform: where the lock bytes come from

    @property
    def lock_present(self) -> bool:
        return self.status != "absent"

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "lock": self.lock,
            "ecosystem": self.ecosystem,
            "status": self.status,
            "reason": self.reason,
            "source": self.source,
        }


def _starter_locks(index: Mapping[str, Mapping[str, Any]]) -> dict[str, set[str]]:
    """``{lock_sha: {manifest_sha, ...}}`` of every checked-in starter lock."""
    out: dict[str, set[str]] = {}
    for entry in index.values():
        lock_sha, manifest_sha = entry.get("lock_sha"), entry.get("manifest_sha")
        if lock_sha and manifest_sha:
            out.setdefault(str(lock_sha), set()).add(str(manifest_sha))
    return out


def check_tree_locks(
    files: Mapping[str, bytes],
    *,
    origins: Mapping[str, str] | None = None,
    platform_locks: Iterable[str] = (),
    index: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[LockCheck]:
    """Every phase-A manifest of ``files`` (``{path: bytes}``) with the state of the lock next to it.

    A lock is ``incomplete`` (``stale_starter_lock``) when it is a starter lock (its bytes equal a checked-in
    starter lock, or its ``origins`` entry is ``starter``) and the manifest beside it is not the one it was
    generated from (another sha than the index records, or a manifest whose origin is not ``starter``); it is
    ``incomplete`` (``not_covered``) when it does not pin every direct requirement (``lock_covers``). Paths in
    ``platform_locks`` came from the platform lock step (``ResolvedLock.files``) and are only coverage
    checked. A manifest without a lock is ``absent`` (the starter Dockerfiles install it unlocked).
    """
    origins = origins or {}
    platform = set(platform_locks)
    starter_locks = _starter_locks(index if index is not None else lock_index())
    out: list[LockCheck] = []
    for path in sorted(files):
        pure = PurePosixPath(path)
        ecosystem = _BY_MANIFEST.get(pure.name)
        if ecosystem is None or ecosystem.phase != "A" or "node_modules" in pure.parts:
            continue
        lock_path = (pure.parent / ecosystem.lock).as_posix()
        manifest = files[path]
        lock = files.get(lock_path)
        if lock is None:
            out.append(LockCheck(path, lock_path, ecosystem.name, "absent"))
            continue
        lock_sha = identity.bytes_sha(lock)
        if lock_path in platform:
            source = "platform"
        elif lock_sha in starter_locks or origins.get(lock_path) == "starter":
            source = "starter"
        else:
            source = "tree"
        reason: str | None = None
        if source == "starter":
            recorded = starter_locks.get(lock_sha)
            if recorded is not None:
                stale = identity.bytes_sha(manifest) not in recorded
            else:
                stale = origins.get(path, "starter") != "starter"
            if stale:
                reason = "stale_starter_lock"
        if reason is None and not lock_covers(manifest, lock, ecosystem=ecosystem.name):
            reason = "not_covered"
        status = LOCK_INCOMPLETE if reason else LOCK_COMPLETE
        out.append(LockCheck(path, lock_path, ecosystem.name, status, reason, source))
    return out


# =====================================================================================
# Generation
# =====================================================================================


class LockRunner(Protocol):
    """Runs ``script`` in ``image`` with ``files`` in the working directory and returns ``outputs``."""

    def run(self, image: str, files: Mapping[str, bytes], script: str, outputs: Iterable[str]) -> dict[str, bytes]:
        """``{output name: bytes}``; raises ``LockError`` when the script fails or an output is missing."""
        ...


def _tar(files: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.uid = info.gid = 1000
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _untar_one(chunks: Iterable[bytes]) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(b"".join(chunks)), mode="r") as tar:
        member = next((m for m in tar.getmembers() if m.isfile()), None)
        handle = tar.extractfile(member) if member is not None else None
        if handle is None:
            msg = "the lock container returned no file"
            raise LockError(msg)
        return handle.read()


class DockerLockRunner:
    """``LockRunner`` on a Docker daemon (apps-dind by default; ``settings.STARTERS_DOCKER_HOST`` overrides).

    Files go in with ``put_archive`` and come out with ``get_archive``, so no bind mount is needed and the
    daemon may run on another host. The container is removed afterwards in every case.
    """

    def __init__(self, client: Any | None = None, *, timeout_s: int = 900) -> None:
        self.client = client if client is not None else starters_docker_client()
        self.timeout_s = timeout_s

    def run(self, image: str, files: Mapping[str, bytes], script: str, outputs: Iterable[str]) -> dict[str, bytes]:
        self.client.images.pull(image)
        container = self.client.containers.create(
            image,
            command=["sh", "-c", f"mkdir -p {_WORKDIR} && cd {_WORKDIR} && {script}"],
            working_dir="/",
            # Paths inside the throwaway lock container, not on this host.
            environment={"HOME": "/tmp", "npm_config_cache": "/tmp/npm-cache", "UV_CACHE_DIR": "/tmp/uv-cache"},  # noqa: S108
            labels={"llmlab.purpose": "starter-lock"},
        )
        try:
            container.put_archive("/", _tar({f"{_WORKDIR.strip('/')}/{name}": data for name, data in files.items()}))
            container.start()
            status = container.wait(timeout=self.timeout_s)
            logs = container.logs().decode("utf-8", errors="replace")
            if int(status.get("StatusCode", 1)) != 0:
                msg = f"lock script failed in {image} (exit {status.get('StatusCode')}): {logs[-2000:]}"
                raise LockError(msg)
            result: dict[str, bytes] = {}
            for name in outputs:
                chunks, _ = container.get_archive(f"{_WORKDIR}/{name}")
                result[name] = _untar_one(chunks)
            return result
        finally:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001 - cleanup must not hide the real error
                logger.warning("could not remove lock container %s", getattr(container, "id", "?"))


def starters_docker_client() -> Any:
    """Docker client for starter locks and docker tests: ``STARTERS_DOCKER_HOST`` or the apps daemon."""
    from django.conf import settings

    host = getattr(settings, "STARTERS_DOCKER_HOST", "") or ""
    if host:
        import docker

        return docker.DockerClient(base_url=host)
    from backend.runtime.services import docker_manager

    return docker_manager.new_client()


def generate_locks(
    fragments: Iterable[str],
    runner: LockRunner,
    *,
    root: Path | None = None,
    lock_root: Path | None = None,
    only_stale: bool = True,
    phases: Iterable[str] = ("A",),
) -> list[dict[str, Any]]:
    """(Re)generate the locks of ``fragments`` and update ``lock/index.yaml``; returns one record per spec.

    With ``only_stale`` (the default) a lock whose index entry still matches the manifest is kept as is.
    """
    lock_root = lock_root or LOCK_ROOT
    index_path = lock_root / "index.yaml"
    index = lock_index(index_path)
    images = dockerfiles.base_images()
    tools = dockerfiles.lock_tools()
    allowed_phases = set(phases)
    records: list[dict[str, Any]] = []
    for spec in lock_specs(fragments, root=root):
        if spec.ecosystem.phase not in allowed_phases:
            records.append({"key": spec.key, "status": "skipped", "reason": f"phase {spec.ecosystem.phase}"})
            continue
        manifest = spec.manifest_bytes(root)
        manifest_sha = identity.bytes_sha(manifest)
        lock_path = lock_root / spec.fragment / spec.lock
        current = index.get(spec.key)
        if (
            only_stale
            and current is not None
            and current.get("manifest_sha") == manifest_sha
            and lock_path.is_file()
            and current.get("lock_sha") == identity.bytes_sha(lock_path.read_bytes())
        ):
            records.append({"key": spec.key, "status": "fresh"})
            continue
        image = images[spec.ecosystem.image_key].pinned
        script = spec.ecosystem.script(tools)
        produced = runner.run(image, {spec.ecosystem.manifest: manifest}, script, [spec.ecosystem.lock])
        data = produced[spec.ecosystem.lock]
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_bytes(data)
        index[spec.key] = {
            "manifest": spec.manifest,
            "manifest_sha": manifest_sha,
            "lock": spec.lock,
            "lock_sha": identity.bytes_sha(data),
            "image": image,
            "tool": script,
        }
        _write_index(index, index_path)
        records.append({"key": spec.key, "status": "locked", "lock": spec.lock})
    return records
