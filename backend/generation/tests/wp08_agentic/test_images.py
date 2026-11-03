"""Image readiness (spec 6.3): content-hash tags, digest comparison, pending plan-time digests."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

import pytest

from backend.generation.agentic import images
from backend.generation.agentic.images import ImageDigestMismatch
from backend.generation.contracts.harness import ImageSpec


class NotFound(Exception):  # noqa: N818 - mirrors docker.errors.ImageNotFound's class name check
    pass


NotFound.__name__ = "ImageNotFound"


@dataclass
class FakeImage:
    id: str
    attrs: dict = field(default_factory=dict)


class FakeImages:
    def __init__(self, known: dict[str, FakeImage]) -> None:
        self.known = known
        self.pulled: list[str] = []

    def get(self, tag: str) -> FakeImage:
        if tag not in self.known:
            raise NotFound(tag)
        return self.known[tag]

    def pull(self, ref: str) -> FakeImage:
        self.pulled.append(ref)
        self.known[ref] = FakeImage(id="sha256:" + "b" * 64, attrs={"RepoDigests": [f"{ref}@sha256:{'c' * 64}"]})
        return self.known[ref]


class FakeAPI:
    def __init__(self, images_: FakeImages, digests: dict[str, str]) -> None:
        self.images = images_
        self.digests = digests
        self.built: list[tuple[str, dict]] = []

    def build(self, *, path: str, tag: str, buildargs: dict, rm: bool, decode: bool):
        self.built.append((tag, dict(buildargs)))
        repo = tag.split(":", maxsplit=1)[0].rsplit("/", maxsplit=1)[-1]
        self.images.known[tag] = FakeImage(id=self.digests.get(repo, "sha256:" + "e" * 64))
        return iter([{"stream": "ok"}])


class FakeClient:
    def __init__(self, digests: dict[str, str]) -> None:
        self.images = FakeImages({})
        self.api = FakeAPI(self.images, digests)


def write_images(root):
    for name, body in (("agent-base", "FROM node:24\n"), ("agent-x", "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")):
        (root / name).mkdir(parents=True)
        (root / name / "Dockerfile").write_text(body)


def test_tags_are_content_hashes_of_the_build_context(tmp_path) -> None:
    write_images(tmp_path)
    first = images.image_tag("agent-x", {"V": "1"}, root=tmp_path)
    assert first.startswith("llmlab/agent-x:")
    assert images.image_tag("agent-x", {"V": "1"}, root=tmp_path) == first
    assert images.image_tag("agent-x", {"V": "2"}, root=tmp_path) != first
    (tmp_path / "agent-x" / "entry.sh").write_text("echo\n")
    assert images.image_tag("agent-x", {"V": "1"}, root=tmp_path) != first
    plan = images.harness_image_plan(ImageSpec(image="agent-x", build_args={"V": "1"}, lock_files=()), root=tmp_path)
    assert [step[0] for step in plan] == ["agent-base", "agent-x"]
    assert plan[1][1]["BASE_IMAGE"] == plan[0][2]  # the harness tag pins its base


def _manifest(harness_digest: str, gateway_digest: str, *, plan_hash: str | None = None) -> dict:
    manifest = {
        "harness": {"name": "x", "version": "1", "image": "agent-mini-swe-agent", "image_digest": harness_digest},
        "gateway": {"image_digest": gateway_digest},
        "starter": {"base_images": [{"ref": "python:3.13-slim", "digest": "sha256:" + "c" * 64}]},
    }
    if plan_hash:
        manifest["study"] = {"plan_hash": plan_hash}
    return manifest


SPEC = ImageSpec(image="agent-mini-swe-agent", build_args={"MINI_SWE_AGENT_VERSION": "2.4.6"}, lock_files=())
HARNESS = "sha256:" + "1" * 64
GATEWAY = "sha256:" + "2" * 64


def test_ensure_images_builds_and_matches() -> None:
    client = FakeClient({"agent-mini-swe-agent": HARNESS, "llm-gateway": GATEWAY})
    result = images.ensure_images(_manifest(HARNESS, GATEWAY), SPEC, client=client)
    assert (result.harness_digest, result.gateway_digest) == (HARNESS, GATEWAY)
    assert [tag.split(":")[0] for tag, _ in client.api.built] == [
        "llmlab/agent-base",
        "llmlab/agent-mini-swe-agent",
        "llmlab/llm-gateway",
    ]
    assert client.images.pulled == ["python:3.13-slim"]  # registry digest matched through RepoDigests
    assert result.built_after_plan == []


def test_a_digest_mismatch_never_proceeds() -> None:
    client = FakeClient({"agent-mini-swe-agent": "sha256:" + "9" * 64, "llm-gateway": GATEWAY})
    with pytest.raises(ImageDigestMismatch) as caught:
        images.ensure_images(_manifest(HARNESS, GATEWAY), SPEC, client=client)
    assert caught.value.role == "harness"


@pytest.mark.django_db
def test_pending_digests_are_recorded_and_issue_image_built_after_plan() -> None:
    tag = images.image_tag("agent-mini-swe-agent", {**SPEC.build_args, "BASE_IMAGE": images.image_tag("agent-base")})
    client = FakeClient({"agent-mini-swe-agent": HARNESS, "llm-gateway": GATEWAY})
    result = images.ensure_images(_manifest(f"pending:{tag}", GATEWAY, plan_hash="f" * 64), SPEC, client=client)
    assert result.built_after_plan == ["harness"]
    with pytest.raises(ImageDigestMismatch):  # the plan pinned another build context
        images.ensure_images(
            _manifest("pending:llmlab/agent-mini-swe-agent:000000000000", GATEWAY),
            SPEC,
            client=client,
        )
    issues = images.image_issues({"harness": f"pending:{tag}", "gateway": GATEWAY})
    assert [issue.code for issue in issues] == ["image_built_after_plan"]
    assert issues[0].severity == "info"


def test_resolve_digest_is_pending_when_the_image_is_missing() -> None:
    client = FakeClient({})
    assert images.resolve_digest("llmlab/agent-x:abc", client=client) == "pending:llmlab/agent-x:abc"
    client.images.known["llmlab/agent-x:abc"] = FakeImage(id=HARNESS)
    assert images.resolve_digest("llmlab/agent-x:abc", client=client) == HARNESS
