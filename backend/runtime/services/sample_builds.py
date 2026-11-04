"""Build immutable sample images once, then reuse them in fresh containers.

Only source-derived image contents are reused. A running container's writable
filesystem never enters the cache. Base/dependency resolution belongs to the
cached generation until it expires or the owner explicitly refreshes it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import stat
import tempfile
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db import connections
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from backend.runtime.models import ContainerInstance
from backend.runtime.models import SampleBuildArtifact
from backend.runtime.services import build_backend
from backend.runtime.services import docker_manager
from backend.runtime.services.build_context import BuildContextReport
from backend.runtime.services.build_context import build_identity
from backend.runtime.services.build_context import prepare_build_dir

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterator

    from backend.generation.models import GenerationRun

logger = logging.getLogger(__name__)

_LOCK_GUARD = threading.Lock()
_LOCAL_LOCKS: weakref.WeakValueDictionary[str, Any] = weakref.WeakValueDictionary()
_HANDOFF_SECONDS = 120
_RECIPE_VERSION = 1


@dataclass
class SampleBuildResult:
    image_id: str
    image_tag: str
    log: str
    cache_hit: bool
    build_key: str
    seconds: float
    queue_seconds: float
    image_bytes: int
    build_context: dict[str, Any]
    base_images: dict[str, Any] = field(default_factory=dict)
    resolved_python_deps: list[str] = field(default_factory=list)
    prepare_seconds: float = 0.0
    cpu_seconds: float | None = None
    peak_memory_bytes: int | None = None

    def metrics(self) -> dict[str, Any]:
        return {
            "build_key": self.build_key,
            "image_id": self.image_id,
            "image_tag": self.image_tag,
            "cache_hit": self.cache_hit,
            "seconds": self.seconds,
            "queue_seconds": self.queue_seconds,
            "prepare_seconds": self.prepare_seconds,
            "image_bytes": self.image_bytes,
            "base_images": self.base_images,
            "cpu_seconds": self.cpu_seconds,
            "peak_memory_bytes": self.peak_memory_bytes,
        }


class BuildWaitCancelledError(RuntimeError):
    """The owner stopped a request while it waited for the build lock."""


def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        msg = "Sample image build cancelled"
        raise BuildWaitCancelledError(msg)


@contextmanager
def _build_lock(
    name: str,
    *,
    cancelled: Callable[[], bool] | None = None,
    blocking: bool = True,
) -> Iterator[bool]:
    """Session advisory lock, with a process-local fallback for SQLite tests.

    The dedicated PostgreSQL connection never enters a transaction or pins an
    ORM request connection for the duration of a Docker build. A process death
    releases the lock automatically.
    """
    default = connections["default"]
    if default.vendor == "postgresql":
        conn = default.copy(alias="sample-build-lock")
        lock_id = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
        acquired = False
        try:
            while not acquired:
                _check_cancelled(cancelled)
                with conn.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
                    acquired = bool(cursor.fetchone()[0])
                if acquired or not blocking:
                    break
                time.sleep(0.1)
            yield acquired
        finally:
            # Closing a dedicated session releases all its advisory locks,
            # including when the socket failed before an explicit unlock.
            conn.close()
        return

    with _LOCK_GUARD:
        lock = _LOCAL_LOCKS.setdefault(name, threading.Lock())
    acquired = False
    try:
        while not acquired:
            _check_cancelled(cancelled)
            acquired = lock.acquire(timeout=0.1) if blocking else lock.acquire(blocking=False)
            if acquired or not blocking:
                break
        yield acquired
    finally:
        if acquired:
            lock.release()


def _platform_info(client) -> tuple[str, str]:
    info = client.info()
    arch = info.get("Architecture", "amd64")
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(arch, arch)
    return f"{info.get('OSType', 'linux')}/{arch}", str(info.get("ID", ""))


def _context_key(job: GenerationRun, path: Path, *, platform: str, daemon_id: str) -> str:
    """Hash paths and bytes, never timestamps; reject special filesystem nodes."""
    owner = job.created_by_id
    identity = {
        "recipe": _RECIPE_VERSION,
        "backend": getattr(settings, "SAMPLE_BUILD_BACKEND", "legacy"),
        "owner": owner if owner is not None else f"anonymous:{job.id}",
        "platform": platform,
        "daemon": daemon_id,
        "build": build_identity(job),
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())
    for entry in sorted(path.rglob("*")):
        mode = entry.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            msg = f"Sample build context contains a special file: {entry.relative_to(path)}"
            raise ValueError(msg)
        name = entry.relative_to(path).as_posix().encode()
        content = entry.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(stat.S_IMODE(mode).to_bytes(4, "big"))
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _cached_artifact(build_key: str, client) -> SampleBuildArtifact | None:
    from docker.errors import ImageNotFound

    now = timezone.now()
    artifact = SampleBuildArtifact.objects.filter(build_key=build_key, expires_at__gt=now).first()
    if artifact is None:
        return None
    try:
        image = client.images.get(artifact.image_id)
    except ImageNotFound:
        # Lost daemon storage is a cache miss, not a failed sample. Retain the
        # row until normal cleanup so historical image identities stay visible.
        return None
    if image.id != artifact.image_id:
        return None
    artifact.last_used_at = now
    artifact.reserved_until = now + timedelta(seconds=_HANDOFF_SECONDS)
    artifact.save(update_fields=["last_used_at", "reserved_until"])
    return artifact


def _result(
    artifact: SampleBuildArtifact,
    *,
    report: BuildContextReport,
    cache_hit: bool,
    queue_seconds: float,
    prepare_seconds: float,
    log: str = "",
    seconds: float = 0,
    cpu_seconds: float | None = None,
    peak_memory_bytes: int | None = None,
) -> SampleBuildResult:
    metadata = artifact.metadata or {}
    return SampleBuildResult(
        image_id=artifact.image_id,
        image_tag=artifact.image_tag,
        log=log,
        cache_hit=cache_hit,
        build_key=artifact.build_key,
        seconds=seconds,
        queue_seconds=round(queue_seconds, 3),
        image_bytes=artifact.image_bytes,
        build_context=report.to_dict(),
        base_images=metadata.get("base_images", {}),
        resolved_python_deps=metadata.get("resolved_python_deps", []),
        prepare_seconds=round(prepare_seconds, 3),
        cpu_seconds=cpu_seconds,
        peak_memory_bytes=peak_memory_bytes,
    )


def cached_sample(
    job: GenerationRun,
    *,
    client=None,
    cancelled: Callable[[], bool] | None = None,
) -> SampleBuildResult | None:
    """Reserve a cache hit for immediate preview startup, without building a miss."""
    client = client if client is not None else docker_manager.new_client()
    _check_cancelled(cancelled)
    started = time.monotonic()
    report = BuildContextReport()
    with tempfile.TemporaryDirectory(prefix="llmlab-sample-peek-") as tmp:
        path = Path(tmp)
        prepare_build_dir(job, path, report=report)
        platform, daemon_id = _platform_info(client)
        build_key = _context_key(job, path, platform=platform, daemon_id=daemon_id)
        prepare_seconds = time.monotonic() - started
        queued = time.monotonic()
        with _build_lock(f"sample-key:{build_key}", cancelled=cancelled, blocking=False) as acquired:
            # Web requests must not wait behind a long build. The executor
            # resolves this miss after ordinary resource-aware admission.
            if not acquired:
                return None
            artifact = _cached_artifact(build_key, client)
            if artifact is None:
                return None
            return _result(
                artifact,
                report=report,
                cache_hit=True,
                queue_seconds=time.monotonic() - queued,
                prepare_seconds=prepare_seconds,
                log=f"Reusing cached sample image {artifact.image_id}\n",
            )


def build_sample(
    job: GenerationRun,
    *,
    client=None,
    force_rebuild: bool = False,
    cancelled: Callable[[], bool] | None = None,
    context_dir: Path | None = None,
) -> SampleBuildResult:
    """Get this owner's unchanged sample image, or build one immutable generation.

    ``context_dir`` (the opt-in ``benchmark_sample_builds`` command only) replaces ``prepare_build_dir``
    with a copy of an already prepared, possibly edited, build context of ``job``. The run is never saved.
    """
    client = client if client is not None else docker_manager.new_client()
    _check_cancelled(cancelled)
    started = time.monotonic()
    report = BuildContextReport()
    with tempfile.TemporaryDirectory(prefix="llmlab-sample-") as tmp:
        path = Path(tmp)
        if context_dir is not None:
            shutil.copytree(context_dir, path, symlinks=True, dirs_exist_ok=True)
            report.notes.append("context: prepared by the caller (benchmark)")
        else:
            prepare_build_dir(job, path, report=report)
        platform, daemon_id = _platform_info(client)
        build_key = _context_key(job, path, platform=platform, daemon_id=daemon_id)
        prepare_seconds = time.monotonic() - started
        queued = time.monotonic()
        with _build_lock(f"sample-key:{build_key}", cancelled=cancelled):
            if not force_rebuild:
                artifact = _cached_artifact(build_key, client)
                if artifact is not None:
                    return _result(
                        artifact,
                        report=report,
                        cache_hit=True,
                        queue_seconds=time.monotonic() - queued,
                        prepare_seconds=prepare_seconds,
                        log=f"Reusing cached sample image {artifact.image_id}\n",
                    )

            # A daemon-wide limit spans Django, executor and maintenance callers.
            # Queue admission separately respects agent/evaluation memory slots.
            with _build_lock(f"sample-builder:{daemon_id}", cancelled=cancelled):
                queue_seconds = time.monotonic() - queued
                _check_cancelled(cancelled)
                previous = SampleBuildArtifact.objects.filter(build_key=build_key).first()
                refresh = force_rebuild or (previous is not None and previous.expires_at <= timezone.now())
                tag = f"llmlab/sample:{build_key[:24]}-{uuid.uuid4().hex[:12]}"
                owner = job.created_by_id
                namespace = f"owner-{owner}" if owner is not None else f"anonymous-{job.id}"
                result = build_backend.build(
                    str(path),
                    tag,
                    client=client,
                    labels={"llmlab.sample_image": "true", "llmlab.sample_key": build_key},
                    force_rebuild=refresh,
                    cache_namespace=namespace,
                    timeout_s=getattr(settings, "SAMPLE_BUILD_TIMEOUT_S", 1200),
                    cancelled=cancelled,
                    build_contexts=report.build_context_args(),
                )
                image = client.images.get(result.image_id)
                now = timezone.now()
                artifact = SampleBuildArtifact.objects.create(
                    build_key=build_key,
                    owner_id=owner,
                    image_id=result.image_id,
                    image_tag=result.image_tag,
                    image_bytes=int(image.attrs.get("Size") or 0),
                    platform=platform,
                    metadata={"base_images": result.base_images},
                    built_at=now,
                    expires_at=now + timedelta(seconds=getattr(settings, "SAMPLE_IMAGE_CACHE_TTL_S", 604800)),
                    last_used_at=now,
                    reserved_until=now + timedelta(seconds=_HANDOFF_SECONDS),
                )
                return _result(
                    artifact,
                    report=report,
                    cache_hit=False,
                    queue_seconds=queue_seconds,
                    prepare_seconds=prepare_seconds,
                    log=result.log,
                    seconds=result.seconds,
                    cpu_seconds=getattr(result, "cpu_seconds", None),
                    peak_memory_bytes=getattr(result, "peak_memory_bytes", None),
                )


def record_resolved_dependencies(build_key: str, image_id: str, dependencies: list[str]) -> None:
    """Attach pip-freeze evidence obtained by evaluation, without starting a container."""
    with _build_lock(f"sample-key:{build_key}"):
        for artifact in SampleBuildArtifact.objects.filter(build_key=build_key, image_id=image_id):
            artifact.metadata = {**(artifact.metadata or {}), "resolved_python_deps": dependencies}
            artifact.save(update_fields=["metadata"])


def _referenced_images(client) -> set[str]:
    """Protect actual Docker containers and previews tracked during their hand-off."""
    referenced = set(
        ContainerInstance.objects.exclude(status=ContainerInstance.Status.REMOVED)
        .exclude(image="")
        .values_list("image", flat=True),
    )
    for container in client.containers.list(all=True):
        attrs = container.attrs
        if attrs.get("Image"):
            referenced.add(attrs["Image"])
        image_ref = (attrs.get("Config") or {}).get("Image")
        if image_ref:
            referenced.add(image_ref)
    return referenced


def _cleanup_untracked_images(client, stats: dict[str, int]) -> None:
    """Reap exports whose worker died before publishing their database row."""
    cutoff = timezone.now() - timedelta(seconds=getattr(settings, "SAMPLE_IMAGE_CACHE_TTL_S", 604800))
    tracked_tags = set(SampleBuildArtifact.objects.values_list("image_tag", flat=True))
    for image in client.images.list(filters={"label": "llmlab.sample_image=true"}):
        attrs = image.attrs
        created = parse_datetime(attrs.get("Created", ""))
        if created is None or timezone.is_naive(created) or created >= cutoff:
            continue
        labels = (attrs.get("Config") or {}).get("Labels") or {}
        build_key = labels.get("llmlab.sample_key")
        if not build_key:
            continue
        tags = [tag for tag in attrs.get("RepoTags", []) if tag.startswith("llmlab/sample:")]
        for tag in tags:
            if tag in tracked_tags:
                continue
            with _build_lock(f"sample-key:{build_key}", blocking=False) as acquired:
                if not acquired:
                    stats["protected"] += 1
                    continue
                if SampleBuildArtifact.objects.filter(image_tag=tag).exists():
                    continue
                referenced = _referenced_images(client)
                if image.id in referenced or tag in referenced:
                    stats["protected"] += 1
                    continue
                # No force: Docker is the final guard against a container
                # appearing between the reference scan and tag removal.
                client.images.remove(tag, force=False)
                stats["removed"] += 1
                stats["bytes_reclaimed"] += int(attrs.get("Size") or 0)


def cleanup_sample_images(*, client=None) -> dict[str, int]:
    """Expire unused generations, then evict least recently used ones to the quota.

    Docker refuses non-force deletion of an image referenced by a concurrent
    container. Combined with per-key locks and the hand-off reservation, this
    makes cleanup safe when a preview/evaluation begins during a sweep.
    """
    from docker.errors import ImageNotFound

    client = client if client is not None else docker_manager.new_client()
    stats = {"removed": 0, "bytes_reclaimed": 0, "protected": 0, "errors": 0}
    with _build_lock("sample-image-cleanup", blocking=False) as acquired:
        if not acquired:
            return stats
        artifacts = list(SampleBuildArtifact.objects.order_by("last_used_at"))
        total = sum(artifact.image_bytes for artifact in artifacts)
        budget = getattr(settings, "SAMPLE_IMAGE_CACHE_BYTES", 5 * 1024**3)
        for candidate in artifacts:
            now = timezone.now()
            if candidate.expires_at > now and total <= budget:
                continue
            with _build_lock(f"sample-key:{candidate.build_key}", blocking=False) as acquired_key:
                if not acquired_key:
                    stats["protected"] += 1
                    continue
                try:
                    with transaction.atomic():
                        # A cache hit in an ATOMIC_REQUESTS view can have
                        # saved its reservation without committing it yet.
                        # Skip that locked row rather than reading its old
                        # reservation and deleting the image before commit.
                        artifact = (
                            SampleBuildArtifact.objects.select_for_update(skip_locked=True)
                            .filter(pk=candidate.pk)
                            .first()
                        )
                        if artifact is None:
                            stats["protected"] += 1
                            continue
                        if artifact.reserved_until > now:
                            stats["protected"] += 1
                            continue
                        referenced = _referenced_images(client)
                        if artifact.image_id in referenced or artifact.image_tag in referenced:
                            stats["protected"] += 1
                            continue
                        # Delete only our immutable tag, and never force an
                        # image Docker reports as in use by another container.
                        with suppress(ImageNotFound):
                            client.images.remove(artifact.image_tag, force=False)
                        image_bytes = artifact.image_bytes
                        artifact.delete()
                except Exception:
                    logger.exception("Removing cached sample image %s failed", candidate.image_tag)
                    stats["errors"] += 1
                    continue
                total -= image_bytes
                stats["removed"] += 1
                stats["bytes_reclaimed"] += image_bytes
        try:
            _cleanup_untracked_images(client, stats)
        except Exception:
            logger.exception("Removing untracked sample images failed")
            stats["errors"] += 1
    return stats
