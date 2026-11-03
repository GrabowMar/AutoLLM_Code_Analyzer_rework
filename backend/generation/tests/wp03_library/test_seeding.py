"""seed_library: golden hashes, idempotence, user rows, new versions, @latest, archived rows, composition."""

from __future__ import annotations

import json
import shutil

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from backend.generation.contracts.library import StarterContent
from backend.generation.library import service
from backend.generation.library.seeding import SEED_ORDER
from backend.generation.library.seeding import lint_seed_data
from backend.generation.library.seeding import load_seed_entries
from backend.generation.library.seeding import seed_library
from backend.generation.models import LIBRARY_MODELS
from backend.generation.models import PromptTemplate
from backend.generation.models import StackProfile
from backend.generation.models import TaskSpec
from backend.generation.testing import content as builders
from backend.generation.testing.golden_library import GOLDEN_ROOT
from backend.generation.testing.golden_library import HASHES_PATH

pytestmark = pytest.mark.django_db

# The golden minimal starters (WP-01 fixture) ship manifests without lockfiles, which WP-05's starter rules
# report (lint_starter_file_missing); CHANGES "WP-03 -> WP-01" asks for lockfiles in the fixture.
GOLDEN_STARTER_LINT = {
    "starter:flask-react-minimal@1": ["lint_starter_file_missing", "lint_starter_file_missing"],
    "starter:fastapi-vue-minimal@1": ["lint_starter_file_missing", "lint_starter_file_missing"],
}


@pytest.fixture
def data_dir(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(
        GOLDEN_ROOT,
        root,
        ignore=shutil.ignore_patterns("__pycache__", "*.py", "parent_app*", "hashes.json"),
    )
    return root


def stored_hashes() -> dict[str, str]:
    hashes = {}
    for kind, model in LIBRARY_MODELS.items():
        for row in model.objects.all():
            hashes[f"{kind}:{row.task.slug if kind == 'task' else row.slug}"] = row.content_hash
    return hashes


def test_the_golden_library_seeds_to_its_frozen_hashes():
    report = seed_library(data_dir=GOLDEN_ROOT)
    assert report["errors"] == []
    assert report["skipped"] == []
    lint_errors = {key: [issue["code"] for issue in issues] for key, issues in report["lint_errors"].items()}
    assert lint_errors.keys() <= GOLDEN_STARTER_LINT.keys()
    assert all(codes == ["lint_starter_file_missing"] * len(codes) for codes in lint_errors.values())
    frozen = json.loads(HASHES_PATH.read_text())
    frozen.pop("parent_app")
    assert stored_hashes() == frozen
    assert all(row.is_system for row in PromptTemplate.objects.all())
    assert TaskSpec.objects.get(slug="crud_todo_list").is_system


def test_seeding_twice_changes_nothing():
    first = seed_library(data_dir=GOLDEN_ROOT)
    assert first["changes"] == len(first["created"]) > 30
    second = seed_library(data_dir=GOLDEN_ROOT)
    assert second["changes"] == 0
    assert second["unchanged"] == len(first["created"])


def test_user_rows_are_never_touched(data_dir, alice):
    seed_library(data_dir=data_dir, kinds=["prompt"])
    user_stack = service.create(
        "stack",
        slug="flask-react",
        name="Alice's stack",
        content=builders.stack_content(brief_fragment="stack-flask-react@1"),
        user=alice,
    )
    report = seed_library(data_dir=data_dir)
    assert {"key": "stack:flask-react", "reason": "the slug belongs to a user; the system entry is skipped"} in report[
        "skipped"
    ]
    user_stack.refresh_from_db()
    assert (user_stack.is_system, user_stack.created_by, StackProfile.objects.filter(slug="flask-react").count()) == (
        False,
        alice,
        1,
    )
    # The golden starter names the system flask-react@1#<hash>, which the user's row does not match.
    assert any(item["key"] == "starter:flask-react-minimal" for item in report["errors"])


def test_changed_content_creates_a_system_version(data_dir):
    seed_library(data_dir=data_dir)
    template = data_dir / "prompts" / "style-tailwind.jinja2"
    template.write_text("Use Tailwind CSS utility classes only.\n")
    report = seed_library(data_dir=data_dir)
    assert report["created"] == [service.resolve_ref("style-tailwind@2", kind="prompt").pinned_ref]
    v2 = service.resolve_ref("style-tailwind@2", kind="prompt")
    assert v2.is_system
    assert v2.derived_from.version == 1
    # Styles pin "style-tailwind@1": they keep v1 until their file says otherwise.
    assert seed_library(data_dir=data_dir)["changes"] == 0


def test_latest_refs_pin_the_newest_system_version(data_dir):
    seed_library(data_dir=data_dir)
    (data_dir / "prompts" / "style-tailwind.jinja2").write_text("Tailwind, v2.\n")
    style_file = data_dir / "styles" / "tailwind.yaml"
    style_file.write_text(style_file.read_text().replace("style-tailwind@1", "style-tailwind@latest"))
    report = seed_library(data_dir=data_dir)
    style = service.latest("style", "tailwind")
    assert style.version == 2
    assert style.content["fragment"].startswith("style-tailwind@2#")
    assert style.pinned_ref in report["created"]


def test_names_and_archived_flags_follow_the_files(data_dir):
    seed_library(data_dir=data_dir)
    preset = data_dir / "harness_presets" / "opencode-controlled.yaml"
    text = preset.read_text()
    preset.write_text("is_archived: true\n" + text.replace("name:", "name: Renamed #", 1))
    report = seed_library(data_dir=data_dir)
    row = service.resolve_ref("opencode-controlled@1", kind="harness_preset")
    assert row.is_archived
    assert row.name.startswith("Renamed")
    assert report["created"] == []
    assert report["updated"] == ["harness_preset:opencode-controlled@1"]


def test_dry_run_writes_nothing():
    report = seed_library(data_dir=GOLDEN_ROOT, dry_run=True)
    assert report["dry_run"]
    assert len(report["created"]) > 30
    assert stored_hashes() == {}


def test_lint_errors_are_reported_and_seeded(data_dir):
    (data_dir / "prompts" / "budget-neutral.jinja2").write_text("{{ undeclared }}\n")
    report = seed_library(data_dir=data_dir)
    assert [i["code"] for i in report["lint_errors"]["prompt:budget-neutral@1"]] == ["lint_undeclared_variable"]
    assert report["errors"] == []  # dependents still pin it


def test_invalid_content_is_not_seeded(data_dir):
    meta = data_dir / "prompts" / "budget-neutral.meta.yaml"
    meta.write_text(meta.read_text().replace("role: fragment", "role: nonsense"))
    report = seed_library(data_dir=data_dir)
    errors = {item["key"] for item in report["errors"]}
    assert {"prompt:budget-neutral", "prompt_set:app-default"} <= errors


def test_kinds_filter(data_dir):
    report = seed_library(data_dir=data_dir, kinds=["prompt"])
    assert {ref.split("@")[0] for ref in report["created"]} == {
        e.slug for e in load_seed_entries(data_dir) if e.kind == "prompt"
    }


def test_composed_starters_go_through_build_starters(data_dir, monkeypatch):
    starters_dir = data_dir / "starters"
    shutil.rmtree(starters_dir)
    starters_dir.mkdir()
    (starters_dir / "compose.yaml").write_text(
        "starters:\n  - {stack: flask-react, levels: [minimal]}\n  - {stack: go-react, levels: [bare], phase: B}\n",
    )
    from backend.generation import blobs
    from backend.generation.build import starters

    dockerfile = f"FROM {builders.BASE_IMAGE['digest']}\n".encode()

    def compose(stack_profile, level):
        sha = blobs.put_bytes(dockerfile)
        return StarterContent.model_validate(
            builders.starter_content(
                stack_profile.pinned_ref,
                level="bare",
                files={"Dockerfile": {"blob": sha, "mode": 0o644}},
            ),
        )

    monkeypatch.setattr(starters, "compose", compose)
    report = seed_library(data_dir=data_dir)
    assert any(ref.startswith("flask-react-minimal@1#") for ref in report["created"])
    assert report["deferred"] == [{"key": "starter:go-react-bare", "reason": "phase B starter (wave 3)"}]
    starter = service.latest("starter", "flask-react-minimal")
    assert starter.stack_profile == service.latest("stack", "flask-react")


def test_composition_not_ready_is_skipped(data_dir, monkeypatch):
    (data_dir / "starters" / "compose.yaml").write_text("starters:\n  - {stack: fastapi-vue, levels: [full]}\n")
    from backend.generation.build import starters

    def compose(stack_profile, level):
        msg = "WP-05: full level later"
        raise NotImplementedError(msg)

    monkeypatch.setattr(starters, "compose", compose)
    report = seed_library(data_dir=data_dir)
    assert {
        "key": "starter:fastapi-vue-full",
        "reason": "starter composition unavailable: WP-05: full level later",
    } in report["skipped"]


def test_lint_reports_phase_a_starters_that_cannot_be_composed(data_dir, monkeypatch, capsys):
    (data_dir / "starters" / "compose.yaml").write_text(
        "starters:\n"
        "  - {stack: no-such-stack, levels: [minimal]}\n"
        "  - {stack: fastapi-vue, levels: [full]}\n"
        "  - {stack: go-react, levels: [bare], phase: B}\n",
    )
    from backend.generation.build import starters

    def compose(stack_profile, level):
        msg = "WP-05: full level later"
        raise NotImplementedError(msg)

    monkeypatch.setattr(starters, "compose", compose)
    deferred: list[dict] = []
    results = lint_seed_data(data_dir, deferred=deferred)
    assert [i.code for i in results["starter:no-such-stack-minimal"]] == ["lint_unknown_library_ref"]
    assert [i.severity for i in results["starter:no-such-stack-minimal"]] == ["error"]
    assert [i.code for i in results["starter:fastapi-vue-full"]] == ["lint_starter_file_missing"]
    assert "WP-05: full level later" in results["starter:fastapi-vue-full"][0].message
    assert "starter:go-react-bare" not in results
    assert deferred == [{"key": "starter:go-react-bare", "reason": "phase B starter (wave 3)"}]
    with pytest.raises(CommandError):
        call_command("lint_library", "--data-dir", str(data_dir), "--kind", "starter")
    out = capsys.readouterr().out
    assert "lint_unknown_library_ref [compose.stack]" in out
    assert "defer starter:go-react-bare: phase B starter (wave 3)" in out
    assert "1 deferred (not linted)" in out


def test_lint_seed_data_on_an_empty_database():
    results = lint_seed_data(GOLDEN_ROOT)
    assert len(results) == len(load_seed_entries(GOLDEN_ROOT))
    errors = {key: [i.code for i in issues if i.severity == "error"] for key, issues in results.items()}
    known = {key.rsplit("@", 1)[0] for key in GOLDEN_STARTER_LINT}
    assert {key: codes for key, codes in errors.items() if codes and key not in known} == {}
    assert stored_hashes() == {}


class TestCommands:
    def test_seed_and_lint_commands(self, capsys):
        kinds = [arg for kind in SEED_ORDER if kind != "starter" for arg in ("--kind", kind)]
        call_command("seed_library", "--data-dir", str(GOLDEN_ROOT), "--strict", *kinds)
        assert "created" in capsys.readouterr().out
        call_command("seed_library", "--data-dir", str(GOLDEN_ROOT), "--json", *kinds)
        assert json.loads(capsys.readouterr().out)["changes"] == 0
        call_command("lint_library", "--data-dir", str(GOLDEN_ROOT), "--quiet", *kinds)
        call_command("lint_library", "--db", *kinds)
        assert "entries linted" in capsys.readouterr().out

    def test_lint_library_fails_on_errors(self, data_dir):
        (data_dir / "prompts" / "budget-neutral.jinja2").write_text("{% if %}\n")
        with pytest.raises(CommandError):
            call_command("lint_library", "--data-dir", str(data_dir), "--kind", "prompt")

    def test_strict_seed_fails_on_errors(self, data_dir):
        (data_dir / "prompts" / "budget-neutral.jinja2").write_text("{% if %}\n")
        with pytest.raises(CommandError):
            call_command("seed_library", "--data-dir", str(data_dir), "--strict")
