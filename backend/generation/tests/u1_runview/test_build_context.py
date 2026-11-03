"""``build/context.py`` for app runs, ``build_identity`` and the re-pointed ``sample_builds`` (section 6.10)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.generation import blobs
from backend.generation.build.context import BuildContextReport
from backend.generation.build.context import build_identity
from backend.generation.build.context import prepare_build_dir
from backend.generation.models import GenerationRun
from backend.generation.models import choices
from backend.generation.testing import factories
from backend.runtime.models import SampleBuildArtifact
from backend.runtime.services import build_context as shim
from backend.runtime.services import sample_builds

pytestmark = pytest.mark.django_db

O = choices.FileOrigin  # noqa: E741


def add_file(run, path, text, origin=O.AUTHORED, **extra):
    if origin == O.DELETED:
        return factories.RunFileFactory(run=run, path=path, blob="", size=0, origin=origin)
    sha = blobs.put_text(text)
    return factories.RunFileFactory(run=run, path=path, blob=sha, size=len(text), origin=origin, **extra)


@pytest.fixture
def app_run():
    run = factories.GenerationRunFactory(tested=True)
    add_file(run, "Dockerfile", "FROM python:3.13-slim\nCOPY . /app\n", origin=O.STARTER)
    add_file(run, "app.py", "print('sample')\n", origin=O.MODIFIED)
    add_file(run, "run.sh", "#!/bin/sh\nexec python app.py\n", origin=O.AUTHORED, mode=0o755)
    add_file(run, "requirements.txt", "Flask==3.1.0\n", origin=O.AUTHORED, excluded="lockfile")
    add_file(run, "dist/bundle.js", "built\n", origin=O.ARTIFACT)
    add_file(run, "legacy.py", "", origin=O.DELETED)
    return run


@pytest.fixture
def ctx(tmp_path):
    """A build-context directory (``tmp_path`` itself also holds MEDIA_ROOT, i.e. the blob store)."""
    return tmp_path / "ctx"


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def test_shim_reexports_the_seam():
    assert shim.BuildContextReport is BuildContextReport
    assert shim.prepare_build_dir is prepare_build_dir
    assert shim.build_identity is build_identity


def test_prepare_build_dir_writes_the_app_tree_verbatim(app_run, ctx):
    report = BuildContextReport()
    out = prepare_build_dir(app_run, ctx, report=report)
    assert out == ctx
    assert tree(ctx) == {
        "Dockerfile": "FROM python:3.13-slim\nCOPY . /app\n",
        "app.py": "print('sample')\n",
        "run.sh": "#!/bin/sh\nexec python app.py\n",
        "requirements.txt": "Flask==3.1.0\n",
    }
    assert (ctx / "run.sh").stat().st_mode & 0o777 == 0o755
    assert (ctx / "app.py").stat().st_mode & 0o777 == 0o644
    assert report.to_dict() == {"notes": ["tree: 4 files written verbatim"]}


def test_prepare_build_dir_refuses_a_run_without_tree(ctx):
    with pytest.raises(ValueError, match="no collected tree"):
        prepare_build_dir(factories.GenerationRunFactory(), ctx)


def test_prepare_build_dir_adds_platform_lock_files(app_run, ctx):
    lock_text = "flask==3.1.0 --hash=sha256:abc\n"
    lock = blobs.put_json({"ecosystems": {}, "files": {"requirements.lock": blobs.put_text(lock_text)}})
    GenerationRun.objects.filter(pk=app_run.pk).update(resolved_lock_blob=lock, lock_source="platform")
    app_run.refresh_from_db()
    report = BuildContextReport()
    prepare_build_dir(app_run, ctx, report=report)
    assert (ctx / "requirements.lock").read_text() == lock_text
    assert any("lock" in note for note in report.notes)


def test_slot_variant_builds_parent_tree_with_its_slot_file(app_run, ctx):
    target = factories.SlotTargetFactory(slot_set=factories.SlotSetFactory(parent_run=app_run), path="app.py")
    variant = factories.GenerationRunFactory(slot_single_shot=True, slot_target=target)
    add_file(variant, "app.py", "print('variant')\n", origin=O.SLOT)
    prepare_build_dir(variant, ctx)
    files = tree(ctx)
    assert files["app.py"] == "print('variant')\n"
    assert files["Dockerfile"] == "FROM python:3.13-slim\nCOPY . /app\n"
    assert "dist/bundle.js" not in files


def test_build_identity(app_run):
    stack = factories.StackProfileFactory()
    starter = factories.StarterFactory(stack_profile=stack)
    GenerationRun.objects.filter(pk=app_run.pk).update(stack_profile=stack, starter=starter)
    app_run.refresh_from_db()
    assert build_identity(app_run) == {
        "protocol": "app@1",
        "stack": stack.pinned_ref,
        "starter": starter.tree_hash,
        "lock": None,
    }
    target = factories.SlotTargetFactory(slot_set=factories.SlotSetFactory(parent_run=app_run))
    variant = factories.GenerationRunFactory(slot_agent=True, slot_target=target)
    identity = build_identity(variant)
    assert identity["protocol"] == "slot@1"
    assert identity["stack"] == stack.pinned_ref
    assert identity["starter"] == starter.tree_hash


# -- sample_builds through the seam (ported from runtime/tests/test_sample_build_integration.py) ------------


def _client():
    from docker.errors import ImageNotFound

    client = MagicMock()
    client.info.return_value = {"OSType": "linux", "Architecture": "amd64", "ID": "daemon-1"}
    images: dict[str, object] = {}

    def get_image(tag):
        if tag not in images:
            raise ImageNotFound(tag)
        return images[tag]

    client.images.get.side_effect = get_image
    return client, images


@pytest.mark.django_db(transaction=True)
def test_build_sample_builds_once_then_reuses_the_image(app_run, monkeypatch):
    client, images = _client()
    contexts = []

    def build(path, tag, **kwargs):
        from pathlib import Path

        contexts.append(tree(Path(path)))
        image = SimpleNamespace(id="sha256:immutable", attrs={"Size": 128})
        images[tag] = images[image.id] = image
        return SimpleNamespace(image_id=image.id, image_tag=tag, log="built", seconds=1, base_images={})

    backend = MagicMock(side_effect=build)
    monkeypatch.setattr(sample_builds.build_backend, "build", backend)
    first = sample_builds.build_sample(app_run, client=client)
    second = sample_builds.build_sample(app_run, client=client)
    peek = sample_builds.cached_sample(app_run, client=client)
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert peek is not None
    assert peek.cache_hit is True
    assert first.build_key == second.build_key == peek.build_key
    assert first.build_context["notes"][0].startswith("tree: ")
    assert backend.call_count == 1
    assert contexts[0]["app.py"] == "print('sample')\n"
    assert SampleBuildArtifact.objects.count() == 1


def test_context_key_changes_with_build_identity_and_tree(app_run, ctx):
    prepare_build_dir(app_run, ctx)
    key = sample_builds._context_key(app_run, ctx, platform="linux/amd64", daemon_id="d")
    assert key == sample_builds._context_key(app_run, ctx, platform="linux/amd64", daemon_id="d")
    GenerationRun.objects.filter(pk=app_run.pk).update(starter=factories.StarterFactory())
    app_run.refresh_from_db()
    changed = sample_builds._context_key(app_run, ctx, platform="linux/amd64", daemon_id="d")
    assert changed != key
    (ctx / "app.py").write_text("print('edited')\n")
    edited = sample_builds._context_key(app_run, ctx, platform="linux/amd64", daemon_id="d")
    assert edited != changed
