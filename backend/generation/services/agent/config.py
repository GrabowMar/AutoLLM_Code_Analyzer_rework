"""Run configuration for agent-mode jobs: harness, budgets, policies, routing.

Every value here is frozen into the job snapshot's ``agent`` section at job
creation and enters ``run_fingerprint``, so two runs only count as replicates
when they ran under the same limits. Defaults come from settings
(``AGENT_*``); per-job / per-experiment overrides go through
:func:`validate_agent_config`.

Budget semantics: ``max_llm_requests`` (successful model calls) is the
binding, model-neutral budget. The wall clock and USD caps are safety nets
and default high: a USD cap gives expensive models fewer steps, and a wall
clock penalises slow *providers*, neither of which is a model property. A
run stopped by either is flagged in its validity notes.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

NETWORK_POLICIES = ("none", "packages")

# provider_default: the gateway strips every sampling knob the harness sets
#   itself (OpenCode's per-family temperatures, Aider's temperature 0, ...);
#   only params the run configures explicitly reach the model, so every
#   (harness, model) pair samples at the provider's defaults.
# harness_native: harness-sent sampling params pass through (recorded per call).
SAMPLING_POLICIES = ("provider_default", "harness_native")

# Package registries reachable under the ``packages`` network policy (HTTPS
# CONNECT through the job's gateway; everything else is refused).
PACKAGE_REGISTRY_HOSTS = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
)

_LIMITS = {
    # each value is (type, min, max)
    "wall_clock_seconds": (int, 60, 4 * 3600),
    "max_llm_requests": (int, 1, 2000),
    "max_total_tokens": (int, 1000, 200_000_000),
    "max_cost_usd": (float, 0.001, 100.0),
}
# What a budget preset may set: the limits plus the network policy (the
# harness and sampling policy come from harness / sampling presets).
BUDGET_KEYS = (*_LIMITS, "network_policy")


def limits_schema() -> dict[str, dict[str, Any]]:
    """``{key: {type, min, max}}`` of every numeric limit, for UIs."""
    return {
        key: {"type": "int" if kind is int else "float", "min": lo, "max": hi}
        for key, (kind, lo, hi) in _LIMITS.items()
    }


def default_agent_config() -> dict[str, Any]:
    return {
        "harness": settings.AGENT_DEFAULT_HARNESS,
        "sampling_policy": settings.AGENT_SAMPLING_POLICY,
        "wall_clock_seconds": settings.AGENT_WALL_CLOCK_SECONDS,
        "max_llm_requests": settings.AGENT_MAX_LLM_REQUESTS,
        "max_total_tokens": settings.AGENT_MAX_TOTAL_TOKENS,
        "max_cost_usd": settings.AGENT_MAX_COST_USD,
        "network_policy": settings.AGENT_NETWORK_POLICY,
        # Not user-tunable per run, but part of the environment every run is
        # compared under, so frozen alongside the budgets.
        "sandbox_mem_limit": settings.AGENT_SANDBOX_MEM_LIMIT,
        "sandbox_nano_cpus": settings.AGENT_SANDBOX_NANO_CPUS,
    }


def coerce_limit_types(config: dict[str, Any]) -> dict[str, Any]:
    """*config* with each numeric limit in its declared type (in place; returned for chaining).

    An integral float int limit becomes an int; a float limit stored as an
    int (a budget preset's ``max_cost_usd: 3``) becomes a float, so it
    freezes into the snapshot ``agent`` section -- and fingerprints -- like
    the same value sent inline. Defaults are already in their declared types.
    Values of the wrong type are left for :func:`validate_agent_config`.
    """
    for key, (kind, _lo, _hi) in _LIMITS.items():
        value = config.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if kind is int and isinstance(value, float) and value.is_integer():
            config[key] = int(value)
        elif kind is float and isinstance(value, int):
            config[key] = float(value)
    return config


def validate_agent_config(data: dict[str, Any] | None) -> dict[str, Any]:
    """Validate a user-supplied override dict; ``None`` values mean "inherit".

    Raises ``ValueError`` listing every problem (callers turn it into a 400).
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = "agent config must be an object"
        raise ValueError(msg)  # noqa: TRY004 - one exception type for every validation problem
    config = coerce_limit_types({k: v for k, v in data.items() if v is not None})
    errors: list[str] = []
    unknown = set(config) - {*_LIMITS, "network_policy", "harness", "sampling_policy"}
    if unknown:
        errors.append(f"Unknown agent config keys: {', '.join(sorted(unknown))}")
    for key, (kind, lo, hi) in _LIMITS.items():
        if key not in config:
            continue
        value = config[key]
        ok_type = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not ok_type or (kind is int and not isinstance(value, int)) or not lo <= value <= hi:
            article = "an integer" if kind is int else "a number"
            errors.append(f"{key} must be {article} between {lo} and {hi}")
    if "network_policy" in config and config["network_policy"] not in NETWORK_POLICIES:
        errors.append(f"network_policy must be one of {', '.join(NETWORK_POLICIES)}")
    if "sampling_policy" in config and config["sampling_policy"] not in SAMPLING_POLICIES:
        errors.append(f"sampling_policy must be one of {', '.join(SAMPLING_POLICIES)}")
    if "harness" in config:
        from backend.generation.services.agent.harnesses import HARNESSES

        if config["harness"] not in HARNESSES:
            errors.append(f"harness must be one of {', '.join(HARNESSES)}")
    if errors:
        raise ValueError("; ".join(errors))
    return config


def merge_agent_config(*layers: dict[str, Any] | None) -> dict[str, Any]:
    """Defaults, then each override layer in order (later wins); limits in their declared types."""
    merged = default_agent_config()
    for layer in layers:
        for key, value in (layer or {}).items():
            if value is not None:
                merged[key] = value
    return coerce_limit_types(merged)


def default_provider_routing() -> dict[str, Any]:
    """OpenRouter routing applied when the run's llm params set no ``provider``.

    Open-weight models are served by many providers at different
    quantizations (fp4 through bf16) and with different parameter support,
    so unconstrained routing turns "model X" into a per-request mix of
    serving setups. The default keeps only providers that honour every
    forwarded parameter (incl. ``seed``) and serve at fp8 or better
    (``unknown`` stays allowed: closed models never report a quantization);
    the provider that answered each call is still recorded per call.
    """
    return dict(settings.AGENT_DEFAULT_PROVIDER_ROUTING)
