"""The per-variant record (``SlotVariantResult``): parse status, replacement, diff to the original, imports.

``record_variant`` is shared by the single-shot executor, the batch submitter and the restricted agent, so
every variant is described the same way. ``normalized_sha`` and ``diff_vs_original.normalized_equal`` /
``ast_edit_distance`` are left for the pattern analysis (WP-13's normalizer), which fills them in its own
pass; they are never guessed here.

``new_imports`` are modules the replacement imports that the original file did not; ``new_dependencies`` are
the subset that is neither local to the parent tree, nor the standard library / a node builtin, nor already
declared in the parent's dependency manifests (for the restricted agent, edits to manifests are added too).
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from collections.abc import Iterable
from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Final

from backend.generation import blobs
from backend.generation import identity
from backend.generation.contracts.slot import DiffVsOriginal
from backend.generation.contracts.slot import OutOfSlotEdit
from backend.generation.models import SlotVariantResult
from backend.generation.slot.index.imports import file_imports
from backend.generation.slot.tree import is_dependency_file

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.slot.part import Part

__all__ = [
    "declared_dependencies",
    "declared_in",
    "diff_vs_original",
    "import_changes",
    "record_variant",
    "replacement_payload",
]

_NODE_BUILTINS: Final = frozenset(
    {
        "assert",
        "buffer",
        "child_process",
        "crypto",
        "events",
        "fs",
        "http",
        "https",
        "net",
        "os",
        "path",
        "process",
        "querystring",
        "readline",
        "stream",
        "string_decoder",
        "timers",
        "tls",
        "url",
        "util",
        "zlib",
    },
)
_REQ_NAME_RE: Final = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_GO_REQUIRE_RE: Final = re.compile(r"^\s*(?:require\s+)?([\w./-]+)\s+v[\w.+-]+", re.M)


def replacement_payload(replacement: str | dict[str, str]) -> tuple[str, str]:
    """``(blob, sha)`` of a replacement.

    The blob holds the model's bytes exactly (UTF-8 text, or JSON of ``{path: text}`` with sorted keys), so
    ``assemble.spliced_files`` reproduces the stored rows byte for byte; ``sha`` is the identity of the content
    (``text_sha`` of the code, ``H`` of the files dict), both NFC/LF-normalised.
    """
    if isinstance(replacement, dict):
        data = json.dumps(replacement, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return blobs.put_bytes(data.encode("utf-8"), blobs.JSON_MEDIA_TYPE), identity.H(replacement)
    return blobs.put_bytes(replacement.encode("utf-8"), blobs.TEXT_MEDIA_TYPE), identity.text_sha(replacement)


def diff_vs_original(original: str, replacement: str) -> DiffVsOriginal:
    a = identity.normalize_text(original).splitlines()
    b = identity.normalize_text(replacement).splitlines()
    added = removed = 0
    for line in difflib.unified_diff(a, b, lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    similarity = difflib.SequenceMatcher(None, "\n".join(a), "\n".join(b), autojunk=False).ratio()
    return DiffVsOriginal(lines_added=added, lines_removed=removed, similarity=round(similarity, 6))


def declared_dependencies(part: Part) -> set[str]:
    """Package names declared in the parent's manifests (requirements*.txt, package.json, go.mod, pyproject)."""
    return declared_in(part.parent.texts)


def declared_in(texts: Mapping[str, str]) -> set[str]:
    """Package names declared in the dependency manifests among ``{path: text}``."""
    names: set[str] = set()
    for path, text in texts.items():
        if not is_dependency_file(path):
            continue
        name = path.rsplit("/", 1)[-1].lower()
        if name.endswith(".txt"):
            names |= {m.group(1).lower() for line in text.splitlines() if (m := _REQ_NAME_RE.match(line))}
        elif name == "package.json":
            try:
                data = json.loads(text)
            except ValueError:
                continue
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                names |= {dep.lower() for dep in (data.get(key) or {})}
        elif name == "go.mod":
            names |= {m.group(1).lower() for m in _GO_REQUIRE_RE.finditer(text)}
        elif name == "pyproject.toml":
            names |= {m.group(1).lower() for m in re.finditer(r'"([A-Za-z0-9][A-Za-z0-9._-]*)', text)}
    return names


def _top_level(module: str, language: str) -> str:
    if language == "python":
        return module.lstrip(".").split(".", 1)[0].lower()
    if module.startswith("@"):
        return "/".join(module.split("/")[:2]).lower()
    return module.split("/", 1)[0].lower()


def _external(module: str, language: str) -> bool:
    if language == "python":
        top = _top_level(module, language)
        return not module.startswith(".") and top not in sys.stdlib_module_names
    if language in {"javascript", "typescript", "tsx", "vue", "svelte"}:
        bare = module.removeprefix("node:")
        return (
            not module.startswith((".", "/", "@/", "~/"))
            and not module.startswith("node:")
            and (_top_level(bare, language) not in _NODE_BUILTINS)
        )
    if language == "go":
        return "." in module.split("/", 1)[0]
    return False


def import_changes(part: Part, new_texts: dict[str, str]) -> tuple[list[str], list[str]]:
    """``(new_imports, new_dependencies)`` of the replaced files against the original files."""
    paths = frozenset(part.parent.tree.files) | frozenset(new_texts)
    declared = declared_dependencies(part)
    new_imports: set[str] = set()
    new_dependencies: set[str] = set()
    for path, text in new_texts.items():
        before = {i.module for i in file_imports(path, part.parent.texts.get(path, ""), paths)}
        after = file_imports(path, text, paths)
        for item in after:
            if item.module in before:
                continue
            new_imports.add(item.module)
            language = part.language if path == part.path else ""
            if item.path is None and _external(item.module, language or part.language):
                top = _top_level(item.module, language or part.language)
                aliases = {top, top.replace("_", "-"), top.replace("-", "_")}
                if not aliases & declared:
                    new_dependencies.add(top)
    return sorted(new_imports), sorted(new_dependencies)


def record_variant(
    run: GenerationRun,
    part: Part,
    *,
    parse_status: str,
    replacement: str | dict[str, str] | None,
    assembled_texts: dict[str, str] | None = None,
    out_of_slot_edits: Iterable[OutOfSlotEdit] = (),
    edit_violation: bool = False,
    extra_dependencies: Iterable[str] = (),
) -> SlotVariantResult:
    """Create or replace the run's ``SlotVariantResult`` (a retry overwrites the previous attempt's row)."""
    blob = sha = ""
    diff: DiffVsOriginal | None = None
    new_imports: list[str] = []
    new_dependencies: list[str] = list(extra_dependencies)
    if replacement is not None:
        blob, sha = replacement_payload(replacement)
        if isinstance(replacement, dict):
            original = "\n".join(part.parent.texts.get(p, "") for p in sorted(replacement))
            new_text = "\n".join(replacement[p] for p in sorted(replacement))
        else:
            original, new_text = part.original_text, replacement
        diff = diff_vs_original(original, new_text)
        if assembled_texts:
            new_imports, deps = import_changes(part, assembled_texts)
            new_dependencies = sorted(set(new_dependencies) | set(deps))
    edits = [edit.model_dump(mode="json") for edit in out_of_slot_edits]
    result, _ = SlotVariantResult.objects.update_or_create(
        run=run,
        defaults={
            "slot_target": run.slot_target,
            "parse_status": parse_status,
            "replacement_blob": blob,
            "replacement_sha": sha,
            "normalized_sha": "",
            "normalizer_version": None,
            "out_of_slot_edits": edits,
            "edit_violation": edit_violation,
            "diff_vs_original": diff.model_dump(mode="json") if diff is not None else None,
            "new_imports": new_imports,
            "new_dependencies": sorted(set(new_dependencies)),
        },
    )
    return result
