"""Seed the analyzer tool catalog and the system analysis profiles.

* **Tools** — one :class:`AnalyzerTool` row per catalog spec
  (``data/tools/*.yaml`` via ``engine.catalog``). A tool that left the
  catalog is disabled, never deleted: past results reference its slug.
* **Versions** — read from the built analyzer image's manifest when asked
  (``with_image=True``: management command, after an image build); a row
  keeps its manifest version while its spec is unchanged, otherwise it shows
  the spec's declared version until the next sync. Runs never rely on this
  column: plans and results record versions themselves.
* **System profiles** — ``data/profiles/*.yaml`` → :class:`AnalysisProfile`
  rows with ``is_system=True``. Content is immutable per version: an edited
  YAML creates version+1, identical content keeps the current version, and a
  removed YAML archives its profile.

Runs automatically after every ``migrate`` (without touching Docker) and via
``manage.py seed_analysis_tools``. Note: under pytest's ``--reuse-db`` the
post_migrate signal only fires when the test database is (re)created.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from django.db import DEFAULT_DB_ALIAS

logger = logging.getLogger(__name__)

PROFILES_DIR = Path(__file__).resolve().parent / "data" / "profiles"
RECONCILE_TASK_NAME = "analysis-reconcile"
RECONCILE_TASK = "analysis.reconcile"
RECONCILE_EVERY_SECONDS = 60


def _emitter(log: Callable[[str], None] | None) -> Callable[[str], None]:
    return log or logger.debug


def image_manifest_or_none() -> dict[str, Any] | None:
    """The built analyzer image's manifest, or None (not built, daemon down, no definition)."""
    from backend.analysis.engine import sandbox

    try:
        _tag, image_id = sandbox.ensure_image(build=False)
        return sandbox.image_manifest(image_id)
    except sandbox.SandboxError as exc:
        logger.info("Analyzer image manifest unavailable: %s", exc)
        return None


def seed_tools(
    *,
    using: str = DEFAULT_DB_ALIAS,
    log: Callable[[str], None] | None = None,
    with_image: bool = False,
) -> tuple[int, int]:
    """Upsert AnalyzerTool rows from the catalog; returns ``(created, updated)``."""
    from backend.analysis.engine import catalog
    from backend.analysis.engine import sandbox
    from backend.analysis.models import AnalyzerTool

    emit = _emitter(log)
    specs = catalog.load_specs(refresh=True)
    manifest = image_manifest_or_none() if with_image else None
    existing = {tool.slug: tool for tool in AnalyzerTool.objects.using(using).all()}
    created = updated = 0
    for spec in sorted(specs.values(), key=lambda s: (s.display_order, s.slug)):
        row = existing.get(spec.slug)
        version = sandbox.tool_version(manifest, spec.slug) if manifest else ""
        if not version and row is not None and row.spec_hash == spec.spec_hash and row.version:
            version = row.version  # synced from an image earlier; the spec did not change since
        if not version:
            version = str(spec.raw.get("version") or "")
        values = {
            "name": spec.name,
            "description": spec.description,
            "category": spec.category,
            "kind": spec.kind,
            "languages": list(spec.languages),
            "output_kind": spec.output_kind,
            "icon": spec.icon,
            "docs_url": spec.docs_url,
            "config_schema": [dict(field) for field in spec.config_schema],
            "default_config": dict(spec.default_config),
            "spec": dict(spec.raw),
            "spec_hash": spec.spec_hash,
            "version": version[:100],
            "is_enabled": True,
            "display_order": spec.display_order,
        }
        if row is None:
            AnalyzerTool.objects.using(using).create(slug=spec.slug, **values)
            created += 1
            emit(f"Created tool: {spec.slug} {version}")
            continue
        changed = [field for field, value in values.items() if getattr(row, field) != value]
        if changed:
            for field in changed:
                setattr(row, field, values[field])
            row.save(using=using, update_fields=[*changed, "updated_at"])
            updated += 1
            emit(f"Updated tool: {spec.slug} ({', '.join(changed)})")
    stale = AnalyzerTool.objects.using(using).exclude(slug__in=list(specs)).filter(is_enabled=True)
    for slug in stale.values_list("slug", flat=True):
        emit(f"Disabled tool no longer in the catalog: {slug}")
    stale.update(is_enabled=False)
    return created, updated


def sync_tool_versions(manifest: dict[str, Any] | None, *, using: str = DEFAULT_DB_ALIAS) -> int:
    """Copy versions from an analyzer image manifest onto AnalyzerTool rows; returns rows changed."""
    from backend.analysis.engine import sandbox
    from backend.analysis.models import AnalyzerTool

    if not manifest:
        return 0
    changed = 0
    for tool in AnalyzerTool.objects.using(using).all():
        version = sandbox.tool_version(manifest, tool.slug)[:100]
        if version and version != tool.version:
            tool.version = version
            tool.save(using=using, update_fields=["version", "updated_at"])
            changed += 1
    return changed


def _load_profile_files() -> list[tuple[Path, dict[str, Any]]]:
    files = []
    for path in sorted(PROFILES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            logger.warning("Ignoring %s: not a mapping", path.name)
            continue
        files.append((path, data))
    return files


def seed_profiles(*, using: str = DEFAULT_DB_ALIAS, log: Callable[[str], None] | None = None) -> dict[str, int]:
    """Sync system profiles with ``data/profiles/*.yaml``; returns counts per action."""
    from backend.analysis.engine import catalog
    from backend.analysis.engine import plan
    from backend.analysis.models import AnalysisProfile

    emit = _emitter(log)
    counts = {"created": 0, "versioned": 0, "updated": 0, "unchanged": 0, "invalid": 0, "archived": 0}
    seen: list[str] = []
    for path, data in _load_profile_files():
        slug = str(data.get("slug") or path.stem)
        seen.append(slug)
        try:
            config = plan.validate_profile_config(data.get("config") or {})
        except catalog.ConfigError as exc:
            counts["invalid"] += 1
            logger.warning("System profile %s is invalid: %s", slug, exc.field_errors)
            emit(f"Invalid system profile {slug}: {exc}")
            continue
        content_hash = plan.profile_content_hash(config)
        name = str(data.get("name") or slug)[:200]
        description = str(data.get("description") or "")
        latest = AnalysisProfile.objects.using(using).filter(slug=slug).order_by("-version").first()
        if latest is not None and not latest.is_system:
            counts["invalid"] += 1
            logger.warning("Not seeding system profile %s: a user profile already uses the slug", slug)
            continue
        if latest is None or latest.content_hash != content_hash:
            version = 1 if latest is None else latest.version + 1
            AnalysisProfile.objects.using(using).create(
                slug=slug,
                version=version,
                name=name,
                description=description,
                config=config,
                content_hash=content_hash,
                is_system=True,
            )
            counts["created" if latest is None else "versioned"] += 1
            emit(f"{'Created' if latest is None else 'New version of'} system profile {slug} v{version}")
            continue
        updates = {"name": name, "description": description, "is_archived": False}
        changed = [field for field, value in updates.items() if getattr(latest, field) != value]
        if changed:
            for field in changed:
                setattr(latest, field, updates[field])
            latest.save(using=using, update_fields=[*changed, "updated_at"])
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
    gone = AnalysisProfile.objects.using(using).filter(is_system=True, is_archived=False).exclude(slug__in=seen)
    counts["archived"] = gone.update(is_archived=True)
    return counts


def ensure_reconcile_schedule(*, using: str = DEFAULT_DB_ALIAS) -> bool:
    """Create (idempotently) the beat entry running ``analysis.reconcile`` every minute; True if created.

    An admin who disabled the entry keeps it disabled; only its task and
    interval are corrected.
    """
    from django_celery_beat.models import IntervalSchedule
    from django_celery_beat.models import PeriodicTask

    schedules = IntervalSchedule.objects.using(using)
    schedule = schedules.filter(every=RECONCILE_EVERY_SECONDS, period=IntervalSchedule.SECONDS).first()
    if schedule is None:
        schedule = schedules.create(every=RECONCILE_EVERY_SECONDS, period=IntervalSchedule.SECONDS)
    task, created = PeriodicTask.objects.using(using).get_or_create(
        name=RECONCILE_TASK_NAME,
        defaults={
            "task": RECONCILE_TASK,
            "interval": schedule,
            "description": "Recover analysis runs whose worker died; reap orphaned analysis containers.",
        },
    )
    if not created and (task.task != RECONCILE_TASK or task.interval_id != schedule.pk or task.crontab_id):
        task.task = RECONCILE_TASK
        task.interval = schedule
        task.crontab = None
        task.solar = None
        task.clocked = None
        task.save(using=using)
    return created


def seed_all(
    *,
    using: str = DEFAULT_DB_ALIAS,
    log: Callable[[str], None] | None = None,
    with_image: bool = False,
) -> dict[str, Any]:
    """Tools, system profiles and the reconcile schedule; each step independent of the others' failures."""
    report: dict[str, Any] = {}
    steps: list[tuple[str, Callable[[], Any]]] = [
        ("tools", lambda: seed_tools(using=using, log=log, with_image=with_image)),
        ("profiles", lambda: seed_profiles(using=using, log=log)),
        ("schedule", lambda: ensure_reconcile_schedule(using=using)),
    ]
    for key, step in steps:
        try:
            report[key] = step()
        except Exception as exc:
            # Seed data must never break `migrate` (malformed YAML, a stage
            # still being implemented, tables torn down by `migrate x zero`).
            logger.exception("Analysis seeding step %s failed", key)
            report[key] = {"error": f"{type(exc).__name__}: {exc}"}
    return report
