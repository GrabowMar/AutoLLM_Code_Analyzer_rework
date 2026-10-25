"""LLM access resolver (spec §D.1): which endpoint, credentials and served name a call uses.

- OpenRouter (``model.endpoint_id`` NULL or the system row): the user's
  OpenRouter key; the credential resolver is unchanged.
- Local (Ollama / OpenAI-compatible): the endpoint must be enabled and usable
  by the user and the model available; the API key is decrypted for direct
  endpoints only (relay endpoints never hold one).

``launch_issues`` reports what blocks a launch and what only warns (an
offline endpoint just makes jobs wait).
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
    "access_from_snapshot",
    "can_use_endpoint",
    "effective_num_ctx",
    "endpoint_access",
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
    # Ollama ``/api/show`` capabilities of the model (``think`` is only sent
    # when they include "thinking"); empty for OpenRouter.
    capabilities: tuple[str, ...] = ()

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


def owner_label(user) -> str:
    """How an endpoint owner is named in the UI (users have no username: name, else email)."""
    if user is None:
        return "another user"
    return getattr(user, "name", "") or getattr(user, "email", "") or f"user {user.pk}"


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
      only.
    """
    if model is None:
        raise LLMAccessError(code="model_unavailable", message="job has no model", retryable=False)
    if not is_local(model):
        return _openrouter_access(user, model)
    return _local_access(user, model)


def model_capabilities(model: LLMModel) -> tuple[str, ...]:
    """The model's discovered capabilities (``metadata.ollama.capabilities``)."""
    caps = ((model.metadata or {}).get("ollama") or {}).get("capabilities") or []
    return tuple(str(c) for c in caps if isinstance(c, str))


def _decrypt_api_key(endpoint: LLMEndpoint) -> str | None:
    if endpoint.connection != LLMEndpoint.Connection.DIRECT or not endpoint.encrypted_api_key:
        return None
    from backend.common.crypto import DecryptionError
    from backend.common.crypto import decrypt

    try:
        return decrypt(endpoint.encrypted_api_key)
    except DecryptionError as exc:
        raise LLMAccessError(
            code="unavailable",
            message=f"The API key of endpoint {endpoint.slug} cannot be decrypted",
            retryable=False,
            remediation="Re-enter the endpoint's API key in its settings.",
        ) from exc


def _local_access(user, model: LLMModel) -> Access:
    endpoint = model.endpoint
    if not endpoint.enabled:
        raise LLMAccessError(
            code="endpoint_disabled",
            message=f"Endpoint {endpoint.slug} is disabled",
            retryable=False,
            remediation="Enable the endpoint on the Endpoints page.",
        )
    if not can_use_endpoint(user, endpoint):
        raise LLMAccessError(
            code="endpoint_forbidden",
            message=f"You cannot use endpoint {endpoint.slug}",
            retryable=False,
            status_code=403,
        )
    if not model.is_available:
        raise LLMAccessError(
            code="model_unavailable",
            message=f"{model.model_id} is no longer served by {endpoint.slug}",
            retryable=False,
            remediation="Pull the model again on the endpoint, or choose another model.",
        )
    return Access(
        endpoint=endpoint,
        kind=endpoint.kind,
        connection=endpoint.connection,
        api_flavor=endpoint.api_flavor,
        served_model_name=model.served_model_name,
        digest=model.digest,
        num_ctx=effective_num_ctx(model),
        keep_alive=endpoint.keep_alive,
        base_url=endpoint.base_url,
        api_key=_decrypt_api_key(endpoint),
        request_timeout_s=endpoint.request_timeout_s,
        capabilities=model_capabilities(model),
    )


def endpoint_access(endpoint: LLMEndpoint, *, served_model_name: str = "", digest: str = "") -> Access:
    """An :class:`Access` for endpoint-level requests (probes, warm-ups, unloads); no user checks."""
    return Access(
        endpoint=endpoint,
        kind=endpoint.kind,
        connection=endpoint.connection,
        api_flavor=endpoint.api_flavor,
        served_model_name=served_model_name,
        digest=digest,
        num_ctx=None,
        keep_alive=endpoint.keep_alive,
        base_url=endpoint.base_url,
        api_key=None,
        request_timeout_s=endpoint.request_timeout_s,
    )


def access_from_snapshot(section: dict[str, Any], endpoint: LLMEndpoint, *, capabilities=()) -> Access:
    """The :class:`Access` frozen in ``snapshot["endpoint"]`` (served name, digest, ``num_ctx``, keep-alive).

    The internal LLM proxy builds upstream bodies from this, never from the
    live model row, so a running job keeps the parameters it was created
    with. Only the queue routing (``endpoint``) and timeouts are live.
    """
    return Access(
        endpoint=endpoint,
        kind=str(section.get("kind") or endpoint.kind),
        connection=endpoint.connection,
        api_flavor=str(section.get("api_flavor") or endpoint.api_flavor),
        served_model_name=str(section.get("served_model_name") or ""),
        digest=str(section.get("digest") or ""),
        num_ctx=section.get("num_ctx"),
        keep_alive=str(section.get("keep_alive") if section.get("keep_alive") is not None else endpoint.keep_alive),
        base_url=endpoint.base_url,
        api_key=None,
        request_timeout_s=endpoint.request_timeout_s,
        capabilities=tuple(capabilities or section.get("capabilities") or ()),
    )


def launch_issues(user, models, *, check_openrouter_key: bool = True) -> list[dict[str, Any]]:
    """Launch-time issues for ``models``: ``[{code, detail, model_id, endpoint, blocking}]``.

    Blocking: ``missing_api_key`` (only if some model is OpenRouter and
    ``check_openrouter_key``), ``endpoint_disabled``, ``endpoint_forbidden``,
    ``model_unavailable``. Warnings: ``endpoint_offline``,
    ``model_not_served``, ``foreign_endpoint``.

    Liveness is checked once per endpoint: ``endpoint_offline`` when no
    relay session (or direct pump inventory) is live, ``model_not_served``
    when the live server does not list the model's ``(name, digest)``.
    """
    from backend.credentials.services.resolver import has_resolvable_key
    from backend.inference.live import endpoint_status
    from backend.inference.live import served_pairs

    issues: list[dict[str, Any]] = []
    liveness: dict[int, tuple[str, set[tuple[str, str]]]] = {}
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
        else:
            if endpoint.owner_id is not None and endpoint.owner_id != getattr(user, "pk", None):
                owner = owner_label(endpoint.owner)
                add(
                    "foreign_endpoint",
                    f"Prompts and generated code pass through a machine run by {owner} ({slug}).",
                    blocking=False,
                )
            if endpoint.pk not in liveness:
                status = endpoint_status(endpoint)
                liveness[endpoint.pk] = (status, served_pairs(endpoint) if status != "offline" else set())
            status, served = liveness[endpoint.pk]
            if status == "offline":
                add(
                    "endpoint_offline",
                    f"Endpoint {slug} is offline; its jobs wait until it connects.",
                    blocking=False,
                )
            elif (model.served_model_name, model.digest) not in served:
                weights = f" ({model.digest[7:19]})" if model.digest else ""
                add(
                    "model_not_served",
                    f"{slug} does not currently serve {model.served_model_name}{weights}; "
                    "its jobs wait until it does.",
                    blocking=False,
                )
    return issues
