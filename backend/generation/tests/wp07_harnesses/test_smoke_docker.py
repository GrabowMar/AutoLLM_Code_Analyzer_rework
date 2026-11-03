"""Docker-marked smoke runs: each adapter against the scripted upstream on an isolated network.

These need the host docker daemon and the ``llmlab-v3-wt/agent-*:dev`` images (``docs/HARNESSES.md``), so
they skip inside the worktree test container. They never call a real model: ``smoke/scripted_upstream.py``
answers as ``llm-gateway:8080`` on an ``--internal`` network. The real-model smoke (three model families
through the WP-06 gateway) is an operator step logged in the PR.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import SMOKED

SMOKE_DIR = Path(__file__).resolve().parent / "smoke"
pytestmark = pytest.mark.docker


def _image_exists(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(["docker", "image", "inspect", image], capture_output=True, check=False)  # noqa: S603, S607
    return result.returncode == 0


@pytest.mark.parametrize("harness", SMOKED)
def test_smoke_run(harness, tmp_path):
    from backend.generation.harnesses import registry

    adapter = registry.get(harness)
    image = f"llmlab-v3-wt/{adapter.image_dir}:dev"
    if not _image_exists(image) or not _image_exists("llmlab-v3-wt/agent-base:dev"):
        pytest.skip(f"needs docker and {image}")
    out = tmp_path / harness
    subprocess.run(  # noqa: S603
        ["python", str(SMOKE_DIR / "render.py"), harness, str(out)],  # noqa: S607
        check=True,
        timeout=120,
    )
    subprocess.run(["sh", str(SMOKE_DIR / "run_smoke.sh"), str(out)], check=True, timeout=600)  # noqa: S603, S607
    assert "hello.txt" in (out / "logs" / "name_status.txt").read_text()
