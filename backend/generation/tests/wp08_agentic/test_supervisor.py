"""Supervisor and idle detection (spec 6.5).

Invariant (13.4, WP-08): the idle timer never fires while a call is in flight or the CPU is busy. Checked on
the detector, on the supervisor loop, and end to end with the local sandbox plus a ``FakeGateway`` holding a
slow call.
"""

from __future__ import annotations

import threading
import time

import pytest

from backend.generation.agentic import runner
from backend.generation.agentic.sandbox import IdleDetector
from backend.generation.agentic.sandbox import SandboxHooks
from backend.generation.agentic.sandbox import Supervisor
from backend.generation.agentic.sandbox import time_split
from backend.generation.models import RunAttempt
from backend.generation.testing.fake_gateway import ScriptedReply


def test_idle_clock_needs_no_inflight_and_a_quiet_cpu() -> None:
    idle = IdleDetector(timeout_s=10)
    for _ in range(100):
        assert not idle.observe(1.0, inflight=1, last_call_end=None, cpu_fraction=0.0, workspace_changed=False)
    for _ in range(100):
        assert not idle.observe(1.0, inflight=0, last_call_end=None, cpu_fraction=0.5, workspace_changed=False)
    for _ in range(100):  # unknown facts are never idle
        assert not idle.observe(1.0, inflight=None, last_call_end=None, cpu_fraction=0.0, workspace_changed=False)
        assert not idle.observe(1.0, inflight=0, last_call_end=None, cpu_fraction=None, workspace_changed=False)
    fired = [
        idle.observe(1.0, inflight=0, last_call_end=None, cpu_fraction=0.01, workspace_changed=False)
        for _ in range(12)
    ]
    assert fired.index(True) == 9  # the 10th quiet second
    assert idle.fired


def test_idle_clock_resets_on_call_end_and_workspace_change() -> None:
    idle = IdleDetector(timeout_s=5)
    for _ in range(4):
        idle.observe(1.0, inflight=0, last_call_end=1.0, cpu_fraction=0.0, workspace_changed=False)
    assert not idle.observe(1.0, inflight=0, last_call_end=2.0, cpu_fraction=0.0, workspace_changed=False)
    assert idle.idle_s == 0.0
    for _ in range(4):
        idle.observe(1.0, inflight=0, last_call_end=2.0, cpu_fraction=0.0, workspace_changed=False)
    assert not idle.observe(1.0, inflight=0, last_call_end=2.0, cpu_fraction=0.0, workspace_changed=True)
    assert idle.idle_s == 0.0
    assert not IdleDetector(None).observe(
        99.0,
        inflight=0,
        last_call_end=None,
        cpu_fraction=0.0,
        workspace_changed=False,
    )


class ScriptedProcess:
    """A ``SupervisedProcess`` that exits after ``ticks`` polls (or when killed)."""

    def __init__(self, ticks: int, *, inflight: int = 0, cpu: float = 0.0, procs=()) -> None:
        self.ticks = ticks
        self.inflight = inflight
        self.cpu = cpu
        self.procs = list(procs)
        self.signals: list[str] = []

    def exited(self) -> bool:
        self.ticks -= 1
        return self.ticks < 0 or "KILL" in self.signals or "TERM" in self.signals

    def read_state(self):
        return {"inflight": self.inflight, "last_call_end": None}

    def cpu_fraction(self):
        return self.cpu

    def workspace_changed(self) -> bool:
        return False

    def processes(self):
        return self.procs

    def terminate(self) -> None:
        self.signals.append("TERM")

    def kill(self) -> None:
        self.signals.append("KILL")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def supervise(process, *, idle_timeout_s=5, hooks=None):
    clock = FakeClock()
    supervisor = Supervisor(
        process,
        hooks or SandboxHooks(),
        wall_clock_s=10_000,
        idle_timeout_s=idle_timeout_s,
        tick_s=1.0,
        clock=clock,
        sleep=clock.sleep,
    )
    return supervisor.run()


def test_supervisor_fires_idle_only_when_quiet() -> None:
    quiet = ScriptedProcess(100)
    assert supervise(quiet).idle_fired
    assert quiet.signals == ["TERM"]
    in_flight = ScriptedProcess(100, inflight=1)
    assert not supervise(in_flight).idle_fired
    assert in_flight.signals == []
    busy = ScriptedProcess(100, cpu=0.9)
    assert not supervise(busy).idle_fired


def test_supervisor_counts_install_time_and_stops_on_lost_ownership() -> None:
    report = supervise(ScriptedProcess(20, cpu=1.0, procs=["/usr/bin/npm", "node"]), idle_timeout_s=None)
    assert report.install_s >= 18
    lost = ScriptedProcess(100, cpu=1.0)
    report = supervise(lost, hooks=SandboxHooks(heartbeat=lambda: False, heartbeat_every_s=1.0))
    assert report.cancelled
    assert lost.signals == ["KILL"]


def test_time_split_from_call_durations() -> None:
    split = time_split([{"duration_s": 2.0}, {"duration_s": 3.0}], elapsed_s=20.0, install_s=4.0, idle_s=1.0)
    assert (split.llm_s, split.install_s, split.idle_s, split.tool_s) == (5.0, 4.0, 1.0, 10.0)


@pytest.mark.django_db
def test_idle_never_fires_while_a_slow_call_is_in_flight(agentic_env) -> None:
    hold = threading.Event()
    agentic_env.policy = agentic_env.policy.model_copy(
        update={"content": agentic_env.policy.content.model_copy(
            update={"time": agentic_env.policy.content.time.model_copy(update={"idle_timeout_s": 1})},
        )},
    )  # fmt: skip
    agentic_env.backend.replies = [ScriptedReply(content="slow", hold=hold)]
    agentic_env.script.call_gateway = True
    agentic_env.script.requests = [{"messages": [{"role": "user", "content": "go"}]}]
    agentic_env.script.files = {"backend/todos.py": b"def list_todos():\n    return []\n"}
    run = agentic_env.make_run()
    threading.Timer(3.0, hold.set).start()  # the call stays in flight for 3 s, three idle timeouts
    started = time.monotonic()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    assert time.monotonic() - started >= 2.5
    attempt = RunAttempt.objects.get(run=run, attempt=1)
    assert attempt.termination == "completed"
    assert "TERM" not in agentic_env.backend.processes[0].signals


@pytest.mark.django_db
def test_idle_fires_when_nothing_happens_and_the_workspace_is_kept(agentic_env) -> None:
    policy = agentic_env.policy
    content = policy.content.model_copy(update={"time": policy.content.time.model_copy(update={"idle_timeout_s": 1})})
    agentic_env.policy = policy.model_copy(update={"content": content})
    agentic_env.script.files = {"backend/todos.py": b"def list_todos():\n    return []\n"}
    agentic_env.script.sleep_s = 30
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    attempt = RunAttempt.objects.get(run=run, attempt=1)
    assert attempt.termination == "idle_timeout"
    run.refresh_from_db()
    assert run.validity["truncated"] is True
    assert run.phase == "eval_queued"  # the partial tree goes to evaluation (ITT)
    assert run.files.filter(path="backend/todos.py", origin="authored").exists()


@pytest.mark.django_db
def test_idle_does_not_fire_while_the_cpu_is_busy(agentic_env) -> None:
    policy = agentic_env.policy
    content = policy.content.model_copy(update={"time": policy.content.time.model_copy(update={"idle_timeout_s": 1})})
    agentic_env.policy = policy.model_copy(update={"content": content})
    agentic_env.backend.cpu = lambda: 0.8
    agentic_env.script.files = {"backend/todos.py": b"def list_todos():\n    return []\n"}
    agentic_env.script.sleep_s = 2.5
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    assert RunAttempt.objects.get(run=run, attempt=1).termination == "completed"
