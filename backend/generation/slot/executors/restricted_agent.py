"""Restricted slot agent: allowlist-limited agentic regeneration (spec section 7.6).

The sandbox, gateway and harness adapter are the agentic pipeline's (``agentic/``, WP-08). This module gives
that pipeline the slot-specific steps of a ``slot_agent`` run:

* ``prepare(run) -> SlotAgentContext``: the ``HarnessContext.slot`` extras (the part, its allowlist, a neutral
  description). An adapter with ``supports_path_restriction`` may use it in ``restrict_paths`` as a soft guide;
  the allowlist diff below applies either way;
* ``hole_overlay(run) -> {path: bytes | None}``: what ``agentic.workspace.slot_agent_seed`` lays over the
  parent tree (the slot file with the hole applied; ``None`` deletes a removed file). The seeder drops
  parent-authored tests unless ``include_tests`` and never seeds artifact rows; ``seed_files`` /
  ``seed_workspace`` build the same tree here (the leak scan's ``workspace`` scope covers exactly it);
* ``collect_variant(run, final=..., seed=..., workspace_tar=...) -> agentic.collector.Collected``: the slot
  collector. It diffs the final workspace against the seed: any change outside the allowlist is an out-of-slot
  edit; for span kinds the slot file must keep the seeded text before and after the hole byte-for-byte (a
  difference is ``<path>#outside_span``). Dependency manifests and lockfiles count like any other file
  (out-of-slot unless allowlisted) and ``new_dependencies`` is recorded either way. ``edit_policy=revert``
  assembles the variant from the slot region and the allowlisted files only (the out-of-slot edits are
  recorded); ``edit_policy=fail`` makes the run ``output_invalid`` with reason ``edit_violation``. The
  replacement is the text between the unchanged prefix and suffix; when the agent also changed other
  allowlisted files it is ``{path: text}`` of the slot file (in full) and those files, which
  ``assemble.recorded_replacement`` recognises. It writes the ``SlotVariantResult`` and the slot rows;
  ``finalize(run, directory)`` does the same from a workspace directory.

Parse status for agents: ``ok`` (a replacement was extracted), ``empty`` (the hole was left as seeded or the
allowlisted files are empty) or ``schema_violation`` (the slot region cannot be located any more: the text
around the hole changed; outcome reason ``edit_violation``).
"""

from __future__ import annotations

import difflib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Any

from backend.generation import identity
from backend.generation.contracts.harness import SlotAgentContext
from backend.generation.contracts.slot import OutOfSlotEdit
from backend.generation.contracts.slot import SlotAgentTarget
from backend.generation.models import RunManifest
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import ParseStatus
from backend.generation.slot.part import Part
from backend.generation.slot.part import part_of
from backend.generation.slot.tree import ARTIFACT_DIRS
from backend.generation.slot.tree import is_dependency_file
from backend.generation.slot.variants import declared_in

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun

__all__ = [
    "AgentSettings",
    "VariantRecord",
    "WorkspaceDiff",
    "collect_variant",
    "diff_workspace",
    "finalize",
    "hole_overlay",
    "prepare",
    "record_from_workspace",
    "seed_files",
    "seed_workspace",
]


@dataclass(frozen=True)
class AgentSettings:
    hole_mode: str
    keep_docstring: bool
    include_tests: bool
    edit_policy: str


def settings_for(run: GenerationRun, part: Part) -> AgentSettings:
    """The condition-level slot policy of the run (from its manifest; the target's defaults without one)."""
    manifest: dict[str, Any] = RunManifest.objects.filter(run=run).values_list("manifest", flat=True).first() or {}
    slot = manifest.get("slot") or {}
    context_policy = slot.get("context_policy") or {}
    return AgentSettings(
        hole_mode=slot.get("hole_mode") or part.default_hole_mode,
        keep_docstring=bool(slot.get("keep_docstring", part.default_keep_docstring)),
        include_tests=bool(slot.get("include_tests", context_policy.get("include_tests", False))),
        edit_policy=slot.get("edit_policy") or part.edit_policy,
    )


def prepare(run: GenerationRun) -> SlotAgentContext:
    """The ``HarnessContext.slot`` extras of a ``slot_agent`` run."""
    if run.slot_target is None:
        msg = f"run {run.pk} has no slot target"
        raise ValueError(msg)
    part = part_of(run.slot_target)
    settings = settings_for(run, part)
    label = part.label or part.path
    return SlotAgentContext(
        target=SlotAgentTarget(
            kind=part.address.kind,
            label=label,
            path=part.path,
            language=part.language,
            span=part.address.span,
            hole_mode=settings.hole_mode,  # type: ignore[arg-type]
            allowlist=part.allowlist,
            include_tests=settings.include_tests,
        ),
        allowlist=tuple(part.allowlist),
        description=f"{label} in {part.path}",
    )


def seed_files(part: Part, settings: AgentSettings) -> dict[str, tuple[bytes, int]]:
    """``{path: (bytes, mode)}`` of the agent workspace (the hole applied, tests out unless included)."""
    tree = part.parent.tree
    hole = part.hole(settings.hole_mode, settings.keep_docstring)
    files: dict[str, tuple[bytes, int]] = {}
    for entry in tree:
        if not entry.blob or entry.origin in {"artifact", "deleted"}:
            continue
        if not settings.include_tests and tree.is_test(entry.path):
            continue
        if entry.path == part.path:
            if hole.removed:
                continue
            files[entry.path] = (hole.holed or b"", entry.mode)
        else:
            files[entry.path] = (tree.read(entry.path), entry.mode)
    return files


def seed_workspace(run: GenerationRun, dest: Path) -> str:
    """Write the agent workspace into ``dest``; returns its tree hash (``RunResult.seed_tree_hash``)."""
    if run.slot_target is None:
        msg = f"run {run.pk} has no slot target"
        raise ValueError(msg)
    part = part_of(run.slot_target)
    files = seed_files(part, settings_for(run, part))
    for path, (data, mode) in files.items():
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        os.chmod(target, 0o755 if mode & 0o111 else 0o644)  # noqa: PTH101 - explicit mode bits
    return identity.tree_hash((path, mode, identity.bytes_sha(data)) for path, (data, mode) in files.items())


def _read_workspace(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for current, directories, names in os.walk(root):
        directories[:] = sorted(d for d in directories if d not in ARTIFACT_DIRS)
        for name in sorted(names):
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                continue
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def _changed_lines(before: bytes, after: bytes) -> int:
    a = before.decode("utf-8", errors="replace").splitlines()
    b = after.decode("utf-8", errors="replace").splitlines()
    return sum(
        1
        for line in difflib.unified_diff(a, b, lineterm="", n=0)
        if line[:1] in "+-" and line[:3] not in {"+++", "---"}
    )


@dataclass
class WorkspaceDiff:
    out_of_slot: list[OutOfSlotEdit]
    replacement: str | dict[str, str] | None
    parse_status: str
    dependency_edits: list[str]


def diff_workspace(
    part: Part,
    settings: AgentSettings,
    workspace: Mapping[str, bytes],
    seed: Mapping[str, bytes] | None = None,
) -> WorkspaceDiff:
    """Out-of-slot edits and the extracted replacement of a final workspace (``seed``: the seeded tree)."""
    if seed is None:
        seed = {path: data for path, (data, _) in seed_files(part, settings).items()}
    allowed = set(part.allowlist)
    edits: list[OutOfSlotEdit] = []
    dependency_edits: list[str] = []
    for path in sorted(set(seed) | set(workspace)):
        before, after = seed.get(path), workspace.get(path)
        if before == after:
            continue
        if is_dependency_file(path):
            dependency_edits.append(path)
        if path in allowed:
            continue
        kind = "added" if before is None else ("deleted" if after is None else "modified")
        edits.append(OutOfSlotEdit(path=path, kind=kind, lines=_changed_lines(before or b"", after or b"")))
    hole = part.hole(settings.hole_mode, settings.keep_docstring)
    whole = part.address.span is None or (part.whole_file_component and settings.hole_mode == "remove")
    if whole:
        texts = {path: workspace.get(path, b"").decode("utf-8", errors="replace") for path in part.allowlist}
        if all(not text.strip() for text in texts.values()) or all(
            workspace.get(path) == seed.get(path) for path in part.allowlist
        ):
            return WorkspaceDiff(edits, None, ParseStatus.EMPTY, dependency_edits)
        replacement: str | dict[str, str] = texts
        return WorkspaceDiff(edits, replacement, ParseStatus.OK, dependency_edits)
    final = workspace.get(part.path)
    prefix, suffix = hole.prefix, hole.suffix
    if (
        final is None
        or not final.startswith(prefix)
        or not final.endswith(suffix)
        or len(final) < len(prefix) + len(suffix)
    ):
        changed = _changed_lines(seed.get(part.path, b""), final or b"")
        edits.append(
            OutOfSlotEdit(path=f"{part.path}#outside_span", kind="modified" if final else "deleted", lines=changed),
        )
        return WorkspaceDiff(edits, None, ParseStatus.SCHEMA_VIOLATION, dependency_edits)
    middle = final[len(prefix) : len(final) - len(suffix)]
    if not middle.strip() or middle == hole.hole:
        return WorkspaceDiff(edits, None, ParseStatus.EMPTY, dependency_edits)
    # Other allowlisted files (e.g. a dependency manifest the researcher allowed) are in-slot edits: they are
    # never flagged and their final bytes are part of the variant. The replacement is then the files form
    # (the slot file in full, its text outside the span checked unchanged above, plus the edited files).
    extras = sorted(path for path in allowed - {part.path} if seed.get(path) != workspace.get(path))
    if extras:
        files = {path: workspace.get(path, b"").decode("utf-8", errors="replace") for path in [part.path, *extras]}
        return WorkspaceDiff(edits, files, ParseStatus.OK, dependency_edits)
    return WorkspaceDiff(edits, middle.decode("utf-8", errors="replace"), ParseStatus.OK, dependency_edits)


def _new_dependencies(part: Part, workspace: Mapping[str, bytes], edited: list[str]) -> list[str]:
    """Packages the agent added to the dependency manifests (declared after, not before)."""
    if not edited:
        return []
    after_texts = dict(part.parent.texts)
    for path in edited:
        if path in workspace:
            after_texts[path] = workspace[path].decode("utf-8", errors="replace")
        else:
            after_texts.pop(path, None)
    return sorted(declared_in(after_texts) - declared_in(part.parent.texts))


@dataclass(frozen=True)
class VariantRecord:
    """What the slot collector decided: the stage outcome, the slot rows and the invalid-output reason."""

    outcome: str
    rows: list[Any]
    invalid_reason: str | None


def _without_artifacts(files: Mapping[str, bytes]) -> dict[str, bytes]:
    return {path: data for path, data in files.items() if not set(PurePosixPath(path).parts) & ARTIFACT_DIRS}


def record_from_workspace(
    run: GenerationRun,
    final: Mapping[str, bytes],
    seed: Mapping[str, bytes] | None = None,
) -> VariantRecord:
    """Diff the final workspace, write the ``SlotVariantResult`` and (when valid) the slot rows."""
    from backend.execution import transitions
    from backend.generation import blobs
    from backend.generation.slot.assemble import assemble
    from backend.generation.slot.variants import record_variant

    if run.slot_target is None:
        msg = f"run {run.pk} has no slot target"
        raise ValueError(msg)
    part = part_of(run.slot_target)
    settings = settings_for(run, part)
    files = _without_artifacts(final)
    diff = diff_workspace(part, settings, files, _without_artifacts(seed) if seed is not None else None)
    new_dependencies = _new_dependencies(part, files, diff.dependency_edits)
    violation = bool(diff.out_of_slot)
    common: dict[str, Any] = {
        "out_of_slot_edits": diff.out_of_slot,
        "edit_violation": violation,
        "extra_dependencies": new_dependencies,
    }
    if diff.replacement is None:
        record_variant(run, part, parse_status=diff.parse_status, replacement=None, **common)
        reason = "edit_violation" if diff.parse_status == ParseStatus.SCHEMA_VIOLATION else diff.parse_status
        run.outcome, run.outcome_reason = Outcome.OUTPUT_INVALID, reason
        return VariantRecord(transitions.OUTCOME_DONE, [], reason)
    if violation and settings.edit_policy == "fail":
        record_variant(run, part, parse_status=diff.parse_status, replacement=diff.replacement, **common)
        run.outcome, run.outcome_reason = Outcome.OUTPUT_INVALID, "edit_violation"
        return VariantRecord(transitions.OUTCOME_DONE, [], "edit_violation")
    rows = assemble(run, diff.replacement)
    texts = {row.path: blobs.get_text(row.blob) for row in rows}
    record_variant(
        run,
        part,
        parse_status=ParseStatus.OK,
        replacement=diff.replacement,
        assembled_texts=texts,
        **common,
    )
    run.outcome, run.outcome_reason = "", ""
    return VariantRecord(transitions.OUTCOME_EVALUATE, rows, None)


def hole_overlay(run: GenerationRun) -> dict[str, bytes | None]:
    """``{slot path: holed bytes | None}`` for the agentic seeder (``None`` deletes a removed file)."""
    if run.slot_target is None:
        msg = f"run {run.pk} has no slot target"
        raise ValueError(msg)
    part = part_of(run.slot_target)
    settings = settings_for(run, part)
    hole = part.hole(settings.hole_mode, settings.keep_docstring)
    return {part.path: None if hole.removed else hole.holed}


def collect_variant(
    run: GenerationRun,
    *,
    final: Mapping[str, Any],
    seed: Mapping[str, Any],
    workspace_tar: Path | None = None,
) -> Any:
    """The slot collector for ``agentic.runner`` (returns ``agentic.collector.Collected``).

    ``final`` / ``seed`` map paths to ``agentic.workspace.SeedFile`` (or bytes). Only the slot rows are
    written; an invalid variant has no rows, ``model_code=False`` and its ``invalid_reason``.
    """
    from backend.generation import blobs
    from backend.generation.agentic.collector import Collected
    from backend.generation.agentic.collector import FileRow
    from backend.generation.contracts.outcome import CollectionSummary

    def raw(files: Mapping[str, Any]) -> dict[str, bytes]:
        return {path: getattr(entry, "data", entry) for path, entry in files.items()}

    record = record_from_workspace(run, raw(final), raw(seed))
    rows = [
        FileRow(
            path=row.path,
            origin=row.origin,
            size=row.size,
            mode=row.mode,
            language=row.language,
            loc=row.loc,
            is_test=row.is_test,
            excluded=row.excluded,
            data=blobs.get_bytes(row.blob),
        )
        for row in record.rows
    ]
    workspace_blob = ""
    if workspace_tar is not None and Path(workspace_tar).exists():
        workspace_blob = blobs.put_file(workspace_tar, media_type="application/x-tar")
    summary = CollectionSummary(
        files=len(rows),
        by_origin={"slot": len(rows)} if rows else {},
        loc_model=sum(row.loc for row in rows),
    )
    return Collected(
        rows=rows,
        tree_hash=run.tree_hash if rows else "",
        workspace_blob=workspace_blob,
        summary=summary,
        invalid_reason=record.invalid_reason,
        model_code=bool(rows),
    )


def finalize(run: GenerationRun, workspace: Path) -> str:
    """Record the variant from a final workspace directory; returns ``evaluate`` or ``done``."""
    return record_from_workspace(run, _read_workspace(workspace)).outcome
