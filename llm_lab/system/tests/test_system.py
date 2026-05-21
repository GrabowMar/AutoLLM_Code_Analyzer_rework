"""Tests for the system monitoring app."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone

from llm_lab.analysis.models import AnalysisTask
from llm_lab.generation.models import GenerationJob
from llm_lab.generation.tests.factories import GenerationJobFactory
from llm_lab.users.tests.factories import UserFactory

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
def test_clear_stuck_analysis_updates_db(staff_client, db):
    client, _ = staff_client
    user = UserFactory()
    old_time = timezone.now() - timedelta(minutes=90)

    task = AnalysisTask.objects.create(
        name="stuck-task",
        status=AnalysisTask.Status.RUNNING,
        created_by=user,
    )
    AnalysisTask.objects.filter(pk=task.pk).update(updated_at=old_time)

    resp = client.post(
        "/api/system/maintenance/clear-stuck-analysis?older_than_minutes=60",
    )
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["updated"] >= 1

    task.refresh_from_db()
    assert task.status == AnalysisTask.Status.FAILED


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
def test_purge_orphan_containers_with_docker_error(staff_client):
    """When docker is unavailable, purge gracefully handles the error."""
    client, _ = staff_client
    with patch("docker.from_env", side_effect=Exception("docker unavailable")):
        resp = client.post("/api/system/maintenance/purge-orphan-containers")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["purged"] == 0
