"""services/context.py: spec views, requirement ids, finding samples, the frozen context, hosting provenance."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationRun
from backend.generation.runs.view import run_view
from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.services import context
from backend.human_eval.services import hosting
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.instruments import SUS_QUESTION_IDS
from backend.human_eval.tests.factories import SPEC_API_ENDPOINTS
from backend.human_eval.tests.factories import UI_REQUIREMENTS
from backend.human_eval.tests.factories import eval_task_content
from backend.human_eval.tests.factories import eval_task_version
from backend.human_eval.tests.factories import make_open_task
from backend.human_eval.tests.factories import workbench_assignment
from backend.human_eval.tests.factories import workbench_design
from backend.human_eval.tests.factories import workbench_findings
from backend.runtime.models import ContainerInstance
from backend.runtime.tests.factories import ContainerInstanceFactory

pytestmark = pytest.mark.django_db

REVIEW_FINDING_KEYS = {
    "id",
    "tool",
    "tool_name",
    "tool_severity",
    "category",
    "rule_id",
    "title",
    "description",
    "file_path",
    "line",
    "code_snippet",
    "docs_url",
    "cwe",
    "url",
    "method",
    "param",
    "evidence",
}


def _task(n_apps: int = 2, **design_kwargs) -> EvalTask:
    return make_open_task(n_apps=n_apps, design=workbench_design(**design_kwargs))


def _apps(task: EvalTask) -> list[EvalTaskApp]:
    return list(task.apps.select_related("job").order_by("position"))


def _item_of(task: EvalTask, app: EvalTaskApp):
    return task.items.get(app_a=app)


# --- spec -------------------------------------------------------------------


def test_spec_dict_is_the_runs_task_dict():
    task = _task(n_apps=1)
    run = _apps(task)[0].job
    spec = context.spec_dict(run)
    assert spec == run_view(run).task_dict()
    assert spec["title"] == "Item tracker"
    assert spec["variant"] == "standard"
    assert spec["prose"] == "A todo app. Each user sees only their own todos."
    assert context.spec_dict(None) is None
    assert context.spec_view(None) is None


def test_spec_view_has_only_the_app_spec_view_fields():
    task = _task(n_apps=1)
    view = context.spec_view(_apps(task)[0].job)
    assert set(view) == {
        "name",
        "description",
        "prose",
        "frontend_requirements",
        "api_endpoints",
        "admin_api_endpoints",
    }
    assert view["name"] == "Item tracker"
    assert view["prose"] == "A todo app. Each user sees only their own todos."
    assert view["frontend_requirements"] == UI_REQUIREMENTS
    assert [f"{e['method']} {e['path']}" for e in view["api_endpoints"]] == [
        f"{e['method']} {e['path']}" for e in SPEC_API_ENDPOINTS
    ]
    assert view["api_endpoints"][2] == {"method": "GET", "path": "/api/items", "description": "", "auth": "user"}
    assert view["admin_api_endpoints"] == [
        {"method": "GET", "path": "/api/admin/users", "description": "", "auth": "admin"},
    ]


# --- requirement items ------------------------------------------------------------


def _spec(endpoints=(), ui=()):
    return {"structured": {"endpoints": list(endpoints), "ui": list(ui)}}


def test_requirement_items_ids_text_and_order():
    spec = _spec(
        endpoints=[
            {"id": "a", "method": "get", "path": "/api/items", "auth": "user"},
            {"id": "b", "method": "POST", "path": "/api/items", "auth": "user"},
            {"id": "c", "method": "", "path": "/broken", "auth": "user"},
            {"id": "d", "method": "DELETE", "path": "/api/admin/users/{id}", "auth": "admin"},
        ],
        ui=[
            {"id": "u1", "text": "Users can log in"},
            {"id": "u2", "text": "Users can log in"},
            {"id": "u3", "text": " "},
        ],
    )
    items = context.requirement_items(
        spec,
        ["api", "frontend", "admin_api"],
        [{"id": "readme", "text": "Has a README"}, {"id": "empty", "text": " "}],
    )
    assert [item["source"] for item in items] == ["api", "api", "frontend", "admin_api", "custom"]
    assert items[0]["text"] == "GET /api/items"
    assert items[1]["text"] == "POST /api/items"
    assert items[2]["text"] == "Users can log in"  # listed once
    assert items[3]["text"] == "DELETE /api/admin/users/{id}"  # admin endpoints only in admin_api
    assert items[-1] == {"id": "c_readme", "source": "custom", "text": "Has a README"}
    assert re.fullmatch(r"ep_[0-9a-f]{10}", items[0]["id"])
    assert re.fullmatch(r"f_[0-9a-f]{10}", items[2]["id"])
    assert re.fullmatch(r"aep_[0-9a-f]{10}", items[3]["id"])
    assert items[0]["id"] == context.requirement_id("api", "GET /api/items")
    assert items[2]["id"] == context.requirement_id("frontend", "Users can log in")
    assert context.requirement_id("api", "x") != context.requirement_id("frontend", "x")
    assert context.requirement_items(None, ["frontend"], []) == []


def test_requirement_ids_are_stable_across_apps_with_the_same_task():
    task = _task(n_apps=3)
    contexts = []
    for app in _apps(task):
        assignment = workbench_assignment(task, item=_item_of(task, app))
        contexts.append(context.build_context(assignment)["blocks"]["reqs"]["requirements"]["single"])
    ids = [[item["id"] for item in items] for items in contexts]
    assert ids[0] == ids[1] == ids[2]
    assert len(ids[0]) == len(UI_REQUIREMENTS) + len(SPEC_API_ENDPOINTS) + 1  # frontend + api endpoints + custom
    assert ids[0][-1] == "c_readme"

    # Another task text yields other ids; the shared text keeps its id.
    other = context.requirement_items(
        _spec(ui=[{"id": "x", "text": UI_REQUIREMENTS[0]}, {"id": "y", "text": "New"}]),
        ["frontend"],
        [],
    )
    assert other[0]["id"] == ids[0][0]
    assert other[1]["id"] not in ids[0]


# --- seat allocation -------------------------------------------------------------


def test_allocate_seats_is_proportional_with_a_minimum_of_one():
    assert context.allocate_seats({"a": 30, "b": 10, "c": 2}, 10) == {"a": 7, "b": 2, "c": 1}
    # Everything fits: take the whole population.
    assert context.allocate_seats({"a": 3, "b": 1}, 10) == {"a": 3, "b": 1}
    # The minimum takes seats from the largest share; capped at the population.
    assert context.allocate_seats({"a": 100, "b": 1, "c": 1, "d": 1}, 5) == {"a": 2, "b": 1, "c": 1, "d": 1}
    # Fewer seats than tools: the largest populations get one each (ties by slug).
    assert context.allocate_seats({"a": 5, "b": 9, "c": 5}, 2) == {"a": 1, "b": 1, "c": 0}
    assert context.allocate_seats({"a": 0, "b": 4}, 3) == {"b": 3}
    assert context.allocate_seats({}, 3) == {}
    for populations, seats in (({"a": 7, "b": 7, "c": 7}, 10), ({"x": 50, "y": 3}, 9), ({"p": 2, "q": 40}, 41)):
        allocation = context.allocate_seats(populations, seats)
        assert sum(allocation.values()) == min(seats, sum(populations.values()))
        assert all(1 <= allocation[slug] <= populations[slug] for slug in populations)


# --- finding samples ---------------------------------------------------------------


def _config(**overrides) -> dict:
    return {
        "enabled": True,
        "max_findings": 5,
        "categories": [],
        "include_ai": False,
        "include_dependency": False,
        "include_dynamic": False,
        **overrides,
    }


def test_finding_sample_is_proportional_seeded_frozen_and_records_the_frame():
    task = _task(n_apps=2)
    app, other_app = _apps(task)
    big = workbench_findings(app.job, "heval-sast-big", 12)
    small = workbench_findings(app.job, "heval-sast-small", 3)

    sample = context.finding_sample(app, _config(), task.seed)
    big_ids = {str(f.id) for f in big}
    small_ids = {str(f.id) for f in small}
    assert len(sample) == 5
    assert sum(1 for fid in sample if fid in big_ids) == 4
    assert sum(1 for fid in sample if fid in small_ids) == 1
    app.refresh_from_db()
    assert app.finding_sample == sample
    assert app.finding_sample_at is not None
    assert app.finding_frame == {
        "heval-sast-big": {"population": 12, "sampled": 4},
        "heval-sast-small": {"population": 3, "sampled": 1},
    }
    # Round-robin across tools (slug order): big, small, big, big, big.
    assert sample[0] in big_ids
    assert sample[1] in small_ids

    # Deterministic: redrawing with the same seed and app gives the same ids in the same order.
    EvalTaskApp.objects.filter(pk=app.pk).update(finding_sample=[], finding_sample_at=None, finding_frame={})
    app.refresh_from_db()
    assert context.finding_sample(app, _config(), task.seed) == sample

    # Frozen: findings added later do not change a drawn sample.
    workbench_findings(app.job, "heval-sast-late", 5)
    app.refresh_from_db()
    assert context.finding_sample(app, _config(), task.seed) == sample
    assert "heval-sast-late" not in app.finding_frame

    # The other app of the task has no findings: an empty, recorded sample.
    assert context.finding_sample(other_app, _config(), task.seed) == []
    other_app.refresh_from_db()
    assert other_app.finding_sample_at is not None
    assert other_app.finding_frame == {}


def test_finding_sample_differs_by_seed_but_not_by_call():
    task = _task(n_apps=1)
    app = _apps(task)[0]
    workbench_findings(app.job, "heval-sast-many", 40)
    first = context._draw(app, _config(max_findings=5), task.seed)[0]
    assert context._draw(app, _config(max_findings=5), task.seed)[0] == first
    others = {tuple(context._draw(app, _config(max_findings=5), seed)[0]) for seed in range(1, 6)}
    assert len(others) > 1


def test_finding_sample_filters_tools_categories_and_results():
    task = _task(n_apps=1)
    app = _apps(task)[0]
    job = app.job
    keep = workbench_findings(job, "heval-keep", 2)
    workbench_findings(job, "heval-ai", 2, kind="ai")
    workbench_findings(job, "heval-dast", 2, analysis_mode="dynamic")
    workbench_findings(job, "heval-deps", 2, category="dependency")
    workbench_findings(job, "heval-lint", 2, category="style")
    workbench_findings(job, "heval-failed", 2, result_status="failed")
    # An older run of heval-keep is superseded by the latest result per (job, tool).
    old_run = AnalysisRunFactory(generation_job=job, created_by=job.created_by, status=AnalysisRun.Status.COMPLETED)
    old_result = ToolResultFactory(run=old_run, tool_slug="heval-keep")
    old = FindingFactory(result=old_result, category="security")
    old_result.created_at = timezone.now() - timedelta(days=3)
    old_result.save(update_fields=["created_at"])

    population = context._population(job, _config(categories=["security", "dependency"]))
    assert set(population) == {"heval-keep"}
    assert set(population["heval-keep"]) == {str(f.id) for f in keep}
    assert str(old.id) not in population["heval-keep"]

    wide = context._population(job, _config(include_ai=True, include_dynamic=True, include_dependency=True))
    assert set(wide) == {"heval-keep", "heval-ai", "heval-dast", "heval-deps", "heval-lint"}
    assert set(context._population(job, _config(categories=["style"]))) == {"heval-lint"}
    assert context._population(None, _config()) == {}


def test_draw_finding_samples_draws_every_app_once():
    task = _task(n_apps=3)
    apps = _apps(task)
    workbench_findings(apps[0].job, "heval-sast-a", 3)
    assert context.draw_finding_samples(task) == 3
    assert all(app.finding_sample_at is not None for app in task.apps.all())
    assert len(task.apps.get(pk=apps[0].pk).finding_sample) == 3
    assert context.draw_finding_samples(task) == 0

    without_review = _task(n_apps=1, finding_review=False)
    assert context.draw_finding_samples(without_review) == 0
    assert without_review.apps.get().finding_sample_at is None


# --- review findings -----------------------------------------------------------------


def test_review_finding_never_carries_the_normalized_severity():
    task = _task(n_apps=1)
    job = _apps(task)[0].job
    finding = workbench_findings(
        job,
        "heval-semgrep",
        1,
        severity="critical",
        tool_severity="",
        description="Possible SQL injection",
        code_snippet="cursor.execute(q)",
        tool_specific_data={
            "docs_url": "https://example.com/rule",
            "cwe": ["CWE-89", "CWE-20"],
            "url": "http://10.0.0.5:8000/x",
        },
    )[0]
    payload = context.review_finding(finding)
    assert set(payload) == REVIEW_FINDING_KEYS
    assert "severity" not in payload
    assert payload["tool_severity"] == ""
    assert payload["tool"] == "heval-semgrep"
    assert payload["tool_name"] == "Tool heval-semgrep"
    assert payload["file_path"] == "backend/file_0.py"
    assert payload["line"] == 1
    assert payload["docs_url"] == "https://example.com/rule"
    assert payload["cwe"] == "CWE-89, CWE-20"
    assert payload["url"] == ""  # static tools: their url key is not a DAST endpoint

    finding.tool_specific_data = {"docs_url": "javascript:alert(1)"}
    assert context.review_finding(finding)["docs_url"] == ""


def test_review_finding_dast_fields_and_workspace_paths():
    task = _task(n_apps=1)
    job = _apps(task)[0].job
    dast = workbench_findings(
        job,
        "heval-zap",
        1,
        analysis_mode="dynamic",
        tool_specific_data={
            "url": "http://172.17.0.4:8000/api/items?id=1",
            "method": "post",
            "param": "id",
            "evidence": "<script>",
        },
    )[0]
    payload = context.review_finding(dast)
    assert payload["url"] == "/api/items?id=1"
    assert payload["method"] == "POST"
    assert payload["param"] == "id"
    assert payload["evidence"] == "<script>"

    # Workspace prefixes of analysis paths are dropped: the path names a file of the source viewer's tree.
    dast.file_path = "/work/backend/app.py"
    assert context.review_finding(dast)["file_path"] == "backend/app.py"


# --- context ------------------------------------------------------------------------


def test_build_context_shape_and_contents():
    task = _task(n_apps=1)
    app = _apps(task)[0]
    findings = workbench_findings(app.job, "heval-sast-ctx", 2)
    assignment = workbench_assignment(task)
    built = context.build_context(assignment)
    assert set(built) == {"blocks", "hosting", "instructions_sha256"}
    assert set(built["blocks"]) == {"ratings", "reqs", "sec", "tasks", "sus", "extra", "bugs"}
    assert built["blocks"]["ratings"] == {}
    assert [q["id"] for q in built["blocks"]["sus"]["questions"]] == list(SUS_QUESTION_IDS)
    assert "questions" not in built["blocks"]["extra"]  # custom questions live in the block
    reviewed = built["blocks"]["sec"]["findings"]["single"]
    assert {entry["id"] for entry in reviewed} == {str(f.id) for f in findings}
    assert all(set(entry) == REVIEW_FINDING_KEYS for entry in reviewed)
    assert built["hosting"] == {}
    assert re.fullmatch(r"[0-9a-f]{64}", built["instructions_sha256"])
    assignment.refresh_from_db()
    assert assignment.context == built


def test_context_is_frozen_at_first_open():
    task = _task(n_apps=1)
    app = _apps(task)[0]
    assignment = workbench_assignment(task)
    context.open_assignment(assignment)
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.IN_PROGRESS
    assert assignment.started_at is not None
    frozen = assignment.context
    first_ids = [item["id"] for item in frozen["blocks"]["reqs"]["requirements"]["single"]]

    # Later changes to the task, instructions and findings never reach the stored context.
    changed = eval_task_content()
    changed["ui"] = [{"id": "ui.other", "text": "Something else entirely", "endpoints": []}]
    GenerationRun.objects.filter(pk=app.job_id).update(task_version=eval_task_version(content=changed))
    task.instructions = "New instructions"
    task.save(update_fields=["instructions"])
    workbench_findings(app.job, "heval-sast-new", 3)
    started = assignment.started_at

    again = Assignment.objects.select_related("task", "item", "item__app_a", "participant").get(pk=assignment.pk)
    assert context.build_context(again) == frozen
    context.open_assignment(again)
    again.refresh_from_db()
    assert again.context == frozen
    assert again.started_at == started
    assert [item["id"] for item in again.context["blocks"]["reqs"]["requirements"]["single"]] == first_ids


def test_open_assignment_leaves_read_only_assignments_alone():
    task = _task(n_apps=1)
    assignment = workbench_assignment(task)
    EvalTask.objects.filter(pk=task.pk).update(status=EvalTask.Status.PAUSED)
    assignment = Assignment.objects.select_related("task", "participant", "item", "item__app_a").get(pk=assignment.pk)
    context.open_assignment(assignment)
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.ASSIGNED
    assert assignment.context == {}
    assert context.portal_context(assignment) == {"blocks": {}, "hosting": {}, "instructions_sha256": ""}


def test_build_context_keeps_provenance_recorded_before_it():
    task = _task(n_apps=1)
    assignment = workbench_assignment(task, status=Assignment.Status.IN_PROGRESS)
    Assignment.objects.filter(pk=assignment.pk).update(context={"hosting": {"single": {"prior_users": 2}}})
    assignment.refresh_from_db()
    built = context.build_context(assignment)
    assert built["hosting"] == {"single": {"prior_users": 2}}
    assert "reqs" in built["blocks"]


def test_portal_context_withholds_hosting_provenance():
    task = _task(n_apps=1)
    assignment = workbench_assignment(task, status=Assignment.Status.IN_PROGRESS)
    context.build_context(assignment)
    assignment.context = {
        **assignment.context,
        "hosting": {"single": {"container_id": "abc", "container_name": "llm-1234abcd", "image": "x"}},
        "skip_hosting": {"single": {"state": "failed", "error": "boom", "extra": 1}},
    }
    payload = context.portal_context(assignment)
    assert payload["hosting"] == {}
    assert payload["skip_hosting"] == {"single": {"state": "failed", "error": "boom"}}
    assert payload["blocks"] == assignment.context["blocks"]


def test_finding_entry_and_slot_helpers():
    task = make_open_task(n_apps=2, mode="pairwise")
    item = task.items.first()
    assignment = workbench_assignment(task, item=item, swapped=True)
    slots = context.slot_apps(assignment)
    assert [slot for slot, _ in slots] == ["A", "B"]
    assert slots[0][1] == item.app_b
    assert context.app_for_slot(assignment, "B") == item.app_a
    assert context.slot_for_app(assignment, item.app_b) == "A"
    with pytest.raises(HumanEvalServiceError) as excinfo:
        context.app_for_slot(assignment, "single")
    assert excinfo.value.status == 404
    with pytest.raises(HumanEvalServiceError) as excinfo:
        context.finding_entry(assignment, "nope")
    assert excinfo.value.status == 404


# --- hosting provenance -------------------------------------------------------------


def _running_container(app: EvalTaskApp, **fields) -> ContainerInstance:
    container = ContainerInstanceFactory(
        generation_job=app.job,
        name=fields.pop("name", "llm-0a1b2c3d"),
        status=ContainerInstance.Status.RUNNING,
        image="backend/llm-0a1b2c3d:latest",
        **fields,
    )
    EvalTaskApp.objects.filter(pk=app.pk).update(container=container)
    app.refresh_from_db()
    return container


PROVENANCE_KEYS = {"container_id", "container_name", "image", "reset_at", "prior_users", "first_running_at"}


def test_record_hosting_provenance_stores_the_hosting_service_values_once(monkeypatch):
    task = _task(n_apps=1)
    app = _apps(task)[0]
    calls = []

    def fake_provenance(task_app, *, assignment=None):
        calls.append((task_app.pk, assignment.pk))
        return {
            "container_id": "c-1",
            "container_name": "llm-0a1b2c3d",
            "image": "backend/llm-0a1b2c3d:latest",
            "reset_at": "2026-11-02T10:00:00+00:00",
            "prior_users": 2 + len(calls),
            "first_running_at": "2026-11-02T10:05:00+00:00",
        }

    monkeypatch.setattr(hosting, "hosting_provenance", fake_provenance)
    assignment = workbench_assignment(task, status=Assignment.Status.IN_PROGRESS)
    context.build_context(assignment)
    context.record_hosting_provenance(assignment, "single", app)
    assignment.refresh_from_db()
    stored = assignment.context["hosting"]["single"]
    assert set(stored) == PROVENANCE_KEYS
    assert stored["prior_users"] == 3
    assert stored["reset_at"] == "2026-11-02T10:00:00+00:00"
    assert assignment.context["blocks"]  # the rest of the context is kept
    assert calls == [(app.pk, assignment.pk)]

    # Recorded once: later calls neither ask again nor overwrite.
    context.record_hosting_provenance(assignment, "single", app)
    fresh = Assignment.objects.select_related("item", "item__app_a").get(pk=assignment.pk)
    context.record_hosting_provenance(fresh, "single", app)
    fresh.refresh_from_db()
    assert fresh.context["hosting"]["single"] == stored
    assert len(calls) == 1


def test_record_hosting_provenance_with_the_hosting_service():
    task = _task(n_apps=1)
    app = _apps(task)[0]
    container = _running_container(app)
    assignment = workbench_assignment(task, status=Assignment.Status.IN_PROGRESS)
    context.record_hosting_provenance(assignment, "single", app)
    assignment.refresh_from_db()
    stored = assignment.context["hosting"]["single"]
    assert set(stored) == PROVENANCE_KEYS
    assert stored["container_name"] == container.name
    assert stored["image"] == "backend/llm-0a1b2c3d:latest"
    assert stored["prior_users"] == 0
    assert stored["first_running_at"]


def test_record_hosting_provenance_needs_a_running_app_the_right_slot_and_an_open_assignment():
    task = _task(n_apps=1)
    app = _apps(task)[0]
    assignment = workbench_assignment(task, status=Assignment.Status.IN_PROGRESS)
    context.record_hosting_provenance(assignment, "single", app)  # no container at all
    assignment.refresh_from_db()
    assert assignment.context == {}

    container = _running_container(app)
    ContainerInstance.objects.filter(pk=container.pk).update(status=ContainerInstance.Status.STOPPED)
    app.refresh_from_db()
    context.record_hosting_provenance(assignment, "single", app)
    assignment.refresh_from_db()
    assert assignment.context == {}

    ContainerInstance.objects.filter(pk=container.pk).update(status=ContainerInstance.Status.RUNNING)
    app.refresh_from_db()
    context.record_hosting_provenance(assignment, "A", app)  # wrong slot
    other_app = make_open_task(n_apps=1).apps.get()
    context.record_hosting_provenance(assignment, "single", other_app)  # not this assignment's app
    submitted = workbench_assignment(task, status=Assignment.Status.SUBMITTED)
    context.record_hosting_provenance(submitted, "single", app)
    assignment.refresh_from_db()
    submitted.refresh_from_db()
    assert assignment.context == {}
    assert submitted.context == {}


# --- regressions: review findings and the spec are frozen per app (C18) ---------------------------


def test_first_open_freezes_the_spec_and_review_findings_for_every_later_rater():
    from backend.human_eval.services import tasks as tasks_service
    from backend.human_eval.tests.factories import make_draft_task

    task = make_draft_task(n_apps=1, conditions=0, design=workbench_design(max_findings=5))
    app = task.apps.select_related("job").get()
    workbench_findings(app.job, "heval-sast-freeze", 3)
    task = tasks_service.transition(task, task.created_by, "open")
    app.refresh_from_db()
    assert app.snapshot["spec"] == context.spec_dict(app.job)
    assert {"checks", "spec_content_hash", "blinding_scan"} <= set(app.snapshot)  # the re-taken snapshot
    frozen = app.snapshot["review_findings"]
    assert [entry["id"] for entry in frozen] == app.finding_sample
    assert len(frozen) == 3
    assert all(set(entry) == REVIEW_FINDING_KEYS for entry in frozen)

    first = workbench_assignment(task)
    context.open_assignment(first)
    first.refresh_from_db()

    # Later the researcher deletes the analysis runs and even the generation run (task app job → NULL).
    AnalysisRun.objects.filter(generation_job_id=app.job_id).delete()
    GenerationRun.objects.filter(pk=app.job_id).delete()
    app.refresh_from_db()
    assert app.job_id is None

    second = workbench_assignment(task)
    second = Assignment.objects.select_related("task", "item", "item__app_a", "participant").get(pk=second.pk)
    context.open_assignment(second)
    second.refresh_from_db()
    assert second.context["blocks"]["sec"] == first.context["blocks"]["sec"]
    assert second.context["blocks"]["reqs"] == first.context["blocks"]["reqs"]
    assert len(second.context["blocks"]["sec"]["findings"]["single"]) == 3
    sources = {item["source"] for item in second.context["blocks"]["reqs"]["requirements"]["single"]}
    assert sources == {"frontend", "api", "custom"}  # the task's items survived the run's deletion
    assert context.app_spec_view(app)["name"] == app.snapshot["spec"]["title"]  # the participant's spec view too


def test_review_findings_survive_a_deleted_analysis_run():
    task = _task(n_apps=1)
    app = _apps(task)[0]
    findings = workbench_findings(app.job, "heval-sast-gone", 3)
    sample = context.finding_sample(app, _config(), task.seed)
    app.refresh_from_db()
    assert [entry["id"] for entry in app.snapshot["review_findings"]] == sample
    first = context.review_findings(app, _config(), task.seed)
    AnalysisRun.objects.filter(pk=findings[0].result.run_id).delete()
    fresh = EvalTaskApp.objects.select_related("job").get(pk=app.pk)
    assert context.review_findings(fresh, _config(), task.seed) == first
    assert [entry["id"] for entry in first] == sample


def test_a_sample_drawn_before_freezing_existed_is_frozen_at_its_next_use():
    task = _task(n_apps=1)
    app = _apps(task)[0]
    findings = workbench_findings(app.job, "heval-sast-legacy", 3)
    sample = context.finding_sample(app, _config(), task.seed)
    # A sample drawn by the old code: ids only, no frozen ReviewFinding dicts.
    legacy = {key: value for key, value in app.snapshot.items() if key != "review_findings"}
    EvalTaskApp.objects.filter(pk=app.pk).update(snapshot=legacy)
    gone = next(f for f in findings if str(f.id) == sample[0])
    gone.delete()  # already deleted before the first rater: skipped, as before

    fresh = EvalTaskApp.objects.select_related("job").get(pk=app.pk)
    listed = context.review_findings(fresh, _config(), task.seed)
    assert [entry["id"] for entry in listed] == sample[1:]
    stored = EvalTaskApp.objects.get(pk=app.pk).snapshot["review_findings"]
    assert stored == listed
    for finding in findings:
        if finding is not gone:
            finding.delete()
    assert context.review_findings(EvalTaskApp.objects.get(pk=app.pk), _config(), task.seed) == listed


def test_frozen_spec_prefers_the_snapshot_copy():
    task = _task(n_apps=1)
    app = _apps(task)[0]
    live = context.spec_dict(app.job)
    assert "spec" not in app.snapshot
    assert context.frozen_spec(app) == live  # apps without a frozen copy: the run's task
    frozen_ui = {**live["structured"], "ui": [{"id": "only", "text": "Only this"}]}
    app.snapshot = {**app.snapshot, "spec": {**live, "title": "Frozen name", "structured": frozen_ui}}
    assert context.frozen_spec(app)["title"] == "Frozen name"
    assert context.app_spec_view(app)["frontend_requirements"] == ["Only this"]
    app.snapshot = {**app.snapshot, "spec": None}  # the app had no spec at first open: never a later one
    assert context.frozen_spec(app) is None
    assert context.app_spec_view(app) is None
    app.snapshot = {key: value for key, value in app.snapshot.items() if key != "spec"}
    app.save(update_fields=["snapshot"])
    frozen_app = EvalTaskApp.objects.select_related("job").get(pk=app.pk)
    frozen_app.snapshot = {**frozen_app.snapshot, "spec": {"structured": {"ui": [{"id": "o", "text": "Only this"}]}}}
    frozen_app.save(update_fields=["snapshot"])
    assignment = workbench_assignment(task)
    built = context.build_context(assignment)
    texts = [item["text"] for item in built["blocks"]["reqs"]["requirements"]["single"]]
    assert texts == ["Only this", "The app has a README"]
