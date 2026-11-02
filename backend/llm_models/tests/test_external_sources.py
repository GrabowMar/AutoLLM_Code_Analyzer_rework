"""Epoch AI / LMArena import through the curated name map (no network: fixtures only)."""

from __future__ import annotations

import csv
import io
from unittest.mock import patch

import pytest
from django.core.management import call_command

from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.models import LLMModel
from backend.llm_models.services import external_sources as es
from backend.llm_models.services.external_sources import ARENA_CONFIGS
from backend.llm_models.services.external_sources import MATCH_KINDS
from backend.llm_models.services.external_sources import MapEntry
from backend.llm_models.services.external_sources import import_external_covariates
from backend.llm_models.services.external_sources import index_lmarena
from backend.llm_models.services.external_sources import load_model_map
from backend.llm_models.services.external_sources import normalize_epoch_row
from backend.llm_models.services.external_sources import parse_active_params
from backend.llm_models.services.external_sources import parse_epoch_csv
from backend.llm_models.services.external_sources import parse_model_map
from backend.llm_models.tests.factories import LLMModelFactory

EPOCH_HEADER = [
    "Model",
    "Organization",
    "Publication date",
    "Link",
    "Parameters",
    "Parameters notes",
    "Training compute (FLOP)",
    "Training compute notes",
    "Training dataset size (total)",
    "Dataset size notes",
    "Confidence",
    "Model accessibility",
    "Last modified",
    "Open model weights?",
]


def _epoch_csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EPOCH_HEADER)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in EPOCH_HEADER})
    return buffer.getvalue()


EPOCH_ROWS = parse_epoch_csv(
    _epoch_csv(
        [
            {
                "Model": "Flash One",
                "Organization": "Lab",
                "Publication date": "2026-09-09",
                "Link": "https://example.org/report.pdf \n\nhttps://example.org/news",
                "Parameters": "552000000000.0",
                "Parameters notes": "552B total, (not counting 196B Engram parameters) 16B active during decode.",
                "Training compute (FLOP)": "4.32e+24",
                "Training dataset size (total)": "45000000000000",
                "Confidence": "Likely",
                "Model accessibility": "Open weights (unrestricted)",
                "Last modified": "2026-10-29 16:01:19+00:00",
                "Open model weights?": "Yes",
            },
            {"Model": "Dense Two", "Parameters": "31000000000", "Confidence": "Confident"},
        ],
    ),
)


def _arena_row(name: str, category: str, rating: float, *, votes: int = 1000, rank: int = 1) -> dict:
    return {
        "model_name": name,
        "organization": "lab",
        "license": "MIT",
        "rating": rating,
        "rating_lower": rating - 8,
        "rating_upper": rating + 8,
        "variance": 16.0,
        "vote_count": votes,
        "rank": rank,
        "category": category,
        "leaderboard_publish_date": "2026-10-28",
    }


ARENA_ROWS = {
    "webdev": [
        _arena_row("Flash One (Max)", "overall", 1619.2, votes=3510, rank=29),
        _arena_row("Flash One (Max)", "webdev", 1619.2, votes=3510, rank=29),
        _arena_row("Flash One (Max)", "webdev-html", 1600.0),
        _arena_row("Other", "webdev", 1369.0, rank=114),
        _arena_row("Twice", "webdev", 1406.5),
        _arena_row("Twice", "webdev", 1370.9),
        _arena_row("Flash One (Max)", "image_to_webdev", 1500.0),
    ],
    "text": [
        _arena_row("Flash One (Max)", "coding", 1480.0, rank=12),
        _arena_row("Flash One (Max)", "overall", 1465.0),
    ],
    "agent": [
        {
            "model_name": "Flash One (Max)",
            "score": 0.0191,
            "score_ci_lower": 0.0158,
            "score_ci_upper": 0.0225,
            "observation_count": 42504300.0,
            "session_count": 266436.0,
            "rank": 23,
            "category": "overall",
            "leaderboard_publish_date": "2026-10-28",
        },
    ],
}


def _entry(model_id: str, **kwargs) -> MapEntry:
    defaults = {"epoch": None, "lmarena": {}, "match": "exact", "note": "", "twin": None}
    defaults.update(kwargs)
    return MapEntry(model_id=model_id, **defaults)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        msg = "network access in a test"
        raise AssertionError(msg)

    monkeypatch.setattr(es.requests, "get", refuse)


# ── Name map ────────────────────────────────────────────────────────────────


def test_parse_model_map_validates_shape():
    entries = parse_model_map(
        {
            "models": {
                "a/b": {"epoch": "B", "lmarena": {"webdev": "B (Max)", "text": None}, "match": "variant", "note": "x"},
                "c/d": {"epoch": None, "lmarena": None, "match": "assumed", "twin": "c/e"},
            },
        },
    )
    assert entries["a/b"].lmarena == {"webdev": "B (Max)"}
    assert entries["a/b"].mapped
    assert not entries["c/d"].mapped
    assert entries["c/d"].twin == "c/e"

    for bad in (
        {},
        {"models": {"a/b": {"epoch": "B", "colour": "red"}}},
        {"models": {"a/b": {"lmarena": {"vision": "B"}}}},
        {"models": {"a/b": {"match": "fuzzy"}}},
        {"models": {"a/b": {"epoch": 3, "match": "exact"}}},
        {"models": {"a/b": {"epoch": "B", "note": "match forgotten"}}},  # never defaults to "exact"
    ):
        with pytest.raises(ValueError, match="external model map"):
            parse_model_map(bad)


def test_shipped_map_is_valid_and_local_routes_are_assumed():
    entries = load_model_map()
    assert entries, "the curated map must not be empty"
    for model_id, entry in entries.items():
        assert entry.match in MATCH_KINDS
        assert set(entry.lmarena) <= set(ARENA_CONFIGS)
        assert entry.note, f"{model_id} needs a curation note"
        if model_id.startswith("local-"):
            assert entry.match == "assumed", model_id
    assert entries["z-ai/glm-5.3-flash"].epoch == "GLM-5.3-Flash"


# ── Epoch ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        ("320B total, 18B active.", 18_000_000_000),
        ("1.6T total, 49B active", 49_000_000_000),
        ("104 billion active parameters", 104_000_000_000),
        ("MoE with 1T total parameters and 32B parameters active per forward pass", 32_000_000_000),
        ("from the base model: Total Parameters: 456B Activated Parameters per Token: 45.9B", 45_900_000_000),
        ("Total Parameters\t1T Activated Parameters\t32B", 32_000_000_000),
        ("MoE total parameters: 300B active parameters: 47B", 47_000_000_000),
        ("a large-scale MoE model that activates 14 billion parameters out of 142 billion", 14_000_000_000),
        ("141B params, 39B active: https://example.org", 39_000_000_000),
        ("552B total, (not counting 196B Engram) 16B active during decode, 8B during prefill.", 16_000_000_000),
        ("235B total, 22B active. Variant P1-30B-A3B has 30B total, 3B active.", None),  # two values
        ('"maintaining activations around 10B" safetensors: 229B params', None),
        ("Based on HF page", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_active_params_only_when_unambiguous(note, expected):
    assert parse_active_params(note) == expected


def test_parse_epoch_csv_drops_duplicate_names():
    rows = parse_epoch_csv(_epoch_csv([{"Model": "Same"}, {"Model": "Same"}, {"Model": "Unique"}]))
    assert set(rows) == {"Unique"}


def test_normalize_epoch_row_keeps_notes_source_and_license():
    record = normalize_epoch_row(
        EPOCH_ROWS["Flash One"],
        _entry("x/y", epoch="Flash One", match="variant", note="effort differs"),
        fetched_at="2026-11-02T00:00:00+00:00",
    )
    assert record["parameters"] == 552_000_000_000
    assert record["active_params_parsed"] == 16_000_000_000
    assert "16B active" in record["parameters_notes"]
    assert record["training_compute_flop"] == pytest.approx(4.32e24)
    assert record["training_dataset_size"] == pytest.approx(4.5e13)
    assert record["link"] == "https://example.org/report.pdf"
    assert record["license"] == "CC-BY 4.0 (Epoch AI)"
    assert record["match"] == "variant"
    assert record["mapping_note"] == "effort differs"
    dense = normalize_epoch_row(EPOCH_ROWS["Dense Two"], _entry("a/b"), fetched_at="t")
    assert dense["active_params_parsed"] is None
    assert dense["active_params_source"] is None
    assert dense["training_compute_flop"] is None  # missing stays None, never 0


# ── LMArena ─────────────────────────────────────────────────────────────────


def test_index_lmarena_maps_categories_and_marks_duplicates_ambiguous():
    index = index_lmarena(ARENA_ROWS)
    assert set(index) == {"webdev", "webdev_html", "text_overall", "text_coding", "agent"}
    webdev = index["webdev"]
    assert webdev["n_ranked"] == 4  # all rows of the category, duplicates included
    assert webdev["min"] == pytest.approx(1369.0)
    assert webdev["max"] == pytest.approx(1619.2)
    assert "Twice" not in webdev["rows"]
    assert "Twice" in webdev["ambiguous"]


def test_arena_paging_stops_once_wanted_categories_are_complete(monkeypatch):
    calls: list[dict] = []

    class Resp:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    pages = {
        0: [{"row": _arena_row("A", "overall", 1.0)}, {"row": _arena_row("A", "chinese", 1.0)}],
        2: [{"row": _arena_row("A", "coding", 1.0)}],
        4: [{"row": _arena_row("A", "math", 1.0)}],
    }

    def fake_get(url, *, params=None):
        calls.append({"url": url, **(params or {})})
        if url == es.LMARENA_STATS_URL:
            stats = {"column_name": "category", "column_statistics": {"frequencies": {"overall": 1, "coding": 1}}}
            return Resp({"statistics": [stats]})
        return Resp({"rows": pages.get(params["offset"], []), "num_rows_total": 6})

    monkeypatch.setattr(es, "_http_get", fake_get)
    monkeypatch.setattr(es, "LMARENA_PAGE_SIZE", 2)
    rows = es._arena_paged({"dataset": "d", "config": "text", "split": "latest"}, categories=("overall", "coding"))
    assert [r["category"] for r in rows] == ["overall", "coding"]
    assert [c.get("offset") for c in calls if c["url"] == es.LMARENA_ROWS_URL] == [0, 2]


def test_http_get_waits_out_a_rate_limit_instead_of_aborting(monkeypatch):
    class Resp:
        def __init__(self, status, headers=None):
            self.status_code = status
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise es.requests.HTTPError(str(self.status_code))

    answers = [Resp(429, {"Retry-After": "7"}), Resp(429), Resp(200)]
    pauses: list[float] = []
    monkeypatch.setattr(es.requests, "get", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr(es.time, "sleep", pauses.append)
    assert es._http_get("https://datasets-server.huggingface.co/rows").status_code == 200
    assert pauses == [7.0, es.RATE_LIMIT_PAUSE_SECONDS * 2]

    monkeypatch.setattr(es.requests, "get", lambda *a, **k: Resp(404))
    with pytest.raises(es.requests.HTTPError):
        es._http_get("https://datasets-server.huggingface.co/rows")  # other 4xx: no retry


def test_partial_arena_statistics_are_not_used_to_stop_paging(monkeypatch):
    # Partial statistics count a prefix of the split only; trusting them would cut rows off.
    class Resp:
        def json(self):
            stats = {"column_name": "category", "column_statistics": {"frequencies": {"overall": 1}}}
            return {"partial": True, "statistics": [stats]}

    monkeypatch.setattr(es, "_http_get", lambda url, *, params=None: Resp())
    assert es._category_counts({"dataset": "d", "config": "text", "split": "latest"}) == {}


# ── Import ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestImport:
    def _run(self, entries, **kwargs):
        return import_external_covariates(
            entries=entries,
            epoch_rows=EPOCH_ROWS,
            arena_rows=ARENA_ROWS,
            fetched_at="2026-11-02T00:00:00+00:00",
            **kwargs,
        )

    def test_writes_epoch_and_lmarena_and_keeps_other_metadata(self):
        model = LLMModelFactory(model_id="lab/flash-one", metadata={"models_dev": {"knowledge": "2025-05"}})
        entries = {
            "lab/flash-one": _entry(
                "lab/flash-one",
                epoch="Flash One",
                lmarena={"webdev": "Flash One (Max)", "text": "Flash One (Max)", "agent": "Flash One (Max)"},
                match="variant",
                note="arena effort Max",
            ),
        }
        report = self._run(entries)
        model.refresh_from_db()
        assert model.metadata["models_dev"] == {"knowledge": "2025-05"}
        epoch = model.metadata["epoch"]
        assert epoch["model"] == "Flash One"
        assert epoch["fetched_at"] == "2026-11-02T00:00:00+00:00"
        arena = model.metadata["lmarena"]
        assert set(arena["categories"]) == {"webdev", "webdev_html", "text_overall", "text_coding", "agent"}
        webdev = arena["categories"]["webdev"]
        assert webdev["rating"] == pytest.approx(1619.2)
        assert webdev["rating_lower"] == pytest.approx(1611.2)
        assert webdev["vote_count"] == 3510
        assert webdev["rank"] == 29
        assert webdev["n_ranked"] == 4
        agent = arena["categories"]["agent"]
        assert agent["metric"] == "score"
        assert agent["score"] == pytest.approx(0.0191)
        assert agent["session_count"] == 266436
        assert arena["license"] == "CC-BY 4.0 (LMArena)"
        assert arena["match"] == "variant"
        assert report.rows[0]["epoch"] == "matched"
        assert report.rows[0]["lmarena"] == "matched"
        assert report.count("epoch", "matched") == 1

    def test_dry_run_writes_nothing(self):
        model = LLMModelFactory(model_id="lab/flash-one", metadata={})
        report = self._run({"lab/flash-one": _entry("lab/flash-one", epoch="Flash One")}, dry_run=True)
        model.refresh_from_db()
        assert model.metadata == {}
        assert report.rows[0]["changed"] == ["epoch"]

    def test_null_mapping_removes_stale_value_and_missing_name_keeps_it(self):
        stale = LLMModelFactory(model_id="lab/stale", metadata={"epoch": {"model": "Old"}, "lmarena": {"x": 1}})
        kept = LLMModelFactory(model_id="lab/kept", metadata={"epoch": {"model": "Not In Epoch"}})
        entries = {
            "lab/stale": _entry("lab/stale"),
            "lab/kept": _entry("lab/kept", epoch="Not In Epoch", lmarena={"webdev": "Not In Arena"}),
        }
        report = self._run(entries)
        stale.refresh_from_db()
        kept.refresh_from_db()
        assert "epoch" not in stale.metadata
        assert "lmarena" not in stale.metadata
        assert kept.metadata["epoch"] == {"model": "Not In Epoch"}
        statuses = {r["model_id"]: (r["epoch"], r["lmarena"]) for r in report.rows}
        assert statuses == {
            "lab/kept": ("missing_in_source", "missing_in_source"),
            "lab/stale": ("unmapped", "unmapped"),
        }
        assert any("Not In Epoch" in w for w in report.warnings)
        assert any("Not In Arena" in w for w in report.warnings)

    def test_corrected_mapping_never_keeps_the_record_of_the_old_name(self):
        # The curator replaced a wrong match; the new name is (still) missing from the download.
        # The record matched under the old name must go, not linger as this model's covariate.
        relinked = LLMModelFactory(
            model_id="lab/relinked",
            metadata={
                "epoch": {"model": "Wrong Model", "parameters": 7},
                "lmarena": {"names": {"webdev": "Wrong (Max)"}, "categories": {"webdev": {"rating": 1.0}}},
                "models_dev": {"knowledge": "2025-05"},
            },
        )
        same = LLMModelFactory(
            model_id="lab/same",
            metadata={"lmarena": {"names": {"webdev": "Not In Arena"}, "categories": {"webdev": {"rating": 2.0}}}},
        )
        entries = {
            "lab/relinked": _entry("lab/relinked", epoch="Not In Epoch", lmarena={"webdev": "Not In Arena"}),
            "lab/same": _entry("lab/same", lmarena={"webdev": "Not In Arena"}),
        }
        report = self._run(entries)
        relinked.refresh_from_db()
        same.refresh_from_db()
        assert "epoch" not in relinked.metadata
        assert "lmarena" not in relinked.metadata
        assert relinked.metadata["models_dev"] == {"knowledge": "2025-05"}
        assert same.metadata["lmarena"]["categories"]["webdev"]["rating"] == 2.0  # same name: kept
        rows = {r["model_id"]: r for r in report.rows}
        assert rows["lab/relinked"]["changed"] == ["epoch", "lmarena"]
        assert rows["lab/same"]["changed"] == []
        assert any("earlier mapping" in w and "lab/relinked" in w for w in report.warnings)

    def test_ambiguous_arena_name_is_not_matched(self):
        LLMModelFactory(model_id="lab/twice", metadata={})
        report = self._run({"lab/twice": _entry("lab/twice", lmarena={"webdev": "Twice"})}, sources=("lmarena",))
        assert report.rows[0]["lmarena"] == "missing_in_source"
        assert any("ambiguous" in w for w in report.warnings)

    def test_reports_unknown_models_and_used_models_missing_from_map(self, user):
        used = LLMModelFactory(model_id="lab/used-unmapped")
        GenerationJobFactory(model=used, created_by=user)
        report = self._run({"lab/ghost": _entry("lab/ghost", epoch="Flash One")})
        assert report.rows[0]["epoch"] == "model_not_in_db"
        assert any("lab/used-unmapped" in w for w in report.warnings)

    def test_model_filter_limits_scope(self):
        a = LLMModelFactory(model_id="lab/a", metadata={})
        b = LLMModelFactory(model_id="lab/b", metadata={})
        entries = {"lab/a": _entry("lab/a", epoch="Flash One"), "lab/b": _entry("lab/b", epoch="Dense Two")}
        report = self._run(entries, model_ids={"lab/a", "lab/missing"})
        a.refresh_from_db()
        b.refresh_from_db()
        assert "epoch" in a.metadata
        assert "epoch" not in b.metadata
        assert [r["model_id"] for r in report.rows] == ["lab/a"]
        assert any("lab/missing" in w for w in report.warnings)

    def test_command_dry_run_and_suggest(self, tmp_path):
        LLMModelFactory(model_id="lab/flash-one", model_name="Flash One", metadata={})
        map_file = tmp_path / "map.yaml"
        map_file.write_text("models:\n  lab/flash-one:\n    epoch: Flash One\n    match: exact\n    note: test\n")
        out = io.StringIO()
        with (
            patch.object(es, "fetch_epoch_csv", return_value=_epoch_csv([{"Model": "Flash One"}])),
            patch.object(es, "fetch_lmarena", return_value=ARENA_ROWS),
        ):
            call_command("import_external_covariates", "--dry-run", "--epoch", "--map", str(map_file), stdout=out)
            assert "would write epoch" in out.getvalue()
            assert LLMModel.objects.get(model_id="lab/flash-one").metadata == {}

        out = io.StringIO()
        with (
            patch(
                "backend.llm_models.management.commands.import_external_covariates.fetch_epoch_csv",
                return_value=_epoch_csv([{"Model": "Flash One"}, {"Model": "Unrelated"}]),
            ),
            patch(
                "backend.llm_models.management.commands.import_external_covariates.fetch_lmarena",
                return_value=ARENA_ROWS,
            ),
        ):
            call_command("import_external_covariates", "--suggest", "--model", "lab/flash-one", stdout=out)
        text = out.getvalue()
        assert "lab/flash-one" in text
        assert "epoch: Flash One (1.00)" in text
        assert "lmarena.webdev: Flash One (Max)" in text
