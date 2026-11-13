"""App footprint: settings, bulk stop/remove, idle sweep, memory cap, image pruning."""

from __future__ import annotations

import time
from datetime import timedelta
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from backend.runtime.models import ContainerAction
from backend.runtime.models import ContainerInstance
from backend.runtime.tests.factories import ContainerActionFactory
from backend.runtime.tests.factories import ContainerInstanceFactory
from backend.system.models import FootprintSettings
from backend.system.services import footprint
from backend.users.tests.factories import UserFactory

_S = ContainerInstance.Status


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def staff_client(db):
    user = UserFactory(is_staff=True)
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def dispatched():
    """Record stop/remove dispatches instead of touching Docker."""
    calls: list[tuple[str, str]] = []
    with (
        patch(
            "backend.runtime.services.container_service.stop_instance",
            side_effect=lambda c, u: calls.append(("stop", c.name)),
        ),
        patch(
            "backend.runtime.services.container_service.remove_instance",
            side_effect=lambda c, u: calls.append(("remove", c.name)),
        ),
    ):
        yield calls


def _app(name: str, status=_S.RUNNING, *, age_minutes: int = 0, **kw) -> ContainerInstance:
    app = ContainerInstanceFactory(name=name, status=status, **kw)
    if age_minutes:
        ContainerInstance.objects.filter(pk=app.pk).update(created_at=timezone.now() - timedelta(minutes=age_minutes))
        app.refresh_from_db()
    return app


# ---------------------------------------------------------------------------
# API / settings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_footprint_endpoints_are_staff_only():
    user = UserFactory(is_staff=False)
    c = Client()
    c.force_login(user)
    assert c.get("/api/system/footprint").status_code == HTTPStatus.FORBIDDEN
    assert c.post("/api/system/footprint/stop-all").status_code == HTTPStatus.FORBIDDEN
    assert c.post("/api/system/footprint/prune").status_code == HTTPStatus.FORBIDDEN


@pytest.mark.django_db
def test_settings_patch_saves_and_validates(staff_client):
    resp = staff_client.patch(
        "/api/system/footprint/settings",
        data={"idle_stop_enabled": True, "idle_stop_minutes": 30, "app_mem_limit_mb": 256},
        content_type="application/json",
    )
    assert resp.status_code == HTTPStatus.OK, resp.content
    cfg = FootprintSettings.get()
    assert cfg.idle_stop_enabled is True
    assert cfg.idle_stop_minutes == 30
    assert footprint.app_mem_limit() == "256m"

    bad = staff_client.patch(
        "/api/system/footprint/settings",
        data={"app_mem_limit_mb": 10},
        content_type="application/json",
    )
    assert bad.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert FootprintSettings.get().app_mem_limit_mb == 256


@pytest.mark.django_db
def test_run_container_uses_configured_memory_limit():
    from backend.runtime.services import docker_manager

    FootprintSettings.objects.update_or_create(pk=1, defaults={"app_mem_limit_mb": 384})
    client = MagicMock()
    client.containers.run.return_value = SimpleNamespace(id="cid")
    with patch.object(docker_manager, "client", return_value=client):
        docker_manager.run_container("img", "llm-x", {}, {})
    assert client.containers.run.call_args.kwargs["mem_limit"] == "384m"


# ---------------------------------------------------------------------------
# Bulk actions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_stop_all_skips_busy_apps_and_analyzer_workspaces(dispatched):
    _app("llm-a")
    busy = _app("llm-b")
    _app("llm-c", status=_S.STOPPED)
    _app("analyzer-1", metadata={"kind": "analyzer-workspace"})
    ContainerActionFactory(container=busy, status=ContainerAction.Status.RUNNING)

    result = footprint.stop_all_apps()

    assert dispatched == [("stop", "llm-a")]
    assert result == {"stopping": 1, "skipped": 1}


@pytest.mark.django_db
def test_remove_apps_keeps_running_unless_asked(dispatched):
    _app("llm-run")
    _app("llm-stopped", status=_S.STOPPED)
    _app("llm-failed", status=_S.FAILED)
    _app("llm-gone", status=_S.REMOVED)

    footprint.remove_apps()
    assert sorted(dispatched) == [("remove", "llm-failed"), ("remove", "llm-stopped")]

    dispatched.clear()
    footprint.remove_apps(include_running=True)
    assert sorted(dispatched) == [("remove", "llm-failed"), ("remove", "llm-run"), ("remove", "llm-stopped")]


@pytest.mark.django_db
def test_stop_idle_uses_proxy_access_and_start_actions(dispatched):
    _app("llm-old", age_minutes=120)
    _app("llm-visited", age_minutes=120)
    restarted = _app("llm-restarted", age_minutes=120)
    _app("llm-new", age_minutes=5)
    footprint.record_app_access("llm-visited")
    ContainerActionFactory(
        container=restarted,
        action_type=ContainerAction.ActionType.START,
        status=ContainerAction.Status.COMPLETED,
        completed_at=timezone.now() - timedelta(minutes=10),
    )

    result = footprint.stop_idle_apps(idle_minutes=60)

    assert dispatched == [("stop", "llm-old")]
    assert result["idle"] == 1


@pytest.mark.django_db
def test_running_cap_stops_least_recently_used(dispatched):
    for i, age in enumerate((50, 40, 30, 20)):
        _app(f"llm-{i}", age_minutes=age)

    result = footprint.stop_idle_apps(idle_minutes=None, max_running=2)

    assert sorted(dispatched) == [("stop", "llm-0"), ("stop", "llm-1")]
    assert result["over_cap"] == 2


@pytest.mark.django_db
def test_sweep_respects_disabled_rules(dispatched):
    _app("llm-old", age_minutes=600)
    assert footprint.sweep() == {}
    assert dispatched == []

    FootprintSettings.objects.update_or_create(pk=1, defaults={"idle_stop_enabled": True, "idle_stop_minutes": 60})
    footprint.sweep()
    assert dispatched == [("stop", "llm-old")]
    assert FootprintSettings.get().last_sweep_at is not None


def test_record_app_access_is_throttled():
    footprint.record_app_access("llm-z")
    first = cache.get("system:app_last_access:llm-z")
    time.sleep(0.01)
    footprint.record_app_access("llm-z")
    assert cache.get("system:app_last_access:llm-z") == first


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------


def _image(tags: list[str], created: str):
    return SimpleNamespace(tags=tags, attrs={"Created": created}, id=f"sha256:{tags[0]}")


@pytest.mark.django_db
def test_prune_keeps_current_and_pinned_tool_images():
    _app("llm-live")
    images = [
        _image(["llmlab/agent-aider:aaaaaaaaaaaa"], "2026-11-01T00:00:00Z"),
        _image(["llmlab/agent-aider:bbbbbbbbbbbb"], "2026-11-03T00:00:00Z"),  # newest -> kept
        _image(["llmlab/agent-codex:cccccccccccc"], "2026-11-01T00:00:00Z"),  # pinned -> kept
        _image(["llmlab/agent-codex:dddddddddddd"], "2026-11-03T00:00:00Z"),  # newest -> kept
        _image(["llmlab/sample:abc-def"], "2026-11-01T00:00:00Z"),  # sample cache -> own eviction
        _image(["backend/llm-live:latest"], "2026-11-01T00:00:00Z"),  # app exists -> kept
        _image(["backend/llm-dead:latest"], "2026-11-01T00:00:00Z"),  # app gone -> removed
        _image(["node:22-alpine"], "2026-11-01T00:00:00Z"),  # base -> aggressive only
    ]
    client = MagicMock()
    client.images.list.return_value = images
    client.containers.list.return_value = []
    client.df.return_value = {"LayersSize": 100}
    client.api.prune_builds.return_value = {"SpaceReclaimed": 5}

    with (
        patch.object(footprint.docker_manager, "new_client", return_value=client),
        patch.object(footprint, "_pinned_tool_tags", return_value={"llmlab/agent-codex:cccccccccccc"}),
        patch("backend.runtime.services.sample_builds.cleanup_sample_images", return_value={"removed": 0}),
    ):
        result = footprint.prune_apps_daemon()
        removed = [c.args[0] for c in client.images.remove.call_args_list]
        assert sorted(removed) == ["backend/llm-dead:latest", "llmlab/agent-aider:aaaaaaaaaaaa"]
        assert all(c.kwargs == {"force": False} for c in client.images.remove.call_args_list)
        assert result["removed_images"] == 2

        client.images.remove.reset_mock()
        footprint.prune_apps_daemon(aggressive=True)
        removed = [c.args[0] for c in client.images.remove.call_args_list]
        assert "node:22-alpine" in removed
        assert "llmlab/agent-aider:bbbbbbbbbbbb" not in removed
        assert "llmlab/sample:abc-def" not in removed


@pytest.mark.django_db
def test_prune_removes_only_unmanaged_stopped_containers():
    managed = SimpleNamespace(name="llm-a", labels={"backend.managed": "true"}, remove=MagicMock())
    builder = SimpleNamespace(name="buildx_buildkit_x0", labels={}, remove=MagicMock())
    stray = SimpleNamespace(name="brave_x", labels={}, remove=MagicMock())
    client = MagicMock()
    client.images.list.return_value = []
    client.containers.list.return_value = [managed, builder, stray]
    client.df.return_value = {"LayersSize": 0}
    client.api.prune_builds.return_value = {}
    with (
        patch.object(footprint.docker_manager, "new_client", return_value=client),
        patch("backend.runtime.services.sample_builds.cleanup_sample_images", return_value={"removed": 0}),
    ):
        result = footprint.prune_apps_daemon()
    stray.remove.assert_called_once()
    managed.remove.assert_not_called()
    builder.remove.assert_not_called()
    assert result["removed_containers"] == 1
