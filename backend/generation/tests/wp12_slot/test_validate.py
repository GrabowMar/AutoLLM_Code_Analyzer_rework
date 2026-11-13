"""Target validation: discriminating checks only, flaky and unstable probes dropped (spec section 7.4).

Includes the section 13.4 invariant owned by WP-12: slot targets keep only linked checks that discriminate.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models.choices import RunPhase
from backend.generation.models.choices import RunStatus
from backend.generation.slot import selection
from backend.generation.slot import validate
from backend.generation.testing.content import eval_result_content
from backend.generation.testing.fake_eval import FakeEvaluator
from backend.generation.testing.fake_slot import SlotEvalScript

pytestmark = pytest.mark.django_db

PATCH = {"kind": "endpoint", "endpoint": {"method": "PATCH", "path": "/api/todos/{id}"}}
LIST = {"kind": "endpoint", "endpoint": {"method": "GET", "path": "/api/todos"}}


def _validated(slot_set: Any, user: Any, selection_in: dict[str, Any]) -> Any:
    target = selection.create_target(slot_set, selection_in, user=user, validate=False)
    validate.validate_target(target, synchronous=True)
    target.refresh_from_db()
    return target


def test_discriminating_check_is_kept_and_original_run_set(slot_set: Any, researcher: Any, evaluator: Any) -> None:
    target = _validated(slot_set, researcher, PATCH)
    v = target.validation
    assert v["status"] == "ok"
    assert v["original_pass"] == {"todos.update.ok": [True, True, True]}
    assert v["holed_fail"] == {"todos.update.ok": True}
    assert v["discriminating_checks"] == ["todos.update.ok"]
    assert v["dropped_checks"] == []
    assert target.linked_checks == ["todos.update.ok"]
    assert target.linked_probes == ["probe.todos.idor"]
    assert v["probe_stability"] == {"probe.todos.idor": ["not_exploited"] * 3}
    runs = GenerationRun.objects.filter(pk__in=v["runs"])
    assert sorted(runs.values_list("reference_of", flat=True)) == ["slot_holed"] + ["slot_original"] * 3
    assert str(target.original_run_id) == v["runs"][0]
    original = target.original_run
    assert original.tree_hash == slot_set.frozen_tree_hash
    holed = runs.get(reference_of="slot_holed")
    assert holed.tree_hash != slot_set.frozen_tree_hash
    # Every validation evaluation ran exactly the candidate selection.
    calls = {call.run_id: call for call in evaluator.calls}
    assert {tuple(call.only_checks) for call in calls.values()} == {("todos.update.ok",)}
    assert {tuple(call.probes) for call in calls.values()} == {("probe.todos.idor",)}
    assert validate.target_usable(target) == (True, "")


def test_non_discriminating_check_is_dropped(
    slot_set: Any,
    researcher: Any,
    evaluator: Any,
    script: SlotEvalScript,
) -> None:
    script.holed = {"todos.search.matches": "pass"}  # still passes with the hole: proves nothing about the part
    target = _validated(slot_set, researcher, LIST)
    v = target.validation
    assert v["status"] == "ok"
    assert v["discriminating_checks"] == ["todos.list.ok"]
    assert v["dropped_checks"] == ["todos.search.matches"]
    assert target.linked_checks == ["todos.list.ok"]


def test_flaky_check_is_dropped_and_none_left_is_uninformative(
    slot_set: Any,
    researcher: Any,
    evaluator: Any,
    script: SlotEvalScript,
) -> None:
    script.original_runs = {1: {"todos.update.ok": "fail"}}
    target = _validated(slot_set, researcher, PATCH)
    v = target.validation
    assert v["status"] == "uninformative"
    assert v["flaky_checks"] == ["todos.update.ok"]
    assert v["original_pass"]["todos.update.ok"] == [True, False, True]
    assert target.linked_checks == []
    (issue,) = validate.validation_issues(target)
    assert (issue.code, issue.severity) == ("slot_tests_uninformative", "warn")
    assert validate.validation_issues(target, "confirmatory")[0].severity == "error"
    assert validate.target_usable(target)[0] is True  # usable, but reported n/a and flagged


def test_unstable_probe_is_dropped(slot_set: Any, researcher: Any, evaluator: Any, script: SlotEvalScript) -> None:
    script.probe_runs = {2: {"probe.todos.idor": "exploited"}}
    target = _validated(slot_set, researcher, PATCH)
    assert target.validation["probe_stability"]["probe.todos.idor"] == ["not_exploited", "not_exploited", "exploited"]
    assert target.linked_probes == []


def test_holed_tree_that_does_not_build_fails_every_check(slot_set: Any, researcher: Any, monkeypatch: Any) -> None:
    script = SlotEvalScript()
    fake = FakeEvaluator(results=script).install(monkeypatch)
    original_evaluate = fake.evaluate

    def evaluate(run: GenerationRun, suite: Any, **kwargs: Any) -> RunEvaluation:
        if run.reference_of != "slot_holed":
            return original_evaluate(run, suite, **kwargs)
        return RunEvaluation.objects.create(
            run=run,
            eval_suite=suite,
            eval_level="security",
            status="not_measured",
            stages={"lock": {"status": "ok"}, "build": {"status": "fail"}},
            result=eval_result_content(measured=False, not_measured_reason="build_failed"),
        )

    from backend.generation.evaluation import suite as suite_mod

    monkeypatch.setattr(suite_mod, "evaluate", evaluate)
    target = _validated(slot_set, researcher, {**PATCH, "default_hole_mode": "remove"})
    assert target.validation["status"] == "ok"
    assert target.validation["holed_fail"] == {"todos.update.ok": True}


def test_unmeasured_original_is_a_validation_error_never_a_pass(
    slot_set: Any,
    researcher: Any,
    monkeypatch: Any,
) -> None:
    FakeEvaluator(default=eval_result_content(measured=False)).install(monkeypatch)
    target = _validated(slot_set, researcher, PATCH)
    assert target.validation["status"] == "error"
    assert "not measured" in target.validation["error"]
    assert target.linked_checks == ["todos.update.ok"]  # untouched: nothing was learned
    assert validate.target_usable(target) == (False, "validation is error")


def test_rewrite_targets_have_no_holed_run(slot_set: Any, researcher: Any, evaluator: Any) -> None:
    target = _validated(slot_set, researcher, {**PATCH, "default_hole_mode": "rewrite"})
    assert len(target.validation["runs"]) == validate.VALIDATION_RUNS
    assert target.validation["holed_fail"] == {}
    assert target.validation["discriminating_checks"] == ["todos.update.ok"]


def test_queued_validation_completes_on_the_last_reference_evaluation(
    slot_set: Any,
    researcher: Any,
    evaluator: Any,
    django_capture_on_commit_callbacks: Any,
) -> None:
    from backend.generation.slot.evaluate import evaluate_variant

    with django_capture_on_commit_callbacks(execute=True):
        target = selection.create_target(slot_set, PATCH, user=researcher)
    runs = list(GenerationRun.objects.filter(pk__in=target.validation["runs"]))
    # enqueue_evaluation ran on commit: every validation run is claimable by phase.
    assert {(r.status, r.phase) for r in runs} == {(RunStatus.RUNNING, RunPhase.EVAL_QUEUED)}
    assert {r.priority for r in runs} == {validate.VALIDATION_PRIORITY}
    by_id = {str(r.pk): r for r in runs}
    for index, run_id in enumerate(target.validation["runs"]):
        evaluate_variant(by_id[run_id])
        result = validate.on_reference_evaluated(by_id[run_id])
        target.refresh_from_db()
        if index < len(runs) - 1:
            assert result.status == "pending"
            assert target.validation["status"] == "pending"
    assert target.validation["status"] == "ok"
    assert target.linked_checks == ["todos.update.ok"]


def test_revalidate_restores_the_edited_selection(slot_set: Any, researcher: Any, evaluator: Any) -> None:
    target = selection.create_target(
        slot_set,
        {**LIST, "linked_checks": ["todos.list.ok"]},
        user=researcher,
        validate=False,
    )
    validate.validate_target(target, synchronous=True)
    target.refresh_from_db()
    validate.revalidate(target, user=researcher)
    target.refresh_from_db()
    assert target.validation["status"] == "pending"
    assert list(target.validation["original_pass"]) == ["todos.list.ok"]  # the edit (search check removed) is kept


def test_unmeasured_holed_check_is_dropped_never_discriminating(
    slot_set: Any,
    researcher: Any,
    evaluator: Any,
    script: SlotEvalScript,
) -> None:
    script.holed = {"todos.search.matches": "error"}  # tester error with the hole: not a measured failure
    target = _validated(slot_set, researcher, LIST)
    v = target.validation
    assert v["status"] == "ok"
    assert v["holed_fail"] == {"todos.list.ok": True}  # no entry for the unmeasured check
    assert v["discriminating_checks"] == ["todos.list.ok"]
    assert v["dropped_checks"] == ["todos.search.matches"]


def test_holed_run_measuring_no_stable_check_is_a_validation_error(
    slot_set: Any,
    researcher: Any,
    evaluator: Any,
    script: SlotEvalScript,
) -> None:
    script.holed = {"todos.update.ok": "error"}
    target = _validated(slot_set, researcher, PATCH)
    assert target.validation["status"] == "error"
    assert "measured none" in target.validation["error"]
    assert validate.target_usable(target)[0] is False


def test_holed_verdicts_count_only_measured_failures() -> None:
    from types import SimpleNamespace

    from backend.generation.testing.fake_slot import slot_eval_result

    result = slot_eval_result(
        {"a": "pass", "b": "fail", "c": "error", "d": "skipped", "e": "skipped"},
    )
    for row in result["checks"]:
        if row["id"] == "e":
            row["skipped_due_to"] = ["b"]  # skipped because a depends_on check failed: a measured failure
    evaluation = SimpleNamespace(result=result)
    verdicts = validate._holed_verdicts(evaluation, ["a", "b", "c", "d", "e", "missing"])
    assert verdicts == {"a": False, "b": True, "e": True}
