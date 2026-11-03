"""The budget API (9.8) through ninja's TestClient on ``budget.api.router``."""

from __future__ import annotations

from decimal import Decimal

import pytest
from ninja.testing import TestClient

from backend.generation.budget import api
from backend.generation.contracts.plan import Estimate
from backend.generation.models import SpendCap
from backend.generation.tests.factories import CostPriorFactory
from backend.generation.tests.factories import ResourcePolicyFactory
from backend.generation.tests.factories import SpendCapFactory
from backend.generation.tests.wp11_budget.conftest import make_study
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


class Client:
    def __init__(self, user) -> None:
        self.user = user
        self.client = TestClient(api.router)

    def get(self, path, **params):
        return self.client.get(path, query_params=params, user=self.user, auth=self.user)

    def post(self, path, body):
        return self.client.post(path, json=body, user=self.user, auth=self.user)

    def patch(self, path, body):
        return self.client.patch(path, json=body, user=self.user, auth=self.user)

    def delete(self, path):
        return self.client.delete(path, user=self.user, auth=self.user)


@pytest.fixture
def client(user):
    return Client(user)


@pytest.fixture
def staff(db):
    return Client(UserFactory(is_staff=True))


def test_knobs(client):
    data = client.get("/budget/knobs/").json()
    assert len(data["knobs"]) >= 70
    assert "run.max_cost_usd" in data["key_limits"]
    assert set(data["tiers"]) == {"frugal", "standard", "generous"}


def test_study_payload_and_ledger(client, user, system_ep, tick):
    tree = make_study(user, n_conditions=2, n_units=1, repeats=2, cap=1.0, cap_usd=0.15)
    tick(1)
    data = client.get(f"/budget/studies/{tree.study.pk}/").json()
    assert data["cap"] == 1.0
    assert data["cap_basis"] == "billed"
    assert data["spent"] == 0.0
    assert data["reserved"] == pytest.approx(0.30)
    assert data["remaining"] == pytest.approx(0.70)
    assert data["blocks"] == {"started": 1, "complete": 0, "total": 2}
    assert {row["condition"] for row in data["by_condition"]} == {c.pk for c in tree.conditions}
    assert data["expected_remaining"] == pytest.approx(0.4)
    ledger = client.get(f"/budget/studies/{tree.study.pk}/ledger/", kind="reserve").json()
    assert ledger["total"] == 2
    assert ledger["page"] == 1
    assert {row["dedupe_key"].split(":")[0] for row in ledger["items"]} == {"blk", "res"}
    other = Client(UserFactory())
    assert other.get(f"/budget/studies/{tree.study.pk}/").status_code == 404
    assert client.get("/budget/studies/not-a-uuid/").status_code == 404


def test_estimate_of_a_frozen_plan(client, user, system_ep):
    from backend.generation import blobs
    from backend.generation.budget import policy as policy_mod
    from backend.generation.contracts.resources import preset_content
    from backend.generation.models import StudyPlan
    from backend.generation.tests.wp11_budget.test_estimate import _fixed
    from backend.generation.tests.wp11_budget.test_estimate import _plan

    tree = make_study(user, n_conditions=1, n_units=1, repeats=1)
    plan = _plan()
    effective = policy_mod.effective_policy(preset_content("frugal")).model_dump(mode="json", by_alias=True)
    plan = plan.model_copy(
        update={
            "cells": [c.model_copy(update={"estimate": _fixed(c), "effective_policy": effective}) for c in plan.cells],
        },
    )
    sha = blobs.put_json(plan.model_dump(mode="json"))
    from backend.generation.tests.factories import StudyPlanFactory

    row = StudyPlanFactory(study=tree.study, payload_blob=sha)
    response = client.post("/budget/estimate/", {"study": str(tree.study.pk), "plan_hash": row.plan_hash})
    assert response.status_code == 200, response.content
    estimate = Estimate.model_validate(response.json())
    assert estimate.expected_total == pytest.approx(1.8)
    assert len(estimate.by_condition) == 2
    assert client.post("/budget/estimate/", {}).status_code == 400
    bare = _plan()
    row2 = StudyPlanFactory(study=tree.study, payload_blob=blobs.put_json(bare.model_dump(mode="json")))
    assert (
        client.post("/budget/estimate/", {"study": str(tree.study.pk), "plan_hash": row2.plan_hash}).status_code == 422
    )
    assert StudyPlan.objects.filter(pk=row.pk).exists()


def test_me_and_header(client, user, system_ep, tick):
    SpendCapFactory(user=user, period="day", unit="usd", limit=Decimal(5))
    tree = make_study(user, n_conditions=1, n_units=1, repeats=1)
    from backend.generation.models import Study

    Study.objects.filter(pk=tree.study.pk).update(status="paused", pause_reason="quota")
    me = client.get("/budget/me/").json()
    assert any(p["period"] == "day" and p["cap"] == 5.0 for p in me["periods"])
    assert {p["period"] for p in me["periods"]} >= {"day", "week", "month"}
    assert [s["id"] for s in me["studies"]] == [str(tree.study.pk)]
    header = client.get("/budget/header/").json()
    assert header["period"] == "day"
    assert header["period_cap"] == 5.0
    assert header["paused_studies"] == [{"id": str(tree.study.pk), "reason": "quota"}]


def test_caps_crud_and_permissions(client, staff, user):
    body = {"scope": "user", "period": "day", "unit": "usd", "limit": "3"}
    created = client.post("/budget/caps/", body)
    assert created.status_code == 200, created.content
    cap_id = created.json()["id"]
    assert created.json()["user"] == user.pk
    assert client.patch(f"/budget/caps/{cap_id}/", {"limit": "2"}).json()["limit"] == 2.0
    assert client.post("/budget/caps/", {**body, "scope": "global"}).status_code == 403
    # A staff cap on the user is a ceiling: the user may only go below it.
    staff_cap = staff.post("/budget/caps/", {**body, "user": user.pk, "limit": "4"}).json()
    assert client.post("/budget/caps/", {**body, "limit": "5"}).status_code == 403
    assert client.post("/budget/caps/", {**body, "limit": "1"}).status_code == 200
    assert client.patch(f"/budget/caps/{staff_cap['id']}/", {"limit": "9"}).status_code == 403
    assert (
        staff.post(
            "/budget/caps/",
            {"scope": "model", "model_slug": "x/y", "period": "5h", "unit": "list_usd", "limit": "1"},
        ).status_code
        == 200
    )
    listed = client.get("/budget/caps/").json()
    assert {c["scope"] for c in listed} == {"user", "model"}
    assert client.delete(f"/budget/caps/{cap_id}/").json() == {"deleted": cap_id}
    assert not SpendCap.objects.filter(pk=cap_id).exists()
    assert client.delete(f"/budget/caps/{staff_cap['id']}/").status_code == 403


def test_priors_listing(client):
    CostPriorFactory(key="app_agentic|opencode|vendor/a|M|frugal", n=7)
    CostPriorFactory(key="app_agentic|aider|vendor/b|M|frugal", n=2)
    rows = client.get("/budget/priors/", harness="opencode").json()
    assert [r["key"] for r in rows] == ["app_agentic|opencode|vendor/a|M|frugal"]
    assert rows[0]["usable"]
    assert [r["key"] for r in client.get("/budget/priors/", model="vendor/b").json()] == [
        "app_agentic|aider|vendor/b|M|frugal",
    ]


def test_policy_explain(client, user):
    from backend.generation.tests.wp11_budget.conftest import model

    policy = ResourcePolicyFactory(created_by=user)
    m = model(model_id="vendor/explain")
    data = client.get(f"/budget/policies/{policy.ref}/explain/", model=m.model_id, harness="opencode").json()
    assert data["ref"] == policy.pinned_ref
    assert data["tier"] == "frugal"
    assert data["generation_policy_hash"]
    assert data["summary"].endswith("no overrides")
    assert data["diff_from_tier"] == []
    assert client.get("/budget/policies/nope/explain/").status_code == 400
    assert client.get("/budget/policies/missing@1/explain/").status_code == 404
    assert client.get(f"/budget/policies/{policy.ref}/explain/", model="no/such").status_code == 404


def test_estimate_of_a_draft_design_resolves_without_persisting(client, user, monkeypatch):
    from backend.generation.budget import policy as policy_mod
    from backend.generation.contracts.resources import preset_content
    from backend.generation.models import Study
    from backend.generation.testing.content import design_content
    from backend.generation.tests.wp11_budget.test_estimate import _fixed
    from backend.generation.tests.wp11_budget.test_estimate import _plan

    preset = ResourcePolicyFactory(created_by=user)
    effective = policy_mod.effective_policy(preset_content("frugal")).model_dump(mode="json", by_alias=True)
    plan = _plan()
    plan = plan.model_copy(
        update={
            "cells": [c.model_copy(update={"estimate": _fixed(c), "effective_policy": effective}) for c in plan.cells],
        },
    )
    seen = {}

    def resolve(study, *, persist):
        seen["persist"], seen["saved"] = persist, Study.objects.filter(pk=study.pk).exists()
        return plan

    monkeypatch.setattr("backend.generation.study.plan.resolve", resolve)
    response = client.post("/budget/estimate/", {"design": design_content(resource_policy=preset.ref), "cap": 1.0})
    assert response.status_code == 200, response.content
    assert seen == {"persist": False, "saved": False}
    body = response.json()
    assert body["cap_usd"] == 1.0
    assert [i["code"] for i in body["issues"]] == ["estimate_exceeds_cap"]
    bad = client.post("/budget/estimate/", {"design": {"mode": "app"}})
    assert bad.status_code == 422
