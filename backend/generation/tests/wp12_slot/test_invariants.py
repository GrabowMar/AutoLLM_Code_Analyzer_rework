"""Section 13.4 invariants owned by WP-12, plus the slot parts of appendix A (#9, #16).

* Slot targets: linked checks discriminate (every kept check passed 3/3 on the original and failed on the holed
  tree); a verbatim leak blocks the target.
* Slot single-shot: no auto-fixing, no repair, no merging: an accepted replacement is the model's code verbatim, and
  an invalid answer is a data point (``output_invalid``), never retried.
* The original is a reference point: it runs the variants' selection and is a ``reference`` run, not a cell.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.generation import blobs
from backend.generation.models import RunFile
from backend.generation.models import SlotTarget
from backend.generation.slot import leak
from backend.generation.slot import selection
from backend.generation.slot import validate
from backend.generation.slot.executors.single_shot import SlotRequest
from backend.generation.slot.executors.single_shot import parse_output
from backend.generation.testing.fake_slot import SlotEvalScript
from backend.generation.testing.fake_slot import chat_response

pytestmark = pytest.mark.django_db

SELECTIONS = [
    {"kind": "endpoint", "endpoint": {"method": "GET", "path": "/api/todos"}},
    {"kind": "endpoint", "endpoint": {"method": "PATCH", "path": "/api/todos/{id}"}},
    {"kind": "symbol", "symbol_id": "backend/auth.py::login_required"},
    {"kind": "file", "path": "backend/todos.py"},
]


@pytest.mark.parametrize(
    "script",
    [
        SlotEvalScript(),
        SlotEvalScript(holed={"todos.list.ok": "pass", "todos.update.ok": "pass"}),
        SlotEvalScript(original_runs={0: {"todos.search.matches": "fail"}, 2: {"todos.delete.ok": "fail"}}),
        SlotEvalScript(original={"todos.create.ok": "fail"}, holed={"todos.search.matches": "error"}),
    ],
)
def test_linked_checks_always_discriminate(
    slot_set: Any,
    researcher: Any,
    monkeypatch: Any,
    script: SlotEvalScript,
) -> None:
    from backend.generation.testing.fake_eval import FakeEvaluator

    FakeEvaluator(results=script).install(monkeypatch)
    for selection_in in SELECTIONS:
        target = selection.create_target(slot_set, selection_in, user=researcher, validate=False)
        validation = validate.validate_target(target, synchronous=True)
        target.refresh_from_db()
        assert validation.status in {"ok", "uninformative"}
        for check in target.linked_checks:
            assert validation.original_pass[check] == [True, True, True]
            assert validation.holed_fail[check] is True
            assert check not in validation.flaky_checks
        assert set(target.linked_checks) | set(validation.dropped_checks) == set(validation.original_pass)
        assert (validation.status == "uninformative") == (not target.linked_checks)
    assert SlotTarget.objects.filter(slot_set=slot_set).count() == len(SELECTIONS)


def test_a_verbatim_leak_blocks_the_target(slot_set: Any, researcher: Any, evaluator: Any) -> None:
    target = selection.create_target(slot_set, SELECTIONS[1], user=researcher, validate=False)
    validate.validate_target(target, synchronous=True)
    target.refresh_from_db()
    assert validate.target_usable(target) == (True, "")
    target.leak = {
        "score": 1.0,
        "matches": [{"path": "backend/copy.py", "shingles": 40, "verbatim": True}],
        "scope": ["context"],
        "version": 1,
    }
    target.save(update_fields=["leak"])
    usable, reason = validate.target_usable(target)
    assert usable is False
    assert "verbatim" in reason
    issues = leak.leak_issues(target.leak, "exploratory")
    assert {(i.code, i.severity) for i in issues} >= {("slot_leak_verbatim", "error")}


@pytest.mark.parametrize(
    "code",
    [
        "def f():\n\treturn  1   # odd spacing kept\n",
        "def f():\r\n    return 1\r\n",
        "x = 'unterminated",  # syntactically broken code is still the model's answer: no repair
    ],
)
def test_single_shot_replacement_is_used_verbatim(code: str) -> None:
    request = SlotRequest(
        body={},
        files_mode=False,
        allowlist=("a.py",),
        max_output_tokens=None,
        structured_output=False,
        est_input_tokens=1,
    )
    result = parse_output(chat_response(json.dumps({"code": code})), request)
    assert result.status == "ok"
    assert result.replacement == code


def test_the_original_is_a_reference_run_with_the_variants_selection(
    slot_set: Any,
    researcher: Any,
    evaluator: Any,
) -> None:
    from backend.generation.slot.evaluate import evaluate_original

    target = selection.create_target(slot_set, SELECTIONS[1], user=researcher, validate=False)
    validate.validate_target(target, synchronous=True)
    target.refresh_from_db()
    original = target.original_run
    assert (original.kind, original.reference_of, original.cell_id, original.study_id) == (
        "reference",
        "slot_original",
        None,
        None,
    )
    # Its tree is the parent's, row for row and blob for blob.
    parent_rows = dict(
        RunFile.objects.filter(run=slot_set.parent_run).exclude(origin="deleted").values_list("path", "blob"),
    )
    original_rows = dict(RunFile.objects.filter(run=original).values_list("path", "blob"))
    assert original_rows == parent_rows
    evaluator.calls.clear()
    evaluate_original(target)
    assert evaluator.calls[0].only_checks == target.validation["discriminating_checks"]
    assert blobs.get_text(target.original_blob) == selection.preview(slot_set, SELECTIONS[1])["original_text"]
