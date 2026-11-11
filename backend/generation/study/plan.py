"""Resolve a study design into a ``ResolvedPlan`` (ephemeral or persisted as ``StudyPlan``) (spec sections 6.1, 9.4).

``resolve(study)`` pins every factor level to ``slug@v#hash12`` (a bare slug takes the latest non-archived
version *now*, at plan time; nothing is ever looked up as "latest" at run time), expands the grid
(``study/expand.py``), and for every (condition, unit):

* renders the stimuli once (they never depend on the repeat; ``StudyDeps.render_stimuli``, WP-03);
* resolves the effective policy and its lint issues (``StudyDeps.effective_policy``, WP-11), the environment
  (harness, gateway, sandbox, image digests; ``study/environment.py``) and, in slot mode, the resolved context
  (``StudyDeps.resolve_slot_context``, WP-12);
* builds the manifest body every run of the pair shares.

Every cell ``(condition, unit, stage, repeat)`` then gets its CRN seed (``identity.crn_seed``: equal across
conditions, prose variants included, for one (unit, repeat)), its block index and claim sequence
(``study/order.py``: repeat-major, seeded unit order, seeded condition shuffle per block) and its identity
keys from ``identity.compute_keys``, the only implementation of the identity table. Estimates come from
``StudyDeps.estimate_cell`` / ``estimate_plan`` (WP-11), computed once per (condition, unit).

A pilot (``policy.pilot.enabled``) adds ``stage="pilot"`` cells: ``pilot.repeats`` repeats of the first
``pilot.units`` units in unit order; they come first in the claim sequence and in block order. Pilot and main
cells of one (condition, unit, repeat) share the CRN seed, which makes ``pilot.reuse_as_main`` sound.

``resolve(..., persist=False)`` writes nothing: the result is cached in-process by ``(study, design_hash,
options)`` for :data:`CACHE_TTL_S`. ``persist=True`` (freeze-plan and launch) stores the payload and every
stimulus text in the blob store and creates the ``StudyPlan`` row (immutable; unique per ``(study, plan_hash)``).

Issues use registered codes only (``contracts/issues.py``); any ``error`` blocks launch. Incompatible
combinations (a style that does not fit the frontend, a missing starter, ``spec_defined`` without an
``auth_flow``, a prose variant the task lacks, a harness that is not enabled or has no protocol mode for the
endpoint) are errors unless ``drop_incompatible``, which drops them with ``combination_pruned`` (info).
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.db import IntegrityError
from django.db import transaction

from backend.generation import identity
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import is_registered
from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.library import HarnessPresetContent
from backend.generation.contracts.library import SamplingContent
from backend.generation.contracts.manifest import FactorRecord
from backend.generation.contracts.manifest import ManifestBaseImage
from backend.generation.contracts.manifest import ManifestBody
from backend.generation.contracts.manifest import ManifestContextFile
from backend.generation.contracts.manifest import ManifestEndpoint
from backend.generation.contracts.manifest import ManifestEvalSuite
from backend.generation.contracts.manifest import ManifestIdentity
from backend.generation.contracts.manifest import ManifestModel
from backend.generation.contracts.manifest import ManifestPricing
from backend.generation.contracts.manifest import ManifestResolvedContext
from backend.generation.contracts.manifest import ManifestResources
from backend.generation.contracts.manifest import ManifestSampling
from backend.generation.contracts.manifest import ManifestSlot
from backend.generation.contracts.manifest import ManifestSlotValidation
from backend.generation.contracts.manifest import ManifestStarter
from backend.generation.contracts.manifest import ManifestStimulus
from backend.generation.contracts.manifest import ManifestTask
from backend.generation.contracts.manifest import ParentSelectionRecord
from backend.generation.contracts.manifest import PlannedEvaluation
from backend.generation.contracts.manifest import StimulusRecord
from backend.generation.contracts.plan import Design
from backend.generation.contracts.plan import ResolvedCell
from backend.generation.contracts.plan import ResolvedCondition
from backend.generation.contracts.plan import ResolvedPlan
from backend.generation.contracts.plan import ResolvedUnit
from backend.generation.contracts.plan import cell_key
from backend.generation.contracts.resources import ResourcePolicyContent
from backend.generation.contracts.slot import LeakReport
from backend.generation.contracts.slot import SlotPolicy
from backend.generation.contracts.slot import TargetValidation
from backend.generation.study import design as design_rules
from backend.generation.study import parents as study_parents
from backend.generation.study.deps import get_deps
from backend.generation.study.environment import EnvironmentRequest
from backend.generation.study.environment import study_sandbox
from backend.generation.study.expand import expand_grid
from backend.generation.study.order import block_sequence
from backend.generation.study.order import condition_order
from backend.generation.study.order import unit_orders
from backend.inference.snapshot import endpoint_section

if TYPE_CHECKING:
    from backend.generation.contracts.resources import EffectivePolicy
    from backend.generation.contracts.slot import ResolvedContext
    from backend.generation.models import EvalSuite
    from backend.generation.models import GenerationRun
    from backend.generation.models import SlotTarget
    from backend.generation.models import Study
    from backend.generation.models import StudyPlan
    from backend.generation.models import TaskSpecVersion
    from backend.generation.study.environment import CellEnvironment
    from backend.llm_models.models import LLMModel
    from backend.users.models import User

logger = logging.getLogger(__name__)
# Registered by WP-01 on request (CHANGES "WP-10 -> WP-01: parent_draw_pending"); emitted once it exists.
PARENT_DRAW_PENDING: Final = "parent_draw_pending"

__all__ = [
    "CACHE_TTL_S",
    "RESOLVER_VERSION",
    "PlanBundle",
    "cached_bundle",
    "clear_cache",
    "freeze",
    "load_bundle",
    "persist_bundle",
    "resolve",
    "resolve_bundle",
]

RESOLVER_VERSION: Final = 1
CACHE_TTL_S: Final = 600
_CACHE_MAX: Final = 32
_CHARS_PER_TOKEN: Final = 3.6
_REF_RE: Final = re.compile(
    r"^(?P<slug>[A-Za-z0-9][A-Za-z0-9_-]*)(?:@(?P<version>[1-9][0-9]*))?(?:#(?P<hash>[0-9a-f]{12}))?$",
)
# Defaults for factors the design does not list (section 5.5: the default harness is opencode-native).
APP_DEFAULTS: Final[dict[str, str]] = {
    "harness_preset": "opencode-native",
    "starter_level": "minimal",
    "style": "model-choice",
    "auth_mode": "contract_bearer",
    "guidance": "none",
    "prompt_set": "app-default",
    "prose_variant": "standard",
    "sampling_preset": "provider-default",
}
SLOT_DEFAULTS: Final[dict[str, str]] = {
    "slot_executor": "single_shot",
    "guidance": "none",
    "sampling_preset": "t07",  # slot studies never use provider-default sampling (section 7.10, WP-04)
    "hole_mode": "stub",
    "keep_docstring": "false",
    "context_policy": "related_files",
}
SLOT_PROMPT_SET_DEFAULTS: Final = {"slot_single_shot": "slot-single-shot-default", "slot_agent": "slot-agent-default"}
SLOT_HARNESS_DEFAULT: Final = "opencode-native"
# run kind -> budget.policy executor (WP-11)
_POLICY_EXECUTORS: Final = {
    "app_agentic": "agentic",
    "slot_single_shot": "single_shot",
    "slot_agent": "restricted_agent",
}
# budget lint rules that read token sizes (filled after rendering, ``_Resolver._token_lint``)
_TOKEN_LINT_CODES: Final = frozenset({"brief_exceeds_call_cap", "slot_output_cap_tight", "slot_context_exceeds_model"})
_KIND_EXECUTOR_LEVEL: Final = {"slot_single_shot": "single_shot", "slot_agent": "restricted_agent"}


class Unresolved(Exception):  # noqa: N818 - a control-flow signal carrying its issue
    """A level or unit that cannot be resolved; carries the registered issue."""

    def __init__(self, issue: Issue) -> None:
        super().__init__(issue.message)
        self.issue = issue


# ---------------------------------------------------------------------------
# Library resolution
# ---------------------------------------------------------------------------


def _visible(row: Any, user: User | None) -> bool:
    if user is None or getattr(user, "is_staff", False):
        return True
    visibility = getattr(row, "visibility", "system")
    return bool(row.is_system) or visibility in ("system", "shared") or row.created_by_id == user.pk


class Library:
    """Resolves design refs to library rows, pinning bare refs at plan time (cached per resolve)."""

    def __init__(self, user: User | None, kind: str) -> None:
        self.user = user
        self.study_kind = kind
        self._cache: dict[tuple[str, str], Any] = {}

    def _unknown(self, kind: str, ref: str, path: str, why: str) -> Unresolved:
        return Unresolved(
            make_issue(
                "lint_unknown_library_ref",
                self.study_kind,
                message=f"{kind} {ref!r}: {why}",
                path=path,
                data={"kind": kind, "ref": ref},
            ),
        )

    def entity(self, kind: str, ref: str, *, path: str) -> Any:
        """The row ``ref`` names, pinned now (open WP-01 follow-up #10: draft refs in ``Design`` are pinned here).

        ``slug@v`` / ``slug@v#hash12`` go through ``library.service.resolve_ref`` (visibility, 404, 409
        ``hash_mismatch``) and a bare ``slug`` through ``library.service.latest`` (the latest non-archived
        version visible to the study owner); any ``LibraryError`` is a ``lint_unknown_library_ref`` issue.
        """
        key = (kind, ref)
        if key in self._cache:
            return self._cache[key]
        from backend.generation.library import service
        from backend.generation.library.service import LibraryError

        match = _REF_RE.match(ref.strip())
        if match is None:
            raise self._unknown(kind, ref, path, "not a slug, slug@version or slug@version#hash12")
        slug, version, pin = match.group("slug"), match.group("version"), match.group("hash")
        try:
            if version:
                row = service.resolve_ref(f"{slug}@{version}" + (f"#{pin}" if pin else ""), kind=kind, user=self.user)
            else:
                row = service.latest(kind, slug, user=self.user)
        except LibraryError as exc:
            why = "hash mismatch" if exc.code == "hash_mismatch" else "no such version visible to the study owner"
            raise self._unknown(kind, ref, path, f"{why} ({exc.message})") from None
        if pin and not version and not row.content_hash.startswith(pin):
            raise self._unknown(kind, ref, path, f"hash mismatch (stored {row.pinned_ref})")
        self._cache[key] = row
        return row

    def model(self, model_id: str, *, path: str) -> LLMModel:
        from backend.llm_models.models import LLMModel

        key = ("model", model_id)
        if key not in self._cache:
            row = LLMModel.objects.select_related("endpoint").filter(model_id=model_id).first()
            if row is None:
                msg = f"unknown model {model_id!r}"
                raise design_rules.DesignError(msg, path=path)
            self._cache[key] = row
        return self._cache[key]


# ---------------------------------------------------------------------------
# Data carried through a resolve
# ---------------------------------------------------------------------------


@dataclass
class _Unit:
    index: int
    key: str
    unit_order: int = 0
    task_version: TaskSpecVersion | None = None
    target: SlotTarget | None = None

    @property
    def parent(self) -> GenerationRun | None:
        return self.target.slot_set.parent_run if self.target is not None else None


@dataclass
class _Condition:
    index: int
    raw: dict[str, str]
    levels: dict[str, str]
    records: dict[str, FactorRecord]
    kind: str
    model: LLMModel
    rows: dict[str, Any]  # factor key -> library row
    label: str = ""
    starter_level: str | None = None
    auth_mode: str | None = None
    prose_variant: str | None = None
    network_policy: str = "none"
    slot_policy: SlotPolicy | None = None
    policy: EffectivePolicy | None = None
    treatment_view: dict[str, Any] = field(default_factory=dict)
    policy_dump: dict[str, Any] = field(default_factory=dict)
    starters: dict[int, Any] = field(default_factory=dict)  # stack pk -> Starter (app)
    full: dict[str, str] = field(default_factory=dict)  # raw levels with the mode defaults


@dataclass
class _Pair:
    """Everything the cells of one (condition, unit) share."""

    condition: _Condition
    unit: _Unit
    manifest: ManifestBody | None = None
    identity_inputs: dict[str, Any] = field(default_factory=dict)
    stimuli: list[StimulusRecord] = field(default_factory=list)
    slot_context: ResolvedContext | None = None
    excluded: str | None = None
    estimate: Any = None
    problems: list[Issue] = field(default_factory=list)
    # Token sizes for the budget lint (``brief_exceeds_call_cap``, ``slot_*``), filled while rendering.
    brief_tokens: int | None = None
    prompt_tokens: int | None = None
    original_tokens: int | None = None


@dataclass
class PlanBundle:
    """A resolved plan with the stimulus texts it references (``sha -> text``) and its persisted row, if any."""

    plan: ResolvedPlan
    texts: dict[str, str] = field(default_factory=dict)
    row: StudyPlan | None = None
    unbalanced: bool = False

    @property
    def persisted(self) -> bool:
        return self.row is not None

    def cell(self, key: str) -> ResolvedCell | None:
        for cell in self.plan.cells:
            if cell.cell_key == key:
                return cell
        return None


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def _est_tokens(text: str) -> int:
    return math.ceil(len(text) / _CHARS_PER_TOKEN)


def _short(level: str) -> str:
    return level.split("#", 1)[0]


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen: dict[tuple[str, str, str | None], Issue] = {}
    for issue in issues:
        key = (issue.code, issue.message, issue.path)
        if key in seen:
            merged = seen[key]
            merged.cells.extend(c for c in issue.cells if c not in merged.cells)
        else:
            seen[key] = issue.model_copy(deep=True)
    return list(seen.values())


def _stack_minimums(stacks: list[Any]) -> tuple[str | None, int | None]:
    """The largest ``resources.min_memory`` (as written) and ``min_cpus`` (nano CPUs) over ``stacks``."""
    from backend.generation.study.environment import memory_bytes

    memory: str | None = None
    cpus: int | None = None
    for stack in stacks:
        resources = (getattr(stack, "content", None) or {}).get("resources") or {}
        if resources.get("min_memory") and (
            memory is None or memory_bytes(resources["min_memory"]) > memory_bytes(memory)
        ):
            memory = str(resources["min_memory"])
        if resources.get("min_cpus"):
            cpus = max(cpus or 0, int(float(resources["min_cpus"]) * 1_000_000_000))
    return memory, cpus


def _largest(values: Any) -> int | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _original_tokens(target: Any) -> int | None:
    """The estimated token size of a slot's original code (None when its blob is not stored)."""
    from backend.generation import blobs

    sha = getattr(target, "original_blob", None)
    if not sha or not blobs.exists(sha):
        return None
    return _est_tokens(blobs.get_text(sha))


def _supports_seed(model: LLMModel) -> bool:
    if model.endpoint_id is not None:
        # Local endpoints (ollama, vLLM) honour the seed; Anthropic's native Messages API has none.
        return model.endpoint.api_flavor != "anthropic"
    supported = (model.metadata or {}).get("openrouter_supported_parameters")
    return isinstance(supported, list) and "seed" in supported


class _Resolver:
    def __init__(
        self,
        study: Study,
        design: Design,
        *,
        drop_incompatible: bool,
        repeats: int | None = None,
        include_pilot: bool = True,
    ) -> None:
        self.study = study
        self.design = design
        self.kind = design.kind
        self.drop = drop_incompatible
        self.repeats = repeats if repeats is not None else design.repeats
        self.include_pilot = include_pilot
        self.deps = get_deps()
        self.user = study.created_by
        self.lib = Library(self.user, design.kind)
        self.issues: list[Issue] = []
        self.texts: dict[str, str] = {}
        self.pruned = 0
        self._env_cache: dict[tuple[Any, ...], CellEnvironment] = {}
        self._stack_minimums: tuple[str | None, int | None] = (None, None)
        self.draw: study_parents.DrawnUnits | None = None

    # -- issues ----------------------------------------------------------------------------------

    def _issue(self, code: str, **kwargs: Any) -> Issue:
        issue = make_issue(code, self.kind, **kwargs)
        self.issues.append(issue)
        return issue

    # -- study-level inputs ----------------------------------------------------------------------

    def _study_policy(self) -> tuple[Any, ResourcePolicyContent]:
        row = self.lib.entity("resource_policy", self.design.resource_policy, path="resource_policy")
        return row, ResourcePolicyContent.model_validate(row.content)

    def _eval_suite(self) -> EvalSuite:
        return self.lib.entity("eval_suite", self.design.evaluation.eval_suite, path="evaluation.eval_suite")

    def _units(self) -> list[_Unit]:
        units: list[_Unit] = []
        if self.design.mode == "app":
            for position, ref in enumerate(self.design.units.tasks):
                base = design_rules.LEGACY_UNIT_RE.sub("", ref)  # the legacy issue is recorded
                try:
                    version = self.lib.entity("task", base, path=f"units.tasks[{position}]")
                except Unresolved as exc:
                    self.issues.append(exc.issue)
                    continue
                if any(unit.key == version.unit_key for unit in units):
                    continue
                units.append(_Unit(index=len(units), key=version.unit_key, task_version=version))
        else:
            from backend.generation.models import SlotTarget

            sets = [str(sid) for sid in self.design.units.slot_sets]
            qs = SlotTarget.objects.select_related(
                "slot_set__parent_run__stack_profile",
                "slot_set__parent_run__starter",
                "slot_set__parent_run__model",
                "slot_set__task_version__task",
            )
            if study_parents.uses_draw(self.design):
                ordered = self._drawn_targets()
            elif self.design.units.slot_targets:
                targets = list(qs.filter(pk__in=list(self.design.units.slot_targets)))
                by_id = {str(t.pk): t for t in targets}
                ordered = []
                for position, target_id in enumerate(self.design.units.slot_targets):
                    target = by_id.get(str(target_id))
                    if target is None or (sets and str(target.slot_set_id) not in sets):
                        msg = f"slot target {target_id} is unknown or not in the listed slot sets"
                        raise design_rules.DesignError(msg, path=f"units.slot_targets[{position}]")
                    ordered.append(target)
            else:
                ordered = list(qs.filter(slot_set_id__in=sets).order_by("slot_set__created_at", "created_at", "pk"))
                if not ordered:
                    msg = "the listed slot sets have no targets"
                    raise design_rules.DesignError(msg, path="units.slot_sets")
            units = [_Unit(index=i, key=target.unit_key, target=target) for i, target in enumerate(ordered)]
        if not units:
            msg = "no unit of the design could be resolved"
            raise design_rules.DesignError(msg, path="units")
        orders = unit_orders([unit.key for unit in units], self.design.order_seed)
        for unit, order in zip(units, orders, strict=True):
            unit.unit_order = order
        return units

    def _drawn_targets(self) -> list[SlotTarget]:
        """Units of a design that draws its parents (``study/parents.py``; nothing is created here)."""
        drawn = study_parents.drawn_units(self.design, user=self.user, create=False, study=self.study)
        self.draw = drawn
        if not drawn.parents:
            msg = "the parent pool of parent_selection is empty (no usable tested app_agentic run matches it)"
            raise design_rules.DesignError(msg, path="parent_selection.pool_query")
        if not drawn.complete:
            message = (
                f"{len(drawn.parents)} parent(s) drawn (seed {drawn.seed}, pool {drawn.pool_size}); "
                f"{len(drawn.missing_sets)} not frozen yet and {len(drawn.missing_targets)} template match(es) "
                "missing: freezing the plan freezes the parents and matches the templates"
            )
            data = {
                "seed": drawn.seed,
                "pool_size": drawn.pool_size,
                "pinned": drawn.pinned,
                "parents": [str(p.pk) for p in drawn.parents],
                "missing_sets": drawn.missing_sets,
                "missing_targets": drawn.missing_targets,
            }
            if is_registered(PARENT_DRAW_PENDING):
                self._issue(PARENT_DRAW_PENDING, message=message, path="parent_selection", data=data)
            else:
                logger.info("study %s: %s", self.study.pk, message)
        if not drawn.targets:
            msg = "no drawn parent has a matched target yet; freeze the plan to draw the parents"
            raise design_rules.DesignError(msg, path="parent_selection")
        return drawn.targets

    # -- conditions ------------------------------------------------------------------------------

    def _level_row(self, key: str, raw: str) -> Any:
        kind = design_rules.LIBRARY_FACTORS[key]
        return self.lib.entity(kind, raw, path=f"factors.{key}")

    def _raw_with_defaults(self, raw: dict[str, str]) -> dict[str, str]:
        full = dict(raw)
        defaults = APP_DEFAULTS if self.design.mode == "app" else SLOT_DEFAULTS
        for key, value in defaults.items():
            full.setdefault(key, value)
        full.setdefault("resource_policy", self.design.resource_policy)
        if self.design.mode == "slot":
            kind = design_rules.SLOT_EXECUTORS[full["slot_executor"]]
            full.setdefault("prompt_set", SLOT_PROMPT_SET_DEFAULTS[kind])
            if kind == "slot_agent":
                full.setdefault("harness_preset", SLOT_HARNESS_DEFAULT)
        return full

    def _compatible(self, raw: dict[str, str]) -> list[Issue]:
        """Condition-level incompatibilities (expansion callback; resolution failures surface later)."""
        full = self._raw_with_defaults(raw)
        problems: list[Issue] = []
        try:
            stack = self._level_row("stack_profile", full["stack_profile"]) if "stack_profile" in full else None
            style = self._level_row("style", full["style"]) if "style" in full else None
        except Unresolved:
            return []
        if stack is not None and style is not None:
            frontend = stack.frontend_framework
            compatible = list((style.content or {}).get("compatible_frontends") or [])
            if frontend not in compatible and "any" not in compatible:
                problems.append(
                    make_issue(
                        "incompatible_style",
                        self.kind,
                        message=f"style {style.ref} does not fit frontend {frontend!r} of {stack.ref}",
                        data={"style": style.pinned_ref, "stack": stack.pinned_ref, "frontend": frontend},
                    ),
                )
        if stack is not None and self.design.mode == "app" and self._starter(stack, full["starter_level"]) is None:
            problems.append(
                make_issue(
                    "starter_missing",
                    self.kind,
                    message=f"{stack.ref} has no {full['starter_level']} starter",
                    data={"stack": stack.pinned_ref, "level": full["starter_level"]},
                ),
            )
        return problems

    def _starter(self, stack: Any, level: str) -> Any:
        """The starter of exactly this profile version at ``level`` (``build.starters.find_starter``, WP-05)."""
        from backend.generation.build.starters import find_starter

        key = ("starter", f"{stack.pk}:{level}")
        if key not in self.lib._cache:  # noqa: SLF001 - the resolver's own cache
            starter = find_starter(stack, level)
            self.lib._cache[key] = starter if starter is not None and _visible(starter, self.user) else None  # noqa: SLF001
        return self.lib._cache[key]  # noqa: SLF001

    def _record(self, key: str, level: str, row: Any = None) -> FactorRecord:
        if row is not None:
            return FactorRecord(ref=row.pinned_ref, content_hash=row.content_hash)
        return FactorRecord(value=level)

    def _condition(self, index: int, raw: dict[str, str], varying: list[str]) -> _Condition:
        full = self._raw_with_defaults(raw)
        rows: dict[str, Any] = {}
        levels: dict[str, str] = {}
        records: dict[str, FactorRecord] = {}
        for key, level in full.items():
            if key == "model":
                model = self.lib.model(level, path="factors.model")
                levels[key] = model.model_id
                records[key] = FactorRecord(value=model.model_id)
            elif key in design_rules.LIBRARY_FACTORS:
                row = self._level_row(key, level)
                rows[key] = row
                levels[key] = row.pinned_ref
                records[key] = self._record(key, level, row)
            elif key == "context_policy":
                pinned = design_rules.context_policy_level(design_rules.parse_context_policy(level))
                levels[key] = pinned
                records[key] = FactorRecord(value=pinned)
            else:
                levels[key] = level
                records[key] = FactorRecord(value=level)
        for key, row in rows.items():
            mode = (row.content or {}).get("mode")
            if key == "prompt_set" and mode != self.design.mode:
                raise Unresolved(
                    make_issue(
                        "lint_library_ref_kind",
                        self.kind,
                        message=f"prompt set {row.ref} is a {mode} prompt set, not {self.design.mode}",
                        path="factors.prompt_set",
                    ),
                )
        mode_kind = "app_agentic" if self.design.mode == "app" else design_rules.SLOT_EXECUTORS[full["slot_executor"]]
        condition = _Condition(
            index=index,
            raw=raw,
            levels=levels,
            records=records,
            kind=mode_kind,
            model=self.lib.model(full["model"], path="factors.model"),
            rows=rows,
            full=full,
        )
        condition.label = " · ".join(_short(levels[key]) for key in varying if key in levels)[:200] or (
            condition.model.model_id
        )
        if self.design.mode == "app":
            condition.starter_level = full["starter_level"]
            condition.auth_mode = full["auth_mode"]
            condition.prose_variant = full["prose_variant"]
        else:
            if mode_kind == "slot_single_shot":
                rows.pop("harness_preset", None)
            condition.slot_policy = SlotPolicy(
                hole_mode=full["hole_mode"],  # type: ignore[arg-type]
                keep_docstring=full["keep_docstring"] == "true",
                context_policy=design_rules.parse_context_policy(full["context_policy"]),
                edit_policy="revert",
            )
        return condition

    # -- effective policies (budget.policy.resolve_study_policies, WP-11) --------------------------------

    @staticmethod
    def _harness(condition: _Condition) -> str | None:
        preset = condition.rows.get("harness_preset")
        return preset.harness if preset is not None else None

    def _condition_inputs(self, conditions: list[_Condition]) -> list[Any]:
        from backend.generation.budget.policy import ConditionInput

        return [
            ConditionInput(
                index=c.index,
                policy=c.rows["resource_policy"],
                model=c.model,
                harness=self._harness(c),
                executor=_POLICY_EXECUTORS[c.kind],
                stack=getattr(c.rows.get("stack_profile"), "slug", "") or "",
                cells=(f"c{c.index}",),
            )
            for c in conditions
        ]

    def _resolve_policies(self, conditions: list[_Condition], stacks: list[Any]) -> None:
        """Every condition's effective policy, resolved once per study with the study context.

        ``resolve_study_policies`` sees all conditions at once, so the serving tier is one per study and
        the cross-condition lint (``resources_vary_by_stack``, ``mixed_serving_tier``) fires at resolve;
        the stacks' largest minimums raise the sandbox as :func:`study_sandbox` does for the manifest.
        Token-size rules need the rendered stimuli and run later (:meth:`_token_lint`).
        """
        policies: dict[int, EffectivePolicy] = {}
        if self.deps.study_policies is None:
            for c in conditions:  # a binding without the study-level seam (see ``StudyDeps.study_policies``)
                policies[c.index] = self.deps.effective_policy(
                    c.rows["resource_policy"],
                    study=self.study,
                    model=c.model,
                    harness=self._harness(c),
                    executor=_POLICY_EXECUTORS[c.kind],
                )
        else:
            self._stack_minimums = _stack_minimums(stacks)
            resolved = self.deps.study_policies(
                self.study,
                self._condition_inputs(conditions),
                stack_min_memory=self._stack_minimums[0],
                stack_min_nano_cpus=self._stack_minimums[1],
            )
            policies = dict(resolved.by_condition)
            self.issues += list(resolved.issues)
        views: dict[int, dict[str, Any]] = {}
        for c in conditions:
            policy = policies[c.index]
            for issue in policy.issues:
                self.issues.append(issue.model_copy(update={"cells": [f"c{c.index}"]}))
            if id(policy) not in views:
                views[id(policy)] = self.deps.treatment_view(policy)
            c.policy, c.treatment_view = policy, views[id(policy)]
            c.policy_dump = policy.model_dump(mode="json")
            self._network_policy(c)

    def _token_lint(self, conditions: list[_Condition], pairs: dict[tuple[int, int], _Pair]) -> None:
        """``brief_exceeds_call_cap``, ``slot_output_cap_tight``, ``slot_context_exceeds_model`` at resolve.

        The rules read token sizes from the study context, so conditions are grouped by their largest
        (brief, original, prompt) sizes and resolved again per group; only these rules' issues are kept
        (the policies themselves were fixed by :meth:`_resolve_policies` and do not depend on them).
        """
        if self.deps.study_policies is None:
            return
        groups: dict[tuple[int | None, int | None, int | None], list[_Condition]] = {}
        for c in conditions:
            mine = [p for (ci, _), p in pairs.items() if ci == c.index]
            key = (
                _largest(p.brief_tokens for p in mine),
                _largest(p.original_tokens for p in mine),
                _largest(p.prompt_tokens for p in mine),
            )
            if key != (None, None, None):
                groups.setdefault(key, []).append(c)
        memory, cpus = self._stack_minimums
        for (brief, original, prompt), group in groups.items():
            resolved = self.deps.study_policies(
                self.study,
                self._condition_inputs(group),
                stack_min_memory=memory,
                stack_min_nano_cpus=cpus,
                brief_tokens=brief,
                slot_original_tokens=original,
                slot_prompt_tokens=prompt,
            )
            for c in group:
                for issue in resolved.by_condition[c.index].issues:
                    if issue.code in _TOKEN_LINT_CODES:
                        self.issues.append(issue.model_copy(update={"cells": [f"c{c.index}"]}))

    def _network_policy(self, condition: _Condition) -> None:
        full = condition.full
        if "network_policy" in full:
            condition.network_policy = full["network_policy"]
        else:
            condition.network_policy = condition.policy.content.network_policy
            condition.levels["network_policy"] = condition.network_policy
            condition.records["network_policy"] = FactorRecord(value=condition.network_policy)

    def _conditions(self) -> tuple[list[_Condition], bool]:
        expansion = expand_grid(self.design, drop_incompatible=self.drop, compatible=self._compatible)
        self.issues += expansion.issues
        self.pruned += expansion.pruned
        varying = [factor.key for factor in self.design.factors if factor.role != "fixed" and len(factor.levels) > 1]
        conditions = []
        for raw in expansion.conditions:
            try:
                conditions.append(self._condition(len(conditions), raw, varying))
            except Unresolved as exc:
                self.issues.append(exc.issue)
        return conditions, expansion.unbalanced

    # -- (condition, unit) pairs -----------------------------------------------------------------

    def _manifest_model(self, model: LLMModel, reasoning: dict[str, Any] | None = None) -> ManifestModel:
        """The frozen model section; ``reasoning`` is the condition's policy ``reasoning`` (it sets the effort
        the native Anthropic route plans to send, recorded in ``endpoint.native``)."""
        endpoint = model.endpoint
        # The local serving settings (context size, residency, dialect, ``think`` capability) are frozen
        # here, so the proxy uses them instead of the live endpoint row; all None for OpenRouter. Recorded,
        # not identity inputs.
        section = endpoint_section(model) or {}
        manifest_endpoint = ManifestEndpoint(
            id=endpoint.pk if endpoint is not None else 0,
            kind=endpoint.kind if endpoint is not None else "openrouter",
            served_model_name=(model.served_model_name or None) if endpoint is not None else None,
            digest=(model.digest or None) if endpoint is not None else None,
            cost_basis=endpoint.cost_basis if endpoint is not None else "billed",
            num_ctx=section.get("num_ctx") or None,
            keep_alive=section.get("keep_alive") or None,
            api_flavor=section.get("api_flavor") or None,
            capabilities=list(section.get("capabilities") or []) if section else None,
        )
        cache_prices: dict[str, Any] = {}
        source = "catalog"
        if endpoint is not None and endpoint.api_flavor == "anthropic":
            # Anthropic's native route caches prompts: the cache-read and cache-write prices are frozen too, so the
            # gateway and proxy budgets reserve and settle at what Anthropic bills (``inference.anthropic_prices``).
            from backend.generation.budget.model_facts import model_facts

            facts = model_facts(model)
            for key in ("cached_input_per_mtok", "cache_write_per_mtok"):
                value = getattr(facts, key)
                if value is not None:
                    cache_prices[key] = round(float(value), 6)
            source = "anthropic_list_price"
            from backend.inference.anthropic_native import planned_settings

            native = planned_settings(model.served_model_name or "", reasoning=reasoning)
            manifest_endpoint = manifest_endpoint.model_copy(update={"native": native})
        pricing = ManifestPricing(
            input_per_mtok=round(float(model.input_price_per_token or 0.0) * 1_000_000, 6),
            output_per_mtok=round(float(model.output_price_per_token or 0.0) * 1_000_000, 6),
            source=source,
            as_of=model.updated_at.isoformat() if model.updated_at else "unknown",
            **cache_prices,
        )
        return ManifestModel(slug=model.model_id, endpoint=manifest_endpoint, pricing=pricing)

    def _sampling(self, condition: _Condition, gateway: Any = None) -> ManifestSampling:
        """The sampling section; ``seed_forwarded`` = the preset forwards the seed, the model accepts one and the
        upstream request of the cell's gateway front/mode can carry it (``budget.gateway_config.seed_carried``:
        a translated Anthropic/Responses front does, a native one does not). Results pair by CRN on it."""
        from backend.generation.budget.gateway_config import seed_carried

        row = condition.rows["sampling_preset"]
        content = SamplingContent.model_validate(row.content)
        forced = {k: v for k, v in content.params.model_dump(mode="json").items() if v is not None}
        if condition.kind == "slot_single_shot" and condition.model.supports_json_mode:
            forced["response_format"] = "json_schema"  # recorded; the single-shot executor reads it (WP-12)
        if content.policy != "explicit":
            forced = {k: v for k, v in forced.items() if k == "response_format"}
        not_sent: list[str] = []
        endpoint = condition.model.endpoint if condition.model.endpoint_id is not None else None
        if endpoint is not None and endpoint.api_flavor == "anthropic":
            # The native Messages route sends no sampling knobs (temperature/top_p/top_k answer 400 on current
            # Claude models; there is no seed): recorded, so the manifest never claims Claude ran at them.
            from backend.inference.anthropic_native import not_sent as anthropic_not_sent

            not_sent = anthropic_not_sent(forced)
        return ManifestSampling(
            ref=row.pinned_ref,
            policy=content.policy,
            not_sent_upstream=not_sent,
            forced_params=forced,
            seed=None,
            seed_forwarded=bool(
                content.forward_seed
                and _supports_seed(condition.model)
                and (gateway is None or seed_carried(gateway.front_protocol, gateway.protocol_mode)),
            ),
        )

    def _environment(self, condition: _Condition, unit: _Unit, brief: str, system: str | None) -> CellEnvironment:
        preset = condition.rows.get("harness_preset")
        stack = condition.rows.get("stack_profile") if unit.target is None else unit.parent.stack_profile
        starter = condition.starters.get(stack.pk) if (stack is not None and unit.target is None) else None
        if unit.target is not None:
            starter = unit.parent.starter
        key = (condition.index, getattr(starter, "pk", None), identity.text_sha(brief), system)
        if key not in self._env_cache:
            request = EnvironmentRequest(
                kind=condition.kind,
                model=condition.model,
                policy=condition.policy,
                harness_preset=preset,
                starter=starter,
                stack=stack,
                sandbox=self.sandbox,
                brief=brief,
                system_prompt=system,
                study_kind=self.kind,
            )
            self._env_cache[key] = self.deps.environment(request)
        return self._env_cache[key]

    def _render(self, condition: _Condition, unit: _Unit, levels: dict[str, Any]) -> list[Any]:
        prompt_set = condition.rows["prompt_set"]
        stimuli = self.deps.render_stimuli(self.design.mode, levels, prompt_set.content)
        return list(stimuli)

    def _stimulus_records(self, stimuli: list[Any]) -> list[StimulusRecord]:
        records = []
        for stimulus in stimuli:
            text = identity.normalize_text(stimulus.text)
            sha = identity.text_sha(text)
            self.texts[sha] = text
            records.append(
                StimulusRecord(
                    role=stimulus.role,
                    template_ref=stimulus.template_ref,
                    sha=sha,
                    blob=sha,
                    chars=len(text),
                    est_tokens=_est_tokens(text),
                    sources=list(stimulus.sources),
                ),
            )
        return records

    def _app_pair(self, pair: _Pair) -> None:
        condition, unit = pair.condition, pair.unit
        version = unit.task_version
        assert version is not None
        stack = condition.rows["stack_profile"]
        starter = self._starter(stack, condition.starter_level or "minimal")
        condition.starters[stack.pk] = starter
        content = version.content or {}
        variant = condition.prose_variant or "standard"
        cell_ref = f"c{condition.index}-u{unit.index}"
        if variant not in (content.get("prose") or {}) or variant not in (version.prose_hashes or {}):
            pair.problems.append(
                make_issue(
                    "lint_prose_variant_missing",
                    self.kind,
                    message=f"{version.ref} has no {variant} prose",
                    cells=[cell_ref],
                    data={"task": version.pinned_ref, "variant": variant},
                ),
            )
        if condition.auth_mode == "spec_defined" and not content.get("auth_flow"):
            pair.problems.append(
                make_issue(
                    "auth_flow_missing",
                    self.kind,
                    message=f"{version.ref} has no auth_flow for auth_mode spec_defined",
                    cells=[cell_ref],
                    data={"task": version.pinned_ref},
                ),
            )
        if pair.problems:
            return
        levels = {
            "task_version": version,
            "prose_variant": variant,
            "stack": stack,
            "starter": starter,
            "style": condition.rows.get("style"),
            "auth_mode": condition.auth_mode,
            "guidance": condition.rows.get("guidance"),
            "harness_preset": condition.rows.get("harness_preset"),
            "network_policy": condition.network_policy,
            "prompt_set": condition.rows["prompt_set"],
            "allowed_bases": None,
        }
        from backend.generation.study.environment import allowed_bases_for

        allowed = allowed_bases_for(stack)
        levels["allowed_bases"] = list(allowed.refs) if allowed is not None else None
        stimuli = self._render(condition, unit, levels)
        brief = next((s.text for s in stimuli if s.role == "brief"), "")
        system = next((s.text for s in stimuli if s.role == "system"), None)
        pair.brief_tokens = _est_tokens(brief) if brief else None
        env = self._environment(condition, unit, brief, system)
        self._env_problems(pair, env, cell_ref)
        if pair.problems:
            return
        pair.stimuli = self._stimulus_records(stimuli)
        task = ManifestTask(
            ref=version.pinned_ref,
            structured_hash=version.structured_hash,
            variant=variant,  # type: ignore[arg-type]
            prose_sha=version.prose_hashes[variant],
        )
        style, guidance, preset = (
            condition.rows.get("style"),
            condition.rows.get("guidance"),
            condition.rows.get(
                "harness_preset",
            ),
        )
        treatment = {
            "stack": stack.content_hash,
            "starter": starter.content_hash if starter is not None else None,
            "style": style.content_hash if style is not None else None,
            "auth_mode": condition.auth_mode,
            "guidance": guidance.content_hash if guidance is not None else None,
            "prompt_set": condition.rows["prompt_set"].content_hash,
            "harness_preset": preset.content_hash if preset is not None else None,
            "gateway": env.gateway_treatment,
            "allowed_bases_sha": env.allowed_bases.sha if env.allowed_bases is not None else None,
            "slot_policy": None,
        }
        starter_section = None
        if starter is not None:
            starter_section = ManifestStarter(
                ref=starter.pinned_ref,
                tree_hash=starter.tree_hash,
                base_images=[
                    ManifestBaseImage(ref=b["ref"], digest=b["digest"])
                    for b in (starter.content or {}).get("base_images") or []
                ],
            )
        pair.identity_inputs = {
            "protocol": "app@1",
            "kind": condition.kind,
            "task": {"structured_hash": task.structured_hash, "variant": variant, "prose_sha": task.prose_sha},
            "target": None,
            "treatment": treatment,
        }
        self._finish_pair(pair, env, task=task, starter=starter_section)

    def _slot_pair(self, pair: _Pair) -> None:
        condition, unit = pair.condition, pair.unit
        target = unit.target
        assert target is not None
        assert condition.slot_policy is not None
        slot_set = target.slot_set
        parent = slot_set.parent_run
        cell_ref = f"c{condition.index}-u{unit.index}"
        style = condition.rows.get("style")
        if style is not None and target.kind != "component":
            pair.problems.append(
                make_issue(
                    "incompatible_style",
                    self.kind,
                    message=f"style applies to component slots only; {target.label} is a {target.kind} slot",
                    cells=[cell_ref],
                ),
            )
            return
        context = self.deps.resolve_slot_context(
            target,
            condition.slot_policy.context_policy,
            hole_mode=condition.slot_policy.hole_mode,
            keep_docstring=condition.slot_policy.keep_docstring,
        )
        pair.slot_context = context
        policy = condition.slot_policy.model_copy(update={"edit_policy": target.edit_policy or "revert"})
        levels = {
            "slot_target": target,
            "slot_policy": policy,
            "resolved_context": context,
            "executor": _KIND_EXECUTOR_LEVEL[condition.kind],
            "guidance": condition.rows.get("guidance"),
            "style": style,
            "harness_preset": condition.rows.get("harness_preset"),
            "prompt_set": condition.rows["prompt_set"],
        }
        stimuli = self._render(condition, unit, levels)
        brief = next((s.text for s in stimuli if s.role == "slot_agent_brief"), "")
        if condition.kind == "slot_single_shot":
            pair.prompt_tokens = sum(_est_tokens(s.text) for s in stimuli) or None
            pair.original_tokens = _original_tokens(target)
        elif brief:
            pair.brief_tokens = _est_tokens(brief)
        env = self._environment(condition, unit, brief, None)
        self._env_problems(pair, env, cell_ref)
        if pair.problems:
            return
        pair.stimuli = self._stimulus_records(stimuli)
        validation = TargetValidation.model_validate(target.validation or {})
        leak = LeakReport.model_validate(target.leak) if target.leak else None
        selection = slot_set.selection or {}
        slot = ManifestSlot(
            slot_set=str(slot_set.pk),
            target=str(target.pk),
            target_hash=target.target_hash,
            parent_run=str(parent.pk),
            parent_tree_hash=slot_set.frozen_tree_hash,
            parent_model=slot_set.parent_model,
            parent_harness=slot_set.parent_harness or "unknown",
            parent_selection=ParentSelectionRecord(rule=selection.get("rule", "manual"), seed=selection.get("seed")),
            address=target.address,
            hole_mode=policy.hole_mode,
            keep_docstring=policy.keep_docstring,
            context_policy=policy.context_policy,
            resolved_context=ManifestResolvedContext(
                files=[ManifestContextFile(path=f.path, sha=f.sha, tokens=f.tokens) for f in context.files],
                sha=context.sha,
            ),
            include_tests=policy.context_policy.include_tests,
            edit_policy=policy.edit_policy,
            allowlist=list(target.allowlist or []),
            leak_score=leak.score if leak is not None else 0.0,
            validation=ManifestSlotValidation(
                discriminating_checks=validation.discriminating_checks,
                dropped_checks=validation.dropped_checks,
            ),
        )
        guidance, preset = condition.rows.get("guidance"), condition.rows.get("harness_preset")
        treatment = {
            "stack": parent.stack_profile.content_hash if parent.stack_profile_id else None,
            "starter": parent.starter.content_hash if parent.starter_id else None,
            "style": style.content_hash if style is not None else None,
            "auth_mode": parent.auth_mode or None,
            "guidance": guidance.content_hash if guidance is not None else None,
            "prompt_set": condition.rows["prompt_set"].content_hash,
            "harness_preset": preset.content_hash if preset is not None else None,
            "gateway": env.gateway_treatment,
            "allowed_bases_sha": None,
            "slot_policy": policy.identity(context.sha),
        }
        pair.identity_inputs = {
            "protocol": "slot@1",
            "kind": condition.kind,
            "task": None,
            "target": {"target_hash": target.target_hash},
            "treatment": treatment,
        }
        self._finish_pair(pair, env, slot=slot)
        self._slot_issues(pair, policy)

    def _slot_issues(self, pair: _Pair, policy: SlotPolicy) -> None:
        """Target validation, leak and slot-sampling issues (WP-12), plus the parent-model overlap."""
        target = pair.unit.target
        assert target is not None
        cell_ref = f"c{pair.condition.index}-u{pair.unit.index}"
        issues = self.deps.slot_issues(
            target,
            context_policy=policy.context_policy,
            executor=_KIND_EXECUTOR_LEVEL[pair.condition.kind],
            hole_mode=policy.hole_mode,
            sampling_content=dict(pair.condition.rows["sampling_preset"].content or {}),
            study_kind=self.kind,
        )
        for issue in issues:
            self.issues.append(issue.model_copy(update={"cells": [cell_ref]}))
        if pair.condition.model.model_id == target.slot_set.parent_model:
            self._issue(
                "variant_model_equals_parent_model",
                message=f"{pair.condition.model.model_id} also wrote the parent of {target.label}",
                cells=[cell_ref],
            )

    def _env_problems(self, pair: _Pair, env: CellEnvironment, cell_ref: str) -> None:
        for issue in env.issues:
            tagged = issue.model_copy(update={"cells": [cell_ref]})
            if issue.severity == "error" and issue.code != "model_lacks_tools":
                pair.problems.append(tagged)
            else:
                self.issues.append(tagged)
        pair.excluded = env.excluded

    def _finish_pair(
        self,
        pair: _Pair,
        env: CellEnvironment,
        *,
        task: ManifestTask | None = None,
        slot: ManifestSlot | None = None,
        starter: ManifestStarter | None = None,
    ) -> None:
        condition = pair.condition
        policy = condition.policy
        assert policy is not None
        reasoning = getattr(getattr(policy, "content", None), "reasoning", None)
        model = self._manifest_model(
            condition.model,
            reasoning=reasoning.model_dump(mode="json") if hasattr(reasoning, "model_dump") else None,
        )
        pair.identity_inputs.update(
            {
                "stimuli": [(record.role, record.sha) for record in pair.stimuli],
                "model": {
                    "slug": condition.model.model_id,
                    "endpoint_kind": model.endpoint.kind,
                    "pinned_digest": model.endpoint.digest,
                },
                "sampling_hash": condition.rows["sampling_preset"].content_hash,
                "policy_treatment_view": condition.treatment_view,
                "images": env.images,
            },
        )
        resources = ManifestResources(
            ref=condition.rows["resource_policy"].pinned_ref,
            generation_policy_hash=identity.H(condition.treatment_view),
            budget_basis=policy.budget_basis,
            effective=condition.policy_dump,
            notes=list(policy.notes),
        )
        evaluation = self.design.evaluation
        pair.manifest = ManifestBody(
            protocol=pair.identity_inputs["protocol"],
            kind=condition.kind,  # type: ignore[arg-type]
            factors=dict(condition.records),
            task=task,
            slot=slot,
            model=model,
            sampling=self._sampling(condition, env.gateway),
            resources=resources,
            gateway=env.gateway,
            harness=env.harness,
            starter=starter,
            allowed_bases=env.allowed_bases,
            stimuli=[ManifestStimulus(role=r.role, template_ref=r.template_ref, sha=r.sha) for r in pair.stimuli],
            identity=ManifestIdentity(**self._keys(pair, 0).as_dict()),
            planned_evaluation=PlannedEvaluation(
                eval_suite=ManifestEvalSuite(ref=self.suite.pinned_ref, content_hash=self.suite.content_hash),
                eval_level=evaluation.eval_level,
                mode=evaluation.slot.mode if (slot is not None and evaluation.slot is not None) else "full",
            ),
            network_policy=condition.network_policy,  # type: ignore[arg-type]
            sandbox=env.sandbox if condition.kind in ("app_agentic", "slot_agent") else None,
        )

    def _keys(self, pair: _Pair, seed: int) -> identity.IdentityKeys:
        return identity.compute_keys(identity.IdentityInputs(**pair.identity_inputs, seed=seed))

    # -- cells -----------------------------------------------------------------------------------

    def _cell(self, pair: _Pair, *, repeat: int, stage: str, block: int, sequence: int) -> ResolvedCell:
        assert pair.manifest is not None
        seed = identity.crn_seed(self.design.base_seed, pair.unit.key, repeat)
        keys = self._keys(pair, seed)
        sampling = pair.manifest.sampling
        manifest = pair.manifest.model_copy(
            update={
                "identity": ManifestIdentity(**keys.as_dict()),
                "sampling": sampling.model_copy(update={"seed": seed}) if sampling is not None else None,
            },
        )
        return ResolvedCell.model_construct(
            cell_key=cell_key(pair.condition.index, pair.unit.index, repeat, stage),
            condition_index=pair.condition.index,
            unit_index=pair.unit.index,
            repeat_index=repeat,
            block_index=block,
            sequence=sequence,
            stage=stage,
            seed=seed,
            unit_key=pair.unit.key,
            levels=dict(pair.condition.levels),
            manifest=manifest,
            stimuli=pair.stimuli,
            effective_policy=pair.condition.policy_dump,
            slot_context=pair.slot_context,
            estimate=pair.estimate,
            excluded=pair.excluded,
            issues=[],
        )

    def _stages(self, policy: ResourcePolicyContent, units: list[_Unit]) -> list[tuple[str, list[int], list[_Unit]]]:
        stages: list[tuple[str, list[int], list[_Unit]]] = []
        pilot = policy.pilot
        if self.include_pilot and pilot.enabled:
            by_order = sorted(units, key=lambda unit: unit.unit_order)
            chosen = by_order if pilot.units == "all" else by_order[: int(pilot.units)]
            stages.append(("pilot", list(range(min(pilot.repeats, self.repeats))), chosen))
        stages.append(("main", list(range(self.repeats)), units))
        return stages

    def _groups(self, conditions: list[_Condition]) -> list[str] | None:
        scheduling = self.design.scheduling
        local = any(condition.model.endpoint_id is not None for condition in conditions)
        if scheduling == "blocked" or (scheduling == "auto" and local):
            return [condition.model.model_id for condition in conditions]
        return None

    def _build_cells(
        self,
        conditions: list[_Condition],
        units: list[_Unit],
        pairs: dict[tuple[int, int], _Pair],
        policy: ResourcePolicyContent,
    ) -> tuple[list[ResolvedCell], int]:
        cells: list[ResolvedCell] = []
        groups = self._groups(conditions)
        block = 0
        sequence = 0
        unit_keys = [unit.key for unit in units]
        for stage, repeats, stage_units in self._stages(policy, units):
            allowed = {unit.index for unit in stage_units}
            for repeat, unit_index in block_sequence(unit_keys, repeats, self.design.order_seed):
                if unit_index not in allowed:
                    continue
                order = condition_order(len(conditions), order_seed=self.design.order_seed, block_index=block,
                                        groups=groups)  # fmt: skip
                for position in order:
                    pair = pairs.get((conditions[position].index, unit_index))
                    if pair is None:
                        continue
                    cells.append(self._cell(pair, repeat=repeat, stage=stage, block=block, sequence=sequence))
                    sequence += 1
                block += 1
        return cells, block

    def _estimates(self, pairs: dict[tuple[int, int], _Pair]) -> None:
        for pair in pairs.values():
            sample = self._cell(pair, repeat=0, stage="main", block=0, sequence=0)
            pair.estimate = self.deps.estimate_cell(sample)

    # -- plan-level issues -----------------------------------------------------------------------

    def _plan_issues(self, conditions: list[_Condition], units: list[_Unit], pairs: dict) -> None:
        presets = {
            c.rows["harness_preset"].pk: c.rows["harness_preset"] for c in conditions if "harness_preset" in c.rows
        }
        harnesses = {p.harness for p in presets.values()}
        if len(harnesses) > 1:
            controlled = {HarnessPresetContent.model_validate(p.content).controlled for p in presets.values()}
            if len(controlled) > 1:
                self._issue(
                    "mixed_harness_profiles",
                    message="harnesses are crossed with a mix of native and controlled presets",
                    data={"presets": sorted(p.pinned_ref for p in presets.values())},
                )
        envs = list(self._env_cache.values())
        if any(not env.agentic for env in envs) and any(env.agentic for env in envs) and self.design.mode == "app":
            self._issue(
                "harness_brief_differs",
                message="an editor harness (aider) is crossed with agentic harnesses: the harness factor is "
                "confounded with the brief",
            )
        if self.design.mode == "app":
            self._oracle_issues(conditions, units)
        else:
            self._parent_issues(units)

    def _oracle_issues(self, conditions: list[_Condition], units: list[_Unit]) -> None:
        stacks = {c.rows["stack_profile"].pk: c.rows["stack_profile"] for c in conditions}
        for unit in units:
            version = unit.task_version
            oracle = version.oracle if version is not None and isinstance(version.oracle, dict) else {}
            for stack in stacks.values():
                entry = next((oracle[k] for k in (stack.pinned_ref, stack.ref, stack.slug) if k in oracle), None)
                status = entry.get("status") if isinstance(entry, dict) else None
                if status != "pass":
                    self._issue(
                        "oracle_missing",
                        message=f"{version.ref} has no passing oracle for {stack.ref} ({status or 'missing'})",
                        data={"task": version.pinned_ref, "stack": stack.pinned_ref, "status": status or "missing"},
                    )
        frameworks = sorted({stack.backend_framework for stack in stacks.values()})
        coverage = self.deps.conformance_coverage()
        for framework in frameworks:
            if coverage is None or not coverage.get(framework):
                self._issue(
                    "conformance_missing",
                    message=f"no passing tester conformance kit for {framework}"
                    + (" (conformance status unavailable)" if coverage is None else ""),
                    data={"framework": framework},
                )

    def _parent_issues(self, units: list[_Unit]) -> None:
        plan = self.design.analysis_plan or {}
        conditional = plan.get("estimand") == "conditional_on_parent"
        severity = "info" if conditional else None
        parents = {unit.target.slot_set.parent_run_id for unit in units if unit.target is not None}
        if len(parents) == 1:
            self._issue(
                "single_parent",
                severity=severity,
                message="every target comes from one parent app: results are conditional on that parent",
            )
        rules = {(unit.target.slot_set.selection or {}).get("rule") for unit in units if unit.target is not None}
        if "manual" in rules:
            self._issue(
                "manual_parent_selection",
                severity=severity,
                message="parents were picked manually: results are conditional on the picked parents",
            )

    # -- run -------------------------------------------------------------------------------------

    def run(self) -> PlanBundle:
        try:
            _, study_policy = self._study_policy()
            self.suite = self._eval_suite()
        except Unresolved as exc:
            raise _NoConditions(exc.issue.message, [exc.issue]) from exc
        self.issues += design_rules.validate_design(self.design, user=self.user, policy=study_policy)
        units = self._units()
        conditions, unbalanced = self._conditions()
        if not conditions:
            msg = "no condition of the design could be resolved (see issues)"
            raise _NoConditions(msg, self.issues)
        stacks = [c.rows["stack_profile"] for c in conditions if "stack_profile" in c.rows]
        stacks += [u.parent.stack_profile for u in units if u.parent is not None and u.parent.stack_profile_id]
        self.sandbox = study_sandbox(study_policy.sandbox.memory, study_policy.sandbox.nano_cpus, stacks)
        self._resolve_policies(conditions, stacks)
        pairs: dict[tuple[int, int], _Pair] = {}
        for condition in conditions:
            for unit in units:
                pair = _Pair(condition=condition, unit=unit)
                if self.design.mode == "app":
                    self._app_pair(pair)
                else:
                    self._slot_pair(pair)
                if pair.problems:
                    if self.drop:
                        self.pruned += 1
                        self._issue(
                            "combination_pruned",
                            message="dropped: " + "; ".join(p.message or p.code for p in pair.problems),
                            cells=[f"c{condition.index}-u{unit.index}"],
                            data={"levels": condition.levels, "unit": unit.key},
                        )
                        unbalanced = True
                    else:
                        self.issues += pair.problems
                    continue
                pairs[(condition.index, unit.index)] = pair
        if self.pruned and self.drop and not any(i.code == "unbalanced_grid" for i in self.issues):
            self._issue("unbalanced_grid", message=f"{self.pruned} combination(s) pruned; by-factor slices use EMMs")
        self._token_lint(conditions, pairs)
        self._plan_issues(conditions, units, pairs)
        self._estimates(pairs)
        cells, n_blocks = self._build_cells(conditions, units, pairs, study_policy)
        if len(cells) > design_rules.MAX_CELLS:
            msg = f"the plan has {len(cells)} cells; the limit is {design_rules.MAX_CELLS}"
            raise design_rules.DesignError(msg, path="repeats")
        prompt_hashes = {cell.cell_key: cell.manifest.identity.prompt_hash for cell in cells}  # type: ignore[union-attr]
        design_hash = identity.design_hash(self.design.model_dump(mode="json"))
        resolved_conditions = [
            ResolvedCondition(
                index=c.index,
                label=c.label,
                condition_hash=identity.condition_hash(c.levels),
                levels=c.levels,
                treatment_hash=next(
                    (
                        self._keys(p, 0).treatment_hash
                        for (ci, _), p in pairs.items()
                        if ci == c.index and p.manifest is not None
                    ),
                    None,
                ),
                is_baseline=_is_baseline(self.design, c),
            )
            for c in conditions
        ]
        resolved_units = [
            ResolvedUnit(
                index=u.index,
                unit_key=u.key,
                task_version=u.task_version.pinned_ref if u.task_version is not None else None,
                slot_target=str(u.target.pk) if u.target is not None else None,
                unit_order=u.unit_order,
            )
            for u in units
        ]
        plan_hash = identity.plan_hash(
            design_hash=design_hash,
            conditions=[c.condition_hash for c in resolved_conditions],
            units=[u.unit_key for u in resolved_units],
            cells=[[c.condition_index, c.unit_index, c.repeat_index, c.stage, c.seed, c.sequence] for c in cells],
            prompt_hashes=prompt_hashes,
        )
        plan = ResolvedPlan.model_construct(
            resolver_version=RESOLVER_VERSION,
            design_hash=design_hash,
            plan_hash=plan_hash,
            conditions=resolved_conditions,
            units=resolved_units,
            cells=cells,
            n_blocks=n_blocks,
            prompt_hashes=prompt_hashes,
            estimate=None,
            issues=[],
        )
        estimate = self.deps.estimate_plan(plan, study_kind=self.kind) if cells else None
        if estimate is not None:
            self.issues += list(estimate.issues)
        plan.estimate = estimate
        plan.issues = _dedupe(self.issues)
        return PlanBundle(plan=plan, texts=dict(self.texts), unbalanced=unbalanced)


class _NoConditions(design_rules.DesignError):
    def __init__(self, message: str, issues: list[Issue]) -> None:
        super().__init__(message, path="factors")
        self.issues = issues


def _is_baseline(design: Design, condition: _Condition) -> bool:
    baseline = (design.analysis_plan or {}).get("baseline")
    if not isinstance(baseline, dict) or not baseline:
        return False
    return all(
        _short(condition.levels.get(k, "")) in (v, _short(v)) or condition.raw.get(k) == v for k, v in baseline.items()
    )


# ---------------------------------------------------------------------------
# Cache, persistence and the public entry points
# ---------------------------------------------------------------------------

_CACHE: OrderedDict[tuple[Any, ...], tuple[float, PlanBundle]] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _cache_key(study: Study, design: Design, drop_incompatible: bool, repeats: int | None) -> tuple[Any, ...]:
    # The pinned parent draw (E2E-14) decides a drawing design's units, so it is part of the key.
    pin = (getattr(study, "parent_draw", None) or {}).get("selection_hash", "")
    pinned = ",".join(str(p) for p in (getattr(study, "parent_draw", None) or {}).get("parents") or [])
    return (
        str(study.pk),
        identity.design_hash(design.model_dump(mode="json")),
        drop_incompatible,
        repeats,
        pin,
        pinned,
    )


def clear_cache() -> None:
    """Drop every cached ephemeral resolve (tests; a library edit the user wants reflected at once)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _drop_cached(study: Study) -> None:
    with _CACHE_LOCK:
        for key in [k for k in _CACHE if k[0] == str(study.pk)]:
            del _CACHE[key]


def cached_bundle(study: Study, plan_hash: str) -> PlanBundle | None:
    """An unexpired cached bundle of ``study`` with ``plan_hash`` (ephemeral plans in the cell drawer)."""
    now = time.monotonic()
    with _CACHE_LOCK:
        for key, (at, bundle) in list(_CACHE.items()):
            if now - at > CACHE_TTL_S:
                del _CACHE[key]
                continue
            if key[0] == str(study.pk) and bundle.plan.plan_hash == plan_hash:
                return bundle
    return None


def resolve_bundle(
    study: Study,
    *,
    persist: bool = False,
    drop_incompatible: bool = False,
    repeats: int | None = None,
    use_cache: bool = True,
) -> PlanBundle:
    """Resolve ``study.design`` (see the module docstring); ``persist`` stores the plan (freeze / launch)."""
    design = design_rules.parse_design(study.design)
    if persist and study_parents.uses_draw(design):
        # Freeze the drawn parents and match the templates first (ephemeral resolves never write rows).
        if study_parents.materialize(design, user=study.created_by, study=study).created:
            _drop_cached(study)
    key = _cache_key(study, design, drop_incompatible, repeats)
    bundle: PlanBundle | None = None
    if use_cache:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit is not None and time.monotonic() - hit[0] <= CACHE_TTL_S:
                bundle = hit[1]
    if bundle is None:
        bundle = _Resolver(study, design, drop_incompatible=drop_incompatible, repeats=repeats).run()
        with _CACHE_LOCK:
            _CACHE[key] = (time.monotonic(), bundle)
            _CACHE.move_to_end(key)
            while len(_CACHE) > _CACHE_MAX:
                _CACHE.popitem(last=False)
    if persist:
        return persist_bundle(study, bundle)
    return bundle


def resolve(
    study: Study,
    *,
    persist: bool = False,
    drop_incompatible: bool = False,
    sample_cells: int = 50,
) -> ResolvedPlan:
    """Resolve ``study.design``; ``persist=False`` writes no rows (cached in-process by design_hash for 10 minutes)."""
    del sample_cells  # the API samples cells from the full plan
    return resolve_bundle(study, persist=persist, drop_incompatible=drop_incompatible).plan


def persist_bundle(study: Study, bundle: PlanBundle) -> PlanBundle:
    """Store the payload and stimulus texts and create (or find) the immutable ``StudyPlan`` row."""
    from backend.generation import blobs
    from backend.generation.models import StudyPlan

    plan = bundle.plan
    existing = StudyPlan.objects.filter(study=study, plan_hash=plan.plan_hash).first()
    if existing is not None:
        if not _stale_draft(study, existing, plan):
            return PlanBundle(plan=plan, texts=bundle.texts, row=existing, unbalanced=bundle.unbalanced)
        # E2E-21: same plan, newer diagnostics (a target validated since, fresher priors): replace the draft.
        existing.delete()
    for text in bundle.texts.values():
        blobs.put_text(text)
    payload_sha = blobs.put_json(plan.model_dump(mode="json"))
    try:
        with transaction.atomic():
            row = StudyPlan.objects.create(
                study=study,
                plan_hash=plan.plan_hash,
                design_hash=plan.design_hash,
                payload_blob=payload_sha,
                n_conditions=len(plan.conditions),
                n_units=len(plan.units),
                n_cells=len(plan.cells),
                n_blocks=plan.n_blocks,
                estimate=plan.estimate.model_dump(mode="json") if plan.estimate is not None else None,
                issues=[issue.model_dump(mode="json") for issue in plan.issues],
                resolver_version=plan.resolver_version,
                created_by=study.created_by,
            )
    except IntegrityError:
        # A concurrent freeze of the same study created it first (unique (study, plan_hash)).
        row = StudyPlan.objects.get(study=study, plan_hash=plan.plan_hash)
    _mirror_factors(study)
    return PlanBundle(plan=plan, texts=bundle.texts, row=row, unbalanced=bundle.unbalanced)


def _stale_draft(study: Study, row: StudyPlan, plan: ResolvedPlan) -> bool:
    """A never-launched plan row whose stored issues or estimate differ from a fresh resolve of the same hash.

    The plan hash covers what runs (design, conditions, units, cells, prompts), not the diagnostics: a slot
    target's validation (``slot_tests_uninformative`` "validation is pending") or the estimate's priors change
    without changing the hash. Since the parent draw is pinned (E2E-14) a re-freeze after target validation
    yields the same hash, so the row frozen first would keep its blocking issues for ever. Such a row is
    replaced, but only while nothing refers to it (not launched, not the active plan, no materialized rows).
    """
    from backend.generation.models.choices import PlanStatus

    if row.status == PlanStatus.LAUNCHED or study.active_plan_id == row.pk:
        return False
    if row.conditions.exists() or row.units.exists() or row.cells.exists():
        return False
    issues = [issue.model_dump(mode="json") for issue in plan.issues]
    estimate = plan.estimate.model_dump(mode="json") if plan.estimate is not None else None
    return row.issues != issues or row.estimate != estimate


def _mirror_factors(study: Study) -> None:
    """``StudyFactor`` rows mirror the design's factors (for querying)."""
    from backend.generation.models import StudyFactor

    design = design_rules.parse_design(study.design)
    StudyFactor.objects.filter(study=study).delete()
    StudyFactor.objects.bulk_create(
        [
            StudyFactor(study=study, key=f.key, role=f.role, levels=list(f.levels), position=position)
            for position, f in enumerate(design.factors)
        ],
    )


def load_bundle(row: StudyPlan) -> PlanBundle:
    """The persisted plan of ``row`` (payload blob; stimulus texts load on demand through ``blobs.get_text``)."""
    from backend.generation import blobs

    payload = blobs.get_json(row.payload_blob)
    return PlanBundle(plan=ResolvedPlan.model_validate(payload), row=row)


def freeze(study: Study, *, drop_incompatible: bool = False, redraw_parents: bool = False) -> StudyPlan:
    """Persist the resolved plan as a draft ``StudyPlan`` (older draft plans of the study are superseded).

    A drawing design reuses the parents pinned at its first freeze; ``redraw_parents`` drops that pin first,
    so the parents are drawn again from the current pool (refused once the study has launched or is locked).
    """
    from backend.generation.models import StudyPlan
    from backend.generation.models.choices import PlanStatus
    from backend.generation.models.choices import StudyStatus

    if redraw_parents:
        if study.locked_at is not None or study.status not in (StudyStatus.DRAFT, StudyStatus.PLANNED):
            msg = "the parents of a launched or locked study cannot be redrawn"
            raise design_rules.DesignError(msg, path="parent_selection")
        if study_parents.forget_draw(study):
            _drop_cached(study)
    # A freeze resolves afresh (E2E-21): the ephemeral cache does not see target validation finishing.
    bundle = resolve_bundle(study, persist=True, drop_incompatible=drop_incompatible, use_cache=False)
    assert bundle.row is not None
    StudyPlan.objects.filter(study=study, status=PlanStatus.DRAFT).exclude(pk=bundle.row.pk).update(
        status=PlanStatus.SUPERSEDED,
    )
    if study.status == StudyStatus.DRAFT:
        study.status = StudyStatus.PLANNED
        study.save(update_fields=["status", "updated_at"])
    return bundle.row
