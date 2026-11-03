"""``seed_library``: idempotent seeding of ``generation/data/`` (spec section 11.1).

Rules:

* a content hash equal to an existing version of the slug is a no-op;
* a different hash creates version+1 with ``is_system=True`` (``derived_from`` = the previous version);
* user rows are never touched, and a slug owned by a user is never made a system slug: the entry is
  skipped and reported as a conflict;
* entries are seeded in dependency order (prompts, stacks, starters, styles, guidance, prompt sets, presets,
  budget presets, eval suites, detectors, tasks), so ``slug@v`` refs inside content pin to rows seeded
  earlier in the same run. Seed files may also write ``slug@latest``, which pins the latest system version
  **at seed time** (never at run time: stored content always holds ``slug@v#hash12``);
* lint issues are reported per entity: ``lint_errors`` (seeded anyway, so one bad file does not cascade into
  every dependent; ``seed_library --strict`` fails on them) and ``warnings``. Content that does not validate
  or names a missing version is not seeded (``errors``);
* mutable columns follow the files: ``name``, ``description`` and ``archived`` (section 11.6 seeds the
  harness presets of disabled adapters archived) are synced on the latest system version.

Layout (section 11.1, read from ``settings.GENERATION_LIBRARY_DATA_DIR`` unless ``data_dir`` is given):
``tasks/<slug>/task.yaml`` + ``prose/<variant>.md``; ``prompts/<slug>.jinja2`` + ``<slug>.meta.yaml``;
``stacks/``, ``styles/`` (``styles/tones/`` is not an entity), ``guidance/``, ``prompt_sets/``,
``harness_presets/``, ``sampling_presets/``, ``resource_policies/`` and ``eval_suites/`` hold ``<slug>.yaml``
files, either ``{name, description, archived?, tier?, content: {...}}`` or the content itself with
``name``/``description`` beside it; ``detectors/**`` holds such files, or ``<slug>.meta.yaml`` plus the rule in
``<slug>.{yaml,scm,regex,txt}`` (``detectors/tests/`` is ignored); ``starters/compose.yaml`` lists the
``(stack, level)`` starters composed by ``build/starters.py`` (WP-05), and ``starters/<slug>/starter.yaml``
plus ``files/`` is a pre-composed starter (the golden fixture's layout).

The post-migrate hook calls ``seed_library(using=...)`` when ``GENERATION_SEED_ON_MIGRATE`` is on.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import Final

import yaml
from django.conf import settings
from django.db import transaction

from backend.generation import blobs
from backend.generation import identity
from backend.generation.contracts.library import LIBRARY_REF_FIELDS
from backend.generation.contracts.library import iter_content_refs
from backend.generation.library import service
from backend.generation.models import TaskSpec
from backend.generation.models.choices import PolicyTier

__all__ = [
    "SEED_ORDER",
    "SeedEntry",
    "SeedReport",
    "lint_seed_data",
    "load_seed_entries",
    "seed_library",
]

logger = logging.getLogger(__name__)

# Dependencies first (stacks name prompt fragments, starters name stacks, prompt sets name prompts, ...).
SEED_ORDER: Final = (
    "prompt",
    "stack",
    "starter",
    "style",
    "guidance",
    "prompt_set",
    "harness_preset",
    "sampling",
    "resource_policy",
    "eval_suite",
    "detector",
    "task",
)
_YAML_DIRS: Final = {
    "stack": "stacks",
    "style": "styles",
    "guidance": "guidance",
    "prompt_set": "prompt_sets",
    "harness_preset": "harness_presets",
    "sampling": "sampling_presets",
    "resource_policy": "resource_policies",
    "eval_suite": "eval_suites",
}
_WRAPPER_KEYS: Final = frozenset({"name", "description", "archived", "is_archived", "tier", "slug", "phase"})
_RULE_SUFFIXES: Final = (".yaml", ".yml", ".scm", ".regex", ".txt")
_EXECUTABLE: Final = 0o755
_REGULAR: Final = 0o644


@dataclass
class SeedEntry:
    """One entity read from the data directory (content as written: refs may be ``slug@v`` or ``@latest``)."""

    kind: str
    slug: str
    name: str
    description: str = ""
    content: dict[str, Any] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)  # pre-composed starters: path -> bytes
    archived: bool = False
    tier: str | None = None
    compose: tuple[str, str] | None = None  # (stack slug, level) for starters composed by WP-05
    phase: str = "A"  # compose.yaml phase (provenance); only phases outside build.starters.ACTIVE_PHASES defer
    source: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.slug}"


@dataclass
class SeedReport:
    created: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)  # mutable columns synced (name, description, archived)
    skipped: list[dict[str, Any]] = field(default_factory=list)  # conflicts with user slugs, missing seams
    deferred: list[dict[str, Any]] = field(default_factory=list)  # starters of a phase not in ACTIVE_PHASES
    errors: list[dict[str, Any]] = field(default_factory=list)
    lint_errors: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    warnings: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    dry_run: bool = False

    @property
    def changes(self) -> int:
        return len(self.created) + len(self.updated)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "unchanged": len(self.unchanged),
            "updated": self.updated,
            "skipped": self.skipped,
            "deferred": self.deferred,
            "errors": self.errors,
            "lint_errors": self.lint_errors,
            "warnings": self.warnings,
            "changes": self.changes,
            "dry_run": self.dry_run,
        }


# =====================================================================================
# Loading the data directory
# =====================================================================================


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _mapping(path: Path) -> dict[str, Any]:
    data = _yaml(path) or {}
    if not isinstance(data, dict):
        msg = f"{path} must hold a mapping"
        raise TypeError(msg)
    return data


def _archived(data: Mapping[str, Any]) -> bool:
    return bool(data.get("is_archived", data.get("archived", False)))


def _wrapped(kind: str, path: Path, slug: str | None = None) -> SeedEntry:
    data = _mapping(path)
    if "content" in data and isinstance(data["content"], dict):
        content = data["content"]
    else:
        content = {key: value for key, value in data.items() if key not in _WRAPPER_KEYS}
    return SeedEntry(
        kind=kind,
        slug=str(data.get("slug") or slug or path.stem),
        name=str(data.get("name") or path.stem),
        description=str(data.get("description") or ""),
        content=content,
        archived=_archived(data),
        tier=data.get("tier"),
        source=str(path),
    )


def _prompt(meta_path: Path) -> SeedEntry:
    slug = meta_path.name.removesuffix(".meta.yaml")
    meta = _mapping(meta_path)
    template = (meta_path.parent / f"{slug}.jinja2").read_text(encoding="utf-8").removesuffix("\n")
    content = {
        "role": meta["role"],
        "engine": meta.get("engine", "jinja2-sandbox@1"),
        "template": template,
        "variables": meta.get("variables", []),
        "modes": meta["modes"],
        "tags": meta.get("tags", []),
    }
    return SeedEntry(
        "prompt",
        slug,
        str(meta.get("name") or slug),
        str(meta.get("description") or ""),
        content,
        archived=_archived(meta),
        source=str(meta_path),
    )


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def _precomposed_starter(directory: Path) -> SeedEntry:
    data = _mapping(directory / "starter.yaml")
    files = _tree(directory / "files")
    modes = data.pop("modes", {}) or {}
    content = {key: value for key, value in data.items() if key not in _WRAPPER_KEYS} | {
        "files": {
            path: {"blob": identity.bytes_sha(blob), "mode": _EXECUTABLE if modes.get(path) == "x" else _REGULAR}
            for path, blob in files.items()
        },
    }
    return SeedEntry(
        "starter",
        str(data.get("slug") or directory.name),
        str(data.get("name") or directory.name),
        str(data.get("description") or ""),
        content,
        files=files,
        archived=_archived(data),
        source=str(directory),
    )


def _composed_starters(path: Path) -> list[SeedEntry]:
    data = _yaml(path) or []
    items = data.get("starters", []) if isinstance(data, Mapping) else data
    entries = []
    for item in items or []:
        if not isinstance(item, Mapping) or not item.get("stack"):
            continue
        stack = str(item["stack"])
        levels = item.get("levels") or ([item["level"]] if item.get("level") else [])
        for level in levels:
            slug = str(item.get("slug") or f"{stack}-{level}") if len(levels) == 1 else f"{stack}-{level}"
            entries.append(
                SeedEntry(
                    "starter",
                    slug,
                    str(item.get("name") or slug),
                    str(item.get("description") or ""),
                    compose=(stack, str(level)),
                    archived=_archived(item),
                    phase=str(item.get("phase") or "A"),
                    source=str(path),
                ),
            )
    return entries


def _detectors(root: Path) -> list[SeedEntry]:
    entries = []
    metas = {path for path in root.rglob("*.meta.yaml") if "tests" not in path.relative_to(root).parts}
    rule_files = set()
    for meta_path in sorted(metas):
        slug = meta_path.name.removesuffix(".meta.yaml")
        entry = _wrapped("detector", meta_path, slug)
        for suffix in _RULE_SUFFIXES:
            rule_path = meta_path.parent / f"{slug}{suffix}"
            if rule_path.is_file():
                entry.content["rule"] = rule_path.read_text(encoding="utf-8")
                rule_files.add(rule_path)
                break
        entries.append(entry)
    for path in sorted(root.rglob("*.y*ml")):
        if path in metas or path in rule_files or "tests" in path.relative_to(root).parts:
            continue
        entries.append(_wrapped("detector", path))
    return entries


def _task(directory: Path) -> SeedEntry:
    content = _mapping(directory / "task.yaml")
    name = content.pop("name", None)
    content.pop("description", None)
    archived = bool(content.pop("is_archived", content.pop("archived", False)))
    prose_dir = directory / "prose"
    content["prose"] = {
        path.stem: path.read_text(encoding="utf-8").removesuffix("\n") for path in sorted(prose_dir.glob("*.md"))
    }
    return SeedEntry(
        "task",
        directory.name,
        str(name or content.get("title") or directory.name),
        "",
        content,
        archived=archived,
        source=str(directory),
    )


def load_seed_entries(data_dir: Path | None = None, *, kinds: Iterable[str] | None = None) -> list[SeedEntry]:
    """Every entity in ``data_dir`` (default ``settings.GENERATION_LIBRARY_DATA_DIR``), in ``SEED_ORDER``."""
    root = Path(data_dir or settings.GENERATION_LIBRARY_DATA_DIR)
    wanted = set(kinds or SEED_ORDER)
    entries: list[SeedEntry] = []
    if "prompt" in wanted and (root / "prompts").is_dir():
        entries += [_prompt(path) for path in sorted((root / "prompts").rglob("*.meta.yaml"))]
    for kind, dirname in _YAML_DIRS.items():
        if kind in wanted and (root / dirname).is_dir():
            entries += [_wrapped(kind, path) for path in sorted((root / dirname).glob("*.yaml"))]
    starters = root / "starters"
    if "starter" in wanted and starters.is_dir():
        if (starters / "compose.yaml").is_file():
            entries += _composed_starters(starters / "compose.yaml")
        entries += [
            _precomposed_starter(path)
            for path in sorted(starters.iterdir())
            if path.is_dir() and (path / "starter.yaml").is_file()
        ]
    if "detector" in wanted and (root / "detectors").is_dir():
        entries += _detectors(root / "detectors")
    if "task" in wanted and (root / "tasks").is_dir():
        entries += [
            _task(path)
            for path in sorted((root / "tasks").iterdir())
            if path.is_dir() and (path / "task.yaml").is_file()
        ]
    entries.sort(key=lambda entry: (SEED_ORDER.index(entry.kind), entry.slug))
    return entries


# =====================================================================================
# Seeding
# =====================================================================================


def _resolve_latest_refs(entry: SeedEntry) -> dict[str, Any]:
    """``entry.content`` with ``slug@latest`` refs replaced by the latest system ``slug@v``."""
    content = copy.deepcopy(entry.content)
    for path, target in LIBRARY_REF_FIELDS.get(entry.kind, ()):
        head, _, tail = path.partition(".")
        if tail == "*":
            mapping = content.get(head)
            slots = list(mapping) if isinstance(mapping, dict) else []
            containers = [(mapping, key) for key in slots]
        else:
            containers = [(content, head)] if head in content else []
        for container, key in containers:
            value = container[key]
            if isinstance(value, str) and value.endswith("@latest"):
                slug = value.removesuffix("@latest")
                row = service.slug_rows(target, slug).filter(is_system=True).first()
                if row is None:
                    msg = f"{key}: no system {target} {slug!r} to pin @latest to"
                    raise service.LibraryError(msg, status=422, code="invalid")
                container[key] = row.ref
    return content


def _issues_payload(issues: Iterable[Any]) -> list[dict[str, Any]]:
    return [issue.model_dump(mode="json") for issue in issues]


def _sync_mutable(row: Any, entry: SeedEntry, report: SeedReport) -> None:
    changed = []
    header = row.task if entry.kind == "task" else row
    if row.name != entry.name:
        row.name = entry.name
        changed.append("name")
    if entry.kind != "task" and row.description != entry.description:
        row.description = entry.description
        changed.append("description")
    if changed:
        row.save(update_fields=changed)
    if header.is_archived != entry.archived:
        header.is_archived = entry.archived
        header.save(update_fields=["is_archived"])
        changed.append("is_archived")
    if changed:
        report.updated.append(f"{entry.key}@{row.version}")


def _composed_content(entry: SeedEntry, stack: Any = None) -> dict[str, Any] | None:
    """The WP-05 composition of a ``starters/compose.yaml`` entry (``None`` when the seam is not ready).

    ``stack`` defaults to the latest stored profile; ``lint_seed_data`` passes an in-memory stand-in
    (``slug``, ``name``, ``content``, ``pinned_ref``).
    """
    from backend.generation.build import starters

    stack_slug, level = entry.compose or ("", "")
    result = starters.compose(stack or service.latest("stack", stack_slug), level)
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return dict(result) if isinstance(result, Mapping) else None


def _phase_active(entry: SeedEntry) -> bool:
    """True when the entry's compose.yaml phase is built (``build.starters.ACTIVE_PHASES``, WP-05)."""
    from backend.generation.build.starters import ACTIVE_PHASES

    return entry.phase in ACTIVE_PHASES


def _inactive_reason(entry: SeedEntry) -> str:
    return f"phase {entry.phase} starter (not in build.starters.ACTIVE_PHASES)"


def _seed_one(entry: SeedEntry, report: SeedReport) -> None:
    rows = list(service.slug_rows(entry.kind, entry.slug))
    header_owned = entry.kind == "task" and TaskSpec.objects.filter(slug=entry.slug, is_system=False).exists()
    if header_owned or any(not service.is_system_row(row) for row in rows):
        report.skipped.append({"key": entry.key, "reason": "the slug belongs to a user; the system entry is skipped"})
        return
    if entry.compose is not None:
        stack_slug = entry.compose[0]
        if not service.slug_rows("stack", stack_slug).filter(is_archived=False).exists():
            if not _phase_active(entry):
                report.deferred.append({"key": entry.key, "reason": _inactive_reason(entry)})
            else:
                reason = f"starter not composed: stack profile {stack_slug!r} is not seeded"
                report.skipped.append({"key": entry.key, "reason": reason})
            return
        try:
            composed = _composed_content(entry)
        except NotImplementedError as exc:
            report.skipped.append({"key": entry.key, "reason": f"starter composition unavailable: {exc}"})
            return
        if composed is None:
            report.skipped.append({"key": entry.key, "reason": "build.starters.compose returned no content"})
            return
        entry.content = composed
    for data in entry.files.values():
        blobs.put_bytes(data)
    content = service.pin_refs(entry.kind, _resolve_latest_refs(entry))
    for location, target, ref in iter_content_refs(entry.kind, content):
        referenced = service.resolve_ref(ref, kind=target)
        if not service.is_system_row(referenced):
            msg = f"{location}: system content cannot reference the user entity {target} {ref}"
            raise service.LibraryError(msg, status=422, code="invalid")
    content_hash = service.content_hash_of(entry.kind, content)
    existing = next((row for row in rows if row.content_hash == content_hash), None)
    if existing is not None:
        report.unchanged.append(existing.pinned_ref)
        if existing.pk == rows[0].pk:
            _sync_mutable(existing, entry, report)
        return
    extra: dict[str, Any] = {}
    if entry.kind == "resource_policy":
        tier = entry.tier or (entry.slug if entry.slug in PolicyTier.values else PolicyTier.CUSTOM)
        extra["tier"] = tier
    if rows:
        row = service.new_version(
            rows[0],
            base_version=rows[0].version,
            content=content,
            name=entry.name,
            description=entry.description,
            user=None,
            system=True,
            extra=extra or None,
            allow_lint_errors=True,
        )
    else:
        row = service.create(
            entry.kind,
            slug=entry.slug,
            name=entry.name,
            content=content,
            description=entry.description,
            user=None,
            system=True,
            extra=extra or None,
            allow_lint_errors=True,
        )
    report.created.append(row.pinned_ref)
    if entry.archived:
        service.archive(row, archived=True, all_versions=False)
    issues = service.lint_entity(entry.kind, row.content, system=True)
    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warn"]
    if errors:
        report.lint_errors[f"{entry.kind}:{row.ref}"] = _issues_payload(errors)
    if warnings:
        report.warnings[f"{entry.kind}:{row.ref}"] = _issues_payload(warnings)


def seed_library(
    *,
    using: str = "default",
    data_dir: Path | str | None = None,
    kinds: Iterable[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Seed every library kind from ``data_dir``: an equal content hash is a no-op, a new one creates
    version+1; user rows are kept. Returns the report (``changes`` is 0 on a repeated run).

    ``dry_run`` rolls the database back at the end (blobs of pre-composed starters are still written: the
    store is content-addressed and unreferenced blobs are collected by ``gc_blobs``).
    """
    report = SeedReport(dry_run=dry_run)
    entries = load_seed_entries(Path(data_dir) if data_dir else None, kinds=kinds)

    class _Rollback(Exception):  # noqa: N818 - control flow only
        pass

    try:
        with transaction.atomic(using=using):
            for entry in entries:
                try:
                    with transaction.atomic(using=using):
                        _seed_one(entry, report)
                except service.LibraryError as exc:
                    report.errors.append(
                        {
                            "key": entry.key,
                            "detail": exc.message,
                            "code": exc.code,
                            "issues": _issues_payload(exc.issues),
                        },
                    )
                except (KeyError, TypeError, ValueError, OSError) as exc:
                    report.errors.append(
                        {"key": entry.key, "detail": f"{type(exc).__name__}: {exc}", "code": "invalid"},
                    )
            if dry_run:
                raise _Rollback
    except _Rollback:
        pass
    logger.info(
        "seed_library: %d created, %d updated, %d unchanged, %d skipped, %d errors, %d with lint errors%s",
        len(report.created),
        len(report.updated),
        len(report.unchanged),
        len(report.skipped),
        len(report.errors),
        len(report.lint_errors),
        " (dry run)" if dry_run else "",
    )
    return report.as_dict()


# =====================================================================================
# Linting the data directory without touching the database (``lint_library``)
# =====================================================================================


def lint_seed_data(
    data_dir: Path | str | None = None,
    *,
    kinds: Iterable[str] | None = None,
    deferred: list[dict[str, Any]] | None = None,
) -> dict[str, list[Any]]:
    """``{"<kind>:<slug>": [Issue]}`` for every entry of ``data_dir``, linted as system entities.

    Starters of an active phase (``build.starters.ACTIVE_PHASES``: A and B) that cannot be composed (stack
    missing, composition failing) get an ``error`` issue, never a silent skip; starters of any other phase are
    not linted and, when ``deferred`` is given, appended to it (the ``seed_library`` report lists the same
    entries as deferred).

    Refs resolve to the stored version when it exists, else to the entry of the same slug in ``data_dir``
    (whatever version the file names: the seeder assigns versions), so the directory lints on an empty
    database. Nothing is written except the blobs of composed starters (``build/starters.py``).
    """
    from pydantic import ValidationError

    from backend.generation.contracts.library import normalize_content
    from backend.generation.contracts.library import pin_content_refs
    from backend.generation.library import lint as library_lint

    entries = load_seed_entries(Path(data_dir) if data_dir else None, kinds=None)
    wanted = set(kinds or SEED_ORDER)
    memory: dict[tuple[str, str], dict[str, Any]] = {}  # (kind, slug) -> pinned, normalized content
    hashes: dict[tuple[str, str], str] = {}
    file_blobs: dict[str, bytes] = {}
    results: dict[str, list[Any]] = {}

    def stored(kind: str, slug: str, version: int) -> Any:
        return service.slug_rows(kind, slug).filter(version=version).first()

    def resolve_hash(kind: str, slug: str, version: int) -> str | None:
        row = stored(kind, slug, version)
        return row.content_hash if row is not None else hashes.get((kind, slug))

    def resolve_content(kind: str, ref: str) -> Mapping[str, Any] | None:
        slug, _, rest = ref.partition("@")
        version = int(rest.split("#", 1)[0]) if rest.split("#", 1)[0].isdigit() else 0
        row = stored(kind, slug, version) if version else None
        if row is not None:
            return row.content
        return memory.get((kind, slug))

    for entry in entries:
        file_blobs.update({identity.bytes_sha(data): data for data in entry.files.values()})
        content = copy.deepcopy(entry.content)
        if entry.compose is not None:
            stack_slug = entry.compose[0]
            stack = None
            if (("stack", stack_slug)) in memory:
                stack = SimpleNamespace(
                    slug=stack_slug,
                    name=stack_slug,
                    content=memory[("stack", stack_slug)],
                    pinned_ref=f"{stack_slug}@1#{hashes[('stack', stack_slug)][:12]}",
                )
            if stack is None and not service.slug_rows("stack", stack_slug).exists():
                if not _phase_active(entry):
                    # A phase outside ACTIVE_PHASES: listed as deferred, as by seed_library, and not counted
                    # among the linted entries.
                    if deferred is not None and entry.kind in wanted:
                        deferred.append({"key": entry.key, "reason": _inactive_reason(entry)})
                elif entry.kind in wanted:
                    results[entry.key] = [
                        library_lint.make_issue(
                            "lint_unknown_library_ref",
                            message=f"compose: stack profile {stack_slug!r} is not in the data directory nor stored",
                            path="compose.stack",
                        ),
                    ]
                continue
            try:
                composed = _composed_content(entry, stack)
            except (NotImplementedError, service.LibraryError) as exc:
                composed, failure = None, f"{type(exc).__name__}: {exc}"
            else:
                failure = None if composed else "build.starters.compose returned no content"
            if failure is not None:
                if not _phase_active(entry):
                    if deferred is not None and entry.kind in wanted:
                        deferred.append({"key": entry.key, "reason": f"phase {entry.phase} starter: {failure}"})
                elif entry.kind in wanted:
                    results[entry.key] = [
                        library_lint.make_issue(
                            "lint_starter_file_missing",
                            message=f"starter could not be composed from {stack_slug!r}: {failure}",
                            path="files",
                        ),
                    ]
                continue
            content = composed
        for path, _target in LIBRARY_REF_FIELDS.get(entry.kind, ()):
            head, _, tail = path.partition(".")
            slots = (
                [(content.get(head), key) for key in list(content.get(head) or {})]
                if tail == "*"
                else [(content, head)]
                if head in content
                else []
            )
            for container, key in slots:
                value = container[key]
                if isinstance(value, str) and value.endswith("@latest"):
                    container[key] = value.removesuffix("@latest") + "@1"
        pinned, errors = pin_content_refs(entry.kind, content, resolve_hash)
        issues: list[Any] = [
            library_lint.make_issue("lint_unknown_library_ref", message=f"{loc}: {msg}", path=loc)
            for loc, messages in errors.items()
            for msg in messages
        ]
        try:
            normalized = normalize_content(entry.kind, pinned)
        except ValidationError:
            normalized = pinned
        else:
            memory[(entry.kind, entry.slug)] = normalized
            hashes[(entry.kind, entry.slug)] = identity.library_content_hash(
                entry.kind,
                library_lint.SCHEMA_VERSIONS[entry.kind],
                normalized,
            )
        if entry.kind in wanted:
            issues += library_lint.lint(
                entry.kind,
                normalized,
                system=True,
                resolve=resolve_content,
                read_blob=lambda sha: file_blobs.get(sha) or library_lint.read_store_blob(sha),
            )
            results[entry.key] = issues
    return results
