"""Portal workbench API (api/views/portal_work.py): contract shapes, blinding, ownership, read-only, CSRF, source."""

from __future__ import annotations

import json
import re

import pytest
from django.conf import settings as django_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from backend.generation.models import GenerationJob
from backend.human_eval.models import Assignment
from backend.human_eval.models import Attachment
from backend.human_eval.models import EvalTask
from backend.human_eval.models import FindingVerdict
from backend.human_eval.models import Observation
from backend.human_eval.models import Participant
from backend.human_eval.models import Response
from backend.human_eval.services import access
from backend.human_eval.services import allocation
from backend.human_eval.services import context
from backend.human_eval.services import source
from backend.human_eval.services.instruments import SUS_QUESTION_IDS
from backend.human_eval.tests.factories import PNG_BYTES
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import make_open_task
from backend.human_eval.tests.factories import workbench_assignment
from backend.human_eval.tests.factories import workbench_design
from backend.human_eval.tests.factories import workbench_findings
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BASE = "/api/human-eval/portal"
CSRF_TOKEN = "b" * 32

PORTAL_ASSIGNMENT_KEYS = {
    "id",
    "task",
    "status",
    "position",
    "started_at",
    "submitted_at",
    "active_seconds",
    "read_only",
    "apps",
    "blocks",
    "context",
    "responses",
    "observations",
    "finding_verdicts",
    "allow_skip",
    "http_console",
}
PORTAL_APP_KEYS = {"slot", "label", "hosting", "spec", "admin_credentials", "has_source"}
HOSTING_KEYS = {"state", "url", "progress", "message", "error"}
ERROR_KEYS = {"detail", "code", "errors"}


def _client(participant: Participant | None = None, *, user=None, csrf: bool = False) -> Client:
    client = Client(enforce_csrf_checks=csrf)
    if user is not None:
        client.force_login(user)
    if participant is not None:
        client.cookies[access.cookie_name()] = access.issue_session(participant)
    return client


def _task(**kwargs) -> EvalTask:
    return make_open_task(n_apps=kwargs.pop("n_apps", 1), design=kwargs.pop("design", workbench_design()), **kwargs)


def _work(task: EvalTask | None = None, **assignment_fields) -> tuple[Assignment, Client]:
    task = task or _task()
    assignment = workbench_assignment(task, **assignment_fields)
    return assignment, _client(assignment.participant)


def _url(assignment: Assignment, suffix: str = "") -> str:
    return f"{BASE}/assignments/{assignment.id}/{suffix}"


def _put(client: Client, url: str, body: dict, **extra):
    return client.put(url, data=json.dumps(body), content_type="application/json", **extra)


def _patch(client: Client, url: str, body: dict, **extra):
    return client.patch(url, data=json.dumps(body), content_type="application/json", **extra)


def _post(client: Client, url: str, body: dict | None = None, **extra):
    return client.post(url, data=json.dumps(body or {}), content_type="application/json", **extra)


def _keys(value, path: str = ""):
    """Every (path, key) of a JSON document."""
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else key
            yield child, key
            yield from _keys(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _keys(item, f"{path}[{index}]")


def _open(client: Client, assignment: Assignment) -> dict:
    response = client.get(_url(assignment))
    assert response.status_code == 200, response.content
    return response.json()


# --- auth ---------------------------------------------------------------------


def test_workbench_requires_a_portal_identity():
    assignment, _ = _work()
    assert Client().get(_url(assignment)).status_code == 401
    assert _post(Client(), _url(assignment, "submit/")).status_code == 401


def test_unsafe_methods_need_the_csrf_token():
    assignment, _ = _work()
    client = _client(assignment.participant, csrf=True)
    body = {"slot": "single", "kind": "bug", "title": "Broken"}
    assert _post(client, _url(assignment, "observations/"), body).status_code == 403
    client.cookies[django_settings.CSRF_COOKIE_NAME] = CSRF_TOKEN
    response = _post(client, _url(assignment, "observations/"), body, HTTP_X_CSRFTOKEN=CSRF_TOKEN)
    assert response.status_code == 201


# --- assignment view ---------------------------------------------------------------


def test_get_assignment_opens_it_and_matches_the_contract():
    task = _task()
    workbench_findings(task.apps.get().job, "heval-sast-view", 2)
    assignment, client = _work(task)
    body = _open(client, assignment)
    assert set(body) == PORTAL_ASSIGNMENT_KEYS
    assert body["status"] == "in_progress"
    assert body["started_at"] is not None
    assert body["read_only"] is False
    assert body["allow_skip"] is True
    assert body["http_console"] is True
    assert body["task"] == {
        "id": str(task.id),
        "title": task.title,
        "mode": "single",
        "instructions": task.instructions,
    }
    (app,) = body["apps"]
    assert set(app) == PORTAL_APP_KEYS
    assert set(app["hosting"]) == HOSTING_KEYS
    assert app["slot"] == "single"
    assert app["label"] == "App 01"
    assert app["admin_credentials"] == {"username": "admin", "password": "admin"}
    assert app["spec"]["api_endpoints"][0]["method"] == "GET"
    assert app["has_source"] is False  # show_source_code is off by default
    assert [block["id"] for block in body["blocks"]] == ["ratings", "reqs", "sec", "tasks", "sus", "extra", "bugs"]
    assert "expected" not in json.dumps(body["blocks"])  # attention answers never reach participants
    assert set(body["context"]) == {"blocks", "hosting", "instructions_sha256"}
    assert len(body["context"]["blocks"]["sec"]["findings"]["single"]) == 2
    assert len(body["context"]["blocks"]["sus"]["questions"]) == 10
    assert body["responses"] == {}
    assert body["observations"] == []
    assert body["finding_verdicts"] == {}
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.IN_PROGRESS
    assert body["context"]["blocks"] == assignment.context["blocks"]

    # Opening again returns the same frozen context.
    assert _open(client, assignment)["context"] == body["context"]


def test_blinding_settings_hide_spec_and_credentials_and_show_source():
    task = _task(
        settings={"blinding": {"show_spec": False, "show_admin_credentials": False, "show_source_code": True}},
    )
    assignment, client = _work(task)
    (app,) = _open(client, assignment)["apps"]
    assert app["spec"] is None
    assert app["admin_credentials"] is None
    assert app["has_source"] is True


def test_portal_assignment_never_reveals_who_built_the_app():
    task = make_open_task(n_apps=2, mode="pairwise")
    item = task.items.get()
    for index, app in enumerate((item.app_a, item.app_b)):
        model = app.job.model
        model.model_id = f"zzsecretvendor/zz-model-{index}"
        model.model_name = f"ZzSecretModel {index}"
        model.provider = "ZzSecretProvider"
        model.save()
        app.job.condition.label = f"ZzCondition {index}"
        app.job.condition.save()
    task.experiment.name = "ZzExperimentName"
    task.experiment.save()
    assignment, client = _work(task, item=item)
    context.open_assignment(assignment)
    # Provenance with container details may sit in the stored context: it is never sent.
    stored = Assignment.objects.get(pk=assignment.pk)
    stored.context["hosting"] = {"A": {"container_id": "zzcontainer-id", "container_name": "llm-zz123456"}}
    stored.save(update_fields=["context"])

    response = client.get(_url(assignment))
    assert response.status_code == 200
    body = response.content.decode()
    payload = response.json()
    assert payload["context"]["hosting"] == {}
    assert [app["slot"] for app in payload["apps"]] == ["A", "B"]
    forbidden = [
        str(item.app_a.job_id),
        str(item.app_b.job_id),
        str(item.app_a_id),
        str(item.app_b_id),
        str(item.id),
        str(task.experiment_id),
        "zzsecretvendor",
        "ZzSecretModel",
        "ZzSecretProvider",
        "ZzCondition",
        "ZzExperimentName",
        "zzcontainer-id",
        "llm-zz123456",
    ]
    for value in forbidden:
        assert value not in body, value
    assert "model" not in {key for _, key in _keys(payload)}
    assert "condition_label" not in body


def test_no_normalized_severity_anywhere_in_portal_payloads():
    task = _task()
    job = task.apps.get().job
    workbench_findings(job, "heval-sast-sev", 3, severity="critical", tool_severity="")
    assignment, client = _work(task)
    body = _open(client, assignment)
    finding_id = body["context"]["blocks"]["sec"]["findings"]["single"][0]["id"]
    _put(client, _url(assignment, f"finding-verdicts/{finding_id}/"), {"verdict": "fp", "note": ""})
    _post(
        client,
        _url(assignment, "observations/"),
        {"slot": "single", "kind": "bug", "title": "x", "severity": "low"},
    )
    body = _open(client, assignment)
    severity_paths = [path for path, key in _keys(body) if key == "severity"]
    # The only "severity" is the tester's own rating on their observations.
    assert severity_paths == ["observations[0].severity"]
    assert body["observations"][0]["severity"] == "low"
    assert "critical" not in json.dumps(body["context"])
    for entry in body["context"]["blocks"]["sec"]["findings"]["single"]:
        assert entry["tool_severity"] == ""
    record = FindingVerdict.objects.get(assignment=assignment)
    assert "severity" not in record.finding_snapshot


def test_read_only_assignment_is_viewable_but_not_writable():
    assignment, client = _work()
    _open(client, assignment)
    EvalTask.objects.filter(pk=assignment.task_id).update(status=EvalTask.Status.PAUSED)
    body = _open(client, assignment)
    assert body["read_only"] is True
    for response in (
        _put(client, _url(assignment, "responses/ratings/"), {"answers": {}}),
        _post(client, _url(assignment, "submit/")),
        _post(client, _url(assignment, "skip/"), {"reason": "other"}),
        _post(client, _url(assignment, "observations/"), {"slot": "single", "kind": "bug", "title": "x"}),
    ):
        assert response.status_code == 409
        assert response.json() == {"detail": response.json()["detail"], "code": "read_only", "errors": []}
    assert not Response.objects.filter(assignment=assignment).exists()
    assert not Observation.objects.filter(assignment=assignment).exists()


# --- responses, submit, skip ------------------------------------------------------------


def test_autosave_and_its_errors():
    assignment, client = _work()
    response = _put(client, _url(assignment, "responses/ratings/"), {"answers": {"criteria": {"functionality": 6}}})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"block_id", "answers", "updated_at"}
    assert body["answers"] == {"criteria": {"functionality": 6}}

    bad = _put(client, _url(assignment, "responses/ratings/"), {"answers": {"criteria": {"functionality": [1]}}})
    assert bad.status_code == 400
    assert set(bad.json()) == ERROR_KEYS
    assert bad.json()["code"] == "invalid_answers"
    assert bad.json()["errors"] == [
        {"path": "ratings.criteria.functionality", "message": 'Must be a whole number on the scale or "na"'},
    ]
    assert _put(client, _url(assignment, "responses/nope/"), {"answers": {}}).status_code == 404
    assert _put(client, _url(assignment, "responses/ratings/"), {"answers": "text"}).status_code == 400
    assert Response.objects.get(assignment=assignment, block_id="ratings").answers == {
        "criteria": {"functionality": 6},
    }
    assert _open(client, assignment)["responses"]["ratings"]["answers"] == {"criteria": {"functionality": 6}}


def test_submit_incomplete_then_complete(monkeypatch):
    task = _task(n_apps=2)
    assignment, client = _work(task)
    following = workbench_assignment(
        task,
        participant=assignment.participant,
        item=task.items.exclude(pk=assignment.item_id).get(),
    )
    monkeypatch.setattr(allocation, "next_assignment", lambda participant: (following, "assigned"))
    body = _open(client, assignment)

    incomplete = _post(client, _url(assignment, "submit/"))
    assert incomplete.status_code == 400
    error = incomplete.json()
    assert set(error) == ERROR_KEYS
    assert error["code"] == "incomplete"
    assert all(set(entry) == {"path", "message"} for entry in error["errors"])
    assert "ratings.criteria.functionality" in {entry["path"] for entry in error["errors"]}
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.IN_PROGRESS

    requirement_ids = [item["id"] for item in body["context"]["blocks"]["reqs"]["requirements"]["single"]]
    answers = {
        "ratings": {"criteria": {"functionality": 5}, "comment": "Solid"},
        "reqs": {"items": {item_id: {"verdict": "met"} for item_id in requirement_ids}},
        "sec": {"checklist": {"xss": {"outcome": "not_vulnerable"}, "idor": {"outcome": "not_tested"}}},
        "tasks": {"scenarios": {"create": {"outcome": "success", "ease": 5}, "share": {"outcome": "not_attempted"}}},
        "sus": {"answers": dict.fromkeys(SUS_QUESTION_IDS, 4)},
        "extra": {"answers": {"liked": "no", "tags": ["ugly"], "age": 40, "attn": "b"}},
        "bugs": {"none_found": {"single": True}},
    }
    for block_id, block_answers in answers.items():
        assert _put(client, _url(assignment, f"responses/{block_id}/"), {"answers": block_answers}).status_code == 200
    done = _post(client, _url(assignment, "submit/"))
    assert done.status_code == 200, done.content
    assert done.json() == {"status": "submitted", "next": {"assignment_id": str(following.id), "reason": "assigned"}}
    assert _open(client, assignment)["read_only"] is True


def test_skip_returns_the_next_result(monkeypatch):
    assignment, client = _work()
    monkeypatch.setattr(allocation, "next_assignment", lambda participant: (None, "done"))
    response = _post(client, _url(assignment, "skip/"), {"reason": "platform_unavailable", "note": "Never started"})
    assert response.status_code == 200
    assert response.json() == {"status": "skipped", "next": None}
    assignment.refresh_from_db()
    assert assignment.skip_reason == "platform_unavailable"
    assert assignment.skip_note == "Never started"
    assert set(assignment.context["skip_hosting"]["single"]) == {"state", "error"}

    other, other_client = _work(assignment.task)
    invalid = _post(other_client, _url(other, "skip/"), {"reason": "bored"})
    assert invalid.status_code == 400
    assert invalid.json()["errors"] == [{"path": "reason", "message": "Unknown skip reason"}]
    other.refresh_from_db()
    assert other.status == Assignment.Status.ASSIGNED


# --- ownership ---------------------------------------------------------------------


def test_assignments_observations_and_attachments_of_others_are_404():
    task = _task()
    mine, my_client = _work(task)
    same_task_other = ParticipantFactory(task=task)
    other_task = _task()
    other_task_participant = ParticipantFactory(task=other_task)
    obs = _post(my_client, _url(mine, "observations/"), {"slot": "single", "kind": "bug", "title": "Mine"}).json()
    upload = my_client.post(
        f"{BASE}/observations/{obs['id']}/attachments/",
        {"file": SimpleUploadedFile("a.png", PNG_BYTES, content_type="image/png")},
    )
    attachment_id = upload.json()["id"]
    stranger = UserFactory()

    for client in (_client(same_task_other), _client(other_task_participant), _client(user=stranger)):
        assert client.get(_url(mine)).status_code == 404
        assert client.get(_url(mine, "observations/")).status_code == 404
        assert _put(client, _url(mine, "responses/ratings/"), {"answers": {}}).status_code == 404
        assert _patch(client, f"{BASE}/observations/{obs['id']}/", {"title": "Hijacked"}).status_code == 404
        assert client.delete(f"{BASE}/observations/{obs['id']}/").status_code == 404
        hijack = client.post(
            f"{BASE}/observations/{obs['id']}/attachments/",
            {"file": SimpleUploadedFile("b.png", PNG_BYTES, content_type="image/png")},
        )
        assert hijack.status_code == 404
        assert client.get(f"{BASE}/attachments/{attachment_id}/").status_code == 404
        assert client.delete(f"{BASE}/attachments/{attachment_id}/").status_code == 404
        assert client.get(_url(mine, "source/?slot=single")).status_code == 404
    assert Observation.objects.get(pk=obs["id"]).title == "Mine"
    assert Attachment.objects.filter(observation_id=obs["id"]).count() == 1
    assert my_client.get(f"{BASE}/observations/not-a-uuid/").status_code == 404
    assert my_client.get(f"{BASE}/assignments/not-a-uuid/").status_code == 404


def test_account_users_reach_their_own_assignments():
    user = UserFactory()
    task = _task()
    participant = ParticipantFactory(task=task, kind=Participant.Kind.ACCOUNT, user=user)
    assignment = workbench_assignment(task, participant=participant)
    client = _client(user=user)
    assert client.get(_url(assignment)).status_code == 200
    stranger_assignment = workbench_assignment(task)
    assert client.get(_url(stranger_assignment)).status_code == 404


# --- observations and attachments over the API ----------------------------------------------


def test_observation_and_attachment_lifecycle():
    assignment, client = _work()
    created = _post(
        client,
        _url(assignment, "observations/"),
        {"slot": "single", "kind": "vulnerability", "title": "IDOR on /api/items", "location": "/api/items/2"},
    )
    assert created.status_code == 201
    obs = created.json()
    assert obs["app_slot"] == "single"
    assert "app_id" not in obs

    patched = _patch(client, f"{BASE}/observations/{obs['id']}/", {"severity": "critical", "cwe": "639"})
    assert patched.status_code == 200
    assert (patched.json()["severity"], patched.json()["cwe"]) == ("critical", "CWE-639")
    moved = _patch(client, f"{BASE}/observations/{obs['id']}/", {"slot": "A"})
    assert moved.status_code == 400

    upload = client.post(
        f"{BASE}/observations/{obs['id']}/attachments/",
        {"file": SimpleUploadedFile("screen.png", PNG_BYTES, content_type="image/png")},
    )
    assert upload.status_code == 201
    attachment = upload.json()
    assert attachment["url"] == f"{BASE}/attachments/{attachment['id']}/"
    assert attachment["content_type"] == "image/png"

    served = client.get(attachment["url"])
    assert served.status_code == 200
    assert served.content == PNG_BYTES
    assert served["Content-Type"] == "image/png"
    assert served["Content-Disposition"] == f'inline; filename="{attachment["id"]}.png"'
    assert served["X-Content-Type-Options"] == "nosniff"
    assert served["Cache-Control"] == "private, max-age=0"
    assert served["Content-Security-Policy"] == "default-src 'none'; sandbox"

    disguised = client.post(
        f"{BASE}/observations/{obs['id']}/attachments/",
        {"file": SimpleUploadedFile("cat.png", b"<html><script>alert(1)</script>", content_type="image/png")},
    )
    assert disguised.status_code == 400
    assert disguised.json()["code"] == "unsupported_file"

    listed = client.get(_url(assignment, "observations/"))
    assert listed.status_code == 200
    assert [entry["id"] for entry in listed.json()] == [obs["id"]]
    assert listed.json()[0]["attachments"][0]["id"] == attachment["id"]

    # Viewing stays possible once the assignment is closed; changing does not.
    Assignment.objects.filter(pk=assignment.pk).update(status=Assignment.Status.SUBMITTED)
    assert client.get(attachment["url"]).status_code == 200
    assert client.delete(attachment["url"]).status_code == 409
    Assignment.objects.filter(pk=assignment.pk).update(status=Assignment.Status.IN_PROGRESS)
    assert client.delete(attachment["url"]).status_code == 204
    assert client.delete(f"{BASE}/observations/{obs['id']}/").status_code == 204
    assert not Observation.objects.filter(pk=obs["id"]).exists()


def test_refused_observation_leaves_no_rows():
    assignment, client = _work()
    response = _post(client, _url(assignment, "observations/"), {"slot": "B", "kind": "bug", "title": ""})
    assert response.status_code == 400
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert {entry["path"] for entry in body["errors"]} == {"slot", "title"}
    assert not Observation.objects.exists()
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.ASSIGNED


# --- finding verdicts ---------------------------------------------------------------------


def test_finding_verdicts_only_for_findings_in_the_context():
    task = _task()
    job = task.apps.get().job
    workbench_findings(job, "heval-sast-verdict", 1)
    assignment, client = _work(task)
    body = _open(client, assignment)
    finding_id = body["context"]["blocks"]["sec"]["findings"]["single"][0]["id"]
    stray = workbench_findings(job, "heval-sast-stray", 1)[0]

    response = _put(client, _url(assignment, f"finding-verdicts/{finding_id}/"), {"verdict": "tp", "note": "Real"})
    assert response.status_code == 200
    assert response.json() == {"finding_key": finding_id, "verdict": "tp", "note": "Real"}
    assert _open(client, assignment)["finding_verdicts"] == {finding_id: {"verdict": "tp", "note": "Real"}}

    assert _put(client, _url(assignment, f"finding-verdicts/{stray.id}/"), {"verdict": "tp"}).status_code == 404
    assert _put(client, _url(assignment, "finding-verdicts/not-a-uuid/"), {"verdict": "tp"}).status_code == 404
    bad = _put(client, _url(assignment, f"finding-verdicts/{finding_id}/"), {"verdict": "yes"})
    assert bad.status_code == 400
    assert FindingVerdict.objects.filter(assignment=assignment).count() == 1


# --- source viewer -------------------------------------------------------------------------


def _source_task(**settings) -> EvalTask:
    return _task(settings={"blinding": {"show_source_code": True, **settings}})


def test_source_tree_and_file_for_agent_jobs():
    assignment, client = _work(_source_task())
    tree = client.get(_url(assignment, "source/?slot=single"))
    assert tree.status_code == 200
    files = tree.json()["files"]
    assert [entry["path"] for entry in files] == ["backend/app.py", "frontend/src/App.jsx"]
    job = assignment.item.app_a.job
    expected = job.result_data["files"]["backend/app.py"]
    assert files[0]["size"] == len(expected.encode())
    file = client.get(_url(assignment, "source/file/?slot=single&path=backend/app.py"))
    assert file.status_code == 200
    assert file.json() == {"path": "backend/app.py", "content": expected, "truncated": False}
    assert client.get(_url(assignment, "source/file/?slot=single&path=secret.env")).status_code == 404
    assert client.get(_url(assignment, "source/?slot=A")).status_code == 404
    assert client.get(_url(assignment, "source/")).status_code == 404
    assert client.get(_url(assignment, "source/file/?slot=single")).status_code == 404


def test_source_for_scaffolding_jobs_and_large_files():
    task = _source_task()
    job = task.apps.get().job
    big = "é" * (source.MAX_FILE_BYTES // 2 + 10)  # two bytes per character
    GenerationJob.objects.filter(pk=job.pk).update(
        mode=GenerationJob.Mode.SCAFFOLDING,
        result_data={"backend_code": "from flask import Flask\n", "frontend_code": big, "backend_scan": {"x": 1}},
    )
    assignment, client = _work(task)
    tree = client.get(_url(assignment, "source/?slot=single")).json()
    assert [entry["path"] for entry in tree["files"]] == ["backend/app.py", "frontend/App.jsx"]
    file = client.get(_url(assignment, "source/file/?slot=single&path=frontend/App.jsx")).json()
    assert file["truncated"] is True
    assert len(file["content"].encode()) <= source.MAX_FILE_BYTES
    assert file["content"] == big[: len(file["content"])]


def test_source_is_404_when_not_shown_or_missing():
    assignment, client = _work()
    assert client.get(_url(assignment, "source/?slot=single")).status_code == 404
    assert client.get(_url(assignment, "source/file/?slot=single&path=backend/app.py")).status_code == 404

    task = _source_task()
    GenerationJob.objects.filter(pk=task.apps.get().job_id).update(result_data={"files": {}})
    empty, empty_client = _work(task)
    assert empty_client.get(_url(empty, "source/?slot=single")).status_code == 404
    assert _open(empty_client, empty)["apps"][0]["has_source"] is False


def test_openapi_document_lists_the_workbench_routes():
    client = Client()
    client.force_login(UserFactory(is_staff=True))
    response = client.get(reverse("api:openapi-json"))
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/human-eval/portal/assignments/{assignment_id}/" in paths
    assert "/api/human-eval/portal/observations/{observation_id}/attachments/" in paths
    assert any(re.search(r"finding-verdicts/\{finding_id\}/$", path) for path in paths)


def test_missing_fields_get_the_structured_400_not_a_422():
    task = _task()
    workbench_findings(task.apps.get().job, "heval-sast-empty", 1)
    assignment, client = _work(task)
    finding_id = _open(client, assignment)["context"]["blocks"]["sec"]["findings"]["single"][0]["id"]
    observation = _post(client, _url(assignment, "observations/"), {"title": None})
    assert observation.status_code == 400
    assert {entry["path"] for entry in observation.json()["errors"]} == {"slot", "kind", "title"}
    skip = _post(client, _url(assignment, "skip/"), {})
    assert skip.status_code == 400
    assert skip.json()["errors"] == [{"path": "reason", "message": "Unknown skip reason"}]
    verdict = _put(client, _url(assignment, f"finding-verdicts/{finding_id}/"), {"note": None})
    assert verdict.status_code == 400
    assert set(verdict.json()) == ERROR_KEYS
    assert not Observation.objects.exists()
    assert not FindingVerdict.objects.exists()


# --- regressions --------------------------------------------------------------------------


def test_participants_see_the_spec_frozen_at_first_open():
    """C18: the spec view comes from the task app's frozen copy (the one the requirement items use)."""
    task = _task()
    app = task.apps.get()
    app.snapshot = {**app.snapshot, "spec": {"name": "Frozen spec", "backend_requirements": ["Only this"]}}
    app.save(update_fields=["snapshot"])
    GenerationJob.objects.filter(pk=app.job_id).update(resolved_bundle={"app_requirement": {"name": "Live spec"}})
    assignment, client = _work(task)
    body = _open(client, assignment)
    (shown,) = body["apps"]
    assert shown["spec"]["name"] == "Frozen spec"
    assert shown["spec"]["backend_requirements"] == ["Only this"]
    texts = [item["text"] for item in body["context"]["blocks"]["reqs"]["requirements"]["single"]]
    assert texts == ["Only this", "The app has a README"]


def test_nul_characters_in_answers_and_reports_are_a_400_not_a_500():
    """C22: U+0000 cannot be stored by Postgres; every portal text input refuses it with the usual 400."""
    assignment, client = _work()
    saved = _put(client, _url(assignment, "responses/ratings/"), {"answers": {"comment": "a\u0000b"}})
    assert saved.status_code == 400
    assert saved.json()["code"] == "invalid_answers"
    assert saved.json()["errors"][0]["path"] == "ratings.comment"
    report = _post(client, _url(assignment, "observations/"), {"slot": "single", "kind": "bug", "title": "x\u0000"})
    assert report.status_code == 400
    assert report.json()["errors"][0]["path"] == "title"
    skipped = _post(client, _url(assignment, "skip/"), {"reason": "other", "note": "\u0000"})
    assert skipped.status_code == 400
    assert not Response.objects.filter(assignment=assignment).exists()
    assert not Observation.objects.filter(assignment=assignment).exists()
    assignment.refresh_from_db()
    assert assignment.status != Assignment.Status.SKIPPED
