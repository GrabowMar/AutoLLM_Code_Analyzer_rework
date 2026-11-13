"""Wave-3 follow-ups of the study engine (WP-10): frozen local serving settings, study-level policy
resolution (WP-11), starter baselines and retests (WP-09), ``not_tested:<level>`` and ``common_pass_rate``."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import Study
from backend.generation.models import StudyPostRun
from backend.generation.models.choices import Outcome
from backend.generation.study import automation
from backend.generation.study import launch as svc
from backend.generation.study import plan as planner
from backend.generation.study import results as results_svc
from backend.generation.study.deps import default_deps
from backend.generation.study.deps import get_deps
from backend.generation.study.deps import override_deps
from backend.generation.study.design import parse_design
from backend.generation.testing.factories import EvalSuiteFactory
from backend.inference.models import LLMEndpoint

from .conftest import MODELS
from .test_launch import finish
from .test_results import _complete
from .test_results import _runs
from .test_results import _two_models

pytestmark = pytest.mark.django_db

BASE, OTHER = MODELS


# ---- ManifestEndpoint: local serving settings frozen at plan time ------------------------------------------------


def _local_model(user, **endpoint_fields: Any):
    from backend.llm_models.models import LLMModel

    endpoint = LLMEndpoint.objects.create(
        slug="gpu-wp10",
        name="GPU box",
        kind="ollama",
        connection="relay",
        api_flavor="native",
        max_concurrency=2,
        owner=user,
        **endpoint_fields,
    )
    return LLMModel.objects.create(
        model_id="local-gpu-wp10/qwen2.5-coder-7b",
        canonical_slug="local-gpu-wp10_qwen2.5-coder-7b",
        provider=endpoint.slug,
        model_name="qwen2.5-coder:7b",
        endpoint=endpoint,
        served_model_name="qwen2.5-coder:7b",
        digest="sha256:" + "ab" * 32,
        context_window=32768,
        supports_function_calling=True,
        metadata={"ollama": {"capabilities": ["completion", "tools"]}},
    )


def _resolver(study: Study) -> planner._Resolver:
    return planner._Resolver(study, parse_design(study.design), drop_incompatible=False)


def test_manifest_endpoint_freezes_the_local_serving_settings(make_study, fake_study, user):
    from backend.inference.snapshot import effective_num_ctx
    from backend.llm_models.models import LLMModel

    local = _local_model(user, keep_alive="45m")
    endpoint = _resolver(make_study())._manifest_model(local).endpoint
    assert endpoint.kind == "ollama"
    assert endpoint.num_ctx == effective_num_ctx(local)
    assert endpoint.keep_alive == "45m"
    assert endpoint.api_flavor == "native"
    assert endpoint.capabilities == ["completion", "tools"]

    remote = LLMModel.objects.get(model_id=BASE)
    frozen = _resolver(make_study(name="Remote"))._manifest_model(remote).endpoint
    assert (frozen.num_ctx, frozen.keep_alive, frozen.api_flavor, frozen.capabilities) == (None, None, None, None)


def test_manifest_endpoint_keep_alive_blank_is_none(make_study, fake_study, user):
    local = _local_model(user, keep_alive="")
    endpoint = _resolver(make_study())._manifest_model(local).endpoint
    assert endpoint.keep_alive is None
    assert endpoint.num_ctx is not None


# ---- budget.policy.resolve_study_policies (WP-11) ---------------------------------------------------------------


def _recording_study_policies(calls: list[dict[str, Any]]):
    real = default_deps().study_policies
    assert real is not None

    def record(study, conditions, **kwargs):
        calls.append({"conditions": [c.index for c in conditions], "inputs": list(conditions), **kwargs})
        return real(study, conditions, **kwargs)

    return record


def test_policies_are_resolved_once_per_study_with_every_condition(make_study, fake_study):
    study = make_study()
    calls: list[dict[str, Any]] = []
    with override_deps(
        study_policies=_recording_study_policies(calls),
        effective_policy=default_deps().effective_policy,
        treatment_view=default_deps().treatment_view,
    ):
        bundle = planner.resolve_bundle(study, use_cache=False)
    first = calls[0]
    assert first["conditions"] == [c.index for c in bundle.plan.conditions]
    assert {c.executor for c in first["inputs"]} == {"agentic"}
    assert {c.stack for c in first["inputs"]} == {"flask-react", "fastapi-vue"}
    assert "brief_tokens" not in first  # the policy pass has no token sizes
    # the token pass: grouped by brief size, every condition covered, briefs measured
    token_calls = calls[1:]
    assert token_calls
    assert sorted(i for call in token_calls for i in call["conditions"]) == first["conditions"]
    assert all(call["brief_tokens"] and call["brief_tokens"] > 0 for call in token_calls)
    # the cells carry the study-level effective policy
    cell = bundle.plan.cells[0]
    assert cell.effective_policy["serving_tier"] == "standard"


def test_token_lint_reports_a_brief_over_the_call_cap(make_study, fake_study):
    from backend.generation.contracts.issues import make_issue

    study = make_study()
    real = default_deps().study_policies

    def tight(study_, conditions, **kwargs):
        resolved = real(study_, conditions, **kwargs)
        if kwargs.get("brief_tokens"):
            issue = make_issue("brief_exceeds_call_cap", "exploratory", message="brief too large")
            for index, policy in list(resolved.by_condition.items()):
                resolved.by_condition[index] = policy.model_copy(update={"issues": [issue]})
        return resolved

    with override_deps(
        study_policies=tight,
        effective_policy=default_deps().effective_policy,
        treatment_view=default_deps().treatment_view,
    ):
        plan = planner.resolve_bundle(study, use_cache=False).plan
    issue = next(i for i in plan.issues if i.code == "brief_exceeds_call_cap")
    assert sorted(issue.cells) == sorted(f"c{c.index}" for c in plan.conditions)


def test_a_binding_without_study_policies_falls_back_to_per_condition_policies(make_study, fake_study):
    assert get_deps().study_policies is None  # the WP-01 FakeStudyEnv predates the seam
    plan = planner.resolve_bundle(make_study(), use_cache=False).plan
    assert plan.cells
    assert all(cell.effective_policy for cell in plan.cells)


# ---- launch: starter baselines and the retest post-run (WP-09) --------------------------------------------------


def test_launch_queues_one_baseline_per_starter_task_auth_mode(make_study, fake_study):
    queued: list[tuple[str, str, str, str]] = []

    def queue(starter, task_version, auth_mode, suite):
        queued.append((starter.slug, task_version.ref, auth_mode, suite.slug))
        return object()

    study = make_study(evaluation={"eval_suite": "eval-v2@1", "overrides": {"baseline": True}})
    with override_deps(queue_baseline=queue):
        svc.launch(study, planner.resolve(study).plan_hash)
    # 2 stacks x 2 tasks x 1 auth mode, from every cell of the plan (pilot and main), once each
    assert len(queued) == len(set(queued)) == 4
    assert {q[0] for q in queued} == {"flask-react-minimal", "fastapi-vue-minimal"}
    assert {q[2] for q in queued} == {"contract_bearer"}
    assert {q[3] for q in queued} == {"eval-v2"}


def test_launch_queues_no_baseline_when_the_stage_is_off(make_study, fake_study):
    queued: list[Any] = []
    study = make_study(evaluation={"eval_suite": "eval-v2@1", "overrides": {"baseline": False}})
    with override_deps(queue_baseline=lambda *a: queued.append(a)):
        svc.launch(study, planner.resolve(study).plan_hash)
    assert queued == []


def test_a_failing_baseline_never_blocks_the_launch(make_study, fake_study):
    def boom(*_: Any) -> None:
        msg = "no starter tree"
        raise RuntimeError(msg)

    study = make_study(evaluation={"eval_suite": "eval-v2@1", "overrides": {"baseline": True}})
    with override_deps(queue_baseline=boom):
        result = svc.launch(study, planner.resolve(study).plan_hash)
    assert result["created_runs"] == GenerationRun.objects.filter(study=study).count() > 0


def test_launch_attaches_a_retest_post_run_that_runs_on_completion(make_study, fake_study, monkeypatch):
    study = make_study()
    svc.launch(study, planner.resolve(study).plan_hash)
    post_run = StudyPostRun.objects.get(study=study, config__retest=True)
    assert (post_run.kind, post_run.trigger) == ("analysis", "completed")
    assert post_run.config == {"tool_slugs": [], "only_passing": False, "report": False, "retest": True}

    called: list[Any] = []
    monkeypatch.setattr(
        "backend.generation.evaluation.retest.run_retests",
        lambda s: called.append(s.pk) or [11, 12],
    )
    automation.run_post_run(post_run)
    post_run.refresh_from_db()
    assert called == [study.pk]
    assert post_run.status == "completed"
    assert post_run.progress["retests"] == 2


def test_no_retest_post_run_without_the_tester_or_with_fraction_zero(make_study, fake_study):
    for name, evaluation in (
        ("Build", {"eval_suite": "eval-v2@1", "eval_level": "build"}),
        ("Zero", {"eval_suite": "eval-v2@1", "retest_fraction": 0}),
    ):
        study = make_study(name=name, evaluation=evaluation)
        svc.launch(study, planner.resolve(study).plan_hash)
        assert not StudyPostRun.objects.filter(study=study, config__retest=True).exists()


def test_post_run_config_keeps_the_retest_flag():
    assert automation.normalize_post_run_config("analysis", {"retest": True})["retest"] is True
    assert "retest" not in automation.normalize_post_run_config("analysis", {})


# ---- results: not_tested:<level> and common_pass_rate ------------------------------------------------------------


def test_not_tested_level_is_not_measured_never_zero(make_study, fake_study):
    study = _two_models(make_study)
    for run in _runs(study, BASE):
        finish(run, Outcome.TESTED, outcome_reason="not_tested:build")
    for run in _runs(study, OTHER):
        finish(run, Outcome.TESTED, pass_rate=1.0)
    _complete(study)
    cells, _ = results_svc._load(study, "pass_rate")
    base_ids = {str(r.pk) for r in _runs(study, BASE)}
    base_cells = [c for c in cells if c.run_id in base_ids]
    assert base_cells
    assert all(c.code == "u" for c in base_cells)
    assert all(c.value is None for c in base_cells)


def _checks(*pairs: tuple[str, str], category: str = "functional") -> list[dict[str, Any]]:
    return [{"id": cid, "status": status, "category": category} for cid, status in pairs]


def _measured(run: GenerationRun, suite, checks: list[dict[str, Any]], **result: Any) -> None:
    from backend.generation.testing.content import eval_result_content

    content = eval_result_content()
    content["checks"] = [{**check, "ms": 1} for check in checks]
    content["metrics"]["agent_pass_rate"] = 0.5
    content.update(result)
    RunEvaluation.objects.create(run=run, eval_suite=suite, eval_level="security", status="measured", result=content)


def test_common_pass_rate_uses_the_checks_every_auth_mode_reports(make_study, fake_study):
    study = _two_models(make_study, n_tasks=1)
    suite = EvalSuiteFactory.create()
    base, other = _runs(study, BASE)[0], _runs(study, OTHER)[0]
    GenerationRun.objects.filter(pk=other.pk).update(auth_mode="session_cookie")
    infra = _checks(("health", "fail"), category="infra")
    _measured(base, suite, [*_checks(("a.ok", "pass"), ("b.ok", "fail"), ("a.anon_401", "pass")), *infra])
    _measured(
        other,
        suite,
        _checks(("a.ok", "pass"), ("b.ok", "pass"), ("c.flaky", "fail"), ("a.csrf", "fail")),
        flaky_checks=["c.flaky"],
    )
    for run in (base, other):
        finish(GenerationRun.objects.get(pk=run.pk), Outcome.TESTED)
    _complete(study)
    cells, _ = results_svc._load(study, "common_pass_rate")
    by_run = {c.run_id: c for c in cells}
    # common to both auth modes: a.ok, b.ok (infra, flaky and auth-mode-only checks left out)
    assert by_run[str(base.pk)].value == pytest.approx(0.5)
    assert by_run[str(other.pk)].value == pytest.approx(1.0)
    assert by_run[str(base.pk)].eval_metrics["agent_pass_rate"] == 0.5  # the evaluation's own metrics kept


def test_common_pass_rate_helper_is_none_without_a_common_check():
    evaluations = {
        1: {"status": "measured", "result": {"measured": True, "checks": _checks(("x", "pass"))}},
        2: {"status": "measured", "result": {"measured": True, "checks": _checks(("y", "pass"))}},
        3: {"status": "not_measured", "result": {"measured": False, "checks": _checks(("x", "pass"))}},
    }
    runs = {1: {"auth_mode": "contract_bearer"}, 2: {"auth_mode": "session_cookie"}, 3: {"auth_mode": ""}}
    rates = results_svc._common_pass_rates(evaluations, runs, {1: "u", 2: "u", 3: "u"})
    assert rates == {1: None, 2: None}


def test_study_deps_default_binding_has_the_new_seams():
    deps = default_deps()
    assert deps.study_policies is not None
    assert deps.queue_baseline is not None
    assert {f.name for f in dataclasses.fields(deps)} >= {"study_policies", "queue_baseline"}


# ---- seed_forwarded follows the gateway front (lead decision, wave 3) --------------------------------------------


@pytest.mark.parametrize(
    ("front", "mode", "forwarded"),
    [
        ("openai_chat", "native", True),
        ("anthropic_messages", "translate", True),
        ("openai_responses", "translate", True),
        ("anthropic_messages", "native", False),
        ("openai_responses", "passthrough", False),
    ],
)
def test_seed_forwarded_needs_a_front_that_carries_the_seed(make_study, fake_study, front, mode, forwarded):
    real = get_deps().environment

    def environment(request):
        env = real(request)
        if env.gateway is None:
            return env
        gateway = env.gateway.model_copy(update={"front_protocol": front, "protocol_mode": mode})
        return dataclasses.replace(env, gateway=gateway)

    with override_deps(environment=environment):
        plan = planner.resolve_bundle(make_study(), use_cache=False).plan
    assert {cell.manifest.sampling.seed_forwarded for cell in plan.cells} == {forwarded}
