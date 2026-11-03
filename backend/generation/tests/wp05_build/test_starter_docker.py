"""Docker (LLMLAB_DOCKER_TESTS=1): every minimal starter builds and answers ``/api/health`` (WP-05 done-when).

Runs against ``settings.STARTERS_DOCKER_HOST`` (default: the apps daemon, apps-dind). Images are tagged
``llmlab-v3-wt/starter-<slug>:dev`` and removed afterwards; the health check runs inside the container
(``exec``), so the test works on a remote daemon without published ports.
"""

from __future__ import annotations

import io
import json
import tarfile
import time

import pytest

from backend.generation.build import starters

from .helpers import compose_entry
from .helpers import phase_a_entries

MINIMAL = [e for e in phase_a_entries() if e.level == "minimal"]
_PY_GET = "import urllib.request,sys;print(urllib.request.urlopen(sys.argv[1],timeout=3).read().decode())"
_NODE_GET = "fetch(process.argv[1]).then(r=>r.text()).then(t=>{console.log(t)}).catch(()=>process.exit(1))"


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


def _get(container, url: str, python: bool) -> tuple[int, str]:
    command = ["python", "-c", _PY_GET, url] if python else ["node", "-e", _NODE_GET, url]
    result = container.exec_run(command)
    return result.exit_code, result.output.decode("utf-8", errors="replace")


@pytest.mark.docker
@pytest.mark.parametrize("entry", MINIMAL, ids=lambda e: starters.starter_slug(e.stack, e.level))
def test_minimal_starter_builds_and_answers_health(docker_client, entry):
    starter = compose_entry(entry)
    modes = {path: int(meta["mode"]) for path, meta in starter.content["files"].items()}
    tag = f"llmlab-v3-wt/starter-{starter.slug}:dev"
    python = starter.content["composition"]["backend_fragment"].split("/")[1] in {"flask", "fastapi", "django"}
    image, _logs = docker_client.images.build(
        fileobj=_context(starter.files, modes),
        custom_context=True,
        tag=tag,
        rm=True,
        forcerm=True,
        labels={"llmlab.purpose": "wp05-starter-test"},
    )
    container = docker_client.containers.run(image.id, detach=True, network_mode="none", mem_limit="1g")
    try:
        deadline = time.monotonic() + 90
        code, body = 1, ""
        while time.monotonic() < deadline:
            code, body = _get(container, "http://127.0.0.1:8000/api/health", python)
            if code == 0:
                break
            container.reload()
            assert container.status == "running", container.logs().decode()[-3000:]
            time.sleep(1)
        assert code == 0, (body, container.logs().decode()[-3000:])
        assert json.loads(body.strip()) == {"status": "ok"}
        code, page = _get(container, "http://127.0.0.1:8000/", python)
        assert code == 0, page
        assert "<title>App</title>" in page
        print(f"{starter.slug}: built {image.short_id}, /api/health ok, / served")  # noqa: T201 - logged for the PR
    finally:
        container.remove(force=True)
        docker_client.images.remove(image.id, force=True)
