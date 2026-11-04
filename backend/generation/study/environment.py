"""The execution environment of a condition: harness, gateway, sandbox and image digests (sections 3.8, 5, 6.3).

The resolver needs, per condition, the manifest's ``harness``, ``gateway`` and ``sandbox`` sections, the image
digests that enter ``config_fingerprint`` and the treatment's gateway mode. They come from the harness adapters
(WP-07, ``harnesses.registry``), the image pinning (WP-08, ``agentic.images``) and the policy;
this module assembles them through those published interfaces only. It is bound as ``StudyDeps.environment``
so tests replace it (``generation/testing/fake_study.py``).

Rules applied here (never silently relaxed):

* the harness issues come from ``harnesses.registry.plan_issues`` (WP-07): ``harness_not_enabled`` (open
  questions, disabled), ``harness_protocol_unavailable_for_endpoint``, ``output_cap_below_harness_min``,
  ``model_lacks_tools`` and ``vendor_affinity``; a preset whose ``harness_version`` is not one of the adapter's
  verified ``versions`` is ``harness_not_enabled`` (``data.reason = "version_not_verified"``);
* the protocol mode is ``adapter.protocol_mode(endpoint_kind=..., translators=settings.GATEWAY_TRANSLATORS)``;
* ``model_lacks_tools`` excludes the cells (``excluded:config_invalid``) instead of blocking the plan;
* image tags are content hashes (``agentic.images.harness_image_plan`` / ``gateway_image_plan``, WP-08); their
  digests are resolved at plan time (``pending:<tag>`` when not built yet, issue ``image_built_after_plan``);
* free-choice stacks render and hash the allowed base images (``build.dockerfiles.allowed_bases_record``);
* the sandbox is resolved once per study (``resolution: study_max``): the policy's memory and CPUs raised to the
  largest stack minimum, then capped by the machine limits ``AGENT_SANDBOX_MEM_LIMIT_MAX`` /
  ``AGENT_SANDBOX_NANO_CPUS_MAX``.

The harness command and config files are rendered once at plan time with the nil run id
(:data:`PLAN_RUN_ID`) and hashed (``command_sha``, ``config_files_sha``): adapters keep the run id out of both
(it reaches the sandbox through the environment), so the hashes are those of every run of the condition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.conf import settings

from backend.generation import identity
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.manifest import ManifestAllowedBases
from backend.generation.contracts.manifest import ManifestGateway
from backend.generation.contracts.manifest import ManifestHarness
from backend.generation.contracts.manifest import ManifestSandbox

if TYPE_CHECKING:
    from backend.generation.contracts.resources import EffectivePolicy
    from backend.generation.models import HarnessPreset
    from backend.generation.models import StackProfile
    from backend.generation.models import Starter
    from backend.llm_models.models import LLMModel

__all__ = [
    "PLAN_RUN_ID",
    "CellEnvironment",
    "EnvironmentRequest",
    "allowed_bases_for",
    "cell_environment",
    "memory_bytes",
    "study_sandbox",
]

PLAN_RUN_ID: Final = "00000000-0000-0000-0000-000000000000"
GATEWAY_HOST: Final = "llm-gateway"
_SIDECAR_KINDS: Final = frozenset({"app_agentic", "slot_agent"})
_MEMORY_RE: Final = re.compile(r"^([1-9][0-9]*)([kmg])$")
_UNITS: Final = {"k": 1024, "m": 1024**2, "g": 1024**3}


@dataclass(frozen=True)
class EnvironmentRequest:
    """What the environment of one condition depends on."""

    kind: str
    model: LLMModel
    policy: EffectivePolicy
    harness_preset: HarnessPreset | None = None
    starter: Starter | None = None
    stack: StackProfile | None = None
    sandbox: ManifestSandbox | None = None  # the study-level sandbox (study_sandbox)
    brief: str = ""
    system_prompt: str | None = None
    study_kind: str = "exploratory"


@dataclass(frozen=True)
class CellEnvironment:
    """Manifest sections and identity inputs of one condition's environment."""

    harness: ManifestHarness | None
    gateway: ManifestGateway | None
    sandbox: ManifestSandbox | None
    images: dict[str, Any]  # identity images: {"harness", "gateway", "starter_base"}
    gateway_treatment: dict[str, str] | None  # {"front_protocol", "protocol_mode"} (treatment_hash)
    allowed_bases: ManifestAllowedBases | None = None
    agentic: bool = True  # harness capabilities.agentic (aider is an editor)
    controlled: bool = False  # the harness preset is a controlled profile (section 5.5)
    vendor_affinity: str | None = None
    excluded: str | None = None  # "config_invalid" when the cells cannot run as designed
    issues: list[Issue] = field(default_factory=list)


def memory_bytes(value: str) -> int:
    """``"3g"`` -> bytes (docker memory strings, binary units)."""
    match = _MEMORY_RE.match(str(value).strip().lower())
    if match is None:
        msg = f"not a memory size: {value!r}"
        raise ValueError(msg)
    return int(match.group(1)) * _UNITS[match.group(2)]


def _memory_str(n_bytes: int) -> str:
    for unit in ("g", "m", "k"):
        if n_bytes % _UNITS[unit] == 0:
            return f"{n_bytes // _UNITS[unit]}{unit}"
    return f"{-(-n_bytes // 1024)}k"


def study_sandbox(policy_memory: str, policy_nano_cpus: int, stacks: list[StackProfile]) -> ManifestSandbox:
    """The study-level sandbox: policy values raised to the stacks' minimums, capped by the machine limits."""
    memory = memory_bytes(policy_memory)
    cpus = int(policy_nano_cpus)
    for stack in stacks:
        resources = (stack.content or {}).get("resources") or {}
        if resources.get("min_memory"):
            memory = max(memory, memory_bytes(resources["min_memory"]))
        if resources.get("min_cpus"):
            cpus = max(cpus, int(float(resources["min_cpus"]) * 1_000_000_000))
    memory = min(memory, memory_bytes(settings.AGENT_SANDBOX_MEM_LIMIT_MAX))
    cpus = min(cpus, int(settings.AGENT_SANDBOX_NANO_CPUS_MAX))
    return ManifestSandbox(memory=_memory_str(memory), nano_cpus=cpus)


def allowed_bases_for(stack: StackProfile | None) -> ManifestAllowedBases | None:
    """The free-choice allowlist rendered into the brief (``build.dockerfiles.allowed_bases_record``, WP-05).

    ``None`` for fixed stacks; for a ``choice_mode: free`` stack its ``sha`` is the treatment's
    ``allowed_bases_sha`` (it commits to every ref and digest).
    """
    if stack is None or (stack.content or {}).get("choice_mode") != "free":
        return None
    from backend.generation.build.dockerfiles import allowed_bases_record

    record = allowed_bases_record()
    return ManifestAllowedBases(sha=record["sha"], refs=list(record["refs"]))


def _starter_base_digests(starter: Starter | None) -> list[str]:
    if starter is None:
        return []
    return [str(base["digest"]) for base in (starter.content or {}).get("base_images") or []]


def _translators() -> frozenset[str]:
    """Front protocols the gateway can translate (``settings.GATEWAY_TRANSLATORS``, read by the registry too)."""
    return frozenset(getattr(settings, "GATEWAY_TRANSLATORS", ()) or ())


def _render_hashes(adapter: Any, request: EnvironmentRequest, wiring: Any, preset_content: Any) -> tuple[str, dict]:
    from backend.generation.budget.model_facts import model_facts
    from backend.generation.contracts.harness import HarnessContext

    ctx = HarnessContext(
        run_id=PLAN_RUN_ID,
        brief=request.brief,
        system_prompt=request.system_prompt,
        model_name=request.model.model_id,
        gateway=wiring,
        preset=preset_content,
        policy=request.policy,
        # The same value the run-time render gets (``GatewayConfig.model.context_length`` comes from the
        # same model facts), so harness context knobs that fall back to it hash equally at plan time.
        model_context_window=model_facts(request.model).context_length,
    )
    rendered = adapter.render(ctx)
    config = {path: identity.bytes_sha(data) for path, data in sorted(rendered.files.items())}
    return identity.text_sha(rendered.command), config


def _gateway_section(
    request: EnvironmentRequest,
    front_protocol: str,
    protocol_mode: str,
    digest: str,
) -> ManifestGateway:
    content = request.policy.content
    routing = content.routing.model_dump(mode="json")
    section = {
        "front_protocol": front_protocol,
        "protocol_mode": protocol_mode,
        "routing": routing,
        "serving_tier": request.policy.serving_tier,
    }
    return ManifestGateway(
        image_digest=digest,
        front_protocol=front_protocol,
        protocol_mode=protocol_mode,
        config_sha=identity.H(section),
        routing=routing,
        serving_tier=request.policy.serving_tier,
    )


def cell_environment(request: EnvironmentRequest) -> CellEnvironment:
    """The environment of one condition (see the module docstring for the rules)."""
    allowed = allowed_bases_for(request.stack)
    starter_base = _starter_base_digests(request.starter)
    if request.kind not in _SIDECAR_KINDS:
        # Single-shot slot calls apply the gateway policy in-process: no sidecar, no harness, no sandbox.
        return CellEnvironment(
            harness=None,
            gateway=None,
            sandbox=None,
            images={"harness": None, "gateway": None, "starter_base": starter_base},
            gateway_treatment=None,
            allowed_bases=allowed,
            agentic=False,
        )
    preset = request.harness_preset
    if preset is None:
        msg = f"{request.kind} cells need a harness preset"
        raise ValueError(msg)
    from backend.generation.agentic import images
    from backend.generation.contracts.library import HarnessPresetContent
    from backend.generation.harnesses import registry

    adapter = registry.get(preset.harness)
    facts = adapter.facts
    kind = request.study_kind
    model = request.model
    endpoint_kind = model.endpoint.kind if model.endpoint_id is not None else "openrouter"
    issues = list(
        registry.plan_issues(
            preset.harness,
            model_slug=model.model_id,
            endpoint_kind=endpoint_kind,
            max_output_tokens=request.policy.content.call.max_output_tokens,
            model_supports_tools=bool(model.supports_function_calling),
            study_kind=kind,
        ),
    )
    verified = tuple(getattr(adapter, "versions", ()) or ())
    if verified and preset.harness_version not in verified:
        issues.append(
            make_issue(
                "harness_not_enabled",
                kind,
                message=f"{preset.harness} {preset.harness_version} is not a verified version ({', '.join(verified)})",
                data={"harness": preset.harness, "reason": "version_not_verified", "version": preset.harness_version},
            ),
        )
    excluded = "config_invalid" if any(issue.code == "model_lacks_tools" for issue in issues) else None
    mode = adapter.protocol_mode(endpoint_kind=endpoint_kind, translators=_translators())
    blocking = [i for i in issues if i.severity == "error" and i.code != "model_lacks_tools"]
    if blocking or mode is None:
        return CellEnvironment(
            harness=None,
            gateway=None,
            sandbox=request.sandbox,
            images={"harness": None, "gateway": None, "starter_base": starter_base},
            gateway_treatment=None,
            allowed_bases=allowed,
            agentic=adapter.capabilities.agentic,
            excluded=excluded,
            issues=issues,
        )
    front = facts.front_protocol
    preset_content = HarnessPresetContent.model_validate(preset.content)
    spec = adapter.image_spec(preset.harness_version)
    harness_tag = images.harness_image_plan(spec)[-1][2]
    gateway_tag = images.gateway_image_plan(endpoint_kind != "openrouter")[-1][2]
    harness_digest = images.resolve_digest(harness_tag)
    gateway_digest = images.resolve_digest(gateway_tag)
    issues += images.image_issues({"harness": harness_digest, "gateway": gateway_digest}, kind)
    wiring = adapter.gateway_wiring(GATEWAY_HOST)
    command_sha, config_files_sha = _render_hashes(adapter, request, wiring, preset_content)
    affinity = facts.vendor_affinity if any(i.code == "vendor_affinity" for i in issues) else None
    harness = ManifestHarness(
        name=preset.harness,
        version=preset.harness_version,
        preset_ref=preset.pinned_ref,
        image=harness_tag,
        image_digest=harness_digest,
        config_files_sha=config_files_sha,
        command_sha=command_sha,
        options=adapter.normalize_options(preset_content.options),
        facts_version=facts.facts_version,
        unsupported_knobs=sorted(facts.unsupported_knobs),
        vendor_affinity=affinity,
    )
    gateway = _gateway_section(request, front, mode, gateway_digest)
    return CellEnvironment(
        harness=harness,
        gateway=gateway,
        sandbox=request.sandbox,
        images={"harness": harness_digest, "gateway": gateway_digest, "starter_base": starter_base},
        gateway_treatment={"front_protocol": front, "protocol_mode": mode},
        allowed_bases=allowed,
        agentic=adapter.capabilities.agentic,
        controlled=preset_content.controlled,
        vendor_affinity=affinity,
        excluded=excluded,
        issues=issues,
    )
