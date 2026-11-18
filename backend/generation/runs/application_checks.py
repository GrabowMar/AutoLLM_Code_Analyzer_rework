"""Application deployment filters and read-only summaries of recorded task checks."""

from typing import Any

from django.db.models import Case
from django.db.models import CharField
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models import Subquery
from django.db.models import Value
from django.db.models import When

from backend.generation.models import RunEvaluation
from backend.runtime.models import ContainerInstance


def selected_evaluations():
    """Correlated equivalent of supersede.current_evaluation; pending rows are not current."""
    return RunEvaluation.objects.filter(
        run_id=OuterRef("pk"),
        retest_of__isnull=True,
        status__in=RunEvaluation.FINAL_STATUSES,
    ).order_by("-created_at", "-pk")


def with_deployment(runs):
    selected = selected_evaluations()
    return runs.annotate(
        _selected_evaluation=Subquery(selected.values("pk")[:1]),
        _selected_build=Subquery(selected.values("stages__build__status")[:1]),
        _selected_start=Subquery(selected.values("stages__start__status")[:1]),
    ).annotate(
        _deployment=Case(
            When(Q(_selected_build="fail") | Q(_selected_start="fail"), then=Value("failed")),
            When(_selected_start="ok", then=Value("started")),
            When(outcome__in=["output_invalid", "build_fail", "start_fail"], then=Value("failed")),
            When(Q(outcome="tested") & ~Q(outcome_reason__startswith="not_tested:"), then=Value("started")),
            default=Value("unknown"),
            output_field=CharField(),
        ),
    )


def with_preview_status(runs):
    return runs.annotate(
        _preview_running=Exists(
            ContainerInstance.objects.filter(generation_job_id=OuterRef("pk"), status="running"),
        ),
    )


def feature_check_summary(task: dict[str, Any], evaluation) -> dict[str, Any]:
    """Report failed checks and untested requirements; neither is proof that a feature is absent."""
    result = evaluation.result or {} if evaluation is not None else {}
    measured = bool(
        evaluation is not None and evaluation.status == "measured" and result and result.get("measured", True),
    )
    suite = evaluation.eval_suite.content or {} if evaluation is not None else {}
    flaky = set(suite.get("flaky_checks") or []) | set(result.get("flaky_checks") or [])
    checks = (
        [
            row
            for row in result.get("checks") or []
            if isinstance(row, dict) and row.get("category") != "infra" and row.get("id") not in flaky
        ]
        if measured
        else []
    )
    statuses = {
        name: sum(row.get("status") == name for row in checks) for name in ("pass", "fail", "error", "skipped")
    }
    uncovered = []
    for kind, key in (("endpoint", "endpoints"), ("ui", "ui"), ("acceptance", "acceptance")):
        for requirement in task.get(key) or []:
            if not isinstance(requirement, dict) or not requirement.get("id"):
                continue
            identity = requirement["id"]
            if kind == "endpoint":
                matched = [c for c in checks if c.get("endpoint") == identity]
            else:
                matched = [
                    c for c in checks if c.get("id") == identity or str(c.get("id", "")).startswith(identity + ".")
                ]
            if not any(c.get("status") in {"pass", "fail"} for c in matched):
                uncovered.append(
                    {
                        "id": identity,
                        "kind": kind,
                        "description": requirement.get("text")
                        or " ".join(str(requirement.get(k) or "") for k in ("method", "path")).strip()
                        or identity,
                    },
                )

    def brief(row):
        return {key: row.get(key) for key in ("id", "category", "endpoint", "status", "note")}

    return {
        "evaluation_id": evaluation.pk if evaluation is not None else None,
        "eval_suite": evaluation.eval_suite.ref if evaluation is not None else None,
        "evaluation_status": evaluation.status if evaluation is not None else "missing",
        "measured": measured,
        "checks": len(checks),
        "passed": statuses["pass"],
        "failed": statuses["fail"] + statuses["error"],
        "skipped": statuses["skipped"],
        "failed_checks": [brief(c) for c in checks if c.get("status") in {"fail", "error"}],
        "skipped_checks": [brief(c) for c in checks if c.get("status") == "skipped"],
        "uncovered_requirements": uncovered,
        "excluded_flaky_checks": sorted(flaky),
    }
