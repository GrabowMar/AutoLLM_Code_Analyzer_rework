"""``study_runs`` scope: pilot runs never enter a main-stage sample (Appendix A #11)."""

from __future__ import annotations

import pytest

from backend.generation.models.choices import Stage
from backend.statistics.services.runs import study_runs
from backend.statistics.tests.helpers import StudyBuilder
from backend.statistics.tests.helpers import make_run
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def scope():
    user = UserFactory()
    builder = StudyBuilder(user, n_conditions=1, n_units=1)
    main = builder.run(0, 0, 0)
    pilot = builder.run(0, 0, 1, cell_stage=Stage.PILOT)
    adhoc = make_run(user)
    adhoc_pilot = make_run(user, stage=Stage.PILOT)
    return user, builder, {"main": main, "pilot": pilot, "adhoc": adhoc, "adhoc_pilot": adhoc_pilot}


def _ids(qs):
    return {str(pk) for pk in qs.values_list("pk", flat=True)}


def test_study_runs_without_study_leaves_pilot_runs_out(scope):
    user, _, runs = scope
    assert _ids(study_runs(user)) == {str(runs["main"].pk), str(runs["adhoc"].pk)}


def test_study_runs_with_run_ids_leaves_pilot_runs_out(scope):
    user, _, runs = scope
    picked = [str(run.pk) for run in runs.values()]
    assert _ids(study_runs(user, run_ids=picked)) == {str(runs["main"].pk), str(runs["adhoc"].pk)}


def test_study_runs_pilot_stage_on_request(scope):
    user, builder, runs = scope
    assert _ids(study_runs(user, study_id=builder.study.pk, stage=Stage.PILOT)) == {str(runs["pilot"].pk)}
    assert _ids(study_runs(user, study_id=builder.study.pk)) == {str(runs["main"].pk)}
