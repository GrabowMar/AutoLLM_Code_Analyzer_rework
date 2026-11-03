"""Content-addressed blob store (spec section 3.2).

Files live at ``GENERATION_BLOBS_ROOT/sha256/<aa>/<bb>/<sha>.<codec>``; the key
is the sha256 of the **uncompressed** bytes. ``GENERATION_BLOBS_ROOT`` defaults
to ``MEDIA_ROOT/blobs`` and is read from settings at call time (tests override
``MEDIA_ROOT`` per test).

* Compression is zstd (``zstandard``) when it is importable, else gzip; the
  codec is recorded per blob (``Blob.codec``) and in the file extension, and
  readers accept either.
* Writes are idempotent and atomic: a temporary file in the target directory
  plus ``os.replace``. A repeated put of an existing blob only refreshes the
  file's mtime (so a blob that is about to be referenced again is not
  collected) and never rewrites it.
* The file is written **before** the ``Blob`` row, so a row never points at a
  missing file; a rolled-back transaction leaves an orphan file that GC
  removes.
* Reads verify the sha of the decompressed bytes (``BlobCorruptError``).
* The ``Blob`` model is looked up lazily (``apps.get_model("generation",
  "Blob")``) at call time, so this module imports without the app registry.

GC (:func:`gc_blobs`) deletes unreferenced blobs older than 7 days.
References are found by scanning every model's ``BLOB_FIELDS`` columns (and,
for shas embedded in JSON, an optional ``iter_blob_refs()`` classmethod).
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from pathlib import Path
from typing import IO
from typing import Any
from typing import Final
from typing import cast

from backend.generation.identity import bytes_sha
from backend.generation.identity import canonical_json
from backend.generation.identity import normalize_text

logger = logging.getLogger(__name__)

__all__ = [
    "CODECS",
    "DEFAULT_GC_AGE",
    "TEXT_MEDIA_TYPE",
    "BlobCorruptError",
    "BlobNotFoundError",
    "GcReport",
    "blob_path",
    "blobs_root",
    "copy_to",
    "exists",
    "find_blob_file",
    "gc_blobs",
    "get_bytes",
    "get_json",
    "get_text",
    "nested_blob_refs",
    "open_stream",
    "preferred_codec",
    "put_bytes",
    "put_file",
    "put_json",
    "put_text",
    "referenced_shas",
    "verify",
]

CODECS: Final = ("zst", "gz")
TEXT_MEDIA_TYPE: Final = "text/plain; charset=utf-8"
JSON_MEDIA_TYPE: Final = "application/json"
DEFAULT_MEDIA_TYPE: Final = "application/octet-stream"
DEFAULT_GC_AGE: Final = timedelta(days=7)
_SHA_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_ZSTD_LEVEL: Final = 10
_GZIP_LEVEL: Final = 6
_CHUNK: Final = 1024 * 1024
_MEDIA_TYPE_MAX: Final = 80
_INCOMING: Final = ".incoming"  # staging dir for streamed puts (not matched by the <aa>/<bb> layout)


class BlobNotFoundError(LookupError):
    """No blob file exists for the requested sha."""


class BlobCorruptError(ValueError):
    """A blob file's decompressed bytes do not hash to its name."""


# ---------------------------------------------------------------------------
# Paths and codecs
# ---------------------------------------------------------------------------


def blobs_root() -> Path:
    """``settings.GENERATION_BLOBS_ROOT`` or ``MEDIA_ROOT/blobs``, read at call time."""
    from django.conf import settings

    configured = getattr(settings, "GENERATION_BLOBS_ROOT", None)
    return Path(configured) if configured else Path(settings.MEDIA_ROOT) / "blobs"


def _check_sha(sha: str) -> str:
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        msg = f"not a lowercase hex sha256: {sha!r}"
        raise ValueError(msg)
    return sha


def blob_path(sha: str, codec: str, root: Path | None = None) -> Path:
    """``<root>/sha256/<aa>/<bb>/<sha>.<codec>``."""
    _check_sha(sha)
    if codec not in CODECS:
        msg = f"unknown blob codec {codec!r}; expected one of {CODECS}"
        raise ValueError(msg)
    base = root if root is not None else blobs_root()
    return base / "sha256" / sha[:2] / sha[2:4] / f"{sha}.{codec}"


def _zstd() -> Any | None:
    try:
        import zstandard
    except ImportError:  # pragma: no cover - depends on the environment
        return None
    return zstandard


def preferred_codec() -> str:
    """``zst`` when ``zstandard`` is importable, else ``gz``."""
    return "zst" if _zstd() is not None else "gz"


def find_blob_file(sha: str, root: Path | None = None) -> tuple[Path, str] | None:
    """The existing file and codec for ``sha`` (any codec), or ``None``."""
    base = root if root is not None else blobs_root()
    for codec in CODECS:
        path = blob_path(sha, codec, base)
        if path.is_file():
            return path, codec
    return None


def _compressor(codec: str, sink: IO[bytes]) -> Any:
    if codec == "zst":
        zstd = _zstd()
        if zstd is None:  # pragma: no cover - guarded by preferred_codec
            msg = "codec 'zst' needs the zstandard package"
            raise RuntimeError(msg)
        return zstd.ZstdCompressor(level=_ZSTD_LEVEL).stream_writer(sink, closefd=False)
    # mtime=0 keeps gzip output deterministic for identical input.
    return gzip.GzipFile(fileobj=sink, mode="wb", compresslevel=_GZIP_LEVEL, mtime=0)


def _decompressing_reader(path: Path, codec: str) -> IO[bytes]:
    if codec == "zst":
        zstd = _zstd()
        if zstd is None:
            msg = f"blob {path.name} is zstd-compressed but the zstandard package is not installed"
            raise RuntimeError(msg)
        return zstd.ZstdDecompressor().stream_reader(path.open("rb"), closefd=True)
    return cast("IO[bytes]", gzip.open(path, "rb"))


# ---------------------------------------------------------------------------
# Blob rows (lazy model lookup)
# ---------------------------------------------------------------------------


def _blob_model() -> Any:
    from django.apps import apps

    return apps.get_model("generation", "Blob")


def _record(sha: str, *, size: int, codec: str, media_type: str) -> None:
    model = _blob_model()
    model.objects.get_or_create(
        sha256=sha,
        defaults={"size": size, "codec": codec, "media_type": media_type[:_MEDIA_TYPE_MAX]},
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _touch(path: Path) -> None:
    with contextlib.suppress(OSError):
        now = time.time()
        os.utime(path, (now, now))


def _write_atomic(target: Path, codec: str, chunks: Iterable[bytes]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as raw:
            writer = _compressor(codec, raw)
            try:
                for chunk in chunks:
                    writer.write(chunk)
            finally:
                writer.close()
            raw.flush()
            os.fsync(raw.fileno())
        tmp.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _store(sha: str, size: int, media_type: str, chunks: Iterable[bytes], *, record: bool) -> str:
    root = blobs_root()
    found = find_blob_file(sha, root)
    if found is not None:
        path, codec = found
        _touch(path)
    else:
        codec = preferred_codec()
        _write_atomic(blob_path(sha, codec, root), codec, chunks)
    if record:
        _record(sha, size=size, codec=codec, media_type=media_type)
    return sha


def put_bytes(data: bytes, media_type: str = DEFAULT_MEDIA_TYPE, *, record: bool = True) -> str:
    """Store ``data`` and return its sha256 (idempotent). ``record=False`` skips the ``Blob`` row."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        msg = f"put_bytes expects bytes, got {type(data).__name__}"
        raise TypeError(msg)
    payload = bytes(data)
    sha = bytes_sha(payload)
    return _store(sha, len(payload), media_type, [payload], record=record)


def put_text(text: str, media_type: str = TEXT_MEDIA_TYPE, *, record: bool = True) -> str:
    """Store NFC/LF-normalized UTF-8 text; the returned sha equals ``identity.text_sha(text)``."""
    return put_bytes(normalize_text(text).encode("utf-8"), media_type, record=record)


def put_json(obj: Any, *, record: bool = True) -> str:
    """Store ``canonical_json(obj)``; the returned sha equals ``identity.H(obj)``."""
    return put_bytes(canonical_json(obj), JSON_MEDIA_TYPE, record=record)


def put_file(path: Path | str, media_type: str = DEFAULT_MEDIA_TYPE, *, record: bool = True) -> str:
    """Store a (possibly large) file in one streaming pass (hash and compress together); returns its sha256."""
    source = Path(path)
    root = blobs_root()
    incoming = root / "sha256" / _INCOMING
    incoming.mkdir(parents=True, exist_ok=True)
    codec = preferred_codec()
    digest = hashlib.sha256()
    size = 0
    fd, tmp_name = tempfile.mkstemp(prefix=".put.", suffix=".tmp", dir=incoming)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as raw:
            writer = _compressor(codec, raw)
            try:
                with source.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(_CHUNK), b""):
                        digest.update(chunk)
                        size += len(chunk)
                        writer.write(chunk)
            finally:
                writer.close()
            raw.flush()
            os.fsync(raw.fileno())
        sha = digest.hexdigest()
        found = find_blob_file(sha, root)
        if found is not None:
            path_found, codec = found
            _touch(path_found)
        else:
            target = blob_path(sha, codec, root)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp.replace(target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    if record:
        _record(sha, size=size, codec=codec, media_type=media_type)
    return sha


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def exists(sha: str) -> bool:
    """``True`` when a blob file exists for ``sha`` (the file is the source of truth for content)."""
    return find_blob_file(_check_sha(sha)) is not None


def open_stream(sha: str) -> IO[bytes]:
    """A readable, decompressing binary stream (not verified; use ``get_bytes`` for verified reads)."""
    found = find_blob_file(_check_sha(sha))
    if found is None:
        msg = f"blob {sha} not found under {blobs_root()}"
        raise BlobNotFoundError(msg)
    return _decompressing_reader(*found)


def verify(sha: str) -> None:
    """Stream-check that the stored content of ``sha`` hashes to ``sha`` (raises ``BlobCorruptError``)."""
    digest = hashlib.sha256()
    with open_stream(sha) as reader:
        for chunk in iter(lambda: reader.read(_CHUNK), b""):
            digest.update(chunk)
    if digest.hexdigest() != sha:
        msg = f"blob {sha} is corrupt: content hashes to {digest.hexdigest()}"
        raise BlobCorruptError(msg)


def get_bytes(sha: str) -> bytes:
    """The verified uncompressed bytes of ``sha``."""
    with open_stream(sha) as stream:
        data = stream.read()
    actual = bytes_sha(data)
    if actual != sha:
        msg = f"blob {sha} is corrupt: content hashes to {actual}"
        raise BlobCorruptError(msg)
    return data


def get_text(sha: str) -> str:
    return get_bytes(sha).decode("utf-8")


def get_json(sha: str) -> Any:
    return json.loads(get_bytes(sha))


# ---------------------------------------------------------------------------
# Garbage collection
# ---------------------------------------------------------------------------


@dataclass
class GcReport:
    dry_run: bool
    scanned: int = 0
    referenced: int = 0
    candidates: list[str] = field(default_factory=list)
    deleted: int = 0
    bytes_freed: int = 0


def nested_blob_refs(sha: str, extract: Callable[[Any], Iterable[str]]) -> list[str]:
    """The shas ``extract`` finds in the JSON payload of blob ``sha`` (a blob that references blobs).

    A missing, corrupt or non-JSON payload yields nothing (its references are unreachable anyway) and is
    logged; any other error propagates, so :func:`gc_blobs` aborts instead of deleting what it cannot see.
    """
    try:
        payload = get_json(sha)
    except (BlobNotFoundError, BlobCorruptError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("blob %s: nested references not readable (%s)", sha, exc)
        return []
    return [ref for ref in extract(payload) if isinstance(ref, str) and _SHA_RE.match(ref)]


def referenced_shas() -> set[str]:
    """Every sha referenced by a model's ``BLOB_FIELDS`` columns or its ``iter_blob_refs()`` hook."""
    from django.apps import apps

    refs: set[str] = set()
    for model in apps.get_models():
        manager = model._default_manager  # noqa: SLF001 - Django's documented default-manager attribute
        for field_name in getattr(model, "BLOB_FIELDS", ()):
            values = manager.exclude(**{field_name: ""}).values_list(field_name, flat=True)
            refs.update(value for value in values.iterator() if value)
        hook = getattr(model, "iter_blob_refs", None)
        if callable(hook):
            refs.update(sha for sha in hook() if sha)
    return refs


def _iter_blob_files(root: Path) -> Iterator[tuple[str, Path]]:
    base = root / "sha256"
    if not base.is_dir():
        return
    for path in base.glob("*/*/*"):
        name, _, codec = path.name.partition(".")
        if codec in CODECS and _SHA_RE.match(name) and path.is_file():
            yield name, path


def gc_blobs(
    *,
    dry_run: bool = True,
    min_age: timedelta = DEFAULT_GC_AGE,
    referenced: Iterable[str] | None = None,
    now: float | None = None,
) -> GcReport:
    """Delete blob files (and rows) that nothing references and that are older than ``min_age``.

    ``referenced`` defaults to :func:`referenced_shas`. Age is the file mtime,
    which a repeated put refreshes. Stray temporary files older than
    ``min_age`` are removed too. With ``dry_run`` nothing is deleted.
    """
    root = blobs_root()
    keep = set(referenced) if referenced is not None else referenced_shas()
    cutoff = (now if now is not None else time.time()) - min_age.total_seconds()
    report = GcReport(dry_run=dry_run, referenced=len(keep))
    doomed: list[tuple[str, Path]] = []
    for sha, path in _iter_blob_files(root):
        report.scanned += 1
        if sha in keep:
            continue
        stat = path.stat()
        if stat.st_mtime > cutoff:
            continue
        report.candidates.append(sha)
        report.bytes_freed += stat.st_size
        doomed.append((sha, path))
    report.candidates.sort()
    if dry_run:
        return report
    for _sha, path in doomed:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
            report.deleted += 1
    if doomed:
        _blob_model().objects.filter(sha256__in=[sha for sha, _ in doomed]).delete()
    base = root / "sha256"
    if base.is_dir():
        for tmp in [*base.glob("*/*/.*.tmp"), *base.glob(f"{_INCOMING}/*.tmp")]:
            with contextlib.suppress(FileNotFoundError):
                if tmp.stat().st_mtime <= cutoff:
                    tmp.unlink()
    return report


def copy_to(sha: str, dest: Path | str) -> Path:
    """Write the verified content of ``sha`` to ``dest`` (streamed; parent dirs created)."""
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with open_stream(sha) as stream, target.open("wb") as out:
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != sha:
        with contextlib.suppress(OSError):
            target.unlink()
        msg = f"blob {sha} is corrupt: content hashes to {digest.hexdigest()}"
        raise BlobCorruptError(msg)
    return target
