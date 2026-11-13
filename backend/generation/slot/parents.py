"""Parent pool queries and ``parent_selection`` (random, all or manual; spec section 7.2).

The pool of a task is every usable ``app_agentic`` run with outcome ``tested`` (``beyond_plan`` runs are
left out: they are excluded from results). ``draw`` is reproducible forever, not just within one Python
version: candidates are ranked by ``sha256(seed, run id)`` (never ``random.shuffle``), and with
``stratify_by="parent_model"`` the picks rotate over the parent models (in seeded order) so k parents come
from as many models as possible.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import TYPE_CHECKING
from typing import Any

from django.utils import timezone

from backend.generation.contracts import Ref
from backend.generation.contracts.slot import ParentSelection
from backend.generation.contracts.slot import PoolQuery
from backend.generation.contracts.slot import SlotSetSelection
from backend.generation.models import GenerationRun
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunKind
from backend.generation.slot.errors import SlotInvalidError

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from backend.users.models import User

__all__ = ["DrawResult", "draw", "draw_with_record", "new_seed", "pool", "pool_for", "selection_record"]

_SEED_BITS = 31


def _ref_filter(prefix: str, ref: str) -> dict[str, Any]:
    try:
        parsed = Ref.parse(ref)
    except ValueError as exc:
        raise SlotInvalidError(str(exc)) from exc
    lookup: dict[str, Any] = {f"{prefix}__version": parsed.version}
    lookup[f"{prefix}__task__slug" if prefix == "task_version" else f"{prefix}__slug"] = parsed.slug
    if parsed.hash12:
        lookup[f"{prefix}__content_hash__startswith"] = parsed.hash12
    return lookup


def pool_for(
    *,
    task: str | None = None,
    stack: str | None = None,
    models: list[str] | None = None,
    harnesses: list[str] | None = None,
    studies: list[str] | None = None,
    min_pass_rate: float | None = None,
    outcome: str = Outcome.TESTED,
    base: QuerySet[GenerationRun] | None = None,
) -> QuerySet[GenerationRun]:
    """The parent pool with optional filters (``task`` may be omitted for the picker's unfiltered list).

    ``base`` restricts the pool before anything else (the API passes the runs the user may see, so a draw
    ranks, counts and picks only those).
    """
    source = base if base is not None else GenerationRun.objects.all()
    qs = source.filter(kind=RunKind.APP_AGENTIC, usable=True, outcome=outcome, beyond_plan=False)
    if task:
        qs = qs.filter(**_ref_filter("task_version", task))
    if stack:
        qs = qs.filter(**_ref_filter("stack_profile", stack))
    if models:
        qs = qs.filter(model__model_id__in=models)
    if harnesses:
        qs = qs.filter(harness_preset__harness__in=harnesses)
    if studies:
        qs = qs.filter(study_id__in=studies)
    if min_pass_rate is not None:
        qs = qs.filter(metrics__eval__agent_pass_rate__gte=min_pass_rate)
    return qs.select_related("model", "task_version__task", "stack_profile", "harness_preset").order_by(
        "created_at",
        "id",
    )


def pool(query: PoolQuery, *, base: QuerySet[GenerationRun] | None = None) -> QuerySet[GenerationRun]:
    """Eligible parents: usable ``app_agentic`` runs with outcome ``tested`` (within ``base`` when given)."""
    return pool_for(
        base=base,
        task=query.task,
        stack=query.stack,
        models=query.models,
        harnesses=query.harnesses,
        studies=query.studies,
        min_pass_rate=query.min_pass_rate,
    )


def new_seed() -> int:
    """A fresh draw seed (recorded with the draw; a study derives it from ``base_seed`` instead)."""
    return secrets.randbelow(2**_SEED_BITS)


def _rank(seed: int, key: str) -> str:
    return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()


def _model_of(run: GenerationRun) -> str:
    return run.model.model_id if run.model is not None else ""


class DrawResult:
    """The parents of a draw, in draw order, with the seed and the pool size it was drawn from."""

    def __init__(self, parents: list[GenerationRun], seed: int | None, pool_size: int) -> None:
        self.parents = parents
        self.seed = seed
        self.pool_size = pool_size


def _random_draw(candidates: list[GenerationRun], selection: ParentSelection, seed: int) -> list[GenerationRun]:
    ranked = sorted(candidates, key=lambda run: _rank(seed, str(run.pk)))
    if selection.stratify_by == "none":
        return ranked[: selection.k]
    groups: dict[str, list[GenerationRun]] = {}
    for run in ranked:
        groups.setdefault(_model_of(run), []).append(run)
    order = sorted(groups, key=lambda model: _rank(seed, f"model:{model}"))
    picked: list[GenerationRun] = []
    while len(picked) < selection.k and any(groups.values()):
        for model in order:
            if groups[model] and len(picked) < selection.k:
                picked.append(groups[model].pop(0))
    return picked


def draw_with_record(selection: ParentSelection, *, base: QuerySet[GenerationRun] | None = None) -> DrawResult:
    """Draw parents from the pool (nothing is frozen). ``random`` needs a seed (``new_seed()`` if omitted).

    ``base`` restricts the pool before ranking (a user's visible runs), so ``k`` and ``pool_size`` hold for it.
    """
    candidates = list(pool(selection.pool_query, base=base))
    if selection.rule == "manual":
        msg = "rule 'manual' records the researcher's picks; there is nothing to draw"
        raise SlotInvalidError(msg)
    if selection.rule == "all":
        return DrawResult(candidates, selection.seed, len(candidates))
    seed = selection.seed if selection.seed is not None else new_seed()
    return DrawResult(_random_draw(candidates, selection, seed), seed, len(candidates))


def draw(selection: ParentSelection, *, base: QuerySet[GenerationRun] | None = None) -> list[GenerationRun]:
    """Reproducible (seeded, stratified) draw from the pool (within ``base`` when given)."""
    if selection.rule == "random" and selection.seed is None:
        msg = "a random draw needs a seed (derived from the study's base_seed, or returned by the dry draw)"
        raise SlotInvalidError(msg)
    return draw_with_record(selection, base=base).parents


def selection_record(
    selection: ParentSelection | None,
    *,
    user: User | None,
    pool_size: int | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """The ``SlotSet.selection`` JSON: the rule, its pool query and seed, or who picked the parent."""
    at = timezone.now().isoformat()
    picked_by = getattr(user, "username", None) if user is not None else None
    if selection is None:
        record = SlotSetSelection(rule="manual", picked_by=picked_by, at=at)
    else:
        record = SlotSetSelection(
            rule=selection.rule,
            pool_query=selection.pool_query,
            pool_size=pool_size,
            seed=seed if seed is not None else selection.seed,
            picked_by=picked_by if selection.rule == "manual" else None,
            at=at,
        )
    return record.model_dump(mode="json")
