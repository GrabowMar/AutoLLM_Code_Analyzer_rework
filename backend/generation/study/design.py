"""Design validation: factor vocabulary per mode, units, slot sets of one task, primary features (spec 9.4, 7.10).

``contracts.plan.Design`` checks structure; this module checks meaning. Two kinds of findings:

* :class:`DesignError` for a design that cannot be resolved at all (a factor not allowed in its mode, an enum
  level outside its vocabulary, no units, a ``fixed`` factor with several levels, a confirmatory study without
  an analysis plan). The API answers 400 with the message; nothing is planned.
* registered issues (:func:`validate_design`) for designs that resolve but break a study rule:
  ``prose_variant_in_unit`` (legacy per-task variants), ``mixed_parents`` (slot sets of different tasks),
  ``primary_feature_missing`` (``ci_width_reached`` or a confirmatory slot study without primary features),
  ``adaptive_stop_in_confirmatory``.

Enum factor vocabularies live here (:data:`ENUM_LEVELS`); library factors name a library kind
(:data:`LIBRARY_FACTORS`) and accept ``slug``, ``slug@v`` or ``slug@v#hash12`` (pinned at plan time by
``study/plan.py``; a bare slug takes the latest non-archived version *at plan time*, never at run time).
The ``model`` factor names ``LLMModel.model_id``.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from pydantic import ValidationError

from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import IssueFix
from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.library import AUTH_MODES
from backend.generation.contracts.library import NETWORK_POLICIES
from backend.generation.contracts.plan import APP_FACTORS
from backend.generation.contracts.plan import SLOT_FACTORS
from backend.generation.contracts.plan import Design
from backend.generation.contracts.slot import ContextPolicy

if TYPE_CHECKING:
    from backend.generation.contracts.resources import ResourcePolicyContent
    from backend.users.models import User

__all__ = [
    "CONTEXT_MODES",
    "ENUM_LEVELS",
    "LIBRARY_FACTORS",
    "MAX_CELLS",
    "PROSE_VARIANTS",
    "SLOT_EXECUTORS",
    "DesignError",
    "check_structure",
    "context_policy_level",
    "parse_context_policy",
    "parse_design",
    "validate_design",
]

PROSE_VARIANTS: Final = ("minimal", "standard", "exhaustive")
# slot_executor level -> run kind
SLOT_EXECUTORS: Final = {"single_shot": "slot_single_shot", "restricted_agent": "slot_agent"}
CONTEXT_MODES: Final = ("slot_file_only", "related_files", "full_app", "interface_only")
ENUM_LEVELS: Final[dict[str, tuple[str, ...]]] = {
    "auth_mode": AUTH_MODES,
    "prose_variant": PROSE_VARIANTS,
    "network_policy": NETWORK_POLICIES,
    "starter_level": ("bare", "minimal", "full"),
    "slot_executor": tuple(SLOT_EXECUTORS),
    "hole_mode": ("stub", "remove", "rewrite"),
    "keep_docstring": ("true", "false"),
}
# factor key -> library kind (``generation.models.LIBRARY_MODELS`` key)
LIBRARY_FACTORS: Final[dict[str, str]] = {
    "harness_preset": "harness_preset",
    "stack_profile": "stack",
    "style": "style",
    "guidance": "guidance",
    "prompt_set": "prompt_set",
    "sampling_preset": "sampling",
    "resource_policy": "resource_policy",
}
# GET /config/ limits.max_cells (section 9.3).
MAX_CELLS: Final = 20_000
# A legacy unit names its prose variant: "crud_todo_list@1:minimal", "crud_todo_list@1/standard", ...
LEGACY_UNIT_RE: Final = re.compile(rf"[:/](?P<variant>{'|'.join(PROSE_VARIANTS)})$")


class DesignError(ValueError):
    """A design that cannot be resolved (answered 400 by the API)."""

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


def parse_design(raw: Any) -> Design:
    """``raw`` as a validated :class:`Design` (``DesignError`` with pydantic's message otherwise)."""
    if isinstance(raw, Design):
        return raw
    try:
        return Design.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        path = ".".join(str(part) for part in first.get("loc", ()))
        msg = f"invalid design at {path or '<root>'}: {first.get('msg')}"
        raise DesignError(msg, path=path or None) from exc


def parse_context_policy(level: str) -> ContextPolicy:
    """A ``context_policy`` level: a mode name (defaults for the rest) or a JSON ``ContextPolicy`` object."""
    text = level.strip()
    if text in CONTEXT_MODES:
        return ContextPolicy(mode=text)  # type: ignore[arg-type]
    try:
        return ContextPolicy.model_validate(json.loads(text))
    except (ValueError, ValidationError) as exc:
        msg = f"context_policy level {level!r} is neither a mode ({', '.join(CONTEXT_MODES)}) nor a ContextPolicy"
        raise DesignError(msg, path="factors.context_policy") from exc


def context_policy_level(policy: ContextPolicy) -> str:
    """The pinned level of a context policy: its canonical JSON (every default filled)."""
    return json.dumps(policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _check_factors(design: Design) -> None:
    allowed = APP_FACTORS if design.mode == "app" else SLOT_FACTORS
    for position, factor in enumerate(design.factors):
        path = f"factors[{position}]"
        if factor.key not in allowed:
            msg = f"factor {factor.key!r} is not allowed in {design.mode} mode"
            raise DesignError(msg, path=path)
        if factor.role == "fixed" and len(factor.levels) != 1:
            msg = f"fixed factor {factor.key!r} must have exactly one level, not {len(factor.levels)}"
            raise DesignError(msg, path=path)
        vocabulary = ENUM_LEVELS.get(factor.key)
        if vocabulary is not None:
            unknown = [level for level in factor.levels if level not in vocabulary]
            if unknown:
                msg = f"unknown {factor.key} level(s) {unknown}; expected one of {list(vocabulary)}"
                raise DesignError(msg, path=path)
        if factor.key == "context_policy":
            for level in factor.levels:
                parse_context_policy(level)
    if not any(factor.key == "model" for factor in design.factors):
        msg = "the design needs a model factor"
        raise DesignError(msg, path="factors")


def _check_grid(design: Design) -> None:
    fractions = [factor for factor in design.factors if factor.role == "fraction"]
    if design.grid.type == "fractional":
        if design.grid.fraction is None:
            msg = "a fractional grid needs grid.fraction.generator (e.g. 'D=ABC')"
            raise DesignError(msg, path="grid.fraction")
        if len(fractions) != 1:
            msg = "a fractional grid has exactly one factor with role 'fraction' (its generator's left side)"
            raise DesignError(msg, path="factors")
    elif fractions:
        msg = "factors with role 'fraction' need grid.type 'fractional'"
        raise DesignError(msg, path="factors")
    keys = {factor.key for factor in design.factors}
    for position, combo in enumerate(design.grid.exclude):
        unknown = sorted(set(combo) - keys)
        if unknown or not combo:
            msg = f"grid.exclude[{position}] names factors that are not in the design: {unknown or 'none given'}"
            raise DesignError(msg, path=f"grid.exclude[{position}]")


def _check_units(design: Design) -> None:
    units = design.units
    if design.mode == "app":
        if not units.tasks:
            msg = "an app study needs at least one task in units.tasks"
            raise DesignError(msg, path="units.tasks")
        if units.slot_sets or units.slot_targets:
            msg = "an app study has task units only (slot sets and targets are for slot studies)"
            raise DesignError(msg, path="units")
        if len(set(units.tasks)) != len(units.tasks):
            msg = "units.tasks lists a task twice"
            raise DesignError(msg, path="units.tasks")
        if not any(factor.key == "stack_profile" for factor in design.factors):
            msg = "an app study needs a stack_profile factor"
            raise DesignError(msg, path="factors")
        return
    if units.tasks:
        msg = "a slot study has slot-target units only (the task is fixed by its parents)"
        raise DesignError(msg, path="units.tasks")
    if not units.slot_sets and not units.slot_targets:
        msg = "a slot study needs units.slot_sets or units.slot_targets"
        raise DesignError(msg, path="units")


def check_structure(design: Design) -> None:
    """Raise :class:`DesignError` for a design that cannot be resolved (see the module docstring)."""
    _check_factors(design)
    _check_grid(design)
    _check_units(design)
    if design.kind == "confirmatory" and not design.analysis_plan:
        msg = "a confirmatory study needs an analysis_plan (pre-registered at launch)"
        raise DesignError(msg, path="analysis_plan")
    if design.mode == "app" and design.parent_selection is not None:
        msg = "parent_selection is for slot studies"
        raise DesignError(msg, path="parent_selection")


def _legacy_units(design: Design) -> list[Issue]:
    issues = []
    for position, ref in enumerate(design.units.tasks):
        if LEGACY_UNIT_RE.search(ref):
            issues.append(
                make_issue(
                    "prose_variant_in_unit",
                    design.kind,
                    message=f"unit {ref!r} names a prose variant; prose variants are the prose_variant factor",
                    path=f"units.tasks[{position}]",
                    data={"unit": ref},
                    fix=[IssueFix(label="Move variants to the prose_variant factor", action="move_prose_variant")],
                ),
            )
    return issues


def _slot_set_tasks(design: Design) -> list[Issue]:
    """``mixed_parents``: the slot sets (and the sets of the listed targets) must share one task version."""
    from backend.generation.models import SlotSet
    from backend.generation.models import SlotTarget

    set_ids = {str(sid) for sid in design.units.slot_sets}
    target_sets = SlotTarget.objects.filter(pk__in=list(design.units.slot_targets)).values_list(
        "slot_set_id",
        flat=True,
    )
    set_ids |= {str(sid) for sid in target_sets}
    tasks = set(SlotSet.objects.filter(pk__in=list(set_ids)).values_list("task_version_id", flat=True))
    if len(tasks) <= 1:
        return []
    return [
        make_issue(
            "mixed_parents",
            design.kind,
            message="the slot sets belong to different tasks; a slot study spans parents of one task",
            path="units.slot_sets",
            data={"task_versions": sorted(tasks)},
        ),
    ]


def _stop_rule_issues(design: Design, policy: ResourcePolicyContent | None) -> list[Issue]:
    issues: list[Issue] = []
    rules = list(policy.study.stop_rules) if policy is not None else []
    adaptive = [rule for rule in rules if rule.code == "ci_width_reached"]
    if adaptive and design.kind == "confirmatory":
        issues.append(
            make_issue(
                "adaptive_stop_in_confirmatory",
                "confirmatory",
                message="ci_width_reached stops adaptively; confirmatory studies use a pre-registered sequential plan",
                path="resource_policy.study.stop_rules",
            ),
        )
    needs_features = bool(adaptive) or (design.mode == "slot" and design.kind == "confirmatory")
    if needs_features and not design.slot_primary_features:
        issues.append(
            make_issue(
                "primary_feature_missing",
                design.kind,
                message="ci_width_reached and confirmatory slot studies need slot_primary_features",
                path="slot_primary_features",
            ),
        )
    for rule in adaptive:
        missing = sorted(set(rule.features) - set(design.slot_primary_features))
        if missing and design.slot_primary_features:
            issues.append(
                make_issue(
                    "primary_feature_missing",
                    design.kind,
                    message=f"ci_width_reached lists features outside slot_primary_features: {missing}",
                    path="resource_policy.study.stop_rules",
                    data={"features": missing},
                ),
            )
    return issues


def validate_design(
    design: Design,
    *,
    user: User | None = None,
    policy: ResourcePolicyContent | None = None,
) -> list[Issue]:
    """Registered issues of a design; raises :class:`DesignError` when it cannot be resolved at all."""
    del user  # visibility of the named entities is checked when they are resolved (study/plan.py)
    check_structure(design)
    issues = _legacy_units(design)
    if design.mode == "slot":
        issues += _slot_set_tasks(design)
    issues += _stop_rule_issues(design, policy)
    return issues
