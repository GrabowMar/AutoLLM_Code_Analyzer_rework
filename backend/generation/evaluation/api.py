"""Evaluation API (spec section 9.9), mounted under ``/api/generation/``.

| Method | Path | Operation id | Response |
|---|---|---|---|
| GET | ``/eval/suites/{ref}/checks/?task=&auth_mode=&stack=`` | ``eval_suite_checks`` | compiled plan preview |
| GET | ``/eval/baselines/?task=&starter=`` | ``eval_baselines`` | ``[EvalBaselineOut]`` |
| GET | ``/eval/probes/catalog/`` | ``eval_probe_catalog`` | ``[{kind, cwe, requires, description, validated}]`` |
| GET | ``/eval/conformance/`` | ``eval_conformance`` | ``[{framework, tasks: [{task, status, runs}], ok}]`` |
| GET | ``/eval/retest/?study=`` | ``eval_retest`` | ``{n, check_agreement, run_agreement, kappa}`` |

The plan preview never contains a secret (``ADMIN_PASSWORD``/``WEBHOOK_SECRET`` reach the tester through its
environment only). Static paths are registered before parameterised ones.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest  # noqa: TC002 - ninja reads the annotations at runtime
from ninja import Router
from ninja import Schema
from ninja import Status

router = Router(tags=["generation: eval"])


class ErrorOut(Schema):
    detail: str
    code: str = "error"


class EvalBaselineOut(Schema):
    id: int
    starter: str
    task: str
    auth_mode: str
    eval_suite: str
    agent_pass_rate: float
    checks_passed: list[str]
    reference_run: str
    created_at: str


class ProbeCatalogOut(Schema):
    kind: str
    cwe: str
    requires: dict[str, Any]
    description: str
    validated: bool


class ConformanceTaskOut(Schema):
    task: str
    status: str
    runs: list[str]


class ConformanceOut(Schema):
    framework: str
    tasks: list[ConformanceTaskOut]
    ok: bool


class RetestOut(Schema):
    n: int
    check_agreement: float | None
    run_agreement: float | None
    kappa: float | None


class PlanPreviewOut(Schema):
    suite: str
    task: str
    auth_mode: str
    stack: str | None
    plan: dict[str, Any]
    n_checks: int
    n_probes: int
    disabled_probes: list[str]


def _user(request: HttpRequest) -> Any:
    return getattr(request, "auth", None) or request.user


def _resolve(ref: str, kind: str, request: HttpRequest) -> Any:
    from backend.generation.library import service

    return service.resolve_ref(ref, kind=kind, user=_user(request))


@router.get("/eval/probes/catalog/", response=list[ProbeCatalogOut], operation_id="eval_probe_catalog")
def eval_probe_catalog(request: HttpRequest) -> Any:
    """Every probe kind with its CWE, ``requires`` tags and whether a mutant pair validated it."""
    from backend.generation.evaluation import probes

    return probes.catalog_rows()


@router.get("/eval/conformance/", response=list[ConformanceOut], operation_id="eval_conformance")
def eval_conformance(request: HttpRequest) -> Any:
    """Tester conformance kit status per backend framework (spec 8.5)."""
    from backend.generation.evaluation import conformance

    return conformance.status_rows()


@router.get("/eval/retest/", response={200: RetestOut, 404: ErrorOut}, operation_id="eval_retest")
def eval_retest(request: HttpRequest, study: str) -> Any:
    """Test-retest agreement of a study (rates are ``null`` when nothing was compared, never 0)."""
    from django.core.exceptions import ValidationError

    from backend.generation.evaluation.retest import retest_agreement
    from backend.generation.models import Study

    try:
        row = Study.objects.filter(pk=study).first()
    except (ValueError, ValidationError):
        row = None
    if row is None:
        return Status(404, {"detail": f"no study {study}", "code": "not_found"})
    return retest_agreement(row)


@router.get(
    "/eval/baselines/",
    response={200: list[EvalBaselineOut], 400: ErrorOut, 404: ErrorOut},
    operation_id="eval_baselines",
)
def eval_baselines(request: HttpRequest, task: str | None = None, starter: str | None = None) -> Any:
    """Starter baselines, filtered by task (``slug@v``) and/or starter (``slug@v``)."""
    from backend.generation.library.service import LibraryError
    from backend.generation.models import EvalBaseline

    qs = EvalBaseline.objects.select_related("starter", "task_version__task", "eval_suite").order_by("-created_at")
    try:
        if task:
            qs = qs.filter(task_version=_resolve(task, "task", request))
        if starter:
            qs = qs.filter(starter=_resolve(starter, "starter", request))
    except LibraryError as exc:
        return Status(exc.status_code, {"detail": str(exc), "code": "not_found"})
    return [
        {
            "id": row.pk,
            "starter": row.starter.pinned_ref,
            "task": row.task_version.pinned_ref,
            "auth_mode": row.auth_mode,
            "eval_suite": row.eval_suite.pinned_ref,
            "agent_pass_rate": row.agent_pass_rate,
            "checks_passed": list(row.checks_passed or []),
            "reference_run": str(row.reference_run_id),
            "created_at": row.created_at.isoformat(),
        }
        for row in qs[:500]
    ]


@router.get(
    "/eval/suites/{ref}/checks/",
    response={200: PlanPreviewOut, 400: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    operation_id="eval_suite_checks",
)
def eval_suite_checks(
    request: HttpRequest,
    ref: str,
    task: str,
    auth_mode: str = "contract_bearer",
    stack: str | None = None,
) -> Any:
    """The compiled ``AcceptancePlan`` (checks and probes) of a task under a suite."""
    from backend.generation.evaluation import acceptance
    from backend.generation.library.service import LibraryError

    if auth_mode not in acceptance.AUTH_MODES:
        return Status(
            400,
            {"detail": f"auth_mode must be one of {', '.join(acceptance.AUTH_MODES)}", "code": "invalid"},
        )
    try:
        suite = _resolve(ref, "eval_suite", request)
        task_version = _resolve(task, "task", request)
        stack_row = _resolve(stack, "stack", request) if stack else None
    except LibraryError as exc:
        return Status(
            exc.status_code if exc.status_code in (400, 404, 409) else 404,
            {"detail": str(exc), "code": "not_found"},
        )
    probes = str((suite.content or {}).get("probes") or "task")
    plan = acceptance.compile(task_version, auth_mode, stack_row, probes=probes)
    data = acceptance.plan_json(plan)
    disabled = [p["id"] for p in data["probes"] if (p.get("params") or {}).get("disabled")]
    return {
        "suite": suite.pinned_ref,
        "task": task_version.pinned_ref,
        "auth_mode": auth_mode,
        "stack": stack_row.pinned_ref if stack_row is not None else None,
        "plan": data,
        "n_checks": len(data["checks"]),
        "n_probes": len(data["probes"]),
        "disabled_probes": disabled,
    }
