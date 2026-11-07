"""Postal activation must preserve working signup until sender DNS is ready."""

import argparse
import importlib.util
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("postal_workflow", ROOT / "scripts/postal.py")
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


def test_env_update_preserves_other_settings_and_private_permissions(tmp_path):
    path = tmp_path / ".django"
    path.write_text("# Preserve this comment\nEMAIL_PORT=1025\nOTHER_SETTING=unchanged\nEMAIL_PORT=25\n")

    workflow.update_env(path, {"EMAIL_PORT": "2525", "EMAIL_HOST_PASSWORD": "test-smtp-key"})

    content = path.read_text()
    assert "# Preserve this comment" in content
    assert "OTHER_SETTING=unchanged" in content
    assert content.count("EMAIL_PORT=") == 1
    assert "EMAIL_PORT='2525'" in content
    assert "EMAIL_HOST_PASSWORD='test-smtp-key'" in content
    assert path.stat().st_mode & 0o777 == 0o600


def test_env_update_rejects_newline_injection(tmp_path):
    path = tmp_path / ".django"
    path.write_text("EMAIL_PORT=1025\n")

    with pytest.raises(ValueError, match="newline"):
        workflow.update_env(path, {"EMAIL_PORT": "2525\nEMAIL_USE_TLS=False"})

    assert path.read_text() == "EMAIL_PORT=1025\n"


def test_postal_cannot_replace_working_email_until_dns_is_ready(tmp_path, monkeypatch):
    env_path = tmp_path / ".envs/.local/.django"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("EMAIL_HOST=mailpit\n")
    (tmp_path / "config/settings").mkdir(parents=True)
    (tmp_path / "config/settings/local.py").write_text('EMAIL_PORT = env.int("EMAIL_PORT", default=1025)\n')
    for name in ["docker-compose.local.yml", "docker-compose.postal.yml"]:
        (tmp_path / name).write_text("services: {}\n")
    monkeypatch.setattr(workflow, "verify", lambda *args: {"ready_for_delivery": False})
    args = argparse.Namespace(app_root=tmp_path, environment="local", compose_file=[])

    with pytest.raises(ValueError, match="DNS/PTR"):
        workflow.configure_app(tmp_path / "state", args)

    assert env_path.read_text() == "EMAIL_HOST=mailpit\n"
    assert not (tmp_path / ".env").exists()
    assert not env_path.with_name(".django.before-postal").exists()


def test_local_settings_accept_authenticated_smtp(monkeypatch):
    for key, value in {
        "USE_DOCKER": "no",
        "EMAIL_HOST": "postal-smtp",
        "EMAIL_PORT": "2525",
        "EMAIL_HOST_USER": "llmlab",
        "EMAIL_HOST_PASSWORD": "test-smtp-key",
        "EMAIL_USE_TLS": "False",
        "EMAIL_USE_SSL": "False",
        "DJANGO_DEFAULT_FROM_EMAIL": "LLM Lab <noreply@notify.example.org>",
    }.items():
        monkeypatch.setenv(key, value)

    settings = runpy.run_module("config.settings.local")

    assert settings["EMAIL_HOST"] == "postal-smtp"
    assert settings["EMAIL_PORT"] == 2525
    assert settings["EMAIL_HOST_USER"] == "llmlab"
    assert settings["EMAIL_HOST_PASSWORD"] == "test-smtp-key"  # noqa: S105 - test credential
    assert settings["EMAIL_USE_TLS"] is False
    assert settings["EMAIL_USE_SSL"] is False
    assert settings["DEFAULT_FROM_EMAIL"] == "LLM Lab <noreply@notify.example.org>"
