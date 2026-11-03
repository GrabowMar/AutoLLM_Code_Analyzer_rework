"""Parents drawn from ``Design.parent_selection`` at plan time (spec section 7.2; WP-12 ask, wave-3 seam).

A slot design names the part to regenerate through **template targets** (``units.slot_targets``, or every
target of ``units.slot_sets``). With ``parent_selection`` (rule ``random`` or ``all``) the units are the same
part (same ``match_key``) in each parent drawn from the pool, not the templates themselves:

* the draw is ``slot.parents.draw(selection)`` with ``selection.seed``, or, when omitted, a seed derived from
  ``base_seed`` (:func:`selection_seed`), within the runs the study owner may use; it is reproducible, so an
  ephemeral resolve and the freeze draw the same parents;
* each drawn parent gets a ``SlotSet`` whose recorded selection is this draw (same rule and seed), reused
  when it exists, else frozen with ``slot.freeze.freeze(..., selection=, pool_size=, seed=)``;
* each template target is matched into that set (``slot.selection.match``) unless a target with the same
  ``match_key`` already exists there.

Ephemeral resolves write nothing (:func:`drawn_units` with ``create=False`` only reads the rows that already
exist and reports what is still missing); ``plan.resolve_bundle(persist=True)`` calls :func:`materialize`
first, so the frozen plan has every (parent, template) unit and a later ephemeral resolve equals it.
``rule="manual"`` keeps the listed slot sets / targets as units (the researcher's picks).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from backend.generation import identity

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from backend.generation.contracts.plan import Design
    from backend.generation.contracts.slot import ParentSelection
    from backend.generation.models import GenerationRun
    from backend.generation.models import SlotSet
    from backend.generation.models import SlotTarget
    from backend.users.models import User

__all__ = ["DrawnUnits", "drawn_units", "materialize", "selection_seed", "uses_draw"]

_SEED_MODULUS: Final = 2**31
_SEED_HEX_DIGITS: Final = 12


def uses_draw(design: Design) -> bool:
    """The slot design draws its parents (``parent_selection`` with rule ``random`` or ``all``)."""
    selection = design.parent_selection
    return design.mode == "slot" and selection is not None and selection.rule in ("random", "all")


def selection_seed(design: Design) -> int:
    """``parent_selection.seed``, else ``int(H({"base_seed", "purpose": "parent_selection"})[:12], 16) % 2**31``."""
    assert design.parent_selection is not None
    if design.parent_selection.seed is not None:
        return int(design.parent_selection.seed)
    digest = identity.H({"base_seed": design.base_seed, "purpose": "parent_selection"})
    return int(digest[:_SEED_HEX_DIGITS], 16) % _SEED_MODULUS


def _seeded(design: Design) -> ParentSelection:
    assert design.parent_selection is not None
    return design.parent_selection.model_copy(update={"seed": selection_seed(design)})


def _runs_for(user: User | None) -> QuerySet[GenerationRun] | None:
    """The runs the study owner may draw from (staff: all; same rule as the slot API's parent pool)."""
    from backend.generation.models import GenerationRun

    if user is None or getattr(user, "is_staff", False):
        return None
    return GenerationRun.objects.filter(created_by=user)


def templates(design: Design) -> list[SlotTarget]:
    """The template targets in design order (``units.slot_targets``, else every target of the slot sets)."""
    from backend.generation.models import SlotTarget

    qs = SlotTarget.objects.select_related("slot_set__task_version")
    if design.units.slot_targets:
        by_id = {str(t.pk): t for t in qs.filter(pk__in=list(design.units.slot_targets))}
        return [by_id[str(i)] for i in design.units.slot_targets if str(i) in by_id]
    sets = [str(sid) for sid in design.units.slot_sets]
    return list(qs.filter(slot_set_id__in=sets).order_by("slot_set__created_at", "created_at", "pk"))


@dataclass
class DrawnUnits:
    """The units of a drawn design: targets in (parent draw order, template order), plus what is missing."""

    seed: int
    pool_size: int
    parents: list[GenerationRun] = field(default_factory=list)
    targets: list[SlotTarget] = field(default_factory=list)
    missing_sets: list[str] = field(default_factory=list)  # parent run ids without a slot set of this draw
    missing_targets: list[dict[str, str]] = field(default_factory=list)  # {slot_set, template, reason}
    created: int = 0  # rows created by this call (slot sets + targets)

    @property
    def complete(self) -> bool:
        return not self.missing_sets and not self.missing_targets


def _draw_set(parent: GenerationRun, selection: ParentSelection, seed: int) -> SlotSet | None:
    """The parent's slot set recorded with this draw (same rule and seed, same frozen tree)."""
    from backend.generation.models import SlotSet

    for slot_set in SlotSet.objects.filter(parent_run=parent, frozen_tree_hash=parent.tree_hash).order_by(
        "created_at",
        "pk",
    ):
        record = slot_set.selection or {}
        if record.get("rule") == selection.rule and record.get("seed") == seed:
            return slot_set
    return None


def _target_in(slot_set: SlotSet, template: SlotTarget) -> SlotTarget | None:
    if template.slot_set_id == slot_set.pk:
        return template
    if not template.match_key:
        return None
    return slot_set.targets.filter(match_key=template.match_key).order_by("created_at", "pk").first()


def drawn_units(design: Design, *, user: User | None, create: bool = False) -> DrawnUnits:
    """Draw the parents and collect the matched targets (``create`` freezes sets and matches templates)."""
    from backend.generation.slot import freeze as slot_freeze
    from backend.generation.slot import parents as slot_parents
    from backend.generation.slot import selection as slot_selection

    selection = _seeded(design)
    seed = int(selection.seed or 0)
    result = slot_parents.draw_with_record(selection, base=_runs_for(user))
    out = DrawnUnits(seed=seed, pool_size=result.pool_size, parents=list(result.parents))
    template_rows = templates(design)
    for parent in out.parents:
        slot_set = _draw_set(parent, selection, seed)
        if slot_set is None and create:
            slot_set = slot_freeze.freeze(
                parent,
                name=f"{parent.task_version} parent {str(parent.pk)[:8]} (draw {seed})",
                user=user,
                selection=selection,
                pool_size=result.pool_size,
                seed=seed,
            )
            out.created += 1
        if slot_set is None:
            out.missing_sets.append(str(parent.pk))
            continue
        for template in template_rows:
            target = _target_in(slot_set, template)
            if target is None and create:
                matched: dict[str, Any] = slot_selection.match(template, [slot_set], user=user)
                created = matched.get("created") or []
                if created:
                    target = created[0]
                    out.created += 1
                else:
                    reason = next((u.get("reason", "") for u in matched.get("unmatched") or []), "not_found")
                    out.missing_targets.append(
                        {"slot_set": str(slot_set.pk), "template": str(template.pk), "reason": reason},
                    )
                    continue
            if target is None:
                out.missing_targets.append(
                    {"slot_set": str(slot_set.pk), "template": str(template.pk), "reason": "not_matched_yet"},
                )
                continue
            out.targets.append(target)
    return out


def materialize(design: Design, *, user: User | None) -> DrawnUnits:
    """Freeze the drawn parents and match the templates into them (freeze / launch; idempotent)."""
    return drawn_units(design, user=user, create=True)
