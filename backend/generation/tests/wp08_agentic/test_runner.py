"""The agentic generation stage (spec 6.3-6.9, 6.12, 6.14) end to end on the local sandbox.

Covers: empty/skeleton-only workspace -> ``output_invalid``; termination by elapsed time; ``budget_exhausted``
when main requests reach the cap; OOM -> ``excluded:resource_limit``; infra (zero successful calls,
transport failures) -> retried with the same manifest and seed, ``RunAttempt`` history kept; harness crash
retried once; image digest mismatch -> ``infra_error``; harness not enabled -> excluded; cancel; workspace
size limit; dispatch by kind.
"""

from __future__ import annotations

import pytest

from backend.generation.agentic import dispatch
from backend.generation.agentic import runner
from backend.generation.models import GenerationRun
from backend.generation.models import RunAttempt
from backend.generation.models import RunManifest
from backend.generation.models import choices
from backend.generation.testing.fake_gateway import ScriptedReply
from backend.generation.testing.fake_gateway import canned_call
from backend.generation.tests.wp08_agentic.conftest import effective_policy

pytestmark = pytest.mark.django_db

CODE = {"backend/todos.py": b"def list_todos():\n    return []\n"}


def attempts(run: GenerationRun) -> list[RunAttempt]:
    return list(RunAttempt.objects.filter(run=run).order_by("attempt"))


def test_skeleton_only_workspace_is_output_invalid(agentic_env) -> None:
    agentic_env.script.files = {}
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert run.status == choices.RunStatus.COMPLETED
    assert (run.outcome, run.outcome_reason) == ("output_invalid", "no_model_code")
    assert run.usable is True  # analysed as model behaviour (ITT), never tested
    assert run.files.filter(origin="starter").exists()
    assert attempts(run)[0].outcome == "output_invalid"


def test_wall_clock_termination_is_decided_by_elapsed_time(agentic_env) -> None:
    agentic_env.policy = effective_policy(wall_clock_s=3)
    agentic_env.script.files = CODE
    agentic_env.script.sleep_s = 20
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    attempt = attempts(run)[0]
    assert attempt.termination == "wall_clock"
    assert run.validity["truncated"] is True
    assert {n["code"] for n in run.validity["notes"]} >= {"wall_clock"}
    assert run.phase == choices.RunPhase.EVAL_QUEUED  # partial tree, intention to treat


def test_budget_exhausted_when_main_requests_reach_the_cap(agentic_env) -> None:
    policy = effective_policy()
    run_limits = policy.content.run.model_copy(update={"max_llm_requests": 2})
    agentic_env.policy = policy.model_copy(update={"content": policy.content.model_copy(update={"run": run_limits})})
    agentic_env.gateway_overrides = {"budget": {"max_llm_requests": 5}}  # the harness stops itself at 2
    agentic_env.script.call_gateway = True
    agentic_env.script.requests = [{"messages": [{"role": "user", "content": f"step {i}"}]} for i in range(2)]
    agentic_env.script.files = CODE
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    attempt = attempts(run)[0]
    assert attempt.budget_exhausted == "requests"
    assert attempt.termination == "budget_exhausted"
    run.refresh_from_db()
    assert "budget_exhausted:requests" in {n["code"] for n in run.validity["notes"]}


def test_gateway_refusal_is_budget_exhausted(agentic_env) -> None:
    agentic_env.gateway_overrides = {"budget": {"max_llm_requests": 1}}
    agentic_env.script.call_gateway = True
    agentic_env.script.requests = [{"messages": [{"role": "user", "content": f"step {i}"}]} for i in range(3)]
    agentic_env.script.files = CODE
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    attempt = attempts(run)[0]
    assert attempt.termination == "budget_exhausted"
    assert attempt.budget_exhausted == "requests"
    assert attempt.usage["requests_by_class"] == {"main": 1}


def test_oom_is_excluded_resource_limit(agentic_env) -> None:
    agentic_env.backend.oom_killed = True
    agentic_env.script.files = CODE
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert (run.status, run.outcome, run.outcome_reason) == ("completed", "excluded", "resource_limit")
    assert run.usable is False
    assert attempts(run)[0].oom_killed is True
    assert not run.files.exists()  # not a sample: kept as evidence only


def test_zero_successful_calls_is_infra_and_retried_with_the_same_manifest(agentic_env) -> None:
    agentic_env.backend.replies = [ScriptedReply(transport_error=True)]
    agentic_env.script.call_gateway = True
    agentic_env.script.requests = [{"messages": [{"role": "user", "content": "go"}]}]
    agentic_env.script.files = CODE
    run = agentic_env.make_run()
    manifest_hash = RunManifest.objects.get(run=run).manifest_hash
    seed = run.seed
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert run.status == choices.RunStatus.PENDING
    assert run.attempt == 1  # executor counter: attempt 2 runs next
    assert run.outcome == ""
    first = attempts(run)[0]
    assert first.termination == "infra_interrupted"
    assert first.infra_error == "infra_interrupted"
    # attempt 2 succeeds on the same row, manifest and seed
    agentic_env.backend.replies = []
    run = agentic_env.claim(run)
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert run.phase == choices.RunPhase.EVAL_QUEUED
    assert [a.attempt for a in attempts(run)] == [1, 2]
    assert attempts(run)[1].termination == "completed"
    assert RunManifest.objects.get(run=run).manifest_hash == manifest_hash
    assert run.seed == seed


def test_infra_retries_exhausted_end_infra_error(agentic_env, monkeypatch) -> None:
    monkeypatch.setattr("backend.execution.transitions.infra_retries_for", lambda run_id: 0)
    agentic_env.script.llm_error = True
    agentic_env.script.files = CODE
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert (run.status, run.outcome, run.outcome_reason) == ("failed", "infra_error", "llm_error")
    assert run.usable is False
    assert attempts(run)[0].termination == "llm_error"


def test_harness_crash_is_retried_once_then_evaluated(agentic_env) -> None:
    agentic_env.script.exit_code = 139  # killed by a signal: a crash, not a harness error
    agentic_env.script.files = CODE
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert (run.status, run.attempt) == ("pending", 1)
    run = agentic_env.claim(run)
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert run.phase == choices.RunPhase.EVAL_QUEUED
    assert [a.termination for a in attempts(run)] == ["harness_crash", "harness_crash"]
    assert "harness_crash" in {n["code"] for n in run.validity["notes"]}


def test_harness_error_proceeds_to_evaluation(agentic_env) -> None:
    agentic_env.script.exit_code = 2
    agentic_env.script.files = CODE
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert run.phase == choices.RunPhase.EVAL_QUEUED
    assert attempts(run)[0].termination == "harness_error"


def test_image_digest_mismatch_is_infra_error_without_running(agentic_env) -> None:
    agentic_env.backend.mismatch_role = "harness"
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert (run.status, run.outcome, run.outcome_reason) == ("failed", "infra_error", "image_digest_mismatch")
    assert agentic_env.backend.plans == []
    assert not attempts(run)


def test_harness_not_enabled_is_excluded(agentic_env, monkeypatch) -> None:
    def unknown(name):
        raise KeyError(name)

    monkeypatch.setattr(runner, "get_adapter", unknown)
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert (run.status, run.outcome, run.outcome_reason) == ("completed", "excluded", "harness_not_enabled")
    assert run.usable is False


def test_cancelled_before_start(agentic_env) -> None:
    run = agentic_env.make_run()
    GenerationRun.objects.filter(pk=run.pk).update(status=choices.RunStatus.CANCELLED)
    runner.run_app_agentic(run, backend=agentic_env.backend)
    assert agentic_env.backend.plans == []


@pytest.mark.django_db(transaction=True)
def test_cancelled_during_the_sandbox_run(agentic_env) -> None:
    agentic_env.script.files = CODE
    agentic_env.script.sleep_s = 20
    run = agentic_env.make_run()
    heartbeat = agentic_env.backend.heartbeat_every_s

    def cancel_soon() -> None:
        GenerationRun.objects.filter(pk=run.pk).update(status=choices.RunStatus.CANCELLED)

    import threading

    threading.Timer(heartbeat * 2, cancel_soon).start()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert run.status == choices.RunStatus.CANCELLED
    assert attempts(run)[0].termination == "cancelled"


def test_workspace_too_large_is_output_invalid(agentic_env) -> None:
    policy = effective_policy()
    limits = policy.content.run.model_copy(update={"max_workspace_bytes": 4096})
    agentic_env.policy = policy.model_copy(update={"content": policy.content.model_copy(update={"run": limits})})
    agentic_env.script.files = {**CODE, "assets/big.txt": b"x" * 50_000}
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert (run.outcome, run.outcome_reason) == ("output_invalid", "workspace_too_large")
    assert not run.files.exists()


def test_evidence_and_artifacts_are_kept_per_attempt(agentic_env) -> None:
    from backend.generation.agentic import evidence

    agentic_env.script.files = CODE
    agentic_env.script.calls = [canned_call(1), canned_call(2)]
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    directory = evidence.attempt_dir(run.pk, 1)
    assert (directory / "calls.jsonl").exists()
    assert (directory / "workspace.tar").exists()
    assert (directory / "changes.patch").exists()
    assert (directory / "trajectory.json").exists()
    assert len(evidence.read_calls(run.pk, 1)) == 2
    assert run.artifacts.filter(attempt=1).count() == 2


def test_dispatch_routes_by_kind(agentic_env, monkeypatch) -> None:
    seen = []
    monkeypatch.setattr("backend.generation.slot.executors.single_shot.run_stage", lambda run: seen.append(run.kind))
    from backend.generation.tests.factories import GenerationRunFactory

    variant = GenerationRunFactory.create(slot_single_shot=True)
    dispatch.run_stage(variant)
    assert seen == ["slot_single_shot"]
    with pytest.raises(ValueError, match="unknown kind"):
        dispatch.run_stage(GenerationRun(kind="nope"))


def test_dependency_cache_settings_reach_the_agent_and_the_gateway(agentic_env, monkeypatch) -> None:
    from backend.generation.build.depcache import SandboxDepConfig

    config = SandboxDepConfig(
        env={"npm_config_registry": "http://depcache-npm:4873/"},
        files={"/etc/llmlab/pip.conf": b"[global]\n"},
        ephemeral_dirs=("/tmp/llmlab-deps",),  # noqa: S108 - an in-container path
        proxy_hosts=("depcache-npm:4873",),
        notes=("depcache_unavailable",),
    )
    monkeypatch.setattr("backend.generation.build.depcache.sandbox_config", lambda **kwargs: config)
    agentic_env.script.files = CODE
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    plan = agentic_env.backend.plans[0]
    assert plan.rendered.env["npm_config_registry"] == "http://depcache-npm:4873/"
    assert "/etc/llmlab/pip.conf" in plan.rendered.files
    assert plan.gateway_env["GATEWAY_PROXY_HOSTS"] == "depcache-npm:4873"
    assert "GATEWAY_PING_INTERVAL_S" in plan.gateway_env
    run.refresh_from_db()
    assert "depcache_unavailable" in {n["code"] for n in run.validity["notes"]}


def test_gateway_config_invalid_is_infra_error_not_retried(agentic_env, monkeypatch) -> None:
    from backend.generation.agentic.sandbox import GatewayConfigInvalid

    def refuse(plan, hooks):
        msg = "config_invalid: translate mode"
        raise GatewayConfigInvalid(msg)

    monkeypatch.setattr(agentic_env.backend, "run", refuse)
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert (run.status, run.outcome, run.outcome_reason) == ("failed", "infra_error", "gateway_config_invalid")
    assert run.attempt == 0


def test_native_system_prompt_is_stored(agentic_env) -> None:
    from backend.generation import blobs

    agentic_env.script.files = CODE
    agentic_env.script.calls = [canned_call(1, system_prompt="You are a coding agent.")]
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    observed = attempts(run)[0].observed_inputs
    assert blobs.get_text(observed["native_system_prompt_blob"]) == "You are a coding agent."


# -- review fixes: platform failures are never model outcomes (6.12, appendix A #5) ------------------------


@pytest.mark.parametrize("dropped", ["exit.json", "workspace.tar"])
def test_lost_workspace_is_infra_and_retried(agentic_env, dropped) -> None:
    """The backstop kill / a failed get_archive leaves no exit record or tar: infra, never output_invalid."""
    agentic_env.backend.drop_logs = dropped
    agentic_env.script.files = CODE
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert (run.status, run.attempt, run.outcome) == (choices.RunStatus.PENDING, 1, "")
    assert run.usable is None
    assert not run.files.exists()


def test_record_attempt_treats_a_missing_tar_as_infra(agentic_env, monkeypatch) -> None:
    """Defence in depth: a backend that returns no tar (and no truncation) still gives INFRA."""
    import dataclasses

    from backend.generation.contracts.outcome import Termination

    real_run = agentic_env.backend._run

    def without_tar(*args, **kwargs):
        result = real_run(*args, **kwargs)
        return dataclasses.replace(result, workspace_tar=None, workspace_truncated=False)

    monkeypatch.setattr(agentic_env.backend, "_run", without_tar)
    agentic_env.script.files = CODE
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert attempts(run)[0].termination == Termination.INFRA.value
    assert (run.status, run.outcome) == (choices.RunStatus.PENDING, "")


@pytest.mark.parametrize("status", [404, 503])
def test_only_upstream_http_errors_is_llm_error_not_model_data(agentic_env, monkeypatch, status) -> None:
    monkeypatch.setattr("backend.execution.transitions.infra_retries_for", lambda run_id: 0)
    agentic_env.script.calls = [canned_call(1, status=status), canned_call(2, status=status)]
    agentic_env.script.exit_code = 1  # would be HARNESS_ERROR -> evaluated
    agentic_env.script.files = {}
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    assert attempts(run)[0].termination == "llm_error"
    assert (run.status, run.outcome, run.outcome_reason) == ("failed", "infra_error", "llm_error")
    assert run.usable is False


def test_zero_calls_is_infra_not_model_data(agentic_env) -> None:
    agentic_env.script.calls = []
    agentic_env.script.exit_code = 127  # the harness binary never started, no call made
    agentic_env.script.files = {}
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    first = attempts(run)[0]
    assert (first.termination, first.infra_error) == ("infra_interrupted", "infra_interrupted")
    assert (run.status, run.outcome) == (choices.RunStatus.PENDING, "")


def test_zero_calls_completed_exit_is_infra(agentic_env) -> None:
    agentic_env.script.calls = []
    agentic_env.script.files = CODE
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    assert attempts(run)[0].termination == "infra_interrupted"
