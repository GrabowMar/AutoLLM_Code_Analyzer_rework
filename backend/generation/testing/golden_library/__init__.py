"""A minimal, frozen library fixture (spec section 12.2) and a parent app tree.

Contents (every entity is version 1; the files write references inside content as ``slug@1`` and the loader
pins them to ``slug@1#<hash12>``, the stored form the contracts require):

* tasks ``crud_todo_list`` and ``notes_markdown_render`` (``tasks/<slug>/task.yaml`` plus ``prose/*.md``);
* stacks ``flask-react`` and ``fastapi-vue``; starters ``flask-react-minimal``, ``fastapi-vue-minimal`` (so a
  2 tasks x 2 stacks design at ``starter_level: minimal`` resolves, section 13.3) and ``fastapi-vue-bare``
  (``starters/<slug>/starter.yaml`` plus the ``files/`` tree);
* styles ``model-choice`` and ``tailwind``; guidance ``none`` and ``generic``;
* prompt templates (``prompts/<slug>.jinja2`` plus ``.meta.yaml``), including the fragments
  ``auth-contract-<mode>``, ``network-<policy>`` and ``budget-neutral`` that ``app-default`` pins in its
  ``fragments``, and the prompt sets ``app-default``, ``slot-single-shot-default`` and ``slot-agent-default``;
* presets: ``opencode-native`` / ``opencode-controlled``, sampling ``provider-default`` / ``t07``, the budget
  preset ``frugal``, the eval suite ``eval-v2`` and the regex detector ``py-sql-fstring``;
* ``parent_app/``: a small Flask + React todo app (``parent_app.yaml`` describes it) grown from the
  ``flask-react-minimal`` starter, with endpoint handlers and components for slot tests.

The layout mirrors ``backend/generation/data/`` (section 11.1) in miniature. ``hashes.json`` freezes the content
hash of every entry and the parent tree hash; ``test_testing_golden`` fails when either drifts (regenerate with
``write_golden_hashes()`` only for an intended change, and say so in the commit).

``load_golden_library()`` reads the files (no database). ``install_golden_library()`` creates the rows as
system entities and stores the starter and parent blobs; ``install_parent_run()`` adds the parent as a usable
``app_agentic`` run with its ``RunFile`` rows.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from pathlib import Path
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

import yaml

from backend.generation import identity
from backend.generation.contracts.library import SCHEMA_VERSIONS
from backend.generation.contracts.library import normalize_content
from backend.generation.contracts.library import pin_content_refs

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import VersionedEntity
    from backend.users.models import User

__all__ = [
    "GOLDEN_ROOT",
    "HASHES_PATH",
    "INSTALL_ORDER",
    "GoldenEntry",
    "GoldenLibrary",
    "GoldenParent",
    "InstalledGoldenLibrary",
    "golden_hashes",
    "install_golden_library",
    "install_parent_run",
    "load_golden_library",
    "parent_app_files",
    "write_golden_hashes",
]

GOLDEN_ROOT: Final = Path(__file__).resolve().parent
HASHES_PATH: Final = GOLDEN_ROOT / "hashes.json"
# Dependencies first (stacks name prompt fragments, starters name stacks, ...).
INSTALL_ORDER: Final = (
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
_LANGUAGES: Final = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "vue",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".md": "markdown",
    ".txt": "text",
}
_EXECUTABLE: Final = 0o755
_REGULAR: Final = 0o644


@dataclass(frozen=True)
class GoldenEntry:
    """One library entity of the fixture, as stored (refs pinned: ``slug@1#<hash12>``)."""

    kind: str
    slug: str
    name: str
    description: str
    content: dict[str, Any]
    files: Mapping[str, bytes] = field(default_factory=dict)  # starter only: path -> bytes

    @property
    def ref(self) -> str:
        return f"{self.slug}@1"

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.slug}"

    def content_hash(self) -> str:
        """``H({"kind", "schema", "content"})`` over the normalized content (what the model stores)."""
        normalized = normalize_content(self.kind, self.content)
        return identity.library_content_hash(self.kind, SCHEMA_VERSIONS[self.kind], normalized)


@dataclass(frozen=True)
class GoldenParent:
    """The parent app: its description (``parent_app.yaml``) and its tree."""

    meta: Mapping[str, Any]
    files: Mapping[str, bytes]

    def tree_hash(self) -> str:
        return identity.tree_hash((path, _REGULAR, identity.bytes_sha(data)) for path, data in self.files.items())


@dataclass(frozen=True)
class GoldenLibrary:
    entries: tuple[GoldenEntry, ...]
    parent: GoldenParent

    def of(self, kind: str) -> list[GoldenEntry]:
        return [entry for entry in self.entries if entry.kind == kind]

    def get(self, kind: str, slug: str) -> GoldenEntry:
        for entry in self.entries:
            if entry.kind == kind and entry.slug == slug:
                return entry
        msg = f"the golden library has no {kind} {slug!r}"
        raise KeyError(msg)

    def __iter__(self) -> Iterator[GoldenEntry]:
        return iter(self.entries)


# =====================================================================================
# Loading (no database)
# =====================================================================================


def _yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        msg = f"{path} must hold a mapping"
        raise TypeError(msg)
    return data


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def _wrapped(kind: str, path: Path) -> GoldenEntry:
    data = _yaml(path)
    return GoldenEntry(
        kind=kind,
        slug=path.stem,
        name=data["name"],
        description=data.get("description", ""),
        content=data["content"],
    )


def _prompt(meta_path: Path) -> GoldenEntry:
    slug = meta_path.name.removesuffix(".meta.yaml")
    meta = _yaml(meta_path)
    template = (meta_path.parent / f"{slug}.jinja2").read_text(encoding="utf-8").removesuffix("\n")
    content = {
        "role": meta["role"],
        "engine": "jinja2-sandbox@1",
        "template": template,
        "variables": meta.get("variables", []),
        "modes": meta["modes"],
        "tags": meta.get("tags", []),
    }
    return GoldenEntry("prompt", slug, meta["name"], meta.get("description", ""), content)


def _starter(directory: Path) -> GoldenEntry:
    data = _yaml(directory / "starter.yaml")
    files = _tree(directory / "files")
    modes = data.pop("modes", {}) or {}
    content = {key: value for key, value in data.items() if key not in {"name", "description"}} | {
        "files": {
            path: {"blob": identity.bytes_sha(blob), "mode": _EXECUTABLE if modes.get(path) == "x" else _REGULAR}
            for path, blob in files.items()
        },
    }
    return GoldenEntry("starter", directory.name, data["name"], data.get("description", ""), content, files)


def _task(directory: Path) -> GoldenEntry:
    content = _yaml(directory / "task.yaml")
    content["prose"] = {
        path.stem: path.read_text(encoding="utf-8").removesuffix("\n")
        for path in sorted((directory / "prose").glob("*.md"))
    }
    return GoldenEntry("task", directory.name, content["title"], content["summary"], content)


def _pin_refs(entries: list[GoldenEntry]) -> list[GoldenEntry]:
    """Pin every ``slug@1`` ref to ``slug@1#<hash12>`` (entries in dependency order, see INSTALL_ORDER)."""
    hashes: dict[tuple[str, str, int], str] = {}
    pinned: list[GoldenEntry] = []
    for entry in entries:
        content, errors = pin_content_refs(
            entry.kind,
            entry.content,
            lambda kind, slug, version: hashes.get((kind, slug, version)),
        )
        if errors:
            msg = f"golden {entry.key}: unresolved refs {errors}"
            raise ValueError(msg)
        entry = replace(entry, content=content)  # noqa: PLW2901
        hashes[(entry.kind, entry.slug, 1)] = entry.content_hash()
        pinned.append(entry)
    return pinned


@functools.cache
def load_golden_library() -> GoldenLibrary:
    """Read the fixture from disk (cached; the entries are shared, so copy content before changing it)."""
    entries: list[GoldenEntry] = [_prompt(path) for path in sorted((GOLDEN_ROOT / "prompts").glob("*.meta.yaml"))]
    for kind, dirname in _YAML_DIRS.items():
        entries += [_wrapped(kind, path) for path in sorted((GOLDEN_ROOT / dirname).glob("*.yaml"))]
    entries += [_starter(path) for path in sorted((GOLDEN_ROOT / "starters").iterdir()) if path.is_dir()]
    entries += [_wrapped("detector", path) for path in sorted((GOLDEN_ROOT / "detectors").glob("*/*.yaml"))]
    entries += [_task(path) for path in sorted((GOLDEN_ROOT / "tasks").iterdir()) if path.is_dir()]
    entries.sort(key=lambda entry: (INSTALL_ORDER.index(entry.kind), entry.slug))
    entries = _pin_refs(entries)
    parent = GoldenParent(meta=_yaml(GOLDEN_ROOT / "parent_app.yaml"), files=_tree(GOLDEN_ROOT / "parent_app"))
    return GoldenLibrary(entries=tuple(entries), parent=parent)


def parent_app_files() -> dict[str, bytes]:
    """The parent app tree (relative POSIX path -> bytes)."""
    return dict(load_golden_library().parent.files)


def golden_hashes() -> dict[str, str]:
    """``{"<kind>:<slug>": content_hash, "parent_app": tree_hash}`` computed from the files."""
    library = load_golden_library()
    hashes = {entry.key: entry.content_hash() for entry in library}
    hashes["parent_app"] = library.parent.tree_hash()
    return dict(sorted(hashes.items()))


def write_golden_hashes(path: Path = HASHES_PATH) -> Path:
    """Regenerate ``hashes.json`` (only for an intended change of the fixture or of a contract)."""
    load_golden_library.cache_clear()
    path.write_text(json.dumps(golden_hashes(), indent=2) + "\n", encoding="utf-8")
    return path


# =====================================================================================
# Installing: database rows
# =====================================================================================


@dataclass
class InstalledGoldenLibrary:
    """The installed rows by kind and slug (``task`` maps to ``TaskSpecVersion`` rows)."""

    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get(self, kind: str, slug: str) -> Any:
        return self.rows[kind][slug]

    def pinned_ref(self, kind: str, slug: str) -> str:
        return self.get(kind, slug).pinned_ref


def _system_fields(created_by: User | None) -> dict[str, Any]:
    from backend.generation.models import choices

    return {"is_system": True, "visibility": choices.Visibility.SYSTEM, "created_by": created_by}


def _install_versioned(entry: GoldenEntry, model: type[VersionedEntity], **extra: Any) -> VersionedEntity:
    from backend.generation.models import ImmutableVersionError

    existing = model.objects.filter(slug=entry.slug, version=1).first()
    if existing is not None:
        if existing.content_hash != entry.content_hash():
            msg = f"{model.__name__} {entry.ref} exists with other content; the golden library cannot be installed"
            raise ImmutableVersionError(msg)
        return existing
    return model.objects.create(
        slug=entry.slug,
        version=1,
        name=entry.name,
        description=entry.description,
        content=entry.content,
        **extra,
    )


def install_golden_library(*, created_by: User | None = None) -> InstalledGoldenLibrary:
    """Create every fixture entity as a system row (idempotent) and store the starter blobs."""
    from backend.generation import blobs
    from backend.generation.models import LIBRARY_MODELS
    from backend.generation.models import TaskSpec
    from backend.generation.models import TaskSpecVersion
    from backend.generation.models import choices

    installed = InstalledGoldenLibrary()
    system = _system_fields(created_by)
    for entry in load_golden_library():
        bucket = installed.rows.setdefault(entry.kind, {})
        if entry.kind == "task":
            task, _ = TaskSpec.objects.get_or_create(
                slug=entry.slug,
                defaults={**system, "latest_version": 1},
            )
            version = TaskSpecVersion.objects.filter(task=task, version=1).first()
            if version is None:
                version = TaskSpecVersion.objects.create(
                    task=task,
                    version=1,
                    name=entry.name,
                    content=entry.content,
                    created_by=created_by,
                )
            bucket[entry.slug] = version
            continue
        extra: dict[str, Any] = dict(system)
        if entry.kind == "starter":
            for data in entry.files.values():
                blobs.put_bytes(data)
            stack_slug = entry.content["stack_profile"].split("@", 1)[0]
            extra["stack_profile"] = installed.get("stack", stack_slug)
        if entry.kind == "resource_policy" and entry.slug in choices.PolicyTier.values:
            extra["tier"] = entry.slug
        bucket[entry.slug] = _install_versioned(entry, LIBRARY_MODELS[entry.kind], **extra)
    return installed


def _origin(path: str, data: bytes, starter_files: Mapping[str, bytes]) -> str:
    if path not in starter_files:
        return "authored"
    return "starter" if starter_files[path] == data else "modified"


def _is_test(path: str) -> bool:
    pure = PurePosixPath(path)
    return "tests" in pure.parts or pure.name.startswith("test_")


def install_parent_run(
    installed: InstalledGoldenLibrary | None = None,
    *,
    created_by: User | None = None,
) -> GenerationRun:
    """The parent app as a completed, usable ``app_agentic`` run (``outcome=tested``) with its files.

    File origins are computed against the starter (same bytes: ``starter``; changed: ``modified``; new:
    ``authored``; starter paths the parent lacks: ``deleted``). ``loc`` counts non-blank lines (the real
    ``source_loc`` is WP-02b's). The model is created when missing.
    """
    from django.utils import timezone

    from backend.generation import blobs
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunFile
    from backend.generation.models import choices
    from backend.llm_models.models import LLMModel

    installed = installed or install_golden_library(created_by=created_by)
    library = load_golden_library()
    meta = library.parent.meta
    starter_slug = meta["starter"].split("@", 1)[0]
    starter_files = library.get("starter", starter_slug).files
    model, _ = LLMModel.objects.get_or_create(
        model_id=meta["model"],
        defaults={"canonical_slug": meta["model"].replace("/", "_"), "provider": "test", "model_name": meta["model"]},
    )
    run = GenerationRun.objects.create(
        kind=choices.RunKind.APP_AGENTIC,
        status=choices.RunStatus.COMPLETED,
        outcome=choices.Outcome.TESTED,
        usable=True,
        task_version=installed.get("task", meta["task"].split("@", 1)[0]),
        prose_variant=meta["prose_variant"],
        stack_profile=installed.get("stack", meta["stack_profile"].split("@", 1)[0]),
        starter=installed.get("starter", starter_slug),
        model=model,
        tree_hash=library.parent.tree_hash(),
        completed_at=timezone.now(),
        created_by=created_by,
    )
    rows = []
    for path, data in library.parent.files.items():
        text = data.decode("utf-8", errors="replace")
        rows.append(
            RunFile(
                run=run,
                path=path,
                blob=blobs.put_bytes(data),
                size=len(data),
                mode=_REGULAR,
                origin=_origin(path, data, starter_files),
                language=_LANGUAGES.get(PurePosixPath(path).suffix, ""),
                loc=sum(1 for line in text.splitlines() if line.strip()),
                is_test=_is_test(path),
            ),
        )
    rows += [
        RunFile(run=run, path=path, blob="", size=0, origin=choices.FileOrigin.DELETED)
        for path in starter_files
        if path not in library.parent.files
    ]
    RunFile.objects.bulk_create(rows)
    return run
