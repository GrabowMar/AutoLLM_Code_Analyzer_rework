"""Image build, pinning and digest resolution (spec section 6.3).

* **Tags are content hashes of their build context**: ``llmlab/<dir>:<hash12>`` over every file of
  ``generation/images/<dir>`` plus the build args. A harness image builds ``FROM`` the ``agent-base`` tag,
  which is one of its build args, so a harness tag pins its base too; the ``llm-gateway-local`` overlay pins
  the ``llm-gateway`` tag the same way.
* **Digests** (the image id, ``sha256:...``) are resolved at plan time when the image exists
  (:func:`resolve_digest`). When it does not, the plan records ``pending:<tag>``; the first run that builds the
  image records its digest under ``GenerationRun.execution["images"]`` and every later cell of the same plan
  must match it (issue ``image_built_after_plan``, info; :func:`image_issues`).
* :func:`ensure_images` builds or pulls the harness, gateway and starter base images of a manifest and
  **compares digests**. A mismatch raises :class:`ImageDigestMismatch`, which the runner turns into
  ``infra_error`` with reason ``image_digest_mismatch``: a run never proceeds on a different environment.

Docker access goes through ``runtime.services.docker_manager.new_client()`` (the apps daemon); every function
takes an explicit ``client`` so tests can pass a fake.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

if TYPE_CHECKING:
    from backend.generation.contracts.harness import ImageSpec
    from backend.generation.contracts.issues import Issue

logger = logging.getLogger(__name__)

__all__ = [
    "BASE_IMAGE",
    "FRONTEND_VALIDATOR_IMAGE",
    "GATEWAY_IMAGE",
    "GATEWAY_LOCAL_IMAGE",
    "IMAGES_DIR",
    "PENDING_PREFIX",
    "ImageBuildError",
    "ImageDigestMismatch",
    "ImageSet",
    "digest_matches",
    "ensure_image",
    "ensure_images",
    "gateway_image_plan",
    "harness_image_plan",
    "image_issues",
    "image_tag",
    "is_pending",
    "recorded_plan_digest",
    "resolve_digest",
]

IMAGES_DIR: Final = Path(__file__).resolve().parents[1] / "images"
BASE_IMAGE: Final = "agent-base"
GATEWAY_IMAGE: Final = "llm-gateway"
GATEWAY_LOCAL_IMAGE: Final = "llm-gateway-local"  # overlay for non-OpenRouter endpoints, FROM the gateway tag
FRONTEND_VALIDATOR_IMAGE: Final = "frontend-validator"
TAG_REPOSITORY: Final = "llmlab"
PENDING_PREFIX: Final = "pending:"
_HASH_CHARS: Final = 12


class ImageBuildError(RuntimeError):
    """An image could not be built or pulled (infrastructure; the attempt is retried)."""


class ImageDigestMismatch(RuntimeError):  # noqa: N818 - the spec's name for the condition
    """The environment differs from the manifest's pinned digest (``infra_error:image_digest_mismatch``)."""

    def __init__(self, role: str, expected: str, actual: str, tag: str = "") -> None:
        super().__init__(f"{role} image {tag or '?'} has digest {actual}, the manifest pins {expected}")
        self.role = role
        self.expected = expected
        self.actual = actual
        self.tag = tag


def _dir_name(image: str) -> str:
    return image.removeprefix("images/").strip("/")


def _context_hash(path: Path, buildargs: Mapping[str, str]) -> str:
    digest = hashlib.sha256(json.dumps(dict(buildargs), sort_keys=True).encode())
    files = (p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    for file in sorted(files):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(file.read_bytes())
    return digest.hexdigest()[:_HASH_CHARS]


def image_tag(name: str, buildargs: Mapping[str, str] | None = None, *, root: Path | None = None) -> str:
    """``llmlab/<dir>:<content hash>`` of ``images/<dir>`` and its build args."""
    directory = (root or IMAGES_DIR) / _dir_name(name)
    if not directory.is_dir():
        msg = f"no image directory {directory}"
        raise ImageBuildError(msg)
    return f"{TAG_REPOSITORY}/{_dir_name(name)}:{_context_hash(directory, buildargs or {})}"


BuildStep = tuple[str, dict[str, str], str]  # (image dir, build args, tag)


def harness_image_plan(spec: ImageSpec, *, root: Path | None = None) -> list[BuildStep]:
    """The build order of a harness image: its base (``agent-base``), then the harness layer."""
    base_dir = _dir_name(spec.base)
    base_tag = image_tag(base_dir, root=root)
    args = {**spec.build_args, "BASE_IMAGE": base_tag}
    harness_dir = _dir_name(spec.image)
    return [(base_dir, {}, base_tag), (harness_dir, args, image_tag(harness_dir, args, root=root))]


def gateway_image_plan(local: bool, *, root: Path | None = None) -> list[BuildStep]:
    """The gateway images: the frozen base, plus the local overlay (built ``FROM`` the base tag) if ``local``."""
    base = image_tag(GATEWAY_IMAGE, root=root)
    if not local:
        return [(GATEWAY_IMAGE, {}, base)]
    args = {"GATEWAY_BASE": base}
    return [(GATEWAY_IMAGE, {}, base), (GATEWAY_LOCAL_IMAGE, args, image_tag(GATEWAY_LOCAL_IMAGE, args, root=root))]


def is_pending(digest: str | None) -> bool:
    return not digest or digest.startswith(PENDING_PREFIX)


def _image_id(image: Any) -> str:
    return str(getattr(image, "id", "") or "")


def _repo_digests(image: Any) -> list[str]:
    attrs = getattr(image, "attrs", None) or {}
    return [str(d) for d in attrs.get("RepoDigests") or []]


def digest_matches(image: Any, expected: str) -> bool:
    """Whether a docker image is the one ``expected`` pins (image id, or a registry ``RepoDigests`` entry)."""
    if not expected:
        return False
    if _image_id(image) == expected:
        return True
    return any(entry == expected or entry.endswith(f"@{expected}") for entry in _repo_digests(image))


def _not_found(exc: Exception) -> bool:
    return type(exc).__name__ in {"ImageNotFound", "NotFound"}


def resolve_digest(image: str, *, client: Any = None) -> str:
    """The digest of the image tagged ``image`` when it exists, else ``pending:<tag>`` (plan time).

    Never builds: the first run of the plan builds the image and records the digest (issue
    ``image_built_after_plan``). An unreachable daemon also gives ``pending:<tag>``.
    """
    try:
        if client is None:
            from backend.runtime.services import docker_manager

            client = docker_manager.new_client()
        return _image_id(client.images.get(image)) or f"{PENDING_PREFIX}{image}"
    except Exception as exc:  # noqa: BLE001 - plan time must not fail on a missing image or daemon
        if not _not_found(exc):
            logger.warning("Could not resolve the digest of %s: %s", image, exc)
        return f"{PENDING_PREFIX}{image}"


def ensure_image(
    client: Any,
    name: str,
    buildargs: Mapping[str, str] | None = None,
    *,
    tag: str = "",
) -> tuple[str, str]:
    """Build ``images/<name>`` on the daemon when its tag is missing; returns ``(tag, image id)``."""
    buildargs = dict(buildargs or {})
    tag = tag or image_tag(name, buildargs)
    try:
        return tag, _image_id(client.images.get(tag))
    except Exception as exc:
        if not _not_found(exc):
            msg = f"inspecting {tag} failed: {exc}"
            raise ImageBuildError(msg) from exc
    logger.info("Building sandbox image %s", tag)
    errors: list[str] = []
    try:
        for chunk in client.api.build(
            path=str(IMAGES_DIR / _dir_name(name)),
            tag=tag,
            buildargs=buildargs,
            rm=True,
            decode=True,
        ):
            if isinstance(chunk, dict) and chunk.get("error"):
                errors.append(str(chunk["error"]))
        image = client.images.get(tag)
    except Exception as exc:
        msg = f"building {tag} failed: {exc}"
        raise ImageBuildError(msg) from exc
    if errors:
        msg = f"building {tag} failed: {errors[-1]}"
        raise ImageBuildError(msg)
    return tag, _image_id(image)


def _ensure_pulled(client: Any, ref: str) -> Any:
    try:
        return client.images.get(ref)
    except Exception as exc:
        if not _not_found(exc):
            msg = f"inspecting {ref} failed: {exc}"
            raise ImageBuildError(msg) from exc
    try:
        return client.images.pull(ref)
    except Exception as exc:
        msg = f"pulling {ref} failed: {exc}"
        raise ImageBuildError(msg) from exc


@dataclass
class ImageSet:
    """The images one attempt runs on (tags and digests), recorded under ``execution["images"]``."""

    harness_tag: str = ""
    harness_digest: str = ""
    gateway_tag: str = ""
    gateway_digest: str = ""
    starter_base: dict[str, str] = field(default_factory=dict)  # ref -> digest
    built_after_plan: list[str] = field(default_factory=list)  # roles whose manifest digest was pending

    def as_record(self) -> dict[str, Any]:
        return {
            "harness": {"tag": self.harness_tag, "digest": self.harness_digest},
            "gateway": {"tag": self.gateway_tag, "digest": self.gateway_digest},
            "starter_base": dict(self.starter_base),
            "built_after_plan": list(self.built_after_plan),
        }


def recorded_plan_digest(plan_hash: str | None, role: str, *, exclude_run: Any = None) -> str | None:
    """The digest another run of the same plan recorded for ``role`` after building it (``None`` if none)."""
    if not plan_hash:
        return None
    from backend.generation.models import GenerationRun

    rows = GenerationRun.objects.filter(
        manifest__manifest__study__plan_hash=plan_hash,
        **{f"execution__images__{role}__digest__isnull": False},
    )
    if exclude_run is not None:
        rows = rows.exclude(pk=exclude_run)
    digest = rows.order_by("created_at").values_list(f"execution__images__{role}__digest", flat=True).first()
    return str(digest) if digest else None


def _check(
    role: str,
    tag: str,
    actual: str,
    expected: str,
    recorded: Callable[[str], str | None],
    images: ImageSet,
) -> None:
    if is_pending(expected):
        pending_tag = expected.removeprefix(PENDING_PREFIX)
        if pending_tag and pending_tag != tag:
            raise ImageDigestMismatch(role, expected, f"{PENDING_PREFIX}{tag}", tag)
        previous = recorded(role)
        if previous and previous != actual:
            raise ImageDigestMismatch(role, previous, actual, tag)
        images.built_after_plan.append(role)
        return
    if actual != expected:
        raise ImageDigestMismatch(role, expected, actual, tag)


def ensure_images(
    manifest: Mapping[str, Any],
    spec: ImageSpec,
    *,
    client: Any,
    local_gateway: bool = False,
    run_id: Any = None,
) -> ImageSet:
    """Build or pull every image of ``manifest`` and compare each digest with the pinned one.

    ``spec`` is the harness adapter's ``image_spec(version)``. Raises :class:`ImageDigestMismatch` or
    :class:`ImageBuildError`.
    """
    plan_hash = (manifest.get("study") or {}).get("plan_hash")

    def recorded(role: str) -> str | None:
        return recorded_plan_digest(plan_hash, role, exclude_run=run_id)

    images = ImageSet()
    harness = manifest.get("harness") or {}
    tag = image_id = ""
    for name, args, step_tag in harness_image_plan(spec):
        tag, image_id = ensure_image(client, name, args, tag=step_tag)
    images.harness_tag, images.harness_digest = tag, image_id
    _check("harness", tag, image_id, str(harness.get("image_digest") or ""), recorded, images)
    gateway = manifest.get("gateway") or {}
    for name, args, step_tag in gateway_image_plan(local_gateway):
        tag, image_id = ensure_image(client, name, args, tag=step_tag)
    images.gateway_tag, images.gateway_digest = tag, image_id
    _check("gateway", tag, image_id, str(gateway.get("image_digest") or ""), recorded, images)
    for base in (manifest.get("starter") or {}).get("base_images") or []:
        ref, digest = str(base.get("ref") or ""), str(base.get("digest") or "")
        if not ref:
            continue
        image = _ensure_pulled(client, ref)
        if digest and not is_pending(digest) and not digest_matches(image, digest):
            role = "starter_base"
            raise ImageDigestMismatch(role, digest, _image_id(image), ref)
        images.starter_base[ref] = digest or _image_id(image)
    return images


def image_issues(digests: Mapping[str, str], study_kind: str = "exploratory") -> list[Issue]:
    """``image_built_after_plan`` (info) for every role whose plan-time digest is pending."""
    from backend.generation.contracts.issues import make_issue

    pending = sorted(role for role, digest in digests.items() if is_pending(digest))
    if not pending:
        return []
    return [
        make_issue(
            "image_built_after_plan",
            study_kind,  # type: ignore[arg-type]
            message=f"Images built at the first run of the plan: {', '.join(pending)}",
            data={"roles": pending},
        ),
    ]
