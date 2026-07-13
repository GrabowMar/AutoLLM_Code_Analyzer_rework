"""Seed builtin Stack rows from ``runtime/scaffolding/`` after every migrate.

Mirrors :mod:`backend.generation.seeding`: idempotent, content-hash aware, and
never raises out of the ``post_migrate`` signal. The manifest + skeleton
directories stay in-repo as the source of truth for builtin stacks; the DB
rows give jobs an immutable slug+version to pin in their snapshots.

Note: under pytest's ``--reuse-db`` the post_migrate signal only fires when
the test database is (re)created, so scaffolding/ changes require ``--create-db``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from django.db import DEFAULT_DB_ALIAS

from backend.generation.services.versioning import content_hash

logger = logging.getLogger(__name__)

SCAFFOLDING_DIR = Path(__file__).resolve().parent / "scaffolding"
MANIFEST_PATH = SCAFFOLDING_DIR / "manifest.json"


def _read_skeleton_files(directory: Path) -> dict[str, str]:
    """Walk a skeleton dir into a {relative_path: text} map (sorted for stable hashing)."""
    files: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(directory).as_posix()
        files[rel] = path.read_text(encoding="utf-8")
    return files


def _stack_hash_payload(config: dict[str, Any], files: dict[str, str]) -> dict[str, Any]:
    return {
        "has_frontend": bool(config.get("has_frontend")),
        "default_port": config.get("default_port", 8000),
        "patch_profile": config.get("patch_profile", "none"),
        "frontend_component": config.get("frontend_component", ""),
        "backend_filename": config.get("backend_filename", "app.py"),
        "files": files,
    }


def seed_stacks(*, using: str = DEFAULT_DB_ALIAS, log=None) -> tuple[int, int]:
    """Version-aware upsert of builtin ``Stack`` rows from the scaffolding manifest."""
    from backend.runtime.models import Stack

    if not MANIFEST_PATH.is_file():
        if log:
            log(f"  Scaffolding manifest not found: {MANIFEST_PATH}")
        return 0, 0

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    created = updated = 0

    for slug, config in manifest.get("stacks", {}).items():
        directory = SCAFFOLDING_DIR / config["directory"]
        files = _read_skeleton_files(directory)
        new_hash = content_hash(_stack_hash_payload(config, files))

        latest = Stack.objects.using(using).filter(slug=slug, is_builtin=True).order_by("-version").first()
        defaults = {
            "name": slug,
            "description": f"Builtin stack seeded from runtime/scaffolding/{config['directory']}",
            "is_builtin": True,
            "is_approved": True,
            "has_frontend": bool(config.get("has_frontend")),
            "default_port": config.get("default_port", 8000),
            "patch_profile": config.get("patch_profile", "none"),
            "frontend_component": config.get("frontend_component", ""),
            "backend_filename": config.get("backend_filename", "app.py"),
            "aliases": config.get("aliases", []),
            "files": files,
            "content_hash": new_hash,
            "dockerfile_mode": Stack.DockerfileMode.BUNDLED,
        }

        if latest is None:
            Stack.objects.using(using).create(slug=slug, version=1, **defaults)
            created += 1
            if log:
                log(f"  Created stack: {slug} v1")
        elif latest.content_hash != new_hash:
            Stack.objects.using(using).create(slug=slug, version=latest.version + 1, **defaults)
            updated += 1
            if log:
                log(f"  New stack version: {slug} v{latest.version + 1}")
        elif latest.aliases != defaults["aliases"]:
            # Cosmetic manifest change (aliases don't affect provisioning).
            latest.aliases = defaults["aliases"]
            latest.save(update_fields=["aliases", "updated_at"])
            updated += 1
            if log:
                log(f"  Updated stack aliases: {slug} v{latest.version}")
        elif log:
            log(f"  Up to date stack: {slug} v{latest.version}")

    return created, updated
