"""Live scoring and ci_width_reached, planned/exploratory labelling, manual labels and detector audits."""

from __future__ import annotations

import pytest
from django.utils import timezone

from backend.generation import identity
from backend.generation.contracts.slot import ClusterParams
from backend.generation.contracts.slot import PatternScope
from backend.generation.models import PatternHit
from backend.generation.models import StudyCell
from backend.generation.patterns import audit as audit_service
from backend.generation.patterns import create_pattern_run
from backend.generation.patterns import detectors as det
from backend.generation.patterns import live
from backend.generation.patterns import manual
from backend.generation.patterns import normalize as norm
from backend.generation.patterns import run as run_patterns
from backend.generation.patterns import stats
from backend.generation.testing.fake_patterns import FakeSlotExecutor
from backend.generation.testing.fake_patterns import TargetSpec
from backend.generation.testing.fake_patterns import build_slot_study
from backend.generation.tests.wp13_patterns.test_pipeline import INSECURE
from backend.generation.tests.wp13_patterns.test_pipeline import ROUTES
from backend.generation.tests.wp13_patterns.test_pipeline import SECURE
from backend.generation.tests.wp13_patterns.test_pipeline import make_study
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_live_hits_drive_a_ci_width_reached_stop(detectors, fake_semgrep_runner):
    primary = detectors["py-sql-fstring"].pinned_ref
    synthetic = build_slot_study(
        [TargetSpec(path="app/routes.py", file_text=ROUTES, span=(4, 6))],
        repeats=10,
        status="running",
        primary_features=[primary],
    )
    ex = FakeSlotExecutor(synthetic)
    rule = {"code": "ci_width_reached", "threshold": 0.15, "n_min": 8, "features": [primary], "action": "stop"}

    for r in range(3):
        live.score_variant(ex.produce(0, 0, r, INSECURE))
        live.score_variant(ex.produce(1, 0, r, SECURE))
    assert live.ci_width_reached(synthetic.study, rule) == []  # n = 3 < n_min

    for r in range(3, 10):
        live.score_variant(ex.produce(0, 0, r, INSECURE))
        live.score_variant(ex.produce(1, 0, r, SECURE))
    reached = live.ci_width_reached(synthetic.study, rule)
    c0, c1 = (c.pk for c in synthetic.conditions)
    assert sorted(x["condition"] for x in reached) == [c0, c1]
    first = next(x for x in reached if x["condition"] == c0)
    assert first["features"]["py-sql-fstring"]["k"] == 10
    assert first["features"]["py-sql-fstring"]["half_width"] < 0.15
    assert first["looks"] == 2

    live_run = live.live_run(synthetic.study)
    assert (live_run.is_live, live_run.label, live_run.status) == (True, "exploratory", "running")
    assert (
        PatternHit.objects.filter(pattern_run=live_run, provisional=True).count() == 20
    )  # one per (variant, detector): the span scope
    assert live.ci_width_reached(synthetic.study, rule) == []  # already stopped: no further looks

    # The stop marks the cells adaptive_n (WP-11); the final run uses Wilson at alpha / K looks.
    StudyCell.objects.filter(study=synthetic.study).update(adaptive_n=True)
    final = create_pattern_run(synthetic.study, detectors=[primary])
    run_patterns(final)
    final.refresh_from_db()
    row = next(
        p
        for p in final.summary["prevalence"]
        if p["condition"] == c0 and p["column"] == "py-sql-fstring@span" and not p.get("conditional_on")
    )
    assert (row["method"], row["looks"], row["adaptive_n"]) == ("wilson_repeated_looks", 2, True)
    assert row["ci_low"] < 0.7225  # wider than the plain Wilson 10/10 interval


def test_ci_width_reached_is_refused_in_confirmatory_studies(detectors):
    synthetic = build_slot_study([TargetSpec(path="a.py", file_text="x = 1\n")], kind="confirmatory", repeats=1)
    with pytest.raises(ValueError, match="confirmatory"):
        live.ci_width_reached(synthetic.study, {"threshold": 0.1})


def _lock(study, detectors_refs, **overrides):
    specs = det.resolve_refs(detectors_refs)
    patterns = {
        "detector_set_hash": det.detector_set_hash(specs),
        "normalizer_version": norm.NORMALIZER_VERSION,
        "cluster_params": ClusterParams().model_dump(mode="json"),
        "scope": PatternScope().model_dump(mode="json"),
        "primary_features": detectors_refs[:1],
        **overrides,
    }
    study.analysis_plan = {"itt": True, "patterns": patterns}
    study.analysis_plan_hash = identity.H(study.analysis_plan)
    study.prereg_hash = identity.H({"fake": "prereg"})
    study.locked_at = timezone.now()
    study.save()


def test_planned_only_when_the_locked_pattern_plan_matches(detectors, fake_semgrep_runner):
    synthetic = make_study(kind="confirmatory")
    locked_refs = [detectors["py-sql-fstring"].pinned_ref]
    _lock(synthetic.study, locked_refs)
    planned = create_pattern_run(synthetic.study, detectors=locked_refs)
    other_set = create_pattern_run(synthetic.study, detectors=[*locked_refs, detectors["probe-sqli"].pinned_ref])
    other_params = create_pattern_run(synthetic.study, detectors=locked_refs, cluster_params={"method": "exact"})
    assert planned.label == "planned"
    assert other_set.label == "exploratory"
    assert other_params.label == "exploratory"

    run_patterns(planned)
    planned.refresh_from_db()
    comps = {c["column"]: c for c in planned.summary["comparisons"]}
    span = comps["py-sql-fstring@span"]
    assert span["planned"] is True
    assert span["p"] == 0.0625  # exact McNemar with 5 discordant pairs, reported for the planned comparison
    assert span["q"] is None
    assert comps["equal_to_original"]["planned"] is False
    assert comps["equal_to_original"]["p"] is None


def test_a_plan_that_fixes_the_set_by_refs_is_honoured(detectors):
    synthetic = make_study(kind="confirmatory")
    locked_refs = [detectors["py-sql-fstring"].pinned_ref]
    _lock(synthetic.study, locked_refs, detector_set_hash="", detectors=locked_refs)
    assert create_pattern_run(synthetic.study, detectors=locked_refs).label == "planned"


def test_unlocked_confirmatory_study_is_exploratory(detectors):
    synthetic = make_study(kind="confirmatory")
    run = create_pattern_run(synthetic.study, detectors=[detectors["py-sql-fstring"].pinned_ref])
    assert run.label == "exploratory"


def test_manual_labels_apply_to_every_member_of_the_cluster(detectors, fake_semgrep_runner):
    synthetic = make_study()
    target = synthetic.targets[0]
    key = norm.normalized_sha(INSECURE, "python").sha
    detector = detectors["manual-owner-check"]
    for value, user in (
        ("absent", UserFactory.create()),
        ("absent", UserFactory.create()),
        ("present", synthetic.user),
    ):
        manual.label_cluster(user=user, slot_target=target, normalized_sha=key, detector=detector, value=value)
    # Re-labelling replaces the labeler's own code (one label per labeler).
    manual.label_cluster(
        user=synthetic.user,
        slot_target=target,
        normalized_sha=key,
        detector=detector,
        value="absent",
    )
    pattern_run = create_pattern_run(synthetic.study, detectors="default")
    assert detector.pinned_ref in pattern_run.detectors  # labelled manual detectors join "default"
    run_patterns(pattern_run)
    hits = PatternHit.objects.filter(pattern_run=pattern_run, detector=detector)
    c0 = synthetic.conditions[0]
    absent = hits.filter(status="absent").values_list("run_id", flat=True)
    expected = {synthetic.runs[(0, 0, r)].pk for r in range(3)}  # INSECURE, its renamed copy, INSECURE
    assert set(absent) == expected
    assert all(synthetic.runs[(0, 0, r)].condition_id == c0.pk for r in range(3))
    assert hits.exclude(run_id__in=expected).filter(status__in=["present", "absent"]).count() == 0


def test_audit_sampling_is_stratified_and_seeded_and_scores_codes(detectors, fake_semgrep_runner):
    from backend.human_eval.models import Assignment
    from backend.human_eval.services import detector_audit
    from backend.human_eval.tests.factories import AssignmentFactory
    from backend.human_eval.tests.factories import EvalItemFactory
    from backend.human_eval.tests.factories import ParticipantFactory
    from backend.human_eval.tests.factories import ResponseFactory

    synthetic = make_study()
    pattern_run = create_pattern_run(synthetic.study, detectors=[detectors["py-sql-fstring"].pinned_ref])
    run_patterns(pattern_run)
    pattern_run.refresh_from_db()
    first = audit_service.draw_sample(pattern_run, "py-sql-fstring", scope="span", n_hits=2, n_non_hits=3)
    again = audit_service.draw_sample(pattern_run, "py-sql-fstring", scope="span", n_hits=2, n_non_hits=3)
    assert first == again
    # Hits live in two strata (c0 x t0: 3 runs, c0 x t1: 2 runs): proportional allocation takes one of each.
    strata = {(r.condition_id, str(r.slot_target_id)) for r in synthetic.runs.values() if str(r.pk) in first["hits"]}
    assert len(first["hits"]) == 2
    assert len(strata) == 2
    assert audit_service.allocate({"a": 3, "b": 2}, 2) == {"a": 1, "b": 1}
    assert audit_service.allocate({"a": 9, "b": 1}, 4) == {"a": 4, "b": 0}

    audit = audit_service.create_audit(pattern_run, "py-sql-fstring", n_hits=2, n_non_hits=3)
    assert audit.sample == first
    task = audit.eval_task
    assert (task.status, task.mode, task.study_id) == ("draft", "single", synthetic.study.pk)
    assert task.apps.count() == 5
    assert audit_service.refresh_audit(audit).status == "pending"

    # Two hits coded present/absent, the non-hits all absent.
    codes = dict.fromkeys(first["non_hits"], "absent")
    codes[first["hits"][0]] = "present"
    codes[first["hits"][1]] = "absent"
    participant = ParticipantFactory.create(task=task)
    for app in task.apps.all():
        item = EvalItemFactory.create(task=task, app_a=app)
        assignment = AssignmentFactory.create(
            participant=participant,
            item=item,
            status=Assignment.Status.SUBMITTED,
        )
        ResponseFactory.create(
            assignment=assignment,
            block_id=detector_audit.BLOCK_ID,
            answers={"answers": {detector_audit.QUESTION_ID: codes[str(app.job_id)]}},
        )
    assert detector_audit.collect_codes(task) == codes
    audit = audit_service.refresh_audit(audit)
    assert audit.status == "complete"
    assert (audit.precision, audit.npv) == (0.5, 1.0)
    assert audit.summary["precision"]["ci_low"] == round(stats.wilson(1, 2)[0], 6)
    assert audit_service.audits_by_feature(pattern_run)["py-sql-fstring"]["status"] == "complete"


def test_audit_needs_a_completed_run(detectors):
    synthetic = make_study()
    pattern_run = create_pattern_run(synthetic.study, detectors=[detectors["py-sql-fstring"].pinned_ref])
    with pytest.raises(audit_service.AuditError):
        audit_service.create_audit(pattern_run, "py-sql-fstring")
