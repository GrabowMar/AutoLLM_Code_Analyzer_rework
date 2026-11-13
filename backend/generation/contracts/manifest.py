"""RunManifest content (spec section 3.8) and the stimulus records of ``RunManifest.stimuli``.

The manifest is immutable and written once before a run is queued. It holds
everything a sample depends on, by pinned ref and digest. Per-attempt facts
(served providers, timings, executor id, request classes, translation losses)
belong to ``RunAttempt``, never here.

``ManifestBody`` is the part a ``ResolvedCell`` carries at plan time;
``agentic/manifest.py:build_manifest`` adds the ``study`` block and
``created_at`` and copies the identity keys unchanged (it never recomputes
them).
"""

from typing import Any
from typing import Literal
from typing import Self

from pydantic import Field
from pydantic import model_validator

from backend.generation.contracts._base import ContractModel
from backend.generation.contracts._base import Fraction
from backend.generation.contracts._base import NonEmptyStr
from backend.generation.contracts._base import NonNegFloat
from backend.generation.contracts._base import NonNegInt
from backend.generation.contracts._base import PinnedRefStr
from backend.generation.contracts._base import PosInt
from backend.generation.contracts._base import Sha256
from backend.generation.contracts.slot import ContextPolicy
from backend.generation.contracts.slot import SlotAddress

__all__ = [
    "FactorRecord",
    "ManifestAllowedBases",
    "ManifestBody",
    "ManifestEndpoint",
    "ManifestEvalSuite",
    "ManifestGateway",
    "ManifestHarness",
    "ManifestIdentity",
    "ManifestModel",
    "ManifestPricing",
    "ManifestResources",
    "ManifestSampling",
    "ManifestSandbox",
    "ManifestSlot",
    "ManifestStarter",
    "ManifestStimulus",
    "ManifestStudy",
    "ManifestTask",
    "PlannedEvaluation",
    "RunManifest",
    "SourceSpan",
    "StimulusRecord",
]

Protocol = Literal["app@1", "slot@1", "ref@1"]
RunKind = Literal["app_agentic", "slot_single_shot", "slot_agent", "reference"]


class FactorRecord(ContractModel):
    """One factor level: library levels use ``ref`` + ``content_hash``; enum factors use ``value``."""

    ref: PinnedRefStr | None = None
    content_hash: Sha256 | None = None
    value: str | None = None

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        if (self.ref is None) == (self.value is None):
            msg = "a factor record has exactly one of ref or value"
            raise ValueError(msg)
        if self.ref is not None and self.content_hash is None:
            msg = "a library factor record needs its content_hash"
            raise ValueError(msg)
        return self


class ManifestStudy(ContractModel):
    id: NonEmptyStr
    plan_hash: Sha256
    condition_hash: Sha256
    cell: tuple[NonNegInt, NonNegInt, NonNegInt]  # (condition index, unit index, repeat index)
    block_index: NonNegInt
    stage: Literal["pilot", "main"]
    cell_attempt: PosInt = 1
    beyond_plan: bool = False


class ManifestTask(ContractModel):
    ref: PinnedRefStr
    structured_hash: Sha256
    variant: Literal["minimal", "standard", "exhaustive"]
    prose_sha: Sha256


class ParentSelectionRecord(ContractModel):
    rule: Literal["random", "all", "manual"]
    seed: NonNegInt | None = None


class ManifestContextFile(ContractModel):
    path: NonEmptyStr
    sha: Sha256
    tokens: NonNegInt


class ManifestResolvedContext(ContractModel):
    files: list[ManifestContextFile] = Field(default_factory=list)
    sha: Sha256


class ManifestSlotValidation(ContractModel):
    discriminating_checks: list[str] = Field(default_factory=list)
    dropped_checks: list[str] = Field(default_factory=list)


class ManifestSlot(ContractModel):
    slot_set: NonEmptyStr
    target: NonEmptyStr
    target_hash: Sha256
    parent_run: NonEmptyStr
    parent_tree_hash: Sha256
    parent_model: NonEmptyStr
    parent_harness: NonEmptyStr
    parent_selection: ParentSelectionRecord
    address: SlotAddress
    hole_mode: Literal["stub", "remove", "rewrite"]
    keep_docstring: bool
    context_policy: ContextPolicy
    resolved_context: ManifestResolvedContext
    include_tests: bool
    edit_policy: Literal["revert", "fail"]
    allowlist: list[str]
    leak_score: Fraction
    validation: ManifestSlotValidation


class ManifestEndpoint(ContractModel):
    """The serving endpoint, frozen at plan time.

    ``num_ctx``, ``keep_alive``, ``api_flavor`` and ``capabilities`` change what a local model sees
    (context size, residency, request dialect, whether ``think`` is sent). They are frozen here for local
    endpoints (``None`` for OpenRouter); the LLM proxy takes a frozen value over the live endpoint row
    (``inference.proxy_server.frozen_endpoint``). Recorded, not hashed into identity.

    ``native`` is what a native upstream API will be sent for this model, for ``api_flavor="anthropic"``
    only (``inference.anthropic_native.planned_settings``: effort and its source, thinking, thinking replay,
    forced tool_choice support, cache TTL). Recorded, not hashed.
    """

    id: int
    kind: NonEmptyStr
    served_model_name: str | None = None
    digest: str | None = None
    cost_basis: Literal["billed", "list_price", "free"]
    num_ctx: PosInt | None = None
    keep_alive: str | None = None
    api_flavor: str | None = None
    capabilities: list[str] | None = None
    native: dict[str, Any] | None = None


class ManifestPricing(ContractModel):
    input_per_mtok: NonNegFloat
    output_per_mtok: NonNegFloat
    cached_input_per_mtok: NonNegFloat | None = None
    cache_write_per_mtok: NonNegFloat | None = None
    source: NonEmptyStr
    as_of: NonEmptyStr


class ManifestModel(ContractModel):
    slug: NonEmptyStr
    endpoint: ManifestEndpoint
    pricing: ManifestPricing


class ManifestSampling(ContractModel):
    ref: PinnedRefStr
    policy: Literal["provider_default", "explicit"]
    forced_params: dict[str, Any] = Field(default_factory=dict)
    stripped_params: list[str] = Field(default_factory=list)
    # Forced params the upstream API never receives (the native Anthropic route sends no sampling knobs):
    # the gateway still forces them on the harness body, the proxy drops them before Claude sees them.
    not_sent_upstream: list[str] = Field(default_factory=list)
    seed: NonNegInt | None = None
    seed_forwarded: bool


class ManifestResources(ContractModel):
    ref: PinnedRefStr
    generation_policy_hash: Sha256
    budget_basis: Literal["usd", "tokens"]
    effective: dict[str, Any]  # class O values and the clamped effective policy (recorded, not hashed)
    notes: list[str] = Field(default_factory=list)


class ManifestGateway(ContractModel):
    image_digest: NonEmptyStr
    front_protocol: Literal["openai_chat", "openai_responses", "anthropic_messages", "gemini_generate"]
    protocol_mode: Literal["native", "translate", "passthrough"]
    config_sha: Sha256
    routing: dict[str, Any] = Field(default_factory=dict)
    serving_tier: Literal["standard", "flex", "batch"]
    session_id_rule: Literal["run_uuid"] = "run_uuid"


class ManifestHarness(ContractModel):
    name: NonEmptyStr
    version: NonEmptyStr
    preset_ref: PinnedRefStr
    image: NonEmptyStr
    image_digest: NonEmptyStr
    config_files_sha: dict[str, Sha256] = Field(default_factory=dict)
    command_sha: Sha256
    options: dict[str, Any] = Field(default_factory=dict)
    facts_version: PosInt
    unsupported_knobs: list[str] = Field(default_factory=list)
    vendor_affinity: str | None = None


class ManifestBaseImage(ContractModel):
    ref: NonEmptyStr
    digest: NonEmptyStr


class ManifestStarter(ContractModel):
    ref: PinnedRefStr
    tree_hash: Sha256
    base_images: list[ManifestBaseImage] = Field(default_factory=list)


class ManifestAllowedBases(ContractModel):
    sha: Sha256
    refs: list[str]


class ManifestStimulus(ContractModel):
    role: NonEmptyStr
    template_ref: NonEmptyStr
    sha: Sha256


class ManifestIdentity(ContractModel):
    task_hash: Sha256
    treatment_hash: Sha256
    prompt_hash: Sha256
    generation_policy_hash: Sha256
    config_fingerprint: Sha256
    run_fingerprint: Sha256


class ManifestEvalSuite(ContractModel):
    ref: PinnedRefStr
    content_hash: Sha256


class PlannedEvaluation(ContractModel):
    """Recorded, NOT hashed (evaluation results are compared per suite)."""

    eval_suite: ManifestEvalSuite
    eval_level: Literal["none", "static", "build", "functional", "security", "full"]
    mode: Literal["full", "overlay", "static_only"] = "full"


class ManifestSandbox(ContractModel):
    memory: NonEmptyStr
    nano_cpus: PosInt
    pids_limit: PosInt = 512
    resolution: Literal["study_max"] = "study_max"


class ManifestBody(ContractModel):
    """Every manifest section except ``study`` and ``created_at`` (what a ResolvedCell carries)."""

    protocol: Protocol
    kind: RunKind
    manifest_schema: Literal[1] = 1
    factors: dict[str, FactorRecord] = Field(default_factory=dict)  # every factor key incl. fixed ones
    task: ManifestTask | None = None
    slot: ManifestSlot | None = None
    model: ManifestModel | None = None
    sampling: ManifestSampling | None = None
    resources: ManifestResources | None = None
    gateway: ManifestGateway | None = None
    harness: ManifestHarness | None = None
    starter: ManifestStarter | None = None
    allowed_bases: ManifestAllowedBases | None = None  # rendered into the brief (section 6.10)
    stimuli: list[ManifestStimulus] = Field(default_factory=list)
    identity: ManifestIdentity | None = None
    planned_evaluation: PlannedEvaluation
    network_policy: Literal["none", "packages"]
    sandbox: ManifestSandbox | None = None

    @model_validator(mode="after")
    def _protocol_shape(self) -> Self:
        expected = {"app_agentic": "app@1", "slot_single_shot": "slot@1", "slot_agent": "slot@1", "reference": "ref@1"}
        if expected[self.kind] != self.protocol:
            msg = f"kind {self.kind!r} runs under {expected[self.kind]!r}, not {self.protocol!r}"
            raise ValueError(msg)
        if self.protocol == "app@1" and (self.task is None or self.slot is not None):
            msg = "app@1 manifests carry task and no slot"
            raise ValueError(msg)
        if self.protocol == "slot@1" and (self.slot is None or self.task is not None):
            msg = "slot@1 manifests carry slot and no task"
            raise ValueError(msg)
        if self.protocol != "ref@1":
            missing = [name for name in ("model", "sampling", "resources", "identity") if getattr(self, name) is None]
            if self.kind in {"app_agentic", "slot_agent"}:
                # Single-shot slot calls apply the gateway policy in-process: no sidecar, no harness.
                missing += [name for name in ("gateway", "harness", "sandbox") if getattr(self, name) is None]
            if missing:
                msg = f"{self.protocol} {self.kind} manifest is missing: {', '.join(missing)}"
                raise ValueError(msg)
        return self


class RunManifest(ManifestBody):
    """``RunManifest.manifest`` (``manifest_hash = H(manifest)``)."""

    study: ManifestStudy | None = None
    created_at: NonEmptyStr  # ISO; NOT hashed into identity


class SourceSpan(ContractModel):
    """Which library entity owns a span of rendered text (section 9.2)."""

    template_ref: NonEmptyStr
    owner_kind: Literal["prompt", "prompt_set", "stack", "style", "guidance", "auth_fragment", "task", "starter"]
    owner_ref: NonEmptyStr
    role: str | None = None
    char_start: NonNegInt
    char_end: NonNegInt

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.char_end < self.char_start:
            msg = "source span ends before it starts"
            raise ValueError(msg)
        return self


class StimulusRecord(ContractModel):
    """One entry of the ``RunManifest.stimuli`` column (the text itself is in the blob store)."""

    role: NonEmptyStr
    template_ref: NonEmptyStr
    sha: Sha256
    blob: Sha256
    chars: NonNegInt
    est_tokens: NonNegInt
    sources: list[SourceSpan] = Field(default_factory=list)
