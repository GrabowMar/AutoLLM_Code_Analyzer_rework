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

Run order is frozen per job at launch (``GenerationJob.sequence``) by
``execution.sequence.cell_sequence`` under the experiment's scheduling
policy: ``interleaved`` (randomised complete blocks: all cells of repeat 0
before any of repeat 1, shuffled within a block), ``blocked`` (a model's cells
contiguous within each block, for endpoints that hold few models at once) or
``grouped``; ``auto`` picks per endpoint lane. Without blocking on repeats a
condition-major order would confound conditions with time (provider load and
routing, alias repointing, rate limits all drift over hours). The executor
claims each lane strictly in sequence order.

Besides design and launch this module owns the experiment lifecycle (pause,
resume, cancel, top-up, clone, archive) and the read models the API serves
(list rows with progress, detail, status rollups, the cell grid, preview v2
and export). Statistics live in ``experiment_results``; schedules and
post-runs in ``experiment_automation``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import random
import statistics
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Count
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from backend.execution.api import cancel_jobs
from backend.execution.api import enqueue_jobs
from backend.execution.api import wake
from backend.generation.models import AppRequirementTemplate
from backend.generation.models import Experiment
from backend.generation.models import ExperimentCondition
from backend.generation.models import ExperimentPostRun
from backend.generation.models import ExperimentSchedule
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.models import GenerationProfile
from backend.generation.services.agent.config import merge_agent_config
from backend.generation.services.agent.config import validate_agent_config
from backend.generation.services.agent.harnesses import HARNESSES
from backend.generation.services.agent.harnesses import default_harness_name
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.agent.request import SEED_PLACEHOLDER
from backend.generation.services.agent.snapshot import agent_llm_layers
from backend.generation.services.agent.snapshot import apply_agent_snapshot_to_job
from backend.generation.services.agent.snapshot import resolve_agent_run
from backend.generation.services.agent.snapshot import resolve_llm
from backend.generation.services.analysis_plan import PlanError
from backend.generation.services.analysis_plan import normalize_analysis_plan
from backend.generation.services.analysis_plan import plan_hash
from backend.generation.services.batches import refresh_batch
from backend.generation.services.llm_params import merge_llm_params
from backend.generation.services.llm_params import validate_llm_params
from backend.generation.services.openrouter_client import OpenRouterClient
from backend.generation.services.presets import builtin_default_config
from backend.generation.services.presets import preset_config_for_use
from backend.generation.services.presets import provenance
from backend.generation.services.presets import system_default_preset
from backend.generation.services.presets import visible_q
from backend.generation.services.profile_resolver import apply_snapshot_to_job
from backend.generation.services.profile_resolver import experiment_cell_seed
from backend.generation.services.sampling import MODE_DEFAULT_MAX_TOKENS
from backend.generation.services.sampling import MODE_DEFAULT_TEMPERATURE
from backend.generation.services.versioning import canonical_json
from backend.inference.access import launch_issues
from backend.inference.models import SYSTEM_ENDPOINT_SLUG
from backend.inference.models import LLMEndpoint
from backend.inference.snapshot import effective_num_ctx
from backend.inference.snapshot import is_local
from backend.llm_models.models import LLMModel
from backend.realtime.events import publish_experiment_event
from backend.runtime.services.scaffolding import canonical_stack_slug
from backend.runtime.services.scaffolding import get_stack_row
from backend.runtime.services.scaffolding import is_known_stack_slug
from backend.runtime.services.stack_validation import stack_usable_by

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

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
    # Where the model runs (spec §D.5): equal for every OpenRouter condition.
    "endpoint",
    "digest",
    "num_ctx",
    "api_flavor",
    "quantization",
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
    return list(experiment.conditions.select_related("profile", "model__endpoint", "harness_preset"))


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
    # budget preset -> sampling preset policy -> experiment.agent_config (minus
    # harness): explicit experiment overrides beat presets, as inline
    # agent_config does for POST /jobs/agent/.
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
      The experiment's explicit overrides beat the presets (an
      ``agent_config.sampling_policy`` wins over the sampling preset's), the
      same order ad-hoc batches give inline ``agent_config``.
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
    local = is_local(model)
    if local:
        provider_routing: Any = "n/a"
    else:
        provider_routing = run["llm"]["provider"] if explicit_routing else "default"
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
        "provider_routing": provider_routing,
        "seed_forwarded": run["seed_forwarded"],
        "dropped_params": dropped,
        "context_window": run["context_window"],
        "context_window_source": run["context_window_source"],
        "stack_slug": layers.stack_slug,
        "profile": _profile_ref(layers.profile),
        "param_overrides": dict(condition.param_overrides or {}),
        **_source_columns(model),
    }
    return row, issues


def _source_columns(model: LLMModel) -> dict[str, Any]:
    """Comparability columns for where *model* runs (OpenRouter rows share one value per column)."""
    if not is_local(model):
        return {
            "endpoint": SYSTEM_ENDPOINT_SLUG,
            "digest": "",
            "num_ctx": None,
            "api_flavor": "openai",
            "quantization": "",
        }
    endpoint = model.endpoint
    return {
        "endpoint": endpoint.slug,
        "digest": model.digest,
        "num_ctx": effective_num_ctx(model),
        "api_flavor": endpoint.api_flavor,
        "quantization": model.quantization,
    }


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
    if is_local(cell.condition.model):
        return 0.0  # local endpoints bill nothing
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
    return _preview_base(experiment, base, plans, list(experiment.app_requirements.all()))


def _preview_base(
    experiment: Experiment,
    base: _ExperimentBase,
    plans: list[ConditionPlan],
    apps: list[AppRequirementTemplate],
) -> dict[str, Any]:
    cells = _cells([p.condition for p in plans], apps, experiment.repeats)
    covered = _covered_cells(experiment)
    is_agent = experiment.mode == GenerationJob.Mode.AGENT

    per_condition: list[dict[str, Any]] = []
    total_cost = 0.0
    cells_by_condition: dict[int, list[MatrixCell]] = defaultdict(list)
    for cell in cells:
        cells_by_condition[cell.condition.id].append(cell)
    for plan in plans:
        condition, layers = plan.condition, plan.layers
        cost = sum(_estimate_cell_cost(c, layers) for c in cells_by_condition[condition.id])
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
                "endpoint": condition.model.endpoint.slug if is_local(condition.model) else SYSTEM_ENDPOINT_SLUG,
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


# ── endpoints, capacity ───────────────────────────────────────────────

# A direct endpoint counts as online while its pump's inventory is this fresh
# (the pump re-inventories every 120 s).
# History used by the duration estimates (spec §F.1: last 50 completed jobs).
_ESTIMATE_HISTORY = 50
_NUM_CTX_SMALL = 16_384
_EXECUTOR_STALE_SECONDS = 90


def system_endpoint() -> LLMEndpoint:
    from backend.inference.access import system_openrouter

    return system_openrouter()


def model_endpoint(model: LLMModel, system: LLMEndpoint) -> LLMEndpoint:
    """Where *model* runs: its own endpoint, or the system OpenRouter row."""
    return model.endpoint if is_local(model) else system


@dataclass(frozen=True)
class EndpointLive:
    online: bool
    sessions: int


def endpoint_liveness(endpoints: list[LLMEndpoint]) -> dict[int, EndpointLive]:
    """Online state and live relay session count per endpoint (one query for every relay endpoint).

    Same rule as ``inference.live.endpoint_status`` (what ``launch_issues``
    reports), batched: a relay endpoint is online with a live *active*
    session (standbys are counted in ``sessions``), a direct one with fresh
    pump inventory (``degraded`` with a last error still counts as online),
    a disabled one never.
    """
    from backend.inference.live import direct_online
    from backend.inference.live import live_sessions
    from backend.inference.models import RelaySession

    relay_ids = [e.pk for e in endpoints if not e.is_system and e.connection == LLMEndpoint.Connection.RELAY]
    sessions: dict[int, int] = {}
    active: set[int] = set()
    if relay_ids:
        for endpoint_id, role in live_sessions(relay_ids).values_list("endpoint_id", "role"):
            sessions[endpoint_id] = sessions.get(endpoint_id, 0) + 1
            if role == RelaySession.Role.ACTIVE:
                active.add(endpoint_id)
    live: dict[int, EndpointLive] = {}
    for endpoint in endpoints:
        if endpoint.is_system:
            live[endpoint.pk] = EndpointLive(online=bool(endpoint.enabled), sessions=0)
        elif endpoint.connection == LLMEndpoint.Connection.RELAY:
            live[endpoint.pk] = EndpointLive(
                online=bool(endpoint.enabled) and endpoint.pk in active,
                sessions=sessions.get(endpoint.pk, 0),
            )
        else:
            live[endpoint.pk] = EndpointLive(online=bool(endpoint.enabled) and direct_online(endpoint), sessions=0)
    return live


def job_cap(endpoint: LLMEndpoint) -> int:
    """Jobs one endpoint may run at once in either lane (spec §C.4: max_active_jobs, else max_concurrency)."""
    return int(endpoint.max_active_jobs or endpoint.max_concurrency or 1)


def resident_limit(endpoint: LLMEndpoint, experiment: Experiment) -> int | None:
    """K for *experiment*'s lane on *endpoint*: the endpoint's, tightened by ``execution_options``."""
    if endpoint.is_system:
        return None
    limits = [endpoint.max_resident_models, (experiment.execution_options or {}).get("max_resident_models")]
    finite = [int(k) for k in limits if k]
    return min(finite) if finite else None


def effective_policies(experiment: Experiment, endpoints: list[LLMEndpoint]) -> dict[str, str]:
    """``{endpoint slug: policy}``: ``auto`` is ``blocked`` on finite-K endpoints and ``interleaved`` elsewhere."""
    requested = experiment.scheduling_policy or Experiment.SchedulingPolicy.AUTO
    out: dict[str, str] = {}
    for endpoint in endpoints:
        if requested != Experiment.SchedulingPolicy.AUTO:
            out[endpoint.slug] = requested
        elif resident_limit(endpoint, experiment) is not None:
            out[endpoint.slug] = Experiment.SchedulingPolicy.BLOCKED
        else:
            out[endpoint.slug] = Experiment.SchedulingPolicy.INTERLEAVED
    return out


def _policy_reason(experiment: Experiment) -> str:
    requested = experiment.scheduling_policy or Experiment.SchedulingPolicy.AUTO
    if requested == Experiment.SchedulingPolicy.AUTO:
        return "auto: blocked on endpoints that hold a limited number of models at once, interleaved elsewhere"
    return f"{requested}: chosen for this experiment"


def _executor_settings():
    from backend.execution.models import ExecutorSettings

    return ExecutorSettings.get()


def executor_state() -> dict[str, bool]:
    """``{online, draining, code_stale}`` from the executor bookkeeping rows (no executor call)."""
    from backend.execution.models import ExecutorInstance

    executor_settings = _executor_settings()
    online = ExecutorInstance.objects.filter(
        stopped_at__isnull=True,
        last_seen_at__gt=timezone.now() - timedelta(seconds=_EXECUTOR_STALE_SECONDS),
    ).exists()
    return {"online": online, "draining": bool(executor_settings.draining), "code_stale": _code_stale()}


def _code_stale() -> bool:
    """Whether the live leader executor runs older code than this process."""
    from backend.execution import capacity
    from backend.execution.models import ExecutorInstance

    try:
        leader = (
            ExecutorInstance.objects.filter(
                is_leader=True,
                stopped_at__isnull=True,
                last_seen_at__gt=timezone.now() - timedelta(seconds=_EXECUTOR_STALE_SECONDS),
            )
            .order_by("-last_seen_at")
            .first()
        )
        return bool(leader is not None and leader.code_version and leader.code_version != capacity.code_version())
    except Exception:  # noqa: BLE001 — a rollup must not fail on an advisory flag
        logger.debug("code_stale check failed", exc_info=True)
        return False


def _slots_for(mode: str, executor_settings) -> int:
    if mode == GenerationJob.Mode.AGENT:
        return max(1, int(executor_settings.sandbox_slots))
    return max(1, int(executor_settings.llm_job_threads))


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def run_seconds_p50(model: LLMModel, endpoint: LLMEndpoint, mode: str) -> float | None:
    """Median duration of the last completed jobs of *model* in *mode*; else of the endpoint; else None."""
    done = GenerationJob.objects.filter(
        mode=mode,
        status=GenerationJob.Status.COMPLETED,
        duration_seconds__isnull=False,
    ).order_by("-completed_at")
    own = list(done.filter(model=model).values_list("duration_seconds", flat=True)[:_ESTIMATE_HISTORY])
    if own:
        return _median(own)
    scope = done.filter(model__endpoint=endpoint) if not endpoint.is_system else done.filter(model__endpoint=None)
    return _median(list(scope.values_list("duration_seconds", flat=True)[:_ESTIMATE_HISTORY]))


def _request_seconds_p50(endpoint: LLMEndpoint) -> float | None:
    """Median upstream call time of the endpoint's recent requests (the ``wall_clock_tight`` check)."""
    from backend.inference.models import LLMRequest

    values = list(
        LLMRequest.objects.filter(endpoint=endpoint, upstream_ms__isnull=False)
        .order_by("-completed_at")
        .values_list("upstream_ms", flat=True)[:200],
    )
    median = _median([float(v) for v in values])
    return median / 1000.0 if median is not None else None


def _lru_loads(models_in_order: list[int], capacity: int | None) -> int:
    """Model loads an endpoint holding ``capacity`` models (None: unlimited) needs for this order."""
    if capacity is None:
        return len(set(models_in_order))
    resident: list[int] = []
    loads = 0
    for model_id in models_in_order:
        if model_id in resident:
            resident.remove(model_id)
        else:
            loads += 1
            if len(resident) >= capacity:
                resident.pop(0)
        resident.append(model_id)
    return loads


def _issue(code: str, detail: str, *, blocking: bool, **extra: Any) -> dict[str, Any]:
    return {
        "code": code,
        "detail": detail,
        "blocking": blocking,
        "model_id": extra.get("model_id"),
        "endpoint": extra.get("endpoint"),
        "condition_id": extra.get("condition_id"),
    }


def _digest_drift(conditions: list[ExperimentCondition]) -> list[dict[str, Any]]:
    """Blocking ``digest_drift`` issues: local conditions whose live digest moved since launch pinned it."""
    issues = []
    for condition in conditions:
        model = condition.model
        if is_local(model) and condition.pinned_digest and model.digest != condition.pinned_digest:
            issues.append(
                _issue(
                    "digest_drift",
                    f"{model.model_id} changed weights since launch ({condition.pinned_digest[:19]} → "
                    f"{(model.digest or '?')[:19]}); resume with accept_digest_drift or clone the experiment",
                    blocking=True,
                    model_id=model.pk,
                    endpoint=model.endpoint.slug,
                    condition_id=condition.id,
                ),
            )
    return issues


def _model_issues(experiment: Experiment, conditions: list[ExperimentCondition]) -> list[dict[str, Any]]:
    """Per-model non-blocking issues for local conditions (§F.1 issue codes)."""
    issues: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    digests: dict[str, set[str]] = defaultdict(set)
    is_agent = experiment.mode == GenerationJob.Mode.AGENT
    for condition in conditions:
        model = condition.model
        if not is_local(model):
            continue
        endpoint = model.endpoint
        extra = {"model_id": model.pk, "endpoint": endpoint.slug}

        def add(code: str, detail: str, _extra=extra) -> None:
            if (code, _extra["model_id"]) not in seen:
                seen.add((code, _extra["model_id"]))
                issues.append(_issue(code, detail, blocking=False, **_extra))

        num_ctx = effective_num_ctx(model)
        if is_agent and num_ctx is not None and num_ctx < _NUM_CTX_SMALL:
            add("num_ctx_small", f"{model.model_id} runs with a {num_ctx:,}-token context; agents need more")
        if endpoint.kind == LLMEndpoint.Kind.OLLAMA and endpoint.api_flavor == LLMEndpoint.ApiFlavor.OPENAI:
            add(
                "num_ctx_server_default",
                f"{endpoint.slug} uses the OpenAI flavour: Ollama applies its server default context length",
            )
        if not model.digest:
            add("digest_unknown", f"{model.model_id} reports no digest; weight changes cannot be detected")
        else:
            digests[model.digest].add(endpoint.slug)
    for digest, slugs in digests.items():
        if len(slugs) > 1:
            issues.append(
                _issue(
                    "same_digest_multiple_endpoints",
                    f"The same weights ({digest[:19]}) run on {', '.join(sorted(slugs))}; "
                    "they are separate conditions (fingerprints include the endpoint)",
                    blocking=False,
                ),
            )
    return issues


def _plan_issues(plans: list[ConditionPlan]) -> list[dict[str, Any]]:
    out = []
    seen: set[tuple[str, str]] = set()
    for plan in plans:
        for issue in plan.issues:
            if (issue.code, issue.detail) in seen:
                continue
            seen.add((issue.code, issue.detail))
            out.append(_issue(issue.code, issue.detail, blocking=issue.blocking, condition_id=plan.condition.id))
    return out


def preview_experiment_v2(experiment: Experiment, user: AbstractUser) -> dict[str, Any]:
    """The preview plus the v2 sections (spec §F.1 ``ExperimentPreviewV2``): endpoints, plan, capacity, cost."""
    base = _experiment_base(experiment)
    plans = plan_conditions(experiment, base=base)
    apps = list(experiment.app_requirements.all())
    payload = _preview_base(experiment, base, plans, apps)
    conditions = [p.condition for p in plans]
    is_agent = experiment.mode == GenerationJob.Mode.AGENT
    system = system_endpoint()

    endpoints: dict[int, LLMEndpoint] = {}
    for condition in conditions:
        endpoint = model_endpoint(condition.model, system)
        endpoints.setdefault(endpoint.pk, endpoint)
    endpoint_list = list(endpoints.values())
    live = endpoint_liveness(endpoint_list)
    policies = effective_policies(experiment, endpoint_list)
    executor_settings = _executor_settings()
    slots = _slots_for(experiment.mode, executor_settings)

    issues = _plan_issues(plans)
    issues += launch_issues(user, [c.model for c in conditions])
    issues += _model_issues(experiment, conditions)
    if experiment.status != Experiment.Status.DRAFT:
        issues += _digest_drift(conditions)

    # Run order and the swaps it costs per endpoint, over every cell (as launch freezes it).
    all_cells = [_cell_key(c) for c in _cells(conditions, apps, experiment.repeats)]
    sequence = _sequence(experiment, all_cells) if all_cells else {}
    condition_by_id = {c.id: c for c in conditions}
    covered = _covered_cells(experiment)
    pending_by_condition = Counter(key[0] for key in all_cells if key not in covered)

    agent = merge_agent_config(base.agent_config) if is_agent else {}
    endpoint_rows: list[dict[str, Any]] = []
    estimated_swaps: dict[str, int] = {}
    total_seconds = 0.0
    unknown_estimate = False
    worst: tuple[float, str] | None = None
    for endpoint in endpoint_list:
        conds = [c for c in conditions if model_endpoint(c.model, system).pk == endpoint.pk]
        k = resident_limit(endpoint, experiment)
        cap = job_cap(endpoint)
        ordered = sorted((key for key in all_cells if condition_by_id[key[0]] in conds), key=sequence.__getitem__)
        if not endpoint.is_system:
            estimated_swaps[endpoint.slug] = _lru_loads([condition_by_id[key[0]].model_id for key in ordered], k)
        cells_e = sum(pending_by_condition[c.id] for c in conds)
        seconds_e = 0.0
        p50s: list[tuple[float, int]] = []
        missing = False
        for condition in conds:
            p50 = run_seconds_p50(condition.model, endpoint, experiment.mode)
            if p50 is None:
                missing = True
                continue
            n = pending_by_condition[condition.id]
            seconds_e += p50 * n
            p50s.append((p50, n))
        weight = sum(n for _p, n in p50s)
        est_p50 = sum(p * n for p, n in p50s) / weight if weight else (p50s[0][0] if p50s else None)
        parallel = max(1, min(cap, slots))
        est_hours = None if missing or not cells_e else round(seconds_e / parallel / 3600, 3)
        if cells_e and missing:
            unknown_estimate = True
        total_seconds += seconds_e
        if est_hours is not None and (worst is None or est_hours > worst[0]):
            worst = (est_hours, endpoint.slug)

        ep_issues = [i for i in issues if i.get("endpoint") == endpoint.slug]
        already_offline = any(i["code"] == "endpoint_offline" for i in ep_issues)  # from launch_issues
        if not live[endpoint.pk].online and not endpoint.is_system and not already_offline:
            ep_issues.append(
                _issue(
                    "endpoint_offline",
                    f"{endpoint.slug} has no live connection; its jobs wait until it reconnects",
                    blocking=False,
                    endpoint=endpoint.slug,
                ),
            )
        models_on_e = list({c.model_id: c.model for c in conds}.values())
        if (
            k is not None
            and policies[endpoint.slug] == Experiment.SchedulingPolicy.INTERLEAVED
            and len(models_on_e) > k
        ):
            ep_issues.append(
                _issue(
                    "model_thrash_risk",
                    f"Interleaved order on {endpoint.slug} (holds {k} model(s)) swaps models about "
                    f"{estimated_swaps.get(endpoint.slug, 0)} times; blocked order needs far fewer loads",
                    blocking=False,
                    endpoint=endpoint.slug,
                ),
            )
        if is_agent and not endpoint.is_system:
            call_s = _request_seconds_p50(endpoint)
            if call_s is not None and call_s * agent["max_llm_requests"] > agent["wall_clock_seconds"]:
                ep_issues.append(
                    _issue(
                        "wall_clock_tight",
                        f"At {call_s:.0f}s per call, {agent['max_llm_requests']} requests exceed the "
                        f"{agent['wall_clock_seconds']}s wall clock on {endpoint.slug}",
                        blocking=False,
                        endpoint=endpoint.slug,
                    ),
                )
        issues += [i for i in ep_issues if i not in issues]
        endpoint_rows.append(
            {
                "id": endpoint.pk,
                "slug": endpoint.slug,
                "name": endpoint.name,
                "kind": endpoint.kind,
                "connection": endpoint.connection,
                "online": live[endpoint.pk].online,
                "sessions": live[endpoint.pk].sessions,
                "max_concurrency": endpoint.max_concurrency,
                "job_cap": cap,
                "resident_limit": k,
                "models": [
                    {
                        "id": m.pk,
                        "name": m.model_name,
                        "digest": m.digest,
                        "num_ctx": effective_num_ctx(m),
                        "tools": bool(m.supports_function_calling),
                    }
                    for m in models_on_e
                ],
                "cells": cells_e,
                "est_run_s_p50": round(est_p50, 1) if est_p50 is not None else None,
                "est_hours": est_hours,
                "issues": ep_issues,
            },
        )

    policy = experiment.scheduling_policy or Experiment.SchedulingPolicy.AUTO
    if policy == Experiment.SchedulingPolicy.GROUPED:
        issues.append(
            _issue(
                "time_confounded",
                "Grouped order runs one model after another: model differences confound with time",
                blocking=False,
            ),
        )
    elif Experiment.SchedulingPolicy.BLOCKED in policies.values():
        issues.append(
            _issue(
                "block_grouped",
                "Blocked order: within each repeat block a model's cells run together (model order re-randomised)",
                blocking=False,
            ),
        )

    sandbox_hours = total_seconds / slots / 3600 if total_seconds else 0.0
    total_hours: float | None = None
    bottleneck: str | None = None
    if not unknown_estimate and (worst is not None or sandbox_hours):
        endpoint_hours = worst[0] if worst is not None else 0.0
        total_hours = round(max(endpoint_hours, sandbox_hours), 3)
        if sandbox_hours >= endpoint_hours:
            bottleneck = "sandbox" if is_agent else None
        else:
            bottleneck = f"endpoint:{worst[1]}"

    local_cells = sum(pending_by_condition[c.id] for c in conditions if is_local(c.model))
    remote_cells = sum(pending_by_condition[c.id] for c in conditions if not is_local(c.model))
    if remote_cells and local_cells:
        cost_kind = "mixed"
    elif local_cells:
        cost_kind = "local_zero"
    else:
        cost_kind = payload["cost_estimate_kind"]
    payload.update(
        {
            "issues": issues,
            "schedule": {
                "policy_effective_by_endpoint": policies,
                "reason": _policy_reason(experiment),
                "order_seed": _order_seed(experiment),
                "estimated_swaps": estimated_swaps,
            },
            "endpoints": endpoint_rows,
            "capacity": {
                "sandbox_slots": int(executor_settings.sandbox_slots),
                "eval_slots": int(executor_settings.eval_slots),
                "llm_job_threads": int(executor_settings.llm_job_threads),
                "est_parallel_runs": min(slots, sum(job_cap(e) for e in endpoint_list) or slots),
            },
            "estimate": {"total_hours": total_hours, "bottleneck": bottleneck},
            "cost": {
                "openrouter_upper_bound_usd": payload["estimated_cost"],
                "local_cells": local_cells,
                "kind": cost_kind,
            },
        },
    )
    return payload


# ── launch ────────────────────────────────────────────────────────────

LAUNCH = "launch"
TOP_UP = "top_up"
_JOB_CHUNK = 500


def _cell_key(cell: MatrixCell) -> tuple[int, int, int]:
    return (cell.condition.id, cell.app_requirement.id, cell.repeat_index)


def _covered_cells(experiment: Experiment) -> set[tuple[int, int, int]]:
    """(condition_id, app_id, repeat_index) already covered by a live or completed job.

    Failed and cancelled cells are not covered: a re-launch tops them up.
    """
    if experiment._state.adding:  # noqa: SLF001 - an unsaved design has no jobs
        return set()
    rows = experiment.jobs.exclude(
        status__in=(GenerationJob.Status.FAILED, GenerationJob.Status.CANCELLED),
    ).values_list("condition_id", "app_requirement_id", "repeat_index")
    return {(c, a, r) for c, a, r in rows if c is not None and a is not None and r is not None}


def _attempted_cells(experiment: Experiment) -> set[tuple[int, int, int]]:
    rows = experiment.jobs.values_list("condition_id", "app_requirement_id", "repeat_index")
    return {(c, a, r) for c, a, r in rows if c is not None and a is not None and r is not None}


def _order_seed(experiment: Experiment) -> int:
    return experiment.base_seed if experiment.base_seed is not None else int(experiment.id.int % 2_147_483_647)


def _fallback_sequence(experiment: Experiment, cells: list[tuple[int, int, int]]) -> dict[tuple[int, int, int], int]:
    """Randomised complete blocks (``interleaved``), drawn per repeat block like ``execution.sequence``."""
    seed = _order_seed(experiment)
    blocks: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for cell in sorted(set(cells)):
        blocks[cell[2]].append(cell)
    block_size = max((len(b) for b in blocks.values()), default=0)
    out: dict[tuple[int, int, int], int] = {}
    for repeat in sorted(blocks):
        block = blocks[repeat]
        random.Random(hash((seed, repeat))).shuffle(block)  # noqa: S311 - run-order randomisation, not crypto
        for position, cell in enumerate(block):
            out[cell] = repeat * block_size + position
    return out


def _sequence(experiment: Experiment, cells: list[tuple[int, int, int]]) -> dict[tuple[int, int, int], int]:
    """Every cell's frozen run position under the experiment's policy (``execution.sequence.cell_sequence``)."""
    from backend.execution.sequence import cell_sequence

    policy = experiment.scheduling_policy or Experiment.SchedulingPolicy.AUTO
    try:
        return cell_sequence(experiment, cells, policy)
    except NotImplementedError:
        return _fallback_sequence(experiment, cells)


def run_order(jobs: list[GenerationJob], experiment: Experiment) -> list[GenerationJob]:
    """*jobs* in their frozen launch order (``sequence``; unsequenced jobs last)."""
    return sorted(jobs, key=lambda j: (j.sequence is None, j.sequence or 0, j.repeat_index or 0))


def _batch_request(
    experiment: Experiment,
    base: _ExperimentBase,
    plans: list[ConditionPlan],
    apps: list[AppRequirementTemplate],
    *,
    cell_count: int,
    pending_count: int,
    schedule: dict[str, Any],
    warnings: list[dict[str, Any]],
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
        "schedule": schedule,
        "warnings": warnings,
    }


def _targeted_plans(plans: list[ConditionPlan], condition_ids: list[int] | None) -> list[ConditionPlan]:
    if condition_ids is None:
        return plans
    wanted = set(condition_ids)
    unknown = sorted(wanted - {p.condition.id for p in plans})
    if unknown:
        raise ExperimentError(
            404,
            f"Unknown condition id {', '.join(str(i) for i in unknown)}",
            "unknown_condition",
        )
    return [p for p in plans if p.condition.id in wanted]


def _publish_state(experiment: Experiment) -> None:
    """``state`` event on ``experiment:<id>`` once the surrounding transaction commits."""
    event = {"type": "state", "status": experiment.status, "pause_reason": experiment.pause_reason}
    experiment_id = experiment.id
    transaction.on_commit(lambda: publish_experiment_event(experiment_id, event))


def _new_job(
    experiment: Experiment,
    user: AbstractUser,
    batch: GenerationBatch,
    cell: MatrixCell,
    layers: ExperimentLayers,
    *,
    endpoint: LLMEndpoint,
    sequence: int,
    policy: str,
) -> GenerationJob:
    """An unsaved job for one cell, with its frozen snapshot (or FAILED when the snapshot cannot be built)."""
    condition, app_req = cell.condition, cell.app_requirement
    is_agent = layers.is_agent
    job = GenerationJob(
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
        build_healing=experiment.build_healing,
        harness_preset=layers.harness_preset,
        sampling_preset=layers.sampling_preset,
        budget_preset=layers.budget_preset,
        # Execution queue fields (the executor claims lanes in sequence order).
        endpoint=None if endpoint.is_system else endpoint,
        lane=GenerationJob.Lane.SANDBOX if is_agent else GenerationJob.Lane.LLM,
        priority=experiment.priority,
        sequence=sequence,
        execution={"policy": policy},
    )
    if experiment.base_seed is not None:
        # The one cell-seed formula a retry re-derives too (job_cloning.cell_seed).
        job.experiment_seed = experiment_cell_seed(
            experiment.base_seed,
            agent=is_agent,
            condition_id=condition.id,
            app_id=app_req.id,
            repeat_index=cell.repeat_index,
        )
    try:
        if is_agent:
            apply_agent_snapshot_to_job(job, save=False)
        else:
            # Template mode only: the agent harness has its own budget and
            # retry loop, so continuation/repair do not apply to agent cells.
            apply_snapshot_to_job(
                job,
                save=False,
                continuation_limit=experiment.continuation_limit,
                enable_repair=experiment.enable_repair,
            )
    except Exception as exc:  # noqa: BLE001 — isolate one bad cell from the launch
        logger.warning("Snapshot build failed for experiment job %s: %s", job.id, exc)
        job.status = GenerationJob.Status.FAILED
        job.error_message = f"Snapshot build failed: {exc}"
        job.completed_at = timezone.now()
    return job


def launch_experiment(
    experiment: Experiment,
    user: AbstractUser,
    *,
    mode: str = LAUNCH,
    condition_ids: list[int] | None = None,
    kind: str | None = None,
) -> GenerationBatch:
    """Create jobs for every targeted cell lacking a live or completed job and hand them to the executor.

    A draft is launched (status running, ``launched_at``, the pre-registered
    baseline defaults to the first condition, local conditions pin their
    model digest). Any later call is a top-up: idempotent and resumable, it
    only creates jobs for cells that still need one (failed or cancelled
    cells, or cells added by raising ``repeats``), optionally limited to
    ``condition_ids``, so a top-up never duplicates completed work. A
    completed or cancelled experiment is reopened; a paused one stays paused
    (its jobs wait). Every targeted condition is resolved first; blocking
    problems (invalid presets, a stack mismatch, a harness a model cannot
    drive, an unusable endpoint, a missing key, ``digest_drift``) refuse the
    whole launch with ``ExperimentError`` before anything is written.

    ``kind`` overrides the batch kind (``schedule`` for schedule-fired top-ups);
    otherwise it is ``launch``, ``retry`` (only previously attempted cells) or
    ``top_up``.
    """
    if mode not in (LAUNCH, TOP_UP):
        raise ExperimentError(400, f"Unknown launch mode {mode!r}", "invalid_mode")
    with transaction.atomic():
        # Same row lock clone_job takes first, so a concurrent retry and this
        # top-up cannot both fill one cell (an extra, unpaired trial).
        Experiment.objects.select_for_update().filter(id=experiment.id).values_list("id", flat=True).first()
        experiment.refresh_from_db()
        if experiment.status == Experiment.Status.ARCHIVED:
            raise ExperimentError(400, "Cannot launch an archived experiment.", "archived")
        first = experiment.status == Experiment.Status.DRAFT

        base = _experiment_base(experiment)
        plans = plan_conditions(experiment, base=base)
        if not plans:
            raise ExperimentError(400, "Add at least one condition before launching.", "no_conditions")
        apps = list(experiment.app_requirements.order_by("id"))
        if not apps:
            raise ExperimentError(400, "Add at least one app spec before launching.", "no_apps")
        targeted = _targeted_plans(plans, None if first else condition_ids)
        target_conditions = [p.condition for p in targeted]

        access = launch_issues(user, [c.model for c in target_conditions])
        errors = launch_errors(targeted)
        errors += [i for i in access if i["blocking"]]
        if not first:
            errors += _digest_drift(target_conditions)
        if errors:
            raise ExperimentError(400, "; ".join(e["detail"] for e in errors), errors[0]["code"], errors)
        warnings = [i for i in access if not i["blocking"]]
        warnings += [i for i in _plan_issues(targeted) if not i["blocking"]]
        warnings += _model_issues(experiment, target_conditions)

        all_cells = _cells([p.condition for p in plans], apps, experiment.repeats)
        target_ids = {c.id for c in target_conditions}
        covered = _covered_cells(experiment)
        pending_cells = [c for c in all_cells if c.condition.id in target_ids and _cell_key(c) not in covered]
        sequence = _sequence(experiment, [_cell_key(c) for c in all_cells])

        system = system_endpoint()
        endpoint_of = {c.id: model_endpoint(c.model, system) for c in target_conditions}
        endpoints = list({e.pk: e for e in endpoint_of.values()}.values())
        policies = effective_policies(experiment, endpoints)
        swaps = {}
        for endpoint in endpoints:
            if endpoint.is_system:
                continue
            ordered = sorted(
                (_cell_key(c) for c in all_cells if endpoint_of.get(c.condition.id) == endpoint),
                key=sequence.__getitem__,
            )
            condition_models = {c.id: c.model_id for c in target_conditions}
            swaps[endpoint.slug] = _lru_loads(
                [condition_models[k[0]] for k in ordered],
                resident_limit(endpoint, experiment),
            )
        schedule = {
            "policy_requested": experiment.scheduling_policy or Experiment.SchedulingPolicy.AUTO,
            "policy_effective_by_endpoint": policies,
            "order_seed": _order_seed(experiment),
            "K_by_endpoint": {e.slug: resident_limit(e, experiment) for e in endpoints if not e.is_system},
            "estimated_swaps": swaps,
            "history": [],
        }

        now = timezone.now()
        update_fields = ["updated_at"]
        status_changed = False
        if first:
            experiment.status = Experiment.Status.RUNNING
            experiment.launched_at = now
            update_fields += ["status", "launched_at"]
            if experiment.analysis_plan and not experiment.analysis_plan_frozen_at:
                # Pre-registration: the plan is fixed before any result exists.
                experiment.analysis_plan_hash = plan_hash(experiment.analysis_plan)
                experiment.analysis_plan_frozen_at = now
                update_fields += ["analysis_plan_hash", "analysis_plan_frozen_at"]
            if experiment.baseline_condition_id is None:
                experiment.baseline_condition = plans[0].condition
                update_fields.append("baseline_condition")
            for condition in target_conditions:
                if is_local(condition.model) and condition.pinned_digest != condition.model.digest:
                    condition.pinned_digest = condition.model.digest
                    condition.save(update_fields=["pinned_digest"])
            status_changed = True
        elif experiment.status in (Experiment.Status.COMPLETED, Experiment.Status.CANCELLED) and pending_cells:
            # Top-up reopens a finished experiment (spec §C.8); a paused one stays paused.
            experiment.status = Experiment.Status.RUNNING
            experiment.completed_at = None
            experiment.cancelled_at = None
            update_fields += ["status", "completed_at", "cancelled_at"]
            status_changed = True

        if kind is None:
            if first:
                kind = GenerationBatch.Kind.LAUNCH
            else:
                attempted = _attempted_cells(experiment)
                retry = pending_cells and all(_cell_key(c) in attempted for c in pending_cells)
                kind = GenerationBatch.Kind.RETRY if retry else GenerationBatch.Kind.TOP_UP
        batch = GenerationBatch.objects.create(
            name=f"Experiment: {experiment.name}",
            mode=experiment.mode,
            # The jobs actually created (a top-up only creates the uncovered cells).
            total_jobs=len(pending_cells),
            created_by=user,
            experiment=experiment,
            kind=kind,
            request=_batch_request(
                experiment,
                base,
                plans,
                apps,
                cell_count=len(all_cells),
                pending_count=len(pending_cells),
                schedule=schedule,
                warnings=warnings,
            ),
        )

        layers_by_condition = {p.condition.id: p.layers for p in targeted}
        jobs = [
            _new_job(
                experiment,
                user,
                batch,
                cell,
                layers_by_condition[cell.condition.id],
                endpoint=endpoint_of[cell.condition.id],
                sequence=sequence[_cell_key(cell)],
                policy=policies[endpoint_of[cell.condition.id].slug],
            )
            for cell in pending_cells
        ]
        GenerationJob.objects.bulk_create(jobs, batch_size=_JOB_CHUNK)
        experiment.save(update_fields=update_fields)
        # Counts and status from the rows: an empty top-up is completed, a launch
        # whose every snapshot failed is failed.
        refresh_batch(batch)
        runnable = [j for j in jobs if j.status == GenerationJob.Status.PENDING]
        if runnable:
            enqueue_jobs(run_order(runnable, experiment))
        if status_changed:
            _publish_state(experiment)
    return batch


def launch_outcome(batch: GenerationBatch) -> dict[str, Any]:
    """``jobs_created``, ``policy_effective_by_endpoint`` and ``warnings`` of a launch batch (``LaunchResult``)."""
    request = batch.request if isinstance(batch.request, dict) else {}
    return {
        # Snapshot failures are created FAILED and run nothing.
        "jobs_created": batch.jobs.exclude(status=GenerationJob.Status.FAILED).count(),
        "policy_effective_by_endpoint": (request.get("schedule") or {}).get("policy_effective_by_endpoint") or {},
        "warnings": request.get("warnings") or [],
    }


# ── design: create, update, condition sets ────────────────────────────

# Design fields of ExperimentCreateV2 (``conditions``/``execution``/``post_run``/``schedules`` are handled apart).
_DESIGN_FIELDS = (
    "name",
    "slug",
    "description",
    "hypothesis",
    "repeats",
    "base_seed",
    "mode",
    "continuation_limit",
    "enable_repair",
    "build_healing",
)
# PATCH fields an explicit null clears; for the others it is ignored.
_NULLABLE_FIELDS = {"base_seed", "stack_slug", "sampling_preset", "budget_preset", "max_concurrency"}
# What may change once an experiment has launched (spec §F.1 PATCH).
_LAUNCHED_FIELDS = {
    "name",
    "description",
    "hypothesis",
    "priority",
    "max_concurrency",
    "infra_retries",
    "execution_options",
    "post_run",
    "repeats",
    "scheduling_policy",
}
_PRIORITY_RANGE = (-10, 10)
_USER_PRIORITY_RANGE = (-5, 0)
_MAX_RESIDENT_MODELS = 64


def _validated_plan(plan: dict | None) -> dict[str, Any]:
    try:
        return normalize_analysis_plan(plan)
    except PlanError as exc:
        raise ExperimentError(400, f"Invalid analysis plan: {exc}", "invalid_analysis_plan") from exc


def _validated_params(params: dict | None) -> dict[str, Any]:
    try:
        return validate_llm_params(params or {})
    except ValueError as exc:
        raise ExperimentError(400, str(exc), "invalid_llm_params") from exc


def _validated_agent(config: dict | None) -> dict[str, Any]:
    try:
        return validate_agent_config(config or {})
    except ValueError as exc:
        raise ExperimentError(400, str(exc), "invalid_agent_config") from exc


def visible_apps(user: AbstractUser, ids: list[int]) -> list[AppRequirementTemplate]:
    """The app specs *ids* names; 404 when any is unknown or another user's private spec.

    Never drops a hidden id silently: the experiment would run fewer apps
    than requested.
    """
    from backend.generation.services.packages import visible_app_templates_for

    wanted = set(ids)
    apps = list(visible_app_templates_for(user).filter(id__in=wanted).order_by("id"))
    missing = sorted(wanted - {app.id for app in apps})
    if missing:
        raise ExperimentError(404, f"Unknown app requirement id {', '.join(str(i) for i in missing)}", "unknown_app")
    return apps


def _stack_or_blank(raw: str | None, user: AbstractUser) -> str:
    """Canonical stack slug ("" = legacy per-profile stacks); 404 unknown, 403 unusable."""
    raw = (raw or "").strip()
    return resolve_stack(raw, user) if raw else ""


def clamp_priority(user: AbstractUser, priority: int) -> int:
    """Validated priority: -10..10; non-staff are clamped to -5..0 (fair share is per user first)."""
    lo, hi = _PRIORITY_RANGE
    if not lo <= int(priority) <= hi:
        raise ExperimentError(400, f"priority must be between {lo} and {hi}", "invalid_priority")
    if not getattr(user, "is_staff", False):
        lo, hi = _USER_PRIORITY_RANGE
    return max(lo, min(hi, int(priority)))


def normalize_execution_options(options: dict | None, current: dict | None = None) -> dict[str, Any]:
    """``{"warmup": bool, "max_resident_models": int|None}`` (K can only tighten the endpoint's)."""
    merged = {"warmup": True, "max_resident_models": None, **(current or {})}
    for key, value in (options or {}).items():
        if key == "warmup" and value is not None:
            merged["warmup"] = bool(value)
        elif key == "max_resident_models":
            if value is not None and not 1 <= int(value) <= _MAX_RESIDENT_MODELS:
                raise ExperimentError(
                    400,
                    f"max_resident_models must be between 1 and {_MAX_RESIDENT_MODELS}",
                    "invalid_execution_options",
                )
            merged["max_resident_models"] = int(value) if value is not None else None
        elif key not in ("warmup", "max_resident_models"):
            raise ExperimentError(400, f"Unknown execution option {key!r}", "invalid_execution_options")
    return merged


def default_post_run(mode: str) -> dict[str, Any]:
    return {
        "evaluate": True,
        "analysis": {"enabled": False, "tool_slugs": [], "only_passing": False},
        "report": {"enabled": False, "exclude_truncated": True},
    }


def normalize_post_run(spec: dict | None, mode: str) -> dict[str, Any]:
    """A validated ``Experiment.post_run`` (unknown analyzer slugs are a 400)."""
    from backend.analysis.models import AnalyzerTool

    out = default_post_run(mode)
    if not spec:
        return out
    if "evaluate" in spec and spec["evaluate"] is not None:
        out["evaluate"] = bool(spec["evaluate"])
    analysis = spec.get("analysis") or {}
    report = spec.get("report") or {}
    slugs = list(dict.fromkeys(str(s) for s in analysis.get("tool_slugs") or []))
    if slugs:
        from backend.analysis.models import AnalysisMode

        modes = dict(AnalyzerTool.objects.filter(slug__in=slugs).values_list("slug", "analysis_mode"))
        unknown = [s for s in slugs if s not in modes]
        if unknown:
            raise ExperimentError(400, f"Unknown analyzer {', '.join(unknown)}", "unknown_analyzer")
        # Post-run analysis runs on the generated source; dynamic analyzers
        # need a deployed, running app, which post-run never starts.
        dynamic = [s for s in slugs if modes[s] == AnalysisMode.DYNAMIC]
        if dynamic:
            raise ExperimentError(
                400,
                f"Dynamic analyzer(s) {', '.join(dynamic)} need a running app; post-run analysis is static only.",
                "dynamic_analyzer",
            )
    enabled = bool(analysis.get("enabled", False))
    if enabled and not slugs:
        raise ExperimentError(400, "Post-run analysis needs at least one analyzer (tool_slugs).", "no_analyzers")
    out["analysis"] = {"enabled": enabled, "tool_slugs": slugs, "only_passing": bool(analysis.get("only_passing"))}
    out["report"] = {
        "enabled": bool(report.get("enabled", False)),
        "exclude_truncated": bool(report.get("exclude_truncated", True)),
    }
    return out


def effective_post_run(experiment: Experiment) -> dict[str, Any]:
    """``experiment.post_run`` over the defaults (rows created before the field are ``{}``)."""
    stored = experiment.post_run if isinstance(experiment.post_run, dict) else {}
    out = default_post_run(experiment.mode)
    if "evaluate" in stored:
        out["evaluate"] = bool(stored["evaluate"])
    out["analysis"].update(stored.get("analysis") or {})
    out["report"].update(stored.get("report") or {})
    return out


def _apply_execution(experiment: Experiment, spec: dict | None, user: AbstractUser) -> None:
    if not spec:
        if experiment._state.adding:  # noqa: SLF001
            experiment.execution_options = normalize_execution_options(None)
        return
    if spec.get("scheduling_policy") is not None:
        experiment.scheduling_policy = spec["scheduling_policy"]
    if spec.get("priority") is not None:
        experiment.priority = clamp_priority(user, spec["priority"])
    if "max_concurrency" in spec:
        experiment.max_concurrency = spec["max_concurrency"]
    if spec.get("infra_retries") is not None:
        experiment.infra_retries = spec["infra_retries"]
    experiment.execution_options = normalize_execution_options(spec.get("options"), experiment.execution_options)


@dataclass
class ConditionChoice:
    """One requested condition before it is saved (from ``cross``, ``extra`` or a clone)."""

    model: LLMModel
    profile: GenerationProfile | None
    harness: str  # the stored harness name ("" = the experiment's default)
    harness_preset: RunPreset | None
    label: str
    param_overrides: dict[str, Any]
    ablation: bool = False

    @property
    def key(self) -> str:
        return choice_key(
            self.model.pk,
            self.harness_preset.pk if self.harness_preset else None,
            self.harness,
            self.profile.pk if self.profile else None,
        )


def choice_key(model_id: int, preset_id: int | None, harness: str | None, profile_id: int | None) -> str:
    """The wire key of a condition choice (``skipped[].key``, baseline matching)."""
    return f"m{model_id}:p{preset_id or '-'}:h{harness or '-'}:g{profile_id or '-'}"


@dataclass
class ConditionSetResult:
    conditions: list[ExperimentCondition]
    skipped: list[dict[str, str]]
    baseline: ExperimentCondition | None


class _ConditionResolver:
    """Resolves condition ingredients for one user and experiment, caching each lookup (O(N) per set)."""

    def __init__(self, experiment: Experiment, user: AbstractUser) -> None:
        self.experiment = experiment
        self.user = user
        self.is_agent = experiment.mode == GenerationJob.Mode.AGENT
        self._models: dict[int, LLMModel] = {}
        self._presets: dict[int, RunPreset] = {}
        self._profiles: dict[int, GenerationProfile] = {}

    def models(self, ids: list[int]) -> None:
        wanted = {int(i) for i in ids} - set(self._models)
        if not wanted:
            return
        found = {
            m.pk: m for m in LLMModel.objects.visible_to(self.user).filter(pk__in=wanted).select_related("endpoint")
        }
        missing = sorted(wanted - set(found))
        if missing:
            raise ExperimentError(404, f"Unknown model id {', '.join(str(i) for i in missing)}", "unknown_model")
        self._models.update(found)

    def model(self, model_id: int) -> LLMModel:
        self.models([model_id])
        return self._models[int(model_id)]

    def preset(self, preset_id: int) -> RunPreset:
        if not self.is_agent:
            raise ExperimentError(400, "A harness preset only applies to agent-mode experiments.", "agent_only")
        if preset_id not in self._presets:
            self._presets[preset_id] = experiment_preset(self.user, preset_id, "harness")
        return self._presets[preset_id]

    def harness(self, name: str) -> str:
        if not self.is_agent:
            raise ExperimentError(400, "A harness only applies to agent-mode experiments.", "agent_only")
        try:
            get_harness(name)
        except ValueError as exc:
            raise ExperimentError(400, str(exc), "unknown_harness") from exc
        return name

    def profile(self, profile_id: int) -> GenerationProfile:
        if profile_id not in self._profiles:
            profile = GenerationProfile.objects.filter(
                Q(is_system=True) | Q(created_by=self.user),
                id=profile_id,
                is_archived=False,
            ).first()
            if profile is None:
                raise ExperimentError(404, f"Unknown profile id {profile_id}", "unknown_profile")
            self._profiles[profile_id] = profile
        return self._profiles[profile_id]

    def choice(
        self,
        model_id: int,
        *,
        preset_id: int | None = None,
        harness: str | None = None,
        profile_id: int | None = None,
        label: str | None = None,
        param_overrides: dict | None = None,
        ablation: bool = False,
    ) -> ConditionChoice:
        model = self.model(model_id)
        preset = self.preset(int(preset_id)) if preset_id is not None else None
        name = (harness or "").strip()
        if preset is not None:
            preset_harness = preset_config_for_use(preset, "harness")["harness"]
            if name and name != preset_harness:
                raise ExperimentError(
                    400,
                    f"harness {name!r} does not match harness preset {preset.slug} ({preset_harness!r})",
                    "harness_preset_mismatch",
                )
            name = preset_harness
        elif name:
            name = self.harness(name)
        profile = self.profile(int(profile_id)) if profile_id is not None else None
        if profile is None and not self.is_agent:
            raise ExperimentError(400, "Scaffolding conditions need a profile (profile_ids).", "profile_required")
        overrides = _validated_params(param_overrides)
        return ConditionChoice(
            model=model,
            profile=profile,
            harness=name,
            harness_preset=preset,
            label=(label or "").strip() or _auto_label(model, name, preset, profile, overrides),
            param_overrides=overrides,
            ablation=ablation,
        )


def _auto_label(
    model: LLMModel,
    harness: str,
    preset: RunPreset | None,
    profile: GenerationProfile | None,
    overrides: dict[str, Any],
) -> str:
    """``"{model} · {harness}@{preset}"`` (+ profile, + ``" · ±{hash6}"`` for parameter ablations)."""
    parts = [model.model_name]
    if preset is not None:
        parts.append(f"{harness}@{preset.slug}")
    elif harness:
        parts.append(harness)
    if profile is not None:
        parts.append(f"{profile.slug}@v{profile.version}")
    label = " · ".join(parts)
    if overrides:
        label += f" · ±{hashlib.sha256(canonical_json(overrides).encode()).hexdigest()[:6]}"
    return label[:200]


def _expand_spec(resolver: _ConditionResolver, spec: dict[str, Any]) -> tuple[list[ConditionChoice], str | None]:
    """``cross`` as a product, then ``extra``; returns the choices and the baseline's choice key."""
    cross = spec.get("cross") or {}
    model_ids = [int(i) for i in cross.get("model_ids") or []]
    resolver.models(model_ids + [int(e["model_id"]) for e in spec.get("extra") or []])
    preset_ids = [int(i) for i in cross.get("harness_preset_ids") or []]
    harnesses = [str(h) for h in cross.get("harnesses") or []]
    profile_ids = [int(i) for i in cross.get("profile_ids") or []]
    if resolver.is_agent:
        method_choices: list[tuple[int | None, str | None]] = [(p, None) for p in preset_ids]
        method_choices += [(None, h) for h in harnesses]
        method_choices = method_choices or [(None, None)]
        profiles: list[int | None] = list(profile_ids) or [None]
    else:
        if preset_ids or harnesses:
            raise ExperimentError(400, "A harness only applies to agent-mode experiments.", "agent_only")
        if model_ids and not profile_ids:
            raise ExperimentError(400, "Scaffolding conditions need a profile (profile_ids).", "profile_required")
        method_choices = [(None, None)]
        profiles = list(profile_ids)
    choices = [
        resolver.choice(model_id, preset_id=preset_id, harness=harness, profile_id=profile_id)
        for model_id in model_ids
        for preset_id, harness in method_choices
        for profile_id in profiles
    ]
    for extra in spec.get("extra") or []:
        choices.append(
            resolver.choice(
                int(extra["model_id"]),
                preset_id=extra.get("harness_preset_id"),
                harness=extra.get("harness"),
                profile_id=extra.get("profile_id"),
                label=extra.get("label"),
                param_overrides=extra.get("param_overrides"),
                ablation=True,
            ),
        )
    baseline = spec.get("baseline")
    baseline_key = None
    if baseline:
        b = resolver.choice(
            int(baseline["model_id"]),
            preset_id=baseline.get("harness_preset_id"),
            harness=baseline.get("harness"),
            profile_id=baseline.get("profile_id"),
        )
        baseline_key = b.key
    return choices, baseline_key


def _identity(experiment: Experiment, condition: ExperimentCondition, base: _ExperimentBase) -> tuple:
    """What a condition runs: two conditions with the same identity are one arm run twice."""
    layers = experiment_layers(experiment, condition, base=base)
    return (
        condition.model_id,
        condition.profile_id,
        layers.harness_name,
        canonical_json(layers.harness_options),
        canonical_json(condition.param_overrides or {}),
    )


def _row_key(condition: ExperimentCondition) -> tuple:
    """The ``generation_experimentcondition_config_uniq`` key (one row per model/profile/harness/preset)."""
    return (condition.model_id, condition.profile_id, condition.harness or "", condition.harness_preset_id)


def build_condition_set(
    experiment: Experiment,
    spec: dict[str, Any],
    user: AbstractUser,
    *,
    existing: list[ExperimentCondition] | None = None,
) -> ConditionSetResult:
    """Expand a ``ConditionSetSpec`` into unsaved conditions, deduped in O(N) against each other and *existing*.

    One ``experiment_layers`` call per candidate, then dicts keyed on the
    resolved identity and on the table's unique key. Duplicates are returned
    in ``skipped`` (``reason="duplicate"``); a profile whose stack the
    experiment cannot run is skipped as ``"ineligible"``. Unknown or invisible
    ids raise ``ExperimentError`` (404) before anything is written.
    """
    resolver = _ConditionResolver(experiment, user)
    choices, baseline_key = _expand_spec(resolver, spec)
    base = _experiment_base(experiment)
    seen: dict[tuple, str] = {}
    rows: dict[tuple, str] = {}
    for condition in existing or []:
        seen[_identity(experiment, condition, base)] = condition_label(condition)
        rows[_row_key(condition)] = condition_label(condition)
    created: list[ExperimentCondition] = []
    skipped: list[dict[str, str]] = []
    keys: dict[str, ExperimentCondition] = {}
    for choice in choices:
        if choice.profile is not None:
            problem = None
            if experiment.stack_slug:
                problem = stack_mismatch(experiment.stack_slug, choice.profile)
            if problem:
                skipped.append({"key": choice.key, "reason": "ineligible", "detail": problem})
                continue
        condition = ExperimentCondition(
            experiment=experiment,
            label=choice.label,
            profile=choice.profile,
            model=choice.model,
            harness=choice.harness,
            harness_preset=choice.harness_preset,
            param_overrides=choice.param_overrides,
        )
        identity = _identity(experiment, condition, base)
        if identity in seen:
            skipped.append(
                {
                    "key": choice.key,
                    "reason": "duplicate",
                    "detail": f"Runs the same configuration as {seen[identity]}",
                },
            )
            continue
        if _row_key(condition) in rows:
            skipped.append(
                {
                    "key": choice.key,
                    "reason": "duplicate",
                    "detail": f"{rows[_row_key(condition)]} already uses this model, profile and harness preset; "
                    "an experiment holds one condition per combination",
                },
            )
            continue
        seen[identity] = choice.label
        rows[_row_key(condition)] = choice.label
        keys.setdefault(choice.key, condition)
        created.append(condition)
    baseline = None
    if baseline_key is not None:
        baseline = keys.get(baseline_key)
        if baseline is None:
            existing_match = [c for c in existing or [] if _condition_key(c) == baseline_key]
            if not existing_match:
                raise ExperimentError(400, "The baseline must be one of the experiment's conditions.", "bad_baseline")
            baseline = existing_match[0]
    return ConditionSetResult(conditions=created, skipped=skipped, baseline=baseline)


def _condition_key(condition: ExperimentCondition) -> str:
    return choice_key(condition.model_id, condition.harness_preset_id, condition.harness, condition.profile_id)


def _save_conditions(conditions: list[ExperimentCondition]) -> None:
    try:
        with transaction.atomic():
            for condition in conditions:
                condition.save()
    except IntegrityError as exc:  # a concurrent create of the same condition
        raise ExperimentError(
            400,
            "A condition with this configuration already exists.",
            "duplicate_condition",
        ) from exc


def _draft_or_error(experiment: Experiment) -> None:
    if experiment.status != Experiment.Status.DRAFT:
        raise ExperimentError(
            400,
            "Only draft experiments can be edited. Clone the experiment to change its design.",
            "not_draft",
        )


def replace_conditions(experiment: Experiment, spec: dict[str, Any], user: AbstractUser) -> list[ExperimentCondition]:
    """``PUT …/conditions/``: the experiment's condition set becomes *spec* (draft only)."""
    _draft_or_error(experiment)
    result = build_condition_set(experiment, spec, user)
    with transaction.atomic():
        experiment.baseline_condition = None
        experiment.save(update_fields=["baseline_condition", "updated_at"])
        experiment.conditions.all().delete()
        _save_conditions(result.conditions)
        experiment.baseline_condition = result.baseline or (result.conditions[0] if result.conditions else None)
        experiment.save(update_fields=["baseline_condition", "updated_at"])
    return list(experiment.conditions.select_related("model__endpoint", "profile", "harness_preset"))


def add_conditions(experiment: Experiment, spec: dict[str, Any], user: AbstractUser) -> ConditionSetResult:
    """``POST …/conditions/bulk/``: append *spec*'s conditions (draft only).

    Duplicates and ineligible conditions are skipped and reported.
    """
    _draft_or_error(experiment)
    existing = _conditions(experiment)
    result = build_condition_set(experiment, spec, user, existing=existing)
    with transaction.atomic():
        _save_conditions(result.conditions)
        if result.baseline is not None:
            experiment.baseline_condition = result.baseline
        elif experiment.baseline_condition_id is None and result.conditions:
            experiment.baseline_condition = (existing or result.conditions)[0]
        experiment.save(update_fields=["baseline_condition", "updated_at"])
    return result


def _design_experiment(user: AbstractUser, data: dict[str, Any]) -> tuple[Experiment, list[AppRequirementTemplate]]:
    """An unsaved, validated experiment from a create payload (no writes)."""
    experiment = Experiment(created_by=user, **{k: data[k] for k in _DESIGN_FIELDS if k in data})
    experiment.stack_slug = _stack_or_blank(data.get("stack_slug"), user)
    experiment.sampling_preset = experiment_preset(user, data.get("sampling_preset_id"), "sampling")
    experiment.budget_preset = experiment_preset(user, data.get("budget_preset_id"), "budget")
    experiment.agent_config = _validated_agent(data.get("agent_config"))
    experiment.llm_defaults = _validated_params(data.get("llm_defaults"))
    _apply_execution(experiment, data.get("execution"), user)
    experiment.post_run = normalize_post_run(data.get("post_run"), experiment.mode)
    experiment.analysis_plan = _validated_plan(data.get("analysis_plan"))
    apps = visible_apps(user, data.get("app_requirement_ids") or [])
    check_design(experiment)
    return experiment, apps


def create_experiment(
    user: AbstractUser,
    data: dict[str, Any],
    *,
    cloned_from: Experiment | None = None,
    created_by_schedule: ExperimentSchedule | None = None,
) -> Experiment:
    """Create a draft with its conditions, post-run and schedules in one transaction.

    Everything is validated before the first write; an ``ExperimentError``
    leaves no row behind.
    """
    from backend.generation.services.experiment_automation import validate_schedule

    slug = data.get("slug") or ""
    if not slug:
        raise ExperimentError(400, "A slug is required.", "invalid_slug")
    if Experiment.objects.filter(created_by=user, slug=slug).exists():
        raise ExperimentError(400, f"You already have an experiment with slug {slug!r}", "duplicate_slug")
    experiment, apps = _design_experiment(user, data)
    experiment.cloned_from = cloned_from
    experiment.created_by_schedule = created_by_schedule
    spec = data.get("conditions") or {"cross": {"model_ids": []}}
    conditions = build_condition_set(experiment, spec, user)
    schedules = [validate_schedule(s) for s in data.get("schedules") or []]
    with transaction.atomic():
        try:
            with transaction.atomic():
                experiment.save()
        except IntegrityError as exc:
            raise ExperimentError(400, f"You already have an experiment with slug {slug!r}", "duplicate_slug") from exc
        if apps:
            experiment.app_requirements.set(apps)
        _save_conditions(conditions.conditions)
        baseline = conditions.baseline or (conditions.conditions[0] if conditions.conditions else None)
        if baseline is not None:
            experiment.baseline_condition = baseline
            experiment.save(update_fields=["baseline_condition", "updated_at"])
        for schedule in schedules:
            ExperimentSchedule.objects.create(experiment=experiment, created_by=user, **schedule)
    return experiment


def update_experiment(experiment: Experiment, user: AbstractUser, data: dict[str, Any]) -> Experiment:
    """PATCH semantics (spec §F.1): a draft takes every design field, a launched experiment only a few."""
    data = dict(data)
    launched = experiment.status != Experiment.Status.DRAFT
    if experiment.status == Experiment.Status.ARCHIVED:
        raise ExperimentError(400, "Archived experiments cannot be edited.", "archived")
    if launched:
        refused = sorted(k for k in data if k not in _LAUNCHED_FIELDS)
        if refused:
            frozen = "baseline_condition_id is frozen at launch; " if "baseline_condition_id" in refused else ""
            raise ExperimentError(
                400,
                f"{frozen}after launch only {', '.join(sorted(_LAUNCHED_FIELDS))} can change "
                f"(refused: {', '.join(refused)}). Clone the experiment to change its design.",
                "not_draft",
            )
        if "repeats" in data and data["repeats"] is not None and data["repeats"] < experiment.repeats:
            raise ExperimentError(400, "repeats can only increase after launch.", "repeats_decrease")
        policy = data.get("scheduling_policy")
        if policy is not None and policy != experiment.scheduling_policy:
            if experiment.status != Experiment.Status.PAUSED:
                raise ExperimentError(400, "Pause the experiment before changing its scheduling policy.", "not_paused")

    app_ids = data.pop("app_requirement_ids", None)
    apps = visible_apps(user, app_ids) if app_ids is not None else None
    if "llm_defaults" in data:
        data["llm_defaults"] = _validated_params(data["llm_defaults"])
    if "agent_config" in data:
        data["agent_config"] = _validated_agent(data["agent_config"])
    if "stack_slug" in data:
        data["stack_slug"] = _stack_or_blank(data["stack_slug"], user)
    for kind in ("sampling", "budget"):
        if f"{kind}_preset_id" in data:
            data[f"{kind}_preset"] = experiment_preset(user, data.pop(f"{kind}_preset_id"), kind)
    if "priority" in data and data["priority"] is not None:
        data["priority"] = clamp_priority(user, data["priority"])
    if "execution_options" in data:
        data["execution_options"] = normalize_execution_options(
            data["execution_options"],
            experiment.execution_options,
        )
    if "post_run" in data:
        data["post_run"] = normalize_post_run(data["post_run"], data.get("mode") or experiment.mode)
    if "analysis_plan" in data:
        data["analysis_plan"] = _validated_plan(data["analysis_plan"])
    baseline_set = "baseline_condition_id" in data
    baseline_id = data.pop("baseline_condition_id", None)
    if baseline_set and baseline_id is not None and not experiment.conditions.filter(id=baseline_id).exists():
        raise ExperimentError(400, "The baseline must be one of the experiment's conditions.", "bad_baseline")

    old_priority, old_policy = experiment.priority, experiment.scheduling_policy
    for field_name, value in data.items():
        if value is None and field_name not in _NULLABLE_FIELDS:
            continue
        setattr(experiment, field_name, value)
    if baseline_set:
        experiment.baseline_condition_id = baseline_id
    # The final state must still agree with the conditions (stack, mode).
    check_design(experiment)
    with transaction.atomic():
        experiment.save()
        if apps is not None:
            experiment.app_requirements.set(apps)
        if launched and experiment.priority != old_priority:
            _propagate_priority(experiment)
        if launched and experiment.scheduling_policy != old_policy:
            _resequence_pending(experiment, user, old_policy)
    return experiment


def _propagate_priority(experiment: Experiment) -> None:
    """Pending jobs and their queued LLM requests take the experiment's new priority (spec §B.3)."""
    from backend.inference.models import LLMRequest

    experiment.jobs.filter(status=GenerationJob.Status.PENDING).update(priority=experiment.priority)
    LLMRequest.objects.filter(job__experiment=experiment, status=LLMRequest.Status.QUEUED).update(
        priority=experiment.priority,
    )
    transaction.on_commit(wake)


def _latest_batch(experiment: Experiment) -> GenerationBatch | None:
    return experiment.batches.order_by("-created_at").first()


def _append_history(experiment: Experiment, entry: dict[str, Any]) -> None:
    batch = _latest_batch(experiment)
    if batch is None:
        return
    request = dict(batch.request or {})
    schedule = dict(request.get("schedule") or {})
    schedule["history"] = [*schedule.get("history", []), entry]
    request["schedule"] = schedule
    batch.request = request
    batch.save(update_fields=["request", "updated_at"])


def _resequence_pending(experiment: Experiment, user: AbstractUser, old_policy: str) -> None:
    """Re-order the pending jobs under the new policy (allowed while paused; spec §C.5)."""
    conditions = _conditions(experiment)
    apps = list(experiment.app_requirements.order_by("id"))
    sequence = _sequence(experiment, [_cell_key(c) for c in _cells(conditions, apps, experiment.repeats)])
    system = system_endpoint()
    endpoints = list(
        {model_endpoint(c.model, system).pk: model_endpoint(c.model, system) for c in conditions}.values(),
    )
    policies = effective_policies(experiment, endpoints)
    pending = list(
        experiment.jobs.filter(status=GenerationJob.Status.PENDING, condition__isnull=False).select_related(
            "condition__model__endpoint",
        ),
    )
    for job in pending:
        key = (job.condition_id, job.app_requirement_id, job.repeat_index)
        if key in sequence:
            job.sequence = sequence[key]
        execution = dict(job.execution or {})
        execution["policy"] = policies.get(model_endpoint(job.condition.model, system).slug, execution.get("policy"))
        job.execution = execution
    GenerationJob.objects.bulk_update(pending, ["sequence", "execution"], batch_size=_JOB_CHUNK)
    _append_history(
        experiment,
        {
            "event": "policy_changed",
            "from": old_policy,
            "to": experiment.scheduling_policy,
            "policy_effective_by_endpoint": policies,
            "pending_resequenced": len(pending),
            "at": timezone.now().isoformat(),
            "user": getattr(user, "username", ""),
        },
    )


# ── lifecycle: pause, resume, cancel, archive, delete, legacy requeue ──

_ACTIVE_STATUSES = (GenerationJob.Status.PENDING, GenerationJob.Status.RUNNING)
_TERMINAL = (Experiment.Status.COMPLETED, Experiment.Status.CANCELLED, Experiment.Status.ARCHIVED)


def _lock(experiment: Experiment) -> None:
    Experiment.objects.select_for_update().filter(id=experiment.id).values_list("id", flat=True).first()
    experiment.refresh_from_db()


def experiment_state(experiment: Experiment) -> dict[str, Any]:
    """``ExperimentState``: status plus how many jobs are still running / pending."""
    counts = dict(
        experiment.jobs.filter(status__in=_ACTIVE_STATUSES)
        .values("status")
        .annotate(n=Count("id"))
        .values_list("status", "n"),
    )
    return {
        "status": experiment.status,
        "pause_reason": experiment.pause_reason,
        "paused_at": experiment.paused_at,
        "running": counts.get(GenerationJob.Status.RUNNING, 0),
        "pending": counts.get(GenerationJob.Status.PENDING, 0),
    }


def pause_experiment(experiment: Experiment, reason: str = Experiment.PauseReason.USER) -> dict[str, Any]:
    """``running → paused``: the executor stops claiming its jobs; running jobs finish."""
    with transaction.atomic():
        _lock(experiment)
        if experiment.status != Experiment.Status.RUNNING:
            raise ExperimentError(
                400,
                f"Only running experiments can be paused (this one is {experiment.status}).",
                "not_running",
            )
        experiment.status = Experiment.Status.PAUSED
        experiment.pause_reason = reason
        experiment.paused_at = timezone.now()
        experiment.save(update_fields=["status", "pause_reason", "paused_at", "updated_at"])
        _publish_state(experiment)
    return experiment_state(experiment)


def resume_experiment(
    experiment: Experiment,
    user: AbstractUser,
    *,
    accept_digest_drift: bool = False,
) -> dict[str, Any]:
    """``paused → running`` and wake the executor.

    After a digest change (``pause_reason=digest_changed``) the caller must
    accept the drift: the pending jobs' snapshots are re-frozen to the new
    weights, the conditions re-pin the digest, and the change is recorded in
    the latest batch's schedule history (Results then flag the conditions
    ``mixed_fingerprints``).
    """
    with transaction.atomic():
        _lock(experiment)
        if experiment.status != Experiment.Status.PAUSED:
            raise ExperimentError(
                400,
                f"Only paused experiments can be resumed (this one is {experiment.status}).",
                "not_paused",
            )
        conditions = _conditions(experiment)
        drifted = _digest_drift(conditions)
        if drifted and not accept_digest_drift:
            raise ExperimentError(
                400,
                "Model weights changed since launch; resume with accept_digest_drift=true or clone the experiment.",
                "digest_drift",
                drifted,
            )
        if drifted:
            _refreeze_drifted(
                experiment,
                user,
                [c for c in conditions if c.id in {d["condition_id"] for d in drifted}],
            )
        experiment.status = Experiment.Status.RUNNING
        experiment.pause_reason = ""
        experiment.paused_at = None
        experiment.save(update_fields=["status", "pause_reason", "paused_at", "updated_at"])
        _publish_state(experiment)
        transaction.on_commit(wake)
    return experiment_state(experiment)


def _refreeze_drifted(experiment: Experiment, user: AbstractUser, conditions: list[ExperimentCondition]) -> None:
    now = timezone.now().isoformat()
    for condition in conditions:
        jobs = experiment.jobs.filter(status=GenerationJob.Status.PENDING, condition=condition).select_related(
            "model__endpoint",
            "app_requirement",
            "profile",
            "harness_preset",
            "sampling_preset",
            "budget_preset",
        )
        for job in jobs:
            # Same seed (job.experiment_seed), new weights: a new config_fingerprint.
            if job.mode == GenerationJob.Mode.AGENT:
                apply_agent_snapshot_to_job(job)
            else:
                apply_snapshot_to_job(
                    job,
                    continuation_limit=experiment.continuation_limit,
                    enable_repair=experiment.enable_repair,
                )
        _append_history(
            experiment,
            {
                "event": "digest_accepted",
                "condition_id": condition.id,
                "from": condition.pinned_digest,
                "to": condition.model.digest,
                "at": now,
                "user": getattr(user, "username", ""),
            },
        )
        condition.pinned_digest = condition.model.digest
        condition.save(update_fields=["pinned_digest"])


def cancel_experiment(experiment: Experiment, scope: str = "all") -> dict[str, Any]:
    """Cancel pending (and, for ``all``, running) jobs; ``all`` also ends the experiment as cancelled.

    Jobs already waiting for or in evaluation finish and are graded
    (``execution.transitions.cancel_jobs``).
    """
    if scope not in ("all", "pending"):
        raise ExperimentError(400, "scope must be 'all' or 'pending'", "invalid_scope")
    with transaction.atomic():
        _lock(experiment)
        if experiment.status in (Experiment.Status.DRAFT, Experiment.Status.COMPLETED, Experiment.Status.ARCHIVED):
            # A completed experiment has no active jobs; cancelling must not relabel its finished results.
            raise ExperimentError(400, f"A {experiment.status} experiment has nothing to cancel.", "not_active")
        counts = cancel_jobs(experiment.jobs.filter(status__in=_ACTIVE_STATUSES), scope=scope)
        if scope == "all" and experiment.status != Experiment.Status.CANCELLED:
            experiment.status = Experiment.Status.CANCELLED
            experiment.cancelled_at = timezone.now()
            experiment.pause_reason = ""
            experiment.save(update_fields=["status", "cancelled_at", "pause_reason", "updated_at"])
            _publish_state(experiment)
    return {
        **experiment_state(experiment),
        "cancelled_pending": int(counts.get("cancelled_pending", 0)),
        "signalled_running": int(counts.get("signalled_running", 0)),
        "finishing_evaluations": int(counts.get("finishing_evaluations", 0)),
    }


def archive_experiment(experiment: Experiment) -> Experiment:
    with transaction.atomic():
        _lock(experiment)
        if experiment.jobs.filter(status__in=_ACTIVE_STATUSES).exists():
            raise ExperimentError(400, "Cancel the experiment's active jobs before archiving it.", "active_jobs")
        experiment.status = Experiment.Status.ARCHIVED
        experiment.save(update_fields=["status", "updated_at"])
        experiment.schedules.update(enabled=False)
        _publish_state(experiment)
    return experiment


def delete_experiment(experiment: Experiment) -> None:
    """Drafts, or finished experiments without active jobs (their jobs stay, unlinked)."""
    if experiment.status not in (Experiment.Status.DRAFT, *_TERMINAL):
        raise ExperimentError(400, "Cancel the experiment before deleting it.", "active")
    if experiment.jobs.filter(status__in=_ACTIVE_STATUSES).exists():
        raise ExperimentError(400, "The experiment still has active jobs.", "active_jobs")
    experiment.delete()


def requeue_legacy_jobs(experiment: Experiment) -> dict[str, int]:
    """Hand thread-era PENDING/RUNNING rows (``queued_at IS NULL``) to the executor (their threads are gone)."""
    with transaction.atomic():
        _lock(experiment)
        legacy = list(
            experiment.jobs.select_for_update()
            .filter(queued_at__isnull=True, status__in=_ACTIVE_STATUSES)
            .order_by("repeat_index", "created_at"),
        )
        for job in legacy:
            job.status = GenerationJob.Status.PENDING
            job.phase = GenerationJob.Phase.NONE
            job.started_at = None
            job.lease_owner = ""
            job.lease_expires_at = None
        GenerationJob.objects.bulk_update(
            legacy,
            ["status", "phase", "started_at", "lease_owner", "lease_expires_at"],
            batch_size=_JOB_CHUNK,
        )
        if legacy:
            enqueue_jobs(legacy)
        batch_ids = {j.batch_id for j in legacy if j.batch_id}
        for batch in GenerationBatch.objects.filter(id__in=batch_ids):
            refresh_batch(batch)
    return {"requeued": len(legacy), "batches_refreshed": len(batch_ids)}


# ── clone ─────────────────────────────────────────────────────────────


def unique_clone_slug(user: AbstractUser, name: str, *, now=None) -> str:
    """``slugify(name)[:40]-YYYYMMDD-HHMM`` (+ ``-2``, ``-3``… on collision)."""
    now = timezone.localtime(now or timezone.now())
    stem = f"{(slugify(name)[:40] or 'experiment').strip('-')}-{now:%Y%m%d-%H%M}"
    taken = set(Experiment.objects.filter(created_by=user, slug__startswith=stem).values_list("slug", flat=True))
    if stem not in taken:
        return stem
    n = 2
    while f"{stem}-{n}" in taken:
        n += 1
    return f"{stem}-{n}"


def design_payload(experiment: Experiment) -> dict[str, Any]:
    """*experiment*'s design as an ``ExperimentCreateV2`` body (conditions verbatim, as ``extra``)."""
    conditions = _conditions(experiment)
    baseline = next((c for c in conditions if c.id == experiment.baseline_condition_id), None)

    def choice(c: ExperimentCondition) -> dict[str, Any]:
        return {
            "model_id": c.model_id,
            "harness_preset_id": c.harness_preset_id,
            "harness": c.harness or None,
            "profile_id": c.profile_id,
        }

    return {
        "name": experiment.name,
        "slug": experiment.slug,
        "description": experiment.description,
        "hypothesis": experiment.hypothesis,
        "app_requirement_ids": list(experiment.app_requirements.values_list("id", flat=True)),
        "repeats": experiment.repeats,
        "base_seed": experiment.base_seed,
        "mode": experiment.mode,
        "stack_slug": experiment.stack_slug or None,
        "sampling_preset_id": experiment.sampling_preset_id,
        "budget_preset_id": experiment.budget_preset_id,
        "agent_config": dict(experiment.agent_config or {}),
        "continuation_limit": experiment.continuation_limit,
        "enable_repair": experiment.enable_repair,
        "build_healing": experiment.build_healing,
        "llm_defaults": dict(experiment.llm_defaults or {}),
        "conditions": {
            "cross": {"model_ids": []},
            "extra": [
                {**choice(c), "label": c.label, "param_overrides": dict(c.param_overrides or {})} for c in conditions
            ],
            "baseline": choice(baseline) if baseline is not None else None,
        },
        "execution": {
            "scheduling_policy": experiment.scheduling_policy,
            "priority": experiment.priority,
            "max_concurrency": experiment.max_concurrency,
            "infra_retries": experiment.infra_retries,
            "options": dict(experiment.execution_options or {}),
        },
        "post_run": effective_post_run(experiment),
        "analysis_plan": dict(experiment.analysis_plan or {}),
        "schedules": [
            {
                "action": s.action,
                "cron": s.cron,
                "timezone": s.timezone,
                "params": dict(s.params or {}),
                "skip_if_active": s.skip_if_active,
                "enabled": s.enabled,
            }
            for s in experiment.schedules.all()
        ],
    }


def clone_experiment(
    experiment: Experiment,
    user: AbstractUser,
    *,
    name: str | None = None,
    slug: str | None = None,
    include_schedules: bool = False,
    include_post_run: bool = True,
    overrides: dict[str, Any] | None = None,
    created_by_schedule: ExperimentSchedule | None = None,
) -> Experiment:
    """A new draft with *experiment*'s design (``cloned_from`` set).

    Conditions point at the same ``LLMModel`` rows, whose digests are the
    current ones (a launch pins them again). ``overrides`` is a partial
    create body applied over the copied design (``conditions`` replaces the
    whole set).
    """
    data = design_payload(experiment)
    overrides = dict(overrides or {})
    data.update({k: v for k, v in overrides.items() if k not in ("name", "slug")})
    data["name"] = (name or overrides.get("name") or f"{experiment.name} (copy)")[:200]
    data["slug"] = slug or overrides.get("slug") or unique_clone_slug(user, data["name"])
    if not include_schedules and "schedules" not in overrides:
        data["schedules"] = []
    if not include_post_run and "post_run" not in overrides:
        data["post_run"] = None
    return create_experiment(user, data, cloned_from=experiment, created_by_schedule=created_by_schedule)


# ── read models: list, detail, status, cells ──────────────────────────

PROGRESS_KEYS = ("pending", "running", "eval_queued", "evaluating", "completed", "failed", "cancelled")
_LIST_ORDERINGS = {
    "-updated_at": ("-updated_at",),
    "name": ("name", "-updated_at"),
    "-launched_at": ("-launched_at", "-updated_at"),
}
_STATUS_GROUPS = {
    "active": (Experiment.Status.RUNNING, Experiment.Status.PAUSED),
    "finished": (Experiment.Status.COMPLETED, Experiment.Status.CANCELLED),
}
MAX_PER_PAGE = 100

_CURRENT_PROGRESS_SQL = """
SELECT cur.experiment_id, cur.status, cur.phase, count(*)
FROM (
    SELECT DISTINCT ON (experiment_id, condition_id, app_requirement_id, repeat_index)
           experiment_id, status, phase
    FROM generation_generationjob
    WHERE experiment_id = ANY(%s)
      AND condition_id IS NOT NULL AND app_requirement_id IS NOT NULL AND repeat_index IS NOT NULL
    ORDER BY experiment_id, condition_id, app_requirement_id, repeat_index, created_at DESC
) cur
GROUP BY 1, 2, 3
"""
_COST_DURATION_SQL = """
SELECT experiment_id,
       COALESCE(SUM(CASE WHEN jsonb_typeof(metrics->'cost') = 'number' THEN (metrics->>'cost')::float END), 0),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_seconds)
           FILTER (WHERE status = 'completed' AND duration_seconds IS NOT NULL)
FROM generation_generationjob
WHERE experiment_id = ANY(%s)
GROUP BY 1
"""


def progress_key(status: str, phase: str) -> str:
    """The ``ProgressCounts`` bucket of a job (legacy ``queued`` rows wait like pending ones)."""
    if status == GenerationJob.Status.RUNNING:
        if phase == GenerationJob.Phase.EVAL_QUEUED:
            return "eval_queued"
        if phase == GenerationJob.Phase.EVALUATING:
            return "evaluating"
        if phase == GenerationJob.Phase.QUEUED:
            return "pending"
        return "running"
    return status if status in PROGRESS_KEYS else "pending"


def _progress_for(experiment_ids: list) -> dict[Any, dict[str, int]]:
    from django.db import connection

    out: dict[Any, dict[str, int]] = {eid: dict.fromkeys(PROGRESS_KEYS, 0) for eid in experiment_ids}
    if not experiment_ids:
        return out
    with connection.cursor() as cursor:
        cursor.execute(_CURRENT_PROGRESS_SQL, [list(experiment_ids)])
        for experiment_id, status, phase, n in cursor.fetchall():
            out.setdefault(experiment_id, dict.fromkeys(PROGRESS_KEYS, 0))[progress_key(status, phase)] += n
    return out


def _cost_duration_for(experiment_ids: list) -> dict[Any, tuple[float, float | None]]:
    from django.db import connection

    if not experiment_ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(_COST_DURATION_SQL, [list(experiment_ids)])
        return {
            eid: (float(cost or 0.0), float(median) if median is not None else None)
            for eid, cost, median in cursor.fetchall()
        }


def _endpoint_ref(endpoint: LLMEndpoint, live: dict[int, EndpointLive]) -> dict[str, Any]:
    state = live.get(endpoint.pk)
    return {
        "id": endpoint.pk,
        "slug": endpoint.slug,
        "name": endpoint.name,
        "kind": endpoint.kind,
        "online": bool(state and state.online),
    }


def _endpoints_for(experiment_ids: list) -> tuple[dict[Any, list[LLMEndpoint]], dict[int, EndpointLive]]:
    """Endpoints each experiment's conditions run on (system row for OpenRouter models) and their liveness."""
    pairs = (
        ExperimentCondition.objects.filter(experiment_id__in=experiment_ids)
        .values_list(
            "experiment_id",
            "model__endpoint_id",
        )
        .distinct()
    )
    endpoint_ids = {ep for _e, ep in pairs if ep is not None}
    system = system_endpoint()
    rows = {e.pk: e for e in LLMEndpoint.objects.filter(pk__in=endpoint_ids)}
    rows[system.pk] = system
    by_experiment: dict[Any, list[LLMEndpoint]] = defaultdict(list)
    for experiment_id, endpoint_id in pairs:
        endpoint = rows.get(endpoint_id) if endpoint_id is not None else system
        if endpoint is None:
            continue
        if endpoint.is_system and endpoint_id is not None:
            endpoint = system
        if endpoint not in by_experiment[experiment_id]:
            by_experiment[experiment_id].append(endpoint)
    return by_experiment, endpoint_liveness(list(rows.values()))


def _eta(remaining: int, median_s: float | None, parallel: int) -> float | None:
    if not remaining:
        return 0.0
    if median_s is None:
        return None
    return round(remaining * median_s / max(1, parallel), 1)


def list_experiments(
    user: AbstractUser,
    *,
    page: int = 1,
    per_page: int = 25,
    status: str | None = None,
    mode: str | None = None,
    search: str | None = None,
    endpoint: str | None = None,
    ordering: str = "-updated_at",
) -> dict[str, Any]:
    """``{items: ExperimentListItem[], total, page, pages}`` of *user*'s experiments.

    The page rows come from one query (subquery counts, no join blow-up);
    progress from one grouped aggregate over the current attempt of each cell.
    """
    per_page = max(1, min(MAX_PER_PAGE, int(per_page)))
    page = max(1, int(page))
    qs = Experiment.objects.filter(created_by=user)
    if status:
        if status in _STATUS_GROUPS:
            qs = qs.filter(status__in=_STATUS_GROUPS[status])
        elif status in Experiment.Status.values:
            qs = qs.filter(status=status)
        else:
            raise ExperimentError(400, f"Unknown status filter {status!r}", "invalid_filter")
    if mode:
        qs = qs.filter(mode=mode)
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(slug__icontains=search) | Q(description__icontains=search))
    if endpoint:
        if endpoint == SYSTEM_ENDPOINT_SLUG:
            matching = ExperimentCondition.objects.filter(
                Q(model__endpoint__isnull=True) | Q(model__endpoint__is_system=True),
            )
        elif endpoint == "local":
            matching = ExperimentCondition.objects.filter(model__endpoint__is_system=False)
        else:
            matching = ExperimentCondition.objects.filter(model__endpoint__slug=endpoint)
        qs = qs.filter(Exists(matching.filter(experiment=OuterRef("pk"))))
    if ordering not in _LIST_ORDERINGS:
        raise ExperimentError(400, f"Unknown ordering {ordering!r}", "invalid_filter")
    total = qs.count()
    pages = max(1, -(-total // per_page))
    rows = list(_annotated(qs).order_by(*_LIST_ORDERINGS[ordering])[(page - 1) * per_page : page * per_page])
    return {"items": list_items(rows), "total": total, "page": page, "pages": pages}


def _annotated(qs):
    from django.db.models import IntegerField
    from django.db.models import Subquery
    from django.db.models.functions import Coalesce

    def count_of(model_qs, column: str = "id"):
        return Coalesce(
            Subquery(
                model_qs.order_by().values("experiment_id").annotate(n=Count(column, distinct=True)).values("n")[:1],
                output_field=IntegerField(),
            ),
            0,
        )

    conditions = ExperimentCondition.objects.filter(experiment=OuterRef("pk"))
    apps = Experiment.app_requirements.through.objects.filter(experiment_id=OuterRef("pk"))
    enabled = ExperimentSchedule.objects.filter(experiment=OuterRef("pk"), enabled=True)
    return qs.annotate(
        n_conditions=count_of(conditions),
        n_models=count_of(conditions, "model_id"),
        n_apps=Coalesce(
            Subquery(
                apps.order_by().values("experiment_id").annotate(n=Count("id")).values("n")[:1],
                output_field=IntegerField(),
            ),
            0,
        ),
        has_enabled_schedule=Exists(enabled),
        next_schedule_at=Subquery(enabled.order_by("next_run_at").values("next_run_at")[:1]),
    )


def list_items(experiments: list[Experiment]) -> list[dict[str, Any]]:
    """``ExperimentListItem`` dicts for *experiments* (annotated by ``_annotated`` or not)."""
    ids = [e.id for e in experiments]
    progress = _progress_for(ids)
    costs = _cost_duration_for(ids)
    endpoints, live = _endpoints_for(ids)
    executor_settings = _executor_settings()
    items = []
    missing = [e.pk for e in experiments if not hasattr(e, "n_conditions")]
    annotated = {e.pk: e for e in _annotated(Experiment.objects.filter(pk__in=missing))} if missing else {}
    for row in experiments:
        experiment = annotated.get(row.pk, row)
        counts = progress.get(experiment.id) or dict.fromkeys(PROGRESS_KEYS, 0)
        cost, median_s = costs.get(experiment.id, (0.0, None))
        remaining = counts["pending"] + counts["running"] + counts["eval_queued"] + counts["evaluating"]
        caps = sum(job_cap(e) for e in endpoints.get(experiment.id, [])) or 1
        parallel = min(_slots_for(experiment.mode, executor_settings), caps)
        eta = _eta(remaining, median_s, parallel) if experiment.status == Experiment.Status.RUNNING else None
        items.append(
            {
                "id": experiment.id,
                "name": experiment.name,
                "slug": experiment.slug,
                "status": experiment.status,
                "pause_reason": experiment.pause_reason,
                "mode": experiment.mode,
                "scheduling_policy": experiment.scheduling_policy,
                "priority": experiment.priority,
                "created_at": experiment.created_at,
                "updated_at": experiment.updated_at,
                "launched_at": experiment.launched_at,
                "completed_at": experiment.completed_at,
                "condition_count": experiment.n_conditions,
                "model_count": experiment.n_models,
                "app_count": experiment.n_apps,
                "repeats": experiment.repeats,
                "total_cells": experiment.n_conditions * experiment.n_apps * experiment.repeats,
                "progress": counts,
                "cost_usd": round(cost, 4),
                "eta_seconds": eta,
                "endpoints": [_endpoint_ref(e, live) for e in endpoints.get(experiment.id, [])],
                "has_schedule": bool(experiment.has_enabled_schedule),
                "next_run_at": experiment.next_schedule_at,
            },
        )
    return items


def schedule_out(schedule: ExperimentSchedule) -> dict[str, Any]:
    return {
        "id": schedule.id,
        "action": schedule.action,
        "cron": schedule.cron,
        "timezone": schedule.timezone,
        "params": dict(schedule.params or {}),
        "skip_if_active": schedule.skip_if_active,
        "enabled": schedule.enabled,
        "next_run_at": schedule.next_run_at,
        "last_run_at": schedule.last_run_at,
        "last_result": dict(schedule.last_result or {}),
    }


def post_run_out(post_run: ExperimentPostRun | None) -> dict[str, Any] | None:
    if post_run is None:
        return None
    return {
        "id": post_run.id,
        "trigger": post_run.trigger,
        "status": post_run.status,
        "progress": dict(post_run.progress or {}),
        "analysis_run_ids": list(post_run.analysis_run_ids or []),
        "report_id": post_run.report_id,
        "error": post_run.error,
        "created_at": post_run.created_at,
        "completed_at": post_run.completed_at,
    }


def experiment_detail(experiment: Experiment) -> dict[str, Any]:
    """``ExperimentDetail``: the list row plus the design, conditions, batches, schedules and latest post-run."""
    item = list_items([experiment])[0]
    conditions = list(
        experiment.conditions.select_related("model__endpoint", "profile", "harness_preset").order_by("id"),
    )
    from backend.generation.api.schema import _named_preset_ref

    return {
        **item,
        "description": experiment.description,
        "hypothesis": experiment.hypothesis,
        "base_seed": experiment.base_seed,
        "agent_config": dict(experiment.agent_config or {}),
        "llm_defaults": dict(experiment.llm_defaults or {}),
        "stack_slug": experiment.stack_slug or None,
        "sampling_preset_ref": _named_preset_ref(experiment.sampling_preset),
        "budget_preset_ref": _named_preset_ref(experiment.budget_preset),
        "sampling_preset": experiment.sampling_preset_id,
        "budget_preset": experiment.budget_preset_id,
        "app_requirement_ids": list(experiment.app_requirements.order_by("id").values_list("id", flat=True)),
        "max_concurrency": experiment.max_concurrency,
        "infra_retries": experiment.infra_retries,
        "execution_options": normalize_execution_options(None, experiment.execution_options),
        "post_run": effective_post_run(experiment),
        "cloned_from": (
            {"id": experiment.cloned_from_id, "name": experiment.cloned_from.name}
            if experiment.cloned_from_id
            else None
        ),
        "created_by_schedule": (
            {"id": experiment.created_by_schedule_id, "action": experiment.created_by_schedule.action}
            if experiment.created_by_schedule_id
            else None
        ),
        "baseline_condition_id": experiment.baseline_condition_id,
        "conditions": conditions,
        "batches": [
            {"id": b.id, "kind": b.kind, "created_at": b.created_at, "total_jobs": b.total_jobs}
            for b in experiment.batches.order_by("-created_at")
        ],
        "schedules": [schedule_out(s) for s in experiment.schedules.order_by("id")],
        "latest_post_run": post_run_out(experiment.post_runs.order_by("-created_at").first()),
    }


# Current-attempt fields every rollup reads (JSON keys are extracted in SQL, not loaded whole).
CURRENT_FIELDS = (
    "id",
    "condition_id",
    "app_requirement_id",
    "repeat_index",
    "status",
    "phase",
    "attempt",
    "run_after",
    "started_at",
    "completed_at",
    "updated_at",
    "duration_seconds",
    "endpoint_id",
    "execution__prefetch_pending",
    "metrics__functional__pass_rate",
    "metrics__functional__passed",
    "metrics__cost",
    "metrics__total_tokens",
    "result_data__agent__termination",
    "result_data__agent__validity",
    "result_data__backend_truncated",
    "result_data__frontend_truncated",
    "resolved_bundle__config_fingerprint",
)


def current_jobs(experiment: Experiment, fields: tuple[str, ...] = CURRENT_FIELDS) -> list[dict[str, Any]]:
    """The current attempt (latest job) of every cell, as ``values()`` rows. One query."""
    return list(
        GenerationJob.objects.filter(
            experiment=experiment,
            condition__isnull=False,
            app_requirement__isnull=False,
            repeat_index__isnull=False,
        )
        .order_by("condition_id", "app_requirement_id", "repeat_index", "-created_at")
        .distinct("condition_id", "app_requirement_id", "repeat_index")
        .values(*fields),
    )


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def cell_code(row: dict[str, Any], now=None) -> str:
    """The one-letter ``CellsPayload`` state of a current-attempt row."""
    status, phase = row["status"], row["phase"]
    if status == GenerationJob.Status.PENDING:
        now = now or timezone.now()
        if row.get("run_after") is not None and row["run_after"] > now:
            return "b"
        return "w" if row.get("execution__prefetch_pending") else "p"
    if status == GenerationJob.Status.RUNNING:
        return {
            GenerationJob.Phase.EVAL_QUEUED: "q",
            GenerationJob.Phase.EVALUATING: "e",
            GenerationJob.Phase.QUEUED: "p",
        }.get(phase, "r")
    if status == GenerationJob.Status.COMPLETED:
        pass_rate = _as_float(row.get("metrics__functional__pass_rate"))
        if pass_rate is None:
            return "u"
        passed = row.get("metrics__functional__passed")
        return "c" if (passed is True or (passed is None and pass_rate >= 1.0)) else "f"
    return "k" if status == GenerationJob.Status.CANCELLED else "x"


def condition_endpoint_slug(condition: ExperimentCondition) -> str:
    return condition.model.endpoint.slug if is_local(condition.model) else SYSTEM_ENDPOINT_SLUG


def cells_payload(experiment: Experiment) -> dict[str, Any]:
    """``CellsPayload``: the compact grid of every cell's current attempt (~20 B per cell)."""
    conditions = list(experiment.conditions.select_related("model__endpoint").order_by("id"))
    apps = list(experiment.app_requirements.order_by("id"))
    ci = {c.id: i for i, c in enumerate(conditions)}
    ai = {a.id: i for i, a in enumerate(apps)}
    now = timezone.now()
    cells = []
    for row in current_jobs(experiment):
        if row["condition_id"] not in ci or row["app_requirement_id"] not in ai:
            continue
        pass_rate = _as_float(row.get("metrics__functional__pass_rate"))
        cells.append(
            [
                ci[row["condition_id"]],
                ai[row["app_requirement_id"]],
                row["repeat_index"],
                cell_code(row, now),
                str(row["id"]),
                round(pass_rate, 4) if pass_rate is not None else None,
                row["attempt"],
            ],
        )
    return {
        "conditions": [
            {
                "id": c.id,
                "label": condition_label(c),
                "model_id": c.model_id,
                "endpoint_slug": condition_endpoint_slug(c),
            }
            for c in conditions
        ],
        "apps": [{"id": a.id, "slug": a.slug, "name": a.name} for a in apps],
        "repeats": experiment.repeats,
        "cells": cells,
    }


def _ewma_per_hour(completed_at: list, now, hours: int = 6, alpha: float = 0.5) -> float:
    buckets = [0] * hours
    for at in completed_at:
        age = int((now - at).total_seconds() // 3600)
        if 0 <= age < hours:
            buckets[hours - 1 - age] += 1
    value = float(buckets[0])
    for count in buckets[1:]:
        value = alpha * count + (1 - alpha) * value
    return round(value, 2)


def _lane_usage(executor_settings) -> dict[str, dict[str, int]]:
    counts: Counter = Counter()
    for lane, phase, n in (
        GenerationJob.objects.filter(status=GenerationJob.Status.RUNNING, queued_at__isnull=False)
        .values_list("lane", "phase")
        .annotate(n=Count("id"))
    ):
        counts[(lane, phase)] += n
    post_used = ExperimentPostRun.objects.filter(status=ExperimentPostRun.Status.RUNNING).count()
    return {
        "sandbox": {
            "used": counts[(GenerationJob.Lane.SANDBOX, GenerationJob.Phase.RUNNING)],
            "total": int(executor_settings.sandbox_slots),
        },
        "eval": {
            "used": sum(n for (_lane, phase), n in counts.items() if phase == GenerationJob.Phase.EVALUATING),
            "total": int(executor_settings.eval_slots),
        },
        "llm": {
            "used": counts[(GenerationJob.Lane.LLM, GenerationJob.Phase.RUNNING)],
            "total": int(executor_settings.llm_job_threads),
        },
        "post": {"used": post_used, "total": int(executor_settings.post_run_slots)},
    }


def _endpoint_status_rows(
    experiment: Experiment,
    user: AbstractUser,
    conditions: list[ExperimentCondition],
    rows: list[dict[str, Any]],
    slots: int,
) -> list[dict[str, Any]]:
    """``ExperimentStatusV2.endpoints``: lanes this experiment uses, with other tenants' models only counted."""
    from backend.inference.access import is_endpoint_owner
    from backend.inference.models import LLMRequest

    system = system_endpoint()
    endpoints: dict[int, LLMEndpoint] = {}
    own_served: dict[int, set[str]] = defaultdict(set)
    own_models: dict[int, set[int]] = defaultdict(set)
    for condition in conditions:
        endpoint = model_endpoint(condition.model, system)
        endpoints.setdefault(endpoint.pk, endpoint)
        own_served[endpoint.pk].add(condition.model.served_model_name or condition.model.model_id)
        own_models[endpoint.pk].add(condition.model_id)
    if not endpoints:
        return []
    live = endpoint_liveness(list(endpoints.values()))
    now = timezone.now()
    out = []
    for endpoint in endpoints.values():
        scope = Q(endpoint=None) if endpoint.is_system else Q(endpoint=endpoint)
        running = list(
            GenerationJob.objects.filter(
                scope,
                status=GenerationJob.Status.RUNNING,
                phase=GenerationJob.Phase.RUNNING,
            ).values_list("created_by_id", "model_id", "model__model_name"),
        )
        active = len(running)
        held_by_others = sum(1 for owner, _m, _n in running if owner != experiment.created_by_id)
        resident_own = sorted({name for _o, mid, name in running if mid in own_models[endpoint.pk]})
        resident_others = len({mid for _o, mid, _n in running if mid not in own_models[endpoint.pk]})
        requests = LLMRequest.objects.filter(endpoint=endpoint)
        leased = requests.filter(status=LLMRequest.Status.LEASED).count()
        queued = requests.filter(status=LLMRequest.Status.QUEUED).count()
        recent = list(
            requests.filter(status=LLMRequest.Status.DONE, completed_at__gte=now - timedelta(minutes=15)).values_list(
                "completed_at",
                "upstream_ms",
                "eval_ms",
                "completion_tokens",
            ),
        )
        five = [r for r in recent if r[0] >= now - timedelta(minutes=5) and r[2]]
        eval_s = sum(r[2] for r in five) / 1000.0
        tokens_per_s = round(sum(r[3] for r in five) / eval_s, 1) if eval_s else None
        p50 = _median([r[1] / 1000.0 for r in recent if r[1] is not None])
        loaded = [m for m in (endpoint.loaded_models or []) if isinstance(m, dict)]
        loaded_own = [m for m in loaded if m.get("name") in own_served[endpoint.pk]]
        state = live[endpoint.pk]
        health = "offline" if not state.online else ("degraded" if endpoint.last_error else "ok")
        out.append(
            {
                "id": endpoint.pk,
                "slug": endpoint.slug,
                "name": endpoint.name,
                "kind": endpoint.kind,
                "connection": endpoint.connection,
                "online": state.online,
                "sessions": state.sessions,
                "health": health,
                "slots": (
                    {"used": active, "total": job_cap(endpoint)}
                    if endpoint.is_system
                    else {"used": leased, "total": endpoint.max_concurrency}
                ),
                "jobs": {"active": active, "cap": job_cap(endpoint), "held_by_others": held_by_others},
                "resident": {
                    "models": resident_own,
                    "others": resident_others,
                    "limit": resident_limit(endpoint, experiment),
                },
                "loaded": [
                    {
                        "name": m.get("name", ""),
                        "size_vram_gb": round(m["size_vram"] / 1e9, 2) if m.get("size_vram") else None,
                        "cpu_spill": bool(
                            m.get("size") and m.get("size_vram") is not None and m["size_vram"] < m["size"],
                        ),
                    }
                    for m in loaded_own
                ],
                "loaded_others": len(loaded) - len(loaded_own),
                "queued_requests": queued,
                "queued_jobs_this_experiment": sum(
                    1
                    for r in rows
                    if r["status"] == GenerationJob.Status.PENDING
                    and (r["endpoint_id"] == endpoint.pk or (endpoint.is_system and r["endpoint_id"] is None))
                ),
                "tokens_per_s_5m": tokens_per_s,
                "p50_request_s_15m": round(p50, 2) if p50 is not None else None,
                "circuit": "closed",
                "blocked_reason": None,
                "last_error": endpoint.last_error if is_endpoint_owner(user, endpoint) else None,
                "_parallel": max(1, min(job_cap(endpoint), slots)),
            },
        )
    return out


def experiment_status_v2(experiment: Experiment, user: AbstractUser) -> dict[str, Any]:
    """``ExperimentStatusV2``: rollups of the current attempt per cell, endpoints, lanes and the executor (no grid)."""
    conditions = list(experiment.conditions.select_related("model__endpoint", "harness_preset").order_by("id"))
    n_apps = experiment.app_requirements.count()
    rows = current_jobs(experiment)
    now = timezone.now()
    executor_settings = _executor_settings()
    slots = _slots_for(experiment.mode, executor_settings)

    counts = dict.fromkeys(PROGRESS_KEYS, 0)
    per_condition: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        counts[progress_key(row["status"], row["phase"])] += 1
        per_condition[row["condition_id"]].append(row)
    all_jobs = list(
        experiment.jobs.filter(condition__isnull=False).values_list(
            "condition_id",
            "app_requirement_id",
            "repeat_index",
            "attempt",
            "status",
            "completed_at",
        ),
    )
    jobs_per_cell = Counter((c, a, r) for c, a, r, *_rest in all_jobs)
    retried = sum(
        1
        for row in rows
        if row["attempt"] > 0
        or jobs_per_cell[(row["condition_id"], row["app_requirement_id"], row["repeat_index"])] > 1
    )
    completed_at = [at for *_k, status, at in all_jobs if status == GenerationJob.Status.COMPLETED and at]

    condition_rows = []
    for condition in conditions:
        mine = per_condition.get(condition.id, [])
        c_counts = dict.fromkeys(PROGRESS_KEYS, 0)
        for row in mine:
            c_counts[progress_key(row["status"], row["phase"])] += 1
        graded = [
            _as_float(r["metrics__functional__pass_rate"])
            for r in mine
            if r["status"] == GenerationJob.Status.COMPLETED
            and _as_float(r["metrics__functional__pass_rate"]) is not None
        ]
        durations = [
            r["duration_seconds"]
            for r in mine
            if r["status"] == GenerationJob.Status.COMPLETED and r["duration_seconds"]
        ]
        activity = [r["updated_at"] for r in mine if r["updated_at"]]
        condition_rows.append(
            {
                "condition_id": condition.id,
                "label": condition_label(condition),
                "model": {
                    "id": condition.model_id,
                    "name": condition.model.model_name,
                    "endpoint_slug": condition_endpoint_slug(condition),
                    "kind": condition.model.endpoint.kind
                    if is_local(condition.model)
                    else LLMEndpoint.Kind.OPENROUTER,
                },
                "harness": condition.harness,
                "counts": c_counts,
                "k_passed": sum(1 for r in mine if cell_code(r, now) == "c"),
                "n_graded": len(graded),
                "pass_rate": round(sum(graded) / len(graded), 4) if graded else None,
                "median_s": _median([float(d) for d in durations]),
                "tokens": int(sum(_as_float(r["metrics__total_tokens"]) or 0 for r in mine)),
                "cost_usd": round(sum(_as_float(r["metrics__cost"]) or 0.0 for r in mine), 4),
                "last_activity_at": max(activity) if activity else None,
            },
        )

    endpoints = _endpoint_status_rows(experiment, user, conditions, rows, slots)
    total_cells = len(conditions) * n_apps * experiment.repeats
    remaining_by_endpoint: Counter = Counter()
    durations_by_endpoint: dict[Any, list[float]] = defaultdict(list)
    for row in rows:
        key = row["endpoint_id"]
        if row["status"] in _ACTIVE_STATUSES:
            remaining_by_endpoint[key] += 1
        elif row["status"] == GenerationJob.Status.COMPLETED and row["duration_seconds"]:
            durations_by_endpoint[key].append(float(row["duration_seconds"]))
    eta: float | None = 0.0
    bottleneck: str | None = None
    worst = -1.0
    system = system_endpoint()
    for endpoint in endpoints:
        key = None if endpoint["id"] == system.pk else endpoint["id"]
        remaining = remaining_by_endpoint[key]
        if not remaining:
            continue
        median_s = _median(durations_by_endpoint[key])
        if not endpoint["online"]:
            # Not a capacity problem: the relay (or endpoint) is down.
            eta, bottleneck = None, f"endpoint_offline:{endpoint['slug']}"
            break
        estimate = _eta(remaining, median_s, endpoint["_parallel"])
        if estimate is None:
            eta = None
            continue
        if eta is not None and estimate > eta:
            eta = estimate
        if estimate > worst:
            worst = estimate
            bottleneck = f"endpoint:{endpoint['slug']}" if endpoint["jobs"]["cap"] < slots else None
    lanes = _lane_usage(executor_settings)
    in_flight = counts["running"] + counts["eval_queued"] + counts["evaluating"]
    if in_flight and experiment.mode == GenerationJob.Mode.AGENT and bottleneck is None:
        if lanes["sandbox"]["used"] >= lanes["sandbox"]["total"] and counts["pending"]:
            bottleneck = "sandbox"
        elif lanes["eval"]["used"] >= lanes["eval"]["total"] and counts["eval_queued"]:
            bottleneck = "eval"
    if experiment.status != Experiment.Status.RUNNING:
        eta = None
    for endpoint in endpoints:
        endpoint.pop("_parallel", None)

    latest_batch = _latest_batch(experiment)
    schedule = (latest_batch.request or {}).get("schedule") if latest_batch else None
    return {
        "status": experiment.status,
        "pause_reason": experiment.pause_reason,
        "policy_effective": (schedule or {}).get("policy_effective_by_endpoint") or experiment.scheduling_policy,
        "launched_at": experiment.launched_at,
        "updated_at": experiment.updated_at,
        "counts": counts,
        "cells": {
            "total": total_cells,
            "done": counts["completed"],
            "failed_final": counts["failed"] + counts["cancelled"],
            "in_flight": in_flight,
            "retried": retried,
        },
        "attempts_total": sum(attempt + 1 for *_k, attempt, _s, _c in all_jobs),
        "throughput": {
            "completed_last_15m": sum(1 for at in completed_at if at >= now - timedelta(minutes=15)),
            "completed_last_60m": sum(1 for at in completed_at if at >= now - timedelta(minutes=60)),
            "per_hour_ewma": _ewma_per_hour(completed_at, now),
        },
        "eta_seconds": eta,
        "bottleneck": bottleneck,
        "conditions": condition_rows,
        "endpoints": endpoints,
        "lanes": lanes,
        "executor": executor_state(),
        "latest_post_run": post_run_out(experiment.post_runs.order_by("-created_at").first()),
        # Thread-era rows the executor never claims ("Requeue legacy runs", execution.api.requeue_legacy_jobs).
        "legacy_pending": experiment.jobs.filter(
            queued_at__isnull=True,
            status__in=[GenerationJob.Status.PENDING, GenerationJob.Status.RUNNING],
        ).count(),
    }


# ── export ────────────────────────────────────────────────────────────

CSV_COLUMNS = (
    "job_id",
    "condition_id",
    "condition_label",
    "model",
    "endpoint",
    "digest",
    "num_ctx",
    "app",
    "repeat_index",
    "attempt",
    "status",
    "experiment_seed",
    "run_fingerprint",
    "config_fingerprint",
    "harness",
    "termination",
    "pass_rate",
    "valid",
    "cost_usd",
    "total_tokens",
    "duration_seconds",
    "queue_seconds",
    "created_at",
    "completed_at",
)


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
            "endpoint",
        ).order_by("sequence", "created_at"),
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
            "build_healing": experiment.build_healing,
            "analysis_plan": experiment.analysis_plan,
            "analysis_plan_hash": experiment.analysis_plan_hash,
            "analysis_plan_frozen_at": (
                experiment.analysis_plan_frozen_at.isoformat() if experiment.analysis_plan_frozen_at else None
            ),
            "scheduling_policy": experiment.scheduling_policy,
            "baseline_condition_id": experiment.baseline_condition_id,
            "execution_options": experiment.execution_options,
            "infra_retries": experiment.infra_retries,
            "created_at": experiment.created_at.isoformat(),
            "launched_at": experiment.launched_at.isoformat() if experiment.launched_at else None,
        },
        "conditions": [
            {
                "id": c.id,
                "label": c.label,
                "display_label": condition_label(c),
                "model": c.model.model_id,
                "endpoint": condition_endpoint_slug(c),
                "pinned_digest": c.pinned_digest,
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
    source = snapshot.get("endpoint") or {}
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
        # Where it ran (frozen snapshot section; OpenRouter jobs have none).
        "endpoint": source.get("slug") or (job.endpoint.slug if job.endpoint_id else SYSTEM_ENDPOINT_SLUG),
        "digest": source.get("digest") or "",
        "num_ctx": source.get("num_ctx"),
        "attempt": job.attempt,
        "sequence": job.sequence,
        "execution": _public_execution(job.execution),
    }


def _public_execution(execution: Any) -> dict[str, Any]:
    """``job.execution`` without relay session details (owner/staff only; an export is shareable)."""
    data = dict(execution) if isinstance(execution, dict) else {}
    data.pop("relay_sessions", None)
    return data


def export_experiment_csv(experiment: Experiment) -> str:
    """One CSV row per job (every attempt row), columns ``CSV_COLUMNS``."""
    labels = {c.id: condition_label(c) for c in _conditions(experiment)}
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    jobs = experiment.jobs.select_related("model", "app_requirement", "endpoint").order_by("sequence", "created_at")
    for job in jobs:
        row = _export_job(job)
        metrics = job.metrics if isinstance(job.metrics, dict) else {}
        functional = metrics.get("functional") or {}
        validity = ((job.result_data or {}).get("agent") or {}).get("validity") or {}
        writer.writerow(
            {
                "job_id": row["id"],
                "condition_id": job.condition_id,
                "condition_label": labels.get(job.condition_id, ""),
                "model": job.model.model_id if job.model_id else "",
                "endpoint": row["endpoint"],
                "digest": row["digest"],
                "num_ctx": row["num_ctx"],
                "app": row["app_requirement_slug"],
                "repeat_index": job.repeat_index,
                "attempt": job.attempt,
                "status": job.status,
                "experiment_seed": job.experiment_seed,
                "run_fingerprint": row["run_fingerprint"],
                "config_fingerprint": row["config_fingerprint"],
                "harness": row["harness"],
                "termination": row["termination"],
                "pass_rate": functional.get("pass_rate"),
                "valid": validity.get("valid"),
                "cost_usd": metrics.get("cost"),
                "total_tokens": metrics.get("total_tokens"),
                "duration_seconds": job.duration_seconds,
                "queue_seconds": (job.execution or {}).get("queue_seconds"),
                "created_at": job.created_at.isoformat(),
                "completed_at": job.completed_at.isoformat() if job.completed_at else "",
            },
        )
    return buffer.getvalue()


class _PreviewRollback(Exception):  # noqa: N818 — control flow, never escapes
    pass


def preview_design(user: AbstractUser, data: dict[str, Any]) -> dict[str, Any]:
    """``POST /experiments/preview/``: the v2 preview of an unsaved create body, leaving no rows behind.

    The design is built inside a savepoint that is always rolled back, so the
    preview runs exactly the code paths a real create + launch runs.
    """
    import uuid

    payload: dict[str, Any] = {}
    body = {**data, "slug": f"preview-{uuid.uuid4().hex[:12]}", "schedules": []}
    try:
        with transaction.atomic():
            experiment = create_experiment(user, body)
            payload = preview_experiment_v2(experiment, user)
            raise _PreviewRollback
    except _PreviewRollback:
        pass
    return payload
