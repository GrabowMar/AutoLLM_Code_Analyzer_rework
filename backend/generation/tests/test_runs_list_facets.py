"""``GET /runs/`` task / harness / stack filters and sorting, and ``GET /runs/facets/`` (Applications page)."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client

from backend.generation.models import choices
from backend.generation.testing.factories import GenerationRunFactory
from backend.generation.testing.factories import HarnessPresetFactory
from backend.generation.testing.factories import StackProfileFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BASE = "/api/generation/runs"


def _eval(rate: float | None, *, measured: bool = True) -> dict[str, Any]:
    status = "measured" if measured else "not_measured"
    summary = {
        "eval_suite": "suite@1",
        "eval_level": "full",
        "status": status,
        "measured": measured,
        "agent_pass_rate": rate,
    }
    return {"eval": summary}


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def client(user) -> Client:
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def runs(user):
    h1 = HarnessPresetFactory(slug="aider-native")
    h2 = HarnessPresetFactory(slug="goose-native")
    stack = StackProfileFactory(slug="flask-react")
    a = GenerationRunFactory(
        tested=True,
        created_by=user,
        harness_preset=h1,
        stack_profile=stack,
        metrics={**_eval(0.9), "cost_usd": 0.02, "wall_s": 300},
    )
    b = GenerationRunFactory(
        tested=True,
        created_by=user,
        harness_preset=h2,
        stack_profile=stack,
        metrics={**_eval(0.4), "cost_usd": 0.05, "wall_s": 100},
    )
    c = GenerationRunFactory(
        created_by=user,
        harness_preset=h1,
        status=choices.RunStatus.COMPLETED,
        outcome=choices.Outcome.BUILD_FAIL,
        metrics=_eval(None, measured=False),
    )
    other = GenerationRunFactory(tested=True)  # someone else's run: never listed or counted
    return {"a": a, "b": b, "c": c, "other": other, "h1": h1, "h2": h2, "stack": stack}


def _ids(client: Client, **params: Any) -> list[str]:
    response = client.get(f"{BASE}/", params)
    assert response.status_code == 200, response.content
    return [item["id"] for item in response.json()["items"]]


class TestFilters:
    def test_harness_by_ref_and_by_slug(self, client, runs) -> None:
        h1 = runs["h1"]
        assert set(_ids(client, harness=h1.ref)) == {str(runs["a"].pk), str(runs["c"].pk)}
        assert set(_ids(client, harness=h1.slug)) == {str(runs["a"].pk), str(runs["c"].pk)}
        assert _ids(client, harness=f"{h1.slug}@999") == []

    def test_stack_and_task(self, client, runs) -> None:
        assert set(_ids(client, stack=runs["stack"].ref)) == {str(runs["a"].pk), str(runs["b"].pk)}
        slug = runs["b"].task_version.task.slug
        assert _ids(client, task=slug) == [str(runs["b"].pk)]


class TestSort:
    def test_pass_rate_desc_puts_not_measured_last(self, client, runs) -> None:
        ids = _ids(client, sort="-pass_rate")
        assert ids == [str(runs["a"].pk), str(runs["b"].pk), str(runs["c"].pk)]

    def test_pass_rate_asc_also_puts_not_measured_last(self, client, runs) -> None:
        ids = _ids(client, sort="pass_rate")
        assert ids == [str(runs["b"].pk), str(runs["a"].pk), str(runs["c"].pk)]

    def test_cost_and_wall(self, client, runs) -> None:
        assert _ids(client, sort="-cost")[:2] == [str(runs["b"].pk), str(runs["a"].pk)]
        assert _ids(client, sort="wall")[:2] == [str(runs["b"].pk), str(runs["a"].pk)]

    def test_unknown_sort_is_rejected(self, client, runs) -> None:
        response = client.get(f"{BASE}/", {"sort": "bogus"})
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_filter"


class TestFacets:
    def test_counts_and_summary(self, client, runs) -> None:
        data = client.get(f"{BASE}/facets/").json()
        assert data["summary"] == {"total": 3, "tested": 2, "measured": 2, "mean_pass_rate": pytest.approx(0.65)}
        outcomes = {o["value"]: o["count"] for o in data["outcomes"]}
        assert outcomes == {"tested": 2, "build_fail": 1}
        harnesses = {h["value"]: h["count"] for h in data["harnesses"]}
        assert harnesses == {runs["h1"].ref: 2, runs["h2"].ref: 1}
        assert {t["count"] for t in data["tasks"]} == {1}

    def test_a_facet_ignores_its_own_filter(self, client, runs) -> None:
        data = client.get(f"{BASE}/facets/", {"outcome": "tested"}).json()
        # The outcome facet still lists every outcome; the others follow the filter.
        assert {o["value"] for o in data["outcomes"]} == {"tested", "build_fail"}
        assert {h["value"]: h["count"] for h in data["harnesses"]} == {runs["h1"].ref: 1, runs["h2"].ref: 1}
        assert data["summary"]["total"] == 2

    def test_no_measured_run_gives_no_mean(self, client, runs) -> None:
        data = client.get(f"{BASE}/facets/", {"outcome": "build_fail"}).json()
        assert data["summary"]["measured"] == 0
        assert data["summary"]["mean_pass_rate"] is None

    def test_status_facet_and_running_filter(self, client, user, runs) -> None:
        live = GenerationRunFactory(created_by=user, status=choices.RunStatus.RUNNING, outcome="")
        data = client.get(f"{BASE}/facets/", {"status": "running"}).json()
        # The status facet ignores its own filter and lists statuses in lifecycle order.
        assert [s["value"] for s in data["statuses"]] == ["running", "completed"]
        assert {s["value"]: s["count"] for s in data["statuses"]} == {"running": 1, "completed": 3}
        assert data["summary"]["total"] == 1
        assert _ids(client, status="running") == [str(live.pk)]
