"""Preflight on synthetic generation jobs (engine/preflight.py)."""

from __future__ import annotations

import pytest

from backend.analysis.engine import canary as canary_mod
from backend.analysis.engine import plan as plan_mod
from backend.analysis.engine import preflight as preflight_mod
from backend.analysis.engine import sample as sample_mod
from backend.analysis.engine.reviewer import config as reviewer_config
from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalysisStudy
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import ToolResult
from backend.analysis.research import report as report_mod
from backend.analysis.research import study as study_mod
from backend.analysis.tests.support.api import APP_PY
from backend.analysis.tests.support.api import FAKE_TAG
from backend.analysis.tests.support.api import STATIC_CONFIG
from backend.analysis.tests.support.api import install_fakes
from backend.analysis.tests.support.api import llm_model
from backend.analysis.tests.support.api import make_job
from backend.analysis.tests.support.api import review_model
from backend.generation.tests.factories import GenerationJobFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def engine(monkeypatch, settings):
    return install_fakes(monkeypatch, settings)


@pytest.fixture
def plan(engine):
    return plan_mod.freeze_plan(config=STATIC_CONFIG)


def _codes(items):
    return {i["code"] for i in items}


def _row(result, job):
    return next(r for r in result["targets"] if r["job_id"] == str(job.id))


def test_eligible_target_applicability_and_estimates(plan, user):
    job = make_job(user)

    result = preflight_mod.preflight(user=user, plan=plan, jobs=[job])

    assert result["blocking"] == []
    assert result["plan_hash"] == plan_mod.plan_hash(plan)
    row = _row(result, job)
    assert row["eligible"] is True
    assert row["tools"] == {"bandit": "run", "eslint": "run", "gitleaks": "run"}
    assert row["tool_files"]["bandit"] == 1
    assert row["files"] == 3  # app.py, App.jsx, README.md (docs are included, not measured)
    assert row["sloc_total"] > 0
    assert row["sloc_measured"] > 0
    assert set(row["languages"]) >= {"python", "jsx"}
    assert len(row["sample_hash"]) == 64
    assert row["meta"]["model_slug"] == "acme/coder-1"
    assert row["label"].startswith("acme/coder-1 · todo")
    assert result["applicability"]["eslint"] == {"run": 1, "not_applicable": 0}
    assert result["estimates"]["eligible"] == 1
    assert result["estimates"]["tool_executions"] == 3
    assert result["estimates"]["ai_cost_usd"] == 0.0
    assert result["reviewer"] == {"enabled": False}
    # Tools without a self-test on this image are a warning, not blocking.
    assert {t["slug"]: t["status"] for t in result["tools"]} == dict.fromkeys(
        ("bandit", "eslint", "gitleaks"),
        "canary_unknown",
    )
    assert "canary_unknown" in _codes(result["warnings"])
    assert {t["slug"]: t["version"] for t in result["tools"]}["bandit"] == "1.8.6"


def test_ineligible_and_not_applicable_targets(plan, user):
    python_only = make_job(user, files={"app.py": APP_PY})
    docs_only = make_job(user, files={"README.md": "# Nothing here\n"})
    no_code = GenerationJobFactory(created_by=user, mode="scaffolding", status="completed", result_data={})

    result = preflight_mod.preflight(user=user, plan=plan, jobs=[python_only, docs_only, no_code])

    assert _row(result, python_only)["tools"]["eslint"] == "not_applicable"
    docs = _row(result, docs_only)
    assert docs["eligible"] is False
    assert "empty_output" in _codes(docs["reasons"])
    missing = _row(result, no_code)
    assert missing["eligible"] is False
    assert missing["reasons"][0]["severity"] == "exclude"
    assert missing["reasons"][0]["code"] in ("no_code", "unknown_shape")
    excluded = next(w for w in result["warnings"] if w["code"] == "targets_excluded")
    # The code-less job is also flagged empty (its metadata says nothing was written).
    assert excluded["reasons"]["empty_output"] == 2
    assert excluded["reasons"][missing["reasons"][0]["code"]] == 1
    # Only the eligible python-only target counts: eslint never applies.
    assert result["applicability"]["eslint"] == {"run": 0, "not_applicable": 1}
    never = [w for w in result["warnings"] if w["code"] == "tool_never_applicable"]
    assert [w["tool"] for w in never] == ["eslint"]
    assert result["estimates"] == {**result["estimates"], "targets": 3, "eligible": 1, "excluded": 2}


def test_no_targets_and_missing_plan_are_blocking(engine, user):
    result = preflight_mod.preflight(
        user=user,
        plan=None,
        jobs=[],
        plan_error={"code": "no_profile", "detail": "Pick a profile."},
    )
    assert _codes(result["blocking"]) == {"no_profile", "no_targets"}


def test_image_missing_and_daemon_down_block(engine, user):
    engine.built = False
    plan = plan_mod.freeze_plan(config=STATIC_CONFIG)
    job = make_job(user)

    result = preflight_mod.preflight(user=user, plan=plan, jobs=[job])

    assert "image_missing" in _codes(result["blocking"])
    # freeze_plan fell back to spec versions and said so.
    assert "plan_version_unverified" in _codes(result["warnings"])
    assert {t["status"] for t in result["tools"]} == {"image_missing"}

    engine.daemon_ok = False
    result = preflight_mod.preflight(user=user, plan=plan, jobs=[job])
    assert "docker_unavailable" in _codes(result["blocking"])


def test_image_status_always_has_the_full_key_set(plan, user, monkeypatch):
    """Regression: a raising image_status used to leave out tag / image_id / building."""
    from backend.analysis.engine import sandbox

    def boom():
        msg = "daemon exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr(sandbox, "image_status", boom)
    monkeypatch.setattr(sandbox, "is_building", lambda: True)
    image = preflight_mod.preflight(user=user, plan=plan, jobs=[make_job(user)])["image"]
    assert image == {
        "tag": "",
        "built": False,
        "image_id": "",
        "building": True,
        "daemon_ok": False,
        "error": "daemon exploded",
    }


def test_ordered_tool_views_follow_plan_order(plan, user):
    job = make_job(user)
    result = preflight_mod.preflight(user=user, plan=plan, jobs=[job])
    order = [t["slug"] for t in plan["tools"]]
    assert result["tool_order"] == order
    assert [a["slug"] for a in result["applicability_list"]] == order
    assert result["applicability_list"][0] == {"slug": order[0], **result["applicability"][order[0]]}
    row = _row(result, job)
    assert [t["slug"] for t in row["tool_list"]] == order
    assert all(t["status"] == row["tools"][t["slug"]] for t in row["tool_list"])


def test_study_design_notes_can_be_left_out(plan, user):
    job = make_job(user)
    assert "single_group" in _codes(preflight_mod.preflight(user=user, plan=plan, jobs=[job])["info"])
    quiet = preflight_mod.preflight(user=user, plan=plan, jobs=[job], study_design=False)
    assert "single_group" not in _codes(quiet["info"] + quiet["warnings"])


def test_changed_image_blocks(plan, engine, user):
    engine.tag = "llmlab/analyzer:somethingnew"
    result = preflight_mod.preflight(user=user, plan=plan, jobs=[make_job(user)])
    assert "image_changed" in _codes(result["blocking"])


def test_disabled_catalog_tool_warns(plan, user):
    from backend.analysis.tests.factories import AnalyzerToolFactory

    AnalyzerToolFactory(slug="eslint", is_enabled=False)
    AnalyzerTool.objects.filter(slug="eslint").update(is_enabled=False)

    result = preflight_mod.preflight(user=user, plan=plan, jobs=[make_job(user)])

    warning = next(w for w in result["warnings"] if w["code"] == "tool_disabled")
    assert warning["tools"] == ["eslint"]


def test_failed_canary_blocks(plan, user, monkeypatch):
    monkeypatch.setattr(
        canary_mod,
        "latest_per_tool",
        lambda image_tag=None, **_kwargs: {
            "bandit": {"passed": False, "verdict": "failed", "missing": [{"rule": "B602"}]},
            "eslint": {"passed": True, "verdict": "passed"},
        },
    )

    result = preflight_mod.preflight(user=user, plan=plan, jobs=[make_job(user)])

    statuses = {t["slug"]: t["status"] for t in result["tools"]}
    assert statuses == {"bandit": "canary_failed", "eslint": "ready", "gitleaks": "canary_unknown"}
    blocking = next(b for b in result["blocking"] if b["code"] == "canary_failed")
    assert blocking["tools"] == ["bandit"]


def _canary(user, hashes: dict[str, str], *, passed: bool = True, tag: str = FAKE_TAG, age_hours: int = 1):
    """A finished self-test that ran each tool of *hashes* at that configuration (``config_hash``)."""
    from datetime import timedelta

    from django.utils import timezone

    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import FindingFactory
    from backend.analysis.tests.factories import ToolResultFactory

    run = AnalysisRunFactory(
        created_by=user,
        purpose=AnalysisRun.Purpose.CANARY,
        status=AnalysisRun.Status.COMPLETED,
        completed_at=timezone.now() - timedelta(hours=age_hours),
        plan={
            "schema_version": 1,
            "analyzer_image": {"tag": tag},
            "tools": [{"slug": slug, "required": True, "config_hash": h} for slug, h in hashes.items()],
        },
    )
    for slug in hashes:
        result = ToolResultFactory(run=run, tool_slug=slug, status=ToolResult.Status.SUCCEEDED)
        if passed:
            FindingFactory(result=result, run=run, rule_id="R1", file_path="vulnerable/app.py", category="security")
    return run


def test_canary_vouches_only_for_the_configuration_it_ran(engine, user, monkeypatch):
    """A passing self-test of bandit's default config is no control for a narrowed bandit config."""
    expected = {"must_find": [{"rule": "R1"}], "clean_categories": [], "min_findings": None}
    monkeypatch.setattr(
        canary_mod,
        "expectations",
        lambda: {
            "fixture_dirs": {"positive": "vulnerable", "negative": "clean"},
            "tools": {"bandit": expected, "eslint": expected},
        },
    )
    default_plan = plan_mod.freeze_plan(
        config={"tools": [{"slug": "bandit"}, {"slug": "eslint"}], "reviewer": {"enabled": False}},
    )
    narrowed_plan = plan_mod.freeze_plan(
        config={
            "tools": [{"slug": "bandit", "config": {"strict": True}}, {"slug": "eslint"}],
            "reviewer": {"enabled": False},
        },
    )
    defaults = {t["slug"]: t["config_hash"] for t in default_plan["tools"]}
    assert narrowed_plan["tools"][0]["config_hash"] != defaults["bandit"]
    canary_run = _canary(user, defaults)  # what POST /selftest/ runs: the default configs
    job = make_job(user)

    default_rows = {t["slug"]: t for t in preflight_mod.preflight(user=user, plan=default_plan, jobs=[job])["tools"]}
    narrowed = preflight_mod.preflight(user=user, plan=narrowed_plan, jobs=[job])

    assert {s: (t["status"], t["canary"]["passed"]) for s, t in default_rows.items()} == {
        "bandit": ("ready", True),
        "eslint": ("ready", True),
    }
    assert default_rows["bandit"]["canary"]["config_match"] is True
    rows = {t["slug"]: t for t in narrowed["tools"]}
    assert rows["eslint"]["status"] == "ready"  # same configuration: the control applies
    bandit = rows["bandit"]
    assert bandit["status"] == "canary_unknown"
    assert bandit["canary"]["passed"] is None
    assert bandit["canary"]["verdict"] == "config_untested"
    assert bandit["canary"]["run_id"] is None
    assert bandit["canary"]["missing"] == []
    assert bandit["canary"]["other_config"] == {
        "run_id": str(canary_run.pk),
        "config_hash": defaults["bandit"],
        "verdict": "passed",
        "evaluated_at": canary_run.completed_at.isoformat(),
    }
    warning = next(w for w in narrowed["warnings"] if w["code"] == "canary_config_untested")
    assert warning["tools"] == ["bandit"]
    assert "canary_unknown" not in _codes(narrowed["warnings"])  # running a self-test would not help
    assert narrowed["blocking"] == []
    # The protocol's controls do not stamp "passed" on the untested configuration.
    controls = {t["slug"]: t for t in report_mod._controls(narrowed, narrowed_plan)["tools"]}
    assert (controls["bandit"]["status"], controls["bandit"]["passed"]) == ("canary_unknown", None)
    assert controls["eslint"]["passed"] is True

    # A self-test of exactly the narrowed configuration is its control, even when older than the default one.
    _canary(user, {"bandit": narrowed_plan["tools"][0]["config_hash"]}, passed=False, age_hours=2)
    bandit = preflight_mod.preflight(user=user, plan=narrowed_plan, jobs=[job])["tools"][0]
    assert (bandit["status"], bandit["canary"]["passed"], bandit["canary"]["config_match"]) == (
        "canary_failed",
        False,
        True,
    )


def test_reviewer_section_cost_and_missing_key(engine, user):
    review_model()
    config = {
        "tools": [{"slug": "bandit"}],
        "reviewer": {"enabled": True, "samples": 2, "reasoning_effort": None, "budget": {"max_cost_usd": 1.0}},
    }
    plan = plan_mod.freeze_plan(config=config)
    job = make_job(user)

    result = preflight_mod.preflight(user=user, plan=plan, jobs=[job])

    reviewer = result["reviewer"]
    assert reviewer["enabled"] is True
    assert reviewer["model"] == "deepseek/deepseek-v4.1-flash"
    assert reviewer["model_found"] is True
    assert reviewer["structured_outputs"] is True
    assert reviewer["key_present"] is True
    assert reviewer["samples"] == 2
    estimate = reviewer["cost_estimate"]
    assert 0 < estimate["low"] <= estimate["expected"] <= estimate["high"]
    assert estimate["per_target"] == estimate["expected"]
    assert _row(result, job)["tools"]["ai_review"] == "run"
    assert result["estimates"]["ai_reviews"] == 1
    assert result["estimates"]["ai_samples"] == 2
    assert result["estimates"]["ai_cost_usd"] == estimate["expected"]
    assert result["applicability"]["ai_review"] == {"run": 1, "not_applicable": 0}
    assert "reviewer_key_missing" not in _codes(result["blocking"])

    engine.key = None
    result = preflight_mod.preflight(user=user, plan=plan, jobs=[job])
    assert "reviewer_key_missing" in _codes(result["blocking"])
    assert result["reviewer"]["key_present"] is False


def test_comparability_and_design_blocks(plan, user):
    m1 = llm_model("acme/coder-1")
    m2 = llm_model("acme/coder-2")
    a = make_job(user, model=m1, app_slug="todo", prompt_hash="p-todo-1")
    b = make_job(user, model=m2, app_slug="todo", prompt_hash="p-todo-2")
    c = make_job(user, model=m1, app_slug="blog", prompt_hash="p-blog", stack_slug="fastapi-vue")

    result = preflight_mod.preflight(
        user=user,
        plan=plan,
        jobs=[a, b, c],
        analysis_design={"group_by": ["model"], "block_by": "app"},
    )

    comparability = result["comparability"]
    assert comparability["plan_hash"] == result["plan_hash"]
    assert comparability["mixed"]["stack_slug"] == ["fastapi-vue", "flask-react"]
    assert set(comparability["values"]) == {"prompt_hash", "bundle_key", "stack_slug", "mode", "harness"}
    warnings = _codes(result["warnings"])
    assert {"comparability_prompt_hash", "comparability_stack_slug", "incomplete_blocks"} <= warnings
    design = result["design"]
    assert design["group_by"] == ["model"]
    assert design["groups"] == {"acme/coder-1": 2, "acme/coder-2": 1}
    assert design["blocks"] == {"blog": 1, "todo": 2}
    assert design["cells"] == {"acme/coder-1": {"blog": 1, "todo": 1}, "acme/coder-2": {"todo": 1}}
    assert design["complete_blocks"] is False
    assert design["mode"] == "independent"
    assert design["reason"] == "1 empty group \u00d7 block cells"

    # Grouping by stack makes the stack mix the design, not a confound.
    result = preflight_mod.preflight(user=user, plan=plan, jobs=[a, c], analysis_design={"group_by": "stack"})
    assert "comparability_stack_slug" not in _codes(result["warnings"])


def test_complete_balanced_design(plan, user):
    m1, m2 = llm_model("acme/a"), llm_model("acme/b")
    jobs = [make_job(user, model=m, app_slug=app) for m in (m1, m2) for app in ("todo", "blog")]

    design = preflight_mod.preflight(user=user, plan=plan, jobs=jobs, analysis_design={"group_by": "model"})["design"]

    assert design["complete_blocks"] is True
    assert design["mode"] == "blocked"
    assert design["balanced"] is True
    assert design["n_groups"] == 2
    assert design["min_group_size"] == 2
    assert design["replicates"] == {"min": 1, "max": 1}


@pytest.mark.parametrize(
    ("apps", "reason"),
    [
        # A target without an app is never paired: no shared "(none)" block.
        (("todo", None), "2 target(s) without a known app"),
        # One block cannot carry a blocked test.
        (("todo", "todo"), "fewer than 2 app blocks"),
    ],
)
def test_design_agrees_with_the_locked_protocol(plan, user, apps, reason):
    m1, m2 = llm_model("acme/a"), llm_model("acme/b")
    jobs = [make_job(user, model=m, app_slug=app) for m in (m1, m2) for app in apps]
    design = {"group_by": "model", "block_by": "app"}

    result = preflight_mod.preflight(user=user, plan=plan, jobs=jobs, analysis_design=design)

    out = result["design"]
    assert (out["complete_blocks"], out["mode"], out["reason"]) == (False, "independent", reason)
    assert "incomplete_blocks" in _codes(result["warnings"])
    # The protocol built from this preflight reaches the same verdict.
    study = AnalysisStudy(
        created_by=user,
        analysis_design=design,
        preflight=study_mod.json_safe(result),
        plan=plan,
    )
    protocol_design = report_mod.build_protocol(study)["design"]
    assert {k: protocol_design[k] for k in ("groups", "blocks", "cells", "complete", "mode", "reason")} == {
        k: out[k] for k in ("groups", "blocks", "cells", "complete", "mode", "reason")
    }
    rows = {r["job_id"]: r for r in result["targets"]}
    assert {rows[str(j.id)]["block"] for j in jobs} == set(apps)
    assert {rows[str(j.id)]["group"] for j in jobs} == {"acme/a", "acme/b"}


def test_inline_target(plan, user):
    result = preflight_mod.preflight(user=user, plan=plan, inline={"main.py": "print('hi')\n"})

    row = result["targets"][0]
    assert row["job_id"] is None
    assert row["label"] == "inline source"
    assert row["eligible"] is True
    assert row["tools"] == {"bandit": "run", "eslint": "not_applicable", "gitleaks": "run"}


def test_platform_resolver_error_is_blocking(plan, user, monkeypatch):
    job = make_job(user)

    def boom(job, scope=None):
        msg = "resolver bug"
        raise RuntimeError(msg)

    monkeypatch.setattr(sample_mod, "resolve_job", boom)
    result = preflight_mod.preflight(user=user, plan=plan, jobs=[job])

    row = _row(result, job)
    assert row["eligible"] is False
    assert row["reasons"][0]["code"] == "resolver_error"
    assert "resolver_error" in _codes(result["blocking"])


def test_preflight_never_mutates_and_scales(plan, user, django_assert_max_num_queries):
    jobs = [make_job(user, model=llm_model(f"acme/m{i % 4}"), app_slug=f"app{i % 5}") for i in range(40)]
    before = (AnalysisRun.objects.count(), ToolResult.objects.count(), AnalysisStudy.objects.count())

    # Per-target work is in memory (resolution, eligibility, applicability);
    # the query count must not grow with tools or files.
    with django_assert_max_num_queries(10 + 4 * len(jobs)):
        result = preflight_mod.preflight(user=user, plan=plan, jobs=jobs)

    assert len(result["targets"]) == 40
    assert all(r["eligible"] for r in result["targets"])
    assert (AnalysisRun.objects.count(), ToolResult.objects.count(), AnalysisStudy.objects.count()) == before


def test_labels_follow_the_report_vocabulary():
    meta = {"job_id": "0c49cd0d-0000", "model_slug": "m/x", "app_slug": "todo", "harness": "aider", "repeat_index": 2}
    assert preflight_mod.target_label(meta) == "m/x · todo · aider · r2 · 0c49cd0d"
    assert preflight_mod.target_label({}) == "inline source"
    assert preflight_mod.group_label(meta, ["model", "harness"]) == "m/x \u00d7 aider"  # the report's separator
    assert preflight_mod.group_label({}, "condition") == "(none)"
    assert FAKE_TAG.startswith("llmlab/analyzer:")


def test_targets_carry_grouping_meta_reasons_and_canary_controls(plan, user, monkeypatch):
    monkeypatch.setattr(
        canary_mod,
        "latest_per_tool",
        lambda image_tag=None, **_kwargs: {
            "bandit": {
                "passed": False,
                "verdict": "failed",
                "missing": [{"rule": "B602"}],
                "unexpected_in_clean": [{"rule": "B101", "path": "clean/app.py", "category": "security"}],
                "run_id": "r1",
            },
            "eslint": {"passed": False, "verdict": "unverified", "missing": [], "unexpected_in_clean": []},
        },
    )
    docs_only = make_job(user, files={"README.md": "# docs\n"})

    result = preflight_mod.preflight(
        user=user,
        plan=plan,
        jobs=[make_job(user), docs_only],
        inline={"a.py": "x = 1\n"},
    )

    for row in result["targets"]:
        assert set(preflight_mod.GROUPING_KEYS) <= set(row["meta"])
        assert isinstance(row["label"], str)
        assert isinstance(row["eligible"], bool)
        for reason in row["reasons"]:
            assert {"code", "severity", "detail"} <= set(reason)
    inline = next(r for r in result["targets"] if r["job_id"] is None)
    assert inline["meta"]["model_slug"] is None
    assert inline["meta"]["mode"] == "inline"
    excluded = _row(result, docs_only)
    assert excluded["reasons"][0]["detail"]

    canaries = {t["slug"]: t["canary"] for t in result["tools"]}
    assert canaries["bandit"]["passed"] is False
    assert canaries["bandit"]["missing"] == [{"rule": "B602"}]
    assert canaries["bandit"]["unexpected_in_clean"][0]["rule"] == "B101"
    # A tool without expectations, or without a self-test, is no evidence either way.
    assert (canaries["eslint"]["passed"], canaries["eslint"]["verdict"]) == (None, "unverified")
    assert canaries["gitleaks"] == {
        "passed": None,
        "verdict": "untested",
        "missing": [],
        "unexpected_in_clean": [],
        "run_id": None,
        "evaluated_at": None,
    }
    statuses = {t["slug"]: t["status"] for t in result["tools"]}
    assert statuses == {"bandit": "canary_failed", "eslint": "canary_unknown", "gitleaks": "canary_unknown"}


def test_ai_inputs_follow_the_reviewers_exact_selection(engine, user):
    review_model()
    config = {"tools": [{"slug": "bandit"}], "reviewer": {"enabled": True, "samples": 1, "reasoning_effort": None}}
    plan = plan_mod.freeze_plan(config=config)
    reviewed = make_job(user, files={"app.py": APP_PY, "empty.py": "   \n"})
    blank_only = make_job(user, files={"app.py": "\n\n"})

    result = preflight_mod.preflight(user=user, plan=plan, jobs=[reviewed, blank_only])

    # Blank files are never sent to the model, so they are neither counted nor paid for.
    row = _row(result, reviewed)
    assert row["tools"]["ai_review"] == "run"
    assert row["tool_files"]["ai_review"] == 1
    smp = sample_mod.resolve_job(reviewed, plan["scope"])
    chars, files = reviewer_config.review_inputs(plan["reviewer"], smp)
    assert files == 1
    expected = reviewer_config.estimate_cost(plan["reviewer"], chars=chars, files=files)
    assert row["ai_cost_estimate"]["expected"] == expected["expected"]
    nothing = _row(result, blank_only)
    assert nothing["tools"]["ai_review"] == "not_applicable"
    assert nothing["tool_files"]["ai_review"] == 0


def _aider_plan(*, max_tokens: int, reasoning: dict | None, supports_reasoning: bool = True) -> dict:
    review_model()
    plan = plan_mod.freeze_plan(config={"tools": [{"slug": "bandit"}], "reviewer": {"enabled": True}})
    reviewer = plan["reviewer"]
    reviewer.update(engine="agent", harness="aider")
    reviewer["params"].update(max_tokens=max_tokens, reasoning=reasoning)
    reviewer["model"]["supports"].update(reasoning=supports_reasoning, reasoning_default_effort="medium")
    return plan


@pytest.mark.parametrize(
    ("max_tokens", "reasoning", "supports_reasoning", "warned"),
    [
        (16_000, None, True, True),  # reasons at the model default
        (16_000, {"effort": "low"}, True, True),  # the observed truncation
        (32_000, {"effort": "low"}, True, False),  # enough room
        (16_000, {"effort": "none"}, True, False),  # reasoning switched off
        (16_000, None, False, False),  # a model that does not reason
    ],
)
def test_aider_with_a_reasoning_model_and_a_small_output_cap_warns(
    engine,
    user,
    max_tokens,
    reasoning,
    supports_reasoning,
    warned,
):
    plan = _aider_plan(max_tokens=max_tokens, reasoning=reasoning, supports_reasoning=supports_reasoning)

    result = preflight_mod.preflight(user=user, plan=plan, jobs=[make_job(user)])

    warning = next((w for w in result["warnings"] if w["code"] == "reviewer_output_cap"), None)
    assert (warning is not None) is warned
    if warned:
        assert warning["max_output_tokens"] == max_tokens
        assert warning["recommended"] == preflight_mod.AIDER_MIN_OUTPUT_TOKENS


def test_other_harnesses_do_not_get_the_aider_warning(engine, user):
    plan = _aider_plan(max_tokens=16_000, reasoning={"effort": "low"})
    plan["reviewer"]["harness"] = "opencode"

    result = preflight_mod.preflight(user=user, plan=plan, jobs=[make_job(user)])

    assert "reviewer_output_cap" not in _codes(result["warnings"])


def test_a_local_reviewer_model_needs_its_endpoint_not_an_openrouter_key(engine, user):
    """Preflight resolves the frozen model's access like the executor: no key needed for a local model."""
    from backend.inference.tests.conftest import make_endpoint
    from backend.inference.tests.conftest import make_local_model
    from backend.users.tests.factories import UserFactory

    endpoint = make_endpoint(user, api_flavor="native", kind="ollama")
    model = make_local_model(
        endpoint,
        metadata={"ollama": {"capabilities": ["completion"], "show_digest": "sha256:" + "ab" * 32}},
    )
    config = {
        "tools": [{"slug": "bandit"}],
        "reviewer": {
            "enabled": True,
            "model": model.model_id,
            "samples": 1,
            "reasoning_effort": None,
            "max_output_tokens": 2000,
        },
    }
    plan = plan_mod.freeze_plan(config=config, user=user)
    assert plan["reviewer"]["model"]["local"]["endpoint"] == endpoint.slug
    engine.key = None  # no OpenRouter key at all
    job = make_job(user)

    result = preflight_mod.preflight(user=user, plan=plan, jobs=[job])
    codes = _codes(result["blocking"])
    assert "reviewer_key_missing" not in codes
    assert "reviewer_model_unavailable" not in codes
    assert result["reviewer"]["key_present"] is True

    # The endpoint is private: another user's run cannot reach it.
    other = UserFactory()
    other_job = make_job(other)
    result = preflight_mod.preflight(user=other, plan=plan, jobs=[other_job])
    assert "reviewer_model_unavailable" in _codes(result["blocking"])
    assert result["reviewer"]["key_present"] is False


def test_freeze_plan_scopes_a_local_reviewer_model_to_the_user(engine, user):
    from backend.analysis.engine.catalog import ConfigError
    from backend.inference.tests.conftest import make_endpoint
    from backend.inference.tests.conftest import make_local_model
    from backend.users.tests.factories import UserFactory

    model = make_local_model(make_endpoint(user, api_flavor="native", kind="ollama"))
    config = {
        "tools": [{"slug": "bandit"}],
        "reviewer": {
            "enabled": True,
            "model": model.model_id,
            "samples": 1,
            "reasoning_effort": None,
            "max_output_tokens": 2000,
        },
    }
    assert plan_mod.freeze_plan(config=config, user=user)["reviewer"]["model"]["local"]
    with pytest.raises(ConfigError):
        plan_mod.freeze_plan(config=config, user=UserFactory())
