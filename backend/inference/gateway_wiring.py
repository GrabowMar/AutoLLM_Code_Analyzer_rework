"""How an agent job's gateway container reaches its LLM (spec §D.6). Contract → Track B.

Foundation implements the OpenRouter branch of :func:`for_job` (it must
reproduce today's sandbox values exactly, so OpenRouter runs stay
byte-identical); the local branch, :func:`proxy_token` and
:func:`ensure_egress_network` are Track B's.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

OPENROUTER_UPSTREAM = "https://openrouter.ai/api/v1"
PROXY_TOKEN_SALT = "llmlab.llm-proxy"  # noqa: S105 — a signing salt, not a secret


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
    msg = "inference.gateway_wiring.for_job local branch: Track B"
    raise NotImplementedError(msg)


def proxy_token(job) -> str:
    """``TimestampSigner(salt=PROXY_TOKEN_SALT).sign_object({"j": str(job.id), "a": job.attempt})``.

    Verified by the proxy with ``max_age = wall_clock_seconds + 1800`` and
    re-checked against the job's live lease; a requeued attempt's token dies
    because it carries ``attempt``.
    """
    msg = "inference.gateway_wiring.proxy_token: Track B"
    raise NotImplementedError(msg)


def ensure_egress_network(client) -> None:
    """Idempotently create the nested ``llmlab-gw-egress`` network in apps-dind.

    bridge, ipam subnet ``LLM_GATEWAY_EGRESS_SUBNET``, options
    ``{com.docker.network.bridge.name: "llmlab-gwegr", com.docker.network.bridge.enable_icc: "false"}``,
    label ``llmlab.infra=gw-egress``, ``internal=False`` (the CONNECT
    allowlist needs internet). An existing network with different options is
    recreated only when no container uses it.
    """
    msg = "inference.gateway_wiring.ensure_egress_network: Track B"
    raise NotImplementedError(msg)
