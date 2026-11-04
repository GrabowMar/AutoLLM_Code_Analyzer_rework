"""Study models (spec section 3.6): the factorial design, its frozen plans and its cells.

``Study`` keeps the v2 study table's identity (``RenameModel``, ``db_table="generation_study"``), so the
foreign keys of other apps keep working. ``StudyCondition``, ``StudySchedule`` and ``StudyPostRun`` are
new tables (the v2 rows are wiped before the cut-over, section 3.12).

Deletion inside a study tree: the children (factors, plans, conditions, units, cells, prompt overrides,
schedules and post-runs) cascade from the study, while the plan, condition and unit references between
them are ``RESTRICT``. A plan cannot be deleted on its own while conditions, units or cells refer to it,
but deleting the whole study works.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.core.validators import MinValueValidator
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from backend.generation import identity
from backend.generation.contracts._base import PinnedRefStr
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.library import AUTH_MODES
from backend.generation.contracts.library import OVERRIDE_SWAPS
from backend.generation.contracts.library import auth_fragment_key
from backend.generation.contracts.plan import FACTOR_KEYS
from backend.generation.contracts.plan import MAX_REPEATS
from backend.generation.contracts.plan import Design
from backend.generation.contracts.plan import Estimate
from backend.generation.contracts.plan import ExecutionOptions
from backend.generation.contracts.plan import FactorSpec
from backend.generation.contracts.plan import ResolvedCondition
from backend.generation.contracts.plan import StopReason
from backend.generation.models.base import REF_LENGTH
from backend.generation.models.base import Contract
from backend.generation.models.base import Errors
from backend.generation.models.base import FrozenFieldsQuerySet
from backend.generation.models.base import ImmutableVersionError
from backend.generation.models.base import ValidatedModel
from backend.generation.models.base import field_names
from backend.generation.models.base import fill_or_check
from backend.generation.models.base import sha_field
from backend.generation.models.choices import BudgetBasis
from backend.generation.models.choices import CapBasis
from backend.generation.models.choices import CellExclusionReason
from backend.generation.models.choices import CellState
from backend.generation.models.choices import FactorRole
from backend.generation.models.choices import Mode
from backend.generation.models.choices import OnBudgetExhausted
from backend.generation.models.choices import OverrideOwnerKind
from backend.generation.models.choices import PlanStatus
from backend.generation.models.choices import PostRunKind
from backend.generation.models.choices import PostRunTrigger
from backend.generation.models.choices import ScheduleAction
from backend.generation.models.choices import Scheduling
from backend.generation.models.choices import Stage
from backend.generation.models.choices import StudyKind
from backend.generation.models.choices import StudyStatus
from backend.generation.models.choices import WorkStatus

__all__ = [
    "Study",
    "StudyCell",
    "StudyCondition",
    "StudyFactor",
    "StudyPlan",
    "StudyPostRun",
    "StudyPromptOverride",
    "StudySchedule",
    "StudyUnit",
]

pause_reason_validator = RegexValidator(
    r"^(budget|quota|spend_cap|manual|infra|rule:[a-z0-9_]+)$",
    _("Use budget, quota, spend_cap, manual, infra or rule:<code>."),
    code="invalid_pause_reason",
)
_ZERO = Decimal(0)
FACTOR_KEY_CHOICES = [(key, key) for key in FACTOR_KEYS]
_PINNED_REF = Contract(PinnedRefStr)


def _money(verbose_name: str, *, digits: int) -> models.DecimalField:
    return models.DecimalField(verbose_name, max_digits=digits, decimal_places=6, default=_ZERO)


# Study columns mirrored from the design: the design is the single source of these values; the columns
# exist for queries and the scheduler.
DESIGN_MIRROR: tuple[str, ...] = ("kind", "mode", "repeats", "base_seed", "order_seed", "scheduling")


class StudyQuerySet(models.QuerySet):
    """Refuses ``update()`` of ``Study.LOCKED_FIELDS`` when any matched study is locked (the bulk bypass)."""

    def update(self, **kwargs: Any) -> int:
        frozen = sorted(field_names(self.model, kwargs) & set(Study.LOCKED_FIELDS))
        if frozen:
            locked = list(self.filter(locked_at__isnull=False).values_list("pk", flat=True)[:5])
            if locked:
                msg = (
                    f"studies {', '.join(map(str, locked))} are locked (confirmatory launch); "
                    f"refused bulk update of {', '.join(frozen)}"
                )
                raise ImmutableVersionError(msg)
        return super().update(**kwargs)


class Study(ValidatedModel):
    """A factorial design over factors x levels, units and replicates."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {
        "design": Contract(Design),
        "execution_options": Contract(ExecutionOptions),
        "stop_reason": Contract(StopReason),
    }
    DERIVED_FROM: ClassVar[dict[str, tuple[str, ...]]] = {"design": ("design_hash", *DESIGN_MIRROR)}
    # Immutable once a confirmatory study is locked at launch (section 3.6). The lock itself, the hashes
    # it certifies, the active plan and the design mirrors are frozen too, so the lock cannot be removed
    # (save with ``locked_at=None``) and then the design edited, and a bulk ``update()`` cannot bypass it.
    LOCKED_FIELDS: ClassVar[tuple[str, ...]] = (
        "design",
        "design_hash",
        *DESIGN_MIRROR,
        "analysis_plan",
        "analysis_plan_hash",
        "prereg_hash",
        "resource_policy",
        "locked_at",
        "active_plan",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=200)
    description = models.TextField(_("description"), blank=True, default="")
    hypothesis = models.TextField(_("hypothesis"), blank=True, default="")
    kind = models.CharField(_("kind"), max_length=14, choices=StudyKind.choices)
    mode = models.CharField(_("mode"), max_length=6, choices=Mode.choices)
    status = models.CharField(_("status"), max_length=12, choices=StudyStatus.choices, default=StudyStatus.DRAFT)
    design = models.JSONField(_("design"), help_text="contracts.plan.Design: the editable draft")
    design_hash = sha_field(_("design hash"), help_text='H(Design.model_dump(mode="json"))')
    resource_policy = models.ForeignKey(
        "generation.ResourcePolicy",
        on_delete=models.PROTECT,
        related_name="studies",
        help_text="The study-level budget preset",
    )
    base_seed = models.PositiveBigIntegerField(_("base seed"))
    repeats = models.PositiveSmallIntegerField(
        _("repeats"),
        validators=[MinValueValidator(1), MaxValueValidator(MAX_REPEATS)],
    )
    scheduling = models.CharField(
        _("scheduling"),
        max_length=12,
        choices=Scheduling.choices,
        default=Scheduling.AUTO,
    )
    order_seed = models.PositiveBigIntegerField(_("order seed"))
    priority = models.SmallIntegerField(_("priority"), default=0)
    max_parallel = models.PositiveSmallIntegerField(
        _("max parallel runs"),
        null=True,
        blank=True,
        help_text="Filled from concurrency.study_max_parallel at launch; read by the scheduler",
    )
    execution_options = models.JSONField(
        _("execution options"),
        default=dict,
        blank=True,
        help_text="Filled from the effective policy at launch (contracts.plan.ExecutionOptions)",
    )
    budget_basis = models.CharField(
        _("budget basis"),
        max_length=6,
        choices=BudgetBasis.choices,
        default=BudgetBasis.USD,
    )
    cap_basis = models.CharField(_("cap basis"), max_length=6, choices=CapBasis.choices, default=CapBasis.BILLED)
    on_budget_exhausted = models.CharField(
        _("on budget exhausted"),
        max_length=14,
        choices=OnBudgetExhausted.choices,
        default=OnBudgetExhausted.STOP_BALANCED,
    )
    analysis_plan = models.JSONField(_("analysis plan"), default=dict, blank=True)
    analysis_plan_hash = sha_field(_("analysis plan hash"), blank=True)
    prereg_hash = sha_field(_("pre-registration hash"), blank=True)
    locked_at = models.DateTimeField(
        _("locked at"),
        null=True,
        blank=True,
        help_text="Confirmatory launch; design, analysis plan and budget preset are immutable afterwards",
    )
    active_plan = models.ForeignKey(
        "generation.StudyPlan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # E2E-14: the parents a ``parent_selection`` draw picked at the first freeze, pinned so a later resolve or
    # re-freeze reuses them instead of redrawing from a pool that grows ({"selection_hash", "seed",
    # "pool_size", "parents": [run ids in draw order], "at"}; ``{}`` = not drawn yet). Only an explicit
    # ``freeze-plan`` with ``redraw_parents`` (or a changed ``parent_selection``) draws again.
    parent_draw = models.JSONField(_("pinned parent draw"), default=dict, blank=True)
    stop_reason = models.JSONField(_("stop reason"), null=True, blank=True)
    pause_reason = models.CharField(
        _("pause reason"),
        max_length=40,
        blank=True,
        default="",
        validators=[pause_reason_validator],
    )
    spent_usd = _money(_("spent (USD)"), digits=12)
    spent_list_usd = _money(_("spent (list USD)"), digits=12)
    reserved_usd = _money(_("reserved (USD)"), digits=12)
    spent_tokens = models.BigIntegerField(_("spent tokens"), default=0)
    reserved_tokens = models.BigIntegerField(_("reserved tokens"), default=0)
    # WP-10: the study cap resolved at launch (the policy's ``study.max_cost_usd``, ``auto`` resolved from the
    # estimate, or the launch request's ``study_cap``); NULL = no USD cap. Read by the scheduler (WP-11); may
    # be raised later ("raise cap") without touching the locked design.
    cap_usd = models.DecimalField(
        _("study cap (USD)"),
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Resolved at launch from the budget preset (auto -> estimate) or the launch request",
    )
    cap_tokens = models.BigIntegerField(
        _("study cap (tokens)"),
        null=True,
        blank=True,
        help_text="The budget preset's study.max_total_tokens at launch; NULL = no token cap",
    )
    cloned_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clones",
    )
    launched_at = models.DateTimeField(_("launched at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="studies",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = StudyQuerySet.as_manager()

    class Meta:
        db_table = "generation_study"
        verbose_name = _("Study")
        verbose_name_plural = _("Studies")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_by", "-created_at"], name="study_owner_created"),
            models.Index(fields=["status"], name="study_status"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(repeats__gte=1) & Q(repeats__lte=MAX_REPEATS),
                name="generation_study_repeats_range",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            stored = self.stored_values("locked_at")
            if stored is not None and stored["locked_at"] is not None:
                self.refuse_changes(
                    self.LOCKED_FIELDS,
                    "the study is locked (confirmatory launch)",
                    update_fields=kwargs.get("update_fields"),
                )
        super().save(*args, **kwargs)

    @property
    def is_locked(self) -> bool:
        return self.locked_at is not None

    def derive_fields(self) -> Errors:
        errors = super().derive_fields()
        design = Contract(Design).try_validate(self.design)
        if design is None:
            return errors  # contract_errors reports the design
        self.design = design.model_dump(mode="json")
        self.design_hash = identity.design_hash(self.design)
        for name in DESIGN_MIRROR:
            setattr(self, name, getattr(design, name))
        return errors

    def clean(self) -> None:
        super().clean()
        design = Contract(Design).try_validate(self.design)
        if design is None or self.resource_policy_id is None:
            return
        policy = self.resource_policy
        if design.resource_policy not in {policy.ref, policy.pinned_ref}:
            raise ValidationError(
                {"resource_policy": _("design.resource_policy %(ref)s is not %(policy)s.")},
                params={"ref": design.resource_policy, "policy": policy.pinned_ref},
            )


class StudyFactor(ValidatedModel):
    """One factor of the design, mirrored from ``design`` at resolve time for querying."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {"levels": Contract(FactorSpec.model_fields["levels"].annotation)}

    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="factors")
    key = models.CharField(_("factor"), max_length=32, choices=FACTOR_KEY_CHOICES)
    role = models.CharField(_("role"), max_length=8, choices=FactorRole.choices)
    levels = models.JSONField(_("levels"), help_text="[pinned ref or enum value]")
    position = models.SmallIntegerField(_("position"), default=0)

    class Meta:
        verbose_name = _("Study factor")
        verbose_name_plural = _("Study factors")
        ordering = ["study", "position"]
        constraints = [models.UniqueConstraint(fields=["study", "key"], name="generation_studyfactor_study_key_uniq")]

    def __str__(self) -> str:
        return f"{self.key} ({self.role})"


def _plan_stimulus_blobs(payload: Any) -> Iterator[str]:
    cells = payload.get("cells") if isinstance(payload, dict) else None
    for cell in cells if isinstance(cells, list) else ():
        stimuli = cell.get("stimuli") if isinstance(cell, dict) else None
        for record in stimuli if isinstance(stimuli, list) else ():
            blob = record.get("blob") if isinstance(record, dict) else None
            if isinstance(blob, str):
                yield blob


class StudyPlan(ValidatedModel):
    """An immutable, hashed resolution of a design (created only by freeze-plan or launch)."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {
        "estimate": Contract(Estimate),
        "issues": Contract(Issue, many=True),
    }
    BLOB_FIELDS: ClassVar[tuple[str, ...]] = ("payload_blob",)
    IMMUTABLE_FIELDS: ClassVar[tuple[str, ...]] = (
        "study",
        "plan_hash",
        "design_hash",
        "payload_blob",
        "n_conditions",
        "n_units",
        "n_cells",
        "n_blocks",
        "estimate",
        "issues",
        "resolver_version",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="plans")
    plan_hash = sha_field(_("plan hash"), db_index=True)
    design_hash = sha_field(_("design hash"))
    status = models.CharField(_("status"), max_length=12, choices=PlanStatus.choices, default=PlanStatus.DRAFT)
    payload_blob = sha_field(_("payload blob"), help_text="The full ResolvedPlan JSON in the blob store")
    n_conditions = models.PositiveIntegerField(_("conditions"))
    n_units = models.PositiveIntegerField(_("units"))
    n_cells = models.PositiveIntegerField(_("cells"))
    n_blocks = models.PositiveIntegerField(_("blocks"))
    estimate = models.JSONField(_("estimate"), null=True, blank=True)
    issues = models.JSONField(_("issues"), default=list, blank=True)
    resolver_version = models.SmallIntegerField(_("resolver version"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = FrozenFieldsQuerySet.as_manager()

    class Meta:
        verbose_name = _("Study plan")
        verbose_name_plural = _("Study plans")
        ordering = ["-created_at"]
        # Unique per study, not globally: ``plan_hash`` does not include the study, so an exact clone (an
        # exact replication) freezes the same plan hash under its own study (WP-10 ask).
        constraints = [
            models.UniqueConstraint(fields=["study", "plan_hash"], name="generation_studyplan_study_plan_hash_uniq"),
        ]
        indexes = [models.Index(fields=["study", "status", "-created_at"], name="studyplan_study_status")]

    def __str__(self) -> str:
        return f"plan {self.plan_hash[:12]} ({self.status})"

    @classmethod
    def iter_blob_refs(cls) -> Iterator[str]:
        """The stimulus texts inside every plan payload (``ResolvedPlan.cells[*].stimuli[*].blob``).

        A launched plan's stimuli are also in its runs' manifests; a frozen plan that has not launched
        references them only here. Every plan is scanned, since its payload blob is kept too.
        """
        from backend.generation import blobs

        for sha in cls._base_manager.values_list("payload_blob", flat=True).distinct().iterator():
            if sha:
                yield from blobs.nested_blob_refs(sha, _plan_stimulus_blobs)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            self.refuse_changes(
                self.IMMUTABLE_FIELDS,
                "plans are immutable (only the status changes)",
                update_fields=kwargs.get("update_fields"),
            )
        super().save(*args, **kwargs)


class StudyCondition(ValidatedModel):
    """One combination of treatment levels in a plan (identity columns frozen; ``paused`` stays mutable)."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {
        "levels": Contract(ResolvedCondition.model_fields["levels"].annotation),
    }
    IMMUTABLE_FIELDS: ClassVar[tuple[str, ...]] = (
        "study",
        "plan",
        "index",
        "label",
        "condition_hash",
        "levels",
        "model",
        "harness_preset",
        "treatment_hash",
        "is_baseline",
    )

    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="conditions")
    plan = models.ForeignKey(StudyPlan, on_delete=models.RESTRICT, related_name="conditions")
    index = models.SmallIntegerField(_("index"))
    label = models.CharField(_("label"), max_length=200)
    condition_hash = sha_field(_("condition hash"))
    levels = models.JSONField(_("levels"), help_text="{factor_key: pinned ref or enum value}")
    model = models.ForeignKey("llm_models.LLMModel", on_delete=models.PROTECT, related_name="study_conditions")
    harness_preset = models.ForeignKey(
        "generation.HarnessPreset",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="study_conditions",
    )
    treatment_hash = sha_field(_("treatment hash"))
    is_baseline = models.BooleanField(_("baseline"), default=False)
    paused = models.BooleanField(_("paused"), default=False, help_text="Set by stop rules")
    pause_reason = models.CharField(_("pause reason"), max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = FrozenFieldsQuerySet.as_manager()

    class Meta:
        verbose_name = _("Study condition")
        verbose_name_plural = _("Study conditions")
        ordering = ["plan", "index"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "index"], name="generation_studycondition_plan_index_uniq"),
        ]
        indexes = [models.Index(fields=["study", "condition_hash"], name="studycond_study_hash")]

    def __str__(self) -> str:
        return self.label or f"condition {self.index}"

    def derive_fields(self) -> Errors:
        errors = super().derive_fields()
        levels = self.CONTRACT_FIELDS["levels"].try_validate(self.levels)
        if levels is not None:
            fill_or_check(self, "condition_hash", identity.condition_hash(levels), errors)
        return errors

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            self.refuse_changes(
                self.IMMUTABLE_FIELDS,
                "plan conditions are frozen",
                update_fields=kwargs.get("update_fields"),
            )
        super().save(*args, **kwargs)


class StudyUnit(ValidatedModel):
    """The blocking unit of a plan: a task version (app mode) or a slot target (slot mode). Frozen."""

    IMMUTABLE_FIELDS: ClassVar[tuple[str, ...]] = (
        "study",
        "plan",
        "index",
        "unit_key",
        "task_version",
        "slot_target",
        "unit_order",
    )

    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="units")
    plan = models.ForeignKey(StudyPlan, on_delete=models.RESTRICT, related_name="units")
    index = models.SmallIntegerField(_("index"))
    unit_key = models.CharField(_("unit key"), max_length=160, help_text="task:<slug>@<v> or slot:<target uuid>")
    task_version = models.ForeignKey(
        "generation.TaskSpecVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="study_units",
    )
    slot_target = models.ForeignKey(
        "generation.SlotTarget",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="study_units",
    )
    unit_order = models.PositiveIntegerField(_("unit order"), help_text="Seeded order used by the block sequence")

    objects = FrozenFieldsQuerySet.as_manager()

    class Meta:
        verbose_name = _("Study unit")
        verbose_name_plural = _("Study units")
        ordering = ["plan", "index"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "unit_key"], name="generation_studyunit_plan_unit_key_uniq"),
            models.CheckConstraint(
                condition=(
                    (Q(unit_key__startswith="task:") & Q(task_version__isnull=False) & Q(slot_target__isnull=True))
                    | (Q(unit_key__startswith="slot:") & Q(slot_target__isnull=False) & Q(task_version__isnull=True))
                ),
                name="generation_studyunit_one_unit",
            ),
        ]

    def __str__(self) -> str:
        return self.unit_key

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            self.refuse_changes(
                self.IMMUTABLE_FIELDS,
                "plan units are frozen",
                update_fields=kwargs.get("update_fields"),
            )
        super().save(*args, **kwargs)


class StudyCell(ValidatedModel):
    """``(condition, unit, stage, repeat_index)``: one planned sample, which may have several runs.

    ``stage`` is part of the key (a deviation from section 3.6, see CHANGES): pilot repeats are numbered
    from 0 like main repeats, so pilot r=0 and main r=0 of the same condition x unit are two cells. The
    stage stays mutable: ``pilot.reuse_as_main`` flips a pilot cell to ``main`` in place. The expand step
    (WP-10) must then not also create the main r=0 cell it replaces (it skips the repeat indices the reused
    pilot cells take); otherwise the flip collides with the existing main row and raises IntegrityError.

    The plan identity columns are frozen (``IMMUTABLE_FIELDS``); ``stage``, ``state``, ``exclusion_reason``,
    ``current_run``, the money columns, ``adaptive_n`` and ``beyond_plan`` stay mutable.
    """

    IMMUTABLE_FIELDS: ClassVar[tuple[str, ...]] = (
        "study",
        "plan",
        "condition",
        "unit",
        "repeat_index",
        "block_index",
        "seed",
        "sequence",
    )

    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="cells")
    plan = models.ForeignKey(StudyPlan, on_delete=models.RESTRICT, related_name="cells")
    condition = models.ForeignKey(StudyCondition, on_delete=models.RESTRICT, related_name="cells")
    unit = models.ForeignKey(StudyUnit, on_delete=models.RESTRICT, related_name="cells")
    repeat_index = models.PositiveSmallIntegerField(_("repeat index"), help_text="0-based")
    block_index = models.PositiveIntegerField(_("block index"), help_text="Position of (repeat, unit) in block order")
    stage = models.CharField(_("stage"), max_length=6, choices=Stage.choices, default=Stage.MAIN)
    seed = models.PositiveBigIntegerField(_("seed"))
    sequence = models.PositiveIntegerField(_("sequence"), help_text="Claim order")
    current_run = models.ForeignKey(
        "generation.GenerationRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The first usable attempt, else the latest (runs.queries.refresh_current)",
    )
    state = models.CharField(_("state"), max_length=14, choices=CellState.choices, default=CellState.PLANNED)
    exclusion_reason = models.CharField(
        _("exclusion reason"),
        max_length=24,
        choices=CellExclusionReason.choices,
        blank=True,
        default="",
    )
    estimate_usd = _money(_("estimate (USD)"), digits=10)
    # The cell's expected list-price cost: what a ``cap_basis: list`` study's gates and stop rules compare
    # the runs' list cost with (``estimate_usd`` is billed, 0 on subscription and local endpoints).
    estimate_list_usd = _money(_("estimate (list USD)"), digits=10)
    cap_usd = _money(_("per-attempt cap (USD)"), digits=10)
    cell_cap_usd = _money(_("cell cap (USD)"), digits=10)
    spent_usd = _money(_("spent (USD)"), digits=10)
    spent_list_usd = _money(_("spent (list USD)"), digits=10)
    adaptive_n = models.BooleanField(
        _("adaptive n"),
        default=False,
        help_text="This condition x unit was stopped by a precision rule",
    )
    beyond_plan = models.BooleanField(
        _("beyond plan"),
        default=False,
        help_text="A replicate added after launch (add_replicates): not part of the planned sample",
    )

    objects = FrozenFieldsQuerySet.as_manager()

    class Meta:
        verbose_name = _("Study cell")
        verbose_name_plural = _("Study cells")
        ordering = ["study", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["study", "condition", "unit", "stage", "repeat_index"],
                name="generation_studycell_cell_uniq",
            ),
            models.CheckConstraint(
                condition=Q(state=CellState.EXCLUDED) | Q(exclusion_reason=""),
                name="generation_studycell_reason_only_when_excluded",
            ),
        ]
        indexes = [
            models.Index(fields=["study", "stage"], name="studycell_stage"),
            models.Index(fields=["study", "sequence"], name="studycell_sequence"),
            models.Index(fields=["study", "block_index"], name="studycell_block"),
        ]

    def __str__(self) -> str:
        return f"cell c{self.condition_id}/u{self.unit_id}/r{self.repeat_index}/{self.stage}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            self.refuse_changes(
                self.IMMUTABLE_FIELDS,
                "plan cells are frozen",
                update_fields=kwargs.get("update_fields"),
            )
        super().save(*args, **kwargs)


class StudyPromptOverride(ValidatedModel):
    """An inline text edit from the builder or the cell drawer.

    The edit itself is saved as a new user-owned version of the entity that owns the text (with
    ``derived_from`` = the base), and the matching factor level is swapped to it; this row records it.
    ``contracts.library.OVERRIDE_SWAPS`` names the forks and the swapped factor per owner kind. For
    ``auth_fragment`` the owner refs name the fragment PromptTemplate, ``role`` holds the auth mode (the
    prompt-set key is ``auth_contract.<role>``), and the swapped level is the forked ``prompt_set``.
    """

    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="prompt_overrides")
    owner_kind = models.CharField(_("owner kind"), max_length=16, choices=OverrideOwnerKind.choices)
    owner_ref_base = models.CharField(_("base (pinned ref)"), max_length=REF_LENGTH)
    owner_ref_new = models.CharField(_("new (pinned ref)"), max_length=REF_LENGTH)
    role = models.CharField(_("role"), max_length=24, blank=True, default="")
    diff = models.TextField(_("diff"), blank=True, default="", help_text="Unified diff of the template text")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Study prompt override")
        verbose_name_plural = _("Study prompt overrides")
        ordering = ["study", "created_at"]

    def __str__(self) -> str:
        return f"{self.owner_kind}: {self.owner_ref_base} -> {self.owner_ref_new}"

    @property
    def swapped_factor(self) -> str:
        """The design factor whose level this override swaps (``OVERRIDE_SWAPS``)."""
        return OVERRIDE_SWAPS[self.owner_kind][1]

    @property
    def fragment_key(self) -> str | None:
        """The ``PromptSetContent.fragments`` key an ``auth_fragment`` override edits (else ``None``)."""
        if self.owner_kind != OverrideOwnerKind.AUTH_FRAGMENT:
            return None
        return auth_fragment_key(self.role)

    def derive_fields(self) -> Errors:
        errors = super().derive_fields()
        for name in ("owner_ref_base", "owner_ref_new"):
            if _PINNED_REF.try_validate(getattr(self, name)) is None:
                errors.setdefault(name, []).append(f"{name} must be a pinned ref (slug@v#hash12)")
        if self.owner_ref_base and self.owner_ref_base == self.owner_ref_new:
            errors.setdefault("owner_ref_new", []).append("an override must name a new version")
        if self.owner_kind == OverrideOwnerKind.AUTH_FRAGMENT and self.role not in AUTH_MODES:
            errors.setdefault("role", []).append(f"an auth_fragment override names its auth mode: {AUTH_MODES}")
        return errors


class StudySchedule(models.Model):
    """A cron trigger on a study: top up, add replicates, or clone and run."""

    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="schedules")
    action = models.CharField(_("action"), max_length=16, choices=ScheduleAction.choices)
    # croniter-valid, min interval SCHEDULE_MIN_INTERVAL_S
    cron = models.CharField(_("cron"), max_length=120)
    timezone = models.CharField(_("timezone"), max_length=64, default="UTC")
    # add_replicates {"replicates": 1..5}; clone_and_run {"name_template": "{name} {date}"}
    params = models.JSONField(_("params"), default=dict, blank=True)
    skip_if_active = models.BooleanField(_("skip if active"), default=True)
    enabled = models.BooleanField(_("enabled"), default=True)
    next_run_at = models.DateTimeField(_("next run at"), null=True, blank=True)
    last_run_at = models.DateTimeField(_("last run at"), null=True, blank=True)
    # {outcome: fired|skipped|error, study_id?, runs_created?, detail}
    last_result = models.JSONField(_("last result"), default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="study_schedules",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Study schedule")
        verbose_name_plural = _("Study schedules")
        ordering = ["id"]
        indexes = [models.Index(fields=["next_run_at"], condition=Q(enabled=True), name="studysched_due")]

    def __str__(self) -> str:
        return f"{self.action} {self.cron} ({self.study_id})"


class StudyPostRun(models.Model):
    """One post-run of a study (analysis then report, or pattern analysis); leased by the executor."""

    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="post_runs")
    kind = models.CharField(_("kind"), max_length=12, choices=PostRunKind.choices, default=PostRunKind.ANALYSIS)
    trigger = models.CharField(_("trigger"), max_length=12, choices=PostRunTrigger.choices)
    status = models.CharField(_("status"), max_length=12, choices=WorkStatus.choices, default=WorkStatus.PENDING)
    # Frozen configuration of this post-run (analysis: tools, only_passing, report; patterns:
    # {"detectors": "default" | [pinned refs], "cluster_params"?, "scope"?}).
    config = models.JSONField(_("config"), default=dict, blank=True)
    analysis_run_ids = models.JSONField(_("analysis run ids"), default=list, blank=True)
    report = models.ForeignKey(
        "reports.Report",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    pattern_run = models.ForeignKey(
        "generation.PatternRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Keys: analysis_done, analysis_total, analysis_failed.
    progress = models.JSONField(_("progress"), default=dict, blank=True)
    lease_owner = models.CharField(_("lease owner"), max_length=80, blank=True, default="")
    lease_expires_at = models.DateTimeField(_("lease expires at"), null=True, blank=True)
    error = models.TextField(_("error"), blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("Study post-run")
        verbose_name_plural = _("Study post-runs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status"],
                condition=Q(status__in=[WorkStatus.PENDING, WorkStatus.RUNNING]),
                name="studypostrun_active",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} post-run {self.pk} ({self.status})"
