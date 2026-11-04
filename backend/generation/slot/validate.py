"""Target validation: original 3x, holed tree 1x, discriminating checks (spec section 7.4).

A target is usable in studies only when ``validation.status`` is ``ok`` or ``uninformative`` (and the leak
scan found no verbatim copy). Validation runs are ``reference`` runs on the eval lane (machine time only, low
priority):

1. the **original** (the parent tree, rows copied, same blobs) is evaluated ``VALIDATION_RUNS`` (3) times with
   the target's linked checks and probes (``reference_of=slot_original``; the first becomes
   ``SlotTarget.original_run``, variant 0). A check must pass all three times; mixed results make it flaky.
   Probe results must be identical across the three runs, else the probe is dropped;
2. the **hole-applied** tree is evaluated once (``reference_of=slot_holed``; ``stub``/``remove`` only). A check
   that still passes with the hole is non-discriminating. Only a measured failure discriminates (``fail``, or
   ``skipped`` because a failed ``depends_on``); a check missing from the holed results or with a tester
   ``error`` is unmeasured and dropped (no ``holed_fail`` entry), and when none of the stable checks was
   measured the validation is an ``error``. A holed tree that does not build or start fails every check (that
   is what the hole does), but a tester or infra error is a validation ``error``, never a silent pass or fail;
3. ``discriminating_checks`` = checks passing 3/3 on the original and failing on the holed tree; every other
   linked check is dropped (``dropped_checks``) and ``linked_checks`` becomes the discriminating set;
4. no discriminating check left: ``status=uninformative`` and issue ``slot_tests_uninformative`` (warn
   exploratory, error confirmatory); slot pass rates are then ``n/a``.

The selection a validation evaluates is recorded up front as the keys of ``original_pass`` /
``probe_stability`` (empty lists until the results are in), so the evaluation stage knows what to run for
each reference run. ``queue_validation`` enqueues the reference runs on commit; the eval stage calls
``on_reference_evaluated(run)`` after each one, and the last one completes the validation. ``validate_target``
aggregates (or, with ``synchronous=True``, runs everything inline: tests and management commands).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.db import transaction
from django.utils import timezone

from backend.generation import blobs
from backend.generation import identity
from backend.generation.contracts.evaluation import EvalResult
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.slot import TargetValidation
from backend.generation.models import EvalSuite
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import RunFile
from backend.generation.models import SlotTarget
from backend.generation.models.choices import EvaluationStatus
from backend.generation.models.choices import FileOrigin
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import ReferenceOf
from backend.generation.models.choices import RunKind
from backend.generation.models.choices import RunStatus
from backend.generation.slot.part import part_of

if TYPE_CHECKING:
    from backend.users.models import User

__all__ = [
    "SLOT_REFERENCE_KINDS",
    "VALIDATION_PRIORITY",
    "VALIDATION_RUNS",
    "aggregate",
    "on_reference_evaluated",
    "queue_validation",
    "target_usable",
    "validate_target",
    "validation_issues",
    "validation_suite",
]

VALIDATION_RUNS: Final = 3
VALIDATION_PRIORITY: Final = -10  # behind study evaluations on the eval lane
SLOT_REFERENCE_KINDS: Final = frozenset({ReferenceOf.SLOT_ORIGINAL, ReferenceOf.SLOT_HOLED})
_HOLED_MODES: Final = frozenset({"stub", "remove"})
_SUITE_KEY: Final = "slot_validation_suite"
_FAILED_STAGE: Final = "fail"


def validation_suite(target: SlotTarget) -> EvalSuite | None:
    """The suite a target is validated under: the one its parent was evaluated with."""
    parent_eval = target.slot_set.parent_eval
    return parent_eval.eval_suite if parent_eval is not None else None


def _tree_rows(target: SlotTarget, run: GenerationRun, *, holed: bool) -> tuple[list[RunFile], str]:
    """``RunFile`` rows of a reference run (the parent's rows, the slot file holed) and their tree hash."""
    part = part_of(target)
    hole = part.hole() if holed else None
    rows: list[RunFile] = []
    for row in RunFile.objects.filter(run_id=target.slot_set.parent_run_id).order_by("path"):
        if row.origin == FileOrigin.DELETED:
            continue
        blob, size = row.blob, row.size
        if hole is not None and row.path == part.path:
            if hole.removed:
                continue
            blob = blobs.put_bytes(hole.holed or b"")
            size = len(hole.holed or b"")
        rows.append(
            RunFile(
                run=run,
                path=row.path,
                blob=blob,
                size=size,
                mode=row.mode,
                origin=row.origin,
                language=row.language,
                loc=row.loc,
                is_test=row.is_test,
                excluded=row.excluded,
            ),
        )
    tree = identity.tree_hash((r.path, r.mode, r.blob) for r in rows)
    return rows, tree


def _reference_run(target: SlotTarget, reference_of: str, suite: EvalSuite, user: User | None) -> GenerationRun:
    parent = target.slot_set.parent_run
    run = GenerationRun(
        kind=RunKind.REFERENCE,
        reference_of=reference_of,
        status=RunStatus.PENDING,
        slot_target=target,
        parent_run=parent,
        task_version=target.slot_set.task_version,
        stack_profile=parent.stack_profile,
        starter=parent.starter,
        auth_mode=parent.auth_mode,
        resolved_lock_blob=parent.resolved_lock_blob,
        lock_source=parent.lock_source,
        priority=VALIDATION_PRIORITY,
        execution={_SUITE_KEY: suite.pinned_ref},
        created_by=user,
    )
    run.save()
    rows, tree = _tree_rows(target, run, holed=reference_of == ReferenceOf.SLOT_HOLED)
    RunFile.objects.bulk_create(rows)
    # The original's rows are the parent's: it carries the frozen tree hash (eval reuse by tree must find it).
    run.tree_hash = target.slot_set.frozen_tree_hash if reference_of == ReferenceOf.SLOT_ORIGINAL else tree
    run.save(update_fields=["tree_hash"])
    return run


def queue_validation(
    target: SlotTarget,
    *,
    suite: EvalSuite | None = None,
    user: User | None = None,
    enqueue: bool = True,
) -> list[GenerationRun]:
    """Create the validation runs (original x3, holed x1) and enqueue them for evaluation on commit."""
    suite = suite or validation_suite(target)
    candidates = list(target.linked_checks or [])
    probes = list(target.linked_probes or [])
    if suite is None:
        target.validation = TargetValidation(
            status="error",
            error="the parent has no evaluation, so there is no suite to validate the target under",
            at=timezone.now().isoformat(),
        ).model_dump(mode="json")
        target.save(update_fields=["validation"])
        return []
    runs = [
        _reference_run(target, ReferenceOf.SLOT_ORIGINAL, suite, user or target.created_by)
        for _ in range(VALIDATION_RUNS)
    ]
    if target.default_hole_mode in _HOLED_MODES:
        runs.append(_reference_run(target, ReferenceOf.SLOT_HOLED, suite, user or target.created_by))
    target.validation = TargetValidation(
        status="pending",
        original_pass={check: [] for check in candidates},
        probe_stability={probe: [] for probe in probes},
        runs=[str(run.pk) for run in runs],
        at=timezone.now().isoformat(),
    ).model_dump(mode="json")
    target.original_run = runs[0]
    target.save(update_fields=["validation", "original_run"])
    if enqueue:
        from backend.execution import api as execution_api

        run_ids = [run.pk for run in runs]
        transaction.on_commit(lambda: execution_api.enqueue_evaluation(run_ids))
    return runs


def _latest(run_id: str) -> RunEvaluation | None:
    return (
        RunEvaluation.objects.filter(run_id=run_id, retest_of__isnull=True, reused_from__isnull=True)
        .order_by("-created_at", "-id")
        .first()
    )


def _statuses(evaluation: RunEvaluation) -> tuple[dict[str, str], dict[str, str], list[str]]:
    result = EvalResult.model_validate(evaluation.result)
    checks = {check.id: check.status for check in result.checks}
    probes = {probe.id: probe.status for probe in result.probes}
    return checks, probes, list(result.flaky_checks)


def _holed_verdicts(evaluation: RunEvaluation, candidates: list[str]) -> dict[str, bool]:
    """``{check: fails with the hole}`` for the checks the holed evaluation measured.

    Only a measured failure counts: ``fail``, or ``skipped`` because a ``depends_on`` check failed. ``pass`` is
    ``False``. A check that is missing from the results, ``error`` (tester error) or skipped for another reason
    was not measured and gets no entry (it cannot be discriminating; invariant 16, no silent result).
    """
    result = EvalResult.model_validate(evaluation.result)
    rows = {check.id: check for check in result.checks}
    verdicts: dict[str, bool] = {}
    for check in candidates:
        row = rows.get(check)
        if row is None:
            continue
        if row.status == "pass":
            verdicts[check] = False
        elif row.status == "fail" or (row.status == "skipped" and row.skipped_due_to):
            verdicts[check] = True
    return verdicts


def _not_running(evaluation: RunEvaluation) -> bool:
    """A holed tree that failed to build or start: every check fails (the hole did it)."""
    stages = evaluation.stages or {}
    return any((stages.get(name) or {}).get("status") == _FAILED_STAGE for name in ("lock", "build", "start"))


def _measured(evaluation: RunEvaluation) -> bool:
    return evaluation.status == EvaluationStatus.MEASURED and bool((evaluation.result or {}).get("measured"))


def aggregate(target: SlotTarget) -> TargetValidation:
    """The validation computed from the reference runs' latest evaluations (``pending`` until all are final)."""
    current = TargetValidation.model_validate(target.validation or {})
    if current.status != "pending" or not current.runs:
        return current
    runs = {str(r.pk): r for r in GenerationRun.objects.filter(pk__in=current.runs)}
    originals = [rid for rid in current.runs if runs.get(rid) and runs[rid].reference_of == ReferenceOf.SLOT_ORIGINAL]
    holed = [rid for rid in current.runs if runs.get(rid) and runs[rid].reference_of == ReferenceOf.SLOT_HOLED]
    evaluations = {rid: _latest(rid) for rid in current.runs}
    if any(e is None or e.status not in RunEvaluation.FINAL_STATUSES for e in evaluations.values()):
        return current
    candidates = list(current.original_pass)
    probes = list(current.probe_stability)

    def error(reason: str) -> TargetValidation:
        return current.model_copy(update={"status": "error", "error": reason, "at": timezone.now().isoformat()})

    original_pass: dict[str, list[bool]] = {check: [] for check in candidates}
    probe_stability: dict[str, list[Any]] = {probe: [] for probe in probes}
    flaky: set[str] = set()
    for rid in originals:
        evaluation = evaluations[rid]
        assert evaluation is not None
        if not _measured(evaluation):
            return error(f"an evaluation of the original was not measured ({evaluation.status}); no check is assumed")
        checks, probe_results, suite_flaky = _statuses(evaluation)
        flaky |= set(suite_flaky)
        for check in candidates:
            original_pass[check].append(checks.get(check) == "pass")
        for probe in probes:
            probe_stability[probe].append(probe_results.get(probe, "error"))
    holed_fail: dict[str, bool] = {}
    for rid in holed:
        evaluation = evaluations[rid]
        assert evaluation is not None
        if _measured(evaluation):
            holed_fail = _holed_verdicts(evaluation, candidates)
        elif _not_running(evaluation):
            holed_fail = dict.fromkeys(candidates, True)
        else:
            return error(f"the holed tree's evaluation failed ({evaluation.status}) without a build or start failure")
    flaky |= {check for check, results in original_pass.items() if any(results) and not all(results)}
    stable = [check for check in candidates if check not in flaky and all(original_pass[check])]
    if holed and stable and not any(check in holed_fail for check in stable):
        return error("the holed tree's evaluation measured none of the checks that pass on the original")
    discriminating = [check for check in stable if (holed_fail.get(check) is True if holed else True)]
    dropped = [check for check in candidates if check not in discriminating]
    return TargetValidation(
        status="ok" if discriminating else "uninformative",
        original_pass=original_pass,
        holed_fail=holed_fail,
        flaky_checks=sorted(flaky & set(candidates)),
        dropped_checks=dropped,
        discriminating_checks=discriminating,
        probe_stability=probe_stability,
        runs=current.runs,
        at=timezone.now().isoformat(),
    )


def _apply(target: SlotTarget, validation: TargetValidation) -> None:
    fields = ["validation"]
    target.validation = validation.model_dump(mode="json")
    if validation.status in {"ok", "uninformative"}:
        target.linked_checks = list(validation.discriminating_checks)
        target.linked_probes = sorted(
            probe for probe, results in validation.probe_stability.items() if results and len(set(results)) == 1
        )
        fields += ["linked_checks", "linked_probes"]
    target.save(update_fields=fields)


def on_reference_evaluated(run: GenerationRun) -> TargetValidation | None:
    """Eval-stage hook for ``slot_original`` / ``slot_holed`` runs: complete the validation when all are in."""
    if run.kind != RunKind.REFERENCE or run.reference_of not in SLOT_REFERENCE_KINDS or run.slot_target_id is None:
        return None
    with transaction.atomic():
        target = SlotTarget.objects.select_for_update().get(pk=run.slot_target_id)
        if str(run.pk) not in (target.validation or {}).get("runs", []):
            return TargetValidation.model_validate(target.validation or {})  # a superseded validation
        validation = aggregate(target)
        if validation.status != "pending":
            _apply(target, validation)
    return validation


def _evaluate_inline(run: GenerationRun) -> None:
    from backend.generation.slot.evaluate import evaluate_variant
    from backend.generation.slot.evaluate import outcome_of

    evaluation = evaluate_variant(run)
    outcome, reason = outcome_of(evaluation)
    run.status = RunStatus.COMPLETED
    run.outcome = outcome
    run.outcome_reason = reason
    run.completed_at = timezone.now()
    run.save(update_fields=["status", "outcome", "outcome_reason", "usable", "completed_at"])


def validate_target(target: SlotTarget, *, synchronous: bool = False) -> TargetValidation:
    """Keep only the linked checks that discriminate; none left is ``uninformative``.

    ``synchronous=False`` aggregates the queued validation's results (``pending`` while runs are out);
    ``synchronous=True`` queues a fresh validation and evaluates its runs inline.
    """
    if synchronous:
        runs = queue_validation(target, enqueue=False)
        for run in runs:
            _evaluate_inline(run)
            on_reference_evaluated(run)
        target.refresh_from_db()
        return TargetValidation.model_validate(target.validation or {})
    validation = aggregate(target)
    if validation.status != TargetValidation.model_validate(target.validation or {}).status:
        _apply(target, validation)
    return validation


def revalidate(target: SlotTarget, *, user: User | None = None) -> SlotTarget:
    """Validate again from the pre-validation selection (auto links plus the researcher's edits)."""
    from backend.generation.slot.part import ParentContext
    from backend.generation.slot.selection import auto_links

    part = part_of(target, ParentContext(target.slot_set))
    links = auto_links(part.parent, part.address, part.parent.symbol(part.address.symbol_id))
    diff = target.linked_checks_diff or {}
    checks = sorted((set(links.checks) | set(diff.get("added", []))) - set(diff.get("removed", [])))
    target.linked_checks = checks
    target.linked_probes = links.probes
    target.save(update_fields=["linked_checks", "linked_probes"])
    queue_validation(target, user=user)
    return target


def target_usable(target: SlotTarget) -> tuple[bool, str]:
    """Whether a study may use the target, and why not."""
    from backend.generation.slot.leak import blocks_use

    status = (target.validation or {}).get("status", "pending")
    if status not in {"ok", "uninformative"}:
        return False, f"validation is {status}"
    if blocks_use(target.leak):
        return False, "the original leaks verbatim into the context or workspace"
    return True, ""


def validation_issues(target: SlotTarget, study_kind: str = "exploratory") -> list[Issue]:
    """``slot_tests_uninformative``: no discriminating check at all, or a linked endpoint that kept none."""
    validation = TargetValidation.model_validate(target.validation or {})
    if validation.status == "uninformative":
        return [
            make_issue(
                "slot_tests_uninformative",
                study_kind,  # type: ignore[arg-type]
                message="no linked check discriminates between the original and the holed tree",
                data={"target": str(target.pk), "dropped_checks": validation.dropped_checks},
            ),
        ]
    if validation.status != "ok":
        return []
    from backend.generation.evaluation import acceptance

    issues: list[Issue] = []
    discriminating = set(validation.discriminating_checks)
    for endpoint in target.linked_endpoints or []:
        checks = set(acceptance.checks_for_endpoints(target.slot_set.task_version, [endpoint]))
        if checks and not checks & discriminating:
            issues.append(
                make_issue(
                    "slot_tests_uninformative",
                    study_kind,  # type: ignore[arg-type]
                    message=f"linked endpoint {endpoint} kept no discriminating check",
                    data={"target": str(target.pk), "endpoint": endpoint},
                ),
            )
    return issues


def reference_outcome_ok(run: GenerationRun) -> bool:
    return run.outcome == Outcome.TESTED
