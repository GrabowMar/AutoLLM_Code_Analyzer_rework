"""MSS v2 management commands: dry runs write nothing; real runs write provenance (network mocked)."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest
from django.core.management import call_command

from backend.llm_models.models import LLMModel
from backend.llm_models.tests.factories import LLMModelFactory
from backend.rankings.models import RankingSnapshot
from backend.rankings.models import SourceSnapshot
from backend.rankings.tests.mss_helpers import create_eligible_model
from backend.rankings.tests.mss_helpers import load_fixture

pytestmark = pytest.mark.django_db

POP = "backend.rankings.management.commands.import_popularity"
PRICES = "backend.rankings.management.commands.import_list_prices"


def _seed_frame():
    models = [
        ("anthropic/opus", (5.0, 25.0), 0.05, 1800, False),
        ("openai/sol", (2.5, 15.0), 0.03, 1750, False),
        ("google/argon", (2.0, 12.0), 0.01, 1700, False),
        ("z-ai/glm", (1.0, 4.0), 0.02, 1650, True),
        ("tencent/hy", (0.8, 2.5), 0.04, 1630, True),
        ("deepseek/flash", (0.15, 0.6), 0.2, 1620, True),
        ("xiaomi/mimo", (0.1, 0.3), 0.01, 1640, True),
        ("qwen/flash", (0.15, 0.45), 0.004, 1630, False),
        ("minimax/flash", (0.12, 0.5), 0.02, 1600, True),
    ]
    for cid, price, share, rating, open_weights in models:
        create_eligible_model(cid, price=price, share=share, rating=rating, open_weights=open_weights)


def _metadata_snapshot():
    return {m.model_id: json.dumps(m.metadata, sort_keys=True) for m in LLMModel.objects.all()}


SELECT_ARGS = [
    "--dry-run",
    "--json",
    "--offline",
    "--openrouter-budget",
    "1000",
    "--anthropic-budget",
    "400",
    "--as-of",
    "2026-11-09",
    "--set",
    'tier_quotas={"frontier": 2, "mid": 2, "budget": 2}',
    "--set",
    "vendors_min=4",
    "--set",
    "open_min=2",
    "--set",
    "closed_min=2",
    "--set",
    "claude_min=0",
    "--draws",
    "10",
    "--uniform-draws",
    "5",
    "--spec-draws",
    "5",
]


def test_select_models_dry_run_writes_nothing_and_prints_the_payload():
    _seed_frame()
    before = _metadata_snapshot()
    out = io.StringIO()
    call_command("select_models", *SELECT_ARGS, "--n", "6", stdout=out, stderr=io.StringIO())
    text = out.getvalue()
    payload = json.loads(text[: text.rindex("}") + 1])
    assert len(payload["selection"]["picks"]) == 6
    assert payload["budgets"]["openrouter_available_usd"] == 1000.0
    assert RankingSnapshot.objects.count() == 0
    assert SourceSnapshot.objects.count() == 0
    assert _metadata_snapshot() == before
    assert "DRY RUN" in text


def test_select_models_text_report_and_draft_snapshot():
    _seed_frame()
    out = io.StringIO()
    args = [a for a in SELECT_ARGS if a not in ("--dry-run", "--json")]
    call_command("select_models", *args, "--n", "6", "--no-sensitivity", stdout=out, stderr=io.StringIO())
    text = out.getvalue()
    for section in ("== Flow", "== Tiers", "== Eligible frame", "== Picks", "== Constraints", "== Sources"):
        assert section in text
    snap = RankingSnapshot.objects.get()
    assert snap.kind == "selection"
    assert snap.status == "draft"
    assert f"draft selection snapshot {snap.pk}" in text


def test_select_models_rejects_quotas_that_do_not_sum_to_n():
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("select_models", "--dry-run", "--offline", "--n", "8", stdout=io.StringIO(), stderr=io.StringIO())


def _popularity_rows():
    LLMModelFactory(
        model_id="deepseek/deepseek-v4.1-flash",
        capabilities_json={"canonical_slug": "deepseek/deepseek-v4.1-flash-20260910"},
    )
    LLMModelFactory(model_id="z-ai/glm-5.3-flash", capabilities_json={"canonical_slug": "z-ai/glm-5.3-flash-20260826"})


def _patch_popularity():
    body = load_fixture("rankings_daily_2026-11-07_08.json")
    chunks = [{"params": {"start_date": "2026-11-07", "end_date": "2026-11-08"}, "body": body}]
    return (
        patch(f"{POP}.openrouter_key", return_value="sk-test"),
        patch(f"{POP}.fetch_rankings_daily", return_value=chunks),
        patch(f"{POP}.fetch_models_index", return_value={"params": {}, "body": load_fixture("models_index.json")}),
        patch(
            f"{POP}.fetch_task_classifications",
            return_value={"params": {"window": "7d"}, "body": load_fixture("task_classifications.json")},
        ),
    )


def test_import_popularity_dry_run_makes_no_write():
    _popularity_rows()
    before = _metadata_snapshot()
    p1, p2, p3, p4 = _patch_popularity()
    with p1, p2, p3, p4:
        call_command(
            "import_popularity",
            "--dry-run",
            "--end",
            "2026-11-08",
            "--window-days",
            "2",
            stdout=io.StringIO(),
        )
    assert _metadata_snapshot() == before
    assert SourceSnapshot.objects.count() == 0


def test_import_popularity_writes_records_and_raw_snapshots():
    _popularity_rows()
    p1, p2, p3, p4 = _patch_popularity()
    with p1, p2, p3, p4:
        call_command("import_popularity", "--end", "2026-11-08", "--window-days", "2", stdout=io.StringIO())
    record = LLMModel.objects.get(model_id="deepseek/deepseek-v4.1-flash").metadata["popularity"]["openrouter"]
    assert record["days_listed"] == 2
    assert record["window"] == {"start": "2026-11-07", "end": "2026-11-08", "days": 2}
    sources = set(SourceSnapshot.objects.values_list("source", flat=True))
    assert sources == {
        "openrouter_rankings_daily",
        "openrouter_task_classifications",
        "openrouter_models_index",
        "openrouter_popularity_market",
    }
    raw = SourceSnapshot.objects.get(source="openrouter_rankings_daily")
    assert raw.as_of == "2026-11-09T09:22:19.743Z"
    assert raw.license == "CC BY 4.0"
    assert sorted(record["source_snapshot_ids"]) == sorted(
        SourceSnapshot.objects.exclude(source="openrouter_popularity_market").values_list("pk", flat=True),
    )


def test_import_list_prices_dry_run_and_write():
    LLMModelFactory(model_id="z-ai/glm-5.3", metadata={"openrouter_supported_parameters": ["tools"]})
    raw = load_fixture("models_dev_subset.json")
    with patch(f"{PRICES}.download_models_dev_raw", return_value=raw):
        call_command("import_list_prices", "--dry-run", "--no-endpoints", stdout=io.StringIO())
        assert "list_price" not in LLMModel.objects.get(model_id="z-ai/glm-5.3").metadata
        call_command("import_list_prices", "--no-endpoints", stdout=io.StringIO())
    price = LLMModel.objects.get(model_id="z-ai/glm-5.3").metadata["list_price"]
    assert (price["input"], price["output"], price["source"]) == (1.4, 4.4, "models.dev:zai/glm-5.3")
