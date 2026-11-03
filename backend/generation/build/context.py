"""The build seam used by ``runtime/services/sample_builds.py`` (spec section 6.10; frozen interface).

``prepare_build_dir`` writes a run's collected tree verbatim (origins ``starter``, ``modified`` and
``authored``; artifacts excluded; deleted paths removed) plus the resolved lock files. A slot variant gets
its parent's tree (``RunView`` overlay) with the replacement spliced in (section 7.7). The model's files
are built exactly as written: the platform never patches them.

Free-choice stacks build the model's own Dockerfile. Its ``FROM`` lines are checked against the allowlist
(``build.dockerfiles.check_bases``, ``settings.SAMPLE_BUILD_ALLOWED_BASES``); a violation raises
``BaseNotAllowedError`` (outcome ``build_fail``, reason ``base_not_allowed``) and the verdict is in
``BuildContextReport.bases`` either way, for ``RunEvaluation.stages.build.detail``. The allowlist is the
one frozen in the run's manifest (``RunManifest.allowed_bases.refs``, digest-pinned): tag-only FROMs are
pinned to those digests at build time through BuildKit named contexts (``BuildContextReport.base_pins``,
``build_context_args()``), so a re-evaluation never re-resolves a tag. A manifest whose allowlist cannot be
reproduced raises ``AllowedBasesChangedError`` (an infra/config problem, never the model's).

Every manifest/lock pair of the context is checked (``build.locks.check_tree_locks``): a starter lock next to
a manifest the model edited, or a lock that does not pin every direct requirement, is recorded as
``lock_incomplete`` in ``BuildContextReport.locks`` / ``notes`` so the caller attributes the failure to the
lock step, not to the model.

WP-01 wrote the skeleton and WP-02a the app-run case and ``build_identity`` inside U1; WP-05 owns the module
since (allowed bases, the slot splice hook).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import StackProfile

__all__ = [
    "ALLOWED_BASES_CHANGED",
    "BASE_NOT_ALLOWED",
    "LOCK_INCOMPLETE",
    "AllowedBasesChangedError",
    "BaseNotAllowedError",
    "BuildContextReport",
    "VariantNotAssembledError",
    "build_identity",
    "frozen_allowed_bases",
    "prepare_build_dir",
]

_SLOT_KINDS = frozenset({"slot_single_shot", "slot_agent"})
BASE_NOT_ALLOWED = "base_not_allowed"
ALLOWED_BASES_CHANGED = "allowed_bases_changed"
LOCK_INCOMPLETE = "lock_incomplete"


class BaseNotAllowedError(ValueError):
    """A free-choice Dockerfile names a base image outside the allowlist (model-attributable ``build_fail``).

    ``reason`` is the outcome reason ``base_not_allowed``; ``check`` is the ``BaseCheck`` dict.
    """

    reason = BASE_NOT_ALLOWED

    def __init__(self, message: str, check: dict[str, Any]) -> None:
        super().__init__(message)
        self.check = check


class AllowedBasesChangedError(RuntimeError):
    """The run's frozen allowlist cannot be reproduced (infra/config, ``infra_error``; never ``build_fail``).

    Raised when the manifest's ``allowed_bases`` refs carry no digests or do not match its sha, and the current
    allowlist has another sha: building would re-resolve a tag or use digests the run was not generated with.
    """

    reason = ALLOWED_BASES_CHANGED


class VariantNotAssembledError(ValueError):
    """A slot variant has neither ``slot`` RunFile rows nor a replacement the splice hook can assemble."""


@dataclass
class BuildContextReport:
    """What the build context contains beyond the model's files.

    ``notes`` records facts such as the number of tree files written and the platform lock files added.
    ``bases`` is set for free-choice stacks only: the allowlist verdict of the model's Dockerfile
    (``BaseCheck.to_dict()``), including the digest each allowed base is expected to resolve to.
    ``base_pins`` (free-choice only) maps each tag-only FROM (``image:tag``) to the frozen ``ref@digest``;
    the build must pass ``build_context_args()`` to ``docker buildx build`` so the tag is never re-resolved.
    ``locks`` lists every manifest with a lock beside it (``build.locks.LockCheck.to_dict()``); an
    ``incomplete`` one is also noted as ``lock_incomplete: <lock> (<reason>; <source> lock)``.
    """

    notes: list[str] = field(default_factory=list)
    bases: dict[str, Any] | None = None
    base_pins: dict[str, str] = field(default_factory=dict)
    locks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def locks_incomplete(self) -> list[dict[str, Any]]:
        """The ``incomplete`` lock checks (the lock step must replace these locks before a build is measured)."""
        return [lock for lock in self.locks if lock.get("status") == "incomplete"]

    def build_context_args(self) -> list[str]:
        """``--build-context <image:tag>=docker-image://<ref@digest>`` for every pinned tag-only FROM."""
        from backend.generation.build import dockerfiles

        return dockerfiles.build_context_args(self.base_pins)

    def to_dict(self) -> dict[str, Any]:
        """The ``build_context`` record stored with a sample build."""
        out: dict[str, Any] = {"notes": list(self.notes)}
        if self.bases is not None:
            out["bases"] = self.bases
            out["base_pins"] = dict(self.base_pins)
        if self.locks:
            out["locks"] = [dict(lock) for lock in self.locks]
        return out


def _safe_target(root: Path, rel: str) -> Path:
    """``root / rel`` for a relative POSIX path that stays inside ``root`` (no ``..``, no absolute paths)."""
    parts = PurePosixPath(rel).parts
    if not rel or rel.startswith("/") or ".." in parts or "\\" in rel:
        msg = f"refusing unsafe build-context path {rel!r}"
        raise ValueError(msg)
    return root.joinpath(*parts)


def _write(target: Path, data: bytes, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.is_dir():
        msg = f"build-context path {target} already exists as a directory or symlink"
        raise ValueError(msg)
    target.write_bytes(data)
    # Normalised to 0644/0755: the cache key hashes the mode, and nothing else in the stored mode matters.
    target.chmod(0o755 if mode & 0o111 else 0o644)


def _parent(run: GenerationRun) -> GenerationRun | None:
    return run.parent_run if run.kind in _SLOT_KINDS and run.parent_run_id else None


def _lock_files(run: GenerationRun) -> dict[str, str]:
    """``{path: blob}`` of the platform lock (``ResolvedLock.files``); ``{}`` when the run has none."""
    sha = run.resolved_lock_blob
    parent = _parent(run)
    if not sha and parent is not None:
        sha = parent.resolved_lock_blob
    if not sha:
        return {}
    from backend.generation import blobs

    payload = blobs.get_json(sha)
    files = payload.get("files") if isinstance(payload, dict) else None
    return {str(path): str(blob) for path, blob in (files or {}).items() if blob}


def _stack(run: GenerationRun) -> StackProfile | None:
    parent = _parent(run)
    return run.stack_profile or (parent.stack_profile if parent is not None else None)


def _spliced_files(run: GenerationRun) -> dict[str, bytes]:
    """The variant's changed files from WP-12's splice hook, for a variant without ``slot`` RunFile rows.

    Contract (``slot.assemble.spliced_files(run) -> {path: bytes}``): pure, no database writes; the full
    bytes of every file the replacement changes, spliced at the target's span with the original's line
    endings (section 7.7), exactly what ``assemble`` would store as ``slot`` rows.
    """
    from backend.generation.slot import assemble

    hook = getattr(assemble, "spliced_files", None)
    if hook is None:
        msg = f"variant {run.pk} has no slot files and slot.assemble.spliced_files is not available"
        raise VariantNotAssembledError(msg)
    try:
        files = hook(run)
    except NotImplementedError as exc:
        msg = f"variant {run.pk} has no slot files and the splice hook is not implemented"
        raise VariantNotAssembledError(msg) from exc
    if not files:
        msg = f"variant {run.pk} has no slot files and no replacement to splice"
        raise VariantNotAssembledError(msg)
    return {str(path): bytes(data) for path, data in files.items()}


def _manifest_allowed_bases(run: GenerationRun) -> dict[str, Any] | None:
    """``RunManifest.allowed_bases`` of the run, else of its parent (a slot variant builds the parent's stack)."""
    from backend.generation.runs.view import run_view

    for source in (run, _parent(run)):
        if source is None:
            continue
        record = run_view(source).manifest().get("allowed_bases")
        if isinstance(record, dict) and record.get("refs"):
            return record
    return None


def frozen_allowed_bases(run: GenerationRun) -> tuple[list[Any], str, str]:
    """``(allowed bases, sha, source)``: the allowlist the run was generated against.

    ``source`` is ``manifest`` (digest-pinned refs frozen in the manifest), or ``allowlist`` (the current
    file, used when the run has no frozen allowlist, or when the manifest's undigested refs have the current
    sha). Raises ``AllowedBasesChangedError`` otherwise: the run's digests are unknown, and building would
    re-resolve tags.
    """
    from backend.generation.build import dockerfiles

    current = dockerfiles.allowed_bases()
    current_sha = dockerfiles.allowed_bases_record()["sha"]
    frozen = _manifest_allowed_bases(run)
    if frozen is None:
        return current, current_sha, "allowlist"
    sha = str(frozen.get("sha") or "")
    try:
        return dockerfiles.allowed_bases_from_refs(list(frozen["refs"]), sha=sha or None), sha, "manifest"
    except ValueError as exc:
        if sha == current_sha:
            return current, current_sha, "allowlist"
        msg = f"run {run.pk}: the allowlist frozen in its manifest cannot be reproduced ({exc}) and changed since"
        raise AllowedBasesChangedError(msg) from exc


def _check_free_choice(root: Path, run: GenerationRun, stack: StackProfile, report: BuildContextReport) -> None:
    from backend.generation.build import dockerfiles

    dockerfile = root / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8", errors="replace") if dockerfile.is_file() else ""
    allowed, sha, source = frozen_allowed_bases(run)
    check = dockerfiles.check_bases(text, allowed) if text else dockerfiles.BaseCheck(ok=False)
    if not text:
        check.violations.append({"line": 0, "image": "", "reason": "no_dockerfile"})
    check.allowed_bases_sha = sha
    report.bases = check.to_dict()
    report.bases["allowed_bases_source"] = source
    report.base_pins = dict(check.pins) if check.ok else {}
    if check.ok:
        if check.pins:
            report.notes.append(
                f"bases: {len(check.pins)} tag-only FROM pinned to the {source} digests (BuildKit named contexts)",
            )
        report.notes.append(f"bases: {len(check.bases)} FROM lines within the free-choice allowlist")
        return
    reasons = ", ".join(f"{v.get('image') or '-'} ({v['reason']})" for v in check.violations)
    report.notes.append(f"{BASE_NOT_ALLOWED}: {reasons}")
    msg = f"run {run.pk} ({stack.slug}): Dockerfile base not allowed: {reasons}"
    raise BaseNotAllowedError(msg, report.bases)


def _check_locks(root: Path, entries: list[Any], locks: dict[str, str], report: BuildContextReport) -> None:
    """Record every manifest/lock pair of the written context (``build.locks.check_tree_locks``)."""
    from backend.generation.build import locks as build_locks

    names = {eco.manifest for eco in build_locks.ECOSYSTEMS.values()} | {
        eco.lock for eco in build_locks.ECOSYSTEMS.values()
    }
    files: dict[str, bytes] = {}
    for path in sorted({entry.path for entry in entries} | set(locks)):
        if PurePosixPath(path).name in names:
            target = root.joinpath(*PurePosixPath(path).parts)
            if target.is_file():
                files[path] = target.read_bytes()
    if not files:
        return
    origins = {entry.path: entry.origin for entry in entries}
    # A manifest without a lock installs unlocked: nothing to record.
    checks = [c for c in build_locks.check_tree_locks(files, origins=origins, platform_locks=locks) if c.lock_present]
    report.locks = [check.to_dict() for check in checks]
    for check in checks:
        if check.status == build_locks.LOCK_INCOMPLETE:
            report.notes.append(f"{LOCK_INCOMPLETE}: {check.lock} ({check.reason}; {check.source} lock)")


def prepare_build_dir(
    run: GenerationRun,
    dest_path: Path,
    *,
    report: BuildContextReport | None = None,
) -> Path:
    """Write the build context of ``run`` into ``dest_path`` and return it.

    The tree is ``run_view(run).files("app")`` written verbatim (origins ``starter``, ``modified``,
    ``authored`` and ``slot``; artifacts excluded; deleted paths removed; for a slot variant the parent's
    tree with the variant's ``slot`` rows over it), plus the files of the run's resolved lock
    (``resolved_lock_blob``; a variant uses its parent's). A variant that has no ``slot`` rows yet gets its
    changed files from the splice hook (``slot.assemble.spliced_files``). A run without a collected tree
    raises ``ValueError``: there is nothing to build, and the caller must not record a build.

    Free-choice stacks: the model's own Dockerfile is checked against the allowlist frozen in the run's
    manifest; a violation raises ``BaseNotAllowedError`` (``build_fail`` / ``base_not_allowed``) after the
    tree is written, and tag-only FROMs are pinned through ``report.base_pins``.

    Locks: every manifest/lock pair is recorded in ``report.locks``; an incomplete one (stale starter lock,
    uncovered requirement) is noted as ``lock_incomplete`` (the caller must not attribute its build failure to
    the model).
    """
    from backend.generation import blobs
    from backend.generation.runs.view import run_view

    report = report if report is not None else BuildContextReport()
    root = Path(dest_path)
    root.mkdir(parents=True, exist_ok=True)
    view = run_view(run)
    entries = view.files("app")
    if not entries:
        msg = f"run {run.pk} has no collected tree to build"
        raise ValueError(msg)
    spliced: dict[str, bytes] = {}
    if run.kind in _SLOT_KINDS and not view.files("slot"):
        spliced = _spliced_files(run)
    for entry in entries:
        if entry.path in spliced:
            continue
        _write(_safe_target(root, entry.path), blobs.get_bytes(entry.blob), entry.mode)
    modes = {entry.path: entry.mode for entry in entries}
    for path, data in sorted(spliced.items()):
        _write(_safe_target(root, path), data, modes.get(path, 0o644))
    locks = _lock_files(run)
    for path, sha in sorted(locks.items()):
        _write(_safe_target(root, path), blobs.get_bytes(sha), 0o644)
    report.notes.append(f"tree: {len(entries)} files written verbatim")
    if spliced:
        report.notes.append(f"slot: {len(spliced)} files spliced from the variant's replacement")
    if locks:
        report.notes.append(f"lock: {len(locks)} platform lock files ({run.lock_source or 'unknown'} lock source)")
    _check_locks(root, entries, locks, report)
    stack = _stack(run)
    if stack is not None and (stack.content or {}).get("choice_mode") == "free":
        _check_free_choice(root, run, stack, report)
    return root


def build_identity(run: GenerationRun) -> dict[str, Any]:
    """``{"stack": pinned ref, "starter": tree_hash, "lock": resolved_lock sha, "protocol": ...}``.

    The sample build cache key input (``sample_builds._context_key``).
    A slot variant inherits stack, starter and lock from its parent; missing parts are ``None``. Free-choice
    stacks add ``allowed_bases`` (the frozen allowlist sha): its digests pin the build's base images.
    """
    parent = _parent(run)
    stack = run.stack_profile or (parent.stack_profile if parent is not None else None)
    starter = run.starter or (parent.starter if parent is not None else None)
    lock = run.resolved_lock_blob or (parent.resolved_lock_blob if parent is not None else "")
    out: dict[str, Any] = {
        "protocol": run.protocol,
        "stack": stack.pinned_ref if stack is not None else None,
        "starter": starter.tree_hash if starter is not None else None,
        "lock": lock or None,
    }
    if stack is not None and (stack.content or {}).get("choice_mode") == "free":
        frozen = _manifest_allowed_bases(run)
        if frozen is not None and frozen.get("sha"):
            out["allowed_bases"] = str(frozen["sha"])
        else:
            from backend.generation.build import dockerfiles

            out["allowed_bases"] = dockerfiles.allowed_bases_record()["sha"]
    return out
