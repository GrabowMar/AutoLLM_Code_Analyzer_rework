"""The access resolver and launch issues (spec §D.1)."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.core.management import call_command
from django.utils import timezone

from backend.common.crypto import encrypt
from backend.inference.access import LLMAccessError
from backend.inference.access import launch_issues
from backend.inference.access import resolve_access
from backend.inference.access import system_openrouter
from backend.inference.models import LLMEndpoint
from backend.inference.models import RelaySession
from backend.inference.models import RelayToken
from backend.inference.tests.conftest import UNUSED_HASH
from backend.inference.tests.conftest import make_endpoint
from backend.inference.tests.conftest import make_local_model
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _codes(issues):
    return {(i["code"], i["blocking"]) for i in issues}


def _online(endpoint, models):
    token = RelayToken.objects.create(endpoint=endpoint, prefix=f"llr_{endpoint.pk:08d}", secret_hash=UNUSED_HASH)
    return RelaySession.objects.create(
        endpoint=endpoint,
        token=token,
        relay_version="1.0.0",
        upstream_kind="ollama",
        last_seen_at=timezone.now(),
        models=[{"name": m.served_model_name, "digest": m.digest} for m in models],
    )


class TestResolveAccess:
    def test_openrouter_needs_a_key(self, owner):
        model = LLMModelFactory(model_id="vendor/m1")
        with mock.patch("backend.credentials.services.resolver.get_openrouter_key", return_value="sk-1"):
            access = resolve_access(owner, model)
        assert access.is_openrouter
        assert access.api_key == "sk-1"
        assert access.served_model_name == "vendor/m1"
        from backend.credentials.services.resolver import MissingApiKeyError

        with (
            mock.patch(
                "backend.credentials.services.resolver.get_openrouter_key",
                side_effect=MissingApiKeyError("no key"),
            ),
            pytest.raises(LLMAccessError) as exc,
        ):
            resolve_access(owner, model)
        assert exc.value.code == "missing_api_key"

    def test_local_owner_shared_staff_and_forbidden(self, owner, staff, endpoint, local_model):
        access = resolve_access(owner, local_model)
        assert (access.kind, access.served_model_name, access.api_key) == ("ollama", "qwen2.5-coder:7b", None)
        assert access.num_ctx == 32768
        assert "tools" in access.capabilities
        assert resolve_access(staff, local_model).endpoint == endpoint
        stranger = UserFactory()
        with pytest.raises(LLMAccessError) as exc:
            resolve_access(stranger, local_model)
        assert exc.value.code == "endpoint_forbidden"
        endpoint.visibility = LLMEndpoint.Visibility.SHARED
        endpoint.save()
        local_model.refresh_from_db()
        assert resolve_access(stranger, local_model).endpoint == endpoint

    def test_local_disabled_and_unavailable(self, owner, endpoint, local_model):
        endpoint.enabled = False
        endpoint.save()
        local_model.refresh_from_db()
        with pytest.raises(LLMAccessError) as exc:
            resolve_access(owner, local_model)
        assert exc.value.code == "endpoint_disabled"
        endpoint.enabled = True
        endpoint.save()
        local_model.is_available = False
        local_model.save()
        local_model.refresh_from_db()
        with pytest.raises(LLMAccessError) as exc:
            resolve_access(owner, local_model)
        assert exc.value.code == "model_unavailable"

    def test_direct_endpoint_key_is_decrypted(self, staff):
        endpoint = make_endpoint(
            staff,
            connection="direct",
            base_url="http://100.64.1.2:11434",
            encrypted_api_key=encrypt("secret-key"),
        )
        model = make_local_model(endpoint)
        assert resolve_access(staff, model).api_key == "secret-key"


class TestLaunchIssues:
    def test_blocking_and_warnings(self, owner, endpoint, local_model):
        openrouter = LLMModelFactory(model_id="vendor/m2")
        with mock.patch("backend.credentials.services.resolver.has_resolvable_key", return_value=False):
            issues = launch_issues(owner, [openrouter, local_model])
            assert ("missing_api_key", True) in _codes(issues)
            assert ("endpoint_offline", False) in _codes(issues)
            assert "missing_api_key" not in {
                i["code"] for i in launch_issues(owner, [openrouter], check_openrouter_key=False)
            }

    def test_served_and_not_served(self, owner, endpoint, local_model):
        other = make_local_model(endpoint, name="llama3:8b", digest="sha256:" + "cd" * 32)
        _online(endpoint, [local_model])
        issues = launch_issues(owner, [local_model, other])
        assert [i["code"] for i in issues] == ["model_not_served"]
        assert issues[0]["model_id"] == other.pk

    def test_stale_session_is_offline(self, owner, endpoint, local_model):
        session = _online(endpoint, [local_model])
        RelaySession.objects.filter(pk=session.pk).update(last_seen_at=timezone.now() - timedelta(minutes=5))
        assert {i["code"] for i in launch_issues(owner, [local_model])} == {"endpoint_offline"}

    def test_foreign_and_forbidden(self, endpoint, local_model):
        stranger = UserFactory()
        assert ("endpoint_forbidden", True) in _codes(launch_issues(stranger, [local_model]))
        endpoint.visibility = "shared"
        endpoint.save()
        local_model.refresh_from_db()
        issues = launch_issues(stranger, [local_model])
        assert ("foreign_endpoint", False) in _codes(issues)
        assert "machine run by" in next(i for i in issues if i["code"] == "foreign_endpoint")["detail"]


@pytest.mark.django_db(transaction=True)
def test_system_endpoint_survives_a_flush():
    LLMEndpoint.objects.all().delete()
    call_command("flush", interactive=False, verbosity=0)
    assert LLMEndpoint.objects.filter(is_system=True).count() == 1
    assert system_openrouter().slug == "openrouter"
