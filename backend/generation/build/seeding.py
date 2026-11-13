"""Seed WP-05's own library data: stack fragments, stack profiles and the composed starters.

``library.seeding.seed_library`` (WP-03) seeds the whole ``data/`` tree; this module seeds the part WP-05
owns (``data/prompts/{stack-*,run-contract-*}``, ``data/stacks/*.yaml``, then ``build.starters``) with the
same rules, so ``manage.py build_starters --with-stacks`` works on its own and both seeders agree:

* an equal ``content_hash`` is a no-op;
* a different one creates ``version + 1`` as a system row (``derived_from`` = the previous version);
* a slug owned by a user is never touched (reported as ``error``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

import yaml

from backend.generation.build import starters

if TYPE_CHECKING:
    from backend.generation.models import VersionedEntity

__all__ = ["PROMPTS_ROOT", "SeedRecord", "seed_stack_library", "stack_profile_entries", "stack_prompt_entries"]

PROMPTS_ROOT: Final = starters.STARTERS_ROOT.parent / "prompts"
_OWNED_PROMPT_PREFIXES: Final = ("stack-", "run-contract-")

SeedRecord = starters.SeedRecord


@dataclass(frozen=True)
class Entry:
    kind: str
    slug: str
    name: str
    description: str
    content: dict[str, Any]


def stack_prompt_entries(root: Path | None = None) -> list[Entry]:
    """The WP-05 prompt fragments (``stack-*``, ``run-contract-*``) as PromptTemplate content."""
    root = root or PROMPTS_ROOT
    out: list[Entry] = []
    for meta_path in sorted(root.glob("*.meta.yaml")):
        slug = meta_path.name.removesuffix(".meta.yaml")
        if not slug.startswith(_OWNED_PROMPT_PREFIXES):
            continue
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        template = (root / f"{slug}.jinja2").read_text(encoding="utf-8").removesuffix("\n")
        content = {
            "role": meta["role"],
            "engine": "jinja2-sandbox@1",
            "template": template,
            "variables": list(meta.get("variables") or []),
            "modes": list(meta["modes"]),
            "tags": list(meta.get("tags") or []),
        }
        out.append(Entry("prompt", slug, meta["name"], meta.get("description", ""), content))
    return out


def stack_profile_entries(root: Path | None = None) -> list[Entry]:
    """Every ``data/stacks/*.yaml`` as StackProfile content (refs unpinned: ``slug@v``)."""
    root = root or starters.STACKS_ROOT
    out: list[Entry] = []
    for path in sorted(root.glob("*.yaml")):
        data = starters.load_stack_yaml(path.stem, root)
        out.append(Entry("stack", path.stem, data["name"], data["description"], data["content"]))
    return out


def _seed_versioned(
    model: type[VersionedEntity],
    entry: Entry,
    *,
    using: str,
    created_by: Any,
    dry_run: bool,
    pending: dict[tuple[str, str, int], str],
) -> SeedRecord:
    from backend.generation.contracts.library import pin_content_refs
    from backend.generation.models import choices
    from backend.generation.models.library import _resolve_library_ref

    manager = model.objects.db_manager(using)
    latest = manager.filter(slug=entry.slug).order_by("-version").first()
    if latest is not None and not latest.is_system:
        return SeedRecord(entry.slug, "error", latest.version, "the slug belongs to a user entity")

    def resolve(kind: str, slug: str, version: int) -> str | None:
        # A dry run creates nothing, so refs to rows it would create resolve to a placeholder hash.
        return _resolve_library_ref(kind, slug, version) or pending.get((kind, slug, version))

    pinned, errors = pin_content_refs(entry.kind, entry.content, resolve)
    if errors:
        return SeedRecord(entry.slug, "error", None, f"unresolved refs {errors}")
    content_hash = model.compute_content_hash(pinned)
    same = manager.filter(slug=entry.slug, content_hash=content_hash).order_by("-version").first()
    if same is not None:
        return SeedRecord(entry.slug, "unchanged", same.version)
    version = latest.version + 1 if latest is not None else 1
    if dry_run:
        pending[(entry.kind, entry.slug, version)] = "0" * 64
        return SeedRecord(entry.slug, "created", version, "dry run")
    row = model(
        slug=entry.slug,
        version=version,
        name=entry.name,
        description=entry.description,
        content=pinned,
        is_system=True,
        visibility=choices.Visibility.SYSTEM,
        created_by=created_by,
        derived_from=latest,
    )
    row.full_clean()
    row.save(using=using)
    return SeedRecord(entry.slug, "created", version)


def seed_stack_library(
    *,
    using: str = "default",
    created_by: Any = None,
    dry_run: bool = False,
    stacks: list[str] | None = None,
    with_starters: bool = True,
) -> list[SeedRecord]:
    """Seed the stack fragments, then the stack profiles, then (optionally) the starters."""
    from backend.generation.models import PromptTemplate
    from backend.generation.models import StackProfile

    pending: dict[tuple[str, str, int], str] = {}
    options = {"using": using, "created_by": created_by, "dry_run": dry_run, "pending": pending}
    records = [_seed_versioned(PromptTemplate, entry, **options) for entry in stack_prompt_entries()]
    records += [
        _seed_versioned(StackProfile, entry, **options)
        for entry in stack_profile_entries()
        if stacks is None or entry.slug in stacks
    ]
    if with_starters:
        records += starters.seed_starters(using=using, stacks=stacks, created_by=created_by, dry_run=dry_run)
    return records
