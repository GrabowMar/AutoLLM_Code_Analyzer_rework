"""Runs API (spec 9.5) and the run actions (6.14).

Invariants (13.4, WP-08): retry is refused for model-caused outcomes (a usable run is never superseded by a
retry); retry, evaluate and cancel enqueue only after commit (``django_capture_on_commit_callbacks``).
"""

from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock

import pytest
from django.test import Client

from backend.generation import blobs
from backend.generation.agentic import evidence
from backend.generation.agentic import runner
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import RunManifest
from backend.generation.models import choices
from backend.generation.testing.fake_gateway import canned_call
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BASE = "/api/generation"
CODE = {"backend/todos.py": b"def list_todos():\n    return []\n"}


@pytest.fixture
def client(agentic_env) -> Client:
    client = Client()
    client.force_login(agentic_env.user)
    return client


@pytest.fixture
def generated(agentic_env) -> GenerationRun:
    """A run whose generation stage ran (waiting for evaluation)."""
    agentic_env.script.files = CODE
    agentic_env.script.calls = [canned_call(1, finish_reason="length"), canned_call(2, request_class="compaction")]
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    return run


def finish(run: GenerationRun, outcome: str, status: str = choices.RunStatus.COMPLETED, reason: str = "") -> None:
    GenerationRun.objects.filter(pk=run.pk).update(
        status=status,
        phase="",
        outcome=outcome,
        outcome_reason=reason,
        usable=outcome in {"tested", "build_fail", "start_fail", "output_invalid"},
        lease_owner="",
    )
    run.refresh_from_db()


def test_list_and_detail(client, generated) -> None:
    listing = client.get(f"{BASE}/runs/", {"kind": "app_agentic"}).json()
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["id"] == str(generated.pk)
    assert item["agent_pass_rate"] is None  # not evaluated is never 0
    assert item["model"] == "deepseek/deepseek-v4.1-flash"
    detail = client.get(f"{BASE}/runs/{generated.pk}/").json()
    assert detail["phase"] == "eval_queued"
    assert detail["attempt"] == 1
    assert [a["attempt"] for a in detail["attempts"]] == [1]
    assert detail["attempts"][0]["usage"]["requests_by_class"] == {"main": 1, "compaction": 1}
    assert detail["identity"]["run_fingerprint"] == generated.run_fingerprint
    assert detail["latest_evaluation"] is None
    assert client.get(f"{BASE}/runs/", {"outcome": "tested"}).json()["total"] == 0


def test_visibility(generated) -> None:
    stranger = Client()
    stranger.force_login(UserFactory.create())
    assert stranger.get(f"{BASE}/runs/{generated.pk}/").status_code == 404
    assert stranger.get(f"{BASE}/runs/").json()["total"] == 0
    staff = Client()
    staff.force_login(UserFactory.create(is_staff=True))
    assert staff.get(f"{BASE}/runs/{generated.pk}/").status_code == 200


def test_manifest_files_content_trajectory_calls_evidence(client, generated) -> None:
    manifest = client.get(f"{BASE}/runs/{generated.pk}/manifest/").json()
    assert manifest["manifest_hash"] == RunManifest.objects.get(run=generated).manifest_hash
    assert manifest["stimuli"][0]["role"] == "brief"
    assert manifest["stimuli"][0]["text"].startswith("Build the todo app")
    model_files = client.get(f"{BASE}/runs/{generated.pk}/files/").json()
    assert [f["path"] for f in model_files] == ["backend/todos.py"]
    app_files = client.get(f"{BASE}/runs/{generated.pk}/files/", {"scope": "app", "origin": "starter"}).json()
    assert app_files
    assert all(f["origin"] == "starter" for f in app_files)
    content = client.get(f"{BASE}/runs/{generated.pk}/files/content/", {"path": "backend/todos.py"})
    assert content.status_code == 200
    assert content.content.decode() == CODE["backend/todos.py"].decode()
    missing = client.get(f"{BASE}/runs/{generated.pk}/files/content/", {"path": "nope.py"})
    assert missing.status_code == 404
    trajectory = client.get(f"{BASE}/runs/{generated.pk}/trajectory/").json()
    assert trajectory["attempt"] == 1
    assert trajectory["trajectory"]["parser"] == "fake"
    assert trajectory["observed_inputs"]["calls_by_class"] == {"main": 1, "compaction": 1}
    calls = client.get(f"{BASE}/runs/{generated.pk}/calls/").json()
    assert [c["index"] for c in calls] == [1, 2]
    assert calls[0]["finish_reason"] == "length"
    assert calls[1]["request_class"] == "compaction"
    response = client.get(f"{BASE}/runs/{generated.pk}/evidence/")
    assert response["Content-Type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert "a1/calls.jsonl" in names
    assert "a1/workspace.tar" in names


def test_retry_is_refused_for_model_caused_outcomes(client, generated) -> None:
    for outcome in ("tested", "build_fail", "start_fail", "output_invalid"):
        finish(generated, outcome)
        response = client.post(f"{BASE}/runs/{generated.pk}/retry/")
        assert response.status_code == 409
        assert response.json()["code"] == "not_infra"
    finish(generated, "excluded", reason="cancelled")
    assert client.post(f"{BASE}/runs/{generated.pk}/retry/").json()["code"] == "not_infra"
    generated.refresh_from_db()
    assert generated.attempt == 0  # nothing was requeued


def test_retry_of_infra_error_requeues_after_commit(
    client,
    generated,
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    manifest_hash = RunManifest.objects.get(run=generated).manifest_hash
    seed = generated.seed
    finish(generated, "infra_error", status=choices.RunStatus.FAILED, reason="infra_interrupted")
    enqueue = MagicMock()
    monkeypatch.setattr("backend.execution.api.enqueue_runs", enqueue)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        response = client.post(f"{BASE}/runs/{generated.pk}/retry/")
        assert response.status_code == 200, response.content
        assert response.json()["attempt"] == 2
        enqueue.assert_not_called()  # nothing reaches the executor before commit
    assert callbacks
    for callback in callbacks:
        callback()
    enqueue.assert_called_once_with([generated.pk])
    generated.refresh_from_db()
    assert (generated.status, generated.attempt, generated.outcome, generated.usable) == ("pending", 1, "", None)
    assert generated.queued_at is None
    assert not generated.files.exists()
    assert generated.attempts.count() == 1  # the attempt history is kept
    assert RunManifest.objects.get(run=generated).manifest_hash == manifest_hash
    assert generated.seed == seed


def test_retry_of_an_active_run_is_refused(client, generated) -> None:
    assert client.post(f"{BASE}/runs/{generated.pk}/retry/").json()["code"] == "run_active"


def test_evaluate_queues_a_pending_evaluation_after_commit(
    client,
    agentic_env,
    generated,
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    finish(generated, "tested")
    wake = MagicMock()
    monkeypatch.setattr("backend.execution.api.wake", wake)
    suite = agentic_env.golden.get("eval_suite", "eval-v2")
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        response = client.post(
            f"{BASE}/runs/{generated.pk}/evaluate/",
            {"eval_suite": suite.ref},
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        wake.assert_not_called()
    for callback in callbacks:
        callback()
    wake.assert_called()
    evaluation = RunEvaluation.objects.get(pk=response.json()["evaluation_id"])
    assert evaluation.status == "pending"
    generated.refresh_from_db()
    assert (generated.status, generated.phase) == ("running", "eval_queued")
    assert generated.outcome == "tested"  # a re-evaluation never changes the outcome by itself
    assert generated.execution["reeval_evaluation_id"] == evaluation.pk
    unknown = client.post(
        f"{BASE}/runs/{generated.pk}/evaluate/",
        {"eval_suite": "nope@9"},
        content_type="application/json",
    )
    assert unknown.status_code == 409  # the run is active again


def test_evaluate_needs_a_collected_tree(client, agentic_env) -> None:
    run = agentic_env.make_run(claimed=False)
    finish(run, "infra_error", status=choices.RunStatus.FAILED)
    response = client.post(f"{BASE}/runs/{run.pk}/evaluate/", {}, content_type="application/json")
    assert (response.status_code, response.json()["code"]) == (409, "no_tree")


def test_cancel_signals_after_commit(client, agentic_env, django_capture_on_commit_callbacks, monkeypatch) -> None:
    run = agentic_env.make_run(claimed=False)
    publish = MagicMock()
    monkeypatch.setattr("backend.execution.transitions.publish_run", publish)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        response = client.post(f"{BASE}/runs/{run.pk}/cancel/")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        publish.assert_not_called()
    for callback in callbacks:
        callback()
    publish.assert_called()


def test_trajectory_falls_back_to_the_evidence_file(client, generated) -> None:
    attempt = generated.attempts.get(attempt=1)
    blobs_root_blob = attempt.trajectory_blob
    assert blobs.get_json(blobs_root_blob)["parser"] == "fake"
    assert evidence.read_json(generated.pk, 1, evidence.TRAJECTORY_FILE)["parser"] == "fake"


def test_evaluate_refuses_a_generation_decided_outcome(client, agentic_env) -> None:
    """6.8: a skeleton-only workspace (output_invalid, tree kept) is never sent to evaluation."""
    agentic_env.script.files = {}
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert (run.outcome, run.outcome_reason) == ("output_invalid", "no_model_code")
    assert run.tree_hash  # the starter rows are collected
    response = client.post(f"{BASE}/runs/{run.pk}/evaluate/", {}, content_type="application/json")
    assert (response.status_code, response.json()["code"]) == (409, "not_evaluable")
    assert not RunEvaluation.objects.filter(run=run).exists()
