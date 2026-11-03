"""Design and settings schema of human-evaluation tasks: validation, defaults, freezing.

``task.design`` (evaluation blocks, pre-registered analysis plan, consent and the
task-level questionnaires) and ``task.settings`` (blinding, allocation, pairing,
access, hosting, quality) are JSON documents mirrored field for field by
``TaskDesign`` / ``TaskSettings`` in ``frontend/src/lib/api/types/human-eval.ts``.

The pydantic models below check structure (types, enums, unknown keys, string
lengths, numeric ranges). :func:`validate_design` / :func:`validate_settings`
then apply the semantic rules (ids, uniqueness, scales, mode rules, cross
references) and return the *normalized* document: every optional field filled
with its default, in the key order of the TS types, ready to store and serve.
Question dicts only carry the keys that apply to their kind (``options`` for
single/multi/attention, ``scale`` for scale, ``min``/``max`` for number,
``expected`` for attention).

Error paths are dotted and relative to the validated document
(``blocks.0.criteria.1.scale.max``, ``hosting.reset``); a caller validating both
documents prefixes them with ``design.`` / ``settings.``.

Freezing: at first open the task stores :func:`design_hash` over the mode, the
design and the settings minus :func:`editable_after_freeze`; afterwards only those
settings keys may change (:func:`changed_frozen_keys`).
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Annotated
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StringConstraints
from pydantic import ValidationError
from pydantic import model_serializer

from backend.human_eval.services.checklists import default_security_checklist

# ---------------------------------------------------------------------------
# Vocabulary and limits
# ---------------------------------------------------------------------------

MODES = ("single", "pairwise")
BLOCK_TYPES = ("rating", "requirements", "security", "scenarios", "questionnaire", "bugs", "pairwise")
SINGLE_BLOCK_TYPES = ("rating", "requirements", "security", "scenarios", "questionnaire", "bugs")
PAIRWISE_BLOCK_TYPES = ("pairwise", "bugs")
BLOCK_TYPES_BY_MODE = {"single": SINGLE_BLOCK_TYPES, "pairwise": PAIRWISE_BLOCK_TYPES}
QUESTION_KINDS = ("text", "textarea", "single", "multi", "scale", "number", "attention")
CHOICE_KINDS = ("single", "multi", "attention")
REQUIREMENT_SOURCES = ("frontend", "api", "admin_api")
STANDARD_INSTRUMENTS = ("sus", "umux_lite", "nasa_tlx")
COMMENT_MODES = ("off", "optional", "required")

ID_PATTERN = re.compile(r"^[a-z0-9_]{1,40}$")
CATEGORY_PATTERN = re.compile(r"^[a-z0-9_]{1,50}$")
MAX_BLOCKS = 30
MAX_ENTRIES = 50  # criteria / questions / checklist items / scenarios / custom requirements / options
MAX_LABEL = 300
MAX_HELP = 5000
MAX_TEXT = 20000
MAX_OPTION_VALUE = 100
MAX_CUSTOM_REQUIREMENT = 1000
MAX_SCALE_SPAN = 100
MAX_BUG_REPORTS = 20
MAX_FINDINGS = 50
MAX_FINDING_CATEGORIES = 20

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_LABEL)]
OptLabel = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_LABEL)]
Help = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_HELP)]
LongText = Annotated[str, StringConstraints(max_length=MAX_TEXT)]
OptionValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_OPTION_VALUE)]
RequirementText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CUSTOM_REQUIREMENT),
]

CommentMode = Literal["off", "optional", "required"]

DEFAULT_RATING_SCALE: dict[str, Any] = {
    "min": 1,
    "max": 7,
    "step": 1,
    "min_label": "Very poor",
    "max_label": "Excellent",
    "labels": {},
}

DEFAULT_CONSENT_TEXT = """## About this study

You are invited to take part in a research study that evaluates web applications. You will use one or \
more applications and record your judgements about them.

- **Voluntary:** taking part is voluntary. You may stop at any time without giving a reason, and you \
can withdraw from the task page.
- **What is recorded:** your answers, the reports and screenshots you submit, the requests you send \
through the built-in HTTP console, and timing information (how long each item was open). Your name and \
email address are not part of the research data.
- **Pseudonymous:** you are identified only by a participant number such as P007. Results are reported \
in aggregate.
- **Withdrawal:** when you withdraw you can also ask for the data you provided to be deleted.
- **Risks:** there are no known risks beyond everyday computer use. The applications are test \
deployments: do not enter real personal data or passwords you use elsewhere.
- **Questions:** contact the researcher who gave you the link or access code.

By continuing you confirm that you have read this information and agree to take part.
"""

_EXPERIENCE_LABELS = {"1": "None", "2": "Beginner", "3": "Intermediate", "4": "Advanced", "5": "Expert"}

DEFAULT_PRE_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "programming_years",
        "kind": "number",
        "label": "How many years of programming experience do you have?",
        "help": "Count study, work and hobby projects. Enter 0 if you have none.",
        "required": True,
        "min": 0,
        "max": 60,
    },
    {
        "id": "web_dev_experience",
        "kind": "scale",
        "label": "How experienced are you with web development?",
        "help": "Building websites or web applications (frontend or backend).",
        "required": True,
        "scale": {
            "min": 1,
            "max": 5,
            "step": 1,
            "min_label": "None",
            "max_label": "Expert",
            "labels": dict(_EXPERIENCE_LABELS),
        },
    },
    {
        "id": "security_testing_experience",
        "kind": "scale",
        "label": "How experienced are you with security testing of web applications?",
        "help": "For example penetration testing, CTFs, security courses or code reviews for vulnerabilities.",
        "required": True,
        "scale": {
            "min": 1,
            "max": 5,
            "step": 1,
            "min_label": "None",
            "max_label": "Expert",
            "labels": dict(_EXPERIENCE_LABELS),
        },
    },
]

# ---------------------------------------------------------------------------
# Structural models (mirroring the TS types)
# ---------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Scale(_Model):
    min: int
    max: int
    step: int = 1
    min_label: OptLabel = ""
    max_label: OptLabel = ""
    labels: dict[str, Label] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        data = handler(self)
        labels = data.get("labels") or {}
        try:
            data["labels"] = {key: labels[key] for key in sorted(labels, key=int)}
        except ValueError:
            data["labels"] = dict(labels)
        return data


def _default_rating_scale() -> Scale:
    return Scale(**copy.deepcopy(DEFAULT_RATING_SCALE))


class Criterion(_Model):
    id: str
    label: Label
    help: Help = ""
    scale: Scale = Field(default_factory=_default_rating_scale)
    required: bool = True
    allow_na: bool = False


class QuestionOption(_Model):
    value: OptionValue
    label: Label


class Question(_Model):
    id: str
    kind: Literal["text", "textarea", "single", "multi", "scale", "number", "attention"]
    label: Label
    help: Help = ""
    required: bool = True
    options: list[QuestionOption] = Field(default_factory=list)
    scale: Scale | None = None
    min: Any = None
    max: Any = None
    expected: str = ""

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        data = handler(self)
        keep = {"id", "kind", "label", "help", "required"}
        if self.kind in CHOICE_KINDS:
            keep.add("options")
        if self.kind == "scale":
            keep.add("scale")
        if self.kind == "number":
            keep.update(("min", "max"))
        if self.kind == "attention":
            keep.add("expected")
        return {key: value for key, value in data.items() if key in keep}


class RatingBlock(_Model):
    id: str
    type: Literal["rating"]
    title: Label
    instructions: LongText = ""
    criteria: list[Criterion] = Field(default_factory=list)
    comment: CommentMode = "optional"


class CustomRequirement(_Model):
    id: str
    text: RequirementText


class RequirementsBlock(_Model):
    id: str
    type: Literal["requirements"]
    title: Label
    instructions: LongText = ""
    sources: list[Literal["frontend", "api", "admin_api"]] = Field(
        default_factory=lambda: list(REQUIREMENT_SOURCES),
    )
    custom: list[CustomRequirement] = Field(default_factory=list)
    note: Literal["off", "optional", "required_unless_met"] = "optional"


class SecurityChecklistItem(_Model):
    id: str
    label: Label
    help: Help = ""
    category: OptLabel = ""


class FindingReviewConfig(_Model):
    enabled: bool = False
    max_findings: int = Field(default=10, ge=1, le=MAX_FINDINGS)
    categories: list[str] = Field(default_factory=list)
    include_ai: bool = False
    include_dependency: bool = False
    include_dynamic: bool = False


def _default_checklist() -> list[SecurityChecklistItem]:
    return [SecurityChecklistItem(**item) for item in default_security_checklist()]


class SecurityBlock(_Model):
    id: str
    type: Literal["security"]
    title: Label
    instructions: LongText = ""
    checklist: list[SecurityChecklistItem] = Field(default_factory=_default_checklist)
    reports: bool = True
    finding_review: FindingReviewConfig = Field(default_factory=FindingReviewConfig)
    http_console: bool = False


class Scenario(_Model):
    id: str
    title: Label
    description: LongText = ""
    success_criteria: LongText = ""


class ScenariosBlock(_Model):
    id: str
    type: Literal["scenarios"]
    title: Label
    instructions: LongText = ""
    scenarios: list[Scenario] = Field(default_factory=list)
    timed: bool = True
    ease_question: bool = True


class QuestionnaireBlock(_Model):
    id: str
    type: Literal["questionnaire"]
    title: Label
    instructions: LongText = ""
    instrument: Literal["sus", "umux_lite", "nasa_tlx", "custom"] = "sus"
    questions: list[Question] = Field(default_factory=list)


class BugsBlock(_Model):
    id: str
    type: Literal["bugs"]
    title: Label
    instructions: LongText = ""
    min_reports: int = Field(default=0, ge=0, le=MAX_BUG_REPORTS)


class PairwiseCriterion(_Model):
    id: str
    label: Label
    help: Help = ""


class PairwiseBlock(_Model):
    id: str
    type: Literal["pairwise"]
    title: Label
    instructions: LongText = ""
    criteria: list[PairwiseCriterion] = Field(default_factory=list)
    scale: Literal["ab_tie", "five_point"] = "ab_tie"
    overall: bool = True
    rationale: CommentMode = "optional"


Block = Annotated[
    RatingBlock | RequirementsBlock | SecurityBlock | ScenariosBlock | QuestionnaireBlock | BugsBlock | PairwiseBlock,
    Field(discriminator="type"),
]


class PrimaryMetric(_Model):
    block_id: str
    metric: str


class ExclusionRules(_Model):
    speeding: bool = False
    attention_failed: bool = False
    straightlining: bool = False
    app_not_viewed: bool = False


class AnalysisPlan(_Model):
    primary: PrimaryMetric | None = None
    planned_ratings_per_item: int | None = Field(default=None, ge=1, le=50)
    planned_participants: int | None = Field(default=None, ge=1, le=10000)
    exclusion_rules: ExclusionRules = Field(default_factory=ExclusionRules)
    unevaluable_policy: Literal["exclude", "floor"] = "exclude"


class ConsentConfig(_Model):
    enabled: bool = False
    text: LongText = ""


class TaskDesign(_Model):
    version: Literal[1] = 1
    blocks: list[Block] = Field(default_factory=list)
    analysis: AnalysisPlan = Field(default_factory=AnalysisPlan)
    consent: ConsentConfig = Field(default_factory=ConsentConfig)
    pre_questions: list[Question] = Field(default_factory=list)
    post_questions: list[Question] = Field(default_factory=list)


class BlindingSettings(_Model):
    show_spec: bool = True
    show_source_code: bool = False
    show_admin_credentials: bool = True


class AllocationSettings(_Model):
    strategy: Literal["balanced", "all"] = "balanced"
    ratings_per_item: int = Field(default=3, ge=1, le=50)
    allow_overflow: bool = False
    max_items_per_participant: int | None = Field(default=None, ge=1, le=10000)
    assignment_timeout_minutes: int = Field(default=60, ge=5, le=1440)
    allow_skip: bool = True


class PairingSettings(_Model):
    strategy: Literal["all_pairs", "random_k", "vs_baseline"] = "all_pairs"
    k: int = Field(default=2, ge=1, le=20)
    cross_condition_only: bool = True
    baseline_condition_id: int | None = Field(default=None, ge=1)


class AccessSettings(_Model):
    allow_invite_links: bool = True
    allow_access_codes: bool = True
    allow_accounts: bool = True


class HostingSettings(_Model):
    on_demand: bool = True
    idle_stop_minutes: int = Field(default=20, ge=5, le=240)
    max_running_apps: int = Field(default=3, ge=1, le=20)
    isolation: Literal["shared", "exclusive"] = "shared"
    reset: Literal["none", "per_assignment"] = "none"


class QualitySettings(_Model):
    min_seconds_per_item: int = Field(default=60, ge=0, le=7200)


class TaskSettings(_Model):
    blinding: BlindingSettings = Field(default_factory=BlindingSettings)
    allocation: AllocationSettings = Field(default_factory=AllocationSettings)
    pairing: PairingSettings = Field(default_factory=PairingSettings)
    access: AccessSettings = Field(default_factory=AccessSettings)
    hosting: HostingSettings = Field(default_factory=HostingSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)


# ---------------------------------------------------------------------------
# Freezing
# ---------------------------------------------------------------------------

#: Settings leaves (``section.key``) that may change after the design is frozen
#: (they do not change what is measured). ``quality.*`` only while the analysis
#: plan does not exclude speeders (see :func:`editable_after_freeze`).
EDITABLE_AFTER_FREEZE: frozenset[str] = frozenset(
    {
        "allocation.max_items_per_participant",
        "allocation.assignment_timeout_minutes",
        "allocation.allow_skip",
        "allocation.allow_overflow",
        *(f"access.{key}" for key in AccessSettings.model_fields),
        "hosting.on_demand",
        "hosting.idle_stop_minutes",
        "hosting.max_running_apps",
        *(f"quality.{key}" for key in QualitySettings.model_fields),
    },
)
QUALITY_KEYS: frozenset[str] = frozenset(f"quality.{key}" for key in QualitySettings.model_fields)
#: Every other settings leaf: part of ``design_hash``; changing one after freeze is a 409.
FROZEN_SETTINGS_KEYS: frozenset[str] = (
    frozenset(
        f"{section}.{key}"
        for section, model in TaskSettings.model_fields.items()
        for key in model.annotation.model_fields  # type: ignore[union-attr]
    )
    - EDITABLE_AFTER_FREEZE
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def default_settings() -> dict[str, Any]:
    """The full default ``TaskSettings`` document (a fresh dict)."""
    return TaskSettings().model_dump()


def default_design(mode: str) -> dict[str, Any]:
    """A sensible starting ``TaskDesign`` for ``mode`` (normalized, ready to open).

    single: one rating block (functionality, usability, overall on 1..7, can't-judge allowed);
    pairwise: one A/B preference block with an overall preference. Both enable consent with
    :data:`DEFAULT_CONSENT_TEXT`, ask :data:`DEFAULT_PRE_QUESTIONS` and pre-register the block's
    main metric as primary.
    """
    if mode == "pairwise":
        block: dict[str, Any] = {
            "id": "preference",
            "type": "pairwise",
            "title": "Which app is better?",
            "instructions": "Use both apps, then say which one is better on each criterion.",
            "criteria": [
                {
                    "id": "functionality",
                    "label": "Works as specified",
                    "help": "Features work and do what the spec asks.",
                },
                {"id": "usability", "label": "Easier to use", "help": "Clearer navigation, labels and feedback."},
            ],
            "scale": "ab_tie",
            "overall": True,
            "rationale": "optional",
        }
        primary = {"block_id": "preference", "metric": "overall"}
    else:
        block = {
            "id": "ratings",
            "type": "rating",
            "title": "Quality ratings",
            "instructions": "Use the app, then rate it on each criterion.",
            "criteria": [
                {
                    "id": "functionality",
                    "label": "Functional correctness",
                    "help": "Do the features work as the specification describes?",
                    "scale": copy.deepcopy(DEFAULT_RATING_SCALE),
                    "required": True,
                    "allow_na": True,
                },
                {
                    "id": "usability",
                    "label": "Usability",
                    "help": "How easy is the app to understand and use without help?",
                    "scale": copy.deepcopy(DEFAULT_RATING_SCALE),
                    "required": True,
                    "allow_na": True,
                },
                {
                    "id": "overall",
                    "label": "Overall quality",
                    "help": "Your overall judgement of the app as a deliverable for the specification.",
                    "scale": copy.deepcopy(DEFAULT_RATING_SCALE),
                    "required": True,
                    "allow_na": False,
                },
            ],
            "comment": "optional",
        }
        primary = {"block_id": "ratings", "metric": "composite"}
    raw = {
        "version": 1,
        "blocks": [block],
        "analysis": {"primary": primary},
        "consent": {"enabled": True, "text": DEFAULT_CONSENT_TEXT},
        "pre_questions": copy.deepcopy(DEFAULT_PRE_QUESTIONS),
        "post_questions": [],
    }
    return TaskDesign.model_validate(raw).model_dump()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """A new dict: ``override`` merged into ``base``; nested dicts merge, everything else replaces."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def merged_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    """``settings`` deep-merged over :func:`default_settings` without validation (for reading stored rows)."""
    return deep_merge(default_settings(), settings if isinstance(settings, dict) else {})


def scale_values(scale: dict[str, Any]) -> list[int]:
    """The allowed integer values of a (normalized) scale: min, min+step, ..., max."""
    step = int(scale.get("step") or 1)
    return list(range(int(scale["min"]), int(scale["max"]) + 1, step))


def block_by_id(design: dict[str, Any], block_id: str) -> dict[str, Any] | None:
    """The design block with this id, or None."""
    for block in design.get("blocks") or []:
        if block.get("id") == block_id:
            return block
    return None


def blocks_of_type(design: dict[str, Any], block_type: str) -> list[dict[str, Any]]:
    """Every design block of one type, in design order."""
    return [block for block in design.get("blocks") or [] if block.get("type") == block_type]


def finding_review_block(design: dict[str, Any]) -> dict[str, Any] | None:
    """The (only) security block with ``finding_review.enabled``, or None."""
    for block in blocks_of_type(design, "security"):
        if (block.get("finding_review") or {}).get("enabled"):
            return block
    return None


def has_http_console(design: dict[str, Any]) -> bool:
    """Whether any security block enables the HTTP console."""
    return any(block.get("http_console") for block in blocks_of_type(design, "security"))


def has_security_testing(design: dict[str, Any]) -> bool:
    """Whether the design has a security block (participants may change app state while testing)."""
    return bool(blocks_of_type(design, "security"))


def _criterion_ids(block: dict[str, Any]) -> list[str]:
    return [criterion["id"] for criterion in block.get("criteria") or []]


_METRICS: dict[str, Any] = {
    "rating": lambda block: ["composite", *_criterion_ids(block)],
    "requirements": lambda block: ["compliance"],
    "security": lambda block: ["vulnerable_share"] if block.get("checklist") else [],
    "scenarios": lambda block: ["success_rate"],
    "questionnaire": lambda block: ["score"] if block.get("instrument") in STANDARD_INSTRUMENTS else [],
    "bugs": lambda block: ["bugs_per_rater"],
    "pairwise": lambda block: [*(["overall"] if block.get("overall") else []), *_criterion_ids(block)],
}


def metric_options(block: dict[str, Any]) -> list[str]:
    """The ``analysis.primary.metric`` values valid for a block (see TS ``AnalysisPlan``).

    rating → ``composite`` or a criterion id; requirements → ``compliance``; security →
    ``vulnerable_share`` (needs a checklist); scenarios → ``success_rate``; standard questionnaire →
    ``score``; bugs → ``bugs_per_rater``; pairwise → ``overall`` (when asked) or a criterion id.
    """
    options = _METRICS.get(block.get("type", ""))
    return options(block) if options else []


def participant_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copies of ``questions`` without attention-check ``expected`` values (safe to send to participants)."""
    return [_strip_expected(question) for question in questions or []]


def participant_blocks(design: dict[str, Any]) -> list[dict[str, Any]]:
    """The design blocks as participants see them: deep copies with every nested ``expected`` removed."""
    return [_strip_expected(block) for block in design.get("blocks") or []]


def _strip_expected(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_expected(item) for key, item in value.items() if key != "expected"}
    if isinstance(value, list):
        return [_strip_expected(item) for item in value]
    return copy.deepcopy(value)


def canonical_json(value: Any) -> str:
    """Key-sorted, whitespace-free JSON (the input of every content hash in this module)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def editable_after_freeze(design: dict[str, Any] | None = None) -> frozenset[str]:
    """Settings leaves editable after freeze for this design.

    ``quality.*`` is frozen when the pre-registered analysis excludes speeders
    (``design.analysis.exclusion_rules.speeding``): the threshold then decides which data count.
    """
    rules = ((design or {}).get("analysis") or {}).get("exclusion_rules") or {}
    if rules.get("speeding"):
        return EDITABLE_AFTER_FREEZE - QUALITY_KEYS
    return EDITABLE_AFTER_FREEZE


def _leaves(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    leaves: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}{key}"
        if isinstance(item, dict) and item:
            leaves.update(_leaves(item, f"{path}."))
        else:
            leaves[path] = item
    return leaves


def frozen_settings(settings: dict[str, Any], design: dict[str, Any] | None = None) -> dict[str, Any]:
    """``settings`` without the keys :func:`editable_after_freeze` allows to change (nested dict)."""
    editable = editable_after_freeze(design)
    result: dict[str, Any] = {}
    for section, values in (settings or {}).items():
        if isinstance(values, dict):
            kept = {key: copy.deepcopy(item) for key, item in values.items() if f"{section}.{key}" not in editable}
            if kept:
                result[section] = kept
        elif section not in editable:
            result[section] = copy.deepcopy(values)
    return result


def design_hash(mode: str, design: dict[str, Any], settings: dict[str, Any]) -> str:
    """sha256 of the canonical JSON of ``{mode, design, settings minus editable keys}``.

    Pass the normalized documents (as stored on the task).
    """
    payload = {"mode": mode, "design": design, "settings": frozen_settings(settings, design)}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def changed_frozen_keys(
    old_settings: dict[str, Any] | None,
    new_settings: dict[str, Any] | None,
    *,
    design: dict[str, Any] | None = None,
) -> list[str]:
    """Sorted settings leaves (``section.key``) that differ and may not change after freeze.

    Both documents are deep-merged over the defaults first, so a partial update only
    compares what it sets. Pass the task's (frozen) ``design`` so ``quality.*`` counts as
    frozen when the analysis plan excludes speeders.
    """
    editable = editable_after_freeze(design)
    old = _leaves(merged_settings(old_settings))
    new = _leaves(merged_settings(new_settings))
    return sorted(key for key in set(old) | set(new) if key not in editable and old.get(key) != new.get(key))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_FRIENDLY_MESSAGES = {
    "extra_forbidden": "Unknown field",
    "missing": "Required",
    "union_tag_not_found": "Block type is required",
}


def _loc_path(loc: tuple[Any, ...]) -> str:
    parts: list[str] = []
    for index, part in enumerate(loc):
        is_block_tag = (
            isinstance(part, str)
            and part in BLOCK_TYPES
            and index >= 2
            and isinstance(loc[index - 1], int)
            and loc[index - 2] == "blocks"
        )
        if not is_block_tag:
            parts.append(str(part))
    return ".".join(parts)


def _pydantic_errors(exc: ValidationError) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    for error in exc.errors(include_url=False):
        path = _loc_path(tuple(error.get("loc") or ()))
        if path in seen:
            continue
        seen.add(path)
        kind = error.get("type", "")
        message = _FRIENDLY_MESSAGES.get(kind, error.get("msg", "Invalid value"))
        if kind == "union_tag_invalid":
            tag = (error.get("ctx") or {}).get("tag", "")
            message = f"Unknown block type '{tag}'"
        elif kind == "string_too_short" and (error.get("ctx") or {}).get("min_length") == 1:
            message = "Required"
        errors.append({"path": path, "message": message})
    return errors


class _Errors:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, path: str, message: str) -> None:
        self.items.append({"path": path, "message": message})


def _check_id(errors: _Errors, path: str, value: str) -> None:
    if not ID_PATTERN.match(value or ""):
        errors.add(path, "Use 1-40 lowercase letters, digits or underscores")


def _check_ids(errors: _Errors, path: str, entries: list[Any], *, what: str) -> None:
    """Valid and unique ``id`` of every entry; ``path`` is the list's path."""
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        entry_path = f"{path}.{index}.id"
        _check_id(errors, entry_path, entry.id)
        if entry.id in seen:
            errors.add(entry_path, f"Duplicate {what} id '{entry.id}'")
        seen.add(entry.id)


def _check_count(errors: _Errors, path: str, entries: list[Any], *, minimum: int, what: str, one: str = "") -> None:
    """At most :data:`MAX_ENTRIES` and at least ``minimum`` entries (``what`` plural, ``one`` singular)."""
    if len(entries) > MAX_ENTRIES:
        errors.add(path, f"At most {MAX_ENTRIES} {what}")
    elif len(entries) < minimum:
        errors.add(path, f"Add at least {minimum} {what}" if minimum > 1 else f"Add at least one {one or what}")


def _check_scale(errors: _Errors, path: str, scale: Scale) -> None:
    span = scale.max - scale.min
    if span < 1 or span > MAX_SCALE_SPAN:
        errors.add(f"{path}.max", f"max must be 1 to {MAX_SCALE_SPAN} above min")
        return
    if scale.step < 1 or span % scale.step:
        errors.add(f"{path}.step", "step must be a positive divisor of max - min")
        return
    for key in scale.labels:
        try:
            point = int(key)
        except ValueError:
            errors.add(f"{path}.labels.{key}", "Label keys must be scale values")
            continue
        if str(point) != key or point < scale.min or point > scale.max or (point - scale.min) % scale.step:
            errors.add(f"{path}.labels.{key}", "Label keys must be scale values")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _check_question(errors: _Errors, path: str, question: Question) -> None:
    kind = question.kind
    if kind in CHOICE_KINDS:
        options = question.options
        minimum = 1 if kind == "multi" else 2
        _check_count(errors, f"{path}.options", options, minimum=minimum, what="options", one="option")
        seen: set[str] = set()
        for index, option in enumerate(options):
            if option.value in seen:
                errors.add(f"{path}.options.{index}.value", f"Duplicate option value '{option.value}'")
            seen.add(option.value)
        if kind == "attention" and question.expected not in seen:
            errors.add(f"{path}.expected", "Pick the option a careful participant chooses")
    if kind == "scale":
        if question.scale is None:
            errors.add(f"{path}.scale", "Scale questions need a scale")
        else:
            _check_scale(errors, f"{path}.scale", question.scale)
    if kind == "number":
        for bound in ("min", "max"):
            value = getattr(question, bound)
            if value is not None and not _is_number(value):
                errors.add(f"{path}.{bound}", "Must be a number or empty")
        if _is_number(question.min) and _is_number(question.max) and question.min > question.max:
            errors.add(f"{path}.max", "max must not be below min")


def _check_questions(errors: _Errors, path: str, questions: list[Question], *, minimum: int = 0) -> None:
    _check_count(errors, path, questions, minimum=minimum, what="questions", one="question")
    _check_ids(errors, path, questions, what="question")
    for index, question in enumerate(questions):
        _check_question(errors, f"{path}.{index}", question)


def _check_block(errors: _Errors, path: str, block: Any) -> None:
    if isinstance(block, RatingBlock):
        _check_count(errors, f"{path}.criteria", block.criteria, minimum=1, what="criteria", one="criterion")
        _check_ids(errors, f"{path}.criteria", block.criteria, what="criterion")
        for index, criterion in enumerate(block.criteria):
            _check_scale(errors, f"{path}.criteria.{index}.scale", criterion.scale)
    elif isinstance(block, RequirementsBlock):
        if len(set(block.sources)) != len(block.sources):
            errors.add(f"{path}.sources", "Each source can be listed once")
        _check_count(errors, f"{path}.custom", block.custom, minimum=0, what="custom requirements")
        _check_ids(errors, f"{path}.custom", block.custom, what="requirement")
        if not block.sources and not block.custom:
            errors.add(f"{path}.sources", "Pick at least one requirement source or add a custom requirement")
    elif isinstance(block, SecurityBlock):
        _check_count(errors, f"{path}.checklist", block.checklist, minimum=0, what="checklist items")
        _check_ids(errors, f"{path}.checklist", block.checklist, what="checklist item")
        review = block.finding_review
        if len(review.categories) > MAX_FINDING_CATEGORIES:
            errors.add(f"{path}.finding_review.categories", f"At most {MAX_FINDING_CATEGORIES} categories")
        for index, category in enumerate(review.categories):
            if not CATEGORY_PATTERN.match(category):
                errors.add(f"{path}.finding_review.categories.{index}", "Not a finding category")
        if len(set(review.categories)) != len(review.categories):
            errors.add(f"{path}.finding_review.categories", "Each category can be listed once")
        if not (block.checklist or block.reports or review.enabled or block.http_console):
            errors.add(
                f"{path}.checklist",
                "Enable at least one activity (checklist, reports, finding review, console)",
            )
    elif isinstance(block, ScenariosBlock):
        _check_count(errors, f"{path}.scenarios", block.scenarios, minimum=1, what="scenarios", one="scenario")
        _check_ids(errors, f"{path}.scenarios", block.scenarios, what="scenario")
    elif isinstance(block, QuestionnaireBlock):
        if block.instrument == "custom":
            _check_questions(errors, f"{path}.questions", block.questions, minimum=1)
        elif block.questions:
            errors.add(f"{path}.questions", "Standard instruments use the server's fixed questions; leave this empty")
    elif isinstance(block, PairwiseBlock):
        _check_count(errors, f"{path}.criteria", block.criteria, minimum=0, what="criteria")
        _check_ids(errors, f"{path}.criteria", block.criteria, what="criterion")
        if not block.criteria and not block.overall:
            errors.add(f"{path}.criteria", "Add a criterion or ask for an overall preference")


def _check_design(mode: str, design: TaskDesign) -> list[dict[str, str]]:
    errors = _Errors()
    if mode not in MODES:
        errors.add("mode", "Mode must be single or pairwise")
        return errors.items
    blocks = design.blocks
    if len(blocks) > MAX_BLOCKS:
        errors.add("blocks", f"At most {MAX_BLOCKS} blocks")
    _check_ids(errors, "blocks", blocks, what="block")
    allowed = BLOCK_TYPES_BY_MODE[mode]
    for index, block in enumerate(blocks):
        path = f"blocks.{index}"
        if block.type not in allowed:
            errors.add(f"{path}.type", f"{block.type.capitalize()} blocks are not available in {mode} mode")
        _check_block(errors, path, block)
    review_blocks = [i for i, b in enumerate(blocks) if isinstance(b, SecurityBlock) and b.finding_review.enabled]
    for index in review_blocks[1:]:
        errors.add(f"blocks.{index}.finding_review.enabled", "Only one security block may review findings")
    bug_blocks = [i for i, b in enumerate(blocks) if isinstance(b, BugsBlock)]
    for index in bug_blocks[1:]:
        errors.add(f"blocks.{index}.type", "Only one bug log block is allowed")

    consent = design.consent
    if consent.enabled and not consent.text.strip():
        errors.add("consent.text", "Write the consent text participants agree to")

    _check_questions(errors, "pre_questions", design.pre_questions)
    _check_questions(errors, "post_questions", design.post_questions)
    pre_ids = {question.id for question in design.pre_questions}
    for index, question in enumerate(design.post_questions):
        if question.id in pre_ids:
            errors.add(f"post_questions.{index}.id", f"'{question.id}' is already a pre-questionnaire id")

    primary = design.analysis.primary
    if primary is not None:
        block = next((b for b in blocks if b.id == primary.block_id), None)
        if block is None:
            errors.add("analysis.primary.block_id", "No block with this id")
        else:
            options = metric_options(block.model_dump())
            if primary.metric not in options:
                hint = ", ".join(options) or "none for this block"
                errors.add("analysis.primary.metric", f"Not a metric of this block (choose: {hint})")
    return errors.items


def validate_design(mode: str, design: Any) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Validate and normalize a ``TaskDesign`` for ``mode``.

    Returns ``(normalized, [])`` or ``(None, errors)`` with ``errors = [{"path", "message"}]``.
    Saving needs a valid design; zero blocks or a pairwise task without a pairwise block are
    open blockers (:func:`design_open_blockers`), not validation errors.
    """
    if not isinstance(design, dict):
        return None, [{"path": "", "message": "The design must be an object"}]
    try:
        model = TaskDesign.model_validate(design)
    except ValidationError as exc:
        return None, _pydantic_errors(exc)
    errors = _check_design(mode, model)
    if errors:
        return None, errors
    return model.model_dump(), []


def validate_settings(settings: Any) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Validate ``settings`` deep-merged over :func:`default_settings`; returns ``(normalized, errors)``."""
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        return None, [{"path": "", "message": "The settings must be an object"}]
    try:
        model = TaskSettings.model_validate(deep_merge(default_settings(), settings))
    except ValidationError as exc:
        return None, _pydantic_errors(exc)
    errors = _Errors()
    if model.hosting.reset == "per_assignment" and model.hosting.isolation != "exclusive":
        errors.add("hosting.reset", "Resetting apps per assignment needs exclusive isolation")
    if errors.items:
        return None, errors.items
    return model.model_dump(), []


def design_open_blockers(mode: str, design: dict[str, Any]) -> list[str]:
    """Design problems that block opening (the design itself is assumed valid)."""
    blockers: list[str] = []
    blocks = design.get("blocks") or []
    if not blocks:
        blockers.append("Add at least one evaluation block")
    if mode == "pairwise" and not blocks_of_type(design, "pairwise"):
        blockers.append("A pairwise task needs a pairwise (A/B preference) block")
    return blockers


def settings_open_blockers(settings: dict[str, Any]) -> list[str]:
    """Settings problems that block opening (no way for participants to get in)."""
    access = merged_settings(settings)["access"]
    if not any(access.values()):
        return ["Enable at least one access method (invite links, access codes or accounts)"]
    return []
