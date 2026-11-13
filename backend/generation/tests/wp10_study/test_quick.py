"""Quick generations ("Generate app" page): ``POST /studies/adhoc/`` with ``quick`` and the list filter."""

from __future__ import annotations

import pytest
from ninja.testing import TestClient

from backend.generation.models import Study
from backend.generation.study import launch as svc
from backend.generation.study.api import router

from .conftest import MODELS
from .conftest import app_design

pytestmark = pytest.mark.django_db


def _one_app_design() -> dict:
    """One condition, one task, one repeat: what the "Generate app" page sends."""
    return app_design(
        factors=[
            {"key": "model", "role": "fixed", "levels": [MODELS[0]]},
            {"key": "stack_profile", "role": "fixed", "levels": ["flask-react@1"]},
            {"key": "style", "role": "fixed", "levels": ["tailwind@1"]},
        ],
        units={"tasks": ["crud_todo_list@1"]},
        repeats=1,
    )


@pytest.fixture
def api(user):
    client = TestClient(router)

    class Client:
        def get(self, path, **params):
            return client.get(path, query_params=params, auth=user, user=user)

        def post(self, path, body=None):
            return client.post(path, json=body or {}, auth=user, user=user)

    return Client()


def test_quick_launch_marks_the_study_and_returns_one_run(api, golden, fake_study):
    res = api.post("/studies/adhoc/", {"design": _one_app_design(), "launch": True, "quick": True, "name": "Quick"})
    assert res.status_code == 200, res.content
    body = res.json()
    assert len(body["run_ids"]) == 1
    study = Study.objects.get(pk=body["study_id"])
    assert study.is_quick is True
    assert api.get(f"/studies/{study.pk}/").json()["is_quick"] is True


def test_quick_dry_run_prices_without_creating_a_study(api, golden, fake_study):
    before = Study.objects.count()
    res = api.post("/studies/adhoc/", {"design": _one_app_design(), "launch": False, "quick": True})
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["study_id"] is None
    assert body["run_ids"] == []
    assert body["plan_hash"]
    assert body["estimate"] is not None
    assert Study.objects.count() == before


def test_non_quick_adhoc_is_unchanged(golden, user, fake_study):
    draft = svc.launch_adhoc(_one_app_design(), user=user, launch=False)
    study = Study.objects.get(pk=draft["study_id"])
    assert study.is_quick is False
    assert study.status == "draft"


def test_adhoc_plan_errors_are_reported_and_nothing_is_created(api, golden, fake_study):
    before = Study.objects.count()
    bad = _one_app_design()
    bad["units"] = {"tasks": ["no-such-task@1"]}
    res = api.post("/studies/adhoc/", {"design": bad, "launch": False, "quick": True})
    assert res.status_code == 400, res.content
    assert res.json()["code"]
    assert Study.objects.count() == before


def test_list_hides_quick_studies_unless_asked(api, golden, fake_study, make_study):
    regular = make_study(name="Regular")
    quick = api.post("/studies/adhoc/", {"design": _one_app_design(), "launch": True, "quick": True}).json()
    default = api.get("/studies/").json()
    ids = {row["id"] for row in default["items"]}
    assert str(regular.pk) in ids
    assert quick["study_id"] not in ids
    assert default["total"] == 1
    assert all(row["is_quick"] is False for row in default["items"])
    everything = api.get("/studies/", include_quick=True).json()
    rows = {row["id"]: row for row in everything["items"]}
    assert rows[quick["study_id"]]["is_quick"] is True
    assert rows[str(regular.pk)]["is_quick"] is False
    assert everything["total"] == 2


def test_dry_run_returns_rendered_cells_and_keeps_nothing(api, golden, fake_study):
    from backend.generation.models import GenerationRun
    from backend.generation.models import StudyPlan

    before = (Study.objects.count(), StudyPlan.objects.count(), GenerationRun.objects.count())
    res = api.post("/studies/dry-run/", {"design": _one_app_design()})
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["plan"]["plan_hash"]
    assert body["plan"]["persisted"] is False
    assert len(body["cells"]) == body["plan"]["n_cells"]
    assert body["truncated"] is False
    cell = body["cells"][0]
    assert cell["levels"]["model"] == MODELS[0]
    assert cell["stimuli"], "a dry run shows the rendered prompts"
    assert all(stimulus["text"] for stimulus in cell["stimuli"])
    assert cell["manifest"]
    assert (Study.objects.count(), StudyPlan.objects.count(), GenerationRun.objects.count()) == before


def test_dry_run_caps_the_cells_it_renders(api, golden, fake_study):
    design = _one_app_design()
    design["repeats"] = 3
    res = api.post("/studies/dry-run/", {"design": design, "max_cells": 1})
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["plan"]["n_cells"] > 1
    assert len(body["cells"]) == 1
    assert body["truncated"] is True


def test_dry_run_reports_design_errors(api, golden, fake_study):
    bad = _one_app_design()
    bad["units"] = {"tasks": ["no-such-task@1"]}
    res = api.post("/studies/dry-run/", {"design": bad})
    assert res.status_code == 400, res.content
    assert res.json()["code"]
