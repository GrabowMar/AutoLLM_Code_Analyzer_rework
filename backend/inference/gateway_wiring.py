"""How an agent job's gateway container reaches its LLM (spec §D.6).

- OpenRouter: today's exact values (``llm-gateway`` image on ``bridge``,
  upstream OpenRouter, the job owner's key), so OpenRouter runs stay
  byte-identical.
- Local: the ``llm-gateway-local`` overlay on the dedicated nested network
  ``llmlab-gw-egress``, pointed at the executor's internal LLM proxy with a
  per-job signed token instead of an API key.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field

from django.conf import settings
from django.core import signing

logger = logging.getLogger(__name__)

OPENROUTER_UPSTREAM = "https://openrouter.ai/api/v1"
PROXY_TOKEN_SALT = "llmlab.llm-proxy"  # noqa: S105 — a signing salt, not a secret
EGRESS_LABEL = "llmlab.infra"
EGRESS_LABEL_VALUE = "gw-egress"


@dataclass(frozen=True)
class GatewayWiring:
    image_local: bool  # True → build/run llm-gateway-local (images.gateway_image_plan(True))
    upstream: str  # gateway_cfg["upstream"]
    api_key: str  # env OPENROUTER_API_KEY ("" for local)
    network: str  # docker network the gateway joins for egress ("bridge" | LLM_GATEWAY_EGRESS_NETWORK)
    extra_env: dict[str, str] = field(default_factory=dict)  # local: {"LLMLAB_UPSTREAM_TOKEN": proxy_token(job)}
    upstream_timeout_s: float | None = None  # gateway_cfg["upstream_timeout_s"]; None keeps the gateway default


def for_job(job, snapshot: dict) -> GatewayWiring:
    """The gateway wiring for ``job``; raises ``inference.access.LLMAccessError``.

    - OpenRouter (no ``snapshot["endpoint"]``):
      ``GatewayWiring(False, "https://openrouter.ai/api/v1", get_openrouter_key(job.created_by), "bridge", {}, None)``
    - Local: ``GatewayWiring(True, settings.LLM_PROXY_UPSTREAM, "", settings.LLM_GATEWAY_EGRESS_NETWORK,
      {"LLMLAB_UPSTREAM_TOKEN": proxy_token(job)}, timeouts.chain(endpoint).gateway_upstream_s)``
    """
    if not (snapshot or {}).get("endpoint"):
        from backend.credentials.services.resolver import MissingApiKeyError
        from backend.credentials.services.resolver import get_openrouter_key
        from backend.inference.access import LLMAccessError

        try:
            api_key = get_openrouter_key(job.created_by)
        except MissingApiKeyError as exc:
            raise LLMAccessError(
                code="missing_api_key",
                message=str(exc),
                retryable=False,
                remediation=exc.remediation,
                status_code=401,
            ) from exc
        return GatewayWiring(
            image_local=False,
            upstream=OPENROUTER_UPSTREAM,
            api_key=api_key,
            network="bridge",
            extra_env={},
            upstream_timeout_s=None,
        )
    from backend.inference.access import LLMAccessError
    from backend.inference.models import LLMEndpoint
    from backend.inference.timeouts import chain

    if not settings.LLM_LOCAL_AGENT_RUNS:
        raise LLMAccessError(
            code="unavailable",
            message="Agent runs on local models are not supported on this deployment",
            retryable=False,
            remediation=(
                "Local-model agent runs need the apps-dind sandbox daemon (local compose), whose "
                "gateways reach the executor's LLM proxy. Use scaffolding mode or an OpenRouter model."
            ),
        )
    section = snapshot["endpoint"]
    endpoint = LLMEndpoint.objects.filter(pk=section.get("id")).first()
    if endpoint is None:
        raise LLMAccessError(
            code="unavailable",
            message=f"Endpoint {section.get('slug')} of this job no longer exists",
            retryable=False,
        )
    if not endpoint.enabled:
        raise LLMAccessError(
            code="endpoint_disabled",
            message=f"Endpoint {endpoint.slug} is disabled",
            retryable=False,
        )
    from backend.inference.access import can_use_endpoint

    # Re-authorize at run time like the scaffolding path (resolve_access): queued,
    # replayed or rerun jobs must not keep using an endpoint the owner lost access to.
    if not can_use_endpoint(job.created_by, endpoint):
        raise LLMAccessError(
            code="endpoint_forbidden",
            message=f"You cannot use endpoint {endpoint.slug}",
            retryable=False,
            status_code=403,
        )
    return GatewayWiring(
        image_local=True,
        upstream=settings.LLM_PROXY_UPSTREAM,
        api_key="",
        network=settings.LLM_GATEWAY_EGRESS_NETWORK,
        extra_env={"LLMLAB_UPSTREAM_TOKEN": proxy_token(job)},
        upstream_timeout_s=float(chain(endpoint).gateway_upstream_s),
    )


def proxy_token(job) -> str:
    """``TimestampSigner(salt=PROXY_TOKEN_SALT).sign_object({"j": str(job.id), "a": job.attempt})``.

    Verified by the proxy with ``max_age = wall_clock_seconds + 1800`` and
    re-checked against the job's live lease; a requeued attempt's token dies
    because it carries ``attempt``.
    """
    return signing.TimestampSigner(salt=PROXY_TOKEN_SALT).sign_object({"j": str(job.id), "a": int(job.attempt or 0)})


def read_proxy_token(token: str, *, max_age: float) -> tuple[str, int]:
    """``(job_id, attempt)`` of a valid :func:`proxy_token`.

    Raises ``signing.BadSignature`` (or its subclass ``SignatureExpired``).
    """
    data = signing.TimestampSigner(salt=PROXY_TOKEN_SALT).unsign_object(token, max_age=max_age)
    if not isinstance(data, dict) or "j" not in data or "a" not in data:
        msg = "malformed proxy token"
        raise signing.BadSignature(msg)
    return str(data["j"]), int(data["a"])


def ensure_egress_network(client) -> None:
    """Idempotently create the nested ``llmlab-gw-egress`` network in apps-dind.

    bridge, ipam subnet ``LLM_GATEWAY_EGRESS_SUBNET``, options
    ``{com.docker.network.bridge.name: "llmlab-gwegr", com.docker.network.bridge.enable_icc: "false"}``,
    label ``llmlab.infra=gw-egress``, ``internal=False`` (the CONNECT
    allowlist needs internet). An existing network with different options is
    recreated only when no container uses it.
    """
    from docker.types import IPAMConfig
    from docker.types import IPAMPool

    name = settings.LLM_GATEWAY_EGRESS_NETWORK
    wanted_options = {
        "com.docker.network.bridge.name": settings.LLM_GATEWAY_EGRESS_BRIDGE,
        "com.docker.network.bridge.enable_icc": "false",
    }
    subnet = settings.LLM_GATEWAY_EGRESS_SUBNET
    for network in client.networks.list(names=[name]):
        if network.name != name:
            continue
        network.reload()
        attrs = network.attrs or {}
        options = attrs.get("Options") or {}
        subnets = [c.get("Subnet") for c in ((attrs.get("IPAM") or {}).get("Config") or [])]
        matches = (
            all(options.get(k) == v for k, v in wanted_options.items())
            and subnets == [subnet]
            and not attrs.get("Internal")
            and (attrs.get("Labels") or {}).get(EGRESS_LABEL) == EGRESS_LABEL_VALUE
        )
        if matches:
            return
        if attrs.get("Containers"):
            logger.warning(
                "Network %s differs from the expected egress configuration but is in use; keeping it",
                name,
            )
            return
        logger.info("Recreating gateway egress network %s with the expected options", name)
        network.remove()
    from docker.errors import APIError

    try:
        client.networks.create(
            name,
            driver="bridge",
            internal=False,
            options=wanted_options,
            labels={EGRESS_LABEL: EGRESS_LABEL_VALUE},
            ipam=IPAMConfig(pool_configs=[IPAMPool(subnet=subnet)]),
        )
    except APIError as exc:
        # Two sandboxes starting at once both saw no network; the other one won.
        if exc.status_code != 409 or not any(n.name == name for n in client.networks.list(names=[name])):
            raise
        logger.debug("Gateway egress network %s was created concurrently", name)
