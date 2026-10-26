"""Pre-registered analysis plans: validation, freezing at launch, immutability."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from backend.generation.models import Experiment
from backend.generation.services import analysis_plan as plans
from backend.generation.services import experiments as svc
from backend.generation.tests.test_experiments_v2_api import API
from backend.generation.tests.test_experiments_v2_api import ENQUEUE
from backend.generation.tests.test_experiments_v2_api import _apps
from backend.generation.tests.test_experiments_v2_api import _create_body
from backend.generation.tests.test_experiments_v2_api import _models

PLAN = {
    "research_questions": [
        {"question": "Does the new prompt deploy more often?", "metric": "deploy_rate", "direction": "greater"},
    ],
    "primary_metric": "deploy_rate",
    "secondary_metrics": ["pass_rate", "deploy_rate"],
    "alpha": 0.05,
    "correction": "holm",
    "ranking_criteria": [
        {"key": "deploy_rate", "direction": "benefit", "weight": 3},
        {"key": "issues_per_kloc", "direction": "cost", "weight": 1},
    ],
}


def test_normalize_validates_and_canonicalises():
    plan = plans.normalize_analysis_plan(PLAN)
    assert plan["secondary_metrics"] == ["pass_rate"]  # primary dropped from secondaries
    assert plan["research_questions"][0]["id"] == "RQ1"
    assert [c["weight"] for c in plan["ranking_criteria"]] == [0.75, 0.25]
    assert plans.declared_metrics(plan) == {"deploy_rate", "pass_rate"}
    assert plans.normalize_analysis_plan(plan) == plan  # idempotent (clones re-validate)
    assert plans.plan_hash(plan) == plans.plan_hash(json.loads(json.dumps(plan)))
    assert plans.normalize_analysis_plan(None) == {}


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"primary_metric": "vibes"}, "primary_metric"),
        ({"alpha": 0.9}, "alpha"),
        ({"correction": "bonferroni-ish"}, "correction"),
        ({"research_questions": [{"question": ""}]}, "needs a question"),
        ({"ranking_criteria": [{"key": "loc", "direction": "benefit", "weight": 1}]}, "Unknown ranking"),
    ],
)
def test_normalize_rejects(change, error):
    with pytest.raises(plans.PlanError, match=error):
        plans.normalize_analysis_plan({**PLAN, **change})


@pytest.mark.django_db
class TestPlanLifecycle:
    @pytest.fixture(autouse=True)
    def _env(self, settings):
        settings.AGENT_AUTO_EVALUATE = False
        with mock.patch("backend.credentials.services.resolver.has_resolvable_key", return_value=True):
            yield

    def test_plan_is_frozen_at_launch_and_then_immutable(self, client):
        from backend.users.tests.factories import UserFactory

        user = UserFactory()
        client.force_login(user)
        body = _create_body(_models(1), _apps(), analysis_plan=PLAN)
        res = client.post(f"{API}/experiments/", data=json.dumps(body), content_type="application/json")
        assert res.status_code in (200, 201), res.content
        experiment = Experiment.objects.get(id=res.json()["id"])
        assert experiment.analysis_plan["primary_metric"] == "deploy_rate"
        assert experiment.analysis_plan_frozen_at is None

        with mock.patch(ENQUEUE):
            svc.launch_experiment(experiment, user)
        experiment.refresh_from_db()
        assert experiment.analysis_plan_frozen_at is not None
        assert experiment.analysis_plan_hash == plans.plan_hash(experiment.analysis_plan)

        res = client.patch(
            f"{API}/experiments/{experiment.id}/",
            data=json.dumps({"analysis_plan": {**PLAN, "primary_metric": "pass_rate"}}),
            content_type="application/json",
        )
        assert res.status_code == 400
        experiment.refresh_from_db()
        assert experiment.analysis_plan["primary_metric"] == "deploy_rate"

    def test_invalid_plan_is_a_400(self, client):
        from backend.users.tests.factories import UserFactory

        client.force_login(UserFactory())
        body = _create_body(_models(1), _apps(), analysis_plan={"primary_metric": "nope"})
        res = client.post(f"{API}/experiments/", data=json.dumps(body), content_type="application/json")
        assert res.status_code == 400
        assert res.json()["code"] == "invalid_analysis_plan"
