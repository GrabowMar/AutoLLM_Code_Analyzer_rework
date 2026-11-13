"""Cost and time estimation, fit-to-budget and the ``auto`` cap (spec section 4.4).

``estimate_cell`` gives one cell's ``CellEstimate``; ``estimate_plan`` the study ``Estimate`` (totals,
``by_condition``, ``by_level``, ``fit``, time, the resolved ``auto`` cap and the ``estimate_exceeds_cap``
issue). The arithmetic is in ``estimate_inputs(CellInputs)``, a pure function, so the resolver, the slot
picker's cost hint and the tests share it.

Conventions:

* Amounts are USD. ``expected_usd`` is what the endpoint bills (0 for subscription and local endpoints);
  ``expected_list_usd`` (and ``p10_list_usd``/``p90_list_usd``) is always priced from the manifest's frozen
  catalog prices (a prior's list percentiles, or the heuristic before calibration).
* The plan's cap side is in the study's ``cap_basis`` (``Estimate.cap_basis``): the ``auto`` cap, the fit,
  ``by_level`` and ``estimate_exceeds_cap`` use the list totals for a list-basis study and the billed ones
  otherwise (``basis_totals``).
* Estimates are truncated at the cell's per-attempt worst case ``cap_usd`` (the gateway refuses calls past
  ``run.max_cost_usd``), so ``p90 <= cap_usd``.
* A model without known prices gets the run cap as its estimate (``basis`` ends ``:unpriced``), never 0.
* ``p90_total`` is the **sum** of cell p90s: conservative, and documented as such.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Final
from typing import Literal

from backend.generation.budget import constants as k
from backend.generation.budget import priors as prior_store
from backend.generation.budget.model_facts import ModelFacts
from backend.generation.budget.model_facts import model_facts
from backend.generation.budget.policy import policy_content
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import IssueFix
from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.plan import CellEstimate
from backend.generation.contracts.plan import ConditionEstimate
from backend.generation.contracts.plan import Estimate
from backend.generation.contracts.plan import EstimateFit
from backend.generation.contracts.plan import EstimateTime
from backend.generation.contracts.plan import ResolvedCell
from backend.generation.contracts.plan import ResolvedPlan

if TYPE_CHECKING:
    from backend.generation.contracts.resources import ResourcePolicyContent

__all__ = [
    "CellInputs",
    "auto_cap",
    "basis_totals",
    "est_tokens",
    "estimate_cell",
    "estimate_inputs",
    "estimate_plan",
    "inputs_from_cell",
    "list_p90",
    "plan_cap_basis",
    "reestimate_after_pilot",
    "resolve_study_cap",
]

_MTOK = 1_000_000


# =====================================================================================
# Token estimate
# =====================================================================================


@lru_cache(maxsize=1)
def _encoder() -> Any:
    """tiktoken's ``o200k_base`` only when its cache is seeded (never a download at estimate time)."""
    cache = os.environ.get("TIKTOKEN_CACHE_DIR")
    if not cache or not Path(cache).is_dir():
        return None
    try:
        import tiktoken

        return tiktoken.get_encoding("o200k_base")
    except Exception:  # noqa: BLE001 — optional extra; any failure means the char heuristic
        return None


def est_tokens(text: str) -> tuple[int, str]:
    """``(tokens, method)``: tiktoken ``o200k_base`` when available, else ``ceil(chars / 3.6)``."""
    encoder = _encoder()
    if encoder is not None:
        return len(encoder.encode(text)), "tiktoken:o200k_base"
    return math.ceil(len(text) / k.CHARS_PER_TOKEN), "chars/3.6"


def _chars_to_tokens(chars: int) -> int:
    return math.ceil(chars / k.CHARS_PER_TOKEN)


# =====================================================================================
# Cell estimate
# =====================================================================================


@dataclass(frozen=True)
class CellInputs:
    """Everything one cell's estimate depends on."""

    kind: str  # app_agentic | slot_single_shot | slot_agent | reference
    model: ModelFacts
    policy: ResourcePolicyContent  # the effective (clamped) content
    harness: str = ""
    size_bucket: str = "M"
    tier: str = "custom"
    serving_tier: str = "standard"
    brief_tokens: int = 0  # rendered stimuli (app: brief; slot single-shot: system + user)
    original_tokens: int = 0  # slot: the original part
    context_tokens: int = 0  # slot single-shot: the shared context inside brief_tokens
    n_replicates: int = 1  # slot single-shot: replicates sharing the context (cache)
    context_file_tokens: int = 0  # slot agent: the context-file listing
    eval_level: str = "security"
    cost_basis: str = "billed"  # the endpoint's: billed | list_price | free


def _r_avg(policy: ResourcePolicyContent) -> float:
    reasoning = policy.reasoning
    if reasoning.mode == "max_tokens" and reasoning.max_tokens:
        return reasoning.max_tokens * k.REASONING_BUDGET_SHARE
    if reasoning.mode == "effort" and reasoning.effort:
        return float(k.R_AVG.get(reasoning.effort, 0))
    return float(k.R_AVG["provider_default"])


def _context_ceiling(inputs: CellInputs) -> float:
    policy, model = inputs.policy, inputs.model
    limits = [
        v for v in (policy.context.max_context_tokens or model.context_length, policy.call.max_input_tokens) if v
    ]
    return float(min(limits)) if limits else math.inf


def _cache_terms(inputs: CellInputs, hit_share: float) -> tuple[float, float]:
    """``(h, d)``: the cached share of input and the cached-price discount."""
    if inputs.policy.caching.prompt_cache == "off" or not inputs.model.caches:
        return 0.0, 0.0
    discount = inputs.model.cache_discount
    return hit_share, (k.DEFAULT_CACHE_DISCOUNT if discount is None else discount)


def _agentic(inputs: CellInputs) -> dict[str, float]:
    policy, model = inputs.policy, inputs.model
    r0 = k.R0.get(inputs.harness, k.R0_DEFAULT)
    if inputs.kind == "slot_agent":
        r0 = max(1, round(r0 * k.SLOT_AGENT_R0_FACTOR))
    requests = min(policy.run.max_llm_requests, r0)
    p0 = (
        inputs.brief_tokens
        + inputs.context_file_tokens
        + k.HARNESS_OVERHEAD.get(inputs.harness, k.HARNESS_OVERHEAD_DEFAULT)
    )
    ceiling = _context_ceiling(inputs)
    prompt = sum(min(p0 + k.PROMPT_GROWTH_PER_CALL * i, ceiling) for i in range(1, requests + 1))
    out = requests * min(policy.call.max_output_tokens, k.OUTPUT_PER_CALL_CAP)
    reasoning = requests * _r_avg(policy)
    hit, discount = _cache_terms(inputs, k.CACHE_HIT_SHARE)
    p_in = (model.input_per_mtok or 0.0) / _MTOK
    p_out = (model.output_per_mtok or 0.0) / _MTOK
    cost = prompt * p_in * (1 - hit * discount) + (out + reasoning) * p_out
    if hit and model.explicit_cache and policy.caching.prompt_cache == "on" and model.cache_write_per_mtok is not None:
        # Explicit-cache providers bill cache writes at their own price (the uncached share is written).
        writes = prompt * (1 - hit)
        cost += writes * (model.cache_write_per_mtok / _MTOK - p_in)
    return {
        "expected": cost,
        "p10": cost * k.AGENTIC_P10,
        "p90": cost * k.AGENTIC_P90,
        "tokens": prompt + out + reasoning,
        "requests": float(requests),
    }


def _slot_single_shot(inputs: CellInputs) -> dict[str, float]:
    policy, model = inputs.policy, inputs.model
    cap = policy.slot.max_output_tokens or policy.call.max_output_tokens
    prompt = float(inputs.brief_tokens)
    out = min(cap, k.SLOT_OUTPUT_FACTOR * inputs.original_tokens + k.SLOT_OUTPUT_SLACK) + _r_avg(policy)
    share = (inputs.context_tokens / prompt) if prompt else 0.0
    n = max(1, inputs.n_replicates)
    hit, discount = _cache_terms(inputs, (n - 1) / n * min(1.0, share))
    p_in = (model.input_per_mtok or 0.0) / _MTOK
    p_out = (model.output_per_mtok or 0.0) / _MTOK
    tier_factor = k.BATCH_TIER_FACTOR if inputs.serving_tier == "batch" else 1.0
    cost = (prompt * p_in * (1 - hit * discount) + out * p_out) * tier_factor
    return {
        "expected": cost,
        "p10": cost * k.SLOT_P10,
        "p90": cost * k.SLOT_P90,
        "tokens": prompt + out,
        "requests": 1.0,
    }


def _cap_usd(inputs: CellInputs) -> float:
    """4.4 step 5: the theoretical worst case per attempt (single-shot: one call), at most the run cap."""
    policy, model = inputs.policy, inputs.model
    run_cap = policy.run.max_cost_usd
    if not model.priced:
        return run_cap
    p_in = (model.input_per_mtok or 0.0) / _MTOK
    p_out = (model.output_per_mtok or 0.0) / _MTOK
    reasoning_budget = policy.reasoning.max_tokens or 0
    if inputs.kind == "slot_single_shot":
        out_cap = (policy.slot.max_output_tokens or policy.call.max_output_tokens) + reasoning_budget
        worst = inputs.brief_tokens * p_in + out_cap * p_out
        return min(run_cap, worst)
    ceiling = _context_ceiling(inputs)
    if math.isinf(ceiling):
        return run_cap
    per_call = ceiling * p_in + (policy.call.max_output_tokens + reasoning_budget) * p_out
    worst = policy.run.max_llm_requests * per_call + policy.run.max_aux_requests * per_call
    return min(run_cap, worst)


def _wall(inputs: CellInputs, prior: Any | None) -> tuple[float, float]:
    limit = float(inputs.policy.time.wall_clock_s)
    if prior is not None and prior.wall_s_p50:
        expected = float(prior.wall_s_p50)
        p90 = float(prior.wall_s_p90 or expected * k.WALL_P90)
    elif inputs.kind == "slot_single_shot":
        expected = k.SLOT_CALL_WALL_S
        p90 = expected * k.WALL_P90
    else:
        expected = k.WALL_S_BY_SIZE.get(inputs.size_bucket, k.WALL_S_DEFAULT)
        if inputs.kind == "slot_agent":
            expected *= k.SLOT_AGENT_R0_FACTOR
        p90 = expected * k.WALL_P90
    if inputs.kind != "slot_single_shot":
        expected, p90 = min(expected, limit), min(p90, limit)
    return expected, p90


def estimate_inputs(
    inputs: CellInputs,
    *,
    priors: Mapping[str, Any] | None = None,
    use_db_priors: bool = True,
) -> CellEstimate:
    """The ``CellEstimate`` of one cell (4.4 steps 1-7).

    ``priors`` replaces the CostPrior table (``{key: row}``); with ``use_db_priors=False`` and no ``priors``
    only the heuristic is used.
    """
    model, policy = inputs.model, inputs.policy
    cap = _cap_usd(inputs)
    source = priors if priors is not None else (None if use_db_priors else {})
    found = None
    if policy.estimate.method == "priors" and inputs.kind != "reference":
        found = prior_store.lookup(
            kind=inputs.kind,
            harness=inputs.harness,
            model=model.slug,
            family=model.family,
            size=inputs.size_bucket,
            tier=inputs.tier,
            priors=source,
        )
    if inputs.kind == "reference":
        return CellEstimate(
            expected_usd=0.0,
            expected_list_usd=0.0,
            p10_usd=0.0,
            p90_usd=0.0,
            p10_list_usd=0.0,
            p90_list_usd=0.0,
            cap_usd=0.0,
            expected_tokens=0,
            expected_wall_s=0.0,
            p90_wall_s=0.0,
            basis="heuristic:reference",
        )
    prior = found[1] if found else None
    if prior is not None:
        drift = _drift(prior, model)
        expected = float(prior.cost_p50) * drift
        p10 = float(prior.cost_p10 if prior.cost_p10 is not None else prior.cost_p50 * k.AGENTIC_P10) * drift
        p90 = float(prior.cost_p90 if prior.cost_p90 is not None else prior.cost_p50 * k.AGENTIC_P90) * drift
        lists = _prior_list(prior, drift, expected, p10, p90)
        tokens = (prior.prompt_tokens_p50 or 0) + (prior.completion_tokens_p50 or 0)
        basis = f"prior:{found[0] if found else ''}({prior.n})"
    else:
        terms = _slot_single_shot(inputs) if inputs.kind == "slot_single_shot" else _agentic(inputs)
        sort = policy.routing.provider_sort
        calib = prior_store.calibration(model.slug, sort, priors=source) if source != {} else 1.0
        expected, p10, p90 = terms["expected"] * calib, terms["p10"] * calib, terms["p90"] * calib
        # The heuristic prices tokens at the catalog (list) price; the calibration maps it to billed.
        lists = (terms["expected"], terms["p10"], terms["p90"])
        tokens = terms["tokens"]
        basis = "heuristic"
    # 6. failure overhead: x (1 + failed-call share) + infra_retry_rate x mean partial-run cost.
    over = prior_store.overhead(inputs.kind, inputs.harness, priors=source) if source != {} else None
    if over is not None:
        extra = over["infra_retry_rate"] * over["mean_partial_cost"]
        extra_list = over["infra_retry_rate"] * over["mean_partial_list_cost"]
        factor = 1 + over["failed_call_cost_share"]
        expected, p10, p90 = expected * factor + extra, p10 * factor + extra, p90 * factor + extra
        lists = tuple(v * factor + extra_list for v in lists)
    if not model.priced:
        expected, p10, p90 = cap, 0.0, cap
        lists = (cap, 0.0, cap)
        basis = f"{basis}:unpriced"
    expected, p10, p90 = min(expected, cap), min(p10, cap), min(p90, cap)
    # ``cap_usd`` is priced at the catalog price, so it bounds the list amounts too.
    list_expected, list_p10, list_p90 = (min(v, cap) for v in lists)
    if inputs.cost_basis in ("list_price", "free"):
        # Subscription and local endpoints bill nothing per call; list price is tracked against caps.
        billed_expected, billed_p10, billed_p90 = 0.0, 0.0, 0.0
    else:
        billed_expected, billed_p10, billed_p90 = expected, p10, p90
    wall, wall_p90 = _wall(inputs, prior)
    return CellEstimate(
        expected_usd=round(billed_expected, 8),
        expected_list_usd=round(list_expected, 8),
        p10_usd=round(billed_p10, 8),
        p90_usd=round(billed_p90, 8),
        p10_list_usd=round(list_p10, 8),
        p90_list_usd=round(list_p90, 8),
        cap_usd=round(cap, 8),
        expected_tokens=int(min(tokens, policy.run.max_total_tokens)),
        expected_wall_s=round(wall, 2),
        p90_wall_s=round(wall_p90, 2),
        basis=basis,
    )


def _prior_list(prior: Any, drift: float, expected: float, p10: float, p90: float) -> tuple[float, float, float]:
    """``(expected, p10, p90)`` at list price from a prior.

    The list percentiles of the prior when it has them. Otherwise the list median with the billed spread
    (or the default agentic spread when nothing was billed, as on a subscription endpoint), and without a
    list median the billed amounts (catalog = billed). A prior made of subscription runs bills 0 but has a
    real list cost, which a ratio to the billed amount would lose (the E2E-11 defect).
    """
    list_p50 = getattr(prior, "list_cost_p50", None)
    if list_p50 is None:
        return expected, p10, p90
    list_expected = float(list_p50) * drift
    list_p10 = getattr(prior, "list_cost_p10", None)
    list_p90 = getattr(prior, "list_cost_p90", None)
    if list_p10 is not None and list_p90 is not None:
        return list_expected, float(list_p10) * drift, float(list_p90) * drift
    if expected > 0:
        return list_expected, list_expected * p10 / expected, list_expected * p90 / expected
    return list_expected, list_expected * k.AGENTIC_P10, list_expected * k.AGENTIC_P90


def list_p90(estimate: CellEstimate) -> float:
    """The list p90 of a cell estimate (estimates stored before the field scale the billed spread)."""
    if estimate.p90_list_usd is not None:
        return estimate.p90_list_usd
    if estimate.expected_usd > 0:
        return estimate.expected_list_usd * estimate.p90_usd / estimate.expected_usd
    return min(estimate.cap_usd or math.inf, estimate.expected_list_usd * k.AGENTIC_P90)


def _drift(prior: Any, model: ModelFacts) -> float:
    """``price_now / price_at``, blended by the prior's token mix (1.0 when either side is unknown)."""
    price_at = prior.price_at if isinstance(prior.price_at, dict) else {}
    then_in, then_out = price_at.get("input_per_mtok"), price_at.get("output_per_mtok")
    if not model.priced or not isinstance(then_in, (int, float)) or not isinstance(then_out, (int, float)):
        return 1.0
    p = float(prior.prompt_tokens_p50 or 0) - float(prior.cached_tokens_p50 or 0)
    c = float(prior.completion_tokens_p50 or 0)
    if p <= 0 and c <= 0:
        p, c = 1.0, 1.0
    then = p * then_in + c * then_out
    now = p * (model.input_per_mtok or 0.0) + c * (model.output_per_mtok or 0.0)
    return (now / then) if then > 0 else 1.0


# =====================================================================================
# From a resolved cell
# =====================================================================================


def _size_bucket(task_ref: str | None) -> str:
    if not task_ref:
        return "M"
    from backend.generation.models import TaskSpecVersion

    slug, _, version = task_ref.split("#", 1)[0].partition("@")
    row = TaskSpecVersion.objects.filter(task__slug=slug, version=version or 0).only("derived").first()
    derived = row.derived if row is not None and isinstance(row.derived, dict) else {}
    return str(derived.get("size_bucket") or "M")


def _policy_tier(ref: str | None) -> str:
    if not ref:
        return "custom"
    from backend.generation.models import ResourcePolicy

    slug, _, version = ref.split("#", 1)[0].partition("@")
    tier = ResourcePolicy.objects.filter(slug=slug, version=version or 0).values_list("tier", flat=True).first()
    return str(tier or "custom")


def _model_row(slug: str) -> Any:
    from backend.llm_models.models import LLMModel

    return LLMModel.objects.select_related("endpoint").filter(model_id=slug).first()


def inputs_from_cell(cell: ResolvedCell, **overrides: Any) -> CellInputs:
    """``CellInputs`` from a ``ResolvedCell`` (manifest, stimuli, effective policy), plus overrides.

    Model limits come from the ``LLMModel`` row when it exists; prices are the manifest's frozen ones.
    """
    manifest = cell.manifest
    model_block = manifest.model
    slug = model_block.slug if model_block else "unknown"
    facts = overrides.pop("model", None)
    if facts is None:
        row = _model_row(slug) if model_block else None
        facts = model_facts(row) if row is not None else model_facts({"slug": slug})
        if model_block:
            facts = facts.with_pricing(model_block.pricing.model_dump())
    else:
        facts = model_facts(facts)
    effective = cell.effective_policy or (manifest.resources.effective if manifest.resources else {})
    policy = overrides.pop("policy", None)
    policy = policy_content(policy if policy is not None else effective) if (policy or effective) else None
    if policy is None:
        msg = f"cell {cell.cell_key} has no effective policy to estimate with"
        raise ValueError(msg)
    serving = (effective or {}).get("serving_tier") if isinstance(effective, dict) else None
    brief_chars = sum(s.chars for s in cell.stimuli)
    context_tokens = 0
    if cell.slot_context is not None:
        context_tokens = sum(getattr(f, "tokens", 0) or 0 for f in getattr(cell.slot_context, "files", []) or [])
    values: dict[str, Any] = {
        "kind": manifest.kind,
        "model": facts,
        "policy": policy,
        "harness": manifest.harness.name if manifest.harness else "",
        "size_bucket": _size_bucket(manifest.task.ref if manifest.task else None),
        "tier": _policy_tier(manifest.resources.ref if manifest.resources else None),
        "serving_tier": serving or (manifest.gateway.serving_tier if manifest.gateway else "standard"),
        "brief_tokens": _chars_to_tokens(brief_chars),
        "context_tokens": context_tokens,
        "eval_level": manifest.planned_evaluation.eval_level,
        "cost_basis": model_block.endpoint.cost_basis if model_block else "billed",
    }
    values.update(overrides)
    return CellInputs(**values)


def estimate_cell(cell: ResolvedCell, *, priors: Mapping[str, Any] | None = None, **overrides: Any) -> CellEstimate:
    """Prior-based (or heuristic) estimate of one cell."""
    return estimate_inputs(inputs_from_cell(cell, **overrides), priors=priors)


# =====================================================================================
# Plan estimate
# =====================================================================================


def auto_cap(p90_total: float, tier: str) -> float:
    """``study.max_cost_usd: auto`` = round-up(p90_total x 1.1 / 1.25 / 1.5) to the cent."""
    factor = k.AUTO_CAP_FACTOR.get(tier, k.AUTO_CAP_FACTOR_CUSTOM)
    return math.ceil(round(p90_total * factor * 100, 6)) / 100


def resolve_study_cap(policy: Any, p90_total: float, tier: str) -> float:
    """The study cap in USD: the policy's number, or ``auto_cap`` for ``"auto"``."""
    cap = policy_content(policy).study.max_cost_usd
    return auto_cap(p90_total, tier) if cap == "auto" else float(cap)


def plan_cap_basis(content: Any, pairs: Iterable[tuple[ResolvedCell, CellEstimate]] = ()) -> Literal["billed", "list"]:
    """The study cap's unit: the policy's ``study.cap_basis``; ``auto`` (an unclamped preset) resolves as
    ``budget.policy`` does: ``list`` when any cell's endpoint is not billed."""
    basis = content.study.cap_basis if content is not None else "billed"
    if basis == "auto":
        non_billed = any(
            cell.manifest.model is not None and cell.manifest.model.endpoint.cost_basis != "billed"
            for cell, _ in pairs
        )
        return "list" if non_billed else "billed"
    return "list" if basis == "list" else "billed"


def basis_totals(estimate: Estimate) -> tuple[float, float]:
    """``(expected, p90)`` of a plan estimate in its cap's unit (what the cap and the launch confirm use)."""
    if estimate.cap_basis == "list":
        return estimate.expected_list_total, estimate.p90_list_total
    return estimate.expected_total, estimate.p90_total


_STAGE_ORDER: Final = {"pilot": 0, "main": 1}


def _block_amounts(
    cells: list[tuple[ResolvedCell, CellEstimate]],
    amount: Callable[[CellEstimate], float],
) -> list[tuple[float, float]]:
    """``(expected, reserved)`` per replicate block in admission order (pilot blocks, then main blocks).

    ``reserved`` is what ``budget.claims`` reserves when the block's first run is admitted: every run's
    per-attempt worst case (``CellEstimate.cap_usd``, = ``ledger.run_need``)."""
    blocks: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for cell, est in cells:
        row = blocks[(_STAGE_ORDER.get(cell.stage, 1), cell.block_index)]
        row[0] += amount(est)
        row[1] += est.cap_usd
    return [(expected, reserved) for _key, (expected, reserved) in sorted(blocks.items())]


def _admitted_blocks(blocks: list[tuple[float, float]], cap: float) -> int:
    """How many blocks the scheduler admits under ``cap`` (E2E-28): a block is claimed only while the
    expected spend of the blocks before it plus the block's whole worst-case reservation fits."""
    committed, admitted = 0.0, 0
    for expected, reserved in blocks:
        if committed + reserved > cap + 1e-12:
            break
        committed += expected
        admitted += 1
    return admitted


def _fit(
    cells: list[tuple[ResolvedCell, CellEstimate]],
    cap: float,
    repeats: int,
    amount: Callable[[CellEstimate], float] = lambda e: e.expected_usd,
    run_cells: list[tuple[ResolvedCell, CellEstimate]] | None = None,
) -> EstimateFit:
    """``run_cells`` are the cells that become runs (a reused pilot cell once); default ``cells``."""
    main = [(c, e) for c, e in cells if c.stage == "main"] or cells
    by_unit_repeat: dict[tuple[int, int], float] = defaultdict(float)
    for cell, est in main:
        by_unit_repeat[(cell.unit_index, cell.repeat_index)] += amount(est)
    blocks_fit = _admitted_blocks(_block_amounts(run_cells if run_cells is not None else cells, amount), cap)
    n_repeats = max(1, repeats or len({r for _, r in by_unit_repeat}))
    per_unit: dict[int, float] = defaultdict(float)
    for (unit, _repeat), usd in by_unit_repeat.items():
        per_unit[unit] += usd / n_repeats
    per_repeat = sum(per_unit.values())
    max_repeats = int(cap // per_repeat) if per_repeat > 0 else None
    units_fit = 0
    spent = 0.0
    for unit in sorted(per_unit):
        spent += per_unit[unit] * n_repeats
        if spent > cap + 1e-12:
            break
        units_fit += 1
    return EstimateFit(
        max_repeats_under_cap=max_repeats,
        max_units_under_cap=units_fit if per_repeat > 0 else None,
        blocks_under_cap=blocks_fit,
    )


def estimate_plan(
    plan: ResolvedPlan,
    *,
    policy: Any = None,
    tier: str | None = None,
    study_kind: str = "exploratory",
    repeats: int | None = None,
    effective_parallel: int | None = None,
    priors: Mapping[str, Any] | None = None,
    cell_estimator: Callable[[ResolvedCell], CellEstimate] | None = None,
    cap_override: float | str | None = None,
) -> Estimate:
    """Totals, by condition and by level, with the fit, the time range and ``estimate_exceeds_cap``.

    ``policy`` is the study-level preset (default: the first cell's effective policy). ``cap_override``
    is a cap typed at launch (a number or ``"auto"``).
    """
    estimator = cell_estimator or (lambda cell: cell.estimate or estimate_cell(cell, priors=priors))
    pairs = [(cell, estimator(cell)) for cell in plan.cells if cell.excluded is None]
    if policy is None:
        first = next((c.effective_policy for c in plan.cells if c.effective_policy), None)
        policy = first
    content = policy_content(policy) if policy is not None else None
    if tier is None:
        first_cell = next((c for c in plan.cells if c.manifest.resources is not None), None)
        resources = first_cell.manifest.resources if first_cell is not None else None
        tier = _policy_tier(resources.ref) if resources is not None else "custom"
    plan_pairs = pairs
    pairs = _run_pairs(pairs, content, study_kind)
    expected = sum(e.expected_usd for _, e in pairs)
    p90 = sum(e.p90_usd for _, e in pairs)
    list_total = sum(e.expected_list_usd for _, e in pairs)
    list_p90_total = sum(list_p90(e) for _, e in pairs)
    tokens = sum(e.expected_tokens for _, e in pairs)
    infra_retries = content.retries.infra_retries if content else 0
    cell_cap = content.cell.max_cost_usd if content else math.inf
    worst = sum(min(cell_cap, e.cap_usd * (1 + infra_retries)) for _, e in pairs)
    cap: float | None = None
    raw_cap = cap_override if cap_override is not None else (content.study.max_cost_usd if content else None)
    basis = plan_cap_basis(content, pairs)
    # Everything compared with the study cap is in the cap's unit (E2E-13): a list-basis cap is priced
    # against list amounts, a billed one against billed amounts.
    if basis == "list":
        basis_expected, basis_p90 = list_total, list_p90_total

        def amount(e: CellEstimate) -> float:
            return e.expected_list_usd
    else:
        basis_expected, basis_p90 = expected, p90

        def amount(e: CellEstimate) -> float:
            return e.expected_usd

    if raw_cap == "auto":
        cap = auto_cap(basis_p90, tier)
        # "auto" must never stop the plan before it starts: the scheduler reserves each block's
        # per-attempt worst case before admitting it, so on a $0 (free/subscription) billed basis the
        # p90-based cap would admit nothing. Raise it to the smallest cap that admits every block.
        running, full = 0.0, 0.0
        for block_expected, block_reserved in _block_amounts(pairs, amount):
            full = max(full, running + block_reserved)
            running += block_expected
        cap = max(cap, math.ceil(round(full * 100, 6)) / 100)
    elif raw_cap is not None:
        cap = float(raw_cap)
    if cap is not None:
        worst = min(worst, cap)

    by_condition: dict[int, list[float]] = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0])
    by_level: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for cell, est in pairs:
        row = by_condition[cell.condition_index]
        row[0] += 1
        row[1] += est.expected_usd
        row[2] += est.p90_usd
        row[3] += est.expected_list_usd
        row[4] += list_p90(est)
        for factor, level in cell.levels.items():
            by_level[factor][level] += amount(est)
    conditions = [
        ConditionEstimate(
            condition_index=index,
            n_cells=int(values[0]),
            expected_usd=round(values[1], 6),
            p90_usd=round(values[2], 6),
            expected_list_usd=round(values[3], 6),
            p90_list_usd=round(values[4], 6),
        )
        for index, values in sorted(by_condition.items())
    ]
    levels = {factor: {lvl: round(usd, 6) for lvl, usd in values.items()} for factor, values in by_level.items()}
    n_repeats = repeats or (max((c.repeat_index for c, _ in plan_pairs if c.stage == "main"), default=0) + 1)
    fit = _fit(plan_pairs, cap, n_repeats, amount, run_cells=pairs) if cap is not None else EstimateFit()

    parallel = effective_parallel or (content.concurrency.study_max_parallel if content else 1)
    wall = sum(e.expected_wall_s for _, e in pairs)
    wall_p90 = sum(e.p90_wall_s for _, e in pairs)
    eval_minutes = sum(k.EVAL_MINUTES_BY_LEVEL.get(c.manifest.planned_evaluation.eval_level, 0.0) for c, _ in pairs)
    time = EstimateTime(
        expected_wall_s_low=round(wall / max(1, parallel), 1),
        expected_wall_s_high=round(wall_p90 / max(1, parallel), 1),
        effective_parallel=max(1, parallel),
        eval_machine_minutes=round(eval_minutes, 1),
    )
    bases = {e.basis.split(":", 1)[0] for _, e in pairs}
    method: Literal["priors", "heuristic", "mixed"] = (
        "mixed" if len(bases) > 1 else ("priors" if bases == {"prior"} else "heuristic")
    )
    issues = _cap_issues(basis_expected, basis_p90, cap, raw_cap, fit, study_kind, basis)
    if cap is not None and not any(i.severity == "error" for i in issues):
        issues += _block_issues(_block_amounts(pairs, amount), cap, fit, study_kind)
    return Estimate(
        expected_total=round(expected, 6),
        p90_total=round(p90, 6),
        worst_case_total=round(worst, 6),
        expected_list_total=round(list_total, 6),
        p90_list_total=round(list_p90_total, 6),
        cap_basis=basis,
        expected_tokens_total=int(tokens),
        cap_usd=cap,
        by_condition=conditions,
        by_level=levels,
        fit=fit,
        time=time,
        method=method,
        inputs={
            "bases": sorted({e.basis for _, e in pairs}),
            "history_window_days": k.PRIOR_WINDOW_DAYS,
            "tier": tier,
            "cap_rule": "auto" if raw_cap == "auto" else "fixed",
            "p90_total_is_sum_of_cell_p90": True,
            "unpriced_cells": sum(1 for _, e in pairs if e.basis.endswith(":unpriced")),
        },
        issues=issues,
    )


def _run_pairs(
    pairs: list[tuple[ResolvedCell, CellEstimate]],
    content: Any,
    study_kind: str,
) -> list[tuple[ResolvedCell, CellEstimate]]:
    """The cells that become runs (E2E-28): an exploratory study with ``pilot.reuse_as_main`` flips each
    pilot cell to main at expand, so the main cell of the same (condition, unit, repeat) is no second run
    and is counted once (``study.launch.expand_pilot``)."""
    pilot = content.pilot if content is not None else None
    if pilot is None or not (pilot.enabled and pilot.reuse_as_main) or study_kind != "exploratory":
        return pairs
    piloted = {(c.condition_index, c.unit_index, c.repeat_index) for c, _ in pairs if c.stage == "pilot"}
    return [
        (c, e)
        for c, e in pairs
        if not (c.stage == "main" and (c.condition_index, c.unit_index, c.repeat_index) in piloted)
    ]


def _block_issues(
    blocks: list[tuple[float, float]],
    cap: float,
    fit: EstimateFit,
    study_kind: str,
) -> list[Issue]:
    """``cap_admits_fewer_blocks`` (E2E-28): the scheduler reserves a whole block at its worst case before
    the block's first run starts, so a cap above the expected total can still stop the study early."""
    admitted = fit.blocks_under_cap if fit.blocks_under_cap is not None else len(blocks)
    if admitted >= len(blocks):
        return []
    committed = sum(expected for expected, _reserved in blocks[:admitted])
    reserved = blocks[admitted][1] if admitted < len(blocks) else 0.0
    needed = math.ceil(round((committed + reserved) * 100, 6)) / 100
    # The smallest cap that admits every block: max over blocks of (expected before it + its reservation).
    running, full = 0.0, 0.0
    for expected, block_reserved in blocks:
        full = max(full, running + block_reserved)
        running += expected
    full = math.ceil(round(full * 100, 6)) / 100
    return [
        make_issue(
            "cap_admits_fewer_blocks",
            "confirmatory" if study_kind == "confirmatory" else "exploratory",
            severity="warn",
            message=(
                f"the study cap ${cap:.2f} admits {admitted} of {len(blocks)} replicate blocks: block "
                f"{admitted + 1} needs ${committed:.2f} expected spend before it plus its worst-case "
                f"reservation ${reserved:.2f} (= ${needed:.2f}); the study stops there "
                f"(on_budget_exhausted)"
            ),
            path="study.max_cost_usd",
            fix=[
                IssueFix(
                    label=f"raise cap to ${full:.2f}",
                    action="set_study_cap",
                    params={"study_cap": full},
                ),
            ],
            data={"blocks": len(blocks), "admitted": admitted, "cap": cap, "cap_for_all_blocks": full},
        ),
    ]


def _cap_issues(
    expected: float,
    p90: float,
    cap: float | None,
    raw_cap: Any,
    fit: EstimateFit,
    study_kind: str,
    basis: str = "billed",
) -> list[Issue]:
    if cap is None or raw_cap == "auto":
        return []  # an auto cap is derived from p90 and always covers it
    severity = "error" if expected > cap else ("warn" if p90 > cap else None)
    if severity is None:
        return []
    raise_to = math.ceil(round(p90 * 1.1 * 100, 6)) / 100
    fixes = [IssueFix(label=f"raise cap to ${raise_to:.2f}", action="set_study_cap", params={"study_cap": raise_to})]
    if fit.max_repeats_under_cap is not None:
        fixes.append(
            IssueFix(
                label=f"reduce repeats to {fit.max_repeats_under_cap}",
                action="set_repeats",
                params={"repeats": fit.max_repeats_under_cap},
            ),
        )
    which = "expected total" if severity == "error" else "p90 total"
    if basis == "list":
        which = f"{which} at list price"
    value = expected if severity == "error" else p90
    return [
        make_issue(
            "estimate_exceeds_cap",
            "confirmatory" if study_kind == "confirmatory" else "exploratory",
            severity=severity,  # type: ignore[arg-type]
            message=f"the {which} ${value:.2f} exceeds the study cap ${cap:.2f}",
            path="study.max_cost_usd",
            data={"expected_total": expected, "p90_total": p90, "cap": cap, "cap_basis": basis},
            fix=fixes,
        ),
    ]


# =====================================================================================
# Re-estimate after the pilot (4.4 step 10)
# =====================================================================================


def reestimate_after_pilot(
    estimate: Estimate,
    observations: Mapping[int, Iterable[float]],
    *,
    prior_weight: int = k.PRIOR_MIN_N,
) -> tuple[Estimate, list[dict[str, Any]]]:
    """Update each condition's expected cost with its pilot costs (log-normal, weighted by n).

    ``observations`` is ``{condition_index: [cost of each finished pilot attempt]}``, in the estimate's
    ``cap_basis`` (list costs for a list-basis study). The prior mean on the log scale is
    ``ln(expected per cell)`` with weight ``prior_weight``; the posterior per-cell mean is the n-weighted mean
    of the two. Returns the new estimate and ``[{condition_index, before, after, n}]`` (in the cap's unit) to
    show before the expansion approval. Costs of 0 (free endpoints) keep the condition's estimate.
    """
    listed = estimate.cap_basis == "list"
    changes = []
    conditions = []
    for row in estimate.by_condition:
        costs = [c for c in observations.get(row.condition_index, []) if c > 0]
        basis_expected = row.expected_list_usd if listed else row.expected_usd
        per_cell = basis_expected / row.n_cells if row.n_cells else 0.0
        if not costs or per_cell <= 0:
            conditions.append(row)
            continue
        n = len(costs)
        mu0 = math.log(per_cell)
        mu_obs = sum(math.log(c) for c in costs) / n
        var = sum((math.log(c) - mu_obs) ** 2 for c in costs) / max(1, n - 1)
        mu = (prior_weight * mu0 + n * mu_obs) / (prior_weight + n)
        posterior = math.exp(mu + var / 2)  # log-normal mean
        ratio = posterior / per_cell
        updated = row.model_copy(
            update={
                "expected_usd": round(row.expected_usd * ratio, 6),
                "p90_usd": round(row.p90_usd * ratio, 6),
                "expected_list_usd": round(row.expected_list_usd * ratio, 6),
                "p90_list_usd": round(row.p90_list_usd * ratio, 6),
            },
        )
        conditions.append(updated)
        changes.append(
            {
                "condition_index": row.condition_index,
                "before": basis_expected,
                "after": updated.expected_list_usd if listed else updated.expected_usd,
                "n": n,
            },
        )
    expected = sum(c.expected_usd for c in conditions)
    p90 = sum(c.p90_usd for c in conditions)
    new = estimate.model_copy(
        update={
            "by_condition": conditions,
            "expected_total": round(expected, 6),
            "p90_total": round(p90, 6),
            "expected_list_total": round(sum(c.expected_list_usd for c in conditions), 6),
            "p90_list_total": round(sum(c.p90_list_usd for c in conditions), 6),
            "inputs": {**estimate.inputs, "reestimated_after_pilot": True},
        },
    )
    return new, changes
