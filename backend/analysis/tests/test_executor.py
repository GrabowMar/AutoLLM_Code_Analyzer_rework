"""Run state machine (engine/executor.py) with in-memory fakes at every module boundary."""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest
from django.utils import timezone

from backend.analysis.engine import artifacts
from backend.analysis.engine import catalog
from backend.analysis.engine import dedup
from backend.analysis.engine import executor
from backend.analysis.engine import locks
from backend.analysis.engine import measurement
from backend.analysis.engine import normalize
from backend.analysis.engine import plan as plan_mod
from backend.analysis.engine import sample as sample_mod
from backend.analysis.engine import sandbox
from backend.analysis.engine import types
from backend.analysis.engine.types import NotApplicable
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse
from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.models import ReviewSample
from backend.analysis.models import ToolResult
from backend.analysis.tests import executor_fakes as fx
from backend.analysis.tests.factories import AnalysisStudyFactory
from backend.credentials.services import resolver as credentials
from backend.generation.tests.factories import GenerationJobFactory
from backend.users.tests.factories import UserFactory

# The real summary (the world fixture installs a fake one).
REAL_SUMMARIZE = measurement.summarize

Status = AnalysisRun.Status
TStatus = ToolResult.Status

APP = {"app.py": "import os\nos.system(input())\n", "README.md": "# demo\n"}


class _WorkerDied(BaseException):
    """Simulates the worker process dying mid-tool (escapes every ``except Exception``)."""


@pytest.fixture(autouse=True)
def _executor_settings(settings):
    settings.ANALYSIS_HEARTBEAT_THREAD = False
    settings.ANALYSIS_USE_CELERY = False
    settings.ANALYSIS_MAX_ATTEMPTS = 3
    settings.ANALYSIS_STALE_SECONDS = 180
    settings.ANALYSIS_MAX_CONCURRENT_RUNS = 2
    settings.OPENROUTER_API_KEY = ""


@pytest.fixture
def world(monkeypatch):
    world = fx.install_world(
        monkeypatch,
        adapters_by_slug={
            "ruff": fx.FakeAdapter("ruff", findings=[fx.raw("S605", line=2)]),
            "bandit": fx.FakeAdapter(
                "bandit",
                findings=[fx.raw("B605", line=2), fx.raw("B404", line=1, severity="low")],
            ),
            "eslint": fx.not_applicable_adapter("eslint"),
        },
    )
    world.sandbox.manifest = {"tools": {"ruff": {"version": "1.0"}, "bandit": {"version": "1.0"}}}
    # Infra retries back off for seconds in production; not here.
    monkeypatch.setattr(executor._RunExecution, "_pause", lambda self, seconds: None)
    return world


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr(credentials, "get_openrouter_key", lambda user, **kwargs: "sk-test")


def submit(user, plan=None, files=None, **kwargs) -> AnalysisRun:
    plan = plan or fx.make_plan([fx.tool_entry("ruff"), fx.tool_entry("bandit")])
    files = files or APP
    return executor.submit_run(
        user=user,
        plan=plan,
        source_code=files,
        sample=fx.make_sample(files),
        dispatch=kwargs.pop("dispatch", False),
        **kwargs,
    )


def results(run) -> dict[str, ToolResult]:
    return {r.tool_slug: r for r in ToolResult.objects.filter(run=run)}


def process(run) -> AnalysisRun:
    executor.process_run(str(run.pk))
    run.refresh_from_db()
    return run


# --------------------------------------------------------------------------
# submit_run
# --------------------------------------------------------------------------


def test_submit_creates_a_queued_run_with_pending_results(user, world, django_capture_on_commit_callbacks):
    plan = fx.make_plan([fx.tool_entry("ruff"), fx.tool_entry("bandit")], reviewer_cfg=fx.reviewer_entry())
    with django_capture_on_commit_callbacks(execute=True):
        run = submit(user, plan, dispatch=True, name="demo")
    assert run.status == Status.QUEUED
    assert run.plan_hash == plan_mod.plan_hash(plan)
    assert run.sample_hash == fx.make_sample(APP).sample_hash
    assert run.sample_manifest["totals"]["files"] == 2
    assert run.target_meta["source"] == "inline"
    assert run.name == "demo"
    rows = list(ToolResult.objects.filter(run=run).order_by("position"))
    assert [(r.tool_slug, r.position, r.tool_kind, r.status) for r in rows] == [
        ("ruff", 0, "static", TStatus.PENDING),
        ("bandit", 1, "static", TStatus.PENDING),
        ("ai_review", 2, "ai", TStatus.PENDING),
    ]
    assert rows[0].config_hash == "cfg-ruff"
    assert rows[0].tool_version == "1.0"
    assert rows[2].tool_version == "deepseek/deepseek-v4.1-flash-20260910"
    assert rows[2].required
    assert world.enqueued == [(str(run.pk), 0)]


def test_submit_resolves_the_sample_when_not_given(user, world):
    run = executor.submit_run(user=user, plan=fx.make_plan(), source_code=APP, dispatch=False)
    assert world.resolver.calls == 1
    assert run.sample_hash == fx.make_sample(APP).sample_hash


def test_submit_enforces_job_ownership(user, world):
    job = GenerationJobFactory(status="completed", result_data={"files": APP})
    with pytest.raises(PermissionError):
        executor.submit_run(user=user, plan=fx.make_plan(), generation_job=job, dispatch=False)
    staff = UserFactory(is_staff=True)
    run = executor.submit_run(user=staff, plan=fx.make_plan(), generation_job=job, dispatch=False)
    assert run.generation_job_id == job.pk
    # The resolver's scope-aware target_meta is stored as is, never recomputed.
    assert run.target_meta == {"job_id": str(job.pk), "mode": "custom", "files": 2, "scope": fx.SCOPE}
    assert world.resolver.meta_calls == 0


def test_submit_with_a_preresolved_sample_keeps_its_target_meta(user, world):
    job = GenerationJobFactory(created_by=user, status="completed", result_data={"files": APP})
    sample = fx.make_sample(APP, source="agent")
    sample.meta = {"job_id": str(job.pk), "empty_output": False, "truncated": False}
    run = executor.submit_run(user=user, plan=fx.make_plan(), generation_job=job, sample=sample, dispatch=False)
    assert run.target_meta == sample.meta
    assert (world.resolver.calls, world.resolver.meta_calls) == (0, 0)
    # A sample built without meta (not by the resolver) still gets the job's target_meta.
    bare = fx.make_sample(APP)
    bare.meta = {}
    run = executor.submit_run(user=user, plan=fx.make_plan(), generation_job=job, sample=bare, dispatch=False)
    assert run.target_meta["recomputed"] is True
    assert world.resolver.meta_calls == 1


def test_submit_unresolvable_sample_is_a_value_error(user, world):
    job = GenerationJobFactory(created_by=user, status="failed")
    with pytest.raises(ValueError, match="no code"):
        executor.submit_run(user=user, plan=fx.make_plan(), generation_job=job, dispatch=False)
    assert not AnalysisRun.objects.exists()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p.update(schema_version=99), "schema_version"),
        (lambda p: p.update(tools=[], reviewer=None), "no tool"),
        (lambda p: p.pop("scope"), "scope"),
        (lambda p: p.update(tools=[fx.tool_entry("ruff"), fx.tool_entry("ruff")]), "twice"),
    ],
)
def test_submit_rejects_invalid_plans(user, world, mutate, message):
    plan = fx.make_plan()
    mutate(plan)
    with pytest.raises(ValueError, match=message):
        submit(user, plan)


def test_submit_requires_exactly_one_target(user, world):
    with pytest.raises(ValueError, match="exactly one target"):
        executor.submit_run(user=user, plan=fx.make_plan(), dispatch=False)


# --------------------------------------------------------------------------
# Status rules
# --------------------------------------------------------------------------


def test_all_tools_succeed_completes_the_run(user, world):
    run = process(submit(user))
    assert run.status == Status.COMPLETED
    assert run.is_complete
    assert run.error_message == ""
    assert run.lease_owner == ""
    assert run.phase == "done"
    assert run.attempt == 1
    assert run.completed_at is not None
    assert run.duration_seconds is not None
    assert world.sandbox.calls == ["ruff", "bandit"]
    assert world.sandbox.ensure_calls == [{"expect_tag": fx.IMAGE_TAG, "build": True}]
    # Only included files go into the sample volume.
    assert set(world.sandbox.volumes[0]) == {"app.py", "README.md"}
    rows = results(run)
    ruff = rows["ruff"]
    assert ruff.status == TStatus.SUCCEEDED
    assert ruff.attempt == 1
    assert ruff.exit_code == 0
    assert ruff.argv == ["ruff", "/src/app.py"]
    assert ruff.tool_version == "1.0"
    # The frozen tag pinned to the exact image it ran in.
    assert ruff.image_ref == f"{fx.IMAGE_TAG}@{fx.IMAGE_ID}"
    assert ruff.coverage == {"files_in_scope": 1, "files_scanned": 1, "scanned": ["app.py"], "unparseable": []}
    assert ruff.summary["findings"] == 1
    assert ruff.summary["by_severity"]["high"] == 1
    assert ruff.diagnostics["image_id"] == fx.IMAGE_ID
    assert ruff.finished_at is not None
    assert Finding.objects.filter(run=run).count() == 3
    assert not Finding.objects.filter(run=run, issue_key="").exists()
    assert run.measurement["tool_status"] == {"ruff": "succeeded", "bandit": "succeeded"}
    assert run.summary["from_fake"]
    assert run.summary["total_findings"] == 3
    statuses = [e["status"] for _rid, e in world.published if e["type"] == "status"]
    assert statuses[0] == Status.RUNNING
    assert statuses[-1] == Status.COMPLETED
    assert any(e["type"] == "tool" and e["tool_slug"] == "ruff" for _rid, e in world.published)


def test_raw_outputs_are_stored_as_verified_artifacts(user, world):
    world.sandbox.outcomes["ruff"] = [fx.ok_outcome(stdout=b"[1]", stderr=b"warn", out_files={"report.json": b"{}"})]
    run = process(submit(user))
    stored = results(run)["ruff"].artifacts
    assert set(stored) == {"stdout", "stderr", "out/report.json"}
    assert artifacts.load(stored["stdout"]) == b"[1]"
    assert artifacts.load(stored["out/report.json"]) == b"{}"
    assert results(run)["ruff"].diagnostics["stderr_tail"] == "warn"


def test_required_tool_failure_makes_the_run_partial(user, world):
    world.sandbox.outcomes["bandit"] = [fx.ok_outcome(exit_code=2, stderr=b"boom")]
    run = process(submit(user))
    assert run.status == Status.PARTIAL
    assert not run.is_complete
    bandit = results(run)["bandit"]
    assert bandit.status == TStatus.FAILED
    assert bandit.failure_reason == "tool_error"
    assert bandit.exit_code == 2
    assert bandit.diagnostics["stderr_tail"] == "boom"
    assert "bandit (tool_error)" in run.error_message
    # A failed tool is never "0 findings": it has none and is counted as failed.
    assert not Finding.objects.filter(result=bandit).exists()
    assert run.summary["tools_failed"] == 1


def test_optional_tool_failure_keeps_the_run_complete(user, world):
    plan = fx.make_plan([fx.tool_entry("ruff"), fx.tool_entry("bandit", required=False)])
    world.sandbox.outcomes["bandit"] = [fx.ok_outcome(exit_code=2)]
    run = process(submit(user, plan))
    assert run.status == Status.COMPLETED
    assert run.is_complete


def test_all_tools_failing_fails_the_run(user, world):
    world.sandbox.outcomes = {"ruff": [fx.ok_outcome(exit_code=127)], "bandit": [fx.ok_outcome(exit_code=127)]}
    run = process(submit(user))
    assert run.status == Status.FAILED
    assert "No tool succeeded" in run.error_message


def test_timeout_and_oom_are_failures_not_results(user, world):
    world.sandbox.outcomes = {
        "ruff": [fx.ok_outcome(exit_code=137, timed_out=True, out_files={})],
        "bandit": [fx.ok_outcome(exit_code=137, oom_killed=True)],
    }
    run = process(submit(user))
    rows = results(run)
    assert (rows["ruff"].status, rows["ruff"].failure_reason) == (TStatus.TIMED_OUT, "timeout")
    assert (rows["bandit"].status, rows["bandit"].failure_reason) == (TStatus.FAILED, "oom")
    assert run.status == Status.FAILED
    assert not Finding.objects.filter(run=run).exists()


def test_not_applicable_counts_as_ok_but_not_as_success(user, world):
    plan = fx.make_plan([fx.tool_entry("ruff"), fx.tool_entry("eslint")])
    run = process(submit(user, plan))
    eslint = results(run)["eslint"]
    assert eslint.status == TStatus.NOT_APPLICABLE
    assert eslint.coverage == {"files_in_scope": 0}
    assert "eslint" not in world.sandbox.calls
    assert run.status == Status.COMPLETED
    assert run.is_complete


def test_nothing_in_scope_for_any_tool_fails_the_run(user, world):
    run = process(submit(user, fx.make_plan([fx.tool_entry("eslint")])))
    assert run.status == Status.FAILED
    assert "Nothing in the sample is in scope" in run.error_message
    assert world.sandbox.calls == []


def test_adapter_not_applicable_from_prepare(user, world):
    world.adapters["bandit"] = fx.failing_prepare("bandit", NotApplicable("no python"))
    run = process(submit(user))
    assert results(run)["bandit"].status == TStatus.NOT_APPLICABLE
    assert results(run)["bandit"].error_message == "no python"


def test_unparseable_output_is_a_parse_error(user, world):
    def interpret(outcome, files, config):
        msg = "invalid JSON on stdout"
        raise ToolFailure(reason="parse_error", message=msg)

    world.adapters["bandit"] = fx.FakeAdapter("bandit", interpret=interpret)
    run = process(submit(user))
    bandit = results(run)["bandit"]
    assert (bandit.status, bandit.failure_reason) == (TStatus.FAILED, "parse_error")
    assert bandit.error_message == "invalid JSON on stdout"
    assert bandit.artifacts  # raw output kept for re-parsing
    assert run.status == Status.PARTIAL


def test_adapter_and_normalizer_crashes_fail_the_tool(user, world, monkeypatch):
    def interpret(outcome, files, config):
        msg = "results"
        raise KeyError(msg)

    world.adapters["bandit"] = fx.FakeAdapter("bandit", interpret=interpret)
    world.adapters["ruff"] = fx.FakeAdapter("ruff", findings=[fx.raw("X1")])
    monkeypatch.setattr(
        "backend.analysis.engine.normalize.normalize",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("no mapping")),
    )
    run = process(submit(user))
    rows = results(run)
    assert rows["bandit"].failure_reason == "parse_error"
    assert "KeyError" in rows["bandit"].error_message
    assert rows["ruff"].failure_reason == "parse_error"
    assert "no mapping" in rows["ruff"].error_message
    assert run.status == Status.FAILED


def test_parse_errors_an_adapter_lets_through_become_coverage(user, world):
    def interpret(outcome, files, config):
        return ToolParse(
            findings=[fx.raw("E999", line=1), fx.raw("E999", path="./lib.py", line=3), fx.raw("S605", line=2)],
            coverage={"files_scanned": 1, "unparseable": [{"path": "lib.py", "reason": "SyntaxError"}]},
        )

    world.adapters["ruff"] = fx.FakeAdapter("ruff", interpret=interpret)
    run = process(submit(user, fx.make_plan([fx.tool_entry("ruff")])))
    ruff = results(run)["ruff"]
    assert ruff.status == TStatus.SUCCEEDED
    # Never a finding: one unparseable entry per path (the adapter's first).
    assert list(Finding.objects.filter(result=ruff).values_list("rule_id", flat=True)) == ["S605"]
    assert ruff.coverage["unparseable"] == [
        {"path": "lib.py", "reason": "SyntaxError"},
        {"path": "app.py", "reason": "E999 at app.py:1"},
    ]
    assert ruff.diagnostics["parse_errors_moved_to_coverage"] == 2
    assert ruff.summary["findings"] == 1


def test_a_capped_out_dir_is_named_in_the_failure(user, world):
    world.sandbox.outcomes["ruff"] = [fx.ok_outcome(out_files={}, out_truncated=True)]
    ruff = results(process(submit(user, fx.make_plan([fx.tool_entry("ruff")]))))["ruff"]
    assert (ruff.status, ruff.failure_reason) == (TStatus.FAILED, "parse_error")
    assert ruff.error_message.startswith("missing report (/out exceeded the collection cap")
    assert ruff.diagnostics["out_truncated"] is True


def test_unknown_adapter_is_unavailable(user, world):
    run = process(submit(user, fx.make_plan([fx.tool_entry("ruff"), fx.tool_entry("ghost")])))
    ghost = results(run)["ghost"]
    assert (ghost.status, ghost.failure_reason) == (TStatus.FAILED, "unavailable")
    assert run.status == Status.PARTIAL


def test_unexpected_failure_reason_from_an_adapter_is_mapped(user, world):
    def interpret(outcome, files, config):
        msg = "odd"
        raise ToolFailure(reason="weird_reason", message=msg)

    world.adapters["bandit"] = fx.FakeAdapter("bandit", interpret=interpret)
    bandit = results(process(submit(user)))["bandit"]
    assert bandit.failure_reason == "tool_error"
    assert bandit.error_message == "[weird_reason] odd"


def test_style_rule_reaches_the_normalizer(user, world, monkeypatch):
    seen = []

    def normalize(slug, raw, sample, scope, **kwargs):
        seen.append(scope["count_style"])
        return fx.fake_normalize(slug, raw, sample, scope)

    monkeypatch.setattr("backend.analysis.engine.normalize.normalize", normalize)
    plan = fx.make_plan([fx.tool_entry("ruff")])
    plan["outcomes"]["count_style"] = True
    process(submit(user, plan))
    assert seen == [True]


# --------------------------------------------------------------------------
# Versions, image, capacity, infra retries
# --------------------------------------------------------------------------


def test_version_drift_against_a_verified_plan_fails_the_tool(user, world):
    world.sandbox.manifest = {"tools": {"ruff": {"version": "2.0"}, "bandit": {"version": "1.0"}}}
    run = process(submit(user))
    ruff = results(run)["ruff"]
    assert (ruff.status, ruff.failure_reason) == (TStatus.FAILED, "unavailable")
    assert "drift" in ruff.error_message
    assert ruff.tool_version == "2.0"
    assert "ruff" not in world.sandbox.calls


def test_unverified_plan_versions_are_recorded_from_the_image(user, world):
    plan = fx.make_plan([fx.tool_entry("ruff", version="0.9", source="spec")])
    world.sandbox.manifest = {"tools": {"ruff": {"version": "1.0"}}}
    ruff = results(process(submit(user, plan)))["ruff"]
    assert ruff.status == TStatus.SUCCEEDED
    assert ruff.tool_version == "1.0"
    assert "version_unverified" in ruff.diagnostics["version_note"]


def test_changed_image_definition_fails_static_tools_but_not_the_review(user, world, api_key):
    world.sandbox.image_error = sandbox.ImageUnavailable("definition changed")
    plan = fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())
    run = process(submit(user, plan))
    rows = results(run)
    assert (rows["ruff"].status, rows["ruff"].failure_reason) == (TStatus.FAILED, "unavailable")
    assert rows["ai_review"].status == TStatus.SUCCEEDED
    assert run.status == Status.PARTIAL


def test_no_capacity_within_the_wait_fails_the_tool(user, world):
    world.sandbox.capacity_ok = False
    run = process(submit(user, fx.make_plan([fx.tool_entry("ruff")])))
    ruff = results(run)["ruff"]
    assert (ruff.status, ruff.failure_reason) == (TStatus.FAILED, "infra_error")
    assert "No apps-daemon memory" in ruff.error_message


def test_tool_infra_error_is_retried(user, world):
    world.sandbox.outcomes["ruff"] = [fx.ok_outcome(exit_code=None, infra_error="APIError 500"), fx.ok_outcome()]
    run = process(submit(user))
    ruff = results(run)["ruff"]
    assert ruff.status == TStatus.SUCCEEDED
    assert ruff.attempt == 2
    assert world.sandbox.calls == ["ruff", "ruff", "bandit"]
    assert run.status == Status.COMPLETED


def test_tool_infra_error_past_max_attempts_fails_the_tool(user, world):
    world.sandbox.outcomes["ruff"] = [fx.ok_outcome(exit_code=None, infra_error="APIError 500")] * 3
    run = process(submit(user))
    ruff = results(run)["ruff"]
    assert (ruff.status, ruff.failure_reason, ruff.attempt) == (TStatus.FAILED, "infra_error", 3)
    assert run.status == Status.PARTIAL


def test_daemon_down_requeues_the_run_then_resumes(user, world):
    world.sandbox.daemon_up = False
    world.sandbox.outcomes["bandit"] = [fx.ok_outcome(exit_code=None, infra_error="connection refused")]
    run = process(submit(user))
    assert run.status == Status.QUEUED
    assert run.lease_owner == ""
    assert run.phase == "requeued"
    assert world.enqueued == [(str(run.pk), executor.INFRA_RETRY_SECONDS)]
    rows = results(run)
    assert rows["ruff"].status == TStatus.SUCCEEDED
    assert rows["bandit"].status == TStatus.PENDING

    world.sandbox.daemon_up = True
    run = process(run)
    assert run.status == Status.COMPLETED
    assert run.attempt == 2
    assert world.sandbox.calls == ["ruff", "bandit", "bandit"]
    assert Finding.objects.filter(result=results(run)["ruff"]).count() == 1


def test_daemon_down_on_the_last_attempt_fails_the_static_tools(user, world):
    world.sandbox.volume_error = sandbox.SandboxError("daemon unreachable")
    run = submit(user)
    AnalysisRun.objects.filter(pk=run.pk).update(attempt=2)
    run = process(run)
    assert run.status == Status.FAILED
    assert {r.failure_reason for r in results(run).values()} == {"infra_error"}


# --------------------------------------------------------------------------
# Cancellation
# --------------------------------------------------------------------------


def test_cancel_mid_tool_is_final(user, world):
    run = submit(user)

    def cancel_during(slug):
        if slug == "ruff":
            executor.request_cancel(AnalysisRun.objects.get(pk=run.pk))

    world.sandbox.on_run = cancel_during
    run = process(run)
    assert run.status == Status.CANCELLED
    assert run.cancel_requested
    assert not run.is_complete
    assert world.sandbox.calls == ["ruff"]
    rows = results(run)
    assert rows["ruff"].status == TStatus.CANCELLED
    assert rows["bandit"].status == TStatus.CANCELLED
    assert rows["bandit"].failure_reason == "cancelled"
    # Never overwritten: running it again does nothing.
    run = process(run)
    assert run.status == Status.CANCELLED
    assert world.sandbox.calls == ["ruff"]


def test_cancel_between_tools_keeps_finished_results(user, world):
    run = submit(user)

    def ruff_then_cancel(**kwargs):
        executor.request_cancel(AnalysisRun.objects.get(pk=run.pk))
        return fx.ok_outcome()

    world.sandbox.outcomes["ruff"] = [ruff_then_cancel]
    run = process(run)
    rows = results(run)
    assert rows["ruff"].status == TStatus.SUCCEEDED
    assert rows["bandit"].status == TStatus.CANCELLED
    assert run.status == Status.CANCELLED
    assert world.sandbox.calls == ["ruff"]


def test_cancel_of_a_queued_run_is_immediate(user, world):
    run = submit(user)
    run = executor.request_cancel(run)
    assert run.status == Status.CANCELLED
    assert {r.status for r in results(run).values()} == {TStatus.CANCELLED}
    run = process(run)
    assert world.sandbox.calls == []
    assert run.status == Status.CANCELLED


def test_cancel_of_a_finished_run_is_a_no_op(user, world):
    run = process(submit(user))
    run = executor.request_cancel(run)
    assert run.status == Status.COMPLETED
    assert not run.cancel_requested


def test_cancel_landing_during_finalize_wins(user, world, monkeypatch):
    run = submit(user)

    def compute(r):
        AnalysisRun.objects.filter(pk=r.pk).update(cancel_requested=True)
        return fx.fake_compute(r)

    monkeypatch.setattr(measurement, "compute", compute)
    run = process(run)
    assert run.status == Status.CANCELLED
    assert results(run)["ruff"].status == TStatus.SUCCEEDED


def test_cancel_during_the_review_keeps_its_evidence(user, world, api_key):
    plan = fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())
    run = submit(user, plan)
    world.review["during"] = lambda is_cancelled: AnalysisRun.objects.filter(pk=run.pk).update(cancel_requested=True)
    world.review["outcome"] = fx.review_outcome(ok=1, failed=0, status="failed")
    run = process(run)
    assert run.status == Status.CANCELLED
    ai = results(run)["ai_review"]
    assert ai.status == TStatus.CANCELLED
    assert ReviewSample.objects.filter(result=ai).count() == 1
    assert ai.summary["cost_usd"] == pytest.approx(0.01)


# --------------------------------------------------------------------------
# AI reviewer
# --------------------------------------------------------------------------


def test_review_persists_samples_findings_and_cost(user, world, api_key):
    plan = fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())
    run = process(submit(user, plan))
    assert run.status == Status.COMPLETED
    ai = results(run)["ai_review"]
    assert ai.status == TStatus.SUCCEEDED
    assert ai.tool_kind == "ai"
    assert ai.summary == {
        "samples": 3,
        "samples_ok": 3,
        "findings": 3,
        "consensus": 3,
        "counted": 3,
        "cost_usd": pytest.approx(0.03),
        "cost_complete": True,
        "threshold": 2,
    }
    # The reviewer gets the resolved access (OpenRouter here), not a bare key string.
    assert world.review["calls"][0]["api_key"].api_key == "sk-test"
    assert world.review["calls"][0]["api_key"].is_openrouter
    assert world.review["calls"][0]["frozen"]["model"]["slug"] == "deepseek/deepseek-v4.1-flash"
    # The plan's scope with its counting rules folded in (consensus applies them like normalize).
    assert world.review["calls"][0]["scope"] == {**plan["scope"], "count_style": False, "count_metric_findings": False}
    samples = list(ReviewSample.objects.filter(result=ai).order_by("index"))
    assert [s.index for s in samples] == [0, 1, 2]
    assert samples[0].finding_count == 1
    assert artifacts.load(samples[0].artifacts["response.json"]) == b'{"findings": []}'
    ai_findings = Finding.objects.filter(result=ai)
    assert ai_findings.count() == 3
    assert all(f.is_ai and f.review_sample_id for f in ai_findings)
    assert {f.review_sample.index for f in ai_findings} == {0, 1, 2}


def test_missing_api_key_makes_the_review_unavailable(user, world):
    plan = fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())
    run = process(submit(user, plan))
    ai = results(run)["ai_review"]
    assert (ai.status, ai.failure_reason) == (TStatus.FAILED, "unavailable")
    assert "API key" in ai.error_message
    assert world.review["calls"] == []
    assert run.status == Status.PARTIAL


def test_review_uses_the_access_resolved_for_the_frozen_model(user, world, monkeypatch):
    """A local reviewer model needs no OpenRouter key: the executor resolves the frozen model's access."""
    from backend.analysis.engine.reviewer import client as review_client

    seen = []
    access = object()

    def resolve(owner, frozen_model):
        seen.append((owner, frozen_model))
        return access

    monkeypatch.setattr(review_client, "resolve_review_access", resolve)
    plan = fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())
    run = process(submit(user, plan))
    assert run.status == Status.COMPLETED
    assert seen == [(user, plan["reviewer"]["model"])]
    assert world.review["calls"][0]["api_key"] is access


def test_unreachable_review_model_makes_the_review_unavailable(user, world, monkeypatch):
    from backend.analysis.engine.reviewer import client as review_client
    from backend.inference.access import LLMAccessError

    def resolve(owner, frozen_model):
        raise LLMAccessError(
            code="model_unavailable",
            message="the endpoint now serves other weights",
            retryable=False,
            remediation="Re-freeze the plan.",
        )

    monkeypatch.setattr(review_client, "resolve_review_access", resolve)
    plan = fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())
    run = process(submit(user, plan))
    ai = results(run)["ai_review"]
    assert (ai.status, ai.failure_reason) == (TStatus.FAILED, "unavailable")
    assert ai.error_message == "AI review unavailable: the endpoint now serves other weights Re-freeze the plan."
    assert world.review["calls"] == []


def test_review_without_quorum_fails_but_keeps_samples(user, world, api_key):
    world.review["outcome"] = fx.review_outcome(ok=1, failed=2)
    plan = fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry(required=False))
    run = process(submit(user, plan))
    ai = results(run)["ai_review"]
    assert (ai.status, ai.failure_reason) == (TStatus.FAILED, "invalid_output")
    assert ReviewSample.objects.filter(result=ai).count() == 3
    assert ai.summary["cost_usd"] == pytest.approx(0.03)
    assert run.status == Status.COMPLETED  # the reviewer was optional


def test_review_with_nothing_in_scope_is_not_applicable(user, world, api_key):
    world.review["outcome"] = fx.ReviewOutcome(status="not_applicable", message="no reviewable files")
    plan = fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())
    run = process(submit(user, plan))
    ai = results(run)["ai_review"]
    assert (ai.status, ai.error_message) == (TStatus.NOT_APPLICABLE, "no reviewable files")
    assert run.status == Status.COMPLETED


def test_review_cancelled_by_the_reviewer_itself(user, world, api_key):
    plan = fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())
    run = submit(user, plan)

    def cancel(is_cancelled):
        AnalysisRun.objects.filter(pk=run.pk).update(cancel_requested=True)

    world.review["during"] = cancel
    world.review["outcome"] = fx.ReviewOutcome(status="cancelled", failure_reason="cancelled")
    run = process(run)
    assert results(run)["ai_review"].status == TStatus.CANCELLED
    assert run.status == Status.CANCELLED


def test_review_cancelled_inside_the_reviewer_is_a_cancelled_tool(user, world, api_key):
    # e.g. the reviewer's own wait saw a stop; the run itself was not cancelled.
    world.review["outcome"] = fx.review_outcome(ok=1, status="cancelled")
    plan = fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())
    run = process(submit(user, plan))
    ai = results(run)["ai_review"]
    assert (ai.status, ai.failure_reason) == (TStatus.CANCELLED, "cancelled")
    assert ReviewSample.objects.filter(result=ai).count() == 1  # its spend is kept
    assert not Finding.objects.filter(result=ai, counted=True).exists()  # no quorum, no consensus
    assert ai.summary["counted"] == 0
    assert run.status == Status.PARTIAL  # a required tool did not succeed; the run was not cancelled


def test_review_scope_carries_the_plans_counting_rules(user, world, api_key):
    plan = fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())
    plan["outcomes"].update(count_style=True, count_metric_findings=True)
    process(submit(user, plan))
    scope = world.review["calls"][0]["scope"]
    assert (scope["count_style"], scope["count_metric_findings"]) == (True, True)
    assert scope["measured_origins"] == plan["scope"]["measured_origins"]


def test_review_crash_is_a_tool_error(user, world, api_key):
    world.review["error"] = RuntimeError("provider exploded")
    plan = fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())
    ai = results(process(submit(user, plan)))["ai_review"]
    assert (ai.status, ai.failure_reason) == (TStatus.FAILED, "tool_error")
    assert "provider exploded" in ai.error_message


def test_review_only_plan(user, world, api_key):
    run = process(submit(user, fx.make_plan([], reviewer_cfg=fx.reviewer_entry())))
    assert run.status == Status.COMPLETED
    assert world.sandbox.ensure_calls == []  # no analyzer image needed


# --------------------------------------------------------------------------
# Sample verification
# --------------------------------------------------------------------------


def test_changed_sample_fails_the_run(user, world):
    run = submit(user)
    AnalysisRun.objects.filter(pk=run.pk).update(source_code={**APP, "app.py": "print('changed')\n"})
    run = process(run)
    assert run.status == Status.FAILED
    assert run.error_message.startswith("sample_changed:")
    assert "changed since this run was submitted" in run.error_message
    assert {r.status for r in results(run).values()} == {TStatus.SKIPPED}
    assert world.sandbox.calls == []


def test_changed_job_fails_the_run(user, world):
    job = GenerationJobFactory(created_by=user, status="completed", result_data={"files": APP})
    run = executor.submit_run(user=user, plan=fx.make_plan(), generation_job=job, dispatch=False)
    job.result_data = {"files": {"app.py": "x = 2\n"}}
    job.save()
    run = process(run)
    assert run.status == Status.FAILED
    assert "sample_changed" in run.error_message


def test_deleted_job_fails_the_run(user, world):
    job = GenerationJobFactory(created_by=user, status="completed", result_data={"files": APP})
    run = executor.submit_run(user=user, plan=fx.make_plan(), generation_job=job, dispatch=False)
    job.delete()
    run = process(run)
    assert run.status == Status.FAILED
    assert run.error_message.startswith("target_deleted:")


def test_load_sample_verifies_the_hash(user, world):
    run = submit(user)
    assert executor.load_sample(run).sample_hash == run.sample_hash
    run.source_code = {"other.py": "x = 1\n"}
    with pytest.raises(executor.SampleChanged):
        executor.load_sample(run)


# --------------------------------------------------------------------------
# Leases, slots, recovery
# --------------------------------------------------------------------------


def test_stale_lease_recovery_requeues_and_resumes_without_duplicates(user, world):
    run = submit(user)

    def die_on_bandit(slug):
        if slug == "bandit":
            raise _WorkerDied

    world.sandbox.on_run = die_on_bandit
    with pytest.raises(_WorkerDied):
        executor.process_run(str(run.pk))
    run.refresh_from_db()
    assert run.status == Status.RUNNING
    assert results(run)["bandit"].status == TStatus.RUNNING
    ruff_findings = list(Finding.objects.filter(result=results(run)["ruff"]).values_list("pk", flat=True))
    assert len(ruff_findings) == 1

    # The dead worker's lease goes stale; reconcile requeues it.
    AnalysisRun.objects.filter(pk=run.pk).update(heartbeat_at=timezone.now() - timedelta(minutes=10))
    report = executor.reconcile()
    assert report["requeued"] == [str(run.pk)]
    run.refresh_from_db()
    assert run.status == Status.QUEUED
    assert results(run)["bandit"].status == TStatus.PENDING

    world.sandbox.on_run = None
    world.sandbox.calls.clear()
    run = process(run)
    assert run.status == Status.COMPLETED
    assert run.attempt == 2
    assert world.sandbox.calls == ["bandit"]  # the finished tool is not re-run
    assert list(Finding.objects.filter(result=results(run)["ruff"]).values_list("pk", flat=True)) == ruff_findings
    assert Finding.objects.filter(run=run).count() == 3
    assert results(run)["bandit"].attempt == 2


def test_a_redelivered_message_takes_over_a_stale_lease(user, world):
    run = submit(user)
    AnalysisRun.objects.filter(pk=run.pk).update(
        status=Status.RUNNING,
        lease_owner="dead-worker",
        heartbeat_at=timezone.now() - timedelta(minutes=10),
        attempt=1,
    )
    run = process(run)
    assert run.status == Status.COMPLETED
    assert run.attempt == 2


def test_a_live_lease_is_never_taken(user, world):
    run = submit(user)
    AnalysisRun.objects.filter(pk=run.pk).update(
        status=Status.RUNNING,
        lease_owner="live-worker",
        heartbeat_at=timezone.now(),
        attempt=1,
    )
    run = process(run)
    assert run.status == Status.RUNNING
    assert run.lease_owner == "live-worker"
    assert world.sandbox.calls == []


def test_a_worker_that_lost_its_lease_writes_nothing(user, world):
    run = submit(user)

    def steal_on_bandit(slug):
        if slug == "bandit":
            AnalysisRun.objects.filter(pk=run.pk).update(lease_owner="new-owner")

    world.sandbox.on_run = steal_on_bandit
    run = process(run)
    assert run.status == Status.RUNNING
    assert run.lease_owner == "new-owner"
    assert results(run)["bandit"].status == TStatus.RUNNING
    assert not Finding.objects.filter(result=results(run)["bandit"]).exists()


def test_exhausted_attempts_fail_the_run_at_claim(user, world):
    run = submit(user)
    AnalysisRun.objects.filter(pk=run.pk).update(attempt=3)
    run = process(run)
    assert run.status == Status.FAILED
    assert "interrupted 3 times" in run.error_message
    assert {r.failure_reason for r in results(run).values()} == {"infra_error"}
    assert world.sandbox.calls == []


def test_a_tool_interrupted_too_often_fails(user, world):
    run = submit(user)
    ToolResult.objects.filter(run=run, tool_slug="ruff").update(attempt=3)
    run = process(run)
    ruff = results(run)["ruff"]
    assert (ruff.status, ruff.failure_reason) == (TStatus.FAILED, "infra_error")
    assert world.sandbox.calls == ["bandit"]


def test_no_free_slot_defers_the_run(user, world, monkeypatch):
    run = submit(user)
    monkeypatch.setattr(locks, "try_lock", lambda key, **kwargs: False)
    run = process(run)
    assert run.status == Status.QUEUED
    assert run.phase == "waiting_for_slot"
    assert len(world.enqueued) == 1
    rid, countdown = world.enqueued[0]
    assert rid == str(run.pk)
    assert executor.SLOT_RETRY_SECONDS <= countdown <= executor.SLOT_RETRY_SECONDS + executor.SLOT_RETRY_JITTER
    assert world.sandbox.calls == []


def test_slots_are_advisory_locks_released_after_the_run(user, world, monkeypatch):
    seen = []
    world.sandbox.on_run = lambda slug: seen.append(executor.slot_status()["in_use"])
    process(submit(user))
    assert seen == [1, 1]
    assert executor.slot_status() == {"total": 2, "in_use": 0}


def test_executor_crash_requeues_the_run(user, world, monkeypatch):
    def broken(self, results, sample):
        msg = "bug"
        raise RuntimeError(msg)

    monkeypatch.setattr(executor._RunExecution, "_static_phase", broken)
    run = process(submit(user))
    assert run.status == Status.QUEUED
    assert "Executor error: RuntimeError: bug" in run.error_message
    assert world.enqueued == [(str(run.pk), executor.INFRA_RETRY_SECONDS)]


def test_executor_crash_on_the_last_attempt_fails_the_run(user, world, monkeypatch):
    def broken(self, results, sample):
        msg = "bug"
        raise RuntimeError(msg)

    monkeypatch.setattr(executor._RunExecution, "_static_phase", broken)
    run = submit(user)
    AnalysisRun.objects.filter(pk=run.pk).update(attempt=2)
    run = process(run)
    assert run.status == Status.FAILED
    assert "RuntimeError: bug" in run.error_message


def test_measure_stage_failure_fails_the_run_and_retry_measures_again(user, world, monkeypatch):
    """Fail closed: a crashed measure stage never yields a complete (canonical) run without outcomes."""
    monkeypatch.setattr(measurement, "compute", lambda run: (_ for _ in ()).throw(RuntimeError("measure bug")))
    monkeypatch.setattr(measurement, "summarize", lambda run: (_ for _ in ()).throw(RuntimeError("summary bug")))
    run = process(submit(user))
    assert (run.status, run.is_complete) == (Status.FAILED, False)
    assert run.error_message.startswith("measurement_failed: measurement failed: RuntimeError: measure bug")
    assert run.measurement == {}
    assert run.summary["schema_version"] == 2
    assert run.summary["total_findings"] == 3
    assert run.summary["complete"] is False
    assert any("measure bug" in w for w in run.summary["warnings"])
    assert {r.status for r in results(run).values()} == {TStatus.SUCCEEDED}

    # Nothing failed at the tool level, yet the run can be retried: finalize runs again, no tool does.
    monkeypatch.setattr(measurement, "compute", fx.fake_compute)
    monkeypatch.setattr(measurement, "summarize", fx.fake_summarize)
    run = executor.retry(run)
    assert run.status == Status.QUEUED
    world.sandbox.calls.clear()
    run = process(run)
    assert (run.status, run.is_complete) == (Status.COMPLETED, True)
    assert run.measurement["tool_status"] == {"ruff": TStatus.SUCCEEDED, "bandit": TStatus.SUCCEEDED}
    assert world.sandbox.calls == []


def test_a_measure_stage_returning_nothing_is_a_failure(user, world, monkeypatch):
    monkeypatch.setattr(measurement, "compute", lambda run: {})
    run = process(submit(user))
    assert (run.status, run.is_complete) == (Status.FAILED, False)
    assert "measure stage returned nothing" in run.error_message


def test_dedup_failure_fails_the_run_instead_of_overcounting(user, world, monkeypatch):
    """Without issue keys every finding would be its own issue: no measurement is computed from that."""
    computed = []

    def boom(findings, **kwargs):
        msg = "dedup bug"
        raise TypeError(msg)

    monkeypatch.setattr(dedup, "assign_issue_keys", boom)
    monkeypatch.setattr(measurement, "compute", lambda run: computed.append(run.pk) or fx.fake_compute(run))
    job = GenerationJobFactory(status="completed", result_data={"files": APP}, created_by=user)
    good = process(executor.submit_run(user=user, plan=fx.make_plan(), generation_job=job, dispatch=False))
    assert good.status == Status.FAILED
    assert good.error_message.startswith("measurement_failed: issue deduplication failed: TypeError: dedup bug")
    assert good.measurement == {}
    assert computed == []
    assert any("Issue deduplication failed" in w for w in good.summary["warnings"])
    # A failed run is never canonical, so it cannot shadow an earlier valid run of the job.
    assert measurement.canonical_run_ids([job.pk]) == []
    with pytest.raises(ValueError, match="Nothing to retry"):
        executor.retry(good, tools=["ruff"])

    monkeypatch.setattr(dedup, "assign_issue_keys", fx.fake_assign_issue_keys)
    run = process(executor.retry(good))
    assert run.status == Status.COMPLETED
    assert measurement.canonical_run_ids([job.pk]) == [run.pk]
    assert not Finding.objects.filter(run=run, issue_key="").exists()


def test_a_summary_failure_alone_keeps_the_measurement(user, world, monkeypatch):
    monkeypatch.setattr(measurement, "summarize", lambda run: (_ for _ in ()).throw(RuntimeError("summary bug")))
    run = process(submit(user))
    assert (run.status, run.is_complete) == (Status.COMPLETED, True)
    assert run.measurement["tool_status"]
    assert run.summary["total_findings"] == 3
    assert any("summary bug" in w for w in run.summary["warnings"])


def test_a_soft_time_limit_during_finalize_is_never_swallowed(user, world, monkeypatch):
    from celery.exceptions import SoftTimeLimitExceeded

    def slow(findings, **kwargs):
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(dedup, "assign_issue_keys", slow)
    run = process(submit(user))
    assert (run.status, run.is_complete) == (Status.FAILED, False)
    assert "SoftTimeLimitExceeded" in run.error_message
    assert world.enqueued == []


# --------------------------------------------------------------------------
# Studies
# --------------------------------------------------------------------------


@pytest.fixture
def study_hook(monkeypatch):
    """Records research.study.on_run_finished calls (the hook may not exist yet: raising=False)."""
    from backend.analysis.research import study as study_mod

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        study_mod,
        "on_run_finished",
        lambda run: calls.append((str(run.pk), run.status)),
        raising=False,
    )
    return calls


def test_a_finished_study_run_notifies_its_study_after_commit(
    user,
    world,
    study_hook,
    django_capture_on_commit_callbacks,
):
    study = AnalysisStudyFactory(created_by=user)
    run = submit(user, study=study, purpose="research")
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        run = process(run)
    assert study_hook == []  # not before the terminal state is committed
    for callback in callbacks:
        callback()
    assert study_hook == [(str(run.pk), Status.COMPLETED)]


def test_runs_finished_without_a_worker_notify_their_study(
    user,
    world,
    study_hook,
    django_capture_on_commit_callbacks,
):
    study = AnalysisStudyFactory(created_by=user)
    run = submit(user, study=study, purpose="research")
    with django_capture_on_commit_callbacks(execute=True):
        executor.request_cancel(run)
    assert study_hook == [(str(run.pk), Status.CANCELLED)]


def test_an_adhoc_run_notifies_no_study(user, world, study_hook, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        process(submit(user))
    assert study_hook == []


def test_a_failing_study_hook_never_breaks_the_run(user, world, monkeypatch, django_capture_on_commit_callbacks):
    from backend.analysis.research import study as study_mod

    def broken(run):
        msg = "study bug"
        raise RuntimeError(msg)

    monkeypatch.setattr(study_mod, "on_run_finished", broken, raising=False)
    run = submit(user, study=AnalysisStudyFactory(created_by=user), purpose="research")
    with django_capture_on_commit_callbacks(execute=True):
        run = process(run)
    assert run.status == Status.COMPLETED


# --------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------


def test_retry_resets_only_failed_tools(user, world, django_capture_on_commit_callbacks):
    plan = fx.make_plan([fx.tool_entry("ruff"), fx.tool_entry("bandit"), fx.tool_entry("eslint")])
    world.sandbox.outcomes["bandit"] = [fx.ok_outcome(exit_code=2)]
    run = process(submit(user, plan))
    assert run.status == Status.PARTIAL
    ruff_ids = set(Finding.objects.filter(result=results(run)["ruff"]).values_list("pk", flat=True))

    with django_capture_on_commit_callbacks(execute=True):
        run = executor.retry(run)
    assert run.status == Status.QUEUED
    assert run.attempt == 0
    assert run.measurement == {}
    assert world.enqueued[-1] == (str(run.pk), 0)
    rows = results(run)
    assert rows["bandit"].status == TStatus.PENDING
    assert rows["bandit"].attempt == 0
    assert rows["bandit"].artifacts == {}
    assert rows["ruff"].status == TStatus.SUCCEEDED
    assert rows["eslint"].status == TStatus.NOT_APPLICABLE

    world.sandbox.calls.clear()
    run = process(run)
    assert run.status == Status.COMPLETED
    assert world.sandbox.calls == ["bandit"]
    assert set(Finding.objects.filter(result=results(run)["ruff"]).values_list("pk", flat=True)) == ruff_ids


def test_retry_selected_tools_and_guards(user, world):
    world.sandbox.outcomes = {"ruff": [fx.ok_outcome(exit_code=2)], "bandit": [fx.ok_outcome(exit_code=2)]}
    run = process(submit(user))
    with pytest.raises(ValueError, match="Unknown tool"):
        executor.retry(run, tools=["nope"])
    run = executor.retry(run, tools=["bandit"])
    assert results(run)["bandit"].status == TStatus.PENDING
    assert results(run)["ruff"].status == TStatus.FAILED
    with pytest.raises(ValueError, match="still queued or running"):
        executor.retry(run)


def test_retry_with_nothing_to_retry(user, world):
    run = process(submit(user))
    with pytest.raises(ValueError, match="Nothing to retry"):
        executor.retry(run)


def test_retry_of_a_cancelled_run(user, world):
    run = executor.request_cancel(submit(user))
    run = executor.retry(run)
    assert run.status == Status.QUEUED
    assert not run.cancel_requested
    run = process(run)
    assert run.status == Status.COMPLETED


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def _r(slug, status, *, required=True, reason=""):
    return ToolResult(tool_slug=slug, status=status, required=required, failure_reason=reason)


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([_r("a", "succeeded"), _r("b", "not_applicable")], (Status.COMPLETED, True)),
        ([_r("a", "succeeded"), _r("b", "failed", reason="oom")], (Status.PARTIAL, False)),
        ([_r("a", "succeeded"), _r("b", "timed_out", required=False)], (Status.COMPLETED, True)),
        ([_r("a", "failed"), _r("b", "timed_out")], (Status.FAILED, False)),
        ([_r("a", "not_applicable")], (Status.FAILED, False)),
        ([], (Status.FAILED, False)),
        ([_r("a", "succeeded"), _r("b", "skipped")], (Status.PARTIAL, False)),
    ],
)
def test_run_status_rules(rows, expected):
    status, complete, _message = executor.run_status(rows)
    assert (status, complete) == expected


def test_run_status_cancel_and_error_take_precedence():
    rows = [_r("a", "succeeded")]
    assert executor.run_status(rows, cancelled=True)[0] == Status.CANCELLED
    assert executor.run_status(rows, error="sample_changed: x") == (Status.FAILED, False, "sample_changed: x")


def test_interpret_gets_the_collected_outputs(user, world):
    seen = {}

    def interpret(outcome, files, config):
        seen["out"] = dict(outcome.out_files)
        seen["files"] = [f.path for f in files]
        return ToolParse()

    world.adapters["ruff"] = fx.FakeAdapter("ruff", interpret=interpret)
    world.sandbox.outcomes["ruff"] = [fx.ok_outcome(out_files={"report.json": b"[]", "extra.txt": b"x"})]
    process(submit(user))
    assert seen == {"out": {"report.json": b"[]", "extra.txt": b"x"}, "files": ["app.py"]}


# --------------------------------------------------------------------------
# Frozen procedure (plan_hash equal <=> measured by the same code)
# --------------------------------------------------------------------------


def _drift_spec(monkeypatch, slug: str, **changes) -> None:
    """Simulate a deploy that changed *slug*'s spec (e.g. an adapter_version bump) after plans were frozen."""
    real = catalog.get_spec
    changed = dataclasses.replace(real(slug), spec_hash="0" * 64, **changes)
    monkeypatch.setattr(catalog, "get_spec", lambda s: changed if s == slug else real(s))


def test_a_tool_whose_spec_changed_after_freezing_fails_with_procedure_drift(user, world, monkeypatch):
    run = submit(user)  # frozen with the current ruff spec
    _drift_spec(monkeypatch, "ruff", timeout_s=7)
    run = process(run)
    ruff, bandit = results(run)["ruff"], results(run)["bandit"]
    assert (ruff.status, ruff.failure_reason) == (TStatus.FAILED, "unavailable")
    assert ruff.error_message.startswith("procedure_drift: the ruff spec changed since the plan was frozen")
    assert "spec_hash" in ruff.diagnostics["procedure_drift"]
    assert world.sandbox.calls == ["bandit"]  # never executed with the changed adapter
    assert bandit.status == TStatus.SUCCEEDED
    assert (run.status, run.is_complete) == (Status.PARTIAL, False)
    assert run.summary["procedure"]["drift"]["tools"].keys() == {"ruff"}


def test_a_tool_that_left_the_catalog_is_procedure_drift(user, world):
    run = process(submit(user, fx.make_plan([fx.tool_entry("ruff"), fx.tool_entry("ghost")])))
    ghost = results(run)["ghost"]
    assert (ghost.status, ghost.failure_reason) == (TStatus.FAILED, "unavailable")
    assert "no longer in the catalog" in ghost.error_message


@pytest.mark.parametrize("stage", ["measurement", "normalizer", "dedup", "sloc", "sample_resolver"])
def test_an_engine_stage_change_after_freezing_fails_the_whole_run(user, world, api_key, monkeypatch, stage):
    constant = {
        "measurement": "MEASUREMENT_VERSION",
        "normalizer": "NORMALIZER_VERSION",
        "dedup": "DEDUP_VERSION",
        "sloc": "SLOC_VERSION",
        "sample_resolver": "SAMPLE_RESOLVER_VERSION",
    }[stage]
    run = submit(user, fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry()))
    monkeypatch.setattr(types, constant, "v-next")
    # Stages that bind their version at import are read from that binding.
    for module in (dedup, measurement):
        if hasattr(module, constant):
            monkeypatch.setattr(module, constant, "v-next")
    run = process(run)
    assert (run.status, run.is_complete) == (Status.FAILED, False)
    assert run.error_message.startswith("procedure_drift: the running analysis engine no longer implements")
    assert f"{stage} " in run.error_message
    # Nothing ran: no tool, and no paid review.
    assert world.sandbox.calls == []
    assert world.review["calls"] == []
    assert {r.status for r in results(run).values()} == {TStatus.SKIPPED}
    assert run.measurement == {}
    assert run.summary["procedure"]["verified"] is False


def test_a_mapping_table_change_after_freezing_fails_the_run(user, world, monkeypatch):
    run = submit(user)
    changed = {**normalize.mapping_hashes(), "severity": "f" * 64}
    monkeypatch.setattr(normalize, "mapping_hashes", lambda: changed)
    run = process(run)
    assert run.status == Status.FAILED
    assert "mapping table severity" in run.error_message
    assert world.sandbox.calls == []


def test_retrying_under_drifted_code_never_mixes_procedures(user, world, monkeypatch):
    world.sandbox.outcomes["bandit"] = [fx.ok_outcome(exit_code=2)]
    run = process(submit(user))
    assert run.status == Status.PARTIAL
    monkeypatch.setattr(measurement, "MEASUREMENT_VERSION", "measure-v-next")
    run = process(executor.retry(run))
    assert run.status == Status.FAILED
    assert run.error_message.startswith("procedure_drift")
    assert results(run)["bandit"].status == TStatus.SKIPPED
    assert results(run)["ruff"].status == TStatus.SUCCEEDED  # kept, measured by the plan's code


def test_an_unverifiable_procedure_is_drift(user, world, monkeypatch):
    run = submit(user)
    monkeypatch.setattr(normalize, "mapping_hashes", lambda: (_ for _ in ()).throw(OSError("mappings gone")))
    run = process(run)
    assert run.status == Status.FAILED
    assert "could not be verified: OSError: mappings gone" in run.error_message


def test_runs_record_the_procedure_that_measured_them(user, world):
    run = process(submit(user))
    spec = catalog.get_spec("ruff")
    assert results(run)["ruff"].diagnostics["procedure"] == {
        "spec_hash": spec.spec_hash,
        "adapter_version": spec.adapter_version,
    }
    assert run.summary["procedure"] == {
        "verified": True,
        "engine_versions": plan_mod.engine_versions(),
        "drift": {},
    }


# --------------------------------------------------------------------------
# The paid AI review is never re-run automatically
# --------------------------------------------------------------------------


def _budgeted_reviewer(**kwargs) -> dict:
    return {**fx.reviewer_entry(**kwargs), "budget": {"max_cost_usd": 0.5, "max_llm_requests": 40}}


def test_worker_death_during_the_review_never_re_runs_it(user, world, api_key):
    run = submit(user, fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=_budgeted_reviewer()))

    def die(is_cancelled):
        raise _WorkerDied

    world.review["during"] = die
    with pytest.raises(_WorkerDied):
        executor.process_run(str(run.pk))
    assert results(run)["ai_review"].status == TStatus.RUNNING

    AnalysisRun.objects.filter(pk=run.pk).update(heartbeat_at=timezone.now() - timedelta(minutes=10))
    assert executor.reconcile()["requeued"] == [str(run.pk)]
    ai = results(run)["ai_review"]
    assert (ai.status, ai.failure_reason) == (TStatus.FAILED, "infra_error")
    assert ai.error_message.startswith("interrupted:")
    # The three replicates stored as they finished keep their spend: a lower bound (the call in flight is unknown).
    assert ai.summary == {
        "cost_usd": 0.03,
        "cost_unknown": True,
        "cost_complete": False,
        "max_cost_usd": 1.5,
        "samples_persisted": 3,
    }
    assert ReviewSample.objects.filter(result=ai).count() == 3
    assert ai.diagnostics["interrupted"] is True

    world.review["during"] = None
    run = process(run)
    assert len(world.review["calls"]) == 1  # paid once, never again automatically
    assert run.status == Status.PARTIAL  # the reviewer was required
    assert any("spend is not recorded (at most $1.5000" in w for w in run.summary["warnings"])


def test_a_redelivered_message_after_worker_death_does_not_re_run_the_review(user, world, api_key):
    run = submit(user, fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry(required=False)))
    ToolResult.objects.filter(run=run, tool_slug="ai_review").update(status=TStatus.RUNNING, attempt=1)
    AnalysisRun.objects.filter(pk=run.pk).update(
        status=Status.RUNNING,
        lease_owner="dead-worker",
        heartbeat_at=timezone.now() - timedelta(minutes=10),
        attempt=1,
    )
    run = process(run)
    ai = results(run)["ai_review"]
    assert (ai.status, ai.failure_reason) == (TStatus.FAILED, "infra_error")
    assert world.review["calls"] == []
    assert run.status == Status.COMPLETED  # optional reviewer; the static tools ran


def test_a_pending_review_that_already_started_is_not_re_run(user, world, api_key):
    """Defence in depth: only a user retry (attempt reset to 0) may start a review again."""
    run = submit(user, fx.make_plan([], reviewer_cfg=fx.reviewer_entry()))
    ToolResult.objects.filter(run=run, tool_slug="ai_review").update(attempt=1)
    run = process(run)
    ai = results(run)["ai_review"]
    assert ai.error_message.startswith("interrupted:")
    assert world.review["calls"] == []


def test_storing_the_review_failing_keeps_its_spend_and_never_requeues(user, world, api_key, monkeypatch):
    original = executor._finding_row

    def broken(result, nf, **kwargs):
        if kwargs.get("is_ai"):
            msg = "unstorable model output"
            raise ValueError(msg)
        return original(result, nf, **kwargs)

    monkeypatch.setattr(executor, "_finding_row", broken)
    run = process(submit(user, fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())))
    assert world.enqueued == []
    assert len(world.review["calls"]) == 1
    ai = results(run)["ai_review"]
    assert (ai.status, ai.failure_reason) == (TStatus.FAILED, "tool_error")
    assert "Storing the AI review failed (ValueError: unstorable model output)" in ai.error_message
    samples = list(ReviewSample.objects.filter(result=ai).order_by("index"))
    assert len(samples) == 3
    assert sum(s.usage["cost_usd"] for s in samples) == pytest.approx(0.03)
    assert ai.summary["cost_usd"] == pytest.approx(0.03)
    assert ai.summary["counted"] == 0
    assert not Finding.objects.filter(result=ai).exists()
    assert run.status == Status.PARTIAL


def test_a_crash_after_the_review_returned_interrupts_it_instead_of_requeueing_it(user, world, api_key, monkeypatch):
    def broken(self, result, outcome):
        msg = "disk full"
        raise RuntimeError(msg)

    monkeypatch.setattr(executor._RunExecution, "_store_review_artifacts", broken)
    run = process(submit(user, fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())))
    assert run.status == Status.QUEUED  # the executor error requeues the run...
    ai = results(run)["ai_review"]
    assert (ai.status, ai.failure_reason) == (TStatus.FAILED, "infra_error")  # ...but not the paid review
    monkeypatch.undo()
    run = process(run)
    assert len(world.review["calls"]) == 1
    assert run.status == Status.PARTIAL


def test_reviews_with_nul_characters_are_stored(user, world, api_key):
    outcome = fx.review_outcome(ok=3)
    for finding in outcome.findings:
        finding.title = "bad\x00title"
        finding.snippet = "x = '\x00'"
        finding.extra = {"raw": "a\x00b"}
    outcome.samples[0].error_message = "nul\x00"
    outcome.samples[0].providers = {"name\x00": "x\x00"}
    world.review["outcome"] = outcome
    run = process(submit(user, fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())))
    ai = results(run)["ai_review"]
    assert ai.status == TStatus.SUCCEEDED
    assert Finding.objects.filter(result=ai, title="badtitle").count() == 3


def test_retry_keeps_the_spend_of_the_review_attempts_it_replaces(user, world, api_key):
    world.review["outcome"] = fx.review_outcome(ok=1, failed=2)  # no quorum, $0.03 spent
    run = process(submit(user, fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())))
    assert results(run)["ai_review"].status == TStatus.FAILED
    run = executor.retry(run)
    ai = results(run)["ai_review"]
    assert ai.status == TStatus.PENDING
    # The succeeded replicate survives (its spend stays on its row); the two failed ones are replaced.
    [kept] = ReviewSample.objects.filter(result=ai)
    assert (kept.index, kept.status) == (0, "succeeded")
    assert not Finding.objects.filter(result=ai, counted=True).exists()
    [prior] = ai.diagnostics["prior_attempts"]
    assert prior["cost_usd"] == pytest.approx(0.02)
    assert (prior["status"], prior["samples"], prior["cost_unknown"]) == (TStatus.FAILED, 2, False)
    assert (prior["kept_samples"], prior["max_index"]) == (1, 2)

    world.review["outcome"] = fx.review_outcome(ok=2)
    run = process(run)
    call = world.review["calls"][-1]
    assert (call["resumed"], call["first_index"]) == ([0], 3)  # never re-paid, never a re-used seed
    ai = results(run)["ai_review"]
    assert ai.status == TStatus.SUCCEEDED
    assert sorted(ReviewSample.objects.filter(result=ai).values_list("index", flat=True)) == [0, 3, 4]
    assert ai.summary["cost_usd"] == pytest.approx(0.03)
    assert ai.summary["prior_attempts"] == 1
    assert ai.summary["prior_attempts_cost_usd"] == pytest.approx(0.02)
    assert ai.summary["prior_attempts_cost_unknown"] is False
    assert len(ai.diagnostics["prior_attempts"]) == 1


def test_retrying_an_interrupted_review_records_its_unknown_spend(user, world, api_key):
    run = submit(user, fx.make_plan([], reviewer_cfg=_budgeted_reviewer(samples=2)))
    ToolResult.objects.filter(run=run, tool_slug="ai_review").update(attempt=1)
    run = executor.retry(process(run))
    [prior] = results(run)["ai_review"].diagnostics["prior_attempts"]
    assert (prior["cost_usd"], prior["cost_unknown"], prior["max_cost_usd"]) == (None, True, 1.0)
    run = process(run)
    ai = results(run)["ai_review"]
    assert ai.status == TStatus.SUCCEEDED
    assert ai.summary["prior_attempts_cost_usd"] is None
    assert ai.summary["prior_attempts_cost_unknown"] is True


# --------------------------------------------------------------------------
# Infrastructure: a vanished image, requeue backoff
# --------------------------------------------------------------------------


def test_an_image_that_vanishes_mid_run_is_re_ensured_on_the_next_attempt(user, world):
    missing = fx.ok_outcome(
        exit_code=None,
        infra_error=f'ImageNotFound: 404 Client Error: Not Found ("No such image: {fx.IMAGE_ID}")',
    )
    world.sandbox.outcomes["bandit"] = [missing]
    run = process(submit(user))
    assert run.status == Status.QUEUED
    assert "analyzer image disappeared" in run.error_message
    assert world.sandbox.calls == ["ruff", "bandit"]  # the dead image id was not retried
    assert results(run)["bandit"].status == TStatus.PENDING

    run = process(run)
    assert len(world.sandbox.ensure_calls) == 2  # re-ensured (rebuilt when missing)
    assert run.status == Status.COMPLETED
    assert world.sandbox.calls == ["ruff", "bandit", "bandit"]


def test_image_missing_detection():
    assert sandbox.image_missing("ImageNotFound: 404 Client Error")
    assert sandbox.image_missing('APIError: 404 ("No such image: sha256:ab")')
    assert not sandbox.image_missing("APIError: 500 Server Error")
    assert not sandbox.image_missing(None)


def test_infra_requeues_back_off_exponentially(user, world):
    world.sandbox.image_error = sandbox.SandboxError("daemon unreachable")
    run = submit(user)
    for _ in range(3):
        run = process(run)
    assert [countdown for _rid, countdown in world.enqueued] == [60, 300]
    assert run.status == Status.FAILED
    assert {r.failure_reason for r in results(run).values()} == {"infra_error"}
    assert [executor._infra_retry_countdown(n) for n in (1, 2, 3, 4)] == [60, 300, 900, 900]


def test_a_review_lost_on_the_last_attempt_is_recorded_as_unknown_spend(user, world, api_key):
    run = submit(user, fx.make_plan([], reviewer_cfg=_budgeted_reviewer(samples=1)))
    ToolResult.objects.filter(run=run, tool_slug="ai_review").update(status=TStatus.RUNNING, attempt=1)
    AnalysisRun.objects.filter(pk=run.pk).update(
        status=Status.RUNNING,
        lease_owner="dead-worker",
        heartbeat_at=timezone.now() - timedelta(minutes=10),
        attempt=3,
    )
    assert executor.reconcile()["failed"] == [str(run.pk)]
    ai = results(run)["ai_review"]
    assert (ai.status, ai.failure_reason) == (TStatus.FAILED, "infra_error")
    assert ai.summary == {
        "cost_usd": None,
        "cost_unknown": True,
        "cost_complete": False,
        "max_cost_usd": 0.5,
        "samples_persisted": 0,
    }
    # A user retry records that attempt's spend as unknown, never as zero.
    run.refresh_from_db()
    [prior] = results(executor.retry(run))["ai_review"].diagnostics["prior_attempts"]
    assert (prior["cost_usd"], prior["cost_unknown"]) == (None, True)


# --------------------------------------------------------------------------
# Integration round: per-replicate persistence, resume, fail-closed checks, structured problems
# --------------------------------------------------------------------------


def test_each_replicate_is_stored_as_it_finishes_so_a_crash_keeps_its_spend(user, world, api_key):
    # Regression: the spend of replicates that finished before the review crashed was recorded as unknown.
    world.review["outcome"] = fx.review_outcome(ok=3)
    world.review["error"] = RuntimeError("boom")
    world.review["finished_before_error"] = 2
    run = process(submit(user, fx.make_plan([], reviewer_cfg=_budgeted_reviewer())))
    ai = results(run)["ai_review"]
    assert (ai.status, ai.failure_reason) == (TStatus.FAILED, "tool_error")
    assert ai.summary["cost_usd"] == pytest.approx(0.02)
    assert (ai.summary["cost_unknown"], ai.summary["cost_complete"], ai.summary["samples_persisted"]) == (
        True,
        False,
        2,
    )
    rows = list(ReviewSample.objects.filter(result=ai).order_by("index"))
    assert [(r.index, r.status) for r in rows] == [(0, "succeeded"), (1, "succeeded")]
    assert artifacts.load(rows[0].artifacts["response.json"]) == b'{"findings": []}'
    # Stored before consensus: a replicate's findings never count on their own.
    assert Finding.objects.filter(result=ai).count() == 2
    assert not Finding.objects.filter(result=ai, counted=True).exists()


def test_retrying_a_crashed_review_resumes_from_its_stored_replicates(user, world, api_key):
    world.review["outcome"] = fx.review_outcome(ok=3)
    world.review["error"] = RuntimeError("boom")
    world.review["finished_before_error"] = 2
    run = process(submit(user, fx.make_plan([], reviewer_cfg=_budgeted_reviewer())))
    run = executor.retry(run)
    ai = results(run)["ai_review"]
    [prior] = ai.diagnostics["prior_attempts"]
    # The kept replicates' spend stays on their rows; the crash's unknown remainder is flagged.
    assert (prior["samples"], prior["kept_samples"], prior["max_index"]) == (0, 2, 1)
    assert (prior["cost_usd"], prior["cost_unknown"]) == (None, True)

    world.review["error"] = None
    world.review["outcome"] = fx.review_outcome(ok=1)
    run = process(run)
    call = world.review["calls"][-1]
    assert (call["resumed"], call["first_index"]) == ([0, 1], 2)
    ai = results(run)["ai_review"]
    assert ai.status == TStatus.SUCCEEDED
    assert sorted(ReviewSample.objects.filter(result=ai).values_list("index", flat=True)) == [0, 1, 2]
    assert ai.summary["cost_usd"] == pytest.approx(0.03)  # nothing paid twice
    # Every replicate's findings are stored again for consensus (which the real reviewer applies to all three).
    assert sorted(Finding.objects.filter(result=ai).values_list("review_sample__index", flat=True)) == [0, 1, 2]
    # The resumed replicates' artifacts survived the retry.
    row = ReviewSample.objects.get(result=ai, index=0)
    assert artifacts.load(row.artifacts["response.json"]) == b'{"findings": []}'


def test_retry_drops_failed_replicates_and_their_artifacts(user, world, api_key, django_capture_on_commit_callbacks):
    world.review["outcome"] = fx.review_outcome(ok=1, failed=2)
    run = process(submit(user, fx.make_plan([], reviewer_cfg=fx.reviewer_entry())))
    ai = results(run)["ai_review"]
    failed_entry = ReviewSample.objects.get(result=ai, index=1).artifacts["response.json"]
    kept_entry = ReviewSample.objects.get(result=ai, index=0).artifacts["response.json"]
    with django_capture_on_commit_callbacks(execute=True):
        executor.retry(run)
    with pytest.raises(FileNotFoundError):
        artifacts.load(failed_entry)
    assert artifacts.load(kept_entry) == b'{"findings": []}'


def test_review_spend_bound_covers_replacement_replicates():
    frozen = {"budget": {"max_cost_usd": 0.5}, "samples": 3}
    assert executor._review_spend_bound(frozen) == 1.5  # a plan frozen without replacements
    assert executor._review_spend_bound({**frozen, "max_replacements": 2}) == 2.5
    assert executor._review_spend_bound({**frozen, "max_replacements": None}) == 3.0  # null = samples


def test_review_summary_cost_complete_follows_the_replicates(user, world, api_key):
    outcome = fx.review_outcome(ok=3)
    outcome.samples[1].usage = {"cost_usd": None, "llm_calls": 1}
    outcome.usage = {"cost_usd": 0.02}
    world.review["outcome"] = outcome
    run = process(submit(user, fx.make_plan([], reviewer_cfg=fx.reviewer_entry())))
    summary = results(run)["ai_review"].summary
    assert (summary["cost_usd"], summary["cost_complete"]) == (pytest.approx(0.02), False)


def test_mapping_tables_changed_mid_run_fail_closed_before_normalizing(user, world, api_key, monkeypatch):
    calls = []

    def drift(expected):
        calls.append(expected)
        return ["categories"]

    monkeypatch.setattr(normalize, "mapping_drift", drift)
    run = process(submit(user, fx.make_plan([fx.tool_entry("ruff")], reviewer_cfg=fx.reviewer_entry())))
    assert calls  # checked against the plan's frozen hashes
    assert run.status == Status.FAILED
    assert run.error_message.startswith("procedure_drift: mapping table(s) categories changed")
    rows = results(run)
    assert (rows["ruff"].status, rows["ruff"].failure_reason) == (TStatus.FAILED, "unavailable")
    assert rows["ai_review"].status == TStatus.FAILED
    assert world.review["calls"] == []  # nothing paid under drifted tables
    assert not Finding.objects.filter(run=run).exists()
    assert run.measurement == {}


def test_changed_line_attribution_fails_the_run(user, world, monkeypatch):
    monkeypatch.setattr(
        sample_mod,
        "attribution_changed",
        lambda stored, sample: "line attribution changed since submit (line -> whole_file)",
    )
    run = process(submit(user))
    assert run.status == Status.FAILED
    assert run.error_message.startswith("sample_changed: The target's line attribution changed since submit")
    assert {r.status for r in results(run).values()} == {TStatus.SKIPPED}
    assert world.sandbox.calls == []


def test_runtime_sample_warnings_reach_the_summary(user, world, monkeypatch):
    original = world.resolver.resolve_inline

    def resolve(files, scope=None, *, source="inline"):
        sample = original(files, scope, source=source)
        sample.warnings = ["app.py: content sha256 differs from the workspace manifest"]
        return sample

    run = submit(user)
    monkeypatch.setattr(sample_mod, "resolve_inline", resolve)
    run = process(run)
    assert "sample: app.py: content sha256 differs from the workspace manifest" in run.summary["warnings"]


def test_summary_lists_tool_problems_structured(user, world):
    def broken(outcome, files, config):
        raise ToolFailure(reason="parse_error", message="bad json")

    world.adapters["bandit"]._interpret = broken
    run = process(submit(user))
    assert run.summary["problems"] == [
        {
            "slug": "bandit",
            "tool_slug": "bandit",
            "status": TStatus.FAILED,
            "failure_reason": "parse_error",
            "required": True,
            "message": "bad json",
        },
    ]
    clean = process(submit(user, fx.make_plan([fx.tool_entry("ruff")])))
    assert clean.summary["problems"] == []


def test_a_cancelled_queued_run_never_shows_zero_findings(user, world, monkeypatch):
    # Regression: _finalize_unowned built the summary from rows alone and showed 0 findings.
    monkeypatch.setattr(measurement, "summarize", REAL_SUMMARIZE)
    run = executor.request_cancel(submit(user))
    assert run.status == Status.CANCELLED
    assert run.summary["total_findings"] is None
    assert run.summary["severity_counts"] is None
    assert run.summary["issues"] is None
    assert [p["status"] for p in run.summary["problems"]] == [TStatus.CANCELLED, TStatus.CANCELLED]


def test_basic_summary_follows_the_none_rule(user, world):
    run = executor.request_cancel(submit(user))
    summary = executor.basic_summary(run)
    assert (summary["total_findings"], summary["issues"], summary["static_measured"]) == (None, None, False)
    assert len(summary["problems"]) == 2
