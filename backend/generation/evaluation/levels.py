"""``eval_level`` to stage toggles (spec section 4.8); skipped stages are reported as not measured.

The level table is the default; ``EvaluationSpec.overrides`` can switch single stages on or off. A stage
that is off is never reported as clean or 0: ``not_measured_stages`` lists it so results can say so.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Final

if TYPE_CHECKING:
    from backend.generation.contracts.plan import EvaluationSpec

__all__ = ["LEVEL_TABLE", "STAGES", "linked_probes_only", "not_measured_stages", "stages_for"]

# Dependency order: a stage only appears after the stages it needs.
STAGES: Final = (
    "lock",
    "build",
    "functional",
    "probes",
    "adherence_static",
    "adherence",
    "baseline",
    "analysis_static",
    "analysis_dynamic",
)

_T, _F = True, False
# lock, build, functional (start + tester), probes, adherence (static part), adherence (full),
# baseline, SAST (default tools), DAST.
LEVEL_TABLE: Final[dict[str, dict[str, bool]]] = {
    level: dict(zip(STAGES, row, strict=True))
    for level, row in {
        "none": (_F, _F, _F, _F, _F, _F, _F, _F, _F),
        "static": (_T, _F, _F, _F, _T, _F, _F, _T, _F),
        "build": (_T, _T, _F, _F, _T, _T, _F, _F, _F),
        "functional": (_T, _T, _T, _F, _T, _T, _T, _F, _F),
        "security": (_T, _T, _T, _T, _T, _T, _T, _T, _F),
        "full": (_T, _T, _T, _T, _T, _T, _T, _T, _T),
    }.items()
}

# A stage cannot run without the stage it depends on.
_REQUIRES: Final = {
    "build": "lock",
    "functional": "build",
    "probes": "functional",
    "adherence": "build",  # the runtime part (Server header, login response) needs a built app
    "baseline": "functional",
    "analysis_dynamic": "functional",
}

_BOOL_OVERRIDES: Final = ("build", "functional", "probes", "baseline", "analysis_dynamic")


def stages_for(spec: EvaluationSpec) -> dict[str, bool]:
    """Which stages run for an evaluation spec.

    Order: level table, then overrides, then the slot rules of 4.8, then dependencies.

    Slot rules (``spec.slot`` set): ``slot.mode=static_only`` switches the build off (so everything that
    needs a running app follows it off in the dependency pass); and whenever the functional stage is on,
    the probe stage is on, restricted to the target's ``linked_probes`` (``linked_probes_only``). The slot
    rule wins over a ``probes=False`` override, because 4.8 says the linked probes *always* run.
    """
    stages = dict(LEVEL_TABLE[spec.eval_level])
    overrides = spec.overrides
    for name in _BOOL_OVERRIDES:
        value = getattr(overrides, name)
        if value is not None:
            stages[name] = bool(value)
    if overrides.adherence is not None:
        stages["adherence"] = stages["adherence_static"] = bool(overrides.adherence)
    if overrides.analysis_static is not None:
        stages["analysis_static"] = overrides.analysis_static not in ("none", [])
    if spec.slot is not None:
        if spec.slot.mode == "static_only":
            stages["build"] = False
        if stages["functional"] and stages["build"]:
            stages["probes"] = True
    for stage in STAGES:
        needed = _REQUIRES.get(stage)
        if needed is not None and not stages[needed]:
            stages[stage] = False
    return stages


def linked_probes_only(spec: EvaluationSpec) -> bool:
    """True when the probe stage (if on) runs only the slot target's ``linked_probes`` (slot mode, 4.8)."""
    return spec.slot is not None and stages_for(spec)["probes"]


def not_measured_stages(spec: EvaluationSpec) -> list[str]:
    """The stages this spec skips; results report them as "not measured", never as clean or 0."""
    return [name for name, on in stages_for(spec).items() if not on]
