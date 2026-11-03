"""Cost and time estimation, fit-to-budget and the ``auto`` cap (spec section 4.4).

``estimate_cell`` gives one cell's ``CellEstimate``; ``estimate_plan`` the study ``Estimate`` (totals,
``by_condition``, ``by_level``, ``fit``, time, the resolved ``auto`` cap and the ``estimate_exceeds_cap``
issue). The arithmetic is in ``estimate_inputs(CellInputs)``, a pure function, so the resolver, the slot
picker's cost hint and the tests share it.

Conventions:

* Amounts are USD. ``expected_usd`` is what the endpoint bills (0 for subscription and local endpoints);
  ``expected_list_usd`` is always priced from the manifest's frozen catalog prices.
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
    "est_tokens",
    "estimate_cell",
    "estimate_inputs",
    "estimate_plan",
    "inputs_from_cell",
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
        list_ratio = (prior.list_cost_p50 / prior.cost_p50) if prior.list_cost_p50 and prior.cost_p50 else 1.0
        tokens = (prior.prompt_tokens_p50 or 0) + (prior.completion_tokens_p50 or 0)
        basis = f"prior:{found[0] if found else ''}({prior.n})"
    else:
        terms = _slot_single_shot(inputs) if inputs.kind == "slot_single_shot" else _agentic(inputs)
        sort = policy.routing.provider_sort
        calib = prior_store.calibration(model.slug, sort, priors=source) if source != {} else 1.0
        expected, p10, p90 = terms["expected"] * calib, terms["p10"] * calib, terms["p90"] * calib
        list_ratio = 1.0 / calib if calib else 1.0
        tokens = terms["tokens"]
        basis = "heuristic"
    # 6. failure overhead: x (1 + failed-call share) + infra_retry_rate x mean partial-run cost.
    over = prior_store.overhead(inputs.kind, inputs.harness, priors=source) if source != {} else None
    if over is not None:
        extra = over["infra_retry_rate"] * over["mean_partial_cost"]
        factor = 1 + over["failed_call_cost_share"]
        expected, p10, p90 = expected * factor + extra, p10 * factor + extra, p90 * factor + extra
    if not model.priced:
        expected, p10, p90 = cap, 0.0, cap
        basis = f"{basis}:unpriced"
    expected, p10, p90 = min(expected, cap), min(p10, cap), min(p90, cap)
    list_expected = expected * list_ratio
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
        cap_usd=round(cap, 8),
        expected_tokens=int(min(tokens, policy.run.max_total_tokens)),
        expected_wall_s=round(wall, 2),
        p90_wall_s=round(wall_p90, 2),
        basis=basis,
    )


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


def _fit(cells: list[tuple[ResolvedCell, CellEstimate]], cap: float, repeats: int) -> EstimateFit:
    main = [(c, e) for c, e in cells if c.stage == "main"] or cells
    blocks: dict[int, float] = defaultdict(float)
    by_unit_repeat: dict[tuple[int, int], float] = defaultdict(float)
    for cell, est in main:
        blocks[cell.block_index] += est.expected_usd
        by_unit_repeat[(cell.unit_index, cell.repeat_index)] += est.expected_usd
    running, blocks_fit = 0.0, 0
    for index in sorted(blocks):
        running += blocks[index]
        if running > cap + 1e-12:
            break
        blocks_fit += 1
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
    expected = sum(e.expected_usd for _, e in pairs)
    p90 = sum(e.p90_usd for _, e in pairs)
    list_total = sum(e.expected_list_usd for _, e in pairs)
    tokens = sum(e.expected_tokens for _, e in pairs)
    infra_retries = content.retries.infra_retries if content else 0
    cell_cap = content.cell.max_cost_usd if content else math.inf
    worst = sum(min(cell_cap, e.cap_usd * (1 + infra_retries)) for _, e in pairs)
    cap: float | None = None
    raw_cap = cap_override if cap_override is not None else (content.study.max_cost_usd if content else None)
    cap_basis_total = (
        p90
        if (content is None or content.study.cap_basis != "list")
        else p90 * (list_total / expected if expected else 1.0)
    )
    if raw_cap == "auto":
        cap = auto_cap(cap_basis_total, tier)
    elif raw_cap is not None:
        cap = float(raw_cap)
    if cap is not None:
        worst = min(worst, cap)

    by_condition: dict[int, list[float]] = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    by_level: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for cell, est in pairs:
        row = by_condition[cell.condition_index]
        row[0] += 1
        row[1] += est.expected_usd
        row[2] += est.p90_usd
        row[3] += est.expected_list_usd
        for factor, level in cell.levels.items():
            by_level[factor][level] += est.expected_usd
    conditions = [
        ConditionEstimate(
            condition_index=index,
            n_cells=int(values[0]),
            expected_usd=round(values[1], 6),
            p90_usd=round(values[2], 6),
            expected_list_usd=round(values[3], 6),
        )
        for index, values in sorted(by_condition.items())
    ]
    levels = {factor: {lvl: round(usd, 6) for lvl, usd in values.items()} for factor, values in by_level.items()}
    n_repeats = repeats or (max((c.repeat_index for c, _ in pairs if c.stage == "main"), default=0) + 1)
    fit = _fit(pairs, cap, n_repeats) if cap is not None else EstimateFit()

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
    issues = _cap_issues(expected, p90, cap, raw_cap, fit, study_kind)
    return Estimate(
        expected_total=round(expected, 6),
        p90_total=round(p90, 6),
        worst_case_total=round(worst, 6),
        expected_list_total=round(list_total, 6),
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


def _cap_issues(
    expected: float,
    p90: float,
    cap: float | None,
    raw_cap: Any,
    fit: EstimateFit,
    study_kind: str,
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
    value = expected if severity == "error" else p90
    return [
        make_issue(
            "estimate_exceeds_cap",
            "confirmatory" if study_kind == "confirmatory" else "exploratory",
            severity=severity,  # type: ignore[arg-type]
            message=f"the {which} ${value:.2f} exceeds the study cap ${cap:.2f}",
            path="study.max_cost_usd",
            data={"expected_total": expected, "p90_total": p90, "cap": cap},
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

    ``observations`` is ``{condition_index: [cost of each finished pilot attempt]}``. The prior mean on the
    log scale is ``ln(expected per cell)`` with weight ``prior_weight``; the posterior per-cell mean is the
    n-weighted mean of the two. Returns the new estimate and ``[{condition_index, before, after, n}]`` to
    show before the expansion approval. Costs of 0 (free endpoints) keep the condition's estimate.
    """
    changes = []
    conditions = []
    for row in estimate.by_condition:
        costs = [c for c in observations.get(row.condition_index, []) if c > 0]
        per_cell = row.expected_usd / row.n_cells if row.n_cells else 0.0
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
            },
        )
        conditions.append(updated)
        changes.append(
            {
                "condition_index": row.condition_index,
                "before": row.expected_usd,
                "after": updated.expected_usd,
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
            "inputs": {**estimate.inputs, "reestimated_after_pilot": True},
        },
    )
    return new, changes
