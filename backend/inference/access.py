"""LLM access resolver (spec §D.1): which endpoint, credentials and served name a call uses.

Frozen contract (Foundation). Implemented here: ``system_openrouter``,
``visible_endpoints``, ``is_endpoint_owner``, ``can_use_endpoint``,
``effective_num_ctx`` (re-exported from ``inference.snapshot``) and the
OpenRouter branches of ``resolve_access`` / ``launch_issues``. The local
branches are Track B's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Q

from backend.generation.services.openrouter_client import OpenRouterError
from backend.inference.models import SYSTEM_ENDPOINT_DEFAULTS
from backend.inference.models import LLMEndpoint
from backend.inference.snapshot import effective_num_ctx
from backend.inference.snapshot import is_local

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from backend.llm_models.models import LLMModel

__all__ = [
    "LLM_ACCESS_ERROR_CODES",
    "Access",
    "LLMAccessError",
    "can_use_endpoint",
    "effective_num_ctx",
    "is_endpoint_owner",
    "launch_issues",
    "resolve_access",
    "system_openrouter",
    "visible_endpoints",
]

LLM_ACCESS_ERROR_CODES = frozenset(
    {
        "missing_api_key",
        "endpoint_disabled",
        "endpoint_forbidden",
        "endpoint_offline",
        "model_unavailable",
        "unavailable",
        "timeout",
        "lease_expired",
        "cancelled",
        "upstream_error",
        "upstream_oom",
        "too_large",
    },
)
# Codes an infra retry may follow (spec §C.7 "What counts as infra").
RETRYABLE_CODES = frozenset({"unavailable", "timeout", "lease_expired", "endpoint_offline", "upstream_oom"})


@dataclass(frozen=True)
class Access:
    """Everything a call needs to reach one model on one endpoint.

    ``api_key`` is the OpenRouter key for the system endpoint, the decrypted
    key for a direct endpoint, and ``None`` for relay endpoints.
    ``num_ctx`` is ``effective_num_ctx(model)`` (None for OpenRouter).
    """

    endpoint: LLMEndpoint
    kind: str
    connection: str
    api_flavor: str
    served_model_name: str
    digest: str
    num_ctx: int | None
    keep_alive: str
    base_url: str
    api_key: str | None
    request_timeout_s: int

    @property
    def is_openrouter(self) -> bool:
        return self.kind == LLMEndpoint.Kind.OPENROUTER


class LLMAccessError(OpenRouterError):
    """A model cannot be reached. Subclasses ``OpenRouterError`` so the
    orchestrator's ``except OpenRouterError`` keeps working.

    ``code`` is one of ``LLM_ACCESS_ERROR_CODES``.
    """

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        retryable: bool | None = None,
        remediation: str | None = None,
        status_code: int | None = None,
        user_facing_message: str | None = None,
    ):
        super().__init__(
            message or code,
            status_code=status_code,
            user_facing_message=user_facing_message,
            remediation=remediation,
        )
        self.code = code
        self.retryable = code in RETRYABLE_CODES if retryable is None else retryable


# ── Endpoint visibility ─────────────────────────────────────────────────

_system_endpoint_id: int | None = None


def system_openrouter() -> LLMEndpoint:
    """The single ``is_system`` OpenRouter endpoint, created on demand.

    No cross-transaction object cache: only the id is cached per process and
    re-validated with ``.exists()`` on use (tests flush tables).
    """
    global _system_endpoint_id  # noqa: PLW0603
    if _system_endpoint_id is not None:
        row = LLMEndpoint.objects.filter(pk=_system_endpoint_id, is_system=True).first()
        if row is not None:
            return row
    row, _created = LLMEndpoint.objects.get_or_create(is_system=True, defaults=dict(SYSTEM_ENDPOINT_DEFAULTS))
    _system_endpoint_id = row.pk
    return row


def visible_endpoints(user) -> QuerySet[LLMEndpoint]:
    """Endpoints ``user`` may see and use: enabled and (owned | shared | system); staff: all."""
    qs = LLMEndpoint.objects.all()
    if user is not None and getattr(user, "is_staff", False):
        return qs
    visible = Q(is_system=True) | Q(visibility=LLMEndpoint.Visibility.SHARED)
    if user is not None and getattr(user, "is_authenticated", False):
        visible |= Q(owner=user)
    return qs.filter(visible, enabled=True)


def is_endpoint_owner(user, endpoint: LLMEndpoint) -> bool:
    """Owner or staff: gates operational fields (IPs, hostnames, errors, sessions; §F.3)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_staff", False):
        return True
    return endpoint.owner_id is not None and endpoint.owner_id == user.pk


def can_use_endpoint(user, endpoint: LLMEndpoint) -> bool:
    """Whether ``user`` may run models on ``endpoint`` (ignores ``enabled``; see ``resolve_access``)."""
    if endpoint.is_system:
        return True
    if is_endpoint_owner(user, endpoint):
        return True
    return endpoint.visibility == LLMEndpoint.Visibility.SHARED


# ── Resolution ──────────────────────────────────────────────────────────


def _openrouter_access(user, model: LLMModel) -> Access:
    from backend.credentials.services.resolver import MissingApiKeyError
    from backend.credentials.services.resolver import get_openrouter_key

    endpoint = system_openrouter()
    try:
        api_key = get_openrouter_key(user)
    except MissingApiKeyError as exc:
        raise LLMAccessError(
            code="missing_api_key",
            message=str(exc),
            retryable=False,
            remediation=exc.remediation,
            status_code=401,
        ) from exc
    return Access(
        endpoint=endpoint,
        kind=endpoint.kind,
        connection=endpoint.connection,
        api_flavor=endpoint.api_flavor,
        served_model_name=model.model_id,
        digest="",
        num_ctx=None,
        keep_alive="",
        base_url=endpoint.base_url,
        api_key=api_key,
        request_timeout_s=endpoint.request_timeout_s,
    )


def resolve_access(user, model: LLMModel) -> Access:
    """Resolve how ``user`` reaches ``model``; raises :class:`LLMAccessError`.

    - OpenRouter (``model.endpoint_id`` NULL or the system row): the user's
      OpenRouter key (``missing_api_key`` when none resolves).
    - Local: the endpoint must be enabled (``endpoint_disabled``), usable by
      the user (``endpoint_forbidden``) and the model available
      (``model_unavailable``); the API key is decrypted for direct endpoints
      only. (Track B.)
    """
    if model is None:
        raise LLMAccessError(code="model_unavailable", message="job has no model", retryable=False)
    if not is_local(model):
        return _openrouter_access(user, model)
    return _local_access(user, model)


def _local_access(user, model: LLMModel) -> Access:
    msg = "inference.access local branch: Track B"
    raise NotImplementedError(msg)


def launch_issues(user, models, *, check_openrouter_key: bool = True) -> list[dict[str, Any]]:
    """Launch-time issues for ``models``: ``[{code, detail, model_id, endpoint, blocking}]``.

    Blocking: ``missing_api_key`` (only if some model is OpenRouter and
    ``check_openrouter_key``), ``endpoint_disabled``, ``endpoint_forbidden``,
    ``model_unavailable``. Warnings: ``endpoint_offline``,
    ``model_not_served``, ``foreign_endpoint``.

    Foundation implements the OpenRouter key check and the row-level local
    checks (disabled / forbidden / unavailable / foreign); liveness warnings
    (``endpoint_offline``, ``model_not_served``) are Track B's.
    """
    from backend.credentials.services.resolver import has_resolvable_key

    issues: list[dict[str, Any]] = []
    models = [m for m in models if m is not None]
    openrouter = [m for m in models if not is_local(m)]
    if check_openrouter_key and openrouter and not has_resolvable_key(user):
        issues.append(
            {
                "code": "missing_api_key",
                "detail": "No OpenRouter API key is configured for this account.",
                "model_id": None,
                "endpoint": SYSTEM_ENDPOINT_DEFAULTS["slug"],
                "blocking": True,
            },
        )
    for model in models:
        if not is_local(model):
            continue
        endpoint = model.endpoint
        slug = endpoint.slug

        def add(code: str, detail: str, *, blocking: bool, _model=model, _slug=slug) -> None:
            issues.append(
                {"code": code, "detail": detail, "model_id": _model.pk, "endpoint": _slug, "blocking": blocking},
            )

        if not endpoint.enabled:
            add("endpoint_disabled", f"Endpoint {slug} is disabled.", blocking=True)
        elif not can_use_endpoint(user, endpoint):
            add("endpoint_forbidden", f"You cannot use endpoint {slug}.", blocking=True)
        elif not model.is_available:
            add("model_unavailable", f"{model.model_id} is no longer available on {slug}.", blocking=True)
        elif endpoint.owner_id is not None and endpoint.owner_id != getattr(user, "pk", None):
            add("foreign_endpoint", f"Endpoint {slug} is run by another user.", blocking=False)
    return issues
