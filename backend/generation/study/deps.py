"""Seams to the other areas the study engine consumes (spec section 12.3, WP-10 "Consumes").

The resolver and the launcher never import another work package's internals directly: they call the
functions bound in :class:`StudyDeps`. The default binding (:func:`default_deps`) imports the published
interfaces lazily, so a missing or not-yet-implemented area fails at call time with its own error
(``NotImplementedError`` from a skeleton), never at import time and never silently:

* prompts (WP-03): ``prompts.context.build_render_context(mode=..., **levels)`` then
  ``prompts.render_set(prompt_set_content, context)``;
* budget (WP-11): ``budget.policy.resolve_study_policies`` (every condition's policy with the study context,
  one call per study) / ``treatment_view``, ``budget.estimate.estimate_cell``
  / ``estimate_plan``, ``budget.stop_rules.pilot_gates``;
* slot (WP-12): ``slot.context.resolve_context`` and the target issues (``slot.validate.validation_issues`` /
  ``target_usable``, ``slot.leak.leak_issues(scan(...))``, ``single_shot.sampling_issues``);
* runs (WP-08): ``agentic.manifest.build_manifest``;
* execution (WP-11): ``execution.api.enqueue_runs`` / ``cancel_runs``;
* evaluation (WP-09): ``evaluation.baseline.queue_baseline`` (starter baselines, queued at launch);
* the tester conformance kit (WP-09): ``evaluation.conformance.coverage() -> {framework: bool}`` (None, i.e.
  "unknown", until it exists: every framework is then reported ``conformance_missing``);
* the cell environment (harness, gateway, sandbox and image digests): :mod:`study.environment`.

Tests swap the binding with :func:`override_deps` (see ``generation/testing/fake_study.py``).
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.budget.policy import StudyPolicies
    from backend.generation.contracts.plan import CellEstimate
    from backend.generation.contracts.plan import Estimate
    from backend.generation.contracts.plan import ResolvedCell
    from backend.generation.contracts.plan import ResolvedPlan
    from backend.generation.contracts.resources import EffectivePolicy
    from backend.generation.contracts.slot import ContextPolicy
    from backend.generation.contracts.slot import ResolvedContext
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunManifest
    from backend.generation.models import SlotTarget
    from backend.generation.models import Study
    from backend.generation.prompts.engine import Stimulus
    from backend.generation.study.environment import CellEnvironment
    from backend.generation.study.environment import EnvironmentRequest

__all__ = ["StudyDeps", "default_deps", "get_deps", "override_deps"]


@dataclass(frozen=True)
class StudyDeps:
    """The callables the study engine uses from other areas (one binding per process, swappable in tests)."""

    render_stimuli: Callable[[str, dict[str, Any], Any], list[Stimulus]]
    effective_policy: Callable[..., EffectivePolicy]
    treatment_view: Callable[[EffectivePolicy], dict[str, Any]]
    estimate_cell: Callable[[ResolvedCell], CellEstimate]
    estimate_plan: Callable[..., Estimate]  # (plan, *, study_kind=...)
    environment: Callable[[EnvironmentRequest], CellEnvironment]
    resolve_slot_context: Callable[..., ResolvedContext]
    slot_issues: Callable[..., list[Any]]
    build_manifest: Callable[[GenerationRun, ResolvedCell], RunManifest]
    pilot_gates: Callable[[Study], list[dict[str, Any]]]
    enqueue_runs: Callable[[list[Any]], int]
    cancel_runs: Callable[..., dict[str, int]]
    conformance_coverage: Callable[[], dict[str, bool] | None]
    # ``budget.policy.resolve_study_policies``. ``None`` (a binding that predates it, e.g. the WP-01
    # ``FakeStudyEnv``) makes the resolver call ``effective_policy`` once per condition instead, without the
    # cross-condition lint and the token-size rules.
    study_policies: Callable[..., StudyPolicies] | None = None
    # ``evaluation.baseline.queue_baseline(starter, task_version, auth_mode, suite)`` (WP-09), called at
    # launch; ``None`` skips the starter baselines (``baseline_pass_rate`` then stays ``None``).
    queue_baseline: Callable[..., Any] | None = None


def _render_stimuli(mode: str, levels: dict[str, Any], prompt_set_content: Any) -> list[Stimulus]:
    from backend.generation import prompts
    from backend.generation.prompts.context import build_render_context

    context = build_render_context(mode=mode, **levels)
    return prompts.render_set(prompt_set_content, context)


def _effective_policy(policy: Any, **kwargs: Any) -> EffectivePolicy:
    from backend.generation.budget.policy import effective_policy

    return effective_policy(policy, **kwargs)


def _study_policies(study: Any, conditions: Any, **kwargs: Any) -> StudyPolicies:
    from backend.generation.budget.policy import resolve_study_policies

    return resolve_study_policies(study, conditions, **kwargs)


def _queue_baseline(starter: Any, task_version: Any, auth_mode: str, suite: Any) -> Any:
    from backend.generation.evaluation.baseline import queue_baseline

    return queue_baseline(starter, task_version, auth_mode, suite)


def _treatment_view(policy: EffectivePolicy) -> dict[str, Any]:
    from backend.generation.budget.policy import treatment_view

    return treatment_view(policy)


def _estimate_cell(cell: ResolvedCell) -> CellEstimate:
    from backend.generation.budget.estimate import estimate_cell

    return estimate_cell(cell)


def _estimate_plan(plan: ResolvedPlan, *, study_kind: str = "exploratory") -> Estimate:
    from backend.generation.budget.estimate import estimate_plan

    return estimate_plan(plan, study_kind=study_kind)


def _environment(request: EnvironmentRequest) -> CellEnvironment:
    from backend.generation.study.environment import cell_environment

    return cell_environment(request)


def _resolve_slot_context(
    target: SlotTarget,
    policy: ContextPolicy,
    *,
    hole_mode: str | None = None,
    keep_docstring: bool | None = None,
) -> ResolvedContext:
    from backend.generation.slot.context import resolve_context

    return resolve_context(target, policy, hole_mode=hole_mode, keep_docstring=keep_docstring)


def _slot_issues(
    target: SlotTarget,
    *,
    context_policy: ContextPolicy,
    executor: str,
    hole_mode: str,
    sampling_content: dict[str, Any],
    study_kind: str,
) -> list[Any]:
    """Target validation, leak and slot-sampling issues of one (target, condition) (WP-12)."""
    from backend.generation.slot import leak
    from backend.generation.slot import validate
    from backend.generation.slot.executors.single_shot import sampling_issues

    issues = list(validate.validation_issues(target, study_kind))
    usable, reason = validate.target_usable(target)
    if not usable and not any(issue.severity == "error" for issue in issues):
        from backend.generation.contracts.issues import make_issue

        issues.append(
            make_issue(
                "slot_tests_uninformative",
                study_kind,  # type: ignore[arg-type]
                severity="error",
                message=f"{target.label}: not usable in a study ({reason})",
                data={"target": str(target.pk), "reason": reason},
            ),
        )
    report = leak.scan(target, context_policy=context_policy, executor=executor, hole_mode=hole_mode)
    issues += leak.leak_issues(report, study_kind)
    issues += sampling_issues(sampling_content, study_kind)
    return issues


def _build_manifest(run: GenerationRun, cell: ResolvedCell) -> RunManifest:
    from backend.generation.agentic.manifest import build_manifest

    return build_manifest(run, cell)


def _pilot_gates(study: Study) -> list[dict[str, Any]]:
    from backend.generation.budget.stop_rules import pilot_gates

    return pilot_gates(study)


def _enqueue_runs(ids: list[Any]) -> int:
    from backend.execution.api import enqueue_runs

    return enqueue_runs(ids)


def _cancel_runs(qs: Any, scope: str = "all", *, study: Any = None) -> dict[str, int]:
    from backend.execution.api import cancel_runs

    return cancel_runs(qs, scope=scope, study=study)


def _conformance_coverage() -> dict[str, bool] | None:
    """``{backend framework: covered}`` from the tester conformance kit (WP-09), or None when unavailable."""
    try:
        from backend.generation.evaluation import conformance

        coverage = conformance.coverage  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        return None
    try:
        return dict(coverage())
    except NotImplementedError:
        return None


def default_deps() -> StudyDeps:
    """The production binding (every callable imports its area on first use)."""
    return StudyDeps(
        render_stimuli=_render_stimuli,
        effective_policy=_effective_policy,
        treatment_view=_treatment_view,
        estimate_cell=_estimate_cell,
        estimate_plan=_estimate_plan,
        environment=_environment,
        resolve_slot_context=_resolve_slot_context,
        slot_issues=_slot_issues,
        build_manifest=_build_manifest,
        pilot_gates=_pilot_gates,
        enqueue_runs=_enqueue_runs,
        cancel_runs=_cancel_runs,
        conformance_coverage=_conformance_coverage,
        study_policies=_study_policies,
        queue_baseline=_queue_baseline,
    )


_CURRENT: list[StudyDeps] = [default_deps()]


def get_deps() -> StudyDeps:
    """The binding in effect (the innermost :func:`override_deps`, else the default)."""
    return _CURRENT[-1]


@contextlib.contextmanager
def override_deps(deps: StudyDeps | None = None, **replace: Any) -> Iterator[StudyDeps]:
    """Use ``deps`` (or the current binding with ``replace`` applied) inside the ``with`` block."""
    base = deps if deps is not None else get_deps()
    bound = dataclasses.replace(base, **replace) if replace else base
    _CURRENT.append(bound)
    try:
        yield bound
    finally:
        _CURRENT.pop()
