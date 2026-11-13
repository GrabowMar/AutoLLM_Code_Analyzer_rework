"""Image pins and ``HarnessFacts``: every adapter's pinned release is what its image installs and reports.

``fixtures/images.json`` records the wave-3 local verification builds (one build per image): the version
each harness binary reported inside its image and the image id. The offline tests check that the pins
agree everywhere they are written (settings, ``versions``, ``facts.install``, the Dockerfile ARG or the
lock file, the recorded build); the docker-marked test re-asks the local images.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.generation.build import dockerfiles
from backend.generation.harnesses import registry
from backend.generation.library.seeding import load_seed_entries

from .conftest import ALL_HARNESSES
from .conftest import FIXTURES

IMAGES = Path(__file__).resolve().parents[2] / "images"
BUILDS = json.loads((FIXTURES / "images.json").read_text())["images"]
LOCKED = {"mini-swe-agent": "mini-swe-agent", "aider": "aider-chat"}  # pinned by requirements.lock


def _pin_in_image_dir(adapter) -> str:
    image_dir = IMAGES / adapter.image_dir
    if adapter.name in LOCKED:
        lock = (image_dir / "requirements.lock").read_text()
        match = re.search(rf"^{re.escape(LOCKED[adapter.name])}==(\S+)$", lock, re.MULTILINE)
    else:
        dockerfile = (image_dir / "Dockerfile").read_text()
        match = re.search(rf"^ARG {re.escape(adapter.version_build_arg)}=(\S+)$", dockerfile, re.MULTILINE)
    assert match, f"{adapter.name}: no pin in {image_dir}"
    return match.group(1)


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_pins_agree_everywhere(harness):
    adapter = registry.get(harness)
    pin = adapter.version()
    assert pin in adapter.versions
    assert pin in adapter.facts.install
    assert _pin_in_image_dir(adapter) == pin
    build = BUILDS[harness]
    assert build["image"] == adapter.image_dir
    assert re.search(rf"(^|\s){re.escape(pin)}(\s|$)", build["reported"]), build["reported"]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", build["image_id"])
    assert adapter.image_spec(pin).build_args == (
        {adapter.version_build_arg: pin} if adapter.version_build_arg else {}
    )


def test_every_agent_image_was_built():
    built = {build["image"] for name, build in BUILDS.items() if name != "agent-base"}
    assert built == {f"agent-{h}" for h in ALL_HARNESSES}
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", BUILDS["agent-base"]["image_id"])


def test_agent_base_toolchains_are_the_starter_base_images():
    """The agent's Go/JDK/Maven are copied from the exact images the go-stdlib and spring-boot builds use.

    Go stays at the release verified in wave 3 (lead decision: no upgrade beyond what built and smoke-started):
    ``golang:1.25-trixie`` at its recorded digest, which ships go1.25.14 (``fixtures/images.json``), and every
    go stack profile says ``runtime: go1.25``.
    """
    bases = dockerfiles.base_images()
    froms = re.findall(r"^FROM (\S+) AS (\w+)$", (IMAGES / "agent-base" / "Dockerfile").read_text(), re.MULTILINE)
    assert {stage: ref for ref, stage in froms} == {"go": bases["go"].pinned, "maven": bases["maven"].pinned}
    assert bases["go"].ref == "golang:1.25-trixie"
    assert BUILDS["agent-base"]["toolchains"].startswith("go1.25.")
    go_stacks = [e for e in load_seed_entries(kinds=["stack"]) if e.content["backend"]["language"] == "go"]
    assert go_stacks
    assert {e.content["backend"]["runtime"] for e in go_stacks} == {"go1.25"}


def test_facts_versions_name_the_pin():
    for adapter in registry.adapters():
        assert adapter.version() in adapter.facts.verified_against, adapter.name


@pytest.mark.docker
@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_local_image_reports_the_pin(harness):
    adapter = registry.get(harness)
    image = f"llmlab-v3-wt/{adapter.image_dir}:dev"
    if shutil.which("docker") is None:
        pytest.skip("needs docker")
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True, check=False).returncode:  # noqa: S603, S607
        pytest.skip(f"needs {image}")
    result = subprocess.run(  # noqa: S603
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", image, "-c",  # noqa: S607
         f"HOME=/tmp/h {BUILDS[harness]['version_command']}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )  # fmt: skip
    assert result.stdout.strip().splitlines()[-1].strip() == BUILDS[harness]["reported"].strip()
