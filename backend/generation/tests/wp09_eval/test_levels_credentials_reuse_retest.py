"""WP-09: evaluation levels (4.8), the runtime credentials record (8.6), eval reuse and test-retest (8.5)."""

from __future__ import annotations

import pytest

from backend.generation import evaluation
from backend.generation.contracts.plan import EvaluationSpec
from backend.generation.evaluation import levels
from backend.generation.evaluation import retest
from backend.generation.evaluation import reuse
from backend.generation.models import choices
from backend.generation.testing.content import eval_result_content
from backend.generation.testing.factories import EvalSuiteFactory
from backend.generation.testing.factories import GenerationRunFactory
from backend.generation.testing.factories import RunEvaluationFactory
from backend.generation.testing.factories import StudyFactory

pytestmark = pytest.mark.django_db


def _spec(level: str, **overrides: object) -> EvaluationSpec:
    return EvaluationSpec.model_validate({"eval_suite": "eval-v2@1", "eval_level": level, "overrides": overrides})


# --- levels --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "on"),
    [
        ("none", set()),
        ("static", {"lock", "adherence_static", "analysis_static"}),
        ("build", {"lock", "build", "adherence_static", "adherence"}),
        ("functional", {"lock", "build", "functional", "adherence_static", "adherence", "baseline"}),
        (
            "security",
            {"lock", "build", "functional", "probes", "adherence_static", "adherence", "baseline", "analysis_static"},
        ),
        ("full", set(levels.STAGES)),
    ],
)
def test_level_table_matches_the_spec(level: str, on: set[str]) -> None:
    stages = levels.stages_for(_spec(level))
    assert {name for name, value in stages.items() if value} == on
    assert set(levels.not_measured_stages(_spec(level))) == set(levels.STAGES) - on


def test_overrides_switch_stages_and_dependencies_follow() -> None:
    stages = levels.stages_for(_spec("security", probes=False, analysis_static="none"))
    assert stages["probes"] is False
    assert stages["analysis_static"] is False
    # turning the build off removes everything that needs a running app
    stages = levels.stages_for(_spec("full", build=False))
    assert not any(stages[name] for name in ("build", "functional", "probes", "baseline", "analysis_dynamic"))
    assert stages["adherence"] is False
    assert stages["adherence_static"] is True


def test_skipped_levels_are_reported_not_measured() -> None:
    assert "probes" in levels.not_measured_stages(_spec("functional"))
    assert "analysis_dynamic" in levels.not_measured_stages(_spec("security"))


# --- credentials ---------------------------------------------------------------------------------


def test_credentials_round_trip_through_the_start_stage_record() -> None:
    record = evaluation.new_credentials()
    ref = evaluation.store_credentials(record)
    run = GenerationRunFactory.create(tested=True)
    RunEvaluationFactory.create(
        run=run,
        measured=True,
        stages={"start": {"status": "ok", "detail": {"credentials_ref": ref}}},
    )
    assert evaluation.credentials(run) == record
    assert ref not in record["admin_password"]


def test_credentials_are_fresh_per_start_and_never_a_default() -> None:
    first, second = evaluation.new_credentials(), evaluation.new_credentials()
    assert first["admin_password"] != second["admin_password"]
    assert len(first["admin_password"]) >= 20


def test_missing_or_unreadable_record_raises() -> None:
    run = GenerationRunFactory.create(tested=True)
    with pytest.raises(evaluation.CredentialsUnavailableError):
        evaluation.credentials(run)
    from backend.generation import blobs

    bad = blobs.put_text("not-a-fernet-token")
    RunEvaluationFactory.create(
        run=run,
        measured=True,
        stages={"start": {"status": "ok", "detail": {"credentials_ref": bad}}},
    )
    with pytest.raises(evaluation.CredentialsUnavailableError):
        evaluation.credentials(run)


# --- reuse ---------------------------------------------------------------------------------------


def test_reuse_needs_same_tree_suite_lock_and_selection() -> None:
    suite = EvalSuiteFactory.create()
    source = GenerationRunFactory.create(tested=True)
    stored = RunEvaluationFactory.create(
        run=source,
        eval_suite=suite,
        measured=True,
        stages={"tester": {"status": "ok", "detail": {"only_checks": None}}},
    )
    twin = GenerationRunFactory.create(tested=True, tree_hash=source.tree_hash)
    assert reuse.find_reusable(twin, suite) == stored
    assert reuse.find_reusable(twin, suite, only_checks=["check.0"]) is None
    assert reuse.find_reusable(twin, EvalSuiteFactory.create()) is None
    other = GenerationRunFactory.create(tested=True)
    assert reuse.find_reusable(other, suite) is None


def test_unmeasured_evaluations_are_never_reused() -> None:
    suite = EvalSuiteFactory.create()
    source = GenerationRunFactory.create(tested=True)
    RunEvaluationFactory.create(
        run=source,
        eval_suite=suite,
        status=choices.EvaluationStatus.NOT_MEASURED,
        result=eval_result_content(measured=False),
    )
    twin = GenerationRunFactory.create(tested=True, tree_hash=source.tree_hash)
    assert reuse.find_reusable(twin, suite) is None


# --- retest --------------------------------------------------------------------------------------


def test_kappa_vectors() -> None:
    assert retest.cohen_kappa([]) is None
    assert retest.cohen_kappa([(True, True), (False, False)]) == 1.0
    assert retest.cohen_kappa([(True, True), (True, True)]) is None  # chance agreement 1
    assert retest.cohen_kappa([(True, False), (False, True)]) == -1.0


def test_retest_agreement_and_no_data_is_none_not_zero() -> None:
    study = StudyFactory.create()
    assert retest.retest_agreement(study) == {"n": 0, "check_agreement": None, "run_agreement": None, "kappa": None}
    run = GenerationRunFactory.create(tested=True, study=study)
    first = RunEvaluationFactory.create(run=run, measured=True, result=eval_result_content(passed=3, failed=1))
    RunEvaluationFactory.create(
        run=run,
        measured=True,
        result=eval_result_content(passed=2, failed=2),
        retest_of=first,
    )
    agreement = retest.retest_agreement(study)
    assert agreement["n"] == 1
    assert agreement["run_agreement"] == 0.0
    assert agreement["check_agreement"] == pytest.approx(4 / 5)


def test_select_for_retest_is_deterministic() -> None:
    study = StudyFactory.create()
    GenerationRunFactory.create_batch(10, tested=True, study=study)
    picked = retest.select_for_retest(study, 0.2)
    assert len(picked) == 2
    assert picked == retest.select_for_retest(study, 0.2)


# --- slot rules in levels (4.8) ------------------------------------------------------------------


def _slot_spec(level: str, mode: str = "full", **overrides: object) -> EvaluationSpec:
    return EvaluationSpec.model_validate(
        {"eval_suite": "eval-v2@1", "eval_level": level, "overrides": overrides, "slot": {"mode": mode}},
    )


def test_slot_linked_probes_run_at_functional_and_above() -> None:
    assert levels.stages_for(_spec("functional"))["probes"] is False
    stages = levels.stages_for(_slot_spec("functional"))
    assert stages["probes"] is True
    assert levels.linked_probes_only(_slot_spec("functional")) is True
    assert "probes" not in levels.not_measured_stages(_slot_spec("functional"))
    # the slot rule beats a probes=False override ("always run")
    assert levels.stages_for(_slot_spec("security", probes=False))["probes"] is True
    # below functional there is no running app, so no probes
    assert levels.stages_for(_slot_spec("build"))["probes"] is False
    assert levels.linked_probes_only(_spec("security")) is False


def test_slot_static_only_switches_off_build_and_everything_after_it() -> None:
    stages = levels.stages_for(_slot_spec("full", mode="static_only"))
    for name in ("build", "functional", "probes", "adherence", "baseline", "analysis_dynamic"):
        assert stages[name] is False, name
    assert stages["lock"] is True
    assert stages["adherence_static"] is True
    assert stages["analysis_static"] is True
    assert {"build", "functional", "probes"} <= set(levels.not_measured_stages(_slot_spec("full", mode="static_only")))


# --- reuse: level, mode, probe selection, overlay, lock ------------------------------------------


def _source_eval(suite: object, *, level: str = "security", mode: str = "full", **detail: object) -> object:
    source = GenerationRunFactory.create(tested=True)
    tester_detail = {"only_checks": None, "probes": None, **detail}
    return RunEvaluationFactory.create(
        run=source,
        eval_suite=suite,
        eval_level=level,
        mode=mode,
        measured=True,
        stages={"tester": {"status": "ok", "detail": tester_detail}},
    )


def _twin(stored: object, **kwargs: object) -> object:
    return GenerationRunFactory.create(tested=True, tree_hash=stored.run.tree_hash, **kwargs)


def test_reuse_requires_a_level_that_covers_the_request() -> None:
    suite = EvalSuiteFactory.create()
    stored = _source_eval(suite, level="build")
    twin = _twin(stored)
    assert reuse.find_reusable(twin, suite, eval_level="security") is None
    assert reuse.find_reusable(twin, suite, eval_level="full") is None
    assert reuse.find_reusable(twin, suite, eval_level="build") == stored
    # a full row covers a security request; a security row does not cover full (no DAST)
    full = _source_eval(suite, level="full")
    assert reuse.find_reusable(_twin(full), suite, eval_level="security") == full
    security = _source_eval(suite, level="security")
    assert reuse.find_reusable(_twin(security), suite, eval_level="full") is None
    # default request level is security when the run has no manifest
    assert reuse.find_reusable(_twin(stored), suite) is None


def test_reuse_requires_the_same_mode_and_never_serves_overlay() -> None:
    suite = EvalSuiteFactory.create()
    stored = _source_eval(suite, mode="static_only", level="static")
    twin = _twin(stored)
    assert reuse.find_reusable(twin, suite, eval_level="static") is None  # mode full requested
    assert reuse.find_reusable(twin, suite, eval_level="static", mode="static_only") == stored
    overlay = _source_eval(suite, mode="overlay")
    assert reuse.find_reusable(_twin(overlay), suite, mode="overlay") is None


def test_reuse_excludes_image_override_and_overlay_file_rows() -> None:
    suite = EvalSuiteFactory.create()
    with_image = _source_eval(suite, image_override=True)
    assert reuse.find_reusable(_twin(with_image), suite) is None
    with_files = _source_eval(suite, overlay=True)
    assert reuse.find_reusable(_twin(with_files), suite) is None


def test_reuse_requires_the_same_probe_selection() -> None:
    suite = EvalSuiteFactory.create()
    stored = _source_eval(suite, probes=["probe.b", "probe.a"])
    twin = _twin(stored)
    assert reuse.find_reusable(twin, suite) is None
    assert reuse.find_reusable(twin, suite, probes=["probe.a"]) is None
    assert reuse.find_reusable(twin, suite, probes=["probe.a", "probe.b"]) == stored
    assert reuse.selection_detail(only_checks=["c2", "c1"], probes=None) == {
        "only_checks": ["c1", "c2"],
        "probes": None,
        "image_override": False,
        "overlay": False,
    }


def test_twin_with_blank_lock_reuses_and_adopts_the_source_lock() -> None:
    suite = EvalSuiteFactory.create()
    stored = _source_eval(suite)
    lock = "a" * 64
    GenerationRunFactory._meta.model.objects.filter(pk=stored.run_id).update(
        resolved_lock_blob=lock,
        lock_source="platform",
    )
    twin = _twin(stored)
    assert twin.resolved_lock_blob == ""
    assert reuse.find_reusable(twin, suite) == stored
    twin.refresh_from_db()
    assert twin.resolved_lock_blob == lock  # adopted: the reuse never re-resolves
    assert twin.lock_source == "platform"


def test_reuse_refuses_a_different_lock_when_both_are_set() -> None:
    suite = EvalSuiteFactory.create()
    stored = _source_eval(suite)
    model = GenerationRunFactory._meta.model
    model.objects.filter(pk=stored.run_id).update(resolved_lock_blob="a" * 64)
    twin = _twin(stored)
    model.objects.filter(pk=twin.pk).update(resolved_lock_blob="b" * 64)
    twin.refresh_from_db()
    assert reuse.find_reusable(twin, suite) is None
    model.objects.filter(pk=twin.pk).update(resolved_lock_blob="a" * 64)
    twin.refresh_from_db()
    assert reuse.find_reusable(twin, suite) == stored
