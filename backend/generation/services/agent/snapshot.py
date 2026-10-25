"""Freeze everything an agent-mode run depends on into ``GenerationJob.resolved_bundle``.

The snapshot is written once at job creation and never changes, so a job can
always be explained (and replayed) from its own row:

* ``brief`` -- the exact task text the agent received (+ sha256),
* ``harness`` -- which harness, its pinned release, prompts, config, env,
* ``images`` -- content-addressed tags of the sandbox image chain,
* ``llm`` -- model (+ dated canonical slug), explicitly configured params,
  routing,
* ``gateway`` -- what the gateway forces / strips on every request,
* ``agent`` -- harness choice, budgets, sampling and network policy, caps,
* ``stack`` -- the exact skeleton version the workspace is seeded from.

Keys:

* ``prompt_hash`` = the *task* identity (brief + evaluation contract), shared
  across harnesses, models and seeds -- the grouping key for comparisons.
* ``method_key`` (stored as ``bundle_key``) = the *method* identity:
  ``[profile@v/]agent:<harness>@<version>`` plus content-addressed suffixes
  for anything that departs from the defaults -- ``+opts:<sha8>`` (harness
  options), ``+llm:<sha8>`` (explicit sampling params / sampling policy),
  ``+budget:<sha8>`` (limits or network policy). Hashing content rather than
  preset names means a renamed preset never splits a slice, and a default
  run keeps the key it always had.
* ``run_fingerprint`` = everything that can change behaviour, seed included:
  equal fingerprints mean exact replays.
* ``config_fingerprint`` = the same inputs minus the seed: the condition
  identity that replicates (trials) share.

``presets`` records which run presets the job was created from (id, slug,
version, content hash, name). It is provenance only and enters no key: the
values the presets stood for are already in the sections above.

:func:`resolve_agent_run` is the single resolution path; the job-creation
preview and experiment comparability use it too, so what they report is
what the snapshot freezes.
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings

from backend.generation.services.agent import brief as brief_mod
from backend.generation.services.agent.config import BUDGET_KEYS
from backend.generation.services.agent.config import default_agent_config
from backend.generation.services.agent.config import default_provider_routing
from backend.generation.services.agent.config import merge_agent_config
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.agent.harnesses.base import SANDBOX_REVISION
from backend.generation.services.agent.harnesses.base import LocalRender
from backend.generation.services.agent.images import gateway_image_plan
from backend.generation.services.agent.images import harness_image_plan
from backend.generation.services.agent.images import image_tag
from backend.generation.services.evaluation import contract as contract_mod
from backend.generation.services.llm_params import merge_llm_params
from backend.generation.services.profile_resolver import _attach_model_provenance
from backend.generation.services.profile_resolver import app_requirement_to_dict
from backend.generation.services.profile_resolver import coerce_float_params
from backend.generation.services.versioning import canonical_json
from backend.generation.services.versioning import short_hash
from backend.inference.snapshot import effective_num_ctx
from backend.inference.snapshot import endpoint_section
from backend.inference.snapshot import is_local
from backend.inference.snapshot import llm_source
from backend.runtime.services.scaffolding import canonical_stack_slug
from backend.runtime.services.scaffolding import get_stack_config
from backend.runtime.services.scaffolding import stack_snapshot_entry

if TYPE_CHECKING:
    from backend.generation.models import AppRequirementTemplate
    from backend.generation.models import GenerationJob
    from backend.generation.models import GenerationProfile
    from backend.generation.services.agent.harnesses.base import Harness
    from backend.llm_models.models import LLMModel

# Readers accept any version >= this (runner.py). The presets,
# harness_options and config_fingerprint keys and llm.context_window_source
# were added without a bump: they are additive, so v2 snapshots stay
# replayable. So are the local-endpoint additions (spec §D.5): the
# ``endpoint`` section and the ``llm_source`` fingerprint input exist only
# for models on a non-OpenRouter endpoint, so OpenRouter snapshots and
# fingerprints are byte-identical.
AGENT_SNAPSHOT_SCHEMA_VERSION = 2
AGENT_RUN_TITLE = "llm-lab-agent-run"
PRESET_KINDS = ("harness", "sampling", "budget")

# Sampling params a run may configure explicitly; the gateway then forces
# them onto every request (when the model supports them).
GATEWAY_PARAM_KEYS = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "frequency_penalty",
    "presence_penalty",
    "repetition_penalty",
    "reasoning",
)
# Everything a harness may itself send that changes how the model samples or
# reasons. Under sampling policy provider_default these are stripped from
# harness requests before the explicit params are applied.
HARNESS_SAMPLING_KEYS = (
    *GATEWAY_PARAM_KEYS,
    "reasoning_effort",
    "include_reasoning",
    "verbosity",
    "thinking",
    "logit_bias",
)
# Never under harness control: routing, seed and model fallbacks belong to the
# job, and stop/response_format/n would break or multiply agent turns.
ALWAYS_STRIPPED = ("stop", "response_format", "seed", "provider", "models", "route", "transforms", "n")
NOT_APPLICABLE_KEYS = ("stop", "response_format")
DEFAULT_MAX_OUTPUT_TOKENS = 32000
# Used when the catalog row has no context window (recorded as
# llm.context_window_source = "fallback").
FALLBACK_CONTEXT_WINDOW = 128_000
# Explicit llm params that do not distinguish a method: the output cap is a
# per-call safety limit (resolved per model anyway) and the seed is per run.
_METHOD_EXCLUDED_LLM_KEYS = ("max_tokens", "seed")


def _supported_params(model: LLMModel | None) -> set[str] | None:
    """OpenRouter's supported-parameter list for *model*, or None if unknown.

    Unknown (no list, or an empty one, as some router pseudo-models have)
    is not "supports nothing": those params are forwarded unverified rather
    than dropped. Dropping them would silently change every default run on
    such a model (its seed would stop being forwarded), while forwarding
    stays honest: the default routing sets ``require_parameters``, so a
    provider that cannot honour a param fails the run instead of ignoring
    it. The snapshot records ``llm.supported_params_source = "unknown"`` and
    the preview warns ``support_unknown`` before launch.

    Local models (Ollama / OpenAI-compatible endpoints) use the endpoint's
    static parameter table instead (``supported_params_source =
    "endpoint_static"``).
    """
    if model is None:
        return None
    if is_local(model):
        from backend.inference.params import STATIC_PARAMS

        return set(STATIC_PARAMS.get((model.endpoint.kind, model.endpoint.api_flavor), ()))
    supported = (model.metadata or {}).get("openrouter_supported_parameters")
    return set(supported) if isinstance(supported, list) and supported else None


def gateway_policy(
    llm: dict[str, Any],
    seed: int | None,
    model: LLMModel | None,
    *,
    sampling_policy: str,
    max_output_tokens: int,
    local: bool = False,
) -> dict[str, Any]:
    """What the gateway forces and strips on every request of this run.

    *local* (a model on a non-OpenRouter endpoint) forces no ``provider``
    routing; OpenRouter policies are unchanged.
    """
    supported = _supported_params(model)
    where = f"{model.endpoint.slug} ({model.endpoint.kind})" if local and model is not None else "OpenRouter"
    forced: dict[str, Any] = {}
    dropped: dict[str, str] = {}
    candidates = {k: llm[k] for k in GATEWAY_PARAM_KEYS if llm.get(k) is not None}
    if seed is not None:
        candidates["seed"] = seed
    for key, value in candidates.items():
        if supported is not None and key not in supported:
            # Forwarding it with require_parameters would leave no provider;
            # without it the provider would silently ignore it. Either way
            # the run would not be what the snapshot claims.
            dropped[key] = f"not supported by this model on {where}"
            continue
        forced[key] = value
    for key in NOT_APPLICABLE_KEYS:
        if llm.get(key) is not None:
            dropped[key] = "not applicable to agent runs"
    if not local:
        forced["provider"] = llm["provider"]
    # One per-call output cap for every harness (some send none and would get
    # a provider-specific default, others send their own).
    forced["max_tokens"] = max_output_tokens
    stripped = list(ALWAYS_STRIPPED)
    if sampling_policy == "provider_default":
        stripped += [k for k in HARNESS_SAMPLING_KEYS if k not in forced]
    return {
        "forced_params": forced,
        "dropped_params": dropped,
        "stripped_params": sorted(set(stripped)),
        "sampling_policy": sampling_policy,
        "max_output_tokens": max_output_tokens,
    }


def agent_llm_layers(
    max_tokens: int | None,
    profile: GenerationProfile | None,
    llm_overrides: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, ...]:
    """The llm param layers of an agent job, lowest first (its own fields, as the snapshot reads them)."""
    return ({"max_tokens": max_tokens}, dict(profile.llm_config or {}) if profile else {}, llm_overrides)


def resolve_llm(
    *,
    model: LLMModel,
    llm_layers: tuple[dict[str, Any] | None, ...] | list[dict[str, Any] | None],
    seed: int | None,
    sampling_policy: str,
) -> dict[str, Any]:
    """The model side of a run: merged params, routing, output cap, context window, gateway policy.

    No implicit temperature: sampling params exist only when a layer sets
    them; everything else samples at the provider default (see sampling
    policy). Returns ``{llm, explicit_llm, gateway, output_cap,
    context_window, context_window_source, seed_forwarded, params_support,
    unverified_params}``: ``params_support`` is ``"catalog"`` or
    ``"unknown"`` (see :func:`_supported_params`), and ``unverified_params``
    the forwarded params no catalog list vouches for.
    """
    # Float-kind params as floats, whichever layer set them (see coerce_float_params).
    llm = coerce_float_params(merge_llm_params(*llm_layers))
    explicit = dict(llm)
    local = is_local(model)
    if local:
        # OpenRouter provider routing means nothing to a local endpoint.
        llm["provider"] = None
        routing_source = "not_applicable"
    else:
        routing_source = "explicit" if isinstance(llm.get("provider"), dict) else "default"
        if routing_source == "default":
            llm["provider"] = default_provider_routing()
    llm["provider_routing_source"] = routing_source
    _attach_model_provenance(llm, model)
    # The dated snapshot behind an alias like "vendor/model": an alias can be
    # repointed mid-experiment, the canonical slug cannot.
    llm["model_canonical_slug"] = model.canonical_slug
    if local:
        # Harness limits and the output cap must match what the server runs
        # with (num_ctx), not a 128k fallback.
        context_window = effective_num_ctx(model) or model.context_window or FALLBACK_CONTEXT_WINDOW
        context_window_source = "endpoint_num_ctx"
        params_support = "endpoint_static"
    else:
        context_window = model.context_window or FALLBACK_CONTEXT_WINDOW
        context_window_source = "catalog" if model.context_window else "fallback"
        # Provenance like context_window_source (the llm section is not fingerprinted).
        params_support = "catalog" if _supported_params(model) is not None else "unknown"
    llm["context_window_source"] = context_window_source
    llm["supported_params_source"] = params_support
    model_output_cap = model.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS
    output_cap = min(int(llm.get("max_tokens") or DEFAULT_MAX_OUTPUT_TOKENS), model_output_cap)
    if local:
        output_cap = min(output_cap, int(context_window))
    gateway = gateway_policy(
        llm,
        seed,
        model,
        sampling_policy=sampling_policy,
        max_output_tokens=output_cap,
        **({"local": True} if local else {}),
    )
    return {
        "llm": llm,
        "explicit_llm": explicit,
        "gateway": gateway,
        "output_cap": output_cap,
        "context_window": context_window,
        "context_window_source": context_window_source,
        "seed_forwarded": "seed" in gateway["forced_params"],
        "params_support": params_support,
        "unverified_params": (
            sorted(k for k in gateway["forced_params"] if k not in ("provider", "max_tokens"))
            if params_support == "unknown"
            else []
        ),
    }


def resolve_agent_run(
    *,
    model: LLMModel,
    stack_slug: str,
    agent_layers: tuple[dict[str, Any] | None, ...] | list[dict[str, Any] | None] = (),
    llm_layers: tuple[dict[str, Any] | None, ...] | list[dict[str, Any] | None] = (),
    harness_options: dict[str, Any] | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Resolve one run exactly as its snapshot will freeze it (no brief, no writes).

    *agent_layers* are merged over the platform defaults (the harness is the
    merged ``harness`` key); *llm_layers* lowest first (see
    :func:`agent_llm_layers`); *harness_options* the sparse options. *seed*
    may be a placeholder when only seed support matters (previews).

    Raises what the snapshot raises: ``ValueError`` for an unknown harness,
    a harness the model cannot drive, invalid options or a missing harness
    capture; ``KeyError`` for an unknown stack. Returns ``resolve_llm``'s
    keys plus ``harness`` (adapter), ``harness_options`` (normalized),
    ``agent``, ``harness_section``, ``stack_slug`` and ``stack_config``.
    """
    stack_slug = canonical_stack_slug(stack_slug)
    stack_config = get_stack_config(stack_slug)
    agent_config = merge_agent_config(*agent_layers)
    harness = get_harness(agent_config["harness"])
    if harness.requires_tool_calling and not model.supports_function_calling:
        msg = f"{harness.label} needs a tool-calling model; {model.model_id} does not support tools"
        raise ValueError(msg)
    options = harness.normalize_options(harness_options)
    run = resolve_llm(model=model, llm_layers=llm_layers, seed=seed, sampling_policy=agent_config["sampling_policy"])
    local_render = local_render_inputs(model)
    section = harness.snapshot_section(
        model_slug=model.model_id,
        context_window=run["context_window"],
        max_output_tokens=run["output_cap"],
        agent_config=agent_config,
        title=AGENT_RUN_TITLE,
        options=options,
        # Only passed for local models, so OpenRouter sections render exactly as before.
        **({"local": local_render} if local_render is not None else {}),
    )
    section["sandbox_revision"] = SANDBOX_REVISION
    section["requires_tool_calling"] = harness.requires_tool_calling
    return {
        **run,
        "harness": harness,
        "harness_options": options,
        "agent": agent_config,
        "harness_section": section,
        "stack_slug": stack_slug,
        "stack_config": stack_config,
    }


def local_render_inputs(model: LLMModel | None) -> LocalRender | None:
    """What a harness renders differently for a local model (client timeout, capabilities); None for OpenRouter."""
    section = endpoint_section(model)
    if section is None:
        return None
    return LocalRender(
        client_timeout_s=int(section["client_timeout_s"]),
        capabilities=tuple(section.get("capabilities") or ()),
    )


def method_key(
    *,
    harness: Harness,
    harness_version: str,
    profile: GenerationProfile | None,
    harness_options: dict[str, Any],
    explicit_llm: dict[str, Any],
    agent_config: dict[str, Any],
) -> str:
    """``[profile@v/]agent:<harness>@<ver>`` plus content-addressed suffixes (see the module docstring)."""
    key = f"agent:{harness.name}@{harness_version}"
    if profile is not None:
        key = f"{profile.slug}@{profile.version}/{key}"
    if harness_options:
        key += f"+opts:{short_hash(harness_options)}"
    defaults = default_agent_config()
    sampling: dict[str, Any] = {}
    llm_params = {k: v for k, v in explicit_llm.items() if k not in _METHOD_EXCLUDED_LLM_KEYS}
    if llm_params:
        sampling["llm_params"] = llm_params
    if agent_config["sampling_policy"] != defaults["sampling_policy"]:
        sampling["sampling_policy"] = agent_config["sampling_policy"]
    if sampling:
        key += f"+llm:{short_hash(sampling)}"
    budget = {k: agent_config[k] for k in BUDGET_KEYS}
    if canonical_json(budget) != canonical_json({k: defaults[k] for k in BUDGET_KEYS}):
        key += f"+budget:{short_hash(budget)}"
    return key


def build_agent_snapshot(
    *,
    app_requirement: AppRequirementTemplate | None,
    description: str,
    profile: GenerationProfile | None,
    stack_slug: str | None,
    model: LLMModel | None,
    max_tokens: int | None,
    experiment_seed: int | None = None,
    llm_overrides: dict[str, Any] | None = None,
    agent_overrides: dict[str, Any] | None = None,
    harness_options: dict[str, Any] | None = None,
    presets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if model is None:
        msg = "Agent runs need a model"
        raise ValueError(msg)
    if app_requirement is None and not description.strip():
        msg = "Agent runs need an app spec or a task description"
        raise ValueError(msg)

    stack_slug = canonical_stack_slug(
        stack_slug or (profile.scaffolding_slug if profile else settings.GENERATION_DEFAULT_STACK),
    )
    # Reproducibility seed for the run, not a crypto value.
    seed = experiment_seed if experiment_seed is not None else random.randint(0, 2_147_483_647)  # noqa: S311
    run = resolve_agent_run(
        model=model,
        stack_slug=stack_slug,
        agent_layers=(agent_overrides,),
        llm_layers=agent_llm_layers(max_tokens, profile, llm_overrides),
        harness_options=harness_options,
        seed=seed,
    )
    harness = run["harness"]
    agent_config = run["agent"]
    section = run["harness_section"]

    app_dict = app_requirement_to_dict(app_requirement) if app_requirement else None
    brief_text = brief_mod.render_brief(
        app=app_dict,
        description=description,
        stack_slug=stack_slug,
        stack_config=run["stack_config"],
        agent_config=agent_config,
    )
    plan = harness_image_plan(harness.image, harness.build_args(section["version"]))
    endpoint = endpoint_section(model)
    images = {
        "base": plan[0][2],
        "harness": plan[1][2],
        # Local models run the overlay image, whose tag also pins the base gateway.
        "gateway": gateway_image_plan(local=True)[-1][2] if endpoint else image_tag("llm-gateway"),
    }

    snapshot: dict[str, Any] = {
        "bundle_schema_version": AGENT_SNAPSHOT_SCHEMA_VERSION,
        "mode": "agent",
        "bundle_slug": profile.slug if profile else f"agent-{harness.name}",
        "bundle_version": profile.version if profile else brief_mod.BRIEF_VERSION,
        "method_key": method_key(
            harness=harness,
            harness_version=section["version"],
            profile=profile,
            harness_options=run["harness_options"],
            explicit_llm=run["explicit_llm"],
            agent_config=agent_config,
        ),
        "scaffolding_slug": stack_slug,
        "app_requirement": app_dict,
        "description": description,
        "seed": seed,
        "llm": run["llm"],
        "llm_overrides": dict(llm_overrides or {}),
        "gateway": run["gateway"],
        "agent": agent_config,
        "harness": section,
        # Already rendered into the harness section; kept as provenance.
        "harness_options": run["harness_options"],
        "images": images,
        "brief": {
            "version": brief_mod.BRIEF_VERSION,
            "contract_version": contract_mod.CONTRACT_VERSION,
            "text": brief_text,
            "sha256": brief_mod.sha256_text(brief_text),
            "protected_files": list(brief_mod.PROTECTED_FILES),
        },
        "contract": contract_mod.describe(app_dict),
        "presets": {kind: (presets or {}).get(kind) for kind in PRESET_KINDS},
    }
    if stack_entry := stack_snapshot_entry(stack_slug):
        snapshot["stack"] = stack_entry
    if endpoint:
        snapshot["endpoint"] = endpoint
    snapshot["prompt_hash"] = agent_prompt_hash(snapshot)
    snapshot["run_fingerprint"] = agent_run_fingerprint(snapshot)
    snapshot["config_fingerprint"] = agent_config_fingerprint(snapshot)
    return snapshot


def _short_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def agent_prompt_hash(snapshot: dict[str, Any]) -> str:
    """Task identity: same brief and contract, whatever harness, model or seed runs it."""
    return _short_hash(
        {
            "mode": "agent",
            "brief_sha256": snapshot["brief"]["sha256"],
            "contract_version": snapshot["brief"].get("contract_version"),
        },
    )


def _fingerprint_inputs(snapshot: dict[str, Any]) -> dict[str, Any]:
    h = {k: v for k, v in snapshot["harness"].items() if k != "title"}
    inputs = {
        "prompt_hash": snapshot["prompt_hash"],
        "model_slug": snapshot["llm"].get("model_slug"),
        "gateway": snapshot["gateway"],
        "harness": h,
        "images": snapshot.get("images"),
        "agent": snapshot["agent"],
        "stack_content_hash": (snapshot.get("stack") or {}).get("content_hash"),
        "scaffolding_slug": snapshot["scaffolding_slug"],
    }
    # Local models only: which weights on which server flavour (absent for OpenRouter).
    if src := llm_source(snapshot):
        inputs["llm_source"] = src
    return inputs


def agent_run_fingerprint(snapshot: dict[str, Any]) -> str:
    """Everything that can change an agent run's behaviour (seed included)."""
    return _short_hash(_fingerprint_inputs(snapshot))


def agent_config_fingerprint(snapshot: dict[str, Any]) -> str:
    """``run_fingerprint`` minus the seed: the condition identity replicates share.

    The seed only reaches the fingerprint as ``gateway.forced_params.seed``
    (and only for models that take one), so dropping it there makes trials of
    every model group alike.
    """
    inputs = _fingerprint_inputs(snapshot)
    gateway = dict(inputs["gateway"])
    gateway["forced_params"] = {k: v for k, v in (gateway.get("forced_params") or {}).items() if k != "seed"}
    return _short_hash({"identity": "config", **inputs, "gateway": gateway})


def reseed_agent_snapshot(snapshot: dict[str, Any], seed: int) -> dict[str, Any]:
    """Copy of *snapshot* with a new seed (an additional trial of the same configuration).

    Only the seed changes. The frozen gateway policy is kept as it is rather
    than recomputed from today's catalog: a catalog sync that changed the
    model's supported parameters must not make the extra trial force or drop
    other params than its siblings. A seed the original forwarded is
    replaced; one it dropped (unsupported) stays dropped.
    """
    snapshot = json.loads(json.dumps(snapshot))
    snapshot["seed"] = seed
    forced = snapshot["gateway"].get("forced_params") or {}
    if "seed" in forced:
        forced["seed"] = seed
    snapshot["run_fingerprint"] = agent_run_fingerprint(snapshot)
    snapshot["config_fingerprint"] = agent_config_fingerprint(snapshot)
    return snapshot


def presets_provenance(job: GenerationJob) -> dict[str, Any]:
    """``snapshot.presets`` for *job*: what its preset FKs point at (labels only)."""
    from backend.generation.services.presets import provenance

    return {
        "harness": provenance(job.harness_preset),
        "sampling": provenance(job.sampling_preset),
        "budget": provenance(job.budget_preset),
    }


def apply_agent_snapshot_to_job(job: GenerationJob, *, save: bool = True) -> GenerationJob:
    """Build the snapshot from the job's own fields and store it with its slicing keys."""
    snapshot = build_agent_snapshot(
        app_requirement=job.app_requirement,
        description=job.task_description,
        profile=job.profile,
        stack_slug=job.stack_slug,
        model=job.model,
        max_tokens=job.max_tokens,
        experiment_seed=job.experiment_seed,
        llm_overrides=job.llm_params or {},
        agent_overrides=job.agent_config or {},
        harness_options=job.harness_options or {},
        presets=presets_provenance(job),
    )
    job.stack_slug = snapshot["scaffolding_slug"]
    job.experiment_seed = snapshot["seed"]
    job.resolved_bundle = snapshot
    job.prompt_hash = snapshot["prompt_hash"]
    job.bundle_key = snapshot["method_key"][:220]
    if save:
        job.save(
            update_fields=[
                "stack_slug",
                "experiment_seed",
                "resolved_bundle",
                "prompt_hash",
                "bundle_key",
                "updated_at",
            ],
        )
    return job
