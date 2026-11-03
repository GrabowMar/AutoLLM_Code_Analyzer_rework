"""Seeding the stack fragments, profiles and starters, and the ``build_starters`` command (spec 11.1, 11.4).

Done-when check: every phase-A (profile, level) starter is seeded.
"""

from __future__ import annotations

import io
import json

import pytest
from django.core.management import CommandError
from django.core.management import call_command

from backend.generation import blobs
from backend.generation.build import dockerfiles
from backend.generation.build import locks
from backend.generation.build import seeding
from backend.generation.build import starters
from backend.generation.models import PromptTemplate
from backend.generation.models import StackProfile
from backend.generation.models import Starter
from backend.generation.models import choices
from backend.generation.testing import factories

from .helpers import phase_a_entries

pytestmark = pytest.mark.django_db


def _statuses(records) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for record in records:
        out.setdefault(record.status, []).append(record.slug)
    return out


def test_seed_stack_library_seeds_every_phase_a_starter():
    records = seeding.seed_stack_library()
    statuses = _statuses(records)
    assert "error" not in statuses, [r for r in records if r.status == "error"]
    assert PromptTemplate.objects.filter(slug__startswith="stack-").count() == 16
    assert PromptTemplate.objects.filter(slug="run-contract-free").exists()
    assert StackProfile.objects.count() == 17
    expected = {starters.starter_slug(e.stack, e.level) for e in phase_a_entries()}
    assert set(Starter.objects.values_list("slug", flat=True)) == expected
    assert len(expected) == 33
    for starter in Starter.objects.select_related("stack_profile"):
        assert starter.is_system
        assert starter.visibility == choices.Visibility.SYSTEM
        assert starter.content["stack_profile"] == starter.stack_profile.pinned_ref
        assert starter.level == starter.content["level"]
        for entry in starter.content["files"].values():
            assert isinstance(blobs.get_bytes(entry["blob"]), bytes)  # every file blob is stored (raises if not)
    assert set(statuses.get("skipped", [])) == {
        starters.starter_slug(e.stack, e.level) for e in starters.compose_plan() if e.phase == "B"
    }


def test_seeding_is_idempotent_and_versions_on_change(monkeypatch):
    seeding.seed_stack_library()
    again = seeding.seed_stack_library()
    assert set(_statuses(again)) <= {"unchanged", "skipped"}
    before = Starter.objects.latest("flask-react-minimal")

    real = dockerfiles.base_images()
    moved = dict(real)
    moved["node"] = dockerfiles.BaseImageSpec("node", real["node"].ref, "sha256:" + "3" * 64)
    monkeypatch.setattr(dockerfiles, "base_images", lambda path=None: moved)
    changed = starters.seed_starters(stacks=["flask-react", "flask-none"])
    by_slug = {r.slug: r for r in changed}
    assert by_slug["flask-react-minimal"].status == "created"
    assert by_slug["flask-react-minimal"].version == 2
    assert by_slug["flask-none-minimal"].status == "unchanged"  # no node stage without a frontend
    after = Starter.objects.latest("flask-react-minimal")
    assert after.version == 2
    assert after.derived_from_id == before.pk
    assert after.tree_hash != before.tree_hash
    before.refresh_from_db()
    assert before.version == 1  # the old version is kept, immutable


def test_seeding_never_touches_a_user_slug():
    seeding.seed_stack_library(with_starters=False)
    profile = StackProfile.objects.latest("express-none")
    user_starter = factories.StarterFactory(slug="express-none-bare", stack_profile=profile)
    records = {r.slug: r for r in starters.seed_starters(stacks=["express-none"])}
    assert records["express-none-bare"].status == "error"
    assert "user" in records["express-none-bare"].reason
    assert records["express-none-minimal"].status == "created"
    user_starter.refresh_from_db()
    assert user_starter.version == 1


def test_unseeded_profiles_are_skipped_not_failed():
    records = starters.seed_starters(stacks=["django-vue"])
    assert {r.status for r in records} == {"skipped"}
    assert all("not seeded" in r.reason for r in records)


def test_compose_returns_validated_content_and_stores_blobs():
    seeding.seed_stack_library(with_starters=False, stacks=["fastapi-vue"])
    profile = StackProfile.objects.latest("fastapi-vue")
    content = starters.compose(profile, "minimal")
    assert content.stack_profile == profile.pinned_ref
    assert content.level == "minimal"
    assert "frontend/package-lock.json" in content.lockfiles
    for entry in content.files.values():
        assert isinstance(blobs.get_bytes(entry.blob), bytes)


def test_find_starter_matches_the_profile_version_and_level():
    seeding.seed_stack_library(stacks=["flask-svelte"])
    profile = StackProfile.objects.latest("flask-svelte")
    assert starters.find_starter(profile, "minimal").slug == "flask-svelte-minimal"
    assert starters.find_starter(profile, "full") is None  # phase B level: the resolver's starter_missing


def test_build_starters_check_and_seed_commands():
    out = io.StringIO()
    call_command("build_starters", "--check", "--json", stdout=out)
    report = json.loads(out.getvalue())
    assert {row["status"] for row in report} == {"ok", "skipped"}
    assert sum(row["status"] == "ok" for row in report) == 33

    out = io.StringIO()
    call_command("build_starters", "--with-stacks", "--stack", "express-react", "--dry-run", stdout=out)
    assert not Starter.objects.exists()
    call_command("build_starters", "--with-stacks", "--stack", "express-react", stdout=io.StringIO())
    assert set(Starter.objects.values_list("slug", flat=True)) == {"express-react-bare", "express-react-minimal"}


def test_build_starters_check_fails_on_a_stale_lock(monkeypatch):
    index = locks.lock_index()
    key = "frontend/react/minimal:frontend/package.json"
    monkeypatch.setattr(
        locks,
        "lock_index",
        lambda path=None: {**index, key: {**index[key], "manifest_sha": "0" * 64}},
    )
    with pytest.raises(CommandError, match="failed"):
        call_command("build_starters", "--check", "--stack", "flask-react", stdout=io.StringIO())


def test_build_starters_lock_uses_the_docker_runner(monkeypatch):
    runs: list[list[str]] = []

    def fake_generate(fragments, runner, **kwargs):
        runs.append(list(fragments))
        return [{"key": f, "status": "fresh"} for f in fragments]

    monkeypatch.setattr(locks, "DockerLockRunner", lambda client=None: object())
    monkeypatch.setattr(locks, "generate_locks", fake_generate)
    call_command("build_starters", "--lock", stdout=io.StringIO())
    assert runs == [
        [
            "backend/django/minimal",
            "backend/express/minimal",
            "backend/fastapi/minimal",
            "backend/flask/minimal",
            "frontend/react/minimal",
            "frontend/svelte/minimal",
            "frontend/vue/minimal",
        ],
    ]
