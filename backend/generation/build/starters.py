"""Starter composition: backend fragment + frontend fragment + Dockerfile template (spec section 11.4).

``compose(stack_profile, level)`` builds the ``StarterContent`` of one profile and level from
``data/starters/``:

* the backend fragment ``backend/<framework>/<level>/`` (``backend/free/bare/`` for the free-choice profile);
* the frontend fragment ``frontend/<framework>/<level>/`` when the profile has a frontend and that level ships
  one (``bare`` ships no frontend code);
* the checked-in lockfiles of those fragments (``lock/``, see ``build.locks``);
* the rendered Dockerfile template ``dockerfiles/<backend framework>.Dockerfile.jinja2`` with the
  digest-pinned bases of ``base_images.yaml`` (free-choice starters have no Dockerfile: the model writes it).

Files ending in ``.jinja2`` are rendered (suffix dropped) with the profile's run contract and ``has_frontend``;
a template that renders to nothing is left out, so one fragment serves profiles with and without a frontend.
Every other file is copied byte for byte. Nothing depends on time or on the machine, so equal inputs give an
equal ``tree_hash`` and ``content_hash``. The rule list of ``build.starter_rules`` is applied to the result
(``forbidden_content_check``), and ``seed_starters`` refuses a starter that fails it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

import yaml
from jinja2 import FileSystemLoader
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from backend.generation import identity
from backend.generation.build import dockerfiles
from backend.generation.build import locks
from backend.generation.build import starter_rules

if TYPE_CHECKING:
    from backend.generation.contracts.library import StarterContent
    from backend.generation.models import StackProfile
    from backend.generation.models import Starter

__all__ = [
    "COMPOSE_PATH",
    "LEVELS",
    "STACKS_ROOT",
    "ComposeEntry",
    "ComposedStarter",
    "SeedRecord",
    "StarterCompositionError",
    "compose",
    "compose_plan",
    "compose_tree",
    "find_starter",
    "fragments_for",
    "load_stack_yaml",
    "seed_starters",
    "starter_slug",
]

logger = logging.getLogger(__name__)

STARTERS_ROOT: Final = dockerfiles.STARTERS_ROOT
STACKS_ROOT: Final = STARTERS_ROOT.parent / "stacks"
COMPOSE_PATH: Final = STARTERS_ROOT / "compose.yaml"
LEVELS: Final = ("bare", "minimal", "full")
_REGULAR: Final = 0o644
_EXECUTABLE: Final = 0o755
_TEMPLATE_SUFFIX: Final = ".jinja2"
_FRONTEND_LABELS: Final = {"react": "React", "vue": "Vue", "svelte": "Svelte"}


class StarterCompositionError(ValueError):
    """A (profile, level) cannot be composed: missing fragment or template, stale lock, bad profile."""


# =====================================================================================
# compose.yaml and stacks/
# =====================================================================================


@dataclass(frozen=True)
class ComposeEntry:
    stack: str
    level: str
    phase: str = "A"


def compose_plan(path: Path | None = None) -> list[ComposeEntry]:
    """Every (stack, level) of ``compose.yaml`` (phase B rows included, marked)."""
    data = yaml.safe_load((path or COMPOSE_PATH).read_text(encoding="utf-8")) or {}
    out: list[ComposeEntry] = []
    for row in data.get("starters") or []:
        for level in row.get("levels") or []:
            if level not in LEVELS:
                msg = f"compose.yaml: {row['stack']} has unknown level {level!r}"
                raise StarterCompositionError(msg)
            out.append(ComposeEntry(str(row["stack"]), str(level), str(row.get("phase", "A"))))
    return out


def load_stack_yaml(slug: str, root: Path | None = None) -> dict[str, Any]:
    """``{"name", "description", "content"}`` of ``data/stacks/<slug>.yaml``."""
    path = (root or STACKS_ROOT) / f"{slug}.yaml"
    if not path.is_file():
        msg = f"no stack profile file {path.name}"
        raise StarterCompositionError(msg)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {"name": data["name"], "description": data.get("description", ""), "content": data["content"]}


def starter_slug(stack_slug: str, level: str) -> str:
    return f"{stack_slug}-{level}"


# =====================================================================================
# Composition
# =====================================================================================


@dataclass
class ComposedStarter:
    """A composed starter before it is stored: content (with ``files`` as blob refs) plus the file bytes."""

    slug: str
    name: str
    description: str
    stack_slug: str
    level: str
    content: dict[str, Any]
    files: dict[str, bytes] = field(default_factory=dict)
    issues: list[Any] = field(default_factory=list)

    @property
    def tree_hash(self) -> str:
        return identity.tree_hash(
            (path, int(entry["mode"]), str(entry["blob"])) for path, entry in self.content["files"].items()
        )

    @property
    def passed(self) -> bool:
        return bool(self.content["forbidden_content_check"]["passed"])


def _environment() -> SandboxedEnvironment:
    # Platform-authored templates (Dockerfiles, READMEs, starter code). Output is source code, not HTML, so
    # nothing is escaped; the sandbox and StrictUndefined keep a typo in a template from passing silently.
    return SandboxedEnvironment(
        loader=FileSystemLoader(str(STARTERS_ROOT)),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def _render(env: SandboxedEnvironment, template: str, context: Mapping[str, Any]) -> str:
    text = env.get_template(template).render(**context)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip("\n") + "\n" if text.strip() else ""


def fragments_for(stack_content: Mapping[str, Any], level: str) -> tuple[str, str | None]:
    """``(backend fragment, frontend fragment | None)`` directories (relative to ``data/starters``)."""
    if stack_content.get("choice_mode") == "free":
        return "backend/free/bare", None
    backend = f"backend/{stack_content['backend']['framework']}/{level}"
    frontend_framework = stack_content["frontend"]["framework"]
    frontend = None
    if frontend_framework not in {"none", "free"}:
        candidate = f"frontend/{frontend_framework}/{level}"
        if (STARTERS_ROOT / candidate).is_dir():
            frontend = candidate
        elif level != "bare":
            msg = f"no frontend fragment {candidate}"
            raise StarterCompositionError(msg)
    return backend, frontend


def _fragment_files(
    env: SandboxedEnvironment,
    fragment: str,
    context: Mapping[str, Any],
) -> dict[str, tuple[bytes, int]]:
    directory = STARTERS_ROOT / fragment
    if not directory.is_dir():
        msg = f"no starter fragment {fragment}"
        raise StarterCompositionError(msg)
    out: dict[str, tuple[bytes, int]] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = path.relative_to(directory).as_posix()
        mode = _EXECUTABLE if path.stat().st_mode & 0o111 else _REGULAR
        if rel.endswith(_TEMPLATE_SUFFIX):
            text = _render(env, f"{fragment}/{rel}", context)
            if text:
                out[rel.removesuffix(_TEMPLATE_SUFFIX)] = (text.encode("utf-8"), mode)
        else:
            out[rel] = (path.read_bytes(), mode)
    return out


def _context(stack_slug: str, content: Mapping[str, Any], level: str) -> dict[str, Any]:
    frontend = content["frontend"]["framework"]
    has_frontend = frontend not in {"none", "free"}
    return {
        "stack": {
            "slug": stack_slug,
            "backend": dict(content["backend"]),
            "frontend": dict(content["frontend"]),
            "database": content["database"],
        },
        "run": dict(content["run_contract"]),
        "level": level,
        "has_frontend": has_frontend,
        "frontend_label": f"{_FRONTEND_LABELS.get(frontend, frontend)} + TypeScript" if has_frontend else "",
        "base": {key: spec.pinned for key, spec in dockerfiles.base_images().items()},
    }


def _install(tree: Mapping[str, Any], level: str) -> list[str]:
    commands: list[str] = []
    for path in sorted(tree):
        name = path.rsplit("/", 1)[-1]
        directory = path.rsplit("/", 1)[0] if "/" in path else "."
        if name == "requirements.txt":
            lock = f"{directory}/requirements.lock"
            commands.append(f"pip install -r {path} -c {lock}" if lock in tree else f"pip install -r {path}")
        elif name == "package.json":
            lock = f"{directory}/package-lock.json"
            commands.append(f"npm ci (in {directory}/)" if lock in tree else f"npm install (in {directory}/)")
    if level == "bare" and "Dockerfile" in tree:
        commands.append("the Dockerfile installs backend/ and frontend/ manifests when present")
    return commands


def compose_tree(
    stack_slug: str,
    stack_content: Mapping[str, Any],
    level: str,
    *,
    stack_ref: str,
    stack_name: str | None = None,
) -> ComposedStarter:
    """Compose one starter in memory (no blob or database writes).

    ``stack_content`` is a ``StackProfileContent`` dump; ``stack_ref`` is written into the content (the
    profile's pinned ref when the starter is stored).
    """
    if level not in LEVELS:
        msg = f"unknown starter level {level!r}"
        raise StarterCompositionError(msg)
    free = stack_content.get("choice_mode") == "free"
    if free and level != "bare":
        msg = "the free-choice profile has a bare starter only"
        raise StarterCompositionError(msg)
    env = _environment()
    context = _context(stack_slug, stack_content, level)
    backend_fragment, frontend_fragment = fragments_for(stack_content, level)
    tree: dict[str, tuple[bytes, int]] = {}
    lockfiles: list[str] = []
    for fragment in (backend_fragment, frontend_fragment):
        if fragment is None:
            continue
        for path, value in _fragment_files(env, fragment, context).items():
            if path in tree:
                msg = f"{fragment} and another fragment both write {path}"
                raise StarterCompositionError(msg)
            tree[path] = value
        if level != "bare":
            for path, data in locks.locks_for_fragment(fragment).items():
                tree[path] = (data, _REGULAR)
                lockfiles.append(path)
    base_images: list[dict[str, str]] = []
    template = "none"
    if not free:
        framework = stack_content["backend"]["framework"]
        template = f"{framework}.Dockerfile.jinja2"
        if not (STARTERS_ROOT / "dockerfiles" / template).is_file():
            msg = f"no Dockerfile template dockerfiles/{template}"
            raise StarterCompositionError(msg)
        dockerfile = _render(env, f"dockerfiles/{template}", context)
        if "Dockerfile" in tree:
            msg = f"{backend_fragment} must not ship its own Dockerfile"
            raise StarterCompositionError(msg)
        tree["Dockerfile"] = (dockerfile.encode("utf-8"), _REGULAR)
        base_images = dockerfiles.pinned_bases(dockerfile)
    files = {path: data for path, (data, _) in sorted(tree.items())}
    content: dict[str, Any] = {
        "stack_profile": stack_ref,
        "level": level,
        "files": {
            path: {"blob": identity.bytes_sha(data), "mode": mode} for path, (data, mode) in sorted(tree.items())
        },
        "dockerfile": "Dockerfile",
        "base_images": base_images,
        "lockfiles": sorted(lockfiles),
        "install": _install(tree, level),
        "composition": {
            "backend_fragment": backend_fragment,
            "frontend_fragment": frontend_fragment,
            "dockerfile_template": template,
        },
        "forbidden_content_check": {"passed": True, "linter_version": starter_rules.RULES_VERSION},
    }
    issues = starter_rules.starter_issues(content, files, stack=stack_content)
    content["forbidden_content_check"]["passed"] = not issues
    label = {"bare": "bare", "minimal": "minimal", "full": "full"}[level]
    return ComposedStarter(
        slug=starter_slug(stack_slug, level),
        name=f"{stack_name or stack_slug}, {label}",
        description=_description(stack_content, level),
        stack_slug=stack_slug,
        level=level,
        content=content,
        files=files,
        issues=issues,
    )


def _description(content: Mapping[str, Any], level: str) -> str:
    if content.get("choice_mode") == "free":
        return "README with the run contract only; the model writes the Dockerfile from the allowed bases."
    if level == "bare":
        return "Dockerfile (installs manifests when present) and README with the run contract; no code."
    return "Framework installed and locked, GET /api/health, the frontend shell (or a placeholder page)."


def compose(stack_profile: StackProfile, level: str) -> StarterContent:
    """The composed, digest-pinned starter of one profile and level; its file blobs are stored.

    The content names ``stack_profile.pinned_ref``. Nothing is written to the database: ``seed_starters``
    creates the ``Starter`` version.
    """
    from backend.generation import blobs
    from backend.generation.contracts.library import StarterContent

    composed = compose_tree(
        stack_profile.slug,
        stack_profile.content,
        level,
        stack_ref=stack_profile.pinned_ref,
        stack_name=stack_profile.name,
    )
    for data in composed.files.values():
        blobs.put_bytes(data)
    return StarterContent.model_validate(composed.content)


# =====================================================================================
# Seeding and lookup
# =====================================================================================


@dataclass
class SeedRecord:
    slug: str
    status: str  # created | unchanged | skipped | error
    version: int | None = None
    reason: str = ""


def _latest_profile(slug: str, using: str) -> StackProfile | None:
    from backend.generation.models import StackProfile

    return StackProfile.objects.db_manager(using).filter(slug=slug, is_archived=False).order_by("-version").first()


def seed_starters(
    *,
    using: str = "default",
    stacks: Iterable[str] | None = None,
    created_by: Any = None,
    dry_run: bool = False,
) -> list[SeedRecord]:
    """Compose every phase-A entry of ``compose.yaml`` against the latest profile rows and store new versions.

    Idempotent: an equal ``content_hash`` is a no-op, a different one creates ``version + 1`` (system row).
    A slug owned by a user (``is_system=False``) is never touched (``error``). A profile that is not seeded
    yet, and every phase-B entry, is reported as ``skipped``. A starter failing the rule list is ``error``.
    """
    from django.db import transaction

    from backend.generation import blobs
    from backend.generation.models import Starter
    from backend.generation.models import choices

    wanted = set(stacks) if stacks is not None else None
    records: list[SeedRecord] = []
    for entry in compose_plan():
        slug = starter_slug(entry.stack, entry.level)
        if wanted is not None and entry.stack not in wanted:
            continue
        if entry.phase != "A":
            records.append(SeedRecord(slug, "skipped", reason=f"phase {entry.phase} (wave 3)"))
            continue
        profile = _latest_profile(entry.stack, using)
        if profile is None:
            records.append(SeedRecord(slug, "skipped", reason=f"stack profile {entry.stack} is not seeded"))
            continue
        try:
            composed = compose_tree(
                profile.slug,
                profile.content,
                entry.level,
                stack_ref=profile.pinned_ref,
                stack_name=profile.name,
            )
        except (StarterCompositionError, locks.LockError, ValueError) as exc:
            records.append(SeedRecord(slug, "error", reason=str(exc)))
            continue
        if not composed.passed:
            reason = "; ".join(issue.message for issue in composed.issues)
            records.append(SeedRecord(slug, "error", reason=f"starter rules failed: {reason}"))
            continue
        manager = Starter.objects.db_manager(using)
        latest = manager.filter(slug=slug).order_by("-version").first()
        if latest is not None and not latest.is_system:
            records.append(SeedRecord(slug, "error", latest.version, "the slug belongs to a user starter"))
            continue
        content_hash = Starter.compute_content_hash(composed.content)
        same = manager.filter(slug=slug, content_hash=content_hash).order_by("-version").first()
        if same is not None:
            records.append(SeedRecord(slug, "unchanged", same.version))
            continue
        version = (latest.version + 1) if latest is not None else 1
        if dry_run:
            records.append(SeedRecord(slug, "created", version, "dry run"))
            continue
        for data in composed.files.values():
            blobs.put_bytes(data)
        with transaction.atomic(using=using):
            row = Starter(
                slug=slug,
                version=version,
                name=composed.name,
                description=composed.description,
                content=composed.content,
                stack_profile=profile,
                is_system=True,
                visibility=choices.Visibility.SYSTEM,
                created_by=created_by,
                derived_from=latest,
            )
            row.full_clean()
            row.save(using=using)
        records.append(SeedRecord(slug, "created", version))
    return records


def find_starter(stack_profile: StackProfile, level: str) -> Starter | None:
    """The latest non-archived starter composed for exactly this profile version and level, else ``None``.

    The plan resolver reports ``None`` as the issue ``starter_missing`` (error).
    """
    from backend.generation.models import Starter

    return (
        Starter.objects.filter(stack_profile=stack_profile, level=level, is_archived=False)
        .order_by("-version")
        .first()
    )
