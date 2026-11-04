"""Build transport, resource controls and failure semantics without a daemon."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
import requests

from backend.runtime.services import build_backend


def _client():
    client = MagicMock()
    client.api.base_url = "http+docker://localhost"
    client.api.adapters = {"http+docker://": SimpleNamespace(socket_path="/run/apps.sock")}
    client.images.get.return_value.id = "sha256:built"
    return client


def test_cli_uses_apps_mtls_instead_of_inherited_context(settings, monkeypatch):
    settings.APPS_DOCKER_HOST = "tcp://apps-dind:2376"
    settings.APPS_DOCKER_CERT_PATH = "/apps-dind-certs"
    monkeypatch.setenv("DOCKER_CONTEXT", "host-daemon")
    monkeypatch.setenv("DOCKER_HOST", "tcp://wrong-host:2375")
    monkeypatch.setenv("DOCKER_CERT_PATH", "/wrong-certs")
    monkeypatch.setenv("DOCKER_CONFIG", "/worker/docker-config")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")

    env = build_backend.docker_environment(_client())

    assert "DOCKER_CONTEXT" not in env
    assert env["DOCKER_HOST"] == "tcp://apps-dind:2376"
    assert env["DOCKER_CERT_PATH"] == "/apps-dind-certs"
    assert env["DOCKER_TLS_VERIFY"] == "1"
    assert env["DOCKER_CONFIG"] == "/worker/docker-config"
    assert env["HTTPS_PROXY"] == "http://proxy:8080"


def test_cli_matches_sdk_unix_transport_and_clears_stale_tls(settings, monkeypatch):
    settings.APPS_DOCKER_HOST = ""
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.setenv("DOCKER_CERT_PATH", "/stale")

    env = build_backend.docker_environment(_client())

    assert env["DOCKER_HOST"] == "unix:///run/apps.sock"
    assert "DOCKER_CONTEXT" not in env
    assert "DOCKER_TLS_VERIFY" not in env
    assert "DOCKER_CERT_PATH" not in env


def test_buildx_tls_context_preserves_cert_paths_and_selects_apps_daemon():
    env = {
        "DOCKER_HOST": "tcp://apps-dind:2376",
        "DOCKER_TLS_VERIFY": "1",
        "DOCKER_CERT_PATH": "/apps-dind-certs",
        "DOCKER_CONFIG": "/worker/.docker",
        "HTTPS_PROXY": "http://proxy:8080",
    }
    with patch.object(build_backend, "_command", side_effect=[(1, "missing"), (0, "created")]) as command:
        result = build_backend._build_context(env, "llmlab-samples", cancelled=None)

    args = command.call_args_list[1].args[0]
    assert args[:4] == ["docker", "context", "create", "llmlab-samples-daemon"]
    assert "host=tcp://apps-dind:2376" in args[-1]
    assert "ca=/apps-dind-certs/ca.pem" in args[-1]
    assert "cert=/apps-dind-certs/cert.pem" in args[-1]
    assert "key=/apps-dind-certs/key.pem" in args[-1]
    assert result["DOCKER_CONTEXT"] == "llmlab-samples-daemon"
    assert result["DOCKER_CONFIG"] == "/worker/.docker"
    assert result["HTTPS_PROXY"] == "http://proxy:8080"
    assert "DOCKER_HOST" not in result
    assert "DOCKER_TLS_VERIFY" not in result


def test_existing_tls_context_refreshes_rotated_certificates():
    env = {"DOCKER_HOST": "tcp://apps-dind:2376", "DOCKER_TLS_VERIFY": "1", "DOCKER_CERT_PATH": "/certs/client"}
    with patch.object(build_backend, "_command", return_value=(0, "updated")) as command:
        build_backend._build_context(env, "llmlab-samples", cancelled=None)

    assert command.call_args_list[1].args[0][:3] == ["docker", "context", "update"]


def test_builder_is_persistent_resource_limited_and_uses_bridge(settings):
    settings.SAMPLE_BUILDER_NAME = "llmlab-samples"
    settings.SAMPLE_BUILD_MEMORY = "1536m"
    settings.SAMPLE_BUILD_CPUS = 2
    settings.SAMPLE_BUILD_CACHE_BYTES = 5000
    commands = []
    configs = []

    def command(args, env, **kwargs):
        commands.append(args)
        if "create" in args:
            configs.append(Path(args[args.index("--buildkitd-config") + 1]).read_text())
        return (1, "missing") if len(commands) == 1 else (0, "ready")

    with patch.object(build_backend, "_command", side_effect=command):
        name = build_backend._ensure_builder({}, cancelled=None)

    assert name == "llmlab-samples"
    create = commands[1]
    assert "docker-container" in create
    assert "memory=1536m" in create
    assert "memory-swap=1536m" in create
    assert "cpu-period=100000,cpu-quota=200000" in create
    assert "network=bridge" in create
    assert "--buildkitd-flags=--oci-worker-net=bridge" in create
    assert "--bootstrap" in create
    assert "max-parallelism = 1" in configs[0]
    assert "maxUsedSpace = 5000" in configs[0]
    assert "insecure-entitlements = []" in configs[0]
    assert not any("rm" in args for args in commands)


def test_existing_builder_is_bootstrapped_without_discarding_state(settings):
    settings.SAMPLE_BUILDER_NAME = "llmlab-samples"
    with patch.object(build_backend, "_command", return_value=(0, "ready")) as command:
        build_backend._ensure_builder({}, cancelled=None)

    assert command.call_count == 2
    assert command.call_args_list[1].args[0] == ["docker", "buildx", "inspect", "--bootstrap", "llmlab-samples"]


def test_buildkit_loads_image_refreshes_layers_and_records_materials(settings, tmp_path):
    settings.SAMPLE_BUILD_BACKEND = "buildkit"
    settings.APPS_DOCKER_HOST = ""
    client = _client()
    client.containers.get.return_value.stats.side_effect = [
        {"cpu_stats": {"cpu_usage": {"total_usage": 1_000_000_000}}, "memory_stats": {"usage": 100}},
        {"cpu_stats": {"cpu_usage": {"total_usage": 2_000_000_000}}, "memory_stats": {"usage": 300}},
        {"cpu_stats": {"cpu_usage": {"total_usage": 3_000_000_000}}, "memory_stats": {"usage": 200}},
    ]
    calls = []

    def command(args, env, **kwargs):
        calls.append(args)
        if "stop" in args:
            return 0, "stopped"
        metadata = Path(args[args.index("--metadata-file") + 1])
        payload = {
            "buildx.build.provenance": {
                "materials": [{"uri": "pkg:docker/python@3.13-slim", "digest": {"sha256": "base"}}],
            },
        }
        metadata.write_text(json.dumps(payload))
        kwargs["sample"]()
        return 0, "#1 CACHED\n"

    with (
        patch.object(build_backend, "_ensure_builder", return_value="llmlab-samples"),
        patch.object(build_backend, "_command", side_effect=command),
    ):
        result = build_backend.build(
            str(tmp_path),
            "llmlab-sample/test:1",
            client=client,
            force_rebuild=True,
            cache_namespace="owner-7",
            labels={"llmlab.sample_image": "true"},
        )

    assert "--load" in calls[0]
    assert "--no-cache" in calls[0]
    assert "--pull" in calls[0]
    assert "SAMPLE_CACHE_NAMESPACE=owner-7" in calls[0]
    assert "BUILDKIT_CACHE_MOUNT_NS=owner-7" in calls[0]
    assert "llmlab.sample_image=true" in calls[0]
    assert result.image_id == "sha256:built"
    assert result.base_images == {"pkg:docker/python@3.13-slim": "sha256:base"}
    assert result.cpu_seconds == 2
    assert result.peak_memory_bytes == 300
    assert calls[-1] == ["docker", "buildx", "stop", "llmlab-samples"]


@pytest.mark.parametrize(
    ("log", "infra"),
    [("error: invalid syntax", False), ("npm ERR EAI_AGAIN", True), ("process exited with exit code: 137", True)],
)
def test_buildkit_failure_classification(settings, tmp_path, log, infra):
    settings.SAMPLE_BUILD_BACKEND = "buildkit"
    settings.APPS_DOCKER_HOST = ""
    with (
        patch.object(build_backend, "_ensure_builder", return_value="llmlab-samples"),
        patch.object(build_backend, "_command", return_value=(1, log)),
        pytest.raises(build_backend.BuildFailure) as error,
    ):
        build_backend.build(str(tmp_path), "sample:1", client=_client())

    assert error.value.infra_suspected is infra
    assert error.value.timed_out is False


def test_command_timeout_terminates_silent_process():
    with pytest.raises(build_backend.BuildFailure) as error:
        build_backend._command(
            [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"],
            os.environ.copy(),
            timeout_s=0.3,
        )

    assert error.value.timed_out is True
    assert error.value.infra_suspected is False
    assert "started" in error.value.log


def test_command_cancel_terminates_process():
    with pytest.raises(build_backend.BuildFailure) as error:
        build_backend._command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            os.environ.copy(),
            timeout_s=10,
            cancelled=lambda: True,
        )

    assert error.value.cancelled is True
    assert error.value.infra_suspected is True


def test_legacy_applies_supported_resource_limits_and_refresh(settings, tmp_path):
    settings.SAMPLE_BUILD_BACKEND = "legacy"
    settings.SAMPLE_BUILD_MEMORY = "1536m"
    settings.SAMPLE_BUILD_CPUS = 2
    client = _client()
    client.api.build.return_value = iter([{"stream": "built\n"}])

    result = build_backend.build(str(tmp_path), "sample:1", client=client, force_rebuild=True)

    kwargs = client.api.build.call_args.kwargs
    assert kwargs["nocache"] is True
    assert kwargs["pull"] is True
    assert kwargs["container_limits"] == {"memory": 1536 * 1024**2, "memswap": 1536 * 1024**2, "cpushares": 2048}
    assert result.image_id == "sha256:built"


def test_legacy_silent_step_timeout_stays_sample_failure(settings, tmp_path):
    settings.SAMPLE_BUILD_BACKEND = "legacy"
    client = _client()
    client.api.build.side_effect = requests.exceptions.ReadTimeout("silent build step")

    with pytest.raises(build_backend.BuildFailure) as error:
        build_backend.build(str(tmp_path), "sample:1", client=client)

    assert error.value.timed_out is True
    assert error.value.infra_suspected is False


def test_refresh_token_preserves_new_recipe_download_caches(settings, tmp_path):
    settings.SAMPLE_BUILD_BACKEND = "buildkit"
    settings.SAMPLE_BUILDER_NAME = "llmlab-samples"
    settings.APPS_DOCKER_HOST = ""
    (tmp_path / "Dockerfile").write_text("FROM scratch\nARG SAMPLE_DEPENDENCY_REFRESH=0\n")
    with (
        patch.object(build_backend, "_ensure_builder", return_value="llmlab-samples"),
        patch.object(build_backend, "_command", return_value=(0, "built")) as command,
    ):
        build_backend.build(str(tmp_path), "sample:1", client=_client(), force_rebuild=True)

    args = command.call_args_list[0].args[0]
    assert "--pull" in args
    assert "--no-cache" not in args
    assert any(arg.startswith("SAMPLE_DEPENDENCY_REFRESH=") and not arg.endswith("=0") for arg in args)


def test_failed_bootstrap_also_stops_builder(settings, tmp_path):
    settings.SAMPLE_BUILD_BACKEND = "buildkit"
    settings.SAMPLE_BUILDER_NAME = "llmlab-samples"
    settings.APPS_DOCKER_HOST = ""
    with (
        patch.object(build_backend, "_ensure_builder", side_effect=build_backend.BuildFailure("bootstrap failed")),
        patch.object(build_backend, "_stop_builder") as stop,
        pytest.raises(build_backend.BuildFailure),
    ):
        build_backend.build(str(tmp_path), "sample:1", client=_client())

    stop.assert_called_once()


def test_legacy_stream_errors_are_not_successful_images(settings, tmp_path):
    settings.SAMPLE_BUILD_BACKEND = "legacy"
    client = _client()
    client.api.build.return_value = iter([{"stream": "installing\n"}, {"error": "invalid package name"}])

    with pytest.raises(build_backend.BuildFailure) as error:
        build_backend.build(str(tmp_path), "sample:1", client=client)

    assert "invalid package name" in error.value.log
    assert error.value.infra_suspected is False
    client.images.get.assert_not_called()


def test_cancelled_build_never_launches_backend(settings, tmp_path):
    with (
        patch.object(build_backend, "_buildkit") as backend,
        pytest.raises(build_backend.BuildFailure),
    ):
        build_backend.build(str(tmp_path), "sample:1", client=_client(), cancelled=lambda: True)
    backend.assert_not_called()


def test_buildkit_passes_named_contexts_before_the_context_path(settings, tmp_path):
    """Frozen base pins reach ``docker buildx build`` so a tag-only FROM is never re-resolved (WP-05 ask)."""
    settings.SAMPLE_BUILD_BACKEND = "buildkit"
    settings.APPS_DOCKER_HOST = ""
    client = _client()
    client.containers.get.return_value.stats.return_value = {}
    calls = []

    def command(args, env, **kwargs):
        calls.append(args)
        return 0, "ok\n"

    pin = "--build-context=python:3.13-slim=docker-image://python:3.13-slim@sha256:" + "a" * 64
    with (
        patch.object(build_backend, "_ensure_builder", return_value="llmlab-samples"),
        patch.object(build_backend, "_command", side_effect=command),
    ):
        build_backend.build(str(tmp_path), "sample:1", client=client, build_contexts=["--build-context", pin])

    args = calls[0]
    assert args[-1] == str(tmp_path.resolve())
    assert args[-3:-1] == ["--build-context", pin]


def test_legacy_refuses_pinned_bases_as_infra(settings, tmp_path):
    settings.SAMPLE_BUILD_BACKEND = "legacy"
    client = _client()
    with pytest.raises(build_backend.BuildFailure) as error:
        build_backend.build(
            str(tmp_path),
            "sample:1",
            client=client,
            build_contexts=["--build-context", "a=docker-image://a@x"],
        )
    assert error.value.infra_suspected is True
    client.api.build.assert_not_called()
