"""Newest unique runs across studies (``reports.services.cohort_selection``) and the cross-study study report."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models import choices
from backend.generation.testing import content
from backend.generation.testing import factories
from backend.llm_models.tests.factories import LLMModelFactory
from backend.reports.models import Report
from backend.reports.services import renderers
from backend.reports.services import study_report
from backend.reports.services.cohort_selection import select_latest_unique
from backend.reports.services.cohort_selection import selection_warnings
from backend.reports.services.cohort_selection import validate_selection_config
from backend.reports.services.report_service import _make_summary
from backend.reports.tests.helpers import analyze
from backend.reports.tests.helpers import cell_run
from backend.reports.tests.helpers import evaluate
from backend.reports.tests.helpers import make_cell
from backend.reports.tests.helpers import make_condition
from backend.reports.tests.helpers import make_run
from backend.reports.tests.helpers import make_study
from backend.reports.tests.helpers import make_unit
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

T0 = timezone.now() - timedelta(days=30)
A, B, C = "vendor/model-a", "vendor/model-b", "vendor/model-c"
# OpenRouter list prices (USD per token) and a run's token totals: the cost at list price of the final assessment.
LIST_PRICING = {"prompt": "0.000001", "completion": "0.000002"}
TOKENS = {"prompt_tokens": 10_000, "completion_tokens": 1_000}


class Pool:
    """Studies of one user, built cell by cell; every run is created one minute after the previous one."""

    def __init__(self, user=None):
        self.user = user or UserFactory()
        self.models: dict = {}
        self.tasks: dict = {}
        self.conditions: dict = {}
        self.units: dict = {}
        self.cells: dict = {}
        self.minute = 0

    def model(self, model_id):
        if model_id not in self.models:
            self.models[model_id] = LLMModelFactory(model_id=model_id)
        return self.models[model_id]

    def task(self, slug):
        if slug not in self.tasks:
            self.tasks[slug] = factories.TaskSpecVersionFactory.create(
                task=factories.TaskSpecFactory.create(slug=slug),
            )
        return self.tasks[slug]

    def study(self, name, status=choices.StudyStatus.COMPLETED):
        study, plan = make_study(self.user)
        Study.objects.filter(pk=study.pk).update(name=name, status=status)
        study.refresh_from_db()
        return study, plan

    def cell(self, plan, model_id, task, stage=choices.Stage.MAIN):
        key = (plan.pk, model_id)
        if key not in self.conditions:
            self.conditions[key] = make_condition(plan, self.model(model_id), label=f"{model_id} @ {plan.pk}")
        if (plan.pk, task) not in self.units:
            self.units[(plan.pk, task)] = make_unit(plan, self.task(task))
        cell_key = (key, task, stage)
        if cell_key not in self.cells:
            self.cells[cell_key] = make_cell(self.conditions[key], self.units[(plan.pk, task)], stage=stage)
        return self.cells[cell_key]

    def run(self, plan, model_id, task, *, stage=choices.Stage.MAIN, **kwargs):
        cell = self.cell(plan, model_id, task, stage)
        attempt = GenerationRun.objects.filter(cell=cell).count() + 1
        run = cell_run(cell, self.user, attempt=attempt, **kwargs)
        self.minute += 1
        GenerationRun.objects.filter(pk=run.pk).update(created_at=T0 + timedelta(minutes=self.minute))
        run.refresh_from_db()
        return run


def _config(*studies, **extra):
    return {"selection": "latest_unique", "study_ids": [str(s.pk) for s in studies], "bootstrap": False, **extra}


def _cell(summary, group, task):
    return next(c for c in summary["matrix"] if c["group"] == group and c["task"] == task)


def _infra(**kwargs):
    return {"status": choices.RunStatus.FAILED, **kwargs}


# ── selection rule ──────────────────────────────────────────────────────


def test_newest_usable_run_wins_across_two_studies():
    pool = Pool()
    s1, p1 = pool.study("first")
    s2, p2 = pool.study("second")
    old = pool.run(p1, A, "todo")
    new = pool.run(p2, A, "todo")

    selection = select_latest_unique(_config(s1, s2), pool.user)

    assert [r.pk for r in selection.runs] == [new.pk]
    cell = _cell(selection.summary, A, "todo@1")
    assert (cell["status"], cell["run_id"], cell["study_id"], cell["study_name"]) == (
        "selected",
        str(new.pk),
        str(s2.pk),
        "second",
    )
    assert cell["attempts"] == 2
    assert cell["superseded_run_ids"] == [str(old.pk)]
    [superseded] = selection.summary["superseded"]
    assert superseded["run_id"] == str(old.pk)
    assert superseded["reason"] == "newer_usable_run"
    assert superseded["replaced_by"] == str(new.pk)
    assert superseded["study_name"] == "first"
    studies = {s["name"]: s for s in selection.summary["studies"]}
    assert (studies["first"]["runs_considered"], studies["first"]["runs_selected"]) == (1, 0)
    assert (studies["second"]["runs_considered"], studies["second"]["runs_selected"]) == (1, 1)


def test_ties_on_creation_time_break_by_run_id():
    pool = Pool()
    s1, p1 = pool.study("first")
    s2, p2 = pool.study("second")
    runs = [pool.run(p1, A, "todo"), pool.run(p2, A, "todo")]
    GenerationRun.objects.filter(pk__in=[r.pk for r in runs]).update(created_at=T0)

    selection = select_latest_unique(_config(s1, s2), pool.user)

    assert str(selection.runs[0].pk) == max(str(r.pk) for r in runs)


def test_newer_unusable_attempt_never_replaces_a_usable_run():
    pool = Pool()
    study, plan = pool.study("final")
    usable = pool.run(plan, A, "todo")
    infra = pool.run(plan, A, "todo", **_infra())
    excluded = pool.run(
        plan,
        A,
        "todo",
        status=choices.RunStatus.CANCELLED,
        outcome=choices.Outcome.EXCLUDED,
        usable=False,
        code=None,
    )

    selection = select_latest_unique(_config(study), pool.user)

    assert [r.pk for r in selection.runs] == [usable.pk]
    reasons = {s["run_id"]: (s["reason"], s["replaced_by"]) for s in selection.summary["superseded"]}
    assert reasons == {
        str(infra.pk): ("unusable_attempt", str(usable.pk)),
        str(excluded.pk): ("unusable_attempt", str(usable.pk)),
    }
    # The newest attempt is listed first.
    assert _cell(selection.summary, A, "todo@1")["superseded_run_ids"] == [str(excluded.pk), str(infra.pk)]


def test_attrition_missing_and_in_flight_cells():
    pool = Pool()
    study, plan = pool.study("final")
    pool.run(plan, A, "t1")
    pool.run(plan, A, "t2", **_infra())
    newest_infra = pool.run(plan, A, "t2", **_infra())
    pool.run(plan, A, "t3", status=choices.RunStatus.RUNNING, code=None)
    pool.run(plan, B, "t1")  # B never attempted t2 or t3

    selection = select_latest_unique(_config(study), pool.user)
    summary = selection.summary

    assert _cell(summary, A, "t1@1")["status"] == "selected"
    attrition = _cell(summary, A, "t2@1")
    assert (attrition["status"], attrition["run_id"], attrition["outcome"]) == (
        "attrition",
        str(newest_infra.pk),
        "infra_error",
    )
    assert _cell(summary, A, "t3@1")["status"] == "in_flight"
    assert _cell(summary, B, "t2@1")["status"] == "missing"
    assert _cell(summary, B, "t2@1")["run_id"] is None
    counts = summary["counts"]
    assert (counts["attrition_cells"], counts["missing_cells"], counts["in_flight_cells"]) == (1, 2, 1)
    assert dict(selection.attrition) == {"infra_error": 1}
    assert [r.status for r in selection.in_flight] == [choices.RunStatus.RUNNING]
    # Every candidate is accounted for: sampled, superseded, or the recorded attempt of a cell without one.
    assert counts["candidates"] == 5
    assert counts["candidates"] == (
        counts["selected"]
        + counts["dropped_runs"]
        + counts["superseded"]
        + counts["attrition_cells"]
        + counts["in_flight_cells"]
    )
    groups = {g["group"]: g for g in summary["groups"]}
    assert (groups[A]["tasks_selected"], groups[A]["tasks_in_scope"]) == (1, 3)
    assert groups[A]["outcomes"] == {"tested": 1}
    assert (groups[A]["attrition_cells"], groups[A]["in_flight_cells"], groups[B]["missing_cells"]) == (1, 1, 2)


def test_pilot_beyond_plan_reference_and_superseded_plan_runs_are_excluded_and_counted():
    pool = Pool()
    study, plan = pool.study("final")
    kept = pool.run(plan, A, "todo")
    pool.run(plan, A, "pilot-task", stage=choices.Stage.PILOT)
    pool.run(plan, A, "todo", beyond_plan=True)
    make_run(
        pool.user,
        None,
        pool.task("todo"),
        kind=choices.RunKind.REFERENCE,
        reference_of=choices.ReferenceOf.ORACLE,
        study=study,
    )
    # A cell of a plan the study no longer runs.
    old_plan = factories.StudyPlanFactory.create(study=study)
    old_cell = make_cell(make_condition(old_plan, pool.model(A)), make_unit(old_plan, pool.task("todo")))
    cell_run(old_cell, pool.user)

    selection = select_latest_unique(_config(study), pool.user)

    assert [r.pk for r in selection.runs] == [kept.pk]
    counts = selection.summary["counts"]
    assert counts["pilot_excluded"] == 1
    assert counts["beyond_plan_excluded"] == 1
    assert counts["reference_excluded"] == 1
    assert counts["superseded_plan_excluded"] == 1
    assert counts["candidates"] == 1
    assert selection.summary["tasks"] == ["todo@1"]


def test_model_and_task_filters():
    pool = Pool()
    study, plan = pool.study("final")
    pool.run(plan, A, "todo")
    pool.run(plan, A, "blog")
    pool.run(plan, B, "todo")
    blog_v2 = factories.TaskSpecVersionFactory.create(task=pool.task("blog").task)
    cell = make_cell(pool.conditions[(plan.pk, A)], make_unit(plan, blog_v2))
    cell_run(cell, pool.user)

    by_model = select_latest_unique(_config(study, models=[A]), pool.user).summary
    assert [g["group"] for g in by_model["groups"]] == [A]
    assert by_model["counts"]["filtered_out"] == 1
    assert by_model["filters"]["models"] == [A]

    bare = select_latest_unique(_config(study, tasks=["blog"]), pool.user).summary
    assert bare["tasks"] == ["blog@1", "blog@2"]
    assert bare["counts"]["filtered_out"] == 2

    pinned = select_latest_unique(_config(study, tasks=["blog@2", "todo@1"]), pool.user).summary
    assert pinned["tasks"] == ["blog@2", "todo@1"]
    assert [g["group"] for g in pinned["groups"]] == [A, B]

    with pytest.raises(ValueError, match="No runs of model"):
        select_latest_unique(_config(study, models=["vendor/typo"]), pool.user)
    with pytest.raises(ValueError, match="No runs of task"):
        select_latest_unique(_config(study, tasks=["missing-task"]), pool.user)


def test_one_treatment_pools_studies_and_conditions_into_one_group():
    pool = Pool()
    s1, p1 = pool.study("first")
    s2, p2 = pool.study("second")
    treatment = content.fake_sha("treatment")
    r1 = pool.run(p1, A, "t1", treatment_hash=treatment)
    r2 = pool.run(p2, A, "t2", treatment_hash=treatment)

    selection = select_latest_unique(_config(s1, s2), pool.user)

    [group] = selection.summary["groups"]
    assert group["group"] == A
    assert group["treatment_hash"] == treatment
    assert group["conditions"] == sorted([r1.condition_id, r2.condition_id])
    assert group["studies"] == [str(s1.pk), str(s2.pk)]
    assert group["tasks_selected"] == 2
    assert selection.whole_models
    assert set(selection.group_of.values()) == {A}


def test_different_treatments_are_never_pooled():
    pool = Pool()
    s1, p1 = pool.study("first")
    s2, p2 = pool.study("second")
    h1, h2 = content.fake_sha("t-one"), content.fake_sha("t-two")
    pool.run(p1, A, "todo", treatment_hash=h1)
    pool.run(p2, A, "todo", treatment_hash=h2)

    selection = select_latest_unique(_config(s1, s2), pool.user)

    labels = sorted(g["group"] for g in selection.summary["groups"])
    assert labels == sorted([f"{A} (treatment {h1[:8]})", f"{A} (treatment {h2[:8]})"])
    assert {g["treatment_hash"] for g in selection.summary["groups"]} == {h1, h2}
    assert len(selection.runs) == 2
    assert not selection.summary["superseded"]
    assert not selection.whole_models


def test_common_tasks_only_drops_tasks_some_group_lacks():
    pool = Pool()
    study, plan = pool.study("final")
    pool.run(plan, A, "t1")
    dropped = pool.run(plan, A, "t2")
    pool.run(plan, B, "t1")
    pool.run(plan, B, "t2", **_infra())

    full = select_latest_unique(_config(study), pool.user).summary
    assert full["common_tasks"] == ["t1@1"]
    assert full["dropped_tasks"] == []
    assert full["counts"]["selected"] == 3

    selection = select_latest_unique(_config(study, common_tasks_only=True), pool.user)
    summary = selection.summary
    assert summary["dropped_tasks"] == ["t2@1"]
    assert summary["counts"]["dropped_tasks"] == 1
    assert summary["counts"]["dropped_runs"] == 1
    assert dropped.pk not in {r.pk for r in selection.runs}
    assert len(selection.runs) == 2
    cell = _cell(summary, A, "t2@1")
    assert (cell["status"], cell["in_sample"]) == ("selected", False)
    assert {g["group"]: g["tasks_selected"] for g in summary["groups"]} == {A: 1, B: 1}


def test_owner_scoping_and_study_validation():
    pool = Pool()
    _study, plan = pool.study("mine")
    pool.run(plan, A, "todo")
    other = Pool()
    foreign, foreign_plan = other.study("theirs")
    other.run(foreign_plan, A, "todo")

    # No study named: every study the user may see.
    summary = select_latest_unique({"selection": "latest_unique"}, pool.user).summary
    assert [s["name"] for s in summary["studies"]] == ["mine"]
    staff = UserFactory(is_staff=True)
    assert {s["name"] for s in select_latest_unique({"selection": "latest_unique"}, staff).summary["studies"]} == {
        "mine",
        "theirs",
    }
    with pytest.raises(ValueError, match="Study not found"):
        select_latest_unique(_config(foreign), pool.user)
    with pytest.raises(ValueError, match="Study not found"):
        select_latest_unique({"selection": "latest_unique", "study_ids": [str(uuid.uuid4())]}, pool.user)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"selection": "latest_unique", "study_id": "x"}, "study_id cannot be combined"),
        ({"selection": "latest_unique", "run_ids": ["x"]}, "run_ids cannot be combined"),
        ({"selection": "latest_unique", "study_ids": "abc"}, "list of non-empty strings"),
        ({"selection": "latest_unique", "study_ids": ["not-a-uuid"]}, "must be study UUIDs"),
        ({"selection": "latest_unique", "models": [1]}, "list of non-empty strings"),
        ({"selection": "latest_unique", "tasks": ["bad ref!"]}, "task refs"),
        ({"selection": "latest_unique", "common_tasks_only": "yes"}, "true or false"),
        ({"selection": "newest"}, "selection must be"),
        ({"study_ids": [str(uuid.uuid4())]}, "need selection=latest_unique"),
        ({"common_tasks_only": True}, "need selection=latest_unique"),
    ],
)
def test_validate_selection_config_rejects(config, message):
    with pytest.raises(ValueError, match=message):
        validate_selection_config(config, UserFactory())


def test_validate_selection_config_passes_one_study_configs():
    assert validate_selection_config({"study_id": "x"}, None) is None
    assert validate_selection_config({"selection": "study", "models": [], "common_tasks_only": False}, None) is None


# ── analyses ─────────────────────────────────────────────────────────────


def _analysis(run, name, launch, minute, tools):
    analysis = AnalysisRunFactory(
        created_by=run.created_by,
        generation_job=run,
        source_code={},
        code_scope="app",
        status=AnalysisRun.Status.COMPLETED,
        name=name,
        launch_id=launch,
    )
    AnalysisRun.objects.filter(pk=analysis.pk).update(created_at=T0 + timedelta(minutes=minute))
    for slug, status in tools.items():
        result = ToolResultFactory(run=analysis, tool_slug=slug, status=status)
        ToolResult.objects.filter(pk=result.pk).update(created_at=T0 + timedelta(minutes=minute))
    return analysis


def test_newest_unique_analyses_with_superseded_counts_and_launches():
    pool = Pool()
    study, plan = pool.study("final")
    x = pool.run(plan, A, "t1")
    y = pool.run(plan, A, "t2")
    l1, l2 = uuid.uuid4(), uuid.uuid4()
    done, failed = ToolResult.Status.COMPLETED, ToolResult.Status.FAILED
    _analysis(x, "FINAL static 1a2b3c4d", l1, 10, {"bandit": done, "ruff": done})
    _analysis(y, "SHALLOW dynamic 5e6f7a8b", None, 11, {"ruff": done})
    _analysis(x, "RERUN static 9c0d1e2f", l2, 12, {"bandit": failed, "ruff": done})
    _analysis(y, "FINAL static 3a4b5c6d", l1, 13, {"bandit": done, "ruff": done})

    analyses = select_latest_unique(_config(study), pool.user).summary["analyses"]

    assert analyses["analysis_runs_total"] == 4
    assert analyses["analysis_runs_contributing"] == 3
    assert analyses["analysis_runs_superseded"] == 1
    assert (analyses["tool_results_total"], analyses["tool_results_selected"]) == (7, 4)
    assert analyses["tool_results_superseded"] == 3
    # x's bandit: the older measured result wins over the newer failed one.
    assert analyses["older_measured_preferred"] == 1
    by_launch = {b["launch_id"]: b for b in analyses["by_launch"]}
    assert set(by_launch) == {str(l1), str(l2), "none"}
    assert (by_launch[str(l1)]["analysis_runs"], by_launch[str(l1)]["results_selected"]) == (2, 3)
    assert by_launch[str(l1)]["results_superseded"] == 1
    assert by_launch[str(l1)]["example_name"] == "FINAL static 3a4b5c6d"
    assert (by_launch[str(l2)]["results_selected"], by_launch[str(l2)]["results_superseded"]) == (1, 1)
    assert (by_launch["none"]["results_selected"], by_launch["none"]["results_superseded"]) == (0, 1)
    assert analyses["tools_mixed_launches"] == [
        {"tool": "ruff", "launches": 2, "launch_ids": sorted([str(l1), str(l2)])},
    ]
    by_label = {b["label"]: b for b in analyses["by_label"]}
    assert set(by_label) == {"FINAL static", "RERUN static", "SHALLOW dynamic"}
    assert by_label["FINAL static"]["results_selected"] == 3
    assert analyses["tools_mixed_labels"] == [{"tool": "ruff", "labels": ["FINAL static", "RERUN static"]}]


# ── the report ───────────────────────────────────────────────────────────


@pytest.fixture
def final_pool():
    """Three models over two studies: A and B cover t1-t3, C covers t1 only (with older superseded attempts).

    The models are OpenRouter rows with list prices and the runs record tokens, so the final-assessment
    ranking (cost at list price) ranks all three.
    """
    pool = Pool()
    for model in (A, B, C):
        pool.models[model] = LLMModelFactory(model_id=model, metadata={"openrouter_pricing": LIST_PRICING})
    s1, p1 = pool.study("first")
    s2, p2 = pool.study("second")
    policy_one, policy_two = factories.ResourcePolicyFactory.create(), factories.ResourcePolicyFactory.create()
    for i, task in enumerate(("t1", "t2", "t3")):
        for model in (A, B):
            metrics = {"cost_usd": 0.01 * (i + 1), **TOKENS}
            run = pool.run(p1, model, task, resource_policy=policy_one, metrics=metrics)
            evaluate(run, passed=3 - (i % 2), failed=1 + (i % 2))
            analyze(run, {"bandit": [], "ruff": []})
    old = pool.run(p1, C, "t1", resource_policy=policy_one)
    pool.run(p2, C, "t1", **_infra())
    newest = pool.run(p2, C, "t1", resource_policy=policy_two, metrics={"cost_usd": 0.02, **TOKENS})
    evaluate(newest, passed=4, failed=0)
    analyze(newest, {"bandit": [], "ruff": []})
    # B's newest t3: another policy.
    pool.run(p2, B, "t3", resource_policy=policy_two, metrics={"cost_usd": 0.03, **TOKENS})
    return pool, (s1, s2), old, newest


def test_study_report_with_selection_end_to_end(final_pool):
    pool, studies, old, newest = final_pool

    data = study_report.generate_study_report(_config(*studies), user=pool.user)

    selection = data["selection"]
    assert selection["mode"] == "latest_unique"
    assert len(data["per_app"]) == selection["counts"]["selected"] == 7
    assert {r["run_id"] for r in data["per_app"]} == {c["run_id"] for c in selection["matrix"] if c["in_sample"]}
    assert str(newest.pk) in {r["run_id"] for r in data["per_app"]}
    assert str(old.pk) not in {r["run_id"] for r in data["per_app"]}
    protocol = data["protocol"]
    assert protocol["grouped_by"] == "model"
    assert protocol["study"] is None
    assert protocol["scope"]["selection"] == "latest_unique"
    assert protocol["scope"]["study_ids"] == [str(s.pk) for s in studies]
    assert protocol["scope"]["studies_contributing"] == [str(s.pk) for s in studies]
    assert protocol["scope"]["common_tasks_only"] is False
    # The final-assessment criteria rank a cross-study sample by default.
    assert protocol["ranking_preset"] == "final_assessment"
    assert {r["model"] for r in data["ranking"]["ranking"]} == {A, B, C}
    assert {m["model"] for m in data["model_outcomes"]} == {A, B, C}
    assert data["planned_comparisons"] is None
    # Cross-study: the groups are compared pairwise on shared tasks (exploratory; too few pairs for tests here).
    exploratory = data["exploratory_comparisons"]
    assert exploratory["status"] == "exploratory"
    assert exploratory["baseline_condition"].startswith("group A")
    pairs = {(c["condition_a"], c["condition_b"]) for c in exploratory["families"][0]["comparisons"]}
    assert pairs == {(A, B), (A, C), (B, C)}
    assert any(note.startswith("Cross-study sample") for note in exploratory["notes"])
    # Issues per selection group; no per-condition split of the pooled groups.
    assert "by_condition" not in data["issues"]
    assert {r["model"] for r in data["issues"]["by_group"]} == {A, B, C}
    codes = {v["code"]: v for v in data["validity"]}
    assert codes["cross_study_selection"]["level"] == "info"
    assert "2 studies" in codes["cross_study_selection"]["message"]
    assert codes["unequal_task_sets"]["level"] == "warning"
    assert "1 tasks: vendor/model-c" in codes["unequal_task_sets"]["message"]
    assert codes["mixed_resource_policies"]["level"] == "warning"
    assert "vendor/model-b" in codes["mixed_resource_policies"]["message"]
    assert "mixed_study_scope" not in codes
    # C's older usable t1 run was replaced by its newest usable one.
    assert codes["newer_usable_replaced"]["level"] == "warning"
    assert "vendor/model-c t1@1" in codes["newer_usable_replaced"]["message"]
    # A x3 and B t1/t2 analyzed with two tools each, C's newest too; B's newest t3 was never analyzed.
    assert selection["analyses"]["tool_results_selected"] == 12

    summary = _make_summary("study_report", data)
    assert summary["selection"] == {"groups": 3, "tasks": 3, "selected": 7}

    report = Report(
        report_type=Report.Type.STUDY_REPORT,
        title="Final assessment",
        report_data=data,
        status=Report.Status.COMPLETED,
    )
    markdown = renderers.render_markdown(report)
    assert "## Sample selection" in markdown
    assert "## Sample selection: coverage by group" in markdown
    assert "## Sample selection: superseded runs" in markdown
    assert "## Sample selection: analyses by launch" in markdown
    assert "| vendor/model-c | 1 / 3 |" in markdown
    assert "Newest unique runs from 2 studies" in markdown
    latex = renderers.render_latex(report)
    assert "Sample selection: coverage by group" in latex
    assert "\\begin{longtable}" in latex


def test_selection_report_complete_cases_and_truncation(final_pool):
    pool, studies, _old, _newest = final_pool
    GenerationRun.objects.filter(model__model_id=A, task_version__task__slug="t1").update(
        validity={"truncated": True},
    )

    data = study_report.generate_study_report(_config(*studies, exclude_truncated=True), user=pool.user)

    selection = data["selection"]
    # A's truncated t1 run leaves the sample inside the selection (no older attempt takes its place).
    cell = _cell(selection, A, "t1@1")
    assert (cell["status"], cell["in_sample"], cell["excluded"]) == ("selected", False, "truncated")
    assert selection["counts"]["truncated_excluded"] == data["protocol"]["truncated_excluded"] == 1
    assert selection["counts"]["selected"] == len(data["per_app"]) == 6
    groups = {g["group"]: g for g in selection["groups"]}
    assert (groups[A]["tasks_selected"], groups[A]["truncated_excluded"]) == (2, 1)
    assert {r["run_id"] for r in data["per_app"]} == {c["run_id"] for c in selection["matrix"] if c["in_sample"]}
    # The task sets the report analyses differ, so the warning says so (from the post-truncation sets).
    codes = {v["code"]: v for v in data["validity"]}
    assert "2 tasks: vendor/model-a" in codes["unequal_task_sets"]["message"]
    assert "Truncated runs excluded" in selection["rule"]

    # Complete cases are computed after the truncation exclusion: t1 is no longer complete.
    data = study_report.generate_study_report(
        _config(*studies, common_tasks_only=True, exclude_truncated=True),
        user=pool.user,
    )
    selection = data["selection"]
    assert selection["common_tasks"] == []
    assert selection["dropped_tasks"] == ["t1@1", "t2@1", "t3@1"]
    assert data["per_app"] == []
    assert selection["counts"]["selected"] == 0
    assert selection["counts"]["truncated_excluded"] == 1
    codes = {v["code"]: v for v in data["validity"]}
    assert codes["complete_cases"]["level"] == "warning"
    assert "left no task" in codes["complete_cases"]["message"]
    assert "vendor/model-c 2 (missing 2)" in codes["complete_cases"]["message"]
    assert "vendor/model-a 1 (truncated 1)" in codes["complete_cases"]["message"]
    # Every group is empty because complete cases kept no task: that entry says so, once.
    assert "empty_groups" not in codes
    assert "unequal_task_sets" not in codes


def test_selection_report_splits_treatments_like_conditions():
    pool = Pool()
    s1, p1 = pool.study("first")
    s2, p2 = pool.study("second")
    for plan, seed in ((p1, "one"), (p2, "two")):
        run = pool.run(plan, A, "todo", treatment_hash=content.fake_sha(seed))
        evaluate(run)
    pool.run(p1, B, "todo")

    data = study_report.generate_study_report(_config(s1, s2), user=pool.user)

    groups = {
        f"{A} (treatment {content.fake_sha('one')[:8]})",
        f"{A} (treatment {content.fake_sha('two')[:8]})",
        B,
    }
    assert data["protocol"]["grouped_by"] == "condition"
    assert {r["group"] for r in data["per_app"]} == groups
    assert {m["model"]: m["model_id"] for m in data["model_outcomes"]} == {g: g.split(" ")[0] for g in groups}
    assert {f["model"] for f in data["funnel"]["by_model"]} == groups


def _two_models_two_tasks():
    pool = Pool()
    study, plan = pool.study("final")
    runs = {}
    for task in ("t1", "t2"):
        for model in (A, B):
            run = pool.run(plan, model, task)
            evaluate(run)
            runs[(model, task)] = run
    return pool, study, runs


def test_a_group_whose_every_run_is_truncated_is_named_not_silently_dropped():
    pool, study, _runs = _two_models_two_tasks()
    GenerationRun.objects.filter(model__model_id=A).update(validity={"truncated": True})

    data = study_report.generate_study_report(_config(study, exclude_truncated=True), user=pool.user)

    assert {m["model"] for m in data["model_outcomes"]} == {B}
    groups = {g["group"]: g for g in data["selection"]["groups"]}
    assert (groups[A]["tasks_selected"], groups[A]["truncated_excluded"]) == (0, 2)
    codes = {v["code"]: v for v in data["validity"]}
    assert codes["empty_groups"]["level"] == "warning"
    assert f"{A} (truncated 2)" in codes["empty_groups"]["message"]
    # One group left with runs: nothing unequal to compare.
    assert "unequal_task_sets" not in codes
    summary = _make_summary("study_report", data)
    assert summary["selection"]["selected"] == len(data["per_app"]) == 2


def test_complete_cases_name_the_group_that_empties_the_sample():
    pool = Pool()
    study, plan = pool.study("final")
    for task in ("t1", "t2"):
        evaluate(pool.run(plan, A, task))
        evaluate(pool.run(plan, B, task))
        pool.run(plan, C, task, **_infra())

    data = study_report.generate_study_report(_config(study, common_tasks_only=True), user=pool.user)

    selection = data["selection"]
    assert selection["dropped_by_group"] == [{"group": C, "tasks": 2, "reasons": {"attrition": 2}}]
    codes = {v["code"]: v for v in data["validity"]}
    assert codes["complete_cases"]["level"] == "warning"
    assert f"{C} 2 (attrition 2)" in codes["complete_cases"]["message"]
    assert "models filter" in codes["complete_cases"]["message"]
    # The attrition sits on dropped tasks: the complete-case note explains it, not the attrition warning.
    assert "attrition" not in codes
    assert data["protocol"]["attrition_excluded"] == {}
    assert selection["counts"]["attrition_cells"] == 2
    report = Report(report_type=Report.Type.STUDY_REPORT, title="Empty complete cases", report_data=data)
    assert "groups blocking complete cases" in renderers.render_markdown(report)
    renderers.render_latex(report)


def test_complete_cases_warnings_count_only_the_kept_tasks():
    pool = Pool()
    study, plan = pool.study("final")
    for model in (A, B):
        evaluate(pool.run(plan, model, "t1"))
    pool.run(plan, A, "t2", **_infra())
    old = pool.run(plan, A, "t2")
    new = pool.run(plan, A, "t2")
    pool.run(plan, B, "t2", **_infra())

    full = study_report.generate_study_report(_config(study), user=pool.user)
    assert _make_summary("study_report", full)["selection"] == {"groups": 2, "tasks": 2, "selected": 3}
    codes = {v["code"]: v["message"] for v in full["validity"]}
    assert "2 older or unusable attempt(s) superseded." in codes["cross_study_selection"]
    assert f"{A} t2@1" in codes["newer_usable_replaced"]
    assert full["protocol"]["attrition_excluded"] == {"infra_error": 1}

    data = study_report.generate_study_report(_config(study, common_tasks_only=True), user=pool.user)

    # The summary counts the analysed tasks: t2 is dropped for complete cases.
    assert _make_summary("study_report", data)["selection"] == {"groups": 2, "tasks": 1, "selected": 2}
    codes = {v["code"]: v for v in data["validity"]}
    message = codes["cross_study_selection"]["message"]
    assert "0 older or unusable attempt(s) superseded on the 1 task(s) kept" in message
    assert "2 more on the 1 task(s) dropped for complete cases" in message
    # A's t2 replacement is on a dropped task: not in the sample, so not named.
    assert "newer_usable_replaced" not in codes
    assert "attrition" not in codes
    assert codes["complete_cases"]["level"] == "info"
    assert "kept 1 of 2 task(s); 1 dropped with their 1 selected run(s)" in codes["complete_cases"]["message"]
    superseded = {x["run_id"]: x for x in data["selection"]["superseded"]}
    assert superseded[str(old.pk)]["replaced_by"] == str(new.pk)
    assert superseded[str(old.pk)]["unit_in_sample"] is False


def test_attrition_attempts_have_no_replacing_run():
    pool = Pool()
    study, plan = pool.study("final")
    first = pool.run(plan, A, "t1", **_infra())
    newest = pool.run(plan, A, "t1", **_infra())

    [superseded] = select_latest_unique(_config(study), pool.user).summary["superseded"]

    assert superseded["run_id"] == str(first.pk)
    assert superseded["replaced_by"] is None
    assert superseded["recorded_attempt"] == str(newest.pk)
    assert superseded["reason"] == "unusable_attempt"


def test_runs_without_a_treatment_hash_are_never_pooled_with_a_recorded_treatment():
    pool = Pool()
    s1, p1 = pool.study("first")
    s2, p2 = pool.study("second")
    treatment = content.fake_sha("treat")
    unhashed = pool.run(p1, A, "t1")
    pool.run(p2, A, "t2", treatment_hash=treatment)
    GenerationRun.objects.filter(pk=unhashed.pk).update(treatment_hash="")

    selection = select_latest_unique(_config(s1, s2), pool.user)

    groups = {g["group"]: g["treatment_hash"] for g in selection.summary["groups"]}
    assert groups == {f"{A} (treatment unrecorded)": "", f"{A} (treatment {treatment[:8]})": treatment}
    assert not selection.whole_models
    codes = {code: message for code, _level, _section, message in selection_warnings(selection.summary)}
    assert f"{A} (treatment unrecorded)" in codes["treatment_unrecorded"]


def test_split_mode_group_rows_name_no_single_condition_for_pooled_groups():
    pool = Pool()
    s1, p1 = pool.study("first")
    s2, p2 = pool.study("second")
    s3, p3 = pool.study("third")
    h1, h2 = content.fake_sha("one"), content.fake_sha("two")
    runs = [
        pool.run(plan, A, task, treatment_hash=h) for plan, task, h in ((p1, "t1", h1), (p2, "t2", h1), (p3, "t3", h2))
    ]
    for run in runs:
        evaluate(run)

    data = study_report.generate_study_report(_config(s1, s2, s3), user=pool.user)

    pooled, single = f"{A} (treatment {h1[:8]})", f"{A} (treatment {h2[:8]})"
    rows = {m["model"]: m for m in data["model_outcomes"]}
    assert rows[pooled]["condition_id"] is None
    assert rows[pooled]["model_id"] == A
    assert rows[single]["condition_id"] == runs[2].condition_id
    assert {r["model"]: r["condition_id"] for r in data["funnel"]["by_model"]}[pooled] is None
    markdown = renderers.render_markdown(Report(report_type=Report.Type.STUDY_REPORT, title="x", report_data=data))
    assert "Outcome funnel by group" in markdown
    assert "treatment: a model ran under several treatments" in markdown


def test_scope_lists_the_pool_and_the_studies_that_contribute(final_pool):
    pool, (s1, s2), _old, _newest = final_pool

    data = study_report.generate_study_report(_config(s1, s2, models=[A]), user=pool.user)

    scope = data["protocol"]["scope"]
    assert scope["study_ids"] == [str(s1.pk), str(s2.pk)]
    assert scope["studies_contributing"] == [str(s1.pk)]
    codes = {v["code"]: v["message"] for v in data["validity"]}
    assert "pooled from 1 study:" in codes["cross_study_selection"]
    markdown = renderers.render_markdown(Report(report_type=Report.Type.STUDY_REPORT, title="x", report_data=data))
    assert "Newest unique runs from 1 study (2 in the pool)" in markdown


def test_one_study_report_has_no_selection(final_pool):
    pool, (s1, _s2), _old, _newest = final_pool

    data = study_report.generate_study_report({"study_id": str(s1.pk), "bootstrap": False}, user=pool.user)

    assert "selection" not in data
    assert data["protocol"]["scope"] == {"study_id": str(s1.pk), "run_ids": None}


def test_selection_report_of_an_empty_pool():
    user = UserFactory()
    make_study(user)

    data = study_report.generate_study_report({"selection": "latest_unique", "bootstrap": False}, user=user)

    assert data["per_app"] == []
    assert data["selection"]["counts"]["selected"] == 0
    assert data["selection"]["groups"] == []
    report = Report(report_type=Report.Type.STUDY_REPORT, title="Empty", report_data=data)
    assert "## Sample selection" in renderers.render_markdown(report)
    renderers.render_latex(report)


def test_every_run_report_still_warns_about_mixed_study_scope(final_pool):
    pool, _studies, _old, _newest = final_pool

    data = study_report.generate_study_report({"bootstrap": False}, user=pool.user)

    codes = {v["code"] for v in data["validity"]}
    assert "mixed_study_scope" in codes
    assert "cross_study_selection" not in codes


def test_selection_rejects_study_id_in_the_generator(final_pool):
    pool, (s1, _s2), _old, _newest = final_pool
    with pytest.raises(ValueError, match="study_id cannot be combined"):
        study_report.generate_study_report({"selection": "latest_unique", "study_id": str(s1.pk)}, user=pool.user)


# ── API and CLI ──────────────────────────────────────────────────────────


def test_api_creates_a_selection_report_and_rejects_invalid_configs(client, final_pool):
    pool, studies, _old, _newest = final_pool
    client.force_login(pool.user)

    def post(config):
        with mock.patch("backend.reports.services.report_service._dispatch"):
            return client.post(
                "/api/reports/generate/",
                data={"report_type": "study_report", "config": config},
                content_type="application/json",
            )

    res = post(_config(*studies, models=[A, B], tasks=["t1"], common_tasks_only=True))
    assert res.status_code == 201, res.content
    report = Report.objects.get(report_id=res.json()["report_id"])
    assert report.title == "Study report: newest unique runs (2 studies)"
    from backend.reports.services.report_service import _generate

    _generate(report)
    report.refresh_from_db()
    assert report.status == Report.Status.COMPLETED, report.error_message
    assert report.summary["selection"] == {"groups": 2, "tasks": 1, "selected": 2}
    assert report.report_data["selection"]["filters"]["models"] == [A, B]

    foreign, _plan = Pool().study("theirs")
    for config in (
        {**_config(*studies), "study_id": str(studies[0].pk)},
        _config(foreign),
        {"selection": "latest_unique", "study_ids": ["nope"]},
        {"selection": "latest_unique", "tasks": "t1"},
        {"selection": "latest_unique", "common_tasks_only": 1},
        {"selection": "everything"},
        {"study_ids": [str(studies[0].pk)]},
        # Filters matching no run are refused up front, not in the background generation.
        _config(*studies, models=["vendor/typo"]),
        _config(*studies, tasks=["no-such-task"]),
    ):
        res = post(config)
        assert res.status_code == 400, (config, res.content)


def test_management_command_flags(tmp_path, final_pool):
    _pool, studies, _old, _newest = final_pool
    out = tmp_path / "final.json"

    call_command(
        "study_report",
        "--latest-unique",
        "--studies",
        ",".join(str(s.pk) for s in studies),
        "--models",
        f"{A},{C}",
        "--tasks",
        "t1@1",
        "--common-tasks",
        "--no-bootstrap",
        "--out",
        str(out),
    )

    data = json.loads(out.read_text())
    selection = data["selection"]
    assert selection["filters"] == {
        "study_ids": [str(s.pk) for s in studies],
        "models": [A, C],
        "tasks": ["t1@1"],
        "common_tasks_only": True,
        "exclude_truncated": False,
    }
    assert [g["group"] for g in selection["groups"]] == [A, C]
    assert data["protocol"]["scope"]["selection"] == "latest_unique"

    md = tmp_path / "final.md"
    call_command("study_report", "--latest-unique", "--no-bootstrap", "--out", str(md), "--save")
    saved = Report.objects.get(title="Study report: newest unique runs (2 studies)")
    assert saved.config["selection"] == "latest_unique"
    assert "## Sample selection" in md.read_text()

    with pytest.raises(CommandError, match="need --latest-unique"):
        call_command("study_report", "--studies", str(studies[0].pk), "--out", str(out))
    with pytest.raises(CommandError, match="cannot be combined"):
        call_command("study_report", "--latest-unique", "--study", str(studies[0].pk), "--out", str(out))
    with pytest.raises(CommandError, match="Study not found"):
        call_command("study_report", "--latest-unique", "--studies", str(uuid.uuid4()), "--out", str(out))
