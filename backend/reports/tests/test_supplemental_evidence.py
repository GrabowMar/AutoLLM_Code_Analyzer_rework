"""Imported experiments must preserve their planned sample and measured outcomes."""

import copy
import hashlib
import json

import pytest

from backend.reports.services.supplemental_evidence import bind_evidence
from backend.reports.services.supplemental_evidence import validate_evidence

TREE = "a" * 64
CHECKS = (
    "reset.invalid_token",
    "reset.confirm",
    "reset.new_password",
    "reset.old_password_rejected",
    "reset.token_single_use",
    "reset.old_session_invalidated",
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def feature_evidence(run_id="run-a", tree=TREE):
    selected = {"run_id": run_id, "task": "account_password_reset", "tree_hash": tree}
    protocol = {
        "schema_version": 1,
        "selected_runs": [selected],
        "expected_checks": {selected["task"]: list(CHECKS)},
        "module_sha256": "b" * 64,
        "control_source_sha256": "c" * 64,
        "policy": {"mutants": []},
    }
    return {
        "feature_lifecycle": {
            "schema_version": 1,
            "complete": True,
            "status": "completed",
            "protocol": protocol,
            "protocol_sha256": digest(protocol),
            "controls": [
                {
                    "task": selected["task"],
                    "mutant": "positive",
                    "success_status": 204,
                    "status": "pass",
                    "actual_failed_checks": [],
                    "expected_failed_check": None,
                    "checks": [{"id": name, "status": "pass"} for name in CHECKS],
                },
            ],
            "observations": [
                {
                    **selected,
                    "status": "measured",
                    "teardown": "completed",
                    "checks": [{"id": name, "endpoint": "reset.confirm", "status": "pass"} for name in CHECKS],
                },
            ],
        },
    }


def lock_evidence(run_id="run-a", tree=TREE):
    lock_files = {"package-lock.json": {"materialized_sha256": "b" * 64, "collected_sha256": None}}
    protocol = {
        "schema": 1,
        "arms": ["collected", "materialized"],
        "suite": "suite@1#0123456789ab",
        "runs": [
            {
                "run_id": run_id,
                "original_tree_hash": tree,
                "collected_context_hash": "c" * 64,
                "materialized_context_hash": "d" * 64,
                "plan_sha256": "e" * 64,
                "lock_files": lock_files,
            },
        ],
    }
    protocol_json = json.dumps(protocol, indent=2)
    observations = [
        {
            "run_id": run_id,
            "original_source_tree_sha256": tree,
            "arm": arm,
            "outcome": "tested",
            "build_ok": True,
            "start_ok": True,
            "functional_measured": True,
            "functional_pass_rate": 0.5,
            "all_checks_passed": False,
            "known_build_success": True,
            "known_deployment_success": True,
            "known_end_to_end_all_checks_success": False,
            "attempt_count": 1,
            "completed_at": "2026-01-01T00:00:00Z",
            "context_hash": protocol["runs"][0][arm + "_context_hash"],
            "plan_sha256": "e" * 64,
            "lock_files": lock_files,
            "suite_ref": protocol["suite"],
            "environment_hash": "f" * 64,
        }
        for arm in ("collected", "materialized")
    ]
    return {
        "lock_materialization": {
            "schema_version": 1,
            "complete": True,
            "protocol": protocol,
            "protocol_json": protocol_json,
            "protocol_sha256": hashlib.sha256(protocol_json.encode()).hexdigest(),
            "planned_pairs": 1,
            "completed_pairs": 1,
            "run_ids": [run_id],
            "suite_ref": protocol["suite"],
            "observations": observations,
        },
    }


@pytest.mark.parametrize("factory", [feature_evidence, lock_evidence])
def test_complete_evidence_is_bound_without_mutating_the_input(factory):
    value = factory()
    original = copy.deepcopy(value)
    output = bind_evidence(value, [{"run_id": "run-a", "tree_hash": TREE}])
    name = next(iter(value))
    binding = output[name].pop("import_binding")
    assert value == original
    assert binding["selected_source_trees_verified"] is True
    assert (
        binding["source_payload_sha256"]
        == hashlib.sha256(
            json.dumps(original[name], sort_keys=True, ensure_ascii=False, allow_nan=False).encode(),
        ).hexdigest()
    )


def test_absent_evidence_is_optional():
    assert bind_evidence(None, []) == {}
    assert validate_evidence({}) == {}


@pytest.mark.parametrize("factory", [feature_evidence, lock_evidence])
@pytest.mark.parametrize("complete", [False, None, 1, "true"])
def test_partial_experiments_cannot_be_imported(factory, complete):
    value = factory()
    next(iter(value.values()))["complete"] = complete
    with pytest.raises(ValueError, match="supplemental_evidence"):
        validate_evidence(value)


@pytest.mark.parametrize("factory", [feature_evidence, lock_evidence])
@pytest.mark.parametrize("rows", [[], [{"run_id": "run-a", "tree_hash": "b" * 64}]])
def test_cohort_membership_and_source_tree_must_match(factory, rows):
    with pytest.raises(ValueError, match="supplemental_evidence"):
        bind_evidence(factory(), rows)


@pytest.mark.parametrize("factory", [feature_evidence, lock_evidence])
def test_duplicate_observations_are_rejected(factory):
    value = factory()
    block = next(iter(value.values()))
    block["observations"].append(copy.deepcopy(block["observations"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_evidence(value)


def test_lock_pair_requires_both_distinct_arms():
    value = lock_evidence()
    value["lock_materialization"]["observations"].pop()
    with pytest.raises(ValueError, match="both arms"):
        validate_evidence(value)


def test_lock_selection_does_not_accept_duplicate_ids():
    value = lock_evidence()
    value["lock_materialization"]["run_ids"].append("run-a")
    with pytest.raises(ValueError, match="supplemental_evidence"):
        validate_evidence(value)


@pytest.mark.parametrize("field", ["planned_pairs", "completed_pairs", "schema_version"])
def test_boolean_values_are_not_integer_counts_or_versions(field):
    value = lock_evidence()
    value["lock_materialization"][field] = True
    with pytest.raises(ValueError, match="supplemental_evidence"):
        validate_evidence(value)


@pytest.mark.parametrize("rate", [-0.01, 1.01, True, float("nan"), float("inf"), float("-inf")])
def test_functional_rate_is_a_finite_probability(rate):
    value = lock_evidence()
    value["lock_materialization"]["observations"][0]["functional_pass_rate"] = rate
    with pytest.raises(ValueError, match="supplemental_evidence"):
        validate_evidence(value)


@pytest.mark.parametrize("factory", [feature_evidence, lock_evidence])
def test_nonfinite_nested_values_cannot_reach_export(factory):
    value = factory()
    next(iter(value.values()))["summary"] = {"probability": float("nan")}
    with pytest.raises(ValueError, match="supplemental_evidence"):
        validate_evidence(value)


@pytest.mark.parametrize("bad_protocol", [[], "invalid", 1, {"selected_runs": [None]}, {"selected_runs": [{}]}])
def test_malformed_protocol_is_a_validation_error(bad_protocol):
    value = feature_evidence()
    value["feature_lifecycle"]["protocol"] = bad_protocol
    with pytest.raises(ValueError, match="supplemental_evidence"):
        validate_evidence(value)


def test_feature_protocol_hash_binds_selection():
    value = feature_evidence()
    value["feature_lifecycle"]["protocol"]["selection_rule"] = "changed after collection"
    with pytest.raises(ValueError, match="supplemental_evidence"):
        validate_evidence(value)


def test_feature_protocol_tree_must_match_observation():
    value = feature_evidence()
    block = value["feature_lifecycle"]
    block["protocol"]["selected_runs"][0]["tree_hash"] = "b" * 64
    block["protocol_sha256"] = digest(block["protocol"])
    with pytest.raises(ValueError, match="supplemental_evidence"):
        bind_evidence(value, [{"run_id": "run-a", "tree_hash": TREE}])


@pytest.mark.parametrize("check_change", ["empty", "duplicate", "missing_id", "nonterminal"])
def test_measured_features_require_complete_identifiable_checks(check_change):
    value = feature_evidence()
    checks = value["feature_lifecycle"]["observations"][0]["checks"]
    if check_change == "empty":
        checks.clear()
    elif check_change == "duplicate":
        checks.append(copy.deepcopy(checks[0]))
    elif check_change == "missing_id":
        checks[0].pop("id")
    else:
        checks[0]["status"] = "pending"
    with pytest.raises(ValueError, match="supplemental_evidence"):
        validate_evidence(value)


def test_blocked_checks_are_retained_as_unobserved_outcomes():
    value = feature_evidence()
    checks = value["feature_lifecycle"]["observations"][0]["checks"]
    for check in checks:
        check.update(status="blocked", reason="Registration prerequisite failed")
    result = bind_evidence(value, [{"run_id": "run-a", "tree_hash": TREE}])
    assert all(c["status"] == "blocked" for c in result["feature_lifecycle"]["observations"][0]["checks"])


def test_unavailable_runtime_is_retained_without_invented_check_results():
    value = feature_evidence()
    value["feature_lifecycle"]["observations"][0].update(
        status="unavailable",
        reason="build_failed",
        checks=[],
    )
    result = bind_evidence(value, [{"run_id": "run-a", "tree_hash": TREE}])
    row = result["feature_lifecycle"]["observations"][0]
    assert row["status"] == "unavailable"
    assert row["checks"] == []


def refresh_protocol(block):
    block["protocol_json"] = json.dumps(block["protocol"], sort_keys=True)
    block["protocol_sha256"] = hashlib.sha256(block["protocol_json"].encode()).hexdigest()


def test_observed_subset_cannot_be_relabelled_as_the_complete_planned_experiment():
    value = lock_evidence()
    block = value["lock_materialization"]
    extra = copy.deepcopy(block["protocol"]["runs"][0])
    extra["run_id"] = "run-b"
    block["protocol"]["runs"].append(extra)
    refresh_protocol(block)
    with pytest.raises(ValueError, match="missing selected runs"):
        validate_evidence(value)


@pytest.mark.parametrize("field", ["context_hash", "plan_sha256", "environment_hash", "suite_ref", "lock_files"])
def test_lock_observations_are_bound_to_frozen_build_inputs(field):
    value = lock_evidence()
    value["lock_materialization"]["observations"][0][field] = "wrong"
    with pytest.raises(ValueError, match="supplemental_evidence"):
        validate_evidence(value)


def test_protocol_text_cannot_be_replaced_while_retaining_its_hash():
    value = lock_evidence()
    value["lock_materialization"]["protocol_json"] += " "
    with pytest.raises(ValueError, match="protocol hash mismatch"):
        validate_evidence(value)


def test_post_freeze_tree_recovery_requires_unchanged_frozen_state():
    value = lock_evidence()
    block = value["lock_materialization"]
    block["protocol"]["runs"][0]["original_tree_hash"] = None
    state = {"runs": "1" * 64, "run_files": "2" * 64, "evaluations": "3" * 64}
    block["protocol"]["original_state"] = state
    refresh_protocol(block)
    block["source_binding_note"] = "Source field recovered after freeze against unchanged original records."
    block["source_tree_bindings"] = {
        "passed": True,
        "protocol_sha256": block["protocol_sha256"],
        "expected_state": state,
        "observed_state": copy.deepcopy(state),
        "tree_hashes": {"run-a": TREE},
    }
    assert bind_evidence(value, [{"run_id": "run-a", "tree_hash": TREE}])
    block["source_tree_bindings"]["observed_state"]["runs"] = "4" * 64
    with pytest.raises(ValueError, match="source state changed"):
        validate_evidence(value)


@pytest.mark.parametrize("field", ["known_build_success", "known_deployment_success", "functional_measured"])
def test_derived_outcomes_cannot_disagree_with_raw_observations(field):
    value = lock_evidence()
    value["lock_materialization"]["observations"][0][field] = False
    with pytest.raises(ValueError, match="supplemental_evidence"):
        validate_evidence(value)


def test_exported_summaries_and_pair_tables_are_recomputed_from_observations():
    value = lock_evidence()
    block = value["lock_materialization"]
    block["summary"] = {"build": {"fail_to_pass": 999, "paired_success_rate_difference": 1}}
    block["pairs"] = [{"run_id": "run-a", "functional_pass_rate_difference": 1}]
    original = copy.deepcopy(value)
    result = bind_evidence(value, [{"run_id": "run-a", "tree_hash": TREE}])["lock_materialization"]
    assert result["summary"]["build"]["pass_to_pass"] == 1
    assert result["summary"]["build"]["fail_to_pass"] == 0
    assert result["summary"]["build"]["paired_success_rate_difference"] == 0
    assert result["pairs"][0]["functional_pass_rate_difference"] == 0
    assert (
        result["import_binding"]["source_payload_sha256"]
        == hashlib.sha256(
            json.dumps(original["lock_materialization"], sort_keys=True, ensure_ascii=False).encode(),
        ).hexdigest()
    )
    assert value == original


@pytest.mark.parametrize("outcome", ["build_fail", "infra_error"])
def test_known_failures_and_unknown_outcomes_have_distinct_denominators(outcome):
    value = lock_evidence()
    row = value["lock_materialization"]["observations"][0]
    unknown = outcome == "infra_error"
    row.update(
        outcome=outcome,
        build_ok=False,
        start_ok=None,
        functional_measured=False,
        functional_pass_rate=None,
        all_checks_passed=None,
        known_build_success=None if unknown else False,
        known_deployment_success=None if unknown else False,
        known_end_to_end_all_checks_success=None if unknown else False,
        error="Build service unavailable" if unknown else "Source build failed",
    )
    summary = bind_evidence(value, [{"run_id": "run-a", "tree_hash": TREE}])["lock_materialization"]["summary"]
    assert summary["build"]["pairs_known"] == (0 if unknown else 1)
    assert summary["build"]["pairs_missing"] == (1 if unknown else 0)
    assert summary["build"]["fail_to_pass"] == (0 if unknown else 1)
    assert summary["build"]["paired_success_rate_difference"] == (None if unknown else 1)
    assert summary["conditional_functional"]["both_measured_pairs"] == 0
    assert summary["conditional_functional"]["mean_within_pair_pass_rate_difference"] is None


@pytest.mark.parametrize("status", ["pending", None, [], {}])
def test_feature_runtime_must_finish_before_import(status):
    value = feature_evidence()
    value["feature_lifecycle"]["observations"][0]["status"] = status
    with pytest.raises(ValueError, match="supplemental_evidence"):
        validate_evidence(value)


def test_incomplete_teardown_and_truncated_check_sets_are_rejected():
    value = feature_evidence()
    row = value["feature_lifecycle"]["observations"][0]
    row["teardown"] = "pending"
    with pytest.raises(ValueError, match="teardown is incomplete"):
        validate_evidence(value)
    row["teardown"] = "completed"
    row["checks"].pop()
    with pytest.raises(ValueError, match="missing planned checks"):
        validate_evidence(value)


@pytest.mark.django_db
def test_imported_successes_leave_selected_primary_grades_unchanged():
    from backend.llm_models.tests.factories import LLMModelFactory
    from backend.reports.services.study_report import generate_study_report
    from backend.reports.tests.helpers import evaluate
    from backend.reports.tests.helpers import make_run
    from backend.users.tests.factories import UserFactory

    user = UserFactory()
    run = make_run(user, LLMModelFactory())
    evaluation = evaluate(run, passed=0, failed=4)
    config = {
        "run_ids": [str(run.pk)],
        "eval_suite": evaluation.eval_suite.pinned_ref,
        "ranking_draws": 500,
        "bootstrap": False,
    }
    original = generate_study_report(config, user=user)
    revised = generate_study_report(
        {**config, "supplemental_evidence": lock_evidence(str(run.pk), run.tree_hash)},
        user=user,
    )
    for section in ("funnel", "per_app", "ranking", "model_outcomes", "security_bounds", "issues"):
        assert revised[section] == original[section]
    assert revised["per_app"][0]["pass_rate"] == 0
    assert revised["supplemental_evidence"]["lock_materialization"]["summary"]["build"]["pass_to_pass"] == 1


@pytest.mark.django_db
def test_evidence_cannot_expand_report_scope_into_another_users_runs():
    from backend.llm_models.tests.factories import LLMModelFactory
    from backend.reports.services.study_report import generate_study_report
    from backend.reports.tests.helpers import make_run
    from backend.users.tests.factories import UserFactory

    user = UserFactory()
    foreign = make_run(UserFactory(), LLMModelFactory())
    with pytest.raises(ValueError, match="outside this report's cohort"):
        generate_study_report(
            {
                "run_ids": [str(foreign.pk)],
                "ranking_draws": 500,
                "bootstrap": False,
                "supplemental_evidence": lock_evidence(str(foreign.pk), foreign.tree_hash),
            },
            user=user,
        )


def test_paired_transition_summary_counts_both_directions_and_excludes_unknowns():
    block = lock_evidence()["lock_materialization"]
    block.update(observations=[], run_ids=[], planned_pairs=5, completed_pairs=5)
    block["protocol"]["runs"] = []
    for index in range(5):
        rid = f"run-{index}"
        sample = lock_evidence(rid)["lock_materialization"]
        row = sample["observations"][1 if index == 3 else 0]
        unknown = index == 4
        row.update(
            outcome="infra_error" if unknown else "build_fail",
            error="Unavailable build service" if unknown else "Build failed",
            build_ok=False,
            start_ok=None,
            functional_measured=False,
            functional_pass_rate=None,
            all_checks_passed=None,
            known_build_success=None if unknown else False,
            known_deployment_success=None if unknown else False,
            known_end_to_end_all_checks_success=None if unknown else False,
        )
        block["observations"].extend(sample["observations"])
        block["protocol"]["runs"].extend(sample["protocol"]["runs"])
        block["run_ids"].append(rid)
    refresh_protocol(block)
    result = bind_evidence(
        {"lock_materialization": block},
        [{"run_id": rid, "tree_hash": TREE} for rid in block["run_ids"]],
    )["lock_materialization"]["summary"]["build"]
    assert result["pairs_known"] == 4
    assert result["pairs_missing"] == 1
    assert result["fail_to_pass"] == 3
    assert result["pass_to_fail"] == 1
    assert result["paired_success_rate_difference"] == 0.5
    assert result["mcnemar_exact_two_sided"] == 0.625
    assert result["interval"] is None


@pytest.mark.parametrize("controls", [[], [{"status": "pending"}], [{"status": "fail"}], [None]])
def test_incomplete_or_failed_feature_controls_prevent_import(controls):
    value = feature_evidence()
    value["feature_lifecycle"]["controls"] = controls
    with pytest.raises(ValueError, match="supplemental_evidence"):
        validate_evidence(value)


def test_feature_control_success_flag_cannot_hide_an_observed_failure():
    value = feature_evidence()
    control = value["feature_lifecycle"]["controls"][0]
    control["checks"][0]["status"] = "fail"
    control["actual_failed_checks"] = [CHECKS[0]]
    with pytest.raises(ValueError, match="positive control did not satisfy"):
        validate_evidence(value)


def test_feature_control_mutants_cannot_be_dropped_from_the_declared_set():
    value = feature_evidence()
    block = value["feature_lifecycle"]
    block["protocol"]["policy"]["mutants"] = ["reset_accepts_reuse"]
    block["protocol_sha256"] = digest(block["protocol"])
    with pytest.raises(ValueError, match="missing declared mutants"):
        validate_evidence(value)


def test_mutant_failure_lists_are_order_independent_but_cannot_repeat_checks():
    value = feature_evidence()
    block = value["feature_lifecycle"]
    block["protocol"]["policy"]["mutants"] = ["reset_accepts_reuse"]
    block["protocol_sha256"] = digest(block["protocol"])
    mutant = copy.deepcopy(block["controls"][0])
    mutant.update(mutant="reset_accepts_reuse", expected_failed_check=CHECKS[0], actual_failed_checks=list(CHECKS[:2]))
    for check in mutant["checks"][:2]:
        check["status"] = "fail"
    block["controls"].append(mutant)
    assert validate_evidence(value) == value
    mutant["actual_failed_checks"].append(CHECKS[0])
    with pytest.raises(ValueError, match="failure list disagrees"):
        validate_evidence(value)


@pytest.mark.django_db
def test_cli_accepts_explicit_suite_and_bound_evidence(tmp_path):
    from django.core.management import call_command

    from backend.llm_models.tests.factories import LLMModelFactory
    from backend.reports.tests.helpers import evaluate
    from backend.reports.tests.helpers import make_run
    from backend.users.tests.factories import UserFactory

    user = UserFactory()
    run = make_run(user, LLMModelFactory())
    evaluation = evaluate(run, passed=1, failed=3)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(lock_evidence(str(run.pk), run.tree_hash)), encoding="utf-8")
    out = tmp_path / "report.json"
    call_command(
        "study_report",
        "--user",
        user.email,
        "--eval-suite",
        evaluation.eval_suite.pinned_ref,
        "--evidence",
        str(evidence_path),
        "--out",
        str(out),
        "--no-bootstrap",
    )
    report = json.loads(out.read_text())
    assert report["per_app"][0]["pass_rate"] == 0.25
    assert report["protocol"]["evaluation_selection"]["suite_ref"] == evaluation.eval_suite.pinned_ref
    assert report["supplemental_evidence"]["lock_materialization"]["import_binding"]["protocol_hash_verified"]
