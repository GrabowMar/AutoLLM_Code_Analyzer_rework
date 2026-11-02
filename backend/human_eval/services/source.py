"""Source viewer (owner B2): file tree and file content of a job's generated code (blinded, size-capped).

Participants may browse an app's generated source only when the task's
``blinding.show_source_code`` is on (the view answers 404 otherwise). Files come
from ``job.result_data`` exactly as the Applications page lays them out:

- a ``files`` map ``{relpath: text}`` (agent jobs, and scaffolding jobs with result
  schema 2) — paths as they are;
- else the scaffolding blobs ``backend_code`` → ``backend/app.py`` and
  ``frontend_code`` → ``frontend/App.jsx``;
- else the single ``content`` blob → ``output.txt``.

The analysis runner names scaffolding blobs ``backend_code.py`` /
``frontend_code.jsx`` / ``content.py`` (``analysis.services.runner._materialize``),
so automated findings on blob-layout jobs carry those paths: :func:`display_path`
maps them to the viewer's names (spec R1, blinding) so "file:line" points at a
file the participant can open and the harness-specific names never show.

Sizes are UTF-8 bytes; file content is capped at :data:`MAX_FILE_BYTES`
(``truncated``). Nothing else from ``result_data`` (agent run records, served
models, providers, …) is ever returned.
"""

from __future__ import annotations

from typing import Any

from backend.human_eval.services.errors import not_found

MAX_FILE_BYTES = 512 * 1024

#: Scaffolding blobs in ``result_data`` → the viewer's file names.
BLOB_PATHS: dict[str, str] = {"backend_code": "backend/app.py", "frontend_code": "frontend/App.jsx"}
CONTENT_PATH = "output.txt"
#: ``Finding.file_path`` of blob-layout jobs (analysis runner names) → the viewer's file names.
ANALYSIS_BLOB_PATHS: dict[str, str] = {
    "backend_code.py": "backend/app.py",
    "frontend_code.jsx": "frontend/App.jsx",
    "content.py": CONTENT_PATH,
}
_WORKSPACE_PREFIXES = ("/work/", "./")


def _result_data(job: Any) -> dict[str, Any]:
    data = getattr(job, "result_data", None) if job is not None else None
    return data if isinstance(data, dict) else {}


def _normalize(path: str) -> str:
    path = path.replace("\\", "/")
    for prefix in _WORKSPACE_PREFIXES:
        if path.startswith(prefix):
            path = path[len(prefix) :]
    return path.lstrip("/")


def _file_map(data: dict[str, Any]) -> dict[str, str]:
    files = data.get("files")
    if not isinstance(files, dict):
        return {}
    mapped = {
        _normalize(path): text for path, text in files.items() if isinstance(path, str) and isinstance(text, str)
    }
    mapped.pop("", None)
    # An all-empty map is no code (the analysis runner reads it the same way).
    return mapped if any(text.strip() for text in mapped.values()) else {}


def uses_blob_layout(job: Any) -> bool:
    """True when the job's code is shown from the scaffolding/``content`` blobs (no usable ``files`` map)."""
    return not _file_map(_result_data(job))


def job_files(job: Any) -> dict[str, str]:
    """The job's source files ``{path: text}`` in the viewer's layout, sorted by path ({} when there is no code)."""
    data = _result_data(job)
    files = _file_map(data)
    if not files:
        for key, path in BLOB_PATHS.items():
            text = data.get(key)
            if isinstance(text, str) and text.strip():
                files[path] = text
    if not files:
        content = data.get("content")
        if isinstance(content, str) and content.strip():
            files[CONTENT_PATH] = content
    return dict(sorted(files.items()))


def has_source(job: Any) -> bool:
    """Whether the job has any code to show."""
    return bool(job_files(job))


def display_path(file_path: str, job: Any) -> str:
    """A finding's ``file_path`` as the source viewer names the file (scaffolding blob names mapped)."""
    path = _normalize(file_path or "")
    if path in ANALYSIS_BLOB_PATHS and uses_blob_layout(job):
        return ANALYSIS_BLOB_PATHS[path]
    return path


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def source_tree(job: Any) -> dict[str, Any]:
    """TS ``SourceTree``: every file path (sorted) with its size in UTF-8 bytes. 404 when there is no code."""
    files = job_files(job)
    if not files:
        msg = "This app has no source code to show"
        raise not_found(msg)
    return {"files": [{"path": path, "size": _size(text)} for path, text in files.items()]}


def source_file(job: Any, path: str) -> dict[str, Any]:
    """TS ``SourceFile`` for one path of :func:`source_tree` (content capped at 512 KB); unknown path → 404."""
    files = job_files(job)
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
