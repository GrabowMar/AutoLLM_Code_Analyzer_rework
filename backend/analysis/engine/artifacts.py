"""Content-addressed run artifacts under ``<root>/analysis_runs/<run>-<key>/`` (owner: executor stage).

Raw tool outputs (stdout, stderr, report files) and AI review transcripts are
kept so a result can be audited or re-parsed with a newer adapter without
re-running the tool. Each entry stored on a row records the sha256 of the raw
bytes; :func:`load` refuses to return bytes that no longer match it.

Artifacts are served only by the owner-scoped, sha-verified API. They hold
the analysed app's source (line-numbered review transcripts, tool output
with code snippets), so their files must not be reachable another way:

* ``<root>`` is ``ANALYSIS_ARTIFACTS_ROOT`` when set: a private directory
  (a volume the web server does not serve). Otherwise it is ``MEDIA_ROOT``,
  which nginx / Django's static view serve publicly at ``/media/``.
* Either way, a run's directory name carries ``<key>``, an HMAC of the run id
  under ``SECRET_KEY``: a run id (shared in study exports) is not enough to
  name a file, so the public media route cannot be used to bypass the API.

An entry's ``path`` is relative to ``<root>``; entries written while the
artifacts lived under MEDIA_ROOT still load after ``ANALYSIS_ARTIFACTS_ROOT``
is set (they are looked up there when missing from the private root).
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings

# Anything outside this set becomes "_" so a name can never be a separator,
# a parent reference or an absolute path.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_KEY_CHARS = 32


def _media_root() -> Path:
    return Path(settings.MEDIA_ROOT).resolve()


def _storage_root() -> Path:
    """Where artifacts are written: the private root when configured, else MEDIA_ROOT."""
    private = str(getattr(settings, "ANALYSIS_ARTIFACTS_ROOT", "") or "").strip()
    return Path(private).resolve() if private else _media_root()


def _roots() -> list[Path]:
    """Roots an entry may live under: the storage root first, then MEDIA_ROOT (entries from before a private root)."""
    roots = [_storage_root()]
    media = _media_root()
    if media not in roots:
        roots.append(media)
    return roots


def _artifacts_dir_name() -> str:
    return getattr(settings, "ANALYSIS_ARTIFACTS_DIR", "analysis_runs")


def safe_segment(value: str) -> str:
    """One filesystem-safe path segment derived from *value* (never empty, never ``..``)."""
    cleaned = _UNSAFE_CHARS.sub("_", str(value)).strip("._") or "_"
    return cleaned[:150]


def _run_key(run_id: str) -> str:
    """HMAC-SHA256 of the run id under SECRET_KEY (hex prefix): unguessable without the key, stable per run."""
    secret = str(getattr(settings, "SECRET_KEY", "") or "").encode()
    return hmac.new(secret, f"analysis-artifacts:{run_id}".encode(), hashlib.sha256).hexdigest()[:_KEY_CHARS]


def run_dir(run_id: str) -> Path:
    """The directory new artifacts of *run_id* are written to."""
    return _storage_root() / _artifacts_dir_name() / f"{safe_segment(str(run_id))}-{_run_key(str(run_id))}"


def _run_dirs(run_id: str) -> list[Path]:
    """Every existing directory of *run_id*'s artifacts, under any root and any past SECRET_KEY."""
    segment = safe_segment(str(run_id))
    pattern = re.compile(rf"{re.escape(segment)}-[0-9a-f]{{{_KEY_CHARS}}}")
    found: list[Path] = []
    for root in _roots():
        base = root / _artifacts_dir_name()
        if not base.is_dir():
            continue
        # The unkeyed name is the layout from before run keys existed.
        if (base / segment).is_dir():
            found.append(base / segment)
        found.extend(p for p in base.iterdir() if p.is_dir() and pattern.fullmatch(p.name))
    return found


def store(run_id: str, scope: str, name: str, data: bytes, *, compress: bool = True) -> dict[str, Any]:
    """Write bytes (gzip by default); return {path, sha256 (of the raw bytes), bytes, stored_bytes, compressed}.

    ``path`` is relative to the storage root. *scope* may hold
    ``/``-separated segments (``bandit``, ``ai_review/sample-0``); every
    segment and *name* are sanitized, so nothing escapes the run directory.
    The write is atomic (temp file + rename): a crash never leaves a
    truncated file behind a recorded sha256.
    """
    raw = bytes(data)
    segments = [safe_segment(part) for part in str(scope).split("/") if part.strip()]
    directory = run_dir(run_id).joinpath(*segments)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (safe_segment(name) + (".gz" if compress else ""))
    # mtime=0 keeps the stored bytes identical for identical content.
    payload = gzip.compress(raw, compresslevel=6, mtime=0) if compress else raw
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        Path(tmp).replace(target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return {
        "path": target.relative_to(_storage_root()).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "stored_bytes": len(payload),
        "compressed": compress,
    }


def _resolve(entry: dict[str, Any]) -> Path:
    rel = str(entry.get("path") or "")
    if not rel:
        msg = "Artifact entry has no path"
        raise ValueError(msg)
    candidates = []
    for root in _roots():
        path = (root / rel).resolve()
        if root not in path.parents:
            msg = f"Artifact path escapes the artifact root: {rel}"
            raise ValueError(msg)
        candidates.append(path)
    # The storage root wins; MEDIA_ROOT only serves entries written before a private root was configured.
    return next((path for path in candidates if path.is_file()), candidates[0])


def load(entry: dict[str, Any]) -> bytes:
    """Read an artifact back, verifying its sha256 (ValueError on mismatch).

    A missing file raises ``FileNotFoundError``.
    """
    payload = _resolve(entry).read_bytes()
    try:
        raw = gzip.decompress(payload) if entry.get("compressed") else payload
    except (OSError, EOFError) as exc:
        msg = f"Artifact {entry.get('path')} is not valid gzip: {exc}"
        raise ValueError(msg) from exc
    expected = str(entry.get("sha256") or "")
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        msg = f"Artifact {entry.get('path')} failed sha256 verification (expected {expected or '?'}, got {actual})"
        raise ValueError(msg)
    return raw


def delete_run(run_id: str) -> None:
    """Remove every artifact of a run (call when the run is deleted)."""
    for directory in _run_dirs(run_id):
        shutil.rmtree(directory, ignore_errors=True)


def delete_scope(run_id: str, scope: str) -> None:
    """Remove the artifacts of one scope (a tool slug) of a run, e.g. before a retry."""
    segments = [safe_segment(part) for part in str(scope).split("/") if part.strip()]
    if not segments:
        return
    for directory in _run_dirs(run_id):
        shutil.rmtree(directory.joinpath(*segments), ignore_errors=True)
