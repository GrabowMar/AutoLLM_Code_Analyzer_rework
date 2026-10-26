"""Reviewer configuration: defaults, validation, freezing, cost estimates.

Profile config (``AnalysisProfile.config["reviewer"]``, see ``plan.py``)::

    {"enabled": true, "required": true, "engine": "single_shot" | "agent",
     "harness": null | "opencode" | "mini-swe-agent" | "aider", "harness_options": {},
     "model": "deepseek/deepseek-v4.1-flash", "samples": 3, "temperature": 0.2,
     "reasoning_effort": null | "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max",
     "max_output_tokens": 32000, "provider": null | {...OpenRouter routing...},
     "max_replacements": null | 0..20,
     "prompt_version": "review-v1", "schema_version": "findings-v1",
     "include_origins": ["authored", "added", "modified"],
     "include_kinds": ["source", "config", "manifest", "env"],
     "chunk_char_budget": 300000, "seed_base": 1,
     "budget": {"max_cost_usd": 0.5, "max_llm_requests": 40, "wall_clock_seconds": 900}}

Budgets are **per replicate** (one sample of the k). ``reasoning_effort`` null
means "the model's own default" (DeepSeek V4.1 Flash reasons at effort high
by default, which dominates cost — hence the explicit ``low`` default).
An effort is accepted at freeze only when the model offers it (the
catalog's ``reasoning.supported_efforts``; ``xhigh`` and ``max`` exist on
few models): :func:`model_capabilities` tells the UI which ones a model
offers. ``max_replacements`` bounds the extra replicates the engine may run
to replace failed ones (null = ``samples``, resolved when the plan is
frozen). ``temperature`` null means the provider default. ``max_output_tokens``
bounds reasoning *and* answer: DeepSeek V4.1 Flash reasons even at effort
``low`` (1.5k-13.6k reasoning tokens observed per call) and one live review
was truncated at 16k, so the default is 32k (a truncated replicate fails).

:func:`freeze_reviewer` resolves a validated config against the model
catalog into the plan's frozen ``reviewer`` section (contract §4.2). Params
the model does not accept on OpenRouter are dropped *and recorded* (sending
them with ``require_parameters`` would leave no provider; without it they
would be silently ignored), mirroring ``generation.services.agent.snapshot``.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.engine.catalog import AI_TOOL_SLUG
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.engine.reviewer import consensus
from backend.analysis.engine.reviewer import prompt

if TYPE_CHECKING:
    from backend.analysis.engine.types import Sample
    from backend.llm_models.models import LLMModel

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
ENGINES = ("single_shot", "agent")
# Version of the reviewer's deterministic algorithms (scope selection,
# chunking, grounding, consensus, sample acceptance), frozen so a change
# shows in provenance; ``run_review`` refuses a plan frozen under another.
# v2: consensus threshold and quorum = majority of the k planned samples
# (was ceil(m/2) of the successful ones); clustering on the evidence's lines;
# short evidence on identifier boundaries; nl -ba / OpenCode line prefixes
# and /workspace paths tolerated; agent samples need a declared, non-empty
# review; mini-swe-agent's own step limit is a budget stop.
REVIEWER_ENGINE_VERSION = "reviewer-v2"

# OpenRouter's reasoning vocabulary, lowest to highest (the order the UI
# lists them in); validated per model at freeze time against the catalog's
# ``reasoning.supported_efforts`` (e.g. DeepSeek V4.1 Flash: max/high/low/none,
# others list xhigh).
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
OUTPUT_MODES = ("json_schema", "json_object", "prompt")

_LIMITS: dict[str, tuple[type, float, float]] = {
    "samples": (int, 1, 10),
    "temperature": (float, 0.0, 2.0),
    "max_output_tokens": (int, 1000, 200_000),
    "chunk_char_budget": (int, 5_000, 4_000_000),
    "seed_base": (int, 0, 2**31 - 1),
    # Extra replicates the engine may run to replace failed ones (null =
    # samples). Bounded: each replacement can spend a replicate's budget.
    "max_replacements": (int, 0, 20),
}
_BUDGET_LIMITS: dict[str, tuple[type, float, float]] = {
    "max_cost_usd": (float, 0.001, 100.0),
    "max_llm_requests": (int, 1, 2000),
    "wall_clock_seconds": (int, 60, 4 * 3600),
}
DEFAULT_BUDGET = {"max_cost_usd": 0.5, "max_llm_requests": 40, "wall_clock_seconds": 900}
# Output tokens per call, reasoning included (see the module docstring).
DEFAULT_MAX_OUTPUT_TOKENS = 32_000
_NULLABLE = frozenset({"harness", "temperature", "reasoning_effort", "provider", "max_replacements"})

# OpenRouter provider-routing preferences (https://openrouter.ai/docs/features/provider-routing).
_PROVIDER_LISTS = ("order", "only", "ignore", "quantizations")
_PROVIDER_BOOLS = ("allow_fallbacks", "require_parameters", "zdr")
_PROVIDER_KEYS = frozenset({*_PROVIDER_LISTS, *_PROVIDER_BOOLS, "sort", "data_collection", "max_price"})
# An OpenRouter id ("vendor/model[:variant]", "~" for floating aliases) or a
# catalog canonical slug ("vendor_model-20260910"), resolved by find_model.
_MODEL_RE = re.compile(r"^~?[A-Za-z0-9._-]+(/[A-Za-z0-9._:~-]+)?$")

# Token estimates (see estimate_cost): ~3.5 characters per token for code is
# the usual planning figure; chunks are sized with a conservative 2.5 so a
# dense file (minified-ish JS, long identifiers) still fits the context.
CHARS_PER_TOKEN = 3.5
CHARS_PER_TOKEN_SAFE = 2.5
CONTEXT_SAFETY_TOKENS = 2048
FALLBACK_CONTEXT_WINDOW = 128_000
MIN_CHUNK_CHARS = 2000
# The line-number prefix ("  12| ") adds ~6 chars to a ~40-char line.
LINE_PREFIX_FACTOR = 1.15
FILE_HEADER_CHARS = 90
# Expected (mean) answer size and reasoning tokens per call by effort.
# Calibrated on live deepseek-v4.1-flash reviews of three real agent apps
# (2026-10-22, 8 calls at effort low): reasoning 1.5k-13.6k tokens (mean
# ~4.1k, heavy-tailed), answers 0.8k-2.1k tokens (mean ~1.4k).
EXPECTED_ANSWER_TOKENS = 1500
LOW_ANSWER_TOKENS = 600
REASONING_TOKENS = {
    "none": 0,
    "minimal": 800,
    "low": 4000,
    "medium": 8000,
    "high": 12000,
    "xhigh": 14000,
    "max": 16000,
}
# The catalog price is OpenRouter's model price (its cheapest endpoint);
# routing (fp8-or-better, require_parameters, fallbacks) lands on dearer
# providers: the same 8 calls billed 1.5-2.6x the catalog price (mean ~1.9x;
# CoreWeave $0.15/M in + $0.65/M out vs catalog $0.049/$0.525; Crusoe and
# Phala dearer). Expected uses this factor, high a wider one.
ROUTED_PRICE_FACTOR = 2.0
HIGH_PRICE_FACTOR = 3.0
# Agent engine heuristics (calls re-send the growing conversation).
AGENT_EXPECTED_CALLS = 20
AGENT_BASE_CONTEXT_TOKENS = 8000
AGENT_ANSWER_TOKENS = 600


def _default_model() -> str:
    from django.conf import settings

    return getattr(settings, "ANALYSIS_DEFAULT_REVIEW_MODEL", "") or DEFAULT_MODEL


def default_reviewer_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "required": True,
        "engine": "single_shot",
        "harness": None,
        "harness_options": {},
        "model": _default_model(),
        "samples": 3,
        "temperature": 0.2,
        "reasoning_effort": "low",
        "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
        "provider": None,
        "prompt_version": prompt.PROMPT_VERSION,
        "schema_version": prompt.SCHEMA_VERSION,
        "include_origins": list(prompt.DEFAULT_INCLUDE_ORIGINS),
        "include_kinds": list(prompt.DEFAULT_INCLUDE_KINDS),
        "chunk_char_budget": 300_000,
        "seed_base": 1,
        # null = as many replacements as planned samples (resolved at freeze).
        "max_replacements": None,
        "budget": dict(DEFAULT_BUDGET),
    }


def _number(value: Any, kind: type, lo: float, hi: float) -> tuple[Any, str | None]:
    """(normalized value, error) for an int/float limit."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value, f"must be a number between {lo:g} and {hi:g}"
    if kind is int:
        if not float(value).is_integer():
            return value, f"must be an integer between {lo:g} and {hi:g}"
        value = int(value)
    else:
        value = float(value)
    if not lo <= value <= hi:
        return value, f"must be between {lo:g} and {hi:g}"
    return value, None


def _validate_provider(value: Any, errors: dict[str, str]) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        errors["reviewer.provider"] = "must be an object of OpenRouter provider preferences or null"
        return None
    out: dict[str, Any] = {}
    for key, item in value.items():
        field = f"reviewer.provider.{key}"
        if key not in _PROVIDER_KEYS:
            errors[field] = f"unknown provider preference (allowed: {', '.join(sorted(_PROVIDER_KEYS))})"
        elif key in _PROVIDER_LISTS:
            if not isinstance(item, list) or not all(isinstance(x, str) and x for x in item):
                errors[field] = "must be a list of strings"
            else:
                out[key] = list(item)
        elif key in _PROVIDER_BOOLS:
            if not isinstance(item, bool):
                errors[field] = "must be true or false"
            else:
                out[key] = item
        elif key == "sort":
            if item not in ("price", "throughput", "latency"):
                errors[field] = "must be one of price, throughput, latency"
            else:
                out[key] = item
        elif key == "data_collection":
            if item not in ("allow", "deny"):
                errors[field] = "must be allow or deny"
            else:
                out[key] = item
        elif not isinstance(item, dict):  # max_price
            errors[field] = "must be an object"
        else:
            out[key] = dict(item)
    return out


def _validate_choices(value: Any, choices: tuple[str, ...], field: str, errors: dict[str, str]) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value):
        errors[field] = f"must be a non-empty list of: {', '.join(choices)}"
        return []
    unknown = sorted(set(value) - set(choices))
    if unknown:
        errors[field] = f"unknown value(s) {', '.join(unknown)} (allowed: {', '.join(choices)})"
        return []
    # Canonical order, so equal sets hash equal in the frozen plan.
    return [c for c in choices if c in set(value)]


def validate_reviewer_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize; raises catalog.ConfigError({"reviewer.<field>": msg}).

    ``None`` (no reviewer section) means no AI review: the defaults with
    ``enabled`` False. Missing keys take defaults; ``null`` is kept for the
    nullable keys (harness, temperature, reasoning_effort, provider,
    max_replacements). The
    model is checked against the catalog only by :func:`freeze_reviewer`, so
    validation needs no database.
    """
    defaults = default_reviewer_config()
    if cfg is None:
        return {**defaults, "enabled": False}
    if not isinstance(cfg, dict):
        raise ConfigError({"reviewer": "must be an object"})
    errors: dict[str, str] = {}
    for key in cfg:
        if key not in defaults:
            errors[f"reviewer.{key}"] = "unknown field"
    out = dict(defaults)
    out.update({k: v for k, v in cfg.items() if k in defaults and (v is not None or k in _NULLABLE)})

    for key in ("enabled", "required"):
        if not isinstance(out[key], bool):
            errors[f"reviewer.{key}"] = "must be true or false"
    if out["engine"] not in ENGINES:
        errors["reviewer.engine"] = f"must be one of {', '.join(ENGINES)}"
    if not isinstance(out["harness_options"], dict):
        errors["reviewer.harness_options"] = "must be an object"
    if out["engine"] == "agent":
        from backend.analysis.engine.reviewer.agentic import REVIEW_HARNESSES

        if out["harness"] not in REVIEW_HARNESSES:
            errors["reviewer.harness"] = f"the agent engine needs a harness: one of {', '.join(REVIEW_HARNESSES)}"
    else:
        # The single-shot engine has no harness; a leftover choice from the
        # UI is not part of the instrument and must not split plan hashes.
        out["harness"] = None
        out["harness_options"] = {}

    model = out["model"]
    if not isinstance(model, str) or not _MODEL_RE.match(model.strip()):
        errors["reviewer.model"] = "must be an OpenRouter model id like vendor/model (or a catalog canonical slug)"
    else:
        out["model"] = model.strip()

    for key, (kind, lo, hi) in _LIMITS.items():
        if key in _NULLABLE and out[key] is None:
            continue
        out[key], err = _number(out[key], kind, lo, hi)
        if err:
            errors[f"reviewer.{key}"] = err
    if out["reasoning_effort"] is not None and out["reasoning_effort"] not in REASONING_EFFORTS:
        errors["reviewer.reasoning_effort"] = f"must be null or one of {', '.join(REASONING_EFFORTS)}"
    out["provider"] = _validate_provider(out["provider"], errors)
    if out["prompt_version"] not in prompt.PROMPT_FILES:
        errors["reviewer.prompt_version"] = f"unknown prompt version (known: {', '.join(prompt.PROMPT_FILES)})"
    if out["schema_version"] not in prompt.SCHEMA_FILES:
        errors["reviewer.schema_version"] = f"unknown schema version (known: {', '.join(prompt.SCHEMA_FILES)})"
    out["include_origins"] = _validate_choices(
        out["include_origins"],
        prompt.ORIGIN_CHOICES,
        "reviewer.include_origins",
        errors,
    )
    out["include_kinds"] = _validate_choices(
        out["include_kinds"],
        prompt.KIND_CHOICES,
        "reviewer.include_kinds",
        errors,
    )

    budget = out["budget"]
    if not isinstance(budget, dict):
        errors["reviewer.budget"] = "must be an object"
    else:
        merged = dict(DEFAULT_BUDGET)
        for key, value in budget.items():
            if key not in _BUDGET_LIMITS:
                errors[f"reviewer.budget.{key}"] = "unknown field"
            elif value is not None:
                merged[key] = value
        for key, (kind, lo, hi) in _BUDGET_LIMITS.items():
            merged[key], err = _number(merged[key], kind, lo, hi)
            if err:
                errors[f"reviewer.budget.{key}"] = err
        out["budget"] = merged
    if errors:
        raise ConfigError(errors)
    return out


# --------------------------------------------------------------------------
# Freezing
# --------------------------------------------------------------------------


# "No user given": the model is looked up among all rows (the pre-local
# behaviour); a given user (even None, anonymous) sees only visible models.
_ANY_USER: Any = object()
# Public name for "no user given" (lookups are not scoped to a user).
ANY_USER: Any = _ANY_USER


def find_model(slug: str, user: Any = _ANY_USER) -> LLMModel | None:
    """The model row for an OpenRouter id (or a canonical slug), or a local endpoint's model id.

    With *user*, a local model counts only when its endpoint is visible to
    that user (``LLMModel.objects.visible_to``).
    """
    from backend.llm_models.models import LLMModel

    rows = LLMModel.objects.select_related("endpoint")
    if user is not _ANY_USER:
        rows = rows.visible_to(user)
    return rows.filter(model_id=slug).first() or rows.filter(canonical_slug=slug).first()


def _local_supports(model: LLMModel) -> dict[str, Any]:
    """What the reviewer may ask of a local model: conservative, from what the inference layer sends.

    Parameters are the endpoint's ``inference.params.STATIC_PARAMS``. No
    structured outputs (grammar support differs by server and version):
    Ollama's native API gets JSON mode (``format: "json"``), an
    OpenAI-compatible server the prompt-only mode; both are validated
    locally against the schema. Reasoning is Ollama's boolean ``think`` on a
    model that reports the ``thinking`` capability: the only effort that can
    be requested is ``none`` (thinking off), null keeps the model default.
    """
    from backend.inference.access import model_capabilities as discovered
    from backend.inference.params import STATIC_PARAMS

    endpoint = model.endpoint
    allowed = STATIC_PARAMS.get((endpoint.kind, endpoint.api_flavor), frozenset())
    native = endpoint.api_flavor == "native"
    found = set(discovered(model))
    thinking = native and "thinking" in found
    return {
        "parameters_known": True,
        "structured_outputs": False,
        "response_format": native,
        "tools": "tools" in allowed and ("tools" in found or bool(model.supports_function_calling)),
        "seed": "seed" in allowed,
        "temperature": "temperature" in allowed,
        "reasoning": thinking,
        "reasoning_efforts": ["none"] if thinking else [],
        "reasoning_default_effort": None,
        "reasoning_default_enabled": True if thinking else None,
        "reasoning_mandatory": False,
    }


def _local_section(model: LLMModel) -> dict[str, Any]:
    """The frozen identity of a local model: endpoint and the exact weights served."""
    from backend.inference.snapshot import effective_num_ctx

    endpoint = model.endpoint
    return {
        "endpoint": endpoint.slug,
        "endpoint_id": endpoint.pk,
        "kind": endpoint.kind,
        "api_flavor": endpoint.api_flavor,
        "served_model_name": model.served_model_name,
        "digest": model.digest,
        "num_ctx": effective_num_ctx(model),
    }


def _supports(model: LLMModel) -> dict[str, Any]:
    """What the reviewer may ask of a model, from the catalog's OpenRouter metadata (freeze's flat view)."""
    caps_json = model.capabilities_json or {}
    supported = (model.metadata or {}).get("openrouter_supported_parameters") or caps_json.get("supported_parameters")
    known = isinstance(supported, list) and bool(supported)
    params = set(supported) if known else set()
    reasoning = caps_json.get("reasoning") if isinstance(caps_json.get("reasoning"), dict) else {}
    efforts = [e for e in reasoning.get("supported_efforts") or [] if isinstance(e, str)]
    default_enabled = reasoning.get("default_enabled")
    return {
        "parameters_known": known,
        "structured_outputs": "structured_outputs" in params,
        "response_format": "response_format" in params or (not known and bool(model.supports_json_mode)),
        "tools": bool(model.supports_function_calling) or "tools" in params,
        "seed": "seed" in params,
        # Unknown parameter support: send temperature and let OpenRouter route.
        "temperature": (not known) or "temperature" in params,
        "reasoning": "reasoning" in params or bool(reasoning),
        "reasoning_efforts": efforts,
        "reasoning_default_effort": reasoning.get("default_effort"),
        "reasoning_default_enabled": default_enabled if isinstance(default_enabled, bool) else None,
        "reasoning_mandatory": bool(reasoning.get("mandatory")),
    }


def _effort_choices(caps: dict[str, Any]) -> list[str]:
    """The ``reasoning_effort`` values :func:`freeze_reviewer` accepts for a model, lowest first.

    The efforts the catalog lists for the model, in vocabulary order (an
    effort outside the vocabulary cannot pass validation); the whole
    vocabulary when the model reasons but the catalog lists no efforts (the
    effort is then sent unverified, see the freeze notes); none when it does
    not reason (an effort would be dropped). ``none`` is never a choice for a
    model whose reasoning is mandatory.
    """
    if not caps["reasoning"]:
        return []
    listed = set(caps["reasoning_efforts"])
    choices = [e for e in REASONING_EFFORTS if e in listed] if listed else list(REASONING_EFFORTS)
    if caps["reasoning_mandatory"]:
        choices = [e for e in choices if e != "none"]
    return choices


def _output_mode(caps: dict[str, Any]) -> str:
    """How the single-shot engine asks this model for JSON (see :data:`OUTPUT_MODES`)."""
    if caps["structured_outputs"]:
        return "json_schema"
    if caps["response_format"]:
        return "json_object"
    return "prompt"


def _positive_int(value: Any) -> int | None:
    """A catalog token count, None when the catalog does not know it (stored as 0)."""
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _price(value: Any) -> float | None:
    """A catalog price (string or number per token), None when absent or unparseable."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def model_capabilities(model_slug: str | LLMModel, user: Any = _ANY_USER) -> dict[str, Any]:
    """What the reviewer can ask of a model: the public view for the API's capabilities endpoint.

    *model_slug* is an OpenRouter id or a catalog canonical slug (resolved
    as :func:`freeze_reviewer` resolves it), or a catalog row. Stable shape
    (keys are only ever added)::

        {"in_catalog": bool, "requested": str, "model_id", "canonical_slug", "name", "provider",
         "context_window": int|None, "max_output_tokens": int|None,
         "structured_outputs", "response_format", "tools", "seed", "temperature": bool|None,
         "parameters_known": bool, "output_mode": "json_schema"|"json_object"|"prompt"|None,
         "reasoning": {"supported": bool|None, "supported_efforts": [...], "effort_choices": [...],
                       "efforts_known": bool, "default_effort": str|None, "default_enabled": bool|None,
                       "mandatory": bool|None},
         "pricing": {"input_per_token", "output_per_token", "cache_read_per_token", "is_free"}|None}

    ``reasoning.supported_efforts`` is the catalog's list in vocabulary
    order; ``effort_choices`` is what the UI should offer: exactly the
    values freeze accepts (see :func:`_effort_choices`). A model outside the
    catalog has ``in_catalog`` False and every capability None (unknown,
    never False); freezing it fails. A 0 context window or output limit in
    the catalog means unknown and is reported as None.

    Added keys: ``resolvable`` (the reviewer can call the model: a catalog
    model, or a local model whose endpoint is enabled and still serves it)
    and ``local`` (None for OpenRouter models; for a model on a local
    endpoint ``{endpoint, endpoint_id, kind, api_flavor, served_model_name,
    digest, num_ctx, available}``). A local model has ``in_catalog`` False
    (it is not an OpenRouter catalog model) and conservative capabilities
    (see :func:`_local_supports`); its ``context_window`` is the context it
    runs with (``num_ctx``) and its pricing 0 (not billed). With *user*, a
    local model that user cannot see is reported as unknown.
    """
    from backend.llm_models.models import LLMModel

    if isinstance(model_slug, LLMModel):
        model: LLMModel | None = model_slug
        requested = model_slug.model_id
    else:
        requested = model_slug.strip() if isinstance(model_slug, str) else ""
        model = find_model(requested, user) if requested else None
    if model is None:
        return {
            "in_catalog": False,
            "resolvable": False,
            "local": None,
            "requested": requested,
            "model_id": None,
            "canonical_slug": None,
            "name": None,
            "provider": None,
            "context_window": None,
            "max_output_tokens": None,
            "structured_outputs": None,
            "response_format": None,
            "tools": None,
            "seed": None,
            "temperature": None,
            "parameters_known": False,
            "output_mode": None,
            "reasoning": {
                "supported": None,
                "supported_efforts": [],
                "effort_choices": [],
                "efforts_known": False,
                "default_effort": None,
                "default_enabled": None,
                "mandatory": None,
            },
            "pricing": None,
        }
    local = model.is_local
    caps = _local_supports(model) if local else _supports(model)
    rank = {e: i for i, e in enumerate(REASONING_EFFORTS)}
    listed = sorted(caps["reasoning_efforts"], key=lambda e: (rank.get(e, len(rank)), e))
    reasons = caps["reasoning"]
    pricing = (model.capabilities_json or {}).get("pricing") or {}
    local_info = None
    context_window = _positive_int(model.context_window)
    if local:
        local_info = {**_local_section(model), "available": bool(model.is_available)}
        context_window = _positive_int(local_info["num_ctx"]) or context_window
    return {
        "in_catalog": not local,
        "resolvable": (not local) or (bool(model.is_available) and bool(model.endpoint.enabled)),
        "local": local_info,
        "requested": requested,
        "model_id": model.model_id,
        "canonical_slug": model.canonical_slug,
        "name": model.model_name,
        "provider": model.provider,
        "context_window": context_window,
        "max_output_tokens": _positive_int(model.max_output_tokens),
        "structured_outputs": caps["structured_outputs"],
        "response_format": caps["response_format"],
        "tools": caps["tools"],
        "seed": caps["seed"],
        "temperature": caps["temperature"],
        "parameters_known": caps["parameters_known"],
        "output_mode": _output_mode(caps),
        "reasoning": {
            "supported": reasons,
            "supported_efforts": listed,
            "effort_choices": _effort_choices(caps),
            "efforts_known": bool(listed),
            "default_effort": caps["reasoning_default_effort"] if reasons else None,
            "default_enabled": caps["reasoning_default_enabled"] if reasons else None,
            "mandatory": caps["reasoning_mandatory"] if reasons else None,
        },
        "pricing": (
            {"input_per_token": 0.0, "output_per_token": 0.0, "cache_read_per_token": None, "is_free": True}
            if local
            else {
                "input_per_token": model.input_price_per_token,
                "output_per_token": model.output_price_per_token,
                "cache_read_per_token": _price(pricing.get("input_cache_read")),
                "is_free": bool(model.is_free),
            }
        ),
    }


def effective_chunk_chars(
    *,
    chunk_char_budget: int,
    context_window: int,
    max_tokens: int,
    prompt_version: str = prompt.PROMPT_VERSION,
    schema_version: str = prompt.SCHEMA_VERSION,
) -> int:
    """Largest code chunk (rendered chars) one call may carry: the configured
    budget, or less when the model's context (minus the output reservation,
    the prompt and a safety margin) cannot hold it."""
    usable_tokens = context_window - max_tokens - CONTEXT_SAFETY_TOKENS
    cap = int(usable_tokens * CHARS_PER_TOKEN_SAFE) - prompt.overhead_chars(prompt_version, schema_version)
    return min(chunk_char_budget, cap)


def freeze_reviewer(cfg: dict[str, Any], *, user: Any = _ANY_USER) -> dict[str, Any] | None:
    """Resolve model (LLMModel: id, canonical slug, pricing snapshot, context window,
    supports structured_outputs / tools / reasoning efforts), provider routing,
    prompt + schema version/sha256, harness section/version/image tags (agent).

    Returns None for a disabled reviewer (the plan's ``reviewer: null``).
    Raises :class:`ConfigError` for an unknown model, an effort the model
    does not offer, a context window too small for the output reservation,
    or a harness the model cannot drive.

    A model on a local endpoint (Ollama / OpenAI-compatible) freezes with
    its endpoint and exact weights (``model["local"]``: served name, digest,
    ``num_ctx``), conservative capabilities (:func:`_local_supports`), the
    context it runs with as context window, no provider routing and a 0
    price (local calls are not billed); it needs the single-shot engine.
    With *user*, a local model must be visible to that user.
    """
    from django.utils import timezone

    from backend.generation.services.agent.config import default_provider_routing

    cfg = validate_reviewer_config(cfg)
    if not cfg["enabled"]:
        return None
    model = find_model(cfg["model"], user)
    if model is None:
        raise ConfigError({"reviewer.model": f"unknown model {cfg['model']!r}: not in the model catalog"})
    local = model.is_local
    if local:
        if cfg["engine"] == "agent":
            raise ConfigError(
                {
                    "reviewer.engine": (
                        "the agent engine reviews through the OpenRouter gateway; a local model needs the "
                        "single_shot engine"
                    ),
                },
            )
        if cfg["provider"] is not None:
            raise ConfigError({"reviewer.provider": "provider routing applies to OpenRouter models only"})
    caps = _local_supports(model) if local else _supports(model)
    where = f"on endpoint {model.endpoint.slug}" if local else "on OpenRouter"
    notes: list[str] = []
    dropped: dict[str, str] = {}

    temperature = cfg["temperature"]
    if temperature is not None and not caps["temperature"]:
        dropped["temperature"] = f"not supported by this model {where}"
        temperature = None

    reasoning: dict[str, Any] | None = None
    effort = cfg["reasoning_effort"]
    if effort is not None:
        choices = _effort_choices(caps)
        if not caps["reasoning"]:
            dropped["reasoning_effort"] = f"model does not support reasoning {where}"
        elif effort not in choices:
            # An effort the model does not list (xhigh/max on most models,
            # none where reasoning is mandatory) would be remapped or
            # rejected by the provider: a different instrument than the plan
            # says, so it fails here instead.
            raise ConfigError(
                {
                    "reviewer.reasoning_effort": (
                        f"{model.model_id} offers reasoning efforts {', '.join(choices)} (or null for the model "
                        "default)"
                    ),
                },
            )
        else:
            reasoning = {"effort": effort}
            if not caps["reasoning_efforts"]:
                notes.append(
                    f"the catalog lists no reasoning efforts for this model: effort {effort} is sent as requested "
                    "and its effect is unverified",
                )
    elif caps["reasoning"]:
        if caps["reasoning_default_enabled"] is False and not caps["reasoning_mandatory"]:
            notes.append("reasoning off by default for this model (no effort requested)")
        else:
            notes.append(f"reasoning at the model default (effort {caps['reasoning_default_effort'] or 'unknown'})")

    max_tokens = cfg["max_output_tokens"]
    if model.max_output_tokens and model.max_output_tokens < max_tokens:
        notes.append(f"max_output_tokens capped at the model's {model.max_output_tokens}")
        max_tokens = model.max_output_tokens
    local_section = _local_section(model) if local else None
    context_window = model.context_window or 0
    context_source = "catalog" if model.context_window else "fallback"
    if local_section and local_section["num_ctx"]:
        # The context the endpoint runs the model with, not the model's maximum.
        context_window, context_source = int(local_section["num_ctx"]), "num_ctx"
    if context_window <= 0:
        # Same fallback as generation's resolve_llm; a too-large chunk then
        # fails loudly (HTTP 400 -> failed sample), never silently.
        context_window = FALLBACK_CONTEXT_WINDOW
        notes.append(f"no context window in the catalog: assumed {FALLBACK_CONTEXT_WINDOW} tokens")
    chunk_chars = effective_chunk_chars(
        chunk_char_budget=cfg["chunk_char_budget"],
        context_window=context_window,
        max_tokens=max_tokens,
        prompt_version=cfg["prompt_version"],
        schema_version=cfg["schema_version"],
    )
    if chunk_chars < MIN_CHUNK_CHARS:
        raise ConfigError(
            {
                "reviewer.max_output_tokens": (
                    f"{model.model_id}'s context window ({context_window} tokens) leaves no room for code after "
                    f"reserving {max_tokens} output tokens"
                ),
            },
        )
    if chunk_chars < cfg["chunk_char_budget"]:
        notes.append(f"chunks limited to {chunk_chars} chars by the context window")

    output_mode = _output_mode(caps)
    if output_mode == "json_object":
        notes.append("no structured outputs: json_object mode + local schema validation")
    elif output_mode == "prompt":
        notes.append("no response_format support: JSON requested by the prompt + local schema validation")
    if cfg["model"].startswith("~"):
        notes.append("floating model alias: the served model may change; compare by canonical slug")

    routing: dict[str, Any] | None
    if local:
        routing, routing_source = None, "local"
        notes.append(
            f"local model on endpoint {model.endpoint.slug} ({model.served_model_name}"
            + (f" {model.digest[7:19]}" if model.digest else "")
            + "): not billed, runs only while the endpoint serves these weights",
        )
        if caps["reasoning"]:
            notes.append("local thinking model: thinking is on (model default) or off (effort none), no other effort")
    elif cfg["provider"] is not None:
        routing, routing_source = dict(cfg["provider"]), "explicit"
    else:
        routing, routing_source = default_provider_routing(), "default"
    # Without require_parameters a provider may ignore response_format /
    # seed / reasoning, silently changing the instrument.
    if routing is not None:
        routing.setdefault("require_parameters", True)

    pricing = (model.capabilities_json or {}).get("pricing") or {}
    samples = cfg["samples"]
    # null = one replacement per planned sample; resolved here so the plan
    # (and its hash) records the number the engine will apply.
    max_replacements = samples if cfg["max_replacements"] is None else cfg["max_replacements"]
    frozen: dict[str, Any] = {
        "enabled": True,
        "required": cfg["required"],
        "tool_slug": AI_TOOL_SLUG,
        "engine": cfg["engine"],
        "engine_version": REVIEWER_ENGINE_VERSION,
        "harness": cfg["harness"],
        "harness_options": dict(cfg["harness_options"]),
        "harness_version": "",
        "harness_images": {},
        "model": {
            "slug": model.model_id,
            "id": model.id,
            "canonical_slug": model.canonical_slug,
            "openrouter_canonical_slug": (model.capabilities_json or {}).get("canonical_slug") or "",
            "provider": model.provider,
            "name": model.model_name,
            "context_window": context_window,
            "context_window_source": context_source,
            "max_output_tokens": model.max_output_tokens or None,
            "pricing": {
                "input_per_token": 0.0 if local else model.input_price_per_token,
                "output_per_token": 0.0 if local else model.output_price_per_token,
                "cache_read_per_token": None if local else _price(pricing.get("input_cache_read")),
                "snapshot_at": timezone.now().isoformat(),
            },
            "supports": {
                "structured_outputs": caps["structured_outputs"],
                "response_format": caps["response_format"],
                "tools": caps["tools"],
                "seed": caps["seed"],
                "temperature": caps["temperature"],
                "reasoning": caps["reasoning"],
                "reasoning_efforts": caps["reasoning_efforts"],
                "reasoning_default_effort": caps["reasoning_default_effort"],
                "reasoning_default_enabled": caps["reasoning_default_enabled"],
                "reasoning_mandatory": caps["reasoning_mandatory"],
                "parameters_known": caps["parameters_known"],
            },
        },
        "params": {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "reasoning": reasoning,
            "seed_base": cfg["seed_base"],
            "seed_forwarded": caps["seed"],
            "dropped": dropped,
        },
        "provider": routing,
        "provider_routing_source": routing_source,
        "output_mode": output_mode,
        "samples": samples,
        "quorum": consensus.majority(samples),
        # Extra replicates the engine may run to replace failed ones (read by
        # run_review; a plan frozen before this key existed has none).
        "max_replacements": max_replacements,
        "prompt_version": cfg["prompt_version"],
        "prompt_sha256": prompt.prompt_sha256(cfg["prompt_version"]),
        "schema_version": cfg["schema_version"],
        "schema_sha256": prompt.schema_sha256(cfg["schema_version"]),
        "include_origins": list(cfg["include_origins"]),
        "include_kinds": list(cfg["include_kinds"]),
        "chunk_char_budget": cfg["chunk_char_budget"],
        "chunking": {
            "effective_chars": chunk_chars,
            "chars_per_token": CHARS_PER_TOKEN_SAFE,
            "context_safety_tokens": CONTEXT_SAFETY_TOKENS,
        },
        "consensus": {
            "line_window": 3,
            "grounding_window": 3,
            "threshold": consensus.THRESHOLD_RULE,
            "quorum": consensus.QUORUM_RULE,
        },
        "budget": dict(cfg["budget"]),
        "notes": notes,
    }
    if local_section is not None:
        # Only local models carry the key: OpenRouter plans keep their hashes.
        frozen["model"]["local"] = local_section
    if cfg["engine"] == "agent":
        from backend.analysis.engine.reviewer import agentic

        try:
            section = agentic.freeze_harness(
                cfg["harness"],
                cfg["harness_options"],
                model=model,
                max_output_tokens=max_tokens,
                budget=cfg["budget"],
            )
        except ValueError as exc:
            raise ConfigError({"reviewer.harness_options": str(exc)}) from exc
        frozen["agent"] = section
        frozen["harness_version"] = str(section.get("version") or section.get("harness_version") or "")
        frozen["harness_images"] = dict(section.get("images") or section.get("harness_images") or {})
        frozen["harness_options"] = dict(section.get("options") or section.get("harness_options") or {})
    return frozen


# --------------------------------------------------------------------------
# Cost estimate
# --------------------------------------------------------------------------


def _reasoning_tokens(frozen: dict[str, Any]) -> int:
    params = frozen.get("params") or {}
    supports = (frozen.get("model") or {}).get("supports") or {}
    if params.get("reasoning"):
        effort = params["reasoning"].get("effort")
    elif supports.get("reasoning_default_enabled") is False and not supports.get("reasoning_mandatory"):
        # The model only reasons when asked, and the plan does not ask.
        effort = "none"
    elif supports.get("reasoning"):
        effort = supports.get("reasoning_default_effort") or "medium"
    else:
        effort = "none"
    return REASONING_TOKENS.get(effort or "none", REASONING_TOKENS["medium"])


def review_inputs(frozen: dict[str, Any], sample: Sample) -> tuple[int, int]:
    """(characters, files) the reviewer will be shown for *sample*: the inputs of :func:`estimate_cost`.

    Same selection as the engines (``prompt.select_files`` with the frozen
    origins/kinds), minus binary and blank files, which are never sent.
    """
    files, _ = prompt.select_files(
        sample,
        include_origins=frozen.get("include_origins") or prompt.DEFAULT_INCLUDE_ORIGINS,
        include_kinds=frozen.get("include_kinds") or prompt.DEFAULT_INCLUDE_KINDS,
    )
    texts = [t for t in (prompt.file_text(f) for f in files) if t is not None and t.strip()]
    return sum(len(t) for t in texts), len(texts)


def estimate_cost(frozen: dict[str, Any], *, chars: int, files: int) -> dict[str, Any]:
    """{low, expected, high (USD per target), tokens: {input, output}, calls}.

    *chars* / *files*: raw characters and number of the files the reviewer
    will be shown (``prompt.select_files``). Token estimate (single-shot):
    rendered chars = chars x 1.15 (line-number prefixes) + 90 per file
    header; input tokens = rendered / 3.5 + per-call overhead (system prompt
    + template + schema, also / 3.5); output tokens per call = ~1.5k answer
    + reasoning tokens by effort (none 0, minimal 0.8k, low 4k, medium 8k,
    high 12k, max 16k), capped at max_tokens. Prices are the frozen catalog
    snapshot: *low* = catalog price, 10% fewer input tokens, short answers,
    half the reasoning (a floor); *expected* = the token estimate at
    ``ROUTED_PRICE_FACTOR`` x catalog price (providers that honour the
    routing bill more than the catalog's cheapest endpoint); *high* = a
    repair per call (input re-sent with the answer) and every answer at
    max_tokens, at ``HIGH_PRICE_FACTOR`` x catalog price, capped by the
    per-replicate ``max_cost_usd``. Cache discounts are ignored. The agent
    engine is estimated from typical exploration (~20 calls re-sending ~8k
    tokens of context + part of the code) and bounded by its budget. All
    values are per target: *low* and *expected* for the k planned
    replicates, *high* for the most replicates the review can run (k +
    ``max_replacements``: every replicate fails and is replaced),
    ``max_samples``. Calibration of the token and price figures: see the
    constants above.
    """
    model = frozen.get("model") or {}
    pricing = model.get("pricing") or {}
    in_price = float(pricing.get("input_per_token") or 0.0)
    out_price = float(pricing.get("output_per_token") or 0.0)
    params = frozen.get("params") or {}
    max_tokens = int(params.get("max_tokens") or DEFAULT_MAX_OUTPUT_TOKENS)
    k = int(frozen.get("samples") or 1)
    # A plan frozen before replacements existed runs none.
    max_samples = k + int(frozen.get("max_replacements") or 0)
    budget_cap = float((frozen.get("budget") or {}).get("max_cost_usd") or 0.0) or math.inf
    reasoning = min(_reasoning_tokens(frozen), max_tokens)

    def usd(tokens_in: float, tokens_out: float) -> float:
        return tokens_in * in_price + tokens_out * out_price

    if files <= 0 or chars <= 0:
        zero = {"low": 0.0, "expected": 0.0, "high": 0.0}
        return {
            **zero,
            "per_sample": dict(zero),
            "tokens": {"input": 0, "output": 0},
            "calls": 0,
            "samples": k,
            "max_samples": max_samples,
        }

    if frozen.get("engine") == "agent":
        code_tokens = chars / CHARS_PER_TOKEN
        tokens_in = AGENT_EXPECTED_CALLS * AGENT_BASE_CONTEXT_TOKENS + 2 * code_tokens
        tokens_out = AGENT_EXPECTED_CALLS * (AGENT_ANSWER_TOKENS + reasoning // 4)
        expected = min(usd(tokens_in, tokens_out) * ROUTED_PRICE_FACTOR, budget_cap)
        per_sample = {
            "low": min(usd(tokens_in, tokens_out) / 4, budget_cap),
            "expected": expected,
            "high": budget_cap if budget_cap < math.inf else expected * 3,
        }
        calls = AGENT_EXPECTED_CALLS
        method = "agent heuristic: ~20 calls x 8k context tokens + 2x code tokens; high = per-replicate budget"
    else:
        chunking = frozen.get("chunking") or {}
        chunk_chars = int(chunking.get("effective_chars") or frozen.get("chunk_char_budget") or 300_000)
        rendered = chars * LINE_PREFIX_FACTOR + files * FILE_HEADER_CHARS
        calls = max(1, math.ceil(rendered / chunk_chars))
        overhead = prompt.overhead_chars(
            frozen.get("prompt_version") or prompt.PROMPT_VERSION,
            frozen.get("schema_version") or prompt.SCHEMA_VERSION,
        )
        tokens_in = (rendered + calls * overhead) / CHARS_PER_TOKEN
        tokens_out = calls * min(max_tokens, EXPECTED_ANSWER_TOKENS + reasoning)
        low_out = calls * min(max_tokens, LOW_ANSWER_TOKENS + reasoning // 2)
        # Worst case: a repair per call (input + the answer re-sent), both answers at the cap.
        high_in = 2 * tokens_in * 1.25 + calls * max_tokens
        high_out = 2 * calls * max_tokens
        per_sample = {
            "low": min(usd(tokens_in * 0.9, low_out), budget_cap),
            "expected": min(usd(tokens_in, tokens_out) * ROUTED_PRICE_FACTOR, budget_cap),
            "high": min(usd(high_in, high_out) * HIGH_PRICE_FACTOR, budget_cap),
        }
        method = (
            "single-shot: input = (chars x 1.15 + 90/file + calls x prompt overhead) / 3.5 chars per token; "
            f"output = calls x (1.5k answer + reasoning by effort), capped at max_tokens; expected at "
            f"{ROUTED_PRICE_FACTOR}x catalog price; high = repair per call at max_tokens and {HIGH_PRICE_FACTOR}x "
            "catalog price, capped by max_cost_usd"
        )
    return {
        "low": round(per_sample["low"] * k, 6),
        "expected": round(per_sample["expected"] * k, 6),
        "high": round(per_sample["high"] * max_samples, 6),
        "per_sample": {key: round(value, 6) for key, value in per_sample.items()},
        "tokens": {"input": round(tokens_in * k), "output": round(tokens_out * k)},
        "calls": calls * k,
        "samples": k,
        "max_samples": max_samples,
        "method": method,
    }


# --------------------------------------------------------------------------
# UI options
# --------------------------------------------------------------------------


def reviewer_options() -> dict[str, Any]:
    """For the UI: engines, harnesses (+ option_schema/defaults), default model,
    prompt versions, limits."""
    from backend.analysis.engine.reviewer import agentic

    return {
        "engines": [
            {
                "name": "single_shot",
                "label": "Single-shot",
                "description": (
                    "The in-scope files, line-numbered, in as few structured-output calls as the model's "
                    "context allows. No tools; cheapest and most reproducible."
                ),
                "requires_harness": False,
            },
            {
                "name": "agent",
                "label": "Agent (harness)",
                "description": (
                    "A coding-agent harness explores a read-only copy of the app in a sandbox behind the "
                    "LLM gateway and writes a findings file validated against the same schema."
                ),
                "requires_harness": True,
            },
        ],
        "harnesses": agentic.harness_review_options(),
        "default_model": _default_model(),
        "defaults": default_reviewer_config(),
        "prompt_versions": prompt.prompt_versions(),
        "schema_versions": prompt.schema_versions(),
        "reasoning_efforts": list(REASONING_EFFORTS),
        "output_modes": list(OUTPUT_MODES),
        "categories": list(prompt.CATEGORIES),
        "severities": list(prompt.FINDING_SEVERITIES),
        "include_origins": {"choices": list(prompt.ORIGIN_CHOICES), "default": list(prompt.DEFAULT_INCLUDE_ORIGINS)},
        "include_kinds": {"choices": list(prompt.KIND_CHOICES), "default": list(prompt.DEFAULT_INCLUDE_KINDS)},
        "limits": {
            **{
                key: {"type": "int" if kind is int else "float", "min": lo, "max": hi}
                for key, (kind, lo, hi) in _LIMITS.items()
            },
            "budget": {
                key: {"type": "int" if kind is int else "float", "min": lo, "max": hi}
                for key, (kind, lo, hi) in _BUDGET_LIMITS.items()
            },
        },
        "engine_version": REVIEWER_ENGINE_VERSION,
    }
