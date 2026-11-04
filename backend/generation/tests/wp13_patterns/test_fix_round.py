"""WP-13 fix round: ITT correctness for model failures, a frozen export, plan-corrected planned p-values."""

from __future__ import annotations

import csv
import io
import zipfile

import pytest
from django.utils import timezone

from backend.generation import identity
from backend.generation.contracts.slot import ClusterParams
from backend.generation.contracts.slot import PatternScope
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.patterns import create_pattern_run
from backend.generation.patterns import detectors as det
from backend.generation.patterns import export
from backend.generation.patterns import features as feat
from backend.generation.patterns import normalize as norm
from backend.generation.patterns import run as run_patterns
from backend.generation.testing.fake_patterns import FakeSlotExecutor
from backend.generation.testing.fake_patterns import TargetSpec
from backend.generation.testing.fake_patterns import build_slot_study
from backend.generation.tests.wp13_patterns.test_pipeline import INSECURE
from backend.generation.tests.wp13_patterns.test_pipeline import ROUTES
from backend.generation.tests.wp13_patterns.test_pipeline import SECURE
from backend.generation.tests.wp13_patterns.test_pipeline import make_study
from backend.generation.tests.wp13_patterns.test_pipeline import refs
from backend.generation.tests.wp13_patterns.test_pipeline import row

pytestmark = pytest.mark.django_db

OK, FAIL = {"c1": "pass"}, {"c1": "fail"}


def _lock_with(study, detector_refs, extra_plan):
    patterns = {
        "detector_set_hash": det.detector_set_hash(det.resolve_refs(detector_refs)),
        "normalizer_version": norm.NORMALIZER_VERSION,
        "cluster_params": ClusterParams().model_dump(mode="json"),
        "scope": PatternScope().model_dump(mode="json"),
        "primary_features": detector_refs[:1],
    }
    study.analysis_plan = {"itt": True, "patterns": patterns, **extra_plan}
    study.analysis_plan_hash = identity.H(study.analysis_plan)
    study.prereg_hash = identity.H({"fake": "prereg"})
    study.locked_at = timezone.now()
    study.save()


def _zip_tables(pattern_run):
    with zipfile.ZipFile(io.BytesIO(export.build_export(pattern_run))) as zf:
        return {
            name: list(csv.DictReader(io.StringIO(zf.read(name).decode())))
            for name in ("variants.csv", "features.csv", "prevalence.csv", "comparisons.csv")
        }


# ---------------------------------------------------------------------------
# 1. build_fail / start_fail / output_invalid count as 0 pass for correctness (ITT)
# ---------------------------------------------------------------------------


def test_model_failures_are_zero_pass_for_correctness(detectors, fake_semgrep_runner):
    synthetic = build_slot_study(
        [TargetSpec(path="app/routes.py", file_text=ROUTES, span=(4, 6))],
        n_conditions=2,
        repeats=4,
    )
    ex = FakeSlotExecutor(synthetic)
    ex.produce(0, 0, 0, SECURE, checks=OK)
    ex.produce(0, 0, 1, SECURE, outcome="build_fail")
    ex.produce(0, 0, 2, None)  # output_invalid
    ex.produce(0, 0, 3, INSECURE, outcome="start_fail")
    for r in range(4):
        ex.produce(1, 0, r, SECURE, checks=OK)
    pattern_run = create_pattern_run(
        synthetic.study,
        detectors=refs(detectors, "py-sql-fstring"),
        cluster_params={"method": "exact"},
        user=synthetic.user,
    )
    run_patterns(pattern_run)
    pattern_run.refresh_from_db()
    s = pattern_run.summary
    c0 = synthetic.conditions[0].pk
    t0 = str(synthetic.targets[0].pk)

    correctness = row(s, c0, t0, "slot_tests_all_pass")
    assert (correctness["k"], correctness["n_measured"], correctness["n_itt"]) == (1, 4, 4)
    assert (correctness["missing"], correctness["na"], correctness["errors"], correctness["p"]) == (0, 0, 0, 0.25)
    assert "bounds" not in correctness  # nothing unknown: the failures are data, not attrition

    # The detector feature: the output_invalid variant has no code, so it stays missing there.
    feature = row(s, c0, t0, "py-sql-fstring@span")
    assert (feature["k"], feature["n_measured"], feature["missing"]) == (1, 3, 1)
    assert feature["correctness"] == {"k": 1, "n_measured": 4, "p": 0.25}
    conditional = row(s, c0, t0, "py-sql-fstring@span", conditional_on="slot_tests_all_pass")
    assert (conditional["k"], conditional["n_measured"]) == (0, 1)

    # The comparison of correctness pairs every c0 variant, the failures included.
    comp = next(c for c in s["comparisons"] if c["column"] == "slot_tests_all_pass")
    assert comp["table"] == {"a": 1, "b": 0, "c": 3, "d": 0}

    tables = _zip_tables(pattern_run)
    invalid_run = str(synthetic.runs[(0, 0, 2)].pk)
    invalid_rows = [r for r in tables["features.csv"] if r["run_id"] == invalid_run]
    assert [(r["column"], r["value"], r["y"]) for r in invalid_rows] == [("slot_tests_all_pass", "absent", "0")]
    build_fail = next(r for r in tables["variants.csv"] if r["run_id"] == str(synthetic.runs[(0, 0, 1)].pk))
    assert (build_fail["outcome"], build_fail["slot_tests_all_pass"]) == ("build_fail", "absent")


def test_an_uninformative_target_stays_na_for_model_failures(detectors, fake_semgrep_runner):
    synthetic = build_slot_study([TargetSpec(path="app/routes.py", file_text=ROUTES, span=(4, 6))], repeats=2)
    target = synthetic.targets[0]
    target.validation = {"status": "uninformative", "discriminating_checks": []}
    target.save(update_fields=["validation"])
    ex = FakeSlotExecutor(synthetic)
    ex.produce(0, 0, 0, SECURE, outcome="build_fail")
    ex.produce(0, 0, 1, None)
    pattern_run = create_pattern_run(synthetic.study, detectors=refs(detectors, "py-sql-fstring"))
    run_patterns(pattern_run)
    pattern_run.refresh_from_db()
    r = row(pattern_run.summary, synthetic.conditions[0].pk, str(target.pk), "slot_tests_all_pass")
    assert (r["k"], r["n_measured"], r["na"], r["missing"]) == (0, 0, 2, 0)


# ---------------------------------------------------------------------------
# 2. The export of a completed PatternRun is its own analysed record set
# ---------------------------------------------------------------------------


def test_export_is_frozen_against_retries_and_retests(detectors, fake_semgrep_runner):
    synthetic = make_study()
    pattern_run = create_pattern_run(
        synthetic.study,
        detectors=refs(detectors, "py-sql-fstring", "probe-sqli"),
        cluster_params={"method": "exact"},
        user=synthetic.user,
    )
    run_patterns(pattern_run)
    pattern_run.refresh_from_db()
    before = _zip_tables(pattern_run)
    records = pattern_run.summary["records"]
    assert len(records) == 16
    assert {r["run"] for r in records} == {str(run.pk) for run in synthetic.runs.values()}

    # After the analysis: an infra retry gives cell (0, 0, 0) a new current run, and a retest makes a
    # passing variant fail.
    retried = synthetic.runs[(0, 0, 0)]
    GenerationRun.objects.filter(pk=retried.pk).update(outcome="infra_error", usable=False)
    ex = FakeSlotExecutor(synthetic)
    replacement = ex.produce(0, 0, 0, SECURE, checks={"c1": "pass"})
    retested = synthetic.runs[(1, 0, 0)]
    evaluation = RunEvaluation.objects.filter(run=retested).latest("created_at")
    result = dict(evaluation.result)
    result["checks"] = [{"id": "c1", "category": "functional", "status": "fail"}]
    RunEvaluation.objects.create(
        run=retested,
        eval_suite=evaluation.eval_suite,
        eval_level=evaluation.eval_level,
        status="measured",
        result=result,
        stages=evaluation.stages,
    )

    after = _zip_tables(pattern_run)
    assert after == before
    run_ids = {r["run_id"] for r in after["variants.csv"]}
    assert str(retried.pk) in run_ids
    assert str(replacement.pk) not in run_ids
    old = next(r for r in after["variants.csv"] if r["run_id"] == str(retried.pk))
    assert (old["outcome"], old["usable"]) == ("tested", "true")
    kept = next(r for r in after["variants.csv"] if r["run_id"] == str(retested.pk))
    assert kept["slot_tests_all_pass"] == feat.PRESENT
    assert "error" not in {r["value"] for r in after["features.csv"] if r["column"] == "py-sql-fstring@span"}


# ---------------------------------------------------------------------------
# 3. Planned confirmatory comparisons: raw p plus the plan's correction over the planned family
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        ({}, ("holm", 0.05, 0.125, False)),
        ({"correction": "none", "alpha": 0.1}, ("none", 0.1, 0.0625, True)),
        ({"correction": "bh", "alpha": 0.2}, ("bh", 0.2, 0.125, True)),
    ],
)
def test_planned_comparisons_carry_plan_corrected_p(detectors, fake_semgrep_runner, plan, expected):
    synthetic = make_study(kind="confirmatory")
    locked = [detectors["py-sql-fstring"].pinned_ref]
    _lock_with(synthetic.study, locked, plan)
    pattern_run = create_pattern_run(synthetic.study, detectors=locked)
    assert pattern_run.label == "planned"
    run_patterns(pattern_run)
    pattern_run.refresh_from_db()
    comps = {c["column"]: c for c in pattern_run.summary["comparisons"]}
    span, file_ = comps["py-sql-fstring@span"], comps["py-sql-fstring@file"]
    # The planned family: one condition pair x one primary feature x two scopes.
    assert span["planned"]
    assert file_["planned"]
    assert (span["family_size"], file_["family_size"]) == (2, 2)
    assert span["p"] == 0.0625  # raw exact McNemar p
    assert file_["p"] == 0.5  # file scope: 2 discordant pairs on the notes target
    correction, alpha, adjusted, significant = expected
    assert (span["correction"], span["alpha"], span["p_adjusted"], span["significant"]) == (
        correction,
        alpha,
        adjusted,
        significant,
    )
    assert file_["p_adjusted"] == 0.5
    assert pattern_run.summary["multiplicity"] == {"correction": correction, "alpha": alpha}
    exploratory = comps["equal_to_original"]
    assert (exploratory["p"], exploratory["p_adjusted"], exploratory["correction"]) == (None, None, "bh")
    with zipfile.ZipFile(io.BytesIO(export.build_export(pattern_run))) as zf:
        rows = list(csv.DictReader(io.StringIO(zf.read("comparisons.csv").decode())))
    exported = next(r for r in rows if r["column"] == "py-sql-fstring@span")
    assert (exported["p"], exported["p_adjusted"], exported["correction"]) == ("0.0625", str(adjusted), correction)
