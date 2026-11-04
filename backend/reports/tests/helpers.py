"""Builders shared by the reports and export tests: v3 runs with a collected tree, analyses and study cells."""

from __future__ import annotations

from typing import Any

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation import blobs
from backend.generation.models import choices
from backend.generation.testing import content
from backend.generation.testing import factories
from backend.reports.services.loc import source_loc

# 1,000 non-blank, non-comment lines of Python: the model's whole app in most tests.
CODE_1000 = "x = 1\n" * 1000
APP_PATH = "app.py"


def add_file(run, path: str, text: str, origin: str = choices.FileOrigin.AUTHORED, **extra: Any):
    """A RunFile with a real blob; ``loc`` is the file's source LOC unless given."""
    extra.setdefault("loc", source_loc(path, text))
    extra.setdefault("is_test", False)
    return factories.RunFileFactory(
        run=run,
        path=path,
        blob=blobs.put_text(text),
        size=len(text.encode()),
        origin=origin,
        **extra,
    )


def make_run(
    user,
    model,
    task_version=None,
    *,
    status: str = choices.RunStatus.COMPLETED,
    code: str | None = CODE_1000,
    path: str = APP_PATH,
    files: dict[str, str] | None = None,
    **kwargs: Any,
):
    """An ``app_agentic`` run of *model* on *task_version* with its tree collected (*code* at *path*).

    Completed runs are ``tested`` and usable unless ``outcome``/``usable`` are given; pending and running
    runs have no outcome; failed runs are infra errors.
    """
    if status == choices.RunStatus.COMPLETED:
        kwargs.setdefault("outcome", choices.Outcome.TESTED)
        kwargs.setdefault("usable", kwargs["outcome"] not in (choices.Outcome.INFRA_ERROR, choices.Outcome.EXCLUDED))
    elif status == choices.RunStatus.FAILED:
        kwargs.setdefault("outcome", choices.Outcome.INFRA_ERROR)
        kwargs.setdefault("usable", False)
    collected = code is not None or bool(files)
    if collected and status in (choices.RunStatus.COMPLETED, choices.RunStatus.FAILED):
        kwargs.setdefault("tree_hash", content.fake_sha(f"tree:{path}:{len(code or '')}:{sorted(files or {})}"))
    run = factories.GenerationRunFactory.create(
        created_by=user,
        model=model,
        task_version=task_version if task_version is not None else factories.TaskSpecVersionFactory.create(),
        status=status,
        **kwargs,
    )
    if code is not None and collected and status != choices.RunStatus.PENDING:
        add_file(run, path, code)
    for file_path, text in (files or {}).items():
        add_file(run, file_path, text)
    return run


def analyze(run, tools: dict[str, list[dict]], *, statuses: dict[str, str] | None = None) -> AnalysisRun:
    """One finished analysis of *run*: per tool slug, its findings (Finding field dicts; ``file_path`` defaults
    to the run's app file)."""
    analysis = AnalysisRunFactory(
        created_by=run.created_by,
        generation_job=run,
        source_code={},
        code_scope="app",
        status=AnalysisRun.Status.COMPLETED,
    )
    for slug, findings in tools.items():
        status = (statuses or {}).get(slug, ToolResult.Status.COMPLETED)
        result = ToolResultFactory(run=analysis, tool_slug=slug, status=status)
        for f in findings:
            fields = {k: v for k, v in f.items() if k != "title"}
            fields.setdefault("file_path", APP_PATH)
            Finding.objects.create(result=result, title=f.get("title", "f"), **fields)
    return analysis


def high(n: int = 1, **extra: Any) -> list[dict[str, Any]]:
    return [{"severity": "high", **extra} for _ in range(n)]


def make_study(user, *, repeats: int = 1, **design: Any):
    """A study of *user* (its design names its own resource policy) and its launched plan."""
    policy = factories.ResourcePolicyFactory.create()
    study = factories.StudyFactory.create(
        created_by=user,
        resource_policy=policy,
        design=content.design_content(resource_policy=policy.ref, repeats=repeats, **design),
    )
    plan = factories.StudyPlanFactory.create(study=study)
    study.active_plan = plan
    study.save(update_fields=["active_plan"])
    return study, plan


def make_condition(plan, model, label: str | None = None, **extra: Any):
    kwargs: dict[str, Any] = {"plan": plan, "model": model, **extra}
    if label is not None:
        kwargs["label"] = label
    return factories.StudyConditionFactory.create(**kwargs)


def make_unit(plan, task_version=None):
    tv = task_version if task_version is not None else factories.TaskSpecVersionFactory.create()
    return factories.StudyUnitFactory.create(plan=plan, task_version=tv)


def make_cell(condition, unit, *, repeat_index: int = 0, stage: str = choices.Stage.MAIN, **extra: Any):
    return factories.StudyCellFactory.create(
        condition=condition,
        unit=unit,
        repeat_index=repeat_index,
        stage=stage,
        **extra,
    )


def cell_run(cell, user, *, attempt: int = 1, **kwargs: Any):
    """A run of *cell* (its study, condition, model, task and repeat), attempt *attempt*."""
    kwargs.setdefault("cell_attempt", attempt)
    return make_run(
        user,
        cell.condition.model,
        cell.unit.task_version,
        study=cell.study,
        condition=cell.condition,
        cell=cell,
        repeat_index=cell.repeat_index,
        stage=cell.stage,
        **kwargs,
    )


def evaluate(run, *, passed: int = 3, failed: int = 1, measured: bool = True, suite=None):
    """A final RunEvaluation of *run* and its mirror in ``run.metrics["eval"]``."""
    result = content.eval_result_content(passed=passed, failed=failed, measured=measured)
    status = choices.EvaluationStatus.MEASURED if measured else choices.EvaluationStatus.NOT_MEASURED
    evaluation = factories.RunEvaluationFactory.create(
        run=run,
        status=status,
        result=result,
        **({"eval_suite": suite} if suite is not None else {}),
    )
    rate = result["metrics"]["agent_pass_rate"]
    run.metrics = {
        **(run.metrics or {}),
        "eval": {
            "eval_suite": evaluation.eval_suite.ref,
            "eval_level": evaluation.eval_level,
            "status": status,
            "measured": measured,
            "agent_pass_rate": rate,
            "evaluation_id": evaluation.pk,
        },
    }
    run.save(update_fields=["metrics"])
    return evaluation
