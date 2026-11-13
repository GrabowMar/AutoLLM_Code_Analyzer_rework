"""Context resolution (deterministic, policy-sensitive) and the leak scan (spec section 7.4)."""

from __future__ import annotations

from typing import Any

import pytest

from backend.generation import blobs
from backend.generation import identity
from backend.generation.contracts.slot import ContextPolicy
from backend.generation.models import RunFile
from backend.generation.slot import leak
from backend.generation.slot import selection
from backend.generation.slot.context import materialize
from backend.generation.slot.context import resolve_context
from backend.generation.slot.executors.restricted_agent import AgentSettings
from backend.generation.slot.executors.restricted_agent import seed_files
from backend.generation.slot.freeze import freeze
from backend.generation.slot.part import part_of
from backend.generation.slot.validate import target_usable
from backend.generation.tests.wp12_slot.helpers import make_parent

pytestmark = pytest.mark.django_db


def _target(slot_set: Any, user: Any, **selection_in: Any) -> Any:
    return selection.create_target(slot_set, selection_in, user=user, validate=False)


def test_related_files_order_and_determinism(slot_set: Any, researcher: Any) -> None:
    target = _target(slot_set, researcher, kind="endpoint", endpoint={"method": "PATCH", "path": "/api/todos/{id}"})
    context = materialize(part_of(target), ContextPolicy())
    paths = [f.path for f in context.files]
    assert paths[0] == "backend/todos.py"  # the slot file with the hole first
    assert paths[1:3] == ["backend/auth.py", "backend/db.py"]  # imported by the slot file (depth 1)
    assert "backend/app.py" in paths  # the entry file
    assert "backend/tests/test_todos.py" not in paths  # parent-authored tests are hidden by default
    assert "raise NotImplementedError" in context.files[0].text
    assert context.resolved == resolve_context(target, ContextPolicy())
    assert context.resolved.sha == resolve_context(target, ContextPolicy()).sha


def test_context_sha_changes_with_policy_and_hole_mode(slot_set: Any, researcher: Any) -> None:
    target = _target(slot_set, researcher, kind="symbol", symbol_id="backend/todos.py::list_todos")
    base = resolve_context(target, ContextPolicy())
    only = resolve_context(target, ContextPolicy(mode="slot_file_only"))
    assert [f.path for f in only.files] == ["backend/todos.py"]
    assert only.sha != base.sha
    with_tests = resolve_context(target, ContextPolicy(include_tests=True))
    assert with_tests.sha != base.sha
    assert "backend/tests/test_todos.py" in [f.path for f in with_tests.files]
    assert resolve_context(target, ContextPolicy(), hole_mode="remove").sha != base.sha
    outline = materialize(part_of(target), ContextPolicy(mode="interface_only"))
    auth = next(f for f in outline.files if f.path == "backend/auth.py")
    assert auth.role == "outline"
    assert "def login_required(view)" in auth.text
    assert "pbkdf2" not in auth.text


def test_context_budget_truncates_whole_files(slot_set: Any, researcher: Any) -> None:
    target = _target(slot_set, researcher, kind="symbol", symbol_id="backend/todos.py::list_todos")
    full = resolve_context(target, ContextPolicy(mode="full_app"))
    small = resolve_context(target, ContextPolicy(mode="full_app", max_context_tokens=full.files[0].tokens + 1))
    assert [f.path for f in small.files] == ["backend/todos.py"]
    assert small.truncated_paths == [f.path for f in full.files[1:]] + full.truncated_paths
    assert small.tokens <= full.files[0].tokens + 1


def test_leak_scan_partial_and_verbatim(slot_set: Any, researcher: Any, golden: Any) -> None:
    clean = _target(slot_set, researcher, kind="endpoint", endpoint={"method": "PATCH", "path": "/api/todos/{id}"})
    # update_todo shares boilerplate with create_todo/delete_todo (decorators, SELECT by id): a partial
    # overlap is a score (a warning above 0.3), never a verbatim copy.
    assert 0 < clean.leak["score"] < leak.VERBATIM_SHARE
    assert not any(match["verbatim"] for match in clean.leak["matches"])
    assert "slot_leak_verbatim" not in [issue.code for issue in leak.leak_issues(clean.leak)]
    assert target_usable(clean) == (False, "validation is pending")  # usable only once validated
    # A parent that ships a verbatim copy of update_todo in another module.
    parent = make_parent(golden, researcher)
    original = selection.preview(slot_set, {"kind": "symbol", "symbol_id": "backend/todos.py::update_todo"})[
        "original_text"
    ]
    copy = f"from flask import g, request\nfrom backend import db\n\n\n{original}\n".encode()
    RunFile.objects.create(
        run=parent,
        path="backend/legacy.py",
        blob=blobs.put_bytes(copy),
        size=len(copy),
        origin="authored",
    )
    rows = RunFile.objects.filter(run=parent).exclude(origin="deleted")
    tree = identity.tree_hash((r.path, r.mode, r.blob) for r in rows)
    type(parent).objects.filter(pk=parent.pk).update(tree_hash=tree)
    parent.refresh_from_db()
    leaky_set = freeze(parent, name="leaky", user=researcher)
    leaky = _target(leaky_set, researcher, kind="symbol", symbol_id="backend/todos.py::update_todo")
    verbatim = [match for match in leaky.leak["matches"] if match["verbatim"]]
    assert [match["path"] for match in verbatim] == ["backend/legacy.py"]
    codes = [issue.code for issue in leak.leak_issues(leaky.leak)]
    assert "slot_leak_verbatim" in codes
    assert leak.leak_issues(leaky.leak, "confirmatory")[0].severity == "error"
    # Single-shot scope (context only) still sees it when the copy is in the context.
    report = leak.scan(leaky, executor="single_shot")
    assert report.scope == ["context"]


def test_score_texts_partial_copy() -> None:
    original = (
        "def f(items):\n"
        "    total = 0\n"
        "    for item in items:\n"
        "        total += item.price * item.qty\n"
        "    return total\n"
    )
    partial = "def g(rows):\n    for item in items:\n        total += item.price * item.qty\n"
    report = leak.score_texts(original, "python", {"other.py": partial})
    assert 0 < report.score < 1
    assert report.matches[0].verbatim is False
    comment_only = "# total += item.price * item.qty for item in items\n"
    assert leak.score_texts(original, "python", {"c.py": comment_only}).score == 0


def test_agent_workspace_excludes_parent_tests_unless_included(slot_set: Any, researcher: Any) -> None:
    target = _target(slot_set, researcher, kind="endpoint", endpoint={"method": "PATCH", "path": "/api/todos/{id}"})
    part = part_of(target)
    hidden = seed_files(
        part,
        AgentSettings(hole_mode="stub", keep_docstring=False, include_tests=False, edit_policy="revert"),
    )
    shown = seed_files(
        part,
        AgentSettings(hole_mode="stub", keep_docstring=False, include_tests=True, edit_policy="revert"),
    )
    assert "backend/tests/test_todos.py" not in hidden
    assert "backend/tests/test_todos.py" in shown
    assert b"raise NotImplementedError" in hidden["backend/todos.py"][0]
    assert b"update_todo(todo_id: int):\n    # __SLOT__" in hidden["backend/todos.py"][0]
