"""API tests for rankings endpoints."""

from __future__ import annotations

import pytest

from backend.llm_models.tests.factories import LLMModelFactory
from backend.rankings.models import RankingSnapshot
from backend.rankings.tests.mss_helpers import create_eligible_model
from backend.rankings.tests.mss_helpers import create_route_model
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_client(client):
    user = UserFactory()
    client.force_login(user)
    return client, user


def test_list_rankings(auth_client):
    client, _ = auth_client
    LLMModelFactory(model_id="m1", model_name="One", provider="OpenAI")
    LLMModelFactory(model_id="m2", model_name="Two", provider="Google")
    create_eligible_model("z-ai/glm-5.3", provider="Z.AI")

    res = client.get("/api/rankings/?per_page=10")
    assert res.status_code == 200
    body = res.json()
    assert body["pagination"]["total"] == 3
    stats = body["statistics"]
    assert stats["eligible"] == 1
    assert stats["with_popularity"] == 1
    assert stats["with_capability"] == 1
    assert stats["unique_vendors"] == 3
    assert stats["avg_mss"] == pytest.approx(
        next(r for r in body["rankings"] if r["model_id"] == "z-ai/glm-5.3")["mss_score"],
        abs=1e-4,
    )
    row = next(r for r in body["rankings"] if r["model_id"] == "z-ai/glm-5.3")
    for key in (
        "canonical_id",
        "popularity_score",
        "price_score",
        "capability_score",
        "recency_score",
        "mss_coverage",
        "run_cost_usd",
        "price_tier",
        "exclusion",
        "routes",
    ):
        assert key in row
    assert "benchmark_score" not in row
    assert "adoption_score" not in row

    eligible = client.get("/api/rankings/?eligible_only=true").json()
    assert [r["model_id"] for r in eligible["rankings"]] == ["z-ai/glm-5.3"]
    assert client.get("/api/rankings/?min_mss=0.99").json()["pagination"]["total"] == 0


def test_rankings_sort_keys(auth_client):
    client, _ = auth_client
    create_eligible_model("a/cheap", price=(0.1, 0.4))
    create_eligible_model("b/dear", price=(5.0, 25.0))
    for sort_by, first in (("price", "a/cheap"), ("popularity", "a/cheap"), ("coverage", "a/cheap")):
        res = client.get(f"/api/rankings/?sort_by={sort_by}&sort_dir=asc&eligible_only=true")
        assert res.status_code == 200
        assert res.json()["rankings"][0]["model_id"] == first, sort_by


def test_rankings_reject_unsortable_sort_keys(auth_client):
    client, _ = auth_client
    create_eligible_model("a/one")
    # Rows carry dict fields (routes, popularity, ...): a sort on them falls back to the MSS.
    for sort_by in ("routes", "popularity", "list_price", "exclusion"):
        assert client.get(f"/api/rankings/?sort_by={sort_by}").status_code == 200, sort_by


def test_exercised_route_copies_are_counted_once(auth_client):
    from backend.generation.tests.factories import GenerationRunFactory

    client, user = auth_client
    create_eligible_model("z-ai/glm-5.3")
    local = create_route_model("opencode-go", "glm-5.3", "z-ai/glm-5.3", owner=user)
    GenerationRunFactory(created_by=user, model=local, tested=True)

    body = client.get("/api/rankings/?eligible_only=true").json()
    ids = {r["model_id"] for r in body["rankings"]}
    assert {"z-ai/glm-5.3", local.model_id} <= ids  # both rows are listed
    stats = body["statistics"]
    assert stats["eligible"] == 1
    assert stats["with_popularity"] == 1
    assert stats["with_capability"] == 1


def test_list_rankings_filters(auth_client):
    client, _ = auth_client
    LLMModelFactory(model_id="m1", model_name="One", provider="OpenAI")
    LLMModelFactory(model_id="m2", model_name="Two", provider="Google")

    res = client.get("/api/rankings/?provider=openai")
    assert res.status_code == 200
    body = res.json()
    assert body["pagination"]["total"] == 1
    assert body["rankings"][0]["model_id"] == "m1"


def test_top_models(auth_client):
    client, _ = auth_client
    LLMModelFactory(model_id="m1", model_name="One")
    create_eligible_model("z-ai/glm-5.3")

    res = client.get("/api/rankings/top/?count=5")
    assert res.status_code == 200
    body = res.json()
    assert [m["model_id"] for m in body["models"]] == ["z-ai/glm-5.3"]  # eligible models only


def test_sources_endpoint(auth_client):
    client, _ = auth_client
    create_eligible_model("z-ai/glm-5.3")

    res = client.get("/api/rankings/sources/")
    assert res.status_code == 200
    body = res.json()
    names = [s["name"] for s in body["sources"]]
    assert any("rankings-daily" in n for n in names)
    assert any("LMArena" in n for n in names)
    assert body["coverage"]["eligible"] == 1
    assert body["flow"][0]["step"] == "K0"
    assert client.get("/api/rankings/status/").status_code == 404


def test_export_csv(auth_client):
    client, _ = auth_client
    LLMModelFactory(model_id="m1", model_name="One", provider="OpenAI")

    res = client.get("/api/rankings/export/")
    assert res.status_code == 200
    assert res["Content-Type"].startswith("text/csv")
    body = res.content.decode()
    assert "model_id" in body
    assert "m1" in body
    header = body.splitlines()[0]
    for column in (
        "canonical_id",
        "eligible",
        "exclusion",
        "popularity_score",
        "price_score",
        "capability_score",
        "recency_score",
        "mss_coverage",
        "run_cost_usd",
        "price_tier",
        "open_weights",
        "vendor",
        "empirical_quality_score",
        "composite_score",
        "smoke_pass_rate",
        "n_trials",
        "empirical_density_stdev",
        "findings_total_static",
        "ai_findings_total",
    ):
        assert column in header
    for gone in ("adoption_score", "benchmark_score", "cost_efficiency_score", "accessibility_score"):
        assert gone not in header


def test_sensitivity_endpoint(auth_client):
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import Finding
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import ToolResultFactory
    from backend.generation.tests.factories import GenerationRunFactory
    from backend.generation.tests.factories import RunFileFactory

    client, user = auth_client
    model = LLMModelFactory(model_id="m1", model_name="One")
    job = GenerationRunFactory(created_by=user, model=model, tested=True)
    RunFileFactory(run=job, path="app.py", loc=1000)
    run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    result = ToolResultFactory(run=run, tool_slug="bandit")
    Finding.objects.create(result=result, severity="high", title="f", file_path="app.py")

    res = client.get("/api/rankings/sensitivity/")
    assert res.status_code == 200
    body = res.json()
    assert body["models_evaluated"] == 1
    assert body["baseline_ranking"][0]["model_id"] == "m1"
    schemes = {s["scheme"] for s in body["schemes"]}
    assert schemes == {"security_heavy", "flat", "info_included"}
    for scheme in body["schemes"]:
        assert -1.0 <= scheme["kendall_tau"] <= 1.0
        assert isinstance(scheme["adjacent_swaps"], list)


def test_unauthenticated(client):
    assert client.get("/api/rankings/").status_code == 401
    assert client.get("/api/rankings/sensitivity/").status_code == 401


def test_list_rankings_rejects_a_malformed_study_id(auth_client):
    client, _ = auth_client
    res = client.get("/api/rankings/?study_id=not-a-uuid")
    assert res.status_code == 400
    ok = client.get("/api/rankings/?study_id=6f1c1d5e-9a43-4a8b-b6a4-3c3f4f0b9d2e")
    assert ok.status_code == 200


def test_export_csv_leaves_unmeasured_counts_empty(auth_client):
    """A model nothing measured exports empty cells, not 0 findings."""
    import csv
    import io

    client, _ = auth_client
    LLMModelFactory(model_id="m1", model_name="One", provider="OpenAI")

    res = client.get("/api/rankings/export/")

    row = next(r for r in csv.DictReader(io.StringIO(res.content.decode())) if r["model_id"] == "m1")
    assert row["findings_total_static"] == ""
    assert row["dependency_findings"] == ""
    assert row["empirical_quality_score"] == ""


# ── study selection ────────────────────────────────────────────────────────


def _seed_selection_frame():
    for cid, price, share, rating, open_weights in (
        ("anthropic/opus", (5.0, 25.0), 0.05, 1800, False),
        ("openai/sol", (2.5, 15.0), 0.03, 1750, False),
        ("google/argon", (2.0, 12.0), 0.01, 1700, False),
        ("z-ai/glm", (1.0, 4.0), 0.02, 1650, True),
        ("tencent/hy", (0.8, 2.5), 0.04, 1630, True),
        ("deepseek/flash", (0.15, 0.6), 0.2, 1620, True),
        ("xiaomi/mimo", (0.1, 0.3), 0.01, 1640, True),
        ("qwen/flash", (0.15, 0.45), 0.004, 1630, False),
        ("minimax/flash", (0.12, 0.5), 0.02, 1600, True),
    ):
        create_eligible_model(cid, price=price, share=share, rating=rating, open_weights=open_weights)
    # Q-claude counts Claude models on the user's Anthropic key, so the Claude model needs that route.
    create_route_model("anthropic", "claude-opus", "anthropic/opus")


PREVIEW = "/api/rankings/selection/?n=6&frontier=2&mid=2&budget=2&openrouter_budget=1000&anthropic_budget=400"


@pytest.fixture
def staff_client(client):
    user = UserFactory(is_staff=True)
    client.force_login(user)
    return client, user


def test_selection_preview_writes_nothing(auth_client):
    client, _ = auth_client
    _seed_selection_frame()

    res = client.get(PREVIEW)

    assert res.status_code == 200
    body = res.json()
    assert body["preview"] == {
        "written": False,
        "sensitivity": False,
        "draws_capped": False,
        "draws": {"S1": 1000, "S2": 500, "S3": 0},
    }
    assert "inputs" not in body
    assert {t: len(v) for t, v in body["selection"]["by_tier"].items()} == {"frontier": 2, "mid": 2, "budget": 2}
    pick = body["selection"]["picks"][0]
    for key in (
        "canonical_id",
        "tier",
        "mss_tier",
        "mss",
        "scores",
        "route",
        "planning_cost_usd",
        "admitted_by",
        "capability_pct_in_tier",
    ):
        assert key in pick
    assert {"anthropic", "openrouter", "go"} <= set(body["selection"]["budget_use"])
    assert RankingSnapshot.objects.count() == 0


def test_selection_preview_rejects_quotas_that_do_not_sum_to_n(auth_client):
    client, _ = auth_client
    assert client.get("/api/rankings/selection/?n=6&frontier=3&mid=3&budget=3").status_code == 400


def test_selection_snapshot_lifecycle(staff_client):
    client, user = staff_client
    _seed_selection_frame()

    created = client.post(
        "/api/rankings/selection/snapshots/",
        data={
            "n": 6,
            "tier_quotas": {"frontier": 2, "mid": 2, "budget": 2},
            "openrouter_budget": 1000,
            "anthropic_budget": 400,
            "sensitivity": False,
        },
        content_type="application/json",
    )
    assert created.status_code == 201, created.content
    snap = created.json()
    assert snap["status"] == "draft"
    assert len(snap["picks"]) == 6
    assert snap["created_by"] == user.username
    # The replay inputs (about 1 MB live) are stored but only sent on request.
    assert "inputs" not in snap["payload"]
    assert snap["code_version"].startswith(("git:", "src:"))

    listing = client.get("/api/rankings/selection/snapshots/").json()
    assert [s["id"] for s in listing] == [snap["id"]]
    assert "payload" not in listing[0]
    detail = client.get(f"/api/rankings/selection/snapshots/{snap['id']}/").json()
    assert detail["id"] == snap["id"]
    assert "inputs" not in detail["payload"]
    full = client.get(f"/api/rankings/selection/snapshots/{snap['id']}/?include_inputs=true").json()
    assert full["payload"]["inputs"]["frame"]["candidates"]
    latest = client.get("/api/rankings/selection/latest/").json()
    assert latest["id"] == snap["id"]
    assert "inputs" not in latest["payload"]

    bad = client.post(
        f"/api/rankings/selection/snapshots/{snap['id']}/confirm/",
        data={"final_ids": [*snap["picks"][:-1], "nope/x"]},
        content_type="application/json",
    )
    assert bad.status_code == 400

    confirmed = client.post(
        f"/api/rankings/selection/snapshots/{snap['id']}/confirm/",
        data={"final_ids": snap["picks"], "notes": "ok"},
        content_type="application/json",
    )
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["status"] == "confirmed"
    assert body["confirmed_by"] == user.username
    assert body["confirmation"]["final_ids"] == snap["picks"]


def test_selection_writes_are_staff_only(auth_client):
    client, _ = auth_client
    _seed_selection_frame()
    res = client.post(
        "/api/rankings/selection/snapshots/",
        data={"sensitivity": False},
        content_type="application/json",
    )
    assert res.status_code == 403
    assert client.get("/api/rankings/selection/latest/").status_code == 404


def test_model_mss_position_counts_canonical_models_only(auth_client):
    from backend.generation.tests.factories import GenerationRunFactory
    from backend.llm_models.services.insights import _ranking_stats

    _, user = auth_client
    create_eligible_model("a/top", share=0.2)
    create_eligible_model("z-ai/glm-5.3", share=0.001)
    local = create_route_model("opencode-go", "glm-5.3", "z-ai/glm-5.3", owner=user)
    GenerationRunFactory(created_by=user, model=local, tested=True)

    for model_id in ("z-ai/glm-5.3", local.model_id):
        from backend.llm_models.models import LLMModel

        stats = _ranking_stats(LLMModel.objects.get(model_id=model_id), user)
        assert stats["positions"]["mss"]["of"] == 2, model_id  # the route copy is not a third model
        assert stats["positions"]["mss"]["rank"] == 2, model_id
