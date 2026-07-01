from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router
from ninja import Schema
from ninja.security import SessionAuth

from backend.tokens import services
from backend.tokens.api.schema import ApiTokenCreated
from backend.tokens.api.schema import ApiTokenSummary
from backend.tokens.models import ApiToken

router = Router(tags=["tokens"])

_session = SessionAuth()


class CreateTokenSchema(Schema):
    name: str
    scopes: list[str] = []
    expires_at: datetime | None = None


@router.get("/", response=list[ApiTokenSummary], auth=_session)
def list_tokens(request):
    return ApiToken.objects.filter(user=request.auth)


@router.post("/", response={201: ApiTokenCreated}, auth=_session)
def create_token(request, data: CreateTokenSchema):
    raw, token = services.generate_token(
        user=request.auth,
        name=data.name,
        scopes=data.scopes,
        expires_at=data.expires_at,
    )
    return 201, ApiTokenCreated(
        id=token.id,
        name=token.name,
        prefix=token.prefix,
        scopes=token.scopes,
        expires_at=token.expires_at,
        created_at=token.created_at,
        token=raw,
    )


@router.delete("/{token_id}/", response={204: None}, auth=_session)
def revoke_token(request, token_id: UUID):
    token = get_object_or_404(ApiToken, id=token_id, user=request.auth)
    token.revoked_at = timezone.now()
    token.save(update_fields=["revoked_at", "updated_at"])
    return 204, None
