"""Shared constants for bundle/template package import and export."""

from __future__ import annotations

from pathlib import Path

BUNDLE_PACKAGE_SCHEMA_VERSION = 1
BUNDLE_PACKAGE_KIND = "llm-lab-template-bundle"
# v3 added ``assets.presets`` (run presets); v1/v2 packages are upgraded on import.
TEMPLATE_PACKAGE_SCHEMA_VERSION = 3
SUPPORTED_TEMPLATE_PACKAGE_SCHEMA_VERSIONS = (1, 2, 3)
TEMPLATE_PACKAGE_KIND = "llm-lab-template-package"
ALLOWED_CONFLICT_STRATEGIES = {"rename", "overwrite", "error"}
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STARTERS_DIR = DATA_DIR / "packages"
