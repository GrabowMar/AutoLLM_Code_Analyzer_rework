"""Orchestrator for container lifecycle actions."""

from __future__ import annotations

import logging
import tempfile
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from django.db import connection
from django.db import transaction
from django.utils import timezone

from llm_lab.realtime import events as realtime
from llm_lab.runtime.models import ContainerAction
from llm_lab.runtime.models import ContainerInstance
from llm_lab.runtime.models import PortAllocation
from llm_lab.runtime.services import docker_manager
from llm_lab.runtime.services import port_allocator

if TYPE_CHECKING:
    from llm_lab.generation.models import GenerationJob
    from llm_lab.users.models import User

logger = logging.getLogger(__name__)


def create_action(
    container: ContainerInstance,
    action_type: str,
    user: User | None = None,
) -> ContainerAction:
    """Persist a ContainerAction and dispatch it in a daemon thread."""
    action = ContainerAction.objects.create(
        action_id=f"act_{uuid.uuid4().hex[:12]}",
        container=container,
        action_type=action_type,
        status=ContainerAction.Status.PENDING,
        triggered_by=user,
    )
    _dispatch(action.id)
    return action


def _dispatch(action_id) -> None:
    thread = threading.Thread(
        target=_execute,
        args=(action_id,),
        daemon=True,
        name=f"container-action-{action_id}",
    )
    thread.start()


def _execute(action_id) -> None:
    try:
        with transaction.atomic():
            action = ContainerAction.objects.select_for_update().get(id=action_id)
            if action.status != ContainerAction.Status.PENDING:
                return
            action.mark_running()

        container = action.container
        if container is None:
            action.mark_failed("Container no longer exists")
            return

        action_type = action.action_type

        if action_type == ContainerAction.ActionType.BUILD:
            _do_build(action, container)
        elif action_type == ContainerAction.ActionType.START:
            _do_start(action, container)
        elif action_type == ContainerAction.ActionType.STOP:
            _do_stop(action, container)
        elif action_type == ContainerAction.ActionType.RESTART:
            _do_restart(action, container)
        elif action_type == ContainerAction.ActionType.REMOVE:
            _do_remove(action, container)
        elif action_type == ContainerAction.ActionType.LOGS:
            _do_logs(action, container)
        elif action_type == ContainerAction.ActionType.HEALTH:
            _do_health(action, container)
        else:
            action.mark_failed(f"Unknown action type: {action_type}")

    except ContainerAction.DoesNotExist:
        logger.warning("ContainerAction %s vanished before execution", action_id)
    except Exception as exc:
        logger.exception("ContainerAction %s failed unexpectedly", action_id)
        try:
            a = ContainerAction.objects.get(id=action_id)
            if a.status in (
                ContainerAction.Status.PENDING,
                ContainerAction.Status.RUNNING,
            ):
                a.mark_failed(str(exc))
        except ContainerAction.DoesNotExist:
            pass
    finally:
        connection.close()


def _do_build(action: ContainerAction, container: ContainerInstance) -> None:
    if not docker_manager.ping():
        action.mark_failed("Docker daemon unavailable")
        container.status = ContainerInstance.Status.FAILED
        container.save(update_fields=["status"])
        return

    container.status = ContainerInstance.Status.BUILDING
    container.save(update_fields=["status"])
    action.update_progress(10)
    realtime.publish(
        f"runtime:{container.id}",
        {
            "type": "status",
            "status": container.status,
            "updated_at": timezone.now().isoformat(),
        },
    )

    job = container.generation_job
    tag = f"llm_lab/{container.name}:latest"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            build_path = Path(tmpdir)
            if job is not None:
                from llm_lab.runtime.services.scaffolding import prepare_build_dir

                prepare_build_dir(job, build_path)
            else:
                _write_minimal_dockerfile(build_path)

            action.update_progress(40)
            docker_manager.build_image(str(build_path), tag)

        container.image = tag
        action.update_progress(70)

        ports = {}
        env: dict[str, str] = {}
        if container.app_port:
            ports["8000/tcp"] = container.app_port

        cid = docker_manager.run_container(
            tag,
            container.name,
            ports,
            env,
            str(container.id),
        )
        container.container_id = cid
        container.status = ContainerInstance.Status.RUNNING
        container.save(update_fields=["image", "container_id", "status"])
        action.update_progress(100)
        action.mark_completed(output=f"Built {tag}, container {cid}", exit_code=0)
        realtime.publish(
            f"runtime:{container.id}",
            {
                "type": "status",
                "status": container.status,
                "updated_at": timezone.now().isoformat(),
            },
        )

    except Exception as exc:
        logger.exception("Build failed for %s", container.name)
        container.status = ContainerInstance.Status.FAILED
        container.save(update_fields=["status"])
        action.mark_failed(str(exc))
        realtime.publish(
            f"runtime:{container.id}",
            {
                "type": "status",
                "status": container.status,
                "updated_at": timezone.now().isoformat(),
            },
        )


def _write_minimal_dockerfile(path: Path) -> None:
    (path / "Dockerfile").write_text(
        'FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\nRUN pip install flask --quiet\nCMD ["python", "app.py"]\n',
    )
    (path / "app.py").write_text(
        "from flask import Flask, jsonify\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/api/health')\n"
        "def health():\n"
        "    return jsonify({'status': 'ok'})\n\n"
        "if __name__ == '__main__':\n"
        "    app.run(host='0.0.0.0', port=5000)\n",
    )


def _do_start(action: ContainerAction, container: ContainerInstance) -> None:
    result = docker_manager.start(container.name)
    if "error" in result:
        action.mark_failed(result["error"])
    else:
        container.status = ContainerInstance.Status.RUNNING
        container.save(update_fields=["status"])
        action.mark_completed(output="Started", exit_code=0)
        realtime.publish(
            f"runtime:{container.id}",
            {
                "type": "status",
                "status": container.status,
                "updated_at": timezone.now().isoformat(),
            },
        )


def _do_stop(action: ContainerAction, container: ContainerInstance) -> None:
    result = docker_manager.stop(container.name)
    if "error" in result:
        action.mark_failed(result["error"])
    else:
        container.status = ContainerInstance.Status.STOPPED
        container.save(update_fields=["status"])
        action.mark_completed(output="Stopped", exit_code=0)
        realtime.publish(
            f"runtime:{container.id}",
            {
                "type": "status",
                "status": container.status,
                "updated_at": timezone.now().isoformat(),
            },
        )


def _do_restart(action: ContainerAction, container: ContainerInstance) -> None:
    result = docker_manager.restart(container.name)
    if "error" in result:
        action.mark_failed(result["error"])
    else:
        container.status = ContainerInstance.Status.RUNNING
        container.save(update_fields=["status"])
        action.mark_completed(output="Restarted", exit_code=0)
        realtime.publish(
            f"runtime:{container.id}",
            {
                "type": "status",
                "status": container.status,
                "updated_at": timezone.now().isoformat(),
            },
        )


def _do_remove(action: ContainerAction, container: ContainerInstance) -> None:
    result = docker_manager.remove(container.name)
    if "error" in result:
        action.mark_failed(result["error"])
    else:
        port_allocator.release(container)
        container.status = ContainerInstance.Status.REMOVED
        container.save(update_fields=["status"])
        action.mark_completed(output="Removed", exit_code=0)
        realtime.publish(
            f"runtime:{container.id}",
            {
                "type": "status",
                "status": container.status,
                "updated_at": timezone.now().isoformat(),
            },
        )


def _do_logs(action: ContainerAction, container: ContainerInstance) -> None:
    output = docker_manager.logs(container.name)
    action.mark_completed(output=output, exit_code=0)


def _do_health(action: ContainerAction, container: ContainerInstance) -> None:
    data = docker_manager.health(container.name)
    if "error" in data:
        action.mark_failed(data["error"])
    else:
        container.health_status = data.get("health", "")
        container.last_health_check = timezone.now()
        container.save(update_fields=["health_status", "last_health_check"])
        action.mark_completed(
            output=f"status={data.get('status')} health={data.get('health')}",
            exit_code=0,
        )


def build_for_job(job: GenerationJob, user: User | None) -> ContainerInstance:
    """Create a ContainerInstance for *job* and kick off a build action."""
    app_port = port_allocator.allocate()
    slug = f"llm-{uuid.uuid4().hex[:8]}"

    instance = ContainerInstance.objects.create(
        generation_job=job,
        name=slug,
        status=ContainerInstance.Status.PENDING,
        app_port=app_port,
        created_by=user,
    )

    try:
        alloc = PortAllocation.objects.get(app_port=app_port)
        alloc.container = instance
        alloc.save(update_fields=["container"])
    except PortAllocation.DoesNotExist:
        pass

    create_action(instance, ContainerAction.ActionType.BUILD, user)
    return instance


def stop_instance(container: ContainerInstance, user: User | None) -> ContainerAction:
    return create_action(container, ContainerAction.ActionType.STOP, user)


def start_instance(
    container: ContainerInstance,
    user: User | None,
) -> ContainerAction:
    return create_action(container, ContainerAction.ActionType.START, user)


def restart_instance(
    container: ContainerInstance,
    user: User | None,
) -> ContainerAction:
    return create_action(container, ContainerAction.ActionType.RESTART, user)


def remove_instance(
    container: ContainerInstance,
    user: User | None,
) -> ContainerAction:
    return create_action(container, ContainerAction.ActionType.REMOVE, user)
