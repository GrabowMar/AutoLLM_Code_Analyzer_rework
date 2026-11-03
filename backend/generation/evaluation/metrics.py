"""Evaluation metrics (spec 8.1 rules) and ``RunMetrics`` with the latest evaluation mirrored.

``compute(...)`` turns tester rows into ``EvalMetrics``:

* ``agent_pass_rate`` = passed / counted checks. Counted: every non-``infra`` check of the selection, minus the
  suite's ``flaky_checks``. ``error`` (an app connection failure) and ``skipped`` (a ``depends_on`` failed) count
  as fail. No counted check gives ``None``, never 0.
* ``over_baseline`` = ``agent_pass_rate - baseline_pass_rate`` when both exist.
* ``probes_tested`` counts only probes that reached the attack (``exploited``/``not_exploited``);
  ``sec_pass`` = every counted check passed and no tested probe was exploited, ``None`` when probes were off or
  none was tested.
* ``stated_pass_rate`` / ``unstated_pass_rate``: checks whose ``stated_in`` includes the run's prose variant,
  and the others (8.2). ``common_pass_rate`` needs the study's auth modes and is filled by the results port.

``run_metrics(run)`` is the documented ``RunMetrics`` of a run with ``eval`` mirrored from its latest
evaluation (retests excluded).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from backend.generation.contracts.evaluation import EvalMetrics
from backend.generation.contracts.outcome import EvalSummary
from backend.generation.contracts.outcome import RunMetrics

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping

    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation

__all__ = ["compute", "eval_summary", "rate", "run_metrics"]


def rate(rows: Iterable[Mapping[str, Any]]) -> float | None:
    items = list(rows)
    return (sum(1 for r in items if r.get("status") == "pass") / len(items)) if items else None


def compute(
    checks: list[Mapping[str, Any]],
    probes: list[Mapping[str, Any]],
    *,
    flaky: Iterable[str] = (),
    only_checks: Iterable[str] | None = None,
    prose_variant: str | None = None,
    baseline_pass_rate: float | None = None,
    probes_on: bool = True,
) -> EvalMetrics:
    flaky_set = set(flaky)
    selected = set(only_checks) if only_checks is not None else None
    infra = [c for c in checks if c.get("category") == "infra"]
    counted = [
        c
        for c in checks
        if c.get("category") != "infra" and c["id"] not in flaky_set and (selected is None or c["id"] in selected)
    ]
    passed = sum(1 for c in counted if c.get("status") == "pass")
    by_category: dict[str, dict[str, int]] = {}
    for c in counted:
        row = by_category.setdefault(str(c.get("category")), {"n": 0, "passed": 0})
        row["n"] += 1
        row["passed"] += int(c.get("status") == "pass")
    agent_rate = (passed / len(counted)) if counted else None
    tested = [p for p in probes if p.get("status") in ("exploited", "not_exploited")]
    exploited = sum(1 for p in tested if p.get("status") == "exploited")
    skipped = sum(1 for p in probes if p.get("status") == "skipped")
    sec_pass: bool | None = None
    if probes_on and tested:
        sec_pass = bool(counted) and passed == len(counted) and exploited == 0
    stated = unstated = None
    if prose_variant:
        stated = rate(c for c in counted if prose_variant in (c.get("stated_in") or []))
        unstated = rate(c for c in counted if prose_variant not in (c.get("stated_in") or []))
    over = (agent_rate - baseline_pass_rate) if agent_rate is not None and baseline_pass_rate is not None else None
    return EvalMetrics(
        infra_ok=bool(infra) and all(c.get("status") == "pass" for c in infra),
        agent_checks=len(counted),
        agent_passed=passed,
        agent_pass_rate=agent_rate,
        by_category=by_category,
        baseline_pass_rate=baseline_pass_rate,
        over_baseline=over,
        probes_tested=len(tested),
        probes_exploited=exploited,
        probes_skipped=skipped,
        sec_pass=sec_pass,
        stated_pass_rate=stated,
        unstated_pass_rate=unstated,
    )


def eval_summary(evaluation: RunEvaluation | None, *, slot_pass_rate: float | None = None) -> EvalSummary | None:
    """``GenerationRun.metrics["eval"]``: the mirror of one evaluation (``None`` when there is none)."""
    if evaluation is None:
        return None
    result = evaluation.result if isinstance(evaluation.result, dict) else {}
    metrics = result.get("metrics") or {}
    measured = bool(result.get("measured")) and evaluation.status == "measured"
    adherence = (evaluation.adherence or {}).get("status") if isinstance(evaluation.adherence, dict) else None
    return EvalSummary(
        eval_suite=evaluation.eval_suite.pinned_ref,
        eval_level=evaluation.eval_level,
        status=evaluation.status,
        measured=measured,
        agent_pass_rate=metrics.get("agent_pass_rate") if measured else None,
        baseline_pass_rate=metrics.get("baseline_pass_rate") if measured else None,
        over_baseline=metrics.get("over_baseline") if measured else None,
        probes_tested=int(metrics.get("probes_tested") or 0) if measured else 0,
        probes_exploited=int(metrics.get("probes_exploited") or 0) if measured else 0,
        sec_pass=metrics.get("sec_pass") if measured else None,
        slot_pass_rate=slot_pass_rate if measured else None,
        adherence=adherence,
        evaluation_id=evaluation.pk,
    )


def run_metrics(run: GenerationRun) -> RunMetrics:
    """The documented metric keys of a run, with the latest evaluation mirrored."""
    from backend.generation.models import RunEvaluation

    known = set(RunMetrics.model_fields)
    base = {k: v for k, v in (run.metrics or {}).items() if k in known and k != "eval"}
    latest = (
        RunEvaluation.objects.filter(run=run, retest_of__isnull=True)
        .select_related("eval_suite")
        .order_by("-created_at", "-pk")
        .first()
    )
    previous = (run.metrics or {}).get("eval") or {}
    summary = eval_summary(latest, slot_pass_rate=previous.get("slot_pass_rate"))
    return RunMetrics.model_validate({**base, "eval": summary.model_dump(mode="json") if summary else None})
