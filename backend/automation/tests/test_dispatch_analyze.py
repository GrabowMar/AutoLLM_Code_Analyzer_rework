"""The automation analyze step: plan building, submission through the executor, bounded wait.

The executor/plan/catalog engine modules belong to other stages; they are
faked at their public boundary (``executor.submit_run``/``request_cancel``,
``plan.default_profile_config``/``validate_profile_config``/``freeze_plan``,
``catalog.load_specs``).
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.tests.factories import AnalysisProfileFactory
from backend.automation.engine import dispatchers
from backend.automation.tests.factories import PipelineRunFactory
from backend.automation.tests.factories import PipelineStepFactory
from backend.automation.tests.factories import PipelineStepRunFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory
from backend.rankings.tests.measured import fake_engine  # noqa: F401 - fixture
from backend.rankings.tests.measured import make_run
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

DEFAULT_CONFIG = {
    "tools": [
        {"slug": "ruff", "required": True, "config": {}},
        {"slug": "bandit", "required": True, "config": {"level": "low"}},
        {"slug": "semgrep", "required": True, "config": {}},
    ],
    "reviewer": {"enabled": True, "model": "deepseek/deepseek-v4.1-flash", "samples": 3},
    "scope": {},
    "outcomes": {},
}
SPECS = {
    "ruff": SimpleNamespace(kind="static", default_required=True, default_config={}),
    "bandit": SimpleNamespace(kind="static", default_required=True, default_config={}),
    "semgrep": SimpleNamespace(kind="static", default_required=True, default_config={}),
    "gitleaks": SimpleNamespace(kind="static", default_required=False, default_config={"redact": True}),
    "ai_review": SimpleNamespace(kind="ai", default_required=True, default_config={}),
}


class FakeEngine:
    """Records submissions; the submitted run's final state is configurable."""

    def __init__(self):
        self.frozen: list[dict] = []
        self.submitted: list[dict] = []
        self.cancelled: list = []
        self.final: dict = {"status": AnalysisRun.Status.COMPLETED}

    def freeze_plan(self, *, profile=None, config=None):
        plan = {"schema_version": 1, "profile": None, "tools": [], "reviewer": None}
        if profile is not None:
            plan["profile"] = {"slug": profile.slug, "version": profile.version}
            config = profile.config
        plan["tools"] = [{"slug": t["slug"]} for t in config.get("tools", [])]
        if (config.get("reviewer") or {}).get("enabled"):
            plan["reviewer"] = {"enabled": True, "tool_slug": "ai_review"}
        self.frozen.append({"profile": profile, "config": config, "plan": plan})
        return plan

    def submit_run(self, **kwargs):
        self.submitted.append(kwargs)
        job = kwargs["generation_job"]
        return make_run(job, user=kwargs["user"], **self.final)

    def request_cancel(self, run):
        self.cancelled.append(run.id)
        return run


@pytest.fixture
def engine(monkeypatch, fake_engine):  # noqa: F811
    from backend.analysis.engine import catalog
    from backend.analysis.engine import executor
    from backend.analysis.engine import plan

    fake = FakeEngine()
    monkeypatch.setattr(plan, "default_profile_config", lambda: DEFAULT_CONFIG)
    monkeypatch.setattr(plan, "validate_profile_config", lambda config: config)
    monkeypatch.setattr(plan, "freeze_plan", fake.freeze_plan)
    monkeypatch.setattr(catalog, "load_specs", lambda refresh=False: SPECS)
    monkeypatch.setattr(executor, "submit_run", fake.submit_run)
    monkeypatch.setattr(executor, "request_cancel", fake.request_cancel)
    monkeypatch.setattr(dispatchers, "_sleep", lambda s: None)
    return fake


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def step_run(user):
    run = PipelineRunFactory(triggered_by=user, pipeline__owner=user)
    return PipelineStepRunFactory(run=run, step=PipelineStepFactory(pipeline=run.pipeline, kind="analyze"))


@pytest.fixture
def job(user):
    return GenerationJobFactory(created_by=user, status=GenerationJob.Status.COMPLETED)


def _analyze(step_run, params, run_params=None):
    return dispatchers.dispatch_analyze(step_run, params, run_params or {})


def test_legacy_tool_list_restricts_the_default_profile(engine, step_run, job, user):
    out = _analyze(step_run, {"analyzers": ["bandit", "gitleaks"], "generation_job_id": str(job.id)})

    frozen = engine.frozen[0]
    assert frozen["profile"] is None
    assert [t["slug"] for t in frozen["config"]["tools"]] == ["bandit", "gitleaks"]
    # the default profile's per-tool config survives the restriction
    assert frozen["config"]["tools"][0]["config"] == {"level": "low"}
    assert frozen["config"]["tools"][1] == {"slug": "gitleaks", "required": False, "config": {"redact": True}}
    assert frozen["config"]["reviewer"]["enabled"] is False
    submitted = engine.submitted[0]
    assert submitted["user"] == user
    assert submitted["generation_job"] == job
    assert submitted["plan"] == frozen["plan"]
    assert submitted["purpose"] == AnalysisRun.Purpose.ADHOC
    assert set(out) >= {"analysis_run_id", "status", "summary", "threshold_status"}
    assert out["status"] == AnalysisRun.Status.COMPLETED
    assert "error" not in out


def test_tool_list_from_run_params_job_and_ai_alias(engine, step_run, job):
    out = _analyze(step_run, {"tool_slugs": ["ruff", "llm_review"]}, {"generation_job_id": str(job.id)})

    config = engine.frozen[0]["config"]
    assert [t["slug"] for t in config["tools"]] == ["ruff"]
    assert config["reviewer"]["enabled"] is True
    assert engine.submitted[0]["generation_job"] == job
    assert out["status"] == "completed"


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"tool_slugs": []}, "empty tool list"),
        ({}, "needs a 'profile'"),
        ({"tool_slugs": ["bandit", "zap"]}, "Unknown analysis tool"),
        ({"tool_slugs": "{{params.tools}}"}, "Unknown analysis tool"),
        ({"tool_slugs": [1, 2]}, "must be a list"),
        ({"tool_slugs": ["bandit"], "thresholds": [1]}, "'thresholds' must map"),
        ({"tool_slugs": ["bandit"], "timeout_seconds": "soon"}, "timeout_seconds"),
    ],
)
def test_invalid_configs_never_submit(engine, step_run, job, params, message):
    out = _analyze(step_run, {"generation_job_id": str(job.id), **params})

    assert engine.submitted == []
    assert out["status"] == "failed"
    assert message in out["error"]
    assert out["analysis_run_id"] is None
    assert out["threshold_status"] == "not_evaluated"


def test_missing_target_is_rejected(engine, step_run):
    out = _analyze(step_run, {"tool_slugs": ["bandit"]})

    assert engine.submitted == []
    assert "generation_job_id or source_code" in out["error"]


def test_unknown_or_foreign_job_is_rejected(engine, step_run):
    foreign = GenerationJobFactory(created_by=UserFactory(), status=GenerationJob.Status.COMPLETED)

    missing = _analyze(
        step_run,
        {"tool_slugs": ["bandit"], "generation_job_id": "00000000-0000-0000-0000-000000000000"},
    )
    malformed = _analyze(step_run, {"tool_slugs": ["bandit"], "generation_job_id": "not-a-uuid"})
    other = _analyze(step_run, {"tool_slugs": ["bandit"], "generation_job_id": str(foreign.id)})

    assert "not found" in missing["error"]
    assert "not found" in malformed["error"]
    assert "your own" in other["error"]
    assert engine.submitted == []


def test_profile_by_slug_freezes_the_latest_version(engine, step_run, job):
    # A slug of its own: migrate seeds the system profiles (research-default, quick-check, ...).
    AnalysisProfileFactory(slug="pipeline-check", version=1, is_system=True)
    latest = AnalysisProfileFactory(slug="pipeline-check", version=2, is_system=True, config=DEFAULT_CONFIG)
    AnalysisProfileFactory(slug="pipeline-check", version=3, is_system=True, is_archived=True)

    out = _analyze(step_run, {"profile": "pipeline-check", "generation_job_id": str(job.id)})

    assert engine.frozen[0]["profile"] == latest
    assert engine.submitted[0]["profile"] == latest
    assert out["profile"] == "pipeline-check@2"


def test_a_seeded_system_profile_is_usable_by_slug(engine, step_run, job):
    from backend.analysis.models import AnalysisProfile

    seeded = AnalysisProfile.objects.filter(slug="quick-check", is_archived=False).order_by("-version").first()
    if seeded is None:
        pytest.skip("system profiles not seeded in this database")

    out = _analyze(step_run, {"profile": "quick-check", "generation_job_id": str(job.id)})

    assert engine.frozen[0]["profile"] == seeded
    assert out["profile"] == f"quick-check@{seeded.version}"


def test_profile_with_tools_restricts_that_profile(engine, step_run, job):
    profile = AnalysisProfileFactory(is_system=True, config=DEFAULT_CONFIG)

    _analyze(step_run, {"profile_id": str(profile.id), "analyzers": ["semgrep"], "generation_job_id": str(job.id)})

    assert [t["slug"] for t in engine.frozen[0]["config"]["tools"]] == ["semgrep"]
    assert engine.submitted[0]["profile"] == profile


def test_profile_with_empty_tool_toggles_uses_the_profile_as_is(engine, step_run, job):
    profile = AnalysisProfileFactory(is_system=True, config=DEFAULT_CONFIG)

    out = _analyze(step_run, {"profile_id": str(profile.id), "analyzers": [], "generation_job_id": str(job.id)})

    assert engine.frozen[0]["profile"] == profile
    assert "error" not in out


def test_another_users_private_profile_is_not_usable(engine, step_run, job):
    private = AnalysisProfileFactory(created_by=UserFactory(), is_system=False)

    out = _analyze(step_run, {"profile": str(private.id), "generation_job_id": str(job.id)})

    assert "profile not found" in out["error"]
    assert engine.submitted == []


def test_runs_as_the_pipeline_owner_when_the_trigger_user_is_gone(engine, job, user):
    run = PipelineRunFactory(triggered_by=None, pipeline__owner=user)
    step_run = PipelineStepRunFactory(run=run, step=PipelineStepFactory(pipeline=run.pipeline, kind="analyze"))
    UserFactory()  # an unrelated user must never be picked

    _analyze(step_run, {"tool_slugs": ["bandit"], "generation_job_id": str(job.id)})

    assert engine.submitted[0]["user"] == user


def test_thresholds_use_static_counted_findings(engine, step_run, job):
    engine.final = {"findings": {"high": 2}, "ai": {"critical": 9}}

    ok = _analyze(step_run, {"tool_slugs": ["bandit"], "generation_job_id": str(job.id), "thresholds": {"high": 2}})
    over = _analyze(
        step_run,
        {"tool_slugs": ["bandit"], "generation_job_id": str(job.id), "thresholds": {"high": 1, "critical": 0}},
    )

    # AI opinions (9 critical) never trip a gate
    assert ok["threshold_status"] == "passed"
    assert over["threshold_status"] == "failed"
    assert "error" not in over  # breaching is reported, failing the step is opt-in

    strict = _analyze(
        step_run,
        {
            "tool_slugs": ["bandit"],
            "generation_job_id": str(job.id),
            "thresholds": {"high": 1},
            "fail_on_threshold": True,
        },
    )
    assert strict["error"] == "Finding thresholds exceeded"


def test_thresholds_are_not_evaluated_when_the_static_measurement_is_missing(engine, step_run, job):
    engine.final = {"status": AnalysisRun.Status.PARTIAL, "is_complete": False, "static_available": False}

    out = _analyze(step_run, {"tool_slugs": ["bandit"], "generation_job_id": str(job.id), "thresholds": {"high": 0}})
    strict = _analyze(
        step_run,
        {"tool_slugs": ["bandit"], "generation_job_id": str(job.id), "fail_on_partial": True},
    )

    assert out["status"] == "partial"
    assert out["threshold_status"] == "not_evaluated"  # a broken scanner never "passes"
    assert "error" not in out
    assert "partial" in strict["error"]


def test_unresolved_templated_threshold_fails_the_step_before_submitting(engine, step_run, job):
    """A templated limit whose run param is missing must fail the gate, never silently pass it."""
    from backend.automation.engine.params import resolve_params
    from backend.automation.services import validate_pipeline_dsl

    config = {
        "tool_slugs": ["bandit"],
        "generation_job_id": str(job.id),
        "thresholds": {"high": "{{params.max_high}}"},
        "fail_on_threshold": True,
    }
    # The save-time validator accepts the template (it resolves at run time).
    assert validate_pipeline_dsl({"steps": [{"name": "a", "kind": "analyze", "config": config}]}) == []
    engine.final = {"findings": {"high": 50}}

    # Scheduled runs trigger with no params: the reference stays unresolved.
    out = _analyze(step_run, resolve_params(config, {}, {}))

    assert engine.submitted == []
    assert out["status"] == "failed"
    assert out["threshold_status"] == "not_evaluated"
    assert "unresolved template {{params.max_high}}" in out["error"]

    resolved = _analyze(step_run, resolve_params(config, {}, {"max_high": 0}))

    assert len(engine.submitted) == 1
    assert resolved["threshold_status"] == "failed"
    assert resolved["error"] == "Finding thresholds exceeded"


def test_static_unavailable_output_has_no_zero_counts(engine, step_run, job):
    """When no static measurement exists the step output never shows zero findings."""
    # A partial run whose required static tool failed: the stored summary
    # zero-fills severity_counts anyway.
    engine.final = {"status": AnalysisRun.Status.PARTIAL, "is_complete": False, "static_available": False}

    out = _analyze(step_run, {"tool_slugs": ["bandit"], "generation_job_id": str(job.id)})

    run = AnalysisRun.objects.get(id=out["analysis_run_id"])
    assert run.summary["severity_counts"]["high"] == 0
    assert out["static_available"] is False
    assert out["summary"]["severity_counts"] is None
    assert out["summary"]["total_findings"] is None
    assert out["summary"]["tools"]  # the rest of the summary is kept

    engine.final = {"findings": {"high": 2}}
    ok = _analyze(step_run, {"tool_slugs": ["bandit"], "generation_job_id": str(job.id)})

    assert ok["static_available"] is True
    assert ok["summary"]["severity_counts"]["high"] == 2


def test_failed_and_cancelled_runs_fail_the_step(engine, step_run, job):
    engine.final = {"status": AnalysisRun.Status.FAILED}

    out = _analyze(step_run, {"tool_slugs": ["bandit"], "generation_job_id": str(job.id)})

    assert out["status"] == "failed"
    assert out["error"] == "Analysis failed"


def test_submission_errors_become_step_errors(engine, step_run, job, monkeypatch):
    from backend.analysis.engine import executor

    def refuse(**kwargs):
        msg = "sample unavailable: no code"
        raise ValueError(msg)

    monkeypatch.setattr(executor, "submit_run", refuse)

    out = _analyze(step_run, {"tool_slugs": ["bandit"], "generation_job_id": str(job.id)})

    assert out["status"] == "failed"
    assert "no code" in out["error"]


def test_waits_for_the_executor_instead_of_running_inline(engine, step_run, job, monkeypatch):
    engine.final = {"status": AnalysisRun.Status.QUEUED}
    polls = []

    def advance(seconds):
        polls.append(seconds)
        run = AnalysisRun.objects.get(id=engine_run_id())
        if len(polls) == 2:
            run.status = AnalysisRun.Status.COMPLETED
            run.save(update_fields=["status"])

    def engine_run_id():
        return AnalysisRun.objects.filter(generation_job=job).latest("created_at").id

    monkeypatch.setattr(dispatchers, "_sleep", advance)

    out = _analyze(step_run, {"tool_slugs": ["bandit"], "generation_job_id": str(job.id)})

    assert len(polls) == 2
    assert out["status"] == "completed"
    assert "error" not in out


def test_wait_is_bounded_and_cancels_the_run(engine, step_run, job):
    run = make_run(job, status=AnalysisRun.Status.RUNNING)

    out_run, error = dispatchers.wait_for_analysis(run.id, step_run=step_run, timeout_s=0.0, poll_s=0.0)

    assert out_run.id == run.id
    assert "did not finish within" in error
    assert engine.cancelled == [run.id]


def test_wait_stops_when_the_pipeline_is_cancelled(engine, step_run, job):
    run = make_run(job, status=AnalysisRun.Status.RUNNING)
    step_run.run.status = "cancelled"
    step_run.run.save(update_fields=["status"])

    _, error = dispatchers.wait_for_analysis(run.id, step_run=step_run, timeout_s=60, poll_s=0.0)

    assert "Pipeline cancelled" in error
    assert engine.cancelled == [run.id]


def test_step_timeout_reaches_the_wait(engine, step_run, job, monkeypatch):
    engine.final = {"status": AnalysisRun.Status.RUNNING}
    seen = {}

    def fake_wait(run_id, *, step_run, timeout_s, poll_s=dispatchers.ANALYZE_POLL_SECONDS):
        seen["timeout_s"] = timeout_s
        return AnalysisRun.objects.get(id=run_id), "Analysis did not finish within 30s; cancellation requested"

    monkeypatch.setattr(dispatchers, "wait_for_analysis", fake_wait)

    out = _analyze(step_run, {"tool_slugs": ["bandit"], "generation_job_id": str(job.id), "timeout_seconds": 30})

    assert seen["timeout_s"] == 30
    assert out["status"] == "running"
    assert "did not finish" in out["error"]


def test_threshold_helper_never_passes_an_unusable_limit():
    summary = {"severity_counts": {"high": 3}, "total_findings": 3}
    assert dispatchers._threshold_status(summary, {}) == "not_evaluated"
    # A limit that is not a non-negative integer was not evaluated: it must
    # never make the gate "pass" (it used to be skipped).
    assert dispatchers._threshold_status(summary, {"high": "x"}) == "not_evaluated"
    assert dispatchers._threshold_status(summary, {"high": "{{params.max_high}}"}) == "not_evaluated"
    assert dispatchers._threshold_status(summary, {"high": -1}) == "not_evaluated"
    assert dispatchers._threshold_status(summary, {"high": True}) == "not_evaluated"
    assert dispatchers._threshold_status(summary, {"severe": 1}) == "not_evaluated"
    assert dispatchers._threshold_status(summary, {"high": "{{params.max_high}}", "total": 1000}) == "not_evaluated"
    assert dispatchers._threshold_status(summary, {"total": 2}) == "failed"
    assert dispatchers._threshold_status(summary, {"high": "2"}) == "failed"  # a resolved digit string
    assert dispatchers._threshold_status(summary, {"high": 3}) == "passed"
    assert dispatchers._threshold_status(summary, {"high": 0}, available=False) == "not_evaluated"


@pytest.mark.parametrize(
    ("thresholds", "message"),
    [
        ({"high": "{{params.max_high}}"}, "unresolved template {{params.max_high}}"),
        ({"high": "five"}, "is not a non-negative integer"),
        ({"high": -1}, "is not a non-negative integer"),
        ({"severe": 1}, "Unknown threshold 'severe'"),
        ("{{params.thresholds}}", "'thresholds' must map"),
    ],
)
def test_validated_thresholds_rejects_unusable_limits(thresholds, message):
    with pytest.raises(dispatchers.AnalyzeConfigError, match=re.escape(message)):
        dispatchers.validated_thresholds(thresholds)


def test_validated_thresholds_accepts_resolved_limits():
    assert dispatchers.validated_thresholds(None) == {}
    assert dispatchers.validated_thresholds({}) == {}
    assert dispatchers.validated_thresholds({"high": "5", "total": 0}) == {"high": 5, "total": 0}
