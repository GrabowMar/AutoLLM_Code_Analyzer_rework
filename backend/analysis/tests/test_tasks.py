"""Celery tasks and the Celery-or-thread dispatch (backend/analysis/tasks.py)."""

from __future__ import annotations

import threading

import pytest

from backend.analysis import tasks
from backend.analysis.engine import executor
from backend.analysis.engine import sandbox


@pytest.fixture(autouse=True)
def _fresh_ping():
    saved = dict(tasks._ping_cache)
    tasks._ping_cache.update(at=-1e9, ok=False)
    yield
    tasks._ping_cache.update(saved)


def _in_thread(target, args) -> None:
    """Run a dispatched target on its own thread, as in production (own DB connection)."""
    thread = threading.Thread(target=target, args=args)
    thread.start()
    thread.join(timeout=10)


@pytest.fixture
def threads(monkeypatch):
    started = []
    monkeypatch.setattr(tasks, "dispatch_in_thread", lambda target, *args, name=None: started.append((target, args)))
    return started


def test_process_run_task_options():
    task = tasks.process_run_task
    assert task.name == "analysis.process_run"
    assert task.acks_late is True
    assert task.reject_on_worker_lost is True
    assert task.soft_time_limit >= 4 * 3600
    assert task.time_limit > task.soft_time_limit
    assert task.ignore_result is True
    assert tasks.reconcile_task.name == "analysis.reconcile"
    assert tasks.build_image_task.name == "analysis.build_image"


def test_tasks_call_the_engine(monkeypatch):
    calls = []
    monkeypatch.setattr(executor, "process_run", lambda run_id: calls.append(("run", run_id)))
    monkeypatch.setattr(executor, "reconcile", lambda: {"requeued": []})
    monkeypatch.setattr(sandbox, "build_image", lambda: calls.append(("build",)))
    tasks.process_run_task.run("r1")
    assert tasks.reconcile_task.run() == {"requeued": []}
    tasks.build_image_task.run()
    assert calls == [("run", "r1"), ("build",)]


def test_dispatch_uses_celery_when_a_worker_answers(monkeypatch, threads):
    sent = []
    monkeypatch.setattr(tasks, "celery_available", lambda: True)
    monkeypatch.setattr(tasks.process_run_task, "apply_async", lambda **kwargs: sent.append(kwargs))
    assert tasks.dispatch_process_run("r1", countdown=20) == "celery"
    assert sent == [{"args": ["r1"], "countdown": 20}]
    assert threads == []


def test_dispatch_falls_back_to_a_thread_when_the_broker_fails(monkeypatch, threads):
    def broken(**kwargs):
        msg = "broker down"
        raise ConnectionError(msg)

    monkeypatch.setattr(tasks, "celery_available", lambda: True)
    monkeypatch.setattr(tasks.process_run_task, "apply_async", broken)
    assert tasks.dispatch_process_run("r1") == "thread"
    assert threads[0][1] == ("r1", 0)


def test_thread_dispatch_runs_the_executor(monkeypatch, threads):
    calls = []
    monkeypatch.setattr(tasks, "celery_available", lambda: False)
    monkeypatch.setattr(executor, "process_run", calls.append)
    assert tasks.dispatch_process_run("r2", countdown=0) == "thread"
    target, args = threads[0]
    _in_thread(target, args)
    assert calls == ["r2"]


def test_a_crashing_run_thread_is_contained(monkeypatch):
    def crash(run_id):
        msg = "bug"
        raise RuntimeError(msg)

    monkeypatch.setattr(executor, "process_run", crash)
    _in_thread(tasks._run_in_thread, ("r3", 0))  # logged, not raised


def test_celery_ping_is_cached_and_can_be_disabled(monkeypatch, settings):
    pings = []

    class Control:
        def ping(self, timeout):
            pings.append(timeout)
            return [{"worker": "pong"}]

    class App:
        control = Control()

    monkeypatch.setattr("celery.current_app", App())
    settings.ANALYSIS_USE_CELERY = True
    assert tasks.celery_available()
    assert tasks.celery_available()
    assert pings == [1.0]
    settings.ANALYSIS_USE_CELERY = False
    assert not tasks.celery_available()


def test_image_build_dispatch(monkeypatch, threads):
    monkeypatch.setattr(tasks, "celery_available", lambda: False)
    assert tasks.dispatch_image_build() == "thread"
    built = []
    monkeypatch.setattr(sandbox, "build_image", lambda: built.append(1))
    target, args = threads[0]
    _in_thread(target, args)
    assert built == [1]
