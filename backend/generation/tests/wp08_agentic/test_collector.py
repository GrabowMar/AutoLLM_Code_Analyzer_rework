"""Collector (spec 6.8) and workspace plumbing (6.4, 6.5): origins, excludes, nested directories, LOC."""

from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from backend.generation import identity
from backend.generation.agentic import collector
from backend.generation.agentic import sandbox
from backend.generation.agentic import workspace
from backend.generation.agentic.workspace import SeedFile
from backend.generation.agentic.workspace import UnsafePathError
from backend.generation.models import RunFile
from backend.generation.tests.factories import GenerationRunFactory

PY = b"def handler(request):\n    return {'ok': True}\n"


def tree(**files: bytes) -> dict[str, SeedFile]:
    return {path.replace("__", "/"): SeedFile(data) for path, data in files.items()}


def by_path(rows: list[collector.FileRow]) -> dict[str, collector.FileRow]:
    return {row.path: row for row in rows}


def test_origins_against_the_seed() -> None:
    seed = {"backend/app.py": SeedFile(PY), "README.md": SeedFile(b"# app\n"), "gone.txt": SeedFile(b"x")}
    final = {
        "backend/app.py": SeedFile(PY + b"# changed\n"),
        "README.md": SeedFile(b"# app\n"),
        "backend/todos.py": SeedFile(PY),
    }
    rows = by_path(collector.classify_tree(final, seed))
    assert rows["backend/app.py"].origin == "modified"
    assert rows["README.md"].origin == "starter"
    assert rows["backend/todos.py"].origin == "authored"
    assert rows["gone.txt"].origin == "deleted"
    assert rows["gone.txt"].blob == ""
    assert rows["backend/todos.py"].loc == 2


def test_nested_data_dist_and_build_directories_are_source() -> None:
    final = {
        "backend/data/models.py": SeedFile(PY),
        "src/build/index.ts": SeedFile(b"export const x = 1;\n"),
        "app/dist/helpers.js": SeedFile(b"export function f() { return 1; }\n"),
        "frontend/package.json": SeedFile(b'{"name": "web"}'),
        "frontend/dist/assets/index.js": SeedFile(b"console.log(1)\n"),
        "frontend/build/x.js": SeedFile(b"1\n"),
        "data/app.db": SeedFile(b"SQLite format 3\x00"),
        "backend/__pycache__/app.cpython-313.pyc": SeedFile(b"\x00\x01"),
        "node_modules/react/index.js": SeedFile(b"module.exports = 1\n"),
        "frontend/node_modules/x/index.js": SeedFile(b"1\n"),
    }
    rows = by_path(collector.classify_tree(final, {}))
    for path in ("backend/data/models.py", "src/build/index.ts", "app/dist/helpers.js"):
        assert rows[path].origin == "authored", path
        assert rows[path].excluded == "", path
        assert rows[path].loc > 0, path
    assert rows["frontend/dist/assets/index.js"].origin == "artifact"
    assert rows["frontend/build/x.js"].origin == "artifact"
    assert (rows["data/app.db"].origin, rows["data/app.db"].excluded) == ("artifact", "data")
    assert rows["backend/__pycache__/app.cpython-313.pyc"].origin == "artifact"
    assert not any("node_modules" in path for path in rows)


def test_static_dir_filled_by_the_dockerfile_is_an_artifact() -> None:
    dockerfile = b"FROM node AS web\nRUN npm run build\nFROM python\nCOPY --from=web /app/dist /app/backend/static\n"
    final = {"Dockerfile": SeedFile(dockerfile), "backend/static/index.html": SeedFile(b"<html></html>\n")}
    rows = by_path(collector.classify_tree(final, {}))
    assert rows["backend/static/index.html"].origin == "artifact"
    hand_written = by_path(collector.classify_tree({"backend/static/site.css": SeedFile(b"a{}\n")}, {}))
    assert hand_written["backend/static/site.css"].origin == "authored"


def test_exclusion_labels() -> None:
    big_lock = b'{"lockfileVersion": 3, "packages": {}}' + b" " * (2 * 1024 * 1024)
    huge_lock = b"{}" + b" " * (9 * 1024 * 1024)
    final = {
        "frontend/package-lock.json": SeedFile(big_lock),
        "other/package-lock.json": SeedFile(huge_lock),
        "backend/big.py": SeedFile(b"x = 1\n" * 200_000),
        "assets/logo.png": SeedFile(b"\x89PNG\r\n\x1a\n\x00\x00"),
        "vendor/lib.js": SeedFile(b"var a=1;\n"),
        "static/app.min.js": SeedFile(b"var a=1;\n"),
        ".venv/lib/x.py": SeedFile(PY),
        "requirements.txt": SeedFile(b"flask==3.1.0 --hash=sha256:abc\n"),
    }
    rows = by_path(collector.classify_tree(final, {}))
    assert rows["frontend/package-lock.json"].excluded == "lockfile"  # kept up to 8 MiB (the 512 KB drop)
    assert rows["frontend/package-lock.json"].blob
    assert rows["other/package-lock.json"].excluded == "too_large"
    assert rows["backend/big.py"].excluded == "too_large"
    assert rows["assets/logo.png"].excluded == "binary"
    assert rows["vendor/lib.js"].excluded == "vendored"
    assert rows["static/app.min.js"].excluded == "vendored"
    assert rows[".venv/lib/x.py"].excluded == "non_source"
    assert rows["requirements.txt"].excluded == "lockfile"
    assert all(row.loc == 0 for row in rows.values())


def test_tree_hash_skips_deleted_artifact_and_binary_entries() -> None:
    final = {"a.py": SeedFile(PY), "data/x.db": SeedFile(b"\x00"), "logo.png": SeedFile(b"\x00\x01")}
    rows = collector.classify_tree(final, {"gone.py": SeedFile(PY)})
    assert collector.tree_hash_of(rows) == identity.tree_hash([("a.py", 0o644, identity.bytes_sha(PY))])


def test_skeleton_only_workspace_has_no_model_code() -> None:
    seed = {"backend/app.py": SeedFile(PY)}
    final = {"backend/app.py": SeedFile(PY), "notes.md": SeedFile(b"# todo\n"), "tests/test_app.py": SeedFile(PY)}
    rows = collector.classify_tree(final, seed)
    assert not any(row.is_model_code for row in rows)


@pytest.mark.django_db
def test_collect_writes_rows_blobs_and_summary(tmp_path) -> None:
    run = GenerationRunFactory.create()
    tar_path = tmp_path / "workspace.tar"
    tar_path.write_bytes(
        workspace.make_tar({"backend/app.py": SeedFile(PY + b"# v2\n"), "backend/x.py": SeedFile(PY)}),
    )
    collected = collector.collect(run, tar_path, attempt=1, seed={"backend/app.py": SeedFile(PY)})
    rows = {row.path: row for row in RunFile.objects.filter(run=run)}
    assert {path: row.origin for path, row in rows.items()} == {
        "backend/app.py": "modified",
        "backend/x.py": "authored",
    }
    assert collected.has_model_code
    assert collected.summary.by_origin == {"modified": 1, "authored": 1}
    assert collected.summary.loc_model == 4  # non-blank, non-comment source lines
    assert collected.workspace_blob
    assert collected.static["python"]["status"] == "ok"
    truncated = collector.collect(run, None, attempt=1, seed={}, workspace_truncated=True)
    assert truncated.rows == []
    assert truncated.summary.workspace_truncated


def test_sanitize_rel_path() -> None:
    assert workspace.sanitize_rel_path("./backend/app.py") == "backend/app.py"
    for bad in ("/etc/passwd", "../x", "a/../b", "a//b", "a\\b", "a/\x00b", ""):
        with pytest.raises(UnsafePathError):
            workspace.sanitize_rel_path(bad)


def test_read_tar_drops_links_and_escaping_members(tmp_path) -> None:
    raw = tmp_path / "evil.tar"
    with tarfile.open(raw, "w") as tar:
        for name in ("./ok.py", "../escape.py", "/abs.py"):
            info = tarfile.TarInfo(name)
            info.size = 2
            tar.addfile(info, __import__("io").BytesIO(b"x\n"))
        link = tarfile.TarInfo("./link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    assert set(workspace.read_tar(raw)) == {"ok.py"}


def test_make_tar_round_trip_keeps_modes() -> None:
    files = {"bin/run.sh": SeedFile(b"#!/bin/sh\n", 0o755), "a/b/c.txt": SeedFile(b"c")}
    back = workspace.read_tar(workspace.make_tar(files))
    assert back["bin/run.sh"].executable
    assert back["a/b/c.txt"].data == b"c"


def _run_entry_script(root: Path, command: str, *, max_bytes: int = 0) -> subprocess.CompletedProcess[bytes]:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(root / "tmp"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "LLMLAB_ROOT": str(root),
        "LLMLAB_WALL": "30",
        "LLMLAB_COMMAND": command,
        "LLMLAB_MAX_WORKSPACE_BYTES": str(max_bytes),
    }
    script = ["bash", "-c", sandbox.entry_script()]
    return subprocess.run(script, env=env, cwd=root, capture_output=True, check=False)  # noqa: S603


def test_entry_script_keeps_nested_directories_and_drops_anchored_ones(tmp_path) -> None:
    for name in ("workspace", "logs", "tmp"):
        (tmp_path / name).mkdir()
    (tmp_path / "workspace" / "seed.txt").write_text("seed\n")
    command = (
        'cd "$LLMLAB_ROOT/workspace" && mkdir -p backend/data src/build app/dist node_modules/x .venv/lib data '
        "&& echo m > backend/data/models.py && echo b > src/build/index.ts && echo d > app/dist/h.js "
        "&& echo n > node_modules/x/i.js && echo v > .venv/lib/v.py "
        "&& echo db > data/app.db && echo k > data/keep.json"
    )
    result = _run_entry_script(tmp_path, command)
    assert result.returncode == 0, result.stderr
    logs = tmp_path / "logs"
    assert sandbox.read_exit(logs).exit_code == 0
    members = set(workspace.read_tar(logs / "workspace.tar"))
    assert {"backend/data/models.py", "src/build/index.ts", "app/dist/h.js", "seed.txt", "data/app.db"} <= members
    assert not any(m.startswith(("node_modules/", ".venv/")) for m in members)
    patch = (logs / "changes.patch").read_text()
    assert "backend/data/models.py" in patch
    assert "data/keep.json" in patch
    assert "data/app.db" not in patch  # /data/*.db is an anchored git exclude
    assert "node_modules" not in patch


def test_entry_script_workspace_limit_writes_a_manifest(tmp_path) -> None:
    for name in ("workspace", "logs", "tmp"):
        (tmp_path / name).mkdir()
    result = _run_entry_script(
        tmp_path,
        'head -c 200000 /dev/zero > "$LLMLAB_ROOT/workspace/blob.bin"',
        max_bytes=1000,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "logs" / "workspace.tar").exists()
    assert "blob.bin" in (tmp_path / "logs" / "workspace.manifest").read_text()
