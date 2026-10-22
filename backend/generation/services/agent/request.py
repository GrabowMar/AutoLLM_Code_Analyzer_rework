"""Turn a ``POST /jobs/agent/`` request into a run plan (shared by create and preview).

The plan resolves everything a batch needs before any row is written: the
stack, tasks, models, the harness presets (the comparison axis), the sampling
and budget presets, and the job fields the presets' values are copied into:

* ``job.llm_params``      = legacy ``max_tokens`` -> profile ``llm_config`` ->
                            sampling preset ``llm_params`` -> inline ``llm_params``
* ``job.agent_config``    = budget preset -> sampling preset ``sampling_policy``
                            -> inline ``agent_config`` (+ the harness per job)
* ``job.harness_options`` = the harness preset's normalized options

Every (model, harness preset) cell is then resolved with the same resolver
the snapshot uses (:func:`~backend.generation.services.agent.snapshot.resolve_agent_run`),
so what the preview reports is what the jobs will freeze. Problems that make
the request itself malformed raise :class:`RequestError` (an HTTP error for
both endpoints); everything else is an :class:`Issue` -- create refuses on
any ``error`` issue, preview lists them all.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db.models import Q

from backend.generation.services.agent.config import default_agent_config
from backend.generation.services.agent.config import merge_agent_config
from backend.generation.services.agent.config import validate_agent_config
from backend.generation.services.agent.harnesses import default_harness_name
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.agent.snapshot import agent_llm_layers
from backend.generation.services.agent.snapshot import resolve_agent_run
from backend.generation.services.agent.snapshot import resolve_llm
from backend.generation.services.llm_params import merge_llm_params
from backend.generation.services.llm_params import validate_llm_params
from backend.generation.services.presets import builtin_default_config
from backend.generation.services.presets import preset_config_for_use
from backend.generation.services.presets import system_default_preset
from backend.generation.services.versioning import canonical_json
from backend.runtime.services.scaffolding import canonical_stack_slug
from backend.runtime.services.scaffolding import get_stack_row
from backend.runtime.services.scaffolding import is_known_stack_slug
from backend.runtime.services.stack_validation import stack_usable_by

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from backend.generation.models import AppRequirementTemplate
    from backend.generation.models import GenerationProfile
    from backend.generation.models import RunPreset
    from backend.generation.services.agent.harnesses.base import Harness
    from backend.llm_models.models import LLMModel

# Preview heuristics (documented in the warning texts).
COST_CAP_TOKENS_PER_REQUEST = 20_000
MIN_SECONDS_PER_REQUEST = 30
WHOLE_FILE_MIN_OUTPUT_TOKENS = 16_000
LARGE_BATCH_RUNS = 50
# Any int works: the preview only needs to know whether a seed is forwarded.
SEED_PLACEHOLDER = 0


class RequestError(Exception):
    """The request itself is malformed (unknown ids, conflicting fields, invalid inline values)."""

    def __init__(self, status: int, detail: str, code: str = "") -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code


@dataclass
class Issue:
    code: str
    severity: str  # "error" | "warn" | "info"
    detail: str
    model_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "severity": self.severity, "detail": self.detail}
        if self.model_id is not None:
            data["model_id"] = self.model_id
        return data


@dataclass
class AgentRequest:
    """The create/preview body, as plain values (see ``AgentJobCreateSchema``)."""

    model_ids: list[int] = field(default_factory=list)
    app_requirement_ids: list[int] = field(default_factory=list)
    description: str = ""
    stack_slug: str | None = None
    harness_preset_ids: list[int] = field(default_factory=list)
    harnesses: list[str] = field(default_factory=list)
    sampling_preset_id: int | None = None
    budget_preset_id: int | None = None
    llm_params: dict[str, Any] = field(default_factory=dict)
    agent_config: dict[str, Any] = field(default_factory=dict)
    max_tokens: int | None = None
    profile_id: int | None = None
    trials: int = 1
    base_seed: int | None = None


@dataclass
class HarnessChoice:
    harness: Harness
    preset: RunPreset | None
    options: dict[str, Any]

    def preset_ref(self) -> dict[str, Any] | None:
        if self.preset is None:
            return None
        return _preset_ref(self.preset)

    def label(self) -> str:
        return f"{self.preset.slug} v{self.preset.version}" if self.preset else f"{self.harness.name} (default)"


@dataclass
class AgentRunPlan:
    request: AgentRequest
    stack_slug: str
    app_reqs: list[AppRequirementTemplate]
    models: list[LLMModel]
    profile: GenerationProfile | None
    choices: list[HarnessChoice]
    sampling_preset: RunPreset | None
    budget_preset: RunPreset | None
    job_llm_params: dict[str, Any]
    llm_sources: dict[str, str]
    job_agent_config: dict[str, Any]
    agent_sources: dict[str, str]
    issues: list[Issue] = field(default_factory=list)
    model_rows: list[dict[str, Any]] = field(default_factory=list)

    # ── shape ──
    @property
    def description(self) -> str:
        return self.request.description

    @property
    def tasks(self) -> list[AppRequirementTemplate | None]:
        if self.app_reqs:
            return list(self.app_reqs)
        return [None] if self.description.strip() else []

    @property
    def run_count(self) -> int:
        return len(self.tasks) * len(self.models) * len(self.choices) * self.request.trials

    @property
    def graded(self) -> bool:
        """Only app specs carry the hidden conformance suite; a free-form task is not graded."""
        return bool(self.app_reqs)

    @property
    def job_max_tokens(self) -> int:
        # The column is NOT NULL; with no legacy max_tokens the column default
        # is the lowest layer, which is also the platform's output cap.
        return self.request.max_tokens if self.request.max_tokens is not None else 32000

    @property
    def effective_agent_config(self) -> dict[str, Any]:
        return merge_agent_config(self.job_agent_config)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    def agent_layers(self, choice: HarnessChoice) -> tuple[dict[str, Any], ...]:
        return ({**self.job_agent_config, "harness": choice.harness.name},)

    def llm_layers(self) -> tuple[dict[str, Any] | None, ...]:
        return agent_llm_layers(self.job_max_tokens, self.profile, self.job_llm_params)

    def batch_request(self, *, order_seed: int) -> dict[str, Any]:
        """What ``GenerationBatch.request`` records (resolved preset ids included)."""
        return {
            "mode": "agent",
            "base_seed": self.request.base_seed,
            "order_seed": order_seed,
            "trials": self.request.trials,
            "stack_slug": self.stack_slug,
            "app_requirement_ids": [a.id for a in self.app_reqs],
            "description": self.description,
            "model_ids": [m.id for m in self.models],
            "profile_id": self.profile.id if self.profile else None,
            "harnesses": [c.harness.name for c in self.choices],
            "harness_preset_ids": [c.preset.id for c in self.choices if c.preset is not None],
            "sampling_preset_id": self.sampling_preset.id if self.sampling_preset else None,
            "budget_preset_id": self.budget_preset.id if self.budget_preset else None,
            "llm_params": dict(self.request.llm_params),
            "agent_config": dict(self.request.agent_config),
            "max_tokens": self.request.max_tokens,
        }


# ── building the plan ─────────────────────────────────────────────────


def _visible_preset(user: AbstractUser, preset_id: int) -> RunPreset:
    from backend.generation.models import RunPreset
    from backend.generation.services.presets import visible_q

    preset = RunPreset.objects.filter(visible_q(user), id=preset_id).first()
    if preset is None:
        raise RequestError(404, f"Unknown preset id {preset_id}")
    return preset


def _applied_preset(
    user: AbstractUser,
    kind: str,
    preset_id: int | None,
    issues: list[Issue],
) -> tuple[RunPreset | None, dict[str, Any]]:
    """The preset of *kind* a request uses (explicit or the system default) and its config."""
    preset = _visible_preset(user, preset_id) if preset_id is not None else system_default_preset(kind)
    if preset is None:
        return None, builtin_default_config(kind)
    try:
        return preset, preset_config_for_use(preset, kind)
    except ValueError as exc:
        issues.append(Issue("invalid_preset", "error", str(exc)))
        return preset, builtin_default_config(kind)


def _harness_choices(request: AgentRequest, user: AbstractUser, issues: list[Issue]) -> list[HarnessChoice]:
    if request.harnesses and request.harness_preset_ids:
        raise RequestError(400, "Send harness_preset_ids or harnesses, not both", "conflicting_harness_fields")
    if request.agent_config.get("harness") and (request.harnesses or request.harness_preset_ids):
        raise RequestError(
            400,
            "agent_config.harness cannot be combined with harness_preset_ids or harnesses",
            "conflicting_harness_fields",
        )
    choices: list[HarnessChoice] = []
    if request.harness_preset_ids:
        for preset_id in dict.fromkeys(request.harness_preset_ids):
            preset = _visible_preset(user, preset_id)
            try:
                config = preset_config_for_use(preset, "harness")
            except ValueError as exc:
                issues.append(Issue("invalid_preset", "error", str(exc)))
                continue
            choices.append(HarnessChoice(get_harness(config["harness"]), preset, config["options"]))
        return choices
    names = list(dict.fromkeys(request.harnesses)) or [request.agent_config.get("harness") or default_harness_name()]
    for name in names:
        try:
            harness = get_harness(name)
        except ValueError as exc:
            raise RequestError(400, str(exc), "unknown_harness") from exc
        preset = system_default_preset("harness", name)
        options: dict[str, Any] = {}
        if preset is not None:
            try:
                options = preset_config_for_use(preset, "harness")["options"]
            except ValueError as exc:
                issues.append(Issue("invalid_preset", "error", str(exc)))
                continue
        choices.append(HarnessChoice(harness, preset, options))
    return choices


def _layered(layers: list[tuple[str, dict[str, Any] | None]]) -> tuple[dict[str, Any], dict[str, str]]:
    """Merge named layers (later wins, None unsets) and record which layer set each key."""
    merged = merge_llm_params(*(layer for _name, layer in layers))
    sources: dict[str, str] = {}
    for name, layer in layers:
        for key, value in (layer or {}).items():
            if value is None:
                sources.pop(key, None)
            else:
                sources[key] = name
    return merged, {k: sources[k] for k in merged if k in sources}


def _stack_slug(request: AgentRequest, profile: GenerationProfile | None, user: AbstractUser) -> str:
    raw = request.stack_slug or (profile.scaffolding_slug if profile else "") or settings.GENERATION_DEFAULT_STACK
    if not is_known_stack_slug(raw):
        raise RequestError(404, f"Unknown stack slug: {raw}", "unknown_stack")
    slug = canonical_stack_slug(raw)
    row = get_stack_row(slug)
    if row is not None and not stack_usable_by(row, user):
        raise RequestError(403, f"Stack {row.slug!r} is not approved for use yet.", "stack_not_usable")
    return slug


def plan_agent_request(request: AgentRequest, user: AbstractUser) -> AgentRunPlan:
    """Resolve *request* for *user* (``RequestError`` for malformed requests, see the module docstring)."""
    from backend.generation.models import GenerationProfile
    from backend.generation.services.packages.visibility import visible_app_templates_for
    from backend.llm_models.models import LLMModel

    try:
        inline_llm = validate_llm_params(request.llm_params)
    except ValueError as exc:
        raise RequestError(400, str(exc), "invalid_llm_params") from exc
    try:
        inline_agent = validate_agent_config(request.agent_config)
    except ValueError as exc:
        raise RequestError(400, str(exc), "invalid_agent_config") from exc
    request.llm_params, request.agent_config = inline_llm, inline_agent

    profile = None
    if request.profile_id is not None:
        visible = Q(is_system=True) | Q(created_by=user)
        profile = GenerationProfile.objects.filter(visible, id=request.profile_id).first()
        if profile is None:
            raise RequestError(404, "Unknown profile id", "unknown_profile")
    stack_slug = _stack_slug(request, profile, user)

    # Another user's private spec is "unknown" (404), never resolved into a
    # snapshot the caller could read back.
    app_reqs = list(visible_app_templates_for(user).filter(id__in=request.app_requirement_ids).order_by("id"))
    if len(app_reqs) != len(set(request.app_requirement_ids)):
        raise RequestError(404, "Unknown app requirement id", "unknown_app_requirement")
    models = list(LLMModel.objects.filter(id__in=request.model_ids).order_by("id"))
    if len(models) != len(set(request.model_ids)):
        raise RequestError(404, "Unknown model id", "unknown_model")

    issues: list[Issue] = []
    choices = _harness_choices(request, user, issues)
    sampling_preset, sampling_config = _applied_preset(user, "sampling", request.sampling_preset_id, issues)
    budget_preset, budget_config = _applied_preset(user, "budget", request.budget_preset_id, issues)

    job_llm_params, llm_sources = _layered(
        [
            ("max_tokens", {"max_tokens": request.max_tokens} if request.max_tokens is not None else {}),
            ("profile", dict(profile.llm_config or {}) if profile else {}),
            ("preset", sampling_config.get("llm_params") or {}),
            ("inline", inline_llm),
        ],
    )
    policy = {"sampling_policy": sampling_config["sampling_policy"]} if "sampling_policy" in sampling_config else {}
    agent_overrides = {k: v for k, v in inline_agent.items() if k != "harness"}
    job_agent_config, agent_sources = _layered(
        [("preset", budget_config), ("preset", policy), ("inline", agent_overrides)],
    )

    plan = AgentRunPlan(
        request=request,
        stack_slug=stack_slug,
        app_reqs=app_reqs,
        models=models,
        profile=profile,
        choices=choices,
        sampling_preset=sampling_preset,
        budget_preset=budget_preset,
        job_llm_params=job_llm_params,
        llm_sources=llm_sources,
        job_agent_config=job_agent_config,
        agent_sources=agent_sources,
        issues=issues,
    )
    _analyze(plan)
    return plan


# ── analysis (preview warnings, create gating) ────────────────────────


def _duplicate_choices(plan: AgentRunPlan) -> None:
    seen: dict[tuple[str, str], HarnessChoice] = {}
    for choice in plan.choices:
        key = (choice.harness.name, canonical_json(choice.options))
        if key in seen:
            plan.issues.append(
                Issue(
                    "duplicate_harness_config",
                    "error",
                    f"{seen[key].label()} and {choice.label()} run {choice.harness.label} with the same options; "
                    "pick one of them",
                ),
            )
        else:
            seen[key] = choice


def _batch_issues(plan: AgentRunPlan) -> None:
    request, agent, defaults = plan.request, plan.effective_agent_config, default_agent_config()
    if not plan.app_reqs and not plan.description.strip():
        plan.issues.append(Issue("no_tasks", "error", "Give at least one app spec or a task description"))
    if not plan.models:
        plan.issues.append(Issue("no_models", "error", "Pick at least one model"))
    if not plan.choices and not plan.errors:
        plan.issues.append(Issue("invalid_preset", "error", "No usable harness preset"))
    if not plan.app_reqs and plan.description.strip():
        plan.issues.append(
            Issue("ungraded_task", "warn", "A free-form task has no hidden conformance suite: runs are not graded"),
        )
    if agent["sampling_policy"] == "harness_native":
        plan.issues.append(
            Issue(
                "harness_native_sampling",
                "warn",
                "Sampling policy harness_native lets each harness send its own (per-model-family) sampling "
                "settings, so models are not sampled alike",
            ),
        )
    if agent["wall_clock_seconds"] / max(1, agent["max_llm_requests"]) < MIN_SECONDS_PER_REQUEST:
        plan.issues.append(
            Issue(
                "wall_clock_may_bind",
                "warn",
                f"Less than {MIN_SECONDS_PER_REQUEST} s of wall clock per allowed request: slow providers may hit "
                "the time limit before the request budget",
            ),
        )
    if plan.run_count > LARGE_BATCH_RUNS:
        plan.issues.append(Issue("large_batch", "warn", f"{plan.run_count} runs in one batch"))
    for choice in plan.choices:
        if choice.options:
            plan.issues.append(
                Issue(
                    "non_default_harness_options",
                    "info",
                    f"{choice.label()}: non-default {choice.harness.label} options "
                    f"({', '.join(f'{k}={v}' for k, v in sorted(choice.options.items()))})",
                ),
            )
    # What the brief states: the request budget, the wall clock in whole
    # minutes and the network policy (services/agent/brief.py).
    stated = ("max_llm_requests", "network_policy")
    if any(agent[k] != defaults[k] for k in stated) or round(agent["wall_clock_seconds"] / 60) != round(
        defaults["wall_clock_seconds"] / 60,
    ):
        plan.issues.append(
            Issue(
                "budget_changes_brief",
                "info",
                "The request budget, wall clock or network policy differs from the platform default; the brief "
                "states them, so these runs get a different prompt hash than default-budget runs",
            ),
        )
    if request.trials == 1:
        plan.issues.append(Issue("single_trial", "info", "One trial per cell: no estimate of run-to-run variance"))
    if request.base_seed is None:
        plan.issues.append(
            Issue("random_seeds", "info", "No base seed: every run draws its own seed (no common random numbers)"),
        )


def _model_issues(plan: AgentRunPlan) -> None:
    agent = plan.effective_agent_config
    tool_harnesses = [c.harness for c in plan.choices if c.harness.requires_tool_calling]
    for model in plan.models:
        run = resolve_llm(
            model=model,
            llm_layers=plan.llm_layers(),
            seed=SEED_PLACEHOLDER,
            sampling_policy=agent["sampling_policy"],
        )
        gateway = run["gateway"]
        dropped = {k: v for k, v in gateway["dropped_params"].items() if k != "seed"}
        tools_ok = bool(model.supports_function_calling) or not tool_harnesses
        plan.model_rows.append(
            {
                "model_id": model.id,
                "model_slug": model.model_id,
                "name": model.model_name,
                "provider": model.provider,
                "supports_tools": bool(model.supports_function_calling),
                "tool_harness_ok": tools_ok,
                "context_window": run["context_window"],
                "context_window_source": run["context_window_source"],
                "output_cap": run["output_cap"],
                "forced_params": {k: v for k, v in gateway["forced_params"].items() if k != "seed"},
                "dropped_params": dropped,
                "seed_forwarded": run["seed_forwarded"],
                "params_support": run["params_support"],
            },
        )
        if not tools_ok:
            names = sorted({h.label for h in tool_harnesses})
            verb = "needs" if len(names) == 1 else "need"
            plan.issues.append(
                Issue(
                    "models_without_tools",
                    "error",
                    f"{model.model_id} does not support tools; {', '.join(names)} {verb} tool-calling models",
                    model.id,
                ),
            )
        if dropped:
            plan.issues.append(
                Issue(
                    "params_dropped",
                    "warn",
                    f"{model.model_id} cannot take {', '.join(sorted(dropped))}; those params are not applied",
                    model.id,
                ),
            )
        if run["unverified_params"]:
            plan.issues.append(
                Issue(
                    "support_unknown",
                    "warn",
                    f"The catalog lists no supported parameters for {model.model_id}: "
                    f"{', '.join(run['unverified_params'])} "
                    f"{'is' if len(run['unverified_params']) == 1 else 'are'} forwarded unverified. "
                    "With the default routing a provider that cannot honour one fails the run rather than "
                    "ignoring it; re-sync the model catalog to check",
                    model.id,
                ),
            )
        if run["context_window_source"] == "fallback":
            plan.issues.append(
                Issue(
                    "context_window_fallback",
                    "warn",
                    f"{model.model_id} has no context window in the catalog; harnesses are told "
                    f"{run['context_window']:,} tokens",
                    model.id,
                ),
            )
        price = float(model.input_price_per_token or 0)
        if price * agent["max_llm_requests"] * COST_CAP_TOKENS_PER_REQUEST > float(agent["max_cost_usd"]):
            plan.issues.append(
                Issue(
                    "cost_cap_may_bind",
                    "warn",
                    f"At {COST_CAP_TOKENS_PER_REQUEST:,} input tokens per request, {agent['max_llm_requests']} "
                    f"requests of {model.model_id} cost more than the ${agent['max_cost_usd']} cap: the USD cap, "
                    "not the request budget, may end its runs",
                    model.id,
                ),
            )
        reasoning = run["llm"].get("reasoning")
        reasoning_tokens = reasoning.get("max_tokens") if isinstance(reasoning, dict) else None
        if isinstance(reasoning_tokens, int) and reasoning_tokens >= run["output_cap"]:
            plan.issues.append(
                Issue(
                    "reasoning_exceeds_output_cap",
                    "error",
                    f"reasoning.max_tokens {reasoning_tokens} is not below {model.model_id}'s output cap "
                    f"{run['output_cap']}",
                    model.id,
                ),
            )
        for choice in plan.choices:
            options = choice.harness.effective_options(choice.options)
            if options.get("edit_format") == "whole" and run["output_cap"] < WHOLE_FILE_MIN_OUTPUT_TOKENS:
                plan.issues.append(
                    Issue(
                        "whole_file_small_output",
                        "warn",
                        f"{choice.label()} rewrites whole files, but {model.model_id}'s output cap is "
                        f"{run['output_cap']} tokens: large files will be cut off",
                        model.id,
                    ),
                )
            if choice.harness.requires_tool_calling and not model.supports_function_calling:
                continue  # reported once above
            try:
                resolve_agent_run(
                    model=model,
                    stack_slug=plan.stack_slug,
                    agent_layers=plan.agent_layers(choice),
                    llm_layers=plan.llm_layers(),
                    harness_options=choice.options,
                    seed=SEED_PLACEHOLDER,
                )
            except (ValueError, KeyError) as exc:
                plan.issues.append(
                    Issue("resolution_failed", "error", f"{choice.label()} x {model.model_id}: {exc}", model.id),
                )


def _analyze(plan: AgentRunPlan) -> None:
    _duplicate_choices(plan)
    _batch_issues(plan)
    _model_issues(plan)


# ── preview payload ───────────────────────────────────────────────────


def _preset_ref(preset: RunPreset | None) -> dict[str, Any] | None:
    if preset is None:
        return None
    return {"id": preset.id, "slug": preset.slug, "version": preset.version, "name": preset.name}


def preview_payload(plan: AgentRunPlan, *, extra_issues: list[Issue] | None = None) -> dict[str, Any]:
    """The ``POST /jobs/agent/preview/`` body for *plan*."""
    agent = plan.effective_agent_config
    agent_view = {k: v for k, v in agent.items() if k != "harness"}
    issues = [*(extra_issues or []), *plan.issues]
    severity_rank = {"error": 0, "warn": 1, "info": 2}
    return {
        "run_count": plan.run_count,
        "task_count": len(plan.tasks),
        "model_count": len(plan.models),
        "harness_count": len(plan.choices),
        "trials": plan.request.trials,
        "stack_slug": plan.stack_slug,
        "graded": plan.graded,
        "agent_config": agent_view,
        "agent_config_sources": {k: plan.agent_sources.get(k, "default") for k in agent_view},
        "llm_params": {"effective": dict(plan.job_llm_params), "sources": dict(plan.llm_sources)},
        "presets": {"sampling": _preset_ref(plan.sampling_preset), "budget": _preset_ref(plan.budget_preset)},
        "harnesses": [
            {
                "preset": choice.preset_ref(),
                "harness": choice.harness.name,
                "label": choice.harness.label,
                "version": choice.harness.version(),
                "requires_tool_calling": choice.harness.requires_tool_calling,
                "effective_options": choice.harness.effective_options(choice.options),
                "non_default_options": sorted(choice.options),
            }
            for choice in plan.choices
        ],
        "models": plan.model_rows,
        "max_cost_usd_total": round(plan.run_count * float(agent["max_cost_usd"]), 4),
        "warnings": [i.to_dict() for i in sorted(issues, key=lambda i: severity_rank.get(i.severity, 3))],
    }
