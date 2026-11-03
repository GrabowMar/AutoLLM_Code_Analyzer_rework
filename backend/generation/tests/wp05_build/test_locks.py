"""Starter lockfiles: the checked-in index, staleness and generation (``build_starters --lock``)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
import yaml

from backend.generation import identity
from backend.generation.build import locks


@pytest.fixture
def fragment_root(tmp_path) -> Path:
    root = tmp_path / "starters"
    (root / "backend/flask/minimal/backend").mkdir(parents=True)
    (root / "backend/flask/minimal/backend/requirements.txt").write_text("flask==3.1.3\n")
    (root / "backend/flask/minimal/backend/app.py.jinja2").write_text("x\n")
    (root / "frontend/vue/minimal/frontend").mkdir(parents=True)
    (root / "frontend/vue/minimal/frontend/package.json").write_text('{"dependencies": {"vue": "3"}}\n')
    return root


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str, list]] = []

    def run(self, image, files, script, outputs):
        self.calls.append((image, dict(files), script, list(outputs)))
        return {name: b"locked:" + next(iter(files.values())) for name in outputs}


def test_every_checked_in_lock_matches_its_manifest():
    index = locks.lock_index()
    assert index
    for key, entry in index.items():
        fragment, manifest = key.split(":", 1)
        manifest_bytes = (locks.dockerfiles.STARTERS_ROOT / fragment / manifest).read_bytes()
        assert entry["manifest_sha"] == identity.bytes_sha(manifest_bytes), key
        lock = locks.LOCK_ROOT / fragment / entry["lock"]
        assert entry["lock_sha"] == identity.bytes_sha(lock.read_bytes()), key
        assert "@sha256:" in entry["image"]


def test_python_locks_are_hashed_and_npm_locks_resolve_every_package():
    for key, entry in locks.lock_index().items():
        text = (locks.LOCK_ROOT / key.split(":", 1)[0] / entry["lock"]).read_text()
        if entry["lock"].endswith("requirements.lock"):
            assert "--hash=sha256:" in text
            assert "--generate-hashes" in entry["tool"]
        else:
            assert '"lockfileVersion": 3' in text
            assert '"integrity": "sha512-' in text


def test_lock_specs_find_plain_manifests_only(fragment_root):
    specs = locks.lock_specs(["backend/flask/minimal", "frontend/vue/minimal", "missing"], root=fragment_root)
    assert [(s.key, s.lock, s.ecosystem.name) for s in specs] == [
        ("backend/flask/minimal:backend/requirements.txt", "backend/requirements.lock", "pypi"),
        ("frontend/vue/minimal:frontend/package.json", "frontend/package-lock.json", "npm"),
    ]


def test_generate_locks_writes_locks_and_index_then_skips_fresh(fragment_root, tmp_path):
    lock_root = tmp_path / "lock"
    runner = FakeRunner()
    fragments = ["backend/flask/minimal", "frontend/vue/minimal"]
    records = locks.generate_locks(fragments, runner, root=fragment_root, lock_root=lock_root)
    assert [r["status"] for r in records] == ["locked", "locked"]
    image, files, script, outputs = runner.calls[0]
    assert image.startswith("python:3.13-slim@sha256:")
    assert files == {"requirements.txt": b"flask==3.1.3\n"}
    assert "uv pip compile" in script
    assert "--generate-hashes" in script
    assert outputs == ["requirements.lock"]
    assert runner.calls[1][0].startswith("node:24-slim@sha256:")
    assert "--package-lock-only" in runner.calls[1][2]
    assert (lock_root / "backend/flask/minimal/backend/requirements.lock").read_bytes() == b"locked:flask==3.1.3\n"
    index = yaml.safe_load((lock_root / "index.yaml").read_text())["locks"]
    assert set(index) == {
        f"{f}:{m}" for f, m in [(fragments[0], "backend/requirements.txt"), (fragments[1], "frontend/package.json")]
    }

    again = locks.generate_locks(fragments, runner, root=fragment_root, lock_root=lock_root)
    assert [r["status"] for r in again] == ["fresh", "fresh"]
    assert len(runner.calls) == 2

    out = locks.locks_for_fragment("backend/flask/minimal", root=fragment_root, lock_root=lock_root)
    assert out == {"backend/requirements.lock": b"locked:flask==3.1.3\n"}

    (fragment_root / "backend/flask/minimal/backend/requirements.txt").write_text("flask==3.1.4\n")
    with pytest.raises(locks.StaleLockError):
        locks.locks_for_fragment("backend/flask/minimal", root=fragment_root, lock_root=lock_root)
    relocked = locks.generate_locks(fragments, runner, root=fragment_root, lock_root=lock_root)
    assert [r["status"] for r in relocked] == ["locked", "fresh"]
    assert locks.locks_for_fragment("backend/flask/minimal", root=fragment_root, lock_root=lock_root)

    (lock_root / "backend/flask/minimal/backend/requirements.lock").write_text("tampered\n")
    with pytest.raises(locks.StaleLockError, match="differs"):
        locks.locks_for_fragment("backend/flask/minimal", root=fragment_root, lock_root=lock_root)


def test_missing_lock_is_an_error(fragment_root, tmp_path):
    with pytest.raises(locks.MissingLockError):
        locks.locks_for_fragment("backend/flask/minimal", root=fragment_root, lock_root=tmp_path / "none")


def test_phase_b_ecosystems_are_skipped(tmp_path):
    root = tmp_path / "starters"
    (root / "backend/go/minimal").mkdir(parents=True)
    (root / "backend/go/minimal/go.mod").write_text("module app\n")
    runner = FakeRunner()
    records = locks.generate_locks(["backend/go/minimal"], runner, root=root, lock_root=tmp_path / "lock")
    assert records == [{"key": "backend/go/minimal:go.mod", "status": "skipped", "reason": "phase B"}]
    assert runner.calls == []


class _FakeContainer:
    def __init__(self, status: int, outputs: dict[str, bytes]) -> None:
        self.status = status
        self.outputs = outputs
        self.put: list[tuple[str, bytes]] = []
        self.removed = False
        self.started = False
        self.id = "c1"

    def put_archive(self, path, data):
        self.put.append((path, data))

    def start(self):
        self.started = True

    def wait(self, timeout):
        return {"StatusCode": self.status}

    def logs(self):
        return b"resolver output"

    def get_archive(self, path):
        name = path.rsplit("/", 1)[-1]
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            info = tarfile.TarInfo(name)
            info.size = len(self.outputs[name])
            tar.addfile(info, io.BytesIO(self.outputs[name]))
        return [buffer.getvalue()], {}

    def remove(self, force):
        self.removed = True


class _FakeClient:
    def __init__(self, container) -> None:
        self.container = container
        self.pulled: list[str] = []
        self.created: dict = {}
        self.images = self
        self.containers = self

    def pull(self, image):
        self.pulled.append(image)

    def create(self, image, **kwargs):
        self.created = {"image": image, **kwargs}
        return self.container


def test_docker_lock_runner_copies_files_in_and_out_and_always_removes():
    container = _FakeContainer(0, {"requirements.lock": b"flask==3.1.3\n"})
    client = _FakeClient(container)
    runner = locks.DockerLockRunner(client)
    out = runner.run(
        "python:3.13-slim@sha256:" + "a" * 64,
        {"requirements.txt": b"flask\n"},
        "true",
        ["requirements.lock"],
    )
    assert out == {"requirements.lock": b"flask==3.1.3\n"}
    assert container.started
    assert container.removed
    with tarfile.open(fileobj=io.BytesIO(container.put[0][1])) as tar:
        assert tar.getnames() == ["work/requirements.txt"]
    assert client.created["labels"] == {"llmlab.purpose": "starter-lock"}
    assert "volumes" not in client.created  # no bind mounts: works on a remote daemon

    failing = _FakeContainer(1, {})
    with pytest.raises(locks.LockError, match="exit 1"):
        locks.DockerLockRunner(_FakeClient(failing)).run("img", {"a": b""}, "false", ["x"])
    assert failing.removed
