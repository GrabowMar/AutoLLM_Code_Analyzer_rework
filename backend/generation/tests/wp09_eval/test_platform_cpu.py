"""A step killed by SIGILL (a binary built for a newer x86-64 level than the host CPU) is never the model's.

Run f79e8f11 (flask-react, 2026-11-09): ``vite build`` died with "Illegal instruction (core dumped)", exit 132,
because the platform re-lock picked ``@rolldown/binding-linux-x64-gnu@1.2.18`` (x86-64-v3 mimalloc) on the Ivy
Bridge host. These tests pin: the classification (``infra_error:platform_cpu``, not measured, never 0) in the
lock, build, start and tester stages; the npm re-lock seeded with the tree's lock; the operator supersession
(``evaluation.supersede`` / ``manage.py reevaluate_platform_failures``). No live builds: fake logs only.
"""

from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from backend.generation.build import locks as build_locks
from backend.generation.contracts.outcome import is_usable
from backend.generation.evaluation import lock as lock_mod
from backend.generation.evaluation import suite as suite_mod
from backend.generation.evaluation import supersede as supersede_mod
from backend.generation.evaluation.runtime import BuildOutcome
from backend.generation.evaluation.runtime import LocalProcessRuntime
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import choices
from backend.generation.tests.wp09_eval.helpers import kit_tree
from backend.generation.tests.wp09_eval.test_suite import FakeLockRunner
from backend.generation.tests.wp09_eval.test_suite import ScriptedRuntime
from backend.generation.tests.wp09_eval.test_suite import claim
from backend.generation.tests.wp09_eval.test_suite import make_run
from backend.generation.tests.wp09_eval.test_suite import suite_of

pytestmark = pytest.mark.django_db

# The tail of the stored log of evaluation 592 (BuildKit, docker-container driver).
BUILDKIT_SIGILL = (
    "#16 [frontend 6/6] RUN cd frontend && npm run build\n#16 0.223 > vite build\n"
    "#16 0.573 Illegal instruction (core dumped)\n"
    '#16 ERROR: process "/bin/sh -c cd frontend && npm run build" did not complete successfully: exit code: 132\n'
    'ERROR: failed to build: failed to solve: process "/bin/sh -c cd frontend && npm run build" did not complete '
    "successfully: exit code: 132\n"
)


@pytest.fixture(autouse=True)
def _local_runtime(settings: Any) -> None:
    settings.EVAL_RUNTIME = "local"


@pytest.fixture
def golden() -> Any:
    from backend.generation.testing.golden_library import install_golden_library

    return install_golden_library()


# --- the detector -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "log",
    [
        BUILDKIT_SIGILL,
        "Illegal instruction (core dumped)",
        '#9 ERROR: process "/bin/sh -c npm run build" did not complete successfully: exit code: 132',
        "The command '/bin/sh -c npm run build' returned a non-zero code: 132",
        "SIGILL: illegal instruction\nPC=0x4f336f m=0 sigcode=2",
        "[ERROR] Worker (pid:12) was sent SIGILL!",
        "Fatal Python error: Illegal instruction",
        "error: process didn't exit successfully (signal: 4, SIGILL: illegal instruction)",
        # what bash, npm, node and Python print for a child killed by SIGILL (observed on the host)
        'bash: line 1: 420934 Illegal instruction        (core dumped) node -e "x"',
        "Illegal instruction (core dumped)\nnpm error code 132\nnpm error command failed",
        "npm error signal SIGILL",
        "Error: Command failed: esbuild --version\n{ killed: false, code: null, signal: 'SIGILL' }",
        "subprocess.CalledProcessError: Command '['node']' died with <Signals.SIGILL: 4>.",
        "Call to `setuptools.build_meta.build_wheel` failed (exit status: 132)",
    ],
)
def test_sigill_logs_are_platform_cpu_faults(log: str) -> None:
    assert suite_mod.platform_cpu_fault(log)


@pytest.mark.parametrize(
    "log",
    [
        "",
        None,
        "src/App.tsx(3,1): error TS2304: Cannot find name 'foo'.\nexit code: 2",
        "did not complete successfully: exit code: 1\nadded 39 packages: sigill-utils, mimalloc",
        "exit code: 1320",
        "exit code: 137",
        # the sample's own text in a log: a compiler quoting a source line, an exception message, a traceback
        'src/vm.ts(12,11): error TS2345: Argument\n12     throw new Error("Illegal instruction", opcode);',
        "Error: Illegal instruction\n    at Vm.step (/app/vm.js:12:11)",
        "ValueError: Illegal instruction",
        "Error: Illegal instruction at pc=0x10",
        '  File "/app/app.py", line 3, in <module>\n    signal.signal(signal.SIGILL, handler)',
        "process.kill(process.pid, 'SIGILL')",
        "an illegal instruction was decoded at 0x10",
    ],
)
def test_other_failures_are_not_platform_cpu_faults(log: str | None) -> None:
    assert not suite_mod.platform_cpu_fault(log)


def test_sigill_exit_codes_are_platform_cpu_faults() -> None:
    assert suite_mod.platform_cpu_fault("", exit_code=132)
    assert suite_mod.platform_cpu_fault(None, exit_code=-4)  # Popen.poll() of a SIGILL-killed process
    assert not suite_mod.platform_cpu_fault("", exit_code=1)
    assert not suite_mod.platform_cpu_fault("", exit_code=None)


# --- the build stage ----------------------------------------------------------------------------------


def _assert_platform_cpu(row: RunEvaluation, stage: str) -> None:
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, suite_mod.PLATFORM_CPU)
    assert row.status == choices.EvaluationStatus.ERROR
    assert row.stages[stage]["status"] == "error"
    assert row.stages[stage]["detail"]["reason"] == suite_mod.PLATFORM_CPU
    assert row.stages[stage]["detail"]["signal"] == "SIGILL"
    assert row.result["measured"] is False
    assert row.result["not_measured_reason"] == "infra:platform_cpu"
    assert row.result["metrics"]["agent_pass_rate"] is None
    assert row.result["checks"] == []
    assert suite_mod.stored_platform_cpu_fault(row) == stage


@pytest.mark.parametrize("infra_suspected", [False, True])
def test_sigill_build_is_infra_platform_cpu_never_build_fail(golden: Any, infra_suspected: bool) -> None:
    runtime = ScriptedRuntime(
        builds=[BuildOutcome(ok=False, infra_suspected=infra_suspected, log_tail=BUILDKIT_SIGILL)],
    )
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden), runtime=runtime)
    _assert_platform_cpu(row, "build")
    assert runtime.build_calls == 1  # deterministic: never retried
    assert row.stages["build"]["detail"]["attempts"] == [
        {"attempt": 1, "ok": False, "infra_suspected": infra_suspected, "sigill": True},
    ]
    assert "start" not in row.stages


def test_sigill_wins_over_lock_incomplete(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        suite_mod._Evaluation,
        "_locks_incomplete",
        lambda self: [{"lock": "x", "status": "incomplete"}],
    )
    runtime = ScriptedRuntime(builds=[BuildOutcome(ok=False, log_tail=BUILDKIT_SIGILL)])
    row = suite_mod.evaluate(make_run(golden, level="build"), suite_of(golden), runtime=runtime)
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, suite_mod.PLATFORM_CPU)


def test_a_sigill_after_an_infra_retry_is_still_platform_cpu(golden: Any) -> None:
    runtime = ScriptedRuntime(
        builds=[
            BuildOutcome(ok=False, infra_suspected=True, log_tail="ECONNRESET"),
            BuildOutcome(ok=False, log_tail=BUILDKIT_SIGILL),
        ],
    )
    row = suite_mod.evaluate(make_run(golden, level="build"), suite_of(golden), runtime=runtime)
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, suite_mod.PLATFORM_CPU)
    assert runtime.build_calls == 2


def test_an_ordinary_build_failure_stays_the_models(golden: Any) -> None:
    runtime = ScriptedRuntime(builds=[BuildOutcome(ok=False, log_tail="error TS2304: Cannot find name\nexit code: 2")])
    row = suite_mod.evaluate(make_run(golden, level="build"), suite_of(golden), runtime=runtime)
    assert suite_mod.outcome_of(row) == (choices.Outcome.BUILD_FAIL, "")
    assert suite_mod.stored_platform_cpu_fault(row) is None


# A SIGILL that an earlier step survived (a native probe with a fallback), then the sample's own tsc error.
SURVIVED_SIGILL_THEN_TSC = (
    "#8 [frontend 4/6] RUN node scripts/probe-native.js || echo falling back to wasm\n"
    "#8 0.311 Illegal instruction (core dumped)\n#8 0.312 falling back to wasm\n#8 DONE 0.4s\n"
    "#12 [frontend 6/6] RUN cd frontend && npm run build\n"
    "#12 1.802 src/App.tsx(3,1): error TS2304: Cannot find name 'foo'.\n"
    '#12 ERROR: process "/bin/sh -c cd frontend && npm run build" did not complete successfully: exit code: 2\n'
    'ERROR: failed to build: failed to solve: process "/bin/sh -c cd frontend && npm run build" did not complete '
    "successfully: exit code: 2\n"
)
# The failing step itself died of SIGILL, but a wrapper turned the 132 into 1.
WRAPPED_SIGILL = (
    "#12 [frontend 6/6] RUN cd frontend && (npm run build || exit 1)\n"
    "#12 0.573 Illegal instruction (core dumped)\n"
    '#12 ERROR: process "/bin/sh -c cd frontend && (npm run build || exit 1)" did not complete successfully: '
    "exit code: 1\n"
)
# The step's own "#12" lines were cut from the tail; BuildKit's error context block still holds them.
TRUNCATED_WRAPPED_SIGILL = (
    '#12 ERROR: process "/bin/sh -c npm run build || exit 1" did not complete successfully: exit code: 1\n'
    "------\n > [frontend 6/6] RUN npm run build || exit 1:\n0.223 > vite build\n"
    "0.573 Illegal instruction (core dumped)\n------\nDockerfile:20\n"
    'ERROR: failed to build: failed to solve: process "/bin/sh -c npm run build || exit 1" did not complete '
    "successfully: exit code: 1\n"
)
LEGACY_SIGILL = "Step 6/6 : RUN npm run build\n ---> Running in 1a2b\nIllegal instruction (core dumped)\n"
LEGACY_SIGILL += "The command '/bin/sh -c npm run build' returned a non-zero code: 1\n"


@pytest.mark.parametrize(
    ("log", "expected"),
    [
        (BUILDKIT_SIGILL, True),
        (WRAPPED_SIGILL, True),
        (LEGACY_SIGILL, True),
        (
            "Step 3/6 : RUN probe || true\nIllegal instruction\nStep 6/6 : RUN tsc\nerror TS2304\n"
            "The command '/bin/sh -c tsc' returned a non-zero code: 2",
            False,
        ),
        (SURVIVED_SIGILL_THEN_TSC, False),
        ("#12 1.0 Illegal instruction (core dumped)\n", True),  # no step status in the tail: the tail decides
        (TRUNCATED_WRAPPED_SIGILL, True),
    ],
    ids=["buildkit-132", "wrapped-exit-1", "legacy", "legacy-survived", "buildkit-survived", "no-status", "context"],
)
def test_the_failed_build_step_decides(log: str, expected: bool) -> None:
    assert suite_mod.build_platform_cpu_fault(log) is expected


# A build that timed out (no step status): a SIGILL an earlier step survived, then the sample's build hung.
TIMED_OUT_AFTER_SURVIVED_SIGILL = (
    "#8 [frontend 4/6] RUN node scripts/probe-native.js || true\n#8 0.311 Illegal instruction (core dumped)\n"
    "#8 DONE 0.4s\n#12 [frontend 6/6] RUN cd frontend && npm run build\n#12 0.5 > vite build --watch\n"
)
TIMED_OUT_IN_SIGILL_STEP = "#12 [frontend 6/6] RUN npm run build\n#12 0.573 Illegal instruction (core dumped)\n"


@pytest.mark.parametrize(
    ("log", "expected"),
    [(TIMED_OUT_AFTER_SURVIVED_SIGILL, False), (TIMED_OUT_IN_SIGILL_STEP, True)],
    ids=["earlier-step", "running-step"],
)
def test_a_timed_out_build_reads_only_the_step_still_running(log: str, expected: bool) -> None:
    assert suite_mod.build_platform_cpu_fault(log, timed_out=True) is expected


def test_a_sigill_an_earlier_step_survived_never_relabels_the_models_build_failure(golden: Any) -> None:
    runtime = ScriptedRuntime(builds=[BuildOutcome(ok=False, log_tail=SURVIVED_SIGILL_THEN_TSC)])
    row = suite_mod.evaluate(make_run(golden, level="build"), suite_of(golden), runtime=runtime)
    assert suite_mod.outcome_of(row) == (choices.Outcome.BUILD_FAIL, "")
    assert suite_mod.stored_platform_cpu_fault(row) is None
    assert "sigill" not in row.stages["build"]["detail"]["attempts"][0]


def test_platform_cpu_stages_record_the_host_cpu(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(suite_mod, "host_cpu", lambda: {"x86_64_level": "v2", "missing_v3": ["avx2", "bmi2"]})
    runtime = ScriptedRuntime(builds=[BuildOutcome(ok=False, log_tail=WRAPPED_SIGILL)])
    row = suite_mod.evaluate(make_run(golden, level="build"), suite_of(golden), runtime=runtime)
    _assert_platform_cpu(row, "build")
    assert row.stages["build"]["detail"]["host_cpu"]["missing_v3"] == ["avx2", "bmi2"]


def test_host_cpu_reads_the_x86_64_level() -> None:
    info = suite_mod.host_cpu()
    if info.get("x86_64_level") is None:
        pytest.skip("no x86 /proc/cpuinfo")
    assert info["x86_64_level"] in ("v1", "v2", "v3", "v4")
    assert (info["x86_64_level"] in ("v3", "v4")) == (not info["missing_v3"])


# --- lock, start and tester stages --------------------------------------------------------------------


def test_sigill_in_the_lock_step_is_platform_cpu(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        build_locks,
        "DockerLockRunner",
        lambda: FakeLockRunner("lock script failed in python (exit 132): Illegal instruction (core dumped)"),
    )
    tree = kit_tree()
    tree["backend/requirements.txt"] = (b"flask\n", 0o644)
    row = suite_mod.evaluate(make_run(golden, tree), suite_of(golden))
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, suite_mod.PLATFORM_CPU)
    assert row.stages["lock"]["detail"]["reason"] == suite_mod.PLATFORM_CPU


def test_a_resolver_killed_by_sigill_is_never_lock_unresolvable(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    long_tail = "\n".join(f"  building wheel {i}" for i in range(80))  # pushes the SIGILL out of the last 500
    message = (
        "x Failed to build `fastnative==1.0`\n  Call to `setuptools.build_meta.build_wheel` failed (exit status: 132)"
        f"\n{long_tail}\nNo solution found when resolving dependencies"
    )
    monkeypatch.setattr(build_locks, "DockerLockRunner", lambda: FakeLockRunner(message))
    tree = kit_tree()
    tree["backend/requirements.txt"] = (b"fastnative\n", 0o644)
    row = suite_mod.evaluate(make_run(golden, tree), suite_of(golden))
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, suite_mod.PLATFORM_CPU)
    assert row.stages["lock"]["status"] == "error"
    assert row.stages["lock"]["detail"]["signal"] == "SIGILL"


def test_an_unresolvable_requirement_stays_the_models(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    message = "No solution found when resolving dependencies: nosuchpkg was not found in the package registry"
    monkeypatch.setattr(build_locks, "DockerLockRunner", lambda: FakeLockRunner(message))
    tree = kit_tree()
    tree["backend/requirements.txt"] = (b"nosuchpkg\n", 0o644)
    row = suite_mod.evaluate(make_run(golden, tree), suite_of(golden))
    assert suite_mod.outcome_of(row) == (choices.Outcome.BUILD_FAIL, lock_mod.LOCK_UNRESOLVABLE)


class _NeverHealthy(LocalProcessRuntime):
    def __init__(self, logs: str, code: int | None) -> None:
        super().__init__()
        self._logs, self._code = logs, code

    def wait_healthy(self, handle: Any, path: str, timeout_s: float) -> bool:
        return False

    def logs(self, handle: Any) -> str:
        return self._logs

    def exit_code(self, handle: Any) -> int | None:
        return self._code


@pytest.mark.parametrize(("logs", "code"), [("Illegal instruction (core dumped)\n", 132), ("", 132), ("", -4)])
def test_sigill_on_start_is_platform_cpu_never_start_fail(golden: Any, logs: str, code: int) -> None:
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden), runtime=_NeverHealthy(logs, code))
    _assert_platform_cpu(row, "start")
    assert row.stages["start"]["detail"]["exit_code"] == code


def test_an_ordinary_start_failure_stays_start_fail(golden: Any) -> None:
    runtime = _NeverHealthy("Traceback: ModuleNotFoundError: No module named 'flask_cors'", 1)
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden), runtime=runtime)
    assert suite_mod.outcome_of(row) == (choices.Outcome.START_FAIL, "")


class _SigillDuringTester(ScriptedRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.tested = False

    def run_tester(self, handle: Any, plan: dict[str, Any], env: Any, timeout_s: float) -> dict[str, Any]:
        self.tested = True
        raw = super().run_tester(handle, plan, env, timeout_s)
        # The requests the dead worker would have served fail.
        raw["checks"] = [{**c, "status": "fail"} if i == 0 else c for i, c in enumerate(raw.get("checks") or [])]
        return raw

    def logs(self, handle: Any) -> str:
        return "[ERROR] Worker (pid:12) was sent SIGILL!\n" if self.tested else ""


def test_sigill_during_the_tester_discards_the_checks(golden: Any) -> None:
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden), runtime=_SigillDuringTester())
    _assert_platform_cpu(row, "tester")
    assert row.stages["tester"]["detail"]["discarded_checks"] > 0
    assert row.stages["start"]["status"] == "ok"


class _SigillLogsButAllPass(ScriptedRuntime):
    def logs(self, handle: Any) -> str:
        return "[ERROR] Worker (pid:12) was sent SIGILL!\n"


def test_sigill_in_the_logs_never_discards_a_clean_measured_result(golden: Any) -> None:
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden), runtime=_SigillLogsButAllPass())
    assert row.status == choices.EvaluationStatus.MEASURED
    assert row.result["checks"]
    assert all(c["status"] in ("pass", "skipped") for c in row.result["checks"])
    assert row.stages["tester"]["status"] == "ok"
    assert row.stages["tester"]["detail"]["sigill_in_app_logs"] is True
    assert suite_mod.outcome_of(row) == (choices.Outcome.TESTED, "")


# --- the run: not usable, attrition, ungraded ---------------------------------------------------------


def test_run_eval_stage_records_platform_cpu_as_unusable_and_ungraded(golden: Any) -> None:
    from backend.rankings.services.aggregator import _pass_rate

    run = claim(make_run(golden, level="functional"))
    runtime = ScriptedRuntime(builds=[BuildOutcome(ok=False, log_tail=BUILDKIT_SIGILL)])
    original = suite_mod.evaluate

    def evaluate(*args: Any, **kwargs: Any) -> RunEvaluation:
        return original(*args, **{**kwargs, "runtime": runtime})

    suite_mod.evaluate = evaluate  # type: ignore[assignment]
    try:
        suite_mod.run_eval_stage(run)
    finally:
        suite_mod.evaluate = original  # type: ignore[assignment]
    run.refresh_from_db()
    assert (run.outcome, run.outcome_reason) == (choices.Outcome.INFRA_ERROR, suite_mod.PLATFORM_CPU)
    assert run.usable is False
    assert is_usable(run.outcome) is False
    assert run.metrics["eval"]["measured"] is False
    assert run.metrics["eval"]["agent_pass_rate"] is None
    assert _pass_rate(run.outcome, run.usable, run.metrics) is None  # left out of the mean, never 0


# --- npm re-lock seeded with the tree's lock ----------------------------------------------------------

PACKAGE = {"name": "frontend", "dependencies": {"vite": "8.3.5"}}
TREE_LOCK = {
    "name": "frontend",
    "lockfileVersion": 3,
    "packages": {
        "": {"name": "frontend", "dependencies": {"vite": "^8.3.5"}},  # stale root spec: not covered
        "node_modules/vite": {"version": "8.3.5"},
        "node_modules/@rolldown/binding-linux-x64-gnu": {"version": "1.2.16"},
    },
}


class NpmRunner:
    """Records each call's inputs; with a seed it keeps the seeded versions, without one it 'resolves today'."""

    def __init__(self, *, seed_error: str | None = None, seed_uncovered: bool = False) -> None:
        self.calls: list[dict[str, bytes]] = []
        self.seed_error, self.seed_uncovered = seed_error, seed_uncovered

    def run(self, image: str, files: Any, script: str, outputs: Any) -> dict[str, bytes]:
        self.calls.append(dict(files))
        package = json.loads(files["package.json"])
        if "package-lock.json" in files:
            if self.seed_error:
                raise build_locks.LockError(self.seed_error)
            doc = json.loads(files["package-lock.json"])
            if not self.seed_uncovered:
                doc["packages"][""]["dependencies"] = package["dependencies"]
        else:
            doc = {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "frontend", "dependencies": package["dependencies"]},
                    "node_modules/vite": {"version": "8.3.5"},
                    "node_modules/@rolldown/binding-linux-x64-gnu": {"version": "1.2.18"},
                },
            }
        return dict.fromkeys(outputs, json.dumps(doc).encode())


def _npm_tree(lock: dict[str, Any] | None = TREE_LOCK) -> dict[str, tuple[bytes, int]]:
    tree = kit_tree()
    tree["frontend/package.json"] = (json.dumps(PACKAGE).encode(), 0o644)
    if lock is not None:
        tree["frontend/package-lock.json"] = (json.dumps(lock).encode(), 0o644)
    return tree


def _binding(resolved: Any) -> str:
    return next(p.version for p in resolved.ecosystems["npm"] if p.name == "@rolldown/binding-linux-x64-gnu")


def test_npm_relock_is_seeded_with_the_trees_lock(golden: Any) -> None:
    run = make_run(golden, _npm_tree())
    runner = NpmRunner()
    resolved = lock_mod.resolve_lock(run, runner=runner)
    assert run.lock_source == choices.LockSource.PLATFORM
    assert len(runner.calls) == 1
    assert json.loads(runner.calls[0]["package-lock.json"]) == TREE_LOCK
    assert _binding(resolved) == "1.2.16"  # the model's pin survives; only the root specs are rewritten
    assert list(resolved.files) == ["frontend/package-lock.json"]


@pytest.mark.parametrize(
    "runner",
    [NpmRunner(seed_error="npm error code EJSONPARSE"), NpmRunner(seed_uncovered=True)],
    ids=["seed-fails", "seed-uncovered"],
)
def test_an_unusable_seed_falls_back_to_the_manifest_only_resolve(golden: Any, runner: NpmRunner) -> None:
    run = make_run(golden, _npm_tree())
    resolved = lock_mod.resolve_lock(run, runner=runner)
    assert len(runner.calls) == 2
    assert "package-lock.json" not in runner.calls[1]
    assert _binding(resolved) == "1.2.18"


@pytest.mark.parametrize("lock", [None, {"lockfileVersion": 1, "dependencies": {}}], ids=["no-lock", "v1-lock"])
def test_no_usable_tree_lock_means_no_seed(golden: Any, lock: dict[str, Any] | None) -> None:
    runner = NpmRunner()
    lock_mod.resolve_lock(make_run(golden, _npm_tree(lock)), runner=runner)
    assert len(runner.calls) == 1
    assert "package-lock.json" not in runner.calls[0]


# --- superseding a platform-failed evaluation -------------------------------------------------------


def _legacy_platform_failed_run(golden: Any, tree: dict[str, tuple[bytes, int]] | None = None) -> tuple[Any, Any]:
    """A completed run scored ``build_fail`` from a SIGILL build, as the code before this fix recorded it."""
    run = make_run(golden, tree, level="functional")
    runtime = ScriptedRuntime(builds=[BuildOutcome(ok=False, log_tail=BUILDKIT_SIGILL)])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(suite_mod, "platform_cpu_fault", lambda *a, **k: False)  # the detector did not exist
        patch.setattr(suite_mod, "build_platform_cpu_fault", lambda *a, **k: False)
        row = suite_mod.evaluate(run, suite_of(golden), runtime=runtime)
    assert suite_mod.outcome_of(row) == (choices.Outcome.BUILD_FAIL, "")
    GenerationRun.objects.filter(pk=run.pk).update(
        status=choices.RunStatus.COMPLETED,
        outcome=choices.Outcome.BUILD_FAIL,
        outcome_reason="",
        usable=True,
        metrics={"eval": {"evaluation_id": row.pk, "measured": False}},
    )
    run.refresh_from_db()
    return run, row


def test_find_platform_failures_lists_the_legacy_build_fail(golden: Any) -> None:
    run, row = _legacy_platform_failed_run(golden)
    found = supersede_mod.find_platform_failures()
    assert [(c.run_id, c.evaluation_id, c.fault_stage, c.misclassified) for c in found] == [
        (str(run.pk), row.pk, "build", True),
    ]


def test_supersede_relocks_requeues_and_the_run_follows_the_new_evaluation(
    golden: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = kit_tree()
    tree["backend/requirements.txt"] = (b"flask\n", 0o644)
    monkeypatch.setattr(build_locks, "DockerLockRunner", FakeLockRunner)  # the lock step's container, faked
    run, old = _legacy_platform_failed_run(golden, tree)
    old_lock = run.resolved_lock_blob
    assert run.lock_source == choices.LockSource.PLATFORM

    pending = supersede_mod.supersede(run, relock=True, actor="test")
    run.refresh_from_db()
    assert pending.status == choices.EvaluationStatus.PENDING
    assert (run.status, run.phase) == (choices.RunStatus.RUNNING, choices.RunPhase.EVAL_QUEUED)
    assert (run.resolved_lock_blob, run.lock_source, run.registry_snapshot_at) == ("", "", None)
    [entry] = run.execution[supersede_mod.HISTORY_KEY]
    assert entry["superseded_evaluation"] == old.pk
    assert entry["superseded_outcome"] == choices.Outcome.BUILD_FAIL
    assert entry["evaluation"] == pending.pk
    assert entry["fault_stage"] == "build"
    assert entry["relock"]["previous_lock_blob"] == old_lock
    assert entry["by"] == "test"
    # The dropped lock (and its lock files) stays referenced for blob GC: 591/592 were built under it.
    from backend.generation import blobs

    refs = set(GenerationRun.iter_blob_refs())
    assert old_lock in refs
    assert set(blobs.get_json(old_lock)["files"].values()) <= refs
    note = next(n for n in run.validity["notes"] if n["code"] == "eval_superseded:platform_cpu")
    assert note["level"] == "warn"

    # The eval worker picks the pending row up: the run's outcome and metrics follow the new evaluation.
    run = claim(run)
    suite_mod.run_eval_stage(run)
    run.refresh_from_db()
    pending.refresh_from_db()
    old.refresh_from_db()
    assert pending.status == choices.EvaluationStatus.MEASURED
    assert old.stages["build"]["status"] == "fail"  # the superseded row is final and unchanged
    assert (run.outcome, run.usable) == (choices.Outcome.TESTED, True)
    assert run.metrics["eval"]["evaluation_id"] == pending.pk
    assert run.metrics["eval"]["measured"] is True
    assert run.status == choices.RunStatus.COMPLETED
    assert run.resolved_lock_blob
    assert run.lock_source == choices.LockSource.PLATFORM
    assert run.execution[supersede_mod.HISTORY_KEY][0]["evaluation"] == pending.pk
    codes = {n["code"] for n in run.validity["notes"]}
    assert "eval_superseded:platform_cpu" in codes


def test_supersede_refuses_a_run_without_a_platform_fault(golden: Any) -> None:
    run = make_run(golden, level="functional")
    GenerationRun.objects.filter(pk=run.pk).update(status=choices.RunStatus.COMPLETED, outcome="build_fail")
    run.refresh_from_db()
    with pytest.raises(supersede_mod.SupersedeRefusedError, match="no platform fault"):
        supersede_mod.supersede(run)
    assert not RunEvaluation.objects.filter(run=run).exists()


def test_supersede_never_drops_a_model_lock(golden: Any) -> None:
    run, _ = _legacy_platform_failed_run(golden)
    lock_mod.resolve_lock(run, runner=FakeLockRunner())  # kit tree: no manifest -> lock_source=model
    assert run.lock_source == choices.LockSource.MODEL
    with pytest.raises(supersede_mod.SupersedeRefusedError, match="platform lock"):
        supersede_mod.supersede(run, relock=True)
    run.refresh_from_db()
    assert run.resolved_lock_blob
    assert run.status == choices.RunStatus.COMPLETED
    assert supersede_mod.HISTORY_KEY not in (run.execution or {})


def test_command_lists_by_default_and_queues_with_apply(golden: Any) -> None:
    run, _ = _legacy_platform_failed_run(golden)
    out = StringIO()
    call_command("reevaluate_platform_failures", stdout=out)
    assert f"* run={run.pk}" in out.getvalue()
    assert "dry run" in out.getvalue()
    run.refresh_from_db()
    assert run.status == choices.RunStatus.COMPLETED
    with pytest.raises(CommandError):  # the only platform fault the scan detects
        call_command("reevaluate_platform_failures", str(run.pk), "--apply", "--reason", "other", stdout=StringIO())
    out = StringIO()
    call_command("reevaluate_platform_failures", str(run.pk), "--apply", "--by", "operator-a", stdout=out)
    assert "queued run=" in out.getvalue()
    run.refresh_from_db()
    assert run.phase == choices.RunPhase.EVAL_QUEUED
    [entry] = run.execution[supersede_mod.HISTORY_KEY]
    assert entry["by"] == "manage.py reevaluate_platform_failures (operator-a)"
    with pytest.raises(CommandError, match="refused"):
        call_command("reevaluate_platform_failures", str(run.pk), "--apply", stdout=StringIO(), stderr=StringIO())
