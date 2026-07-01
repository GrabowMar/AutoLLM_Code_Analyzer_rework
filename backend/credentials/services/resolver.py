"""Resolve which OpenRouter API key to use for a given user.

Order of precedence:
1. The user's own stored key (if any).
2. The deployment-wide ``settings.OPENROUTER_API_KEY`` when
   ``settings.OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK`` is True.
3. Otherwise raise :class:`MissingApiKeyError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings

from backend.credentials.models import Provider
from backend.credentials.models import UserApiCredential

if TYPE_CHECKING:
    from backend.users.models import User


SETTINGS_LINK_REMEDIATION = "Add or update your OpenRouter API key in Settings → API Access."


class MissingApiKeyError(Exception):
    """Raised when no usable OpenRouter API key can be resolved."""

    def __init__(self, message: str, remediation: str = SETTINGS_LINK_REMEDIATION):
        super().__init__(message)
        self.remediation = remediation


def get_user_credential(user: User | None) -> UserApiCredential | None:
    """Return the user's OpenRouter credential row, if any."""
    return get_user_credential_for(user, Provider.OPENROUTER)


def get_user_credential_for(
    user: User | None,
    provider: str,
) -> UserApiCredential | None:
    """Return the user's credential for ``provider``, if any."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return UserApiCredential.objects.filter(
        user=user,
        provider=provider,
    ).first()


def get_openrouter_key(
    user: User | None,
    *,
    allow_global_fallback: bool | None = None,
) -> str:
    """Return the OpenRouter API key to use for ``user``.

    Falls back to the global ``OPENROUTER_API_KEY`` when allowed and
    necessary. Raises :class:`MissingApiKeyError` if neither source provides
    a usable key.
    """
    cred = get_user_credential(user)
    if cred is not None:
        try:
            secret = cred.get_secret()
        except Exception:  # noqa: BLE001 — surface as missing-key to caller
            secret = ""
        if secret:
            return secret

    allow_global = (
        getattr(settings, "OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK", True)
        if allow_global_fallback is None
        else allow_global_fallback
    )
    if allow_global:
        global_key = getattr(settings, "OPENROUTER_API_KEY", "") or ""
        if global_key:
            return global_key

    msg = "No OpenRouter API key is configured for this account."
    raise MissingApiKeyError(msg)


def has_resolvable_key(user: User | None) -> bool:
    """Return True if :func:`get_openrouter_key` would succeed for ``user``."""
    try:
        get_openrouter_key(user)
    except MissingApiKeyError:
        return False
    return True
