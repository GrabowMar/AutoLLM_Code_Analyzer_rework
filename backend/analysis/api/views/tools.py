"""Catalog ("shop") endpoints: browse available analyzer tools."""

from __future__ import annotations

from typing import Any

from django.shortcuts import get_object_or_404

from backend.analysis.api.schema import AnalyzerToolSchema
from backend.analysis.api.schema import AppCoverageSchema
from backend.analysis.api.schema import CatalogMetaSchema
from backend.analysis.api.views._router import analyzers_router
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import AnalyzerWorkspace
from backend.analysis.models import InstalledTool
from backend.analysis.seeding import load_catalog_meta
from backend.analysis.services.coverage import app_coverage


def install_state_for(user) -> dict[str, InstalledTool]:
    """Return ``{tool_slug: InstalledTool}`` for the user's workspace."""
    workspace = AnalyzerWorkspace.objects.filter(user=user).first()
    if workspace is None:
        return {}
    return {it.tool.slug: it for it in workspace.installed_tools.select_related("tool").all()}


def serialize_tool(tool: AnalyzerTool, installs: dict[str, InstalledTool]) -> dict[str, Any]:
    installed = installs.get(tool.slug)
    return {
        "slug": tool.slug,
        "name": tool.name,
        "description": tool.description,
        "category": tool.category,
        "kind": tool.kind,
        "target_language": tool.target_language,
        "analysis_mode": tool.analysis_mode,
        "technologies": tool.technologies or [],
        "icon": tool.icon,
        "version": tool.version,
        "docs_url": tool.docs_url,
        "output_kind": tool.output_kind,
        "config_schema": tool.config_schema or [],
        "default_config": tool.default_config or {},
        "run_timeout": tool.run_timeout,
        "is_enabled": tool.is_enabled,
        "installed": installed is not None,
        "install_status": installed.status if installed else "",
        "installed_version": installed.installed_version if installed else "",
        "update_available": installed.update_available if installed else False,
    }


@analyzers_router.get("/tools/", response=list[AnalyzerToolSchema])
def list_tools(request):
    """List the catalog of available analyzer tools."""
    installs = install_state_for(request.auth)
    tools = AnalyzerTool.objects.filter(is_enabled=True)
    return [serialize_tool(t, installs) for t in tools]


@analyzers_router.get("/catalog/", response=CatalogMetaSchema)
def catalog_meta(request):
    """Shop vocabulary: categories, static/dynamic modes and scanned technologies."""
    meta = load_catalog_meta()
    return {key: meta.get(key) or [] for key in ("categories", "modes", "technology_groups", "technologies")}


@analyzers_router.get("/coverage/", response=AppCoverageSchema)
def coverage(request):
    """Technologies in the caller's recent generated apps and how many apps each tool fits."""
    return app_coverage(request.auth)


@analyzers_router.get("/tools/{slug}/", response=AnalyzerToolSchema)
def get_tool(request, slug: str):
    """Get a single catalog tool by slug."""
    tool = get_object_or_404(AnalyzerTool, slug=slug, is_enabled=True)
    return serialize_tool(tool, install_state_for(request.auth))
