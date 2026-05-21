from __future__ import annotations

import hashlib
import secrets
from typing import TYPE_CHECKING

from llm_lab.tokens.models import ApiToken

if TYPE_CHECKING:
    from datetime import datetime

    from llm_lab.users.models import User


def generate_token(
    user: User,
    name: str,
    scopes: list[str] | None = None,
    expires_at: datetime | None = None,
) -> tuple[str, ApiToken]:
    raw = "llml_" + secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:12]
    token = ApiToken.objects.create(
        user=user,
        name=name,
        token_hash=token_hash,
        prefix=prefix,
        scopes=scopes or [],
        expires_at=expires_at,
    )
    return raw, token


def verify_token(raw: str) -> ApiToken | None:
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    try:
        token = ApiToken.objects.select_related("user").get(token_hash=token_hash)
    except ApiToken.DoesNotExist:
        return None
    if not token.is_valid():
        return None
    return token
