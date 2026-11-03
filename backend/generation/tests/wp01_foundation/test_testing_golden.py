"""WP-01 testing infrastructure: the frozen golden library fixture and its parent app.

Until WP-02a/WP-02b land, run with ``--ds=backend.generation.tests.wp01_foundation.settings_u1``.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter

import pytest

from backend.generation import blobs
from backend.generation import identity
from backend.generation.contracts.library import iter_content_refs
from backend.generation.contracts.library import validate_content
from backend.generation.models import RunFile
from backend.generation.models import choices
from backend.generation.testing import factories
from backend.generation.testing.golden_library import HASHES_PATH
from backend.generation.testing.golden_library import golden_hashes
from backend.generation.testing.golden_library import install_golden_library
from backend.generation.testing.golden_library import install_parent_run
from backend.generation.testing.golden_library import load_golden_library
from backend.generation.testing.golden_library import parent_app_files

LIBRARY = load_golden_library()


def test_hashes_are_frozen():
    """A failure means the fixture or a content contract changed: regenerate only on purpose."""
    assert golden_hashes() == json.loads(HASHES_PATH.read_text(encoding="utf-8"))


def test_contents_match_the_spec_minimum():
    counts = Counter(entry.kind for entry in LIBRARY)
    assert counts["task"] == 2
    assert counts["stack"] == 2
    assert counts["starter"] == 3
    for kind in ("style", "guidance", "prompt", "prompt_set", "harness_preset", "sampling", "resource_policy"):
        assert counts[kind] >= 1, kind
    assert counts["eval_suite"] == counts["detector"] == 1
    assert {entry.content["level"] for entry in LIBRARY.of("starter")} == {"minimal", "bare"}


@pytest.mark.parametrize("entry", list(LIBRARY), ids=lambda e: e.key)
def test_every_entry_validates(entry):
    validate_content(entry.kind, entry.content)


def test_system_tasks_have_all_prose_variants():
    for entry in LIBRARY.of("task"):
        assert set(entry.content["prose"]) == {"minimal", "standard", "exhaustive"}, entry.slug


def _refs(value, key=None):
    if key == "engine":
        return  # "jinja2-sandbox@1" is an engine id, not a library ref
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*@1", value):
        yield value
    elif isinstance(value, dict):
        for name, item in value.items():
            yield from _refs(item, name)
    elif isinstance(value, list):
        for item in value:
            yield from _refs(item)


def test_references_resolve_inside_the_library():
    pinned = {(entry.kind, identity.pinned_ref(entry.slug, 1, entry.content_hash())) for entry in LIBRARY}
    for entry in LIBRARY:
        assert not list(_refs(entry.content)), f"{entry.key} keeps an unpinned ref"
        for location, kind, ref in iter_content_refs(entry.kind, entry.content):
            assert (kind, ref) in pinned, f"{entry.key} {location} names {ref}"
    starters = {entry.slug: entry for entry in LIBRARY.of("starter")}
    stack = LIBRARY.get("stack", "flask-react")
    assert starters["flask-react-minimal"].content["stack_profile"] == identity.pinned_ref(
        "flask-react",
        1,
        stack.content_hash(),
    )


def test_both_stacks_have_a_starter_at_a_shared_level():
    """A 2 tasks x 2 stacks golden plan (13.3) with one fixed starter_level must find a starter per stack."""
    levels: dict[str, set[str]] = {}
    for entry in LIBRARY.of("starter"):
        stack_slug = entry.content["stack_profile"].split("@", 1)[0]
        levels.setdefault(stack_slug, set()).add(entry.content["level"])
    assert set(levels) == {entry.slug for entry in LIBRARY.of("stack")}
    assert "minimal" in set.intersection(*levels.values())
    prompts = {entry.slug: entry for entry in LIBRARY.of("prompt")}
    assert prompts["stack-flask-react"].content["role"] == "fragment"
    assert prompts["brief-app-default"].content["role"] == "brief"


def test_starter_files_are_content_addressed():
    for entry in LIBRARY.of("starter"):
        assert set(entry.content["files"]) == set(entry.files)
        for path, record in entry.content["files"].items():
            assert record["blob"] == identity.bytes_sha(entry.files[path])
        assert "Dockerfile" in entry.files


def test_parent_app_describes_its_endpoints_and_components():
    parent = LIBRARY.parent
    files = parent_app_files()
    task = LIBRARY.get("task", parent.meta["task"].split("@")[0])
    assert set(parent.meta["endpoints"]) <= {endpoint["id"] for endpoint in task.content["endpoints"]}
    for endpoint in parent.meta["endpoints"].values():
        path, function = endpoint["handler"].split("::")
        assert f"def {function}(" in files[path].decode()
    for name, path in parent.meta["components"].items():
        assert f"function {name}(" in files[path].decode()
    for path, data in files.items():
        if path.endswith(".py"):
            ast.parse(data.decode(), filename=path)


def test_parent_app_carries_the_detector_pattern():
    detector = LIBRARY.get("detector", "py-sql-fstring")
    pattern = re.compile(detector.content["rule"])
    hits = [path for path, data in parent_app_files().items() if pattern.search(data.decode(errors="replace"))]
    assert hits == ["backend/todos.py"]


@pytest.mark.django_db
def test_install_creates_system_rows_once(user):
    installed = install_golden_library(created_by=user)
    again = install_golden_library()
    hashes = json.loads(HASHES_PATH.read_text(encoding="utf-8"))
    for entry in LIBRARY:
        row = installed.get(entry.kind, entry.slug)
        row.full_clean()
        assert row.content_hash == hashes[entry.key], entry.key
        assert again.get(entry.kind, entry.slug).pk == row.pk
        if entry.kind != "task":
            assert (row.is_system, row.visibility) == (True, choices.Visibility.SYSTEM)
    starter = installed.get("starter", "flask-react-minimal")
    assert starter.stack_profile == installed.get("stack", "flask-react")
    for record in starter.content["files"].values():
        assert blobs.exists(record["blob"])
    assert installed.get("resource_policy", "frugal").tier == choices.PolicyTier.FRUGAL
    task = installed.get("task", "crud_todo_list")
    assert (task.task.is_system, task.task.latest_version) == (True, 1)
    assert set(task.prose_hashes) == {"minimal", "standard", "exhaustive"}


@pytest.mark.django_db
def test_parent_run_is_a_usable_parent_with_its_files():
    run = install_parent_run()
    run.full_clean()
    assert (run.kind, run.outcome, run.usable) == (choices.RunKind.APP_AGENTIC, choices.Outcome.TESTED, True)
    assert run.tree_hash == json.loads(HASHES_PATH.read_text(encoding="utf-8"))["parent_app"]
    origins = dict(RunFile.objects.filter(run=run).values_list("path", "origin"))
    assert origins["Dockerfile"] == choices.FileOrigin.STARTER
    assert origins["backend/app.py"] == choices.FileOrigin.MODIFIED
    assert origins["backend/todos.py"] == choices.FileOrigin.AUTHORED
    assert set(origins) == set(parent_app_files())
    files = {f.path: f for f in RunFile.objects.filter(run=run)}
    assert files["backend/tests/test_todos.py"].is_test is True
    assert files["backend/todos.py"].language == "python"
    assert blobs.get_bytes(files["backend/todos.py"].blob) == parent_app_files()["backend/todos.py"]
    # The parent works as a slot parent.
    slot_set = factories.SlotSetFactory.create(parent_run=run)
    slot_set.full_clean()
    target = factories.SlotTargetFactory.create(
        slot_set=slot_set,
        path="backend/todos.py",
        content_sha=identity.text_sha(parent_app_files()["backend/todos.py"].decode()),
    )
    target.full_clean()
