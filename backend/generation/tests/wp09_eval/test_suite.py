"""WP-09: ``suite.evaluate`` / ``run_eval_stage``, the lock step, baselines, oracles and conformance (6.10-6.12, 8.5).

Evaluations run on the local runtime (``EVAL_RUNTIME=local``): the bundled reference app as a process, the
tester and adherence checker in-process. Build-seam outcomes use a runtime whose ``build`` is scripted.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.utils import timezone

from backend.generation import evaluation as evaluation_pkg
from backend.generation.build import context as build_context
from backend.generation.build import locks as build_locks
from backend.generation.evaluation import baseline
from backend.generation.evaluation import conformance
from backend.generation.evaluation import lock as lock_mod
from backend.generation.evaluation import retest
from backend.generation.evaluation import suite as suite_mod
from backend.generation.evaluation.runtime import BuildOutcome
from backend.generation.evaluation.runtime import LocalProcessRuntime
from backend.generation.evaluation.runtime import TesterCrashError as CrashError
from backend.generation.models import EvalBaseline
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import choices
from backend.generation.testing.golden_library import install_golden_library
from backend.generation.tests.wp09_eval.helpers import kit_tree

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _local_runtime(settings: Any) -> None:
    settings.EVAL_RUNTIME = "local"


@pytest.fixture
def golden() -> Any:
    return install_golden_library()


def make_run(
    golden: Any,
    tree: dict[str, tuple[bytes, int]] | None = None,
    *,
    level: str = "security",
    **kw: Any,
) -> GenerationRun:
    return baseline.reference_run(
        reference_of=kw.pop("reference_of", choices.ReferenceOf.ORACLE),
        task_version=golden.get("task", "crud_todo_list"),
        stack=golden.get("stack", "flask-react"),
        starter=kw.pop("starter", None),
        auth_mode=kw.pop("auth_mode", "contract_bearer"),
        files=tree or kit_tree(),
        suite=golden.get("eval_suite", "eval-v2"),
        eval_level=level,
    )


def suite_of(golden: Any) -> Any:
    return golden.get("eval_suite", "eval-v2")


# --- the golden-library acceptance of WP-09 --------------------------------------------------------


def test_reference_passes_100_percent_three_times_and_the_bare_starter_scores_below_half(golden: Any) -> None:
    version = golden.get("task", "crud_todo_list")
    stack = golden.get("stack", "flask-react")
    out = conformance.validate_oracle(
        version,
        stack,
        suite_of(golden),
        reference=kit_tree(),
        starter_tree=kit_tree(LLMLAB_MUTANT="bare"),
        write_flaky=False,
    )
    assert out["rates"] == [1.0, 1.0, 1.0]
    assert out["flaky"] == []
    assert out["starter_pass_rate"] is not None
    assert out["starter_pass_rate"] < 0.5
    assert out["status"] == "pass"
    version.refresh_from_db()
    assert version.oracle[stack.pinned_ref]["status"] == "pass"


def test_evaluate_records_every_stage(golden: Any) -> None:
    run = make_run(golden)
    row = suite_mod.evaluate(run, suite_of(golden))
    assert row.status == choices.EvaluationStatus.MEASURED
    stages = row.stages
    assert stages["lock"]["status"] == "ok"
    assert stages["lock"]["detail"]["source"] == "model"
    assert stages["build"]["status"] == "ok"
    assert stages["start"]["status"] == "ok"
    assert stages["tester"]["detail"]["only_checks"] is None  # reuse.selection_detail
    assert stages["probes"]["status"] == "ok"
    result = row.result
    assert result["measured"] is True
    assert result["metrics"]["agent_pass_rate"] == 1.0
    assert result["metrics"]["probes_exploited"] == 0
    assert result["metrics"]["probes_tested"] == len(result["probes"]) >= 1
    assert all(p["status"] == "not_exploited" for p in result["probes"])
    assert row.adherence["status"] in {"compliant", "partial", "violated"}
    creds = evaluation_pkg.credentials(run)
    assert creds["admin_username"] == "admin"
    assert len(creds["admin_password"]) > 12
    assert suite_mod.outcome_of(row) == (choices.Outcome.TESTED, "")
    run.refresh_from_db()
    assert run.lock_source == choices.LockSource.MODEL
    assert run.resolved_lock_blob


def test_crash_on_start_is_start_fail_not_a_pass_rate(golden: Any) -> None:
    run = make_run(golden, kit_tree(LLMLAB_MUTANT="crash"))
    settings_content = dict(suite_of(golden).content)
    assert settings_content["timeouts"]["start_s"] > 0
    row = suite_mod.evaluate(run, suite_of(golden))
    assert row.stages["start"]["status"] == "fail"
    assert row.status == choices.EvaluationStatus.NOT_MEASURED
    assert row.result["measured"] is False
    assert row.result["metrics"]["agent_pass_rate"] is None
    assert suite_mod.outcome_of(row) == (choices.Outcome.START_FAIL, "")


def test_levels_without_the_tester_are_not_measured(golden: Any) -> None:
    row = suite_mod.evaluate(make_run(golden, level="build"), suite_of(golden))
    assert row.status == choices.EvaluationStatus.NOT_MEASURED
    assert row.stages["tester"]["status"] == "not_measured"
    assert "start" not in row.stages
    assert row.result["metrics"]["agent_pass_rate"] is None
    assert suite_mod.outcome_of(row) == (choices.Outcome.TESTED, "not_tested:build")


def test_functional_level_lists_probes_as_skipped_by_level(golden: Any) -> None:
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden))
    assert row.result["measured"] is True
    assert row.result["probes"]
    assert all(p["status"] == "skipped" and p["skip_reason"] == "level" for p in row.result["probes"])
    assert row.result["metrics"]["sec_pass"] is None
    assert row.stages["probes"]["status"] == "not_measured"


def test_output_invalid_is_never_built(golden: Any) -> None:
    run = make_run(golden)
    run.outcome = choices.Outcome.OUTPUT_INVALID
    with pytest.raises(ValueError, match="never built"):
        suite_mod.evaluate(run, suite_of(golden))


# --- tester crash and the build seam -----------------------------------------------------------------


class ScriptedRuntime(LocalProcessRuntime):
    def __init__(self, builds: list[Any] | None = None, tester_crashes: int = 0) -> None:
        super().__init__()
        self.builds = list(builds or [])
        self.build_calls = 0
        self.tester_crashes = tester_crashes

    def build(self, run: Any, **kwargs: Any) -> BuildOutcome:
        self.build_calls += 1
        if self.builds:
            item = self.builds.pop(0)
            if isinstance(item, Exception):
                raise item
            if item is not None:
                return item
        return super().build(run, **kwargs)

    def run_tester(self, handle: Any, plan: dict[str, Any], env: Any, timeout_s: float) -> dict[str, Any]:
        if self.tester_crashes:
            self.tester_crashes -= 1
            msg = "the tester wrote no result"
            raise CrashError(msg)
        return super().run_tester(handle, plan, env, timeout_s)


def test_tester_crash_is_retried_inside_the_stage(golden: Any) -> None:
    runtime = ScriptedRuntime(tester_crashes=1)
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden), runtime=runtime)
    assert row.status == choices.EvaluationStatus.MEASURED
    assert row.stages["tester"]["detail"]["attempts"] == 2


def test_repeated_tester_crash_is_infra_error_never_zero(golden: Any) -> None:
    runtime = ScriptedRuntime(tester_crashes=2)
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden), runtime=runtime)
    assert row.status == choices.EvaluationStatus.ERROR
    assert row.result["measured"] is False
    assert row.result["metrics"]["agent_pass_rate"] is None
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, "tester_crash")


@pytest.mark.parametrize(
    ("builds", "outcome", "calls"),
    [
        (
            [build_context.BaseNotAllowedError("FROM evil:latest", {"ok": False})],
            (choices.Outcome.BUILD_FAIL, "base_not_allowed"),
            1,
        ),
        (
            [build_context.AllowedBasesChangedError("allowlist moved")],
            (choices.Outcome.INFRA_ERROR, "allowed_bases_changed"),
            1,
        ),
        (
            [BuildOutcome(ok=False, infra_suspected=True), BuildOutcome(ok=False, infra_suspected=True)],
            (choices.Outcome.INFRA_ERROR, "build_infra"),
            2,
        ),
        ([BuildOutcome(ok=False, log_tail="SyntaxError")], (choices.Outcome.BUILD_FAIL, ""), 1),
    ],
)
def test_build_seam_outcomes(golden: Any, builds: list[Any], outcome: tuple[str, str], calls: int) -> None:
    runtime = ScriptedRuntime(builds=builds)
    row = suite_mod.evaluate(make_run(golden, level="build"), suite_of(golden), runtime=runtime)
    assert suite_mod.outcome_of(row) == outcome
    assert runtime.build_calls == calls
    assert row.result["measured"] is False


def test_infra_suspected_build_is_retried_once_then_measured(golden: Any) -> None:
    runtime = ScriptedRuntime(builds=[BuildOutcome(ok=False, infra_suspected=True), None])
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden), runtime=runtime)
    assert row.status == choices.EvaluationStatus.MEASURED
    assert [a["ok"] for a in row.stages["build"]["detail"]["attempts"]] == [False, True]


def test_lock_incomplete_build_failure_is_never_the_models(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        suite_mod._Evaluation,
        "_locks_incomplete",
        lambda self: [{"lock": "backend/requirements.lock", "status": "incomplete"}],
    )
    runtime = ScriptedRuntime(builds=[BuildOutcome(ok=False, log_tail="pip: hashes required")])
    row = suite_mod.evaluate(make_run(golden, level="build"), suite_of(golden), runtime=runtime)
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, "lock_incomplete")


# --- lock step ---------------------------------------------------------------------------------------

LOCK = b"flask==3.1.0 \\\n    --hash=sha256:" + b"a" * 64 + b"\n"


class FakeLockRunner:
    def __init__(self, error: str | None = None) -> None:
        self.calls = 0
        self.error = error

    def run(self, image: str, files: Any, script: str, outputs: Any) -> dict[str, bytes]:
        self.calls += 1
        if self.error:
            raise build_locks.LockError(self.error)
        return dict.fromkeys(outputs, LOCK)


def _tree_with_manifest(lock: bytes | None = None) -> dict[str, tuple[bytes, int]]:
    tree = kit_tree()
    tree["backend/requirements.txt"] = (b"flask\n", 0o644)
    if lock is not None:
        tree["backend/requirements.lock"] = (lock, 0o644)
    return tree


def test_platform_lock_pins_installs_and_reevaluation_never_reresolves(golden: Any) -> None:
    run = make_run(golden, _tree_with_manifest())
    runner = FakeLockRunner()
    resolved = lock_mod.resolve_lock(run, runner=runner)
    assert run.lock_source == choices.LockSource.PLATFORM
    assert run.registry_snapshot_at is not None
    assert list(resolved.files) == ["backend/requirements.lock"]
    assert resolved.ecosystems["pip"][0].name == "flask"
    assert resolved.ecosystems["pip"][0].hash
    again = lock_mod.resolve_lock(run, runner=runner)
    assert runner.calls == 1
    assert again == resolved


def test_complete_model_lock_is_used_as_written(golden: Any) -> None:
    run = make_run(golden, _tree_with_manifest(LOCK))
    runner = FakeLockRunner()
    resolved = lock_mod.resolve_lock(run, runner=runner)
    assert runner.calls == 0
    assert run.lock_source == choices.LockSource.MODEL
    assert resolved.files == {}


def test_lock_unresolvable_is_build_fail(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        build_locks,
        "DockerLockRunner",
        lambda: FakeLockRunner("No solution found when resolving: flask>=99"),
    )
    row = suite_mod.evaluate(make_run(golden, _tree_with_manifest()), suite_of(golden))
    assert suite_mod.outcome_of(row) == (choices.Outcome.BUILD_FAIL, "lock_unresolvable")
    assert "build" not in row.stages


def test_registry_outage_is_infra_error(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_locks, "DockerLockRunner", lambda: FakeLockRunner("Connection refused (depcache)"))
    row = suite_mod.evaluate(make_run(golden, _tree_with_manifest()), suite_of(golden))
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, "lock_infra")


# --- eval stage --------------------------------------------------------------------------------------


def claim(run: GenerationRun) -> GenerationRun:
    GenerationRun.objects.filter(pk=run.pk).update(
        status=choices.RunStatus.RUNNING,
        phase=choices.RunPhase.EVALUATING,
        lease_owner="ex-wp09",
        attempt=1,
        lease_expires_at=timezone.now() + timezone.timedelta(minutes=10),
    )
    return GenerationRun.objects.get(pk=run.pk)


def test_run_eval_stage_sets_outcome_notes_and_the_metrics_mirror(golden: Any) -> None:
    run = claim(make_run(golden, level="functional"))
    suite_mod.run_eval_stage(run)
    run.refresh_from_db()
    assert run.status == choices.RunStatus.COMPLETED
    assert run.outcome == choices.Outcome.TESTED
    mirror = run.metrics["eval"]
    assert mirror["measured"] is True
    assert mirror["agent_pass_rate"] == 1.0
    codes = {note["code"] for note in run.validity["notes"]}
    assert "lock_source:model" in codes
    assert any(code.startswith("adherence:") for code in codes)


def test_run_eval_stage_fills_the_pending_reevaluation_row(golden: Any) -> None:
    run = make_run(golden, level="functional")
    pending = RunEvaluation.objects.create(
        run=run,
        eval_suite=suite_of(golden),
        eval_level="functional",
        status=choices.EvaluationStatus.PENDING,
    )
    GenerationRun.objects.filter(pk=run.pk).update(
        execution={
            **run.execution,
            "reeval": True,
            "reeval_prev_status": "completed",
            "reeval_evaluation_id": pending.pk,
            "reeval_suite": suite_of(golden).pinned_ref,
        },
    )
    run = claim(run)
    suite_mod.run_eval_stage(run)
    assert RunEvaluation.objects.filter(run=run).count() == 1
    pending.refresh_from_db()
    assert pending.status == choices.EvaluationStatus.MEASURED


def test_run_eval_stage_dispatches_slot_runs(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.generation.slot import evaluate as slot_evaluate
    from backend.generation.slot import validate as slot_validate
    from backend.generation.testing.factories import GenerationRunFactory
    from backend.generation.testing.fake_eval import FakeEvaluator

    fake = FakeEvaluator()
    calls: list[str] = []
    hooks: list[str] = []

    def evaluate_variant(run: GenerationRun, *, mode: str | None = None) -> RunEvaluation:
        calls.append(str(run.pk))
        return fake.evaluate(run, suite_of(golden))

    monkeypatch.setattr(slot_evaluate, "evaluate_variant", evaluate_variant)
    monkeypatch.setattr(slot_evaluate, "slot_pass_rate", lambda evaluation, target: 0.5)
    monkeypatch.setattr(slot_validate, "on_reference_evaluated", lambda run: hooks.append(str(run.pk)))
    variant = GenerationRunFactory(slot_single_shot=True, tree_hash="b" * 64)
    variant = claim(variant)
    suite_mod.run_eval_stage(variant)
    variant.refresh_from_db()
    assert calls == [str(variant.pk)]
    assert variant.outcome == choices.Outcome.TESTED
    assert variant.metrics["eval"]["slot_pass_rate"] == 0.5
    assert hooks == []
    reference = GenerationRunFactory(
        kind=choices.RunKind.REFERENCE,
        model=None,
        reference_of=choices.ReferenceOf.SLOT_ORIGINAL,
        slot_target=variant.slot_target,
        tree_hash="c" * 64,
    )
    suite_mod.run_eval_stage(claim(reference))
    assert hooks == [str(reference.pk)]


# --- baselines, oracles, conformance, retest -------------------------------------------------------


def test_baseline_subtraction(golden: Any) -> None:
    starter = golden.get("starter", "flask-react-minimal")
    version = golden.get("task", "crud_todo_list")
    ref = make_run(
        golden,
        kit_tree(LLMLAB_MUTANT="bare"),
        reference_of=choices.ReferenceOf.STARTER_BASELINE,
        starter=starter,
    )
    EvalBaseline.objects.create(
        starter=starter,
        task_version=version,
        auth_mode="contract_bearer",
        eval_suite=suite_of(golden),
        reference_run=ref,
        agent_pass_rate=0.25,
    )
    run = make_run(golden, level="functional", starter=starter)
    row = suite_mod.evaluate(run, suite_of(golden))
    assert row.result["metrics"]["baseline_pass_rate"] == 0.25
    assert row.result["metrics"]["over_baseline"] == pytest.approx(0.75)
    assert row.baseline_id is not None


def test_ensure_baseline_evaluates_the_bare_starter(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(baseline, "starter_tree", lambda starter: kit_tree(LLMLAB_MUTANT="bare"))
    starter = golden.get("starter", "flask-react-minimal")
    row = baseline.ensure_baseline(starter, golden.get("task", "crud_todo_list"), "contract_bearer", suite_of(golden))
    assert row is not None
    assert row.agent_pass_rate < 0.5
    assert (
        baseline.ensure_baseline(starter, golden.get("task", "crud_todo_list"), "contract_bearer", suite_of(golden))
        == row
    )


def test_a_starter_that_never_starts_gives_no_baseline(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(baseline, "starter_tree", lambda starter: kit_tree(LLMLAB_MUTANT="crash"))
    starter = golden.get("starter", "flask-react-minimal")
    assert (
        baseline.ensure_baseline(starter, golden.get("task", "crud_todo_list"), "contract_bearer", suite_of(golden))
        is None
    )
    assert not EvalBaseline.objects.exists()


def test_queue_oracle_runs_records_missing_references(golden: Any) -> None:
    version = golden.get("task", "crud_todo_list")
    queued = baseline.queue_oracle_runs(version, [golden.get("stack", "flask-react")], suite=suite_of(golden))
    assert queued == []
    version.refresh_from_db()
    assert version.oracle[golden.get("stack", "flask-react").pinned_ref]["status"] == "missing"


def test_conformance_coverage(golden: Any) -> None:
    from backend.generation.study.deps import _conformance_coverage
    from backend.generation.testing.factories import TaskSpecVersionFactory

    assert conformance.coverage() == {"fastapi": False, "flask": False}
    flask = golden.get("stack", "flask-react")
    for slug in conformance.KIT_TASKS:
        version = golden.rows["task"].get(slug) or TaskSpecVersionFactory(task__slug=slug)
        baseline._set_oracle(version, baseline.oracle_key(flask, kit=True), {"status": "pass", "run_id": None})
    assert conformance.coverage() == {"fastapi": False, "flask": True}
    assert _conformance_coverage() == {"fastapi": False, "flask": True}


def test_run_retests_writes_retest_rows_and_agreement(golden: Any) -> None:
    from backend.generation.testing.factories import StudyFactory

    study = StudyFactory()
    run = make_run(golden, level="functional")
    first = suite_mod.evaluate(run, suite_of(golden))
    GenerationRun.objects.filter(pk=run.pk).update(study=study, outcome=choices.Outcome.TESTED)
    created = retest.run_retests(study, fraction=1.0)
    assert len(created) == 1
    assert RunEvaluation.objects.get(pk=created[0]).retest_of_id == first.pk
    agreement = retest.retest_agreement(study)
    assert agreement["n"] == 1
    assert agreement["run_agreement"] == 1.0


@pytest.mark.parametrize(("vuln", "status"), [("", "not_exploited"), ("plaintext_password_db", "exploited")])
def test_plaintext_password_probe_reads_the_data_directory(golden: Any, vuln: str, status: str) -> None:
    from backend.generation.testing.factories import TaskSpecVersionFactory

    version = TaskSpecVersionFactory(content=conformance.kit_task_content())
    tree = kit_tree(LLMLAB_VULN=vuln) if vuln else kit_tree()
    run = baseline.reference_run(
        reference_of=choices.ReferenceOf.ORACLE,
        task_version=version,
        stack=golden.get("stack", "flask-react"),
        starter=None,
        auth_mode="contract_bearer",
        files=tree,
        suite=suite_of(golden),
    )
    row = suite_mod.evaluate(run, suite_of(golden), probes=["probe.kit.plaintext_password"])
    probes = row.result["probes"]
    assert [(p["kind"], p["status"]) for p in probes] == [("plaintext_password_db", status)]
    assert row.stages["tester"]["detail"]["probes"] == ["probe.kit.plaintext_password"]
