"""End to end on a synthetic slot study (WP-13 "Done when").

A FakeSlotExecutor produces known variants for 2 conditions x 2 targets x 4 repeats; the PatternRun's
prevalence, clusters, comparisons, pooled estimates and export must match hand-computed values exactly.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile

import pytest

from backend.generation.models import PatternHit
from backend.generation.models import StudyPostRun
from backend.generation.patterns import create_pattern_run
from backend.generation.patterns import export
from backend.generation.patterns import run as run_patterns
from backend.generation.testing.fake_patterns import FakeSlotExecutor
from backend.generation.testing.fake_patterns import TargetSpec
from backend.generation.testing.fake_patterns import build_slot_study

pytestmark = pytest.mark.django_db

ROUTES = """from flask import request


def list_todos(db, user_id):
    rows = db.execute("SELECT * FROM todos WHERE owner = ?", (user_id,))
    return [dict(r) for r in rows]


def helper(db):
    return db.execute(f"SELECT 1 FROM t WHERE x = {request.args['x']}")
"""
SECURE = ROUTES.splitlines(keepends=True)[3:6]
SECURE = "".join(SECURE)
SECURE2 = """def list_todos(db, user_id):
    cur = db.execute("SELECT * FROM todos WHERE owner = ?", (user_id,))
    return [dict(row) for row in cur.fetchall()]
"""
INSECURE = """def list_todos(db, user_id):
    rows = db.execute(f"SELECT * FROM todos WHERE owner = {user_id}")
    return [dict(r) for r in rows]
"""
INSECURE_RENAMED = """def list_todos(db, uid):
    items = db.execute(f"SELECT * FROM todos WHERE owner = {uid}")
    return [dict(x) for x in items]
"""
NOTES_SECURE = """def get_note(db, note_id):
    return db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
"""
NOTES_INSECURE = """def get_note(db, note_id):
    return db.execute(f"SELECT * FROM notes WHERE id = {note_id}").fetchone()
"""
PROBE_HIT = [{"id": "p-sqli", "cwe": "cwe-89", "kind": "sqli_boolean", "status": "exploited"}]
PROBE_MISS = [{"id": "p-sqli", "cwe": "cwe-89", "kind": "sqli_boolean", "status": "not_exploited"}]


def make_study(*, seed_forwarded_b=True, kind="exploratory", status="completed"):
    synthetic = build_slot_study(
        [
            TargetSpec(path="app/routes.py", file_text=ROUTES, span=(4, 6), linked_probes=("p-sqli",)),
            TargetSpec(path="app/notes.py", file_text=NOTES_SECURE),
        ],
        n_conditions=2,
        repeats=4,
        kind=kind,
        status=status,
    )
    ex = FakeSlotExecutor(synthetic)
    fail, ok = {"c1": "fail"}, {"c1": "pass"}
    for r, part in enumerate([INSECURE, INSECURE_RENAMED, INSECURE, None]):
        ex.produce(0, 0, r, part, checks=fail, probes=PROBE_HIT)
    for r, part in enumerate([SECURE, SECURE2, SECURE, SECURE]):
        ex.produce(1, 0, r, part, checks=ok, probes=PROBE_MISS, seed_forwarded=seed_forwarded_b)
    for r, part in enumerate([NOTES_INSECURE, NOTES_INSECURE, NOTES_SECURE, NOTES_SECURE]):
        ex.produce(0, 1, r, part, checks=ok)
    for r in range(4):
        ex.produce(1, 1, r, NOTES_SECURE, checks=ok, seed_forwarded=seed_forwarded_b)
    return synthetic


def refs(detectors, *slugs):
    return [detectors[s].pinned_ref for s in slugs]


@pytest.fixture
def completed(detectors, fake_semgrep_runner):
    synthetic = make_study()
    pattern_run = create_pattern_run(
        synthetic.study,
        detectors=refs(detectors, "py-sql-fstring", "py-parameterized-sql", "probe-sqli", "sg-sql-fstring"),
        cluster_params={"method": "exact"},
        user=synthetic.user,
    )
    run_patterns(pattern_run)
    pattern_run.refresh_from_db()
    return synthetic, pattern_run


def row(summary, condition, target, column, **extra):
    matches = [
        r
        for r in summary["prevalence"]
        if r["condition"] == condition
        and r["target"] == target
        and r["column"] == column
        and r.get("conditional_on") == extra.get("conditional_on")
    ]
    assert len(matches) == 1, (condition, target, column)
    return matches[0]


def test_create_queues_a_post_run_and_pins_the_detector_set(detectors):
    synthetic = make_study()
    pattern_run = create_pattern_run(synthetic.study, detectors="default", user=synthetic.user)
    assert pattern_run.status == "pending"
    assert pattern_run.label == "exploratory"
    assert all("#" in ref for ref in pattern_run.detectors)
    # default: python detectors + the probe detector (a target links probes); manual only with labels.
    slugs = sorted(ref.split("@")[0] for ref in pattern_run.detectors)
    assert slugs == ["probe-sqli", "py-parameterized-sql", "py-sql-fstring", "sg-sql-fstring"]
    post = StudyPostRun.objects.get(pattern_run=pattern_run)
    assert (post.kind, post.status, post.config["detectors"]) == ("patterns", "pending", "default")


def test_prevalence_itt_measured_bounds_and_correctness(completed):
    synthetic, pattern_run = completed
    s = pattern_run.summary
    c0, c1 = (c.pk for c in synthetic.conditions)
    t0, t1 = (str(t.pk) for t in synthetic.targets)
    assert pattern_run.status == "completed"
    assert (s["n_planned"], s["n_with_output"], s["n_unparsed"]) == (16, 15, 0)

    r = row(s, c0, t0, "py-sql-fstring@span")
    assert (r["k"], r["n_measured"], r["n_itt"], r["missing"], r["errors"], r["na"]) == (3, 3, 4, 1, 0, 0)
    assert (r["p"], r["ci_low"], r["ci_high"], r["method"]) == (1.0, 0.438503, 1.0, "wilson")
    assert r["bounds"] == {"low": 0.75, "high": 1.0}
    assert (r["cp_low"], r["cp_high"]) == (0.292402, 1.0)
    # The output_invalid variant is 0 pass in ITT (spec 6.13), not missing, for correctness.
    assert r["correctness"] == {"k": 0, "n_measured": 4, "p": 0.0}
    assert r["low_effective_n"] is True

    r = row(s, c1, t0, "py-sql-fstring@span")
    assert (r["k"], r["n_measured"], r["n_itt"], r["p"]) == (0, 4, 4, 0.0)
    assert "bounds" not in r
    assert r["correctness"]["p"] == 1.0
    assert r["low_effective_n"] is False  # exact-duplicate share 2/4 is not > 0.5

    # The parent's helper has an f-string: present at file scope for every variant, whatever the slot does.
    assert row(s, c1, t0, "py-sql-fstring@file")["p"] == 1.0
    r = row(s, c0, t1, "py-sql-fstring@span")
    assert (r["k"], r["n_measured"], r["ci_low"], r["ci_high"]) == (2, 4, 0.150039, 0.849961)
    # The semgrep detector (through the rulepack) agrees with the regex and copies the rule's severity.
    assert row(s, c0, t1, "sg-sql-fstring@span")["k"] == 2
    assert set(
        PatternHit.objects.filter(pattern_run=pattern_run, feature_key="sg-sql-fstring", status="present").values_list(
            "tool_severity",
            flat=True,
        ),
    ) == {"ERROR"}

    # Probes: exploited -> present, not_exploited -> absent; the unlinked target has no matching probe (n/a).
    r = row(s, c0, t0, "probe-sqli@span")
    assert (r["k"], r["n_measured"]) == (3, 3)
    assert row(s, c1, t0, "probe-sqli@span")["k"] == 0
    assert row(s, c0, t1, "probe-sqli@span")["na"] == 4

    # Conditional on slot tests passing (BaxBench sec_pass): none of c0's t0 variants pass.
    r = row(s, c0, t0, "py-sql-fstring@span", conditional_on="slot_tests_all_pass")
    assert (r["k"], r["n_measured"], r["p"]) == (0, 0, None)
    assert row(s, c1, t0, "equal_to_original")["k"] == 3
    pooled_row = row(s, c0, "all", "py-sql-fstring@span")
    assert (pooled_row["k"], pooled_row["n_measured"], pooled_row["n_itt"], pooled_row["pooled"]) == (5, 7, 8, True)


def test_variant_results_get_the_pattern_pass_fields(completed):
    from backend.generation.models import SlotVariantResult
    from backend.generation.patterns import normalize as norm

    synthetic, _pattern_run = completed
    insecure = SlotVariantResult.objects.get(run=synthetic.runs[(0, 0, 1)])
    assert insecure.normalized_sha == norm.normalized_sha(INSECURE, "python").sha
    assert insecure.normalizer_version == norm.NORMALIZER_VERSION
    assert insecure.diff_vs_original["normalized_equal"] is False
    same = SlotVariantResult.objects.get(run=synthetic.runs[(1, 0, 0)])
    assert same.diff_vs_original["normalized_equal"] is True
    assert same.diff_vs_original["ast_edit_distance"] == 0
    invalid = SlotVariantResult.objects.get(run=synthetic.runs[(0, 0, 3)])
    assert invalid.normalized_sha == ""


def test_adaptive_rows_without_recorded_looks_use_n_minus_n_min_plus_one(detectors, fake_semgrep_runner):
    from backend.generation.models import StudyCell

    synthetic = make_study()
    StudyCell.objects.filter(study=synthetic.study, condition=synthetic.conditions[1]).update(adaptive_n=True)
    pattern_run = create_pattern_run(synthetic.study, detectors=refs(detectors, "py-sql-fstring"))
    run_patterns(pattern_run)
    pattern_run.refresh_from_db()
    c1 = synthetic.conditions[1].pk
    t0 = str(synthetic.targets[0].pk)
    r = row(pattern_run.summary, c1, t0, "py-sql-fstring@span")
    assert (r["method"], r["looks"]) == ("wilson_repeated_looks", 1)  # n_at_stop 4 < default n_min 10 -> K = 1


def test_attrition_issue_and_cluster_statistics(completed):
    synthetic, pattern_run = completed
    s = pattern_run.summary
    t0 = str(synthetic.targets[0].pk)
    codes = {i["code"] for i in s["issues"]}
    assert {"differential_attrition", "low_effective_n", "few_clusters"} <= codes
    stats = {(x["condition"], x["target"]): x for x in s["cluster_stats"]}
    c0, c1 = (c.pk for c in synthetic.conditions)
    assert (stats[(c0, t0)]["n"], stats[(c0, t0)]["distinct"], stats[(c0, t0)]["exact_dup_share"]) == (3, 1, 0.666667)
    assert (stats[(c1, t0)]["distinct"], stats[(c1, t0)]["top1_share"]) == (2, 0.75)
    clusters = list(pattern_run.clusters.filter(slot_target=synthetic.targets[0]).order_by("-size", "pk"))
    assert [(c.size, c.includes_original) for c in clusters] == [(3, False), (3, True), (1, False)]


def test_paired_comparison_and_pooled_estimate(completed):
    synthetic, pattern_run = completed
    s = pattern_run.summary
    c0, c1 = (c.pk for c in synthetic.conditions)
    comp = next(c for c in s["comparisons"] if c["column"] == "py-sql-fstring@span")
    assert (comp["a"], comp["b"], comp["method"], comp["test"]) == (c0, c1, "paired", "mcnemar_exact")
    assert comp["table"] == {"a": 0, "b": 5, "c": 0, "d": 2}
    assert comp["diff"] == 0.714286
    assert comp["p"] is None  # exploratory: BH q-values with the family size, never a raw p
    assert comp["q"] is not None
    assert comp["family_size"] >= 1
    assert comp["planned"] is False
    pooled = next(p for p in s["pooled"] if p["column"] == "py-sql-fstring@span")
    assert pooled["method"] == "mh_stratified"
    assert pooled["pooled"]["rd"] == 0.730769
    assert pooled["permutation"] == {"method": "exact", "arrangements": 4}
    assert "few_clusters" in pooled["notes"]
    assert [t["rd"] for t in pooled["per_target"]] == [1.0, 0.5] or [t["rd"] for t in pooled["per_target"]] == [
        0.5,
        1.0,
    ]


def test_independent_methods_without_seed_forwarding(detectors, fake_semgrep_runner):
    synthetic = make_study(seed_forwarded_b=False)
    pattern_run = create_pattern_run(synthetic.study, detectors=refs(detectors, "py-sql-fstring"), user=synthetic.user)
    run_patterns(pattern_run)
    pattern_run.refresh_from_db()
    comp = next(c for c in pattern_run.summary["comparisons"] if c["column"] == "py-sql-fstring@span")
    assert (comp["method"], comp["test"]) == ("independent", "fisher_exact")
    assert (comp["k_a"], comp["n_a"], comp["k_b"], comp["n_b"]) == (5, 7, 0, 8)
    assert comp["diff"] == 0.714286


def test_interim_withholds_q_and_p(detectors, fake_semgrep_runner):
    synthetic = make_study(status="running")
    pattern_run = create_pattern_run(synthetic.study, detectors=refs(detectors, "py-sql-fstring"), user=synthetic.user)
    run_patterns(pattern_run)
    pattern_run.refresh_from_db()
    assert pattern_run.summary["interim"] is True
    assert all(c["p"] is None and c["q"] is None for c in pattern_run.summary["comparisons"])


def test_completed_runs_are_immutable_and_rerun_creates_a_new_run(completed, detectors):
    from backend.generation.patterns.service import PatternRunError

    synthetic, pattern_run = completed
    with pytest.raises(PatternRunError):
        run_patterns(pattern_run)
    again = create_pattern_run(synthetic.study, detectors=pattern_run.detectors, cluster_params={"method": "exact"})
    assert again.pk != pattern_run.pk
    assert again.detector_set_hash == pattern_run.detector_set_hash


VARIANT_HEADER = [
    "run_id", "study_id", "cell_id", "condition_id", "condition_index", "condition_label", "condition_levels",
    "target_id", "target_label", "match_key", "slot_set_id", "parent_run", "parent_model", "parent_harness",
    "model", "same_author", "repeat_index", "outcome", "usable", "has_output", "parse_status", "eval_status",
    "normalized_sha", "normalize_reason", "exact_cluster", "cluster_id", "cluster_size", "leak_score",
    "equal_to_original", "slot_tests_all_pass", "exploited_any", "uses_new_dependency", "out_of_slot_edit",
    "adaptive_n", "started_at",
]  # fmt: skip
FEATURE_HEADER = [
    "run_id", "condition_id", "condition_label", "target_id", "match_key", "parent_run", "parent_model",
    "same_author", "repeat_index", "feature", "scope", "column", "kind", "detector", "value", "y",
    "tool_severity", "adaptive_n",
]  # fmt: skip


def test_export_column_contract_and_contents(completed):
    synthetic, pattern_run = completed
    data = export.build_export(pattern_run)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert {"variants.csv", "features.csv", "prevalence.csv", "comparisons.csv", "pooled.json", "audit.csv",
                "clusters.json", "manifest.json", "README.md"} <= names  # fmt: skip
        variants = list(csv.reader(io.StringIO(zf.read("variants.csv").decode())))
        features = list(csv.DictReader(io.StringIO(zf.read("features.csv").decode())))
        prevalence = list(csv.DictReader(io.StringIO(zf.read("prevalence.csv").decode())))
        manifest = json.loads(zf.read("manifest.json"))
        readme = zf.read("README.md").decode()
        t0 = str(synthetic.targets[0].pk)
        assert sum(1 for n in names if n.startswith(f"variants/{t0}/")) == 7
        assert f"original/{t0}.py" in names
    header = variants[0]
    assert header[: len(VARIANT_HEADER)] == VARIANT_HEADER
    assert [h for h in header[len(VARIANT_HEADER) :] if not h.startswith("f:")] == []
    assert "f:py-sql-fstring@span" in header
    assert len(variants) == 17  # every planned variant, attrition included
    missing = [r for r in variants[1:] if r[header.index("has_output")] == "false"]
    assert len(missing) == 1
    assert missing[0][header.index("f:py-sql-fstring@span")] == ""
    assert list(features[0].keys()) == FEATURE_HEADER
    # 15 variants with output x every column, plus the output_invalid variant's correctness (absent, ITT).
    assert len(features) == 15 * len(pattern_run.summary["features"]) + 1
    assert {r["y"] for r in features if r["value"] == "present"} == {"1"}
    assert prevalence[0]["n_itt"] != ""
    assert list(prevalence[0].keys()) == list(export.PREVALENCE_COLUMNS)
    assert manifest["detector_set_hash"] == pattern_run.detector_set_hash
    assert manifest["label"] == "exploratory"
    assert "(1 | target_id) + (1 | parent_run)" in readme


def test_a_launch_attached_post_run_without_a_pattern_run(detectors, fake_semgrep_runner):
    from backend.generation.patterns import run_post_run

    synthetic = make_study()
    post = StudyPostRun.objects.create(
        study=synthetic.study,
        kind="patterns",
        trigger="manual",
        config={"detectors": "default"},
    )
    pattern_run = run_post_run(post)
    post.refresh_from_db()
    assert post.pattern_run_id == pattern_run.pk
    assert pattern_run.status == "completed"
    assert StudyPostRun.objects.filter(study=synthetic.study).count() == 1  # no second post-run queued
