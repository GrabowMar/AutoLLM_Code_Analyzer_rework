"""Tests for research/export.py: tidy tables, CSV and the replication zip."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC
from datetime import datetime
from uuid import UUID

import pytest
from django.utils import timezone

from backend.analysis.engine import measurement
from backend.analysis.models import AnalysisRun
from backend.analysis.research import export
from backend.analysis.research import report as report_mod
from backend.analysis.tests.research_factories import fake_outcomes
from backend.analysis.tests.research_factories import make_study

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def outcomes_boundary(monkeypatch):
    monkeypatch.setattr(measurement, "outcomes", fake_outcomes)
    monkeypatch.setattr(measurement, "OUTCOMES", {})
    monkeypatch.setenv("LLMLAB_GIT_SHA", "deadbeef")


@pytest.fixture
def small_study(user):
    return make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"), design={"bootstrap_resamples": 50})


def read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_tables_have_one_row_per_entity(small_study):
    study, _ = small_study
    tables = export.tables(study)
    assert tuple(tables) == export.TABLE_NAMES
    assert len(tables["runs"]) == 4
    assert all(r["in_analysis"] and r["canonical"] for r in tables["runs"])
    assert {r["group"] for r in tables["runs"]} == {"m-a", "m-b"}
    assert len(tables["tool_executions"]) == 12  # bandit, semgrep, ai_review per run
    assert len(tables["review_samples"]) == 12
    # Per run: 3 static findings + one AI finding per sample + one extra AI finding.
    assert len(tables["findings"]) == 4 * 7
    assert len(tables["measurements"]) == 4
    assert len(tables["outcomes"]) == 4
    for name in ("tool_executions", "findings", "issues", "review_samples", "measurements", "outcomes"):
        assert all(r["in_analysis"] is True for r in tables[name]), name


def test_runs_table_carries_target_identity_and_attrition(small_study):
    study, runs = small_study
    failed = runs[("m-a", "app-1", 0)]
    failed.status = AnalysisRun.Status.FAILED
    failed.is_complete = False
    failed.save()
    by_id = {r["run_id"]: r for r in export.tables(study)["runs"]}
    row = by_id[failed.id]
    assert row["in_analysis"] is False
    assert row["canonical"] is True
    assert row["exclusion"] == "run_failed"
    assert row["model_slug"] == "m-a"
    assert row["app_slug"] == "app-1"
    assert row["harness"] == "opencode"
    assert row["functional_pass_rate"] == 0.8
    assert row["generation_cost"] == 0.25
    assert row["measured_sloc"] == 1000.0
    other = by_id[runs[("m-b", "app-2", 0)].id]
    assert other["exclusion"] is None


def test_tool_executions_include_provenance_and_counts(small_study):
    study, runs = small_study
    rows = [r for r in export.tables(study)["tool_executions"] if r["run_id"] == runs[("m-a", "app-1", 0)].id]
    assert [r["tool_slug"] for r in rows] == ["bandit", "semgrep", "ai_review"]
    bandit, semgrep, ai = rows
    assert bandit["tool_version"] == "1.9.4"
    assert bandit["config_hash"] == "c1" * 32
    assert (bandit["findings"], bandit["counted_findings"]) == (1, 1)
    assert (semgrep["files_in_scope"], semgrep["files_scanned"], semgrep["unparseable"]) == (10, 9, 1)
    assert ai["findings"] == 4
    assert ai["counted_findings"] == 0


def test_findings_and_issues(small_study):
    study, runs = small_study
    tables = export.tables(study)
    run_id = runs[("m-b", "app-2", 0)].id
    findings = [f for f in tables["findings"] if f["run_id"] == run_id]
    static = [f for f in findings if not f["is_ai"]]
    assert [f["severity"] for f in static] == ["high", "high", "medium"]  # ordered by severity rank
    assert static[0]["cwe"] == "CWE-89"
    assert static[0]["tool_version"] in ("1.9.4", "1.180.0")
    ai = [f for f in findings if f["is_ai"]]
    assert sorted(f["review_sample_index"] for f in ai) == [0, 0, 1, 2]
    assert all(f["grounding"] == "exact" for f in ai)

    issues = [i for i in tables["issues"] if i["run_id"] == run_id]
    assert len(issues) == 2  # AI findings are not counted, so they form no issues
    shared = next(i for i in issues if i["tools"] == "bandit;semgrep")
    assert (shared["n_findings"], shared["severity"], shared["cwes"]) == (2, "high", "CWE-89")
    assert shared["rule_ids"] == "bandit:B608;semgrep:sql-injection"
    single = next(i for i in issues if i["tools"] == "semgrep")
    assert (single["severity"], single["cwes"], single["line_min"]) == ("medium", "CWE-79", 40)


def test_review_samples_and_measurements(small_study):
    study, _ = small_study
    tables = export.tables(study)
    sample = tables["review_samples"][0]
    assert sample["status"] == "succeeded"
    assert sample["cost_usd"] == 0.004
    assert sample["model_canonical_slug"] == "deepseek/deepseek-v4.1-flash-1017"
    assert sample["grounding_exact"] == 2
    assert sample["providers"] == {"DeepInfra": 1}
    m = tables["measurements"][0]
    assert m["static.issues.total"] is not None
    assert m["sloc.measured"] == 1000
    assert m["ai.agreement.mean_pairwise_jaccard"] == 0.5
    assert list(m) == list(tables["measurements"][1])  # same column order in every row


def test_outcomes_table_matches_report_values(small_study):
    study, runs = small_study
    rows = export.tables(study)["outcomes"]
    run = runs[("m-a", "app-2", 0)]
    row = next(r for r in rows if r["run_id"] == run.id)
    expected = fake_outcomes(run.measurement)
    for key, value in expected.items():
        assert row[key] == (float(value) if value is not None else None)
    assert row["group"] == "m-a"
    assert row["block"] == "app-2"


def test_to_csv_conventions():
    when = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    rows = [
        {"a": None, "b": True, "c": 0.1, "d": [1, "x"], "e": {"k": 1}, "f": UUID(int=1), "g": when, "h": 0},
        {"a": "=SUM(A1)", "i": "late column"},
    ]
    text = export.to_csv(rows)
    lines = text.splitlines()
    assert lines[0] == "a,b,c,d,e,f,g,h,i"
    parsed = read_csv(text)
    assert parsed[0]["a"] == ""  # missing is empty, never 0
    assert parsed[0]["b"] == "true"
    assert parsed[0]["c"] == "0.1"
    assert json.loads(parsed[0]["d"]) == [1, "x"]
    assert json.loads(parsed[0]["e"]) == {"k": 1}
    assert parsed[0]["f"] == "00000000-0000-0000-0000-000000000001"
    assert parsed[0]["g"] == "2026-10-22T12:00:00+00:00"
    assert parsed[0]["h"] == "0"
    assert parsed[1]["a"] == "=SUM(A1)"  # verbatim by default
    assert parsed[1]["i"] == "late column"
    assert read_csv(export.to_csv(rows, escape_formulas=True))[1]["a"] == "'=SUM(A1)"
    assert export.to_csv([], columns=["x", "y"]) == "x,y\n"
    # A lone empty field is quoted so the row is not a blank line; it still reads back as missing.
    assert export.to_csv([{"v": float("nan")}]) == 'v\n""\n'
    assert read_csv(export.to_csv([{"v": float("nan")}])) == [{"v": ""}]


def test_replication_zip_contents_and_checksums(small_study, monkeypatch):
    study, _ = small_study
    stored = {**report_mod.build_report(study), "status": "ready"}
    study.report = stored
    study.report_hash = stored["report_hash"]
    study.report_generated_at = timezone.now()
    study.save()

    def no_rebuild(study):
        msg = "the replication package must not rebuild the report"
        raise AssertionError(msg)

    monkeypatch.setattr(report_mod, "build_report", no_rebuild)
    payload = export.replication_zip(study)
    archive = zipfile.ZipFile(io.BytesIO(payload))
    names = set(archive.namelist())
    expected_tables = {f"tables/{t}.csv" for t in export.TABLE_NAMES} | {"tables/codebook.csv"}
    assert names == expected_tables | {
        "README.md",
        "SHA256SUMS",
        "manifest.json",
        "plan.json",
        "protocol.json",
        "protocol.md",
        "report.json",
        "report.md",
    }
    sums = {}
    for line in archive.read("SHA256SUMS").decode().splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    assert set(sums) == names - {"SHA256SUMS"}
    for name, digest in sums.items():
        assert hashlib.sha256(archive.read(name)).hexdigest() == digest, name
    for info in archive.infolist():
        assert info.date_time == (1980, 1, 1, 0, 0, 0)

    manifest = json.loads(archive.read("manifest.json"))
    report = json.loads(archive.read("report.json"))
    assert report == json.loads(json.dumps(stored))
    assert manifest["report_hash"] == report["report_hash"] == stored["report_hash"]
    assert manifest["report_verified"] is True
    assert manifest["report_generated_at"] == stored["generated_at"]
    assert manifest["data_changed_since_report"] is False
    assert manifest["protocol_hash"] == study.protocol_hash
    assert manifest["tables"]["runs"]["rows"] == 4
    assert json.loads(archive.read("protocol.json"))["kind"] == "analysis_study_protocol"
    assert json.loads(archive.read("plan.json"))["analyzer_image"]["tag"].startswith("llmlab/analyzer:")
    assert archive.read("report.md").decode().startswith("# Analysis report: Synthetic study")
    readme = archive.read("README.md").decode()
    assert "Report verified against the stored hash: True" in readme
    assert "Study data changed since the report was generated: False" in readme
    assert "empty CSV cell is missing data" in readme
    runs_csv = read_csv(archive.read("tables/runs.csv").decode())
    assert len(runs_csv) == 4
    assert {r["in_analysis"] for r in runs_csv} == {"true"}


def test_replication_zip_is_stable_and_omits_missing_protocol(user):
    study, _ = make_study(
        user,
        models=("m-a",),
        apps=("app-1", "app-2"),
        lock=False,
        design={"bootstrap_resamples": 50},
    )
    with pytest.raises(export.ReportUnavailable, match="no finished report"):
        export.replication_zip(study)
    study.report = {"status": "generating", "request_id": "r1", "requested_at": timezone.now().isoformat()}
    with pytest.raises(export.ReportUnavailable):
        export.replication_zip(study)

    report = report_mod.build_report(study)
    first = zipfile.ZipFile(io.BytesIO(export.replication_zip(study, report=report)))
    second = zipfile.ZipFile(io.BytesIO(export.replication_zip(study, report=report)))
    assert "protocol.json" not in first.namelist()
    for name in first.namelist():
        if name.startswith("tables/") or name == "plan.json":
            assert first.read(name) == second.read(name), name
    assert (
        json.loads(first.read("report.json"))["report_hash"] == json.loads(second.read("report.json"))["report_hash"]
    )
    manifest = json.loads(first.read("manifest.json"))
    assert manifest["protocol_hash"] is None
    assert manifest["report_verified"] is None
    assert manifest["data_changed_since_report"] is None
    assert "none (not locked)" in first.read("README.md").decode()


# --------------------------------------------------------------------------
# Regression tests (review fixes)
# --------------------------------------------------------------------------


def _store_report(study):
    stored = {**report_mod.build_report(study), "status": "ready"}
    study.report = stored
    study.report_hash = stored["report_hash"]
    study.report_generated_at = timezone.now()
    study.save()
    return stored


def _manifest(study):
    archive = zipfile.ZipFile(io.BytesIO(export.replication_zip(study)))
    return json.loads(archive.read("manifest.json")), archive


def test_deleted_job_keeps_its_run_in_the_tables(small_study):
    study, runs = small_study
    _store_report(study)
    run = runs[("m-a", "app-1", 0)]
    job_id = run.target_meta["job_id"]
    run.generation_job.delete()

    manifest, archive = _manifest(study)
    # The run keeps its target (the target_meta job id): tables and the stored report still agree.
    assert manifest["data_changed_since_report"] is False
    row = next(r for r in read_csv(archive.read("tables/runs.csv").decode()) if r["run_id"] == str(run.id))
    assert (row["job_id"], row["target_key"]) == (job_id, job_id)
    assert row["in_analysis"] == "true"
    assert row["exclusion"] == ""


def test_deleted_run_marks_the_package_as_changed(small_study):
    study, runs = small_study
    _store_report(study)
    runs[("m-b", "app-2", 0)].delete()  # no remaining run's updated_at moves
    manifest, _ = _manifest(study)
    assert manifest["data_changed_since_report"] is True


def _set_static(run, *, issues_by_category, findings_by_tool, tool_status, category_available, available=True):
    m = run.measurement
    m["tool_status"] = tool_status
    m["static"]["available"] = available
    m["static"]["issues"]["by_category"] = issues_by_category
    m["static"]["findings"]["by_category"] = dict(issues_by_category)
    m["static"]["findings"]["by_tool"] = findings_by_tool
    m["static"]["category_available"] = category_available
    run.measurement = m
    run.save()


def test_measurements_fill_sparse_count_maps(small_study):
    """Absent keys of sparse count maps are 0 when measured and NA when not, never an ambiguous empty cell."""
    study, runs = small_study
    ok = runs[("m-a", "app-1", 0)]
    broken = runs[("m-b", "app-1", 0)]
    _set_static(
        ok,
        issues_by_category={"security": 2},
        findings_by_tool={"semgrep": 2},
        tool_status={"bandit": "succeeded", "semgrep": "succeeded", "ai_review": "succeeded"},
        category_available={"security": True, "bug": True},
    )
    _set_static(
        broken,
        issues_by_category={"bug": 3},
        findings_by_tool={"bandit": 3},
        tool_status={"bandit": "succeeded", "semgrep": "failed", "ai_review": "succeeded"},
        category_available={"security": False, "bug": True},
        available=False,
    )
    ai_off = runs[("m-b", "app-2", 0)]
    m = ai_off.measurement
    m["ai"]["available"] = False
    m["ai"]["consensus"]["by_category"] = {}
    ai_off.measurement = m
    ai_off.save()
    other = runs[("m-a", "app-2", 0)]
    m = other.measurement
    m["ai"]["consensus"]["by_category"] = {"security": 2}
    m["sloc"]["by_language"] = {"python": 600, "typescript": 400}
    other.measurement = m
    other.save()

    rows = {r["run_id"]: r for r in read_csv(export.to_csv(export.tables(study)["measurements"]))}
    a, b = rows[str(ok.id)], rows[str(broken.id)]
    # A real zero where it was measured...
    assert a["static.issues.by_category.bug"] == "0"
    assert a["static.findings.by_tool.bandit"] == "0"  # bandit succeeded with no counted findings
    assert a["static.issues.by_category.security"] == "2"
    # ...and NA where it was not: a failed tool's 0 is not its success.
    assert b["static.findings.by_tool.semgrep"] == ""
    assert b["static.issues.by_category.security"] == ""
    assert b["static.issues.by_category.bug"] == "3"
    # Every category of the vocabulary is a column, so the table is rectangular.
    assert "static.issues.by_category.container" in a
    assert rows[str(ai_off.id)]["ai.consensus.by_category.security"] == ""
    assert a["ai.consensus.by_category.security"] == "0"
    assert rows[str(other.id)]["ai.consensus.by_category.security"] == "2"
    assert a["sloc.by_language.typescript"] == "0"
    # The stored measurement is never modified by the export.
    ok.refresh_from_db()
    assert ok.measurement["static"]["issues"]["by_category"] == {"security": 2}


def test_to_csv_escapes_only_selected_columns():
    rows = [{"title": '=HYPERLINK("http://x")', "code_snippet": "@app.route('/')", "rule_id": "-x", "count": "-1"}]
    escaped = read_csv(export.to_csv(rows, escape_formulas=export.UNTRUSTED_TEXT_COLUMNS))[0]
    assert escaped["title"] == '\'=HYPERLINK("http://x")'
    assert escaped["code_snippet"] == "'@app.route('/')"
    assert escaped["rule_id"] == "'-x"
    assert escaped["count"] == "-1"  # not a free-text column: verbatim
    assert read_csv(export.to_csv(rows))[0]["title"] == rows[0]["title"]  # research default: verbatim
    assert {"title", "description", "code_snippet", "file_path", "error_message"} <= export.UNTRUSTED_TEXT_COLUMNS
