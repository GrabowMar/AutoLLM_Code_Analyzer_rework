from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ninja import ModelSchema
from ninja import Schema

from llm_lab.tokens.models import ApiToken


class ApiTokenSummary(ModelSchema):
    class Meta:
        model = ApiToken
        fields = [
            "id",
            "name",
            "prefix",
            "scopes",
            "expires_at",
            "last_used_at",
            "last_used_ip",
            "revoked_at",
            "created_at",
        ]


class ApiTokenCreated(Schema):
    id: UUID
    name: str
    prefix: str
    scopes: list[str]
    expires_at: datetime | None
    created_at: datetime
    token: str
