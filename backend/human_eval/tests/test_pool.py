"""services/pool.py + snapshot.py and the app-pool API: candidate runs, adding apps, app edits, snapshots, scoping."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationRun
from backend.generation.models import choices
from backend.generation.runs.view import run_view
from backend.generation.tests.factories import GenerationRunFactory
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.services import snapshot as snapshot_service
from backend.human_eval.services import source as source_service
from backend.human_eval.tests.factories import EvalTaskFactory
from backend.human_eval.tests.factories import add_app_files
from backend.human_eval.tests.factories import eval_run
from backend.human_eval.tests.factories import evaluate_run
from backend.human_eval.tests.factories import make_draft_task
from backend.human_eval.tests.factories import make_eval_runs
from backend.human_eval.tests.factories import make_open_task
from backend.runtime.models import ContainerInstance
from backend.runtime.tests.factories import ContainerInstanceFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/human-eval"
TASK_APP_KEYS = {
    "id",
    "label",
    "run_id",
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
#: ``EvalTaskApp.snapshot`` keys: pinned so analysis, exports and the frontend can rely on them.
LIGHT_SNAPSHOT_KEYS = {
    "run_id",
    "model_id",
    "model_name",
    "provider",
    "condition_id",
    "condition_label",
    "study_id",
    "study_name",
    "app_slug",
    "app_name",
    "task_ref",
    "prose_variant",
    "stack",
    "auth_mode",
    "unit_key",
    "repeat_index",
    "kind",
    "harness",
    "functional",
    "evaluator_version",
    "tester_version",
    "created_at",
    "spec_content_hash",
    "run_fingerprint",
}
FULL_SNAPSHOT_KEYS = LIGHT_SNAPSHOT_KEYS | {"checks", "static_findings", "blinding_scan"}


@pytest.fixture
def researcher():
    return UserFactory()


@pytest.fixture
def api(client, researcher):
    client.force_login(researcher)
    return client


def _retry(run: GenerationRun, *, minutes: int = 5, **fields) -> GenerationRun:
    """A later attempt of the same study cell (completed and tested unless ``fields`` say otherwise)."""
    retry = eval_run(
        run.created_by,
        task_version=run.task_version,
        model=run.model,
        study=run.study,
        condition=run.condition,
        cell=run.cell,
        repeat_index=run.repeat_index,
        **fields,
    )
    GenerationRun.objects.filter(pk=retry.pk).update(created_at=run.created_at + dt.timedelta(minutes=minutes))
    retry.refresh_from_db()
    return retry


def _candidate_ids(api, **query) -> list[str]:
    res = api.get(f"{API}/candidate-runs/", query)
    assert res.status_code == 200, res.content
    return [row["id"] for row in res.json()["items"]]


# --- candidate runs ----------------------------------------------------------------


def test_candidate_runs_are_scoped_to_the_owner_and_current_runs(api, researcher):
    study, _, runs = make_eval_runs(researcher, 4, conditions=2)  # cells (c0,r0) (c1,r0) (c0,r1) (c1,r1)
    # runs[0] is usable: a later usable attempt of its cell is NOT current (first usable wins).
    later = _retry(runs[0])
    # runs[1]'s cell: the first attempt failed to build, a later one is usable -> the later one is current.
    GenerationRun.objects.filter(pk=runs[1].pk).update(outcome=choices.Outcome.BUILD_FAIL, usable=False)
    fixed = _retry(runs[1])
    # runs[3]'s cell: the latest attempt is still running and none is usable -> the cell offers nothing.
    GenerationRun.objects.filter(pk=runs[3].pk).update(outcome=choices.Outcome.INFRA_ERROR, usable=False)
    _retry(runs[3], tested=False, status=choices.RunStatus.RUNNING)
    eval_run(researcher, task_version=runs[0].task_version, model=runs[0].model, beyond_plan=True)
    GenerationRunFactory(created_by=researcher, status=choices.RunStatus.PENDING)
    make_eval_runs(UserFactory(), 2, conditions=1)  # someone else's runs

    assert set(_candidate_ids(api)) == {str(runs[0].pk), str(fixed.pk), str(runs[2].pk)}
    assert set(_candidate_ids(api, current_only="false")) == {str(run.pk) for run in (*runs, later, fixed)}
    assert set(_candidate_ids(api, study_id=str(study.pk))) == {str(runs[0].pk), str(fixed.pk), str(runs[2].pk)}
    assert _candidate_ids(api, study_id="not-a-uuid") == []


def test_staff_see_every_users_candidate_runs(client):
    _, _, foreign = make_eval_runs(UserFactory(), 1, conditions=0)
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    assert str(foreign[0].pk) in _candidate_ids(client)


def test_candidate_run_payload_and_filters(api, researcher):
    study, conditions, runs = make_eval_runs(researcher, 2, conditions=2)
    ContainerInstanceFactory(generation_job=runs[0], status=ContainerInstance.Status.RUNNING, name="llm-0a1b2c3d")
    ContainerInstanceFactory(generation_job=runs[1], status=ContainerInstance.Status.RUNNING, name="analyzer-x1")
    res = api.get(f"{API}/candidate-runs/", {"per_page": 500})
    body = res.json()
    assert body["per_page"] == 100  # clamped
    rows = {row["id"]: row for row in body["items"]}
    first = rows[str(runs[0].pk)]
    assert first["running"] is True
    assert rows[str(runs[1].pk)]["running"] is False  # only llm-* app containers count
    assert first["functional"] == {"status": "tested", "pass_rate": 1.0, "deployed": True, "source": "eval_suite"}
    assert rows[str(runs[1].pk)]["functional"]["pass_rate"] == pytest.approx(5 / 6, abs=1e-4)
    assert first["study"] == {"id": str(study.pk), "name": study.name}
    assert first["condition"] == {"id": conditions[0].pk, "label": conditions[0].label}
    assert first["kind"] == "app_agentic"
    slug = runs[0].task_version.task.slug
    assert first["app_slug"] == slug
    assert first["model_id"] == runs[0].model.model_id
    assert set(_candidate_ids(api, model_id=runs[1].model.model_id)) == {str(runs[1].pk)}
    assert set(_candidate_ids(api, app_slug=slug)) == {str(run.pk) for run in runs}
    assert set(_candidate_ids(api, search=runs[1].model.model_id[:6])) >= {str(runs[1].pk)}
    assert _candidate_ids(api, app_slug="nope") == []


def test_candidate_runs_need_authentication(client):
    assert client.get(f"{API}/candidate-runs/").status_code == 401


# --- adding apps ---------------------------------------------------------------------


def test_add_runs_gives_light_snapshots_group_keys_and_provisional_labels(api, researcher):
    task = EvalTaskFactory(created_by=researcher)
    _, _, runs = make_eval_runs(researcher, 3, conditions=2)
    res = api.post(
        f"{API}/tasks/{task.id}/apps/",
        {"run_ids": [str(run.pk) for run in runs]},
        content_type="application/json",
    )
    assert res.status_code == 200, res.content
    body = res.json()
    assert [row["label"] for row in body] == ["App 01", "App 02", "App 03"]
    assert set(body[0]) == TASK_APP_KEYS
    assert body[0]["run_id"] == str(runs[0].pk)
    assert body[0]["hosting"]["state"] == "idle"
    assert body[0]["stats"] == {"submitted": 0, "in_progress": 0, "skipped": 0, "unavailable_skips": 0}
    app = EvalTaskApp.objects.get(task=task, job=runs[0])
    snap = app.snapshot
    assert set(snap) == LIGHT_SNAPSHOT_KEYS
    assert snap["model_id"] == runs[0].model.model_id
    assert snap["condition_id"] == runs[0].condition_id
    assert snap["functional"]["deployed"] is True
    assert "checks" not in snap  # taken in full at first open
    assert app.group_key == f"{snap['app_slug']}:{snap['spec_content_hash'][:12]}"


def test_add_runs_skips_duplicates_and_continues_numbering(api, researcher):
    task = make_draft_task(researcher, n_apps=3)
    _, _, extra = make_eval_runs(researcher, 1, conditions=0)
    existing = list(task.apps.order_by("position"))
    api.delete(f"{API}/tasks/{task.id}/apps/{existing[1].id}/")
    payload = {"run_ids": [str(existing[0].job_id), str(extra[0].pk), str(extra[0].pk)]}
    res = api.post(f"{API}/tasks/{task.id}/apps/", payload, content_type="application/json")
    assert res.status_code == 200
    assert [row["label"] for row in res.json()] == ["App 01", "App 03", "App 04"]


def test_add_foreign_or_unfinished_runs_leaves_nothing_behind(api, researcher):
    task = EvalTaskFactory(created_by=researcher)
    _, _, mine = make_eval_runs(researcher, 1, conditions=0)
    _, _, foreign = make_eval_runs(UserFactory(), 1, conditions=0)
    res = api.post(
        f"{API}/tasks/{task.id}/apps/",
        {"run_ids": [str(mine[0].pk), str(foreign[0].pk), "garbage"]},
        content_type="application/json",
    )
    assert res.status_code == 404
    body = res.json()
    assert body["code"] == "not_found"
    assert {error["path"] for error in body["errors"]} == {"run_ids.1", "run_ids.2"}
    assert not EvalTaskApp.objects.filter(task=task).exists()

    running = GenerationRunFactory(created_by=researcher, status=choices.RunStatus.RUNNING)
    res = api.post(f"{API}/tasks/{task.id}/apps/", {"run_ids": [str(running.pk)]}, content_type="application/json")
    assert res.status_code == 400
    assert res.json()["errors"][0]["path"] == "run_ids.0"
    assert not EvalTaskApp.objects.filter(task=task).exists()

    res = api.post(f"{API}/tasks/{task.id}/apps/", {}, content_type="application/json")
    assert res.status_code == 400


def test_reference_runs_are_never_candidates(api, researcher):
    task = EvalTaskFactory(created_by=researcher)
    reference = GenerationRunFactory(created_by=researcher, reference=True, status=choices.RunStatus.COMPLETED)
    assert str(reference.pk) not in _candidate_ids(api, current_only="false")
    res = api.post(f"{API}/tasks/{task.id}/apps/", {"run_ids": [str(reference.pk)]}, content_type="application/json")
    assert res.status_code == 404


def test_add_study_adds_current_completed_runs_and_links_it(api, researcher):
    task = EvalTaskFactory(created_by=researcher)
    study, _, runs = make_eval_runs(researcher, 3, conditions=2)
    GenerationRun.objects.filter(pk=runs[0].pk).update(outcome=choices.Outcome.START_FAIL, usable=False)
    retry = _retry(runs[0])
    res = api.post(
        f"{API}/tasks/{task.id}/apps/",
        {"study_id": str(study.pk)},
        content_type="application/json",
    )
    assert res.status_code == 200, res.content
    assert {row["run_id"] for row in res.json()} == {str(retry.pk), str(runs[1].pk), str(runs[2].pk)}
    task.refresh_from_db()
    assert task.study_id == study.pk

    other = EvalTaskFactory(created_by=researcher)
    res = api.post(
        f"{API}/tasks/{other.id}/apps/",
        {"study_id": str(study.pk), "current_only": False},
        content_type="application/json",
    )
    assert len(res.json()) == 4


def test_add_foreign_study_is_404(api, researcher):
    task = EvalTaskFactory(created_by=researcher)
    study, _, _ = make_eval_runs(UserFactory(), 1, conditions=1)
    res = api.post(
        f"{API}/tasks/{task.id}/apps/",
        {"study_id": str(study.pk)},
        content_type="application/json",
    )
    assert res.status_code == 404
    assert not EvalTaskApp.objects.filter(task=task).exists()


def test_pool_is_frozen_after_open(api, researcher):
    task = make_open_task(researcher, n_apps=2)
    _, _, runs = make_eval_runs(researcher, 1, conditions=0)
    res = api.post(f"{API}/tasks/{task.id}/apps/", {"run_ids": [str(runs[0].pk)]}, content_type="application/json")
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


def test_snapshot_keys_are_stable_for_a_factory_run(researcher):
    """Analysis, exports and the frontend read these keys: a factory run gives exactly them, twice the same."""
    _, _, [run] = make_eval_runs(researcher, 1, conditions=1)
    light = snapshot_service.run_snapshot(run, full=False)
    assert set(light) == LIGHT_SNAPSHOT_KEYS
    assert set(light["functional"]) == set(snapshot_service.FUNCTIONAL_KEYS)
    full = snapshot_service.run_snapshot(run)
    assert set(full) == FULL_SNAPSHOT_KEYS
    assert set(full["functional"]) == {*snapshot_service.FUNCTIONAL_KEYS, "by_category"}
    assert set(full["static_findings"]) == {"count", "loc", "loc_model", "tools", "by_tool"}
    assert set(full["blinding_scan"]) == {"source", "findings"}
    assert snapshot_service.run_snapshot(run) == full
    assert snapshot_service.run_snapshot(run, full=False) == light
    assert tuple(light) == snapshot_service.BASE_KEYS
    view = run_view(run)
    assert light["run_id"] == str(run.pk)
    assert light["app_slug"] == view.task.slug
    assert light["app_name"] == view.task.title
    assert light["task_ref"] == view.task.ref
    assert light["prose_variant"] == "standard"
    assert light["kind"] == "app_agentic"
    assert light["harness"] == "opencode"
    assert light["auth_mode"] == "contract_bearer"
    assert light["study_id"] == str(run.study_id)
    assert light["study_name"] == run.study.name
    assert light["condition_label"] == run.condition.label
    assert light["spec_content_hash"] == run.task_hash
    assert light["evaluator_version"] == view.latest_evaluation().eval_suite.ref
    assert light["tester_version"] == 1


def test_functional_summary_never_reports_missing_data_as_zero(researcher):
    """No evaluation, a not-measured or an errored evaluation: pass_rate None, never 0 (Appendix A)."""
    summary = snapshot_service.functional_summary
    _, _, [seed] = make_eval_runs(researcher, 1, conditions=0)
    run = eval_run(researcher, task_version=seed.task_version, model=seed.model)
    view = run_view(run)
    assert summary(view, None) == {"status": None, "pass_rate": None, "deployed": None, "source": None}
    not_measured = evaluate_run(
        run,
        status=choices.EvaluationStatus.NOT_MEASURED,
        result={
            "tester_version": 1,
            "compiler_version": 1,
            "auth_mode": "contract_bearer",
            "metrics": {"infra_ok": False, "agent_checks": 0, "agent_passed": 0, "agent_pass_rate": None},
            "measured": False,
            "not_measured_reason": "tester_error",
        },
        stages={"start": {"status": "ok"}, "tester": {"status": "error"}},
    )
    assert summary(view, not_measured) == {
        "status": "not_measured",
        "pass_rate": None,
        "deployed": True,
        "source": "eval_suite",
    }
    errored = evaluate_run(run, status=choices.EvaluationStatus.ERROR, result=None, stages={})
    assert summary(view, errored)["pass_rate"] is None
    assert summary(view, errored)["status"] == "error"
    GenerationRun.objects.filter(pk=run.pk).update(outcome=choices.Outcome.BUILD_FAIL, usable=False)
    run.refresh_from_db()
    failed_view = run_view(run)
    assert summary(failed_view, None) == {"status": "build_fail", "pass_rate": None, "deployed": False, "source": None}
    assert summary(failed_view, errored)["status"] == "build_fail"
    assert summary(failed_view, errored)["deployed"] is False


def _tool_result(run, slug, *, status=ToolResult.Status.COMPLETED, category="lint", summary=None, minutes=0):
    analysis = AnalysisRunFactory(generation_job=run, status=AnalysisRun.Status.COMPLETED, created_by=run.created_by)
    result = ToolResultFactory(run=analysis, tool_slug=slug, category=category, status=status, summary=summary or {})
    if minutes:
        ToolResult.objects.filter(pk=result.pk).update(created_at=timezone.now() + dt.timedelta(minutes=minutes))
    return result


def test_full_snapshot_adds_checks_categories_and_static_findings(researcher):
    _, _, runs = make_eval_runs(researcher, 2, conditions=1)
    run = runs[1]  # odd apps fail the admin endpoint
    AnalyzerToolFactory(slug="heval-lint", category="lint")
    AnalyzerToolFactory(slug="heval-na", category="lint")
    AnalyzerToolFactory(slug="heval-ai", category="ai", kind="ai")
    AnalyzerToolFactory(slug="heval-dast", category="security", analysis_mode="dynamic")
    AnalyzerToolFactory(slug="heval-deps", category="dependencies")
    old = _tool_result(run, "heval-lint")
    FindingFactory.create_batch(5, result=old, category="quality")
    latest = _tool_result(run, "heval-lint", minutes=5)
    FindingFactory(result=latest, category="quality")
    FindingFactory(result=latest, category="dependency")  # never counted
    _tool_result(run, "heval-na", status=ToolResult.Status.SKIPPED, summary={"skip_reason": "not_applicable"})
    FindingFactory(result=_tool_result(run, "heval-ai", category="ai"))
    FindingFactory(result=_tool_result(run, "heval-dast", category="security"))
    FindingFactory(result=_tool_result(run, "heval-deps", category="dependencies"), category="security")
    _tool_result(run, "heval-broken", status=ToolResult.Status.FAILED)

    snap = snapshot_service.run_snapshot(run)
    # A tool without applicable files did not measure the app: left out, never a zero (spec 8.8).
    assert snap["static_findings"]["tools"] == ["heval-lint"]
    assert snap["static_findings"]["by_tool"] == {"heval-lint": 1}
    assert snap["static_findings"]["count"] == 1
    assert snap["static_findings"]["loc"] == 120
    assert snap["static_findings"]["loc_model"] == 120
    assert {"name": "GET /api/items", "category": "api", "passed": True, "endpoint_id": "items.list"} in snap["checks"]
    assert {"name": "GET /api/admin/users", "category": "admin", "passed": False, "endpoint_id": "admin.users"} in (
        snap["checks"]
    )
    assert {check["category"] for check in snap["checks"]} == {"api", "admin"}
    assert snap["functional"]["by_category"] == {"functional": {"passed": 5, "total": 6}}
    assert snap["harness"] == "opencode"
    assert len(snap["spec_content_hash"]) == 64
    assert snap["blinding_scan"] == {"source": [], "findings": []}


def test_endpoint_checks_ignore_flaky_errored_and_unknown_checks():
    task = {
        "structured": {
            "endpoints": [
                {"id": "a.list", "method": "get", "path": "/api/a", "auth": "user"},
                {"id": "a.admin", "method": "GET", "path": "/api/admin/a", "auth": "admin"},
            ],
        },
    }
    result = {
        "checks": [
            {"id": "c1", "endpoint": "a.list", "status": "pass"},
            {"id": "c2", "endpoint": "a.list", "status": "fail"},
            {"id": "c3", "endpoint": "a.admin", "status": "error"},  # not measured: no verdict
            {"id": "c4", "endpoint": "nope", "status": "pass"},
            {"id": "c5", "endpoint": "a.admin", "status": "fail"},  # flaky: excluded
        ],
        "flaky_checks": ["c5"],
    }
    assert snapshot_service.endpoint_checks(result, task) == [
        {"name": "GET /api/a", "category": "api", "passed": False, "endpoint_id": "a.list"},
    ]
    assert snapshot_service.endpoint_checks({}, None) == []


def test_snapshot_of_a_run_without_tree_reports_unknown_loc(researcher):
    _, _, [run] = make_eval_runs(researcher, 1, conditions=0)
    bare = eval_run(researcher, task_version=run.task_version, model=run.model, tree_hash="")
    snap = snapshot_service.run_snapshot(bare)
    assert snap["static_findings"]["loc"] is None  # not collected: never a silent 0
    assert snap["functional"]["status"] is None
    assert snap["functional"]["pass_rate"] is None


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


def test_source_scan_reads_exactly_what_the_source_viewer_serves(researcher):
    _, _, [run] = make_eval_runs(researcher, 1, conditions=0)
    files = source_service.run_files(run)
    assert list(files) == ["backend/app.py", "frontend/src/App.jsx"]
    assert snapshot_service.source_texts(run) == [*files, *files.values()]
    assert snapshot_service.source_texts(None) == []


def test_source_scan_covers_file_paths_shown_in_the_tree(researcher):
    _, _, [run] = make_eval_runs(researcher, 1, conditions=0)
    bare = eval_run(researcher, task_version=run.task_version, model=run.model)
    add_app_files(bare, {"src/claude_client.py": ("def ask():\n    return 1\n", 2)})
    assert [row["path"] for row in source_service.source_tree(bare)["files"]] == ["src/claude_client.py"]
    assert snapshot_service.scan_text(snapshot_service.source_texts(bare), ["claude"]) == ["claude"]


def test_group_key_for_apps_without_task():
    snap = {"app_slug": "", "spec_content_hash": None}
    assert snapshot_service.group_key(snap, "app-id") == "app-id"
    assert snapshot_service.group_key({"app_slug": "todo", "spec_content_hash": "ab" * 32}, "x") == "todo:" + "ab" * 6


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
