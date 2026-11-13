"""Study design and resolved-plan contracts (spec sections 4.4, 4.8, 6.1, 9.4).

``Design`` is the editable draft stored on ``Study.design``; ``design_hash`` is
``identity.design_hash(design.model_dump(mode="json"))``. ``ResolvedPlan`` is
what ``study/plan.py:resolve`` returns (and, when persisted, the payload blob
of a ``StudyPlan``). Mode-specific validation (units of one task, allowed
factors per mode, primary features) is done by ``study/design.py`` and
reported as registered issues; the schemas here check structure only.
"""

from typing import Annotated
from typing import Any
from typing import Final
from typing import Literal
from typing import get_args

from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from backend.generation.contracts._base import ContractModel
from backend.generation.contracts._base import Fraction
from backend.generation.contracts._base import NonEmptyStr
from backend.generation.contracts._base import NonNegFloat
from backend.generation.contracts._base import NonNegInt
from backend.generation.contracts._base import PosInt
from backend.generation.contracts._base import Sha256
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.manifest import ManifestBody
from backend.generation.contracts.manifest import StimulusRecord
from backend.generation.contracts.slot import ParentSelection
from backend.generation.contracts.slot import ResolvedContext

__all__ = [
    "APP_FACTORS",
    "FACTOR_KEYS",
    "MAX_REPEATS",
    "SLOT_FACTORS",
    "CellEstimate",
    "ConditionEstimate",
    "Design",
    "Estimate",
    "EstimateFit",
    "EstimateTime",
    "EvaluationOverrides",
    "EvaluationSpec",
    "ExecutionOptions",
    "FactorKey",
    "FactorSpec",
    "GridSpec",
    "ParentSelection",
    "ResolvedCell",
    "ResolvedCondition",
    "ResolvedPlan",
    "ResolvedUnit",
    "SlotEvaluation",
    "StopReason",
    "UnitSpec",
    "cell_key",
]

MAX_REPEATS: Final = 200

FactorKey = Literal[
    "model",
    "harness_preset",
    "stack_profile",
    "starter_level",
    "style",
    "auth_mode",
    "guidance",
    "prompt_set",
    "prose_variant",
    "sampling_preset",
    "resource_policy",
    "network_policy",
    "slot_executor",
    "hole_mode",
    "keep_docstring",
    "context_policy",
]
FACTOR_KEYS: Final[tuple[str, ...]] = get_args(FactorKey)
# Factors allowed per mode (section 7.10; stack_profile, starter, auth_mode and task are fixed by slot parents).
SLOT_FACTORS: Final = frozenset(
    {
        "model",
        "slot_executor",
        "prompt_set",
        "sampling_preset",
        "resource_policy",
        "guidance",
        "style",
        "harness_preset",
        "hole_mode",
        "keep_docstring",
        "context_policy",
    },
)
APP_FACTORS: Final = frozenset(FACTOR_KEYS) - {"slot_executor", "hole_mode", "keep_docstring", "context_policy"}


def cell_key(condition_index: int, unit_index: int, repeat_index: int, stage: str) -> str:
    """Stable key of a cell inside one plan: ``"c<cond>-u<unit>-r<repeat>-<stage>"``.

    The stage is part of the key: pilot and main repeats are both numbered from 0 (``StudyCell`` is unique
    on ``(study, condition, unit, stage, repeat_index)``).
    """
    if stage not in {"pilot", "main"}:
        msg = f"stage must be 'pilot' or 'main', not {stage!r}"
        raise ValueError(msg)
    return f"c{condition_index}-u{unit_index}-r{repeat_index}-{stage}"


class FactorSpec(ContractModel):
    key: FactorKey
    role: Literal["crossed", "fixed", "fraction"]
    levels: list[NonEmptyStr] = Field(min_length=1)  # pinned refs or enum values

    @field_validator("levels")
    @classmethod
    def _unique(cls, levels: list[str]) -> list[str]:
        if len(set(levels)) != len(levels):
            msg = f"duplicate factor levels: {levels}"
            raise ValueError(msg)
        return levels


class UnitSpec(ContractModel):
    """App: task versions; slot: targets across slot sets of one task."""

    tasks: list[str] = Field(default_factory=list)  # TaskSpecVersion refs (prose variant is a factor)
    slot_sets: list[str] = Field(default_factory=list)  # SlotSet uuids (validated: one task)
    slot_targets: list[str] = Field(default_factory=list)  # SlotTarget uuids


class GridFraction(ContractModel):
    generator: NonEmptyStr  # e.g. "D=ABC"


class GridSpec(ContractModel):
    type: Literal["full", "fractional"] = "full"
    fraction: GridFraction | None = None
    exclude: list[dict[str, str]] = Field(default_factory=list)  # level combos {factor_key: level}


class EvaluationOverrides(ContractModel):
    build: bool | None = None
    functional: bool | None = None
    probes: bool | None = None
    adherence: bool | None = None
    baseline: bool | None = None
    analysis_static: Literal["none", "default"] | list[str] | None = None
    analysis_dynamic: bool | None = None
    only_passing: bool | None = None
    human_eval_pool: bool | None = None


class SlotEvaluation(ContractModel):
    mode: Literal["full", "overlay", "static_only"] = "full"
    regression: bool = False


class EvaluationSpec(ContractModel):
    """``Design.evaluation`` (section 4.8): part of the design and of ``prereg_hash``, not of the budget preset."""

    eval_suite: NonEmptyStr  # pinned ref
    eval_level: Literal["none", "static", "build", "functional", "security", "full"] = "security"
    overrides: EvaluationOverrides = Field(default_factory=EvaluationOverrides)
    slot: SlotEvaluation | None = None
    retest_fraction: Fraction = 0.05


class Design(ContractModel):
    mode: Literal["app", "slot"]
    kind: Literal["exploratory", "confirmatory"]
    factors: list[FactorSpec]
    units: UnitSpec
    repeats: Annotated[int, Field(ge=1, le=MAX_REPEATS)]
    grid: GridSpec = Field(default_factory=GridSpec)
    base_seed: NonNegInt
    seed_rule: Literal["crn_unit_repeat"] = "crn_unit_repeat"
    scheduling: Literal["auto", "interleaved", "blocked"] = "auto"
    order_seed: NonNegInt
    resource_policy: NonEmptyStr  # study-level policy ref (default level for factor resource_policy)
    evaluation: EvaluationSpec
    parent_selection: ParentSelection | None = None  # slot only
    slot_primary_features: list[str] = Field(default_factory=list)  # detector refs (slot)
    # confirmatory: required (ported schema + patterns, sequential, estimand, random_effects, itt, sensitivity)
    analysis_plan: dict[str, Any] | None = None

    @field_validator("factors")
    @classmethod
    def _unique_keys(cls, factors: list[FactorSpec]) -> list[FactorSpec]:
        keys = [factor.key for factor in factors]
        if len(set(keys)) != len(keys):
            msg = f"duplicate factor keys: {keys}"
            raise ValueError(msg)
        return factors


# ---------------------------------------------------------------------------
# Estimates: section 4.4
# ---------------------------------------------------------------------------


class CellEstimate(ContractModel):
    """One cell's estimate. ``*_usd`` is what the endpoint bills (0 on subscription/local endpoints);
    ``*_list_usd`` is the catalog (list) price, the unit of a ``cap_basis: list`` study. Both are truncated at
    ``cap_usd``. ``p10_list_usd``/``p90_list_usd`` are ``None`` on estimates stored before they existed
    (readers fall back to scaling the billed spread, see ``budget.estimate.list_p90``)."""

    expected_usd: NonNegFloat
    expected_list_usd: NonNegFloat
    p10_usd: NonNegFloat
    p90_usd: NonNegFloat
    p10_list_usd: NonNegFloat | None = None
    p90_list_usd: NonNegFloat | None = None
    cap_usd: NonNegFloat  # theoretical worst case per attempt; what the scheduler reserves
    expected_tokens: NonNegInt
    expected_wall_s: NonNegFloat
    p90_wall_s: NonNegFloat
    basis: NonEmptyStr  # "prior:<key>(n)" | "heuristic"


class ConditionEstimate(ContractModel):
    condition_index: NonNegInt
    n_cells: NonNegInt
    expected_usd: NonNegFloat
    p90_usd: NonNegFloat
    expected_list_usd: NonNegFloat = 0.0
    p90_list_usd: NonNegFloat = 0.0


class EstimateFit(ContractModel):
    max_repeats_under_cap: NonNegInt | None = None
    max_units_under_cap: NonNegInt | None = None
    blocks_under_cap: NonNegInt | None = None


class EstimateTime(ContractModel):
    expected_wall_s_low: NonNegFloat
    expected_wall_s_high: NonNegFloat
    effective_parallel: PosInt
    eval_machine_minutes: NonNegFloat = 0.0


class Estimate(ContractModel):
    """``StudyPlan.estimate`` and the ``/budget/estimate/`` response."""

    expected_total: NonNegFloat
    p90_total: NonNegFloat  # sum of p90 (conservative, documented as such)
    worst_case_total: NonNegFloat
    expected_list_total: NonNegFloat = 0.0
    p90_list_total: NonNegFloat = 0.0  # sum of cell list p90s
    # The unit the study cap counts (``study.cap_basis``): ``cap_usd``, ``fit``, ``by_level``, the
    # ``estimate_exceeds_cap`` issue and the launch confirmation all use the totals of this basis
    # (billed: ``expected_total``/``p90_total``; list: ``expected_list_total``/``p90_list_total``).
    cap_basis: Literal["billed", "list"] = "billed"
    expected_tokens_total: NonNegInt = 0
    cap_usd: NonNegFloat | None = None  # resolved from "auto" when applicable
    by_condition: list[ConditionEstimate] = Field(default_factory=list)
    by_level: dict[str, dict[str, NonNegFloat]] = Field(default_factory=dict)
    fit: EstimateFit = Field(default_factory=EstimateFit)
    time: EstimateTime | None = None
    method: Literal["priors", "heuristic", "mixed"] = "priors"
    inputs: dict[str, Any] = Field(default_factory=dict)  # prior keys, history window, prices used
    issues: list[Issue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Resolved plan (6.1)
# ---------------------------------------------------------------------------


class ResolvedCondition(ContractModel):
    index: NonNegInt
    label: str
    condition_hash: Sha256
    levels: dict[str, str]  # factor_key -> pinned ref or enum value
    treatment_hash: Sha256 | None = None
    is_baseline: bool = False


class ResolvedUnit(ContractModel):
    index: NonNegInt
    unit_key: NonEmptyStr  # "task:<slug>@<v>" or "slot:<target uuid>"
    task_version: str | None = None  # pinned ref (app)
    slot_target: str | None = None  # uuid (slot)
    unit_order: NonNegInt


class ResolvedCell(ContractModel):
    """One planned sample, fully pinned; ``agentic/manifest.py`` copies ``manifest`` into the RunManifest."""

    cell_key: NonEmptyStr
    condition_index: NonNegInt
    unit_index: NonNegInt
    repeat_index: NonNegInt
    block_index: NonNegInt
    sequence: NonNegInt
    stage: Literal["pilot", "main"]
    seed: NonNegInt
    unit_key: NonEmptyStr
    levels: dict[str, str]
    manifest: ManifestBody  # includes the identity keys computed by identity.compute_keys
    stimuli: list[StimulusRecord] = Field(default_factory=list)
    effective_policy: dict[str, Any] = Field(default_factory=dict)
    slot_context: ResolvedContext | None = None
    estimate: CellEstimate | None = None
    excluded: Literal["config_invalid"] | None = None
    issues: list[Issue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _key_matches(self) -> "ResolvedCell":
        expected = cell_key(self.condition_index, self.unit_index, self.repeat_index, self.stage)
        if self.cell_key != expected:
            msg = f"cell_key {self.cell_key!r} does not match its indices and stage ({expected!r})"
            raise ValueError(msg)
        return self


class ResolvedPlan(ContractModel):
    resolver_version: PosInt
    design_hash: Sha256
    plan_hash: Sha256
    conditions: list[ResolvedCondition]
    units: list[ResolvedUnit]
    cells: list[ResolvedCell]
    n_blocks: NonNegInt
    prompt_hashes: dict[str, Sha256] = Field(default_factory=dict)  # cell_key -> prompt_hash
    estimate: Estimate | None = None
    issues: list[Issue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Study columns filled at launch (section 3.6). Added by WP-01 A3 (models).
# ---------------------------------------------------------------------------


class ExecutionOptions(ContractModel):
    """``Study.execution_options``: filled from the effective policy's ``concurrency`` section at launch.

    ``None`` means "no limit of this kind" (as in ``ConcurrencySettings``); ``warmup`` applies to local
    (ollama) endpoints only.
    """

    max_resident_models: PosInt | None = None
    per_model: PosInt | None = None
    per_endpoint: PosInt | None = None
    per_provider: PosInt | None = None
    rpm_per_model: PosInt | None = None
    eval_parallel: PosInt | None = None
    warmup: bool = True


class StopReason(ContractModel):
    """``Study.stop_reason``: why a study stopped (budget, a stop rule, the user or infra)."""

    code: NonEmptyStr
    detail: str = ""
    at: NonEmptyStr  # ISO 8601 timestamp
    rule: str | None = None  # the stop-rule code when a rule fired
