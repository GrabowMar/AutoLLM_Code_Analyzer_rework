"""Run artifacts: gzip + sha256 storage, never addressable by run id alone (engine/artifacts.py)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from backend.analysis.engine import artifacts


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.ANALYSIS_ARTIFACTS_DIR = "analysis_runs"
    settings.ANALYSIS_ARTIFACTS_ROOT = ""
    settings.SECRET_KEY = "test-secret"  # noqa: S105 - a test key


def test_store_and_load_roundtrip_compressed(tmp_path):
    data = b'{"results": []}\n' * 100
    entry = artifacts.store("run-1", "bandit", "stdout.txt", data)
    assert re.fullmatch(r"analysis_runs/run-1-[0-9a-f]{32}/bandit/stdout\.txt\.gz", entry["path"])
    assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert entry["bytes"] == len(data)
    assert entry["compressed"] is True
    assert entry["stored_bytes"] < len(data)
    assert (tmp_path / entry["path"]).is_file()
    assert artifacts.load(entry) == data


def test_store_uncompressed_and_identical_bytes_for_identical_content():
    a = artifacts.store("run-1", "x", "a.bin", b"abc", compress=False)
    assert a["path"].endswith("/x/a.bin")
    assert artifacts.load(a) == b"abc"
    first = artifacts.store("run-1", "y", "b.txt", b"same")
    blob = (Path(artifacts.run_dir("run-1")).parent.parent / first["path"]).read_bytes()
    second = artifacts.store("run-1", "y", "b.txt", b"same")
    assert (Path(artifacts.run_dir("run-1")).parent.parent / second["path"]).read_bytes() == blob


def test_names_are_sanitized_and_cannot_escape(tmp_path):
    entry = artifacts.store("../../etc", "../../x/./sub", "../passwd", b"x")
    resolved = (tmp_path / entry["path"]).resolve()
    assert resolved.is_relative_to(tmp_path / "analysis_runs")
    assert ".." not in entry["path"].split("/")
    assert artifacts.load(entry) == b"x"


def test_load_detects_tampering(tmp_path):
    entry = artifacts.store("run-1", "ruff", "stdout.txt", b"original", compress=False)
    (tmp_path / entry["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="sha256"):
        artifacts.load(entry)


def test_load_rejects_bad_entries(tmp_path):
    with pytest.raises(ValueError, match="no path"):
        artifacts.load({})
    with pytest.raises(ValueError, match="escapes"):
        artifacts.load({"path": "../outside.txt", "sha256": "x"})
    entry = artifacts.store("run-1", "ruff", "r.txt", b"data")
    (tmp_path / entry["path"]).write_bytes(b"not gzip")
    with pytest.raises(ValueError, match="gzip"):
        artifacts.load(entry)
    with pytest.raises(FileNotFoundError):
        artifacts.load({"path": "analysis_runs/none.txt", "sha256": "x"})


def test_delete_scope_and_run():
    keep = artifacts.store("run-1", "ruff", "stdout.txt", b"a")
    artifacts.store("run-1", "bandit", "stdout.txt", b"b")
    artifacts.delete_scope("run-1", "bandit")
    assert not (artifacts.run_dir("run-1") / "bandit").exists()
    assert artifacts.load(keep) == b"a"
    artifacts.delete_run("run-1")
    assert not artifacts.run_dir("run-1").exists()


def test_a_run_id_alone_does_not_name_its_artifacts(settings, tmp_path):
    """MEDIA_ROOT is served publicly at /media/: the run directory carries an HMAC of the run id."""
    entry = artifacts.store("run-1", "ai_review/sample-0", "transcript.json", b"source code")
    assert not (tmp_path / "analysis_runs" / "run-1").exists()
    key = entry["path"].split("/")[1].removeprefix("run-1-")
    assert len(key) == 32
    assert key not in artifacts.store("run-2", "x", "y", b"z")["path"]
    # Stable per run (later stores land in the same directory) but tied to SECRET_KEY.
    assert artifacts.store("run-1", "ruff", "stdout.txt", b"a")["path"].split("/")[1] == f"run-1-{key}"
    settings.SECRET_KEY = "another-secret"  # noqa: S105 - a test key
    assert artifacts.run_dir("run-1").name != f"run-1-{key}"


def test_a_private_root_keeps_artifacts_out_of_media(settings, tmp_path):
    private = tmp_path / "private"
    settings.MEDIA_ROOT = str(tmp_path / "media")
    old = artifacts.store("run-1", "ruff", "stdout.txt", b"written under media")
    settings.ANALYSIS_ARTIFACTS_ROOT = str(private)
    new = artifacts.store("run-1", "bandit", "stdout.txt", b"written privately")
    assert (private / new["path"]).is_file()
    assert not (tmp_path / "media" / new["path"]).exists()
    assert artifacts.load(new) == b"written privately"
    # Entries from before the private root still load from MEDIA_ROOT.
    assert artifacts.load(old) == b"written under media"
    with pytest.raises(ValueError, match="escapes"):
        artifacts.load({"path": "../media/x", "sha256": "x"})
    artifacts.delete_scope("run-1", "ruff")
    with pytest.raises(FileNotFoundError):
        artifacts.load(old)
    artifacts.delete_run("run-1")
    with pytest.raises(FileNotFoundError):
        artifacts.load(new)


def test_delete_run_finds_directories_of_a_rotated_secret_and_the_unkeyed_layout(settings, tmp_path):
    before = artifacts.store("run-1", "ruff", "stdout.txt", b"a")
    legacy = tmp_path / "analysis_runs" / "run-1" / "ruff"
    legacy.mkdir(parents=True)
    (legacy / "stdout.txt.gz").write_bytes(b"x")
    other = artifacts.store("run-10", "ruff", "stdout.txt", b"b")
    settings.SECRET_KEY = "rotated"  # noqa: S105 - a test key
    artifacts.store("run-1", "ruff", "stdout.txt", b"c")
    artifacts.delete_run("run-1")
    assert not (tmp_path / before["path"]).exists()
    assert not legacy.exists()
    assert not artifacts.run_dir("run-1").exists()
    assert (tmp_path / other["path"]).is_file()
