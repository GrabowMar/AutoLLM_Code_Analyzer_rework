"""Validate API keys against upstream providers.

A lightweight, fast call used both as a preflight before queueing generation
jobs and as a "Test" button in the settings UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from llm_lab.credentials.models import ValidationStatus

logger = logging.getLogger(__name__)

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
HUGGINGFACE_WHOAMI_URL = "https://huggingface.co/api/whoami-v2"
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_RATE_LIMITED = 429
DEFAULT_TIMEOUT = 10


@dataclass
class ValidationResult:
    status: str  # one of ValidationStatus values
    message: str

    @property
    def is_valid(self) -> bool:
        return self.status == ValidationStatus.VALID


def validate_openrouter_key(api_key: str, *, timeout: int = DEFAULT_TIMEOUT) -> ValidationResult:
    """Probe OpenRouter to check whether ``api_key`` is accepted.

    Hits ``GET /api/v1/key`` which is cheap and returns metadata about the
    authenticated key without consuming model credits.
    """
    if not api_key or not api_key.strip():
        return ValidationResult(ValidationStatus.INVALID, "API key is empty.")

    try:
        response = requests.get(
            OPENROUTER_KEY_URL,
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return ValidationResult(
            ValidationStatus.NETWORK_ERROR,
            f"OpenRouter did not respond within {timeout}s.",
        )
    except requests.exceptions.RequestException as exc:
        return ValidationResult(
            ValidationStatus.NETWORK_ERROR,
            f"Could not reach OpenRouter: {exc}",
        )

    if response.status_code == HTTP_OK:
        return ValidationResult(ValidationStatus.VALID, "Key accepted by OpenRouter.")
    if response.status_code == HTTP_UNAUTHORIZED:
        return ValidationResult(
            ValidationStatus.INVALID,
            "OpenRouter rejected this key (401). The key is invalid, revoked, or expired.",
        )
    if response.status_code == HTTP_RATE_LIMITED:
        return ValidationResult(
            ValidationStatus.RATE_LIMITED,
            "OpenRouter rate-limited the validation request. Try again shortly.",
        )
    return ValidationResult(
        ValidationStatus.NETWORK_ERROR,
        f"Unexpected response from OpenRouter ({response.status_code}).",
    )


def validate_huggingface_key(
    api_key: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> ValidationResult:
    """Probe Hugging Face to check whether ``api_key`` is accepted.

    Hits ``GET /api/whoami-v2`` which returns metadata about the
    authenticated token without consuming any quota.
    """
    if not api_key or not api_key.strip():
        return ValidationResult(ValidationStatus.INVALID, "API key is empty.")

    try:
        response = requests.get(
            HUGGINGFACE_WHOAMI_URL,
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return ValidationResult(
            ValidationStatus.NETWORK_ERROR,
            f"Hugging Face did not respond within {timeout}s.",
        )
    except requests.exceptions.RequestException as exc:
        return ValidationResult(
            ValidationStatus.NETWORK_ERROR,
            f"Could not reach Hugging Face: {exc}",
        )

    if response.status_code == HTTP_OK:
        return ValidationResult(ValidationStatus.VALID, "Token accepted by Hugging Face.")
    if response.status_code in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN):
        return ValidationResult(
            ValidationStatus.INVALID,
            "Hugging Face rejected this token. It is invalid, revoked, or lacks permissions.",
        )
    if response.status_code == HTTP_RATE_LIMITED:
        return ValidationResult(
            ValidationStatus.RATE_LIMITED,
            "Hugging Face rate-limited the validation request. Try again shortly.",
        )
    return ValidationResult(
        ValidationStatus.NETWORK_ERROR,
        f"Unexpected response from Hugging Face ({response.status_code}).",
    )


PROVIDER_VALIDATORS = {
    "openrouter": validate_openrouter_key,
    "huggingface": validate_huggingface_key,
}


def validate_for_provider(provider: str, api_key: str) -> ValidationResult:
    """Validate ``api_key`` using the validator registered for ``provider``."""
    validator = PROVIDER_VALIDATORS.get(provider)
    if validator is None:
        return ValidationResult(
            ValidationStatus.INVALID,
            f"Unknown provider '{provider}'.",
        )
    return validator(api_key)
