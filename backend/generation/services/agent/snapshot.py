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
* ``bundle_key`` = the *method* identity (harness@version, plus the profile
  when one supplied the stack/llm_config) -- the condition slicing key.
* ``run_fingerprint`` = everything that can change behaviour; equal
  fingerprints mean replicate runs.
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.services.agent import brief as brief_mod
from backend.generation.services.agent.config import default_provider_routing
from backend.generation.services.agent.config import merge_agent_config
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.agent.harnesses.base import SANDBOX_REVISION
from backend.generation.services.agent.images import harness_image_plan
from backend.generation.services.agent.images import image_tag
from backend.generation.services.evaluation import contract as contract_mod
from backend.generation.services.llm_params import merge_llm_params
from backend.generation.services.profile_resolver import _attach_model_provenance
from backend.generation.services.profile_resolver import app_requirement_to_dict
from backend.runtime.services.scaffolding import canonical_stack_slug
from backend.runtime.services.scaffolding import get_stack_config
from backend.runtime.services.scaffolding import stack_snapshot_entry

if TYPE_CHECKING:
    from backend.generation.models import AppRequirementTemplate
    from backend.generation.models import GenerationJob
    from backend.generation.models import GenerationProfile
    from backend.llm_models.models import LLMModel

AGENT_SNAPSHOT_SCHEMA_VERSION = 2

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


def _supported_params(model: LLMModel | None) -> set[str] | None:
    """OpenRouter's supported-parameter list for *model*, or None if unknown."""
    if model is None:
        return None
    supported = (model.metadata or {}).get("openrouter_supported_parameters")
    return set(supported) if isinstance(supported, list) and supported else None


def gateway_policy(
    llm: dict[str, Any],
    seed: int | None,
    model: LLMModel | None,
    *,
    sampling_policy: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    """What the gateway forces and strips on every request of this run."""
    supported = _supported_params(model)
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
            dropped[key] = "not supported by this model on OpenRouter"
            continue
        forced[key] = value
    for key in NOT_APPLICABLE_KEYS:
        if llm.get(key) is not None:
            dropped[key] = "not applicable to agent runs"
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


def build_agent_snapshot(
    *,
    app_requirement: AppRequirementTemplate | None,
    description: str,
    profile: GenerationProfile | None,
    stack_slug: str,
    model: LLMModel | None,
    max_tokens: int,
    experiment_seed: int | None = None,
    llm_overrides: dict[str, Any] | None = None,
    agent_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if model is None:
        msg = "Agent runs need a model"
        raise ValueError(msg)
    if app_requirement is None and not description.strip():
        msg = "Agent runs need an app spec or a task description"
        raise ValueError(msg)

    stack_slug = canonical_stack_slug(stack_slug or (profile.scaffolding_slug if profile else "flask-react"))
    stack_config = get_stack_config(stack_slug)
    agent_config = merge_agent_config(agent_overrides)
    harness = get_harness(agent_config["harness"])
    if harness.requires_tool_calling and not model.supports_function_calling:
        msg = f"{harness.label} needs a tool-calling model; {model.model_id} does not support tools"
        raise ValueError(msg)

    # No implicit temperature: sampling params exist only when a profile,
    # experiment or run sets them. Everything else samples at the provider
    # default (see sampling policy).
    llm = merge_llm_params(
        {"max_tokens": max_tokens},
        dict(profile.llm_config or {}) if profile else {},
        llm_overrides,
    )
    routing_source = "explicit" if isinstance(llm.get("provider"), dict) else "default"
    if routing_source == "default":
        llm["provider"] = default_provider_routing()
    llm["provider_routing_source"] = routing_source
    _attach_model_provenance(llm, model)
    # The dated snapshot behind an alias like "vendor/model": an alias can be
    # repointed mid-experiment, the canonical slug cannot.
    llm["model_canonical_slug"] = model.canonical_slug

    # Reproducibility seed for the run, not a crypto value.
    seed = experiment_seed if experiment_seed is not None else random.randint(0, 2_147_483_647)  # noqa: S311
    context_window = model.context_window or 128_000
    model_output_cap = model.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS
    max_output = min(int(llm.get("max_tokens") or DEFAULT_MAX_OUTPUT_TOKENS), model_output_cap)
    gateway = gateway_policy(
        llm,
        seed,
        model,
        sampling_policy=agent_config["sampling_policy"],
        max_output_tokens=max_output,
    )

    app_dict = app_requirement_to_dict(app_requirement) if app_requirement else None
    brief_text = brief_mod.render_brief(
        app=app_dict,
        description=description,
        stack_slug=stack_slug,
        stack_config=stack_config,
        agent_config=agent_config,
    )
    section = harness.snapshot_section(
        model_slug=model.model_id,
        context_window=context_window,
        max_output_tokens=max_output,
        agent_config=agent_config,
        title="llm-lab-agent-run",
    )
    section["sandbox_revision"] = SANDBOX_REVISION
    section["requires_tool_calling"] = harness.requires_tool_calling
    plan = harness_image_plan(harness.image, harness.build_args(section["version"]))
    images = {
        "base": plan[0][2],
        "harness": plan[1][2],
        "gateway": image_tag("llm-gateway"),
    }

    method_key = f"agent:{harness.name}@{section['version']}"
    snapshot: dict[str, Any] = {
        "bundle_schema_version": AGENT_SNAPSHOT_SCHEMA_VERSION,
        "mode": "agent",
        "bundle_slug": profile.slug if profile else f"agent-{harness.name}",
        "bundle_version": profile.version if profile else brief_mod.BRIEF_VERSION,
        "method_key": f"{profile.slug}@{profile.version}/{method_key}" if profile else method_key,
        "scaffolding_slug": stack_slug,
        "app_requirement": app_dict,
        "description": description,
        "seed": seed,
        "llm": llm,
        "llm_overrides": dict(llm_overrides or {}),
        "gateway": gateway,
        "agent": agent_config,
        "harness": section,
        "images": images,
        "brief": {
            "version": brief_mod.BRIEF_VERSION,
            "contract_version": contract_mod.CONTRACT_VERSION,
            "text": brief_text,
            "sha256": brief_mod.sha256_text(brief_text),
            "protected_files": list(brief_mod.PROTECTED_FILES),
        },
        "contract": contract_mod.describe(app_dict),
    }
    if stack_entry := stack_snapshot_entry(stack_slug):
        snapshot["stack"] = stack_entry
    snapshot["prompt_hash"] = agent_prompt_hash(snapshot)
    snapshot["run_fingerprint"] = agent_run_fingerprint(snapshot)
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


def agent_run_fingerprint(snapshot: dict[str, Any]) -> str:
    """Everything that can change an agent run's behaviour."""
    h = {k: v for k, v in snapshot["harness"].items() if k != "title"}
    return _short_hash(
        {
            "prompt_hash": snapshot["prompt_hash"],
            "model_slug": snapshot["llm"].get("model_slug"),
            "gateway": snapshot["gateway"],
            "harness": h,
            "images": snapshot.get("images"),
            "agent": snapshot["agent"],
            "stack_content_hash": (snapshot.get("stack") or {}).get("content_hash"),
            "scaffolding_slug": snapshot["scaffolding_slug"],
        },
    )


def reseed_agent_snapshot(snapshot: dict[str, Any], seed: int, model: LLMModel | None) -> dict[str, Any]:
    """Copy of *snapshot* with a new seed (an additional trial of the same configuration)."""
    snapshot = json.loads(json.dumps(snapshot))
    snapshot["seed"] = seed
    snapshot["gateway"] = gateway_policy(
        snapshot["llm"],
        seed,
        model,
        sampling_policy=snapshot["gateway"].get("sampling_policy", "provider_default"),
        max_output_tokens=snapshot["gateway"]["max_output_tokens"],
    )
    snapshot["run_fingerprint"] = agent_run_fingerprint(snapshot)
    return snapshot


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
