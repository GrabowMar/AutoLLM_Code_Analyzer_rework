"""Harnesses and config API (spec section 9.3).

Mounted by ``backend.generation.api`` without a prefix, so routes declare their full path under
``/api/generation/``. Static paths are registered before parameterised ones. Operation ids (published in
``SAMPLE_GEN_V3_CHANGES.md``): ``harnesses_list``, ``harness_facts`` and ``generation_config``.
"""

from __future__ import annotations

from typing import Any
from typing import Final
from typing import get_args

from django.conf import settings
from django.http import HttpRequest  # noqa: TC002 - ninja resolves the annotations at runtime
from ninja import Router
from ninja import Schema
from ninja.responses import Status
from pydantic import Field

from backend.generation.contracts import ErrorOut
from backend.generation.contracts.issues import registry_payload
from backend.generation.contracts.library import PROSE_VARIANTS
from backend.generation.contracts.plan import APP_FACTORS
from backend.generation.contracts.plan import FACTOR_KEYS
from backend.generation.contracts.plan import MAX_REPEATS
from backend.generation.contracts.plan import SLOT_FACTORS
from backend.generation.contracts.plan import EvaluationSpec
from backend.generation.contracts.resources import KNOB_META
from backend.generation.contracts.resources import RoutingSettings
from backend.generation.contracts.slot import ContextPolicy
from backend.generation.contracts.slot import HoleMode
from backend.generation.harnesses import registry
from backend.generation.models.choices import AuthMode

router = Router(tags=["generation: harnesses"])

MAX_CELLS: Final = 20_000
SLOT_EXECUTORS: Final = ("single_shot", "restricted_agent")
KNOB_CLASSES: Final = {
    "C": "confound: changes what the model sees or how it samples (in generation_policy_hash)",
    "R": "recorded limit: changes the outcome only through truncation or stopping (hashed; stop reason recorded)",
    "O": "operational: cost, speed or scheduling only (recorded, never hashed)",
    "S": "stopping/design: study-level rule that changes inference (in prereg_hash)",
}


class OptionFieldOut(Schema):
    key: str
    type: str
    label: str
    help: str
    default: Any = None
    choices: list[dict[str, str]] = Field(default_factory=list)
    min: int | None = None
    max: int | None = None


class HarnessOut(Schema):
    name: str
    label: str
    description: str
    protocol: str
    capabilities: dict[str, Any]
    enabled: bool
    blocked_reason: str | None = None
    versions: list[str]
    pinned_version: str
    option_schema: list[OptionFieldOut]
    default_options: dict[str, Any]
    facts_version: int
    vendor_affinity: str | None = None
    min_call_output_tokens: int
    open_questions: list[str]
    endpoint_open_questions: dict[str, list[str]] = Field(default_factory=dict)
    exploratory_only: str | None = None


class IssueCodeOut(Schema):
    code: str
    exploratory: str | None = None  # None: the code does not apply to that study kind
    confirmatory: str | None = None
    alternatives: list[str]
    owner: str
    summary: str


class ConfigOut(Schema):
    factor_vocabulary: dict[str, Any]
    stacks_matrix: dict[str, list[str]]
    auth_modes: list[dict[str, str]]
    prose_variants: list[str]
    hole_modes: list[str]
    context_modes: list[str]
    executors: list[str]
    eval_levels: list[str]
    serving_tiers: list[str]
    knob_classes: dict[str, str]
    harnesses: list[dict[str, Any]]
    issue_codes: list[IssueCodeOut]
    limits: dict[str, Any]


@router.get("/harnesses/", response=list[HarnessOut], operation_id="harnesses_list")
def harnesses_list(request: HttpRequest) -> list[dict[str, Any]]:
    """Every adapter with ``enabled``/``blocked_reason`` (disabled adapters are listed, never hidden)."""
    return [item.to_dict() for item in registry.entries()]


@router.get(
    "/harnesses/{name}/facts/",
    response={200: dict[str, Any], 404: ErrorOut},
    operation_id="harness_facts",
)
def harness_facts(request: HttpRequest, name: str) -> Any:
    """The adapter's ``HarnessFacts`` as a dict (section 5.3)."""
    try:
        adapter = registry.get(name)
    except KeyError:
        return Status(404, {"detail": f"unknown harness {name!r}", "code": "harness_unknown"})
    return adapter.facts.to_dict()


def _stacks_matrix(request: HttpRequest) -> dict[str, list[str]]:
    """``{backend framework: [frontend frameworks]}`` over the visible, non-archived stack profiles."""
    from backend.generation.library import service

    user = getattr(request, "user", None)
    rows = (
        service.visible("stack", user)
        .filter(is_archived=False)
        .values_list("backend_framework", "frontend_framework")
        .distinct()
    )
    matrix: dict[str, set[str]] = {}
    for backend, frontend in rows:
        matrix.setdefault(backend, set()).add(frontend)
    return {backend: sorted(frontends) for backend, frontends in sorted(matrix.items())}


def _literal_values(model: type, field: str) -> list[str]:
    return [str(v) for v in get_args(model.model_fields[field].annotation)]


@router.get("/config/", response=ConfigOut, operation_id="generation_config")
def generation_config(request: HttpRequest) -> dict[str, Any]:
    """Vocabularies and limits the Study builder needs (section 9.3)."""
    return {
        "factor_vocabulary": {
            "keys": list(FACTOR_KEYS),
            "app": sorted(APP_FACTORS),
            "slot": sorted(SLOT_FACTORS),
        },
        "stacks_matrix": _stacks_matrix(request),
        "auth_modes": [{"value": value, "label": str(label)} for value, label in AuthMode.choices],
        "prose_variants": list(PROSE_VARIANTS),
        "hole_modes": [str(v) for v in get_args(HoleMode)],
        "context_modes": _literal_values(ContextPolicy, "mode"),
        "executors": list(SLOT_EXECUTORS),
        "eval_levels": _literal_values(EvaluationSpec, "eval_level"),
        "serving_tiers": _literal_values(RoutingSettings, "serving_tier"),
        "knob_classes": {
            cls: f"{text} ({sum(1 for k in KNOB_META.values() if k.cls == cls)} knobs)"
            for cls, text in KNOB_CLASSES.items()
        },
        "harnesses": [
            {"name": item.adapter.name, "label": item.adapter.label, "enabled": item.enabled}
            for item in registry.entries()
        ],
        "issue_codes": registry_payload(),
        "limits": {
            "max_repeats": MAX_REPEATS,
            "max_cells": MAX_CELLS,
            "sandbox_max": {
                "memory": settings.AGENT_SANDBOX_MEM_LIMIT_MAX,
                "nano_cpus": settings.AGENT_SANDBOX_NANO_CPUS_MAX,
            },
        },
    }
