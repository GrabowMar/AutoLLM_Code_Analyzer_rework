"""Expand, preview, launch, and report on :class:`Experiment` runs.

An experiment's matrix is ``conditions x app_requirements x repeats``. Each
cell becomes one :class:`GenerationJob` in the experiment's mode -- ``agent``
(the default: a coding-agent harness builds the app in a sandbox) or
``scaffolding`` (template prompts) -- reusing the same snapshot/dispatch
machinery as ad-hoc batches (``api/views/custom.py``) so nothing downstream
(analysis, runtime, reports) needs to know an experiment was involved.

Experiment-wide, identical for every condition: the stack, the sampling
preset (params + sampling policy), the budget preset (limits + network
policy), ``llm_defaults`` and ``agent_config``. Per condition: the model and
the harness preset (agent) or profile (scaffolding), plus two declared
ablation knobs that comparability always surfaces -- ``param_overrides`` and,
in agent mode, an optional profile (only its stack, which must match the
experiment's, and its ``llm_config`` apply; its prompt blocks do not, the
brief is rendered from the app spec).

:func:`experiment_layers` is the one place these layers are combined;
comparability, the cost estimate, launch and export all read it, and the
agent side is resolved with the snapshot's own resolver
(``agent/snapshot.resolve_agent_run``), so what the preview reports is what
the jobs freeze. Preset values are copied into each job's own fields at
launch; the job's preset FKs are provenance.

Run order is a randomised complete block design: all cells of repeat 0 run
before any cell of repeat 1, and within a repeat the cells are shuffled with
a seed derived from the experiment. Without this, a condition-major order
would confound conditions with time (provider load and routing, alias
repointing, rate limits all drift over hours). The FIFO run queue keeps
dispatch order = execution order.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings

from backend.generation.models import Experiment
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.services.agent.config import merge_agent_config
from backend.generation.services.agent.harnesses import HARNESSES
from backend.generation.services.agent.harnesses import default_harness_name
from backend.generation.services.agent.request import SEED_PLACEHOLDER
from backend.generation.services.agent.snapshot import agent_llm_layers
from backend.generation.services.agent.snapshot import apply_agent_snapshot_to_job
from backend.generation.services.agent.snapshot import resolve_agent_run
from backend.generation.services.agent.snapshot import resolve_llm
from backend.generation.services.batches import refresh_batch
from backend.generation.services.dispatcher import dispatch_job
from backend.generation.services.llm_params import merge_llm_params
from backend.generation.services.openrouter_client import OpenRouterClient
from backend.generation.services.presets import builtin_default_config
from backend.generation.services.presets import preset_config_for_use
from backend.generation.services.presets import provenance
from backend.generation.services.presets import system_default_preset
from backend.generation.services.presets import visible_q
from backend.generation.services.profile_resolver import apply_snapshot_to_job
from backend.generation.services.profile_resolver import derive_experiment_seed
from backend.generation.services.sampling import MODE_DEFAULT_MAX_TOKENS
from backend.generation.services.sampling import MODE_DEFAULT_TEMPERATURE
from backend.generation.services.versioning import canonical_json
from backend.runtime.services.scaffolding import canonical_stack_slug
from backend.runtime.services.scaffolding import get_stack_row
from backend.runtime.services.scaffolding import is_known_stack_slug
from backend.runtime.services.stack_validation import stack_usable_by

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from backend.generation.models import AppRequirementTemplate
    from backend.generation.models import ExperimentCondition
    from backend.generation.models import GenerationProfile
    from backend.generation.models import RunPreset

logger = logging.getLogger(__name__)

# Rough heuristic for a pre-generation cost estimate: chars/token ratio for
# prompt sizing, and a typical completion length assumption. Neither is
# billing-accurate — this is a "does this look reasonable" preview, not
# an invoice.
_CHARS_PER_TOKEN_ESTIMATE = 4
_TYPICAL_COMPLETION_TOKENS = 2500
_STAGES_PER_JOB = 2  # backend + frontend

# Comparability columns a condition can differ on. ``forced_params`` leaves
# out the seed (per run), the output cap (own column) and routing (own column).
DIFFERS_KEYS = (
    "harness",
    "harness_options",
    "sampling_policy",
    "forced_params",
    "output_cap",
    "provider_routing",
    "seed_forwarded",
    "stack_slug",
    "profile",
    "param_overrides",
)
_FORCED_EXCLUDED = ("seed", "provider", "max_tokens")


class ExperimentError(Exception):
    """An experiment request the API refuses (``status`` is the HTTP status)."""

    def __init__(self, status: int, detail: str, code: str = "", errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code
        self.errors = errors or []

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"detail": self.detail, "code": self.code}
        if self.errors:
            body["errors"] = self.errors
        return body


@dataclass(frozen=True)
class ConditionIssue:
    """A problem with one condition; blocking ones stop a launch."""

    code: str
    detail: str
    blocking: bool = True


@dataclass(frozen=True)
class MatrixCell:
    condition: ExperimentCondition
    app_requirement: AppRequirementTemplate
    repeat_index: int


def _cells(
    conditions: list[ExperimentCondition],
    apps: list[AppRequirementTemplate],
    repeats: int,
) -> list[MatrixCell]:
    return [
        MatrixCell(condition=condition, app_requirement=app, repeat_index=repeat)
        for condition in conditions
        for app in apps
        for repeat in range(repeats)
    ]


def _conditions(experiment: Experiment) -> list[ExperimentCondition]:
    return list(experiment.conditions.select_related("profile", "model", "harness_preset"))


def expand_matrix(experiment: Experiment) -> list[MatrixCell]:
    """Every (condition, app, repeat) cell the experiment's config implies."""
    return _cells(_conditions(experiment), list(experiment.app_requirements.all()), experiment.repeats)


# ── validation helpers (API + layers) ─────────────────────────────────


def resolve_stack(raw: str, user: AbstractUser | None) -> str:
    """Canonical slug of stack *raw* if *user* may run it (``ExperimentError`` 404 unknown / 403 unusable)."""
    if not raw or not is_known_stack_slug(raw):
        raise ExperimentError(404, f"Unknown stack slug: {raw}", "unknown_stack")
    slug = canonical_stack_slug(raw)
    row = get_stack_row(slug)
    if row is not None and not stack_usable_by(row, user):
        raise ExperimentError(403, f"Stack {row.slug!r} is not approved for use yet.", "stack_not_usable")
    return slug


def stack_mismatch(stack_slug: str, profile: GenerationProfile) -> str | None:
    """Why *profile* cannot run in an experiment on *stack_slug* (None if it can).

    A profile's prompts and ``llm_config`` are written for its own stack; one
    experiment runs every condition on the same stack, so the brief (and
    ``prompt_hash``) stays identical across conditions.
    """
    raw = profile.scaffolding_slug or ""
    if is_known_stack_slug(raw) and canonical_stack_slug(raw) == stack_slug:
        return None
    return (
        f"Profile {profile.slug} v{profile.version} targets stack {raw!r}, but this experiment runs on "
        f"{stack_slug!r}; pick a profile for that stack (or none, in agent mode)"
    )


def experiment_preset(user: AbstractUser, preset_id: int | None, kind: str) -> RunPreset | None:
    """The visible preset *preset_id* for an experiment's *kind* slot (404 unknown, 400 wrong kind / invalid)."""
    if preset_id is None:
        return None
    from backend.generation.models import RunPreset

    preset = RunPreset.objects.filter(visible_q(user), id=preset_id).first()
    if preset is None:
        raise ExperimentError(404, f"Unknown preset id {preset_id}", "unknown_preset")
    try:
        preset_config_for_use(preset, kind)
    except ValueError as exc:
        raise ExperimentError(400, str(exc), "invalid_preset") from exc
    return preset


def check_design(experiment: Experiment) -> None:
    """Refuse (``ExperimentError`` 400) an experiment whose settings contradict its conditions.

    Called with the experiment's final (unsaved) state on create and update.
    """
    is_agent = experiment.mode == GenerationJob.Mode.AGENT
    if not is_agent and experiment.budget_preset_id is not None:
        raise ExperimentError(400, "A budget preset only applies to agent-mode experiments.", "budget_not_applicable")
    if experiment._state.adding:  # noqa: SLF001 - no conditions yet
        return
    conditions = _conditions(experiment)
    if not is_agent:
        agent_only = [c for c in conditions if c.profile_id is None or c.harness or c.harness_preset_id is not None]
        if agent_only:
            raise ExperimentError(
                400,
                "Scaffolding experiments need a profile and no harness on every condition; "
                f"remove {', '.join(condition_label(c) for c in agent_only)} first.",
                "agent_conditions",
            )
    if experiment.stack_slug:
        for condition in conditions:
            if condition.profile is not None and (problem := stack_mismatch(experiment.stack_slug, condition.profile)):
                raise ExperimentError(400, problem, "stack_mismatch")


def condition_label(condition: ExperimentCondition) -> str:
    return condition.label or f"{condition.model.model_name} / {condition.method_label()}"


def _preset_ref(preset: RunPreset | None) -> dict[str, Any] | None:
    if preset is None:
        return None
    return {"id": preset.id, "slug": preset.slug, "version": preset.version, "name": preset.name}


def _profile_ref(profile: GenerationProfile | None) -> str | None:
    return f"{profile.slug}@{profile.version}" if profile is not None else None


# ── layers ────────────────────────────────────────────────────────────


@dataclass
class _ExperimentBase:
    """The experiment-wide layers, resolved once and shared by every condition."""

    stack_slug: str | None
    stack_issue: ConditionIssue | None
    sampling_preset: RunPreset | None
    budget_preset: RunPreset | None
    sampling_llm_params: dict[str, Any]
    # budget preset -> sampling preset policy -> experiment.agent_config (minus harness)
    agent_config: dict[str, Any]
    issues: list[ConditionIssue]
    harness_defaults: dict[str, RunPreset | None] = field(default_factory=dict)

    def default_harness_preset(self, name: str) -> RunPreset | None:
        if name not in self.harness_defaults:
            self.harness_defaults[name] = system_default_preset("harness", name)
        return self.harness_defaults[name]


def _applied_preset(
    preset: RunPreset | None,
    kind: str,
    issues: list[ConditionIssue],
    *,
    implicit: bool,
) -> tuple[RunPreset | None, dict[str, Any]]:
    """The preset of *kind* an experiment applies (its own, else the system default if *implicit*) and its config."""
    if preset is None:
        if not implicit:
            return None, {}
        preset = system_default_preset(kind)
        if preset is None:
            return None, builtin_default_config(kind)
    try:
        return preset, preset_config_for_use(preset, kind)
    except ValueError as exc:
        issues.append(ConditionIssue("invalid_preset", str(exc)))
        return preset, builtin_default_config(kind)


def _experiment_base(experiment: Experiment) -> _ExperimentBase:
    is_agent = experiment.mode == GenerationJob.Mode.AGENT
    issues: list[ConditionIssue] = []
    stack_slug, stack_issue = None, None
    if experiment.stack_slug:
        try:
            stack_slug = resolve_stack(experiment.stack_slug, experiment.created_by)
        except ExperimentError as exc:
            stack_slug, stack_issue = experiment.stack_slug, ConditionIssue(exc.code, exc.detail)
    # Template modes have no implicit sampling preset (services/sampling.py),
    # and take only its llm_params.
    sampling_preset, sampling_config = _applied_preset(
        experiment.sampling_preset,
        "sampling",
        issues,
        implicit=is_agent,
    )
    budget_preset, agent_config = None, {}
    if is_agent:
        budget_preset, budget_config = _applied_preset(experiment.budget_preset, "budget", issues, implicit=True)
        policy = {k: sampling_config[k] for k in ("sampling_policy",) if k in sampling_config}
        overrides = {k: v for k, v in (experiment.agent_config or {}).items() if k != "harness"}
        agent_config = {**budget_config, **policy, **overrides}
    return _ExperimentBase(
        stack_slug=stack_slug,
        stack_issue=stack_issue,
        sampling_preset=sampling_preset,
        budget_preset=budget_preset,
        sampling_llm_params=dict(sampling_config.get("llm_params") or {}),
        agent_config=agent_config,
        issues=issues,
    )


@dataclass
class ExperimentLayers:
    """What one condition runs with: the job fields launch writes and their provenance."""

    mode: str
    stack_slug: str
    stack_source: str  # "experiment" | "profile" | "default"
    profile: GenerationProfile | None
    harness_name: str  # "" in scaffolding mode
    harness_preset: RunPreset | None
    harness_options: dict[str, Any]
    sampling_preset: RunPreset | None
    budget_preset: RunPreset | None
    # job.llm_params: sampling preset -> llm_defaults -> param_overrides.
    llm_params: dict[str, Any]
    # job.agent_config (agent mode): the experiment's layer plus the harness.
    agent_config: dict[str, Any]
    issues: list[ConditionIssue]

    @property
    def is_agent(self) -> bool:
        return self.mode == GenerationJob.Mode.AGENT

    @property
    def max_tokens(self) -> int:
        """``job.max_tokens``: the lowest llm layer (the profile's ``llm_config`` sits above it)."""
        return int(self.llm_params.get("max_tokens") or MODE_DEFAULT_MAX_TOKENS)

    @property
    def temperature(self) -> float:
        """Legacy ``job.temperature`` column: the template modes' base layer (agent runs never send it)."""
        return float(self.llm_params.get("temperature", MODE_DEFAULT_TEMPERATURE))

    @property
    def effective_agent_config(self) -> dict[str, Any]:
        return merge_agent_config(self.agent_config)

    def llm_layers(self) -> tuple[dict[str, Any] | None, ...]:
        """The agent snapshot's llm layers: max_tokens -> profile llm_config -> job.llm_params."""
        return agent_llm_layers(self.max_tokens, self.profile, self.llm_params)


def _condition_stack(
    experiment: Experiment,
    profile: GenerationProfile | None,
    base: _ExperimentBase,
    issues: list[ConditionIssue],
) -> tuple[str, str]:
    """(canonical stack, source): the experiment's, else the profile's (legacy), else the platform default."""
    if base.stack_slug is not None:
        if base.stack_issue is not None:
            issues.append(base.stack_issue)
        elif profile is not None and (problem := stack_mismatch(base.stack_slug, profile)):
            issues.append(ConditionIssue("stack_mismatch", problem))
        return base.stack_slug, "experiment"
    raw, source = (profile.scaffolding_slug, "profile") if profile is not None else ("", "default")
    raw = raw or settings.GENERATION_DEFAULT_STACK
    try:
        return resolve_stack(raw, experiment.created_by), source
    except ExperimentError as exc:
        issues.append(ConditionIssue(exc.code, exc.detail))
        return raw, source


def _condition_harness(
    experiment: Experiment,
    condition: ExperimentCondition,
    base: _ExperimentBase,
    issues: list[ConditionIssue],
) -> tuple[str, RunPreset | None, dict[str, Any]]:
    """(harness name, harness preset, its non-default options) of an agent condition.

    Without a preset the condition's harness (else the experiment's
    ``agent_config.harness``, else the platform default) runs with that
    harness's system default preset, as ad-hoc runs naming a harness do.
    """
    preset = condition.harness_preset
    if preset is not None:
        try:
            config = preset_config_for_use(preset, "harness")
        except ValueError as exc:
            issues.append(ConditionIssue("invalid_preset", str(exc)))
            return condition.harness or default_harness_name(), preset, {}
        if condition.harness and condition.harness != config["harness"]:
            issues.append(
                ConditionIssue(
                    "harness_preset_mismatch",
                    f"Condition harness {condition.harness!r} does not match its preset {preset.slug} "
                    f"({config['harness']!r})",
                ),
            )
        return config["harness"], preset, dict(config["options"])
    name = condition.harness or (experiment.agent_config or {}).get("harness") or default_harness_name()
    if name not in HARNESSES:
        issues.append(ConditionIssue("unknown_harness", f"Unknown harness {name!r}"))
        return name, None, {}
    preset = base.default_harness_preset(name)
    options: dict[str, Any] = {}
    if preset is not None:
        try:
            options = dict(preset_config_for_use(preset, "harness")["options"])
        except ValueError as exc:
            issues.append(ConditionIssue("invalid_preset", str(exc)))
    return name, preset, options


def experiment_layers(
    experiment: Experiment,
    condition: ExperimentCondition,
    *,
    base: _ExperimentBase | None = None,
) -> ExperimentLayers:
    """The layers of *condition*, exactly as launch applies them (later wins).

    * ``llm_params`` (``job.llm_params``) = sampling preset ``llm_params`` ->
      ``experiment.llm_defaults`` -> ``condition.param_overrides``. Snapshots
      put the ``max_tokens`` column and the profile's ``llm_config`` below
      it, so the effective chain is max_tokens -> profile ``llm_config`` ->
      sampling preset -> ``llm_defaults`` -> ``param_overrides`` (scaffolding:
      the mode default temperature/max_tokens instead of max_tokens alone).
    * ``agent_config`` (``job.agent_config``, agent mode; merged over the
      platform defaults) = budget preset -> sampling preset
      ``sampling_policy`` -> ``experiment.agent_config``, plus the harness.
    * ``harness_options``: the condition's harness preset.
    * stack: ``experiment.stack_slug``, else the profile's, else the platform default.

    Agent mode applies the system sampling / budget / harness default
    presets when none is set; scaffolding has no implicit sampling preset.
    Problems never raise; they are collected in ``issues``.
    """
    base = base if base is not None else _experiment_base(experiment)
    issues = list(base.issues)
    is_agent = experiment.mode == GenerationJob.Mode.AGENT
    profile = condition.profile
    if not is_agent and profile is None:
        issues.append(ConditionIssue("profile_required", "Scaffolding conditions need a profile"))
    stack_slug, stack_source = _condition_stack(experiment, profile, base, issues)
    layers = ExperimentLayers(
        mode=experiment.mode,
        stack_slug=stack_slug,
        stack_source=stack_source,
        profile=profile,
        harness_name="",
        harness_preset=None,
        harness_options={},
        sampling_preset=base.sampling_preset,
        budget_preset=base.budget_preset,
        llm_params=merge_llm_params(base.sampling_llm_params, experiment.llm_defaults, condition.param_overrides),
        agent_config={},
        issues=issues,
    )
    if is_agent:
        name, preset, options = _condition_harness(experiment, condition, base, issues)
        layers.harness_name, layers.harness_preset, layers.harness_options = name, preset, options
        layers.agent_config = {**base.agent_config, "harness": name}
    return layers


# ── condition reports (comparability + launch gating) ─────────────────


@dataclass
class ConditionPlan:
    condition: ExperimentCondition
    layers: ExperimentLayers
    issues: list[ConditionIssue]
    row: dict[str, Any] | None = None  # comparability row (agent mode)


def _agent_report(
    condition: ExperimentCondition,
    layers: ExperimentLayers,
) -> tuple[dict[str, Any], list[ConditionIssue]]:
    """Comparability row and issues of an agent condition, resolved as its snapshot will be."""
    model = condition.model
    issues = list(layers.issues)
    agent = layers.effective_agent_config
    run = resolve_llm(
        model=model,
        llm_layers=layers.llm_layers(),
        seed=SEED_PLACEHOLDER,
        sampling_policy=agent["sampling_policy"],
    )
    gateway = run["gateway"]
    harness = HARNESSES.get(layers.harness_name)
    if harness is not None and harness.requires_tool_calling and not model.supports_function_calling:
        issues.append(
            ConditionIssue("models_without_tools", f"{harness.label} needs tool calling; {model.model_id} has none"),
        )
    elif harness is not None and not any(i.blocking for i in issues):
        try:
            resolve_agent_run(
                model=model,
                stack_slug=layers.stack_slug,
                agent_layers=(layers.agent_config,),
                llm_layers=layers.llm_layers(),
                harness_options=layers.harness_options,
                seed=SEED_PLACEHOLDER,
            )
        except (ValueError, KeyError) as exc:
            issues.append(ConditionIssue("resolution_failed", f"Cannot resolve the run: {exc}"))
    reasoning = run["llm"].get("reasoning")
    reasoning_tokens = reasoning.get("max_tokens") if isinstance(reasoning, dict) else None
    if isinstance(reasoning_tokens, int) and reasoning_tokens >= run["output_cap"]:
        issues.append(
            ConditionIssue(
                "reasoning_exceeds_output_cap",
                f"reasoning.max_tokens {reasoning_tokens} is not below {model.model_id}'s output cap "
                f"{run['output_cap']}",
            ),
        )
    # The seed is per run and reported as seed_forwarded, never as a drop.
    dropped = {k: v for k, v in gateway["dropped_params"].items() if k != "seed"}
    issues += [
        ConditionIssue("params_dropped", f"{key} not applied: {why}", blocking=False) for key, why in dropped.items()
    ]
    if run["context_window_source"] == "fallback":
        issues.append(
            ConditionIssue(
                "context_window_fallback",
                f"{model.model_id} has no context window in the catalog; harnesses are told {run['context_window']:,}",
                blocking=False,
            ),
        )
    explicit_routing = run["llm"].get("provider_routing_source") == "explicit"
    row = {
        "condition_id": condition.id,
        "label": condition_label(condition),
        "model": model.model_id,
        "model_name": model.model_name,
        "harness": layers.harness_name,
        "harness_version": harness.version() if harness is not None else "",
        "harness_preset": _preset_ref(layers.harness_preset),
        "harness_options": dict(layers.harness_options),
        "sampling_policy": agent["sampling_policy"],
        "forced_params": {k: v for k, v in gateway["forced_params"].items() if k not in _FORCED_EXCLUDED},
        "output_cap": run["output_cap"],
        "provider_routing": run["llm"]["provider"] if explicit_routing else "default",
        "seed_forwarded": run["seed_forwarded"],
        "dropped_params": dropped,
        "context_window": run["context_window"],
        "context_window_source": run["context_window_source"],
        "stack_slug": layers.stack_slug,
        "profile": _profile_ref(layers.profile),
        "param_overrides": dict(condition.param_overrides or {}),
    }
    return row, issues


def _flag_duplicates(plans: list[ConditionPlan]) -> None:
    """Conditions that would run exactly the same configuration (a wasted, pseudo-replicated arm)."""
    seen: dict[str, ConditionPlan] = {}
    for plan in plans:
        if plan.row is None:
            continue
        key = canonical_json({k: plan.row[k] for k in ("model", *DIFFERS_KEYS)})
        if key in seen:
            first = seen[key]
            plan.issues.append(
                ConditionIssue(
                    "duplicate_condition",
                    f"Runs the same configuration as condition #{first.condition.id} ({first.row['label']})",
                    blocking=False,
                ),
            )
        else:
            seen[key] = plan


def plan_conditions(experiment: Experiment, *, base: _ExperimentBase | None = None) -> list[ConditionPlan]:
    """Layers, issues and (agent mode) the comparability row of every condition."""
    base = base if base is not None else _experiment_base(experiment)
    plans: list[ConditionPlan] = []
    for condition in _conditions(experiment):
        layers = experiment_layers(experiment, condition, base=base)
        if layers.is_agent:
            row, issues = _agent_report(condition, layers)
            plans.append(ConditionPlan(condition, layers, issues, row))
        else:
            plans.append(ConditionPlan(condition, layers, list(layers.issues)))
    _flag_duplicates(plans)
    for plan in plans:
        if plan.row is not None:
            plan.row["problems"] = [i.detail for i in plan.issues]
            plan.row["errors"] = [i.detail for i in plan.issues if i.blocking]
    return plans


def _comparability_payload(plans: list[ConditionPlan]) -> dict[str, Any]:
    rows = [plan.row for plan in plans if plan.row is not None]
    differs = sorted(key for key in DIFFERS_KEYS if len({canonical_json(r[key]) for r in rows}) > 1)
    return {
        "conditions": rows,
        "differs_on": differs,
        "has_problems": any(r["problems"] for r in rows),
        "has_errors": any(r["errors"] for r in rows),
    }


def comparability(experiment: Experiment) -> dict[str, Any]:
    """What each agent condition will actually run with, and where conditions differ.

    Differences are not errors (an ablation varies something on purpose), but
    unintended ones -- a param one model cannot take, a harness a model
    cannot drive -- must be visible before any money is spent. ``errors``
    are the problems that block a launch.
    """
    return _comparability_payload(plan_conditions(experiment))


def launch_errors(plans: list[ConditionPlan]) -> list[dict[str, Any]]:
    """Blocking issues as ``[{code, detail, condition_id}]`` (experiment-wide ones once)."""
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for plan in plans:
        for issue in plan.issues:
            if not issue.blocking or (issue.code, issue.detail) in seen:
                continue
            seen.add((issue.code, issue.detail))
            errors.append({"code": issue.code, "detail": issue.detail, "condition_id": plan.condition.id})
    return errors


def duplicate_condition(experiment: Experiment, candidate: ExperimentCondition) -> ExperimentCondition | None:
    """An existing condition that would run exactly what the unsaved *candidate* runs, if any."""
    base = _experiment_base(experiment)

    def identity(condition: ExperimentCondition) -> tuple:
        layers = experiment_layers(experiment, condition, base=base)
        return (
            condition.model_id,
            condition.profile_id,
            layers.harness_name,
            canonical_json(layers.harness_options),
            canonical_json(condition.param_overrides or {}),
        )

    target = identity(candidate)
    return next((c for c in _conditions(experiment) if identity(c) == target), None)


# ── preview, cost ─────────────────────────────────────────────────────


def _estimate_cell_cost(cell: MatrixCell, layers: ExperimentLayers) -> float:
    if layers.is_agent:
        # Agent runs vary too much for a token model; the budget cap is the
        # honest number (an upper bound per job).
        return float(layers.effective_agent_config["max_cost_usd"])
    model = cell.condition.model
    if not model.input_price_per_token and not model.output_price_per_token:
        return 0.0
    # Rough prompt-size proxy: app spec size scales with requirement count.
    app = cell.app_requirement
    requirement_chars = sum(
        len(r)
        for r in (app.backend_requirements or []) + (app.frontend_requirements or []) + (app.admin_requirements or [])
    )
    prompt_tokens = max(500, requirement_chars // _CHARS_PER_TOKEN_ESTIMATE) * _STAGES_PER_JOB
    completion_tokens = _TYPICAL_COMPLETION_TOKENS * _STAGES_PER_JOB
    return OpenRouterClient.estimate_cost(
        prompt_tokens,
        completion_tokens,
        model.input_price_per_token,
        model.output_price_per_token,
    )


def _experiment_agent_config(experiment: Experiment, base: _ExperimentBase) -> dict[str, Any] | None:
    """The effective experiment-wide agent config, harness excluded (that is per condition)."""
    if experiment.mode != GenerationJob.Mode.AGENT:
        return None
    return {k: v for k, v in merge_agent_config(base.agent_config).items() if k != "harness"}


def preview_experiment(experiment: Experiment) -> dict[str, Any]:
    """Matrix size, cost estimate, comparability and launch blockers, without creating any jobs."""
    base = _experiment_base(experiment)
    plans = plan_conditions(experiment, base=base)
    apps = list(experiment.app_requirements.all())
    cells = _cells([p.condition for p in plans], apps, experiment.repeats)
    covered = _covered_cells(experiment)
    is_agent = experiment.mode == GenerationJob.Mode.AGENT

    per_condition: list[dict[str, Any]] = []
    total_cost = 0.0
    for plan in plans:
        condition, layers = plan.condition, plan.layers
        cost = sum(_estimate_cell_cost(c, layers) for c in cells if c.condition.id == condition.id)
        total_cost += cost
        per_condition.append(
            {
                "condition_id": condition.id,
                "label": condition_label(condition),
                "harness": layers.harness_name or condition.harness,
                "harness_preset": _preset_ref(layers.harness_preset),
                "model": condition.model.model_name,
                "model_id": condition.model.model_id,
                "profile": _profile_ref(layers.profile),
                "bundle_slug": layers.profile.slug if layers.profile else None,
                "bundle_version": layers.profile.version if layers.profile else None,
                "stack_slug": layers.stack_slug,
                "jobs": len(apps) * experiment.repeats,
                "estimated_cost": round(cost, 4),
            },
        )
    return {
        "total_jobs": len(cells),
        # Cells a launch would create now (failed / cancelled cells are re-run).
        "pending_jobs": sum(1 for c in cells if _cell_key(c) not in covered),
        "conditions": len(plans),
        "apps": len(apps),
        "repeats": experiment.repeats,
        "estimated_cost": round(total_cost, 4),
        "cost_estimate_kind": "budget_upper_bound" if is_agent else "heuristic",
        "mode": experiment.mode,
        "stack_slug": experiment.stack_slug or None,
        # Every stack a condition runs on (several only for legacy experiments without a stack).
        "stacks": sorted({p.layers.stack_slug for p in plans}),
        "presets": {"sampling": _preset_ref(base.sampling_preset), "budget": _preset_ref(base.budget_preset)},
        "agent_config": _experiment_agent_config(experiment, base),
        "per_condition": per_condition,
        "comparability": _comparability_payload(plans) if is_agent else None,
        "launch_errors": launch_errors(plans),
    }


# ── launch ────────────────────────────────────────────────────────────


def _cell_key(cell: MatrixCell) -> tuple[int, int, int]:
    return (cell.condition.id, cell.app_requirement.id, cell.repeat_index)


def _covered_cells(experiment: Experiment) -> set[tuple[int, int, int]]:
    """(condition_id, app_id, repeat_index) already covered by a live or completed job.

    Failed and cancelled cells are not covered: a re-launch tops them up.
    """
    rows = experiment.jobs.exclude(
        status__in=(GenerationJob.Status.FAILED, GenerationJob.Status.CANCELLED),
    ).values_list("condition_id", "app_requirement_id", "repeat_index")
    return {(c, a, r) for c, a, r in rows if c is not None and a is not None and r is not None}


def _order_seed(experiment: Experiment) -> int:
    return experiment.base_seed if experiment.base_seed is not None else int(experiment.id.int % 2_147_483_647)


def run_order(jobs: list[GenerationJob], experiment: Experiment) -> list[GenerationJob]:
    """Randomised complete blocks: repeat by repeat, cells shuffled within each repeat."""
    rng = random.Random(_order_seed(experiment))  # noqa: S311 - run-order randomisation, not crypto
    blocks: dict[int, list[GenerationJob]] = {}
    for job in sorted(jobs, key=lambda j: (j.repeat_index or 0, j.condition_id or 0, j.app_requirement_id or 0)):
        blocks.setdefault(job.repeat_index or 0, []).append(job)
    ordered: list[GenerationJob] = []
    for repeat in sorted(blocks):
        block = blocks[repeat]
        rng.shuffle(block)
        ordered.extend(block)
    return ordered


def _batch_request(
    experiment: Experiment,
    base: _ExperimentBase,
    plans: list[ConditionPlan],
    apps: list[AppRequirementTemplate],
    *,
    cell_count: int,
    pending_count: int,
) -> dict[str, Any]:
    """``GenerationBatch.request`` of a launch: the design it ran, auditable from the batch."""
    stacks = list(dict.fromkeys(p.layers.stack_slug for p in plans))
    return {
        "mode": experiment.mode,
        "experiment_id": str(experiment.id),
        "base_seed": experiment.base_seed,
        "order_seed": _order_seed(experiment),
        "trials": experiment.repeats,
        "stack_slug": experiment.stack_slug or (stacks[0] if len(stacks) == 1 else None),
        "app_requirement_ids": [a.id for a in apps],
        "model_ids": list(dict.fromkeys(p.condition.model_id for p in plans)),
        "condition_ids": [p.condition.id for p in plans],
        "profile_ids": list(dict.fromkeys(p.condition.profile_id for p in plans if p.condition.profile_id)),
        "harnesses": list(dict.fromkeys(p.layers.harness_name for p in plans if p.layers.harness_name)),
        "harness_preset_ids": list(
            dict.fromkeys(p.layers.harness_preset.id for p in plans if p.layers.harness_preset is not None),
        ),
        "sampling_preset_id": base.sampling_preset.id if base.sampling_preset is not None else None,
        "budget_preset_id": base.budget_preset.id if base.budget_preset is not None else None,
        "llm_params": dict(experiment.llm_defaults or {}),
        "agent_config": dict(experiment.agent_config or {}),
        "cell_count": cell_count,
        "pending_cell_count": pending_count,
    }


def launch_experiment(experiment: Experiment, user: AbstractUser) -> GenerationBatch:
    """Create jobs for every matrix cell lacking a live or completed job, and dispatch them.

    Idempotent/resumable: re-invoking after partial failures only creates
    jobs for the cells that still need one, so retrying a failed launch (or
    topping up after a failure or cancel) never duplicates completed work.
    Every condition is resolved first; blocking problems (invalid presets,
    a stack mismatch, a harness a model cannot drive, ...) refuse the whole
    launch with ``ExperimentError`` before anything is written, rather than
    creating failed cells that unbalance the design.
    """
    base = _experiment_base(experiment)
    plans = plan_conditions(experiment, base=base)
    errors = launch_errors(plans)
    if errors:
        raise ExperimentError(400, "; ".join(e["detail"] for e in errors), errors[0]["code"], errors)

    apps = list(experiment.app_requirements.all())
    cells = _cells([p.condition for p in plans], apps, experiment.repeats)
    covered = _covered_cells(experiment)
    pending_cells = [c for c in cells if _cell_key(c) not in covered]
    layers_by_condition = {p.condition.id: p.layers for p in plans}

    is_agent = experiment.mode == GenerationJob.Mode.AGENT
    batch = GenerationBatch.objects.create(
        name=f"Experiment: {experiment.name}",
        mode=experiment.mode,
        # The jobs actually created (a top-up only creates the uncovered cells).
        total_jobs=len(pending_cells),
        created_by=user,
        request=_batch_request(
            experiment,
            base,
            plans,
            apps,
            cell_count=len(cells),
            pending_count=len(pending_cells),
        ),
    )

    created_jobs: list[GenerationJob] = []
    for cell in pending_cells:
        condition = cell.condition
        app_req = cell.app_requirement
        layers = layers_by_condition[condition.id]
        job = GenerationJob.objects.create(
            mode=GenerationJob.Mode.AGENT if is_agent else GenerationJob.Mode.SCAFFOLDING,
            created_by=user,
            batch=batch,
            experiment=experiment,
            condition=condition,
            repeat_index=cell.repeat_index,
            model=condition.model,
            stack_slug=layers.stack_slug,
            app_requirement=app_req,
            profile=layers.profile,
            # Preset values are applied here; the FKs below are provenance.
            llm_params=dict(layers.llm_params),
            # Legacy columns mirror the collapsed layer so pre-snapshot code
            # paths (and the admin) see the effective values.
            temperature=layers.temperature,
            max_tokens=layers.max_tokens,
            top_p=layers.llm_params.get("top_p"),
            agent_config=dict(layers.agent_config),
            harness_options=dict(layers.harness_options),
            harness_preset=layers.harness_preset,
            sampling_preset=layers.sampling_preset,
            budget_preset=layers.budget_preset,
        )
        if experiment.base_seed is not None:
            # Agent mode uses common random numbers: every condition of an
            # (app, repeat) cell gets the same seed, so paired comparisons
            # differ only in the condition. (Scaffolding keeps its historical
            # per-condition derivation so old launches stay reproducible.)
            parts = (app_req.id, cell.repeat_index) if is_agent else (condition.id, app_req.id, cell.repeat_index)
            job.experiment_seed = derive_experiment_seed(experiment.base_seed, *parts)

        try:
            if is_agent:
                apply_agent_snapshot_to_job(job)
            else:
                apply_snapshot_to_job(job)
        except Exception as exc:  # noqa: BLE001 — isolate one bad cell from the launch
            logger.warning("Snapshot build failed for experiment job %s: %s", job.id, exc)
            job.status = GenerationJob.Status.FAILED
            job.error_message = f"Snapshot build failed: {exc}"
            job.save(update_fields=["status", "error_message", "updated_at"])
            continue
        created_jobs.append(job)

    # Counts and status from the rows: an empty top-up is completed, a launch
    # whose every snapshot failed is failed.
    refresh_batch(batch)

    if experiment.status == Experiment.Status.DRAFT:
        experiment.status = Experiment.Status.RUNNING
        experiment.save(update_fields=["status", "updated_at"])

    for job in run_order(created_jobs, experiment):
        dispatch_job(job)

    return batch


# ── status, export ────────────────────────────────────────────────────


def experiment_status(experiment: Experiment) -> dict[str, Any]:
    """Progress grid (per condition x app) and running cost."""
    jobs = list(
        experiment.jobs.select_related("condition", "app_requirement").exclude(condition__isnull=True),
    )
    total_cells = len(expand_matrix(experiment))

    by_status: dict[str, int] = {}
    running_cost = 0.0
    for job in jobs:
        by_status[job.status] = by_status.get(job.status, 0) + 1
        metrics = job.metrics if isinstance(job.metrics, dict) else {}
        running_cost += float(metrics.get("cost") or 0.0)

    grid: dict[str, dict[str, Any]] = {}
    for job in jobs:
        key = f"{job.condition_id}:{job.app_requirement_id}"
        cell = grid.setdefault(key, {"condition_id": job.condition_id, "app_id": job.app_requirement_id, "runs": []})
        cell["runs"].append(
            {
                "job_id": str(job.id),
                "repeat_index": job.repeat_index,
                "status": job.status,
            },
        )

    return {
        "status": experiment.status,
        "total_cells": total_cells,
        "jobs_created": len(jobs),
        "by_status": by_status,
        "running_cost": round(running_cost, 4),
        "grid": list(grid.values()),
    }


def export_experiment(experiment: Experiment) -> dict[str, Any]:
    """Research export: experiment config (resolved, with preset provenance) + per-job summaries."""
    base = _experiment_base(experiment)
    conditions = _conditions(experiment)
    layers = {c.id: experiment_layers(experiment, c, base=base) for c in conditions}
    is_agent = experiment.mode == GenerationJob.Mode.AGENT
    agent_config = None
    if is_agent:
        default_harness = (experiment.agent_config or {}).get("harness") or default_harness_name()
        agent_config = merge_agent_config(base.agent_config, {"harness": default_harness})
    jobs = list(
        experiment.jobs.select_related(
            "condition",
            "condition__model",
            "condition__profile",
            "app_requirement",
        ),
    )
    return {
        "experiment": {
            "id": str(experiment.id),
            "name": experiment.name,
            "slug": experiment.slug,
            "description": experiment.description,
            "hypothesis": experiment.hypothesis,
            "status": experiment.status,
            "repeats": experiment.repeats,
            "base_seed": experiment.base_seed,
            "llm_defaults": experiment.llm_defaults,
            "mode": experiment.mode,
            "stack_slug": experiment.stack_slug or None,
            # Effective (platform defaults -> budget preset -> sampling policy
            # -> overrides); the per-job snapshots stay authoritative.
            "agent_config": agent_config,
            "agent_config_overrides": experiment.agent_config,
            "presets": {"sampling": provenance(base.sampling_preset), "budget": provenance(base.budget_preset)},
            "continuation_limit": experiment.continuation_limit,
            "enable_repair": experiment.enable_repair,
            "created_at": experiment.created_at.isoformat(),
        },
        "conditions": [
            {
                "id": c.id,
                "label": c.label,
                "display_label": condition_label(c),
                "model": c.model.model_id,
                "profile": _profile_ref(c.profile),
                "bundle_slug": c.profile.slug if c.profile else None,
                "bundle_version": c.profile.version if c.profile else None,
                "harness": layers[c.id].harness_name or c.harness,
                "harness_preset": provenance(layers[c.id].harness_preset),
                "harness_options": layers[c.id].harness_options,
                "param_overrides": c.param_overrides,
                "llm_params": layers[c.id].llm_params,
                "stack_slug": layers[c.id].stack_slug,
            }
            for c in conditions
        ],
        "app_requirements": [a.slug for a in experiment.app_requirements.all()],
        "jobs": [_export_job(job) for job in jobs],
    }


def _export_job(job: GenerationJob) -> dict[str, Any]:
    snapshot = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    result = job.result_data if isinstance(job.result_data, dict) else {}
    agent = result.get("agent") or {}
    return {
        "id": str(job.id),
        "status": job.status,
        "condition_id": job.condition_id,
        "app_requirement_slug": job.app_requirement.slug if job.app_requirement else None,
        "repeat_index": job.repeat_index,
        "experiment_seed": job.experiment_seed,
        "stack_slug": job.stack_slug,
        "prompt_hash": job.prompt_hash,
        "bundle_key": job.bundle_key,
        "run_fingerprint": snapshot.get("run_fingerprint"),
        "config_fingerprint": snapshot.get("config_fingerprint"),
        "harness": (snapshot.get("harness") or {}).get("name") if snapshot.get("mode") == "agent" else None,
        "harness_options": job.harness_options or {},
        "presets": snapshot.get("presets"),
        "termination": agent.get("termination"),
        "providers": agent.get("providers"),
        "metrics": job.metrics,
    }
