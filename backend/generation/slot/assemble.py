"""Splice a variant into its parent tree; writes the variant's ``slot`` RunFile rows only (spec section 7.7).

* ``assemble_tree(target, replacement)`` is pure: the parent's ``app`` tree with the replacement spliced in at
  the span's byte offsets of the original file (span kinds; ``replacement`` is the code string) or with whole
  files replaced (``file`` and whole-file components; ``replacement`` is ``{path: text}``). It returns the
  assembled entries, the ``tree_hash`` over the full assembled tree and the changed files.
* ``assemble(run, replacement)`` stores the changed files as blobs, replaces the run's ``RunFile`` rows with one
  ``origin=slot`` row per changed file (``RunView`` overlays the parent's rows for everything else, so N=100
  variants stay small) and sets ``run.tree_hash`` on the instance (the caller persists it with the stage).

Line endings and encoding: the replacement is UTF-8 and is normalised to the original file's line endings
(CRLF originals get CRLF). A trailing newline is ensured when the original span ended with one; when it did
not, one trailing newline of the replacement is dropped (the splice point is mid-line, so it would add a
blank line). Nothing else is touched: no repair, no merging, no renaming (D13).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.generation import blobs
from backend.generation import identity
from backend.generation.models import RunFile
from backend.generation.models.choices import FileOrigin
from backend.generation.slot.errors import SlotInvalidError
from backend.generation.slot.hole import newline_of
from backend.generation.slot.index.languages import language_for
from backend.generation.slot.part import Part
from backend.generation.slot.part import part_of

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import SlotTarget

__all__ = [
    "AssembledTree",
    "assemble",
    "assemble_tree",
    "decode_replacement",
    "normalize_text_to",
    "recorded_replacement",
    "splice",
    "spliced_files",
]

_DEFAULT_MODE = 0o644


@dataclass(frozen=True)
class AssembledTree:
    entries: list[tuple[str, int, str]]  # (path, mode, blob sha) of the full assembled tree
    tree_hash: str
    changed: dict[str, bytes]  # the files that differ from the parent (path -> new bytes)
    removed: list[str]


def normalize_text_to(text: str, original: bytes | None) -> bytes:
    """``text`` as UTF-8 with the original file's line endings (LF for a new file)."""
    data = text.replace("\r\n", "\n").replace("\r", "\n")
    if original is not None and newline_of(original) == b"\r\n":
        data = data.replace("\n", "\r\n")
    return data.encode("utf-8")


def splice(source: bytes, start: int, end: int, replacement: str) -> bytes:
    """``source[:start] + replacement + source[end:]`` with the line-ending and trailing-newline rules."""
    newline = newline_of(source)
    data = normalize_text_to(replacement, source)
    original = source[start:end]
    if original.endswith(b"\n"):
        if not data.endswith(b"\n"):
            data += newline
    elif data.endswith(newline):
        data = data[: -len(newline)]
    return source[:start] + data + source[end:]


def _file_replacement(part: Part, path: str, text: str) -> bytes:
    original = part.parent.tree.read(path) if path in part.parent.tree else None
    data = normalize_text_to(text, original)
    if original is not None and original.endswith(b"\n") and not data.endswith(b"\n"):
        data += newline_of(original)
    return data


def assemble_tree(target: SlotTarget | Part, replacement: str | dict[str, str]) -> AssembledTree:
    """The parent tree with the replacement spliced in (nothing is stored)."""
    part = target if isinstance(target, Part) else part_of(target)
    tree = part.parent.tree
    changed: dict[str, bytes] = {}
    if isinstance(replacement, dict):
        unknown = sorted(set(replacement) - set(part.allowlist))
        if unknown:
            msg = f"replacement files outside the allowlist: {', '.join(unknown)}"
            raise SlotInvalidError(msg)
        changed = {path: _file_replacement(part, path, text) for path, text in sorted(replacement.items())}
    else:
        # A text replacement is span code whenever the address has a span: the run's hole mode, not the
        # target's default, decides the answer's shape, and a removed whole-file component always answers
        # with ``{path: text}`` (the branch above).
        span = part.address.span
        if span is None:
            changed = {part.path: _file_replacement(part, part.path, replacement)}
        else:
            changed = {part.path: splice(part.source, span.start_byte, span.end_byte, replacement)}
    entries: dict[str, tuple[str, int, str]] = {
        entry.path: (entry.path, entry.mode, entry.blob)
        for entry in tree
        if entry.origin not in {"artifact", "deleted"}
    }
    for path, data in changed.items():
        mode = tree.files[path].mode if path in tree else _DEFAULT_MODE
        entries[path] = (path, mode, identity.bytes_sha(data))
    ordered = [entries[path] for path in sorted(entries)]
    return AssembledTree(entries=ordered, tree_hash=identity.tree_hash(ordered), changed=changed, removed=[])


def assemble(run: GenerationRun, replacement: str | dict[str, str]) -> list[RunFile]:
    """Write the variant's ``slot`` rows (byte offsets, CRLF kept) and set ``run.tree_hash``."""
    from backend.generation.runs.view import source_loc

    if run.slot_target is None:
        msg = f"run {run.pk} has no slot target"
        raise SlotInvalidError(msg)
    assembled = assemble_tree(run.slot_target, replacement)
    parent_files = part_of(run.slot_target).parent.tree.files
    rows: list[RunFile] = []
    for path, data in assembled.changed.items():
        sha = blobs.put_bytes(data)
        text = data.decode("utf-8", errors="replace")
        rows.append(
            RunFile(
                run=run,
                path=path,
                blob=sha,
                size=len(data),
                mode=parent_files[path].mode if path in parent_files else _DEFAULT_MODE,
                origin=FileOrigin.SLOT,
                language=(language_for(path) or "")[:24],
                loc=source_loc(path, text),
                is_test=False,
            ),
        )
    RunFile.objects.filter(run=run).delete()
    RunFile.objects.bulk_create(rows)
    run.tree_hash = assembled.tree_hash
    return rows


def recorded_replacement(run: GenerationRun) -> str | dict[str, str] | None:
    """The replacement stored in the run's ``SlotVariantResult`` (``None`` when it has none)."""
    from backend.generation.models import SlotVariantResult

    result = SlotVariantResult.objects.filter(run=run).only("replacement_blob").first()
    if result is None or not result.replacement_blob:
        return None
    text = blobs.get_text(result.replacement_blob)
    if run.slot_target is None:
        return text
    return decode_replacement(part_of(run.slot_target), text, files_form=_files_form(run))


def decode_replacement(part: Part, text: str, *, files_form: bool) -> str | dict[str, str]:
    """A stored replacement as ``assemble`` takes it.

    The files form is ``{path: text}`` JSON: always for file targets and removed whole-file components, and for a
    span kind when a restricted agent also edited other allowlisted files (then the slot file is one of the
    keys). Span code that merely looks like JSON is never mistaken for it: the keys must be allowlisted paths
    including the slot file.
    """
    import json

    if not files_form and not text.startswith("{"):
        return text
    try:
        files = json.loads(text)
    except ValueError:
        return text
    if not isinstance(files, dict) or not all(isinstance(v, str) for v in files.values()):
        return text
    if files_form or (part.path in files and set(files) <= set(part.allowlist)):
        return files
    return text


def _files_form(run: GenerationRun) -> bool:
    """Whether the run's replacement is ``{path: text}`` (file targets, removed whole-file components)."""
    from backend.generation.models import RunManifest
    from backend.generation.slot.prompt import uses_files_output

    part = part_of(run.slot_target)  # type: ignore[arg-type]
    manifest = RunManifest.objects.filter(run=run).values_list("manifest", flat=True).first() or {}
    hole_mode = (manifest.get("slot") or {}).get("hole_mode") or part.default_hole_mode
    return uses_files_output(part, hole_mode)


def spliced_files(run: GenerationRun) -> dict[str, bytes]:
    """The full bytes of every file the run's recorded replacement changes, exactly as ``assemble`` stores them.

    Pure (no database writes): the build-context hook for a variant whose ``slot`` rows are not stored yet
    (``build.context.prepare_build_dir``). Empty when the run has no recorded replacement.
    """
    if run.slot_target is None:
        return {}
    replacement = recorded_replacement(run)
    if replacement is None:
        return {}
    return dict(assemble_tree(run.slot_target, replacement).changed)
