"""Slot and pattern models (spec section 3.9).

A ``SlotSet`` freezes one completed, usable agentic run as a parent. Its ``SlotTarget`` rows are the
parts that get regenerated; every variant is a ``GenerationRun`` of kind ``slot_*`` with a
``SlotVariantResult``. Pattern analysis (``PatternRun`` and its hits, clusters, labels and audits) reads
the variants; detectors copy ``tool_severity`` verbatim and a detector error is never ``absent``.
"""

from __future__ import annotations

import uuid
from typing import Any
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from backend.generation import identity
from backend.generation.contracts._base import NonEmptyStr
from backend.generation.contracts._base import PinnedRefStr
from backend.generation.contracts._base import RelPath
from backend.generation.contracts.slot import AuditSample
from backend.generation.contracts.slot import ClusterParams
from backend.generation.contracts.slot import ContextPolicy
from backend.generation.contracts.slot import DiffVsOriginal
from backend.generation.contracts.slot import LeakReport
from backend.generation.contracts.slot import LinkedChecksDiff
from backend.generation.contracts.slot import OutOfSlotEdit
from backend.generation.contracts.slot import PatternMatch
from backend.generation.contracts.slot import PatternScope
from backend.generation.contracts.slot import SlotAddress
from backend.generation.contracts.slot import SlotIndexStats
from backend.generation.contracts.slot import SlotInterface
from backend.generation.contracts.slot import SlotSetSelection
from backend.generation.contracts.slot import TargetValidation
from backend.generation.models.base import Contract
from backend.generation.models.base import Errors
from backend.generation.models.base import FrozenFieldsQuerySet
from backend.generation.models.base import ImmutableVersionError
from backend.generation.models.base import ValidatedModel
from backend.generation.models.base import fill_or_check
from backend.generation.models.base import sha_field
from backend.generation.models.choices import AuditStatus
from backend.generation.models.choices import ClusterMethod
from backend.generation.models.choices import DetectorEngine
from backend.generation.models.choices import EditPolicy
from backend.generation.models.choices import HitErrorReason
from backend.generation.models.choices import HitScope
from backend.generation.models.choices import HitStatus
from backend.generation.models.choices import HoleMode
from backend.generation.models.choices import LabelValue
from backend.generation.models.choices import LinkedChecksSource
from backend.generation.models.choices import ParseStatus
from backend.generation.models.choices import PatternRunLabel
from backend.generation.models.choices import RunKind
from backend.generation.models.choices import SlotKind
from backend.generation.models.choices import WorkStatus

__all__ = [
    "ClusterLabel",
    "DetectorAudit",
    "PatternHit",
    "PatternRun",
    "RunSymbolIndex",
    "SlotSet",
    "SlotTarget",
    "SlotVariantResult",
    "VariantCluster",
    "VariantClusterMember",
]

_STRINGS = Contract(NonEmptyStr, many=True)


class RunSymbolIndex(ValidatedModel):
    """The symbol index of one run's tree (``contracts.slot.SymbolIndex`` JSON in the blob store)."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {"stats": Contract(SlotIndexStats)}
    BLOB_FIELDS: ClassVar[tuple[str, ...]] = ("blob",)

    run = models.ForeignKey("generation.GenerationRun", on_delete=models.PROTECT, related_name="symbol_indexes")
    index_version = models.SmallIntegerField(_("index version"))
    blob = sha_field(_("index blob"))
    stats = models.JSONField(_("stats"), help_text="{files, symbols, endpoints, components, unparsed}")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Run symbol index")
        verbose_name_plural = _("Run symbol indexes")
        constraints = [
            models.UniqueConstraint(fields=["run", "index_version"], name="generation_runsymbolindex_uniq"),
        ]

    def __str__(self) -> str:
        return f"index v{self.index_version} of run {str(self.run_id)[:8]}"


class SlotSet(ValidatedModel):
    """One frozen parent sample: a completed, usable ``app_agentic`` run.

    Everything that pins the parent is frozen at creation (``SlotTarget.target_hash`` is computed from
    ``frozen_tree_hash``); only ``name``, ``notes`` and ``parent_build_key`` (a cache key) may change.
    """

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {"selection": Contract(SlotSetSelection)}
    IMMUTABLE_FIELDS: ClassVar[tuple[str, ...]] = (
        "parent_run",
        "task_version",
        "parent_model",
        "parent_harness",
        "frozen_tree_hash",
        "parent_manifest_hash",
        "parent_eval",
        "index",
        "selection",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("name"), max_length=200)
    parent_run = models.ForeignKey(
        "generation.GenerationRun",
        on_delete=models.PROTECT,
        related_name="slot_sets",
        limit_choices_to={"kind": RunKind.APP_AGENTIC, "usable": True},
    )
    task_version = models.ForeignKey(
        "generation.TaskSpecVersion",
        on_delete=models.PROTECT,
        related_name="slot_sets",
        help_text="Denormalised from the parent",
    )
    parent_model = models.CharField(_("parent model"), max_length=200)
    parent_harness = models.CharField(_("parent harness"), max_length=32, blank=True, default="")
    selection = models.JSONField(_("selection"), help_text="{rule, pool_query, pool_size, seed, picked_by, at}")
    frozen_tree_hash = sha_field(_("frozen tree hash"), help_text="Equals parent_run.tree_hash")
    parent_manifest_hash = sha_field(_("parent manifest hash"))
    parent_eval = models.ForeignKey(
        "generation.RunEvaluation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="The baseline evaluation of the parent",
    )
    parent_build_key = models.CharField(_("parent build key"), max_length=64, blank=True, default="")
    index = models.ForeignKey(RunSymbolIndex, on_delete=models.PROTECT, related_name="slot_sets")
    notes = models.TextField(_("notes"), blank=True, default="")
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
        verbose_name = _("Slot set")
        verbose_name_plural = _("Slot sets")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            self.refuse_changes(
                self.IMMUTABLE_FIELDS,
                "a slot set's parent is frozen; create a new slot set",
                update_fields=kwargs.get("update_fields"),
            )
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.parent_run_id is None:
            return
        parent = self.parent_run
        errors: Errors = {}
        if parent.kind != RunKind.APP_AGENTIC or parent.usable is not True:
            errors["parent_run"] = ["a parent must be a usable app_agentic run"]
        if self.frozen_tree_hash and self.frozen_tree_hash != parent.tree_hash:
            errors["frozen_tree_hash"] = ["must equal the parent run's tree_hash"]
        if self.task_version_id is not None and self.task_version_id != parent.task_version_id:
            errors["task_version"] = ["must be the parent run's task version"]
        if self.index_id is not None and self.index.run_id != parent.pk:
            errors["index"] = ["must be the symbol index of the parent run"]
        if errors:
            raise ValidationError(errors)


class SlotTarget(ValidatedModel):
    """A part of a parent chosen for regeneration (file, region, symbol, endpoint or component)."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {
        "address": Contract(SlotAddress),
        "default_context_policy": Contract(ContextPolicy),
        "allowlist": Contract(RelPath, many=True),
        "interface": Contract(SlotInterface),
        "linked_endpoints": _STRINGS,
        "linked_checks": _STRINGS,
        "linked_checks_diff": Contract(LinkedChecksDiff),
        "linked_probes": _STRINGS,
        "validation": Contract(TargetValidation),
        "leak": Contract(LeakReport),
    }
    BLOB_FIELDS: ClassVar[tuple[str, ...]] = ("original_blob",)
    # Everything target_hash depends on; frozen after creation (section 3.1).
    IDENTITY_SOURCES: ClassVar[tuple[str, ...]] = (
        "slot_set",
        "address",
        "original_sha",
        "allowlist",
        "interface",
        "target_hash",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slot_set = models.ForeignKey(SlotSet, on_delete=models.CASCADE, related_name="targets")
    label = models.CharField(_("label"), max_length=200)
    kind = models.CharField(_("kind"), max_length=10, choices=SlotKind.choices)
    address = models.JSONField(_("address"), help_text="contracts.slot.SlotAddress")
    match_key = models.CharField(
        _("match key"),
        max_length=200,
        blank=True,
        default="",
        help_text="endpoint:<spec endpoint id> or component:<name>; finds the same part in other parents",
    )
    language = models.CharField(_("language"), max_length=24)
    original_sha = sha_field(_("original sha"))
    original_blob = sha_field(_("original blob"))
    default_hole_mode = models.CharField(
        _("default hole mode"),
        max_length=10,
        choices=HoleMode.choices,
        default=HoleMode.STUB,
    )
    default_keep_docstring = models.BooleanField(_("keep docstring by default"), default=False)
    default_context_policy = models.JSONField(_("default context policy"), default=dict, blank=True)
    edit_policy = models.CharField(
        _("edit policy"),
        max_length=8,
        choices=EditPolicy.choices,
        default=EditPolicy.REVERT,
    )
    allowlist = models.JSONField(_("allowlist"), default=list, blank=True, help_text="[path]")
    interface = models.JSONField(
        _("interface"),
        default=dict,
        blank=True,
        help_text="{signature, decorators, imports_in_scope, exported_names}",
    )
    linked_endpoints = models.JSONField(_("linked endpoints"), default=list, blank=True)
    linked_checks = models.JSONField(
        _("linked checks"),
        default=list,
        blank=True,
        help_text="After validation: the discriminating checks only",
    )
    linked_checks_source = models.CharField(
        _("linked checks source"),
        max_length=8,
        choices=LinkedChecksSource.choices,
        default=LinkedChecksSource.AUTO,
    )
    linked_checks_diff = models.JSONField(_("linked checks diff"), default=dict, blank=True)
    linked_probes = models.JSONField(_("linked probes"), default=list, blank=True)
    validation = models.JSONField(_("validation"), default=dict, blank=True)
    leak = models.JSONField(_("leak report"), null=True, blank=True)
    target_hash = sha_field(_("target hash"))
    original_run = models.ForeignKey(
        "generation.GenerationRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="The reference/slot_original run (variant 0)",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Slot target")
        verbose_name_plural = _("Slot targets")
        ordering = ["slot_set", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["slot_set", "target_hash"], name="generation_slottarget_uniq"),
        ]
        indexes = [models.Index(fields=["match_key"], name="slottarget_match_key")]

    def __str__(self) -> str:
        return self.label

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            self.refuse_changes(
                self.IDENTITY_SOURCES,
                "a slot target's identity is frozen; create a new target",
                update_fields=kwargs.get("update_fields"),
            )
        super().save(*args, **kwargs)

    @property
    def unit_key(self) -> str:
        """The slot-mode study unit key ``slot:<target uuid>``."""
        return f"slot:{self.pk}"

    def derive_fields(self) -> Errors:
        errors = super().derive_fields()
        if not self._state.adding or self.slot_set_id is None:
            return errors
        address = self.CONTRACT_FIELDS["address"].try_validate(self.address)
        interface = self.CONTRACT_FIELDS["interface"].try_validate(self.interface)
        allowlist = self.CONTRACT_FIELDS["allowlist"].try_validate(self.allowlist)
        if (
            address is None
            or interface is None
            or allowlist is None
            or not identity.SHA256_RE.match(self.original_sha)
        ):
            return errors  # field validation reports the inputs
        fill_or_check(self, "kind", address.kind, errors)
        fill_or_check(
            self,
            "target_hash",
            identity.target_hash(
                parent_tree=self.slot_set.frozen_tree_hash,
                address=address.identity(),
                original_sha=self.original_sha,
                allowlist=allowlist,
                interface=interface.model_dump(mode="json"),
            ),
            errors,
        )
        return errors


class SlotVariantResult(ValidatedModel):
    """The per-variant record of a ``slot_*`` run: parse status, replacement and its diff to the original."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {
        "out_of_slot_edits": Contract(OutOfSlotEdit, many=True),
        "diff_vs_original": Contract(DiffVsOriginal),
        "new_imports": _STRINGS,
        "new_dependencies": _STRINGS,
    }
    BLOB_FIELDS: ClassVar[tuple[str, ...]] = ("replacement_blob",)

    run = models.OneToOneField(
        "generation.GenerationRun",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="variant_result",
    )
    slot_target = models.ForeignKey(SlotTarget, on_delete=models.PROTECT, related_name="variant_results")
    parse_status = models.CharField(_("parse status"), max_length=16, choices=ParseStatus.choices)
    replacement_blob = sha_field(_("replacement blob"), blank=True)
    replacement_sha = sha_field(_("replacement sha"), blank=True)
    normalized_sha = sha_field(_("normalized sha"), blank=True, db_index=True)
    normalizer_version = models.SmallIntegerField(_("normalizer version"), null=True, blank=True)
    out_of_slot_edits = models.JSONField(_("out-of-slot edits"), default=list, blank=True)
    edit_violation = models.BooleanField(_("edit violation"), default=False)
    diff_vs_original = models.JSONField(_("diff vs original"), null=True, blank=True)
    new_imports = models.JSONField(_("new imports"), default=list, blank=True)
    new_dependencies = models.JSONField(_("new dependencies"), default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Slot variant result")
        verbose_name_plural = _("Slot variant results")

    def __str__(self) -> str:
        return f"variant {str(self.run_id)[:8]} ({self.parse_status})"


class PatternRun(ValidatedModel):
    """One run of the detectors over a study's variants. Immutable once completed."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {
        "detectors": Contract(PinnedRefStr, many=True),
        "cluster_params": Contract(ClusterParams),
        "scope": Contract(PatternScope),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    study = models.ForeignKey("generation.Study", on_delete=models.PROTECT, related_name="pattern_runs")
    detectors = models.JSONField(_("detectors"), default=list, blank=True, help_text="[pinned ref]")
    detector_set_hash = sha_field(_("detector set hash"))
    normalizer_version = models.SmallIntegerField(_("normalizer version"))
    cluster_params = models.JSONField(_("cluster parameters"), default=dict, blank=True)
    scope = models.JSONField(_("scope"), default=dict, blank=True)
    is_live = models.BooleanField(_("live"), default=False, help_text="The per-study provisional run")
    label = models.CharField(
        _("label"),
        max_length=12,
        choices=PatternRunLabel.choices,
        default=PatternRunLabel.EXPLORATORY,
    )
    status = models.CharField(_("status"), max_length=12, choices=WorkStatus.choices, default=WorkStatus.PENDING)
    summary = models.JSONField(_("summary"), default=dict, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Pattern run")
        verbose_name_plural = _("Pattern runs")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["study", "-created_at"], name="patternrun_study")]

    def __str__(self) -> str:
        return f"pattern run {str(self.id)[:8]} ({self.label}, {self.status})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            stored = self.stored_values("status")
            if stored is not None and stored["status"] == WorkStatus.COMPLETED:
                msg = f"pattern run {self.pk} is completed; re-running detectors creates a new pattern run"
                raise ImmutableVersionError(msg)
        super().save(*args, **kwargs)


class PatternHit(ValidatedModel):
    """One detector verdict for one variant at one scope. ``error`` is never reported as ``absent``."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {"matches": Contract(PatternMatch, many=True)}

    pattern_run = models.ForeignKey(PatternRun, on_delete=models.CASCADE, related_name="hits")
    run = models.ForeignKey("generation.GenerationRun", on_delete=models.PROTECT, related_name="pattern_hits")
    detector = models.ForeignKey("generation.PatternDetector", on_delete=models.PROTECT, related_name="hits")
    feature_key = models.SlugField(_("feature key"), max_length=100)
    scope = models.CharField(_("scope"), max_length=4, choices=HitScope.choices)
    status = models.CharField(_("status"), max_length=14, choices=HitStatus.choices)
    error_reason = models.CharField(
        _("error reason"),
        max_length=40,
        choices=HitErrorReason.choices,
        blank=True,
        default="",
    )
    matches = models.JSONField(_("matches"), default=list, blank=True)
    tool_severity = models.CharField(
        _("tool severity"),
        max_length=32,
        blank=True,
        default="",
        help_text="Copied verbatim from the tool; never computed",
    )
    provisional = models.BooleanField(_("provisional"), default=False, help_text="Live hits")

    class Meta:
        verbose_name = _("Pattern hit")
        verbose_name_plural = _("Pattern hits")
        constraints = [
            models.UniqueConstraint(
                fields=["pattern_run", "run", "detector", "scope"],
                name="generation_patternhit_uniq",
            ),
            models.CheckConstraint(
                condition=(Q(status=HitStatus.ERROR) & ~Q(error_reason=""))
                | (~Q(status=HitStatus.ERROR) & Q(error_reason="")),
                name="generation_patternhit_error_reason",
            ),
        ]
        indexes = [models.Index(fields=["pattern_run", "feature_key", "status"], name="patternhit_feature")]

    def __str__(self) -> str:
        return f"{self.feature_key} {self.status} ({self.scope})"


class VariantCluster(models.Model):
    """A cluster of variants of one target (exact by ``normalized_sha``, or MinHash)."""

    pattern_run = models.ForeignKey(PatternRun, on_delete=models.CASCADE, related_name="clusters")
    slot_target = models.ForeignKey(SlotTarget, on_delete=models.PROTECT, related_name="clusters")
    method = models.CharField(_("method"), max_length=8, choices=ClusterMethod.choices)
    key = models.CharField(_("key"), max_length=64, help_text="normalized_sha for exact clusters")
    size = models.PositiveIntegerField(_("size"))
    representative = models.ForeignKey("generation.GenerationRun", on_delete=models.PROTECT, related_name="+")
    includes_original = models.BooleanField(_("includes the original"), default=False)

    class Meta:
        verbose_name = _("Variant cluster")
        verbose_name_plural = _("Variant clusters")
        indexes = [models.Index(fields=["pattern_run", "slot_target"], name="variantcluster_target")]

    def __str__(self) -> str:
        return f"{self.method} cluster {self.key[:12]} (n={self.size})"


class VariantClusterMember(models.Model):
    cluster = models.ForeignKey(VariantCluster, on_delete=models.CASCADE, related_name="members")
    run = models.ForeignKey("generation.GenerationRun", on_delete=models.PROTECT, related_name="+")
    distance = models.FloatField(_("distance"), default=0.0)

    class Meta:
        verbose_name = _("Variant cluster member")
        verbose_name_plural = _("Variant cluster members")
        constraints = [
            models.UniqueConstraint(fields=["cluster", "run"], name="generation_variantclustermember_uniq"),
        ]

    def __str__(self) -> str:
        return f"{str(self.run_id)[:8]} in cluster {self.cluster_id}"


class ClusterLabel(models.Model):
    """A manual code for an exact cluster key (engine ``manual``); stable across pattern runs."""

    slot_target = models.ForeignKey(SlotTarget, on_delete=models.PROTECT, related_name="cluster_labels")
    normalized_sha = sha_field(_("normalized sha"))
    detector = models.ForeignKey(
        "generation.PatternDetector",
        on_delete=models.PROTECT,
        related_name="cluster_labels",
        limit_choices_to={"engine": DetectorEngine.MANUAL},
    )
    value = models.CharField(_("value"), max_length=8, choices=LabelValue.choices)
    labeler = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    note = models.TextField(_("note"), blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Cluster label")
        verbose_name_plural = _("Cluster labels")
        constraints = [
            models.UniqueConstraint(
                fields=["slot_target", "normalized_sha", "detector", "labeler"],
                name="generation_clusterlabel_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.normalized_sha[:12]}: {self.value}"

    def clean(self) -> None:
        super().clean()
        if self.detector_id is not None and self.detector.engine != DetectorEngine.MANUAL:
            raise ValidationError({"detector": _("Cluster labels use a detector with engine 'manual'.")})


class DetectorAudit(ValidatedModel):
    """A human precision/NPV audit sample of one feature's detector verdicts (via human_eval)."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {"sample": Contract(AuditSample)}

    pattern_run = models.ForeignKey(PatternRun, on_delete=models.PROTECT, related_name="audits")
    feature_key = models.SlugField(_("feature key"), max_length=100)
    sample = models.JSONField(_("sample"), help_text="{hits: [run ids], non_hits: [run ids], seed}")
    eval_task = models.ForeignKey(
        "human_eval.EvalTask",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="detector_audits",
    )
    status = models.CharField(_("status"), max_length=12, choices=AuditStatus.choices, default=AuditStatus.PENDING)
    precision = models.FloatField(_("precision"), null=True, blank=True)
    npv = models.FloatField(_("negative predictive value"), null=True, blank=True)
    summary = models.JSONField(_("summary"), default=dict, blank=True, help_text="Wilson CIs and counts")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Detector audit")
        verbose_name_plural = _("Detector audits")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"audit of {self.feature_key} ({self.status})"
