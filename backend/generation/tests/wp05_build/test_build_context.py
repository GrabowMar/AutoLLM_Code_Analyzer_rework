"""``prepare_build_dir`` (spec 6.10, 7.7): verbatim app trees, slot overlay + splice, lock files, and the
free-choice FROM allowlist (``base_not_allowed``)."""

from __future__ import annotations

import pytest

from backend.generation import blobs
from backend.generation.build import context as build_context
from backend.generation.build import dockerfiles
from backend.generation.build.context import BaseNotAllowedError
from backend.generation.build.context import BuildContextReport
from backend.generation.build.context import VariantNotAssembledError
from backend.generation.build.context import prepare_build_dir
from backend.generation.models import GenerationRun
from backend.generation.models import choices
from backend.generation.slot import assemble
from backend.generation.testing import content as samples
from backend.generation.testing import factories
from backend.runtime.services import build_context as shim

pytestmark = pytest.mark.django_db

O = choices.FileOrigin  # noqa: E741
PY_DIGEST = "sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1"


def add_file(run, path, text, origin=O.AUTHORED, **extra):
    data = text.encode() if isinstance(text, str) else text
    return factories.RunFileFactory(
        run=run,
        path=path,
        blob=blobs.put_bytes(data),
        size=len(data),
        origin=origin,
        **extra,
    )


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _set(run, **fields):
    GenerationRun.objects.filter(pk=run.pk).update(**fields)
    run.refresh_from_db()
    return run


@pytest.fixture
def app_run():
    run = factories.GenerationRunFactory(tested=True)
    add_file(run, "Dockerfile", f"FROM python:3.13-slim@{PY_DIGEST}\nCOPY . /app\n", origin=O.STARTER)
    add_file(run, "backend/app.py", "def handler():\n    return 'parent'\r\n", origin=O.MODIFIED)
    add_file(run, "backend/util.py", "X = 1\n", origin=O.AUTHORED)
    add_file(run, "frontend/dist/app.js", "built\n", origin=O.ARTIFACT)
    return run


@pytest.fixture
def free_stack():
    content = samples.stack_content()
    content |= {
        "choice_mode": "free",
        "backend": {"language": "free", "framework": "free", "runtime": "free"},
        "frontend": {"framework": "free", "language": "free", "build_tool": "free"},
        "database": "free",
        "brief_fragment": samples.fake_ref("run-contract-free@1"),
    }
    return factories.StackProfileFactory(slug="free-choice", content=content)


def test_shim_reexports_the_seam_and_its_errors():
    assert shim.prepare_build_dir is prepare_build_dir
    assert shim.BaseNotAllowedError is BaseNotAllowedError
    assert shim.VariantNotAssembledError is VariantNotAssembledError
    assert shim.BASE_NOT_ALLOWED == "base_not_allowed"


def test_app_run_is_written_verbatim_with_its_bytes(app_run, tmp_path):
    out = prepare_build_dir(app_run, tmp_path / "ctx")
    files = tree(out)
    assert files["backend/app.py"] == b"def handler():\n    return 'parent'\r\n"  # CRLF kept: never patched
    assert set(files) == {"Dockerfile", "backend/app.py", "backend/util.py"}


def test_lock_files_are_included_for_app_runs_and_inherited_by_variants(app_run, tmp_path):
    lock_sha = blobs.put_text("flask==3.1.3 --hash=sha256:00\n")
    lock = blobs.put_json({"ecosystems": {}, "files": {"backend/requirements.lock": lock_sha}})
    _set(app_run, resolved_lock_blob=lock, lock_source="platform")
    report = BuildContextReport()
    prepare_build_dir(app_run, tmp_path / "a", report=report)
    assert (tmp_path / "a/backend/requirements.lock").read_text() == "flask==3.1.3 --hash=sha256:00\n"
    assert "lock: 1 platform lock files (platform lock source)" in report.notes

    target = factories.SlotTargetFactory(slot_set=factories.SlotSetFactory(parent_run=app_run))
    variant = factories.GenerationRunFactory(slot_single_shot=True, slot_target=target)
    add_file(variant, "backend/app.py", "def handler():\n    return 'variant'\r\n", origin=O.SLOT)
    prepare_build_dir(variant, tmp_path / "v")
    assert (tmp_path / "v/backend/requirements.lock").is_file()


def test_slot_variant_overlays_its_slot_rows_on_the_parent(app_run, tmp_path):
    target = factories.SlotTargetFactory(slot_set=factories.SlotSetFactory(parent_run=app_run))
    variant = factories.GenerationRunFactory(slot_agent=True, slot_target=target)
    add_file(variant, "backend/app.py", "def handler():\n    return 'variant'\r\n", origin=O.SLOT)
    files = tree(prepare_build_dir(variant, tmp_path / "ctx"))
    assert files["backend/app.py"] == b"def handler():\n    return 'variant'\r\n"
    assert files["backend/util.py"] == b"X = 1\n"
    assert "frontend/dist/app.js" not in files


def test_slot_variant_without_rows_is_spliced_through_the_assemble_hook(app_run, tmp_path, monkeypatch):
    """WP-12's ``slot.assemble.spliced_files`` (faked here) returns the spliced files; no rows are written."""
    target = factories.SlotTargetFactory(slot_set=factories.SlotSetFactory(parent_run=app_run))
    variant = factories.GenerationRunFactory(slot_single_shot=True, slot_target=target)
    seen = []

    def fake_spliced_files(run):
        seen.append(run.pk)
        parent = blobs.get_bytes(run.parent_run.files.get(path="backend/app.py").blob)
        start = parent.index(b"'parent'")
        return {"backend/app.py": parent[:start] + b"'spliced'" + parent[start + len(b"'parent'") :]}

    monkeypatch.setattr(assemble, "spliced_files", fake_spliced_files, raising=False)
    report = BuildContextReport()
    files = tree(prepare_build_dir(variant, tmp_path / "ctx", report=report))
    assert seen == [variant.pk]
    assert files["backend/app.py"] == b"def handler():\n    return 'spliced'\r\n"
    assert files["backend/util.py"] == b"X = 1\n"
    assert "slot: 1 files spliced from the variant's replacement" in report.notes
    assert not variant.files.exists()  # building never writes RunFile rows


def test_slot_variant_without_rows_or_hook_is_refused(app_run, tmp_path, monkeypatch):
    target = factories.SlotTargetFactory(slot_set=factories.SlotSetFactory(parent_run=app_run))
    variant = factories.GenerationRunFactory(slot_single_shot=True, slot_target=target)

    def not_yet(run):
        raise NotImplementedError

    monkeypatch.setattr(assemble, "spliced_files", not_yet, raising=False)
    with pytest.raises(VariantNotAssembledError, match="not implemented"):
        prepare_build_dir(variant, tmp_path / "a")
    monkeypatch.setattr(assemble, "spliced_files", lambda run: {}, raising=False)
    with pytest.raises(VariantNotAssembledError, match="no replacement"):
        prepare_build_dir(variant, tmp_path / "b")


def test_free_choice_dockerfile_within_the_allowlist_builds(free_stack, tmp_path):
    run = factories.GenerationRunFactory(tested=True, stack_profile=free_stack)
    add_file(run, "Dockerfile", "FROM node:24-slim AS ui\nRUN true\nFROM python:3.13-slim\nCOPY --from=ui / /\n")
    add_file(run, "main.py", "print('hi')\n")
    report = BuildContextReport()
    prepare_build_dir(run, tmp_path / "ctx", report=report)
    assert report.bases is not None
    assert report.bases["ok"] is True
    assert report.bases["allowed_bases_sha"] == dockerfiles.allowed_bases_record()["sha"]
    expected = {b["ref"]: b["expected_digest"] for b in report.bases["bases"] if b.get("ref")}
    assert expected["python:3.13-slim"] == PY_DIGEST
    assert report.to_dict()["bases"] == report.bases


@pytest.mark.parametrize(
    ("dockerfile", "reason"),
    [
        ("FROM alpine:3.22\nCMD sh\n", "not_allowlisted"),
        (f"FROM python:3.13-slim@sha256:{'2' * 64}\n", "digest_mismatch"),
        ("ARG IMG=python:3.13-slim\nFROM ${IMG}\n", "unresolvable"),
        (None, "no_dockerfile"),
    ],
)
def test_free_choice_base_not_allowed(free_stack, tmp_path, dockerfile, reason):
    run = factories.GenerationRunFactory(tested=True, stack_profile=free_stack)
    add_file(run, "main.py", "print('hi')\n")
    if dockerfile is not None:
        add_file(run, "Dockerfile", dockerfile)
    report = BuildContextReport()
    with pytest.raises(BaseNotAllowedError) as raised:
        prepare_build_dir(run, tmp_path / "ctx", report=report)
    assert raised.value.reason == "base_not_allowed"
    assert [v["reason"] for v in raised.value.check["violations"]] == [reason]
    assert report.bases == raised.value.check
    assert any(note.startswith("base_not_allowed: ") for note in report.notes)
    assert (tmp_path / "ctx/main.py").is_file()  # the tree is still written as collected


def test_fixed_stacks_are_not_allowlist_checked(app_run, tmp_path):
    stack = factories.StackProfileFactory()
    _set(app_run, stack_profile=stack)
    add_file(app_run, "Dockerfile.alt", "FROM alpine:3.22\n")
    report = BuildContextReport()
    prepare_build_dir(app_run, tmp_path / "ctx", report=report)
    assert report.bases is None
    assert report.to_dict() == {"notes": ["tree: 4 files written verbatim"]}


def test_slot_variant_of_a_free_choice_parent_is_checked_too(free_stack, tmp_path):
    parent = factories.GenerationRunFactory(tested=True, stack_profile=free_stack)
    add_file(parent, "Dockerfile", "FROM python:3.13-slim\n")
    add_file(parent, "main.py", "print('parent')\n")
    target = factories.SlotTargetFactory(slot_set=factories.SlotSetFactory(parent_run=parent))
    variant = factories.GenerationRunFactory(slot_single_shot=True, slot_target=target)
    add_file(variant, "Dockerfile", "FROM ubuntu:24.04\n", origin=O.SLOT)
    with pytest.raises(BaseNotAllowedError):
        prepare_build_dir(variant, tmp_path / "ctx")
    assert build_context._stack(variant) == free_stack


# ---- base pins: a tag-only FROM is built against the frozen allowlist digest (Appendix A #1, 6.10) ----


def _freeze_allowlist(run, record):
    from backend.generation.testing.factories import run_manifest_content

    factories.RunManifestFactory(run=run, manifest={**run_manifest_content(run), "allowed_bases": record})
    run.refresh_from_db()


def test_tag_only_free_choice_from_is_pinned_to_the_allowlisted_digest(free_stack, tmp_path):
    run = factories.GenerationRunFactory(tested=True, stack_profile=free_stack)
    add_file(run, "Dockerfile", "FROM python:3.13-slim\nCOPY . /app\n")
    report = BuildContextReport()
    out = prepare_build_dir(run, tmp_path / "ctx", report=report)
    assert (out / "Dockerfile").read_text() == "FROM python:3.13-slim\nCOPY . /app\n"  # never rewritten
    assert report.base_pins == {"python:3.13-slim": f"python:3.13-slim@{PY_DIGEST}"}
    assert report.build_context_args() == [
        "--build-context",
        f"python:3.13-slim=docker-image://python:3.13-slim@{PY_DIGEST}",
    ]
    assert report.to_dict()["base_pins"] == report.base_pins
    assert report.bases["allowed_bases_source"] == "allowlist"  # no manifest: the current file


def test_re_evaluation_pins_the_digest_frozen_in_the_manifest_not_the_current_one(free_stack, tmp_path, settings):
    run = factories.GenerationRunFactory(tested=True, stack_profile=free_stack)
    add_file(run, "Dockerfile", "FROM python:3.13-slim\n")
    frozen = dockerfiles.allowed_bases_record()
    _freeze_allowlist(run, {"sha": frozen["sha"], "refs": frozen["refs"]})
    identity_before = build_context.build_identity(run)
    assert identity_before["allowed_bases"] == frozen["sha"]
    # The allowlist file moves python:3.13-slim to a new digest after generation.
    bumped = tmp_path / "allowed.yaml"
    bumped.write_text(f'bases:\n  - {{ref: "python:3.13-slim", digest: "sha256:{"9" * 64}"}}\n')
    settings.SAMPLE_BUILD_ALLOWED_BASES = str(bumped)
    report = BuildContextReport()
    prepare_build_dir(run, tmp_path / "ctx", report=report)
    assert report.base_pins == {"python:3.13-slim": f"python:3.13-slim@{PY_DIGEST}"}
    assert report.bases["allowed_bases_source"] == "manifest"
    assert report.bases["allowed_bases_sha"] == frozen["sha"]
    assert build_context.build_identity(run) == identity_before  # the cache key follows the frozen digests


def test_unreproducible_frozen_allowlist_is_an_infra_error(free_stack, tmp_path, settings):
    run = factories.GenerationRunFactory(tested=True, stack_profile=free_stack)
    add_file(run, "Dockerfile", "FROM python:3.13-slim\n")
    record = dockerfiles.allowed_bases_record()
    _freeze_allowlist(run, {"sha": record["sha"], "refs": record["tags"]})  # undigested refs
    prepare_build_dir(run, tmp_path / "same")  # same sha as the current file: its digests are the run's
    bumped = tmp_path / "allowed.yaml"
    bumped.write_text(f'bases:\n  - {{ref: "python:3.13-slim", digest: "sha256:{"9" * 64}"}}\n')
    settings.SAMPLE_BUILD_ALLOWED_BASES = str(bumped)
    with pytest.raises(build_context.AllowedBasesChangedError) as raised:
        prepare_build_dir(run, tmp_path / "ctx")
    assert raised.value.reason == "allowed_bases_changed"
    assert not isinstance(raised.value, BaseNotAllowedError)  # never a model-attributed build_fail
    assert shim.AllowedBasesChangedError is build_context.AllowedBasesChangedError


# ---- lock completeness: a starter lock next to an edited manifest is the lock step's job ----


def _starter_lock(fragment="backend/flask/minimal"):
    from backend.generation.build import locks

    manifest = (dockerfiles.STARTERS_ROOT / fragment / "backend/requirements.txt").read_bytes()
    lock = (locks.LOCK_ROOT / fragment / "backend/requirements.lock").read_bytes()
    return manifest, lock


def test_minimal_starter_lock_with_a_model_added_requirement_is_reported_incomplete(tmp_path):
    manifest, lock = _starter_lock()
    run = factories.GenerationRunFactory(tested=True)
    add_file(run, "Dockerfile", f"FROM python:3.13-slim@{PY_DIGEST}\n", origin=O.STARTER)
    add_file(run, "backend/requirements.txt", manifest + b"requests\n", origin=O.MODIFIED)
    add_file(run, "backend/requirements.lock", lock, origin=O.STARTER)
    report = BuildContextReport()
    prepare_build_dir(run, tmp_path / "ctx", report=report)
    assert (tmp_path / "ctx/backend/requirements.lock").read_bytes() == lock  # written as collected
    assert report.locks == [
        {
            "manifest": "backend/requirements.txt",
            "lock": "backend/requirements.lock",
            "ecosystem": "pypi",
            "status": "incomplete",
            "reason": "stale_starter_lock",
            "source": "starter",
        },
    ]
    assert report.locks_incomplete == report.locks
    assert "lock_incomplete: backend/requirements.lock (stale_starter_lock; starter lock)" in report.notes
    assert report.to_dict()["locks"] == report.locks


def test_unchanged_minimal_starter_lock_is_complete_and_a_platform_lock_replaces_a_stale_one(tmp_path):
    manifest, lock = _starter_lock()
    run = factories.GenerationRunFactory(tested=True)
    add_file(run, "backend/requirements.txt", manifest, origin=O.STARTER)
    add_file(run, "backend/requirements.lock", lock, origin=O.STARTER)
    report = BuildContextReport()
    prepare_build_dir(run, tmp_path / "a", report=report)
    assert [(c["status"], c["source"]) for c in report.locks] == [("complete", "starter")]
    assert not report.locks_incomplete

    edited = factories.GenerationRunFactory(tested=True)
    add_file(edited, "backend/requirements.txt", b"flask==3.1.3\ngunicorn==26.2.0\nrequests==2.32.5\n", O.MODIFIED)
    add_file(edited, "backend/requirements.lock", lock, origin=O.STARTER)
    platform = b"flask==3.1.3 \\\n    --hash=sha256:00\ngunicorn==26.2.0\nrequests==2.32.5 --hash=sha256:11\n"
    blob = blobs.put_json({"ecosystems": {}, "files": {"backend/requirements.lock": blobs.put_bytes(platform)}})
    _set(edited, resolved_lock_blob=blob, lock_source="platform")
    report = BuildContextReport()
    prepare_build_dir(edited, tmp_path / "b", report=report)
    assert [(c["status"], c["source"]) for c in report.locks] == [("complete", "platform")]
