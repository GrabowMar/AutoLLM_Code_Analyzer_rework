"""Job transitions (spec §C.3, §C.7, §C.8): guarded writes, finish_stage, infra retries, cancel, completion."""

from __future__ import annotations

import threading
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone

from backend.execution import transitions
from backend.execution.api import request_reeval
from backend.execution.tests.conftest import make_endpoint
from backend.execution.tests.conftest import make_job
from backend.execution.tests.conftest import make_model
from backend.execution.tests.conftest import running_experiment
from backend.execution.transitions import LeaseLost
from backend.execution.transitions import cancel_jobs
from backend.execution.transitions import finish_stage
from backend.execution.transitions import guarded_save
from backend.execution.transitions import infra_retry
from backend.execution.transitions import maybe_complete_experiment
from backend.generation.models import Experiment
from backend.generation.models import ExperimentPostRun
from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.inference.models import LLMRequest
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
S = GenerationJob.Status
OWNER = "exec-test:1:abcd"


def _running(user, **kw):
    kw.setdefault("lease_owner", OWNER)
    kw.setdefault("lease_expires_at", timezone.now() + timedelta(seconds=90))
    kw.setdefault("started_at", timezone.now() - timedelta(seconds=30))
    kw.setdefault("phase", "running")
    return make_job(user, kw.pop("model", None) or make_model(), status=S.RUNNING, **kw)


class TestGuardedSave:
    def test_writes_only_while_the_attempt_is_owned(self, user):
        job = _running(user)
        job.error_message = "x"
        guarded_save(job, ["error_message"])
        assert GenerationJob.objects.get(id=job.id).error_message == "x"

        GenerationJob.objects.filter(id=job.id).update(attempt=1)  # requeued meanwhile
        job.error_message = "stale"
        with pytest.raises(LeaseLost):
            guarded_save(job, ["error_message"])
        assert GenerationJob.objects.get(id=job.id).error_message == "x"

    def test_cancel_wins(self, user):
        job = _running(user)
        GenerationJob.objects.filter(id=job.id).update(status=S.CANCELLED)
        job.metrics = {"cost": 1}
        with pytest.raises(LeaseLost):
            guarded_save(job, ["metrics"])


class TestFinishStage:
    def test_done_completes_refreshes_and_completes_the_experiment(self, user):
        experiment = running_experiment(user)
        batch = GenerationBatch.objects.create(name="b", mode="scaffolding", created_by=user, experiment=experiment)
        job = _running(user, experiment=experiment, batch=batch)
        job.result_data = {"files": {"a.py": "x"}}
        job.status = S.COMPLETED
        with mock.patch(
            "backend.generation.services.experiment_automation.on_experiment_completed",
        ) as completed:
            assert finish_stage(job, transitions.OUTCOME_DONE) is True
        job.refresh_from_db()
        assert (job.status, job.phase, job.lease_owner, job.result_data) == (
            S.COMPLETED,
            "",
            "",
            {"files": {"a.py": "x"}},
        )
        assert job.completed_at is not None
        assert job.duration_seconds > 0
        batch.refresh_from_db()
        assert (batch.status, batch.completed_jobs) == ("completed", 1)
        experiment.refresh_from_db()
        assert experiment.status == Experiment.Status.COMPLETED
        completed.assert_called_once()

    def test_evaluate_releases_the_lease_and_queues_the_evaluation(self, user):
        job = _running(user, lane="sandbox")
        job.result_data = {"agent": {}}
        finish_stage(job, transitions.OUTCOME_EVALUATE)
        job.refresh_from_db()
        assert (job.status, job.phase, job.lease_owner) == (S.RUNNING, "eval_queued", "")
        assert job.result_data == {"agent": {}}

    def test_a_cancel_wins_and_the_worker_only_releases_its_slot(self, user):
        job = _running(user)
        cancel_jobs(GenerationJob.objects.filter(id=job.id))
        assert GenerationJob.objects.get(id=job.id).lease_owner == OWNER  # still held by the worker
        job.status = S.COMPLETED
        assert finish_stage(job, transitions.OUTCOME_DONE) is False
        job.refresh_from_db()
        assert (job.status, job.lease_owner) == (S.CANCELLED, "")

    def test_an_old_attempt_never_overwrites_the_next_one(self, user):
        job = _running(user)
        GenerationJob.objects.filter(id=job.id).update(attempt=1, status=S.PENDING, lease_owner="")
        job.status = S.COMPLETED
        job.error_message = "old attempt"
        assert finish_stage(job, transitions.OUTCOME_DONE) is False
        row = GenerationJob.objects.get(id=job.id)
        assert (row.status, row.attempt, row.error_message) == (S.PENDING, 1, "")

    def test_responses_of_the_attempt_are_cleared(self, user, system_ep):
        job = _running(user)
        request = LLMRequest.objects.create(
            endpoint=system_ep,
            job=job,
            source="scaffolding",
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
        legacy = make_job(user, make_model(), status=S.COMPLETED, queued_at=None, completed_at=timezone.now())
        assert request_reeval(legacy) is True
        legacy.refresh_from_db()
        assert (legacy.status, legacy.phase) == (S.RUNNING, "eval_queued")
        assert legacy.queued_at is not None
        assert request_reeval(legacy) is False  # already queued
        GenerationJob.objects.filter(id=legacy.id).update(phase="evaluating", lease_owner=OWNER)
        legacy.refresh_from_db()
        legacy.status = S.FAILED  # whatever the evaluation thought, a re-evaluation never fails the job
        finish_stage(legacy, transitions.OUTCOME_DONE)
        legacy.refresh_from_db()
        assert legacy.status == S.COMPLETED
        assert "reeval" not in legacy.execution


class TestInfraRetry:
    def test_requeues_the_same_row_with_the_same_snapshot_and_seed(self, user, system_ep):
        job = _running(user, experiment_seed=7, resolved_bundle={"seed": 7})
        GenerationArtifact.objects.create(job=job, stage="backend", request_payload={}, response_payload={})
        old = GenerationArtifact.objects.create(job=job, stage="old", request_payload={}, response_payload={})
        GenerationArtifact.objects.filter(id=old.id).update(created_at=timezone.now() - timedelta(hours=1))
        queued = LLMRequest.objects.create(
            endpoint=system_ep,
            job=job,
            source="scaffolding",
            path="/api/chat",
            deadline_at=timezone.now() + timedelta(hours=1),
        )
        assert infra_retry(job, transitions.REASON_INFRA, "endpoint_offline") is True
        job.refresh_from_db()
        assert (job.status, job.attempt, job.lease_owner, job.started_at) == (S.PENDING, 1, "", None)
        assert (job.experiment_seed, job.resolved_bundle) == (7, {"seed": 7})
        assert job.run_after > timezone.now() + timedelta(seconds=20)
        assert job.execution["attempts"][0]["reason"] == "infra"
        assert list(GenerationArtifact.objects.filter(job=job).values_list("stage", flat=True)) == ["old"]
        queued.refresh_from_db()
        assert queued.status == "cancelled"

    def test_backoff_grows_and_retries_are_bounded(self, user):
        experiment = running_experiment(user, infra_retries=1)
        job = _running(user, experiment=experiment)
        assert infra_retry(job, transitions.REASON_INFRA) is True
        GenerationJob.objects.filter(id=job.id).update(status=S.RUNNING, lease_owner=OWNER)
        job.refresh_from_db()
        assert infra_retry(job, transitions.REASON_INFRA, "boom") is False
        job.refresh_from_db()
        assert job.status == S.FAILED
        assert job.error_message.startswith("Infrastructure failure after 2 attempts: infra")

    def test_executor_lost_has_its_own_budget(self, user, settings):
        settings.EXECUTOR_LOST_RETRIES = 2
        experiment = running_experiment(user, infra_retries=1)
        job = _running(user, experiment=experiment)
        for _ in range(2):
            assert infra_retry(job, transitions.REASON_EXECUTOR_LOST) is True
            GenerationJob.objects.filter(id=job.id).update(status=S.RUNNING, lease_owner=OWNER)
            job.refresh_from_db()
        assert job.execution["executor_lost_count"] == 2
        assert job.run_after < timezone.now() + timedelta(seconds=10)
        # The infra budget is untouched by the two losses.
        assert infra_retry(job, transitions.REASON_INFRA) is True
        GenerationJob.objects.filter(id=job.id).update(status=S.RUNNING, lease_owner=OWNER)
        job.refresh_from_db()
        assert infra_retry(job, transitions.REASON_EXECUTOR_LOST) is False

    def test_exhausted_interruption_is_a_completed_invalid_sample(self, user):
        experiment = running_experiment(user, infra_retries=0)
        job = _running(user, experiment=experiment)
        job.result_data = {"agent": {"validity": {"valid": True, "notes": []}}}
        assert infra_retry(job, transitions.REASON_INFRA_INTERRUPTED, "proxy 503") is False
        job.refresh_from_db()
        assert job.status == S.COMPLETED
        validity = job.result_data["agent"]["validity"]
        assert validity["valid"] is False
        assert "infra_interrupted" in {n["code"] for n in validity["notes"]}

    def test_cancelled_job_is_not_requeued(self, user):
        job = _running(user)
        GenerationJob.objects.filter(id=job.id).update(status=S.CANCELLED)
        assert infra_retry(job, transitions.REASON_INFRA) is False
        job.refresh_from_db()
        assert (job.status, job.lease_owner) == (S.CANCELLED, "")

    def test_requeue_keeps_the_spend_of_wiped_attempts(self, user):
        experiment = running_experiment(user, infra_retries=3)
        job = _running(user, experiment=experiment, metrics={"cost": 0.25, "total_tokens": 1000})
        assert infra_retry(job, transitions.REASON_INFRA_INTERRUPTED, "429") is True
        job.refresh_from_db()
        assert job.metrics == {}
        assert job.execution["attempts"][0]["cost"] == 0.25
        assert job.execution["prior_attempts_cost"] == 0.25
        assert job.execution["prior_attempts_total_tokens"] == 1000
        assert "requeued_at" in job.execution
        GenerationJob.objects.filter(id=job.id).update(
            status=S.RUNNING,
            lease_owner=OWNER,
            metrics={"cost": 0.5, "total_tokens": 10},
        )
        job.refresh_from_db()
        assert infra_retry(job, transitions.REASON_INFRA_INTERRUPTED, "429") is True
        job.refresh_from_db()
        assert job.execution["prior_attempts_cost"] == 0.75
        assert job.execution["prior_attempts_total_tokens"] == 1010

    def test_exhausted_attempt_keeps_its_metrics_and_is_not_double_counted(self, user):
        experiment = running_experiment(user, infra_retries=0)
        job = _running(user, experiment=experiment, metrics={"cost": 0.25})
        job.metrics = {"cost": 0.25}
        assert infra_retry(job, transitions.REASON_INFRA_INTERRUPTED, "429") is False
        job.refresh_from_db()
        assert job.metrics["cost"] == 0.25
        assert job.execution["attempts"][0]["cost"] == 0.25
        assert "prior_attempts_cost" not in job.execution

    def test_infra_failures_feed_the_circuit_breaker(self, user):
        from backend.execution.breaker import breaker

        endpoint = make_endpoint(user)
        for _ in range(3):
            infra_retry(_running(user, model=make_model(endpoint, "m")), transitions.REASON_INFRA)
        assert breaker.is_open(endpoint.pk)


class TestCancel:
    def test_cancel_semantics(self, user, system_ep, django_capture_on_commit_callbacks):
        experiment = running_experiment(user)
        pending = make_job(user, make_model(), experiment=experiment)
        running = _running(user, experiment=experiment)
        waiting_eval = _running(user, experiment=experiment, phase="eval_queued", lease_owner="")
        evaluating = _running(user, experiment=experiment, phase="evaluating")
        request = LLMRequest.objects.create(
            endpoint=system_ep,
            job=running,
            source="scaffolding",
            path="/api/chat",
            status="leased",
            deadline_at=timezone.now() + timedelta(hours=1),
        )
        with django_capture_on_commit_callbacks(execute=True):
            counts = cancel_jobs(experiment.jobs.all(), experiment=experiment)
        assert counts == {"cancelled_pending": 1, "signalled_running": 1, "finishing_evaluations": 2}
        rows = {j.id: j for j in GenerationJob.objects.all()}
        assert rows[pending.id].status == S.CANCELLED
        assert (rows[running.id].status, rows[running.id].lease_owner) == (S.CANCELLED, OWNER)
        assert rows[waiting_eval.id].status == rows[evaluating.id].status == S.RUNNING
        request.refresh_from_db()
        assert request.status == "cancelled"
        experiment.refresh_from_db()
        assert experiment.status == Experiment.Status.CANCELLED

    def test_scope_pending_leaves_running_jobs(self, user):
        pending = make_job(user, make_model())
        running = _running(user)
        cancel_jobs(GenerationJob.objects.all(), scope="pending")
        assert GenerationJob.objects.get(id=pending.id).status == S.CANCELLED
        assert GenerationJob.objects.get(id=running.id).status == S.RUNNING

    def test_legacy_rows_cancel_in_every_phase(self, user):
        legacy = make_job(user, make_model(), queued_at=None, status=S.RUNNING, phase="evaluating")
        assert cancel_jobs(GenerationJob.objects.filter(id=legacy.id))["signalled_running"] == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_completion_creates_one_post_run():
    user = UserFactory()
    experiment = running_experiment(user)

    def create_post_run(exp):
        ExperimentPostRun.objects.create(experiment=exp, trigger="completed", config={})

    results = []
    barrier = threading.Barrier(2)

    def call():
        try:
            barrier.wait(5)
            results.append(maybe_complete_experiment(experiment.id))
        finally:
            connection.close()

    with mock.patch(
        "backend.generation.services.experiment_automation.on_experiment_completed",
        side_effect=create_post_run,
    ):
        threads = [threading.Thread(target=call) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    assert sorted(results) == [False, True]
    assert ExperimentPostRun.objects.filter(experiment=experiment).count() == 1
    assert maybe_complete_experiment(experiment.id) is False


def test_experiment_with_active_jobs_does_not_complete(user):
    experiment = running_experiment(user)
    make_job(user, make_model(), experiment=experiment)
    assert maybe_complete_experiment(experiment.id) is False


def test_append_execution_is_attempt_scoped(user):
    job = _running(user)
    assert transitions.append_execution(job.id, 0, "infra_errors", {"code": "proxy", "status": 503})
    assert transitions.append_execution(job.id, 0, "infra_errors", {"code": "proxy", "status": 507})
    assert not transitions.append_execution(job.id, 1, "infra_errors", {"code": "x"})
    job.refresh_from_db()
    assert [e["status"] for e in job.execution["infra_errors"]] == [503, 507]
