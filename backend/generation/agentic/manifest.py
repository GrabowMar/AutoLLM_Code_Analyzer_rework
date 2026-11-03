"""Manifest freeze: ``build_manifest`` copies the identity keys from the resolved cell (spec sections 3.8, 6.2).

``launch.py`` (WP-10) creates the ``GenerationRun``, then calls :func:`build_manifest` inside the same
transaction, then ``transaction.on_commit(lambda: execution.api.enqueue_runs(ids))``. The manifest:

* is the ``ResolvedCell.manifest`` body plus the ``study`` block and ``created_at``;
* **copies** the identity keys the resolver computed with ``identity.compute_keys``; nothing here computes a
  key (a test asserts that the copied keys equal ``compute_keys`` on the same inputs);
* is written once (``RunManifest`` refuses updates) and reused unchanged by every retry, which increments
  ``GenerationRun.attempt`` only.

The run's query columns mirror the manifest: the identity keys, the seed, ``pinned_served_name`` /
``pinned_digest`` (``manifest.model.endpoint``; the scheduler and capacity code never read JSON paths), and the
treatment foreign keys, which :func:`run_column_mismatches` checks against the manifest's pinned refs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from backend.generation.contracts._base import PinnedRefStr
from backend.generation.contracts.manifest import ManifestBody
from backend.generation.contracts.manifest import ManifestStudy
from backend.generation.contracts.manifest import RunManifest as RunManifestContent
from backend.generation.contracts.manifest import StimulusRecord

if TYPE_CHECKING:
    from backend.generation.contracts.plan import ResolvedCell
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunManifest

__all__ = [
    "FACTOR_COLUMNS",
    "ManifestMismatchError",
    "build_manifest",
    "manifest_columns",
    "manifest_payload",
    "run_column_mismatches",
    "unpinned_refs",
]

# Run foreign key -> the manifest factor that pins it (section 9.4 factor keys).
FACTOR_COLUMNS: Final[Mapping[str, str]] = {
    "stack_profile": "stack_profile",
    "style": "style",
    "guidance": "guidance",
    "prompt_set": "prompt_set",
    "harness_preset": "harness_preset",
    "sampling_preset": "sampling_preset",
    "resource_policy": "resource_policy",
}
_IDENTITY_COLUMNS: Final = ("task_hash", "treatment_hash", "prompt_hash", "config_fingerprint", "run_fingerprint")


class ManifestMismatchError(ValidationError):
    """A run column disagrees with the manifest it is frozen with (appendix A #1, #13)."""


_PINNED_REF: Final = TypeAdapter(PinnedRefStr)


def _library_refs(manifest: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """``(path, ref)`` of every library-entity ref in a manifest payload (follow-up #4).

    Stimulus ``template_ref`` (may be ``draft:<role>``), slot set/target ids and base-image refs are not
    library versions and are not listed.
    """
    refs: list[tuple[str, Any]] = []
    for key, record in sorted((manifest.get("factors") or {}).items()):
        if isinstance(record, Mapping) and record.get("ref") is not None:
            refs.append((f"factors.{key}.ref", record["ref"]))
    for section, field in (
        ("task", "ref"),
        ("starter", "ref"),
        ("sampling", "ref"),
        ("resources", "ref"),
        ("harness", "preset_ref"),
    ):
        block = manifest.get(section)
        if isinstance(block, Mapping) and block.get(field) is not None:
            refs.append((f"{section}.{field}", block[field]))
    suite = (manifest.get("planned_evaluation") or {}).get("eval_suite") or {}
    if suite.get("ref") is not None:
        refs.append(("planned_evaluation.eval_suite.ref", suite["ref"]))
    return refs


def unpinned_refs(manifest: Mapping[str, Any]) -> list[str]:
    """Every library ref of ``manifest`` that is not a pinned ``slug@v#hash12`` (empty when all are pinned)."""
    problems = []
    for path, ref in _library_refs(manifest):
        try:
            _PINNED_REF.validate_python(ref)
        except PydanticValidationError:
            problems.append(f"{path}: {ref!r} is not a pinned ref (slug@version#hash12)")
    return problems


def _ref_matches(ref: str, row: Any) -> bool:
    """``ref`` (``slug@v`` or ``slug@v#hash12``) names ``row`` (a library version)."""
    if row is None:
        return False
    if "#" in ref:
        return ref == row.pinned_ref
    return ref == row.ref


def _study_block(run: GenerationRun, cell: ResolvedCell, study: ManifestStudy | Mapping[str, Any] | None) -> Any:
    if study is not None:
        return ManifestStudy.model_validate(study).model_dump(mode="json")
    if run.study_id is None:
        return None
    cell_row = run.cell
    condition = run.condition or (cell_row.condition if cell_row is not None else None)
    plan = cell_row.plan if cell_row is not None else (condition.plan if condition is not None else None)
    if plan is None and run.study is not None:
        plan = run.study.active_plan
    if plan is None or condition is None:
        msg = f"run {run.pk} belongs to study {run.study_id} but has no plan/condition to freeze into its manifest"
        raise ManifestMismatchError({"study": [msg]})
    block = ManifestStudy(
        id=str(run.study_id),
        plan_hash=plan.plan_hash,
        condition_hash=condition.condition_hash,
        cell=(cell.condition_index, cell.unit_index, cell.repeat_index),
        block_index=cell.block_index,
        stage=cell.stage,
        cell_attempt=run.cell_attempt or 1,
        beyond_plan=run.beyond_plan,
    )
    return block.model_dump(mode="json")


def manifest_payload(
    run: GenerationRun,
    resolved_cell: ResolvedCell,
    *,
    study: ManifestStudy | Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """The validated ``RunManifest`` payload of ``run`` (the resolved body + study block + ``created_at``)."""
    body = ManifestBody.model_validate(resolved_cell.manifest).model_dump(mode="json")
    payload = {
        **body,
        "study": _study_block(run, resolved_cell, study),
        "created_at": created_at or timezone.now().isoformat(),
    }
    return RunManifestContent.model_validate(payload).model_dump(mode="json")


def manifest_columns(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The run columns a manifest determines (kind, protocol, identity keys, seed, served name and digest)."""
    identity = manifest.get("identity") or {}
    endpoint = (manifest.get("model") or {}).get("endpoint") or {}
    task = manifest.get("task") or {}
    columns: dict[str, Any] = {
        "kind": manifest.get("kind"),
        "protocol": manifest.get("protocol"),
        "pinned_served_name": (endpoint.get("served_model_name") or "")[:200],
        "pinned_digest": (endpoint.get("digest") or "")[:80],
    }
    for name in _IDENTITY_COLUMNS:
        if identity.get(name):
            columns[name] = identity[name]
    seed = (manifest.get("sampling") or {}).get("seed")
    if seed is not None:
        columns["seed"] = seed
    if task.get("variant"):
        columns["prose_variant"] = task["variant"]
    auth = (manifest.get("factors") or {}).get("auth_mode") or {}
    if auth.get("value"):
        columns["auth_mode"] = auth["value"]
    return columns


def run_column_mismatches(run: GenerationRun, manifest: Mapping[str, Any]) -> list[str]:
    """Every run column that disagrees with ``manifest`` (empty when the run mirrors it).

    Checks the columns of :func:`manifest_columns` where the run holds a value, the treatment foreign keys
    against the factors' pinned refs, the task version against ``manifest.task.ref`` and the model slug.
    """
    problems: list[str] = []
    for name, value in manifest_columns(manifest).items():
        current = getattr(run, name)
        if current in ("", None) and name not in {"kind", "protocol"}:
            continue
        if current != value:
            problems.append(f"{name}: run has {current!r}, manifest {value!r}")
    factors = manifest.get("factors") or {}
    for column, key in FACTOR_COLUMNS.items():
        ref = (factors.get(key) or {}).get("ref")
        row = getattr(run, column)
        if ref and row is not None and not _ref_matches(ref, row):
            problems.append(f"{column}: run has {row.pinned_ref}, manifest {ref}")
        if ref and row is None:
            problems.append(f"{column}: run has none, manifest {ref}")
    starter = manifest.get("starter") or {}
    if starter.get("ref") and run.starter is not None and not _ref_matches(starter["ref"], run.starter):
        problems.append(f"starter: run has {run.starter.pinned_ref}, manifest {starter['ref']}")
    task = manifest.get("task") or {}
    if task.get("ref") and run.task_version is not None and not _ref_matches(task["ref"], run.task_version):
        problems.append(f"task_version: run has {run.task_version.pinned_ref}, manifest {task['ref']}")
    slug = (manifest.get("model") or {}).get("slug")
    if slug and run.model is not None and run.model.model_id != slug:
        problems.append(f"model: run has {run.model.model_id}, manifest {slug}")
    return problems


def build_manifest(
    run: GenerationRun,
    resolved_cell: ResolvedCell,
    *,
    study: ManifestStudy | Mapping[str, Any] | None = None,
    stimuli: list[StimulusRecord] | None = None,
) -> RunManifest:
    """Freeze the manifest of ``run`` before it is queued and mirror its columns onto the run.

    Must run inside the launch transaction, before ``enqueue_runs`` is scheduled with ``on_commit``.
    ``study`` overrides the study block derived from ``run.cell``. Raises :class:`ManifestMismatchError`
    when a column the caller already set disagrees with the manifest, or when a library ref of the
    manifest is not pinned (``slug@v#hash12``; :func:`unpinned_refs`).
    """
    from backend.generation.models import RunManifest

    payload = manifest_payload(run, resolved_cell, study=study)
    unpinned = unpinned_refs(payload)
    if unpinned:
        raise ManifestMismatchError({"refs": unpinned})
    records = stimuli if stimuli is not None else resolved_cell.stimuli
    with transaction.atomic():
        columns = manifest_columns(payload)
        if run.seed is None:
            columns.setdefault("seed", resolved_cell.seed)
        changed: list[str] = []
        for name, value in columns.items():
            if getattr(run, name) in ("", None):
                setattr(run, name, value)
                changed.append(name)
        if run.repeat_index is None:
            run.repeat_index = resolved_cell.repeat_index
            changed.append("repeat_index")
        if not run.stage:
            run.stage = resolved_cell.stage
            changed.append("stage")
        problems = run_column_mismatches(run, payload)
        if problems:
            raise ManifestMismatchError({"manifest": problems})
        if changed:
            run.save(update_fields=[*changed, "updated_at"])
        return RunManifest.objects.create(
            run=run,
            manifest=payload,
            stimuli=[StimulusRecord.model_validate(r).model_dump(mode="json") for r in records],
        )
