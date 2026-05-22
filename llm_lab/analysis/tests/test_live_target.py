"""Tests for Phase 5: Live URL-targeted analysis integration."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from llm_lab.analysis.services.analysis_service import AnalysisService
from llm_lab.analysis.services.base import AnalyzerOutput
from llm_lab.analysis.services.live_target import LIVE_TARGET_PORT_MAX
from llm_lab.analysis.services.live_target import LIVE_TARGET_PORT_MIN
from llm_lab.analysis.services.live_target import prepare_live_target
from llm_lab.analysis.services.live_target import teardown_live_target
from llm_lab.analysis.services.live_target import validate_live_target_url
from llm_lab.analysis.tests.factories import AnalysisTaskFactory
from llm_lab.generation.tests.factories import GenerationJobFactory
from llm_lab.runtime.models import ContainerInstance
from llm_lab.runtime.tests.factories import ContainerInstanceFactory
from llm_lab.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from llm_lab.analysis.models import AnalysisTask

pytestmark = pytest.mark.django_db(transaction=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CS = "llm_lab.runtime.services.container_service"


def _make_task(
    *,
    live_target: bool = True,
    extra_config: dict | None = None,
) -> AnalysisTask:
    """Create an AnalysisTask with minimal valid configuration."""
    user = UserFactory()
    job = GenerationJobFactory(
        created_by=user,
        status="completed",
        result_data={
            "backend_code": "print('hello')",
            "frontend_code": "",
        },
    )
    config: dict = {
        "analyzers": ["bandit"],
        "settings": {},
        "live_target": live_target,
        "generation_job_id": str(job.id),
    }
    if extra_config:
        config.update(extra_config)
    return AnalysisTaskFactory(
        source_code={"backend": "import os", "frontend": ""},
        configuration=config,
        created_by=user,
    )


def _mock_analyzer(
    name: str = "bandit",
    output: AnalyzerOutput | None = None,
) -> MagicMock:
    """Return a mock analyzer instance."""
    analyzer = MagicMock()
    analyzer.name = name
    analyzer.analyzer_type = "static"
    analyzer.analyze.return_value = output or AnalyzerOutput(
        findings=[],
        summary={"total_issues": 0},
        raw_output={"tool": "mock"},
    )
    return analyzer


def _running_instance() -> ContainerInstance:
    """Create a ContainerInstance in RUNNING state."""
    return ContainerInstanceFactory(
        status=ContainerInstance.Status.RUNNING,
        app_port=7100,
    )


# ---------------------------------------------------------------------------
# 1. validate_live_target_url - loopback allowed on runtime ports
# ---------------------------------------------------------------------------


class TestValidateLiveTargetUrl:
    def test_allows_loopback_on_runtime_port(self):
        port = LIVE_TARGET_PORT_MIN + 10
        valid, err = validate_live_target_url(f"http://127.0.0.1:{port}")
        assert valid, err

    def test_allows_localhost_on_runtime_port(self):
        valid, err = validate_live_target_url(
            f"http://localhost:{LIVE_TARGET_PORT_MAX}",
        )
        assert valid, err

    def test_blocks_loopback_outside_runtime_port_range(self):
        valid, err = validate_live_target_url("http://127.0.0.1:80")
        assert not valid
        assert "not in the allowed runtime range" in err

    def test_blocks_private_ip(self):
        valid, _err = validate_live_target_url("http://192.168.1.1:8080")
        assert not valid

    def test_allows_public_ip(self):
        valid, err = validate_live_target_url("http://8.8.8.8:80")
        assert valid, err

    def test_blocks_invalid_scheme(self):
        valid, _err = validate_live_target_url(
            f"ftp://127.0.0.1:{LIVE_TARGET_PORT_MIN}",
        )
        assert not valid


# ---------------------------------------------------------------------------
# 2. prepare_live_target - happy-path: builds container, polls, returns URL
# ---------------------------------------------------------------------------


class TestPrepareLiveTarget:
    @patch(f"{_CS}.build_for_job")
    @patch("llm_lab.analysis.services.live_target._tcp_probe", return_value=True)
    def test_returns_instance_and_url(self, mock_probe, mock_build):
        task = _make_task()
        instance = ContainerInstanceFactory(
            status=ContainerInstance.Status.RUNNING,
            app_port=7200,
        )
        mock_build.return_value = instance

        returned_instance, url = prepare_live_target(
            task,
            str(task.configuration["generation_job_id"]),
        )

        assert returned_instance.id == instance.id
        assert url == f"http://127.0.0.1:{instance.app_port}"
        mock_build.assert_called_once()

    @patch(f"{_CS}.build_for_job")
    @patch("llm_lab.analysis.services.live_target._tcp_probe", return_value=True)
    def test_saves_container_instance_id_to_task_config(self, mock_probe, mock_build):
        task = _make_task()
        instance = ContainerInstanceFactory(
            status=ContainerInstance.Status.RUNNING,
            app_port=7201,
        )
        mock_build.return_value = instance

        prepare_live_target(task, str(task.configuration["generation_job_id"]))
        task.refresh_from_db()
        assert task.configuration["container_instance_id"] == str(instance.id)

    @patch(f"{_CS}.build_for_job")
    @patch("llm_lab.analysis.services.live_target._tcp_probe", return_value=True)
    def test_raises_on_failed_container(self, mock_probe, mock_build):
        task = _make_task()
        instance = ContainerInstanceFactory(
            status=ContainerInstance.Status.FAILED,
            app_port=7202,
        )
        mock_build.return_value = instance

        with pytest.raises(RuntimeError, match="failed to build/start"):
            prepare_live_target(task, str(task.configuration["generation_job_id"]))


# ---------------------------------------------------------------------------
# 3. teardown_live_target - stop + remove called
# ---------------------------------------------------------------------------


class TestTeardownLiveTarget:
    @patch(f"{_CS}.stop_instance")
    @patch(f"{_CS}.remove_instance")
    @patch("llm_lab.analysis.services.live_target.time.sleep")
    def test_calls_stop_and_remove(self, mock_sleep, mock_remove, mock_stop):
        instance = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING)

        teardown_live_target(instance)

        mock_stop.assert_called_once_with(instance, user=None)
        mock_remove.assert_called_once_with(instance, user=None)

    @patch(f"{_CS}.stop_instance")
    @patch(f"{_CS}.remove_instance")
    @patch("llm_lab.analysis.services.live_target.time.sleep")
    def test_teardown_swallows_exceptions(self, mock_sleep, mock_remove, mock_stop):
        """teardown_live_target must not propagate errors."""
        mock_stop.side_effect = Exception("docker gone")
        instance = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING)

        teardown_live_target(instance)  # must not raise


# ---------------------------------------------------------------------------
# 4. AnalysisService._execute_inner with live_target=True
# ---------------------------------------------------------------------------


class TestAnalysisServiceLiveTarget:
    @patch(
        "llm_lab.analysis.services.result_service.AnalyzerRegistry.get_instance",
    )
    @patch("llm_lab.analysis.services.live_target.prepare_live_target")
    @patch("llm_lab.analysis.services.live_target.teardown_live_target")
    def test_sets_target_url_in_settings(
        self,
        mock_teardown,
        mock_prepare,
        mock_get_instance,
    ):
        """The resolved target_url is injected into every analyzer's settings."""
        task = _make_task()
        instance = _running_instance()
        mock_prepare.return_value = (instance, "http://127.0.0.1:7300")
        mock_get_instance.return_value = _mock_analyzer("bandit")

        service = AnalysisService()
        service.execute(task)

        task.refresh_from_db()
        assert task.configuration.get("target_url") == "http://127.0.0.1:7300"

    @patch(
        "llm_lab.analysis.services.result_service.AnalyzerRegistry.get_instance",
    )
    @patch("llm_lab.analysis.services.live_target.prepare_live_target")
    @patch("llm_lab.analysis.services.live_target.teardown_live_target")
    def test_teardown_called_on_success(
        self,
        mock_teardown,
        mock_prepare,
        mock_get_instance,
    ):
        task = _make_task()
        instance = _running_instance()
        mock_prepare.return_value = (instance, "http://127.0.0.1:7301")
        mock_get_instance.return_value = _mock_analyzer("bandit")

        AnalysisService().execute(task)

        mock_teardown.assert_called_once_with(instance)

    @patch(
        "llm_lab.analysis.services.result_service.AnalyzerRegistry.get_instance",
    )
    @patch("llm_lab.analysis.services.live_target.prepare_live_target")
    @patch("llm_lab.analysis.services.live_target.teardown_live_target")
    def test_teardown_called_on_failure(
        self,
        mock_teardown,
        mock_prepare,
        mock_get_instance,
    ):
        """Teardown must happen even when analyzer execution raises."""
        task = _make_task()
        instance = _running_instance()
        mock_prepare.return_value = (instance, "http://127.0.0.1:7302")
        mock_get_instance.return_value = _mock_analyzer(
            "bandit",
            AnalyzerOutput(error="Bandit crashed"),
        )

        AnalysisService().execute(task)

        mock_teardown.assert_called_once_with(instance)

    @patch(
        "llm_lab.analysis.services.result_service.AnalyzerRegistry.get_instance",
    )
    @patch("llm_lab.analysis.services.live_target.prepare_live_target")
    @patch("llm_lab.analysis.services.live_target.teardown_live_target")
    def test_keep_container_skips_teardown(
        self,
        mock_teardown,
        mock_prepare,
        mock_get_instance,
    ):
        task = _make_task(extra_config={"keep_container": True})
        instance = _running_instance()
        mock_prepare.return_value = (instance, "http://127.0.0.1:7303")
        mock_get_instance.return_value = _mock_analyzer("bandit")

        AnalysisService().execute(task)

        mock_teardown.assert_not_called()

    @patch(
        "llm_lab.analysis.services.result_service.AnalyzerRegistry.get_instance",
    )
    @patch("llm_lab.analysis.services.live_target.prepare_live_target")
    @patch("llm_lab.analysis.services.live_target.teardown_live_target")
    def test_no_live_target_skips_container_logic(
        self,
        mock_teardown,
        mock_prepare,
        mock_get_instance,
    ):
        task = _make_task(live_target=False)
        mock_get_instance.return_value = _mock_analyzer("bandit")

        AnalysisService().execute(task)

        mock_prepare.assert_not_called()
        mock_teardown.assert_not_called()

    @patch(
        "llm_lab.analysis.services.result_service.AnalyzerRegistry.get_instance",
    )
    @patch("llm_lab.analysis.services.live_target.prepare_live_target")
    @patch("llm_lab.analysis.services.live_target.teardown_live_target")
    def test_teardown_uses_config_instance_id_when_prepare_raises(
        self,
        mock_teardown,
        mock_prepare,
        mock_get_instance,
    ):
        """If prepare_live_target saves container_instance_id before raising,
        the finally block should look it up and still call teardown."""
        task = _make_task()
        instance = ContainerInstanceFactory(
            status=ContainerInstance.Status.FAILED,
            app_port=7304,
        )

        def _prepare_side_effect(t, job_id):
            t.configuration["container_instance_id"] = str(instance.id)
            t.save(update_fields=["configuration"])
            msg = "Container failed to start"
            raise RuntimeError(msg)

        mock_prepare.side_effect = _prepare_side_effect
        mock_get_instance.return_value = _mock_analyzer("bandit")

        AnalysisService().execute(task)

        mock_teardown.assert_called_once()
        called_with = mock_teardown.call_args[0][0]
        assert str(called_with.id) == str(instance.id)
