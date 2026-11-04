"""JSON payloads of the studies API (spec section 9.4): studies, plans, cells, cell details and diffs."""

from __future__ import annotations

import difflib
from collections import Counter
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Count
from django.db.models import F
from django.db.models import Q

from backend.generation.contracts.resources import ResourcePolicyContent
from backend.generation.contracts.resources import knob_class
from backend.generation.contracts.resources import knob_values
from backend.generation.models import GenerationRun
from backend.generation.models import StudyCell
from backend.generation.models.choices import CellState

if TYPE_CHECKING:
    from backend.generation.contracts.plan import ResolvedCell
    from backend.generation.models import Study
    from backend.generation.models import StudyPlan
    from backend.generation.study.plan import PlanBundle

__all__ = [
    "cell_detail",
    "cell_diff",
    "cell_out",
    "cells_payload",
    "plan_out",
    "plan_summary",
    "study_list_item",
    "study_out",
]


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _money(value: Any) -> float | None:
    return float(value) if value is not None else None


def plan_summary(row: StudyPlan | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": str(row.pk),
        "plan_hash": row.plan_hash,
        "design_hash": row.design_hash,
        "status": row.status,
        "n_conditions": row.n_conditions,
        "n_units": row.n_units,
        "n_cells": row.n_cells,
        "n_blocks": row.n_blocks,
        "estimate": row.estimate,
        "issues": row.issues,
        "resolver_version": row.resolver_version,
        "created_at": _iso(row.created_at),
    }


def study_out(study: Study) -> dict[str, Any]:
    return {
        "id": str(study.pk),
        "name": study.name,
        "slug": study.slug,
        "description": study.description,
        "hypothesis": study.hypothesis,
        "kind": study.kind,
        "mode": study.mode,
        "status": study.status,
        "pause_reason": study.pause_reason,
        "stop_reason": study.stop_reason,
        "design": study.design,
        "design_hash": study.design_hash,
        "repeats": study.repeats,
        "base_seed": study.base_seed,
        "order_seed": study.order_seed,
        "scheduling": study.scheduling,
        "priority": study.priority,
        "resource_policy": study.resource_policy.pinned_ref if study.resource_policy_id else None,
        "analysis_plan": study.analysis_plan or None,
        "analysis_plan_hash": study.analysis_plan_hash or None,
        "prereg_hash": study.prereg_hash or None,
        "locked_at": _iso(study.locked_at),
        "locked": study.locked_at is not None,
        "active_plan": plan_summary(study.active_plan),
        "factors": [
            {"key": f.key, "role": f.role, "levels": f.levels, "position": f.position}
            for f in study.factors.order_by("position")
        ],
        "max_parallel": study.max_parallel,
        "execution_options": study.execution_options or {},
        "budget_basis": study.budget_basis,
        "cap_basis": study.cap_basis,
        "on_budget_exhausted": study.on_budget_exhausted,
        "cap_usd": _money(study.cap_usd),
        "cap_tokens": study.cap_tokens,
        "spent_usd": _money(study.spent_usd),
        "spent_list_usd": _money(study.spent_list_usd),
        "reserved_usd": _money(study.reserved_usd),
        "spent_tokens": study.spent_tokens,
        "cloned_from": str(study.cloned_from_id) if study.cloned_from_id else None,
        "launched_at": _iso(study.launched_at),
        "completed_at": _iso(study.completed_at),
        "cancelled_at": _iso(study.cancelled_at),
        "created_at": _iso(study.created_at),
        "updated_at": _iso(study.updated_at),
    }


def study_list_item(study: Study, counts: dict[str, int] | None = None) -> dict[str, Any]:
    counts = counts or {}
    total = counts.get("total", 0)
    excluded = counts.get("excluded", 0) + counts.get("infra_pending", 0)
    return {
        "id": str(study.pk),
        "name": study.name,
        "kind": study.kind,
        "mode": study.mode,
        "status": study.status,
        "cells_done": counts.get("done", 0),
        "cells_total": total,
        "spent_usd": _money(study.spent_usd),
        "cap_usd": _money(study.cap_usd),
        "exclusion_rate": round(excluded / total, 4) if total else None,
        "locked": study.locked_at is not None,
        "created_at": _iso(study.created_at),
    }


def cell_counts(studies: list[Study]) -> dict[Any, dict[str, int]]:
    """Main-stage cell counts by state per study (active plan)."""
    rows = (
        StudyCell.objects.filter(study__in=studies, stage="main", beyond_plan=False)
        .filter(Q(study__active_plan__isnull=True) | Q(plan_id=F("study__active_plan_id")))
        .values("study_id", "state")
        .annotate(n=Count("pk"))
    )
    out: dict[Any, dict[str, int]] = {}
    for row in rows:
        bucket = out.setdefault(row["study_id"], {"total": 0})
        bucket[row["state"]] = bucket.get(row["state"], 0) + row["n"]
        bucket["total"] += row["n"]
    return out


def cell_out(cell: ResolvedCell, *, full: bool = False) -> dict[str, Any]:
    identity = cell.manifest.identity.model_dump() if cell.manifest.identity is not None else None
    out = {
        "cell_key": cell.cell_key,
        "condition_index": cell.condition_index,
        "unit_index": cell.unit_index,
        "unit_key": cell.unit_key,
        "repeat_index": cell.repeat_index,
        "stage": cell.stage,
        "seed": cell.seed,
        "sequence": cell.sequence,
        "block_index": cell.block_index,
        "levels": cell.levels,
        "identity": identity,
        "estimate": cell.estimate.model_dump(mode="json") if cell.estimate is not None else None,
        "excluded": cell.excluded,
    }
    if full:
        out["manifest"] = cell.manifest.model_dump(mode="json")
    return out


def plan_out(bundle: PlanBundle, *, sample_cells: int = 50) -> dict[str, Any]:
    plan = bundle.plan
    return {
        "plan_hash": plan.plan_hash,
        "design_hash": plan.design_hash,
        "persisted": bundle.persisted,
        "resolver_version": plan.resolver_version,
        "n_conditions": len(plan.conditions),
        "n_units": len(plan.units),
        "n_cells": len(plan.cells),
        "n_cells_by_stage": dict(Counter(cell.stage for cell in plan.cells)),
        "n_blocks": plan.n_blocks,
        "unbalanced": bundle.unbalanced,
        "estimate": plan.estimate.model_dump(mode="json") if plan.estimate is not None else None,
        "issues": [issue.model_dump(mode="json") for issue in plan.issues],
        "conditions": [condition.model_dump(mode="json") for condition in plan.conditions],
        "units": [unit.model_dump(mode="json") for unit in plan.units],
        "cells_sample": [cell_out(cell) for cell in plan.cells[: max(0, sample_cells)]],
    }


def _text(bundle: PlanBundle, sha: str) -> str:
    if sha in bundle.texts:
        return bundle.texts[sha]
    from backend.generation import blobs

    return blobs.get_text(sha)


def _effective_roles(cell: ResolvedCell, prompt_set_content: dict[str, Any] | None) -> list[dict[str, Any]]:
    content = prompt_set_content or {}
    mode = content.get("system_prompt_mode", "harness_native")
    harness = cell.manifest.harness
    out = []
    for role, ref in sorted((content.get("roles") or {}).items()):
        ignored = role == "system" and mode == "harness_native" and harness is not None
        out.append(
            {
                "role": role,
                "template_ref": ref,
                "status": "ignored(harness_native)" if ignored else "used",
                "native_prompt_ref": f"{harness.name}-{harness.version}" if ignored and harness else None,
            },
        )
    return out


def _effective_params(effective: dict[str, Any]) -> list[dict[str, Any]]:
    content = effective.get("content")
    if not content:
        return []
    policy = ResourcePolicyContent.model_validate(content)
    basis = effective.get("budget_basis", "usd")
    clamped = {clamp.get("key"): clamp for clamp in effective.get("clamps") or [] if isinstance(clamp, dict)}
    rows = []
    for key, value in sorted(knob_values(policy).items()):
        rows.append(
            {
                "key": key,
                "value": value,
                "source": "clamp" if key in clamped else "policy",
                "cls": knob_class(key, basis),
            },
        )
    return rows


def _starter_tree(cell: ResolvedCell) -> list[str]:
    starter = cell.manifest.starter
    if starter is None:
        return []
    from backend.generation.models import Starter

    slug, _, rest = starter.ref.partition("@")
    row = Starter.objects.filter(slug=slug, version=int(rest.split("#", 1)[0])).first()
    return sorted((row.content or {}).get("files") or {}) if row is not None else []


def cell_detail(bundle: PlanBundle, cell: ResolvedCell) -> dict[str, Any]:
    from backend.generation.models import PromptSet

    manifest = cell.manifest
    prompt_set_ref = cell.levels.get("prompt_set", "")
    slug, _, rest = prompt_set_ref.partition("@")
    prompt_set = PromptSet.objects.filter(slug=slug, version=int(rest.split("#", 1)[0])).first() if rest else None
    harness = manifest.harness
    sampling = manifest.sampling
    return {
        "cell_key": cell.cell_key,
        "levels": cell.levels,
        "pinned_refs": {key: record.ref for key, record in manifest.factors.items() if record.ref is not None},
        "stimuli": [
            {
                "role": record.role,
                "template_ref": record.template_ref,
                "text": _text(bundle, record.sha),
                "sha": record.sha,
                "chars": record.chars,
                "est_tokens": record.est_tokens,
                "sources": [source.model_dump(mode="json") for source in record.sources],
            }
            for record in cell.stimuli
        ],
        "effective_roles": _effective_roles(cell, prompt_set.content if prompt_set else None),
        "effective_policy": cell.effective_policy,
        "effective_params": _effective_params(cell.effective_policy or {}),
        "model_effects": {
            "dropped_params": list(sampling.stripped_params) if sampling else [],
            "forced_params": dict(sampling.forced_params) if sampling else {},
            "seed_forwarded": sampling.seed_forwarded if sampling else None,
            "structured_output": None,
        },
        "harness": (
            {
                "name": harness.name,
                "version": harness.version,
                "options": harness.options,
                "unsupported_knobs": harness.unsupported_knobs,
                "protocol_mode": manifest.gateway.protocol_mode if manifest.gateway else None,
                "vendor_affinity": harness.vendor_affinity,
            }
            if harness is not None
            else None
        ),
        "starter_tree": _starter_tree(cell),
        "slot_context": cell.slot_context.model_dump(mode="json") if cell.slot_context is not None else None,
        "identity": manifest.identity.model_dump() if manifest.identity is not None else None,
        "estimate": cell.estimate.model_dump(mode="json") if cell.estimate is not None else None,
        "manifest": manifest.model_dump(mode="json"),
    }


def cell_diff(bundle: PlanBundle, a: ResolvedCell, b: ResolvedCell) -> dict[str, Any]:
    keys = sorted(set(a.levels) | set(b.levels))
    levels_diff = {
        key: [a.levels.get(key), b.levels.get(key)] for key in keys if a.levels.get(key) != b.levels.get(key)
    }
    texts_a = {record.role: _text(bundle, record.sha) for record in a.stimuli}
    texts_b = {record.role: _text(bundle, record.sha) for record in b.stimuli}
    stimuli_diff = []
    for role in sorted(set(texts_a) | set(texts_b)):
        left, right = texts_a.get(role, ""), texts_b.get(role, "")
        if left == right:
            continue
        unified = "".join(
            difflib.unified_diff(
                left.splitlines(keepends=True),
                right.splitlines(keepends=True),
                fromfile=f"{a.cell_key}/{role}",
                tofile=f"{b.cell_key}/{role}",
            ),
        )
        stimuli_diff.append({"role": role, "unified": unified})
    params_a = {row["key"]: row["value"] for row in _effective_params(a.effective_policy or {})}
    params_b = {row["key"]: row["value"] for row in _effective_params(b.effective_policy or {})}
    params_diff = [
        {"key": key, "a": params_a.get(key), "b": params_b.get(key)}
        for key in sorted(set(params_a) | set(params_b))
        if params_a.get(key) != params_b.get(key)
    ]
    return {"levels_diff": levels_diff, "stimuli_diff": stimuli_diff, "params_diff": params_diff}


def cells_payload(study: Study, *, state: str | None, condition: int | None, unit: int | None, stage: str | None):
    """``CellsPayload``: cells of the active plan with their current run, attempts and spend.

    Replicate cells added after launch are listed too (``beyond_plan: true``); their ``attempts`` count their
    own (beyond-plan) runs, a planned cell's ``beyond_plan_runs`` its top-ups beyond the plan.
    """
    from backend.generation.runs.queries import cell_current_ids
    from backend.generation.runs.queries import cells_for_stage

    qs = cells_for_stage(study, stage, beyond_plan=None)
    if state:
        qs = qs.filter(state=state)
    if condition is not None:
        qs = qs.filter(condition_id=condition)
    if unit is not None:
        qs = qs.filter(unit_id=unit)
    qs = cell_current_ids(qs.select_related("unit", "condition")).order_by("sequence")
    cells = list(qs)
    current = {
        run.pk: run
        for run in GenerationRun.objects.filter(pk__in=[c.current_id for c in cells if c.current_id]).only(
            "pk",
            "status",
            "phase",
            "outcome",
            "outcome_reason",
            "usable",
            "attempt",
            "cell_attempt",
        )
    }
    counts = {
        (cell_id, flag): n
        for cell_id, flag, n in GenerationRun.objects.filter(cell__in=cells)
        .values("cell_id", "beyond_plan")
        .annotate(n=Count("pk"))
        .values_list("cell_id", "beyond_plan", "n")
    }
    attempts = {cell.pk: counts.get((cell.pk, cell.beyond_plan), 0) for cell in cells}
    beyond = {cell.pk: 0 if cell.beyond_plan else counts.get((cell.pk, True), 0) for cell in cells}
    rows = []
    for cell in cells:
        run = current.get(cell.current_id)
        rows.append(
            {
                "id": cell.pk,
                "condition_id": cell.condition_id,
                "condition_index": cell.condition.index,
                "unit_id": cell.unit_id,
                "unit_key": cell.unit.unit_key,
                "repeat_index": cell.repeat_index,
                "stage": cell.stage,
                "block_index": cell.block_index,
                "sequence": cell.sequence,
                "seed": cell.seed,
                "state": cell.state,
                "exclusion_reason": cell.exclusion_reason,
                "current_run": (
                    {
                        "id": str(run.pk),
                        "status": run.status,
                        "phase": run.phase,
                        "outcome": run.outcome,
                        "outcome_reason": run.outcome_reason,
                        "usable": run.usable,
                        "attempt": run.attempt,
                        "cell_attempt": run.cell_attempt,
                    }
                    if run is not None
                    else None
                ),
                "outcome": run.outcome if run is not None else "",
                "attempts": attempts.get(cell.pk, 0),
                "beyond_plan_runs": beyond.get(cell.pk, 0),
                "estimate_usd": float(cell.estimate_usd),
                "estimate_list_usd": float(cell.estimate_list_usd),
                "cap_usd": float(cell.cap_usd),
                "cell_cap_usd": float(cell.cell_cap_usd),
                "spent_usd": float(cell.spent_usd),
                "spent_list_usd": float(cell.spent_list_usd),
                "adaptive_n": cell.adaptive_n,
                "beyond_plan": cell.beyond_plan,
            },
        )
    return {
        "cells": rows,
        "counts": dict(Counter(row["state"] for row in rows)),
        "states": list(CellState.values),
    }
