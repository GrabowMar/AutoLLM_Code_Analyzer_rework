"""``manage.py reevaluate_under_suite``: re-evaluating stored runs under a new suite version (eval-v2@1 -> @2).

Append-only: the evaluations under the old suite are never changed; dry run by default; the run's outcome and
``metrics.eval`` follow the new evaluation once it is final (``supersede.current_evaluation``).
"""

from __future__ import annotations

from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from backend.generation.evaluation import acceptance
from backend.generation.evaluation import suite as suite_mod
from backend.generation.evaluation import supersede as supersede_mod
from backend.generation.library import service
from backend.generation.management.commands import reevaluate_under_suite as command_mod
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import choices
from backend.generation.tests.wp09_eval.test_suite import claim
from backend.generation.tests.wp09_eval.test_suite import make_run
from backend.generation.tests.wp09_eval.test_suite import suite_of

pytestmark = pytest.mark.django_db

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
COMMAND = "reevaluate_under_suite"


@pytest.fixture(autouse=True)
def _local_runtime(settings: Any) -> None:
    settings.EVAL_RUNTIME = "local"


@pytest.fixture(autouse=True)
def _tree_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acceptance, "fixtures_digest", lambda: DIGEST_A, raising=False)


@pytest.fixture
def golden() -> Any:
    from backend.generation.testing.golden_library import install_golden_library

    return install_golden_library()


def next_suite(golden: Any, digest: str = DIGEST_A) -> Any:
    """eval-v2@(n+1): the golden suite with the fixtures pin (what the instrument fix seeds)."""
    v1 = suite_of(golden)
    return service.new_version(
        v1,
        base_version=v1.version,
        content={**v1.content, "acceptance_compiler_version": acceptance.COMPILER_VERSION, "fixtures_digest": digest},
        user=None,
        system=service.is_system_row(v1),
    )


def evaluated_run(golden: Any) -> tuple[GenerationRun, RunEvaluation]:
    """A completed run with one final evaluation under the golden suite (through the eval stage)."""
    run = claim(make_run(golden, level="functional"))
    suite_mod.run_eval_stage(run)
    run.refresh_from_db()
    assert run.status == choices.RunStatus.COMPLETED
    row = RunEvaluation.objects.get(run=run)
    return run, row


def snapshot(row: RunEvaluation) -> tuple[Any, ...]:
    row = RunEvaluation.objects.get(pk=row.pk)
    return (row.eval_suite_id, row.status, row.result, row.stages, row.adherence, row.created_at)


def call(*args: str) -> tuple[str, str]:
    out, err = StringIO(), StringIO()
    call_command(COMMAND, *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def test_the_command_needs_an_explicit_selection(golden: Any) -> None:
    v2 = next_suite(golden)
    with pytest.raises(CommandError, match="never selects every run"):
        call("--suite", v2.ref)
    with pytest.raises(CommandError, match="--suite"):
        call("--suite", "eval-v2@999", "00000000-0000-0000-0000-000000000000")


def test_dry_run_is_the_default_and_writes_nothing(golden: Any) -> None:
    run, old = evaluated_run(golden)
    v2 = next_suite(golden)
    before = (snapshot(old), run.status, run.phase, run.execution, run.validity, run.metrics)
    out, _ = call("--suite", v2.ref, str(run.pk))
    assert f"* run={run.pk}" in out
    assert f"current={old.pk}@{suite_of(golden).ref}" in out
    assert "1 to re-evaluate" in out
    assert "dry run" in out
    run.refresh_from_db()
    assert (snapshot(old), run.status, run.phase, run.execution, run.validity, run.metrics) == before
    assert RunEvaluation.objects.filter(run=run).count() == 1


def test_apply_queues_appends_and_the_run_follows_the_new_evaluation(golden: Any) -> None:
    run, old = evaluated_run(golden)
    v1 = suite_of(golden)
    v2 = next_suite(golden)
    frozen = snapshot(old)
    mirror = dict(run.metrics["eval"])
    assert mirror["eval_suite"] == v1.pinned_ref

    out, _ = call("--suite", v2.ref, str(run.pk), "--apply", "--by", "operator-a", "--note", "instrument fix")
    assert "queued run=" in out
    run.refresh_from_db()
    assert (run.status, run.phase) == (choices.RunStatus.RUNNING, choices.RunPhase.EVAL_QUEUED)
    pending = RunEvaluation.objects.exclude(pk=old.pk).get(run=run)
    assert pending.status == choices.EvaluationStatus.PENDING
    assert pending.eval_suite_id == v2.pk
    assert run.execution["reeval_evaluation_id"] == pending.pk
    assert run.execution["reeval_suite"] == v2.pinned_ref
    [entry] = run.execution[supersede_mod.HISTORY_KEY]
    assert entry["reason"] == supersede_mod.SUITE_UPGRADE
    assert entry["by"] == "manage.py reevaluate_under_suite (operator-a)"
    assert entry["note"] == "instrument fix"
    assert (entry["superseded_evaluation"], entry["evaluation"]) == (old.pk, pending.pk)
    assert (entry["eval_suite"], entry["to_eval_suite"]) == (v1.pinned_ref, v2.pinned_ref)
    assert entry["superseded_outcome"] == choices.Outcome.TESTED
    assert entry["superseded_eval"] == mirror
    note = next(n for n in run.validity["notes"] if n["code"] == "eval_superseded:suite_upgrade")
    assert note["level"] == "info"
    # Nothing is current under the new suite until its evaluation is final.
    assert supersede_mod.current_evaluation(run).pk == old.pk
    assert supersede_mod.current_evaluation(run, suite=v2) is None
    assert snapshot(old) == frozen

    out, _ = call("--suite", v2.ref, str(run.pk))  # while it is queued
    assert "skip: active" in out
    assert "0 to re-evaluate" in out

    suite_mod.run_eval_stage(claim(run))
    run.refresh_from_db()
    pending.refresh_from_db()
    assert pending.status == choices.EvaluationStatus.MEASURED
    assert pending.result["fixtures_sha256"] == DIGEST_A
    assert snapshot(old) == frozen  # append-only: the eval-v2@1 row is final and unchanged
    assert RunEvaluation.objects.filter(run=run).count() == 2
    assert supersede_mod.current_evaluation(run).pk == pending.pk
    assert supersede_mod.current_evaluation(run, suite=v1).pk == old.pk
    assert supersede_mod.current_evaluation(run, suite=v2).pk == pending.pk
    assert run.status == choices.RunStatus.COMPLETED
    assert run.outcome == choices.Outcome.TESTED
    assert run.metrics["eval"]["evaluation_id"] == pending.pk
    assert run.metrics["eval"]["eval_suite"] == v2.pinned_ref

    out, _ = call("--suite", v2.ref, str(run.pk), "--apply")
    assert f"skip: already under {v2.ref}: evaluation {pending.pk}" in out
    assert "queued 0 re-evaluation(s)" in out
    assert RunEvaluation.objects.filter(run=run).count() == 2

    out, _ = call("--suite", v2.ref, str(run.pk), "--apply", "--force")
    assert "queued 1 re-evaluation(s)" in out
    assert RunEvaluation.objects.filter(run=run).count() == 3
    assert len(GenerationRun.objects.get(pk=run.pk).execution[supersede_mod.HISTORY_KEY]) == 2


def test_retests_are_never_the_current_evaluation(golden: Any) -> None:
    run, old = evaluated_run(golden)
    RunEvaluation.objects.create(
        run=run,
        eval_suite=suite_of(golden),
        eval_level="functional",
        status=choices.EvaluationStatus.MEASURED,
        retest_of=old,
    )
    assert supersede_mod.current_evaluation(run).pk == old.pk


def test_runs_that_cannot_be_reevaluated_are_listed_with_the_reason(golden: Any) -> None:
    v2 = next_suite(golden)
    active, _ = evaluated_run(golden)
    GenerationRun.objects.filter(pk=active.pk).update(status=choices.RunStatus.RUNNING)
    invalid, _ = evaluated_run(golden)
    GenerationRun.objects.filter(pk=invalid.pk).update(outcome=choices.Outcome.OUTPUT_INVALID)
    waiting, _ = evaluated_run(golden)
    RunEvaluation.objects.create(run=waiting, eval_suite=v2, eval_level="functional")
    fine, _ = evaluated_run(golden)
    ids = [str(r.pk) for r in (active, invalid, waiting, fine)]

    out, err = call("--suite", v2.ref, *ids, "00000000-0000-0000-0000-000000000000")
    lines = {word: line for line in out.splitlines() for word in line.split()[:2] if word.startswith("run=")}
    assert "skip: active" in lines[f"run={active.pk}"]
    assert "skip: decided by the generation stage (output_invalid)" in lines[f"run={invalid.pk}"]
    assert "skip: an evaluation of it is pending or running" in lines[f"run={waiting.pk}"]
    assert lines[f"run={fine.pk}"].startswith("* ")
    assert "4 run(s), 1 to re-evaluate" in out
    assert "no such run" in err

    out, _ = call("--suite", v2.ref, *ids, "--outcome", "build_fail,start_fail")
    assert "0 to re-evaluate" in out
    assert "not in --outcome" in out

    out, _ = call("--suite", v2.ref, *ids, "--apply")
    assert "queued 1 re-evaluation(s)" in out
    assert RunEvaluation.objects.filter(eval_suite=v2, run=fine).count() == 1
    assert not RunEvaluation.objects.filter(eval_suite=v2, run__in=[active.pk, invalid.pk]).exists()


def test_requeue_refuses_what_the_listing_skips(golden: Any) -> None:
    run, _ = evaluated_run(golden)
    v1 = suite_of(golden)
    with pytest.raises(supersede_mod.SupersedeRefusedError, match="already under"):
        supersede_mod.requeue_under_suite(run, v1)
    GenerationRun.objects.filter(pk=run.pk).update(outcome=choices.Outcome.OUTPUT_INVALID)
    run.refresh_from_db()
    with pytest.raises(supersede_mod.SupersedeRefusedError, match="not evaluable"):
        supersede_mod.requeue_under_suite(run, next_suite(golden))
    run.refresh_from_db()
    assert RunEvaluation.objects.filter(run=run).count() == 1  # the refused action left no pending row
    assert supersede_mod.HISTORY_KEY not in (run.execution or {})


def test_a_tree_that_is_not_the_pinned_instrument_queues_nothing(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    run, _ = evaluated_run(golden)
    v2 = next_suite(golden, DIGEST_B)  # the suite was validated with other overlays than this tree's
    assert command_mod.instrument_problems(v2)
    assert command_mod.instrument_problems(suite_of(golden)) == []  # an unpinned suite is never checked
    out, err = call("--suite", v2.ref, str(run.pk))
    assert "instrument mismatch: fixtures_digest" in err
    assert "dry run" in out
    with pytest.raises(CommandError, match="nothing was queued"):
        call("--suite", v2.ref, str(run.pk), "--apply")
    assert RunEvaluation.objects.filter(run=run).count() == 1
    run.refresh_from_db()
    assert run.status == choices.RunStatus.COMPLETED

    monkeypatch.setattr(acceptance, "fixtures_digest", lambda: DIGEST_B, raising=False)
    monkeypatch.setattr(acceptance, "COMPILER_VERSION", acceptance.COMPILER_VERSION + 1)
    assert any(p.startswith("compiler:") for p in command_mod.instrument_problems(v2))


def test_max_queued_bounds_the_runs_in_the_eval_lane(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    runs = [evaluated_run(golden)[0] for _ in range(3)]
    v2 = next_suite(golden)
    seen: list[int] = []

    def worker(_seconds: float) -> None:
        """What the executor does between two polls: finish the evaluations that are in the lane."""
        active = RunEvaluation.objects.filter(eval_suite=v2, status=choices.EvaluationStatus.PENDING)
        seen.append(active.count())
        active.update(status=choices.EvaluationStatus.MEASURED)

    monkeypatch.setattr(command_mod.Command, "sleep", staticmethod(worker))
    out, _ = call("--suite", v2.ref, *[str(r.pk) for r in runs], "--apply", "--max-queued", "1", "--wait")
    assert "queued 3 re-evaluation(s)" in out
    assert seen == [1, 1, 1]  # never more than one in the lane, and the last one is waited for
    assert "final: measured=3" in out


def test_limit_and_unbounded_queueing(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    runs = [evaluated_run(golden)[0] for _ in range(3)]
    v2 = next_suite(golden)

    def no_sleep(_seconds: float) -> None:
        msg = "an unbounded queue never waits"
        raise AssertionError(msg)

    monkeypatch.setattr(command_mod.Command, "sleep", staticmethod(no_sleep))
    out, _ = call("--suite", v2.ref, *[str(r.pk) for r in runs], "--apply", "--max-queued", "0", "--limit", "2")
    assert "--limit 2: 1 selected run(s) left out" in out
    assert "queued 2 re-evaluation(s)" in out
    assert RunEvaluation.objects.filter(eval_suite=v2).count() == 2
