"""Tool self-test: fixtures, expectations, evaluation, latest verdicts, submission (engine/canary.py)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from backend.analysis.engine import canary
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.analysis.tests.support.api import FAKE_TAG
from backend.analysis.tests.support.api import install_fakes

EXPECTATIONS = """\
version: 1
fixture_dirs: {positive: vulnerable, negative: clean}
tools:
  bandit:
    must_find:
      - {rule: B602, path: vulnerable/backend/app.py}
      - "B6*"
    clean_categories: [security]
    min_findings: 2
  eslint:
    must_find:
      - {rule: "no-eval"}
    clean_categories: [security, bug]
"""


@pytest.fixture
def canary_dir(tmp_path, monkeypatch):
    root = tmp_path / "canary"
    (root / "vulnerable" / "backend").mkdir(parents=True)
    (root / "vulnerable" / "backend" / "app.py").write_text("import subprocess\nsubprocess.call(x, shell=True)\n")
    (root / "vulnerable" / "frontend.js").write_text("eval(location.hash)\n")
    (root / "vulnerable" / "__pycache__").mkdir()
    (root / "vulnerable" / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\x00\x01")
    (root / "clean").mkdir()
    (root / "clean" / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "clean" / ".env.example").write_text("DEBUG=false\n")
    (root / "expectations.yaml").write_text(EXPECTATIONS)
    (root / "README.md").write_text("not a fixture\n")
    monkeypatch.setattr(canary, "CANARY_DIR", root)
    monkeypatch.setattr(canary, "EXPECTATIONS_FILE", root / "expectations.yaml")
    canary._load_expectations.cache_clear()
    yield root
    canary._load_expectations.cache_clear()


def _canary_run(
    *,
    tools=("bandit", "eslint"),
    status=AnalysisRun.Status.COMPLETED,
    tag=FAKE_TAG,
    completed_at=None,
    hashes=None,
):
    plan = {
        "schema_version": 1,
        "tools": [
            {"slug": slug, "required": True, **({"config_hash": hashes[slug]} if hashes else {})} for slug in tools
        ],
        "reviewer": None,
        "analyzer_image": {"tag": tag},
    }
    return AnalysisRunFactory(
        purpose=AnalysisRun.Purpose.CANARY,
        status=status,
        plan=plan,
        source_code={"vulnerable/backend/app.py": "x"},
        completed_at=completed_at or timezone.now(),
    )


def _finding(result, rule, path, *, category="security", counted=True):
    return FindingFactory(result=result, rule_id=rule, file_path=path, category=category, counted=counted)


def test_canary_files_and_expectations(canary_dir):
    files = canary.canary_files()

    assert sorted(files) == [
        "clean/.env.example",
        "clean/app.py",
        "vulnerable/backend/app.py",
        "vulnerable/frontend.js",
    ]
    spec = canary.expectations()
    assert spec["fixture_dirs"] == {"positive": "vulnerable", "negative": "clean"}
    assert spec["tools"]["bandit"]["must_find"] == [
        {"rule": "B602", "path": "vulnerable/backend/app.py"},
        {"rule": "B6*"},
    ]
    assert spec["tools"]["bandit"]["min_findings"] == 2
    assert spec["tools"]["eslint"]["min_findings"] is None
    assert len(canary.expectations_hash()) == 64


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("version: 2\ntools: {}\n", "unsupported canary expectations version"),
        ("- just a list\n", "must be a mapping"),
        ("version: 1\ntools:\n  bandit:\n    must_find: [{path: x}]\n", "needs a 'rule'"),
    ],
)
def test_malformed_expectations_raise(canary_dir, text, message):
    (canary_dir / "expectations.yaml").write_text(text)
    canary._load_expectations.cache_clear()
    with pytest.raises(canary.ExpectationsError, match=message):
        canary.expectations()


@pytest.mark.django_db
def test_evaluate_verdicts(canary_dir):
    run = _canary_run(tools=("bandit", "eslint", "codespell"))
    bandit = ToolResultFactory(run=run, tool_slug="bandit", status=ToolResult.Status.SUCCEEDED, tool_version="1.8.6")
    eslint = ToolResultFactory(run=run, tool_slug="eslint", status=ToolResult.Status.SUCCEEDED)
    ToolResultFactory(run=run, tool_slug="codespell", status=ToolResult.Status.SUCCEEDED)
    _finding(bandit, "B602", "vulnerable/backend/app.py")
    _finding(bandit, "B608", "vulnerable/backend/app.py")
    # A style finding under clean/ is fine; only counted findings of the clean categories fail.
    _finding(bandit, "B101", "clean/app.py", category="security", counted=False)
    _finding(eslint, "no-eval", "vulnerable/frontend.js")
    _finding(eslint, "no-unsanitized/property", "clean/app.py", category="security")

    evaluation = canary.evaluate(run)

    assert evaluation["finished"] is True
    assert evaluation["run_id"] == str(run.pk)
    assert evaluation["image_tag"] == FAKE_TAG
    bandit_eval = evaluation["tools"]["bandit"]
    assert bandit_eval["verdict"] == "passed"
    assert bandit_eval["passed"] is True
    assert bandit_eval["missing"] == []
    assert bandit_eval["found"][0]["matched"] == ["vulnerable/backend/app.py:B602"]
    assert bandit_eval["positive_findings"] == 2
    assert bandit_eval["version"] == "1.8.6"
    eslint_eval = evaluation["tools"]["eslint"]
    assert eslint_eval["verdict"] == "failed"
    assert eslint_eval["unexpected_in_clean"] == [
        {"rule": "no-unsanitized/property", "path": "clean/app.py", "category": "security"},
    ]
    assert evaluation["tools"]["codespell"]["verdict"] == "unverified"
    assert evaluation["passed"] is False


@pytest.mark.django_db
def test_failed_tool_or_missing_rule_fails(canary_dir):
    run = _canary_run()
    bandit = ToolResultFactory(
        run=run,
        tool_slug="bandit",
        status=ToolResult.Status.FAILED,
        failure_reason=ToolResult.FailureReason.PARSE_ERROR,
    )
    # Findings of a failed execution never make it pass.
    _finding(bandit, "B602", "vulnerable/backend/app.py")
    _finding(bandit, "B603", "vulnerable/backend/app.py")
    ToolResultFactory(run=run, tool_slug="eslint", status=ToolResult.Status.SUCCEEDED)

    evaluation = canary.evaluate(run)

    assert evaluation["tools"]["bandit"]["verdict"] == "failed"
    assert evaluation["tools"]["bandit"]["failure_reason"] == "parse_error"
    assert evaluation["tools"]["eslint"]["verdict"] == "failed"
    assert evaluation["tools"]["eslint"]["missing"] == [{"rule": "no-eval"}]
    assert evaluation["passed"] is False


@pytest.mark.django_db
def test_min_findings_and_unfinished_run(canary_dir):
    run = _canary_run(tools=("bandit",), status=AnalysisRun.Status.RUNNING)
    bandit = ToolResultFactory(run=run, tool_slug="bandit", status=ToolResult.Status.SUCCEEDED)
    _finding(bandit, "B602", "vulnerable/backend/app.py")

    evaluation = canary.evaluate(run)

    assert evaluation["tools"]["bandit"]["verdict"] == "failed"  # 1 < min_findings 2
    assert evaluation["finished"] is False
    assert evaluation["passed"] is False


@pytest.mark.django_db
def test_latest_and_latest_per_tool(canary_dir):
    now = timezone.now()
    older = _canary_run(completed_at=now - timedelta(hours=2))
    ToolResultFactory(run=older, tool_slug="bandit", status=ToolResult.Status.FAILED)
    eslint = ToolResultFactory(run=older, tool_slug="eslint", status=ToolResult.Status.SUCCEEDED)
    _finding(eslint, "no-eval", "vulnerable/frontend.js")
    newer = _canary_run(tools=("bandit",), completed_at=now - timedelta(hours=1))
    bandit = ToolResultFactory(run=newer, tool_slug="bandit", status=ToolResult.Status.SUCCEEDED)
    _finding(bandit, "B602", "vulnerable/backend/app.py")
    _finding(bandit, "B605", "vulnerable/backend/app.py")
    # Ignored: cancelled self-tests, other images, ad-hoc runs.
    _canary_run(status=AnalysisRun.Status.CANCELLED, completed_at=now)
    _canary_run(tag="llmlab/analyzer:otherimage00", completed_at=now)
    AnalysisRunFactory(completed_at=now)

    latest = canary.latest(FAKE_TAG)
    per_tool = canary.latest_per_tool(FAKE_TAG)

    assert latest["run_id"] == str(newer.pk)
    assert latest["passed"] is True
    assert per_tool["bandit"]["run_id"] == str(newer.pk)
    assert per_tool["bandit"]["passed"] is True
    assert per_tool["eslint"]["run_id"] == str(older.pk)
    assert per_tool["eslint"]["passed"] is True
    assert canary.latest("llmlab/analyzer:nothing00000") is None
    assert canary.latest_per_tool("llmlab/analyzer:nothing00000") == {}


def _passing(run, slug):
    result = ToolResultFactory(run=run, tool_slug=slug, status=ToolResult.Status.SUCCEEDED)
    if slug == "bandit":
        _finding(result, "B602", "vulnerable/backend/app.py")
        _finding(result, "B605", "vulnerable/backend/app.py")
    else:
        _finding(result, "no-eval", "vulnerable/frontend.js")
    return result


@pytest.mark.django_db
def test_latest_per_tool_matches_the_plans_tool_configuration(canary_dir):
    now = timezone.now()
    default = _canary_run(completed_at=now - timedelta(hours=1), hashes={"bandit": "h-default", "eslint": "h-es"})
    _passing(default, "bandit")
    _passing(default, "eslint")
    narrowed = _canary_run(tools=("bandit",), completed_at=now - timedelta(hours=3), hashes={"bandit": "h-narrow"})
    ToolResultFactory(run=narrowed, tool_slug="bandit", status=ToolResult.Status.SUCCEEDED)  # finds nothing

    plain = canary.latest_per_tool(FAKE_TAG)
    matched = canary.latest_per_tool(FAKE_TAG, config_hashes={"bandit": "h-narrow", "eslint": "h-es", "gitleaks": "g"})
    other = canary.latest_per_tool(FAKE_TAG, config_hashes={"bandit": "h-never", "eslint": ""})

    # Without a plan: newest run per tool, whatever its configuration (the tool catalog's view).
    assert (plain["bandit"]["run_id"], plain["bandit"]["verdict"]) == (str(default.pk), "passed")
    assert plain["bandit"]["config_hash"] == "h-default"
    assert "config_match" not in plain["bandit"]
    # With the plan's hashes: only a run of that exact configuration is its control.
    assert (matched["bandit"]["run_id"], matched["bandit"]["verdict"]) == (str(narrowed.pk), "failed")
    assert matched["bandit"]["config_match"] is True
    assert matched["eslint"]["run_id"] == str(default.pk)
    assert matched["eslint"]["passed"] is True
    assert "gitleaks" not in matched  # never self-tested
    for slug in ("bandit", "eslint"):  # another configuration (or no hash) is no evidence
        entry = other[slug]
        assert (entry["verdict"], entry["passed"], entry["config_match"], entry["run_id"]) == (
            "config_untested",
            None,
            False,
            None,
        )
        assert entry["other_config"]["run_id"] == str(default.pk)
        assert entry["other_config"]["verdict"] == "passed"
    assert other["bandit"]["other_config"]["config_hash"] == "h-default"


@pytest.mark.django_db
def test_latest_per_tool_query_count_does_not_grow_with_history(canary_dir, django_assert_num_queries):
    now = timezone.now()
    for i in range(canary.PER_TOOL_LOOKBACK + 5):
        slug = ("bandit", "eslint")[i % 2]
        run = _canary_run(tools=(slug,), completed_at=now - timedelta(minutes=i), hashes={slug: f"h{i}"})
        _passing(run, slug)

    # One query for the candidate runs, one for their tool results, one for their findings.
    with django_assert_num_queries(3):
        plain = canary.latest_per_tool(FAKE_TAG)
    with django_assert_num_queries(3):
        matched = canary.latest_per_tool(FAKE_TAG, config_hashes={"bandit": "h4", "eslint": "h-none"})

    assert plain["bandit"]["passed"] is True
    assert plain["eslint"]["passed"] is True
    assert matched["bandit"]["config_hash"] == "h4"
    assert matched["eslint"]["verdict"] == "config_untested"
    assert matched["eslint"]["other_config"]["config_hash"] == "h1"  # the newest eslint run


@pytest.mark.django_db
def test_run_selftest_submits_a_canary_run(canary_dir, monkeypatch, settings, user):
    engine = install_fakes(monkeypatch, settings)

    run = canary.run_selftest(user=user, tool_slugs=["bandit", "eslint"])

    assert run.purpose == AnalysisRun.Purpose.CANARY
    assert run.source_code == canary.canary_files()
    assert run.plan["reviewer"] is None
    assert run.plan["analyzer_image"]["tag"] == engine.tag
    assert [t["slug"] for t in run.plan["tools"]] == ["bandit", "eslint"]
    assert all(t["required"] for t in run.plan["tools"])
    results = list(run.results.order_by("position"))
    assert [(r.tool_slug, r.status) for r in results] == [("bandit", "pending"), ("eslint", "pending")]
    assert run.sample_manifest["totals"]["included"] == 4

    default_run = canary.run_selftest(user=user)
    assert [t["slug"] for t in default_run.plan["tools"]] == ["bandit", "eslint", "gitleaks"]


@pytest.mark.django_db
@pytest.mark.parametrize("slugs", [["ai_review"], ["bandit", "nope"]])
def test_run_selftest_rejects_unknown_or_ai_tools(canary_dir, monkeypatch, settings, user, slugs):
    install_fakes(monkeypatch, settings)
    with pytest.raises(ConfigError) as excinfo:
        canary.run_selftest(user=user, tool_slugs=slugs)
    assert "tool_slugs" in excinfo.value.field_errors
