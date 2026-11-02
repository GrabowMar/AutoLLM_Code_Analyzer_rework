"""services/tasks.py and the task API: create, list, PATCH + frozen rules, protocol log, status transitions
(first open), blockers / warnings, duplicate, delete, items, preview, owner scoping and rollback on 4xx."""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from django.utils import timezone

from backend.generation.models import GenerationJob
from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalInvite
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.models import Participant
from backend.human_eval.services import design as design_service
from backend.human_eval.services import tasks as tasks_service
from backend.human_eval.services.presets import PRESET_SLUGS
from backend.human_eval.services.presets import get_preset
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import EvalInviteFactory
from backend.human_eval.tests.factories import EvalTaskFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import make_draft_task
from backend.human_eval.tests.factories import make_eval_jobs
from backend.human_eval.tests.factories import make_open_task
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/human-eval"
JSON = "application/json"
DETAIL_KEYS = {
    "id",
    "title",
    "status",
    "mode",
    "counts",
    "experiment",
    "opens_at",
    "closes_at",
    "created_at",
    "updated_at",
    "description",
    "instructions",
    "design",
    "settings",
    "seed",
    "design_hash",
    "frozen_at",
    "opened_at",
    "closed_at",
    "allowed_actions",
    "open_blockers",
    "open_warnings",
    "first_closed_at",
    "protocol_log",
    "owner",
}


@pytest.fixture
def researcher():
    return UserFactory()


@pytest.fixture
def api(client, researcher):
    client.force_login(researcher)
    return client


@pytest.fixture
def stopped(monkeypatch):
    """Record hosting.stop_task_apps calls (the hosting stub is replaced by C; no Docker in tests)."""
    calls: list[str] = []
    monkeypatch.setattr(tasks_service.hosting, "stop_task_apps", lambda task: calls.append(str(task.id)) or 0)
    return calls


def _status(api, task, action):
    return api.post(f"{API}/tasks/{task.id}/status/", {"action": action}, content_type=JSON)


def _patch(api, task, payload):
    return api.patch(f"{API}/tasks/{task.id}/", payload, content_type=JSON)


# --- catalog -----------------------------------------------------------------------------


def test_presets_and_validate_design(api):
    res = api.get(f"{API}/presets/")
    assert res.status_code == 200
    assert [preset["slug"] for preset in res.json()] == list(PRESET_SLUGS)

    payload = {"mode": "single", "design": design_service.default_design("single"), "settings": {}}
    body = api.post(f"{API}/validate-design/", payload, content_type=JSON).json()
    assert body["ok"] is True
    assert body["errors"] == []
    assert body["settings"]["hosting"]["reset"] == "none"

    bad = {
        "mode": "pairwise",
        "design": design_service.default_design("single"),
        "settings": {"hosting": {"reset": "per_assignment"}},
    }
    body = api.post(f"{API}/validate-design/", bad, content_type=JSON).json()
    assert body["ok"] is False
    assert body["design"] is None
    paths = {error["path"] for error in body["errors"]}
    assert any(path.startswith("design.") for path in paths)
    assert "settings.hosting.reset" in paths


# --- create ---------------------------------------------------------------------------------


def test_create_from_a_preset_and_explicit_documents_win(api, researcher):
    res = api.post(
        f"{API}/tasks/",
        {"title": "Security study", "mode": "single", "preset": "security-assessment"},
        content_type=JSON,
    )
    assert res.status_code == 201, res.content
    body = res.json()
    assert set(body) == DETAIL_KEYS
    preset = get_preset("security-assessment")
    assert body["design"] == preset["design"]
    assert body["settings"]["hosting"]["reset"] == "per_assignment"
    assert body["status"] == "draft"
    assert body["owner"] == {"email": researcher.email, "name": researcher.name}

    res = api.post(
        f"{API}/tasks/",
        {
            "title": "Mixed",
            "mode": "single",
            "preset": "security-assessment",
            "design": design_service.default_design("single"),
            "settings": {"allocation": {"ratings_per_item": 5}},
        },
        content_type=JSON,
    )
    body = res.json()
    assert body["design"] == design_service.default_design("single")
    assert body["settings"]["allocation"]["ratings_per_item"] == 5
    assert body["settings"]["hosting"]["isolation"] == "exclusive"  # preset settings kept under the override


def test_create_validation_errors_leave_nothing_behind(api, researcher):
    def post(payload):
        return api.post(f"{API}/tasks/", payload, content_type=JSON)

    res = post({"title": "  ", "mode": "sideways"})
    assert res.status_code == 400
    assert {error["path"] for error in res.json()["errors"]} >= {"title", "mode"}

    res = post({"title": "T", "mode": "pairwise", "design": design_service.default_design("single")})
    assert res.status_code == 400
    assert all(error["path"].startswith("design") for error in res.json()["errors"])

    assert post({"title": "T", "mode": "single", "preset": "nope"}).status_code == 400
    assert post({"title": "T", "mode": "pairwise", "preset": "app-grading"}).json()["errors"][0]["path"] == "preset"
    res = post(
        {"title": "T", "mode": "single", "opens_at": "2026-12-01T10:00:00Z", "closes_at": "2026-11-01T10:00:00Z"},
    )
    assert res.json()["errors"][0]["path"] == "closes_at"

    foreign_experiment, _, foreign_jobs = make_eval_jobs(UserFactory(), 1, conditions=1)
    assert post({"title": "T", "mode": "single", "experiment_id": str(foreign_experiment.id)}).status_code == 404
    _, _, mine = make_eval_jobs(researcher, 1, conditions=0)
    res = post({"title": "T", "mode": "single", "job_ids": [str(mine[0].id), str(foreign_jobs[0].id)]})
    assert res.status_code == 404
    assert not EvalTask.objects.filter(created_by=researcher).exists()
    assert not EvalTaskApp.objects.filter(job=mine[0]).exists()


def test_create_with_an_experiment_adds_its_current_attempts_plus_listed_jobs(api, researcher):
    experiment, _, jobs = make_eval_jobs(researcher, 3, conditions=2)
    _, _, outside = make_eval_jobs(researcher, 1, conditions=0)
    body = api.post(
        f"{API}/tasks/",
        {"title": "All", "mode": "single", "experiment_id": str(experiment.id)},
        content_type=JSON,
    ).json()
    assert body["experiment"] == {"id": str(experiment.id), "name": experiment.name}
    assert body["counts"]["apps"] == 3
    # experiment + extra jobs → union (the wizard links an experiment and adds individual apps)
    body = api.post(
        f"{API}/tasks/",
        {
            "title": "Union",
            "mode": "single",
            "experiment_id": str(experiment.id),
            "job_ids": [str(jobs[1].id), str(outside[0].id)],
        },
        content_type=JSON,
    ).json()
    task = EvalTask.objects.get(pk=body["id"])
    assert set(task.apps.values_list("job_id", flat=True)) == {job.id for job in jobs} | {outside[0].id}
    # an explicit empty list links the experiment without adding its apps
    body = api.post(
        f"{API}/tasks/",
        {"title": "Linked", "mode": "single", "experiment_id": str(experiment.id), "job_ids": []},
        content_type=JSON,
    ).json()
    assert body["experiment"]["id"] == str(experiment.id)
    assert body["counts"]["apps"] == 0


# --- list / detail / scoping -----------------------------------------------------------------


def test_list_is_owner_scoped_with_filters(api, client, researcher):
    EvalTaskFactory(created_by=researcher, title="Alpha study")
    EvalTaskFactory(created_by=researcher, title="Beta", status=EvalTask.Status.CLOSED)
    foreign = EvalTaskFactory(created_by=UserFactory(), title="Alpha foreign")
    body = api.get(f"{API}/tasks/").json()
    assert body["total"] == 2
    assert set(body["items"][0]) == DETAIL_KEYS & {
        "id",
        "title",
        "status",
        "mode",
        "counts",
        "experiment",
        "opens_at",
        "closes_at",
        "created_at",
        "updated_at",
    }
    assert [row["title"] for row in api.get(f"{API}/tasks/", {"status": "closed"}).json()["items"]] == ["Beta"]
    assert [row["title"] for row in api.get(f"{API}/tasks/", {"search": "alpha"}).json()["items"]] == ["Alpha study"]
    assert api.get(f"{API}/tasks/", {"status": "bogus"}).json()["total"] == 0
    assert api.get(f"{API}/tasks/{foreign.id}/").status_code == 404
    assert api.get(f"{API}/tasks/not-a-uuid/").status_code == 404
    for res in (
        _patch(api, foreign, {"title": "x"}),
        api.delete(f"{API}/tasks/{foreign.id}/"),
        _status(api, foreign, "open"),
        api.post(f"{API}/tasks/{foreign.id}/duplicate/"),
        api.get(f"{API}/tasks/{foreign.id}/items/"),
        api.post(f"{API}/tasks/{foreign.id}/preview/"),
    ):
        assert res.status_code == 404
    foreign.refresh_from_db()
    assert foreign.title == "Alpha foreign"

    client.logout()
    assert client.get(f"{API}/tasks/").status_code == 401
    client.force_login(UserFactory(is_staff=True))
    assert client.get(f"{API}/tasks/").json()["total"] >= 3


def test_draft_detail_blockers_and_allowed_actions(api, researcher):
    empty = EvalTaskFactory(created_by=researcher)
    body = api.get(f"{API}/tasks/{empty.id}/").json()
    assert body["allowed_actions"] == []
    assert "Add at least one active app" in body["open_blockers"]
    assert body["counts"]["target_ratings"] is None

    ready = make_draft_task(researcher, n_apps=2)
    body = api.get(f"{API}/tasks/{ready.id}/").json()
    assert body["open_blockers"] == []
    assert body["allowed_actions"] == ["open"]

    closed_access = make_draft_task(
        researcher,
        n_apps=1,
        settings={"access": {"allow_invite_links": False, "allow_access_codes": False, "allow_accounts": False}},
    )
    assert any("access" in blocker for blocker in api.get(f"{API}/tasks/{closed_access.id}/").json()["open_blockers"])


def test_pairwise_blockers(api, researcher):
    same_condition = make_draft_task(researcher, n_apps=2, mode="pairwise", conditions=1)
    blockers = api.get(f"{API}/tasks/{same_condition.id}/").json()["open_blockers"]
    assert any("No pair" in blocker for blocker in blockers)
    no_baseline = make_draft_task(
        researcher,
        n_apps=2,
        mode="pairwise",
        conditions=0,
        settings={"pairing": {"strategy": "vs_baseline"}},
    )
    blockers = api.get(f"{API}/tasks/{no_baseline.id}/").json()["open_blockers"]
    assert any("baseline" in blocker for blocker in blockers)
    res = _status(api, no_baseline, "open")
    assert res.status_code == 409
    assert res.json()["code"] == "open_blocked"


# --- PATCH -------------------------------------------------------------------------------------


def test_patch_draft_merges_settings_and_validates_first(api, researcher):
    task = make_draft_task(researcher, n_apps=1)
    pairwise_design = design_service.default_design("pairwise")
    res = _patch(
        api,
        task,
        {
            "title": "Renamed",
            "instructions": "Be thorough.",
            "settings": {"allocation": {"ratings_per_item": 7}},
            "opens_at": "2026-12-01T09:00:00Z",
        },
    )
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["title"] == "Renamed"
    assert body["settings"]["allocation"]["ratings_per_item"] == 7
    assert body["settings"]["allocation"]["strategy"] == "balanced"
    assert body["opens_at"].startswith("2026-12-01T09:00:00")
    assert body["protocol_log"] == []

    res = _patch(api, task, {"mode": "pairwise"})  # the stored single-mode design does not fit pairwise
    assert res.status_code == 400
    res = _patch(api, task, {"mode": "pairwise", "design": pairwise_design})
    assert res.status_code == 200
    assert res.json()["mode"] == "pairwise"

    res = _patch(api, task, {"title": "Again", "settings": {"allocation": {"ratings_per_item": 0}}})
    assert res.status_code == 400
    assert res.json()["errors"][0]["path"] == "settings.allocation.ratings_per_item"
    res = _patch(api, task, {"closes_at": "2026-11-30T09:00:00Z"})
    assert res.status_code == 400
    task.refresh_from_db()
    assert task.title == "Renamed"
    assert task.settings["allocation"]["ratings_per_item"] == 7
    assert task.closes_at is None


@pytest.mark.parametrize(
    "frozen",
    [
        {"allocation": {"ratings_per_item": 9}},
        {"allocation": {"strategy": "all"}},
        {"pairing": {"k": 4}},
        {"blinding": {"show_spec": False}},
        {"hosting": {"isolation": "exclusive"}},
    ],
)
def test_frozen_settings_cannot_change_after_open(api, researcher, frozen):
    task = make_open_task(researcher, n_apps=2)
    before = task.settings
    res = _patch(api, task, {"settings": frozen})
    assert res.status_code == 409
    body = res.json()
    assert body["code"] == "design_frozen"
    assert body["errors"][0]["path"].startswith("settings.")
    task.refresh_from_db()
    assert task.settings == before


def test_editable_settings_after_open(api, researcher):
    task = make_open_task(researcher, n_apps=2)
    editable = {
        "allocation": {
            "max_items_per_participant": 4,
            "assignment_timeout_minutes": 30,
            "allow_skip": False,
            "allow_overflow": True,
        },
        "access": {"allow_invite_links": False},
        "hosting": {"on_demand": False, "idle_stop_minutes": 10, "max_running_apps": 5},
        "quality": {"min_seconds_per_item": 30},
    }
    res = _patch(api, task, {"settings": editable})
    assert res.status_code == 200, res.content
    settings = res.json()["settings"]
    assert settings["allocation"]["max_items_per_participant"] == 4
    assert settings["hosting"]["idle_stop_minutes"] == 10
    assert settings["quality"]["min_seconds_per_item"] == 30
    task.refresh_from_db()
    assert task.design_hash == design_service.design_hash(task.mode, task.design, task.settings)


def test_quality_is_frozen_when_the_plan_excludes_speeders(api, researcher):
    design = design_service.default_design("single")
    design["analysis"]["exclusion_rules"]["speeding"] = True
    task = make_open_task(researcher, n_apps=2, design=design)
    res = _patch(api, task, {"settings": {"quality": {"min_seconds_per_item": 5}}})
    assert res.status_code == 409
    assert res.json()["errors"][0]["path"] == "settings.quality.min_seconds_per_item"


def test_design_and_mode_are_frozen_after_open(api, researcher):
    task = make_open_task(researcher, n_apps=2)
    changed = design_service.default_design("single")
    changed["blocks"][0]["title"] = "Different"
    assert _patch(api, task, {"design": changed}).json()["code"] == "design_frozen"
    assert _patch(api, task, {"mode": "pairwise"}).json()["code"] == "design_frozen"
    assert _patch(api, task, {"design": task.design}).status_code == 200  # unchanged is fine


def test_protocol_log_records_text_changes_after_open(api, researcher):
    task = make_open_task(researcher, n_apps=2)
    old_title, old_instructions = task.title, task.instructions
    res = _patch(api, task, {"title": "New title", "instructions": "New rules", "description": task.description})
    log = res.json()["protocol_log"]
    assert [entry["field"] for entry in log] == ["title", "instructions"]
    assert log[0]["old_sha256"] == hashlib.sha256(old_title.encode()).hexdigest()
    assert log[0]["new_sha256"] == hashlib.sha256(b"New title").hexdigest()
    assert log[1]["old_sha256"] == hashlib.sha256(old_instructions.encode()).hexdigest()
    assert log[0]["by"] == researcher.email
    assert _patch(api, task, {"title": "New title"}).json()["protocol_log"] == log


def test_experiment_ownership_is_checked_on_patch(api, researcher):
    task = make_draft_task(researcher, n_apps=1)
    foreign, _, _ = make_eval_jobs(UserFactory(), 1, conditions=1)
    assert _patch(api, task, {"experiment_id": str(foreign.id)}).status_code == 404
    assert _patch(api, task, {"experiment_id": "junk"}).status_code == 404
    mine, _, _ = make_eval_jobs(researcher, 1, conditions=1)
    assert _patch(api, task, {"experiment_id": str(mine.id)}).json()["experiment"]["id"] == str(mine.id)
    opened = make_open_task(researcher, n_apps=2)
    assert _patch(api, opened, {"experiment_id": str(mine.id)}).json()["code"] == "design_frozen"
    assert _patch(api, opened, {"experiment_id": None}).json()["code"] == "design_frozen"


# --- status transitions -----------------------------------------------------------------------


def test_first_open_retakes_snapshots_relabels_builds_items_and_draws_samples(api, researcher, monkeypatch):
    task = make_draft_task(researcher, n_apps=3)
    before = {str(app.id): app.label for app in task.apps.all()}
    drawn: list[str] = []
    real_draw = tasks_service.context_service.draw_finding_samples

    def draw(current):
        assert current.pk == task.pk
        assert EvalItem.objects.filter(task=current).count() == 3  # items exist before sampling
        drawn.append(str(current.pk))
        return real_draw(current)  # B2's sampler (no finding-review block here: nothing to draw)

    monkeypatch.setattr(tasks_service.context_service, "draw_finding_samples", draw)
    res = _status(api, task, "open")
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["status"] == "open"
    assert body["allowed_actions"] == ["pause", "close"]
    assert body["open_blockers"] == []
    assert drawn == [str(task.pk)]
    task.refresh_from_db()
    assert task.frozen_at is not None
    assert task.opened_at == task.frozen_at
    assert task.design_hash == design_service.design_hash(task.mode, task.design, task.settings)
    apps = list(task.apps.order_by("position"))
    assert [app.label for app in apps] == ["App 01", "App 02", "App 03"]
    assert set(before) == {str(app.id) for app in apps}
    for app in apps:
        assert {"checks", "static_findings", "spec_content_hash", "blinding_scan"} <= set(app.snapshot)
        assert "by_category" in app.snapshot["functional"]
        assert app.group_key == f"{app.snapshot['app_slug']}:{app.snapshot['spec_content_hash'][:12]}"
    assert EvalItem.objects.filter(task=task).count() == 3
    assert len(api.get(f"{API}/tasks/{task.id}/items/").json()) == 3


def test_open_blocked_or_failing_leaves_the_draft_untouched(api, researcher, monkeypatch):
    empty = EvalTaskFactory(created_by=researcher)
    res = _status(api, empty, "open")
    assert res.status_code == 409
    assert res.json()["errors"][0]["path"] == "open_blockers"
    empty.refresh_from_db()
    assert empty.status == EvalTask.Status.DRAFT
    assert empty.frozen_at is None

    task = make_draft_task(researcher, n_apps=2)
    labels = {app.id: app.label for app in task.apps.all()}

    def broken(current):
        msg = "not ready"
        raise NotImplementedError(msg)

    monkeypatch.setattr(tasks_service.context_service, "draw_finding_samples", broken)
    with pytest.raises(NotImplementedError):
        tasks_service.transition(task, researcher, "open")
    task.refresh_from_db()
    assert task.status == EvalTask.Status.DRAFT
    assert task.frozen_at is None
    assert not EvalItem.objects.filter(task=task).exists()
    for app in task.apps.all():
        assert app.label == labels[app.id]
        assert "checks" not in app.snapshot


def test_reopening_after_a_long_pause_restarts_idle_clocks(api, researcher):
    """C15: a pause longer than the assignment timeout must not expire the work that was open."""
    task = make_open_task(researcher, n_apps=2)
    participant = ParticipantFactory(task=task, number=1, status=Participant.Status.ACTIVE)
    long_ago = timezone.now() - timedelta(hours=5)
    assignment = AssignmentFactory(
        task=task,
        participant=participant,
        item=task.items.first(),
        status=Assignment.Status.IN_PROGRESS,
        last_activity_at=long_ago,
    )
    assert _status(api, task, "pause").status_code == 200
    assert _status(api, task, "open").status_code == 200
    assignment.refresh_from_db()
    assert assignment.last_activity_at > long_ago + timedelta(hours=4)
    from backend.human_eval.services import allocation

    assert allocation.expire_stale(EvalTask.objects.get(pk=task.pk)) == 0
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.IN_PROGRESS


def test_lifecycle_transitions(api, researcher, stopped):
    task = make_open_task(researcher, n_apps=2)
    task.apps.update(pinned=True)
    assert _status(api, task, "pause").json()["allowed_actions"] == ["open", "close"]
    assert _status(api, task, "open").json()["status"] == "open"
    body = _status(api, task, "close").json()
    assert body["status"] == "closed"
    assert body["allowed_actions"] == ["reopen", "archive"]
    assert body["closed_at"] is not None
    assert body["first_closed_at"] == body["closed_at"]
    assert stopped == [str(task.id)]
    assert not task.apps.filter(pinned=True).exists()
    first_closed = body["first_closed_at"]

    body = _status(api, task, "reopen").json()
    assert (body["status"], body["closed_at"], body["first_closed_at"]) == ("open", None, first_closed)
    body = _status(api, task, "close").json()
    assert body["first_closed_at"] == first_closed
    assert body["closed_at"] != first_closed
    body = _status(api, task, "archive").json()
    assert (body["status"], body["allowed_actions"]) == ("archived", ["reopen"])
    assert _status(api, task, "reopen").json()["status"] == "closed"

    res = _status(api, task, "pause")
    assert res.status_code == 409
    assert res.json()["code"] == "invalid_transition"
    assert _status(api, task, "explode").status_code == 400
    draft = make_draft_task(researcher, n_apps=1)
    assert _status(api, draft, "close").json()["code"] == "invalid_transition"


# --- duplicate / delete ------------------------------------------------------------------------


def test_duplicate_copies_design_settings_and_apps_only(api, researcher):
    task = make_open_task(researcher, n_apps=3)
    inactive = task.apps.order_by("position").last()
    inactive.is_active = False
    inactive.save(update_fields=["is_active"])
    ParticipantFactory(task=task, number=1)
    EvalInviteFactory(task=task)
    res = api.post(f"{API}/tasks/{task.id}/duplicate/")
    assert res.status_code == 201
    body = res.json()
    assert body["title"] == f"Copy of {task.title}"
    assert body["status"] == "draft"
    assert body["frozen_at"] is None
    assert body["design"] == task.design
    assert body["settings"] == design_service.merged_settings(task.settings)
    assert body["seed"] != task.seed
    copy = EvalTask.objects.get(pk=body["id"])
    assert set(copy.apps.values_list("job_id", flat=True)) == set(task.apps.values_list("job_id", flat=True))
    assert sorted(copy.apps.values_list("label", flat=True)) == ["App 01", "App 02", "App 03"]
    assert copy.apps.get(job_id=inactive.job_id).is_active is False
    assert not Participant.objects.filter(task=copy).exists()
    assert not EvalInvite.objects.filter(task=copy).exists()
    assert not EvalItem.objects.filter(task=copy).exists()


def test_delete_stops_apps_and_removes_everything(api, researcher, monkeypatch):
    released: list[list[str]] = []
    monkeypatch.setattr(
        tasks_service.hosting,
        "release_apps",
        lambda apps: released.append(sorted(str(app.pk) for app in apps)) or 0,
    )
    task = make_open_task(researcher, n_apps=2)
    app_ids = sorted(str(pk) for pk in task.apps.values_list("pk", flat=True))
    ParticipantFactory(task=task, number=1)
    res = api.delete(f"{API}/tasks/{task.id}/")
    assert res.status_code == 204
    assert released == [app_ids]
    assert not EvalTask.objects.filter(pk=task.pk).exists()
    assert not EvalTaskApp.objects.filter(task_id=task.pk).exists()
    assert not Participant.objects.filter(task_id=task.pk).exists()
    assert GenerationJob.objects.filter(created_by=researcher).count() == 2  # jobs are untouched


# --- warnings --------------------------------------------------------------------------------


def test_open_warnings(api, researcher):
    security = get_preset("security-assessment")
    shared = make_draft_task(researcher, n_apps=1, design=security["design"])
    assert "shared_app_state" in api.get(f"{API}/tasks/{shared.id}/").json()["open_warnings"]
    isolated = make_draft_task(
        researcher,
        n_apps=1,
        design=security["design"],
        settings={"hosting": {"isolation": "exclusive", "reset": "per_assignment"}},
    )
    assert api.get(f"{API}/tasks/{isolated.id}/").json()["open_warnings"] == []

    mixed = make_draft_task(researcher, n_apps=2, settings={"blinding": {"show_source_code": True}})
    _, second = mixed.apps.order_by("position")
    second.snapshot = {**second.snapshot, "spec_content_hash": "e" * 64, "mode": "scaffolding"}
    second.save(update_fields=["snapshot"])
    warnings = api.get(f"{API}/tasks/{mixed.id}/").json()["open_warnings"]
    assert "mixed_spec_versions" in warnings
    assert "layout_reveals_condition" in warnings


def test_model_name_in_shown_source_is_flagged_and_cached(api, researcher):
    task = make_draft_task(researcher, n_apps=1, settings={"blinding": {"show_source_code": True}})
    app = task.apps.get()
    job = app.job
    job.result_data = {"files": {"backend/app.py": "# Written by Claude for this exercise\n"}}
    job.save(update_fields=["result_data"])
    app.snapshot = {
        **app.snapshot,
        "provider": "Anthropic",
        "model_id": "anthropic/claude-3.5-sonnet",
        "model_name": "Anthropic: Claude 3.5 Sonnet",
    }
    app.save(update_fields=["snapshot"])
    assert "model_name_in_source" in api.get(f"{API}/tasks/{task.id}/").json()["open_warnings"]
    app.refresh_from_db()
    assert app.snapshot["blinding_scan"]["source"] == ["claude"]
    hidden = make_draft_task(researcher, n_apps=1)
    assert "model_name_in_source" not in api.get(f"{API}/tasks/{hidden.id}/").json()["open_warnings"]


def test_model_name_in_blob_code_behind_an_all_blank_files_map_is_flagged(api, researcher):
    """C44: participants see ``backend/app.py`` from ``backend_code`` (the blank map is no code), so it is scanned."""
    task = make_draft_task(researcher, n_apps=1, settings={"blinding": {"show_source_code": True}})
    app = task.apps.get()
    job = app.job
    job.result_data = {"files": {"backend/__init__.py": ""}, "backend_code": "# Generated by claude\n"}
    job.save(update_fields=["result_data"])
    app.snapshot = {
        **{key: value for key, value in app.snapshot.items() if key != "blinding_scan"},
        "provider": "Anthropic",
        "model_id": "anthropic/claude-3.5-sonnet",
        "model_name": "Anthropic: Claude 3.5 Sonnet",
    }
    app.save(update_fields=["snapshot"])
    assert "model_name_in_source" in api.get(f"{API}/tasks/{task.id}/").json()["open_warnings"]
    app.refresh_from_db()
    assert app.snapshot["blinding_scan"]["source"] == ["claude"]


# --- items / preview -------------------------------------------------------------------------


def test_items_endpoint(api, researcher):
    draft = make_draft_task(researcher, n_apps=1)
    assert api.get(f"{API}/tasks/{draft.id}/items/").json() == []
    task = make_open_task(researcher, n_apps=4, mode="pairwise")
    rows = api.get(f"{API}/tasks/{task.id}/items/").json()
    assert len(rows) == 4
    assert set(rows[0]) == {
        "id",
        "app_a",
        "app_b",
        "group_key",
        "is_active",
        "submitted",
        "in_progress",
        "skipped",
        "target",
    }
    assert rows[0]["app_b"]["label"].startswith("App ")


def test_preview_is_refused_on_drafts_and_reused(api, researcher):
    draft = make_draft_task(researcher, n_apps=1)
    res = api.post(f"{API}/tasks/{draft.id}/preview/")
    assert res.status_code == 409
    assert res.json()["code"] == "not_frozen"
    assert not Participant.objects.filter(task=draft).exists()

    task = make_open_task(researcher, n_apps=2)
    first = api.post(f"{API}/tasks/{task.id}/preview/").json()
    assert first["portal_path"] == f"/eval/t/{task.id}"
    again = api.post(f"{API}/tasks/{task.id}/preview/").json()
    assert again["participant_id"] == first["participant_id"]
    preview = Participant.objects.get(pk=first["participant_id"])
    assert (preview.kind, preview.status, preview.user_id) == (Participant.Kind.PREVIEW, "active", researcher.pk)
    assert preview.consent_at is not None
    assert api.get(f"{API}/tasks/{task.id}/").json()["counts"]["participants"] == 0
    assert api.delete(f"{API}/tasks/{task.id}/preview/").status_code == 204
    assert not Participant.objects.filter(pk=preview.pk).exists()

    ParticipantFactory(task=task, number=5, kind=Participant.Kind.ACCOUNT, user=researcher)
    res = api.post(f"{API}/tasks/{task.id}/preview/")
    assert res.status_code == 409
    assert res.json()["code"] == "already_participant"


def test_first_open_scans_sampled_findings_for_model_names(api, researcher, monkeypatch):
    from backend.analysis.tests.factories import FindingFactory

    design = design_service.default_design("single")
    design["blocks"].append(
        {"id": "sec", "type": "security", "title": "Security", "finding_review": {"enabled": True, "max_findings": 5}},
    )
    task = make_draft_task(researcher, n_apps=2, design=design)
    leaky = FindingFactory(code_snippet="# TODO(gpt): tidy this up", title="Hard-coded secret")
    clean = FindingFactory(code_snippet="password = 'admin'", title="Hard-coded password")

    def draw(current):
        """Stand-in for B2's sampler: one sampled finding per app."""
        first, second = EvalTaskApp.objects.filter(task=current).order_by("position")
        EvalTaskApp.objects.filter(pk=first.pk).update(finding_sample=[str(leaky.id)])
        EvalTaskApp.objects.filter(pk=second.pk).update(finding_sample=[str(clean.id)])
        return 2

    monkeypatch.setattr(tasks_service.context_service, "draw_finding_samples", draw)
    original_full = tasks_service.snapshot_service.full_snapshots

    def full(jobs):
        """The re-taken snapshots, all naming a known model (factory model names are random words)."""
        snapshots = original_full(jobs)
        for snap in snapshots.values():
            snap.update({"model_id": "openai/gpt-4o", "model_name": "OpenAI: GPT-4o", "provider": "OpenAI"})
        return snapshots

    monkeypatch.setattr(tasks_service.snapshot_service, "full_snapshots", full)
    body = _status(api, task, "open").json()
    assert "model_name_in_source" in body["open_warnings"]
    scans = {app.finding_sample[0]: app.snapshot["blinding_scan"] for app in task.apps.all()}
    assert scans[str(leaky.id)]["findings"] == ["gpt"]
    assert scans[str(clean.id)]["findings"] == []


def test_first_open_with_the_real_sampler_scans_the_drawn_findings(api, researcher):
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import ToolResult
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import AnalyzerToolFactory
    from backend.analysis.tests.factories import FindingFactory
    from backend.analysis.tests.factories import ToolResultFactory

    design = design_service.default_design("single")
    design["blocks"].append(
        {"id": "sec", "type": "security", "title": "Security", "finding_review": {"enabled": True, "max_findings": 5}},
    )
    task = make_draft_task(researcher, n_apps=1, conditions=1, design=design)
    app = task.apps.get()
    model = app.job.model
    model.model_id, model.model_name, model.provider = "anthropic/claude-test-1", "Anthropic: Claude Test", "Anthropic"
    model.save(update_fields=["model_id", "model_name", "provider"])
    AnalyzerToolFactory(slug="heval-sast", category="security")
    run = AnalysisRunFactory(generation_job=app.job, status=AnalysisRun.Status.COMPLETED, created_by=researcher)
    result = ToolResultFactory(
        run=run,
        tool_slug="heval-sast",
        category="security",
        status=ToolResult.Status.COMPLETED,
    )
    finding = FindingFactory(result=result, category="security", code_snippet="// claude added this check")

    res = _status(api, task, "open")
    assert res.status_code == 200, res.content
    app.refresh_from_db()
    assert app.finding_sample == [str(finding.id)]
    assert app.snapshot["blinding_scan"]["findings"] == ["claude"]
    assert app.snapshot["static_findings"]["by_tool"] == {"heval-sast": 1}
    assert "model_name_in_source" in res.json()["open_warnings"]
