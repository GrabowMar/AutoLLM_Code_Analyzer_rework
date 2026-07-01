from __future__ import annotations

from datetime import datetime

from ninja import Schema


class OpenRouterCredentialStatusSchema(Schema):
    """Legacy alias kept for backwards-compat; same shape as the generic one."""

    configured: bool
    key_prefix: str
    last_validation_status: str
    last_validation_message: str
    last_validated_at: datetime | None
    global_fallback_available: bool
    using_global_fallback: bool


class CredentialStatusSchema(Schema):
    provider: str
    configured: bool
    key_prefix: str
    last_validation_status: str
    last_validation_message: str
    last_validated_at: datetime | None
    global_fallback_available: bool
    using_global_fallback: bool


class SetOpenRouterKeySchema(Schema):
    api_key: str


class SetCredentialKeySchema(Schema):
    api_key: str


class ValidationResultSchema(Schema):
    status: str
    message: str
    is_valid: bool
