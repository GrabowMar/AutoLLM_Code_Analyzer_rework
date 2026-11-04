"""Study lifecycle: create, launch, pilot expansion, top-up, replicates, clone, ad-hoc, pause/resume/cancel.

Spec sections 4.6, 6.2, 6.14 and 9.4. Every operation runs in one transaction with the study row locked
(``select_for_update``); every dispatch side effect (``execution.api.enqueue_runs``, SSE events) runs in
``transaction.on_commit`` (Appendix A #12), so a rolled-back launch never queues work.

**Launch** takes a ``plan_hash``: the persisted ``StudyPlan`` of that hash, or a fresh resolve that must hash
to it (409 ``plan_stale`` when the design changed). A plan with ``error`` issues is refused (400
``plan_has_errors``); a plan whose p90 total exceeds ``estimate.require_confirm_above_usd`` needs
``confirm_usd >= p90_total`` (in the study cap's unit, ``estimate.basis_totals``; 400
``confirmation_required``). Launch writes the plan's conditions and units,
the cells of the launched stage (``pilot`` when the plan has pilot cells and no ``pilot_override``) and one
run per cell with its frozen manifest (``agentic.manifest.build_manifest``, which copies the identity keys
from the resolved cell). Cells the resolver marked ``excluded:config_invalid`` get no run. A confirmatory
study is **locked** in the same save: ``locked_at``, ``analysis_plan_hash`` and ``prereg_hash`` (plan hash,
analysis plan incl. patterns and sequential design, stop rules, pilot gates, evaluation) — after which the
design, analysis plan, budget preset and active plan can never change. A slot study gets a ``patterns``
post-run that runs when the study completes.

**Pilot → main** (``expand_pilot``) evaluates the pilot gates (``budget.stop_rules.pilot_gates``, WP-11)
and refuses on a failed gate unless ``override_gates`` with a reason. Exploratory studies with
``pilot.reuse_as_main`` flip the pilot cells to main in place (same plan_hash, same seeds) and create only
the remaining main cells; confirmatory studies never reuse pilot cells.

**Top-up** (6.14) creates a new attempt only for cells with no run or whose current run is ``infra_error``
or ``excluded`` for ``cancelled`` / ``stopped_by_rule`` / ``budget_stop``; cells with a model-caused current
run are refused (``cell_has_usable_run``). A usable run is never superseded.
"""

from __future__ import annotations

import logging
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from backend.generation import identity
from backend.generation.contracts.plan import ExecutionOptions
from backend.generation.contracts.resources import ResourcePolicyContent
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models import StudyCondition
from backend.generation.models import StudyPlan
from backend.generation.models import StudyPostRun
from backend.generation.models import StudyUnit
from backend.generation.models.choices import CellExclusionReason
from backend.generation.models.choices import CellState
from backend.generation.models.choices import ExclusionReason
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import PlanStatus
from backend.generation.models.choices import PostRunKind
from backend.generation.models.choices import PostRunTrigger
from backend.generation.models.choices import RunStatus
from backend.generation.models.choices import StudyStatus
from backend.generation.models.run import LANE_BY_KIND
from backend.generation.runs.queries import cell_current_ids
from backend.generation.runs.queries import cells_for_stage
from backend.generation.runs.queries import refresh_current
from backend.generation.study import analysis_plan as plans
from backend.generation.study import design as design_rules
from backend.generation.study import parents as study_parents
from backend.generation.study.deps import get_deps
from backend.generation.study.errors import StudyError
from backend.generation.study.plan import Library
from backend.generation.study.plan import PlanBundle
from backend.generation.study.plan import Unresolved
from backend.generation.study.plan import load_bundle
from backend.generation.study.plan import persist_bundle
from backend.generation.study.plan import resolve_bundle

if TYPE_CHECKING:
    from backend.generation.contracts.plan import Design
    from backend.generation.contracts.plan import ResolvedCell
    from backend.users.models import User

logger = logging.getLogger(__name__)

__all__ = [
    "add_replicates",
    "cancel",
    "clone",
    "clone_with_diffs",
    "create_study",
    "expand_pilot",
    "launch",
    "launch_adhoc",
    "pause",
    "pause_condition",
    "resume",
    "resume_condition",
    "top_up",
    "update_study",
]

_LAUNCHABLE: Final = (StudyStatus.DRAFT, StudyStatus.PLANNED)
_ACTIVE_RUNS: Final = (RunStatus.PENDING, RunStatus.RUNNING)
_TERMINAL: Final = (StudyStatus.COMPLETED, StudyStatus.CANCELLED, StudyStatus.STOPPED)
# Current-run exclusion reasons a top-up may refill (6.14).
TOP_UP_REASONS: Final = frozenset(
    {ExclusionReason.CANCELLED, ExclusionReason.STOPPED_BY_RULE, ExclusionReason.BUDGET_STOP},
)
_CELL_TOP_UP_REASONS: Final = frozenset(
    {CellExclusionReason.CANCELLED, CellExclusionReason.STOPPED_BY_RULE, CellExclusionReason.BUDGET_STOP},
)
_MODEL_CAUSED: Final = frozenset({Outcome.TESTED, Outcome.BUILD_FAIL, Outcome.START_FAIL, Outcome.OUTPUT_INVALID})
_MAX_REPLICATES: Final = 5
_KIND_MODELS: Final = {
    "harness_preset": "HarnessPreset",
    "stack_profile": "StackProfile",
    "style": "StyleDirective",
    "guidance": "Guidance",
    "prompt_set": "PromptSet",
    "sampling_preset": "SamplingPreset",
    "resource_policy": "ResourcePolicy",
    "eval_suite": "EvalSuite",
}


def _money(value: Any) -> Decimal:
    return Decimal(str(round(float(value or 0.0), 6)))


def _publish_state(study: Study) -> None:
    from backend.realtime.events import publish_study_event

    status, reason = study.status, study.pause_reason or ""
    study_id = study.pk
    transaction.on_commit(
        lambda: publish_study_event(study_id, {"type": "state", "status": status, "pause_reason": reason}),
    )


def _enqueue_on_commit(run_ids: list[Any]) -> None:
    if not run_ids:
        return
    ids = list(run_ids)
    enqueue = get_deps().enqueue_runs
    transaction.on_commit(lambda: enqueue(ids))


def _locked(study: Study) -> Study:
    return (
        Study.objects.select_for_update(of=("self",)).select_related("resource_policy", "created_by").get(pk=study.pk)
    )


def _policy(study: Study) -> ResourcePolicyContent:
    return ResourcePolicyContent.model_validate(study.resource_policy.content)


# ---------------------------------------------------------------------------
# Create and edit
# ---------------------------------------------------------------------------


def _normalized_design(raw: Any, user: User | None) -> tuple[dict[str, Any], dict[str, Any], Any]:
    """(design dump with the normalized analysis plan, the plan, the budget preset row)."""
    design = design_rules.parse_design(raw)
    design_rules.check_structure(design)
    try:
        plan = plans.normalize_analysis_plan(design.analysis_plan, kind=design.kind)
    except plans.PlanError as exc:
        raise StudyError(400, "invalid_analysis_plan", str(exc)) from exc
    dump = design.model_dump(mode="json")
    dump["analysis_plan"] = plan or None
    try:
        policy = Library(user, design.kind).entity("resource_policy", design.resource_policy, path="resource_policy")
    except Unresolved as exc:
        raise StudyError(400, "unknown_resource_policy", exc.issue.message) from exc
    if design.resource_policy not in (policy.ref, policy.pinned_ref):
        dump["resource_policy"] = policy.pinned_ref  # pin a bare slug to the version the FK names
    if design.slot_primary_features:
        dump["slot_primary_features"] = _pinned_features(design.slot_primary_features)
    return dump, plan, policy


def _pinned_features(refs: list[str]) -> list[str]:
    """``slot_primary_features`` as detector refs that resolve: a bare slug is pinned to its latest version.

    The live scorer and the pattern runs read these refs with ``patterns.detectors.resolve_refs``, which accepts
    only ``slug@version[#hash12]``; an unpinned or unknown ref is refused here (400 ``unknown_detector``) instead
    of failing every variant's evaluation later.
    """
    from backend.generation.models import PatternDetector
    from backend.generation.patterns import detectors as det

    pinned: list[str] = []
    for ref in refs:
        text = str(ref).strip()
        if "@" not in text:
            row = PatternDetector.objects.filter(slug=text, is_archived=False).order_by("-version").first()
            if row is None:
                raise StudyError(400, "unknown_detector", f"slot_primary_features: unknown detector {text!r}")
            text = f"{row.slug}@{row.version}#{row.content_hash[:12]}"
        try:
            det.resolve_refs([text])
        except det.DetectorRefError as exc:
            raise StudyError(400, "unknown_detector", f"slot_primary_features: {exc}") from exc
        pinned.append(text)
    return pinned


def create_study(
    *,
    user: User | None,
    name: str,
    design: Any,
    description: str = "",
    hypothesis: str = "",
    cloned_from: Study | None = None,
) -> Study:
    """A draft study; the design is validated and its analysis plan normalized (400 ``StudyError`` otherwise)."""
    dump, plan, policy = _normalized_design(design, user)
    study = Study(
        name=name[:200],
        slug=slugify(name)[:200] or "study",
        description=description,
        hypothesis=hypothesis,
        design=dump,
        resource_policy=policy,
        analysis_plan=plan,
        analysis_plan_hash=plans.analysis_plan_hash(plan),
        cloned_from=cloned_from,
        created_by=user,
    )
    study.full_clean()
    study.save()
    return study


def update_study(
    study: Study,
    *,
    name: str | None = None,
    description: str | None = None,
    hypothesis: str | None = None,
    design: Any = None,
    analysis_plan: dict[str, Any] | None = None,
) -> Study:
    """PATCH: text fields always; design and analysis plan only before launch (409 ``study_locked`` after)."""
    with transaction.atomic():
        locked = _locked(study)
        fields: list[str] = []
        for attr, value in (("name", name), ("description", description), ("hypothesis", hypothesis)):
            if value is not None:
                setattr(locked, attr, value[:200] if attr == "name" else value)
                fields.append(attr)
        if design is not None or analysis_plan is not None:
            if locked.locked_at is not None or locked.status not in _LAUNCHABLE:
                raise StudyError(409, "study_locked", "the design can only change before launch")
            raw = dict(design if design is not None else locked.design)
            if analysis_plan is not None:
                raw["analysis_plan"] = analysis_plan or None
            dump, plan, policy = _normalized_design(raw, locked.created_by)
            locked.design, locked.resource_policy = dump, policy
            locked.analysis_plan, locked.analysis_plan_hash = plan, plans.analysis_plan_hash(plan)
            if locked.status == StudyStatus.PLANNED and locked.design_hash != identity.design_hash(dump):
                locked.status = StudyStatus.DRAFT  # the frozen plan no longer matches the design
            fields += ["design", "resource_policy", "analysis_plan", "analysis_plan_hash", "status"]
        if fields:
            locked.full_clean()
            locked.save(update_fields=[*fields, "updated_at"])
    return locked


# ---------------------------------------------------------------------------
# Materializing a plan
# ---------------------------------------------------------------------------


class _Rows:
    """Library rows named by pinned levels (one query per ref)."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], Any] = {}

    def get(self, factor: str, pinned: str) -> Any:
        from backend.generation import models

        key = (factor, pinned)
        if key not in self._cache:
            slug, _, rest = pinned.partition("@")
            version = int(rest.split("#", 1)[0])
            model = getattr(models, _KIND_MODELS[factor])
            self._cache[key] = model.objects.get(slug=slug, version=version)
        return self._cache[key]


def _plan_bundle(study: Study, plan_hash: str) -> PlanBundle:
    row = StudyPlan.objects.filter(study=study, plan_hash=plan_hash).first()
    if row is not None:
        if row.design_hash != study.design_hash:
            raise StudyError(409, "plan_stale", "the design changed after this plan was frozen; resolve again")
        return load_bundle(row)
    if study_parents.uses_draw(design_rules.parse_design(study.design)):
        # The preview of a drawing design lacks the parents frozen at persist time: persist, then compare.
        bundle = resolve_bundle(study, persist=True)
    else:
        bundle = resolve_bundle(study)
    if bundle.plan.plan_hash != plan_hash:
        raise StudyError(
            409,
            "plan_stale",
            "the plan changed since it was previewed (design or library versions); review the new plan",
            {"plan_hash": bundle.plan.plan_hash},
        )
    return bundle if bundle.persisted else persist_bundle(study, bundle)


def _materialize(study: Study, bundle: PlanBundle) -> tuple[dict[int, StudyCondition], dict[int, StudyUnit]]:
    """The plan's ``StudyCondition`` / ``StudyUnit`` rows (created once per plan)."""
    from backend.generation.models import SlotTarget
    from backend.generation.models import TaskSpecVersion
    from backend.llm_models.models import LLMModel

    row = bundle.row
    assert row is not None
    existing_conditions = {c.index: c for c in StudyCondition.objects.filter(plan=row)}
    existing_units = {u.index: u for u in StudyUnit.objects.filter(plan=row)}
    rows = _Rows()
    plan = bundle.plan
    for condition in plan.conditions:
        if condition.index in existing_conditions or condition.treatment_hash is None:
            continue
        preset = condition.levels.get("harness_preset")
        created = StudyCondition(
            study=study,
            plan=row,
            index=condition.index,
            label=condition.label,
            levels=condition.levels,
            model=LLMModel.objects.get(model_id=condition.levels["model"]),
            harness_preset=rows.get("harness_preset", preset) if preset else None,
            treatment_hash=condition.treatment_hash,
            is_baseline=condition.is_baseline,
        )
        created.save()
        existing_conditions[condition.index] = created
    new_units = []
    for unit in plan.units:
        if unit.index in existing_units:
            continue
        task_version = None
        if unit.task_version is not None:
            slug, _, rest = unit.task_version.partition("@")
            task_version = TaskSpecVersion.objects.get(task__slug=slug, version=int(rest.split("#", 1)[0]))
        target = SlotTarget.objects.get(pk=unit.slot_target) if unit.slot_target else None
        new_units.append(
            StudyUnit(
                study=study,
                plan=row,
                index=unit.index,
                unit_key=unit.unit_key,
                task_version=task_version,
                slot_target=target,
                unit_order=unit.unit_order,
            ),
        )
    for unit in StudyUnit.objects.bulk_create(new_units):
        existing_units[unit.index] = unit
    return existing_conditions, existing_units


def _cell_caps(cell: ResolvedCell) -> Decimal:
    content = (cell.effective_policy or {}).get("content") or {}
    return _money(((content.get("cell") or {}).get("max_cost_usd")) or 0.0)


def _create_cells(
    study: Study,
    bundle: PlanBundle,
    resolved: list[ResolvedCell],
    conditions: dict[int, StudyCondition],
    units: dict[int, StudyUnit],
    *,
    beyond_plan: bool = False,
) -> list[tuple[ResolvedCell, StudyCell]]:
    rows = []
    pairs = []
    for cell in resolved:
        condition = conditions.get(cell.condition_index)
        if condition is None:
            continue
        estimate = cell.estimate
        excluded = cell.excluded == "config_invalid"
        row = StudyCell(
            study=study,
            plan=bundle.row,
            condition=condition,
            unit=units[cell.unit_index],
            repeat_index=cell.repeat_index,
            block_index=cell.block_index,
            stage=cell.stage,
            seed=cell.seed,
            sequence=cell.sequence,
            state=CellState.EXCLUDED if excluded else CellState.PLANNED,
            exclusion_reason=CellExclusionReason.CONFIG_INVALID if excluded else "",
            estimate_usd=_money(estimate.expected_usd if estimate is not None else 0.0),
            estimate_list_usd=_money(estimate.expected_list_usd if estimate is not None else 0.0),
            cap_usd=_money(estimate.cap_usd if estimate is not None else 0.0),
            cell_cap_usd=_cell_caps(cell),
            beyond_plan=beyond_plan,
        )
        rows.append(row)
        pairs.append(cell)
    created = StudyCell.objects.bulk_create(rows, batch_size=1000)
    return list(zip(pairs, created, strict=True))


def _identity_columns(manifest: Any) -> dict[str, str]:
    keys = manifest.identity.model_dump() if manifest.identity is not None else {}
    return {name: keys[name] for name in GenerationRun.IDENTITY_FIELDS if name in keys}


def _run_for(
    study: Study,
    cell: ResolvedCell,
    row: StudyCell,
    rows: _Rows,
    *,
    user: User | None,
    attempt: int,
    beyond_plan: bool,
) -> GenerationRun:
    manifest = cell.manifest
    kind = manifest.kind
    condition = row.condition
    unit = row.unit
    levels = cell.levels
    parent = unit.slot_target.slot_set.parent_run if unit.slot_target_id else None
    endpoint = manifest.model.endpoint if manifest.model is not None else None
    local = endpoint is not None and endpoint.id and endpoint.kind != "openrouter"
    run = GenerationRun(
        kind=kind,
        protocol=manifest.protocol,
        lane=LANE_BY_KIND[kind],
        status=RunStatus.PENDING,
        study=study,
        condition=condition,
        cell=row,
        cell_attempt=attempt,
        beyond_plan=beyond_plan,
        stage=row.stage,
        repeat_index=row.repeat_index,
        model=condition.model,
        task_version=unit.task_version if parent is None else unit.slot_target.slot_set.task_version,
        prose_variant=levels.get("prose_variant", "") if parent is None else (parent.prose_variant or ""),
        stack_profile=rows.get("stack_profile", levels["stack_profile"])
        if "stack_profile" in levels
        else (parent.stack_profile if parent is not None else None),
        starter=None,
        style=rows.get("style", levels["style"]) if "style" in levels else None,
        guidance=rows.get("guidance", levels["guidance"]) if "guidance" in levels else None,
        prompt_set=rows.get("prompt_set", levels["prompt_set"]),
        harness_preset=condition.harness_preset if kind in ("app_agentic", "slot_agent") else None,
        sampling_preset=rows.get("sampling_preset", levels["sampling_preset"]),
        resource_policy=rows.get("resource_policy", levels["resource_policy"]),
        auth_mode=levels.get("auth_mode", "") if parent is None else (parent.auth_mode or ""),
        slot_target=unit.slot_target,
        parent_run=parent,
        seed=cell.seed,
        pinned_served_name=(endpoint.served_model_name or "") if local else "",
        pinned_digest=(endpoint.digest or "") if local else "",
        sequence=cell.sequence,
        priority=study.priority,
        created_by=user,
        **_identity_columns(manifest),
    )
    if manifest.starter is not None:
        from backend.generation.models import Starter

        slug, _, rest = manifest.starter.ref.partition("@")
        run.starter = Starter.objects.filter(slug=slug, version=int(rest.split("#", 1)[0])).first()
    elif parent is not None:
        run.starter = parent.starter
    return run


def _create_runs(
    study: Study,
    pairs: list[tuple[ResolvedCell, StudyCell]],
    *,
    user: User | None,
    beyond_plan: bool = False,
) -> list[Any]:
    """One pending run (attempt 1) per non-excluded cell, each with its frozen manifest; returns the run ids."""
    rows = _Rows()
    build_manifest = get_deps().build_manifest
    runs: list[tuple[ResolvedCell, StudyCell, GenerationRun]] = []
    for cell, row in pairs:
        if row.state == CellState.EXCLUDED:
            continue
        run = _run_for(study, cell, row, rows, user=user, attempt=1, beyond_plan=beyond_plan)
        runs.append((cell, row, run))
    created = GenerationRun.objects.bulk_create([run for _, _, run in runs], batch_size=500)
    for (cell, row, _), run in zip(runs, created, strict=True):
        build_manifest(run, cell)
        row.current_run = run
        row.state = CellState.QUEUED
    StudyCell.objects.bulk_update([row for _, row, _ in runs], ["current_run", "state"], batch_size=1000)
    return [run.pk for run in created]


def _new_attempt(study: Study, cell: ResolvedCell, row: StudyCell, *, user: User | None) -> GenerationRun:
    """A further attempt on ``row`` (top-up); ``beyond_plan`` when the cell already has a usable run or is
    itself a replicate cell (every run of a replicate cell is beyond the plan)."""
    existing = row.runs.order_by("-cell_attempt").values_list("cell_attempt", flat=True).first() or 0
    has_usable = row.runs.filter(usable=True, beyond_plan=row.beyond_plan).exists()
    beyond = row.beyond_plan or has_usable
    run = _run_for(study, cell, row, _Rows(), user=user, attempt=existing + 1, beyond_plan=beyond)
    run.save()
    get_deps().build_manifest(run, cell)
    return run


def _resolved_by_key(bundle: PlanBundle) -> dict[tuple[int, int, int, str], ResolvedCell]:
    return {(c.condition_index, c.unit_index, c.repeat_index, c.stage): c for c in bundle.plan.cells}


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


def _resolved_cap(policy: ResourcePolicyContent, bundle: PlanBundle, study_cap: float | str | None) -> Decimal | None:
    """The USD cap stored in ``Study.cap_usd``, which ``budget.claims.study_cap`` enforces first.

    The request's ``study_cap``, else the preset's ``study.max_cost_usd``; ``auto`` is the frozen plan's
    ``estimate.cap_usd`` (the same value claims falls back to). With no resolved ``auto`` cap the result is
    ``None`` (no USD cap), never a made-up number, so ``resolved_cap`` always equals what claims enforce.
    """
    value: Any = study_cap if study_cap is not None else policy.study.max_cost_usd
    if value == "auto":
        estimate = bundle.plan.estimate
        if estimate is None or estimate.cap_usd is None:
            return None
        value = estimate.cap_usd
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StudyError(400, "invalid_cap", f"study_cap must be a number or 'auto', not {value!r}") from exc
    if number < 0:
        raise StudyError(400, "invalid_cap", "study_cap must not be negative")
    return _money(number)


def _confirm(policy: ResourcePolicyContent, bundle: PlanBundle, confirm_usd: float | None) -> None:
    """The p90 in the study cap's unit (list for a list-basis study) above the preset's threshold needs
    ``confirm_usd >= p90``."""
    from backend.generation.budget.estimate import basis_totals

    estimate = bundle.plan.estimate
    if estimate is None:
        return
    threshold = policy.estimate.require_confirm_above_usd
    expected, p90 = basis_totals(estimate)
    if p90 > threshold and (confirm_usd is None or confirm_usd < p90):
        unit = " (list price)" if estimate.cap_basis == "list" else ""
        raise StudyError(
            400,
            "confirmation_required",
            f"p90 estimate {p90:.2f} USD{unit} exceeds {threshold:.2f} USD; confirm with confirm_usd",
            {"p90_total": p90, "expected_total": expected, "threshold": threshold, "cap_basis": estimate.cap_basis},
        )


def _first_policy(bundle: PlanBundle) -> dict[str, Any]:
    for cell in bundle.plan.cells:
        if cell.effective_policy:
            return cell.effective_policy
    return {}


def _execution_options(effective: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
    concurrency = ((effective.get("content") or {}).get("concurrency")) or {}
    options = ExecutionOptions(
        max_resident_models=concurrency.get("max_resident_models"),
        per_model=concurrency.get("per_model"),
        per_endpoint=concurrency.get("per_endpoint"),
        per_provider=concurrency.get("per_provider"),
        rpm_per_model=concurrency.get("rpm_per_model"),
        eval_parallel=concurrency.get("eval_parallel"),
    )
    return concurrency.get("study_max_parallel"), options.model_dump(mode="json")


def _prereg(study: Study, bundle: PlanBundle, policy: ResourcePolicyContent, plan: dict[str, Any]) -> str:
    design = design_rules.parse_design(study.design)
    return identity.prereg_hash(
        plan_hash=bundle.plan.plan_hash,
        analysis_plan=plan or None,
        stop_rules=[rule.model_dump(mode="json") for rule in policy.study.stop_rules],
        pilot_gates=policy.pilot.gates.model_dump(mode="json"),
        evaluation=design.evaluation.model_dump(mode="json"),
    )


def _eval_stages(study: Study) -> dict[str, bool]:
    from backend.generation.evaluation.levels import stages_for

    return stages_for(design_rules.parse_design(study.design).evaluation)


def _queue_baselines(study: Study, bundle: PlanBundle, units: dict[int, StudyUnit]) -> int:
    """Queue the starter baseline of every (starter, task version, auth mode) of the plan (spec 8.5).

    App studies whose evaluation runs the baseline stage only. All cells of the plan count (the main stage of a
    piloted study too), so a later stage needs no baseline of its own. ``queue_baseline`` (WP-09) skips a
    triple that already has an ``EvalBaseline``; a triple whose baseline run is still pending or running is
    skipped here. A failure to queue one is logged and leaves that ``baseline_pass_rate`` ``None`` (not
    measured, never 0); it never blocks the launch.
    """
    queue = get_deps().queue_baseline
    if queue is None or study.mode != "app" or not _eval_stages(study).get("baseline"):
        return 0
    from backend.generation.models import EvalSuite
    from backend.generation.models import Starter
    from backend.generation.models.choices import ReferenceOf
    from backend.generation.models.choices import RunKind

    triples: dict[tuple[str, int, str, str], None] = {}
    for cell in bundle.plan.cells:
        manifest = cell.manifest
        unit = units.get(cell.unit_index)
        if manifest is None or manifest.starter is None or unit is None or unit.task_version_id is None:
            continue
        suite_ref = manifest.planned_evaluation.eval_suite.ref
        auth_mode = str(cell.levels.get("auth_mode") or "contract_bearer")
        triples[(manifest.starter.ref, unit.task_version_id, auth_mode, suite_ref)] = None
    rows = _Rows()
    queued = 0
    for starter_ref, task_version_id, auth_mode, suite_ref in triples:
        try:
            with transaction.atomic():
                slug, _, rest = starter_ref.partition("@")
                starter = Starter.objects.get(slug=slug, version=int(rest.split("#", 1)[0]))
                suite = rows.get("eval_suite", suite_ref)
                if GenerationRun.objects.filter(
                    kind=RunKind.REFERENCE,
                    reference_of=ReferenceOf.STARTER_BASELINE,
                    starter=starter,
                    task_version_id=task_version_id,
                    auth_mode=auth_mode,
                    execution__eval_suite=suite.pinned_ref,
                    status__in=(RunStatus.PENDING, RunStatus.RUNNING),
                ).exists():
                    continue
                task_version = _unit_task_version(units, task_version_id)
                if queue(starter, task_version, auth_mode, suite) is not None:
                    queued += 1
        except (EvalSuite.DoesNotExist, Starter.DoesNotExist, ValueError) as exc:
            logger.warning("study %s: no starter baseline for %s (%s)", study.pk, starter_ref, exc)
        except Exception:
            logger.exception("study %s: queueing the starter baseline of %s failed", study.pk, starter_ref)
    return queued


def _unit_task_version(units: dict[int, StudyUnit], task_version_id: int) -> Any:
    return next(u.task_version for u in units.values() if u.task_version_id == task_version_id)


def _attach_retest(study: Study) -> StudyPostRun | None:
    """A ``completed``-triggered post-run that re-evaluates ``Design.evaluation.retest_fraction`` of the runs.

    Test-retest agreement (``evaluation.retest.run_retests``, WP-09; spec 8.5) needs the tester, so app
    studies whose evaluation runs the functional stage only. It is an ``analysis`` post-run with no tools,
    no report and ``retest: true`` (``study.automation.run_post_run``); the executor starts it once the
    study is completed or stopped.
    """
    design = design_rules.parse_design(study.design)
    if study.mode != "app" or design.evaluation.retest_fraction <= 0 or not _eval_stages(study).get("functional"):
        return None
    if study.post_runs.filter(kind=PostRunKind.ANALYSIS, config__retest=True).exists():
        return None
    return StudyPostRun.objects.create(
        study=study,
        kind=PostRunKind.ANALYSIS,
        trigger=PostRunTrigger.COMPLETED,
        config={"tool_slugs": [], "only_passing": False, "report": False, "retest": True},
    )


def launch(
    study: Study,
    plan_hash: str,
    *,
    confirm_usd: float | None = None,
    study_cap: float | str | None = None,
    pilot_override: bool = False,
    user: User | None = None,
) -> dict[str, Any]:
    """Create the planned runs of ``plan_hash`` (409 ``plan_stale`` if the design changed); side effects on commit."""
    with transaction.atomic():
        locked = _locked(study)
        if locked.status not in _LAUNCHABLE:
            raise StudyError(409, "not_launchable", f"a {locked.status} study cannot be launched")
        bundle = _plan_bundle(locked, plan_hash)
        errors = [issue for issue in bundle.plan.issues if issue.severity == "error"]
        if errors:
            raise StudyError(
                400,
                "plan_has_errors",
                f"the plan has {len(errors)} blocking issue(s)",
                {"issues": [issue.model_dump(mode="json") for issue in errors]},
            )
        policy = _policy(locked)
        _confirm(policy, bundle, confirm_usd)
        cap = _resolved_cap(policy, bundle, study_cap)
        stages = {cell.stage for cell in bundle.plan.cells}
        stage = "pilot" if "pilot" in stages and not pilot_override else "main"
        conditions, units = _materialize(locked, bundle)
        resolved = [cell for cell in bundle.plan.cells if cell.stage == stage]
        pairs = _create_cells(locked, bundle, resolved, conditions, units)
        run_ids = _create_runs(locked, pairs, user=user or locked.created_by)
        effective = _first_policy(bundle)
        max_parallel, options = _execution_options(effective)
        plan = plans.normalize_analysis_plan(locked.analysis_plan or None, kind=locked.kind)
        now = timezone.now()
        locked.status = StudyStatus.PILOTING if stage == "pilot" else StudyStatus.RUNNING
        locked.active_plan = bundle.row
        locked.launched_at = now
        locked.max_parallel = max_parallel
        locked.execution_options = options
        locked.cap_usd = cap
        locked.cap_tokens = policy.study.max_total_tokens
        locked.budget_basis = effective.get("budget_basis") or locked.budget_basis
        locked.cap_basis = effective.get("cap_basis") or locked.cap_basis
        locked.on_budget_exhausted = policy.study.on_budget_exhausted
        locked.analysis_plan = plan
        locked.analysis_plan_hash = plans.analysis_plan_hash(plan)
        locked.prereg_hash = _prereg(locked, bundle, policy, plan)
        if locked.kind == "confirmatory":
            locked.locked_at = now
        locked.save()
        StudyPlan.objects.filter(pk=bundle.row.pk).update(status=PlanStatus.LAUNCHED)  # type: ignore[union-attr]
        StudyPlan.objects.filter(study=locked).exclude(pk=bundle.row.pk).exclude(  # type: ignore[union-attr]
            status=PlanStatus.LAUNCHED,
        ).update(status=PlanStatus.SUPERSEDED)
        if locked.mode == "slot" and not locked.post_runs.filter(kind=PostRunKind.PATTERNS).exists():
            StudyPostRun.objects.create(
                study=locked,
                kind=PostRunKind.PATTERNS,
                trigger=PostRunTrigger.COMPLETED,
                config={"detectors": "default"},
            )
        _queue_baselines(locked, bundle, units)
        _attach_retest(locked)
        _enqueue_on_commit(run_ids)
        _publish_state(locked)
    study.refresh_from_db()
    return {
        "status": locked.status,
        "created_runs": len(run_ids),
        "run_ids": [str(run_id) for run_id in run_ids],
        "stage": stage,
        "plan_hash": bundle.plan.plan_hash,
        "resolved_cap": _enforced_cap(study),
    }


def _enforced_cap(study: Study) -> float | None:
    """The USD cap the scheduler's claims enforce for ``study`` (``budget.claims.study_cap``), as launched."""
    from backend.generation.budget import claims

    cap = claims.study_cap(study)
    return float(cap) if cap is not None else None


_launch = launch  # launch_adhoc's ``launch`` flag shadows the function


# ---------------------------------------------------------------------------
# Pilot expansion
# ---------------------------------------------------------------------------


def expand_pilot(
    study: Study,
    *,
    override_gates: bool = False,
    reason: str | None = None,
    user: User | None = None,
) -> dict[str, Any]:
    """Pilot to main; reuses pilot cells as repeat 0.. when allowed (exploratory, ``pilot.reuse_as_main``)."""
    with transaction.atomic():
        locked = _locked(study)
        if locked.status != StudyStatus.PILOTING or locked.active_plan_id is None:
            raise StudyError(409, "not_piloting", f"a {locked.status} study has no pilot to expand")
        if cells_for_stage(locked, "pilot").filter(runs__status__in=_ACTIVE_RUNS).exists():
            raise StudyError(409, "pilot_active", "the pilot still has pending or running runs")
        gates = list(get_deps().pilot_gates(locked))
        failed = [gate for gate in gates if not gate.get("ok")]
        if failed and not override_gates:
            raise StudyError(409, "pilot_gates_failed", "pilot gates failed", {"gates": gates})
        if failed and not (reason or "").strip():
            raise StudyError(400, "reason_required", "overriding failed pilot gates needs a reason")
        if failed:
            logger.warning("study %s: pilot gates overridden (%s): %s", locked.pk, reason, failed)
        policy = _policy(locked)
        bundle = load_bundle(locked.active_plan)  # type: ignore[arg-type]
        conditions, units = _materialize(locked, bundle)
        reuse = policy.pilot.reuse_as_main and locked.kind == "exploratory"
        reused = 0
        taken: set[tuple[int, int, int]] = set()
        if reuse:
            main_keys = {
                (c.condition_index, c.unit_index, c.repeat_index) for c in bundle.plan.cells if c.stage == "main"
            }
            pilot_cells = list(
                StudyCell.objects.filter(plan=locked.active_plan, stage="pilot").select_related("condition", "unit"),
            )
            for cell in pilot_cells:
                key = (cell.condition.index, cell.unit.index, cell.repeat_index)
                if key in main_keys:
                    taken.add(key)
            flip = [c.pk for c in pilot_cells if (c.condition.index, c.unit.index, c.repeat_index) in taken]
            StudyCell.objects.filter(pk__in=flip).update(stage="main")
            GenerationRun.objects.filter(cell_id__in=flip).update(stage="main")
            reused = len(flip)
        resolved = [
            cell
            for cell in bundle.plan.cells
            if cell.stage == "main" and (cell.condition_index, cell.unit_index, cell.repeat_index) not in taken
        ]
        pairs = _create_cells(locked, bundle, resolved, conditions, units)
        run_ids = _create_runs(locked, pairs, user=user or locked.created_by)
        locked.status = StudyStatus.RUNNING
        locked.save(update_fields=["status", "updated_at"])
        _enqueue_on_commit(run_ids)
        _publish_state(locked)
    return {
        "created_runs": len(run_ids),
        "reused_pilot_cells": reused,
        "gates": gates,
        "overridden": bool(failed),
        "reason": reason or "",
    }


# ---------------------------------------------------------------------------
# Top-up and added replicates
# ---------------------------------------------------------------------------


def _top_up_status(cell: dict[str, Any], run: GenerationRun | None) -> str | None:  # noqa: PLR0911 - one return per rule
    """None when the cell may be topped up, else the refusal code."""
    if run is None:
        if cell["state"] == CellState.EXCLUDED and cell["exclusion_reason"] not in _CELL_TOP_UP_REASONS:
            return "not_eligible"
        return None
    if run.status in _ACTIVE_RUNS:
        return "cell_active"
    if run.usable or run.outcome in _MODEL_CAUSED:
        return "cell_has_usable_run"
    if run.outcome == Outcome.INFRA_ERROR:
        return None
    if run.outcome == Outcome.EXCLUDED and run.outcome_reason in TOP_UP_REASONS:
        return None
    if run.status == RunStatus.FAILED and not run.outcome:
        return None
    return "not_eligible"


def top_up(study: Study, cells: Any, *, reason: str = "", user: User | None = None) -> dict[str, Any]:
    """New attempts only for cells without a usable current run (6.14).

    ``cells`` is ``"missing"`` (cells with no run), ``"infra"`` (current run ``infra_error`` or excluded for
    cancelled / stopped_by_rule / budget_stop) or a list of cell ids (each checked by the same rules).
    """
    if cells not in ("missing", "infra") and not (isinstance(cells, list) and all(isinstance(c, int) for c in cells)):
        raise StudyError(400, "invalid_cells", "cells must be 'missing', 'infra' or a list of cell ids")
    with transaction.atomic():
        locked = _locked(study)
        if locked.status == StudyStatus.CANCELLED or locked.active_plan_id is None:
            raise StudyError(409, "not_running", f"a {locked.status} study cannot be topped up")
        stage = "pilot" if locked.status == StudyStatus.PILOTING else "main"
        qs = cell_current_ids(cells_for_stage(locked, stage))
        if isinstance(cells, list):
            qs = cell_current_ids(cells_for_stage(locked, None).filter(pk__in=cells))
        rows = list(qs.values("pk", "state", "exclusion_reason", "current_id"))
        if isinstance(cells, list):
            unknown = sorted(set(cells) - {r["pk"] for r in rows})
            if unknown:
                raise StudyError(400, "invalid_cells", f"cells {unknown} are not cells of the active plan")
        runs = {
            r.pk: r for r in GenerationRun.objects.filter(pk__in=[r["current_id"] for r in rows if r["current_id"]])
        }
        bundle = load_bundle(locked.active_plan)  # type: ignore[arg-type]
        by_key = _resolved_by_key(bundle)
        created: list[Any] = []
        refused: list[dict[str, Any]] = []
        cell_rows = {
            c.pk: c
            for c in StudyCell.objects.filter(pk__in=[r["pk"] for r in rows]).select_related("condition", "unit")
        }
        for row in rows:
            run = runs.get(row["current_id"])
            code = _top_up_status(row, run)
            if cells == "missing" and run is not None:
                continue
            if cells == "infra" and run is None:
                continue
            if code is not None:
                if isinstance(cells, list):
                    refused.append({"cell_id": row["pk"], "code": code})
                continue
            cell = cell_rows[row["pk"]]
            resolved = by_key.get((cell.condition.index, cell.unit.index, cell.repeat_index, cell.stage))
            if resolved is None:
                refused.append({"cell_id": row["pk"], "code": "not_in_plan"})
                continue
            new = _new_attempt(locked, resolved, cell, user=user or locked.created_by)
            created.append(new.pk)
            StudyCell.objects.filter(pk=cell.pk).update(state=CellState.PLANNED, exclusion_reason="")
            refresh_current(cell)
        if created and locked.status in _TERMINAL:
            locked.status = StudyStatus.RUNNING if stage == "main" else StudyStatus.PILOTING
            locked.completed_at = None
            locked.stop_reason = None
            locked.save(update_fields=["status", "completed_at", "stop_reason", "updated_at"])
            _publish_state(locked)
        _enqueue_on_commit(created)
    if reason:
        logger.info("study %s: top-up (%s) created %d run(s)", locked.pk, reason, len(created))
    return {"created_runs": len(created), "run_ids": [str(i) for i in created], "refused": refused}


def add_replicates(study: Study, replicates: int, *, user: User | None = None) -> dict[str, Any]:
    """Add ``replicates`` repeats beyond the plan (exploratory only); their runs are ``beyond_plan``.

    The extra cells are resolved from the current design with more repeats; every (condition, unit) must
    keep the active plan's ``config_fingerprint`` (409 ``design_drifted`` otherwise: they would not be
    replicates). Results leave beyond-plan runs out of the planned sample.
    """
    if not 1 <= int(replicates) <= _MAX_REPLICATES:
        raise StudyError(400, "invalid_replicates", f"replicates must be between 1 and {_MAX_REPLICATES}")
    with transaction.atomic():
        locked = _locked(study)
        if locked.locked_at is not None or locked.kind == "confirmatory":
            raise StudyError(409, "study_locked", "confirmatory studies never grow beyond their plan")
        if locked.active_plan_id is None or locked.status in (StudyStatus.DRAFT, StudyStatus.PLANNED):
            raise StudyError(409, "not_running", "launch the study before adding replicates")
        active = load_bundle(locked.active_plan)  # type: ignore[arg-type]
        top = max(
            StudyCell.objects.filter(plan=locked.active_plan, stage="main").values_list("repeat_index", flat=True),
            default=locked.repeats - 1,
        )
        repeats = top + 1 + int(replicates)
        extended = resolve_bundle(locked, repeats=repeats, use_cache=False)
        planned = {
            (c.levels.get("model"), c.condition_index, c.unit_key): c.manifest.identity.config_fingerprint  # type: ignore[union-attr]
            for c in active.plan.cells
            if c.stage == "main" and c.repeat_index == 0
        }
        drift = [
            c.cell_key
            for c in extended.plan.cells
            if c.stage == "main"
            and c.repeat_index == 0
            and planned.get((c.levels.get("model"), c.condition_index, c.unit_key))
            not in (None, c.manifest.identity.config_fingerprint)  # type: ignore[union-attr]
        ]
        if drift:
            raise StudyError(409, "design_drifted", "the configuration changed since launch", {"cells": drift[:20]})
        extra = [c for c in extended.plan.cells if c.stage == "main" and c.repeat_index > top]
        bundle = PlanBundle(plan=active.plan, row=locked.active_plan)
        conditions, units = _materialize(locked, bundle)
        pairs = _create_cells(locked, bundle, extra, conditions, units, beyond_plan=True)
        for text in extended.texts.values():
            from backend.generation import blobs

            blobs.put_text(text)
        run_ids = _create_runs(locked, pairs, user=user or locked.created_by, beyond_plan=True)
        if run_ids and locked.status in _TERMINAL:
            locked.status = StudyStatus.RUNNING
            locked.completed_at = None
            locked.save(update_fields=["status", "completed_at", "updated_at"])
            _publish_state(locked)
        _enqueue_on_commit(run_ids)
    return {"created_runs": len(run_ids), "run_ids": [str(i) for i in run_ids], "repeats": repeats}


# ---------------------------------------------------------------------------
# Clone and ad-hoc
# ---------------------------------------------------------------------------


def _latest_levels(design: dict[str, Any], user: User | None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    lib = Library(user, design.get("kind", "exploratory"))
    diffs: list[dict[str, str]] = []
    out = dict(design)

    def latest(kind: str, ref: str, factor: str) -> str:
        slug = ref.split("@", 1)[0].split("#", 1)[0]
        try:
            row = lib.entity(kind, slug, path=factor)
        except Unresolved:
            return ref
        pinned = row.pinned_ref
        if pinned != ref and _strip(ref) != row.ref:
            diffs.append({"factor": factor, "from": ref, "to": pinned})
        return pinned if pinned != ref and _strip(ref) != row.ref else ref

    factors = []
    for factor in design.get("factors") or []:
        kind = design_rules.LIBRARY_FACTORS.get(factor["key"])
        levels = [latest(kind, level, factor["key"]) for level in factor["levels"]] if kind else factor["levels"]
        factors.append({**factor, "levels": levels})
    out["factors"] = factors
    units = dict(design.get("units") or {})
    units["tasks"] = [latest("task", ref, "units.tasks") for ref in units.get("tasks") or []]
    out["units"] = units
    out["resource_policy"] = latest("resource_policy", design["resource_policy"], "resource_policy")
    evaluation = dict(design.get("evaluation") or {})
    evaluation["eval_suite"] = latest("eval_suite", evaluation["eval_suite"], "evaluation.eval_suite")
    out["evaluation"] = evaluation
    return out, diffs


def _strip(ref: str) -> str:
    return ref.split("#", 1)[0]


def clone_with_diffs(
    study: Study,
    *,
    mode: str,
    name: str,
    user: User | None,
    base_seed: int | None = None,
) -> tuple[Study, list[dict[str, str]]]:
    """Copy ``study`` as a draft: ``exact`` (same design) or ``latest_versions`` (every library level re-pinned)."""
    if mode not in ("exact", "latest_versions"):
        raise StudyError(400, "invalid_mode", "mode must be 'exact' or 'latest_versions'")
    design = dict(study.design)
    diffs: list[dict[str, str]] = []
    if mode == "latest_versions":
        design, diffs = _latest_levels(design, user)
    if base_seed is not None:
        design["base_seed"] = int(base_seed)
    clone_ = create_study(
        user=user,
        name=name,
        design=design,
        description=study.description,
        hypothesis=study.hypothesis,
        cloned_from=study,
    )
    return clone_, diffs


def clone(study: Study, *, mode: str, name: str, user: User | None) -> Study:
    """Copy a study as a draft (``exact`` or ``latest_versions``)."""
    return clone_with_diffs(study, mode=mode, name=name, user=user)[0]


def launch_adhoc(
    design: Design | dict[str, Any],
    *,
    user: User | None,
    launch: bool = True,
    name: str | None = None,
    confirm_usd: float | None = None,
    quick: bool = False,
) -> dict[str, Any]:
    """Resolve and launch in one call (automation, the slot picker's "Generate a parent app", the "Generate
    app" page).

    Returns ``{"study_id", "plan_hash", "run_ids", "status", "issues", "estimate"}``; a plan with blocking
    issues raises ``StudyError`` (400 ``plan_has_errors``) and nothing is created. ``quick`` marks the study
    as a quick generation (``Study.is_quick``). A quick call with ``launch=False`` is a dry run: the plan is
    resolved and priced, then everything is rolled back, so pricing a form never leaves draft studies behind
    (``study_id`` is then ``None``).
    """
    raw = design.model_dump(mode="json") if hasattr(design, "model_dump") else dict(design)
    stamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M")
    with transaction.atomic():
        study = create_study(user=user, name=name or f"Ad-hoc {stamp}", design=raw)
        if quick:
            study.is_quick = True
            study.save(update_fields=["is_quick", "updated_at"])
        bundle = resolve_bundle(study, use_cache=False)
        payload: dict[str, Any] = {
            "study_id": str(study.pk),
            "plan_hash": bundle.plan.plan_hash,
            "run_ids": [],
            "status": study.status,
            "issues": [issue.model_dump(mode="json") for issue in bundle.plan.issues],
            "estimate": bundle.plan.estimate.model_dump(mode="json") if bundle.plan.estimate else None,
        }
        if launch:
            result = _launch(study, bundle.plan.plan_hash, confirm_usd=confirm_usd, user=user)
            payload["run_ids"] = result["run_ids"]
            payload["status"] = result["status"]
        elif quick:
            payload["study_id"] = None
            transaction.set_rollback(True)
    return payload


# ---------------------------------------------------------------------------
# Pause, resume, cancel
# ---------------------------------------------------------------------------


def pause(study: Study, *, reason: str = "manual") -> Study:
    """Stop claiming new runs (running ones finish); ``pause_reason`` is ``manual`` unless a rule says otherwise."""
    with transaction.atomic():
        locked = _locked(study)
        if locked.status not in (StudyStatus.RUNNING, StudyStatus.PILOTING):
            raise StudyError(409, "not_running", f"a {locked.status} study cannot be paused")
        locked.status = StudyStatus.PAUSED
        locked.pause_reason = reason or "manual"
        locked.full_clean(validate_unique=False, validate_constraints=False)
        locked.save(update_fields=["status", "pause_reason", "updated_at"])
        _publish_state(locked)
    return locked


def resume(study: Study) -> Study:
    """Back to piloting (no main cells yet) or running; the executor is woken on commit."""
    from backend.execution.api import wake

    with transaction.atomic():
        locked = _locked(study)
        if locked.status != StudyStatus.PAUSED:
            raise StudyError(409, "not_paused", f"a {locked.status} study is not paused")
        has_main = StudyCell.objects.filter(plan=locked.active_plan, stage="main").exists()
        locked.status = StudyStatus.RUNNING if has_main else StudyStatus.PILOTING
        locked.pause_reason = ""
        locked.save(update_fields=["status", "pause_reason", "updated_at"])
        _publish_state(locked)
        transaction.on_commit(wake)
    return locked


def cancel(study: Study) -> Study:
    """Cancel the study; pending runs are cancelled on commit by the executor's cancel path."""
    from backend.generation.runs.queries import runs_for_study

    with transaction.atomic():
        locked = _locked(study)
        if locked.status in _TERMINAL:
            raise StudyError(409, "not_running", f"a {locked.status} study cannot be cancelled")
        if locked.status in _LAUNCHABLE:
            locked.status = StudyStatus.CANCELLED
            locked.cancelled_at = timezone.now()
            locked.save(update_fields=["status", "cancelled_at", "updated_at"])
            _publish_state(locked)
        else:
            get_deps().cancel_runs(runs_for_study(locked), "all", study=locked)
    locked.refresh_from_db()
    return locked


def pause_condition(condition: StudyCondition, *, reason: str = "manual") -> StudyCondition:
    """Stop claiming this condition only (``pause_condition``)."""
    StudyCondition.objects.filter(pk=condition.pk).update(paused=True, pause_reason=reason[:40])
    condition.refresh_from_db()
    return condition


def resume_condition(condition: StudyCondition) -> StudyCondition:
    """Claim this condition again."""
    from backend.execution.api import wake

    StudyCondition.objects.filter(pk=condition.pk).update(paused=False, pause_reason="")
    condition.refresh_from_db()
    transaction.on_commit(wake)
    return condition
