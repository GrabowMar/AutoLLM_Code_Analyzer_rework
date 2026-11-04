"""``refresh_cost_priors``: observations, the four key levels, calibration and overhead rows, the lookup."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from backend.generation.budget import priors
from backend.generation.models import CostPrior
from backend.generation.models import GenerationRun
from backend.generation.tests.wp11_budget.conftest import make_study

pytestmark = pytest.mark.django_db


def _finished(user, costs):
    tree = make_study(user, n_conditions=1, n_units=1, repeats=len(costs), manifests=True)
    for run, cost in zip(tree.runs, costs, strict=True):
        GenerationRun.objects.filter(pk=run.pk).update(
            status="completed",
            outcome="tested",
            usable=True,
            completed_at=timezone.now(),
            metrics={
                "cost_usd": cost,
                "list_cost_usd": cost * 2,
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "wall_s": 300.0,
            },
        )
    return tree


def test_percentile():
    assert priors.percentile([], 0.5) is None
    assert priors.percentile([3.0], 0.9) == 3.0
    assert priors.percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert priors.percentile([1, 2, 3, 4, 5], 0.1) == pytest.approx(1.4)


def test_refresh_writes_every_level_and_the_lookup_uses_them(user, system_ep):
    tree = _finished(user, [0.10, 0.20, 0.30, 0.40, 0.50])
    written = priors.refresh_priors()
    slug = tree.conditions[0].model.model_id
    family = slug.split("/", 1)[0].lower()
    key = f"app_agentic|opencode|{slug}|*|frugal"  # tasks have no derived size bucket yet
    assert written >= 4
    row = CostPrior.objects.get(key=key)
    assert (row.n, row.cost_p50, row.wall_s_p50) == (5, pytest.approx(0.30), 300.0)
    assert row.price_at == {"input_per_mtok": 1.0, "output_per_mtok": 2.0}
    assert CostPrior.objects.filter(key=f"app_agentic|opencode|{family}|*|*").exists()
    assert CostPrior.objects.filter(key="app_agentic|opencode|*|*|*").exists()
    calibration = CostPrior.objects.get(key=f"calibration|{slug}|price|*|*")
    assert calibration.cost_p50 == pytest.approx(0.5)  # billed / list
    found = priors.lookup(kind="app_agentic", harness="opencode", model=slug, family=family, size="*", tier="frugal")
    assert found is not None
    assert found[0] == key
    assert priors.calibration(slug, "price") == pytest.approx(0.5)


def test_runs_without_a_recorded_cost_are_not_free_observations(user, system_ep):
    tree = make_study(user, n_conditions=1, n_units=1, repeats=2, manifests=True)
    GenerationRun.objects.filter(study=tree.study).update(status="completed", completed_at=timezone.now(), metrics={})
    assert priors.observations() == []


def test_refresh_for_run_and_the_command(user, system_ep):
    tree = _finished(user, [0.1] * 6)
    run = GenerationRun.objects.select_related("manifest", "model", "task_version", "resource_policy").get(
        pk=tree.runs[0].pk,
    )
    assert priors.refresh_for_run(run) == 5  # 3 distinct chain keys (no size bucket) + calibration + overhead
    out = StringIO()
    call_command("refresh_cost_priors", "--dry-run", stdout=out)
    assert "usable" in out.getvalue()
    assert "nothing written" in out.getvalue()
    out = StringIO()
    call_command("refresh_cost_priors", stdout=out)
    assert "refreshed" in out.getvalue()


def test_overhead_row(user, system_ep):
    tree = _finished(user, [0.1] * 5)
    GenerationRun.objects.filter(pk=tree.runs[0].pk).update(
        execution={"attempts": [{"attempt": 0, "reason": "infra"}], "prior_attempts_cost_usd": 0.04},
    )
    priors.refresh_priors()
    over = priors.overhead("app_agentic", "opencode")
    assert over["infra_retry_rate"] == pytest.approx(0.2)
    assert over["mean_partial_cost"] == pytest.approx(0.04)


def test_manifestless_runs_still_observe(user, system_ep):
    tree = make_study(user, n_conditions=1, n_units=1, repeats=1)
    GenerationRun.objects.filter(study=tree.study).update(
        status="completed",
        completed_at=timezone.now(),
        metrics={"cost_usd": 0.2},
    )
    obs = priors.observations()
    assert len(obs) == 1
    assert obs[0].harness == ""
    assert obs[0].cost == 0.2
