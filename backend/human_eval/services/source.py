"""Source viewer (owner B2): file tree and file content of a run's generated app (blinded, size-capped).

Participants may browse an app's source only when the task's ``blinding.show_source_code``
is on (the view answers 404 otherwise). Files come from ``RunView.code_files("app")``: the
whole app tree the participant is testing (starter files, the model's files and, for a slot
variant, the parent's tree with the variant's files overlaid). Binary files are not listed.

Analysis findings carry paths relative to the same tree; :func:`display_path` only
normalizes workspace prefixes (``/work/``, ``./``) so "file:line" points at a file the
participant can open.

Sizes are UTF-8 bytes; file content is capped at :data:`MAX_FILE_BYTES` (``truncated``).
Nothing else about the run (manifest, served models, providers, …) is ever returned.
"""

from __future__ import annotations

from typing import Any

from backend.human_eval.services.errors import not_found

MAX_FILE_BYTES = 512 * 1024

#: The code scope participants see: the whole app they test.
SOURCE_SCOPE = "app"
_WORKSPACE_PREFIXES = ("/work/", "./")


def _normalize(path: str) -> str:
    path = path.replace("\\", "/")
    for prefix in _WORKSPACE_PREFIXES:
        if path.startswith(prefix):
            path = path[len(prefix) :]
    return path.lstrip("/")


def run_files(run: Any) -> dict[str, str]:
    """The run's app tree ``{path: text}``, sorted by path ({} without a run or a collected tree)."""
    if run is None:
        return {}
    from backend.generation.runs.view import run_view

    files = run_view(run).code_files(SOURCE_SCOPE)
    return dict(sorted(files.items()))


def has_source(run: Any) -> bool:
    """Whether the run has any text file to show (no blob is read)."""
    if run is None:
        return False
    from backend.generation.runs.view import run_view

    return any(entry.excluded != "binary" and entry.blob for entry in run_view(run).files(SOURCE_SCOPE))


def display_path(file_path: str) -> str:
    """A finding's ``file_path`` as the source viewer names the file (workspace prefixes removed)."""
    return _normalize(file_path or "")


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def source_tree(run: Any) -> dict[str, Any]:
    """TS ``SourceTree``: every file path (sorted) with its size in UTF-8 bytes. 404 when there is no code."""
    files = run_files(run)
    if not files:
        msg = "This app has no source code to show"
        raise not_found(msg)
    return {"files": [{"path": path, "size": _size(text)} for path, text in files.items()]}


def source_file(run: Any, path: str) -> dict[str, Any]:
    """TS ``SourceFile`` for one path of :func:`source_tree` (content capped at 512 KB); unknown path → 404."""
    files = run_files(run)
    key = _normalize(path or "")
    if key not in files:
        msg = "File not found"
        raise not_found(msg)
    raw = files[key].encode("utf-8")
    truncated = len(raw) > MAX_FILE_BYTES
    if truncated:
        raw = raw[:MAX_FILE_BYTES]
    # Cutting bytes may split a multi-byte character: drop the partial tail.
    return {"path": key, "content": raw.decode("utf-8", errors="ignore"), "truncated": truncated}
