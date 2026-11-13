"""``SlotSet`` creation: freeze a parent (spec section 7.2).

Freezing copies nothing: blobs are immutable and PROTECT foreign keys pin the parent. It checks that the
parent is a usable ``app_agentic`` run with outcome ``tested`` (``build_fail`` / ``start_fail`` only with
``allow_unbuilt_parent``: no runtime slot tests and never confirmatory), that its tree hash is set, that every
non-deleted ``RunFile`` blob exists and that it has a manifest; then it builds the symbol index once and
links the latest evaluation as ``parent_eval``. A later re-evaluation of the parent adds a row and never
changes the slot set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction

from backend.generation import blobs
from backend.generation.contracts.slot import ParentSelection
from backend.generation.models import RunFile
from backend.generation.models import RunManifest
from backend.generation.models import SlotSet
from backend.generation.models.choices import FileOrigin
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunKind
from backend.generation.slot import index as slot_index
from backend.generation.slot.errors import SlotInvalidError
from backend.generation.slot.parents import selection_record

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.users.models import User

__all__ = ["UNBUILT_OUTCOMES", "check_parent", "freeze"]

UNBUILT_OUTCOMES = frozenset({Outcome.BUILD_FAIL, Outcome.START_FAIL})


def check_parent(parent_run: GenerationRun, *, allow_unbuilt_parent: bool = False) -> list[str]:
    """Why ``parent_run`` cannot be frozen (empty when it can)."""
    problems: list[str] = []
    if parent_run.kind != RunKind.APP_AGENTIC:
        problems.append(f"a parent must be an app_agentic run, not {parent_run.kind}")
    if parent_run.usable is not True:
        problems.append("a parent must be usable")
    if parent_run.outcome != Outcome.TESTED and not (allow_unbuilt_parent and parent_run.outcome in UNBUILT_OUTCOMES):
        problems.append(
            f"a parent must have outcome tested (build_fail/start_fail need allow_unbuilt_parent), "
            f"not {parent_run.outcome or 'none'}",
        )
    if not parent_run.tree_hash:
        problems.append("the parent has no tree hash (its files were never collected)")
    if parent_run.task_version_id is None:
        problems.append("the parent has no task version")
    if not RunManifest.objects.filter(run=parent_run).exists():
        problems.append("the parent has no manifest")
    missing = [
        path
        for path, blob in RunFile.objects.filter(run=parent_run)
        .exclude(origin=FileOrigin.DELETED)
        .values_list(
            "path",
            "blob",
        )
        if not blobs.exists(blob)
    ]
    if missing:
        problems.append(f"blobs missing for {len(missing)} files (e.g. {sorted(missing)[0]})")
    return problems


def _parent_facts(parent_run: GenerationRun) -> tuple[str, str, str]:
    """``(manifest_hash, parent_model, parent_harness)`` from the parent's manifest."""
    manifest = RunManifest.objects.get(run=parent_run)
    payload: dict[str, Any] = manifest.manifest or {}
    model = (payload.get("model") or {}).get("slug") or (parent_run.model.model_id if parent_run.model else "")
    harness = (payload.get("harness") or {}).get("name") or ""
    return manifest.manifest_hash, model, harness


def freeze(
    parent_run: GenerationRun,
    *,
    name: str,
    user: User | None,
    selection: dict[str, Any] | ParentSelection | None = None,
    allow_unbuilt_parent: bool = False,
    pool_size: int | None = None,
    seed: int | None = None,
) -> SlotSet:
    """Freeze a usable ``app_agentic`` run as a slot set (index built, tree hash pinned).

    ``selection`` is the ``ParentSelection`` the parent was drawn with (``None``: picked by ``user``).
    """
    problems = check_parent(parent_run, allow_unbuilt_parent=allow_unbuilt_parent)
    if problems:
        raise SlotInvalidError("; ".join(problems), data={"problems": problems})
    parsed_selection = ParentSelection.model_validate(selection) if isinstance(selection, dict) else selection
    from backend.generation.runs.view import run_view

    manifest_hash, parent_model, parent_harness = _parent_facts(parent_run)
    with transaction.atomic():
        index_row = slot_index.ensure_index(parent_run)
        latest = run_view(parent_run).latest_evaluation()
        slot_set = SlotSet(
            name=name or f"{parent_run.task_version} parent {str(parent_run.pk)[:8]}",
            parent_run=parent_run,
            task_version=parent_run.task_version,
            parent_model=parent_model,
            parent_harness=parent_harness,
            selection=selection_record(parsed_selection, user=user, pool_size=pool_size, seed=seed),
            frozen_tree_hash=parent_run.tree_hash,
            parent_manifest_hash=manifest_hash,
            parent_eval=latest,
            index=index_row,
            notes="allow_unbuilt_parent: no runtime slot tests" if parent_run.outcome in UNBUILT_OUTCOMES else "",
            created_by=user,
        )
        slot_set.full_clean()
        slot_set.save()
    return slot_set
