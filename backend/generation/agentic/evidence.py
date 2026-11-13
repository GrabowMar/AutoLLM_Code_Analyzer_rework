"""The on-disk evidence of a run attempt: ``GENERATION_RUNS_ROOT/<run>/a<attempt>/`` (spec sections 3.7, 6.7, 9.5).

Everything the sandbox produced is kept here verbatim (gateway ``calls.jsonl``, ``gateway_events.jsonl``,
``state.json``, the harness's ``/logs`` files and artifacts, ``changes.patch``, ``workspace.tar``), plus the
parsed ``trajectory.json``. ``GENERATION_RUNS_ROOT`` is read at call time (tests override ``MEDIA_ROOT``).
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import zipfile
from collections.abc import Iterator
from collections.abc import Mapping
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from typing import Final

from django.conf import settings

logger = logging.getLogger(__name__)

__all__ = [
    "CALLS_FILE",
    "EVENTS_FILE",
    "STATE_FILE",
    "TRAJECTORY_FILE",
    "attempt_dir",
    "read_calls",
    "read_jsonl",
    "run_dir",
    "write_files",
    "zip_run",
]

CALLS_FILE: Final = "calls.jsonl"
EVENTS_FILE: Final = "gateway_events.jsonl"
STATE_FILE: Final = "state.json"
TRAJECTORY_FILE: Final = "trajectory.json"
# Evidence zips stream from disk; a single member above this is left out (listed in MISSING.txt).
MAX_ZIP_MEMBER_BYTES: Final = 512 * 1024 * 1024


def runs_root() -> Path:
    return Path(settings.GENERATION_RUNS_ROOT)


def run_dir(run_id: Any) -> Path:
    return runs_root() / str(run_id)


def attempt_dir(run_id: Any, attempt: int) -> Path:
    return run_dir(run_id) / f"a{int(attempt)}"


def _safe_name(name: str) -> str:
    pure = PurePosixPath(name)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        msg = f"unsafe evidence file name {name!r}"
        raise ValueError(msg)
    return pure.as_posix()


def write_files(run_id: Any, attempt: int, files: Mapping[str, bytes]) -> Path:
    """Write ``files`` (relative names) into the attempt's evidence directory; returns the directory."""
    root = attempt_dir(run_id, attempt)
    root.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        target = root / _safe_name(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root


def copy_file(run_id: Any, attempt: int, source: Path, name: str | None = None) -> Path:
    """Copy one large file (``workspace.tar``) into the evidence directory without reading it into memory."""
    root = attempt_dir(run_id, attempt)
    root.mkdir(parents=True, exist_ok=True)
    target = root / _safe_name(name or source.name)
    shutil.copyfile(source, target)
    return target


def read_jsonl(data: bytes | str | None) -> list[dict[str, Any]]:
    """The JSON object lines of ``data``; malformed lines are skipped."""
    if not data:
        return []
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()  # noqa: PLW2901
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def read_calls(run_id: Any, attempt: int) -> list[dict[str, Any]]:
    """The gateway's ``calls.jsonl`` of one attempt (``[]`` when it was not recorded)."""
    path = attempt_dir(run_id, attempt) / CALLS_FILE
    try:
        return read_jsonl(path.read_bytes())
    except FileNotFoundError:
        return []


def read_json(run_id: Any, attempt: int, name: str) -> Any:
    path = attempt_dir(run_id, attempt) / _safe_name(name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def _iter_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def zip_run(run_id: Any) -> bytes:
    """A zip of every attempt's evidence of ``run_id`` (``a<n>/...`` paths); empty zip when none exists."""
    root = run_dir(run_id)
    buffer = io.BytesIO()
    skipped: list[str] = []
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if root.is_dir():
            for path in _iter_files(root):
                rel = path.relative_to(root).as_posix()
                if path.stat().st_size > MAX_ZIP_MEMBER_BYTES:
                    skipped.append(rel)
                    continue
                archive.write(path, rel)
        if skipped:
            archive.writestr("MISSING.txt", "Left out (too large):\n" + "\n".join(skipped) + "\n")
    return buffer.getvalue()
