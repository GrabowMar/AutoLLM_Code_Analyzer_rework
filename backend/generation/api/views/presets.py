"""Run preset endpoints: list, read, validate, create, new version, archive.

``/presets/validate/`` is registered before ``/presets/{int:preset_id}/``:
Ninja matches routes in registration order, and a matching path without the
request's method answers 405 instead of falling through.
"""

from __future__ import annotations

from ninja import Query
from ninja.errors import HttpError

from backend.generation.api.schema import RunPresetCreateSchema
from backend.generation.api.schema import RunPresetSchema
from backend.generation.api.schema import RunPresetUpdateSchema
from backend.generation.api.schema import RunPresetValidateSchema
from backend.generation.api.views._router import router
from backend.generation.models import RunPreset
from backend.generation.services import presets as preset_service
from backend.generation.services.presets import KINDS
from backend.generation.services.presets import PresetError


def _payloads(presets: list[RunPreset]) -> list[dict]:
    latest = preset_service.latest_version_numbers(presets)
    return [preset_service.preset_payload(p, latest_version=latest.get((p.kind, p.slug), p.version)) for p in presets]


@router.get("/presets/", response=list[RunPresetSchema])
def list_run_presets(request, kind: str = Query(""), include_archived: bool = Query(default=False)):
    """Latest version per (kind, slug): system presets and your own (defaults first)."""
    if kind and kind not in KINDS:
        raise HttpError(400, f"kind must be one of {', '.join(KINDS)}")
    presets = preset_service.list_presets(request.auth, kind=kind or None, include_archived=include_archived)
    return _payloads(presets)


@router.post("/presets/validate/", response=dict)
def validate_run_preset(request, payload: RunPresetValidateSchema):
    """Normalize a draft config: ``{ok, errors, config}`` (config is null when not ok)."""
    normalized, errors = preset_service.check_config(payload.kind, payload.config)
    if normalized is not None:
        errors = preset_service.saving_errors(payload.kind, normalized)
    return {"ok": not errors, "errors": errors, "config": normalized if not errors else None}


@router.post("/presets/", response={201: RunPresetSchema, 400: dict})
def create_run_preset(request, payload: RunPresetCreateSchema):
    """Version 1 of a new preset (slugs are global per kind)."""
    try:
        preset = preset_service.create_preset(
            request.auth,
            kind=payload.kind,
            slug=payload.slug,
            name=payload.name,
            description=payload.description,
            config=payload.config,
        )
    except PresetError as exc:
        raise HttpError(exc.status, exc.detail) from exc
    return 201, preset_service.preset_payload(preset)


@router.get("/presets/{int:preset_id}/", response=RunPresetSchema)
def get_run_preset(request, preset_id: int):
    """Any version of a visible preset, archived included (pinned job provenance)."""
    try:
        preset = preset_service.get_visible_preset(request.auth, preset_id)
    except RunPreset.DoesNotExist as exc:
        raise HttpError(404, "Preset not found") from exc
    return preset_service.preset_payload(preset)


@router.get("/presets/{kind}/{slug}/versions/", response=list[RunPresetSchema])
def list_run_preset_versions(request, kind: str, slug: str):
    """Every version of a preset, newest first."""
    versions = preset_service.preset_versions(request.auth, kind, slug)
    if not versions:
        raise HttpError(404, "Preset not found")
    return _payloads(versions)


@router.put("/presets/{kind}/{slug}/", response={200: RunPresetSchema, 400: dict, 403: dict, 404: dict})
def update_run_preset(request, kind: str, slug: str, payload: RunPresetUpdateSchema):
    """A config change creates version+1; a name/description change does not."""
    try:
        preset = preset_service.update_preset(
            request.auth,
            kind=kind,
            slug=slug,
            name=payload.name,
            description=payload.description,
            config=payload.config,
        )
    except PresetError as exc:
        raise HttpError(exc.status, exc.detail) from exc
    return preset_service.preset_payload(preset)


@router.delete("/presets/{kind}/{slug}/", response={200: dict, 403: dict, 404: dict})
def archive_run_preset(request, kind: str, slug: str):
    """Archive every version (jobs that used the preset keep their references)."""
    try:
        preset_service.archive_preset(request.auth, kind=kind, slug=slug)
    except PresetError as exc:
        raise HttpError(exc.status, exc.detail) from exc
    return {"success": True}
