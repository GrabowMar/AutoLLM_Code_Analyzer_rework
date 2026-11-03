"""Image reuse is independent of container state and safe under cache eviction."""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db import connections
from django.db import transaction
from django.utils import timezone
from docker.errors import ImageNotFound

from backend.runtime.models import ContainerInstance
from backend.runtime.models import SampleBuildArtifact
from backend.runtime.services import sample_builds
from backend.runtime.tests.factories import ContainerInstanceFactory
from backend.users.tests.factories import UserFactory


def _run(**attrs):
    """The fields ``build_identity`` and the cache key read from a GenerationRun (no database row needed)."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind="app_agentic",
        protocol="app@1",
        parent_run_id=None,
        stack_profile=None,
        starter=None,
        resolved_lock_blob="",
        **attrs,
    )


@pytest.fixture
def sample_builder(monkeypatch, settings):
    settings.SAMPLE_BUILD_BACKEND = "legacy"
    settings.SAMPLE_IMAGE_CACHE_TTL_S = 604800
    settings.SAMPLE_IMAGE_CACHE_BYTES = 5 * 1024**3
    job = _run(created_by_id=UserFactory().pk, source="print('first sample')")
    client = MagicMock()
    client.info.return_value = {"OSType": "linux", "Architecture": "amd64", "ID": "test-daemon"}
    client.containers.list.return_value = []
    client.images.list.return_value = []
    images = {}

    def image_get(image):
        if image not in images:
            raise ImageNotFound(image)
        return images[image]

    client.images.get.side_effect = image_get

    def prepare(job, path, **_options):
        # Stands in for generation.build.context.prepare_build_dir (the app tree, verbatim).
        (path / "Dockerfile").write_text("FROM python:3.13-slim\nCOPY . /app\n")
        (path / ".dockerignore").write_text("__pycache__\n")
        (path / "app.py").write_text(job.source)

    builds = []

    def build(path, tag, **kwargs):
        image_id = f"sha256:{len(builds) + 1:064x}"
        image = SimpleNamespace(id=image_id, attrs={"Size": 1024})
        images[image_id] = image
        images[tag] = image
        builds.append({"tag": tag, **kwargs})
        return SimpleNamespace(
            image_id=image_id,
            image_tag=tag,
            log="built\n",
            seconds=2.5,
            base_images={"python:3.13-slim": "sha256:base"},
            cpu_seconds=0.5,
            peak_memory_bytes=100,
        )

    monkeypatch.setattr(sample_builds, "prepare_build_dir", prepare)
    monkeypatch.setattr(sample_builds.build_backend, "build", build)
    return SimpleNamespace(job=job, client=client, images=images, builds=builds)


@pytest.mark.django_db
def test_build_passes_the_reports_base_pins(sample_builder, monkeypatch):
    """``build_sample`` hands ``report.build_context_args()`` to the backend (frozen free-choice bases)."""
    plain = sample_builds.prepare_build_dir

    def prepare(job, path, *, report):
        plain(job, path, report=report)
        report.base_pins["python:3.13-slim"] = "python:3.13-slim@sha256:" + "b" * 64

    monkeypatch.setattr(sample_builds, "prepare_build_dir", prepare)
    sample_builds.build_sample(sample_builder.job, client=sample_builder.client)
    contexts = list(sample_builder.builds[0]["build_contexts"])
    assert contexts
    assert any("python:3.13-slim@sha256:" + "b" * 64 in arg for arg in contexts)


@pytest.mark.django_db
def test_unpinned_build_passes_no_named_contexts(sample_builder):
    sample_builds.build_sample(sample_builder.job, client=sample_builder.client)
    assert list(sample_builder.builds[0]["build_contexts"]) == []


@pytest.mark.django_db
def test_identical_sample_reuses_image_without_rebuilding(sample_builder):
    env = sample_builder
    first = sample_builds.build_sample(env.job, client=env.client)
    sample_builds.record_resolved_dependencies(first.build_key, first.image_id, ["Flask==3.1.0"])
    second = sample_builds.build_sample(env.job, client=env.client)

    assert not first.cache_hit
    assert second.cache_hit
    assert second.image_id == first.image_id
    assert second.resolved_python_deps == ["Flask==3.1.0"]
    assert second.base_images == {"python:3.13-slim": "sha256:base"}
    assert second.seconds == 0
    assert first.cpu_seconds == 0.5
    assert len(env.builds) == 1
    assert SampleBuildArtifact.objects.count() == 1
    env.client.containers.run.assert_not_called()
    env.client.containers.create.assert_not_called()


@pytest.mark.django_db
def test_peek_reserves_hit_without_building_a_miss(sample_builder):
    env = sample_builder
    assert sample_builds.cached_sample(env.job, client=env.client) is None
    assert not env.builds
    built = sample_builds.build_sample(env.job, client=env.client)
    artifact = SampleBuildArtifact.objects.get(image_id=built.image_id)
    SampleBuildArtifact.objects.filter(pk=artifact.pk).update(reserved_until=timezone.now() - timedelta(seconds=1))
    hit = sample_builds.cached_sample(env.job, client=env.client)
    artifact.refresh_from_db()
    assert hit.cache_hit
    assert hit.image_id == built.image_id
    assert artifact.reserved_until > timezone.now()
    assert len(env.builds) == 1
    with sample_builds._build_lock(f"sample-key:{built.build_key}"):
        assert sample_builds.cached_sample(env.job, client=env.client) is None


@pytest.mark.django_db
def test_changed_source_owner_lock_and_backend_invalidate(sample_builder, settings):
    env = sample_builder
    results = [sample_builds.build_sample(env.job, client=env.client)]
    env.job.source = "print('changed')"
    results.append(sample_builds.build_sample(env.job, client=env.client))
    env.job.created_by_id = UserFactory().pk
    results.append(sample_builds.build_sample(env.job, client=env.client))
    env.job.resolved_lock_blob = "a" * 64  # a new resolved lock is a new build identity
    results.append(sample_builds.build_sample(env.job, client=env.client))
    settings.SAMPLE_BUILD_BACKEND = "buildkit"
    results.append(sample_builds.build_sample(env.job, client=env.client))

    assert len({result.build_key for result in results}) == 5
    assert len(env.builds) == 5
    assert env.builds[-1]["cache_namespace"] == f"owner-{env.job.created_by_id}"


@pytest.mark.django_db
def test_forced_refresh_keeps_previous_generation_and_bypasses_backend_cache(sample_builder):
    env = sample_builder
    first = sample_builds.build_sample(env.job, client=env.client)
    refreshed = sample_builds.build_sample(env.job, client=env.client, force_rebuild=True)
    reused = sample_builds.build_sample(env.job, client=env.client)

    assert refreshed.image_id != first.image_id
    assert refreshed.image_tag != first.image_tag
    assert refreshed.build_key == first.build_key
    assert reused.image_id == refreshed.image_id
    assert env.builds[-1]["force_rebuild"] is True
    assert SampleBuildArtifact.objects.filter(build_key=first.build_key).count() == 2


@pytest.mark.django_db
def test_missing_or_expired_image_rebuilds(sample_builder):
    env = sample_builder
    first = sample_builds.build_sample(env.job, client=env.client)
    env.images.pop(first.image_id)
    second = sample_builds.build_sample(env.job, client=env.client)
    SampleBuildArtifact.objects.filter(image_id=second.image_id).update(
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    third = sample_builds.build_sample(env.job, client=env.client)

    assert not second.cache_hit
    assert not third.cache_hit
    assert len(env.builds) == 3
    assert env.builds[1]["force_rebuild"] is False  # missing image can restore its layers
    assert env.builds[2]["force_rebuild"] is True  # expiry must actually resolve dependencies afresh


@pytest.mark.django_db
def test_failed_build_does_not_publish_artifact(sample_builder, monkeypatch):
    env = sample_builder

    def fail(*args, **kwargs):
        msg = "broken dependency"
        raise RuntimeError(msg)

    monkeypatch.setattr(sample_builds.build_backend, "build", fail)
    with pytest.raises(RuntimeError, match="broken dependency"):
        sample_builds.build_sample(env.job, client=env.client)
    assert not SampleBuildArtifact.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_identical_builds_share_one_generation(sample_builder, monkeypatch):
    env = sample_builder
    entered = threading.Event()
    release = threading.Event()
    build = sample_builds.build_backend.build

    def paused_build(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return build(*args, **kwargs)

    def request():
        try:
            return sample_builds.build_sample(env.job, client=env.client)
        finally:
            connections.close_all()

    monkeypatch.setattr(sample_builds.build_backend, "build", paused_build)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(request)
        assert entered.wait(10)
        second = pool.submit(request)
        release.set()
        results = [first.result(timeout=10), second.result(timeout=10)]
    assert len(env.builds) == 1
    assert results[0].image_id == results[1].image_id
    assert sum(result.cache_hit for result in results) == 1


@pytest.mark.django_db
def test_cancelled_wait_does_not_build(sample_builder):
    env = sample_builder
    with pytest.raises(sample_builds.BuildWaitCancelledError):
        sample_builds.build_sample(env.job, client=env.client, cancelled=lambda: True)
    assert not env.builds


def test_context_hash_ignores_mtime_but_covers_build_files_and_rejects_links(tmp_path, settings):
    settings.SAMPLE_BUILD_BACKEND = "legacy"
    job = _run(created_by_id=1)
    (tmp_path / "Dockerfile").write_text("FROM scratch")
    source = tmp_path / "app.py"
    source.write_text("print('same')")

    def key():
        return sample_builds._context_key(job, tmp_path, platform="linux/amd64", daemon_id="d")

    first = key()
    os.utime(source, (1, 1))
    assert key() == first
    (tmp_path / ".dockerignore").write_text("data")
    assert key() != first
    source.unlink()
    source.symlink_to(tmp_path / "Dockerfile")
    with pytest.raises(ValueError, match="special file"):
        key()


def _expire_reservation(artifact):
    SampleBuildArtifact.objects.filter(pk=artifact.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1),
        reserved_until=timezone.now() - timedelta(seconds=1),
    )


@pytest.mark.django_db
def test_cleanup_preserves_stopped_containers_and_handoff_reservations(sample_builder):
    env = sample_builder
    first = sample_builds.build_sample(env.job, client=env.client)
    row = SampleBuildArtifact.objects.get(image_id=first.image_id)
    _expire_reservation(row)
    container = ContainerInstanceFactory(image=first.image_tag, status=ContainerInstance.Status.STOPPED)
    assert sample_builds.cleanup_sample_images(client=env.client)["protected"] == 1

    container.status = ContainerInstance.Status.REMOVED
    container.save(update_fields=["status"])
    SampleBuildArtifact.objects.filter(pk=row.pk).update(reserved_until=timezone.now() + timedelta(seconds=60))
    assert sample_builds.cleanup_sample_images(client=env.client)["protected"] == 1
    _expire_reservation(row)
    stats = sample_builds.cleanup_sample_images(client=env.client)
    assert stats["removed"] == 1
    assert not SampleBuildArtifact.objects.exists()
    env.client.images.remove.assert_called_once_with(first.image_tag, force=False)


@pytest.mark.django_db
def test_cleanup_protects_actual_evaluation_container(sample_builder):
    env = sample_builder
    first = sample_builds.build_sample(env.job, client=env.client)
    row = SampleBuildArtifact.objects.get(image_id=first.image_id)
    _expire_reservation(row)
    env.client.containers.list.return_value = [SimpleNamespace(attrs={"Image": first.image_id})]
    assert sample_builds.cleanup_sample_images(client=env.client)["protected"] == 1
    env.client.images.remove.assert_not_called()


@pytest.mark.django_db
def test_cleanup_evicts_oldest_unreferenced_image_for_budget(sample_builder, settings):
    env = sample_builder
    settings.SAMPLE_IMAGE_CACHE_BYTES = 1024
    first = sample_builds.build_sample(env.job, client=env.client)
    env.job.source = "print('second')"
    second = sample_builds.build_sample(env.job, client=env.client)
    past = timezone.now() - timedelta(minutes=5)
    SampleBuildArtifact.objects.all().update(reserved_until=past)
    SampleBuildArtifact.objects.filter(image_id=first.image_id).update(last_used_at=past)

    stats = sample_builds.cleanup_sample_images(client=env.client)
    assert stats["removed"] == 1
    assert SampleBuildArtifact.objects.get().image_id == second.image_id


@pytest.mark.django_db
def test_cleanup_skips_key_with_build_in_flight(sample_builder):
    env = sample_builder
    built = sample_builds.build_sample(env.job, client=env.client)
    _expire_reservation(SampleBuildArtifact.objects.get(image_id=built.image_id))
    with sample_builds._build_lock(f"sample-key:{built.build_key}"):
        assert sample_builds.cleanup_sample_images(client=env.client)["protected"] == 1
    env.client.images.remove.assert_not_called()


@pytest.mark.django_db
def test_cleanup_reaps_only_old_untracked_exports(sample_builder):
    env = sample_builder
    recent = timezone.now()
    old = recent - timedelta(days=8)

    def image(tag, created):
        return SimpleNamespace(
            id=f"sha256:{tag}",
            attrs={
                "RepoTags": [f"llmlab/sample:{tag}"],
                "Created": created.isoformat(),
                "Size": 1024,
                "Config": {"Labels": {"llmlab.sample_key": f"key-{tag}"}},
            },
        )

    env.client.images.list.return_value = [image("old", old), image("recent", recent)]
    stats = sample_builds.cleanup_sample_images(client=env.client)
    assert stats["removed"] == 1
    env.client.images.remove.assert_called_once_with("llmlab/sample:old", force=False)


@pytest.mark.django_db(transaction=True)
def test_cleanup_skips_uncommitted_web_request_reservation(sample_builder, settings):
    if connections["default"].vendor != "postgresql":
        pytest.skip("PostgreSQL row-lock behavior")
    env = sample_builder
    settings.SAMPLE_IMAGE_CACHE_BYTES = 0
    built = sample_builds.build_sample(env.job, client=env.client)
    SampleBuildArtifact.objects.filter(image_id=built.image_id).update(
        reserved_until=timezone.now() - timedelta(seconds=1),
    )
    saved = threading.Event()
    release = threading.Event()

    def web_request():
        try:
            with transaction.atomic():
                hit = sample_builds.cached_sample(env.job, client=env.client)
                assert hit is not None
                saved.set()
                assert release.wait(10)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(web_request)
        assert saved.wait(10)
        try:
            stats = sample_builds.cleanup_sample_images(client=env.client)
            assert stats["protected"] == 1
            env.client.images.remove.assert_not_called()
        finally:
            release.set()
        request.result(timeout=10)
