"""Tests for the system monitoring app."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory
from backend.users.tests.factories import UserFactory

_FIVE_CLIENTS = 5
_FIFTY_PERCENT = 50.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def staff_client(db):
    user = UserFactory(is_staff=True)
    c = Client()
    c.force_login(user)
    return c, user


@pytest.fixture
def regular_client(db):
    user = UserFactory(is_staff=False)
    c = Client()
    c.force_login(user)
    return c, user


@pytest.fixture
def anon_client():
    return Client()


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_host_requires_staff_anon(anon_client):
    resp = anon_client.get("/api/system/host")
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.django_db
def test_host_requires_staff_non_staff(regular_client):
    client, _ = regular_client
    resp = client.get("/api/system/host")
    assert resp.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.django_db
def test_snapshot_requires_staff_non_staff(regular_client):
    client, _ = regular_client
    resp = client.get("/api/system/")
    assert resp.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.django_db
def test_maintenance_requires_staff_non_staff(regular_client):
    client, _ = regular_client
    resp = client.post("/api/system/maintenance/clear-caches")
    assert resp.status_code == HTTPStatus.FORBIDDEN


# ---------------------------------------------------------------------------
# Service layer tests (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_host_metrics_staff_ok(staff_client):
    client, _ = staff_client
    mock_cpu = 12.5
    mock_mem = MagicMock(
        total=8_000_000_000,
        used=4_000_000_000,
        available=4_000_000_000,
        percent=_FIFTY_PERCENT,
    )
    mock_load = (0.5, 0.4, 0.3)
    mock_partitions = [MagicMock(mountpoint="/", device="/dev/sda1", fstype="ext4")]
    mock_disk_usage = MagicMock(
        total=100_000_000_000,
        used=50_000_000_000,
        free=50_000_000_000,
        percent=_FIFTY_PERCENT,
    )

    with (
        patch("psutil.cpu_percent", return_value=mock_cpu),
        patch("psutil.virtual_memory", return_value=mock_mem),
        patch("psutil.getloadavg", return_value=mock_load),
        patch("psutil.cpu_count", return_value=4),
        patch("psutil.disk_partitions", return_value=mock_partitions),
        patch("psutil.disk_usage", return_value=mock_disk_usage),
        patch("psutil.boot_time", return_value=1_700_000_000.0),
    ):
        resp = client.get("/api/system/host")

    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["cpu_percent"] == mock_cpu
    assert data["memory"]["percent"] == _FIFTY_PERCENT
    assert len(data["disks"]) == 1


@pytest.mark.django_db
def test_containers_staff_ok(staff_client):
    client, _ = staff_client
    mock_container = MagicMock()
    mock_container.short_id = "abc123"
    mock_container.name = "test-container"
    mock_container.image.tags = ["nginx:latest"]
    mock_container.status = "running"
    mock_container.attrs = {
        "State": {"StartedAt": "2024-01-01T00:00:00Z", "Health": None},
    }

    with patch("docker.from_env") as mock_docker:
        mock_docker_client = MagicMock()
        mock_docker.return_value = mock_docker_client
        mock_docker_client.containers.list.return_value = [mock_container]
        resp = client.get("/api/system/containers")

    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["name"] == "test-container"


@pytest.mark.django_db
def test_redis_staff_ok(staff_client):
    client, _ = staff_client
    with patch("redis.from_url") as mock_redis:
        mock_r = MagicMock()
        mock_redis.return_value = mock_r
        mock_r.ping.return_value = True
        mock_r.info.return_value = {
            "connected_clients": _FIVE_CLIENTS,
            "used_memory_human": "1.5M",
            "total_commands_processed": 1000,
            "redis_version": "7.0.0",
        }
        resp = client.get("/api/system/redis")

    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["reachable"] is True
    assert data["connected_clients"] == _FIVE_CLIENTS


@pytest.mark.django_db
def test_celery_staff_ok(staff_client):
    client, _ = staff_client
    with (
        patch("celery.current_app") as mock_app,
        patch("redis.from_url") as mock_redis,
    ):
        mock_inspector = MagicMock()
        mock_app.control.inspect.return_value = mock_inspector
        mock_inspector.active.return_value = {"worker1": []}
        mock_inspector.scheduled.return_value = {"worker1": []}
        mock_inspector.reserved.return_value = {"worker1": []}
        mock_r = MagicMock()
        mock_redis.return_value = mock_r
        mock_r.llen.return_value = 0
        resp = client.get("/api/system/celery")

    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert "worker_count" in data


@pytest.mark.django_db
def test_db_stats_staff_ok(staff_client):
    client, _ = staff_client
    resp = client.get("/api/system/db")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert "stats" in data
    assert "top_tables_by_size" in data


@pytest.mark.django_db
def test_app_stats_staff_ok(staff_client):
    client, _ = staff_client
    resp = client.get("/api/system/app-stats")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert "analysis_tasks" in data
    assert "generation_jobs" in data
    assert "reports" in data
    assert "container_instances" in data


# ---------------------------------------------------------------------------
# Maintenance actions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_clear_caches_staff_ok(staff_client):
    client, _ = staff_client
    resp = client.post("/api/system/maintenance/clear-caches")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["success"] is True


@pytest.mark.django_db
def test_clear_stuck_analysis_delegates_to_the_reconciler(staff_client, db):
    client, _ = staff_client
    user = UserFactory()
    old_time = timezone.now() - timedelta(minutes=90)
    # A long but healthy run must not be failed just for being old.
    task = AnalysisRun.objects.create(name="long-run", status=AnalysisRun.Status.RUNNING, created_by=user)
    AnalysisRun.objects.filter(pk=task.pk).update(updated_at=old_time)
    calls = []

    def reconcile(now=None):
        calls.append(now)
        return {
            "requeued": ["r1"],
            "failed": ["r2"],
            "cancelled": [],
            "redispatched": [],
            "reaped": {"containers": 2, "volumes": 0, "errors": 5},
            "reap_error": "",
        }

    with patch("backend.analysis.engine.executor.reconcile", side_effect=reconcile):
        resp = client.post("/api/system/maintenance/clear-stuck-analysis?older_than_minutes=60")

    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert calls == [None]
    assert data["updated"] == 4
    # error counters are not actions taken
    assert data["details"]["reaped"] == {"containers": 2, "volumes": 0, "errors": 5}
    task.refresh_from_db()
    assert task.status == AnalysisRun.Status.RUNNING


@pytest.mark.django_db
def test_clear_stuck_reports_endpoint(staff_client):
    from backend.reports.models import Report

    client, _ = staff_client
    report = Report.objects.create(report_type="comprehensive", title="x", status=Report.Status.GENERATING)
    Report.objects.filter(pk=report.pk).update(created_at=timezone.now() - timedelta(hours=3))

    resp = client.post("/api/system/maintenance/clear-stuck-reports?older_than_minutes=60")

    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["updated"] == 1
    report.refresh_from_db()
    assert report.status == Report.Status.FAILED


@pytest.mark.django_db
def test_clear_stuck_generation_updates_db(staff_client, db):
    client, _ = staff_client
    old_time = timezone.now() - timedelta(minutes=90)

    job = GenerationJobFactory(status=GenerationJob.Status.RUNNING)
    GenerationJob.objects.filter(pk=job.pk).update(updated_at=old_time)

    resp = client.post(
        "/api/system/maintenance/clear-stuck-generation?older_than_minutes=60",
    )
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["updated"] >= 1

    job.refresh_from_db()
    assert job.status == GenerationJob.Status.FAILED


@pytest.mark.django_db
def test_clear_stuck_generation_spares_executor_jobs_with_a_live_lease():
    """Executor-owned jobs wait for hours without writing their row: the lease, not age, says they are live."""
    from backend.system.services.maintenance import clear_stuck_generation_jobs

    now = timezone.now()
    old = now - timedelta(minutes=90)
    leased = GenerationJobFactory(status=GenerationJob.Status.RUNNING, mode="agent")
    waiting = GenerationJobFactory(status=GenerationJob.Status.PENDING, mode="agent")
    expired = GenerationJobFactory(status=GenerationJob.Status.RUNNING, mode="agent")  # executor died long ago
    GenerationJob.objects.filter(pk=leased.pk).update(
        queued_at=old,
        updated_at=old,
        lease_owner="exec-1",
        lease_expires_at=now + timedelta(seconds=60),
    )
    GenerationJob.objects.filter(pk=waiting.pk).update(queued_at=old, updated_at=old)
    GenerationJob.objects.filter(pk=expired.pk).update(
        queued_at=old,
        updated_at=old,
        lease_owner="exec-0",
        lease_expires_at=old,
    )

    assert clear_stuck_generation_jobs(60) == 1

    for job in (leased, waiting, expired):
        job.refresh_from_db()
    assert leased.status == GenerationJob.Status.RUNNING
    assert waiting.status == GenerationJob.Status.PENDING
    assert expired.status == GenerationJob.Status.FAILED
    assert (expired.lease_owner, expired.lease_expires_at) == ("", None)


def _container(cid: str):
    container = MagicMock()
    container.id = cid
    container.short_id = cid[:10]
    return container


@pytest.mark.django_db
def test_purge_orphan_containers_with_docker_error_changes_nothing(staff_client):
    """An unreachable daemon says nothing about what exists: abort, do not mark everything removed."""
    from backend.runtime.models import ContainerInstance
    from backend.runtime.tests.factories import ContainerInstanceFactory

    client, _ = staff_client
    instance = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING, container_id="deadbeef0001")
    with (
        patch(
            "backend.runtime.services.docker_manager.new_client",
            side_effect=Exception("docker unavailable"),
        ),
        patch("backend.analysis.engine.sandbox.reap") as reap,
        patch("backend.generation.services.agent.sandbox.reap_orphans") as reap_agents,
    ):
        resp = client.post("/api/system/maintenance/purge-orphan-containers")

    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["purged"] == 0
    assert data["details"]["listing_ok"] is False
    assert "docker listing failed" in data["details"]["errors"][0]
    reap.assert_not_called()
    reap_agents.assert_not_called()
    instance.refresh_from_db()
    assert instance.status == ContainerInstance.Status.RUNNING


@pytest.mark.django_db
def test_purge_orphan_containers_reaps_analysis_agent_and_legacy_leftovers(staff_client):
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.runtime.models import ContainerInstance
    from backend.runtime.tests.factories import ContainerInstanceFactory

    client, _ = staff_client
    present = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING, container_id="aaaaaaaaaaaa")
    gone = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING, container_id="bbbbbbbbbbbb")
    workspace_row = ContainerInstanceFactory(
        status=ContainerInstance.Status.STOPPED,
        name="analyzer-0123456789",
        metadata={"kind": "analyzer-workspace"},
    )
    live = AnalysisRunFactory(status=AnalysisRun.Status.RUNNING)
    AnalysisRunFactory(status=AnalysisRun.Status.COMPLETED)

    docker = MagicMock()
    legacy = _container("cccccccccccc")
    docker.containers.list.side_effect = lambda all=True, filters=None: (  # noqa: A006
        [legacy] if filters else [_container("aaaaaaaaaaaa"), legacy]
    )
    with (
        patch("backend.runtime.services.docker_manager.new_client", return_value=docker),
        patch(
            "backend.analysis.engine.sandbox.reap",
            return_value={"containers": 2, "volumes": 1},
        ) as reap,
        patch("backend.generation.services.agent.sandbox.reap_orphans", return_value=1) as reap_agents,
    ):
        resp = client.post("/api/system/maintenance/purge-orphan-containers")

    data = resp.json()
    details = data["details"]
    assert details["listing_ok"] is True
    assert details["marked_removed"] == 1
    assert details["agent_reaped"] == 1
    assert details["analysis_reaped"] == {"containers": 2, "volumes": 1}
    assert details["legacy_workspaces_removed"] == 1
    assert data["purged"] == 1 + 1 + 3 + 1
    # only queued/running runs are live; everything else may be reaped
    assert reap.call_args.kwargs["live_run_ids"] == {str(live.id)}
    assert reap.call_args.kwargs["older_than_s"] > 0
    assert reap_agents.call_args.args == (docker,)
    legacy.remove.assert_called_once_with(force=True)
    present.refresh_from_db()
    gone.refresh_from_db()
    workspace_row.refresh_from_db()
    assert present.status == ContainerInstance.Status.RUNNING
    assert gone.status == ContainerInstance.Status.REMOVED
    assert workspace_row.status == ContainerInstance.Status.REMOVED


@pytest.mark.django_db
def test_one_failing_reaper_does_not_stop_the_others(staff_client):
    client, _ = staff_client
    docker = MagicMock()
    docker.containers.list.return_value = []
    with (
        patch("backend.runtime.services.docker_manager.new_client", return_value=docker),
        patch("backend.analysis.engine.sandbox.reap", side_effect=NotImplementedError),
        patch("backend.generation.services.agent.sandbox.reap_orphans", return_value=2),
    ):
        data = client.post("/api/system/maintenance/purge-orphan-containers").json()

    assert data["purged"] == 2
    assert any("analysis reap failed" in e for e in data["details"]["errors"])


@pytest.mark.django_db
def test_app_stats_split_analysis_by_purpose(staff_client):
    from django.core.cache import cache

    from backend.analysis.tests.factories import AnalysisRunFactory

    cache.clear()
    client, _ = staff_client
    AnalysisRunFactory(purpose=AnalysisRun.Purpose.CANARY)
    AnalysisRunFactory(purpose=AnalysisRun.Purpose.ADHOC)
    stale = AnalysisRunFactory(status=AnalysisRun.Status.RUNNING)
    AnalysisRun.objects.filter(pk=stale.pk).update(heartbeat_at=timezone.now() - timedelta(hours=1))

    data = client.get("/api/system/app-stats").json()

    assert data["analysis_tasks"]["by_purpose"] == {"canary": 1, "adhoc": 2}
    assert data["analysis_tasks"]["stale_heartbeats"] == 1
    assert "analysis_studies" in data
