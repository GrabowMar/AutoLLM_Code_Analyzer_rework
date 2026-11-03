"""Docker (LLMLAB_DOCKER_TESTS=1): every minimal starter builds and answers ``/api/health`` (WP-05 done-when).

Runs against ``settings.STARTERS_DOCKER_HOST`` (default: the apps daemon, apps-dind). Images are tagged
``llmlab-v3-wt/starter-<slug>:dev``; containers are named ``v3wt-starter-<slug>[-probe|-db]`` and share an
``--internal`` network ``v3wt-starter-<slug>`` (no egress, like an evaluation run). Everything is removed
afterwards. The runtime images of the Go and Spring Boot stacks carry no HTTP client, so the requests come
from a probe container on the same network (the digest-pinned Python base). A ``database: postgres`` profile
gets a sidecar from ``base_images.yaml`` ``sidecars.postgres`` and ``DATABASE_URL`` pointing at it (the run
contract, docs/STACKS.md).

``LLMLAB_STARTER_ONLY=<slug>[,<slug>...]`` limits the run (one starter per invocation keeps a shared build
lock short).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tarfile
import time

import pytest

from backend.generation.build import dockerfiles
from backend.generation.build import starters

from .helpers import active_entries
from .helpers import compose_entry

_ONLY = {s for s in os.environ.get("LLMLAB_STARTER_ONLY", "").split(",") if s}
MINIMAL = [
    e
    for e in active_entries()
    if e.level == "minimal" and (not _ONLY or starters.starter_slug(e.stack, e.level) in _ONLY)
]
_PROBE = (
    "import sys,urllib.request\n"
    "try:\n"
    "    print(urllib.request.urlopen(sys.argv[1], timeout=3).read().decode())\n"
    "except Exception as exc:\n"
    "    print(exc)\n"
    "    sys.exit(1)\n"
)
_DB = {"user": "app", "password": "starter-smoke", "name": "app"}
_LABELS = {"llmlab.purpose": "wp05-starter-test"}


def _context(files: dict[str, bytes], modes: dict[str, int]) -> io.BytesIO:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path, data in sorted(files.items()):
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = modes[path]
            tar.addfile(info, io.BytesIO(data))
    buffer.seek(0)
    return buffer


def _get(client, network: str, url: str) -> tuple[int, str]:
    probe = client.containers.run(
        dockerfiles.base_images()["python"].pinned,
        ["python", "-c", _PROBE, url],
        name=f"{network}-probe",
        network=network,
        detach=True,
        labels=_LABELS,
    )
    try:
        status = probe.wait(timeout=30)
        return int(status.get("StatusCode", 1)), probe.logs().decode("utf-8", errors="replace")
    finally:
        probe.remove(force=True)


def _start_postgres(client, network: str) -> None:
    database = client.containers.run(
        dockerfiles.sidecar_images()["postgres"].pinned,
        name=f"{network}-db",
        network=network,
        detach=True,
        environment={
            "POSTGRES_USER": _DB["user"],
            "POSTGRES_PASSWORD": _DB["password"],
            "POSTGRES_DB": _DB["name"],
        },
        labels=_LABELS,
        mem_limit="512m",
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        # TCP, not the socket: the image's init server listens on the socket only.
        ready = database.exec_run(["pg_isready", "-q", "-h", "127.0.0.1", "-U", _DB["user"]])
        if ready.exit_code == 0:
            return
        time.sleep(1)
    raise AssertionError(database.logs().decode()[-2000:])


def _cleanup(client, name: str, image_id: str | None) -> None:
    # Best-effort: the containers and the network may not exist (yet, or any more).
    for suffix in ("-probe", "-db", ""):
        with contextlib.suppress(Exception):
            client.containers.get(f"{name}{suffix}").remove(force=True)
    with contextlib.suppress(Exception):
        client.networks.get(name).remove()
    if image_id:
        client.images.remove(image_id, force=True)


@pytest.mark.docker
@pytest.mark.parametrize("entry", MINIMAL, ids=lambda e: starters.starter_slug(e.stack, e.level))
def test_minimal_starter_builds_and_answers_health(docker_client, entry):
    starter = compose_entry(entry)
    stack = starters.load_stack_yaml(entry.stack)["content"]
    modes = {path: int(meta["mode"]) for path, meta in starter.content["files"].items()}
    tag = f"llmlab-v3-wt/starter-{starter.slug}:dev"
    name = f"v3wt-starter-{starter.slug}"
    image_id = None
    _cleanup(docker_client, name, None)
    try:
        started = time.monotonic()
        image, _logs = docker_client.images.build(
            fileobj=_context(starter.files, modes),
            custom_context=True,
            tag=tag,
            rm=True,
            forcerm=True,
            labels=_LABELS,
        )
        image_id = image.id
        built_s = time.monotonic() - started
        docker_client.networks.create(name, driver="bridge", internal=True, labels=_LABELS)
        environment = {"SECRET_KEY": "starter-smoke"}
        if stack["database"] == "postgres":
            _start_postgres(docker_client, name)
            environment["DATABASE_URL"] = f"postgresql://{_DB['user']}:{_DB['password']}@{name}-db:5432/{_DB['name']}"
        java = stack["backend"]["framework"] == "spring-boot"
        container = docker_client.containers.run(
            image.id,
            name=name,
            detach=True,
            network=name,
            environment=environment,
            mem_limit="2g" if java else "1g",
            labels=_LABELS,
        )
        base = f"http://{name}:8000"
        deadline = time.monotonic() + 120
        code, body = 1, ""
        while time.monotonic() < deadline:
            code, body = _get(docker_client, name, f"{base}/api/health")
            if code == 0:
                break
            container.reload()
            assert container.status == "running", container.logs().decode()[-3000:]
            time.sleep(1)
        assert code == 0, (body, container.logs().decode()[-3000:])
        assert json.loads(body.strip()) == {"status": "ok"}
        code, page = _get(docker_client, name, f"{base}/")
        assert code == 0, page
        assert "<title>App</title>" in page
        if stack["frontend"]["framework"] != "none":
            code, page = _get(docker_client, name, f"{base}/some/client/route")
            assert code == 0, page  # the SPA fallback serves index.html
            assert "<title>App</title>" in page
        size_mb = image.attrs.get("Size", 0) / 1e6
        print(  # noqa: T201 - logged for the PR
            f"{starter.slug}: built {image.short_id} in {built_s:.0f}s ({size_mb:.0f} MB), /api/health ok, / served",
        )
    finally:
        _cleanup(docker_client, name, image_id)
