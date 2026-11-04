"""Slot tests: restricted suite and probes; ``full``, ``overlay`` or ``static_only`` (spec section 7.8).

``evaluate_variant(run)`` is what the eval stage calls for every run with a slot target: variants
(``slot_single_shot`` / ``slot_agent``), the original (variant 0) and the validation reference runs.

* **Selection.** Variants and the original run the target's discriminating checks (``--only``) plus its
  linked probes at ``functional`` and above; with ``evaluation.slot.regression`` the whole parent suite runs
  and the slot pass rate is read from the discriminating subset. Validation runs evaluate the candidate
  selection recorded in ``validation.original_pass`` / ``probe_stability``. The tester's always-on smoke
  subset (health) is the suite's business.
* **Mode.** ``full`` builds the assembled tree (``sample_builds`` on the parent's layers); ``overlay`` runs the
  parent image with the slot files copied in, only for interpreted backends (python, node), a variant with no
  new dependency and no frontend file that needs a build, else it falls back to ``full`` (recorded in
  ``run.execution["slot_eval"]``); ``static_only`` has no runtime (runtime features are "not measured").
* **Reuse.** With ``caches.reuse_eval_by_tree`` an identical tree under the same suite, check selection,
  probes, eval level and mode reuses that evaluation (a new row with ``reused_from``; note ``eval_reused``).
  Validation runs never reuse.
* **Live features.** After a study variant's evaluation, ``patterns.live.score_variant(run)`` scores the study's
  ``slot_primary_features`` (provisional hits for ``ci_width_reached``).

``slot_pass_rate`` = passed / discriminating checks of the latest evaluation (``None`` for uninformative
targets and for anything not measured: never 0 for a missing measurement).
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Any
from typing import Final

from backend.generation.contracts import Ref
from backend.generation.models import EvalSuite
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import RunFile
from backend.generation.models import SlotTarget
from backend.generation.models.choices import EvalMode
from backend.generation.models.choices import EvaluationStatus
from backend.generation.models.choices import FileOrigin
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import ReferenceOf
from backend.generation.models.choices import RunKind

__all__ = [
    "PROBE_LEVELS",
    "Selection",
    "evaluate_original",
    "evaluate_variant",
    "outcome_of",
    "overlay_fallback_reason",
    "selection_for",
    "slot_pass_rate",
]

PROBE_LEVELS: Final = frozenset({"functional", "security", "full"})
_INTERPRETED: Final = frozenset({"python", "javascript", "typescript"})
_FRONTEND_EXTS: Final = frozenset({".jsx", ".tsx", ".vue", ".svelte", ".css", ".scss", ".html"})
_FRONTEND_DIRS: Final = frozenset({"frontend", "client", "web", "ui"})
_VALIDATION_SUITE_KEY: Final = "slot_validation_suite"

logger = logging.getLogger(__name__)


class Selection:
    """What one evaluation of a slot run runs."""

    def __init__(
        self,
        *,
        only_checks: list[str] | None,
        probes: list[str] | None,
        discriminating: list[str],
        mode: str,
        eval_level: str,
        reuse: bool,
    ) -> None:
        self.only_checks = only_checks
        self.probes = probes
        self.discriminating = discriminating
        self.mode = mode
        self.eval_level = eval_level
        self.reuse = reuse


def _manifest(run: GenerationRun) -> dict[str, Any]:
    from backend.generation.models import RunManifest

    row = RunManifest.objects.filter(run=run).values_list("manifest", flat=True).first()
    return row or {}


def _suite_from_ref(ref: str) -> EvalSuite:
    parsed = Ref.parse(ref)
    qs = EvalSuite.objects.filter(slug=parsed.slug, version=parsed.version)
    if parsed.hash12:
        qs = qs.filter(content_hash__startswith=parsed.hash12)
    suite = qs.first()
    if suite is None:
        msg = f"eval suite {ref} not found"
        raise LookupError(msg)
    return suite


def _suite_for(run: GenerationRun, target: SlotTarget) -> EvalSuite:
    pinned = (run.execution or {}).get(_VALIDATION_SUITE_KEY)
    if pinned:
        return _suite_from_ref(pinned)
    planned = (_manifest(run).get("planned_evaluation") or {}).get("eval_suite") or {}
    if planned.get("ref"):
        ref = planned["ref"]
        if "#" not in ref and planned.get("content_hash"):
            ref = f"{ref}#{planned['content_hash'][:12]}"
        return _suite_from_ref(ref)
    if target.slot_set.parent_eval is not None:
        return target.slot_set.parent_eval.eval_suite
    msg = f"run {run.pk} has no planned eval suite and its parent no evaluation"
    raise LookupError(msg)


def _design_evaluation(run: GenerationRun) -> dict[str, Any]:
    if run.study_id is None:
        return {}
    return dict((run.study.design or {}).get("evaluation") or {})


def _effective_policy(run: GenerationRun) -> dict[str, Any]:
    effective = ((_manifest(run).get("resources") or {}).get("effective")) or {}
    return effective.get("content", effective) if isinstance(effective, dict) else {}


def selection_for(run: GenerationRun) -> Selection:
    """The checks, probes, mode and reuse rule of one evaluation of a slot run."""
    target = run.slot_target
    if target is None:
        msg = f"run {run.pk} has no slot target"
        raise ValueError(msg)
    validation = target.validation or {}
    if run.kind == RunKind.REFERENCE and run.reference_of in {ReferenceOf.SLOT_ORIGINAL, ReferenceOf.SLOT_HOLED}:
        if str(run.pk) in validation.get("runs", []):
            checks = list((validation.get("original_pass") or {}).keys())
            probes = list((validation.get("probe_stability") or {}).keys())
            return Selection(
                only_checks=checks,
                probes=probes,
                discriminating=checks,
                mode=EvalMode.FULL,
                eval_level="security",
                reuse=False,
            )
    planned = _manifest(run).get("planned_evaluation") or {}
    design = _design_evaluation(run)
    eval_level = planned.get("eval_level") or design.get("eval_level") or "security"
    slot_spec = design.get("slot") or {}
    mode = planned.get("mode") or slot_spec.get("mode") or EvalMode.FULL
    discriminating = list(validation.get("discriminating_checks") or []) if validation.get("status") == "ok" else []
    only = None if slot_spec.get("regression") else discriminating
    probes = list(target.linked_probes or []) if eval_level in PROBE_LEVELS else []
    caches = _effective_policy(run).get("caches") or {}
    return Selection(
        only_checks=only,
        probes=probes,
        discriminating=discriminating,
        mode=mode,
        eval_level=eval_level,
        reuse=bool(caches.get("reuse_eval_by_tree", True)) and run.kind != RunKind.REFERENCE,
    )


def _is_frontend_file(path: str, stack_content: dict[str, Any]) -> bool:
    pure = PurePosixPath(path)
    if pure.suffix.lower() in _FRONTEND_EXTS:
        return True
    has_frontend = ((stack_content.get("frontend") or {}).get("framework") or "none") != "none"
    return has_frontend and bool({part.lower() for part in pure.parts[:-1]} & _FRONTEND_DIRS)


def overlay_fallback_reason(run: GenerationRun, slot_paths: list[str], parent_image: str) -> str | None:
    """Why ``overlay`` cannot be used for this variant (``None`` when it can; section 4.8)."""
    stack = run.parent_run.stack_profile if run.parent_run is not None else None
    content: dict[str, Any] = (stack.content if stack is not None else {}) or {}
    language = (content.get("backend") or {}).get("language")
    if language not in _INTERPRETED:
        return f"backend language {language or 'unknown'} needs a build"
    result = getattr(run, "variant_result", None)
    if result is not None and result.new_dependencies:
        return "the variant adds dependencies"
    if any(_is_frontend_file(path, content) for path in slot_paths):
        return "a slot file is a frontend file that needs a build"
    if not parent_image:
        return "the parent has no built image"
    return None


def _parent_image(target: SlotTarget) -> str:
    parent_eval = target.slot_set.parent_eval
    if parent_eval is not None and parent_eval.image_id:
        return parent_eval.image_id
    latest = (
        RunEvaluation.objects.filter(run_id=target.slot_set.parent_run_id, retest_of__isnull=True)
        .exclude(image_id="")
        .order_by("-created_at")
        .first()
    )
    return latest.image_id if latest is not None else ""


def _record_mode(run: GenerationRun, requested: str, used: str, reason: str | None) -> None:
    execution = dict(run.execution or {})
    execution["slot_eval"] = {"requested_mode": requested, "mode": used, "fallback_reason": reason}
    run.execution = execution
    GenerationRun.objects.filter(pk=run.pk).update(execution=execution)


def _reuse(run: GenerationRun, suite: EvalSuite, selection: Selection, mode: str) -> RunEvaluation | None:
    """The full reuse key (WP-09): check selection, probes, eval level and the mode actually used."""
    from backend.generation.evaluation import reuse

    found = reuse.find_reusable(
        run,
        suite,
        only_checks=selection.only_checks,
        probes=selection.probes,
        eval_level=selection.eval_level,
        mode=mode,
    )
    if found is None:
        return None
    if found.run.slot_target_id not in {run.slot_target_id, None}:
        return None  # an identical tree of another target: its probes differ
    return RunEvaluation.objects.create(
        run=run,
        eval_suite=found.eval_suite,
        eval_level=found.eval_level,
        mode=found.mode,
        status=found.status,
        stages=found.stages,
        result=found.result,
        adherence=found.adherence,
        build_key=found.build_key,
        image_id=found.image_id,
        reused_from=found.reused_from or found,
        baseline=found.baseline,
    )


def _note_reused(run: GenerationRun) -> None:
    validity = dict(run.validity or {})
    notes = list(validity.get("notes") or [])
    if not any(note.get("code") == "eval_reused" for note in notes):
        notes.append({"code": "eval_reused", "level": "info"})
    validity["notes"] = notes
    run.validity = validity
    GenerationRun.objects.filter(pk=run.pk).update(validity=validity)


def _live_score(run: GenerationRun) -> None:
    if run.study_id is None or run.kind not in {RunKind.SLOT_SINGLE_SHOT, RunKind.SLOT_AGENT}:
        return
    if not (run.study.design or {}).get("slot_primary_features"):
        return
    from backend.generation.patterns import live

    try:
        live.score_variant(run)
    except Exception:
        # Live scores are provisional (the PatternRun recomputes everything); a scorer failure must not turn the
        # variant's evaluation into an executor crash (``infra_error:eval_lost``).
        logger.exception("live pattern scoring failed for run %s", run.pk)


def evaluate_variant(run: GenerationRun, *, mode: str | None = None) -> RunEvaluation:
    """Evaluate a variant (or the original, or a validation run) with the target's check selection."""
    from backend.generation.evaluation import suite as suite_mod

    if run.outcome == Outcome.OUTPUT_INVALID:
        msg = f"run {run.pk} is output_invalid: it is never built (section 4.8)"
        raise ValueError(msg)
    target = run.slot_target
    if target is None:
        msg = f"run {run.pk} has no slot target"
        raise ValueError(msg)
    selection = selection_for(run)
    suite = _suite_for(run, target)
    requested = mode or selection.mode
    used, reason = requested, None
    image_override: str | None = None
    overlay_files: dict[str, str] | None = None
    if requested == EvalMode.OVERLAY:
        rows = list(RunFile.objects.filter(run=run, origin=FileOrigin.SLOT).values_list("path", "blob"))
        image = _parent_image(target)
        reason = overlay_fallback_reason(run, [path for path, _ in rows], image)
        if reason is None:
            image_override, overlay_files = image, dict(rows)
        else:
            used = EvalMode.FULL
    if requested != selection.mode or reason is not None or requested == EvalMode.OVERLAY:
        _record_mode(run, requested, used, reason)
    if selection.reuse:
        reused = _reuse(run, suite, selection, used)
        if reused is not None:
            _note_reused(run)
            _live_score(run)
            return reused
    evaluation = suite_mod.evaluate(
        run,
        suite,
        only_checks=selection.only_checks,
        probes=selection.probes,
        mode=used,
        image_override=image_override,
        overlay_files=overlay_files,
    )
    _live_score(run)
    return evaluation


def evaluate_original(target: SlotTarget) -> RunEvaluation:
    """Evaluate the original (variant 0) with the same selection the variants get."""
    from backend.generation.evaluation import suite as suite_mod

    original = target.original_run
    if original is None:
        msg = f"target {target.pk} has no original run yet (validate it first)"
        raise ValueError(msg)
    validation = target.validation or {}
    discriminating = list(validation.get("discriminating_checks") or []) if validation.get("status") == "ok" else []
    suite = (
        target.slot_set.parent_eval.eval_suite
        if target.slot_set.parent_eval is not None
        else _suite_for(original, target)
    )
    return suite_mod.evaluate(
        original,
        suite,
        only_checks=discriminating,
        probes=list(target.linked_probes or []),
        mode=EvalMode.FULL,
    )


def slot_pass_rate(evaluation: RunEvaluation | None, target: SlotTarget) -> float | None:
    """passed / discriminating checks; ``None`` when uninformative or not measured (never 0 for "missing")."""
    validation = target.validation or {}
    discriminating = list(validation.get("discriminating_checks") or [])
    if validation.get("status") != "ok" or not discriminating or evaluation is None:
        return None
    if evaluation.status != EvaluationStatus.MEASURED or not (evaluation.result or {}).get("measured"):
        return None
    statuses = {check["id"]: check["status"] for check in (evaluation.result or {}).get("checks", [])}
    flaky = set((evaluation.result or {}).get("flaky_checks") or [])
    counted = [check for check in discriminating if check not in flaky]
    if not counted:
        return None
    return sum(1 for check in counted if statuses.get(check) == "pass") / len(counted)


def outcome_of(evaluation: RunEvaluation) -> tuple[str, str]:
    """``(outcome, reason)`` of a reference run from its evaluation (6.12, used by inline validation).

    Tester errors and unmeasured evaluations are ``infra_error``, never ``tested`` with a pass rate of 0.
    """
    stages = evaluation.stages or {}
    if any((stages.get(name) or {}).get("status") == "fail" for name in ("lock", "build")):
        return Outcome.BUILD_FAIL, ""
    if (stages.get("start") or {}).get("status") == "fail":
        return Outcome.START_FAIL, ""
    if evaluation.status == EvaluationStatus.MEASURED and (evaluation.result or {}).get("measured"):
        return Outcome.TESTED, ""
    if evaluation.status == EvaluationStatus.NOT_MEASURED:
        return Outcome.INFRA_ERROR, "not_measured"
    return Outcome.INFRA_ERROR, "evaluation_error"
