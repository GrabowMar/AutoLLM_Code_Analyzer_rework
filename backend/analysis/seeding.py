"""Seed the analyzer tool catalog ("shop") from ``data/tools/*.yaml``.

Idempotent: each tool is keyed by ``slug`` and upserted via
``update_or_create``. Invoked automatically after every ``migrate`` (see
``apps.AnalysisConfig``) and manually via ``manage.py seed_analysis_tools``.

Note: under pytest's ``--reuse-db`` the post_migrate signal only fires when
the test database is (re)created, so YAML changes require ``--create-db``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import yaml
from django.db import DEFAULT_DB_ALIAS

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data" / "tools"
CATALOG_FILE = DATA_DIR / "catalog.yaml"


def load_catalog_meta() -> dict:
    """Display metadata from ``catalog.yaml``: categories, modes, technologies."""
    if not CATALOG_FILE.exists():
        return {}
    return yaml.safe_load(CATALOG_FILE.read_text()) or {}


# Fields copied verbatim from YAML into the model (when present).
_FIELDS = (
    "name",
    "description",
    "category",
    "kind",
    "target_language",
    "analysis_mode",
    "technologies",
    "icon",
    "version",
    "docs_url",
    "install_cmd",
    "verify_cmd",
    "file_patterns",
    "uninstall_cmd",
    "run_cmd",
    "parser_key",
    "output_kind",
    "config_schema",
    "default_config",
    "run_timeout",
    "install_timeout",
    "sample_code",
    "sample_filename",
    "display_order",
    "is_enabled",
)


# Fields that define the instrument: a row whose stored value differs from
# its YAML still runs the old command / parser / install pin. The seed runs
# only after ``migrate`` (container start) or by hand, while the parser code
# reloads live, so a YAML change can sit unseeded next to new parsers.
MEASUREMENT_FIELDS = (
    "kind",
    "analysis_mode",
    "run_cmd",
    "parser_key",
    "output_kind",
    "file_patterns",
    "default_config",
    "version",
    "install_cmd",
    "verify_cmd",
)


def _tool_definitions() -> list[tuple[str, dict]]:
    """``(slug, yaml data)`` for every tool file in ``data/tools``."""
    out: list[tuple[str, dict]] = []
    for path in sorted(DATA_DIR.glob("*.yaml")):
        if path == CATALOG_FILE:
            continue
        data = yaml.safe_load(path.read_text()) or {}
        out.append((data.get("slug") or path.stem, data))
    return out


def stale_tools(*, using: str = DEFAULT_DB_ALIAS) -> dict[str, list[str]]:
    """Seeded tools whose stored definition differs from their YAML.

    Read-only. Maps slug -> the :data:`MEASUREMENT_FIELDS` that differ (a tool
    with a YAML but no row maps to ``["missing"]``). Empty means the catalog
    is in sync; otherwise ``manage.py seed_analysis_tools`` brings it in line.
    """
    from backend.analysis.models import AnalyzerTool

    if not DATA_DIR.exists():
        return {}
    rows = {t.slug: t for t in AnalyzerTool.objects.using(using).all()}
    stale: dict[str, list[str]] = {}
    for slug, data in _tool_definitions():
        row = rows.get(slug)
        if row is None:
            stale[slug] = ["missing"]
            continue
        fields = [f for f in MEASUREMENT_FIELDS if f in data and getattr(row, f) != data[f]]
        if fields:
            stale[slug] = fields
    return stale


def seed_tools(
    *,
    using: str = DEFAULT_DB_ALIAS,
    log: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """Upsert ``AnalyzerTool`` rows from ``data/tools/*.yaml``.

    Returns ``(created, updated)`` counts.
    """
    from backend.analysis.models import AnalyzerTool
    from backend.analysis.services import parsers

    emit = log or logger.debug

    if not DATA_DIR.exists():
        emit(f"No data dir at {DATA_DIR}")
        return 0, 0

    known_techs = {t["slug"] for t in load_catalog_meta().get("technologies", [])}
    created = updated = 0
    for slug, data in _tool_definitions():
        defaults = {f: data[f] for f in _FIELDS if f in data}
        defaults.setdefault("is_system", True)
        obj, was_created = AnalyzerTool.objects.using(using).update_or_create(
            slug=slug,
            defaults=defaults,
        )
        if was_created:
            created += 1
            emit(f"Created tool: {slug}")
        else:
            updated += 1
            emit(f"Updated tool: {slug}")
        unknown = set(obj.technologies or []) - known_techs
        if unknown:
            logger.warning("Tool %r lists technologies missing from catalog.yaml: %s", slug, sorted(unknown))
        if obj.kind == "container" and obj.parser_key and not parsers.has_parser(obj.parser_key):
            logger.warning(
                "Tool %r declares parser_key %r but no such parser is registered",
                slug,
                obj.parser_key,
            )

    return created, updated
