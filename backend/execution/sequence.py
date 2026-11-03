"""Run order of a study's cells (spec §C.5; v3 ``Study.scheduling``).

Policies (``Study.scheduling``):

- ``interleaved``: within each repeat block, every cell shuffled (randomised
  complete blocks). Residency K is still enforced at admission, so on an
  endpoint with finite K this thrashes model loads.
- ``blocked``: within each repeat block, the model order is shuffled, then
  each model's conditions, then each condition's units. A model's cells are
  contiguous within a block; the model order is re-drawn per block.
- ``auto`` (default): resolved per endpoint group: ``blocked`` for conditions on
  endpoints with a finite K (residency-aware blocking for local endpoints),
  ``interleaved`` otherwise (OpenRouter).

Randomness is drawn **per repeat block** (``random.Random(hash((order_seed,
r)))``) over **all** cells, not only the pending ones, and positions are laid
out with fixed strides, so a top-up or retry keeps each cell's original
position and adding replicates never moves an existing cell (new repeat blocks
come after the current maximum). Seeds and common random numbers do not depend
on the order: they are per cell (``StudyCell.seed``).
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

POLICIES = ("auto", "interleaved", "blocked")
EFFECTIVE_POLICIES = ("interleaved", "blocked")

# A cell is the triple condition id, unit id, repeat index.
Cell = tuple[int, int, int]


@dataclass(frozen=True)
class ConditionInfo:
    """What ordering needs to know about one condition."""

    model_key: Any  # cells of one model are kept together under blocked
    endpoint_key: str  # endpoint slug ("openrouter" for the catalog)
    finite_k: bool  # the endpoint limits resident models (auto -> blocked)


def order_seed(study: Any) -> int:
    """The study's run-order seed (``Study.order_seed``; the id when unset)."""
    if getattr(study, "order_seed", None) is not None:
        return int(study.order_seed)
    return int(study.id.int % 2_147_483_647)


def resident_limit(endpoint: Any, study: Any = None) -> int | None:
    """K for ``endpoint`` under ``study``: the tighter of the endpoint's and ``execution_options.max_resident_models``.

    The system (OpenRouter) endpoint serves everything and has no residency.
    """
    if endpoint is None or getattr(endpoint, "is_system", False):
        return None
    limits = [getattr(endpoint, "max_resident_models", None)]
    options = getattr(study, "execution_options", None) or {}
    if isinstance(options, dict):
        limits.append(options.get("max_resident_models"))
    finite = [int(v) for v in limits if isinstance(v, int) and not isinstance(v, bool) and v > 0]
    return min(finite) if finite else None


def condition_info(study: Any, condition_ids: Iterable[int]) -> dict[int, ConditionInfo]:
    """One query: model, endpoint and residency of each ``StudyCondition``."""
    from backend.generation.models import StudyCondition

    info: dict[int, ConditionInfo] = {}
    rows = StudyCondition.objects.filter(id__in=set(condition_ids)).select_related("model__endpoint")
    for condition in rows:
        model = condition.model
        endpoint = getattr(model, "endpoint", None) if model is not None else None
        info[condition.id] = ConditionInfo(
            model_key=model.pk if model is not None else f"condition:{condition.id}",
            endpoint_key=endpoint.slug if endpoint is not None else "openrouter",
            finite_k=resident_limit(endpoint, study) is not None,
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
    """``{endpoint_slug: effective policy}`` (what ``auto`` resolved to, per endpoint group)."""
    return {info.endpoint_key: effective_policy(policy, info) for info in conditions.values()}


def _block_rng(seed: int, *parts: Any) -> random.Random:
    return random.Random(hash((seed, *parts)))  # noqa: S311 - run-order randomisation, not crypto


def _sort_key(value: Any) -> tuple[str, str]:
    return (type(value).__name__, str(value))


def cell_sequence(
    study: Any,
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
    cells = sorted({(int(c), int(u), int(r)) for c, u, r in cells})
    if not cells:
        return {}
    if conditions is None:
        conditions = condition_info(study, {c for c, _u, _r in cells})
    seed = order_seed(study)

    def model_of(condition_id: int) -> Any:
        info = conditions.get(condition_id)
        return info.model_key if info is not None else f"condition:{condition_id}"

    result: dict[Cell, int] = {}
    # interleaved and blocked share the per-repeat-block layout and RNG.
    block_size = len({(c, u) for c, u, _r in cells})
    blocks: dict[int, dict[str, list[Cell]]] = {}
    for cell in cells:
        name = effective_policy(policy, conditions.get(cell[0]))
        blocks.setdefault(cell[2], {}).setdefault(name, []).append(cell)
    for repeat in sorted(blocks):
        # Each policy group draws from its own copy of the block's RNG, so an endpoint group's order
        # does not depend on which other groups share the study.
        ordered: list[Cell] = []
        interleaved = sorted(blocks[repeat].get("interleaved", []))
        _block_rng(seed, repeat).shuffle(interleaved)
        ordered.extend(interleaved)
        ordered.extend(_blocked_order(blocks[repeat].get("blocked", []), _block_rng(seed, repeat), model_of))
        for position, cell in enumerate(ordered):
            result[cell] = repeat * block_size + position
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
            units = list(by_model[model][condition_id])
            rng.shuffle(units)
            ordered.extend(units)
    return ordered


def estimate_swaps(models_in_order: Iterable[Any], resident_limit: int | None) -> int:
    """Model loads on one endpoint when its runs run in this order with at most ``resident_limit`` models resident.

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
