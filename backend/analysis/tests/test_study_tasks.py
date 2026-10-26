"""Background study jobs: the report and launch Celery tasks and their Celery-or-thread dispatch."""

from __future__ import annotations

import threading

import pytest

from backend.analysis import tasks
from backend.analysis.research import study as study_mod


@pytest.fixture(autouse=True)
def _fresh_ping():
    saved = dict(tasks._ping_cache)
    tasks._ping_cache.update(at=-1e9, ok=False)
    yield
    tasks._ping_cache.update(saved)


@pytest.fixture
def threads(monkeypatch):
    started = []
    monkeypatch.setattr(tasks, "dispatch_in_thread", lambda target, *args, name=None: started.append((target, args)))
    return started


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(study_mod, "generate_report", lambda sid, rid: seen.append(("report", sid, rid)) or "ready")
    monkeypatch.setattr(study_mod, "submit_launch", lambda sid: seen.append(("launch", sid)))
    return seen


def _on_own_thread(target, args) -> None:
    """Run a dispatched target on its own thread, as in production (own DB connection)."""
    thread = threading.Thread(target=target, args=args)
    thread.start()
    thread.join(timeout=10)


def test_task_options():
    report = tasks.generate_study_report_task
    assert report.name == "analysis.generate_study_report"
    assert report.acks_late is True
    assert report.soft_time_limit == tasks.REPORT_SOFT_TIME_LIMIT
    assert report.time_limit == tasks.REPORT_TIME_LIMIT > report.soft_time_limit
    launch = tasks.launch_study_task
    assert launch.name == "analysis.launch_study"
    assert launch.acks_late is True
    assert launch.reject_on_worker_lost is True
    assert launch.time_limit == tasks.LAUNCH_TIME_LIMIT > launch.soft_time_limit == tasks.LAUNCH_SOFT_TIME_LIMIT


def test_tasks_call_the_study_jobs(calls):
    assert tasks.generate_study_report_task.run("s1", "r1") == "ready"
    tasks.launch_study_task.run("s2")
    assert calls == [("report", "s1", "r1"), ("launch", "s2")]


def test_dispatch_uses_celery_when_a_worker_answers(monkeypatch, threads):
    sent = []
    monkeypatch.setattr(tasks, "celery_available", lambda: True)
    monkeypatch.setattr(tasks.generate_study_report_task, "apply_async", lambda **kw: sent.append(("report", kw)))
    monkeypatch.setattr(tasks.launch_study_task, "apply_async", lambda **kw: sent.append(("launch", kw)))

    assert tasks.dispatch_study_report("s1", "r1") == "celery"
    assert tasks.dispatch_study_launch("s2") == "celery"

    assert sent == [("report", {"args": ["s1", "r1"]}), ("launch", {"args": ["s2"]})]
    assert threads == []


@pytest.mark.parametrize("worker", ["none", "broker_down"])
def test_dispatch_falls_back_to_a_thread(monkeypatch, threads, calls, worker):
    def broken(**kwargs):
        msg = "broker down"
        raise ConnectionError(msg)

    monkeypatch.setattr(tasks, "celery_available", lambda: worker == "broker_down")
    monkeypatch.setattr(tasks.generate_study_report_task, "apply_async", broken)
    monkeypatch.setattr(tasks.launch_study_task, "apply_async", broken)

    assert tasks.dispatch_study_report("s1", "r1") == "thread"
    assert tasks.dispatch_study_launch("s2") == "thread"

    for target, args in threads:
        _on_own_thread(target, args)
    assert calls == [("report", "s1", "r1"), ("launch", "s2")]


def test_a_crashing_study_thread_is_contained(monkeypatch, caplog):
    def crash(study_id):
        msg = "bug"
        raise RuntimeError(msg)

    monkeypatch.setattr(study_mod, "submit_launch", crash)
    _on_own_thread(tasks._study_job_in_thread, ("submit_launch", "s3"))  # logged, not raised
    assert "crashed on its thread" in caplog.text
