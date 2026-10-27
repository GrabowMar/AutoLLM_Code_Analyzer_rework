"""Data models of an app spec, and the spec content hash.

Spec schema v3 lists any number of models under ``data_models``; v1/v2 specs
(and every spec stored before v3) carry one ``data_model`` object. Readers go
through :func:`normalize_data_models` so both shapes look alike.

``AppRequirementTemplate.data_model`` stores the spec's own form, so a spec
round-trips unchanged: the legacy object, or the v3 list (see
:func:`stored_data_model`). The app dict a job snapshot embeds
(``profile_resolver.app_requirement_to_dict``) always carries both keys.
"""

from __future__ import annotations

import json
from typing import Any

from backend.generation.services.versioning import content_hash

# The spec format ``schema.json`` describes now; lint rules beyond the schema
# apply from this version on (older specs only have to validate).
SPEC_SCHEMA_VERSION = 3


def _raw_models(spec: Any) -> Any:
    if isinstance(spec, dict):
        models = spec.get("data_models")
        if isinstance(models, list) and models:
            return models
        return spec.get("data_model")
    # An AppRequirementTemplate row: the column holds either form.
    return getattr(spec, "data_model", None)


def normalize_data_models(spec: Any) -> list[dict[str, Any]]:
    """Every data model of *spec* as ``[{name, description?, fields}, ...]``.

    *spec* is a spec/app dict (``data_models`` wins over ``data_model``) or
    an ``AppRequirementTemplate`` row. A legacy single object becomes a
    one-item list; empty objects and non-objects are dropped. Items are
    shallow copies, keys kept as written.
    """
    raw = _raw_models(spec)
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [dict(model) for model in raw if isinstance(model, dict) and model]


def primary_data_model(spec: Any) -> dict[str, Any]:
    """The first data model (the legacy ``data_model``), or ``{}``."""
    models = normalize_data_models(spec)
    return models[0] if models else {}


def stored_data_model(spec: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
    """The value of the ``data_model`` column for *spec*: the v3 list, else the legacy object."""
    models = spec.get("data_models")
    if isinstance(models, list) and models:
        return models
    model = spec.get("data_model")
    return model if isinstance(model, (dict, list)) else {}


def data_model_for_prompt(spec: Any) -> dict[str, Any] | list[dict[str, Any]]:
    """What the ``data_model`` prompt variable renders: the one model, or the list of all of them."""
    models = normalize_data_models(spec)
    if len(models) == 1:
        return models[0]
    return models or {}


def data_model_json(spec: Any) -> str:
    """:func:`data_model_for_prompt` as indented JSON (``{}`` when the spec has no model)."""
    return json.dumps(data_model_for_prompt(spec), indent=2)


def spec_fields_of(row: Any) -> dict[str, Any]:
    """The spec fields of an ``AppRequirementTemplate`` row, in spec-file form (for hashing)."""
    stored = getattr(row, "data_model", None)
    spec: dict[str, Any] = {
        "category": row.category,
        "description": row.description,
        "backend_requirements": row.backend_requirements or [],
        "frontend_requirements": row.frontend_requirements or [],
        "admin_requirements": row.admin_requirements or [],
        "api_endpoints": row.api_endpoints or [],
        "admin_api_endpoints": row.admin_api_endpoints or [],
        "difficulty": row.difficulty or "",
    }
    if isinstance(stored, list):
        spec["data_models"] = stored
    else:
        spec["data_model"] = stored or {}
    return spec


def requirement_hash_payload(data: dict[str, Any]) -> dict[str, Any]:
    """The fields that define a spec's content (name and slug are cosmetic).

    ``data_models`` joins the payload only when the spec has it, so legacy
    specs keep the hash (and version) they were seeded with.
    """
    payload = {
        "category": data.get("category", ""),
        "description": data.get("description", ""),
        "backend_requirements": data.get("backend_requirements", []),
        "frontend_requirements": data.get("frontend_requirements", []),
        "admin_requirements": data.get("admin_requirements", []),
        "api_endpoints": data.get("api_endpoints", []),
        "data_model": data.get("data_model", {}),
        "admin_api_endpoints": data.get("admin_api_endpoints", []),
        "difficulty": data.get("difficulty", ""),
    }
    models = data.get("data_models")
    if isinstance(models, list) and models:
        payload["data_models"] = models
        payload["data_model"] = {}
    return payload


def requirement_content_hash(data: dict[str, Any]) -> str:
    """sha256 of :func:`requirement_hash_payload` (``AppRequirementTemplate.content_hash``)."""
    return content_hash(requirement_hash_payload(data))


def row_content_hash(row: Any) -> str:
    """The content hash of an ``AppRequirementTemplate`` row, computed from its fields.

    Equal to the stored ``content_hash`` of a seeded row, but also right for
    rows saved before hashing or edited outside the seeder.
    """
    return requirement_content_hash(spec_fields_of(row))
