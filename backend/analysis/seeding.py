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

# Fields copied verbatim from YAML into the model (when present).
_FIELDS = (
    "name",
    "description",
    "category",
    "kind",
    "target_language",
    "icon",
    "version",
    "docs_url",
    "install_cmd",
    "verify_cmd",
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

    created = updated = 0
    for path in sorted(DATA_DIR.glob("*.yaml")):
        if path.name == "catalog.yaml":
            continue
        data = yaml.safe_load(path.read_text()) or {}
        slug = data.get("slug") or path.stem
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
        if obj.kind == "container" and obj.parser_key and not parsers.has_parser(obj.parser_key):
            logger.warning(
                "Tool %r declares parser_key %r but no such parser is registered",
                slug,
                obj.parser_key,
            )

    return created, updated
