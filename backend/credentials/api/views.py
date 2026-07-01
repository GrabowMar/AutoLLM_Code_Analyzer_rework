"""Per-user credential endpoints.

Provider-aware endpoints live under ``/credentials/{provider}/`` and
support ``openrouter`` and ``huggingface``.
"""

from __future__ import annotations

from django.conf import settings
from ninja import Router

from backend.credentials.api.schema import CredentialStatusSchema
from backend.credentials.api.schema import SetCredentialKeySchema
from backend.credentials.api.schema import ValidationResultSchema
from backend.credentials.models import Provider
from backend.credentials.models import UserApiCredential
from backend.credentials.services.resolver import get_user_credential_for
from backend.credentials.services.validator import validate_for_provider

router = Router(tags=["credentials"])


VALID_PROVIDERS = {p.value for p in Provider}


def _global_fallback_available(provider: str) -> bool:
    """Whether a deployment-wide fallback secret is available for ``provider``."""
    if provider == Provider.OPENROUTER:
        allow = getattr(settings, "OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK", True)
        return bool(allow and getattr(settings, "OPENROUTER_API_KEY", ""))
    # No global fallback wired for Hugging Face today.
    return False


def _status_payload(
    provider: str,
    cred: UserApiCredential | None,
) -> CredentialStatusSchema:
    fallback = _global_fallback_available(provider)
    if cred is None:
        return CredentialStatusSchema(
            provider=provider,
            configured=False,
            key_prefix="",
            last_validation_status="unvalidated",
            last_validation_message="",
            last_validated_at=None,
            global_fallback_available=fallback,
            using_global_fallback=fallback,
        )
    return CredentialStatusSchema(
        provider=provider,
        configured=True,
        key_prefix=cred.key_prefix,
        last_validation_status=cred.last_validation_status,
        last_validation_message=cred.last_validation_message,
        last_validated_at=cred.last_validated_at,
        global_fallback_available=fallback,
        using_global_fallback=False,
    )


@router.get("/", response=list[CredentialStatusSchema])
def list_credentials(request):
    """Status for every supported provider, configured or not."""
    return [
        _status_payload(provider, get_user_credential_for(request.auth, provider))
        for provider in sorted(VALID_PROVIDERS)
    ]


@router.get("/{provider}/", response={200: CredentialStatusSchema, 404: dict})
def get_credential_status(request, provider: str):
    if provider not in VALID_PROVIDERS:
        return 404, {"detail": f"Unknown provider '{provider}'."}
    cred = get_user_credential_for(request.auth, provider)
    return 200, _status_payload(provider, cred)


@router.put("/{provider}/", response={200: CredentialStatusSchema, 400: dict, 404: dict})
def set_credential_key(request, provider: str, payload: SetCredentialKeySchema):
    if provider not in VALID_PROVIDERS:
        return 404, {"detail": f"Unknown provider '{provider}'."}
    raw = (payload.api_key or "").strip()
    if not raw:
        return 400, {"detail": "API key cannot be empty."}

    result = validate_for_provider(provider, raw)
    if not result.is_valid:
        return 400, {"detail": result.message, "status": result.status}

    cred, _ = UserApiCredential.objects.get_or_create(
        user=request.auth,
        provider=provider,
        defaults={"encrypted_secret": ""},
    )
    cred.set_secret(raw)
    cred.save(update_fields=["encrypted_secret", "key_prefix", "updated_at"])
    cred.mark_validation(result.status, result.message)
    return 200, _status_payload(provider, cred)


@router.delete("/{provider}/", response={200: CredentialStatusSchema, 404: dict})
def delete_credential_key(request, provider: str):
    if provider not in VALID_PROVIDERS:
        return 404, {"detail": f"Unknown provider '{provider}'."}
    UserApiCredential.objects.filter(user=request.auth, provider=provider).delete()
    return 200, _status_payload(provider, None)


@router.post("/{provider}/test/", response={200: ValidationResultSchema, 404: dict})
def test_credential_key(request, provider: str):
    if provider not in VALID_PROVIDERS:
        return 404, {"detail": f"Unknown provider '{provider}'."}
    cred = get_user_credential_for(request.auth, provider)
    if cred is None:
        return 404, {"detail": f"No {provider} key configured for this account."}
    secret = cred.get_secret()
    result = validate_for_provider(provider, secret)
    cred.mark_validation(result.status, result.message)
    return 200, ValidationResultSchema(
        status=result.status,
        message=result.message,
        is_valid=result.is_valid,
    )
