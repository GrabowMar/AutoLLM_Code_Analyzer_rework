"""Effective policy resolution, clamps, knob classification and policy lint (spec section 4.3).

``effective_policy(base, overrides, model=, harness=, study_ctx=)`` turns a stored budget preset into what
one cell actually runs with:

1. per-study overrides are merged into the preset (dotted keys or a nested partial dict) and validated;
2. ``auto`` values are resolved (``study.budget_basis``, ``study.cap_basis``, ``routing.serving_tier``);
3. the clamps of 4.3 are applied, each recorded as a ``PolicyClamp`` (and a note where 4.3 names one);
4. the policy lint runs on the result.

``treatment_view(effective)`` is the class C and R subset **after** clamping, so two presets that clamp to
the same values hash equal: ``generation_policy_hash = H(treatment_view(effective))``. Class O values are
recorded in ``manifest.resources.effective`` and never hashed; class S rules enter ``prereg_hash``.

The lint only reports what it can see. ``lint(content)`` alone checks the content rules (reasoning
exclusivity, the call/context cap ratio, stop rules); passing a model, harness facts and a
``StudyPolicyContext`` adds the model, harness, slot and confirmatory rules. ``lint_study(...)`` adds the
cross-condition rules (``resources_vary_by_stack``, ``mixed_serving_tier``).
"""

from __future__ import annotations

import copy
import math
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from typing import Any
from typing import Literal

from django.conf import settings

from backend.generation import identity
from backend.generation.budget import constants as k
from backend.generation.budget.model_facts import ModelFacts
from backend.generation.budget.model_facts import is_model_alias
from backend.generation.budget.model_facts import model_facts
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import IssueFix
from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.plan import ExecutionOptions
from backend.generation.contracts.resources import KNOB_META
from backend.generation.contracts.resources import TIERS
from backend.generation.contracts.resources import EffectivePolicy
from backend.generation.contracts.resources import PolicyClamp
from backend.generation.contracts.resources import ResourcePolicyContent
from backend.generation.contracts.resources import knob_class
from backend.generation.contracts.resources import knob_values
from backend.generation.contracts.resources import preset_content
from backend.generation.contracts.resources import values_by_class

__all__ = [
    "AGENTIC_EXECUTORS",
    "ConditionInput",
    "ConditionPolicy",
    "HarnessInfo",
    "StudyPolicies",
    "StudyPolicyContext",
    "apply_overrides",
    "context_for_study",
    "diff_from_tier",
    "effective_policy",
    "execution_options",
    "explain",
    "generation_policy_hash",
    "harness_info",
    "knob_meta_payload",
    "lint",
    "lint_study",
    "parse_memory",
    "policy_content",
    "resolve_serving_tier",
    "resolve_study_policies",
    "summary_line",
    "treatment_view",
]

StudyKind = Literal["exploratory", "confirmatory"]
AGENTIC_EXECUTORS = frozenset({"agentic", "restricted_agent"})
_MEM_UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3}
_ANTHROPIC_MIN_REASONING = 1024
_CONTEXT_CALL_RATIO = 1.1


# =====================================================================================
# Inputs
# =====================================================================================


@dataclass(frozen=True)
class HarnessInfo:
    """The harness facts the policy code reads (from ``HarnessFacts``, or the name alone)."""

    name: str
    context_knob: str | None = None
    min_call_output_tokens: int = 0
    max_turns_flag: str | None = None
    known: bool = False  # False: only the name is known (facts not available yet)


def harness_info(harness: Any) -> HarnessInfo | None:
    """``HarnessInfo`` from an adapter (``.facts``), a ``HarnessFacts``, a name, or ``None``."""
    if harness is None or isinstance(harness, HarnessInfo):
        return harness
    if isinstance(harness, str):
        try:
            from backend.generation.harnesses import registry

            adapter = registry.get(harness)
        except (NotImplementedError, KeyError, ImportError):
            return HarnessInfo(name=harness)
        return harness_info(adapter) or HarnessInfo(name=harness)
    facts = getattr(harness, "facts", harness)
    if not hasattr(facts, "min_call_output_tokens"):
        name = getattr(harness, "name", None)
        return HarnessInfo(name=str(name)) if name else None
    return HarnessInfo(
        name=str(getattr(harness, "name", "") or getattr(facts, "name", "") or "unknown"),
        context_knob=facts.context_knob,
        min_call_output_tokens=int(facts.min_call_output_tokens or 0),
        max_turns_flag=facts.max_turns_flag,
        known=True,
    )


@dataclass(frozen=True)
class StudyPolicyContext:
    """Study-level facts the effective policy and the lint need (built by the resolver, WP-10).

    ``executor`` is ``"agentic"`` for app runs, ``"single_shot"`` or ``"restricted_agent"`` for slot
    variants. ``serving_tier`` is the tier already resolved for the whole study (``resolve_serving_tier``),
    so ``auto_batch`` never mixes tiers inside a study. Token estimates are optional; a rule whose input
    is missing does not fire.
    """

    study_kind: StudyKind = "exploratory"
    mode: Literal["app", "slot"] = "app"
    executor: str = "agentic"
    n_model_levels: int = 1
    serving_tier: Literal["standard", "flex", "batch"] | None = None
    stack_min_memory: str | None = None
    stack_min_nano_cpus: int | None = None
    any_non_billed_endpoint: bool = False
    p99_run_usd: float | None = None
    random_effects: tuple[str, ...] = ()
    primary_features: tuple[str, ...] = ()
    sampling_policy: str | None = None
    temperature: float | None = None
    brief_tokens: int | None = None
    slot_original_tokens: int | None = None
    slot_prompt_tokens: int | None = None

    @property
    def agentic(self) -> bool:
        return self.executor in AGENTIC_EXECUTORS


def context_for_study(study: Any) -> StudyPolicyContext:
    """``StudyPolicyContext`` from a ``Study`` row (or a ``Design``): kind, mode, model levels, executor,
    random effects and primary features. Token estimates and stack minimums are the resolver's to add."""
    from backend.generation.contracts.plan import Design

    raw = getattr(study, "design", study)
    design = raw if isinstance(raw, Design) else Design.model_validate(raw)
    levels = {factor.key: factor.levels for factor in design.factors}
    executors = set(levels.get("slot_executor") or [])
    if design.mode == "app":
        executor = "agentic"
    elif executors and executors <= {"single_shot"}:
        executor = "single_shot"
    elif executors and executors <= AGENTIC_EXECUTORS:
        executor = "restricted_agent"
    else:
        executor = "single_shot" if not executors else "mixed"
    plan = design.analysis_plan or getattr(study, "analysis_plan", None) or {}
    random_effects = tuple(str(x) for x in (plan.get("random_effects") or [])) if isinstance(plan, dict) else ()
    return StudyPolicyContext(
        study_kind=design.kind,
        mode=design.mode,
        executor=executor,
        n_model_levels=max(1, len(levels.get("model") or [])),
        random_effects=random_effects,
        primary_features=tuple(design.slot_primary_features),
    )


# =====================================================================================
# Content helpers
# =====================================================================================


def policy_content(base: Any) -> ResourcePolicyContent:
    """``ResourcePolicyContent`` from a ``ResourcePolicy`` row, a content dict or the model itself."""
    if isinstance(base, ResourcePolicyContent):
        return base
    if isinstance(base, EffectivePolicy):
        return base.content
    content = getattr(base, "content", base)
    if isinstance(content, Mapping) and "content" in content and "run" not in content:
        content = content["content"]  # an EffectivePolicy dump
    return ResourcePolicyContent.model_validate(content)


def _set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    if dotted not in KNOB_META and not _is_leaf_path(dotted):
        msg = f"unknown budget knob {dotted!r}"
        raise KeyError(msg)
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            msg = f"budget knob {dotted!r} does not name a field"
            raise KeyError(msg)
        node = child
    if dotted == "reasoning" and isinstance(value, Mapping):
        node["reasoning"] = {**node.get("reasoning", {}), **copy.deepcopy(dict(value))}
    else:
        node[parts[-1]] = copy.deepcopy(value)


def _is_leaf_path(dotted: str) -> bool:
    from backend.generation.contracts.resources import iter_leaf_paths

    return dotted in set(iter_leaf_paths())


def _deep_merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict) and key != "max_price":
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def apply_overrides(base: Any, overrides: Mapping[str, Any] | None) -> ResourcePolicyContent:
    """The preset with ``overrides`` applied: flat dotted knob keys and/or a nested partial dict.

    Unknown keys raise ``KeyError``; an override that breaks the schema raises pydantic's
    ``ValidationError``. Cross-field rules are left to the lint.
    """
    content = policy_content(base).model_dump(mode="json")
    if overrides:
        nested = {key: value for key, value in overrides.items() if "." not in key and key in content}
        content = _deep_merge(content, nested)
        for key, value in overrides.items():
            if "." in key:
                _set_dotted(content, key, value)
            elif key not in content:
                msg = f"unknown budget knob {key!r}"
                raise KeyError(msg)
    return ResourcePolicyContent.model_validate(content)


def parse_memory(value: str | int | None) -> int | None:
    """Docker memory size (``"3g"``, ``"1536m"``, bytes) in bytes."""
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().lower().removesuffix("b").removesuffix("i")
    if text[-1:] in _MEM_UNITS:
        return int(float(text[:-1]) * _MEM_UNITS[text[-1]])
    return int(float(text))


def _format_memory(value: int) -> str:
    for unit in ("g", "m", "k"):
        size = _MEM_UNITS[unit]
        if value % size == 0:
            return f"{value // size}{unit}"
    return f"{math.ceil(value / _MEM_UNITS['m'])}m"


# =====================================================================================
# Resolution
# =====================================================================================


def _batch_verified() -> bool:
    return bool(getattr(settings, "BATCH_BACKENDS_VERIFIED", False))


def resolve_serving_tier(
    policies: Iterable[Any],
    *,
    slot_executors: Iterable[str],
    models: Iterable[Any] = (),
) -> tuple[Literal["standard", "flex", "batch"], list[str]]:
    """The one serving tier of a study (4.2): ``auto_batch`` is ``batch`` only when every slot condition is
    single-shot on an endpoint that supports batch serving (and the batch backend is verified); otherwise
    ``standard`` for all, with the note ``batch_unsupported``. An explicit tier is kept as written (the lint
    reports a mix). Returns ``(tier, notes)``."""
    contents = [policy_content(p) for p in policies]
    tiers = {c.routing.serving_tier for c in contents}
    explicit = tiers - {"auto_batch"}
    if len(explicit) == 1 and "auto_batch" not in tiers:
        tier = next(iter(explicit))
        return tier, []  # type: ignore[return-value]
    if explicit and "auto_batch" not in tiers:
        return "standard", []
    executors = set(slot_executors)
    facts = [model_facts(m) for m in models]
    batch_ok = (
        bool(executors)
        and executors == {"single_shot"}
        and _batch_verified()
        and all(f.supports_batch and not f.is_local for f in facts)
    )
    if batch_ok and not explicit:
        return "batch", []
    return "standard", ["batch_unsupported"]


def _budget_basis(content: ResourcePolicyContent, ctx: StudyPolicyContext) -> Literal["usd", "tokens"]:
    basis = content.study.budget_basis
    if basis == "auto":
        return "tokens" if ctx.study_kind == "confirmatory" and ctx.n_model_levels > 1 else "usd"
    return basis


def _cap_basis(
    content: ResourcePolicyContent,
    ctx: StudyPolicyContext,
    facts: ModelFacts | None,
) -> Literal["billed", "list"]:
    basis = content.study.cap_basis
    if basis == "auto":
        non_billed = ctx.any_non_billed_endpoint or (facts is not None and facts.cost_basis != "billed")
        return "list" if non_billed else "billed"
    return basis


class _Clamper:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.clamps: list[PolicyClamp] = []
        self.notes: list[str] = []

    def get(self, dotted: str) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            node = node[part]
        return node

    def set(self, dotted: str, value: Any, reason: str, *, note: str | None = None) -> None:
        old = self.get(dotted)
        if old == value:
            return
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = copy.deepcopy(value)
        self.clamps.append(PolicyClamp.model_validate({"key": dotted, "from": old, "to": value, "reason": reason}))
        if note and note not in self.notes:
            self.notes.append(note)


def effective_policy(
    base: Any,
    overrides: Mapping[str, Any] | None = None,
    *,
    model: Any = None,
    harness: Any = None,
    study_ctx: StudyPolicyContext | None = None,
    study: Any = None,
    executor: str | None = None,
    machine_memory: str | int | None = None,
    machine_nano_cpus: int | None = None,
) -> EffectivePolicy:
    """The clamped policy for one cell, with the clamps, notes and lint issues (section 4.3).

    ``base`` is a ``ResourcePolicy`` row, its content, or ``ResourcePolicyContent``; ``model`` an ``LLMModel``
    or ``ModelFacts``; ``harness`` an adapter, ``HarnessFacts``, ``HarnessInfo`` or a harness name. The study
    facts come from ``study_ctx``, else are derived from ``study`` (a ``Study`` row; see
    :func:`context_for_study`); ``executor`` overrides the executor of either. The machine maxima default to
    ``AGENT_SANDBOX_MEM_LIMIT_MAX`` / ``AGENT_SANDBOX_NANO_CPUS_MAX`` (read now).
    """
    ctx = study_ctx or (context_for_study(study) if study is not None else StudyPolicyContext())
    if executor is not None and executor != ctx.executor:
        ctx = replace(ctx, executor=executor)
    facts = model_facts(model) if model is not None else None
    hinfo = harness_info(harness)
    content = apply_overrides(base, overrides)
    clamp = _Clamper(content.model_dump(mode="json"))

    budget_basis = _budget_basis(content, ctx)
    cap_basis = _cap_basis(content, ctx, facts)
    clamp.set("study.budget_basis", budget_basis, "auto resolved (confirmatory multi-model -> tokens)")
    clamp.set("study.cap_basis", cap_basis, "auto resolved (list when an endpoint is not billed)")

    # Serving tier: per study; agentic runs are always standard.
    tier = content.routing.serving_tier
    if ctx.agentic:
        resolved_tier = "standard" if tier in ("auto_batch", "batch") else tier
    elif ctx.serving_tier is not None:
        resolved_tier = ctx.serving_tier
    elif tier == "auto_batch":
        resolved_tier, tier_notes = resolve_serving_tier(
            [content],
            slot_executors=[ctx.executor],
            models=[facts] if facts else [],
        )
        clamp.notes.extend(n for n in tier_notes if n not in clamp.notes)
    else:
        resolved_tier = tier
    if tier == "batch" and ctx.agentic:
        resolved_tier = "batch"  # kept so the lint reports batch_requires_single_shot
    clamp.set("routing.serving_tier", resolved_tier, "serving tier resolved per study")
    if (
        tier == "auto_batch"
        and resolved_tier == "standard"
        and not ctx.agentic
        and "batch_unsupported" not in clamp.notes
    ):
        clamp.notes.append("batch_unsupported")

    reasoning = clamp.get("reasoning")
    if facts is not None:
        # reasoning is dropped for models without reasoning support.
        if not facts.supports_reasoning and reasoning["mode"] != "provider_default":
            clamp.set(
                "reasoning.mode",
                "provider_default",
                "model has no reasoning support",
                note="reasoning_unsupported",
            )
            clamp.set("reasoning.effort", None, "model has no reasoning support")
            clamp.set("reasoning.max_tokens", None, "model has no reasoning support")
        reasoning_budget = clamp.get("reasoning.max_tokens") or 0
        if facts.max_output:
            limit = max(1, facts.max_output - reasoning_budget)
            if clamp.get("call.max_output_tokens") > limit:
                clamp.set("call.max_output_tokens", limit, f"model max_output {facts.max_output} - reasoning budget")
            slot_cap = clamp.get("slot.max_output_tokens")
            if slot_cap is not None and slot_cap > limit:
                clamp.set("slot.max_output_tokens", limit, f"model max_output {facts.max_output} - reasoning budget")
        if facts.context_length:
            context = clamp.get("context.max_context_tokens")
            if context is not None and context > facts.context_length:
                clamp.set("context.max_context_tokens", facts.context_length, "model context length")
        max_price = clamp.get("routing.max_price")
        if isinstance(max_price, str):
            factor = float(max_price.split("\u00d7", 1)[1])
            if facts.priced:
                resolved = {
                    "prompt": round(factor * (facts.input_per_mtok or 0.0), 6),
                    "completion": round(factor * (facts.output_per_mtok or 0.0), 6),
                }
                clamp.set("routing.max_price", resolved, f"{max_price} of the listed prices")
            else:
                clamp.set("routing.max_price", None, "model prices unknown", note="max_price_unresolved")
        if facts.is_local:
            # Local endpoints have no upstream providers: routing filters do not apply.
            if clamp.get("routing.max_price") is not None:
                clamp.set("routing.max_price", None, "local endpoint")

    # Sandbox values <= machine maxima, then raised to the study maximum over stack minimums.
    mem_max = parse_memory(machine_memory if machine_memory is not None else settings.AGENT_SANDBOX_MEM_LIMIT_MAX)
    cpu_max = int(machine_nano_cpus if machine_nano_cpus is not None else settings.AGENT_SANDBOX_NANO_CPUS_MAX)
    memory = parse_memory(clamp.get("sandbox.memory")) or 0
    if mem_max and memory > mem_max:
        clamp.set("sandbox.memory", _format_memory(mem_max), "machine maximum", note="sandbox_clamped_to_machine")
        memory = mem_max
    stack_min = parse_memory(ctx.stack_min_memory)
    if stack_min and memory < stack_min:
        raised = min(stack_min, mem_max) if mem_max else stack_min
        clamp.set("sandbox.memory", _format_memory(raised), "raised to the study's largest stack minimum")
        if mem_max and stack_min > mem_max:
            clamp.notes.append("stack_minimum_exceeds_machine")
    cpus = clamp.get("sandbox.nano_cpus")
    if cpu_max and cpus > cpu_max:
        clamp.set("sandbox.nano_cpus", cpu_max, "machine maximum", note="sandbox_clamped_to_machine")
        cpus = cpu_max
    if ctx.stack_min_nano_cpus and cpus < ctx.stack_min_nano_cpus:
        raised_cpus = min(ctx.stack_min_nano_cpus, cpu_max) if cpu_max else ctx.stack_min_nano_cpus
        clamp.set("sandbox.nano_cpus", raised_cpus, "raised to the study's largest stack minimum")

    # budget_basis=tokens: the USD run cap becomes a backstop above the p99 estimate (class O).
    if budget_basis == "tokens" and ctx.p99_run_usd is not None:
        floor = round(ctx.p99_run_usd * k.TOKENS_BASIS_RUN_CAP_FACTOR, 6)
        if clamp.get("run.max_cost_usd") < floor:
            clamp.set("run.max_cost_usd", floor, "budget_basis=tokens: max(policy, p99 x 1.5)")

    clamped = ResourcePolicyContent.model_validate(clamp.data)
    return EffectivePolicy(
        content=clamped,
        budget_basis=budget_basis,
        cap_basis=cap_basis,
        serving_tier=resolved_tier if resolved_tier != "auto_batch" else "standard",
        clamps=clamp.clamps,
        notes=clamp.notes,
        issues=lint(clamped, model=facts, harness=hinfo, study_ctx=ctx, budget_basis=budget_basis),
    )


def treatment_view(policy: EffectivePolicy | ResourcePolicyContent) -> dict[str, Any]:
    """The class C and R knobs after clamping: ``generation_policy_hash = H(treatment_view(policy))``."""
    if isinstance(policy, EffectivePolicy):
        return values_by_class(policy.content, {"C", "R"}, budget_basis=policy.budget_basis)
    basis = policy.study.budget_basis
    return values_by_class(policy, {"C", "R"}, budget_basis="tokens" if basis == "tokens" else "usd")


def generation_policy_hash(policy: EffectivePolicy | ResourcePolicyContent) -> str:
    return identity.H(treatment_view(policy))


# =====================================================================================
# Policy lint, section 4.3
# =====================================================================================


def _issue(code: str, kind: StudyKind, message: str, *, path: str | None = None, **data: Any) -> Issue | None:
    try:
        return make_issue(code, kind, message=message, path=path, data=data)
    except ValueError:
        return None  # the code does not apply to this study kind


def lint(
    content: Any,
    *,
    model: Any = None,
    harness: Any = None,
    study_ctx: StudyPolicyContext | None = None,
    budget_basis: Literal["usd", "tokens"] | None = None,
) -> list[Issue]:
    """Policy lint issues (section 4.3). Rules whose inputs are not given do not fire."""
    policy = policy_content(content)
    ctx = study_ctx or StudyPolicyContext()
    kind = ctx.study_kind
    facts = model_facts(model) if model is not None else None
    hinfo = harness_info(harness)
    issues: list[Issue | None] = []
    call, context, reasoning = policy.call, policy.context, policy.reasoning

    if (
        call.max_input_tokens is not None
        and context.max_context_tokens is not None
        and call.max_input_tokens < _CONTEXT_CALL_RATIO * context.max_context_tokens
    ):
        needed = math.ceil(_CONTEXT_CALL_RATIO * context.max_context_tokens)
        issue = _issue(
            "context_cap_above_call_cap",
            kind,
            f"call.max_input_tokens {call.max_input_tokens} < 1.1 x context.max_context_tokens "
            f"{context.max_context_tokens}: the harness would be refused before it compacts",
            path="call.max_input_tokens",
            minimum=needed,
        )
        if issue is not None:
            issue.fix.append(
                IssueFix(
                    label=f"raise call cap to {needed}",
                    action="set_knob",
                    params={"call.max_input_tokens": needed},
                ),
            )
        issues.append(issue)

    mode = reasoning.mode
    if (
        (mode == "effort" and (reasoning.effort is None or reasoning.max_tokens is not None))
        or (mode == "max_tokens" and (reasoning.max_tokens is None or reasoning.effort is not None))
        or (mode == "provider_default" and (reasoning.effort is not None or reasoning.max_tokens is not None))
    ):
        issues.append(
            _issue(
                "reasoning_mode_invalid",
                kind,
                f"reasoning mode {mode!r} needs exactly its own setting (effort={reasoning.effort}, "
                f"max_tokens={reasoning.max_tokens})",
                path="reasoning",
            ),
        )
    if reasoning.max_tokens is not None and reasoning.max_tokens >= call.max_output_tokens:
        issues.append(
            _issue(
                "reasoning_budget_too_large",
                kind,
                f"reasoning.max_tokens {reasoning.max_tokens} >= call.max_output_tokens {call.max_output_tokens}",
                path="reasoning.max_tokens",
            ),
        )
    if (
        facts is not None
        and facts.is_anthropic
        and reasoning.max_tokens is not None
        and reasoning.max_tokens < _ANTHROPIC_MIN_REASONING
    ):
        issues.append(
            _issue(
                "reasoning_budget_too_small",
                kind,
                f"Anthropic models need reasoning.max_tokens >= 1024 (got {reasoning.max_tokens})",
                path="reasoning.max_tokens",
            ),
        )

    if hinfo is not None and hinfo.known:
        if call.max_input_tokens is not None and hinfo.context_knob is None:
            issues.append(
                _issue(
                    "call_cap_without_compaction",
                    kind,
                    f"{hinfo.name} has no context knob: it cannot compact below call.max_input_tokens",
                    path="call.max_input_tokens",
                ),
            )
        if hinfo.min_call_output_tokens and call.max_output_tokens < hinfo.min_call_output_tokens:
            issues.append(
                _issue(
                    "output_cap_below_harness_min",
                    kind,
                    f"{hinfo.name} needs call.max_output_tokens >= {hinfo.min_call_output_tokens}",
                    path="call.max_output_tokens",
                ),
            )

    agentic = ctx.agentic
    if policy.routing.serving_tier == "batch" and agentic:
        issues.append(
            _issue(
                "batch_requires_single_shot",
                kind,
                "serving_tier=batch only works with slot_executor=single_shot",
                path="routing.serving_tier",
            ),
        )
    if facts is not None:
        if agentic and not facts.supports_tools:
            issues.append(
                _issue(
                    "model_lacks_tools",
                    kind,
                    f"{facts.slug} has no tool calling; an agentic harness cannot use it",
                    path="model",
                ),
            )
        if not facts.is_local and "provider" not in ctx.random_effects:
            routing = policy.routing
            if len(routing.only) != 1 or routing.allow_fallbacks:
                issues.append(
                    _issue(
                        "provider_unpinned",
                        kind,
                        "the upstream provider is not pinned (exactly one routing.only provider with "
                        "allow_fallbacks=false); provider variance is part of the treatment",
                        path="routing.only",
                    ),
                )
        if kind == "confirmatory" and is_model_alias(facts.slug):
            issues.append(
                _issue("model_alias_in_confirmatory", kind, f"{facts.slug} is an alias or router slug", path="model"),
            )
        if (
            ctx.slot_prompt_tokens is not None
            and facts.context_length
            and ctx.slot_prompt_tokens > facts.context_length
        ):
            issues.append(
                _issue(
                    "slot_context_exceeds_model",
                    kind,
                    f"slot context + prompt ({ctx.slot_prompt_tokens} tokens) > model context {facts.context_length}",
                    path="context_policy",
                ),
            )

    if ctx.brief_tokens is not None and call.max_input_tokens is not None and agentic:
        overhead = k.HARNESS_OVERHEAD.get(hinfo.name if hinfo else "", k.HARNESS_OVERHEAD_DEFAULT)
        if ctx.brief_tokens + overhead > call.max_input_tokens:
            issues.append(
                _issue(
                    "brief_exceeds_call_cap",
                    kind,
                    f"brief ({ctx.brief_tokens}) + harness overhead ({overhead}) > call.max_input_tokens "
                    f"{call.max_input_tokens}",
                    path="call.max_input_tokens",
                ),
            )
    if ctx.mode == "slot" and ctx.executor == "single_shot" and ctx.slot_original_tokens is not None:
        cap = policy.slot.max_output_tokens or call.max_output_tokens
        r_avg = _r_avg(policy)
        unbounded = facts is not None and (facts.supports_reasoning or facts.reasoning_model)
        if unbounded:
            # E2E-15: the single-shot body sends no reasoning parameter, so a reasoning model reasons at its
            # provider default however the policy sets ``reasoning``; reserve the observed headroom.
            r_avg = max(r_avg, float(k.R_UNBOUNDED_REASONING))
        need = k.SLOT_OUTPUT_FACTOR * ctx.slot_original_tokens + r_avg
        if need > cap:
            why = (
                f"; {facts.slug} is a reasoning model and single-shot calls do not bound its reasoning"
                if unbounded and facts is not None
                else ""
            )
            issues.append(
                _issue(
                    "slot_output_cap_tight",
                    kind,
                    f"1.6 x original ({ctx.slot_original_tokens}) + reasoning ({int(r_avg)}) = {int(need)} > "
                    f"slot output cap {cap}{why}",
                    path="slot.max_output_tokens",
                ),
            )
    if ctx.mode == "slot" and (
        ctx.sampling_policy == "provider_default" or (ctx.temperature is not None and ctx.temperature == 0)
    ):
        issues.append(
            _issue(
                "slot_sampling_provider_default",
                kind,
                "slot studies need explicit sampling with temperature > 0 (N regenerations must differ)",
                path="sampling_preset",
            ),
        )
    basis = budget_basis or (policy.study.budget_basis if policy.study.budget_basis != "auto" else None)
    if kind == "confirmatory" and basis == "usd" and ctx.n_model_levels > 1:
        issues.append(
            _issue(
                "usd_basis_multi_model",
                kind,
                "a USD budget basis across models makes cheaper models get more work; use budget_basis=tokens",
                path="study.budget_basis",
            ),
        )
    for index, rule in enumerate(policy.study.stop_rules):
        if rule.code != "ci_width_reached":
            continue
        if kind == "confirmatory":
            issues.append(
                _issue(
                    "adaptive_stop_in_confirmatory",
                    kind,
                    "ci_width_reached is adaptive precision stopping; pre-register a group-sequential design instead",
                    path=f"study.stop_rules[{index}]",
                ),
            )
        missing = [f for f in rule.features if f not in ctx.primary_features] if ctx.primary_features else []
        if (not rule.features or missing) and ctx.mode == "slot":
            issues.append(
                _issue(
                    "primary_feature_missing",
                    kind,
                    "ci_width_reached needs features that are listed in Design.slot_primary_features",
                    path=f"study.stop_rules[{index}].features",
                    missing=missing,
                ),
            )
    return [issue for issue in issues if issue is not None]


def _r_avg(policy: ResourcePolicyContent) -> float:
    reasoning = policy.reasoning
    if reasoning.mode == "max_tokens" and reasoning.max_tokens:
        return reasoning.max_tokens * k.REASONING_BUDGET_SHARE
    if reasoning.mode == "effort" and reasoning.effort:
        return float(k.R_AVG.get(reasoning.effort, 0))
    return float(k.R_AVG["provider_default"])


@dataclass(frozen=True)
class ConditionPolicy:
    """One condition's effective policy for the cross-condition lint."""

    condition_index: int
    effective: EffectivePolicy
    stack: str = ""
    cells: Sequence[str] = field(default_factory=tuple)


def lint_study(conditions: Sequence[ConditionPolicy], *, study_kind: StudyKind = "exploratory") -> list[Issue]:
    """Rules across conditions: ``resources_vary_by_stack`` and ``mixed_serving_tier``."""
    issues: list[Issue] = []
    sandboxes = {(c.effective.content.sandbox.memory, c.effective.content.sandbox.nano_cpus) for c in conditions}
    if len(sandboxes) > 1:
        issue = _issue(
            "resources_vary_by_stack",
            study_kind,
            f"sandbox resources differ between cells: {sorted(sandboxes)}",
            path="sandbox",
        )
        if issue is not None:
            issue.cells.extend(cell for c in conditions for cell in c.cells)
            issues.append(issue)
    tiers = {c.effective.serving_tier for c in conditions}
    if len(tiers) > 1:
        issue = _issue("mixed_serving_tier", study_kind, f"serving tiers differ: {sorted(tiers)}", path="routing")
        if issue is not None:
            issues.append(issue)
    return issues


@dataclass(frozen=True)
class ConditionInput:
    """One condition for :func:`resolve_study_policies` (the resolver's view of a condition).

    ``executor`` is ``"agentic"`` (app), ``"single_shot"`` or ``"restricted_agent"`` (slot); ``stack`` the
    condition's stack slug (for ``resources_vary_by_stack``); ``cells`` the issue cell refs (``"c<index>"``).
    """

    index: int
    policy: Any
    model: Any = None
    harness: Any = None
    executor: str = "agentic"
    overrides: Mapping[str, Any] | None = None
    stack: str = ""
    cells: Sequence[str] = ()


@dataclass
class StudyPolicies:
    """What :func:`resolve_study_policies` returns: one effective policy per condition index, the study's
    serving tier (and its notes), and the cross-condition lint issues (``lint_study``). Per-condition lint
    issues stay on each ``EffectivePolicy.issues``."""

    by_condition: dict[int, EffectivePolicy]
    serving_tier: Literal["standard", "flex", "batch"]
    tier_notes: list[str]
    issues: list[Issue]
    context: StudyPolicyContext


def resolve_study_policies(
    study: Any,
    conditions: Sequence[ConditionInput],
    *,
    stack_min_memory: str | None = None,
    stack_min_nano_cpus: int | None = None,
    brief_tokens: int | None = None,
    slot_original_tokens: int | None = None,
    slot_prompt_tokens: int | None = None,
    p99_run_usd: float | None = None,
    machine_memory: str | int | None = None,
    machine_nano_cpus: int | None = None,
) -> StudyPolicies:
    """Every condition's effective policy with the full study context, in one call per study (4.3).

    The base context comes from ``study`` (a ``Study`` row or a ``Design``; :func:`context_for_study`);
    ``any_non_billed_endpoint`` is derived from the condition models; the serving tier is resolved
    **once** over all condition policies (:func:`resolve_serving_tier`) and applied to every condition whose
    policy says ``auto_batch`` (an explicit tier is kept, so ``lint_study`` reports a mix); stack minimums
    and token estimates are passed through so ``stack_minimum_exceeds_machine``, ``brief_exceeds_call_cap``,
    ``slot_output_cap_tight`` and ``slot_context_exceeds_model`` can fire at resolve. Identical
    (policy, overrides, model, harness, executor) inputs share one resolution.
    """
    base_ctx = context_for_study(study)
    facts = {c.index: (model_facts(c.model) if c.model is not None else None) for c in conditions}
    non_billed = any(f is not None and f.cost_basis != "billed" for f in facts.values())
    slot_executors = [c.executor for c in conditions if c.executor != "agentic"]
    tier, tier_notes = resolve_serving_tier(
        [apply_overrides(c.policy, c.overrides) for c in conditions],
        slot_executors=slot_executors,
        models=[f for f in facts.values() if f is not None],
    )
    ctx = replace(
        base_ctx,
        n_model_levels=max(
            base_ctx.n_model_levels,
            len({f.slug for f in facts.values() if f is not None}),
        ),
        serving_tier=None,
        any_non_billed_endpoint=base_ctx.any_non_billed_endpoint or non_billed,
        stack_min_memory=stack_min_memory or base_ctx.stack_min_memory,
        stack_min_nano_cpus=stack_min_nano_cpus or base_ctx.stack_min_nano_cpus,
        brief_tokens=brief_tokens if brief_tokens is not None else base_ctx.brief_tokens,
        slot_original_tokens=(
            slot_original_tokens if slot_original_tokens is not None else base_ctx.slot_original_tokens
        ),
        slot_prompt_tokens=slot_prompt_tokens if slot_prompt_tokens is not None else base_ctx.slot_prompt_tokens,
        p99_run_usd=p99_run_usd if p99_run_usd is not None else base_ctx.p99_run_usd,
    )
    cache: dict[Any, EffectivePolicy] = {}
    by_condition: dict[int, EffectivePolicy] = {}
    rows: list[ConditionPolicy] = []
    for c in conditions:
        content = apply_overrides(c.policy, c.overrides)
        cond_ctx = replace(
            ctx,
            executor=c.executor,
            serving_tier=tier if content.routing.serving_tier == "auto_batch" else None,
        )
        key = (
            identity.H(content.model_dump(mode="json")),
            facts[c.index].slug if facts[c.index] is not None else None,
            facts[c.index].endpoint_id if facts[c.index] is not None else None,
            getattr(harness_info(c.harness), "name", None),
            c.executor,
        )
        if key not in cache:
            cache[key] = effective_policy(
                content,
                model=facts[c.index],
                harness=c.harness,
                study_ctx=cond_ctx,
                machine_memory=machine_memory,
                machine_nano_cpus=machine_nano_cpus,
            )
        by_condition[c.index] = cache[key]
        rows.append(
            ConditionPolicy(
                condition_index=c.index,
                effective=cache[key],
                stack=c.stack,
                cells=tuple(c.cells) or (f"c{c.index}",),
            ),
        )
    issues = lint_study(rows, study_kind=ctx.study_kind)
    return StudyPolicies(
        by_condition=by_condition,
        serving_tier=tier,
        tier_notes=list(tier_notes),
        issues=issues,
        context=ctx,
    )


# =====================================================================================
# Launch helpers, UI payloads
# =====================================================================================


def execution_options(policy: EffectivePolicy | ResourcePolicyContent, *, warmup: bool = True) -> dict[str, Any]:
    """``Study.execution_options`` (and ``max_parallel`` under its own key) from the concurrency section.

    Returns ``{"max_parallel": int, "execution_options": ExecutionOptions dump}``; the launch (WP-10)
    copies both onto the Study row.
    """
    content = policy.content if isinstance(policy, EffectivePolicy) else policy
    c = content.concurrency
    options = ExecutionOptions(
        max_resident_models=c.max_resident_models,
        per_model=c.per_model,
        per_endpoint=c.per_endpoint,
        per_provider=c.per_provider,
        rpm_per_model=c.rpm_per_model,
        eval_parallel=c.eval_parallel,
        warmup=warmup,
    )
    return {"max_parallel": c.study_max_parallel, "execution_options": options.model_dump(mode="json")}


def knob_meta_payload() -> list[dict[str, Any]]:
    """``KNOB_META`` as JSON rows for ``GET /budget/knobs/`` (the Budget preset editor)."""
    return [meta.to_dict() for meta in KNOB_META.values()]


def diff_from_tier(content: Any, tier: str | None = None) -> list[dict[str, Any]]:
    """``[{key, tier_value, value, class}]`` for every knob that differs from ``tier`` (default: the closest)."""
    values = knob_values(policy_content(content))
    if tier is None or tier not in TIERS:
        tier = min(
            TIERS,
            key=lambda t: sum(1 for key, v in knob_values(preset_content(t)).items() if values.get(key) != v),
        )
    reference = knob_values(preset_content(tier))
    return [
        {"key": key, "tier": tier, "tier_value": reference[key], "value": value, "class": knob_class(key)}
        for key, value in values.items()
        if reference.get(key) != value
    ]


def _money(value: float) -> str:
    return f"${value:.2f}" if value < 100 else f"${value:,.0f}"


def summary_line(policy: Any, *, name: str | None = None, version: int | None = None, tier: str | None = None) -> str:
    """ "Frugal v1 · $0.30/run · auto study cap · 2 parallel · effort low · no overrides"."""
    content = policy_content(policy)
    tier = tier or getattr(policy, "tier", None)
    name = name or getattr(policy, "name", None) or (tier.capitalize() if tier else "Custom")
    version = version if version is not None else getattr(policy, "version", None)
    head = f"{name} v{version}" if version else name
    cap = content.study.max_cost_usd
    cap_text = "auto study cap" if cap == "auto" else f"{_money(float(cap))} study cap"
    reasoning = content.reasoning
    if reasoning.mode == "effort":
        reasoning_text = f"effort {reasoning.effort}"
    elif reasoning.mode == "max_tokens":
        reasoning_text = f"reasoning {reasoning.max_tokens} tok"
    else:
        reasoning_text = "provider reasoning"
    diff = diff_from_tier(content, tier if tier in TIERS else None)
    overrides = "no overrides" if not diff else f"{len(diff)} override{'s' if len(diff) != 1 else ''}"
    parts = [
        head,
        f"{_money(content.run.max_cost_usd)}/run",
        cap_text,
        f"{content.concurrency.study_max_parallel} parallel",
        reasoning_text,
    ]
    if not content.pilot.enabled:
        parts.append("no pilot")
    parts.append(overrides)
    return " · ".join(parts)


def explain(
    policy: Any,
    *,
    model: Any = None,
    harness: Any = None,
    study_ctx: StudyPolicyContext | None = None,
) -> dict[str, Any]:
    """``GET /budget/policies/{ref}/explain/``: effective values, notes, issues, treatment view and hash."""
    effective = effective_policy(policy, model=model, harness=harness, study_ctx=study_ctx)
    view = treatment_view(effective)
    return {
        "effective": effective.model_dump(mode="json", by_alias=True),
        "notes": list(effective.notes),
        "issues": [issue.model_dump(mode="json") for issue in effective.issues],
        "treatment_view": view,
        "generation_policy_hash": identity.H(view),
        "knob_meta": knob_meta_payload(),
        "summary": summary_line(policy),
    }
