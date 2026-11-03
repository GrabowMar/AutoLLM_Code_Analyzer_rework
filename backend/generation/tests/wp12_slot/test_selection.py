"""Address resolution, links, relocation, 409 on stale, matching and overlaps (spec section 7.4)."""

from __future__ import annotations

from typing import Any

import pytest

from backend.generation import identity
from backend.generation.contracts.slot import SlotAddress
from backend.generation.models import SlotTarget
from backend.generation.slot import selection
from backend.generation.slot.errors import AddressStaleError
from backend.generation.slot.errors import SlotInvalidError
from backend.generation.slot.freeze import freeze
from backend.generation.slot.selection import anchors_for
from backend.generation.tests.wp12_slot.helpers import make_parent
from backend.generation.tests.wp12_slot.helpers import parent_text

pytestmark = pytest.mark.django_db


def test_endpoint_target_resolves_handler_links_checks_and_probes(slot_set: Any) -> None:
    preview = selection.preview(
        slot_set,
        {"kind": "endpoint", "endpoint": {"method": "PATCH", "path": "/api/todos/{id}"}},
    )
    address = SlotAddress.model_validate(preview["address"])
    assert address.symbol_id == "backend/todos.py::update_todo"
    assert address.endpoint.spec_endpoint_id == "todos.update"
    text = parent_text(slot_set.parent_run, "backend/todos.py")
    assert preview["original_text"] == text.encode()[address.span.start_byte : address.span.end_byte].decode()
    assert preview["original_text"].startswith('@todos.patch("/<int:todo_id>")')
    assert address.content_sha == identity.text_sha(preview["original_text"])
    assert preview["linked_endpoints"] == ["todos.update"]
    assert preview["linked_checks"] == ["todos.update.ok"]
    assert preview["linked_probes"] == ["probe.todos.idor"]
    assert preview["default_hole_mode"] == "stub"
    assert preview["allowlist"] == ["backend/todos.py"]
    assert preview["match_key"] == "endpoint:todos.update"
    assert "raise NotImplementedError" in preview["hole_applied_text"]
    assert "default_hole_mode" in preview["defaults"]
    assert preview["target_hash"] == identity.target_hash(
        parent_tree=slot_set.frozen_tree_hash,
        address=address.identity(),
        original_sha=address.content_sha,
        allowlist=["backend/todos.py"],
        interface=preview["interface"],
    )
    assert preview["interface"]["signature"] == "def update_todo(todo_id: int)"


def test_links_by_kind(slot_set: Any) -> None:
    file_links = selection.preview(slot_set, {"kind": "file", "path": "backend/todos.py"})
    assert file_links["linked_endpoints"] == ["todos.create", "todos.delete", "todos.list", "todos.update"]
    assert file_links["default_hole_mode"] == "remove"
    helper = selection.preview(slot_set, {"kind": "symbol", "symbol_id": "backend/auth.py::login_required"})
    assert helper["linked_endpoints"] == ["todos.create", "todos.delete", "todos.list", "todos.update"]
    serialize = selection.preview(slot_set, {"kind": "symbol", "symbol_id": "backend/todos.py::serialize"})
    assert serialize["linked_endpoints"] == ["todos.create", "todos.list", "todos.update"]
    region = selection.preview(
        slot_set,
        {"kind": "region", "path": "backend/todos.py", "span": {"start_line": 20, "end_line": 23}},
    )
    assert region["linked_endpoints"] == ["todos.list"]
    assert SlotAddress.model_validate(region["address"]).span.start_line == 20
    component = selection.preview(slot_set, {"kind": "component", "component": "TodoList"})
    assert component["default_hole_mode"] == "remove"  # a whole-file component
    # TodoList calls api(`/todos?q=...`) and api(`/todos/${id}`, {method: "PATCH"}) through the /api helper.
    assert component["linked_endpoints"] == ["todos.list", "todos.update"]
    assert component["match_key"] == "component:TodoList"


def test_region_lines_outside_the_file_are_refused(slot_set: Any) -> None:
    with pytest.raises(SlotInvalidError):
        selection.preview(
            slot_set,
            {"kind": "region", "path": "backend/db.py", "span": {"start_line": 5, "end_line": 900}},
        )
    with pytest.raises(SlotInvalidError):
        selection.preview(slot_set, {"kind": "symbol", "symbol_id": "backend/todos.py::nope"})


def test_relocation_by_anchors_and_409_when_stale(slot_set: Any) -> None:
    source = parent_text(slot_set.parent_run, "backend/todos.py").encode()
    real = selection.preview(
        slot_set,
        {"kind": "region", "path": "backend/todos.py", "span": {"start_line": 20, "end_line": 23}},
    )
    address = SlotAddress.model_validate(real["address"])
    # The client still holds lines 18-21 (e.g. selected in an older view) but knows the content and anchors.
    moved = selection.preview(
        slot_set,
        {
            "kind": "region",
            "path": "backend/todos.py",
            "span": {"start_line": 18, "end_line": 21},
            "content_sha": address.content_sha,
            "anchors": anchors_for(source, address.span).model_dump(),
        },
    )
    assert SlotAddress.model_validate(moved["address"]).span == address.span
    with pytest.raises(AddressStaleError) as excinfo:
        selection.preview(
            slot_set,
            {
                "kind": "region",
                "path": "backend/todos.py",
                "span": {"start_line": 18, "end_line": 21},
                "content_sha": identity.text_sha("something that is not in the parent"),
            },
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "address_stale"


def test_symbol_relocated_by_symbol_id(slot_set: Any) -> None:
    preview = selection.preview(slot_set, {"kind": "symbol", "symbol_id": "backend/todos.py::serialize"})
    sha = preview["address"]["content_sha"]
    # A wrong span with the right symbol id and content: the symbol wins.
    again = selection.preview(
        slot_set,
        {"kind": "symbol", "symbol_id": "backend/todos.py::serialize", "content_sha": sha},
    )
    assert again["address"] == preview["address"]


def test_create_target_is_idempotent_and_queues_validation(
    slot_set: Any,
    researcher: Any,
    django_capture_on_commit_callbacks: Any,
) -> None:
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        target = selection.create_target(
            slot_set,
            {"kind": "endpoint", "endpoint": {"method": "POST", "path": "/api/auth/login"}},
            user=researcher,
        )
    assert target.validation["status"] == "pending"
    assert len(target.validation["runs"]) == 4  # original x3, holed x1
    assert len(callbacks) == 1  # enqueue_evaluation after commit
    assert target.kind == "endpoint"
    assert target.match_key == "endpoint:auth.login"
    assert target.leak["version"] == 1
    again = selection.create_target(
        slot_set,
        {"kind": "endpoint", "endpoint": {"method": "POST", "path": "/api/auth/login"}},
        user=researcher,
    )
    assert again.pk == target.pk
    assert SlotTarget.objects.filter(slot_set=slot_set).count() == 1


def test_edited_linked_checks_are_recorded(slot_set: Any, researcher: Any) -> None:
    target = selection.create_target(
        slot_set,
        {
            "kind": "endpoint",
            "endpoint": {"method": "GET", "path": "/api/todos"},
            "linked_checks": ["todos.list.ok", "extra.check"],
        },
        user=researcher,
    )
    assert target.linked_checks_source == "edited"
    assert target.linked_checks_diff == {"added": ["extra.check"], "removed": ["todos.search.matches"]}


def test_overlapping_targets_are_warned(slot_set: Any, researcher: Any) -> None:
    selection.create_target(slot_set, {"kind": "symbol", "symbol_id": "backend/todos.py::list_todos"}, user=researcher)
    preview = selection.preview(
        slot_set,
        {"kind": "region", "path": "backend/todos.py", "span": {"start_line": 20, "end_line": 22}},
    )
    assert [i["code"] for i in preview["issues"]] == ["overlapping_targets"]


def test_match_finds_the_same_endpoint_in_other_parents(slot_set: Any, golden: Any, researcher: Any) -> None:
    other = freeze(make_parent(golden, researcher, model="openai/gpt-oss-120b"), name="other", user=researcher)
    target = selection.create_target(
        slot_set,
        {
            "kind": "endpoint",
            "endpoint": {"method": "PATCH", "path": "/api/todos/{id}"},
            "default_hole_mode": "remove",
        },
        user=researcher,
    )
    result = selection.match(target, "same_task", user=researcher)
    (created,) = result["created"]
    assert created.slot_set_id == other.pk
    assert created.match_key == target.match_key
    assert created.default_hole_mode == "remove"
    assert created.address["symbol_id"] == "backend/todos.py::update_todo"
    assert result["unmatched"] == []
    no_key = selection.create_target(
        slot_set,
        {"kind": "region", "path": "backend/db.py", "span": {"start_line": 1, "end_line": 3}},
        user=researcher,
    )
    assert selection.match(no_key, "same_task")["unmatched"] == [{"slot_set": str(other.pk), "reason": "no_match_key"}]
