"""Random re-evaluation of a fraction of runs for test-retest agreement (spec section 8.5).

``select_for_retest`` picks a seeded random ``retest_fraction`` of a study's ``tested`` runs (deterministic
for the study's ``base_seed``). ``retest_agreement`` compares each retest (``retest_of`` set) with the
evaluation it repeats: per-check agreement on pass/fail, per-run agreement (all checks equal) and Cohen's
kappa over the paired check verdicts. Checks that are not pass/fail in either row (error, skipped) and
flaky checks are left out of the pairs; unmeasured rows are not compared.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import Study

__all__ = ["cohen_kappa", "retest_agreement", "select_for_retest"]


def select_for_retest(study: Study, fraction: float) -> list[GenerationRun]:
    """A seeded random ``fraction`` of the study's ``tested`` runs (at least one when any exist)."""
    from backend.generation.models import GenerationRun
    from backend.generation.models import choices

    runs = list(GenerationRun.objects.filter(study=study, outcome=choices.Outcome.TESTED, beyond_plan=False))
    if not runs or fraction <= 0:
        return []

    def rank(run: GenerationRun) -> str:
        return hashlib.sha256(f"retest:{study.base_seed}:{run.pk}".encode()).hexdigest()

    count = max(1, round(len(runs) * fraction))
    return sorted(runs, key=rank)[:count]


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa of paired binary verdicts; ``None`` when undefined (no pairs, or chance agreement 1)."""
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(1 for a, b in pairs if a == b) / n
    p_a = sum(1 for a, _ in pairs if a) / n
    p_b = sum(1 for _, b in pairs if b) / n
    expected = p_a * p_b + (1 - p_a) * (1 - p_b)
    if expected >= 1:
        return None
    return (observed - expected) / (1 - expected)


def _verdicts(result: Any) -> dict[str, bool] | None:
    if not isinstance(result, dict) or not result.get("measured"):
        return None
    flaky = set(result.get("flaky_checks") or [])
    return {
        check["id"]: check["status"] == "pass"
        for check in result.get("checks") or []
        if check.get("status") in ("pass", "fail") and check.get("id") not in flaky
    }


def retest_agreement(study: Study) -> dict[str, Any]:
    """``{n, check_agreement, run_agreement, kappa}``; rates are ``None`` (not 0) when nothing was compared."""
    from backend.generation.models import RunEvaluation

    retests = RunEvaluation.objects.filter(run__study=study, retest_of__isnull=False).select_related("retest_of")
    pairs: list[tuple[bool, bool]] = []
    runs_compared = runs_equal = 0
    for retest in retests:
        first, second = _verdicts(retest.retest_of.result), _verdicts(retest.result)
        if first is None or second is None:
            continue
        common = sorted(set(first) & set(second))
        if not common:
            continue
        runs_compared += 1
        run_pairs = [(first[cid], second[cid]) for cid in common]
        runs_equal += all(a == b for a, b in run_pairs)
        pairs.extend(run_pairs)
    return {
        "n": runs_compared,
        "check_agreement": (sum(1 for a, b in pairs if a == b) / len(pairs)) if pairs else None,
        "run_agreement": (runs_equal / runs_compared) if runs_compared else None,
        "kappa": cohen_kappa(pairs),
    }


def run_retests(study: Study, *, fraction: float | None = None, runtime: Any = None) -> list[int]:
    """Re-evaluate the selected runs under the suite of their first evaluation (``retest_of`` set).

    ``fraction`` defaults to the study's ``Design.evaluation.retest_fraction``. Runs already retested are
    skipped. Returns the new ``RunEvaluation`` ids. Retest rows never replace the run's evaluation: readers
    ignore ``retest_of`` rows except for agreement.
    """
    from backend.generation.evaluation import suite as suite_mod
    from backend.generation.models import RunEvaluation

    if fraction is None:
        fraction = float(((study.design or {}).get("evaluation") or {}).get("retest_fraction", 0.05))
    created: list[int] = []
    for run in select_for_retest(study, fraction):
        first = (
            RunEvaluation.objects.filter(run=run, retest_of__isnull=True, reused_from__isnull=True, status="measured")
            .order_by("created_at", "pk")
            .first()
        )
        if first is None or RunEvaluation.objects.filter(retest_of=first).exists():
            continue
        detail = ((first.stages or {}).get("tester") or {}).get("detail") or {}
        row = suite_mod.evaluate(
            run,
            first.eval_suite,
            only_checks=detail.get("only_checks"),
            probes=detail.get("probes"),
            mode=first.mode,
            eval_level=first.eval_level,
            runtime=runtime,
            retest_of=first,
        )
        created.append(row.pk)
    return created
