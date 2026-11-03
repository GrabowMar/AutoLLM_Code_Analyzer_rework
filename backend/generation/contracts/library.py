"""Library content contracts (spec section 3.4, plus CheckSpec/ProbeSpec from 8.1 and 8.3).

These schemas are the inter-WP contract for seed data (WP-04, WP-05), the
library API (WP-03), prompt rendering (WP-03), evaluation (WP-09) and the UI
(WP-14). Every model forbids unknown keys.

Conventions:

* Nullable fields default to ``None`` and list/dict fields to empty, unless the
  spec gives another default.
* References to other library entities (``LIBRARY_REF_FIELDS``) are
  **hash-pinned**: ``slug@v#<hash12>`` (section 3.4 "pinned ref"; Appendix A.1:
  a ``content_hash`` commits to the content it references). Authors and seed
  files may write ``slug@v``; :func:`pin_content_refs` rewrites those to the
  pinned form before validation (``VersionedEntity`` does so on create, from the
  database). Stored content never holds an unpinned ref, so two copies of the
  same entity always hash alike.
* Cross-field and vocabulary rules (endpoint ids referenced by checks, the
  feature vocabulary, CWE lists, "all three prose variants for system tasks")
  are lint rules (``library/lint.py``), not schema rules, so lint can report
  them as issues instead of failing validation.

``data/tasks/schema.json`` is generated from ``TaskSpecContent`` by
:func:`write_task_schema` and checked in; a WP-01 test fails when it drifts.
"""

import copy
import json
import re
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated
from typing import Any
from typing import Final
from typing import Literal
from typing import get_args

from pydantic import Field
from pydantic import StringConstraints

from backend.generation.contracts._base import ContractModel
from backend.generation.contracts._base import CweId
from backend.generation.contracts._base import DigestStr
from backend.generation.contracts._base import NonEmptyStr
from backend.generation.contracts._base import NonNegFloat
from backend.generation.contracts._base import NonNegInt
from backend.generation.contracts._base import PinnedRefStr
from backend.generation.contracts._base import PosFloat
from backend.generation.contracts._base import PosInt
from backend.generation.contracts._base import RelPath
from backend.generation.contracts._base import Sha256
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.resources import ResourcePolicyContent

__all__ = [
    "APP_REQUIRED_FRAGMENT_KEYS",
    "AUTH_FRAGMENT_KEYS",
    "AUTH_MODES",
    "BUDGET_NEUTRAL_FRAGMENT",
    "CONTENT_MODELS",
    "FRAGMENT_KEYS",
    "LIBRARY_KINDS",
    "LIBRARY_REF_FIELDS",
    "NETWORK_FRAGMENT_KEYS",
    "NETWORK_POLICIES",
    "OVERRIDE_SWAPS",
    "PROSE_VARIANTS",
    "SCHEMA_VERSIONS",
    "TASK_SCHEMA_PATH",
    "Actor",
    "AuthFlow",
    "CheckCategory",
    "CheckExpect",
    "CheckRequest",
    "CheckSpec",
    "CompiledRequest",
    "DetectorContent",
    "Endpoint",
    "Entity",
    "EntityField",
    "EvalSuiteContent",
    "FieldType",
    "FragmentKey",
    "GuidanceContent",
    "HarnessPresetContent",
    "ProbeKind",
    "ProbeSpec",
    "PromptSetContent",
    "PromptTemplateContent",
    "QueryParam",
    "RefResolver",
    "ResourcePolicyContent",
    "Role",
    "SamplingContent",
    "SetupStep",
    "StackProfileContent",
    "StarterContent",
    "StyleContent",
    "TaskDerived",
    "TaskLint",
    "TaskOracleEntry",
    "TaskSpecContent",
    "UiRequirement",
    "VersionRef",
    "auth_fragment_key",
    "content_model",
    "iter_content_refs",
    "network_fragment_key",
    "normalize_content",
    "pin_content_refs",
    "render_task_schema",
    "swap_prompt_set_fragment",
    "task_spec_json_schema",
    "validate_content",
    "write_task_schema",
]

# A reference to another library entity inside content: always ``slug@v#hash12`` (see LIBRARY_REF_FIELDS).
VersionRef = PinnedRefStr
EndpointId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$", max_length=120)]
HttpStatus = Annotated[int, Field(ge=100, le=599)]

PROSE_VARIANTS: Final = ("minimal", "standard", "exhaustive")
ProseVariant = Literal["minimal", "standard", "exhaustive"]

# =====================================================================================
# TaskSpec
# =====================================================================================

FieldType = Literal[
    "string",
    "text",
    "int",
    "float",
    "bool",
    "date",
    "datetime",
    "enum",
    "ref",
    "email",
    "url",
    "json",
    "file",
]
Auth = Literal["public", "user", "owner", "admin"]
Actor = Literal["anon", "user_a", "user_b", "admin"]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


class EntityField(ContractModel):
    name: NonEmptyStr
    type: FieldType
    required: bool = False
    unique: bool = False
    max_length: PosInt | None = None
    min: float | None = None
    max: float | None = None
    enum: list[str] | None = None
    ref: str | None = None
    default: Any = None
    secret: bool = False  # password-like, never echoed


class Entity(ContractModel):
    name: NonEmptyStr
    fields: list[EntityField]
    owned_by_user: bool = False
    soft_delete: bool = False


class QueryParam(ContractModel):
    name: NonEmptyStr
    type: FieldType
    required: bool = False
    enum: list[str] | None = None
    default: Any = None
    max: float | None = None
    semantics: Literal["filter", "search", "sort", "page", "page_size", "other"] = "other"


class Endpoint(ContractModel):
    id: EndpointId  # stable dotted id, e.g. "todos.update" (checks, slots and probes refer to it)
    method: HttpMethod
    path: Annotated[str, StringConstraints(pattern=r"^/")]  # "/api/todos/{id}"
    auth: Auth
    owner_entity: str | None = None  # required when auth == "owner" (lint)
    success_status: Annotated[int, Field(ge=200, le=399)] = 200  # 3xx: redirect endpoints (WP-04 ask)
    query: list[QueryParam] = Field(default_factory=list)
    request_schema: dict[str, Any] | None = None  # JSON Schema 2020-12 subset
    response_schema: dict[str, Any] | None = None
    content_type: Literal["json", "multipart", "form", "none"] = "json"
    errors: list[HttpStatus] = Field(default_factory=list)  # statuses the spec promises
    tags: list[str] = Field(default_factory=list)
    realtime: Literal["none", "websocket", "sse"] = "none"


class UiRequirement(ContractModel):
    id: NonEmptyStr
    text: NonEmptyStr
    endpoints: list[EndpointId] = Field(default_factory=list)


class AuthFlow(ContractModel):
    """Used only by auth_mode ``spec_defined``; values are endpoint ids."""

    register_endpoint: EndpointId | None = Field(default=None, alias="register")
    login: EndpointId
    logout: EndpointId | None = None
    credential: Literal["bearer", "cookie"]
    token_json_path: str | None = None
    csrf: Literal["none", "double_submit_cookie", "header_from_endpoint"] = "none"
    csrf_endpoint: EndpointId | None = None


CheckCategory = Literal[
    "infra",
    "functional",
    "auth",
    "ownership",
    "validation",
    "negative",
    "persistence",
    "pagination",
    "filter",
]


class CheckRequest(ContractModel):
    method: HttpMethod
    path: Annotated[str, StringConstraints(pattern=r"^/")]
    query: dict[str, Any] = Field(default_factory=dict)
    json_body: Any = Field(default=None, alias="json")
    form: dict[str, Any] | None = None
    files: dict[str, Any] | None = None


class CompiledRequest(CheckRequest):
    """A fully resolved request (probe positive controls, compiled setup steps)."""

    actor: Actor = "user_a"
    headers: dict[str, str] = Field(default_factory=dict)
    expect_status: HttpStatus | list[HttpStatus] | None = None


class SetupStep(ContractModel):
    """One setup request before a check; ``capture`` saves response values (``{name: json_path}``)."""

    actor: Actor | None = None  # defaults to the check's actor
    request: CheckRequest
    expect_status: HttpStatus | list[HttpStatus] | None = None
    capture: dict[str, str] = Field(default_factory=dict)


class CheckExpect(ContractModel):
    status: HttpStatus | list[HttpStatus]
    json_schema: dict[str, Any] | None = None
    json_contains: dict[str, Any] | None = None
    header_contains: dict[str, str] | None = None
    max_ms: PosInt | None = None


class CheckSpec(ContractModel):
    """A functional check (hand-written in ``TaskSpecContent.acceptance`` or compiler output)."""

    id: NonEmptyStr
    category: CheckCategory
    endpoint: EndpointId | None = None
    actor: Actor = "user_a"
    setup: list[SetupStep] = Field(default_factory=list)
    request: CheckRequest
    expect: CheckExpect
    depends_on: list[str] = Field(default_factory=list)
    restart_before: bool = False  # persistence checks restart the container first
    stated_in: list[ProseVariant] = Field(default_factory=list)  # prose variants that state this behaviour


ProbeKind = Literal[
    "sqli_boolean",
    "sqli_error",
    "sqli_time",
    "xss_stored",
    "xss_reflected",
    "path_traversal",
    "open_redirect",
    "idor_enumeration",
    "mass_assignment",
    "xxe",
    "ssrf_mock",
    "csrf_missing",
    "rate_limit_missing",
    "plaintext_password_db",
    "default_credential",
    "jwt_none_alg",
    "verbose_errors",
]


class ProbeSpec(ContractModel):
    id: NonEmptyStr
    cwe: CweId
    kind: ProbeKind
    endpoint: EndpointId | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    requires_checks: list[str] | None = None  # default: the functional checks of `endpoint` (compiler fills it)
    positive_control: CompiledRequest | None = None  # default: generated per kind


class TaskSpecContent(ContractModel):
    """``TaskSpecVersion.content`` (schema_version 4)."""

    title: NonEmptyStr
    summary: NonEmptyStr
    category: NonEmptyStr
    entities: list[Entity]
    endpoints: list[Endpoint]
    ui: list[UiRequirement] = Field(default_factory=list)
    has_frontend: bool = True
    features: list[str]  # closed vocabulary (section 11.3, lint)
    surfaces: list[str]  # "cwe-89", ... (lint: CWE ids or vocabulary)
    auth_flow: AuthFlow | None = None
    seed_data: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    acceptance: list[CheckSpec] = Field(default_factory=list)
    probes: list[ProbeSpec] = Field(default_factory=list)
    prose: dict[ProseVariant, str] = Field(min_length=1)  # markdown; all three required for system tasks (lint)


class TaskDerived(ContractModel):
    """``TaskSpecVersion.derived``: computed, never hashed."""

    endpoint_count: NonNegInt
    entity_count: NonNegInt
    field_count: NonNegInt
    relation_count: NonNegInt
    features: list[str] = Field(default_factory=list)
    surfaces: list[str] = Field(default_factory=list)
    difficulty_score: float
    size_bucket: Literal["S", "M", "L", "XL"]
    check_statedness: dict[str, list[ProseVariant]] = Field(default_factory=dict)


class TaskLint(ContractModel):
    """``TaskSpecVersion.lint``."""

    status: Literal["ok", "warn", "error"]
    issues: list[Issue] = Field(default_factory=list)  # library lint codes are registered like any other
    linter_version: PosInt


class TaskOracleEntry(ContractModel):
    """One value of ``TaskSpecVersion.oracle`` (keyed by stack ref); written only by evaluation/baseline.py."""

    status: Literal["pass", "fail", "missing", "error"]
    run_id: str | None = None
    eval_suite_ref: str | None = None
    at: str | None = None


# =====================================================================================
# Stack / starter
# =====================================================================================


class StackBackend(ContractModel):
    language: Literal["python", "javascript", "typescript", "go", "java", "free"]
    framework: Literal["flask", "fastapi", "django", "express", "go-stdlib", "gin", "spring-boot", "free"]
    runtime: NonEmptyStr  # "python3.13", "node24", "go1.25", "temurin21", or "free"


class StackFrontend(ContractModel):
    framework: Literal["react", "vue", "svelte", "none", "free"]
    language: Literal["ts", "js", "free", "none"]
    build_tool: Literal["vite", "none", "free"]


class RunContract(ContractModel):
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    api_prefix: str = "/api"
    health_path: str = "/api/health"
    static_serving: Literal["backend", "none"]
    data_dir: str = "./data"
    start: Literal["dockerfile"] = "dockerfile"


class StackAdherence(ContractModel):
    backend_markers: list[str] = Field(default_factory=list)
    frontend_markers: list[str] = Field(default_factory=list)
    db_markers: list[str] = Field(default_factory=list)


class StackResources(ContractModel):
    min_memory: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*[kmg]$")] = "2g"
    min_cpus: PosFloat = 1.0


class StackProfileContent(ContractModel):
    choice_mode: Literal["fixed", "free"]
    backend: StackBackend
    frontend: StackFrontend
    database: Literal["sqlite", "postgres", "free"]
    run_contract: RunContract
    brief_fragment: VersionRef  # PromptTemplate ref (role fragment) describing the stack to the model
    adherence: StackAdherence = Field(default_factory=StackAdherence)
    resources: StackResources = Field(default_factory=StackResources)  # study sandbox = max over profiles


class StarterFile(ContractModel):
    blob: Sha256
    mode: Annotated[int, Field(ge=0, le=0o7777)]


class BaseImage(ContractModel):
    ref: NonEmptyStr
    digest: DigestStr


class StarterComposition(ContractModel):
    backend_fragment: NonEmptyStr
    frontend_fragment: str | None = None
    dockerfile_template: NonEmptyStr


class ForbiddenContentCheck(ContractModel):
    passed: bool
    linter_version: PosInt


class StarterContent(ContractModel):
    stack_profile: VersionRef
    level: Literal["bare", "minimal", "full"]
    files: dict[RelPath, StarterFile]  # relative POSIX paths
    dockerfile: RelPath = "Dockerfile"
    base_images: list[BaseImage]  # every FROM, digest-pinned
    lockfiles: list[RelPath] = Field(default_factory=list)
    install: list[str] = Field(default_factory=list)  # informational
    composition: StarterComposition
    forbidden_content_check: ForbiddenContentCheck


# =====================================================================================
# Style / guidance
# =====================================================================================


class StyleLibrary(ContractModel):
    name: NonEmptyStr
    packages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)


class StyleAdherence(ContractModel):
    required_markers: list[str] = Field(default_factory=list)
    forbidden_markers: list[str] = Field(default_factory=list)
    class_share_min: NonNegFloat | None = None


class StyleContent(ContractModel):
    system: Literal["tailwind", "plain-css", "css-modules", "component-library", "model-choice"]
    library: StyleLibrary | None = None
    tone: str | None = None  # tone preset slug
    tone_text: str | None = None
    accessibility: Literal["unspecified", "wcag-aa"] = "unspecified"
    fragment: VersionRef  # PromptTemplate ref (role fragment)
    adherence: StyleAdherence = Field(default_factory=StyleAdherence)
    compatible_frontends: list[str]  # plan resolution rejects incompatible (stack, style) pairs


class LibraryPrescription(ContractModel):
    purpose: NonEmptyStr
    library: NonEmptyStr


class GuidanceContent(ContractModel):
    security_hint: Literal["none", "generic", "cwe_specific"]
    cwe_source: Literal["task_surfaces", "explicit"] = "task_surfaces"
    cwes: list[CweId] = Field(default_factory=list)
    library_prescriptions: list[LibraryPrescription] = Field(default_factory=list)
    fragment: VersionRef | None = None  # PromptTemplate ref; None for "none"


# =====================================================================================
# Prompts
# =====================================================================================

Role = Literal[
    "brief",
    "system",
    "user",
    "slot_single_shot_system",
    "slot_single_shot_user",
    "slot_agent_brief",
    "fragment",
]


class PromptTemplateContent(ContractModel):
    role: Role
    engine: Literal["jinja2-sandbox@1"]
    template: str
    variables: list[str] = Field(default_factory=list)  # declared top-level context vars (lint: undeclared -> error)
    modes: list[Literal["app", "slot"]] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)  # e.g. "editor" for the aider brief variant (section 5.3)


# Fragment slots of a prompt set (CHANGES "WP-01 decisions", round 2). The text rendered for a closed enum
# level (auth mode, network policy) or the optional budget sentence (section 4.1) comes from a pinned
# PromptTemplate (role ``fragment``) named here, so ``prompt_set.content_hash`` -- and with it
# ``treatment_hash`` -- commits to that text. The renderer never looks a fragment up by slug.
AUTH_MODES: Final = ("contract_bearer", "cookie_session", "spec_defined")
NETWORK_POLICIES: Final = ("none", "packages")
BUDGET_NEUTRAL_FRAGMENT: Final = "budget_neutral"
FragmentKey = Literal[
    "auth_contract.contract_bearer",
    "auth_contract.cookie_session",
    "auth_contract.spec_defined",
    "network.none",
    "network.packages",
    "budget_neutral",
]
FRAGMENT_KEYS: Final[tuple[str, ...]] = get_args(FragmentKey)
AUTH_FRAGMENT_KEYS: Final = tuple(f"auth_contract.{mode}" for mode in AUTH_MODES)
NETWORK_FRAGMENT_KEYS: Final = tuple(f"network.{policy}" for policy in NETWORK_POLICIES)
# Keys an app-mode prompt set must define (``library/lint.py``: ``lint_prompt_fragment_missing``); the budget
# sentence is optional (section 4.1: absent renders as "").
APP_REQUIRED_FRAGMENT_KEYS: Final = AUTH_FRAGMENT_KEYS + NETWORK_FRAGMENT_KEYS


def auth_fragment_key(auth_mode: str) -> str:
    """The ``PromptSetContent.fragments`` key holding the contract text of ``auth_mode``."""
    if auth_mode not in AUTH_MODES:
        msg = f"unknown auth mode {auth_mode!r}; expected one of {AUTH_MODES}"
        raise ValueError(msg)
    return f"auth_contract.{auth_mode}"


def network_fragment_key(network_policy: str) -> str:
    """The ``PromptSetContent.fragments`` key holding the network sentence of ``network_policy``."""
    if network_policy not in NETWORK_POLICIES:
        msg = f"unknown network policy {network_policy!r}; expected one of {NETWORK_POLICIES}"
        raise ValueError(msg)
    return f"network.{network_policy}"


class PromptSetContent(ContractModel):
    mode: Literal["app", "slot"]
    # role -> PromptTemplate ref; app requires "brief"; slot single-shot requires both slot_single_shot_* roles;
    # slot agent requires "slot_agent_brief" (lint).
    roles: dict[Role, VersionRef]
    system_prompt_mode: Literal["harness_native", "append", "override"] = "harness_native"  # app mode
    # fragment key -> PromptTemplate ref (role fragment): auth_contract.<mode>, network.<policy>, budget_neutral.
    # App mode requires every auth_contract.* and network.* key (lint); budget_neutral is optional.
    fragments: dict[FragmentKey, VersionRef] = Field(default_factory=dict)


# =====================================================================================
# Run presets
# =====================================================================================


class ToolPolicy(ContractModel):
    deny: list[str] = Field(default_factory=list)
    allow: list[str] | None = None


class HarnessPresetContent(ContractModel):
    harness: NonEmptyStr
    harness_version: NonEmptyStr
    options: dict[str, Any] = Field(default_factory=dict)  # validated by adapter.option_fields
    controlled: bool = False  # equalised profile (section 5.5)
    command_timeout_s: PosInt | None = None
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)
    max_turns: PosInt | None = None  # harness-native turn cap where supported
    subagents: bool = False
    pin_system_prompt: bool = False  # use the captured default prompt file (controlled presets)


class SamplingParams(ContractModel):
    temperature: NonNegFloat | None = None
    top_p: Annotated[float, Field(gt=0, le=1)] | None = None
    top_k: PosInt | None = None
    min_p: Annotated[float, Field(ge=0, le=1)] | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None


class SamplingContent(ContractModel):
    policy: Literal["provider_default", "explicit"]
    params: SamplingParams = Field(default_factory=SamplingParams)
    forward_seed: bool = True


class ImagePin(ContractModel):
    version: PosInt
    image_digest: DigestStr


class EvalTimeouts(ContractModel):
    build_s: PosInt = 900
    start_s: PosInt = 90
    tester_s: PosInt = 600


class EvalSuiteContent(ContractModel):
    tester: ImagePin
    acceptance_compiler_version: PosInt
    adherence_checker: ImagePin
    probes: Literal["none", "task", "all_applicable"] = "task"
    baseline: Literal["starter", "none"] = "starter"
    timeouts: EvalTimeouts = Field(default_factory=EvalTimeouts)
    flaky_checks: list[str] = Field(default_factory=list)  # written by oracle_validate as a new suite version
    tester_rules_version: PosInt = 1
    probe_rules_version: PosInt = 1


class DetectorTool(ContractModel):
    name: NonEmptyStr
    version: NonEmptyStr


class DetectorContent(ContractModel):
    engine: Literal["semgrep", "ts_query", "regex", "probe", "manual"]
    languages: list[str]
    rule: str  # semgrep YAML, tree-sitter query, or regex ("" for probe/manual)
    tool: DetectorTool
    feature_key: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=100)]
    polarity: Literal["insecure", "secure", "neutral"]
    tool_severity_field: str | None = None  # the rule's own label is copied verbatim
    cwe: list[CweId] = Field(default_factory=list)
    description: NonEmptyStr
    scopes: list[Literal["span", "file"]] = ["span", "file"]  # pydantic copies mutable defaults
    taint: bool = False  # taint rules always run on the whole file


# =====================================================================================
# Registry
# =====================================================================================

CONTENT_MODELS: Final[Mapping[str, type[ContractModel]]] = {
    "task": TaskSpecContent,
    "stack": StackProfileContent,
    "starter": StarterContent,
    "style": StyleContent,
    "guidance": GuidanceContent,
    "prompt": PromptTemplateContent,
    "prompt_set": PromptSetContent,
    "harness_preset": HarnessPresetContent,
    "sampling": SamplingContent,
    "resource_policy": ResourcePolicyContent,
    "eval_suite": EvalSuiteContent,
    "detector": DetectorContent,
}
LIBRARY_KINDS: Final = tuple(CONTENT_MODELS)
# Content fields that reference other library entities: kind -> ((dotted path, target kind), ...). A path
# ending in ``.*`` names every value of a mapping. Each must hold a pinned ref (``VersionRef``).
LIBRARY_REF_FIELDS: Final[Mapping[str, tuple[tuple[str, str], ...]]] = {
    "stack": (("brief_fragment", "prompt"),),
    "starter": (("stack_profile", "stack"),),
    "style": (("fragment", "prompt"),),
    "guidance": (("fragment", "prompt"),),
    "prompt_set": (("roles.*", "prompt"), ("fragments.*", "prompt")),
}
# StudyPromptOverride (section 3.6): owner kind -> (kinds forked, innermost first; the design factor whose
# level is swapped to the last fork). An auth fragment is owned by a PromptTemplate that only a prompt set's
# ``fragments`` names (``auth_mode`` is an enum level), so editing it forks the template and the prompt set and
# swaps the ``prompt_set`` level: ``treatment_hash`` then commits to the new text (Appendix A.1).
OVERRIDE_SWAPS: Final[Mapping[str, tuple[tuple[str, ...], str]]] = {
    "prompt": (("prompt", "prompt_set"), "prompt_set"),
    "prompt_set": (("prompt_set",), "prompt_set"),
    "stack": (("prompt", "stack"), "stack_profile"),
    "style": (("prompt", "style"), "style"),
    "guidance": (("prompt", "guidance"), "guidance"),
    "auth_fragment": (("prompt", "prompt_set"), "prompt_set"),
}


def swap_prompt_set_fragment(content: Mapping[str, Any], key: str, new_ref: str) -> dict[str, Any]:
    """A copy of prompt-set ``content`` whose fragment ``key`` names ``new_ref`` (the fork for an override).

    ``key`` must already be defined (an override edits existing text; it never adds a slot).
    """
    if key not in FRAGMENT_KEYS:
        msg = f"unknown fragment key {key!r}; expected one of {FRAGMENT_KEYS}"
        raise ValueError(msg)
    fragments = content.get("fragments") or {}
    if key not in fragments:
        msg = f"the prompt set has no {key!r} fragment to override"
        raise ValueError(msg)
    swapped = copy.deepcopy(dict(content))
    swapped["fragments"] = {**fragments, key: new_ref}
    return swapped


# (target kind, slug, version) -> that version's content_hash, or None when it does not exist.
RefResolver = Callable[[str, str, int], str | None]
_REF_RE: Final = re.compile(
    r"^(?P<slug>[A-Za-z0-9][A-Za-z0-9_-]*)@(?P<version>[1-9][0-9]*)(?:#(?P<hash>[0-9a-f]{12}))?$",
)
# Current schema version per kind (VersionedEntity.schema_version / TaskSpecVersion.schema_version).
SCHEMA_VERSIONS: Final[Mapping[str, int]] = {kind: (4 if kind == "task" else 1) for kind in CONTENT_MODELS}


def content_model(kind: str) -> type[ContractModel]:
    try:
        return CONTENT_MODELS[kind]
    except KeyError:
        msg = f"unknown library kind {kind!r}; expected one of {LIBRARY_KINDS}"
        raise KeyError(msg) from None


def validate_content(kind: str, content: Mapping[str, Any]) -> ContractModel:
    """Validate ``content`` against the contract of ``kind`` (raises ``pydantic.ValidationError``)."""
    return content_model(kind).model_validate(content)


def normalize_content(kind: str, content: Mapping[str, Any]) -> dict[str, Any]:
    """Validated content with every default filled in, as JSON types (the form to store and hash)."""
    return validate_content(kind, content).model_dump(mode="json")


def _ref_slots(content: Any, path: str) -> Iterator[tuple[str, dict[str, Any], str]]:
    """``(location, container, key)`` for every value at ``path`` that exists in ``content``."""
    parts = path.split(".")
    nodes: list[tuple[str, Any]] = [("", content)]
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        following: list[tuple[str, Any]] = []
        for location, node in nodes:
            if not isinstance(node, dict):
                continue
            keys = list(node) if part == "*" else [part] if part in node else []
            for key in keys:
                where = f"{location}.{key}" if location else str(key)
                if last:
                    yield where, node, key
                else:
                    following.append((where, node[key]))
        nodes = following


def iter_content_refs(kind: str, content: Any) -> Iterator[tuple[str, str, str]]:
    """``(location, target kind, ref)`` for every string library ref in raw or stored ``content``."""
    for path, target in LIBRARY_REF_FIELDS.get(kind, ()):
        for location, container, key in _ref_slots(content, path):
            value = container[key]
            if isinstance(value, str):
                yield location, target, value


def pin_content_refs(kind: str, content: Any, resolve: RefResolver) -> tuple[Any, dict[str, list[str]]]:
    """``content`` with every ``slug@v`` ref rewritten to ``slug@v#<hash12>``, plus per-location errors.

    ``resolve`` looks up the referenced version's ``content_hash``. A bare ref to a missing version is an
    error. An already pinned ref is kept; when the referenced version exists and its hash differs, that is
    an error too. A pinned ref to a version ``resolve`` does not know is accepted (packages are imported in
    dependency order, and tests pin fake hashes); the library lint reports unknown refs. Values that are
    not refs at all are left for the contract to reject. ``content`` itself is never modified.
    """
    errors: dict[str, list[str]] = {}
    if not isinstance(content, Mapping) or kind not in LIBRARY_REF_FIELDS:
        return content, errors
    pinned = copy.deepcopy(dict(content))
    for path, target in LIBRARY_REF_FIELDS[kind]:
        for location, container, key in _ref_slots(pinned, path):
            value = container[key]
            match = _REF_RE.match(value) if isinstance(value, str) else None
            if match is None:
                continue
            content_hash = resolve(target, match["slug"], int(match["version"]))
            if match["hash"] is None:
                if content_hash is None:
                    errors.setdefault(location, []).append(f"{target} {value} does not exist")
                else:
                    container[key] = f"{value}#{content_hash[:12]}"
            elif content_hash is not None and content_hash[:12] != match["hash"]:
                errors.setdefault(location, []).append(
                    f"{value} does not match {target} {match['slug']}@{match['version']} "
                    f"(content hash {content_hash[:12]})",
                )
    return pinned, errors


# =====================================================================================
# data/tasks/schema.json
# =====================================================================================

TASK_SCHEMA_PATH: Final = Path(__file__).resolve().parent.parent / "data" / "tasks" / "schema.json"


def task_spec_json_schema() -> dict[str, Any]:
    """JSON Schema (draft 2020-12) of ``TaskSpecContent`` in its stored (by-alias) form."""
    schema = TaskSpecContent.model_json_schema(by_alias=True, mode="validation")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "llmlab:generation/task-spec/v4",
        **schema,
    }


def render_task_schema() -> str:
    return json.dumps(task_spec_json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_task_schema(path: Path | None = None) -> Path:
    """Regenerate the checked-in ``data/tasks/schema.json``."""
    target = path or TASK_SCHEMA_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_task_schema(), encoding="utf-8")
    return target
