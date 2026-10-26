"""Sandbox helpers that need no Docker daemon (the container contract is in test_sandbox_docker.py)."""

from __future__ import annotations

import io
import tarfile

import pytest

from backend.analysis.engine import sandbox
from backend.analysis.engine.types import Invocation
from backend.generation.services.agent.images import _dir_hash
from backend.runtime.services import docker_manager


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("768m", 768 * 1024**2),
        ("1.5g", int(1.5 * 1024**3)),
        ("512MB", 512 * 1024**2),
        ("64k", 64 * 1024),
        ("1024", 1024),
        (" 2G ", 2 * 1024**3),
        ("3500m", 3500 * 1024**2),
        (4096, 4096),
    ],
)
def test_parse_mem(value, expected):
    assert sandbox.parse_mem(value) == expected


@pytest.mark.parametrize("value", ["", "lots", "-1m", "0", "1x", None, True])
def test_parse_mem_rejects_garbage(value):
    with pytest.raises(ValueError, match="Invalid memory size"):
        sandbox.parse_mem(value)


def test_image_tag_matches_the_generation_algorithm_and_tracks_edits(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "ruff.toml").write_text("[lint]\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"ignored")
    monkeypatch.setattr(sandbox, "IMAGE_DIR", tmp_path)
    tag = sandbox.image_tag()
    assert tag == f"llmlab/analyzer:{_dir_hash(tmp_path, {})}"
    (tmp_path / "config" / "ruff.toml").write_text("[lint]\nselect = ['F']\n")
    edited = sandbox.image_tag()
    assert edited != tag
    assert edited == f"llmlab/analyzer:{_dir_hash(tmp_path, {})}"


def test_image_tag_requires_a_definition(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "IMAGE_DIR", tmp_path / "missing")
    with pytest.raises(sandbox.SandboxError, match="definition is missing"):
        sandbox.image_tag()


def test_the_real_image_definition_has_a_tag():
    if not (sandbox.IMAGE_DIR / "Dockerfile").is_file():
        pytest.skip("analyzer image definition not written yet")
    assert sandbox.image_tag().startswith("llmlab/analyzer:")


class _Images:
    def __init__(self, existing: dict[str, str]) -> None:
        self.existing = existing

    def get(self, ref):
        import docker

        if ref not in self.existing:
            raise docker.errors.ImageNotFound(ref)
        return type("Image", (), {"id": self.existing[ref]})()


class _Client:
    def __init__(self, existing: dict[str, str]) -> None:
        self.images = _Images(existing)


def test_ensure_image_with_a_changed_definition(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    monkeypatch.setattr(sandbox, "IMAGE_DIR", tmp_path)
    frozen_tag = "llmlab/analyzer:000000000000"
    # The frozen image is gone and cannot be rebuilt from the current definition.
    monkeypatch.setattr(sandbox, "_client", lambda: _Client({}))
    with pytest.raises(sandbox.ImageUnavailable, match="changed since this plan was frozen"):
        sandbox.ensure_image(expect_tag=frozen_tag)
    # Still on the daemon: it is exactly the frozen toolchain, so it is used.
    monkeypatch.setattr(sandbox, "_client", lambda: _Client({frozen_tag: "sha256:old"}))
    assert sandbox.ensure_image(expect_tag=frozen_tag) == (frozen_tag, "sha256:old")


@pytest.mark.parametrize("path", ["", "/etc/passwd", "../x.py", "a/../b.py", "a//b.py", "./a.py", "a\\b.py"])
def test_check_path_rejects_unsafe_paths(path):
    with pytest.raises(ValueError, match="Unsafe"):
        sandbox.check_path(path)


def test_check_path_accepts_relative_paths():
    sandbox.check_path("backend/app.py")
    sandbox.check_path(".env")


def test_make_tar_has_directories_owners_and_modes():
    data = sandbox._make_tar({"a/b/c.py": b"x", "d.txt": b"yy"}, uid=10001)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = {m.name: m for m in tar.getmembers()}
    assert set(members) == {"a", "a/b", "a/b/c.py", "d.txt"}
    assert members["a"].isdir()
    assert members["a"].mode == 0o755
    assert members["a/b/c.py"].mode == 0o644
    assert {m.uid for m in members.values()} == {10001}


def _tar(entries) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, payload, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "sym":
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                tar.addfile(info)
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def _chunks(data: bytes, size: int = 7):
    return (data[i : i + size] for i in range(0, len(data), size))


def test_read_archive_keeps_regular_files_only():
    data = _tar(
        [
            ("out", b"", "dir"),
            ("out/report.json", b"{}", "file"),
            ("out/../escape.txt", b"no", "file"),
            ("/abs.txt", b"no", "file"),
            ("out/link", "/etc/passwd", "sym"),
            ("./out/dot.txt", b"dot", "file"),
        ],
    )
    files, truncated = sandbox._read_archive(_chunks(data), cap=10**6)
    assert files == {"out/report.json": b"{}", "out/dot.txt": b"dot"}
    assert not truncated


def test_read_archive_stops_at_the_cap():
    data = _tar([("out/a.txt", b"a" * 100, "file"), ("out/b.txt", b"b" * 50_000, "file")])
    files, truncated = sandbox._read_archive(_chunks(data, 512), cap=20_000)
    assert truncated
    assert files == {"out/a.txt": b"a" * 100}


def test_read_archive_of_garbage_is_truncated_not_raised():
    files, truncated = sandbox._read_archive([b"this is not a tar stream" * 40], cap=10**6)
    assert files == {}
    assert truncated


def test_tool_version_shapes():
    assert sandbox.tool_version({"tools": {"ruff": {"version": "0.14.0"}}}, "ruff") == "0.14.0"
    assert sandbox.tool_version({"tools": {"ruff": "0.14.0 "}}, "ruff") == "0.14.0"
    assert sandbox.tool_version({"tools": [{"slug": "ruff", "version": "1"}]}, "ruff") == "1"
    assert sandbox.tool_version({"tools": [{"name": "ruff", "tool_version": "2"}]}, "ruff") == "2"
    assert sandbox.tool_version({"tools": {}}, "ruff") == ""
    assert sandbox.tool_version(None, "ruff") == ""


def test_manifest_digest_drops_free_text():
    manifest = {
        "schema_version": 1,
        "built_at": "2026-10-22T00:00:00+00:00",
        "runtime": {"python": "3.12.11"},
        "configs": {"ruff.toml": "c" * 64},
        "tools": {
            "ruff": {"version": "0.14.0", "version_output": "ruff 0.14.0", "lock_sha256": "l" * 64},
            "semgrep": {"version": "1.140.0", "rules": {"sha256": "r" * 64, "rules_loaded": 812, "files": ["x"]}},
            "odd": "1.0",
        },
    }
    digest = sandbox.manifest_digest(manifest)
    assert digest["tools"]["ruff"] == {"version": "0.14.0", "lock_sha256": "l" * 64}
    assert digest["tools"]["semgrep"] == {"version": "1.140.0", "rules": {"sha256": "r" * 64, "rules_loaded": 812}}
    assert digest["tools"]["odd"] == {"version": "1.0"}
    assert digest["configs"] == {"ruff.toml": "c" * 64}
    assert sandbox.manifest_digest(None) is None


def test_run_tool_reports_an_unreachable_daemon_as_infra_error(monkeypatch):
    def boom():
        msg = "connection refused"
        raise ConnectionError(msg)

    monkeypatch.setattr(sandbox._local, "client", None, raising=False)
    monkeypatch.setattr(docker_manager, "new_client", boom)
    outcome = sandbox.run_tool(
        image="img",
        volume="vol",
        invocation=Invocation(argv=["true"]),
        run_id="r",
        tool_slug="ruff",
    )
    assert "Docker daemon unavailable" in outcome.infra_error
    assert outcome.exit_code is None


class _FakeContainer:
    """Just enough of a docker Container for run_tool: exits at once, /out comes from a prepared tar."""

    def __init__(self, out_tar: bytes) -> None:
        self.name = "llmlab-an-fake"
        self.attrs: dict = {"Image": "sha256:" + "ab" * 32, "State": {}}
        self.out_tar = out_tar
        self.removed = False

    def put_archive(self, path, data):
        return True

    def start(self):
        self.attrs["State"] = {"ExitCode": 1, "OOMKilled": False}

    def wait(self, timeout=None):
        return {"StatusCode": 1}

    def reload(self):
        return None

    def logs(self, *, stdout, stderr, stream, follow):
        return iter([b"stdout" if stdout else b"stderr"])

    def get_archive(self, path):
        return _chunks(self.out_tar, 512), {}

    def remove(self, *, force=False, v=False):
        self.removed = True


class _FakeContainers:
    def __init__(self, container: _FakeContainer) -> None:
        self.container = container

    def create(self, image, **kwargs):
        return self.container


def _run_fake_tool(monkeypatch, out_tar: bytes):
    container = _FakeContainer(out_tar)
    client = type("Client", (), {"containers": _FakeContainers(container)})()
    monkeypatch.setattr(sandbox, "_client", lambda: client)
    outcome = sandbox.run_tool(
        image="img",
        volume="vol",
        invocation=Invocation(argv=["ruff"], inputs={"files.txt": b"app.py"}),
        run_id="r",
        tool_slug="ruff",
    )
    assert container.removed
    return outcome


def test_run_tool_collects_out_files_and_flags_a_capped_out_dir(monkeypatch, settings):
    small = _tar([("out", b"", "dir"), ("out/report.json", b"[]", "file")])
    outcome = _run_fake_tool(monkeypatch, small)
    assert (outcome.exit_code, outcome.stdout, outcome.stderr) == (1, b"stdout", b"stderr")
    assert outcome.out_files == {"report.json": b"[]"}
    assert outcome.out_truncated is False
    assert outcome.infra_error is None

    settings.ANALYSIS_OUT_DIR_CAP_BYTES = 20_000
    large = _tar([("out/report.json", b"[]", "file"), ("out/huge.txt", b"x" * 50_000, "file")])
    outcome = _run_fake_tool(monkeypatch, large)
    # Only completely read files are kept, and the cap is reported, never silently dropped.
    assert outcome.out_files == {"report.json": b"[]"}
    assert outcome.out_truncated is True


def test_image_reference_pins_the_tag_to_the_image_id():
    assert sandbox.image_reference("llmlab/analyzer:abc", "sha256:1f") == "llmlab/analyzer:abc@sha256:1f"
    assert sandbox.image_reference("llmlab/analyzer:abc", "") == "llmlab/analyzer:abc"
    assert sandbox.image_reference("", "sha256:1f") == "sha256:1f"


def test_run_tool_short_circuits_when_already_cancelled(monkeypatch):
    monkeypatch.setattr(sandbox, "_client", lambda: pytest.fail("must not create a container"))
    outcome = sandbox.run_tool(
        image="img",
        volume="vol",
        invocation=Invocation(argv=["true"]),
        run_id="r",
        tool_slug="ruff",
        is_cancelled=lambda: True,
    )
    assert outcome.cancelled
    assert outcome.exit_code is None


class _VolumeDaemon:
    """Records the order of create/remove calls for run_volume."""

    def __init__(self, *, refuse_archive: bool = False) -> None:
        self.events: list[str] = []
        self.refuse_archive = refuse_archive
        daemon = self

        class _Helper:
            name = "helper"

            def put_archive(self, path, data):
                daemon.events.append("fill")
                return not daemon.refuse_archive

            def remove(self, *, force=False, v=False):
                daemon.events.append("remove helper")

        class _Volume:
            def remove(self, *, force=False):
                daemon.events.append("remove volume")

        class _Containers:
            def create(self, image, **kwargs):
                daemon.events.append(f"create helper from {image}")
                return _Helper()

        class _Volumes:
            def create(self, *, name, labels):
                daemon.events.append("create volume")

            def get(self, name):
                return _Volume()

        self.containers = _Containers()
        self.volumes = _Volumes()


def test_run_volume_pins_the_image_for_the_whole_block(monkeypatch):
    """A never-started container using the image keeps the daemon's prune off it between two tools."""
    daemon = _VolumeDaemon()
    monkeypatch.setattr(sandbox, "_client", lambda: daemon)
    with sandbox.run_volume("run-1", {"app.py": b"x = 1\n"}, image="sha256:img") as volume:
        assert volume.startswith("llmlab-an-run1-")
        assert daemon.events == ["create volume", "create helper from sha256:img", "fill"]
        daemon.events.append("tools run")
    assert daemon.events[-3:] == ["tools run", "remove helper", "remove volume"]


def test_run_volume_removes_the_helper_when_filling_fails(monkeypatch):
    daemon = _VolumeDaemon(refuse_archive=True)
    monkeypatch.setattr(sandbox, "_client", lambda: daemon)
    with pytest.raises(sandbox.SandboxError, match="refused"), sandbox.run_volume("run-1", {"a.py": b""}, image="i"):
        pytest.fail("must not yield")
    assert daemon.events == ["create volume", "create helper from i", "fill", "remove helper", "remove volume"]
