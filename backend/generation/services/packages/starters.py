"""Starter template packages bundled with the app: ``data/packages/<slug>.yaml``.

A starter file is a template package (current schema) plus a header::

    starter: {slug: <file stem>, name: ..., description: ...}

Two shortcuts keep starters from copying files that seeding already reads,
so they cannot drift from them:

* ``content_file: <path relative to data/>`` -- an app spec entry is that
  JSON/YAML file (inline keys override it); a block entry takes its
  ``content`` from the file.
* profile ``block_refs`` without a ``version`` resolve at import time to the
  latest *system* version of the slug (see ``importers._default_ref_version``).

``content_file`` is resolved here, for repository data only; uploaded
packages never read server files.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from typing import Any

import yaml

from backend.generation.services.packages.constants import DATA_DIR
from backend.generation.services.packages.constants import STARTERS_DIR
from backend.generation.services.packages.importers import import_template_package_data
from backend.generation.services.packages.importers import upgrade_template_package

if TYPE_CHECKING:
    from pathlib import Path

    from django.contrib.auth.models import AbstractUser

logger = logging.getLogger(__name__)

COUNTED_SECTIONS = ("app_specs", "blocks", "profiles", "stacks", "presets")


def starter_paths() -> list[Path]:
    if not STARTERS_DIR.is_dir():
        return []
    return sorted(STARTERS_DIR.glob("*.yaml"))


def load_starter_document(path: Path) -> dict[str, Any]:
    """The raw starter document (``ValueError`` if it is not valid YAML or not a mapping)."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        msg = f"Starter package {path.name} is not valid YAML: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = f"Starter package {path.name} is not a mapping"
        raise ValueError(msg)
    return data


def _starter_path(slug: str) -> Path:
    for path in starter_paths():
        if path.stem == slug:
            return path
    msg = f"Unknown starter package: {slug}"
    raise FileNotFoundError(msg)


def _header(document: dict[str, Any], slug: str) -> dict[str, str]:
    header = document.get("starter") if isinstance(document.get("starter"), dict) else {}
    return {
        "slug": slug,
        "name": str(header.get("name") or slug),
        "description": str(header.get("description") or ""),
    }


def list_starter_template_packages() -> list[dict[str, Any]]:
    """``[{slug, name, description, <section>_count..., app_template_count, bundle_count}]`` by slug.

    Counts come from the files alone (no ``content_file`` reads, no DB).
    ``app_template_count`` / ``bundle_count`` are the pre-v3 names of the
    app-spec and profile counts.
    """
    packages: list[dict[str, Any]] = []
    for path in starter_paths():
        try:
            document = load_starter_document(path)
        except (OSError, ValueError):
            # The lint catches broken starters; the list must not 500 on one.
            logger.exception("Skipping unreadable starter package %s", path.name)
            continue
        assets = document.get("assets") if isinstance(document.get("assets"), dict) else {}
        counts = {
            f"{section[:-1]}_count": len(assets.get(section) or [])
            for section in COUNTED_SECTIONS  # app_spec_count, block_count, ...
        }
        packages.append(
            {
                **_header(document, path.stem),
                **counts,
                "app_template_count": counts["app_spec_count"],
                "bundle_count": counts["profile_count"],
            },
        )
    return packages


def build_starter_template_package(slug: str) -> dict[str, Any]:
    """The starter as an importable package: ``content_file`` entries resolved, header kept.

    ``FileNotFoundError`` for an unknown slug, ``ValueError`` for a broken file.
    """
    document = load_starter_document(_starter_path(slug))
    package = upgrade_template_package(document)
    assets = package["assets"]
    resolved = {
        **assets,
        "app_specs": [resolve_app_spec_entry(entry) for entry in assets["app_specs"]],
        "blocks": [resolve_block_entry(entry) for entry in assets["blocks"]],
    }
    return {**package, "starter": _header(document, slug), "assets": resolved}


def import_starter_template_package(
    *,
    slug: str,
    user: AbstractUser,
    conflict_strategy: str = "rename",
) -> dict[str, list[Any]]:
    package = build_starter_template_package(slug)
    return import_template_package_data(package=package, user=user, conflict_strategy=conflict_strategy)


def resolve_content_file(relative_path: Any) -> Path:
    """A ``content_file`` path under ``data/`` (``ValueError`` if absent or outside it)."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        msg = "content_file must be a path relative to backend/generation/data/"
        raise ValueError(msg)
    root = DATA_DIR.resolve()
    path = (DATA_DIR / relative_path).resolve()
    if not path.is_relative_to(root):
        msg = f"content_file {relative_path!r} is outside backend/generation/data/"
        raise ValueError(msg)
    if not path.is_file():
        msg = f"content_file {relative_path!r} does not exist"
        raise ValueError(msg)
    return path


def resolve_app_spec_entry(entry: Any) -> Any:
    """An app spec entry with its ``content_file`` read (JSON or YAML); other entries unchanged."""
    if not isinstance(entry, dict) or "content_file" not in entry:
        return entry
    path = resolve_content_file(entry["content_file"])
    text = path.read_text(encoding="utf-8")
    is_json = path.suffix == ".json"
    try:
        data = json.loads(text) if is_json else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as exc:  # JSONDecodeError is a ValueError
        msg = f"content_file {entry['content_file']!r} is not valid {'JSON' if is_json else 'YAML'}: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = f"content_file {entry['content_file']!r} is not an app spec object"
        raise ValueError(msg)
    overrides = {k: v for k, v in entry.items() if k != "content_file"}
    return {**data, **overrides}


def resolve_block_entry(entry: Any) -> Any:
    """A block entry whose ``content`` comes from its ``content_file``; other entries unchanged."""
    if not isinstance(entry, dict) or "content_file" not in entry:
        return entry
    path = resolve_content_file(entry["content_file"])
    block = {k: v for k, v in entry.items() if k != "content_file"}
    block["content"] = path.read_text(encoding="utf-8")
    return block
