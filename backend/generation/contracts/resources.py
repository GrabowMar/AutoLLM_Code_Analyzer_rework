"""Resource and budget knobs (spec section 4): ``ResourcePolicyContent``, ``KNOB_META``, ``GatewayConfig``.

Every knob of the section 4.2 catalogue is a field of ``ResourcePolicyContent``
and has a ``KNOB_META`` entry carrying its validity class, enforcement point,
UI group, key-limit flag, unit and the frugal / standard / generous defaults.
A stored policy is always complete: every field is required, so two policies
never differ by an implicit default. :func:`preset_content` builds the three
tier presets from ``KNOB_META`` (WP-11 writes them to
``data/resource_policies/*.yaml``).

Validity classes (4.1): **C** changes what the model sees or how it samples;
**R** can change the outcome only through truncation or stopping (both in
``treatment_view()`` and so in ``generation_policy_hash``); **O** is
operational (recorded, never hashed); **S** is a study-level stopping/design
rule (in ``prereg_hash``). ``run.max_cost_usd`` is R under ``budget_basis=usd``
and O (a backstop) under ``budget_basis=tokens``.

Cross-field rules (reasoning exclusivity, ``call.max_input_tokens >= 1.1 x
context.max_context_tokens``, ...) are **policy lint** (``budget/policy.py``,
issue codes in ``contracts/issues.py``), not schema rules, so the lint can
report them.
"""

import copy
import re
from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated
from typing import Any
from typing import Final
from typing import Literal

from pydantic import BaseModel
from pydantic import Field
from pydantic import StringConstraints
from pydantic import field_validator

from backend.generation.contracts._base import ContractModel
from backend.generation.contracts._base import Fraction
from backend.generation.contracts._base import NonEmptyStr
from backend.generation.contracts._base import NonNegFloat
from backend.generation.contracts._base import NonNegInt
from backend.generation.contracts._base import PosFloat
from backend.generation.contracts._base import PosInt
from backend.generation.contracts.issues import Issue

__all__ = [
    "DEFAULT_STOP_RULES",
    "KNOB_META",
    "TIERS",
    "CallLimits",
    "CellLimits",
    "ContextSettings",
    "EffectivePolicy",
    "GatewayConfig",
    "KnobMeta",
    "PilotGates",
    "PilotSettings",
    "PolicyClamp",
    "ReasoningSettings",
    "ResourcePolicyContent",
    "RunLimits",
    "StopRule",
    "StudyLimits",
    "iter_leaf_paths",
    "knob_class",
    "knob_for_path",
    "knob_values",
    "preset_content",
    "values_by_class",
]

KnobClass = Literal["C", "R", "O", "S"]
Where = Literal["GW", "SBX", "HAR", "SCH", "LED", "STU", "EXE", "PLN", "SLT", "EVL", "BLD", "UI"]
Tier = Literal["frugal", "standard", "generous"]
TIERS: Final[tuple[Tier, ...]] = ("frugal", "standard", "generous")

MIB: Final = 1024 * 1024
GIB: Final = 1024 * MIB

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
RetryStatus = Annotated[str, StringConstraints(pattern=r"^(?:[1-5][0-9]{2}|[1-5]xx)$")]
MemoryStr = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*[kmg]$")]
_MODEL_MULTIPLIER_RE: Final = re.compile("^model[\u00d7x](?P<k>[0-9]+(?:\\.[0-9]+)?)$")

# =====================================================================================
# Sections
# =====================================================================================


class RunLimits(ContractModel):
    """One attempt of one sample."""

    max_cost_usd: NonNegFloat
    max_total_tokens: PosInt
    max_uncached_input_tokens: PosInt | None
    max_output_tokens: PosInt | None
    max_llm_requests: PosInt
    max_aux_requests: NonNegInt
    max_turns: PosInt | None
    max_workspace_bytes: PosInt


class CallLimits(ContractModel):
    """Per call, forced by the gateway on every request."""

    max_output_tokens: PosInt
    max_input_tokens: PosInt | None


class ContextSettings(ContractModel):
    max_context_tokens: PosInt | None
    compaction: Literal["harness_default", "auto", "auto_prune", "off"]


class ReasoningSettings(ContractModel):
    """Forced OpenRouter ``reasoning``. Exactly one of effort / max_tokens per mode is a lint rule."""

    mode: Literal["provider_default", "effort", "max_tokens"]
    effort: ReasoningEffort | None
    max_tokens: PosInt | None
    exclude: bool


class CachingSettings(ContractModel):
    prompt_cache: Literal["auto", "on", "off"]
    ttl: Literal["5m", "1h"]
    sticky_session: bool


class MaxPrice(ContractModel):
    """USD per million tokens."""

    prompt: NonNegFloat
    completion: NonNegFloat


class RoutingSettings(ContractModel):
    provider_sort: Literal["none", "price", "throughput", "latency"]
    max_price: MaxPrice | str | None  # {prompt, completion} | "model\u00d7k" | None
    allow_fallbacks: bool
    only: list[NonEmptyStr]
    ignore: list[NonEmptyStr]
    order: list[NonEmptyStr]
    quantizations: list[NonEmptyStr]
    require_parameters: bool
    data_collection: Literal["allow", "deny"]
    zdr: bool
    preferred_min_throughput: PosFloat | None
    preferred_max_latency: PosFloat | None
    serving_tier: Literal["standard", "flex", "batch", "auto_batch"]

    @field_validator("max_price", mode="before")
    @classmethod
    def _model_multiplier(cls, value: Any) -> Any:
        """``"model<times>k"`` (U+00D7, or ASCII ``"modelxk"``) is normalized to U+00D7; k must be positive."""
        if isinstance(value, str):
            match = _MODEL_MULTIPLIER_RE.match(value.strip())
            if match is None or float(match["k"]) <= 0:
                msg = f"max_price string must be 'model\u00d7k' (or 'modelxk') with k > 0, got {value!r}"
                raise ValueError(msg)
            return f"model\u00d7{match['k']}"
        return value


class TimeSettings(ContractModel):
    wall_clock_s: PosInt
    idle_timeout_s: PosInt | None
    command_timeout_s: PosInt | None
    llm_call_timeout_s: PosInt


class RetrySettings(ContractModel):
    upstream_retries: Annotated[int, Field(ge=0, le=8)]
    backoff_s: NonNegFloat
    retry_on: list[RetryStatus]
    infra_retries: NonNegInt
    count_failed_calls: bool

    @field_validator("retry_on")
    @classmethod
    def _sorted_unique(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class SandboxSettings(ContractModel):
    memory: MemoryStr
    nano_cpus: PosInt


class ConcurrencySettings(ContractModel):
    study_max_parallel: PosInt
    per_endpoint: PosInt | None
    per_model: PosInt | None
    per_provider: PosInt | None
    rpm_per_model: PosInt | None
    max_resident_models: PosInt | None
    eval_parallel: PosInt


class CellLimits(ContractModel):
    max_cost_usd: NonNegFloat


class StopRule(ContractModel):
    """A study stop rule (section 4.6), evaluated by STU after every run completion and every 60 s."""

    code: Literal[
        "infra_error_rate",
        "no_provider_rate",
        "output_invalid_rate",
        "consecutive_failures",
        "cost_overrun",
        "spend_fraction",
        "condition_error_rate",
        "ci_width_reached",
        "wall_clock_total",
    ]
    scope: Literal["study", "condition", "unit"] = "study"
    window: PosInt = 20  # last N finished runs in scope
    threshold: float
    action: Literal["pause", "stop", "pause_condition", "alert"]
    stages: list[Literal["pilot", "main"]] = ["pilot", "main"]  # pydantic copies mutable defaults
    # ci_width_reached only:
    features: list[str] = Field(default_factory=list)  # subset of Design.slot_primary_features
    n_min: PosInt = 10
    n_max: PosInt | None = None  # defaults to repeats


class StudyLimits(ContractModel):
    max_cost_usd: NonNegFloat | Literal["auto"]
    max_total_tokens: PosInt | None
    max_runs: PosInt | None
    max_wall_hours: PosFloat | None
    cap_basis: Literal["billed", "list", "auto"]
    budget_basis: Literal["usd", "tokens", "auto"]
    alert_at: list[Fraction]
    on_budget_exhausted: Literal["pause", "stop_balanced", "stop"]
    stop_rules: list[StopRule]


class PilotGates(ContractModel):
    """Expand is blocked unless overridden with a recorded reason (per condition unless noted)."""

    max_infra_error_rate: Fraction
    min_build_rate: Fraction
    max_cost_overrun: PosFloat
    max_cap_hit_rate: Fraction
    max_truncation_rate: Fraction
    max_call_truncation_rate: Fraction
    max_wall_clock_rate: Fraction
    max_oom_rate: Fraction  # per stack
    usd_cap_binding: Fraction  # only when budget_basis=tokens


class PilotSettings(ContractModel):
    enabled: bool
    repeats: PosInt
    units: Literal["all"] | PosInt
    smoke_first: bool
    reuse_as_main: bool
    gates: PilotGates
    auto_expand: bool


class CacheSettings(ContractModel):
    dependency_cache: Literal["off", "shared_ro"]
    reuse_sample_images: bool
    reuse_parent_build: bool
    reuse_eval_by_tree: bool


class SlotCallSettings(ContractModel):
    max_output_tokens: PosInt | None


class EstimateSettings(ContractModel):
    method: Literal["priors", "heuristic"]
    require_confirm_above_usd: NonNegFloat


class ResourcePolicyContent(ContractModel):
    """``ResourcePolicy.content``: every knob of section 4.2 (UI name: Budget preset)."""

    run: RunLimits
    call: CallLimits
    context: ContextSettings
    reasoning: ReasoningSettings
    caching: CachingSettings
    routing: RoutingSettings
    time: TimeSettings
    retries: RetrySettings
    sandbox: SandboxSettings
    network_policy: Literal["none", "packages"]
    concurrency: ConcurrencySettings
    cell: CellLimits
    study: StudyLimits
    pilot: PilotSettings
    caches: CacheSettings
    slot: SlotCallSettings
    estimate: EstimateSettings


# =====================================================================================
# KNOB_META
# =====================================================================================

_DEFAULT_STOP_RULES_RAW: Final[tuple[dict[str, Any], ...]] = (
    # infra_error_rate > 0.30 over 20 -> pause (infra; jobs re-queued, never dropped)
    {"code": "infra_error_rate", "scope": "study", "window": 20, "threshold": 0.30, "action": "pause"},
    # no_provider_rate > 0.20 -> pause (usually routing.max_price too tight)
    {"code": "no_provider_rate", "scope": "study", "window": 20, "threshold": 0.20, "action": "pause"},
    # >= 5 consecutive infra_error/build_fail with zero tested, per condition -> pause_condition
    {"code": "consecutive_failures", "scope": "condition", "window": 5, "threshold": 5, "action": "pause_condition"},
    # a condition's mean cost > 2.0 x its estimate after >= 5 runs -> pause_condition (+ alert)
    {"code": "cost_overrun", "scope": "condition", "window": 5, "threshold": 2.0, "action": "pause_condition"},
    # spend_fraction >= 1.0 -> handled by on_budget_exhausted; the rule makes it visible
    {"code": "spend_fraction", "scope": "study", "window": 20, "threshold": 1.0, "action": "alert"},
)
# Normalized (every StopRule default filled in), so KNOB_META defaults equal the stored preset values.
DEFAULT_STOP_RULES: Final[tuple[dict[str, Any], ...]] = tuple(
    StopRule.model_validate(rule).model_dump(mode="json") for rule in _DEFAULT_STOP_RULES_RAW
)


@dataclass(frozen=True)
class KnobMeta:
    """Metadata for one knob of the section 4.2 table."""

    key: str
    cls: KnobClass
    where: tuple[Where, ...]
    ui_group: str
    type: str  # display type, as in the spec table
    unit: str | None
    description: str
    defaults: Mapping[Tier, Any]
    key_limit: bool = False
    cls_when_tokens: KnobClass | None = None  # class under budget_basis=tokens when it differs

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "class": self.cls,
            "class_when_tokens": self.cls_when_tokens,
            "where": list(self.where),
            "ui_group": self.ui_group,
            "key_limit": self.key_limit,
            "type": self.type,
            "unit": self.unit,
            "description": self.description,
            "defaults": {tier: copy.deepcopy(self.defaults[tier]) for tier in TIERS},
        }


def _k(
    key: str,
    cls: KnobClass,
    where: tuple[Where, ...],
    type_: str,
    unit: str | None,
    defaults: tuple[Any, Any, Any],
    description: str,
    *,
    key_limit: bool = False,
    cls_when_tokens: KnobClass | None = None,
) -> KnobMeta:
    return KnobMeta(
        key=key,
        cls=cls,
        where=where,
        ui_group=key.split(".", 1)[0] if "." in key else key,
        type=type_,
        unit=unit,
        description=description,
        defaults=dict(zip(TIERS, defaults, strict=True)),
        key_limit=key_limit,
        cls_when_tokens=cls_when_tokens,
    )


_GATES = "pilot.gates"
_ALL_QUANT = ["fp8", "bf16", "fp16", "fp32"]
_STOP_RULES = [dict(rule) for rule in DEFAULT_STOP_RULES]

_KNOBS: Final[tuple[KnobMeta, ...]] = (
    # --- Run (one attempt of one sample) ---
    _k(
        "run.max_cost_usd",
        "R",
        ("GW", "LED"),
        "float",
        "usd",
        (0.30, 2.00, 10.00),
        "Hard USD cap per attempt (gateway pre-call reservation)",
        key_limit=True,
        cls_when_tokens="O",
    ),
    _k(
        "run.max_total_tokens",
        "R",
        ("GW",),
        "int",
        "tokens",
        (3_000_000, 10_000_000, 30_000_000),
        "Sum of prompt + completion tokens",
    ),
    _k(
        "run.max_uncached_input_tokens",
        "R",
        ("GW",),
        "int|null",
        "tokens",
        (1_000_000, 4_000_000, None),
        "Sum of prompt - cached tokens (the cost-relevant input)",
    ),
    _k(
        "run.max_output_tokens",
        "R",
        ("GW",),
        "int|null",
        "tokens",
        (150_000, 500_000, None),
        "Sum of completion tokens incl. reasoning",
    ),
    _k(
        "run.max_llm_requests",
        "R",
        ("GW", "HAR"),
        "int",
        "requests",
        (40, 100, 200),
        "Successful calls of request class main",
    ),
    _k(
        "run.max_aux_requests",
        "R",
        ("GW",),
        "int",
        "requests",
        (10, 30, 60),
        "Successful calls of classes subagent, compaction, auxiliary",
    ),
    _k(
        "run.max_turns",
        "R",
        ("HAR",),
        "int|null",
        "turns",
        (40, 100, 200),
        "Harness-native turn cap where supported (else gateway caps only, recorded)",
    ),
    _k(
        "run.max_workspace_bytes",
        "R",
        ("SBX",),
        "int",
        "bytes",
        (200 * MIB, 500 * MIB, 1 * GIB),
        "Workspace tar size limit (larger -> output_invalid:workspace_too_large)",
    ),
    # --- Per call (forced by the gateway on every request) ---
    _k(
        "call.max_output_tokens",
        "R",
        ("GW",),
        "int",
        "tokens",
        (8192, 16384, 32000),
        "Visible-output cap; upstream max_tokens = this + reasoning.max_tokens, clamped to the model limit",
    ),
    _k(
        "call.max_input_tokens",
        "R",
        ("GW",),
        "int|null",
        "tokens",
        (72_000, 144_000, None),
        "Refuse a call whose prompt estimate exceeds N (400 context_budget); >= 1.1 x context.max_context_tokens",
    ),
    _k(
        "context.max_context_tokens",
        "C",
        ("HAR",),
        "int|null",
        "tokens",
        (64_000, 128_000, None),
        "Context window advertised to the harness / compaction threshold",
    ),
    _k(
        "context.compaction",
        "C",
        ("HAR",),
        "enum harness_default,auto,auto_prune,off",
        None,
        ("auto_prune", "harness_default", "harness_default"),
        "Harness compaction mode mapping",
    ),
    _k(
        "reasoning",
        "C",
        ("GW",),
        "{mode: provider_default|effort|max_tokens, effort?, max_tokens?}",
        None,
        (
            {"mode": "effort", "effort": "low", "max_tokens": None},
            {"mode": "provider_default", "effort": None, "max_tokens": None},
            {"mode": "effort", "effort": "high", "max_tokens": None},
        ),
        "Forced OpenRouter reasoning; exactly one of effort / max_tokens",
        key_limit=True,
    ),
    _k(
        "reasoning.exclude",
        "C",
        ("GW",),
        "bool",
        None,
        (False, False, False),
        "Ask the provider not to return reasoning text (forced false for replaying protocols)",
    ),
    _k(
        "caching.prompt_cache",
        "O",
        ("GW",),
        "enum auto,on,off",
        None,
        ("on", "on", "on"),
        "on: inject cache_control for explicit-cache providers; auto: provider-implicit only; off: strip",
    ),
    _k(
        "caching.ttl",
        "O",
        ("GW",),
        "enum 5m,1h",
        None,
        ("5m", "5m", "1h"),
        "Anthropic cache TTL (1h costs 2x to write)",
    ),
    _k(
        "caching.sticky_session",
        "O",
        ("GW",),
        "bool",
        None,
        (True, True, True),
        "session_id = run uuid on every forwarded call",
    ),
    _k(
        "routing.provider_sort",
        "C",
        ("GW",),
        "enum none,price,throughput,latency",
        None,
        ("price", "none", "none"),
        "OpenRouter provider.sort (never the object form)",
    ),
    _k(
        "routing.max_price",
        "C",
        ("GW",),
        "{prompt, completion} | 'model\u00d7k' | null",
        "usd_per_mtok",
        ("model\u00d71.25", None, None),
        "OpenRouter provider.max_price (hard filter; no provider -> infra_error:no_provider)",
    ),
    _k(
        "routing.allow_fallbacks",
        "C",
        ("GW",),
        "bool",
        None,
        (True, True, True),
        "OpenRouter provider.allow_fallbacks",
    ),
    _k("routing.only", "C", ("GW",), "list[str]", None, ([], [], []), "Provider allow list"),
    _k("routing.ignore", "C", ("GW",), "list[str]", None, ([], [], []), "Provider deny list"),
    _k("routing.order", "C", ("GW",), "list[str]", None, ([], [], []), "Provider order list"),
    _k(
        "routing.quantizations",
        "C",
        ("GW",),
        "list[str]",
        None,
        (list(_ALL_QUANT), list(_ALL_QUANT), list(_ALL_QUANT)),
        "Quantization floor",
    ),
    _k(
        "routing.require_parameters",
        "C",
        ("GW",),
        "bool",
        None,
        (True, True, True),
        "Only providers that support every forced parameter",
    ),
    _k(
        "routing.data_collection",
        "C",
        ("GW",),
        "enum allow,deny",
        None,
        ("allow", "allow", "allow"),
        "Privacy filter (shrinks the provider pool)",
    ),
    _k("routing.zdr", "C", ("GW",), "bool", None, (False, False, False), "Zero-data-retention filter"),
    _k(
        "routing.preferred_min_throughput",
        "C",
        ("GW",),
        "float|null",
        "tokens_per_s",
        (None, None, None),
        "Soft reordering",
    ),
    _k("routing.preferred_max_latency", "C", ("GW",), "float|null", "s", (None, None, None), "Soft reordering"),
    _k(
        "routing.serving_tier",
        "C",
        ("GW", "SLT"),
        "enum standard,flex,batch,auto_batch",
        None,
        ("auto_batch", "standard", "standard"),
        "batch only with slot_executor=single_shot; auto_batch resolves per study; agentic runs are standard",
        key_limit=True,
    ),
    # --- Time, retries, sandbox ---
    _k("time.wall_clock_s", "R", ("SBX",), "int", "s", (1200, 3600, 7200), "Harness wall clock (timeout -k 15)"),
    _k(
        "time.idle_timeout_s",
        "R",
        ("SBX",),
        "int|null",
        "s",
        (300, 600, 1200),
        "Stop when no gateway call ended and no workspace change for N s (paused while in flight or CPU busy)",
    ),
    _k(
        "time.command_timeout_s",
        "C",
        ("HAR",),
        "int|null",
        "s",
        (120, 300, 600),
        "Per-shell-command timeout (overrides the preset when set)",
    ),
    _k("time.llm_call_timeout_s", "O", ("GW",), "int", "s", (300, 600, 900), "Upstream timeout per call"),
    _k(
        "retries.upstream_retries",
        "O",
        ("GW",),
        "int 0-8",
        "retries",
        (2, 4, 6),
        "Gateway retries before the first byte",
    ),
    _k("retries.backoff_s", "O", ("GW",), "float", "s", (2.0, 2.0, 3.0), "Base backoff"),
    _k(
        "retries.retry_on",
        "O",
        ("GW",),
        "set",
        None,
        (["429", "5xx"], ["408", "429", "5xx"], ["408", "429", "5xx"]),
        "Statuses retried",
    ),
    _k(
        "retries.infra_retries",
        "O",
        ("EXE",),
        "int",
        "retries",
        (1, 2, 3),
        "Re-queue a whole run after infra_error (same manifest and seed, attempt+1)",
    ),
    _k(
        "retries.count_failed_calls",
        "R",
        ("GW",),
        "bool",
        None,
        (False, False, False),
        "Whether failed upstream calls count toward run.max_llm_requests",
    ),
    _k(
        "sandbox.memory",
        "R",
        ("SBX",),
        "str",
        "bytes",
        ("2g", "3g", "4g"),
        "Agent container memory; resolved per study to max(policy, every stack profile minimum)",
    ),
    _k(
        "sandbox.nano_cpus",
        "R",
        ("SBX",),
        "int",
        "nano_cpus",
        (1_000_000_000, 2_000_000_000, 4_000_000_000),
        "Agent container CPUs; resolved per study like sandbox.memory",
    ),
    _k(
        "network_policy",
        "C",
        ("SBX", "GW"),
        "enum none,packages",
        None,
        ("packages", "packages", "packages"),
        "Sandbox egress",
    ),
    # --- Concurrency (copied to Study.max_parallel / execution_options at launch) ---
    _k(
        "concurrency.study_max_parallel",
        "O",
        ("SCH",),
        "int",
        "runs",
        (2, 4, 8),
        "Max running runs of the study",
        key_limit=True,
    ),
    _k(
        "concurrency.per_endpoint",
        "O",
        ("SCH",),
        "int|null",
        "runs",
        (1, None, None),
        "Extra cap below LLMEndpoint.max_concurrency",
    ),
    _k("concurrency.per_model", "O", ("SCH",), "int|null", "runs", (1, 2, None), "Max running runs per model"),
    _k(
        "concurrency.per_provider",
        "O",
        ("SCH",),
        "int|null",
        "runs",
        (None, None, None),
        "Max running runs per pinned upstream provider",
    ),
    _k(
        "concurrency.rpm_per_model",
        "O",
        ("SCH", "GW"),
        "int|null",
        "requests_per_min",
        (20, None, None),
        "Gateway-side rate limit per model across the study",
    ),
    _k(
        "concurrency.max_resident_models",
        "O",
        ("SCH",),
        "int|null",
        "models",
        (1, 1, 2),
        "Local endpoints: residency-aware blocking (scheduling=auto)",
    ),
    _k(
        "concurrency.eval_parallel",
        "O",
        ("SCH",),
        "int",
        "evaluations",
        (1, 2, 2),
        "Max concurrent evaluations of this study",
    ),
    # --- Cell and study (class S rules are in prereg_hash) ---
    _k(
        "cell.max_cost_usd",
        "O",
        ("LED",),
        "float",
        "usd",
        (0.60, 4.00, 20.00),
        "Sum over all attempts of a cell; beyond it, no retries (excluded:cell_budget)",
        key_limit=True,
    ),
    _k(
        "study.max_cost_usd",
        "S",
        ("LED", "SCH"),
        "float | 'auto'",
        "usd",
        ("auto", "auto", "auto"),
        "Hard study cap; auto = round-up(p90_total x 1.1 / 1.25 / 1.5), confirmed in LaunchDialog",
        key_limit=True,
    ),
    _k(
        "study.max_total_tokens",
        "S",
        ("LED", "SCH", "STU"),
        "int|null",
        "tokens",
        (None, None, None),
        "Token cap that binds for local and subscription endpoints",
    ),
    _k("study.max_runs", "S", ("LED", "SCH", "STU"), "int|null", "runs", (None, None, None), "Run-count cap"),
    _k(
        "study.max_wall_hours",
        "S",
        ("LED", "SCH", "STU"),
        "float|null",
        "h",
        (None, None, None),
        "Wall-clock cap for the whole study",
    ),
    _k(
        "study.cap_basis",
        "O",
        ("LED",),
        "enum billed,list,auto",
        None,
        ("auto", "auto", "auto"),
        "auto = list when any condition uses an endpoint with cost_basis != billed",
    ),
    _k(
        "study.budget_basis",
        "R",
        ("PLN",),
        "enum usd,tokens,auto",
        None,
        ("auto", "auto", "auto"),
        "auto = tokens for confirmatory multi-model studies, else usd",
    ),
    _k(
        "study.alert_at",
        "O",
        ("LED",),
        "list[float]",
        "fraction",
        ([0.5, 0.8], [0.5, 0.8], [0.8]),
        "Fractions of the cap that fire budget_alert",
    ),
    _k(
        "study.on_budget_exhausted",
        "S",
        ("SCH",),
        "enum pause,stop_balanced,stop",
        None,
        ("stop_balanced", "pause", "pause"),
        "stop_balanced: finish started blocks, claim no new block",
    ),
    _k(
        "study.stop_rules",
        "S",
        ("STU",),
        "list[StopRule]",
        None,
        (_STOP_RULES, _STOP_RULES, _STOP_RULES),
        "Stop rules (section 4.6); default set in every tier",
    ),
    _k("pilot.enabled", "S", ("PLN",), "bool", None, (True, True, False), "Launch stage pilot first", key_limit=True),
    _k("pilot.repeats", "S", ("PLN",), "int", "repeats", (1, 1, 2), "Repeats per (condition, unit) in the pilot"),
    _k("pilot.units", "S", ("PLN",), "'all' | int", "units", (3, "all", "all"), "All units or a seeded random k"),
    _k(
        "pilot.smoke_first",
        "O",
        ("PLN", "SCH"),
        "bool",
        None,
        (True, True, False),
        "Before the pilot: 1 cell per new (harness, model) pair on the cheapest unit",
    ),
    _k(
        "pilot.reuse_as_main",
        "S",
        ("PLN",),
        "bool",
        None,
        (True, True, True),
        "Exploratory only, same plan_hash: pilot cells become main repeat 0 (forced false for confirmatory)",
    ),
    _k(f"{_GATES}.max_infra_error_rate", "S", ("STU",), "float", "fraction", (0.20, 0.20, 0.30), "infra_error / runs"),
    _k(f"{_GATES}.min_build_rate", "S", ("STU",), "float", "fraction", (0.50, 0.50, 0.30), "built / usable"),
    _k(f"{_GATES}.max_cost_overrun", "S", ("STU",), "float", "ratio", (1.5, 1.5, 2.0), "mean cost / estimate"),
    _k(
        f"{_GATES}.max_cap_hit_rate",
        "S",
        ("STU",),
        "float",
        "fraction",
        (0.30, 0.30, 0.50),
        "runs with budget_exhausted:*, wall_clock or idle_timeout notes",
    ),
    _k(
        f"{_GATES}.max_truncation_rate",
        "S",
        ("STU",),
        "float",
        "fraction",
        (0.20, 0.20, 0.20),
        "truncated runs (BUDGET, WALL_CLOCK, IDLE, MAX_TURNS)",
    ),
    _k(
        f"{_GATES}.max_call_truncation_rate",
        "S",
        ("STU",),
        "float",
        "fraction",
        (0.20, 0.20, 0.20),
        "runs with >= 1 call ending finish_reason=length",
    ),
    _k(
        f"{_GATES}.max_wall_clock_rate",
        "S",
        ("STU",),
        "float",
        "fraction",
        (0.15, 0.15, 0.15),
        "WALL_CLOCK terminations",
    ),
    _k(
        f"{_GATES}.max_oom_rate",
        "S",
        ("STU",),
        "float",
        "fraction",
        (0.05, 0.05, 0.05),
        "OOM terminations (per stack)",
    ),
    _k(
        f"{_GATES}.usd_cap_binding",
        "S",
        ("STU",),
        "float",
        "fraction",
        (0.05, 0.05, 0.05),
        "only when budget_basis=tokens: share of budget_exhausted:cost",
    ),
    _k(
        "pilot.auto_expand",
        "S",
        ("STU",),
        "bool",
        None,
        (False, False, True),
        "Expand automatically when every gate passes",
    ),
    # --- Caches and slot calls ---
    _k(
        "caches.dependency_cache",
        "O",
        ("SBX", "BLD"),
        "enum off,shared_ro",
        None,
        ("shared_ro", "shared_ro", "shared_ro"),
        "Read-only pull-through caches for pip/npm/go/maven",
    ),
    _k(
        "caches.reuse_sample_images",
        "O",
        ("EVL",),
        "bool",
        None,
        (True, True, True),
        "Reuse SampleBuildArtifact images by build key",
    ),
    _k(
        "caches.reuse_parent_build",
        "O",
        ("EVL",),
        "bool",
        None,
        (True, True, True),
        "Slot: build variants on the parent's BuildKit layers",
    ),
    _k(
        "caches.reuse_eval_by_tree",
        "O",
        ("EVL",),
        "bool",
        None,
        (True, True, True),
        "Reuse the RunEvaluation of an identical (tree_hash, suite hash, check selection)",
    ),
    _k(
        "slot.max_output_tokens",
        "R",
        ("GW", "SLT"),
        "int|null",
        "tokens",
        (4096, 8192, 16384),
        "Overrides call.max_output_tokens for single-shot slot calls",
    ),
    _k("estimate.method", "O", ("PLN",), "enum priors,heuristic", None, ("priors", "priors", "priors"), "Section 4.4"),
    _k(
        "estimate.require_confirm_above_usd",
        "O",
        ("UI",),
        "float",
        "usd",
        (2.0, 20.0, 100.0),
        "Typed confirmation above this p90 total",
    ),
)

KNOB_META: Final[Mapping[str, KnobMeta]] = {knob.key: knob for knob in _KNOBS}
if len(KNOB_META) != len(_KNOBS):  # pragma: no cover - guarded by tests
    msg = "duplicate knob key in KNOB_META"
    raise RuntimeError(msg)

# The UI's always-visible key limits (spec 4.2); "repeats" is a Study column, not a policy knob.
KEY_LIMITS: Final = tuple(knob.key for knob in _KNOBS if knob.key_limit)


# =====================================================================================
# Helpers over the knob tree
# =====================================================================================


def iter_leaf_paths(model: type[BaseModel] = ResourcePolicyContent, prefix: str = "") -> Iterator[str]:
    """Every leaf field path of a policy model (``run.max_cost_usd``, ``pilot.gates.min_build_rate``, ...)."""
    for name, info in model.model_fields.items():
        path = f"{prefix}{name}"
        annotation = info.annotation
        if isinstance(annotation, type) and issubclass(annotation, ContractModel) and annotation is not MaxPrice:
            yield from iter_leaf_paths(annotation, f"{path}.")
        else:
            yield path


def knob_for_path(path: str) -> KnobMeta:
    """The ``KNOB_META`` entry governing a leaf path: the exact key, else the longest dotted prefix."""
    candidate = path
    while True:
        if candidate in KNOB_META:
            return KNOB_META[candidate]
        if "." not in candidate:
            msg = f"no KNOB_META entry covers {path!r}"
            raise KeyError(msg)
        candidate = candidate.rsplit(".", 1)[0]


def knob_class(key: str, budget_basis: Literal["usd", "tokens"] = "usd") -> KnobClass:
    knob = KNOB_META[key]
    if budget_basis == "tokens" and knob.cls_when_tokens is not None:
        return knob.cls_when_tokens
    return knob.cls


def _get_path(data: Mapping[str, Any], dotted: str) -> Any:
    value: Any = data
    for part in dotted.split("."):
        value = value[part]
    return value


def knob_values(content: ResourcePolicyContent) -> dict[str, Any]:
    """``{knob_key: value}`` for every knob (JSON types). Group knobs (``reasoning``) map to their sub-dict."""
    dumped = content.model_dump(mode="json")
    out: dict[str, Any] = {}
    for key in KNOB_META:
        value = _get_path(dumped, key)
        if key == "reasoning":
            value = {k: v for k, v in value.items() if k != "exclude"}
        out[key] = value
    return out


def values_by_class(
    content: ResourcePolicyContent,
    classes: frozenset[str] | set[str],
    *,
    budget_basis: Literal["usd", "tokens"],
) -> dict[str, Any]:
    """The flat ``{knob_key: value}`` subset whose class (under ``budget_basis``) is in ``classes``.

    ``budget/policy.py`` builds ``treatment_view()`` as ``values_by_class(clamped, {"C", "R"}, ...)``
    with ``study.budget_basis`` already resolved; WP-11 owns that call.
    """
    return {key: value for key, value in knob_values(content).items() if knob_class(key, budget_basis) in classes}


def _set_path(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    if dotted == "reasoning":
        node.setdefault("reasoning", {}).update(copy.deepcopy(value))
    else:
        node[parts[-1]] = copy.deepcopy(value)


def preset_content(tier: Tier) -> ResourcePolicyContent:
    """The frugal / standard / generous preset built from the ``KNOB_META`` defaults (section 4.2 columns)."""
    if tier not in TIERS:
        msg = f"unknown tier {tier!r}; expected one of {TIERS}"
        raise ValueError(msg)
    data: dict[str, Any] = {}
    for knob in _KNOBS:
        _set_path(data, knob.key, knob.defaults[tier])
    return ResourcePolicyContent.model_validate(data)


# =====================================================================================
# Effective policy and gateway configuration
# =====================================================================================


class PolicyClamp(ContractModel):
    key: str
    from_value: Any = Field(alias="from")
    to_value: Any = Field(alias="to")
    reason: str


class EffectivePolicy(ContractModel):
    """Output of ``budget/policy.py:effective_policy``: content after clamps, plus notes and lint issues."""

    content: ResourcePolicyContent
    budget_basis: Literal["usd", "tokens"]
    cap_basis: Literal["billed", "list"]
    serving_tier: Literal["standard", "flex", "batch"]
    clamps: list[PolicyClamp] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # e.g. reasoning_unsupported, batch_unsupported
    issues: list[Issue] = Field(default_factory=list)


class GatewayPricing(ContractModel):
    """USD per million tokens, from the manifest's frozen pricing."""

    input_per_mtok: NonNegFloat
    output_per_mtok: NonNegFloat
    cached_input_per_mtok: NonNegFloat | None = None
    cache_write_per_mtok: NonNegFloat | None = None
    source: str
    as_of: str


class GatewayUpstream(ContractModel):
    kind: Literal["openrouter", "local_proxy"]
    base_url: NonEmptyStr
    endpoint_id: int | None = None


class GatewayModel(ContractModel):
    upstream_model: NonEmptyStr  # what the gateway sends upstream (the pin)
    served_model_name: str | None = None
    presented_names: list[str] = Field(default_factory=list)  # names a harness may send (all mapped to the pin)
    context_length: PosInt | None = None
    max_output: PosInt | None = None
    supports_reasoning: bool = False


class GatewaySampling(ContractModel):
    forced: dict[str, Any] = Field(default_factory=dict)
    stripped: list[str] = Field(default_factory=list)
    seed: NonNegInt | None = None
    forward_seed: bool = True


class GatewayBudget(ContractModel):
    max_cost_usd: NonNegFloat
    cost_basis: Literal["billed", "list_price", "free"]
    max_total_tokens: PosInt
    max_uncached_input_tokens: PosInt | None = None
    max_output_tokens: PosInt | None = None
    max_llm_requests: PosInt
    max_aux_requests: NonNegInt
    count_failed_calls: bool = False


class GatewayCall(ContractModel):
    max_output_tokens: PosInt
    max_input_tokens: PosInt | None = None


class GatewayRouting(ContractModel):
    provider: dict[str, Any] = Field(default_factory=dict)  # the OpenRouter provider object, already resolved
    serving_tier: Literal["standard", "flex", "batch"] = "standard"


class GatewayCaching(ContractModel):
    prompt_cache: Literal["auto", "on", "off"]
    ttl: Literal["5m", "1h"]
    sticky_session: bool
    cache_control_providers: list[str] = Field(default_factory=list)


class GatewayRetries(ContractModel):
    upstream_retries: Annotated[int, Field(ge=0, le=8)]
    backoff_s: NonNegFloat
    retry_on: list[RetryStatus]
    llm_call_timeout_s: PosInt


class GatewayRateLimit(ContractModel):
    rpm_per_model: PosInt | None = None
    bucket_key: str | None = None  # shared token bucket key across the study


class GatewayConfig(ContractModel):
    """``GATEWAY_CONFIG`` for one attempt, built by ``budget/gateway_config.py:build(run)`` (WP-11)."""

    config_version: Literal[1] = 1
    run_id: NonEmptyStr
    attempt: PosInt
    session_id: str | None = None  # = run uuid when caching.sticky_session
    front_protocol: Literal["openai_chat", "openai_responses", "anthropic_messages", "gemini_generate"]
    protocol_mode: Literal["native", "translate", "passthrough"]
    request_class_source: str = "default:main"
    upstream: GatewayUpstream
    model: GatewayModel
    pricing: GatewayPricing
    sampling: GatewaySampling = Field(default_factory=GatewaySampling)
    reasoning: ReasoningSettings
    budget: GatewayBudget
    call: GatewayCall
    routing: GatewayRouting = Field(default_factory=GatewayRouting)
    caching: GatewayCaching
    retries: GatewayRetries
    rate_limit: GatewayRateLimit = Field(default_factory=GatewayRateLimit)
    log_native_system_prompt: bool = True
