"""Block accounting ignores replicate cells (WP-10 -> WP-11 ask): ``beyond_plan=True`` cells are not part of
the planned sample, so they neither make a planned block incomplete nor add to ``block_status.total``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.generation.budget import report
from backend.generation.models import GenerationRun
from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.factories import StudyCellFactory
from backend.generation.tests.wp11_budget.conftest import make_study

pytestmark = pytest.mark.django_db


def _replicate(tree, user, *, condition: int, block: int, sequence: int):
    cond = tree.conditions[condition]
    unit = tree.units[0]
    cell = StudyCellFactory(
        condition=cond,
        unit=unit,
        repeat_index=1 + block,
        block_index=block,
        sequence=sequence,
        stage="main",
        cap_usd=Decimal("0.15"),
        cell_cap_usd=Decimal("0.60"),
        estimate_usd=Decimal("0.10"),
        state="queued",
        beyond_plan=True,
    )
    GenerationRunFactory(
        created_by=user,
        study=tree.study,
        condition=cond,
        cell=cell,
        stage="main",
        repeat_index=cell.repeat_index,
        sequence=sequence,
        model=cond.model,
        task_version=unit.task_version,
        resource_policy=tree.runs[0].resource_policy,
        beyond_plan=True,
    )
    return cell


def test_replicate_cells_do_not_count_in_blocks(user):
    tree = make_study(user, n_conditions=2, n_units=1, repeats=1)
    GenerationRun.objects.filter(pk__in=[r.pk for r in tree.runs]).update(status="completed", outcome="tested")
    assert report.complete_blocks(tree.study) == [("main", 0)]

    # A pending replicate in the planned block 0 and one in a new block 1.
    _replicate(tree, user, condition=0, block=0, sequence=100)
    _replicate(tree, user, condition=1, block=1, sequence=101)

    assert report.complete_blocks(tree.study) == [("main", 0)]
    assert report.complete_blocks(tree.study, stage="main") == [("main", 0)]
    status = report.block_status(tree.study)
    assert status["total"] == 1
    assert status["complete"] == 1
    payload = report.study_payload(tree.study)
    assert payload["blocks"] == status
