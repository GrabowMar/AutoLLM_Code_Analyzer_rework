"""Run transitions (spec §C.3, §C.7, §C.8; v3 6.12): guarded writes, finish_stage, retries, cancel, completion."""

from __future__ import annotations

import threading
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone

from backend.execution import transitions
from backend.execution.api import enqueue_evaluation
from backend.execution.api import request_reeval
from backend.execution.tests.conftest import make_endpoint
from backend.execution.tests.conftest import make_model
from backend.execution.tests.conftest import make_run
from backend.execution.tests.conftest import policy_with_infra_retries
from backend.execution.tests.conftest import running_study
from backend.execution.transitions import LeaseLost
from backend.execution.transitions import cancel_runs
from backend.execution.transitions import finish_stage
from backend.execution.transitions import guarded_save
from backend.execution.transitions import infra_retry
from backend.execution.transitions import maybe_complete_study
from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationRun
from backend.generation.models import RunFile
from backend.generation.models import Study
from backend.generation.models.choices import RunStatus
from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.factories import StudyCellFactory
from backend.inference.models import LLMRequest
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
S = RunStatus
OWNER = "exec-test:1:abcd"


def _running(user, **kw):
    kw.setdefault("lease_owner", OWNER)
    kw.setdefault("lease_expires_at", timezone.now() + timedelta(seconds=90))
    kw.setdefault("started_at", timezone.now() - timedelta(seconds=30))
    kw.setdefault("phase", "running")
    return make_run(user, kw.pop("model", None) or make_model(), status=S.RUNNING, **kw)


class TestGuardedSave:
    def test_writes_only_while_the_attempt_is_owned(self, user):
        job = _running(user)
        job.error_message = "x"
        guarded_save(job, ["error_message"])
        assert GenerationRun.objects.get(id=job.id).error_message == "x"

        GenerationRun.objects.filter(id=job.id).update(attempt=1)  # requeued meanwhile
        job.error_message = "stale"
        with pytest.raises(LeaseLost):
            guarded_save(job, ["error_message"])
        assert GenerationRun.objects.get(id=job.id).error_message == "x"

    def test_cancel_wins(self, user):
        job = _running(user)
        GenerationRun.objects.filter(id=job.id).update(status=S.CANCELLED)
        job.metrics = {"cost_usd": 1.0}
        with pytest.raises(LeaseLost):
            guarded_save(job, ["metrics"])


class TestFinishStage:
    def test_done_completes_refreshes_the_cell_and_completes_the_study(self, user):
        study = running_study(user)
        cell = StudyCellFactory(condition__plan__study=study)
        job = _running(user, study=study, cell=cell)
        job.outcome = "tested"
        job.result = {"exit_code": 0}
        job.tree_hash = "a" * 64
        job.status = S.COMPLETED
        with mock.patch("backend.generation.runs.queries.refresh_current") as refresh:
            assert finish_stage(job, transitions.OUTCOME_DONE) is True
        job.refresh_from_db()
        assert (job.status, job.phase, job.lease_owner, job.outcome, job.usable) == (
            S.COMPLETED,
            "",
            "",
            "tested",
            True,
        )
        assert (job.result, job.tree_hash) == ({"exit_code": 0}, "a" * 64)
        assert job.completed_at is not None
        assert job.duration_seconds > 0
        assert refresh.call_args.args[0].pk == cell.pk
        study.refresh_from_db()
        assert study.status == "completed"

    def test_evaluate_releases_the_lease_and_queues_the_evaluation(self, user):
        job = _running(user, lane="sandbox")
        job.result = {"termination": "completed"}
        finish_stage(job, transitions.OUTCOME_EVALUATE)
        job.refresh_from_db()
        assert (job.status, job.phase, job.lease_owner) == (S.RUNNING, "eval_queued", "")
        assert job.result == {"termination": "completed"}
        assert (job.outcome, job.usable) == ("", None)  # no outcome before evaluation: unknown, never clean

    def test_a_cancel_wins_and_the_worker_only_releases_its_slot(self, user):
        job = _running(user)
        cancel_runs(GenerationRun.objects.filter(id=job.id))
        assert GenerationRun.objects.get(id=job.id).lease_owner == OWNER  # still held by the worker
        job.status = S.COMPLETED
        assert finish_stage(job, transitions.OUTCOME_DONE) is False
        job.refresh_from_db()
        assert (job.status, job.lease_owner, job.outcome, job.outcome_reason) == (
            S.CANCELLED,
            "",
            "excluded",
            "cancelled",
        )

    def test_an_old_attempt_never_overwrites_the_next_one(self, user):
        job = _running(user)
        GenerationRun.objects.filter(id=job.id).update(attempt=1, status=S.PENDING, lease_owner="")
        job.status = S.COMPLETED
        job.error_message = "old attempt"
        assert finish_stage(job, transitions.OUTCOME_DONE) is False
        row = GenerationRun.objects.get(id=job.id)
        assert (row.status, row.attempt, row.error_message) == (S.PENDING, 1, "")

    def test_responses_of_the_attempt_are_cleared(self, user, system_ep):
        job = _running(user)
        request = LLMRequest.objects.create(
            endpoint=system_ep,
            job=job,
            source="agent",
            path="/api/chat",
            status="done",
            response={"message": "hi"},
            deadline_at=timezone.now() + timedelta(hours=1),
        )
        job.status = S.COMPLETED
        finish_stage(job, transitions.OUTCOME_DONE)
        request.refresh_from_db()
        assert request.response is None

    def test_reevaluation_ends_in_the_previous_status(self, user):
        legacy = make_run(
            user,
            make_model(),
            status=S.COMPLETED,
            outcome="tested",
            queued_at=None,
            completed_at=timezone.now(),
        )
        assert request_reeval(legacy) is True
        legacy.refresh_from_db()
        assert (legacy.status, legacy.phase) == (S.RUNNING, "eval_queued")
        assert legacy.queued_at is not None
        assert request_reeval(legacy) is False  # already queued
        GenerationRun.objects.filter(id=legacy.id).update(phase="evaluating", lease_owner=OWNER)
        legacy.refresh_from_db()
        legacy.status = S.FAILED  # whatever the evaluation thought, a re-evaluation never fails the run
        finish_stage(legacy, transitions.OUTCOME_DONE)
        legacy.refresh_from_db()
        assert (legacy.status, legacy.outcome) == (S.COMPLETED, "tested")
        assert "reeval" not in legacy.execution


class TestInfraRetry:
    def test_requeues_the_same_row_with_the_same_seed_and_wipes_the_attempt(self, user, system_ep):
        job = _running(user, seed=7, outcome="infra_error", tree_hash="b" * 64, result={"exit_code": 1})
        RunFile.objects.create(run=job, path="app.py", blob="c" * 64, origin="authored", size=1)
        GenerationArtifact.objects.create(run=job, stage="agent_call_0001", request_payload={}, response_payload={})
        old = GenerationArtifact.objects.create(run=job, stage="old", request_payload={}, response_payload={})
        GenerationArtifact.objects.filter(id=old.id).update(created_at=timezone.now() - timedelta(hours=1))
        queued = LLMRequest.objects.create(
            endpoint=system_ep,
            job=job,
            source="agent",
            path="/api/chat",
            deadline_at=timezone.now() + timedelta(hours=1),
        )
        assert infra_retry(job, transitions.REASON_INFRA, "endpoint_offline") is True
        job.refresh_from_db()
        assert (job.status, job.attempt, job.lease_owner, job.started_at) == (S.PENDING, 1, "", None)
        assert job.seed == 7
        assert (job.outcome, job.usable, job.tree_hash, job.result) == ("", None, "", {})
        assert not RunFile.objects.filter(run=job).exists()
        assert job.run_after > timezone.now() + timedelta(seconds=20)
        assert job.execution["attempts"][0]["reason"] == "infra"
        assert list(GenerationArtifact.objects.filter(run=job).values_list("stage", flat=True)) == ["old"]
        queued.refresh_from_db()
        assert queued.status == "cancelled"

    def test_backoff_grows_and_retries_are_bounded(self, user):
        job = _running(user, resource_policy=policy_with_infra_retries(1))
        assert infra_retry(job, transitions.REASON_INFRA) is True
        GenerationRun.objects.filter(id=job.id).update(status=S.RUNNING, lease_owner=OWNER)
        job.refresh_from_db()
        assert infra_retry(job, transitions.REASON_INFRA, "boom") is False
        job.refresh_from_db()
        assert (job.status, job.outcome, job.outcome_reason, job.usable) == (S.FAILED, "infra_error", "infra", False)
        assert job.error_message.startswith("Infrastructure failure after 2 attempts: infra")

    def test_executor_lost_has_its_own_budget(self, user, settings):
        settings.EXECUTOR_LOST_RETRIES = 2
        job = _running(user, resource_policy=policy_with_infra_retries(1))
        for _ in range(2):
            assert infra_retry(job, transitions.REASON_EXECUTOR_LOST) is True
            GenerationRun.objects.filter(id=job.id).update(status=S.RUNNING, lease_owner=OWNER)
            job.refresh_from_db()
        assert job.execution["executor_lost_count"] == 2
        assert job.run_after < timezone.now() + timedelta(seconds=10)
        # The infra budget is untouched by the two losses.
        assert infra_retry(job, transitions.REASON_INFRA) is True
        GenerationRun.objects.filter(id=job.id).update(status=S.RUNNING, lease_owner=OWNER)
        job.refresh_from_db()
        assert infra_retry(job, transitions.REASON_EXECUTOR_LOST) is False

    def test_exhausted_interruption_is_an_infra_error_never_a_clean_sample(self, user):
        job = _running(user, resource_policy=policy_with_infra_retries(0))
        job.outcome = "tested"  # whatever the worker had, an exhausted interruption is not usable
        assert infra_retry(job, transitions.REASON_INFRA_INTERRUPTED, "proxy 503") is False
        job.refresh_from_db()
        assert (job.status, job.outcome, job.outcome_reason, job.usable) == (
            S.FAILED,
            "infra_error",
            "infra_interrupted",
            False,
        )
        assert "proxy 503" in job.error_message

    def test_without_a_policy_the_default_retries_apply(self, user):
        job = _running(user)
        assert transitions.infra_retries_for(job.id) == transitions.DEFAULT_INFRA_RETRIES

    def test_cancelled_job_is_not_requeued(self, user):
        job = _running(user)
        GenerationRun.objects.filter(id=job.id).update(status=S.CANCELLED)
        assert infra_retry(job, transitions.REASON_INFRA) is False
        job.refresh_from_db()
        assert (job.status, job.lease_owner) == (S.CANCELLED, "")

    def test_requeue_keeps_the_spend_of_wiped_attempts(self, user):
        job = _running(
            user,
            resource_policy=policy_with_infra_retries(3),
            metrics={"cost_usd": 0.25, "total_tokens": 1000},
        )
        assert infra_retry(job, transitions.REASON_INFRA_INTERRUPTED, "429") is True
        job.refresh_from_db()
        assert job.metrics == {}
        assert job.execution["attempts"][0]["cost_usd"] == 0.25
        assert job.execution["prior_attempts_cost_usd"] == 0.25
        assert job.execution["prior_attempts_total_tokens"] == 1000
        assert "requeued_at" in job.execution
        GenerationRun.objects.filter(id=job.id).update(
            status=S.RUNNING,
            lease_owner=OWNER,
            metrics={"cost_usd": 0.5, "total_tokens": 10},
        )
        job.refresh_from_db()
        assert infra_retry(job, transitions.REASON_INFRA_INTERRUPTED, "429") is True
        job.refresh_from_db()
        assert job.execution["prior_attempts_cost_usd"] == 0.75
        assert job.execution["prior_attempts_total_tokens"] == 1010

    def test_exhausted_attempt_keeps_its_metrics_and_is_not_double_counted(self, user):
        job = _running(user, resource_policy=policy_with_infra_retries(0), metrics={"cost_usd": 0.25})
        assert infra_retry(job, transitions.REASON_INFRA_INTERRUPTED, "429") is False
        job.refresh_from_db()
        assert job.metrics["cost_usd"] == 0.25
        assert job.execution["attempts"][0]["cost_usd"] == 0.25
        assert "prior_attempts_cost_usd" not in job.execution

    def test_infra_failures_feed_the_circuit_breaker(self, user):
        from backend.execution.breaker import breaker

        endpoint = make_endpoint(user)
        for _ in range(3):
            infra_retry(_running(user, model=make_model(endpoint, "m")), transitions.REASON_INFRA)
        assert breaker.is_open(endpoint.pk)


class TestCancel:
    def test_cancel_semantics(self, user, system_ep, django_capture_on_commit_callbacks):
        study = running_study(user)
        pending = make_run(user, make_model(), study=study)
        running = _running(user, study=study)
        waiting_eval = _running(user, study=study, phase="eval_queued", lease_owner="")
        evaluating = _running(user, study=study, phase="evaluating")
        request = LLMRequest.objects.create(
            endpoint=system_ep,
            job=running,
            source="agent",
            path="/api/chat",
            status="leased",
            deadline_at=timezone.now() + timedelta(hours=1),
        )
        with django_capture_on_commit_callbacks(execute=True):
            counts = cancel_runs(GenerationRun.objects.filter(study=study), study=study)
        assert counts == {"cancelled_pending": 1, "signalled_running": 1, "finishing_evaluations": 2}
        rows = {j.id: j for j in GenerationRun.objects.filter(study=study)}
        assert (rows[pending.id].status, rows[pending.id].outcome, rows[pending.id].outcome_reason) == (
            S.CANCELLED,
            "excluded",
            "cancelled",
        )
        assert rows[pending.id].usable is False
        assert (rows[running.id].status, rows[running.id].lease_owner) == (S.CANCELLED, OWNER)
        assert rows[waiting_eval.id].status == rows[evaluating.id].status == S.RUNNING
        request.refresh_from_db()
        assert request.status == "cancelled"
        study.refresh_from_db()
        assert study.status == "cancelled"

    def test_scope_pending_leaves_running_jobs(self, user):
        pending = make_run(user, make_model())
        running = _running(user)
        cancel_runs(GenerationRun.objects.filter(id__in=[pending.id, running.id]), scope="pending")
        assert GenerationRun.objects.get(id=pending.id).status == S.CANCELLED
        assert GenerationRun.objects.get(id=running.id).status == S.RUNNING

    def test_rows_never_handed_to_the_executor_cancel_in_every_phase(self, user):
        unqueued = make_run(user, make_model(), queued_at=None, status=S.RUNNING, phase="evaluating")
        assert cancel_runs(GenerationRun.objects.filter(id=unqueued.id))["signalled_running"] == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_completion_completes_once():
    user = UserFactory()
    study = running_study(user)
    results = []
    barrier = threading.Barrier(2)

    def call():
        try:
            barrier.wait(5)
            results.append(maybe_complete_study(study.id))
        finally:
            connection.close()

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(results) == [False, True]
    assert Study.objects.get(pk=study.pk).status == "completed"
    assert maybe_complete_study(study.id) is False


def test_study_with_active_runs_does_not_complete(user):
    study = running_study(user)
    make_run(user, make_model(), study=study)
    assert maybe_complete_study(study.id) is False


def test_a_piloting_study_is_never_completed_here(user):
    study = running_study(user, status="piloting")
    assert maybe_complete_study(study.id) is False
    assert Study.objects.get(pk=study.pk).status == "piloting"


def test_events_go_to_the_run_and_study_channels(user):
    study = running_study(user)
    job = _running(user, study=study, cell=StudyCellFactory(condition__plan__study=study))
    with (
        mock.patch("backend.realtime.events.publish_run_event") as run_event,
        mock.patch("backend.realtime.events.publish_study_event") as study_event,
    ):
        transitions.publish_run(job)
    assert run_event.call_args.args[1]["type"] == "status"
    payload = study_event.call_args.args[1]
    assert (payload["type"], payload["run_id"], payload["cell_id"]) == ("run", str(job.id), job.cell_id)
    # The payloads match the SSE contracts (extra keys would be refused by the publisher).
    from backend.generation.contracts.events import parse_run_event
    from backend.generation.contracts.events import parse_study_event

    parse_run_event(run_event.call_args.args[1])
    parse_study_event(payload)


class TestEnqueueEvaluation:
    def test_reference_runs_become_claimable_evaluation_work(self, user, django_capture_on_commit_callbacks):
        reference = GenerationRunFactory(reference=True, status=S.PENDING, phase="", created_by=user)
        with django_capture_on_commit_callbacks() as callbacks:
            assert enqueue_evaluation([reference.id]) == 1
        assert len(callbacks) == 1  # the doorbell rings after commit
        reference.refresh_from_db()
        assert (reference.status, reference.phase) == (S.RUNNING, "eval_queued")
        assert reference.queued_at is not None

    def test_a_run_in_its_generation_stage_is_left_alone(self, user):
        running = _running(user)
        assert enqueue_evaluation([running.id]) == 0
        assert GenerationRun.objects.get(id=running.id).phase == "running"


def test_append_execution_is_attempt_scoped(user):
    job = _running(user)
    assert transitions.append_execution(job.id, 0, "infra_errors", {"code": "proxy", "status": 503})
    assert transitions.append_execution(job.id, 0, "infra_errors", {"code": "proxy", "status": 507})
    assert not transitions.append_execution(job.id, 1, "infra_errors", {"code": "x"})
    job.refresh_from_db()
    assert [e["status"] for e in job.execution["infra_errors"]] == [503, 507]
