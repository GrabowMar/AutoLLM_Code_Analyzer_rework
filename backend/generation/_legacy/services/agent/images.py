"""Content-addressed tags for the sandbox images (no Docker access needed).

Every image is ``llmlab/<dir>:<hash>`` where the hash covers the image
directory (Dockerfile, lock files, scripts) and its build args. Harness
images build FROM the agent-base tag, which is one of their build args, so a
harness tag also pins the exact base. Snapshots record these tags at job
creation; editing any image definition therefore changes every new job's
run_fingerprint, and a job can never silently run on a rebuilt environment.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

IMAGES_DIR = Path(__file__).resolve().parents[2] / "images"
BASE_IMAGE = "agent-base"
GATEWAY_IMAGE = "llm-gateway"
# Overlay for non-OpenRouter endpoints, built FROM the gateway tag (spec §D.4).
GATEWAY_LOCAL_IMAGE = "llm-gateway-local"
TESTER_IMAGE = "conformance-tester"


def _dir_hash(path: Path, extra: dict[str, str]) -> str:
    digest = hashlib.sha256(json.dumps(extra, sort_keys=True).encode())
    files = (p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    for file in sorted(files):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()[:12]


def image_tag(name: str, buildargs: dict[str, str] | None = None) -> str:
    return f"llmlab/{name}:{_dir_hash(IMAGES_DIR / name, buildargs or {})}"


def harness_image_plan(harness_image: str, harness_buildargs: dict[str, str]) -> list[tuple[str, dict[str, str], str]]:
    """``[(image dir, build args, tag), ...]`` in build order: base, then the harness layer."""
    base_tag = image_tag(BASE_IMAGE)
    args = {**harness_buildargs, "BASE_IMAGE": base_tag}
    return [(BASE_IMAGE, {}, base_tag), (harness_image, args, image_tag(harness_image, args))]


def gateway_image_plan(local: bool) -> list[tuple[str, dict[str, str], str]]:
    """``[(image dir, build args, tag), ...]`` for the gateway: the frozen base, plus the local overlay if *local*.

    The overlay's build arg is the base tag, so the overlay tag pins the base.
    """
    base = image_tag(GATEWAY_IMAGE)
    if not local:
        return [(GATEWAY_IMAGE, {}, base)]
    args = {"GATEWAY_BASE": base}
    return [(GATEWAY_IMAGE, {}, base), (GATEWAY_LOCAL_IMAGE, args, image_tag(GATEWAY_LOCAL_IMAGE, args))]
