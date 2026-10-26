"""Catalog, analyzer image, tool self-test and reviewer options endpoints.

Tools are baked into one pinned analyzer image; there is nothing to install
per user. ``version`` comes from the built image's manifest (falling back to
the seeded catalog row), and each tool carries its canary expectations and
the latest self-test verdict on the current image.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache
from ninja import Query
from ninja import Status
from ninja.errors import HttpError

from backend.analysis.api import common
from backend.analysis.api.schema import RunListItemSchema
from backend.analysis.api.schema import SelftestSchema
from backend.analysis.api.schema import ToolSchema
from backend.analysis.api.serializers import run_list_item
from backend.analysis.api.views._router import router
from backend.analysis.engine import canary
from backend.analysis.engine import catalog
from backend.analysis.engine import sandbox
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.engine.reviewer import config as reviewer_config
from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool

logger = logging.getLogger(__name__)


def _current_image_tag() -> str:
    try:
        return sandbox.image_tag()
    except Exception as exc:  # noqa: BLE001 - the tag is advisory here
        logger.warning("image_tag failed: %s", exc)
        return ""


def _manifest_version(manifest: dict[str, Any] | None, slug: str) -> str:
    tools = (manifest or {}).get("tools") or {}
    entry = tools.get(slug) if isinstance(tools, dict) else None
    if isinstance(entry, dict):
        return str(entry.get("version") or "")
    return entry if isinstance(entry, str) else ""


def _expectations() -> dict[str, Any]:
    try:
        return canary.expectations().get("tools", {})
    except (OSError, ValueError) as exc:
        logger.warning("canary expectations unavailable: %s", exc)
        return {}


def tool_payload(
    spec: catalog.ToolSpec,
    *,
    row: AnalyzerTool | None,
    manifest: dict[str, Any] | None,
    expectations: dict[str, Any],
    canaries: dict[str, Any],
) -> dict[str, Any]:
    image_version = _manifest_version(manifest, spec.slug)
    return {
        "slug": spec.slug,
        "name": spec.name,
        "description": spec.description,
        "kind": spec.kind,
        "category": spec.category,
        "languages": list(spec.languages),
        "file_kinds": list(spec.file_kinds),
        "output_kind": spec.output_kind,
        "icon": spec.icon,
        "docs_url": spec.docs_url,
        "display_order": spec.display_order,
        "default_enabled": spec.default_enabled,
        "default_required": spec.default_required,
        "mem_limit": spec.mem_limit,
        "timeout_s": spec.timeout_s,
        "config_schema": [dict(field) for field in spec.config_schema],
        "default_config": dict(spec.default_config),
        "spec_hash": spec.spec_hash,
        # Only the built image's manifest is a verified version; the catalog's is what it should be.
        "version": image_version or (row.version if row else "") or str(spec.raw.get("version") or ""),
        "version_source": "image_manifest" if image_version else "catalog",
        "is_enabled": row.is_enabled if row else True,
        "expectations": expectations.get(spec.slug),
        "canary": canaries.get(spec.slug),
    }


def catalog_payloads(*, slug: str | None = None) -> list[dict[str, Any]]:
    try:
        specs = catalog.load_specs()
    except catalog.SpecError as exc:
        # A malformed data/tools/*.yaml is a deployment problem, not a server crash.
        logger.exception("The tool catalog is invalid")
        raise HttpError(503, f"The tool catalog is invalid: {exc}") from exc
    if slug is not None:
        specs = {slug: specs[slug]} if slug in specs else {}
    rows = {row.slug: row for row in AnalyzerTool.objects.filter(slug__in=list(specs))}
    image = common.image_status()
    manifest = image.get("manifest") if isinstance(image.get("manifest"), dict) else None
    try:
        canaries = canary.latest_per_tool(image.get("tag") or _current_image_tag() or None)
    except Exception as exc:  # noqa: BLE001 - verdicts are advisory on the catalog
        logger.warning("latest_per_tool failed: %s", exc)
        canaries = {}
    expectations = _expectations()
    ordered = sorted(specs.values(), key=lambda s: (s.display_order, s.name))
    return [
        tool_payload(s, row=rows.get(s.slug), manifest=manifest, expectations=expectations, canaries=canaries)
        for s in ordered
    ]


@router.get("/tools/", response=list[ToolSchema])
def list_tools(request):
    """Every catalog tool (static, metrics and the AI reviewer) with version and self-test verdict."""
    return catalog_payloads()


@router.get("/tools/{slug}/", response=ToolSchema)
def get_tool(request, slug: str):
    payloads = catalog_payloads(slug=slug)
    if not payloads:
        raise HttpError(404, "Unknown tool")
    return payloads[0]


@router.get("/image/", response=dict)
def get_image(request, refresh: bool = Query(default=False)):
    """The analyzer image: tag, built?, image id, manifest (tool versions, rule-pack hashes), build state."""
    status = common.image_status(refresh=refresh)
    status.setdefault("tag", _current_image_tag())
    return status


@router.post("/image/build/", response={200: dict, 202: dict})
def build_image(request):
    """Build the analyzer image in the background (Celery when a worker answers, else a thread)."""
    status = common.image_status(refresh=True)
    if status.get("built"):
        return Status(200, status)
    if not status.get("daemon_ok", True):
        raise HttpError(503, f"The analysis Docker daemon is unreachable: {status.get('error', '')}")
    if not status.get("building"):
        # Concurrent requests are harmless: builds serialize on a cross-process
        # lock and a finished build makes the next one a no-op.
        status = dict(sandbox.request_image_build())
        cache.set(common.IMAGE_BUILD_LOCK_KEY, getattr(request.auth, "id", 0), common.IMAGE_BUILD_FLAG_TTL_S)
        cache.delete(common.IMAGE_STATUS_CACHE_KEY)
    status.update({"building": True, "build_requested": True})
    return Status(202, status)


@router.post(
    "/selftest/",
    response={201: RunListItemSchema},
    openapi_extra=common.optional_body_openapi(SelftestSchema),
)
def start_selftest(request):
    """Run the canary fixtures through the real pipeline (``{"tool_slugs": [...]}``; default: all static tools)."""
    payload, error = common.optional_body(request, SelftestSchema)
    if error is not None:
        return error
    try:
        run = canary.run_selftest(user=request.auth, tool_slugs=payload.tool_slugs)
    except ConfigError as exc:
        return common.config_error(exc)
    except FileNotFoundError as exc:
        raise HttpError(503, f"Canary fixtures are missing: {exc}") from exc
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    run = AnalysisRun.objects.select_related("profile").prefetch_related("results").get(pk=run.pk)
    return Status(201, run_list_item(run))


@router.get("/selftest/latest/", response=dict)
def latest_selftest(request, image_tag: str = Query(""), any_image: bool = Query(default=False)):
    """Latest finished self-test evaluation and the per-tool verdicts (default: on the current image)."""
    tag = None if any_image else (image_tag or _current_image_tag() or None)
    try:
        latest = canary.latest(tag)
        per_tool = canary.latest_per_tool(tag)
    except (OSError, ValueError) as exc:
        raise HttpError(503, f"Canary expectations are unavailable: {exc}") from exc
    return {"image_tag": tag, "latest": latest, "per_tool": per_tool}


@router.get("/reviewer/options/", response=dict)
def reviewer_options(request):
    """Engines, harnesses (with option schemas), default model, prompt versions, limits; plus key state."""
    try:
        options = dict(reviewer_config.reviewer_options())
    except Exception as exc:
        logger.exception("reviewer_options failed")
        raise HttpError(503, f"Reviewer options are unavailable: {exc}") from exc
    options["key_present"] = common.key_present(request.auth)
    return options
