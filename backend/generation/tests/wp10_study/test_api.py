"""Studies API (spec section 9.4) through ninja's TestClient on the study router."""

from __future__ import annotations

import io
import zipfile

import pytest
from ninja.testing import TestClient

from backend.generation.models import GenerationRun
from backend.generation.study.api import router
from backend.users.tests.factories import UserFactory

from .conftest import app_design

pytestmark = pytest.mark.django_db


@pytest.fixture
def api(user):
    client = TestClient(router)

    class Client:
        def get(self, path, **params):
            return client.get(path, query_params=params, auth=user, user=user)

        def post(self, path, body=None):
            return client.post(path, json=body or {}, auth=user, user=user)

        def patch(self, path, body):
            return client.patch(path, json=body, auth=user, user=user)

        def delete(self, path):
            return client.delete(path, auth=user, user=user)

    return Client()


def _create(api, **overrides):
    res = api.post("/studies/", {"name": "My study", "design": app_design(**overrides)})
    assert res.status_code == 200, res.content
    return res.json()


def test_create_resolve_cell_detail_diff_launch_results(api, golden, fake_study):
    study = _create(api)
    assert study["status"] == "draft"
    assert study["design_hash"]
    resolved = api.post(f"/studies/{study['id']}/resolve/", {"sample_cells": 5})
    assert resolved.status_code == 200, resolved.content
    plan = resolved.json()
    assert plan["persisted"] is False
    assert plan["n_conditions"] == 8
    assert len(plan["cells_sample"]) == 5
    a, b = plan["cells_sample"][0]["cell_key"], plan["cells_sample"][1]["cell_key"]
    detail = api.get(f"/studies/{study['id']}/plans/{plan['plan_hash']}/cells/{a}/")
    assert detail.status_code == 200, detail.content
    body = detail.json()
    assert body["stimuli"][0]["text"]
    assert body["identity"]["run_fingerprint"]
    assert {row["cls"] for row in body["effective_params"]} >= {"C", "O"}
    diff = api.post(f"/studies/{study['id']}/plans/{plan['plan_hash']}/diff/", {"a": a, "b": b})
    assert diff.status_code == 200
    assert diff.json()["levels_diff"]
    page = api.get(f"/studies/{study['id']}/plans/{plan['plan_hash']}/cells/", page_size=10)
    assert page.json()["total"] == plan["n_cells"]
    launched = api.post(f"/studies/{study['id']}/launch/", {"plan_hash": plan["plan_hash"]})
    assert launched.status_code == 200, launched.content
    assert launched.json()["stage"] == "pilot"
    assert launched.json()["created_runs"] == 16
    detail = api.get(f"/studies/{study['id']}/")
    assert detail.json()["status"] == "piloting"
    assert detail.json()["active_plan"]["status"] == "launched"
    cells = api.get(f"/studies/{study['id']}/cells/", stage="pilot")
    assert len(cells.json()["cells"]) == 16
    results = api.get(f"/studies/{study['id']}/results/")
    assert results.status_code == 200
    assert results.json()["interim"] is True
    assert api.get(f"/studies/{study['id']}/results/", metric="loc").status_code == 400
    listing = api.get("/studies/")
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["status"] == "piloting"


def test_errors_are_typed(api, golden, fake_study):
    bad = api.post("/studies/", {"name": "x", "design": app_design(kind="confirmatory")})
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_design"
    study = _create(api)
    stale = api.post(f"/studies/{study['id']}/launch/", {"plan_hash": "0" * 64})
    assert stale.status_code == 409
    assert stale.json()["code"] == "plan_stale"
    assert api.get("/studies/not-a-uuid/").status_code == 404
    other = UserFactory.create()
    client = TestClient(router)
    assert client.get(f"/studies/{study['id']}/", auth=other, user=other).status_code == 404


def test_patch_is_refused_once_locked(api, golden, fake_study):
    plan = {"primary_metric": "pass_rate", "research_questions": [{"question": "q"}]}
    study = _create(api, kind="confirmatory", analysis_plan=plan)
    resolved = api.post(f"/studies/{study['id']}/resolve/", {}).json()
    assert api.post(f"/studies/{study['id']}/launch/", {"plan_hash": resolved["plan_hash"]}).status_code == 200
    patched = api.patch(f"/studies/{study['id']}/", {"design": app_design(kind="confirmatory", analysis_plan=plan)})
    assert patched.status_code == 409
    assert patched.json()["code"] == "study_locked"
    renamed = api.patch(f"/studies/{study['id']}/", {"name": "Renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"


def test_adhoc_top_up_clone_export_schedules_post_runs(api, golden, fake_study):
    adhoc = api.post("/studies/adhoc/", {"design": app_design(), "launch": True})
    assert adhoc.status_code == 200, adhoc.content
    body = adhoc.json()
    assert len(body["run_ids"]) == 16
    study_id = body["study_id"]
    topped = api.post(f"/studies/{study_id}/top-up/", {"cells": "infra", "reason": "nothing yet"})
    assert topped.status_code == 200
    assert topped.json()["created_runs"] == 0
    cloned = api.post(f"/studies/{study_id}/clone/", {"mode": "exact", "name": "copy"})
    assert cloned.status_code == 200
    assert cloned.json()["version_diffs"] == []
    export = api.get(f"/studies/{study_id}/export/")
    assert export.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(export.content))
    names = set(archive.namelist())
    assert {"plan.json", "runs.csv", "attrition.csv", "files.csv", "evaluations.jsonl"} <= names
    assert (
        len([n for n in names if n.startswith("manifests/")])
        == GenerationRun.objects.filter(study_id=study_id).count()
    )
    schedule = api.post(f"/studies/{study_id}/schedules/", {"action": "top_up", "cron": "0 3 * * *"})
    assert schedule.status_code == 200
    assert schedule.json()["next_run_at"]
    too_often = api.post(f"/studies/{study_id}/schedules/", {"action": "top_up", "cron": "* * * * *"})
    assert too_often.status_code == 400
    post_run = api.post(f"/studies/{study_id}/post-runs/", {"kind": "analysis", "trigger": "completed"})
    assert post_run.status_code == 200
    assert post_run.json()["status"] == "pending"
    listed = api.get(f"/studies/{study_id}/post-runs/").json()
    # the launch attached the retest post-run (WP-09 test-retest), plus the one created here
    assert len(listed) == 2
    assert sum(1 for row in listed if (row.get("config") or {}).get("retest")) == 1
    assert api.get("/studies/cron-preview/", cron="0 3 * * *").json()["valid"] is True


def test_slot_export_writes_the_pattern_readme_never_an_empty_directory(make_study, fake_study):
    """WP-02b ask: ``patterns/`` comes from ``export.services.patterns`` and always states its status."""
    import json

    from backend.generation.study import export as study_export

    study = make_study()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        study_export._patterns(archive, study)
    with zipfile.ZipFile(buffer) as archive:
        assert archive.namelist() == ["patterns/README.txt"]
        assert json.loads(archive.read("patterns/README.txt"))["status"] == "not_measured"
