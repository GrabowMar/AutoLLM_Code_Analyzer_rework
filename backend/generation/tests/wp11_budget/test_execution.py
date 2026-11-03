"""Execution integration: per-study concurrency knobs, eval_parallel, enqueue_evaluation and on_commit."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from backend.execution import api as execution_api
from backend.execution.scheduler import STUDY_ENDPOINT_CAP
from backend.execution.scheduler import STUDY_MODEL_CAP
from backend.execution.scheduler import STUDY_PROVIDER_CAP
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.wp11_budget.conftest import attach_manifest
from backend.generation.tests.wp11_budget.conftest import make_study
from backend.generation.tests.wp11_budget.conftest import model

pytestmark = pytest.mark.django_db


def _options(tree, **options):
    Study.objects.filter(pk=tree.study.pk).update(execution_options={"warmup": True, **options})


def _claimed_models(result):
    return list(
        GenerationRun.objects.filter(pk__in=[c.run_id for c in result.claimed]).values_list("model_id", flat=True),
    )


def test_per_model_cap(user, system_ep, tick):
    shared = model()
    tree = make_study(user, n_conditions=2, n_units=2, repeats=2, models=[shared, model()])
    _options(tree, per_model=1)
    result = tick(8)
    models = _claimed_models(result)
    assert sorted(models) == sorted([shared.pk, tree.conditions[1].model.pk])
    assert STUDY_MODEL_CAP in set(result.blocked().values())


def test_per_endpoint_cap(user, system_ep, tick):
    tree = make_study(user, n_conditions=2, n_units=1, repeats=3)
    _options(tree, per_endpoint=2)
    result = tick(8)
    assert len(result.claimed) == 2
    assert STUDY_ENDPOINT_CAP in set(result.blocked().values())


def test_per_provider_cap_reads_the_pinned_provider(user, system_ep, tick):
    tree = make_study(user, n_conditions=2, n_units=1, repeats=2)
    for run in tree.runs:
        manifest = attach_manifest(run)
        payload = dict(manifest.manifest)
        assert payload["gateway"]["routing"] == {}
    # Rewrite the pinned provider through SQL (manifests are write-once).
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE generation_runmanifest SET manifest = jsonb_set(manifest, '{gateway,routing}', "
            '\'{"only": ["DeepInfra"]}\'::jsonb) WHERE run_id = ANY(%s::uuid[])',
            [[str(r.pk) for r in tree.runs]],
        )
    _options(tree, per_provider=1)
    result = tick(8)
    assert len(result.claimed) == 1
    assert STUDY_PROVIDER_CAP in set(result.blocked().values())


def test_paused_condition_never_blocks_the_lane(user, system_ep, tick):
    shared = model()
    tree = make_study(user, n_conditions=2, n_units=1, repeats=2, models=[shared, shared])
    from backend.generation.models import StudyCondition

    StudyCondition.objects.filter(pk=tree.conditions[0].pk).update(paused=True, pause_reason="rule:cost_overrun")
    result = tick(8)
    claimed = GenerationRun.objects.filter(pk__in=[c.run_id for c in result.claimed])
    assert set(claimed.values_list("condition_id", flat=True)) == {tree.conditions[1].pk}
    assert claimed.count() == 2


def _eval_queued(user, study, n):
    now = timezone.now()
    runs = []
    for i in range(n):
        runs.append(
            GenerationRunFactory(
                created_by=user,
                study=study,
                status="running",
                phase="eval_queued",
                queued_at=now,
                stage_entered_at=now - timedelta(seconds=n - i),
            ),
        )
    return runs


def test_eval_parallel_caps_a_studys_evaluations(user, system_ep, tick):
    tree = make_study(user, n_conditions=1, n_units=1, repeats=1)
    _options(tree, eval_parallel=1)
    _eval_queued(user, tree.study, 3)
    other = make_study(user, n_conditions=1, n_units=1, repeats=1)
    _eval_queued(user, other.study, 2)
    result = tick(0, eval_slots=4)
    evals = [c for c in result.claimed if c.lane == "eval"]
    by_study = {}
    for claimed in evals:
        by_study[claimed.study_id] = by_study.get(claimed.study_id, 0) + 1
    assert by_study[tree.study.pk] == 1
    assert by_study[other.study.pk] == 2
    # The next tick sees one evaluating run of the capped study: still one at a time.
    again = tick(0, eval_slots=4)
    assert not [c for c in again.claimed if c.lane == "eval" and c.study_id == tree.study.pk]


def test_enqueue_evaluation_makes_reference_runs_claimable_after_commit(
    user,
    system_ep,
    tick,
    django_capture_on_commit_callbacks,
):
    from unittest import mock

    reference = GenerationRunFactory(
        created_by=user,
        kind="reference",
        model=None,
        reference_of="oracle",
        status="pending",
        phase="",
    )
    with (
        mock.patch("backend.execution.api.wake") as wake,
        django_capture_on_commit_callbacks(execute=False) as callbacks,
    ):
        assert execution_api.enqueue_evaluation([reference.pk]) == 1
        wake.assert_not_called()  # on_commit: nothing rings before the commit
    assert callbacks
    reference.refresh_from_db()
    assert (reference.status, reference.phase, reference.queued_at is not None) == ("running", "eval_queued", True)
    result = tick(0, eval_slots=1)
    assert [(c.run_id, c.lane) for c in result.claimed] == [(reference.pk, "eval")]
    # Idempotent: a run being evaluated is left alone.
    assert execution_api.enqueue_evaluation([reference.pk]) == 0


def test_enqueue_runs_dispatches_after_commit(user, system_ep, django_capture_on_commit_callbacks):
    from unittest import mock

    run = GenerationRunFactory(created_by=user, model=model())
    with mock.patch("backend.execution.api.wake") as wake:
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            assert execution_api.enqueue_runs([run.pk]) == 1
            wake.assert_not_called()
        assert len(callbacks) == 1
        wake.assert_called_once()
    run.refresh_from_db()
    assert run.queued_at is not None
    assert run.lane == "sandbox"


def test_lanes_follow_the_kind(user):
    assert execution_api.lane_for(GenerationRun(kind="app_agentic")) == "sandbox"
    assert execution_api.lane_for(GenerationRun(kind="slot_agent")) == "sandbox"
    assert execution_api.lane_for(GenerationRun(kind="slot_single_shot")) == "llm"


def test_infra_retries_come_from_the_policy(user, system_ep, tick):
    from backend.execution import transitions

    tree = make_study(user, n_conditions=1, n_units=1, repeats=1, policy_overrides={"retries.infra_retries": 1})
    run = tree.runs[0]
    tick(1)
    run.refresh_from_db()
    assert transitions.infra_retry(run, transitions.REASON_INFRA, "boom")
    GenerationRun.objects.filter(pk=run.pk).update(run_after=None)
    tick(1)
    run.refresh_from_db()
    assert not transitions.infra_retry(run, transitions.REASON_INFRA, "boom again")
    run.refresh_from_db()
    assert (run.status, run.outcome, run.usable) == ("failed", "infra_error", False)


def test_maybe_complete_study_stops_a_stopping_study(user, system_ep, events):
    from backend.execution import transitions

    tree = make_study(user, n_conditions=1, n_units=1, repeats=1, status="piloting")
    Study.objects.filter(pk=tree.study.pk).update(
        stop_reason={"code": "rule:infra_error_rate", "detail": "", "at": "x", "rule": "infra_error_rate"},
    )
    assert not transitions.maybe_complete_study(tree.study.pk)  # a pending run is still active
    GenerationRun.objects.filter(study=tree.study).update(status="cancelled")
    assert transitions.maybe_complete_study(tree.study.pk)
    assert Study.objects.get(pk=tree.study.pk).status == "stopped"
    assert {"type": "stopped", "reason": "rule:infra_error_rate"} in events.of("study:", "stopped")
    plain = make_study(user, n_conditions=1, n_units=1, repeats=1)
    GenerationRun.objects.filter(study=plain.study).update(status="completed")
    assert transitions.maybe_complete_study(plain.study.pk)
    assert Study.objects.get(pk=plain.study.pk).status == "completed"
