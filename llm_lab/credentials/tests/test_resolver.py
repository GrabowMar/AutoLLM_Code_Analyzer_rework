from __future__ import annotations

import pytest
from django.test import override_settings

from llm_lab.credentials.models import Provider
from llm_lab.credentials.models import UserApiCredential
from llm_lab.credentials.services.resolver import MissingApiKeyError
from llm_lab.credentials.services.resolver import get_openrouter_key
from llm_lab.credentials.services.resolver import has_resolvable_key

pytestmark = pytest.mark.django_db


def _set_user_key(user, raw: str) -> UserApiCredential:
    cred = UserApiCredential(user=user, provider=Provider.OPENROUTER)
    cred.set_secret(raw)
    cred.save()
    return cred


@override_settings(OPENROUTER_API_KEY="global-key", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True)
def test_user_key_takes_precedence_over_global(user):
    _set_user_key(user, "user-key")
    assert get_openrouter_key(user) == "user-key"


@override_settings(OPENROUTER_API_KEY="global-key", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True)
def test_global_fallback_used_when_user_has_no_key(user):
    assert get_openrouter_key(user) == "global-key"


@override_settings(OPENROUTER_API_KEY="global-key", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True)
def test_global_fallback_can_be_disabled_per_call(user):
    with pytest.raises(MissingApiKeyError):
        get_openrouter_key(user, allow_global_fallback=False)


@override_settings(OPENROUTER_API_KEY="", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True)
def test_missing_key_raises(user):
    with pytest.raises(MissingApiKeyError) as exc_info:
        get_openrouter_key(user)
    assert "Settings" in exc_info.value.remediation


@override_settings(OPENROUTER_API_KEY="global-key", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=False)
def test_global_fallback_disabled(user):
    with pytest.raises(MissingApiKeyError):
        get_openrouter_key(user)


@override_settings(OPENROUTER_API_KEY="global-key", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=False)
def test_global_fallback_disabled_user_has_key(user):
    _set_user_key(user, "user-key")
    assert get_openrouter_key(user) == "user-key"


@override_settings(OPENROUTER_API_KEY="", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True)
def test_has_resolvable_key_negative(user):
    assert not has_resolvable_key(user)


@override_settings(OPENROUTER_API_KEY="global-key", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True)
def test_has_resolvable_key_positive_via_global(user):
    assert has_resolvable_key(user)


def test_anonymous_user_no_key(settings):
    settings.OPENROUTER_API_KEY = ""
    assert not has_resolvable_key(None)
