"""The final assessment's criteria: whole-cohort (ITT) outcomes, the cost at list prices and the ranking preset."""

from __future__ import annotations

from unittest import mock

import pytest

from backend.generation.models import choices
from backend.generation.testing import content
from backend.inference.models import LLMEndpoint
from backend.llm_models.models import LLMModel
from backend.llm_models.tests.factories import LLMModelFactory
from backend.reports.models import Report
from backend.reports.services import renderers
from backend.reports.services import study_report
from backend.reports.services.list_cost import ListPrice
from backend.reports.services.list_cost import cost_basis
from backend.reports.services.list_cost import list_prices
from backend.reports.services.list_cost import map_twin_prices
from backend.reports.services.list_cost import run_list_cost
from backend.reports.services.plan_stats import FINAL_ASSESSMENT_METRICS
from backend.reports.services.plan_stats import PLAN_METRICS
from backend.reports.services.report_service import _make_summary
from backend.reports.tests.helpers import analyze
from backend.reports.tests.helpers import evaluate
from backend.reports.tests.test_cohort_selection import Pool
from backend.reports.tests.test_cohort_selection import _config
from backend.statistics.services import mcdm
from backend.users.tests.factories import UserFactory

FINAL_KEYS = ["deploy_rate_itt", "pass_rate_itt", "issues_per_kloc", "security_per_app", "list_cost_per_app"]
DEFAULT_KEYS = ["deploy_rate", "pass_rate", "issues_per_kloc", "security_per_app", "cost_per_app"]
# One run's token totals as the gateway records them (prompt_tokens counts cache reads and writes).
TOKENS = {
    "prompt_tokens": 1_000_000,
    "cached_tokens": 800_000,
    "cache_write_tokens": 100_000,
    "uncached_prompt_tokens": 200_000,  # prompt - cached: cache writes included
    "completion_tokens": 50_000,
    "reasoning_tokens": 20_000,  # inside completion_tokens
    "total_tokens": 1_050_000,
}
ROUTER_PRICING = {
    "prompt": "0.000002",
    "completion": "0.00001",
    "input_cache_read": "0.0000001",
    "input_cache_write": "0.0000025",
}
TWIN_PRICING = {"prompt": "0.000001", "completion": "0.000002", "input_cache_read": "0.0000001"}


def _endpoint(user=None, slug="go-e2f3"):
    return LLMEndpoint.objects.create(
        slug=slug,
        name="Subscription endpoint",
        kind="ollama",
        connection="relay",
        api_flavor="native",
        max_concurrency=2,
        owner=user or UserFactory(),
    )


def _endpoint_model(endpoint, model_id, *, twin=None, pricing=None):
    meta = {"catalog_twin": {"model_id": twin, "source": "models.dev:test"}} if twin else {}
    return LLMModel.objects.create(
        model_id=model_id,
        canonical_slug=model_id.replace("/", "_"),
        provider=endpoint.slug,
        model_name=model_id.rsplit("/", 1)[-1],
        endpoint=endpoint,
        served_model_name=model_id.rsplit("/", 1)[-1],
        is_free=True,
        input_price_per_token=0,
        output_price_per_token=0,
        metadata=meta,
        capabilities_json={"pricing": pricing} if pricing else {},
    )


def _router_model(model_id, pricing=ROUTER_PRICING):
    return LLMModelFactory(model_id=model_id, metadata={"openrouter_pricing": pricing} if pricing else {})


# ── whole-cohort outcomes of one run ─────────────────────────────────────


def _row(outcome, *, evaluated=False, pass_rate=None, **extra):
    functional = (pass_rate == 1.0) if evaluated and pass_rate is not None else None
    return {
        "outcome": outcome,
        "evaluated": evaluated,
        "pass_rate": pass_rate,
        "functional_passed": functional,
        **extra,
    }


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (_row("tested", evaluated=True, pass_rate=0.75), (True, 0.75, False)),
        (_row("tested", evaluated=True, pass_rate=1.0), (True, 1.0, True)),
        (_row("build_fail"), (False, 0.0, False)),
        (_row("start_fail"), (False, 0.0, False)),
        (_row("output_invalid"), (False, 0.0, False)),
        # A known failure counts as 0 even when an evaluation measured something.
        (_row("build_fail", evaluated=True, pass_rate=0.5), (False, 0.0, False)),
        # Tested but never graded: deployment is known, the grade is not (left out, never a failure).
        (_row("tested"), (True, None, None)),
        # Static/build-only tested outcome without an observed start: deployment unknown too.
        (_row("tested", outcome_reason="not_tested:static_only"), (None, None, None)),
        (_row("tested", outcome_reason="not_tested:static_only", start_stage_status="ok"), (True, None, None)),
    ],
    ids=[
        "tested",
        "tested-all-pass",
        "build_fail",
        "start_fail",
        "output_invalid",
        "failure-precedes-grade",
        "ungraded",
        "unknown-deployment",
        "observed-start",
    ],
)
def test_itt_outcomes_count_known_failures_as_zero_and_leave_unknowns_out(row, expected):
    out = study_report.itt_outcomes(row)

    assert (out["deployed_itt"], out["pass_rate_itt"], out["functional_passed_itt"]) == expected
    # The deployment evidence is the funnel's end-to-end rule.
    assert out["deployed_itt"] == study_report.deployment_outcome(row)


def _app(unit, outcome, *, pass_rate=None, list_cost=None, deployed=None, model="m"):
    evaluated = pass_rate is not None
    row = {
        "run_id": f"{unit}-{outcome}",
        "condition_id": 1,
        "model": model,
        "kind": "app_agentic",
        "unit_key": unit,
        "outcome": outcome,
        "generated": outcome != "output_invalid",
        "evaluated": evaluated,
        "deployed": deployed if deployed is not None else (True if evaluated else None),
        "pass_rate": pass_rate,
        "functional_passed": (pass_rate == 1.0) if evaluated else None,
        "analyzed": False,
        "unique_issues": None,
        "security_unique_model": None,
        "code_unique_model": None,
        "loc": None,
        "cost": 0.0,
        "list_cost": list_cost,
    }
    row.update(study_report.itt_outcomes(row))
    return row


def test_model_criteria_add_whole_cohort_rates_and_list_cost_next_to_the_conditional_ones():
    rows = [
        _app("t1", "tested", pass_rate=0.8, list_cost=1.0),
        _app("t2", "build_fail", list_cost=3.0),
        # Two repeats of t3 average first: one tested (1.0), one start failure (0).
        _app("t3", "tested", pass_rate=1.0, list_cost=2.0),
        {**_app("t3", "start_fail", list_cost=None), "run_id": "t3-b"},
        # Graded nowhere and no known failure: left out of the whole-cohort pass rate.
        _app("t4", "tested", list_cost=2.0),
    ]

    c = study_report.model_criteria(rows)

    assert c["deploy_rate"] == 1.0  # conditional: over measured evaluations only
    assert c["pass_rate"] == 0.9
    # Whole cohort: t1 1, t2 0, t3 0.5, t4 1 (tested) -> 0.625.
    assert c["deploy_rate_itt"] == 0.625
    # t1 0.8, t2 0, t3 (1 + 0) / 2; t4 unknown -> (0.8 + 0 + 0.5) / 3.
    assert c["pass_rate_itt"] == pytest.approx(0.4333, abs=1e-4)
    assert c["functional_passed_itt"] == pytest.approx((0 + 0 + 0.5) / 3, abs=1e-4)
    # Every run's tokens count, failures included; an unknown cost is left out (t3: 2.0 only).
    assert c["list_cost_per_app"] == 2.0
    assert c["cost_per_app"] == 0.0


def test_rows_without_final_assessment_columns_keep_exactly_the_plan_metrics():
    rows = [{k: v for k, v in _app("t1", "tested", pass_rate=0.5).items() if not k.endswith("_itt")}]

    assert set(study_report.model_criteria(rows)) == set(PLAN_METRICS)


# ── the cost at list prices ──────────────────────────────────────────────


OPUS_PRICE = ListPrice(model="m", priced_as="m", prompt=4e-6, completion=2e-5, cache_read=2e-7, cache_write=5e-6)


def test_list_cost_is_cache_aware_and_matches_the_gateway_formula():
    # A recorded native-route run: its list_cost_usd was priced by the gateway at these prices.
    metrics = {
        "prompt_tokens": 1007788,
        "cached_tokens": 963868,
        "cache_write_tokens": 43862,
        "uncached_prompt_tokens": 43920,
        "completion_tokens": 45736,
        "reasoning_tokens": 0,
        "list_cost_usd": 1.3270356,
    }

    assert run_list_cost(metrics, OPUS_PRICE) == pytest.approx(1.3270356, abs=1e-9)


def test_list_cost_formula_and_its_fallbacks():
    price = ListPrice(model="m", prompt=2e-6, completion=1e-5, cache_read=1e-7, cache_write=2.5e-6)
    # 100k uncached x 2 + 800k cached x 0.1 + 100k writes x 2.5 + 50k completion x 10 (per MTok).
    assert run_list_cost(TOKENS, price) == pytest.approx(0.2 + 0.08 + 0.25 + 0.5)
    # Reasoning tokens are inside completion_tokens: never added again.
    assert run_list_cost({**TOKENS, "reasoning_tokens": 0}, price) == run_list_cost(TOKENS, price)
    # No cache-write price: writes at the prompt price.
    no_write = ListPrice(model="m", prompt=2e-6, completion=1e-5, cache_read=1e-7)
    assert run_list_cost(TOKENS, no_write) == pytest.approx(0.4 + 0.08 + 0.5)
    # No cache-read price either: every prompt token at the prompt price.
    plain = ListPrice(model="m", prompt=2e-6, completion=1e-5)
    assert run_list_cost(TOKENS, plain) == pytest.approx(2.0 + 0.5)
    # Without prompt_tokens, uncached_prompt_tokens (prompt - cached) gives them back.
    without_prompt = {k: v for k, v in TOKENS.items() if k != "prompt_tokens"}
    assert run_list_cost(without_prompt, price) == run_list_cost(TOKENS, price)


@pytest.mark.parametrize(
    ("metrics", "price"),
    [
        (TOKENS, None),
        (TOKENS, ListPrice(model="m", missing="no price")),
        ({"completion_tokens": 10}, OPUS_PRICE),
        ({"prompt_tokens": 10}, OPUS_PRICE),
        (None, OPUS_PRICE),
    ],
    ids=["no-price", "unpriced", "no-prompt", "no-completion", "no-metrics"],
)
def test_unknown_price_or_tokens_is_none_never_zero(metrics, price):
    assert run_list_cost(metrics, price) is None


@pytest.mark.django_db
def test_list_prices_use_the_own_openrouter_row_or_the_catalog_twin():
    endpoint = _endpoint()
    router = _router_model("vendor/router", {**ROUTER_PRICING, "overrides": [{"prompt": "0.000004"}]})
    LLMModelFactory(model_id="vendor/twin", metadata={"openrouter_pricing": TWIN_PRICING})
    LLMModelFactory(model_id="vendor/free-twin", metadata={"openrouter_pricing": {"prompt": "0", "completion": "0"}})
    twinned = _endpoint_model(
        endpoint,
        "local-go/twinned",
        twin="vendor/twin",
        pricing={"prompt": "0.000001", "completion": "0.000002", "input_cache_read": "0.0000002"},
    )
    lonely = _endpoint_model(endpoint, "local-go/lonely")
    gone = _endpoint_model(endpoint, "local-go/gone", twin="vendor/not-synced")
    free = _endpoint_model(endpoint, "local-go/free", twin="vendor/free-twin")
    bare = LLMModelFactory(model_id="vendor/bare", metadata={})

    prices = list_prices([router, twinned, lonely, gone, free, bare, None])

    assert prices["vendor/router"].source == "own_row"
    assert prices["vendor/router"].priced_as == "vendor/router"
    assert (prices["vendor/router"].prompt, prices["vendor/router"].cache_write) == (2e-6, 2.5e-6)
    assert prices["vendor/router"].tiers is True
    twin = prices["local-go/twinned"]
    assert (twin.source, twin.priced_as, twin.prompt, twin.completion, twin.cache_read, twin.cache_write) == (
        "catalog_twin",
        "vendor/twin",
        1e-6,
        2e-6,
        1e-7,
        None,
    )
    # The route's own list prices differ (cache read): recorded, not used.
    assert twin.route_pricing == {"prompt": 1.0, "completion": 2.0, "cache_read": 0.2, "cache_write": None}
    assert twin.synced_at is not None
    for model_id, reason in (
        ("local-go/lonely", "without a catalog twin"),
        ("local-go/gone", "vendor/not-synced is not in the catalog"),
        ("local-go/free", "no positive prompt and completion price"),
        ("vendor/bare", "has no OpenRouter pricing"),
    ):
        assert not prices[model_id].priced
        assert reason in prices[model_id].missing
        assert run_list_cost(TOKENS, prices[model_id]) is None


@pytest.mark.django_db
def test_the_curated_name_map_twin_is_recorded_beside_the_catalog_twin():
    endpoint = _endpoint()
    LLMModelFactory(model_id="vendor/pro", metadata={"openrouter_pricing": TWIN_PRICING})
    LLMModelFactory(
        model_id="vendor/pro-0813",
        metadata={"openrouter_pricing": {"prompt": "0.0000001", "completion": "0.000004"}},
    )
    pro = _endpoint_model(endpoint, "local-go/pro", twin="vendor/pro")
    same = _endpoint_model(endpoint, "local-go/same", twin="vendor/pro")
    prices = list_prices([pro, same])
    curated = {"local-go/pro": "vendor/pro-0813", "local-go/same": "vendor/pro"}

    with mock.patch("backend.llm_models.services.model_covariates.map_twins", return_value=curated):
        alternatives = map_twin_prices([pro, same], prices)

    assert list(alternatives) == ["local-go/pro"]
    assert (alternatives["local-go/pro"].priced_as, alternatives["local-go/pro"].prompt) == ("vendor/pro-0813", 1e-7)
    basis = cost_basis(prices, alternatives={"local-go/pro": (alternatives["local-go/pro"], 0.42)})
    entry = next(m for m in basis["per_model"] if m["model"] == "local-go/pro")
    assert entry["priced_as"] == "vendor/pro"
    assert entry["alternative_twin"]["model"] == "vendor/pro-0813"
    assert entry["alternative_twin"]["prompt"] == 0.1
    assert entry["alternative_twin"]["list_cost_per_app"] == 0.42
    assert any("assumes vendor/pro-0813" in note for note in basis["notes"])
    assert basis["source"].startswith("OpenRouter list prices of the model's catalog row / catalog twin as synced 20")
    assert basis["unit"] == "USD per million tokens"
    assert "cache_write_tokens" in basis["formula"]
    assert any(note.startswith("Billed cost (cost_per_app") for note in basis["notes"])


# ── the ranking preset ───────────────────────────────────────────────────


def test_final_assessment_criteria_keep_the_default_weights_and_directions():
    final = mcdm.FINAL_ASSESSMENT_CRITERIA
    assert [c.key for c in final] == FINAL_KEYS
    assert [(c.direction, c.weight) for c in final] == [(c.direction, c.weight) for c in mcdm.DEFAULT_CRITERIA]
    assert set(FINAL_ASSESSMENT_METRICS) >= {"deploy_rate_itt", "pass_rate_itt", "list_cost_per_app"}
    assert mcdm.CRITERIA_PRESETS["final_assessment"] is final


def test_ranking_criteria_presets():
    custom = [{"key": "pass_rate", "direction": "benefit", "weight": 1}]

    def keys(config, plan=None, *, cross_study=False):
        criteria, preset = study_report.ranking_criteria(config, plan or {}, cross_study=cross_study)
        return [c.key for c in criteria], preset

    assert keys({}, cross_study=True) == (FINAL_KEYS, "final_assessment")
    assert keys({}) == (DEFAULT_KEYS, "default")
    assert keys({"ranking_criteria": custom}, cross_study=True) == (["pass_rate"], "report")
    assert keys({"ranking_criteria": custom}) == (["pass_rate"], "report")
    assert keys({}, {"ranking_criteria": custom}) == (["pass_rate"], "analysis_plan")


# ── the reports ──────────────────────────────────────────────────────────


@pytest.fixture
def priced_pool():
    """A (OpenRouter row) and B (endpoint model priced by its twin) on t1-t3; C (no list price) on t1-t3.

    A's t2 failed to build; B's t3 was never graded.
    """
    pool = Pool()
    endpoint = _endpoint(pool.user)
    LLMModelFactory(model_id="vendor/twin-b", metadata={"openrouter_pricing": TWIN_PRICING})
    pool.models["vendor/a"] = _router_model("vendor/a")
    pool.models["local-go/b"] = _endpoint_model(endpoint, "local-go/b", twin="vendor/twin-b")
    pool.models["local-go/c"] = _endpoint_model(endpoint, "local-go/c")
    s1, p1 = pool.study("first")
    s2, p2 = pool.study("second")
    for task in ("t1", "t2", "t3"):
        for model in ("vendor/a", "local-go/b", "local-go/c"):
            plan = p1 if model != "local-go/b" else p2
            if model == "vendor/a" and task == "t2":
                pool.run(plan, model, task, outcome=choices.Outcome.BUILD_FAIL, metrics=TOKENS)
                continue
            run = pool.run(plan, model, task, metrics={**TOKENS, "cost_usd": 0.0})
            analyze(run, {"bandit": [], "ruff": []})
            if not (model == "local-go/b" and task == "t3"):
                evaluate(run, passed=3, failed=1)
    return pool, (s1, s2)


@pytest.mark.django_db
def test_selection_report_ranks_on_the_final_assessment_criteria(priced_pool):
    pool, studies = priced_pool

    data = study_report.generate_study_report(_config(*studies), user=pool.user)

    protocol = data["protocol"]
    assert protocol["ranking_preset"] == "final_assessment"
    assert [c["key"] for c in data["ranking"]["criteria"]] == FINAL_KEYS
    outcomes = {o["model"]: o for o in data["model_outcomes"]}
    a, b = outcomes["vendor/a"], outcomes["local-go/b"]
    # A: the build failure is left out of the conditional rates and counts as 0 in the whole-cohort ones.
    assert (a["deploy_rate"], a["deploy_rate_itt"]) == (1.0, pytest.approx(2 / 3, abs=1e-4))
    assert (a["pass_rate"], a["pass_rate_itt"]) == (0.75, 0.5)
    assert a["list_cost_per_app"] == pytest.approx(1.03)
    assert a["cost_per_app"] == 0.0  # billed, kept for reference
    # B: its ungraded t3 is unknown (left out), not a failure; priced by its catalog twin.
    assert (b["deploy_rate_itt"], b["pass_rate_itt"]) == (1.0, 0.75)
    assert b["list_cost_per_app"] == pytest.approx(0.38)
    # C has no list price: unknown cost, so the ranking leaves it out (never imputed as free).
    assert outcomes["local-go/c"]["list_cost_per_app"] is None
    assert data["ranking"]["dropped_missing_criteria"] == ["local-go/c"]
    assert {r["model"] for r in data["ranking"]["ranking"]} == {"vendor/a", "local-go/b"}
    assert {k: v for k, v in data["metric_definitions"].items() if k in FINAL_ASSESSMENT_METRICS} == (
        FINAL_ASSESSMENT_METRICS
    )
    assert data["definitions"]["conditional_deploy_rate"].startswith("deploy_rate, pass_rate and functional_passed")
    assert {"whole_cohort_rates", "list_cost"} <= set(data["definitions"])
    rows = {(r["model"], r["task"]): r for r in data["per_app"]}
    assert rows[("vendor/a", "t2@1")]["deployed_itt"] is False
    assert rows[("vendor/a", "t2@1")]["list_cost_priced_as"] == "vendor/a"
    assert rows[("local-go/b", "t1@1")]["list_cost_priced_as"] == "vendor/twin-b"
    assert rows[("local-go/c", "t1@1")]["list_cost"] is None

    funnel = {r["model"]: r for r in data["funnel"]["by_model"]}
    assert funnel["vendor/a"]["deploy_rate_itt"]["rate"] == pytest.approx(0.6667, abs=1e-4)
    assert funnel["vendor/a"]["deploy_rate_itt"]["unknown_runs"] == 0
    assert funnel["vendor/a"]["mean_pass_rate_itt"] == 0.5
    assert funnel["vendor/a"]["known_failures"] == 1
    assert funnel["local-go/b"]["functional_rate_itt"]["unknown_runs"] == 1
    # The whole-cohort deployment agrees with the funnel's end-to-end rate when nothing is unknown.
    assert funnel["vendor/a"]["deploy_rate_itt"]["rate"] == funnel["vendor/a"]["end_to_end_deploy_rate"]["rate"]

    basis = {m["model"]: m for m in protocol["cost_basis"]["per_model"]}
    assert set(basis) == {"vendor/a", "local-go/b", "local-go/c"}
    assert (basis["vendor/a"]["priced_as"], basis["vendor/a"]["prompt"], basis["vendor/a"]["cache_write"]) == (
        "vendor/a",
        2.0,
        2.5,
    )
    assert (basis["local-go/b"]["priced_as"], basis["local-go/b"]["source"]) == ("vendor/twin-b", "catalog_twin")
    assert basis["local-go/c"]["priced"] is False

    codes = {v["code"]: v for v in data["validity"]}
    assert codes["conditional_vs_itt"]["level"] == "info"
    assert "vendor/a (1 build_fail: deploy 1 vs 0.667, pass 0.75 vs 0.5)" in codes["conditional_vs_itt"]["message"]
    assert "The ranking uses the whole-cohort rates." in codes["conditional_vs_itt"]["message"]
    assert codes["itt_unknown"]["level"] == "warning"
    assert "local-go/b (1)" in codes["itt_unknown"]["message"]
    assert codes["list_cost_unpriced"]["level"] == "warning"
    assert "local-go/c (an endpoint model without a catalog twin)" in codes["list_cost_unpriced"]["message"]
    assert codes["ranking_dropped"]["message"].endswith("local-go/c (list_cost_per_app).")

    summary = _make_summary("study_report", data)
    assert summary["ranking_criteria"] == "final_assessment"
    assert summary["whole_cohort_pass_rate"] == data["funnel"]["overall"]["mean_pass_rate_itt"]

    report = Report(report_type=Report.Type.STUDY_REPORT, title="Final", report_data=data)
    markdown = renderers.render_markdown(report)
    for text in (
        "Deploy rate \\| evaluated",
        "Deploy rate, failures counted as 0",
        "Pass rate, failures counted as 0",
        "Cost / app billed (USD)",
        "Cost / app at list price (USD)",
        "Deployed, failures counted as 0 [95% CI]",
        "Mean pass rate, failures counted as 0",
        "## Cost basis: list prices by model (USD per million tokens)",
        "| local-go/b | vendor/twin-b |",
        "Criteria: final assessment (whole-cohort rates, failures counted as 0; cost at list price).",
        "| Ranking criteria | final assessment",
    ):
        assert text in markdown, text
    assert "Cost basis" in renderers.render_latex(report)


@pytest.mark.django_db
def test_explicit_ranking_criteria_win_over_the_preset(priced_pool):
    pool, studies = priced_pool
    criteria = [{"key": "pass_rate", "direction": "benefit", "weight": 1}]

    data = study_report.generate_study_report(_config(*studies, ranking_criteria=criteria), user=pool.user)

    assert data["protocol"]["ranking_preset"] == "report"
    assert [c["key"] for c in data["ranking"]["criteria"]] == ["pass_rate"]
    # The final-assessment columns stay in a cross-study report.
    assert "deploy_rate_itt" in data["model_outcomes"][0]
    message = {v["code"]: v for v in data["validity"]}["conditional_vs_itt"]["message"]
    assert message.endswith("The ranking uses the conditional rates; compare it with the whole-cohort rates.")


@pytest.fixture
def single_study():
    pool = Pool()
    model = _router_model("vendor/solo")
    pool.models["vendor/solo"] = model
    study, plan = pool.study("solo")
    for task in ("t1", "t2"):
        run = pool.run(plan, "vendor/solo", task, metrics={**TOKENS, "cost_usd": 0.5})
        evaluate(run)
        analyze(run, {"ruff": []})
    pool.run(plan, "vendor/solo", "t3", outcome=choices.Outcome.START_FAIL, metrics=TOKENS)
    return pool, study


@pytest.mark.django_db
def test_single_study_report_keeps_the_default_criteria_and_its_content(single_study):
    pool, study = single_study

    data = study_report.generate_study_report({"study_id": str(study.pk), "bootstrap": False}, user=pool.user)

    assert [c["key"] for c in data["ranking"]["criteria"]] == DEFAULT_KEYS
    assert "ranking_preset" not in data["protocol"]
    assert "cost_basis" not in data["protocol"]
    assert data["metric_definitions"] == PLAN_METRICS
    assert data["definitions"] == study_report.DEFINITIONS
    assert set(data["model_outcomes"][0]) - {"model", "apps", "runs", "status"} == set(PLAN_METRICS)
    assert not any(k.endswith("_itt") or k.startswith("list_cost") for r in data["per_app"] for k in r)
    assert "deploy_rate_itt" not in data["funnel"]["overall"]
    codes = {v["code"] for v in data["validity"]}
    assert not codes & {"conditional_vs_itt", "itt_unknown", "list_cost_unpriced", "ranking_dropped"}
    assert "ranking_criteria" not in _make_summary("study_report", data)
    markdown = renderers.render_markdown(Report(report_type=Report.Type.STUDY_REPORT, title="x", report_data=data))
    assert "| Deploy rate | Pass rate |" in markdown
    assert "failures counted as 0" not in markdown
    assert "Cost basis" not in markdown


@pytest.mark.django_db
def test_single_study_report_computes_the_columns_its_ranking_names(single_study):
    pool, study = single_study
    criteria = [
        {"key": "deploy_rate_itt", "direction": "benefit", "weight": 1},
        {"key": "list_cost_per_app", "direction": "cost", "weight": 1},
    ]

    data = study_report.generate_study_report(
        {"study_id": str(study.pk), "bootstrap": False, "ranking_criteria": criteria},
        user=pool.user,
    )

    assert data["protocol"]["ranking_preset"] == "report"
    [outcome] = data["model_outcomes"]
    assert outcome["deploy_rate"] == 1.0
    assert outcome["deploy_rate_itt"] == pytest.approx(2 / 3, abs=1e-4)
    assert outcome["list_cost_per_app"] == pytest.approx(1.03)
    assert [r["model"] for r in data["ranking"]["ranking"]] == ["vendor/solo"]
    assert data["protocol"]["cost_basis"]["per_model"][0]["priced_as"] == "vendor/solo"


@pytest.mark.django_db
def test_treatment_groups_of_one_model_each_get_the_columns_and_one_price():
    """A model under two treatments forms two groups; each has its whole-cohort rates, priced as one model."""
    pool = Pool()
    pool.models["vendor/a"] = _router_model("vendor/a")
    s1, p1 = pool.study("first")
    s2, p2 = pool.study("second")
    for plan, seed in ((p1, "one"), (p2, "two")):
        run = pool.run(plan, "vendor/a", "todo", treatment_hash=content.fake_sha(seed), metrics=TOKENS)
        evaluate(run)

    data = study_report.generate_study_report(_config(s1, s2), user=pool.user)

    assert data["protocol"]["grouped_by"] == "condition"
    assert len(data["model_outcomes"]) == 2
    assert all(o["deploy_rate_itt"] == 1.0 for o in data["model_outcomes"])
    assert all(o["list_cost_per_app"] == pytest.approx(1.03) for o in data["model_outcomes"])
    assert [m["model"] for m in data["protocol"]["cost_basis"]["per_model"]] == ["vendor/a"]


# ── review fixes: outcome reasons, tested metrics, cost disclosure, labels ─


@pytest.mark.django_db
def test_a_tested_run_without_an_observed_start_is_unknown_in_the_whole_cohort_rates_as_in_the_funnel():
    """``not_tested:<level>``: the run's own reason decides (app_table runs before provenance adds it to rows)."""
    pool = Pool()
    pool.models["vendor/a"] = _router_model("vendor/a")
    pool.models["vendor/b"] = _router_model("vendor/b")
    s1, p1 = pool.study("first")
    for model in ("vendor/a", "vendor/b"):
        evaluate(pool.run(p1, model, "t1", metrics=TOKENS))
    pool.run(p1, "vendor/a", "t2", outcome_reason="not_tested:build", metrics=TOKENS)
    evaluate(pool.run(p1, "vendor/b", "t2", metrics=TOKENS))

    data = study_report.generate_study_report(_config(s1), user=pool.user)

    row = {(r["model"], r["task"]): r for r in data["per_app"]}[("vendor/a", "t2@1")]
    assert row["deployed_itt"] is None
    assert row["deployed_itt"] == study_report.deployment_outcome(row)
    funnel = {r["model"]: r for r in data["funnel"]["by_model"]}["vendor/a"]
    assert funnel["deploy_rate_itt"]["unknown_runs"] == funnel["end_to_end_deploy_rate"]["unknown_runs"] == 1
    assert funnel["deploy_rate_itt"]["rate"] == 1.0  # over the known app, the unknown one reported
    codes = {v["code"]: v for v in data["validity"]}
    assert codes["itt_unknown"]["message"].startswith("1 run(s) have no known deployment outcome")
    assert "vendor/a (1)" in codes["itt_unknown"]["message"]


def _paired_rows(model, *, pass_rate, list_cost, failed=()):
    rows = []
    for i in range(6):
        unit = f"t{i}"
        outcome = "build_fail" if unit in failed else "tested"
        row = _app(unit, outcome, pass_rate=None if unit in failed else pass_rate, list_cost=list_cost, model=model)
        rows.append({**row, "run_id": f"{model}-{unit}", "condition_id": None, "group": model})
    return rows


def test_final_assessment_comparisons_test_the_whole_cohort_rates_and_list_cost_never_the_billed_cost():
    from backend.reports.services.exploratory_comparisons import FINAL_ASSESSMENT_COMPARISONS
    from backend.reports.services.exploratory_comparisons import exploratory_comparisons

    rows = [
        *_paired_rows("vendor/a", pass_rate=0.9, list_cost=1.0, failed=("t0",)),
        *_paired_rows("vendor/b", pass_rate=0.5, list_cost=0.2),
    ]

    final = exploratory_comparisons(rows, None, cross_study=True, final_assessment=True)
    default = exploratory_comparisons(rows, None, cross_study=True)

    assert [f["metric"] for f in final["families"]] == list(FINAL_ASSESSMENT_COMPARISONS)
    assert not {"cost_per_app", "deploy_rate", "pass_rate", "end_to_end_deploy_rate"} & {
        f["metric"] for f in final["families"]
    }
    assert [f["metric"] for f in default["families"]] == [*PLAN_METRICS, "end_to_end_deploy_rate"]
    by_metric = {f["metric"]: f["comparisons"][0] for f in final["families"]}
    # b minus a per task: a's build failure counts as 0 in the whole-cohort pass rate.
    assert by_metric["pass_rate_itt"]["n_pairs"] == 6
    assert by_metric["pass_rate_itt"]["mean_delta"] == pytest.approx((5 * -0.4 + 0.5) / 6)
    assert by_metric["list_cost_per_app"]["mean_delta"] == pytest.approx(-0.8)
    # The Holm family holds the tested final-assessment comparisons only (unmeasured issue metrics have no pairs).
    assert final["comparisons_tested"] == 4
    assert final["notes"][-1].startswith("Final assessment: the rates tested are whole-cohort")


@pytest.mark.django_db
def test_selection_report_compares_the_final_assessment_metrics(priced_pool):
    from backend.reports.services.exploratory_comparisons import FINAL_ASSESSMENT_COMPARISONS

    pool, studies = priced_pool

    data = study_report.generate_study_report(_config(*studies), user=pool.user)

    metrics = [f["metric"] for f in data["exploratory_comparisons"]["families"]]
    assert metrics == list(FINAL_ASSESSMENT_COMPARISONS)
    markdown = renderers.render_markdown(Report(report_type=Report.Type.STUDY_REPORT, title="F", report_data=data))
    assert "Exploratory comparison: list_cost_per_app" in markdown
    assert "Exploratory comparison: cost_per_app" not in markdown


@pytest.mark.django_db
def test_final_report_funnel_and_ranking_note_name_what_each_rate_counts(priced_pool):
    pool, studies = priced_pool
    criteria = [
        {"key": "deploy_rate", "direction": "benefit", "weight": 1},
        {"key": "cost_per_app", "direction": "cost", "weight": 1},
    ]

    data = study_report.generate_study_report(_config(*studies, ranking_criteria=criteria), user=pool.user)
    markdown = renderers.render_markdown(Report(report_type=Report.Type.STUDY_REPORT, title="F", report_data=data))

    assert "All checks passed \\| evaluated [95% CI]" in markdown
    assert "All checks passed, failures counted as 0 [95% CI]" in markdown
    funnel = {r["model"]: r for r in data["funnel"]["by_model"]}
    # vendor/a: 2 of 3 apps evaluated, 0 fully correct; its build failure counts as 0 over all 3.
    assert funnel["vendor/a"]["functional_rate_itt"]["n"] == 3
    # Unlabelled explicit criteria are named as their columns, the conditional and billed ones so marked.
    assert "Weights: Deploy rate | evaluated 0.50 (benefit), Cost / app billed (USD) 0.50 (cost)." in markdown


def test_cost_basis_table_prints_prices_at_their_recorded_precision():
    basis = cost_basis(
        {
            "go/pro": ListPrice(
                model="go/pro",
                priced_as="vendor/pro",
                source="catalog_twin",
                prompt=0.894012e-6,
                completion=3.5e-6,
                cache_read=0.0028e-6,
                cache_write=None,
                route_pricing={"prompt": 0.9, "completion": 3.5, "cache_read": 0.0014, "cache_write": None},
            ),
        },
        alternatives={
            "go/pro": (
                ListPrice(
                    model="go/pro",
                    priced_as="vendor/pro-0813",
                    prompt=1.2e-6,
                    completion=4e-6,
                    cache_read=7.45e-8,
                ),
                0.3133,
            ),
        },
        earlier={
            "go/pro": {"runs": 2, "attempts": 3, "prompt_tokens": 10, "completion_tokens": 5, "list_cost_usd": 0.15},
        },
    )

    table = renderers._cost_basis_table(basis)
    [row] = table.rows

    assert (row["prompt"], row["completion"], row["cache_read"], row["cache_write"]) == (
        "0.894012",
        "3.5",
        "0.0028",
        "-",
    )
    assert "cache_read 0.0014" in row["note"]
    assert "name map assumes vendor/pro-0813: 1.2 / 4 / 0.0745 per MTok, 0.313 USD / app" in row["note"]
    assert "2 run(s) had earlier attempts not included (0.15 USD at these prices)" in row["note"]
    assert any(n.startswith("Not included (each run's recorded attempt only)") for n in basis["notes"])
    assert "go/pro (2 run(s), 3 attempt(s), 0.1500 USD at list price)" in basis["notes"][-1]
    assert "averaged over generated apps only" in basis["notes"][0]


@pytest.mark.django_db
def test_earlier_attempts_are_disclosed_per_model_and_priced_never_added():
    from backend.generation.testing.factories import RunAttemptFactory

    pool = Pool()
    pool.models["vendor/a"] = _router_model("vendor/a")
    s1, p1 = pool.study("first")
    run = pool.run(p1, "vendor/a", "t1", metrics=TOKENS)
    evaluate(run)
    usage = {k: v for k, v in TOKENS.items() if k != "total_tokens"}  # RunAttempt.usage (AttemptUsage)
    # Attempt 1 failed on infrastructure after spending tokens; attempt 2 produced the run (its metrics).
    RunAttemptFactory(run=run, attempt=1, infra_error="llm_error", usage={**usage, "completion_tokens": 0})
    RunAttemptFactory(run=run, attempt=2, usage=usage)
    other = pool.run(p1, "vendor/a", "t2", metrics=TOKENS)
    evaluate(other)
    RunAttemptFactory(run=other, attempt=1, usage=usage)

    data = study_report.generate_study_report(_config(s1), user=pool.user)

    [entry] = data["protocol"]["cost_basis"]["per_model"]
    # Uncached 0.1 MTok x 2 + cached 0.8 x 0.1 + written 0.1 x 2.5 (USD per MTok); no completion.
    assert entry["earlier_attempts"] == {
        "runs": 1,
        "attempts": 1,
        "prompt_tokens": 1_000_000,
        "completion_tokens": 0,
        "list_cost_usd": pytest.approx(0.53),
    }
    # The run's cost stays its recorded attempt's.
    assert data["model_outcomes"][0]["list_cost_per_app"] == pytest.approx(1.03)
    assert "vendor/a (1 run(s), 1 attempt(s), 0.5300 USD at list price)" in data["protocol"]["cost_basis"]["notes"][-1]
    assert "attempts retried after an infrastructure error" in data["protocol"]["cost_basis"]["formula"]


@pytest.mark.django_db
def test_priced_runs_without_token_counts_are_unknown_and_warned():
    pool = Pool()
    pool.models["vendor/a"] = _router_model("vendor/a")
    s1, p1 = pool.study("first")
    evaluate(pool.run(p1, "vendor/a", "t1", metrics=TOKENS))
    evaluate(pool.run(p1, "vendor/a", "t2", metrics={"cost_usd": 0.0}))

    data = study_report.generate_study_report(_config(s1), user=pool.user)

    rows = {r["task"]: r for r in data["per_app"]}
    assert rows["t2@1"]["list_cost"] is None
    assert data["model_outcomes"][0]["list_cost_per_app"] == pytest.approx(1.03)
    message = {v["code"]: v for v in data["validity"]}["list_cost_tokens_missing"]["message"]
    assert message.startswith("1 run(s) of priced models recorded no token counts")
    assert message.endswith("vendor/a (1).")
