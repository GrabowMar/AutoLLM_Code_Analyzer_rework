"""Content-addressed blob store (spec section 3.2). No database: the Blob model is replaced by a fake."""

from __future__ import annotations

import gzip
import hashlib
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.generation import blobs
from backend.generation import identity


class _FakeQuerySet:
    def __init__(self, manager: _FakeManager, shas: list[str]):
        self.manager = manager
        self.shas = shas

    def delete(self) -> tuple[int, dict[str, int]]:
        removed = [sha for sha in self.shas if self.manager.rows.pop(sha, None) is not None]
        return len(removed), {"generation.Blob": len(removed)}


class _FakeManager:
    def __init__(self):
        self.rows: dict[str, dict[str, Any]] = {}
        self.calls = 0

    def get_or_create(self, *, sha256: str, defaults: dict[str, Any]):
        self.calls += 1
        if sha256 in self.rows:
            return self.rows[sha256], False
        self.rows[sha256] = {"sha256": sha256, **defaults}
        return self.rows[sha256], True

    def filter(self, *, sha256__in: list[str]) -> _FakeQuerySet:
        return _FakeQuerySet(self, list(sha256__in))


class FakeBlob:
    objects = _FakeManager()


@pytest.fixture
def blob_root(settings, tmp_path, monkeypatch) -> Path:
    settings.MEDIA_ROOT = str(tmp_path / "media")
    if hasattr(settings, "GENERATION_BLOBS_ROOT"):
        settings.GENERATION_BLOBS_ROOT = None
    FakeBlob.objects = _FakeManager()
    monkeypatch.setattr(blobs, "_blob_model", lambda: FakeBlob)
    return Path(settings.MEDIA_ROOT) / "blobs"


@pytest.fixture
def gzip_only(monkeypatch):
    monkeypatch.setattr(blobs, "_zstd", lambda: None)


def test_root_follows_media_root_at_call_time(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / "one")
    settings.GENERATION_BLOBS_ROOT = None
    assert blobs.blobs_root() == tmp_path / "one" / "blobs"
    settings.MEDIA_ROOT = str(tmp_path / "two")
    assert blobs.blobs_root() == tmp_path / "two" / "blobs"
    settings.GENERATION_BLOBS_ROOT = str(tmp_path / "explicit")
    assert blobs.blobs_root() == tmp_path / "explicit"


def test_put_get_round_trip_and_layout(blob_root):
    data = b"hello blob store\x00\xff"
    sha = blobs.put_bytes(data, "application/octet-stream")
    assert sha == hashlib.sha256(data).hexdigest()
    codec = blobs.preferred_codec()
    path = blob_root / "sha256" / sha[:2] / sha[2:4] / f"{sha}.{codec}"
    assert path.is_file()
    assert blobs.blob_path(sha, codec) == path
    assert blobs.get_bytes(sha) == data
    assert blobs.exists(sha)
    row = FakeBlob.objects.rows[sha]
    assert row == {"sha256": sha, "size": len(data), "codec": codec, "media_type": "application/octet-stream"}


def test_put_is_idempotent_and_refreshes_mtime(blob_root):
    sha = blobs.put_bytes(b"same")
    path, _ = blobs.find_blob_file(sha)
    old = time.time() - 30 * 86400
    os.utime(path, (old, old))
    content_before = path.read_bytes()
    assert blobs.put_bytes(b"same") == sha
    assert path.read_bytes() == content_before
    assert path.stat().st_mtime > old + 86400
    assert len(FakeBlob.objects.rows) == 1
    assert FakeBlob.objects.calls == 2  # get_or_create is safe to repeat


def test_record_false_skips_the_row(blob_root):
    sha = blobs.put_bytes(b"no row", record=False)
    assert blobs.exists(sha)
    assert sha not in FakeBlob.objects.rows


def test_put_text_normalizes_like_text_sha(blob_root):
    text = "line one\r\ncafé\r"
    sha = blobs.put_text(text)
    assert sha == identity.text_sha(text)
    assert blobs.get_text(sha) == "line one\ncafé\n"
    assert FakeBlob.objects.rows[sha]["media_type"] == blobs.TEXT_MEDIA_TYPE


def test_put_json_sha_equals_h(blob_root):
    obj = {"b": [1, 2], "a": "x\r\n"}
    sha = blobs.put_json(obj)
    assert sha == identity.H(obj)
    assert blobs.get_json(sha) == {"a": "x\n", "b": [1, 2]}


def test_put_file_streams_and_matches_put_bytes(blob_root, tmp_path):
    source = tmp_path / "workspace.tar"
    payload = os.urandom(3 * 1024 * 1024 + 17)
    source.write_bytes(payload)
    sha = blobs.put_file(source, "application/x-tar")
    assert sha == hashlib.sha256(payload).hexdigest()
    assert blobs.get_bytes(sha) == payload
    assert FakeBlob.objects.rows[sha]["size"] == len(payload)
    assert blobs.put_bytes(payload) == sha  # same content, same blob
    assert not list((blob_root / "sha256" / ".incoming").glob("*"))  # staging cleaned up


def test_gzip_codec_when_zstandard_is_missing(blob_root, gzip_only):
    sha = blobs.put_bytes(b"gzip me")
    path, codec = blobs.find_blob_file(sha)
    assert codec == "gz"
    assert path.suffix == ".gz"
    assert gzip.decompress(path.read_bytes()) == b"gzip me"
    assert FakeBlob.objects.rows[sha]["codec"] == "gz"
    assert blobs.get_bytes(sha) == b"gzip me"


def test_gzip_output_is_deterministic(blob_root, gzip_only, tmp_path):
    first = blobs.put_bytes(b"deterministic", record=False)
    path, _ = blobs.find_blob_file(first)
    content = path.read_bytes()
    path.unlink()
    blobs.put_bytes(b"deterministic", record=False)
    assert path.read_bytes() == content


def test_reader_accepts_either_codec(blob_root, gzip_only, monkeypatch):
    sha = blobs.put_bytes(b"written as gzip")
    monkeypatch.undo()  # back to the default codec preference
    monkeypatch.setattr(blobs, "_blob_model", lambda: FakeBlob)
    assert blobs.get_bytes(sha) == b"written as gzip"
    assert blobs.put_bytes(b"written as gzip") == sha
    assert blobs.find_blob_file(sha)[1] == "gz"  # an existing blob is never rewritten in another codec


@pytest.mark.skipif(blobs._zstd() is None, reason="zstandard is not installed in this environment")
def test_zstd_codec(blob_root):
    sha = blobs.put_bytes(b"zstd me" * 100)
    path, codec = blobs.find_blob_file(sha)
    assert codec == "zst"
    assert path.suffix == ".zst"
    assert blobs.get_bytes(sha) == b"zstd me" * 100


def test_open_stream(blob_root):
    sha = blobs.put_bytes(b"x" * 10_000)
    with blobs.open_stream(sha) as stream:
        assert stream.read(5) == b"xxxxx"
        assert len(stream.read()) == 9_995


def test_missing_blob(blob_root):
    sha = "f" * 64
    assert not blobs.exists(sha)
    with pytest.raises(blobs.BlobNotFoundError):
        blobs.get_bytes(sha)
    with pytest.raises(blobs.BlobNotFoundError):
        blobs.open_stream(sha)


@pytest.mark.parametrize("sha", ["", "F" * 64, "a" * 63, "../" + "a" * 61])
def test_invalid_sha_rejected(blob_root, sha):
    with pytest.raises(ValueError, match="sha256"):
        blobs.exists(sha)
    with pytest.raises(ValueError, match="sha256"):
        blobs.blob_path(sha, "gz")


def test_corruption_detected(blob_root, gzip_only):
    sha = blobs.put_bytes(b"original")
    path, _ = blobs.find_blob_file(sha)
    path.write_bytes(gzip.compress(b"tampered"))
    with pytest.raises(blobs.BlobCorruptError):
        blobs.get_bytes(sha)
    with pytest.raises(blobs.BlobCorruptError):
        blobs.verify(sha)


def test_copy_to(blob_root, tmp_path):
    sha = blobs.put_bytes(b"copy me")
    target = blobs.copy_to(sha, tmp_path / "out" / "file.bin")
    assert target.read_bytes() == b"copy me"


def test_atomic_write_leaves_no_partial_file(blob_root, monkeypatch):
    def boom(codec, sink):
        msg = "disk full"
        raise RuntimeError(msg)

    monkeypatch.setattr(blobs, "_compressor", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        blobs.put_bytes(b"never stored")
    sha = hashlib.sha256(b"never stored").hexdigest()
    assert not blobs.exists(sha)
    assert sha not in FakeBlob.objects.rows
    assert not [p for p in blob_root.rglob("*") if p.is_file()]


def test_put_bytes_rejects_text(blob_root):
    with pytest.raises(TypeError):
        blobs.put_bytes("text")  # type: ignore[arg-type]


def test_gc_deletes_only_old_unreferenced_blobs(blob_root):
    keep = blobs.put_bytes(b"referenced")
    old_orphan = blobs.put_bytes(b"old orphan")
    new_orphan = blobs.put_bytes(b"new orphan")
    long_ago = time.time() - 30 * 86400
    for sha in (keep, old_orphan):
        path, _ = blobs.find_blob_file(sha)
        os.utime(path, (long_ago, long_ago))

    dry = blobs.gc_blobs(dry_run=True, referenced={keep})
    assert dry.candidates == [old_orphan]
    assert dry.scanned == 3
    assert dry.deleted == 0
    assert blobs.exists(old_orphan)

    report = blobs.gc_blobs(dry_run=False, referenced={keep})
    assert report.deleted == 1
    assert not blobs.exists(old_orphan)
    assert old_orphan not in FakeBlob.objects.rows
    assert blobs.exists(keep)
    assert blobs.exists(new_orphan)


def test_gc_respects_min_age_and_cleans_stale_tmp_files(blob_root):
    sha = blobs.put_bytes(b"young")
    stale = blob_root / "sha256" / sha[:2] / sha[2:4] / f".{sha}.zst.abc.tmp"
    stale.write_bytes(b"partial")
    long_ago = time.time() - 30 * 86400
    os.utime(stale, (long_ago, long_ago))
    report = blobs.gc_blobs(dry_run=False, referenced=set(), min_age=timedelta(days=7))
    assert report.deleted == 0
    assert blobs.exists(sha)
    assert not stale.exists()
    report = blobs.gc_blobs(dry_run=False, referenced=set(), min_age=timedelta(0), now=time.time() + 1)
    assert report.deleted == 1


def test_default_setting_tracks_media_root(settings, tmp_path):
    default = getattr(settings, "GENERATION_BLOBS_ROOT", None)
    if isinstance(default, str) and default:
        pytest.skip("GENERATION_BLOBS_ROOT is pinned by the environment")
    settings.MEDIA_ROOT = str(tmp_path / "m")
    assert blobs.blobs_root() == tmp_path / "m" / "blobs"
