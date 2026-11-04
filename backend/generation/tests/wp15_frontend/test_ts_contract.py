"""The hand-written TS types of the Study builder and record match the backend payloads (spec 9, 10.6; WP-15).

``frontend/src/lib/api/v3/types.ts`` mirrors the JSON of ``study/serialize.py`` and ``budget/report.py`` by
hand. This test builds real payloads from factories and checks that every key the backend sends is declared
in the matching TS interface of the WP-15 block (and that the TS side declares no required key the backend
never sends), so a renamed or added field fails here instead of silently rendering "—" in the UI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.generation.budget import report
from backend.generation.models import GenerationRun
from backend.generation.models.choices import Outcome
from backend.generation.study import launch as launch_svc
from backend.generation.study import plan as planner
from backend.generation.study import serialize
from backend.generation.testing.factories import StudyFactory

# WP-10's study fixtures (golden library + FakeStudyEnv) build a real resolvable/launchable study.
from backend.generation.tests.wp10_study.conftest import fake_study  # noqa: F401
from backend.generation.tests.wp10_study.conftest import golden  # noqa: F401
from backend.generation.tests.wp10_study.conftest import make_study  # noqa: F401
from backend.generation.tests.wp10_study.test_launch import finish

TYPES = Path(__file__).resolve().parents[4] / "frontend" / "src" / "lib" / "api" / "v3" / "types.ts"
_BLOCK = re.compile(r"// --- WP-15 ---\n(.*?)// --- end WP-15 ---", re.DOTALL)
_FIELD = re.compile(r"^  (\w+)(\?)?:", re.MULTILINE)


def _interfaces() -> dict[str, tuple[set[str], set[str]]]:
    """``{name: (all keys, required keys)}`` of the top-level interfaces in the WP-15 block."""
    text = TYPES.read_text(encoding="utf-8")
    blocks = _BLOCK.findall(text)
    assert blocks, "the WP-15 block of types.ts is missing"
    block = blocks[-1]
    out: dict[str, tuple[set[str], set[str]]] = {}
    for match in re.finditer(
        r"^export interface (\w+)(?:<[^>]*>)?(?: extends [^{]+)? \{\n(.*?)^\}",
        block,
        re.DOTALL | re.MULTILINE,
    ):
        body = match.group(2)
        keys = {m.group(1) for m in _FIELD.finditer(body)}
        required = {m.group(1) for m in _FIELD.finditer(body) if not m.group(2)}
        out[match.group(1)] = (keys, required)
    return out


def _assert_matches(name: str, payload: dict) -> None:
    keys, required = _interfaces()[name]
    sent = set(payload)
    assert sent <= keys, f"{name}: the backend sends keys the TS type does not declare: {sorted(sent - keys)}"
    assert required <= sent, f"{name}: the TS type requires keys the backend does not send: {sorted(required - sent)}"


def test_the_wp15_block_declares_the_payload_interfaces():
    names = set(_interfaces())
    for name in (
        "StudyOut",
        "StudyListOut",
        "PlanOut",
        "ResolvedCellOut",
        "StudyCellOut",
        "StudyBudgetOut",
        "BudgetHeaderOut",
    ):
        assert name in names


@pytest.mark.django_db
def test_study_out_matches_the_ts_type():
    study = StudyFactory.create()
    _assert_matches("StudyOut", serialize.study_out(study))


@pytest.mark.django_db
def test_study_list_item_matches_the_ts_type():
    study = StudyFactory.create()
    _assert_matches("StudyListOut", serialize.study_list_item(study, {"total": 4, "done": 1, "excluded": 1}))


@pytest.mark.django_db
def test_budget_payloads_match_the_ts_types():
    study = StudyFactory.create()
    _assert_matches("StudyBudgetOut", report.study_payload(study))
    _assert_matches("BudgetHeaderOut", report.header_payload(study.created_by))


@pytest.mark.django_db
def test_study_list_exclusion_rate_is_null_without_cells():
    """No silent zero: a study without cells has no exclusion rate (the list shows "—")."""
    study = StudyFactory.create()
    assert serialize.study_list_item(study, {})["exclusion_rate"] is None


def _nested(name: str, rows: list[dict]) -> None:
    for row in rows:
        _assert_matches(name, row)


@pytest.mark.django_db
def test_plan_out_matches_the_ts_type(make_study, fake_study):  # noqa: F811
    study = make_study()
    bundle = planner.resolve_bundle(study)
    payload = serialize.plan_out(bundle)
    _assert_matches("PlanOut", payload)
    assert payload["cells_sample"], "the plan has no cells to check"
    _nested("ResolvedCellOut", payload["cells_sample"])
    _nested("ResolvedConditionOut", payload["conditions"])
    _nested("ResolvedUnitOut", payload["units"])
    if payload["estimate"] is not None:
        _assert_matches("Estimate", payload["estimate"])


@pytest.mark.django_db
def test_cells_payload_matches_the_ts_type_including_replicate_cells(make_study, fake_study):  # noqa: F811
    """Every row of ``cells_payload`` (planned and replicate cells) matches ``StudyCellOut``, incl. ``beyond_plan``."""
    study = make_study()
    launch_svc.launch(study, planner.resolve(study).plan_hash, pilot_override=True)
    study.refresh_from_db()
    for run in GenerationRun.objects.filter(study=study):
        finish(run, Outcome.TESTED, pass_rate=1.0)
    type(study).objects.filter(pk=study.pk).update(status="completed")
    study.refresh_from_db()
    assert launch_svc.add_replicates(study, 1)["created_runs"] > 0

    payload = serialize.cells_payload(study, state=None, condition=None, unit=None, stage=None)
    _assert_matches("StudyCellsPayload", payload)
    _nested("StudyCellOut", payload["cells"])
    flags = {row["beyond_plan"] for row in payload["cells"]}
    assert flags == {True, False}  # both planned and replicate rows were checked
    assert any(row["current_run"] for row in payload["cells"]), "no cell has a current run"
