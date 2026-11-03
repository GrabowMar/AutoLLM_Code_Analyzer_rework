"""Assembly at byte offsets (CRLF originals, slot rows only) and the restricted agent's diff (7.6, 7.7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.generation import blobs
from backend.generation import identity
from backend.generation.models import GenerationRun
from backend.generation.models import RunFile
from backend.generation.models import SlotVariantResult
from backend.generation.models.choices import RunKind
from backend.generation.runs.view import run_view
from backend.generation.slot import selection
from backend.generation.slot.assemble import assemble
from backend.generation.slot.assemble import assemble_tree
from backend.generation.slot.assemble import splice
from backend.generation.slot.executors import restricted_agent
from backend.generation.slot.freeze import freeze
from backend.generation.slot.part import part_of
from backend.generation.tests.wp12_slot.helpers import make_parent
from backend.generation.tests.wp12_slot.helpers import parent_text
from backend.generation.tests.wp12_slot.helpers import slot_run

PATCH = {"kind": "endpoint", "endpoint": {"method": "PATCH", "path": "/api/todos/{id}"}}
NEW = '@todos.patch("/<int:todo_id>")\n@login_required\ndef update_todo(todo_id: int):\n    return {}, 410'


def test_splice_line_endings_and_trailing_newline() -> None:
    crlf = b"a = 1\r\ndef f():\r\n    return 1\r\nb = 2\r\n"
    start = crlf.index(b"def")
    end = crlf.index(b"\r\nb")
    out = splice(crlf, start, end, "def f():\n    return 2\n")
    assert out == b"a = 1\r\ndef f():\r\n    return 2\r\nb = 2\r\n"
    whole = splice(b"x\n", 0, 2, "y")
    assert whole == b"y\n"  # the original slice ended with a newline: ensured


@pytest.mark.django_db
def test_assembly_writes_only_slot_rows_and_the_view_overlays_the_parent(slot_set: Any, researcher: Any) -> None:
    target = selection.create_target(slot_set, PATCH, user=researcher, validate=False)
    run = slot_run(target)
    rows = assemble(run, NEW)
    assert [(r.path, r.origin) for r in rows] == [("backend/todos.py", "slot")]
    assert RunFile.objects.filter(run=run).count() == 1
    view = run_view(run)
    app = {entry.path: entry for entry in view.files("app")}
    assert app["backend/todos.py"].origin == "slot"
    assert app["backend/auth.py"].origin == "parent"
    entries = [(e.path, e.mode, e.blob) for e in view.files("app")]
    assert run.tree_hash == identity.tree_hash(entries)
    # Assembling the original back gives the parent's tree.
    original = assemble_tree(target, part_of(target).original_text)
    assert original.tree_hash == slot_set.frozen_tree_hash


@pytest.mark.django_db
def test_assembly_keeps_crlf_of_the_original_file(golden: Any, researcher: Any, acceptance: Any) -> None:
    parent = make_parent(golden, researcher)
    row = RunFile.objects.get(run=parent, path="backend/db.py")
    crlf = blobs.get_bytes(row.blob).replace(b"\n", b"\r\n")
    row.blob = blobs.put_bytes(crlf)
    row.size = len(crlf)
    row.save()
    rows = RunFile.objects.filter(run=parent).exclude(origin="deleted")
    GenerationRun.objects.filter(pk=parent.pk).update(
        tree_hash=identity.tree_hash((r.path, r.mode, r.blob) for r in rows),
    )
    parent.refresh_from_db()
    slot_set = freeze(parent, name="crlf", user=researcher)
    target = selection.create_target(
        slot_set,
        {"kind": "symbol", "symbol_id": "backend/db.py::init"},
        user=researcher,
        validate=False,
    )
    run = slot_run(target)
    (new_row,) = assemble(run, "def init() -> None:\n    pass\n")
    data = blobs.get_bytes(new_row.blob)
    span = target.address["span"]
    assert data[: span["start_byte"]] == crlf[: span["start_byte"]]
    assert b"def init() -> None:\r\n    pass" in data
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert data.endswith(crlf[span["end_byte"] :])


@pytest.mark.django_db
class TestRestrictedAgent:
    def _workspace(self, run: GenerationRun, tmp_path: Path) -> Path:
        root = tmp_path / "workspace"  # tmp_path itself is MEDIA_ROOT (the blob store) in these tests
        restricted_agent.seed_workspace(run, root)
        return root

    def test_prepare_and_seed(self, slot_set: Any, researcher: Any, tmp_path: Path) -> None:
        target = selection.create_target(slot_set, PATCH, user=researcher, validate=False)
        run = slot_run(target, kind=RunKind.SLOT_AGENT)
        context = restricted_agent.prepare(run)
        assert context.allowlist == ("backend/todos.py",)
        assert context.target.hole_mode == "stub"
        assert context.target.include_tests is False
        root = tmp_path / "workspace"
        seed_hash = restricted_agent.seed_workspace(run, root)
        assert not (root / "backend/tests/test_todos.py").exists()
        assert b"raise NotImplementedError" in (root / "backend/todos.py").read_bytes()
        assert seed_hash
        overlay = restricted_agent.hole_overlay(run)
        assert list(overlay) == ["backend/todos.py"]

    def _edit_slot(self, root: Path, replacement: str) -> None:
        path = root / "backend/todos.py"
        text = path.read_text()
        marker = "def update_todo(todo_id: int):\n    # __SLOT__\n    raise NotImplementedError"
        assert marker in text
        path.write_text(text.replace(marker, replacement))

    def test_valid_edit_inside_the_slot(self, slot_set: Any, researcher: Any, tmp_path: Path) -> None:
        target = selection.create_target(slot_set, PATCH, user=researcher, validate=False)
        run = slot_run(target, kind=RunKind.SLOT_AGENT)
        root = self._workspace(run, tmp_path)
        self._edit_slot(root, "def update_todo(todo_id: int):\n    return {}, 410")
        (root / "node_modules/x").mkdir(parents=True)
        (root / "node_modules/x/index.js").write_text("installed")  # dependency installs are not edits
        assert restricted_agent.finalize(run, root) == "evaluate"
        result = SlotVariantResult.objects.get(run=run)
        assert result.parse_status == "ok"
        assert result.out_of_slot_edits == []
        assert result.edit_violation is False
        (row,) = RunFile.objects.filter(run=run)
        expected = parent_text(slot_set.parent_run, "backend/todos.py")
        span = target.address["span"]
        replacement = (
            '@todos.patch("/<int:todo_id>")\n@login_required\ndef update_todo(todo_id: int):\n    return {}, 410'
        )
        assert blobs.get_text(row.blob) == expected[: span["start_byte"]] + replacement + expected[span["end_byte"] :]

    @pytest.mark.parametrize(("policy", "outcome"), [("revert", "evaluate"), ("fail", "done")])
    def test_out_of_allowlist_edit(
        self,
        slot_set: Any,
        researcher: Any,
        tmp_path: Path,
        policy: str,
        outcome: str,
    ) -> None:
        target = selection.create_target(slot_set, {**PATCH, "edit_policy": policy}, user=researcher, validate=False)
        run = slot_run(target, kind=RunKind.SLOT_AGENT)
        root = self._workspace(run, tmp_path)
        self._edit_slot(root, "def update_todo(todo_id: int):\n    return {}, 410")
        (root / "backend/auth.py").write_text("# rewritten\n")
        (root / "backend/requirements.txt").write_text("flask==3.1.0\nrequests==2.32.0\n")
        assert restricted_agent.finalize(run, root) == outcome
        result = SlotVariantResult.objects.get(run=run)
        edits = {edit["path"]: edit["kind"] for edit in result.out_of_slot_edits}
        assert edits == {"backend/auth.py": "modified", "backend/requirements.txt": "modified"}
        assert result.edit_violation is True
        assert result.new_dependencies == ["requests"]
        # finalize sets the outcome on the instance; the stage transition persists it.
        if policy == "fail":
            assert (run.outcome, run.outcome_reason) == ("output_invalid", "edit_violation")
            assert not RunFile.objects.filter(run=run).exists()
        else:
            # Reverted: the variant is the slot region only, assembled on the untouched parent.
            assert [r.path for r in RunFile.objects.filter(run=run)] == ["backend/todos.py"]

    def test_outside_span_edit_in_the_slot_file(self, slot_set: Any, researcher: Any, tmp_path: Path) -> None:
        target = selection.create_target(slot_set, PATCH, user=researcher, validate=False)
        run = slot_run(target, kind=RunKind.SLOT_AGENT)
        root = self._workspace(run, tmp_path)
        self._edit_slot(root, "def update_todo(todo_id: int):\n    return {}, 410")
        path = root / "backend/todos.py"
        path.write_text(path.read_text().replace("def serialize(row) -> dict:", "def serialize(row):"))
        assert restricted_agent.finalize(run, root) == "done"
        result = SlotVariantResult.objects.get(run=run)
        assert [edit["path"] for edit in result.out_of_slot_edits] == ["backend/todos.py#outside_span"]
        assert (run.outcome, run.outcome_reason) == ("output_invalid", "edit_violation")

    def test_untouched_hole_is_empty(self, slot_set: Any, researcher: Any, tmp_path: Path) -> None:
        target = selection.create_target(slot_set, PATCH, user=researcher, validate=False)
        run = slot_run(target, kind=RunKind.SLOT_AGENT)
        root = self._workspace(run, tmp_path)
        assert restricted_agent.finalize(run, root) == "done"
        assert SlotVariantResult.objects.get(run=run).parse_status == "empty"
        assert (run.outcome, run.outcome_reason) == ("output_invalid", "empty")

    def test_collect_variant_returns_the_agentic_collected_record(
        self,
        slot_set: Any,
        researcher: Any,
        tmp_path: Path,
    ) -> None:
        from backend.generation.agentic.workspace import SeedFile

        target = selection.create_target(slot_set, PATCH, user=researcher, validate=False)
        run = slot_run(target, kind=RunKind.SLOT_AGENT)
        part = part_of(target)
        settings = restricted_agent.settings_for(run, part)
        seed = {p: SeedFile(d, m) for p, (d, m) in restricted_agent.seed_files(part, settings).items()}
        final = dict(seed)
        text = seed["backend/todos.py"].data.replace(
            b"    # __SLOT__\n    raise NotImplementedError",
            b"    return {}, 410",
        )
        final["backend/todos.py"] = SeedFile(text, 0o644)
        collected = restricted_agent.collect_variant(run, final=final, seed=seed)
        assert collected.has_model_code
        assert collected.invalid_reason is None
        assert [row.path for row in collected.rows] == ["backend/todos.py"]
        assert collected.tree_hash == run.tree_hash
        invalid = restricted_agent.collect_variant(run, final=seed, seed=seed)
        assert not invalid.has_model_code
        assert invalid.invalid_reason == "empty"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("selection_in", "replacement"),
    [
        (PATCH, NEW + "\r\n"),
        (
            {"kind": "file", "path": "backend/db.py"},
            {"backend/db.py": "import sqlite3\n\n\ndef connect():\n    return None\n"},
        ),
    ],
)
def test_spliced_files_equals_the_stored_rows(
    slot_set: Any,
    researcher: Any,
    selection_in: Any,
    replacement: Any,
) -> None:
    from backend.generation.slot.assemble import spliced_files
    from backend.generation.slot.variants import record_variant

    target = selection.create_target(slot_set, selection_in, user=researcher, validate=False)
    run = slot_run(target)
    assert spliced_files(run) == {}  # nothing recorded yet
    record_variant(run, part_of(target), parse_status="ok", replacement=replacement)
    pure = spliced_files(run)
    assert not RunFile.objects.filter(run=run).exists()  # the hook writes nothing
    rows = assemble(run, replacement)
    assert pure == {row.path: blobs.get_bytes(row.blob) for row in rows}


TODO_LIST = {"kind": "component", "component": "TodoList"}
STUBBED = {"slot": {"hole_mode": "stub"}}


@pytest.mark.django_db
def test_whole_file_component_under_a_stub_condition_is_spliced_not_replaced(
    slot_set: Any,
    researcher: Any,
) -> None:
    """The run's hole mode decides the answer's shape: stub -> span code, spliced (imports kept)."""
    from backend.generation.slot.assemble import spliced_files
    from backend.generation.slot.prompt import uses_files_output
    from backend.generation.slot.variants import record_variant

    target = selection.create_target(slot_set, TODO_LIST, user=researcher, validate=False)
    part = part_of(target)
    assert part.whole_file_component
    assert target.default_hole_mode == "remove"
    assert not uses_files_output(part, "stub")  # the model answers with the component's code only
    span = target.address["span"]
    original = part.source
    new_code = part.original_text.replace("Search", "Find")
    run = slot_run(target, manifest_updates=STUBBED)
    (row,) = assemble(run, new_code)
    data = blobs.get_bytes(row.blob)
    assert data.startswith(original[: span["start_byte"]])  # the imports are still there
    assert b'import { api } from "../api.js";' in data
    assert data.endswith(original[span["end_byte"] :])
    assert b'placeholder="Find"' in data
    record_variant(run, part, parse_status="ok", replacement=new_code)
    assert spliced_files(run) == {row.path: data}

    # The restricted agent under the same condition extracts span code and assembles the same way.
    agent = slot_run(target, kind=RunKind.SLOT_AGENT, manifest_updates=STUBBED, seed=99)
    settings = restricted_agent.settings_for(agent, part)
    assert settings.hole_mode == "stub"
    seed = {p: d for p, (d, _) in restricted_agent.seed_files(part, settings).items()}
    hole = part.hole("stub")
    final = dict(seed)
    final[part.path] = hole.prefix + new_code.encode() + hole.suffix
    record = restricted_agent.record_from_workspace(agent, final, seed)
    assert record.outcome == "evaluate"
    (agent_row,) = record.rows
    assert blobs.get_bytes(agent_row.blob) == data


@pytest.mark.django_db
@pytest.mark.parametrize("policy", ["revert", "fail"])
def test_allowlisted_dependency_edit_is_in_slot_and_assembled(
    slot_set: Any,
    researcher: Any,
    policy: str,
) -> None:
    """Section 7.6: a dependency manifest on the allowlist is not an out-of-slot edit; its bytes ship."""
    from backend.generation.slot.assemble import recorded_replacement
    from backend.generation.slot.assemble import spliced_files

    allowlist = ["backend/requirements.txt", "backend/todos.py"]
    target = selection.create_target(
        slot_set,
        {**PATCH, "edit_policy": policy, "allowlist": allowlist},
        user=researcher,
        validate=False,
    )
    run = slot_run(target, kind=RunKind.SLOT_AGENT)
    part = part_of(target)
    settings = restricted_agent.settings_for(run, part)
    seed = {p: d for p, (d, _) in restricted_agent.seed_files(part, settings).items()}
    final = dict(seed)
    final["backend/todos.py"] = seed["backend/todos.py"].replace(
        b"    # __SLOT__\n    raise NotImplementedError",
        b"    import requests\n    return {}, 410",
    )
    requirements = seed["backend/requirements.txt"] + b"requests==2.32.0\n"
    final["backend/requirements.txt"] = requirements
    final["backend/auth.py"] = b"# rewritten\n"  # outside the allowlist: still an out-of-slot edit
    record = restricted_agent.record_from_workspace(run, final, seed)
    result = SlotVariantResult.objects.get(run=run)
    assert [edit["path"] for edit in result.out_of_slot_edits] == ["backend/auth.py"]
    assert result.new_dependencies == ["requests"]
    if policy == "fail":
        assert record.invalid_reason == "edit_violation"  # auth.py, not the allowlisted manifest
        return
    assert record.outcome == "evaluate"
    stored = {row.path: blobs.get_bytes(row.blob) for row in RunFile.objects.filter(run=run)}
    assert sorted(stored) == allowlist  # auth.py reverted, the manifest kept
    assert stored["backend/requirements.txt"] == requirements
    assert stored["backend/todos.py"] == final["backend/todos.py"]
    assert set(recorded_replacement(run)) == set(allowlist)  # type: ignore[arg-type]
    assert spliced_files(run) == stored


@pytest.mark.django_db
def test_allowlisted_dependency_edit_alone_is_not_a_violation(slot_set: Any, researcher: Any) -> None:
    target = selection.create_target(
        slot_set,
        {**PATCH, "edit_policy": "fail", "allowlist": ["backend/requirements.txt", "backend/todos.py"]},
        user=researcher,
        validate=False,
    )
    run = slot_run(target, kind=RunKind.SLOT_AGENT)
    part = part_of(target)
    seed = {p: d for p, (d, _) in restricted_agent.seed_files(part, restricted_agent.settings_for(run, part)).items()}
    final = dict(seed)
    final["backend/todos.py"] = seed["backend/todos.py"].replace(
        b"    # __SLOT__\n    raise NotImplementedError",
        b"    return {}, 410",
    )
    final["backend/requirements.txt"] = seed["backend/requirements.txt"] + b"requests==2.32.0\n"
    record = restricted_agent.record_from_workspace(run, final, seed)
    assert record.outcome == "evaluate"
    result = SlotVariantResult.objects.get(run=run)
    assert result.edit_violation is False
    assert result.out_of_slot_edits == []
    assert result.new_dependencies == ["requests"]
