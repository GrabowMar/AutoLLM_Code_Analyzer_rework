"""``seed_library`` loads WP-04's content and ``lint_library`` is clean on it (spec 12.3 WP-04 "Done when")."""

from __future__ import annotations

import pytest

from backend.generation.tests.wp04_content import content

pytestmark = pytest.mark.django_db

OWN_KINDS = ("prompt", "style", "guidance", "prompt_set", "harness_preset", "sampling", "eval_suite", "task")


def _own(key: str) -> bool:
    kind, _, slug = key.partition(":")
    return kind in OWN_KINDS and not slug.startswith(content.FOREIGN_PROMPT_PREFIXES)


def _seeding():
    try:
        from backend.generation.library import seeding
    except ImportError:  # pragma: no cover - WP-03 not merged
        pytest.skip("WP-03 seeding not available")
    return seeding


def test_lint_library_is_clean_on_the_seed_content():
    seeding = _seeding()
    try:
        results = seeding.lint_seed_data(content.DATA_ROOT, kinds=OWN_KINDS)
    except NotImplementedError:
        pytest.skip("WP-03 lint_seed_data not implemented")
    noisy = {
        key: [(i.code, i.message) for i in issues if i.severity in {"error", "warn"}]
        for key, issues in results.items()
        if _own(key)
    }
    assert {key: found for key, found in noisy.items() if found} == {}
    own_keys = {key for key in results if _own(key)}
    assert {f"task:{slug}" for slug in content.task_slugs()} <= own_keys
    assert {f"prompt:{slug}" for slug in content.own_prompts()} <= own_keys


def test_seed_library_loads_everything_and_is_idempotent():
    from backend.generation.models import HarnessPreset
    from backend.generation.models import PromptSet
    from backend.generation.models import StyleDirective
    from backend.generation.models import TaskSpec
    from backend.generation.models import TaskSpecVersion

    seeding = _seeding()
    kinds = ("prompt", "stack", "style", "guidance", "prompt_set", "harness_preset", "sampling", "eval_suite", "task")
    try:
        first = seeding.seed_library(data_dir=content.DATA_ROOT, kinds=kinds)
    except NotImplementedError:
        pytest.skip("WP-03 seed_library not implemented")
    own_errors = [error for error in first["errors"] if _own(error["key"])]
    assert own_errors == []
    assert [item for item in first["skipped"] if _own(item.get("key", ""))] == []
    created = {ref.split("@", 1)[0] for ref in first["created"]}  # pinned refs slug@1#hash12
    own_slugs = set(content.task_slugs()) | set(content.own_prompts())
    for kind in ("style", "guidance", "prompt_set", "harness_preset", "sampling", "eval_suite"):
        own_slugs |= set(content.entries(kind))
    assert own_slugs <= created
    assert all("@1#" in ref for ref in first["created"] if ref.split("@", 1)[0] in own_slugs)

    assert TaskSpec.objects.filter(is_system=True).count() == len(content.task_slugs())
    version = TaskSpecVersion.objects.get(task__slug="crud_todo_list", version=1)
    assert set(version.content["prose"]) == {"minimal", "standard", "exhaustive"}
    assert version.content["prose"] == content.task("crud_todo_list")["prose"]
    archived = set(HarnessPreset.objects.filter(is_archived=True).values_list("slug", flat=True))
    assert archived == {
        f"{h}-{v}" for h in content.HARNESSES if h not in content.ENABLED_HARNESSES for v in ("native", "controlled")
    }
    app_default = PromptSet.objects.get(slug="app-default", version=1)
    assert all("#" in ref for ref in app_default.content["roles"].values()), "refs are pinned at seed time"
    assert StyleDirective.objects.get(slug="model-choice-playful", version=1).content["tone"] == "playful"

    second = seeding.seed_library(data_dir=content.DATA_ROOT, kinds=kinds)
    assert [ref for ref in second["created"] if ref.split("@", 1)[0] in own_slugs] == []
    assert [key for key in second["updated"] if _own(key)] == []
    assert [error for error in second["errors"] if _own(error["key"])] == []
