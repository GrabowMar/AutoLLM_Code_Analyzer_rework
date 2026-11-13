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
        spec = next(s for s in locks.lock_specs([fragment]) if s.manifest == manifest)
        assert entry.get("inputs_sha") == spec.inputs_sha(), key
        lock = locks.LOCK_ROOT / fragment / entry["lock"]
        assert entry["lock_sha"] == identity.bytes_sha(lock.read_bytes()), key
        assert "@sha256:" in entry["image"]


def test_python_locks_are_hashed_and_npm_locks_resolve_every_package():
    seen = set()
    for key, entry in locks.lock_index().items():
        text = (locks.LOCK_ROOT / key.split(":", 1)[0] / entry["lock"]).read_text()
        if entry["lock"].endswith("requirements.lock"):
            assert "--hash=sha256:" in text
            assert "--generate-hashes" in entry["tool"]
            seen.add("pypi")
        elif entry["lock"].endswith("go.sum"):
            assert " h1:" in text
            assert "go mod tidy" in entry["tool"]
            assert entry["image"].startswith("golang:1.25-")
            seen.add("go")
        elif entry["lock"].endswith("dependency-list.txt"):
            assert "The following files have been resolved:" in text
            assert "dependency:list" in entry["tool"]
            seen.add("maven")
        else:
            assert '"lockfileVersion": 3' in text
            assert '"integrity": "sha512-' in text
            seen.add("npm")
    assert seen == {"pypi", "npm", "go", "maven"}


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


def test_phase_b_ecosystems_are_skipped_when_not_requested(tmp_path):
    root = tmp_path / "starters"
    (root / "backend/go/minimal").mkdir(parents=True)
    (root / "backend/go/minimal/go.mod").write_text("module app\n")
    runner = FakeRunner()
    records = locks.generate_locks(
        ["backend/go/minimal"],
        runner,
        root=root,
        lock_root=tmp_path / "lock",
        phases=("A",),
    )
    assert records == [{"key": "backend/go/minimal:go.mod", "status": "skipped", "reason": "phase B"}]
    assert runner.calls == []


def test_go_locks_get_the_module_sources_and_go_stale_with_them(tmp_path):
    root = tmp_path / "starters"
    module = root / "backend/go-stdlib/minimal/backend"
    (module / "templates").mkdir(parents=True)
    (module / "go.mod").write_text("module app\n\ngo 1.25.0\n")
    (module / "db.go").write_text('package main\n\nimport _ "modernc.org/sqlite"\n')
    (module / "main.go.jinja2").write_text("package main\n{% if x %}{% endif %}\n")
    (module / "templates/index.html").write_text("<title>App</title>\n")
    lock_root = tmp_path / "lock"
    runner = FakeRunner()
    records = locks.generate_locks(["backend/go-stdlib/minimal"], runner, root=root, lock_root=lock_root)
    assert [r["status"] for r in records] == ["locked"]
    image, files, script, outputs = runner.calls[0]
    assert image.startswith("golang:1.25-trixie@sha256:")
    assert set(files) == {"go.mod", "db.go", "templates/index.html"}  # templates (*.jinja2) are left out
    assert "go mod tidy" in script
    assert "go.mod is not tidy" in script
    assert outputs == ["go.sum"]
    entry = locks.lock_index(lock_root / "index.yaml")["backend/go-stdlib/minimal:backend/go.mod"]
    assert entry["lock"] == "backend/go.sum"
    assert entry["inputs_sha"]
    assert locks.locks_for_fragment("backend/go-stdlib/minimal", root=root, lock_root=lock_root)

    (module / "db.go").write_text('package main\n\nimport _ "example.com/other"\n')
    with pytest.raises(locks.StaleLockError):
        locks.locks_for_fragment("backend/go-stdlib/minimal", root=root, lock_root=lock_root)
    assert [
        r["status"]
        for r in locks.generate_locks(["backend/go-stdlib/minimal"], runner, root=root, lock_root=lock_root)
    ] == [
        "locked",
    ]


def test_maven_locks_record_the_dependency_list(tmp_path):
    root = tmp_path / "starters"
    (root / "backend/spring-boot/minimal/backend/src").mkdir(parents=True)
    (root / "backend/spring-boot/minimal/backend/pom.xml").write_text("<project/>\n")
    (root / "backend/spring-boot/minimal/backend/src/A.java").write_text("class A {}\n")
    runner = FakeRunner()
    locks.generate_locks(["backend/spring-boot/minimal"], runner, root=root, lock_root=tmp_path / "lock")
    image, files, script, outputs = runner.calls[0]
    assert image.startswith("maven:3.9-eclipse-temurin-21@sha256:")
    assert files == {"pom.xml": b"<project/>\n"}  # the pom alone decides the resolution
    assert "dependency:resolve" in script
    assert "dependency:list" in script
    assert outputs == ["dependency-list.txt"]


def test_locks_are_written_with_one_final_newline(tmp_path):
    """``mvn dependency:list`` ends with a blank line; the end-of-file-fixer hook then rewrote the committed file
    and its ``lock_sha`` went stale. Written locks end in exactly one newline, so the index survives a commit."""
    root = tmp_path / "starters"
    (root / "backend/spring-boot/minimal/backend").mkdir(parents=True)
    (root / "backend/spring-boot/minimal/backend/pom.xml").write_text("<project/>\n")

    class TrailingBlankRunner(FakeRunner):
        def run(self, image, files, script, outputs):
            return dict.fromkeys(outputs, b"The following files have been resolved:\n   a:b:jar:1:compile\n\n")

    lock_root = tmp_path / "lock"
    locks.generate_locks(["backend/spring-boot/minimal"], TrailingBlankRunner(), root=root, lock_root=lock_root)
    data = (lock_root / "backend/spring-boot/minimal/backend/dependency-list.txt").read_bytes()
    assert data == b"The following files have been resolved:\n   a:b:jar:1:compile\n"
    (entry,) = locks.lock_index(lock_root / "index.yaml").values()
    assert entry["lock_sha"] == identity.bytes_sha(data)
    assert locks.eof_normalized(b"") == b""
    assert locks.eof_normalized(b"x") == b"x\n"
    assert locks.eof_normalized(b"x\r\n\r\n") == b"x\n"


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
