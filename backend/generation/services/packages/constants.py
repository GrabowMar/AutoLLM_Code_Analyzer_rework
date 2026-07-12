"""Shared constants for bundle/template package import and export."""

from __future__ import annotations

from pathlib import Path

BUNDLE_PACKAGE_SCHEMA_VERSION = 1
BUNDLE_PACKAGE_KIND = "llm-lab-template-bundle"
TEMPLATE_PACKAGE_SCHEMA_VERSION = 2
TEMPLATE_PACKAGE_KIND = "llm-lab-template-package"
ALLOWED_CONFLICT_STRATEGIES = {"rename", "overwrite", "error"}
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
