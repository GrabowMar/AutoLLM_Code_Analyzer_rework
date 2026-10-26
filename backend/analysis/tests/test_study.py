"""Study validation, target resolution and lifecycle rules (research/study.py)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from backend.analysis import tasks
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalysisStudy
from backend.analysis.models import ToolResult
from backend.analysis.research import report as report_mod
from backend.analysis.research import study as study_mod
from backend.analysis.research.study import StudyError
from backend.analysis.tests.factories import AnalysisStudyFactory
from backend.analysis.tests.support.api import APP_PY
from backend.analysis.tests.support.api import install_fakes
from backend.analysis.tests.support.api import llm_model
from backend.analysis.tests.support.api import make_job
from backend.analysis.tests.support.api import make_profile
from backend.generation.models import GenerationBatch
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
# A question added to a frozen protocol after the fact (an edit in the database).
POST_HOC = [{"id": "RQX", "text": "Post hoc?", "kind": "confirmatory", "outcomes": ["measured_sloc"]}]


@pytest.fixture
def engine(monkeypatch, settings):
    return install_fakes(monkeypatch, settings)


@pytest.fixture
def launch_now(django_capture_on_commit_callbacks):
    """Launch and run the background submission (dispatched on commit) to its end."""

    def launch(study, user):
        with django_capture_on_commit_callbacks(execute=True):
            study_mod.launch(study, user=user)
        study.refresh_from_db()
        return study

    return launch


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def test_validate_target_spec_normalizes_and_reports_paths():
    spec = study_mod.validate_target_spec(
        {
            "kind": "jobs",
            "job_ids": ["0C49CD0D-0000-4000-8000-000000000001", "0c49cd0d-0000-4000-8000-000000000001"],
            "filters": {"modes": ["agent"], "created_after": "2026-10-01", "model_ids": [3, "acme/x", 3]},
        },
    )
    assert spec["job_ids"] == ["0c49cd0d-0000-4000-8000-000000000001"]
    assert spec["filters"]["modes"] == ["agent"]
    assert spec["filters"]["model_ids"] == [3, "acme/x"]
    assert spec["filters"]["created_after"].startswith("2026-10-01T00:00:00")

    with pytest.raises(ConfigError) as excinfo:
        study_mod.validate_target_spec(
            {
                "kind": "batch",
                "condition_ids": [1],
                "job_ids": ["x"],
                "filters": {"modes": ["teleport"], "status": "failed", "colour": "red"},
                "extra": 1,
            },
        )
    assert set(excinfo.value.field_errors) == {
        "target_spec.batch_id",
        "target_spec.condition_ids",
        "target_spec.job_ids",
        "target_spec.filters.modes",
        "target_spec.filters.status",
        "target_spec.filters.colour",
        "target_spec.extra",
    }
    with pytest.raises(ConfigError, match=r"target_spec\.kind"):
        study_mod.validate_target_spec({"kind": "everything"})
    with pytest.raises(ConfigError, match=r"job_ids\.1"):
        study_mod.validate_target_spec({"kind": "jobs", "job_ids": ["0c49cd0d-0000-4000-8000-000000000001", "bad"]})


def test_validate_design_follows_the_report_vocabulary():
    design = study_mod.validate_design({"group_by": "harness", "correction": "bh", "outcomes": ["measured_sloc"]})
    assert design["group_by"] == ["harness"]
    assert design["block_by"] == "app"
    assert design["correction"] == "bh"
    assert design["outcomes"] == ["measured_sloc"]
    assert set(design) == set(report_mod.DEFAULT_DESIGN)
    # The report accepts exactly what the API accepted.
    resolved, notes = report_mod.resolve_design(design)
    assert notes == []
    assert resolved["group_by"] == ["harness"]

    assert study_mod.validate_design({"group_by": ["app", "model"]})["block_by"] is None

    with pytest.raises(ConfigError) as excinfo:
        study_mod.validate_design(
            {
                "group_by": ["model", "planet"],
                "alpha": 0.9,
                "correction": "bonferroni",
                "primary_outcome": "happiness",
                "bootstrap_resamples": -1,
                "colour": "red",
            },
        )
    assert set(excinfo.value.field_errors) == {
        "analysis_design.group_by",
        "analysis_design.alpha",
        "analysis_design.correction",
        "analysis_design.primary_outcome",
        "analysis_design.bootstrap_resamples",
        "analysis_design.colour",
    }
    with pytest.raises(ConfigError, match="cannot also be a grouping factor"):
        study_mod.validate_design({"group_by": "app", "block_by": "app"})


def test_validate_rules_and_questions():
    rules = study_mod.validate_exclusion_rules({"exclude_truncated": False, "min_measured_sloc": 20})
    assert rules["exclude_truncated"] is False
    assert rules["min_measured_sloc"] == 20
    assert rules["exclude_invalid"] is True
    with pytest.raises(ConfigError) as excinfo:
        study_mod.validate_exclusion_rules({"exclude_truncated": "no", "min_measured_sloc": -1, "vibes": True})
    assert set(excinfo.value.field_errors) == {
        "exclusion_rules.exclude_truncated",
        "exclusion_rules.min_measured_sloc",
        "exclusion_rules.vibes",
    }

    questions = study_mod.validate_research_questions(
        [{"text": " Does the model matter? ", "kind": "confirmatory"}, {"id": "Q9", "text": "Harness?"}],
    )
    assert questions == [
        {"id": "RQ1", "text": "Does the model matter?", "kind": "confirmatory"},
        {"id": "Q9", "text": "Harness?", "kind": "exploratory"},
    ]
    with pytest.raises(ConfigError, match=r"research_questions\.0\.text"):
        study_mod.validate_research_questions([{"kind": "confirmatory"}])


def test_confirmatory_questions_name_outcomes_and_the_report_reads_them():
    questions = study_mod.validate_research_questions(
        [
            {"text": "Security?", "kind": "confirmatory", "outcomes": ["security_issues_per_kloc"]},
            {"text": "High+?", "kind": "confirmatory", "outcome": "high_plus_issues_per_kloc", "outcomes": []},
        ],
    )
    assert [q["outcomes"] for q in questions] == [["security_issues_per_kloc"], ["high_plus_issues_per_kloc"]]
    design = study_mod.validate_design({})
    definitions, _notes = report_mod.outcome_definitions(design, questions)
    families = {d["key"]: d["family"] for d in definitions}
    assert families["security_issues_per_kloc"] == families["high_plus_issues_per_kloc"] == "confirmatory"
    assert families["static_issues_per_kloc"] == "confirmatory"  # the primary outcome
    assert families["complexity_max"] == "exploratory"

    cases = {
        "research_questions.0.outcomes": [{"text": "q", "kind": "exploratory", "outcomes": ["measured_sloc"]}],
        "research_questions.0.outcome": [{"text": "q", "kind": "confirmatory", "outcome": ["measured_sloc"]}],
    }
    for path, raw in cases.items():
        with pytest.raises(ConfigError) as excinfo:
            study_mod.validate_research_questions(raw)
        assert list(excinfo.value.field_errors) == [path]
    with pytest.raises(ConfigError, match="unknown outcome"):
        study_mod.validate_research_questions([{"text": "q", "kind": "confirmatory", "outcomes": ["nope"]}])
    subset = study_mod.validate_design({"outcomes": ["measured_sloc"]})
    with pytest.raises(ConfigError) as excinfo:
        study_mod.validate_research_questions(questions, design=subset)
    assert set(excinfo.value.field_errors) == {"research_questions.0.outcomes", "research_questions.1.outcomes"}
    primary_only = study_mod.validate_design(
        {"primary_outcome": "security_issues_per_kloc", "outcomes": ["measured_sloc", "high_plus_issues_per_kloc"]},
    )
    assert study_mod.question_outcome_errors(questions, primary_only) == {}


# --------------------------------------------------------------------------
# Target resolution
# --------------------------------------------------------------------------


def test_resolve_experiment_targets_with_conditions_and_apps(user):
    experiment = ExperimentFactory(created_by=user)
    model_a, model_b = llm_model("acme/a"), llm_model("acme/b")
    cond_a = ExperimentConditionFactory(experiment=experiment, model=model_a)
    cond_b = ExperimentConditionFactory(experiment=experiment, model=model_b)
    jobs = {}
    for cond, model in ((cond_a, model_a), (cond_b, model_b)):
        for app_slug in ("todo", "blog"):
            jobs[(cond.id, app_slug)] = make_job(
                user,
                model=model,
                app_slug=app_slug,
                experiment=experiment,
                condition=cond,
                repeat_index=0,
            )
    make_job(user, experiment=experiment, condition=cond_a, status="failed")
    make_job(user)  # not in the experiment

    targets, notes = study_mod.resolve_targets_detailed(
        {"kind": "experiment", "experiment_id": str(experiment.id)},
        user,
    )
    assert [j.id for j in targets] == [
        jobs[(cond_a.id, "blog")].id,
        jobs[(cond_a.id, "todo")].id,
        jobs[(cond_b.id, "blog")].id,
        jobs[(cond_b.id, "todo")].id,
    ]
    assert notes == [
        {"code": "jobs_not_completed", "detail": "1 matching job(s) are not completed and are not targets"},
    ]

    narrowed = study_mod.resolve_targets(
        {
            "kind": "experiment",
            "experiment_id": str(experiment.id),
            "condition_ids": [cond_b.id],
            "app_slugs": ["todo"],
        },
        user,
    )
    assert [j.id for j in narrowed] == [jobs[(cond_b.id, "todo")].id]


def test_resolve_ownership_rules(user):
    other = UserFactory()
    staff = UserFactory(is_staff=True)
    experiment = ExperimentFactory(created_by=other)
    theirs = make_job(other, experiment=experiment)
    mine = make_job(user)

    with pytest.raises(ConfigError, match="experiment not found"):
        study_mod.resolve_targets({"kind": "experiment", "experiment_id": str(experiment.id)}, user)
    # Staff may reference another user's experiment explicitly ...
    assert study_mod.resolve_targets({"kind": "experiment", "experiment_id": str(experiment.id)}, staff) == [theirs]
    # ... but a filter spec only ever selects the requester's own jobs.
    assert study_mod.resolve_targets({"kind": "filter"}, staff) == []
    assert study_mod.resolve_targets({"kind": "filter"}, user) == [mine]

    targets, notes = study_mod.resolve_targets_detailed(
        {"kind": "jobs", "job_ids": [str(theirs.id), str(mine.id)]},
        user,
    )
    assert targets == [mine]
    assert notes[0]["code"] == "jobs_unavailable"
    assert notes[0]["job_ids"] == [str(theirs.id)]


def test_resolve_batch_and_filters(user):
    batch = GenerationBatch.objects.create(name="b", mode="agent", created_by=user)
    a = make_job(user, batch=batch, model=llm_model("acme/a"), mode="agent", agent_config={"harness": "aider"})
    b = make_job(user, batch=batch, model=llm_model("acme/b"), stack_slug="fastapi-vue")

    assert study_mod.resolve_targets({"kind": "batch", "batch_id": str(batch.id)}, user) == [a, b]
    by_harness = {"kind": "filter", "filters": {"harnesses": ["aider"]}}
    assert study_mod.resolve_targets(by_harness, user) == [a]
    assert study_mod.resolve_targets({"kind": "filter", "filters": {"model_ids": ["acme/b"]}}, user) == [b]
    assert study_mod.resolve_targets({"kind": "filter", "filters": {"model_ids": [a.model_id]}}, user) == [a]
    assert study_mod.resolve_targets({"kind": "filter", "filters": {"stacks": ["fastapi-vue"]}}, user) == [b]
    with pytest.raises(ConfigError, match="batch not found"):
        study_mod.resolve_targets({"kind": "batch", "batch_id": str(batch.id)}, UserFactory())


# --------------------------------------------------------------------------
# Lifecycle rules
# --------------------------------------------------------------------------


def _locked_study(user, jobs, **kwargs):
    study = AnalysisStudyFactory(
        created_by=user,
        profile=make_profile(user),
        target_spec={"kind": "jobs", "job_ids": [str(j.id) for j in jobs]},
        **kwargs,
    )
    return study_mod.lock(study, user=user)


def test_lock_refuses_blocking_issues_but_stores_the_preflight(engine, user):
    engine.built = False
    study = AnalysisStudyFactory(
        created_by=user,
        profile=make_profile(user),
        target_spec={"kind": "jobs", "job_ids": [str(make_job(user).id)]},
    )

    with pytest.raises(StudyError) as excinfo:
        study_mod.lock(study, user=user)

    assert excinfo.value.code == "blocking_issues"
    assert "image_missing" in {b["code"] for b in excinfo.value.extra["blocking"]}
    study.refresh_from_db()
    assert study.status == AnalysisStudy.Status.DRAFT
    assert study.preflight["blocking"]
    assert study.protocol == {}


def test_lock_freezes_plan_and_protocol(engine, user):
    study = _locked_study(user, [make_job(user)])

    assert study.status == AnalysisStudy.Status.LOCKED
    assert study.locked_at is not None
    assert study.plan["analyzer_image"]["tag"] == engine.tag
    assert study.plan_hash == study.preflight["plan_hash"]
    assert study.protocol["kind"] == "analysis_study_protocol"
    assert study.protocol_hash == report_mod.protocol_hash(study.protocol)
    assert study_mod.verify_protocol(study)
    with pytest.raises(StudyError, match="Cannot lock"):
        study_mod.lock(study, user=user)


def test_launch_skips_changed_and_missing_targets(engine, user, launch_now):
    keep, changed, gone = make_job(user), make_job(user), make_job(user)
    study = _locked_study(user, [keep, changed, gone])
    changed.result_data = {"files": {"app.py": APP_PY + "\nprint('edited after lock')\n"}}
    changed.save()
    gone.delete()

    study = launch_now(study, user)

    assert study.status == AnalysisStudy.Status.RUNNING
    launch = study.preflight["launch"]
    assert (launch["status"], launch["planned"], launch["submitted"]) == ("submitted", 3, 1)
    assert {s["reason"] for s in launch["skipped"]} == {"sample_changed", "job_missing"}
    run = AnalysisRun.objects.get(study=study)
    assert launch["run_ids"] == [str(run.pk)]
    assert run.generation_job_id == keep.id
    assert run.purpose == AnalysisRun.Purpose.RESEARCH
    assert run.plan_hash == study.plan_hash
    assert run.created_by == user
    assert set(run.results.values_list("status", flat=True)) == {ToolResult.Status.PENDING}
    assert engine.enqueued == [str(run.pk)]


def test_launch_is_synchronous_only_for_the_checks(engine, user, django_capture_on_commit_callbacks):
    study = _locked_study(user, [make_job(user), make_job(user)])

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        study = study_mod.launch(study, user=user)

    # Running, nothing submitted yet: the submission is dispatched after commit.
    assert study.status == AnalysisStudy.Status.RUNNING
    assert study.launched_at is not None
    assert study.preflight["launch"]["status"] == "submitting"
    assert study.preflight["launch"]["planned"] == 2
    assert not study.runs.exists()
    assert len(callbacks) == 1
    assert engine.dispatched == []
    # A pending launch never lets the (still run-less) study complete.
    assert study_mod.refresh(study).status == AnalysisStudy.Status.RUNNING
    progress = study_mod.progress(study)
    assert progress["launch"]["status"] == "submitting"
    assert progress["launch"]["pending"] is True
    assert (progress["counts"]["planned"], progress["counts"]["submitted"]) == (2, 0)
    assert study_mod.allowed_actions(study)["launch"] is True


def test_a_launch_that_submits_nothing_returns_to_locked(engine, user, launch_now):
    job = make_job(user)
    study = _locked_study(user, [job])
    job.delete()

    study = launch_now(study, user)

    assert study.status == AnalysisStudy.Status.LOCKED
    assert study.launched_at is None
    launch = study.preflight["launch"]
    assert launch["status"] == "failed"
    assert launch["error"] == study_mod.NO_TARGET_SUBMITTED
    assert launch["skipped"][0]["reason"] == "job_missing"
    assert not study.runs.exists()
    assert study_mod.allowed_actions(study)["launch"] is True


def test_an_interrupted_launch_resumes_without_duplicates(engine, user, launch_now, monkeypatch):
    jobs = [make_job(user) for _ in range(3)]
    study = _locked_study(user, jobs)
    real_submit = study_mod.executor.submit_run
    calls = []

    def flaky_submit(**kwargs):
        calls.append(kwargs["generation_job"].id)
        if len(calls) == 2:
            msg = "worker lost its database"
            raise RuntimeError(msg)
        return real_submit(**kwargs)

    monkeypatch.setattr(study_mod.executor, "submit_run", flaky_submit)
    study = launch_now(study, user)

    launch = study.preflight["launch"]
    assert study.status == AnalysisStudy.Status.RUNNING
    assert launch["status"] == "interrupted"
    assert "RuntimeError: worker lost its database" in launch["error"]
    assert launch["submitted"] == 1
    assert study_mod.launch_pending(study)
    # Interrupted is not finished: the study cannot complete under it.
    AnalysisRun.objects.filter(study=study).update(status=AnalysisRun.Status.COMPLETED)
    assert study_mod.refresh(study).status == AnalysisStudy.Status.RUNNING

    monkeypatch.setattr(study_mod.executor, "submit_run", real_submit)
    study = launch_now(study, user)

    launch = study.preflight["launch"]
    assert launch["status"] == "submitted"
    assert launch["attempts"] == 2
    assert sorted(study.runs.values_list("generation_job_id", flat=True)) == sorted(j.id for j in jobs)
    # Idempotent: a duplicated job finds nothing pending and submits nothing.
    assert study_mod.submit_launch(study.pk) is None
    assert study.runs.count() == 3
    with pytest.raises(StudyError, match="Cannot launch"):
        study_mod.launch(study, user=user)


def test_cancel_stops_a_pending_launch(engine, user, django_capture_on_commit_callbacks):
    study = _locked_study(user, [make_job(user), make_job(user)])
    with django_capture_on_commit_callbacks(execute=False):
        study = study_mod.launch(study, user=user)

    study = study_mod.cancel(study)

    assert study.status == AnalysisStudy.Status.CANCELLED
    assert study.preflight["launch"]["status"] == "cancelled"
    assert study_mod.submit_launch(study.pk) is None
    assert not study.runs.exists()


def test_a_launcher_stops_when_the_study_is_cancelled_midway(engine, user, launch_now, monkeypatch):
    study = _locked_study(user, [make_job(user), make_job(user), make_job(user)])
    real_submit = study_mod.executor.submit_run

    def submit_then_cancel(**kwargs):
        run = real_submit(**kwargs)
        AnalysisStudy.objects.filter(pk=kwargs["study"].pk).update(status=AnalysisStudy.Status.CANCELLED)
        return run

    monkeypatch.setattr(study_mod.executor, "submit_run", submit_then_cancel)
    study = launch_now(study, user)

    assert study.runs.count() == 1
    assert study.preflight["launch"]["status"] == "cancelled"


def test_launch_verifies_protocol_and_image(engine, user):
    study = _locked_study(user, [make_job(user)])
    study.protocol = {**study.protocol, "research_questions": [{"id": "RQ1", "text": "edited"}]}
    study.save()
    with pytest.raises(StudyError) as excinfo:
        study_mod.launch(study, user=user)
    assert excinfo.value.code == "protocol_integrity"

    study = _locked_study(user, [make_job(user)])
    engine.tag = "llmlab/analyzer:changed00000"
    with pytest.raises(StudyError) as excinfo:
        study_mod.launch(study, user=user)
    assert excinfo.value.code == "image_changed"


def test_refresh_cancel_and_retry(engine, user, launch_now):
    study = launch_now(_locked_study(user, [make_job(user), make_job(user)]), user)
    runs = list(study.runs.order_by("created_at"))
    assert study_mod.refresh(study).status == AnalysisStudy.Status.RUNNING

    AnalysisRun.objects.filter(pk=runs[0].pk).update(status=AnalysisRun.Status.COMPLETED)
    ToolResult.objects.filter(run=runs[1]).update(status=ToolResult.Status.FAILED, failure_reason="tool_error")
    AnalysisRun.objects.filter(pk=runs[1].pk).update(status=AnalysisRun.Status.PARTIAL)
    study = study_mod.refresh(study)
    assert study.status == AnalysisStudy.Status.COMPLETED
    assert study.completed_at is not None

    outcome = study_mod.retry_failed(study)
    assert outcome == {"retried": [str(runs[1].pk)], "skipped": []}
    study.refresh_from_db()
    assert study.status == AnalysisStudy.Status.RUNNING
    runs[1].refresh_from_db()
    assert runs[1].status == AnalysisRun.Status.QUEUED

    study = study_mod.cancel(study)
    assert study.status == AnalysisStudy.Status.CANCELLED
    runs[1].refresh_from_db()
    assert runs[1].status == AnalysisRun.Status.CANCELLED
    with pytest.raises(StudyError):
        study_mod.retry_failed(study)


def test_allowed_actions_follow_the_state_machine():
    study = AnalysisStudy(status=AnalysisStudy.Status.LOCKED)
    actions = study_mod.allowed_actions(study)
    assert actions["launch"] is True
    assert actions["unlock"] is True
    assert actions["edit"] is False
    assert actions["report"] is False
    study.status = AnalysisStudy.Status.RUNNING
    assert study_mod.allowed_actions(study)["delete"] is False


def test_json_safe_drops_non_finite_numbers():
    value = study_mod.json_safe({"a": float("nan"), "b": (1, 2.5), "c": {"d": float("inf")}, "e": {3}})
    assert value == {"a": None, "b": [1, 2.5], "c": {"d": None}, "e": [3]}


# --------------------------------------------------------------------------
# Executor hook
# --------------------------------------------------------------------------


def test_on_run_finished_completes_the_study_after_its_last_run(engine, user, launch_now):
    study = launch_now(_locked_study(user, [make_job(user), make_job(user)]), user)
    first, second = study.runs.order_by("created_at")

    AnalysisRun.objects.filter(pk=first.pk).update(status=AnalysisRun.Status.COMPLETED)
    study_mod.on_run_finished(first)
    study.refresh_from_db()
    assert study.status == AnalysisStudy.Status.RUNNING

    AnalysisRun.objects.filter(pk=second.pk).update(status=AnalysisRun.Status.FAILED)
    study_mod.on_run_finished(second)
    study.refresh_from_db()
    assert study.status == AnalysisStudy.Status.COMPLETED
    assert study.completed_at is not None

    # Idempotent, and a no-op for runs outside studies.
    study_mod.on_run_finished(second)
    study_mod.on_run_finished(AnalysisRun(study_id=None))


def test_on_run_finished_waits_for_a_pending_launch(engine, user, django_capture_on_commit_callbacks):
    study = _locked_study(user, [make_job(user), make_job(user)])
    with django_capture_on_commit_callbacks(execute=False):
        study = study_mod.launch(study, user=user)
    run = AnalysisRun.objects.create(
        created_by=user,
        study=study,
        status=AnalysisRun.Status.COMPLETED,
        purpose=AnalysisRun.Purpose.RESEARCH,
    )

    study_mod.on_run_finished(run)

    study.refresh_from_db()
    assert study.status == AnalysisStudy.Status.RUNNING


def test_on_run_finished_never_raises(engine, user, launch_now, monkeypatch, caplog):
    study = launch_now(_locked_study(user, [make_job(user)]), user)
    run = study.runs.get()

    def broken(study):
        msg = "database went away"
        raise RuntimeError(msg)

    monkeypatch.setattr(study_mod, "refresh", broken)
    study_mod.on_run_finished(run)

    assert "Refreshing study" in caplog.text
    # The failed hook left the surrounding transaction usable.
    assert AnalysisStudy.objects.filter(pk=study.pk).exists()


# --------------------------------------------------------------------------
# Post-run report (background)
# --------------------------------------------------------------------------


@pytest.fixture
def finished_study(engine, user, launch_now):
    study = launch_now(_locked_study(user, [make_job(user), make_job(user)]), user)
    study.runs.update(status=AnalysisRun.Status.COMPLETED, is_complete=True)
    return study_mod.refresh(study)


def test_report_is_generated_in_the_background(engine, user, finished_study, django_capture_on_commit_callbacks):
    assert study_mod.report_state(finished_study) is None

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        state = study_mod.request_report(finished_study, user=user)

    assert state["status"] == "generating"
    assert state["requested_at"]
    assert len(callbacks) == 1
    # A second request while one is in flight returns it and dispatches nothing.
    with django_capture_on_commit_callbacks(execute=False) as again:
        assert study_mod.request_report(finished_study, user=user)["request_id"] == state["request_id"]
    assert again == []
    finished_study.refresh_from_db()
    assert study_mod.ready_report(finished_study) is None
    assert finished_study.report_hash == ""

    callbacks[0]()

    finished_study.refresh_from_db()
    ready = study_mod.report_state(finished_study)
    assert ready["status"] == "ready"
    assert ready["error"] is None
    assert ready["report_hash"] == finished_study.report_hash == report_mod.report_hash(finished_study.report)
    report = study_mod.ready_report(finished_study)
    assert report["status"] == "ready"
    assert report["kind"] == report_mod.REPORT_KIND
    # ``status`` is lifecycle, not content: the hash is the builder's own.
    assert finished_study.report_hash == report["report_hash"]
    assert finished_study.report_generated_at is not None
    assert study_mod.report_is_stale(finished_study) is False
    # A redelivered job for a finished request is a no-op.
    assert study_mod.generate_report(finished_study.pk, state["request_id"]) == "superseded"


def test_report_failures_are_stored_not_raised(engine, user, finished_study, monkeypatch, caplog):
    def broken(study):
        msg = "bootstrap exploded"
        raise ZeroDivisionError(msg)

    monkeypatch.setattr(report_mod, "build_report", broken)
    with pytest.raises(StudyError, match="Cannot report"):
        study_mod.request_report(AnalysisStudyFactory(created_by=user), user=user)

    state = study_mod.request_report(finished_study, user=user)
    assert study_mod.generate_report(finished_study.pk, state["request_id"]) == "failed"

    finished_study.refresh_from_db()
    failed = study_mod.report_state(finished_study)
    assert failed["status"] == "failed"
    assert failed["error"] == "ZeroDivisionError: bootstrap exploded"
    assert study_mod.ready_report(finished_study) is None
    assert "Generating the report" in caplog.text
    # A failed report can be requested again.
    assert study_mod.request_report(finished_study, user=user)["request_id"] != state["request_id"]


def test_a_lost_report_job_turns_into_a_failure(engine, user, finished_study):
    state = study_mod.request_report(finished_study, user=user)
    finished_study.refresh_from_db()
    old = timezone.now() - timedelta(seconds=study_mod.REPORT_STALE_SECONDS + 1)
    finished_study.report = {**finished_study.report, "requested_at": old.isoformat()}
    finished_study.save()

    lost = study_mod.report_state(finished_study)
    assert lost["status"] == "failed"
    assert "did not finish" in lost["error"]
    # ... and a new request replaces it (the lost job, if it ever wakes up, is superseded).
    renewed = study_mod.request_report(finished_study, user=user)
    assert renewed["status"] == "generating"
    assert study_mod.generate_report(finished_study.pk, state["request_id"]) == "superseded"


def test_a_stored_report_goes_stale_when_a_run_changes(engine, user, finished_study):
    state = study_mod.request_report(finished_study, user=user)
    assert study_mod.generate_report(finished_study.pk, state["request_id"]) == "ready"
    finished_study.refresh_from_db()
    assert study_mod.report_is_stale(finished_study) is False

    run = finished_study.runs.first()
    run.status = AnalysisRun.Status.FAILED
    run.save()

    assert study_mod.report_is_stale(finished_study) is True


def test_a_report_needs_an_intact_protocol_and_plan(engine, user, finished_study):
    protocol = finished_study.protocol
    AnalysisStudy.objects.filter(pk=finished_study.pk).update(protocol={**protocol, "research_questions": POST_HOC})
    with pytest.raises(StudyError) as excinfo:
        study_mod.request_report(finished_study, user=user)
    assert excinfo.value.code == "protocol_integrity"

    AnalysisStudy.objects.filter(pk=finished_study.pk).update(protocol=protocol)
    plan = finished_study.plan
    AnalysisStudy.objects.filter(pk=finished_study.pk).update(plan={**plan, "tools": []})
    with pytest.raises(StudyError) as excinfo:
        study_mod.request_report(finished_study, user=user)
    assert excinfo.value.code == "plan_integrity"

    # Accepted, then the protocol changes before the background job runs: the job refuses too.
    AnalysisStudy.objects.filter(pk=finished_study.pk).update(plan=plan)
    state = study_mod.request_report(finished_study, user=user)
    AnalysisStudy.objects.filter(pk=finished_study.pk).update(protocol={**protocol, "name": "edited"})
    assert study_mod.generate_report(finished_study.pk, state["request_id"]) == "failed"
    finished_study.refresh_from_db()
    assert "Protocol integrity check failed" in study_mod.report_state(finished_study)["error"]
    assert finished_study.report_hash == ""


def test_retry_run_locks_the_study_and_follows_its_state(engine, user, finished_study):
    run = finished_study.runs.first()
    run.status = AnalysisRun.Status.FAILED
    run.save()
    ToolResult.objects.filter(run=run).update(status=ToolResult.Status.FAILED)

    retried = study_mod.retry_run(run)

    assert retried.status == AnalysisStudy.Status.RUNNING  # a completed study runs again
    run.refresh_from_db()
    assert run.status == AnalysisRun.Status.QUEUED
    with pytest.raises(ValueError, match="still queued"):
        study_mod.retry_run(run)
    study_mod.cancel(retried)
    run.refresh_from_db()
    assert run.status == AnalysisRun.Status.CANCELLED
    with pytest.raises(StudyError, match="cancelled study"):
        study_mod.retry_run(run)


def test_report_task_limits_fit_the_stale_window():
    assert tasks.REPORT_TIME_LIMIT > tasks.REPORT_SOFT_TIME_LIMIT
    assert study_mod.REPORT_STALE_SECONDS > tasks.REPORT_TIME_LIMIT


def test_corrections_are_the_reports_own():
    assert study_mod.CORRECTIONS is report_mod.CORRECTIONS
