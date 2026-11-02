"""services/pool.py + snapshot.py and the app-pool API: candidate jobs, adding apps, app edits, snapshots, scoping."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.services import snapshot as snapshot_service
from backend.human_eval.services import source as source_service
from backend.human_eval.tests.factories import EvalTaskFactory
from backend.human_eval.tests.factories import make_draft_task
from backend.human_eval.tests.factories import make_eval_jobs
from backend.human_eval.tests.factories import make_open_task
from backend.runtime.models import ContainerInstance
from backend.runtime.tests.factories import ContainerInstanceFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/human-eval"
TASK_APP_KEYS = {
    "id",
    "label",
    "job_id",
    "group_key",
    "position",
    "is_active",
    "model_name",
    "model_id",
    "condition_label",
    "app_name",
    "app_slug",
    "repeat_index",
    "functional",
    "hosting",
    "stats",
}


@pytest.fixture
def researcher():
    return UserFactory()


@pytest.fixture
def api(client, researcher):
    client.force_login(researcher)
    return client


def _retry(job: GenerationJob, *, status=GenerationJob.Status.COMPLETED, minutes: int = 5) -> GenerationJob:
    """A newer attempt of the same experiment cell."""
    retry = GenerationJobFactory(
        created_by=job.created_by,
        mode=job.mode,
        status=status,
        model=job.model,
        experiment=job.experiment,
        condition=job.condition,
        repeat_index=job.repeat_index,
        app_requirement=job.app_requirement,
        resolved_bundle=job.resolved_bundle,
        metrics=job.metrics,
    )
    GenerationJob.objects.filter(pk=retry.pk).update(created_at=job.created_at + dt.timedelta(minutes=minutes))
    retry.refresh_from_db()
    return retry


def _candidate_ids(api, **query) -> list[str]:
    res = api.get(f"{API}/candidate-jobs/", query)
    assert res.status_code == 200, res.content
    return [row["id"] for row in res.json()["items"]]


# --- candidate jobs ----------------------------------------------------------------


def test_candidate_jobs_are_scoped_to_the_owner_and_current_attempts(api, researcher):
    experiment, _, jobs = make_eval_jobs(researcher, 3, conditions=2)  # cells (c0,r0) (c1,r0) (c0,r1)
    retry_ok = _retry(jobs[0])  # supersedes jobs[0]
    _retry(jobs[1], status=GenerationJob.Status.FAILED)  # newest attempt failed: the cell offers nothing
    GenerationJobFactory(created_by=researcher, status=GenerationJob.Status.PENDING)
    make_eval_jobs(UserFactory(), 2, conditions=1)  # someone else's jobs

    assert set(_candidate_ids(api)) == {str(retry_ok.id), str(jobs[2].id)}
    assert set(_candidate_ids(api, current_only="false")) == {
        str(jobs[0].id),
        str(retry_ok.id),
        str(jobs[1].id),
        str(jobs[2].id),
    }
    assert set(_candidate_ids(api, experiment_id=str(experiment.id))) == {str(retry_ok.id), str(jobs[2].id)}
    assert _candidate_ids(api, experiment_id="not-a-uuid") == []


def test_staff_see_every_users_candidate_jobs(client):
    _, _, foreign = make_eval_jobs(UserFactory(), 1, conditions=0)
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    assert str(foreign[0].id) in _candidate_ids(client)


def test_candidate_job_payload_and_filters(api, researcher):
    experiment, conditions, jobs = make_eval_jobs(researcher, 2, conditions=2)
    ContainerInstanceFactory(generation_job=jobs[0], status=ContainerInstance.Status.RUNNING, name="llm-0a1b2c3d")
    ContainerInstanceFactory(generation_job=jobs[1], status=ContainerInstance.Status.RUNNING, name="analyzer-x1")
    res = api.get(f"{API}/candidate-jobs/", {"per_page": 500})
    body = res.json()
    assert body["per_page"] == 100  # clamped
    rows = {row["id"]: row for row in body["items"]}
    first = rows[str(jobs[0].id)]
    assert first["running"] is True
    assert rows[str(jobs[1].id)]["running"] is False  # only llm-* app containers count
    assert first["functional"] == {"status": "passed", "pass_rate": 1.0, "deployed": True, "source": "conformance"}
    assert first["experiment"] == {"id": str(experiment.id), "name": experiment.name}
    assert first["condition"] == {"id": conditions[0].id, "label": conditions[0].label}
    assert first["app_slug"] == jobs[0].app_requirement.slug
    assert first["model_id"] == jobs[0].model.model_id
    assert set(_candidate_ids(api, model_id=jobs[1].model.model_id)) == {str(jobs[1].id)}
    assert set(_candidate_ids(api, app_slug=jobs[0].app_requirement.slug)) == {str(j.id) for j in jobs}
    assert set(_candidate_ids(api, search=jobs[1].model.model_id[:6])) >= {str(jobs[1].id)}
    assert _candidate_ids(api, app_slug="nope") == []


def test_candidate_jobs_need_authentication(client):
    assert client.get(f"{API}/candidate-jobs/").status_code == 401


# --- adding apps ---------------------------------------------------------------------


def test_add_jobs_gives_light_snapshots_group_keys_and_provisional_labels(api, researcher):
    task = EvalTaskFactory(created_by=researcher)
    _, _, jobs = make_eval_jobs(researcher, 3, conditions=2)
    res = api.post(
        f"{API}/tasks/{task.id}/apps/",
        {"job_ids": [str(j.id) for j in jobs]},
        content_type="application/json",
    )
    assert res.status_code == 200, res.content
    body = res.json()
    assert [row["label"] for row in body] == ["App 01", "App 02", "App 03"]
    assert set(body[0]) == TASK_APP_KEYS
    assert body[0]["hosting"]["state"] == "idle"
    assert body[0]["stats"] == {"submitted": 0, "in_progress": 0, "skipped": 0, "unavailable_skips": 0}
    app = EvalTaskApp.objects.get(task=task, job=jobs[0])
    snap = app.snapshot
    assert snap["model_id"] == jobs[0].model.model_id
    assert snap["condition_id"] == jobs[0].condition_id
    assert snap["functional"]["deployed"] is True
    assert "checks" not in snap  # taken in full at first open
    assert app.group_key == f"{snap['app_slug']}:{snap['spec_content_hash'][:12]}"


def test_add_jobs_skips_duplicates_and_continues_numbering(api, researcher):
    task = make_draft_task(researcher, n_apps=3)
    _, _, extra = make_eval_jobs(researcher, 1, conditions=0)
    existing = list(task.apps.order_by("position"))
    api.delete(f"{API}/tasks/{task.id}/apps/{existing[1].id}/")
    payload = {"job_ids": [str(existing[0].job_id), str(extra[0].id), str(extra[0].id)]}
    res = api.post(f"{API}/tasks/{task.id}/apps/", payload, content_type="application/json")
    assert res.status_code == 200
    assert [row["label"] for row in res.json()] == ["App 01", "App 03", "App 04"]


def test_add_foreign_or_unfinished_jobs_leaves_nothing_behind(api, researcher):
    task = EvalTaskFactory(created_by=researcher)
    _, _, mine = make_eval_jobs(researcher, 1, conditions=0)
    _, _, foreign = make_eval_jobs(UserFactory(), 1, conditions=0)
    res = api.post(
        f"{API}/tasks/{task.id}/apps/",
        {"job_ids": [str(mine[0].id), str(foreign[0].id), "garbage"]},
        content_type="application/json",
    )
    assert res.status_code == 404
    body = res.json()
    assert body["code"] == "not_found"
    assert {error["path"] for error in body["errors"]} == {"job_ids.1", "job_ids.2"}
    assert not EvalTaskApp.objects.filter(task=task).exists()

    running = GenerationJobFactory(created_by=researcher, status=GenerationJob.Status.RUNNING)
    res = api.post(f"{API}/tasks/{task.id}/apps/", {"job_ids": [str(running.id)]}, content_type="application/json")
    assert res.status_code == 400
    assert res.json()["errors"][0]["path"] == "job_ids.0"
    assert not EvalTaskApp.objects.filter(task=task).exists()

    res = api.post(f"{API}/tasks/{task.id}/apps/", {}, content_type="application/json")
    assert res.status_code == 400


def test_add_experiment_adds_current_completed_attempts_and_links_it(api, researcher):
    task = EvalTaskFactory(created_by=researcher)
    experiment, _, jobs = make_eval_jobs(researcher, 3, conditions=2)
    retry = _retry(jobs[0])
    res = api.post(
        f"{API}/tasks/{task.id}/apps/",
        {"experiment_id": str(experiment.id)},
        content_type="application/json",
    )
    assert res.status_code == 200
    assert {row["job_id"] for row in res.json()} == {str(retry.id), str(jobs[1].id), str(jobs[2].id)}
    task.refresh_from_db()
    assert task.experiment_id == experiment.id

    other = EvalTaskFactory(created_by=researcher)
    res = api.post(
        f"{API}/tasks/{other.id}/apps/",
        {"experiment_id": str(experiment.id), "current_only": False},
        content_type="application/json",
    )
    assert len(res.json()) == 4


def test_add_foreign_experiment_is_404(api, researcher):
    task = EvalTaskFactory(created_by=researcher)
    experiment, _, _ = make_eval_jobs(UserFactory(), 1, conditions=1)
    res = api.post(
        f"{API}/tasks/{task.id}/apps/",
        {"experiment_id": str(experiment.id)},
        content_type="application/json",
    )
    assert res.status_code == 404
    assert not EvalTaskApp.objects.filter(task=task).exists()


def test_pool_is_frozen_after_open(api, researcher):
    task = make_open_task(researcher, n_apps=2)
    _, _, jobs = make_eval_jobs(researcher, 1, conditions=0)
    res = api.post(f"{API}/tasks/{task.id}/apps/", {"job_ids": [str(jobs[0].id)]}, content_type="application/json")
    assert res.status_code == 409
    assert res.json()["code"] == "design_frozen"
    assert task.apps.count() == 2


# --- editing / removing apps ----------------------------------------------------------------


def test_patch_app_label_in_draft_and_conflicts(api, researcher):
    task = make_draft_task(researcher, n_apps=2)
    first, second = task.apps.order_by("position")
    url = f"{API}/tasks/{task.id}/apps/{first.id}/"
    res = api.patch(url, {"label": "Alpha"}, content_type="application/json")
    assert res.status_code == 200
    assert res.json()["label"] == "Alpha"
    res = api.patch(url, {"label": second.label}, content_type="application/json")
    assert res.status_code == 400
    assert res.json()["errors"][0]["path"] == "label"


def test_patch_app_after_open_deactivates_items_but_label_is_frozen(api, researcher):
    task = make_open_task(researcher, n_apps=3)
    app = task.apps.order_by("position").first()
    url = f"{API}/tasks/{task.id}/apps/{app.id}/"
    res = api.patch(url, {"label": "Renamed"}, content_type="application/json")
    assert res.status_code == 409
    assert res.json()["code"] == "design_frozen"
    res = api.patch(url, {"is_active": False}, content_type="application/json")
    assert res.status_code == 200
    assert set(res.json()) == TASK_APP_KEYS
    assert res.json()["is_active"] is False
    assert not EvalItem.objects.get(task=task, app_a=app).is_active
    api.patch(url, {"is_active": True}, content_type="application/json")
    assert EvalItem.objects.get(task=task, app_a=app).is_active


def test_delete_app_only_before_freeze(api, researcher):
    draft = make_draft_task(researcher, n_apps=2)
    app = draft.apps.first()
    assert api.delete(f"{API}/tasks/{draft.id}/apps/{app.id}/").status_code == 204
    assert not EvalTaskApp.objects.filter(pk=app.pk).exists()
    opened = make_open_task(researcher, n_apps=2)
    app = opened.apps.first()
    res = api.delete(f"{API}/tasks/{opened.id}/apps/{app.id}/")
    assert res.status_code == 409
    assert res.json()["code"] == "design_frozen"
    assert EvalTaskApp.objects.filter(pk=app.pk).exists()


def test_nested_app_of_another_task_and_foreign_tasks_are_404(api, researcher):
    mine = make_draft_task(researcher, n_apps=1)
    other = make_draft_task(researcher, n_apps=1)
    other_app = other.apps.get()
    assert (
        api.patch(
            f"{API}/tasks/{mine.id}/apps/{other_app.id}/",
            {"is_active": False},
            content_type="application/json",
        ).status_code
        == 404
    )
    assert api.delete(f"{API}/tasks/{mine.id}/apps/{other_app.id}/").status_code == 404
    foreign = make_draft_task(UserFactory(), n_apps=1)
    assert api.get(f"{API}/tasks/{foreign.id}/apps/").status_code == 404
    assert api.get(f"{API}/tasks/not-a-uuid/apps/").status_code == 404
    other_app.refresh_from_db()
    assert other_app.is_active


# --- snapshots ---------------------------------------------------------------------------------


def test_functional_summary_shapes():
    summary = snapshot_service.functional_summary
    assert summary({}) == {"status": None, "pass_rate": None, "deployed": None, "source": None}
    infra = {"functional": {"source": "conformance", "status": "infra_error", "pass_rate": 0.0, "deployed": None}}
    assert summary(infra) == {"status": "infra_error", "pass_rate": None, "deployed": None, "source": "conformance"}
    legacy = {"functional": {"source": "conformance", "status": "build_failed", "pass_rate": 0.0}}
    assert summary(legacy)["deployed"] is False
    smoke = {"functional": {"passed": True, "pass_rate": 0.8}}
    assert summary(smoke) == {"status": None, "pass_rate": 0.8, "deployed": None, "source": "smoke"}


def _tool_result(job, slug, *, status=ToolResult.Status.COMPLETED, category="lint", summary=None, minutes=0):
    run = AnalysisRunFactory(generation_job=job, status=AnalysisRun.Status.COMPLETED, created_by=job.created_by)
    result = ToolResultFactory(run=run, tool_slug=slug, category=category, status=status, summary=summary or {})
    if minutes:
        ToolResult.objects.filter(pk=result.pk).update(created_at=timezone.now() + dt.timedelta(minutes=minutes))
    return result


def test_full_snapshot_adds_checks_categories_and_static_findings(researcher):
    _, _, [job] = make_eval_jobs(researcher, 1, conditions=1)
    AnalyzerToolFactory(slug="heval-lint", category="lint")
    AnalyzerToolFactory(slug="heval-na", category="lint")
    AnalyzerToolFactory(slug="heval-ai", category="ai", kind="ai")
    AnalyzerToolFactory(slug="heval-dast", category="security", analysis_mode="dynamic")
    AnalyzerToolFactory(slug="heval-deps", category="dependencies")
    old = _tool_result(job, "heval-lint")
    FindingFactory.create_batch(5, result=old, category="quality")
    latest = _tool_result(job, "heval-lint", minutes=5)
    FindingFactory(result=latest, category="quality")
    FindingFactory(result=latest, category="dependency")  # never counted
    _tool_result(job, "heval-na", status=ToolResult.Status.SKIPPED, summary={"skip_reason": "not_applicable"})
    FindingFactory(result=_tool_result(job, "heval-ai", category="ai"))
    FindingFactory(result=_tool_result(job, "heval-dast", category="security"))
    FindingFactory(result=_tool_result(job, "heval-deps", category="dependencies"), category="security")
    _tool_result(job, "heval-broken", status=ToolResult.Status.FAILED)

    snap = snapshot_service.job_snapshot(job)
    assert snap["static_findings"]["tools"] == ["heval-lint", "heval-na"]
    assert snap["static_findings"]["by_tool"] == {"heval-lint": 1, "heval-na": 0}
    assert snap["static_findings"]["count"] == 1
    assert isinstance(snap["static_findings"]["loc"], int)
    assert snap["checks"][0] == {"name": "GET /api/items", "category": "api", "passed": True}
    assert {check["category"] for check in snap["checks"]} == {"api", "admin"}
    assert snap["functional"]["by_category"]["api"] == {"passed": 3, "total": 3}
    assert snap["evaluator_version"] == "3"
    assert snap["harness"] == "opencode"
    assert len(snap["spec_content_hash"]) == 64
    assert snap["blinding_scan"] == {"source": [], "findings": []}


def test_model_tokens_and_scan():
    tokens = snapshot_service.model_tokens(
        {
            "provider": "OpenAI",
            "model_id": "openai/gpt-4o-mini:free",
            "model_name": "OpenAI: GPT-4o mini (2024-07-18)",
        },
    )
    assert {"openai", "gpt-4o-mini", "gpt", "gpt-4o mini"} <= set(tokens)
    assert not {"free", "mini", "meta"} & set(tokens)
    assert snapshot_service.scan_text(["# Generated by GPT-4o for you"], tokens) == ["gpt"]
    assert snapshot_service.scan_text(["chatgpts and gptx are fine", "<meta charset>"], tokens) == []
    assert snapshot_service.model_tokens({"provider": "local", "model_id": "local-ollama/qwen2.5-coder:7b"}) == [
        "qwen",
        "qwen2.5-coder",
    ]


@pytest.mark.parametrize(
    "result_data",
    [
        {"files": {"backend/app.py": "print('hi')\n", "/work/frontend/App.jsx": "x"}, "backend_code": "not shown"},
        # C44: an all-blank map is no code for the viewer, which shows the blobs instead.
        {"files": {"backend/__init__.py": "", "backend/models.py": "  \n"}, "backend_code": "a", "frontend_code": "b"},
        {"files": {"a.py": 3, "": "x"}, "frontend_code": "only the frontend blob"},
        {"files": {}, "content": "a single reply"},
        {"content": "   "},
        "not a dict",
    ],
)
def test_source_scan_reads_exactly_what_the_source_viewer_serves(result_data):
    job = SimpleNamespace(result_data=result_data)
    files = source_service.job_files(job)
    assert snapshot_service.source_texts(job) == [*files, *files.values()]


def test_source_scan_sees_blob_code_behind_an_all_blank_files_map():
    """C44: the viewer serves ``backend/app.py`` from ``backend_code`` here, so the scan must read it too."""
    job = SimpleNamespace(
        result_data={"files": {"backend/__init__.py": ""}, "backend_code": "# Generated by claude\n"},
    )
    assert [row["path"] for row in source_service.source_tree(job)["files"]] == ["backend/app.py"]
    assert snapshot_service.scan_text(snapshot_service.source_texts(job), ["anthropic", "claude"]) == ["claude"]


def test_source_scan_covers_file_paths_shown_in_the_tree():
    job = SimpleNamespace(result_data={"files": {"src/claude_client.py": "def ask():\n    return 1\n"}})
    assert snapshot_service.scan_text(snapshot_service.source_texts(job), ["claude"]) == ["claude"]


def test_group_key_for_apps_without_spec(researcher):
    job = GenerationJobFactory(created_by=researcher, status=GenerationJob.Status.COMPLETED)
    snap = snapshot_service.job_snapshot(job, full=False)
    assert snap["app_slug"] == ""
    assert snap["spec_content_hash"] is None
    assert snapshot_service.group_key(snap, "app-id") == "app-id"


def test_list_task_apps_excludes_preview_work_from_stats(api, researcher):
    from backend.human_eval.models import Assignment
    from backend.human_eval.models import Participant
    from backend.human_eval.tests.factories import AssignmentFactory
    from backend.human_eval.tests.factories import ParticipantFactory

    task = make_open_task(researcher, n_apps=1)
    item = task.items.get()
    rater = ParticipantFactory(task=task, number=1)
    preview = ParticipantFactory(task=task, number=2, kind=Participant.Kind.PREVIEW)
    AssignmentFactory(task=task, item=item, participant=rater, status=Assignment.Status.SUBMITTED)
    AssignmentFactory(task=task, item=item, participant=preview, status=Assignment.Status.SUBMITTED)
    [row] = api.get(f"{API}/tasks/{task.id}/apps/").json()
    assert row["stats"]["submitted"] == 1
    assert task.status == EvalTask.Status.OPEN
