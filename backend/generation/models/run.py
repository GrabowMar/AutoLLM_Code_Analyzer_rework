"""Run models (spec section 3.7): runs, their frozen manifests, attempts, files and evaluations.

``GenerationRun`` keeps the v2 run table's identity (``RenameModel``, ``db_table="generation_run"``), so
the foreign keys of other apps (``AnalysisRun.generation_job``, ``LLMRequest.job``,
``ContainerInstance.generation_job``, ``Report.generation_job``, ``EvalTaskApp.job``) keep their names.
The queue columns are owned by ``backend.execution`` and copied verbatim from v2.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from backend.generation import identity
from backend.generation.contracts.evaluation import AdherenceRecord
from backend.generation.contracts.evaluation import EvalResult
from backend.generation.contracts.evaluation import EvalStages
from backend.generation.contracts.harness import ObservedInputs
from backend.generation.contracts.manifest import RunManifest as RunManifestContent
from backend.generation.contracts.manifest import StimulusRecord
from backend.generation.contracts.outcome import USABLE_OUTCOMES
from backend.generation.contracts.outcome import AttemptServed
from backend.generation.contracts.outcome import AttemptUsage
from backend.generation.contracts.outcome import RunMetrics
from backend.generation.contracts.outcome import RunResult
from backend.generation.contracts.outcome import TimeSplit
from backend.generation.contracts.outcome import Validity
from backend.generation.identity import KIND_PROTOCOL
from backend.generation.models.base import Contract
from backend.generation.models.base import Errors
from backend.generation.models.base import ImmutableVersionError
from backend.generation.models.base import ValidatedModel
from backend.generation.models.base import WriteOnceQuerySet
from backend.generation.models.base import field_names
from backend.generation.models.base import fill_or_check
from backend.generation.models.base import sha_field
from backend.generation.models.base import validate_rel_path
from backend.generation.models.choices import EvalLevel
from backend.generation.models.choices import EvalMode
from backend.generation.models.choices import EvaluationStatus
from backend.generation.models.choices import ExclusionReason
from backend.generation.models.choices import FileExclusion
from backend.generation.models.choices import FileOrigin
from backend.generation.models.choices import Lane
from backend.generation.models.choices import LockSource
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import Protocol
from backend.generation.models.choices import ReferenceOf
from backend.generation.models.choices import RunKind
from backend.generation.models.choices import RunPhase
from backend.generation.models.choices import RunStatus
from backend.generation.models.choices import Stage
from backend.generation.models.choices import Termination

__all__ = [
    "LANE_BY_KIND",
    "EvalBaseline",
    "GenerationArtifact",
    "GenerationRun",
    "RunAttempt",
    "RunEvaluation",
    "RunFile",
    "RunManifest",
]

# Generation lane per run kind (section 3.7). A reference run has no generation stage: it is created in
# ``status=running, phase=eval_queued`` and claimed by phase.
LANE_BY_KIND: dict[str, str] = {
    RunKind.APP_AGENTIC: Lane.SANDBOX,
    RunKind.SLOT_AGENT: Lane.SANDBOX,
    RunKind.SLOT_SINGLE_SHOT: Lane.LLM,
}
SLOT_KINDS = (RunKind.SLOT_SINGLE_SHOT, RunKind.SLOT_AGENT)
_PENDING_QUEUED = Q(status=RunStatus.PENDING) & Q(queued_at__isnull=False)
_RUNNING = Q(status=RunStatus.RUNNING)


# Run columns that are write-once once the run has a RunManifest (appendix A #13: retries keep the
# manifest and the seed): each may only go from empty to the manifest's value, never change otherwise.
MANIFEST_FROZEN_FIELDS: tuple[str, ...] = (
    "kind",
    "protocol",
    "seed",
    "task_hash",
    "treatment_hash",
    "prompt_hash",
    "config_fingerprint",
    "run_fingerprint",
)


def manifest_expected(manifest: Any) -> dict[str, Any]:
    """``{run column: value}`` that a run with this manifest payload must hold (missing keys omitted)."""
    if not isinstance(manifest, dict):
        return {}
    expected: dict[str, Any] = {name: manifest.get(name) for name in ("kind", "protocol")}
    keys = manifest.get("identity") or {}
    for name in GenerationRun.IDENTITY_FIELDS:
        expected[name] = keys.get(name)
    sampling = manifest.get("sampling") or {}
    expected["seed"] = sampling.get("seed")
    return {name: value for name, value in expected.items() if value not in ("", None)}


class GenerationRunQuerySet(models.QuerySet):
    """Refuses ``update()`` of ``MANIFEST_FROZEN_FIELDS`` on runs that have a manifest (use ``save()``)."""

    def update(self, **kwargs: Any) -> int:
        frozen = sorted(field_names(self.model, kwargs) & set(MANIFEST_FROZEN_FIELDS))
        if frozen:
            pinned = list(self.filter(manifest__isnull=False).values_list("pk", flat=True)[:5])
            if pinned:
                msg = f"runs {', '.join(map(str, pinned))} have a manifest; refused bulk update of {', '.join(frozen)}"
                raise ImmutableVersionError(msg)
        return super().update(**kwargs)


def _library_fk(model: str, verbose_name: str) -> models.ForeignKey:
    return models.ForeignKey(
        f"generation.{model}",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="runs",
        verbose_name=verbose_name,
    )


class GenerationRun(ValidatedModel):
    """One queued unit that produces one sample or one evaluation."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {
        "validity": Contract(Validity),
        "result": Contract(RunResult),
        "metrics": Contract(RunMetrics),
    }
    BLOB_FIELDS: ClassVar[tuple[str, ...]] = ("workspace_blob", "resolved_lock_blob")
    IDENTITY_FIELDS: ClassVar[tuple[str, ...]] = (
        "task_hash",
        "treatment_hash",
        "prompt_hash",
        "config_fingerprint",
        "run_fingerprint",
    )
    DERIVED_FROM: ClassVar[dict[str, tuple[str, ...]]] = {"outcome": ("usable",)}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(_("kind"), max_length=20, choices=RunKind.choices, db_index=True)
    protocol = models.CharField(_("protocol"), max_length=12, choices=Protocol.choices)
    status = models.CharField(_("status"), max_length=12, choices=RunStatus.choices, default=RunStatus.PENDING)
    # Where a RUNNING run is: in its generation stage, waiting for evaluation, or being evaluated;
    # "" whenever the run is not running.
    phase = models.CharField(_("phase"), max_length=12, choices=RunPhase.choices, blank=True, default="")
    outcome = models.CharField(_("outcome"), max_length=16, choices=Outcome.choices, blank=True, default="")
    outcome_reason = models.CharField(_("outcome reason"), max_length=64, blank=True, default="")
    usable = models.BooleanField(
        _("usable"),
        null=True,
        blank=True,
        help_text="True when the outcome is analysable as model behaviour (section 6.13)",
    )
    validity = models.JSONField(_("validity"), default=dict, blank=True)

    # Study membership.
    study = models.ForeignKey(
        "generation.Study",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    condition = models.ForeignKey(
        "generation.StudyCondition",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    cell = models.ForeignKey(
        "generation.StudyCell",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    cell_attempt = models.PositiveSmallIntegerField(
        _("cell attempt"),
        null=True,
        blank=True,
        help_text="1 for the planned run of a cell, 2.. for top-ups",
    )
    beyond_plan = models.BooleanField(
        _("beyond plan"),
        default=False,
        help_text="Created on a cell that already had a usable run; excluded from results",
    )
    stage = models.CharField(_("stage"), max_length=6, choices=Stage.choices, blank=True, default="")
    repeat_index = models.PositiveSmallIntegerField(_("repeat index"), null=True, blank=True)

    # Treatment, copied from the manifest for querying.
    model = models.ForeignKey(
        "llm_models.LLMModel",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="generation_runs",
    )
    task_version = _library_fk("TaskSpecVersion", _("task version"))
    prose_variant = models.CharField(_("prose variant"), max_length=12, blank=True, default="")
    stack_profile = _library_fk("StackProfile", _("stack profile"))
    starter = _library_fk("Starter", _("starter"))
    style = _library_fk("StyleDirective", _("style"))
    guidance = _library_fk("Guidance", _("guidance"))
    prompt_set = _library_fk("PromptSet", _("prompt set"))
    harness_preset = _library_fk("HarnessPreset", _("harness preset"))
    sampling_preset = _library_fk("SamplingPreset", _("sampling preset"))
    resource_policy = _library_fk("ResourcePolicy", _("budget preset"))
    auth_mode = models.CharField(_("auth mode"), max_length=16, blank=True, default="")
    slot_target = _library_fk("SlotTarget", _("slot target"))
    parent_run = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="slot_variants",
    )
    reference_of = models.CharField(
        _("reference of"),
        max_length=16,
        choices=ReferenceOf.choices,
        blank=True,
        default="",
    )
    seed = models.PositiveBigIntegerField(_("seed"), null=True, blank=True)
    pinned_served_name = models.CharField(_("pinned served name"), max_length=200, blank=True, default="")
    pinned_digest = models.CharField(_("pinned digest"), max_length=80, blank=True, default="")

    # Identity keys (frozen in the manifest; copied here for indexed queries).
    task_hash = sha_field(_("task hash"), blank=True, db_index=True)
    treatment_hash = sha_field(_("treatment hash"), blank=True, db_index=True)
    prompt_hash = sha_field(_("prompt hash"), blank=True, db_index=True)
    config_fingerprint = sha_field(_("config fingerprint"), blank=True)  # indexed as genrun_cfp
    run_fingerprint = sha_field(_("run fingerprint"), blank=True, db_index=True)

    # The collected sample.
    tree_hash = sha_field(_("tree hash"), blank=True)  # indexed as genrun_tree
    workspace_blob = sha_field(_("workspace blob"), blank=True, help_text="tar.zst of the final workspace")
    resolved_lock_blob = sha_field(_("resolved lock blob"), blank=True, help_text="contracts.evaluation.ResolvedLock")
    lock_source = models.CharField(_("lock source"), max_length=10, choices=LockSource.choices, blank=True, default="")
    registry_snapshot_at = models.DateTimeField(_("registry snapshot at"), null=True, blank=True)
    result = models.JSONField(_("result"), default=dict, blank=True, help_text="contracts.outcome.RunResult")
    metrics = models.JSONField(_("metrics"), default=dict, blank=True, help_text="contracts.outcome.RunMetrics")

    # Execution queue (owned by backend.execution). NULL endpoint = OpenRouter.
    endpoint = models.ForeignKey(
        "inference.LLMEndpoint",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="generation_runs",
    )
    lane = models.CharField(_("lane"), max_length=8, choices=Lane.choices, default=Lane.LLM)
    # Non-NULL means the executor owns the row.
    queued_at = models.DateTimeField(_("queued at"), null=True, blank=True)
    run_after = models.DateTimeField(_("run after"), null=True, blank=True)
    priority = models.SmallIntegerField(_("priority"), default=0)
    sequence = models.PositiveIntegerField(_("sequence"), null=True, blank=True)
    attempt = models.PositiveSmallIntegerField(_("attempt"), default=0)
    lease_owner = models.CharField(_("lease owner"), max_length=80, blank=True, default="")
    lease_expires_at = models.DateTimeField(_("lease expires at"), null=True, blank=True)
    heartbeat_at = models.DateTimeField(_("heartbeat at"), null=True, blank=True)
    # When the run entered its current phase (eval FIFO, UI).
    stage_entered_at = models.DateTimeField(_("stage entered at"), null=True, blank=True)
    # Runtime record (policy, started_seq, queue timings, infra errors, ...); not fingerprinted.
    execution = models.JSONField(_("execution"), default=dict, blank=True)

    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    duration_seconds = models.FloatField(_("duration (seconds)"), null=True, blank=True)
    error_message = models.TextField(_("error message"), blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generation_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GenerationRunQuerySet.as_manager()

    class Meta:
        db_table = "generation_run"
        verbose_name = _("Generation run")
        verbose_name_plural = _("Generation runs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-priority", "queued_at"], condition=_PENDING_QUEUED, name="genrun_ready"),
            models.Index(
                fields=["study", "endpoint", "sequence", "queued_at"],
                condition=_PENDING_QUEUED,
                name="genrun_lane_head",
            ),
            models.Index(fields=["lease_owner"], condition=~Q(lease_owner=""), name="genrun_owned"),
            models.Index(fields=["endpoint", "lane", "phase"], condition=_RUNNING, name="genrun_running"),
            models.Index(fields=["lease_expires_at"], condition=_RUNNING, name="genrun_lease"),
            models.Index(fields=["cell", "created_at"], name="genrun_cell"),
            models.Index(fields=["study", "status", "phase"], name="genrun_study_state"),
            models.Index(fields=["config_fingerprint"], name="genrun_cfp"),
            models.Index(fields=["slot_target", "condition"], name="genrun_slot"),
            models.Index(fields=["parent_run"], name="genrun_parent"),
            models.Index(fields=["tree_hash"], name="genrun_tree"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(kind=RunKind.APP_AGENTIC) | Q(task_version__isnull=False),
                name="genrun_app_has_task",
            ),
            models.CheckConstraint(
                condition=~Q(kind__in=SLOT_KINDS) | (Q(slot_target__isnull=False) & Q(parent_run__isnull=False)),
                name="genrun_slot_has_target",
            ),
            models.CheckConstraint(
                condition=Q(kind=RunKind.REFERENCE) | Q(model__isnull=False),
                name="genrun_model_unless_reference",
            ),
            models.CheckConstraint(
                condition=(
                    Q(kind=RunKind.APP_AGENTIC, protocol=Protocol.APP_1)
                    | Q(kind__in=SLOT_KINDS, protocol=Protocol.SLOT_1)
                    | Q(kind=RunKind.REFERENCE, protocol=Protocol.REF_1)
                ),
                name="genrun_kind_protocol",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} run {str(self.id)[:8]} ({self.status})"

    @property
    def is_slot_variant(self) -> bool:
        return self.kind in SLOT_KINDS

    @classmethod
    def iter_blob_refs(cls) -> Iterator[str]:
        """The lock files inside every ``resolved_lock_blob`` payload (``ResolvedLock.files`` values)."""
        from backend.generation import blobs

        locks = cls._base_manager.exclude(resolved_lock_blob="").values_list("resolved_lock_blob", flat=True)
        for sha in locks.distinct().iterator():
            yield from blobs.nested_blob_refs(sha, _resolved_lock_files)

    @property
    def fingerprints(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in self.IDENTITY_FIELDS}

    def derive_fields(self) -> Errors:
        errors = super().derive_fields()
        expected = KIND_PROTOCOL.get(self.kind)
        if expected is not None:
            fill_or_check(self, "protocol", expected, errors)
        if self._state.adding and self.kind in LANE_BY_KIND:
            self.lane = LANE_BY_KIND[self.kind]
        # usable is derived from the outcome alone (section 6.13); no outcome yet means "unknown".
        self.usable = (self.outcome in USABLE_OUTCOMES) if self.outcome else None
        return errors

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            self._refuse_manifest_drift(kwargs.get("update_fields"))
        super().save(*args, **kwargs)

    def _refuse_manifest_drift(self, update_fields: Any) -> None:
        """Once a manifest exists, ``MANIFEST_FROZEN_FIELDS`` may only be filled with the manifest's value."""
        names = list(MANIFEST_FROZEN_FIELDS)
        if update_fields is not None:
            names = [name for name in names if name in set(update_fields)]
        if not names:
            return
        payload = RunManifest._base_manager.filter(run_id=self.pk).values_list("manifest", flat=True).first()  # noqa: SLF001
        if payload is None:
            return
        expected = manifest_expected(payload)
        stored = self.stored_values(*names) or {}
        changed = [
            name
            for name in names
            if getattr(self, name) != stored.get(name) and getattr(self, name) != expected.get(name, stored.get(name))
        ]
        if changed:
            msg = (
                f"run {self.pk} has a manifest; {', '.join(changed)} are write-once "
                "and must equal the manifest (appendix A #13)"
            )
            raise ImmutableVersionError(msg)

    def clean(self) -> None:
        super().clean()
        errors: Errors = {}
        if self.kind in LANE_BY_KIND and self.lane != LANE_BY_KIND[self.kind]:
            errors["lane"] = [f"{self.kind} runs use the {LANE_BY_KIND[self.kind]} lane"]
        if self.outcome == Outcome.EXCLUDED and self.outcome_reason not in ExclusionReason.values:
            errors["outcome_reason"] = [f"excluded runs need one of {', '.join(ExclusionReason.values)}"]
        if self.kind == RunKind.REFERENCE and not self.reference_of:
            errors["reference_of"] = ["reference runs record what they are a reference of"]
        if self.kind != RunKind.REFERENCE and self.reference_of:
            errors["reference_of"] = ["only reference runs have reference_of"]
        if errors:
            raise ValidationError(errors)


def _resolved_lock_files(payload: Any) -> Iterator[str]:
    files = payload.get("files") if isinstance(payload, dict) else None
    if isinstance(files, dict):
        yield from files.values()


class RunManifest(ValidatedModel):
    """The frozen record of everything a run depends on (section 3.8); written once, before queueing."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {
        "manifest": Contract(RunManifestContent),
        "stimuli": Contract(StimulusRecord, many=True),
    }

    run = models.OneToOneField(GenerationRun, on_delete=models.CASCADE, primary_key=True, related_name="manifest")
    protocol = models.CharField(_("protocol"), max_length=12, choices=Protocol.choices)
    manifest = models.JSONField(_("manifest"), help_text="contracts.manifest.RunManifest")
    manifest_hash = sha_field(_("manifest hash"), help_text="H(manifest)")
    stimuli = models.JSONField(
        _("stimuli"),
        default=list,
        blank=True,
        help_text="[{role, template_ref, sha, blob, chars, est_tokens, sources}]",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = WriteOnceQuerySet.as_manager()

    class Meta:
        verbose_name = _("Run manifest")
        verbose_name_plural = _("Run manifests")

    def __str__(self) -> str:
        return f"manifest {self.manifest_hash[:12]} of run {str(self.run_id)[:8]}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            msg = f"run manifest {self.run_id} is write-once"
            raise ImmutableVersionError(msg)
        errors = self.derive_fields()
        if not errors and self.run_id is not None:
            errors = self.run_mismatches()
        if errors:
            raise ValidationError(errors)
        super().save(*args, **kwargs)

    def run_mismatches(self) -> Errors:
        """``{"manifest": [...]}`` for every run column that disagrees with the manifest.

        ``kind`` and ``protocol`` must be equal; the identity columns and the seed must be equal where the
        run already holds a value (an empty column is filled later, see ``GenerationRun.save``).
        """
        run = self.run
        errors: Errors = {}
        for name, value in manifest_expected(self.manifest).items():
            current = getattr(run, name)
            if name in {"kind", "protocol"} or current not in ("", None):
                if current != value:
                    errors.setdefault("manifest", []).append(f"manifest {name} differs from the run's {name}")
        return errors

    @classmethod
    def iter_blob_refs(cls) -> Iterator[str]:
        """The rendered stimulus texts in the blob store (``stimuli[*].blob``)."""
        for stimuli in cls._base_manager.values_list("stimuli", flat=True).iterator():
            for record in stimuli or ():
                if isinstance(record, dict) and record.get("blob"):
                    yield record["blob"]

    def derive_fields(self) -> Errors:
        errors = super().derive_fields()
        manifest = self.CONTRACT_FIELDS["manifest"].try_validate(self.manifest)
        if manifest is None:
            return errors
        self.manifest = manifest.model_dump(mode="json")
        fill_or_check(self, "manifest_hash", identity.H(self.manifest), errors)
        fill_or_check(self, "protocol", self.manifest["protocol"], errors)
        return errors

    def clean(self) -> None:
        super().clean()
        if self.run_id is None or not isinstance(self.manifest, dict):
            return
        errors = self.run_mismatches()
        if errors:
            raise ValidationError(errors)


class RunAttempt(ValidatedModel):
    """One execution attempt of a run (per-attempt facts never enter the manifest).

    ``observed_inputs`` is what the harness actually sent (``harness.observed_inputs``, section 5.2 rule 7:
    system prompt sha, tool names, ...), queryable for ``tool_set_varies``; ``trajectory_blob`` is the
    parsed trajectory (``harness.parse_trajectory``) as JSON in the blob store. Both are written by the
    runner (section 6.9) and are ``None`` / ``""`` until then or when the harness yields nothing.
    """

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {
        "served": Contract(AttemptServed),
        "usage": Contract(AttemptUsage),
        "time_split": Contract(TimeSplit),
        "observed_inputs": Contract(ObservedInputs),
    }
    BLOB_FIELDS: ClassVar[tuple[str, ...]] = ("trajectory_blob",)

    run = models.ForeignKey(GenerationRun, on_delete=models.CASCADE, related_name="attempts")
    attempt = models.PositiveSmallIntegerField(_("attempt"))
    executor_id = models.CharField(_("executor"), max_length=80, blank=True, default="")
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    ended_at = models.DateTimeField(_("ended at"), null=True, blank=True)
    termination = models.CharField(
        _("termination"),
        max_length=24,
        choices=Termination.choices,
        blank=True,
        default="",
    )
    outcome = models.CharField(_("outcome"), max_length=16, choices=Outcome.choices, blank=True, default="")
    infra_error = models.CharField(_("infra error"), max_length=64, blank=True, default="")
    oom_killed = models.BooleanField(_("OOM killed"), default=False)
    served = models.JSONField(_("served"), default=dict, blank=True)
    usage = models.JSONField(_("usage"), default=dict, blank=True)
    budget_exhausted = models.CharField(_("budget exhausted"), max_length=24, blank=True, default="")
    time_split = models.JSONField(_("time split"), default=dict, blank=True)
    observed_inputs = models.JSONField(
        _("observed inputs"),
        null=True,
        blank=True,
        help_text="contracts.harness.ObservedInputs (what the harness sent; section 5.2 rule 7)",
    )
    trajectory_blob = sha_field(
        _("trajectory blob"),
        blank=True,
        help_text="The parsed trajectory (harness.parse_trajectory) as JSON in the blob store",
    )
    evidence_dir = models.CharField(
        _("evidence directory"),
        max_length=255,
        blank=True,
        default="",
        help_text="GENERATION_RUNS_ROOT/<run>/a<attempt>/",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Run attempt")
        verbose_name_plural = _("Run attempts")
        ordering = ["run", "attempt"]
        constraints = [
            models.UniqueConstraint(fields=["run", "attempt"], name="generation_runattempt_run_attempt_uniq"),
            models.CheckConstraint(condition=Q(attempt__gte=1), name="generation_runattempt_attempt_gte_1"),
        ]

    def __str__(self) -> str:
        return f"attempt {self.attempt} of run {str(self.run_id)[:8]}"

    @classmethod
    def iter_blob_refs(cls) -> Iterator[str]:
        """``observed_inputs.native_system_prompt_blob`` of every attempt."""
        values = cls._base_manager.filter(observed_inputs__native_system_prompt_blob__isnull=False).values_list(
            "observed_inputs__native_system_prompt_blob",
            flat=True,
        )
        for sha in values.iterator():
            if isinstance(sha, str) and sha:
                yield sha


class RunFile(ValidatedModel):
    """One collected path of a sample. A slot variant stores only its ``slot`` rows."""

    BLOB_FIELDS: ClassVar[tuple[str, ...]] = ("blob",)

    run = models.ForeignKey(GenerationRun, on_delete=models.CASCADE, related_name="files")
    path = models.CharField(_("path"), max_length=1024, validators=[validate_rel_path])
    blob = sha_field(_("blob"), blank=True, help_text="Empty only for deleted paths")
    size = models.PositiveIntegerField(_("size"), default=0)
    mode = models.SmallIntegerField(_("mode"), default=0o644)
    origin = models.CharField(_("origin"), max_length=12, choices=FileOrigin.choices)
    language = models.CharField(_("language"), max_length=24, blank=True, default="")
    loc = models.PositiveIntegerField(_("source LOC"), default=0, help_text="reports.services.loc.source_loc")
    is_test = models.BooleanField(_("test file"), default=False)
    excluded = models.CharField(
        _("excluded"),
        max_length=16,
        choices=FileExclusion.choices,
        blank=True,
        default="",
    )

    class Meta:
        verbose_name = _("Run file")
        verbose_name_plural = _("Run files")
        ordering = ["run", "path"]
        constraints = [
            models.UniqueConstraint(fields=["run", "path"], name="generation_runfile_run_path_uniq"),
            models.CheckConstraint(
                condition=Q(origin=FileOrigin.DELETED) | ~Q(blob=""),
                name="generation_runfile_blob_unless_deleted",
            ),
        ]
        indexes = [models.Index(fields=["run", "origin"], name="runfile_run_origin")]

    def __str__(self) -> str:
        return f"{self.path} ({self.origin})"


class EvaluationQuerySet(models.QuerySet):
    """Refuses ``update()`` when it would touch a final evaluation (they are frozen, section 3.7)."""

    def update(self, **kwargs: Any) -> int:
        final = self.filter(status__in=RunEvaluation.FINAL_STATUSES).values_list("pk", flat=True)[:5]
        if final:
            msg = f"evaluations {', '.join(map(str, final))} are final; refused bulk update"
            raise ImmutableVersionError(msg)
        return super().update(**kwargs)


class RunEvaluation(ValidatedModel):
    """One evaluation of a run under a suite. Append-only: re-evaluating creates a new row.

    A row may be updated while it is ``pending`` or ``running``; once it reaches ``measured``,
    ``not_measured`` or ``error`` it is frozen. Readers take the latest row per ``(run, eval_suite)``
    and ignore ``retest_of`` rows (they only feed test-retest agreement).
    """

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {
        "stages": Contract(EvalStages),
        "result": Contract(EvalResult),
        "adherence": Contract(AdherenceRecord),
    }
    FINAL_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {EvaluationStatus.MEASURED, EvaluationStatus.NOT_MEASURED, EvaluationStatus.ERROR},
    )

    run = models.ForeignKey(GenerationRun, on_delete=models.CASCADE, related_name="evaluations")
    eval_suite = models.ForeignKey("generation.EvalSuite", on_delete=models.PROTECT, related_name="evaluations")
    eval_level = models.CharField(_("evaluation level"), max_length=12, choices=EvalLevel.choices)
    mode = models.CharField(_("mode"), max_length=12, choices=EvalMode.choices, default=EvalMode.FULL)
    status = models.CharField(
        _("status"),
        max_length=12,
        choices=EvaluationStatus.choices,
        default=EvaluationStatus.PENDING,
    )
    stages = models.JSONField(_("stages"), default=dict, blank=True)
    result = models.JSONField(_("result"), null=True, blank=True, help_text="contracts.evaluation.EvalResult")
    adherence = models.JSONField(_("adherence"), null=True, blank=True)
    build_key = models.CharField(_("build key"), max_length=64, blank=True, default="")
    image_id = models.CharField(_("image id"), max_length=80, blank=True, default="")
    reused_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reuses",
        help_text="Reused for an identical (tree_hash, suite hash, check selection)",
    )
    retest_of = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retests",
        help_text="Set on the random test-retest re-evaluations",
    )
    baseline = models.ForeignKey(
        "generation.EvalBaseline",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="evaluations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = EvaluationQuerySet.as_manager()

    class Meta:
        verbose_name = _("Run evaluation")
        verbose_name_plural = _("Run evaluations")
        ordering = ["run", "-created_at"]
        indexes = [models.Index(fields=["run", "eval_suite", "-created_at"], name="runeval_latest")]

    def __str__(self) -> str:
        return f"evaluation {self.pk} of run {str(self.run_id)[:8]} ({self.status})"

    @classmethod
    def iter_blob_refs(cls) -> Iterator[str]:
        """``adherence.sbom_blob`` and every ``stages.<stage>.detail.credentials_ref`` (section 8.6)."""
        rows = cls._base_manager.values_list("adherence__sbom_blob", "stages")
        for sbom, stages in rows.iterator():
            if isinstance(sbom, str) and sbom:
                yield sbom
            for record in (stages or {}).values() if isinstance(stages, dict) else ():
                detail = record.get("detail") if isinstance(record, dict) else None
                ref = detail.get("credentials_ref") if isinstance(detail, dict) else None
                if isinstance(ref, str) and ref:
                    yield ref

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            stored = self.stored_values("status")
            if stored is not None and stored["status"] in self.FINAL_STATUSES:
                msg = f"evaluation {self.pk} is final ({stored['status']}); evaluate again to get a new row"
                raise ImmutableVersionError(msg)
        super().save(*args, **kwargs)


class EvalBaseline(ValidatedModel):
    """The starter baseline per ``(starter, task_version, auth_mode, eval_suite)``."""

    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {"checks_passed": Contract(str, many=True)}

    starter = models.ForeignKey("generation.Starter", on_delete=models.PROTECT, related_name="baselines")
    task_version = models.ForeignKey("generation.TaskSpecVersion", on_delete=models.PROTECT, related_name="baselines")
    auth_mode = models.CharField(_("auth mode"), max_length=16)
    eval_suite = models.ForeignKey("generation.EvalSuite", on_delete=models.PROTECT, related_name="baselines")
    reference_run = models.ForeignKey(GenerationRun, on_delete=models.PROTECT, related_name="baselines")
    agent_pass_rate = models.FloatField(_("pass rate"))
    checks_passed = models.JSONField(_("checks passed"), default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Evaluation baseline")
        verbose_name_plural = _("Evaluation baselines")
        constraints = [
            models.UniqueConstraint(
                fields=["starter", "task_version", "auth_mode", "eval_suite"],
                name="generation_evalbaseline_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"baseline {self.pk} ({self.auth_mode}, {self.agent_pass_rate:.2f})"

    def clean(self) -> None:
        super().clean()
        if self.reference_run_id is None:
            return
        run = self.reference_run
        errors: Errors = {}
        if run.kind != RunKind.REFERENCE or run.reference_of != ReferenceOf.STARTER_BASELINE:
            errors["reference_run"] = ["a baseline's reference run is a reference run of the starter baseline"]
        elif run.task_version_id is not None and run.task_version_id != self.task_version_id:
            errors["reference_run"] = ["the reference run evaluates another task version"]
        if errors:
            raise ValidationError(errors)


class GenerationArtifact(models.Model):
    """Raw LLM request/response payload of one call (``agent_call_%04d`` or ``slot_call_%04d``)."""

    run = models.ForeignKey(GenerationRun, on_delete=models.CASCADE, related_name="artifacts")
    attempt = models.PositiveSmallIntegerField(_("attempt"), default=1)
    stage = models.CharField(_("stage"), max_length=50, help_text='"agent_call_0001" or "slot_call_0001"')
    request_payload = models.JSONField(_("request payload"), default=dict)
    response_payload = models.JSONField(_("response payload"), default=dict)
    prompt_tokens = models.PositiveIntegerField(_("prompt tokens"), default=0)
    completion_tokens = models.PositiveIntegerField(_("completion tokens"), default=0)
    total_cost = models.FloatField(_("estimated cost"), default=0.0)
    telemetry = models.JSONField(
        _("telemetry"),
        default=dict,
        blank=True,
        help_text=(
            "Normalized per-call telemetry (timing, served provider, token breakdown, request_class, "
            "finish_reason, response_model, system_fingerprint, front_protocol, serving_tier). "
            "Keys absent = unknown."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Generation artifact")
        verbose_name_plural = _("Generation artifacts")
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"artifact {self.stage} of run {str(self.run_id)[:8]}"
