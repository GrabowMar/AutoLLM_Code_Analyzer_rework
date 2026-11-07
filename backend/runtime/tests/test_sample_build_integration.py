"""The evaluator and preview lifecycle adopt the same immutable image."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from backend.generation.services.evaluation.runner import Evaluation
from backend.generation.tests.factories import GenerationJobFactory
from backend.runtime.models import ContainerAction
from backend.runtime.models import ContainerInstance
from backend.runtime.models import SampleBuildArtifact
from backend.runtime.services import container_service
from backend.runtime.services import sample_builds
from backend.runtime.tests.factories import ContainerActionFactory
from backend.runtime.tests.factories import ContainerInstanceFactory


@pytest.mark.django_db(transaction=True)
def test_evaluation_and_two_fresh_previews_build_only_once(monkeypatch):
    from docker.errors import ImageNotFound

    job = GenerationJobFactory(stack_slug="generic-python", result_data={"backend_code": "print('sample')"})
    client = MagicMock()
    client.info.return_value = {"OSType": "linux", "Architecture": "amd64", "ID": "integration-daemon"}
    images = {}

    def get_image(tag):
        if tag not in images:
            raise ImageNotFound(tag)
        return images[tag]

    client.images.get.side_effect = get_image

    def build(path, tag, **kwargs):
        image = SimpleNamespace(id="sha256:immutable", attrs={"Size": 128})
        images[tag] = images[image.id] = image
        return SimpleNamespace(image_id=image.id, image_tag=tag, log="built", seconds=1, base_images={})

    backend = MagicMock(side_effect=build)
    monkeypatch.setattr(sample_builds.build_backend, "build", backend)
    monkeypatch.setattr(container_service.docker_manager, "client", lambda: client)
    monkeypatch.setattr(container_service.docker_manager, "ping", lambda: True)
    monkeypatch.setattr(container_service, "_verify_running", lambda container: (True, ""))
    monkeypatch.setattr(container_service, "publish_container_status", lambda container: None)
    evaluation = Evaluation(job)
    evaluation.client = client
    assert evaluation._build()["cache_hit"] is False
    evaluation._teardown()
    client.images.remove.assert_not_called()

    with (
        patch("backend.runtime.services.smoke.run_smoke", return_value={"passed": True}),
        patch.object(container_service.docker_manager, "run_container", side_effect=["preview-1", "preview-2"]) as run,
    ):
        for index in range(2):
            container = ContainerInstanceFactory(generation_job=job, name=f"llm-integration-{index}", image="")
            action = ContainerActionFactory(container=container, action_type=ContainerAction.ActionType.BUILD)
            container_service._execute(action.id)
            action.refresh_from_db()
            container.refresh_from_db()
            assert action.status == ContainerAction.Status.COMPLETED
            assert container.status == ContainerInstance.Status.RUNNING
            assert container.metadata["build"]["cache_hit"] is True
            assert container.image == evaluation.image_tag
    assert backend.call_count == 1
    assert run.call_count == 2
    assert all(call.args[0] == "sha256:immutable" for call in run.call_args_list)
    assert SampleBuildArtifact.objects.count() == 1


@pytest.mark.django_db
def test_cached_preview_starts_after_commit_without_build_admission(django_capture_on_commit_callbacks):
    from backend.execution.models import RuntimeBuildLease

    job = GenerationJobFactory()
    built = SimpleNamespace(image_id="sha256:sample")
    with (
        patch.object(sample_builds, "cached_sample", return_value=built),
        patch.object(container_service, "_dispatch_cached_start") as dispatch,
        patch("backend.execution.api.wake_preview_build") as wake,
        django_capture_on_commit_callbacks(execute=True),
    ):
        container = container_service.build_for_job(job, job.created_by)
    action = container.actions.get()
    assert action.status == ContainerAction.Status.RUNNING
    lease = RuntimeBuildLease.objects.get(action=action)
    dispatch.assert_called_once_with(action.id, lease.owner, built)
    wake.assert_not_called()


@pytest.mark.django_db
def test_cached_preview_dispatch_failure_fails_action_and_releases_lease(django_capture_on_commit_callbacks):
    from backend.execution.models import RuntimeBuildLease

    job = GenerationJobFactory()
    built = SimpleNamespace(image_id="sha256:sample")
    with (
        patch.object(sample_builds, "cached_sample", return_value=built),
        patch.object(container_service, "dispatch_in_thread", side_effect=RuntimeError("thread limit")),
        django_capture_on_commit_callbacks(execute=True),
    ):
        container = container_service.build_for_job(job, job.created_by)
    action = container.actions.get()
    container.refresh_from_db()
    assert action.status == ContainerAction.Status.FAILED
    assert "thread limit" in action.error_message
    assert container.status == ContainerInstance.Status.FAILED
    assert not RuntimeBuildLease.objects.filter(action=action).exists()


@pytest.mark.django_db
def test_forced_preview_queues_and_does_not_peek_at_cached_image():
    job = GenerationJobFactory()
    with (
        patch.object(sample_builds, "cached_sample") as peek,
        patch("backend.execution.api.wake_preview_build") as wake,
    ):
        container = container_service.build_for_job(job, job.created_by, force_rebuild=True)
    assert container.config["force_rebuild"] is True
    action = container.actions.get()
    assert action.status == ContainerAction.Status.PENDING
    wake.assert_called_once_with(action.id)
    peek.assert_not_called()
