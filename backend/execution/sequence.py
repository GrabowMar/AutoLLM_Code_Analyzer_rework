"""Run order of an experiment's cells (spec §C.5).

Policies:

- ``interleaved``: within each repeat block, every cell shuffled (randomised
  complete blocks). Residency K is still enforced at admission, so on an
  endpoint with finite K this thrashes model loads.
- ``blocked``: within each repeat block, the model order is shuffled, then
  each model's conditions, then each condition's apps. A model's cells are
  contiguous within a block; the model order is re-drawn per block.
- ``grouped``: model-major. One random condition order (models contiguous);
  each condition runs all of its cells, repeat-major, apps shuffled per block.
  Cheapest in swaps, confounded with time.
- ``auto``: resolved per endpoint group: ``blocked`` for conditions on
  endpoints with a finite K, ``interleaved`` otherwise (OpenRouter).

Randomness is drawn **per repeat block** (``random.Random(hash((order_seed,
r)))``) over **all** cells, not only the pending ones, and positions are laid
out with fixed strides, so a top-up or retry keeps each cell's original
position and adding replicates never moves an existing cell (new repeat blocks
come after the current maximum). Seeds and common random numbers do not depend
on the order: they are per cell (``experiment_cell_seed``).
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

POLICIES = ("auto", "interleaved", "blocked", "grouped")
EFFECTIVE_POLICIES = ("interleaved", "blocked", "grouped")

# grouped: sequence = condition_rank * GROUP_STRIDE + repeat * n_apps + app position.
GROUP_STRIDE = 100_000

# A cell is the triple condition id, app requirement id, repeat index.
Cell = tuple[int, int, int]


@dataclass(frozen=True)
class ConditionInfo:
    """What ordering needs to know about one condition."""

    model_key: Any  # cells of one model are kept together under blocked / grouped
    endpoint_key: str  # endpoint slug ("openrouter" for the catalog)
    finite_k: bool  # the endpoint limits resident models (auto -> blocked)


def order_seed(experiment: Any) -> int:
    """The experiment's run-order seed (same formula as ``services.experiments._order_seed``)."""
    if getattr(experiment, "base_seed", None) is not None:
        return int(experiment.base_seed)
    return int(experiment.id.int % 2_147_483_647)


def resident_limit(endpoint: Any, experiment: Any = None) -> int | None:
    """K for ``endpoint`` under ``experiment``: the tighter of the endpoint's and the experiment's limit.

    The system (OpenRouter) endpoint serves everything and has no residency.
    """
    if endpoint is None or getattr(endpoint, "is_system", False):
        return None
    limits = [getattr(endpoint, "max_resident_models", None)]
    options = getattr(experiment, "execution_options", None) or {}
    if isinstance(options, dict):
        limits.append(options.get("max_resident_models"))
    finite = [int(v) for v in limits if isinstance(v, int) and not isinstance(v, bool) and v > 0]
    return min(finite) if finite else None


def condition_info(experiment: Any, condition_ids: Iterable[int]) -> dict[int, ConditionInfo]:
    """One query: model, endpoint and residency of each condition."""
    from backend.generation.models import ExperimentCondition

    info: dict[int, ConditionInfo] = {}
    rows = ExperimentCondition.objects.filter(id__in=set(condition_ids)).select_related("model__endpoint")
    for condition in rows:
        model = condition.model
        endpoint = getattr(model, "endpoint", None) if model is not None else None
        info[condition.id] = ConditionInfo(
            model_key=model.pk if model is not None else f"condition:{condition.id}",
            endpoint_key=endpoint.slug if endpoint is not None else "openrouter",
            finite_k=resident_limit(endpoint, experiment) is not None,
        )
    return info


def effective_policy(policy: str, info: ConditionInfo | None) -> str:
    if policy not in POLICIES:
        msg = f"Unknown scheduling policy {policy!r}"
        raise ValueError(msg)
    if policy != "auto":
        return policy
    return "blocked" if info is not None and info.finite_k else "interleaved"


def policies_by_endpoint(policy: str, conditions: Mapping[int, ConditionInfo]) -> dict[str, str]:
    """``{endpoint_slug: effective policy}`` (``GenerationBatch.request.schedule.policy_effective_by_endpoint``)."""
    return {info.endpoint_key: effective_policy(policy, info) for info in conditions.values()}


def _block_rng(seed: int, *parts: Any) -> random.Random:
    return random.Random(hash((seed, *parts)))  # noqa: S311 - run-order randomisation, not crypto


def _sort_key(value: Any) -> tuple[str, str]:
    return (type(value).__name__, str(value))


def cell_sequence(
    experiment: Any,
    cells: Iterable[Cell],
    policy: str,
    *,
    conditions: Mapping[int, ConditionInfo] | None = None,
) -> dict[Cell, int]:
    """Map every cell to its position (ascending = earlier) in the launch order under ``policy``.

    Pass **all** cells of the design (not only the pending ones). ``policy="auto"``
    resolves per endpoint group (conditions on finite-K endpoints ``blocked``,
    others ``interleaved``). ``conditions`` overrides the per-condition lookup
    (one query otherwise).
    """
    cells = sorted({(int(c), int(a), int(r)) for c, a, r in cells})
    if not cells:
        return {}
    if conditions is None:
        conditions = condition_info(experiment, {c for c, _a, _r in cells})
    seed = order_seed(experiment)

    by_policy: dict[str, list[Cell]] = {name: [] for name in EFFECTIVE_POLICIES}
    for cell in cells:
        by_policy[effective_policy(policy, conditions.get(cell[0]))].append(cell)

    def model_of(condition_id: int) -> Any:
        info = conditions.get(condition_id)
        return info.model_key if info is not None else f"condition:{condition_id}"

    result: dict[Cell, int] = {}
    # interleaved and blocked share the per-repeat-block layout and RNG.
    block_size = len({(c, a) for c, a, _r in cells})
    blocks: dict[int, dict[str, list[Cell]]] = {}
    for name in ("interleaved", "blocked"):
        for cell in by_policy[name]:
            blocks.setdefault(cell[2], {}).setdefault(name, []).append(cell)
    for repeat in sorted(blocks):
        # Each policy group draws from its own copy of the block's RNG, so an endpoint group's order
        # does not depend on which other groups share the experiment.
        ordered: list[Cell] = []
        interleaved = sorted(blocks[repeat].get("interleaved", []))
        _block_rng(seed, repeat).shuffle(interleaved)
        ordered.extend(interleaved)
        ordered.extend(_blocked_order(blocks[repeat].get("blocked", []), _block_rng(seed, repeat), model_of))
        for position, cell in enumerate(ordered):
            result[cell] = repeat * block_size + position

    if by_policy["grouped"]:
        result.update(_grouped_sequence(by_policy["grouped"], seed, model_of))
    return result


def _blocked_order(cells: Sequence[Cell], rng: random.Random, model_of) -> list[Cell]:
    by_model: dict[Any, dict[int, list[Cell]]] = {}
    for cell in sorted(cells):
        by_model.setdefault(model_of(cell[0]), {}).setdefault(cell[0], []).append(cell)
    models = sorted(by_model, key=_sort_key)
    rng.shuffle(models)
    ordered: list[Cell] = []
    for model in models:
        condition_ids = sorted(by_model[model])
        rng.shuffle(condition_ids)
        for condition_id in condition_ids:
            apps = list(by_model[model][condition_id])
            rng.shuffle(apps)
            ordered.extend(apps)
    return ordered


def _grouped_sequence(cells: Sequence[Cell], seed: int, model_of) -> dict[Cell, int]:
    rng0 = _block_rng(seed, 0)
    by_model: dict[Any, set[int]] = {}
    for condition_id, _app, _repeat in cells:
        by_model.setdefault(model_of(condition_id), set()).add(condition_id)
    models = sorted(by_model, key=_sort_key)
    rng0.shuffle(models)
    condition_order: list[int] = []
    for model in models:
        condition_ids = sorted(by_model[model])
        rng0.shuffle(condition_ids)
        condition_order.extend(condition_ids)
    rank = {condition_id: index for index, condition_id in enumerate(condition_order)}
    n_apps = len({a for _c, a, _r in cells})

    result: dict[Cell, int] = {}
    per_block: dict[tuple[int, int], list[Cell]] = {}
    for cell in cells:
        per_block.setdefault((cell[0], cell[2]), []).append(cell)
    for (condition_id, repeat), block in per_block.items():
        apps = sorted(block)
        _block_rng(seed, repeat, condition_id).shuffle(apps)
        for position, cell in enumerate(apps):
            offset = repeat * n_apps + position
            if offset >= GROUP_STRIDE:
                msg = "grouped policy supports at most 100000 cells per condition"
                raise ValueError(msg)
            result[cell] = rank[condition_id] * GROUP_STRIDE + offset
    return result


def estimate_swaps(models_in_order: Iterable[Any], resident_limit: int | None) -> int:
    """Model loads on one endpoint when its jobs run in this order with at most ``resident_limit`` models resident.

    Least-recently-used eviction; the first load of each model counts. With no
    limit every distinct model loads once.
    """
    if resident_limit is None:
        return len(set(models_in_order))
    resident: list[Any] = []
    loads = 0
    for model in models_in_order:
        if model in resident:
            resident.remove(model)
        else:
            loads += 1
            if len(resident) >= resident_limit:
                resident.pop(0)
        resident.append(model)
    return loads
