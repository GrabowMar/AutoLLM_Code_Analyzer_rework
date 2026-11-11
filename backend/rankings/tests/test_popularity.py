"""MSS v2 popularity input from recorded OpenRouter Data API responses (no network), and the API client."""

from __future__ import annotations

import datetime as dt
import logging

import pytest

from backend.rankings.services import openrouter_api
from backend.rankings.services.frame import build_frame
from backend.rankings.services.popularity import compute_openrouter_popularity
from backend.rankings.services.popularity import permaslug_map
from backend.rankings.tests.mss_helpers import AS_OF
from backend.rankings.tests.mss_helpers import CONFIG
from backend.rankings.tests.mss_helpers import eligible_row
from backend.rankings.tests.mss_helpers import load_fixture
from backend.rankings.tests.mss_helpers import row

WINDOW = (dt.date(2026, 11, 7), dt.date(2026, 11, 8))
NEW_MODEL_CREATED = int(dt.datetime(2026, 11, 8, 6, tzinfo=dt.UTC).timestamp())


def _compute(extra_index=()):
    rows = [
        eligible_row(
            "deepseek/deepseek-v4.1-flash",
            canonical_slug="deepseek/deepseek-v4.1-flash-20260910",
            created=1789021285,
        ),
        eligible_row(
            "deepseek/deepseek-v4.1-flash-1017",
            canonical_slug="deepseek/deepseek-v4.1-flash-20261017",
            created=1792225624,
        ),
        eligible_row("z-ai/glm-5.3-flash", canonical_slug="z-ai/glm-5.3-flash-20260826", created=1787752741),
        eligible_row(
            "anthropic/claude-opus-5.5",
            canonical_slug="anthropic/claude-opus-5.5-20260921",
            created=1790094732,
        ),
        eligible_row("tencent/hy4"),
        row("tencent/hy4-preview", canonical_slug="tencent/hy4-preview-20260827", created=1787897375),
        row(
            "nvidia/nemotron-3-ultra-550b-a55b",
            canonical_slug="nvidia/nemotron-3-ultra-550b-a55b-20260604",
            created=1780551208,
        ),
        row("x/new", canonical_slug="x/new-20261108", created=NEW_MODEL_CREATED),
    ]
    frame = build_frame(rows, AS_OF, include_previews=True, config=CONFIG)
    body = load_fixture("rankings_daily_2026-11-07_08.json")
    index = load_fixture("models_index.json")["data"] + list(extra_index)
    return compute_openrouter_popularity(
        [{"params": {"start_date": "2026-11-07", "end_date": "2026-11-08"}, "body": body}],
        candidates=frame["candidates"],
        models_index=index,
        window=WINDOW,
        fetched_at="2026-11-09T09:30:00+00:00",
        classifications={"params": {"window": "7d"}, "body": load_fixture("task_classifications.json")},
        source_snapshot_ids=[7],
    )


def _day_rows(date):
    return [r for r in load_fixture("rankings_daily_2026-11-07_08.json")["data"] if r["date"] == date]


def test_permaslug_map_ignores_variant_ids():
    index = load_fixture("models_index.json")["data"]
    mapping = permaslug_map(index)
    assert mapping["deepseek/deepseek-v4.1-flash-20261017"] == "deepseek/deepseek-v4.1-flash-1017"
    assert all(":" not in v for v in mapping.values())


def test_dated_versions_are_summed_into_the_canonical_model():
    records, _ = _compute()
    ds = records["deepseek/deepseek-v4.1-flash"]
    assert ds["permaslugs"] == ["deepseek/deepseek-v4.1-flash-20260910", "deepseek/deepseek-v4.1-flash-20261017"]
    day = next(d for d in ds["daily"] if d["date"] == "2026-11-07")
    assert day["tokens"] == 2121386631266 + 6985645772629
    assert ds["days_listed"] == 2
    assert ds["days_censored"] == 0


def test_market_total_includes_other_and_free_rows_and_the_limit_is_the_smallest_listed():
    _, market = _compute()
    for date in ("2026-11-07", "2026-11-08"):
        rows = _day_rows(date)
        assert market["days"][date]["total"] == sum(int(r["total_tokens"]) for r in rows)
        listed = [int(r["total_tokens"]) for r in rows if r["model_permaslug"] != "other"]
        assert market["days"][date]["limit"] == min(listed)


def test_free_rows_never_enter_a_model_numerator_and_absent_days_are_censored():
    records, market = _compute()
    nemo = records["nvidia/nemotron-3-ultra-550b-a55b"]
    assert nemo["days_listed"] == 0
    assert nemo["days_censored"] == 2
    assert all(d["tokens"] is None and d["censored"] for d in nemo["daily"])
    assert "nvidia/nemotron-3-ultra-550b-a55b" not in market["days"]["2026-11-07"]["listed"]
    # The censored volume share is half the day's detection limit (L/2).
    expected = sum(0.5 * d["limit"] / d["total"] for d in nemo["daily"]) / 2
    assert nemo["volume_share"] == pytest.approx(expected)
    assert nemo["censor_factor"] == 0.5


def test_prerelease_rows_count_for_the_ga_model():
    records, _ = _compute()
    assert records["tencent/hy4"]["permaslugs"] == ["tencent/hy4-preview-20260827"]
    assert "tencent/hy4-preview" not in records


def test_exposure_starts_at_the_first_openrouter_created_date():
    records, _ = _compute()
    new = records["x/new"]
    assert new["exposure_start"] == "2026-11-08"
    assert new["days_available"] == 1
    assert new["short_exposure"] is True


def test_provenance_is_stored():
    records, market = _compute()
    rec = records["anthropic/claude-opus-5.5"]
    assert rec["as_of"] == "2026-11-09T09:22:19.743Z"
    assert rec["citation"] == "Source: OpenRouter (openrouter.ai/rankings), as of 2026-11-09T09:22:19.743Z"
    assert rec["license"] == "CC BY 4.0"
    assert rec["window"] == {"start": "2026-11-07", "end": "2026-11-08", "days": 2}
    assert rec["source_snapshot_ids"] == [7]
    assert market["citation"] == rec["citation"]


def test_code_tags_resolve_dated_ids():
    records, _ = _compute()
    assert records["deepseek/deepseek-v4.1-flash"]["code_tags"] == {"code:general_impl": pytest.approx(0.398)}
    assert records["tencent/hy4"]["code_tags"] == {"code:general_impl": 0.056}
    assert "classification_tagging" not in records["deepseek/deepseek-v4.1-flash"]["code_tags"]


# ── client ─────────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return self.responses.pop(0)


SECRET = "sk-or-v1-test-secret-123"  # noqa: S105 - fake key for the redaction tests


def test_the_key_goes_only_to_openrouter_and_is_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    session = _Session([_Resp(503), _Resp(429), _Resp(200, {"data": [], "meta": {}})])
    sleeps = []
    body = openrouter_api.get_json(
        "datasets/rankings-daily",
        params={"a": 1},
        key=SECRET,
        session=session,
        sleep=sleeps.append,
    )
    assert body == {"data": [], "meta": {}}
    assert len(session.calls) == 3
    assert sleeps == [4.0, 8.0]
    assert all(c["headers"]["Authorization"] == f"Bearer {SECRET}" for c in session.calls)
    assert all(c["timeout"] == openrouter_api.TIMEOUT for c in session.calls)
    assert SECRET not in caplog.text
    assert openrouter_api._headers("https://example.com/api/v1/x", SECRET) == {"Accept": "application/json"}


def test_client_errors_are_not_retried_and_never_echo_the_key():
    session = _Session([_Resp(401)])
    with pytest.raises(openrouter_api.OpenRouterError) as err:
        openrouter_api.get_json("credits", key=SECRET, session=session, sleep=lambda _s: None)
    assert "HTTP 401" in str(err.value)
    assert SECRET not in str(err.value)
    assert len(session.calls) == 1


def test_rankings_daily_is_fetched_in_seven_day_chunks():
    session = _Session([_Resp(200, {"data": [], "meta": {}}) for _ in range(4)])
    chunks = openrouter_api.fetch_rankings_daily(
        dt.date(2026, 10, 12),
        dt.date(2026, 11, 8),
        key=SECRET,
        session=session,
    )
    assert [c["params"] for c in chunks] == [
        {"start_date": "2026-10-12", "end_date": "2026-10-18"},
        {"start_date": "2026-10-19", "end_date": "2026-10-25"},
        {"start_date": "2026-10-26", "end_date": "2026-11-01"},
        {"start_date": "2026-11-02", "end_date": "2026-11-08"},
    ]


def test_models_index_is_public_and_credits_are_parsed():
    session = _Session([_Resp(200, {"data": [{"id": "a/b", "canonical_slug": "a/b-1", "created": 1, "name": "x"}]})])
    index = openrouter_api.fetch_models_index(session=session)
    assert "Authorization" not in session.calls[0]["headers"]
    assert openrouter_api.trim_models_index(index["body"]) == [{"id": "a/b", "canonical_slug": "a/b-1", "created": 1}]
    credit = openrouter_api.fetch_credits(
        key=SECRET,
        session=_Session([_Resp(200, {"data": {"total_credits": 50, "total_usage": 29.11}})]),
    )
    assert credit["available"] == pytest.approx(20.89)
    with pytest.raises(openrouter_api.OpenRouterError):
        openrouter_api.fetch_credits(key=SECRET, session=_Session([_Resp(200, {"data": {}})]))
