"""factory_boy factories for every model in ``backend.generation.models`` (spec section 12.2).

Every factory's default instance passes ``full_clean()``. Derived columns (content hashes, query
columns, protocol, lane, the study's design mirror, condition and target hashes) are left to the models,
which fill them on save. Related rows are created through ``SubFactory`` and kept consistent:

* study trees derive from the top: a ``StudyCondition`` or ``StudyUnit`` takes its study from its plan, a
  ``StudyCell`` its plan and study from its condition. Pass ``plan=`` (or ``condition=``), not ``study=``
  alone;
* slot rows derive from the slot set: a ``SlotSet`` copies task, model and tree hash from its parent run, and
  slot variants (trait ``slot_single_shot`` / ``slot_agent``) take their parent run from the target.

``GenerationRunFactory`` traits: ``tested`` (a completed, usable sample with a tree hash),
``slot_single_shot``, ``slot_agent`` and ``reference``. Library factories have the trait ``system``.
``BlobFactory`` writes only the index row; use ``blobs.put_bytes`` for a stored blob.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import factory
from django.db.models import Max
from django.utils import timezone
from factory.django import DjangoModelFactory

from backend.generation.models import Blob
from backend.generation.models import ClusterLabel
from backend.generation.models import CostPrior
from backend.generation.models import DetectorAudit
from backend.generation.models import EvalBaseline
from backend.generation.models import EvalSuite
from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationRun
from backend.generation.models import Guidance
from backend.generation.models import HarnessPreset
from backend.generation.models import PatternDetector
from backend.generation.models import PatternHit
from backend.generation.models import PatternRun
from backend.generation.models import PromptSet
from backend.generation.models import PromptTemplate
from backend.generation.models import ResourcePolicy
from backend.generation.models import RunAttempt
from backend.generation.models import RunEvaluation
from backend.generation.models import RunFile
from backend.generation.models import RunManifest
from backend.generation.models import RunSymbolIndex
from backend.generation.models import SamplingPreset
from backend.generation.models import SlotSet
from backend.generation.models import SlotTarget
from backend.generation.models import SlotVariantResult
from backend.generation.models import SpendCap
from backend.generation.models import SpendLedgerEntry
from backend.generation.models import StackProfile
from backend.generation.models import Starter
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models import StudyCondition
from backend.generation.models import StudyFactor
from backend.generation.models import StudyPlan
from backend.generation.models import StudyPostRun
from backend.generation.models import StudyPromptOverride
from backend.generation.models import StudySchedule
from backend.generation.models import StudyUnit
from backend.generation.models import StyleDirective
from backend.generation.models import TaskSpec
from backend.generation.models import TaskSpecVersion
from backend.generation.models import VariantCluster
from backend.generation.models import VariantClusterMember
from backend.generation.models import VersionedEntity
from backend.generation.models import choices
from backend.generation.testing import content as samples
from backend.generation.testing.content import fake_sha
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

__all__ = [
    "BlobFactory",
    "ClusterLabelFactory",
    "CostPriorFactory",
    "DetectorAuditFactory",
    "EvalBaselineFactory",
    "EvalSuiteFactory",
    "GenerationArtifactFactory",
    "GenerationRunFactory",
    "GuidanceFactory",
    "HarnessPresetFactory",
    "PatternDetectorFactory",
    "PatternHitFactory",
    "PatternRunFactory",
    "PromptSetFactory",
    "PromptTemplateFactory",
    "ResourcePolicyFactory",
    "RunAttemptFactory",
    "RunEvaluationFactory",
    "RunFileFactory",
    "RunManifestFactory",
    "RunSymbolIndexFactory",
    "SamplingPresetFactory",
    "SlotSetFactory",
    "SlotTargetFactory",
    "SlotVariantResultFactory",
    "SpendCapFactory",
    "SpendLedgerEntryFactory",
    "StackProfileFactory",
    "StarterFactory",
    "StudyCellFactory",
    "StudyConditionFactory",
    "StudyFactorFactory",
    "StudyFactory",
    "StudyPlanFactory",
    "StudyPostRunFactory",
    "StudyPromptOverrideFactory",
    "StudyScheduleFactory",
    "StudyUnitFactory",
    "StyleDirectiveFactory",
    "TaskSpecFactory",
    "TaskSpecVersionFactory",
    "VariantClusterFactory",
    "VariantClusterMemberFactory",
    "run_manifest_content",
]

_FACTORIES = "backend.generation.testing.factories"


def _sha_sequence(label: str) -> factory.Sequence:
    return factory.Sequence(lambda n: fake_sha(f"{label}:{n}"))


# =====================================================================================
# Library
# =====================================================================================


class _VersionedEntityFactory[E: VersionedEntity](DjangoModelFactory[E]):
    """Shared declarations of the versioned library kinds (version 1, private, user-owned)."""

    version = 1
    name = factory.LazyAttribute(lambda o: f"{o.slug} v{o.version}")
    description = ""

    class Meta:
        abstract = True

    class Params:
        system = factory.Trait(is_system=True, visibility=choices.Visibility.SYSTEM)


class StackProfileFactory(_VersionedEntityFactory[StackProfile]):
    slug = factory.Sequence(lambda n: f"stack-{n}")
    content = factory.LazyFunction(samples.stack_content)

    class Meta:
        model = StackProfile


class StarterFactory(_VersionedEntityFactory[Starter]):
    slug = factory.Sequence(lambda n: f"starter-{n}")
    stack_profile = factory.SubFactory(StackProfileFactory)
    content = factory.LazyAttribute(lambda o: samples.starter_content(o.stack_profile.pinned_ref))

    class Meta:
        model = Starter


class StyleDirectiveFactory(_VersionedEntityFactory[StyleDirective]):
    slug = factory.Sequence(lambda n: f"style-{n}")
    content = factory.LazyFunction(samples.style_content)

    class Meta:
        model = StyleDirective


class GuidanceFactory(_VersionedEntityFactory[Guidance]):
    slug = factory.Sequence(lambda n: f"guidance-{n}")
    content = factory.LazyFunction(samples.guidance_content)

    class Meta:
        model = Guidance


class PromptTemplateFactory(_VersionedEntityFactory[PromptTemplate]):
    slug = factory.Sequence(lambda n: f"prompt-{n}")
    content = factory.LazyFunction(samples.prompt_content)

    class Meta:
        model = PromptTemplate


class PromptSetFactory(_VersionedEntityFactory[PromptSet]):
    slug = factory.Sequence(lambda n: f"prompt-set-{n}")
    content = factory.LazyFunction(samples.prompt_set_content)

    class Meta:
        model = PromptSet


class HarnessPresetFactory(_VersionedEntityFactory[HarnessPreset]):
    slug = factory.Sequence(lambda n: f"harness-preset-{n}")
    content = factory.LazyFunction(samples.harness_preset_content)

    class Meta:
        model = HarnessPreset


class SamplingPresetFactory(_VersionedEntityFactory[SamplingPreset]):
    slug = factory.Sequence(lambda n: f"sampling-{n}")
    content = factory.LazyFunction(samples.sampling_content)

    class Meta:
        model = SamplingPreset


class ResourcePolicyFactory(_VersionedEntityFactory[ResourcePolicy]):
    slug = factory.Sequence(lambda n: f"budget-{n}")
    tier = choices.PolicyTier.FRUGAL
    content = factory.LazyAttribute(lambda o: samples.resource_policy_content(o.tier))

    class Meta:
        model = ResourcePolicy


class EvalSuiteFactory(_VersionedEntityFactory[EvalSuite]):
    slug = factory.Sequence(lambda n: f"eval-suite-{n}")
    content = factory.LazyFunction(samples.eval_suite_content)

    class Meta:
        model = EvalSuite


class PatternDetectorFactory(_VersionedEntityFactory[PatternDetector]):
    slug = factory.Sequence(lambda n: f"detector-{n}")
    content = factory.LazyFunction(samples.detector_content)

    class Meta:
        model = PatternDetector

    class Params:
        manual = factory.Trait(
            content=factory.LazyFunction(lambda: samples.detector_content(engine="manual", feature_key="manual-code")),
        )


class TaskSpecFactory(DjangoModelFactory[TaskSpec]):
    slug = factory.Sequence(lambda n: f"task_{n}")

    class Meta:
        model = TaskSpec
        django_get_or_create = ("slug",)

    class Params:
        system = factory.Trait(is_system=True, visibility=choices.Visibility.SYSTEM)


def _next_task_version(task: TaskSpec) -> int:
    if task.pk is None:
        return 1
    return (task.versions.aggregate(top=Max("version"))["top"] or 0) + 1


class TaskSpecVersionFactory(DjangoModelFactory[TaskSpecVersion]):
    """A task version; ``version`` defaults to the next free number and ``TaskSpec.latest_version`` follows."""

    task = factory.SubFactory(TaskSpecFactory)
    version = factory.LazyAttribute(lambda o: _next_task_version(o.task))
    content = factory.LazyFunction(samples.task_content)
    name = factory.LazyAttribute(lambda o: o.content["title"])

    class Meta:
        model = TaskSpecVersion

    @classmethod
    def _create(cls, model_class: type[TaskSpecVersion], *args: Any, **kwargs: Any) -> TaskSpecVersion:
        version = super()._create(model_class, *args, **kwargs)
        TaskSpec.objects.filter(pk=version.task_id, latest_version__lt=version.version).update(
            latest_version=version.version,
        )
        version.task.latest_version = max(version.task.latest_version, version.version)
        return version


class BlobFactory(DjangoModelFactory[Blob]):
    """A ``Blob`` index row only (no file is written; use ``blobs.put_bytes`` for that)."""

    sha256 = _sha_sequence("blob")
    size = 128
    codec = Blob.Codec.ZSTD
    media_type = "application/octet-stream"

    class Meta:
        model = Blob


# =====================================================================================
# Studies
# =====================================================================================


class StudyFactory(DjangoModelFactory[Study]):
    """An exploratory app study whose design names its own budget preset."""

    name = factory.Sequence(lambda n: f"Study {n}")
    slug = factory.Sequence(lambda n: f"study-{n}")
    resource_policy = factory.SubFactory(ResourcePolicyFactory)
    design = factory.LazyAttribute(lambda o: samples.design_content(resource_policy=o.resource_policy.ref))
    created_by = factory.SubFactory(UserFactory)

    class Meta:
        model = Study


class StudyFactorFactory(DjangoModelFactory[StudyFactor]):
    study = factory.SubFactory(StudyFactory)
    key = "model"
    role = choices.FactorRole.CROSSED
    levels = factory.LazyFunction(lambda: ["deepseek/deepseek-v4.1-flash"])
    position = factory.Sequence(int)

    class Meta:
        model = StudyFactor


class StudyPlanFactory(DjangoModelFactory[StudyPlan]):
    study = factory.SubFactory(StudyFactory)
    plan_hash = _sha_sequence("plan")
    design_hash = factory.LazyAttribute(lambda o: o.study.design_hash)
    payload_blob = _sha_sequence("plan-payload")
    n_conditions = 1
    n_units = 1
    n_cells = 1
    n_blocks = 1
    resolver_version = 1

    class Meta:
        model = StudyPlan


class StudyConditionFactory(DjangoModelFactory[StudyCondition]):
    plan = factory.SubFactory(StudyPlanFactory)
    study = factory.LazyAttribute(lambda o: o.plan.study)
    index = factory.Sequence(int)
    label = factory.LazyAttribute(lambda o: f"condition {o.index}")
    model = factory.SubFactory(LLMModelFactory)
    levels = factory.LazyAttribute(lambda o: {"model": o.model.model_id})
    treatment_hash = _sha_sequence("treatment")

    class Meta:
        model = StudyCondition


class StudyUnitFactory(DjangoModelFactory[StudyUnit]):
    """An app-mode unit (a task version); trait ``slot`` makes a slot-mode unit (a slot target)."""

    plan = factory.SubFactory(StudyPlanFactory)
    study = factory.LazyAttribute(lambda o: o.plan.study)
    index = factory.Sequence(int)
    task_version = factory.SubFactory(TaskSpecVersionFactory)
    slot_target = None
    unit_key = factory.LazyAttribute(
        lambda o: o.task_version.unit_key if o.task_version is not None else o.slot_target.unit_key,
    )
    unit_order = factory.Sequence(int)

    class Meta:
        model = StudyUnit

    class Params:
        slot = factory.Trait(task_version=None, slot_target=factory.SubFactory(f"{_FACTORIES}.SlotTargetFactory"))


class StudyCellFactory(DjangoModelFactory[StudyCell]):
    condition = factory.SubFactory(StudyConditionFactory)
    plan = factory.LazyAttribute(lambda o: o.condition.plan)
    study = factory.LazyAttribute(lambda o: o.condition.study)
    unit = factory.SubFactory(StudyUnitFactory, plan=factory.SelfAttribute("..plan"))
    repeat_index = 0
    block_index = 0
    seed = factory.Sequence(lambda n: 1000 + n)
    sequence = factory.Sequence(int)

    class Meta:
        model = StudyCell


class StudyPromptOverrideFactory(DjangoModelFactory[StudyPromptOverride]):
    study = factory.SubFactory(StudyFactory)
    owner_kind = choices.OverrideOwnerKind.PROMPT
    owner_ref_base = "brief-app-default@1#" + fake_sha("brief-v1")[:12]
    owner_ref_new = "brief-app-default@2#" + fake_sha("brief-v2")[:12]
    role = "brief"
    diff = "-Build it.\n+Build it carefully.\n"

    class Meta:
        model = StudyPromptOverride


class StudyScheduleFactory(DjangoModelFactory[StudySchedule]):
    study = factory.SubFactory(StudyFactory)
    action = choices.ScheduleAction.TOP_UP
    cron = "0 3 * * *"

    class Meta:
        model = StudySchedule


class StudyPostRunFactory(DjangoModelFactory[StudyPostRun]):
    study = factory.SubFactory(StudyFactory)
    trigger = choices.PostRunTrigger.MANUAL

    class Meta:
        model = StudyPostRun


# =====================================================================================
# Runs
# =====================================================================================


class GenerationRunFactory(DjangoModelFactory[GenerationRun]):
    """A pending ``app_agentic`` run of a fresh task version and model.

    Traits: ``tested`` (completed, ``outcome=tested``, usable, with a tree hash); ``slot_single_shot`` and
    ``slot_agent`` (a variant of a fresh slot target; parent run and task come from the target);
    ``reference`` (no model, ``reference_of=oracle``, waiting for evaluation).
    """

    kind = choices.RunKind.APP_AGENTIC
    task_version = factory.SubFactory(TaskSpecVersionFactory)
    model = factory.SubFactory(LLMModelFactory)
    created_by = factory.SubFactory(UserFactory)

    class Meta:
        model = GenerationRun

    class Params:
        tested = factory.Trait(
            status=choices.RunStatus.COMPLETED,
            outcome=choices.Outcome.TESTED,
            usable=True,
            tree_hash=_sha_sequence("tree"),
            completed_at=factory.LazyFunction(timezone.now),
        )
        slot_single_shot = factory.Trait(
            kind=choices.RunKind.SLOT_SINGLE_SHOT,
            slot_target=factory.SubFactory(f"{_FACTORIES}.SlotTargetFactory"),
            parent_run=factory.LazyAttribute(lambda o: o.slot_target.slot_set.parent_run),
            task_version=factory.LazyAttribute(lambda o: o.slot_target.slot_set.task_version),
        )
        slot_agent = factory.Trait(
            kind=choices.RunKind.SLOT_AGENT,
            slot_target=factory.SubFactory(f"{_FACTORIES}.SlotTargetFactory"),
            parent_run=factory.LazyAttribute(lambda o: o.slot_target.slot_set.parent_run),
            task_version=factory.LazyAttribute(lambda o: o.slot_target.slot_set.task_version),
        )
        reference = factory.Trait(
            kind=choices.RunKind.REFERENCE,
            model=None,
            reference_of=choices.ReferenceOf.ORACLE,
            status=choices.RunStatus.RUNNING,
            phase=choices.RunPhase.EVAL_QUEUED,
        )


def _slot_manifest_block(run: GenerationRun) -> dict[str, Any]:
    target = run.slot_target
    slot_set = target.slot_set
    return {
        "slot_set": str(slot_set.pk),
        "target": str(target.pk),
        "target_hash": target.target_hash,
        "parent_run": str(slot_set.parent_run_id),
        "parent_tree_hash": slot_set.frozen_tree_hash,
        "parent_model": slot_set.parent_model,
        "parent_harness": slot_set.parent_harness or "opencode",
        "parent_selection": {"rule": slot_set.selection.get("rule", "manual")},
        "address": target.address,
        "hole_mode": target.default_hole_mode,
        "keep_docstring": target.default_keep_docstring,
        "context_policy": target.default_context_policy,
        "resolved_context": {"files": [], "sha": fake_sha(f"context:{target.pk}")},
        "include_tests": False,
        "edit_policy": target.edit_policy,
        "allowlist": list(target.allowlist),
        "leak_score": 0.0,
        "validation": {},
    }


def run_manifest_content(run: GenerationRun) -> dict[str, Any]:
    """A manifest payload that agrees with ``run`` (protocol, kind, task, model and identity columns)."""
    task: dict[str, Any] | None = None
    if run.kind == choices.RunKind.APP_AGENTIC and run.task_version is not None:
        version = run.task_version
        variant = run.prose_variant or "standard"
        task = {
            "ref": version.pinned_ref,
            "structured_hash": version.structured_hash,
            "variant": variant,
            "prose_sha": version.prose_hashes.get(variant) or fake_sha(f"prose:{variant}"),
        }
    slot = _slot_manifest_block(run) if run.is_slot_variant else None
    # The run's own library rows, so the manifest's pinned refs name them (run/manifest agreement).
    rows = {
        "sampling": run.sampling_preset,
        "resources": run.resource_policy,
        "harness_preset": run.harness_preset,
        "starter": run.starter,
    }
    return samples.manifest_content(
        kind=run.kind,
        task=task,
        slot=slot,
        model_slug=run.model.model_id if run.model is not None else "none",
        identity=run.fingerprints,
        refs={key: row.pinned_ref for key, row in rows.items() if row is not None},
    )


class RunManifestFactory(DjangoModelFactory[RunManifest]):
    run = factory.SubFactory(GenerationRunFactory)
    manifest = factory.LazyAttribute(lambda o: run_manifest_content(o.run))

    class Meta:
        model = RunManifest


def _next_attempt(run: GenerationRun) -> int:
    if run.pk is None or run._state.adding:  # noqa: SLF001 - an unsaved run has no attempts
        return 1
    return (run.attempts.aggregate(top=Max("attempt"))["top"] or 0) + 1


class RunAttemptFactory(DjangoModelFactory[RunAttempt]):
    """The next attempt of ``run`` (1, 2, ...)."""

    run = factory.SubFactory(GenerationRunFactory)
    attempt = factory.LazyAttribute(lambda o: _next_attempt(o.run))
    executor_id = "executor-test"

    class Meta:
        model = RunAttempt


class RunFileFactory(DjangoModelFactory[RunFile]):
    run = factory.SubFactory(GenerationRunFactory)
    path = factory.Sequence(lambda n: f"backend/module_{n}.py")
    blob = _sha_sequence("run-file")
    size = 120
    origin = choices.FileOrigin.AUTHORED
    language = "python"
    loc = 10

    class Meta:
        model = RunFile


class RunEvaluationFactory(DjangoModelFactory[RunEvaluation]):
    """A pending evaluation; trait ``measured`` gives a frozen row with a canned ``EvalResult``."""

    run = factory.SubFactory(GenerationRunFactory, tested=True)
    eval_suite = factory.SubFactory(EvalSuiteFactory)
    eval_level = choices.EvalLevel.SECURITY

    class Meta:
        model = RunEvaluation

    class Params:
        measured = factory.Trait(
            status=choices.EvaluationStatus.MEASURED,
            result=factory.LazyFunction(samples.eval_result_content),
            adherence=factory.LazyFunction(samples.adherence_content),
            stages=factory.LazyFunction(
                lambda: {stage: {"status": "ok"} for stage in ("lock", "build", "start", "tester", "adherence")},
            ),
        )


class EvalBaselineFactory(DjangoModelFactory[EvalBaseline]):
    starter = factory.SubFactory(StarterFactory)
    task_version = factory.SubFactory(TaskSpecVersionFactory)
    auth_mode = choices.AuthMode.CONTRACT_BEARER
    eval_suite = factory.SubFactory(EvalSuiteFactory)
    reference_run = factory.SubFactory(
        GenerationRunFactory,
        reference=True,
        reference_of=choices.ReferenceOf.STARTER_BASELINE,
        task_version=factory.SelfAttribute("..task_version"),
    )
    agent_pass_rate = 0.25
    checks_passed = factory.LazyFunction(lambda: ["health"])

    class Meta:
        model = EvalBaseline


class GenerationArtifactFactory(DjangoModelFactory[GenerationArtifact]):
    run = factory.SubFactory(GenerationRunFactory)
    stage = factory.Sequence(lambda n: f"agent_call_{n + 1:04d}")
    request_payload = factory.LazyFunction(lambda: {"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    response_payload = factory.LazyFunction(lambda: {"choices": [{"message": {"content": "ok"}}]})
    prompt_tokens = 10
    completion_tokens = 5

    class Meta:
        model = GenerationArtifact


# =====================================================================================
# Slot mode and patterns
# =====================================================================================


class RunSymbolIndexFactory(DjangoModelFactory[RunSymbolIndex]):
    run = factory.SubFactory(GenerationRunFactory, tested=True)
    index_version = 1
    blob = _sha_sequence("symbol-index")
    stats = factory.LazyFunction(lambda: {"files": 2, "symbols": 3, "endpoints": 1, "components": 1})

    class Meta:
        model = RunSymbolIndex


class SlotSetFactory(DjangoModelFactory[SlotSet]):
    """A parent: a tested app run, its task, model and tree hash, and its symbol index."""

    name = factory.Sequence(lambda n: f"Parent {n}")
    parent_run = factory.SubFactory(GenerationRunFactory, tested=True)
    task_version = factory.LazyAttribute(lambda o: o.parent_run.task_version)
    parent_model = factory.LazyAttribute(lambda o: o.parent_run.model.model_id)
    parent_harness = "opencode"
    selection = factory.LazyFunction(lambda: {"rule": "manual", "picked_by": "test"})
    frozen_tree_hash = factory.LazyAttribute(lambda o: o.parent_run.tree_hash)
    parent_manifest_hash = _sha_sequence("parent-manifest")
    index = factory.SubFactory(RunSymbolIndexFactory, run=factory.SelfAttribute("..parent_run"))

    class Meta:
        model = SlotSet


class SlotTargetFactory(DjangoModelFactory[SlotTarget]):
    """A whole-file target; ``path`` and ``content_sha`` are parameters that fill address and shas."""

    slot_set = factory.SubFactory(SlotSetFactory)
    label = factory.LazyAttribute(lambda o: o.path)
    address = factory.LazyAttribute(lambda o: {"kind": "file", "path": o.path, "content_sha": o.content_sha})
    language = "python"
    original_sha = factory.LazyAttribute(lambda o: o.content_sha)
    original_blob = factory.LazyAttribute(lambda o: o.content_sha)
    allowlist = factory.LazyAttribute(lambda o: [o.path])

    class Meta:
        model = SlotTarget

    class Params:
        path = factory.Sequence(lambda n: f"backend/routes/module_{n}.py")
        content_sha = _sha_sequence("slot-original")


class SlotVariantResultFactory(DjangoModelFactory[SlotVariantResult]):
    run = factory.SubFactory(GenerationRunFactory, slot_single_shot=True)
    slot_target = factory.LazyAttribute(lambda o: o.run.slot_target)
    parse_status = choices.ParseStatus.OK
    replacement_blob = factory.LazyAttribute(lambda o: o.replacement_sha)
    replacement_sha = _sha_sequence("replacement")
    normalized_sha = _sha_sequence("normalized")
    normalizer_version = 1

    class Meta:
        model = SlotVariantResult


class PatternRunFactory(DjangoModelFactory[PatternRun]):
    study = factory.SubFactory(StudyFactory)
    detectors = factory.LazyFunction(list)
    detector_set_hash = _sha_sequence("detector-set")
    normalizer_version = 1

    class Meta:
        model = PatternRun


class PatternHitFactory(DjangoModelFactory[PatternHit]):
    """An ``absent`` verdict; trait ``error`` gives an error with its reason (never reported as absent)."""

    pattern_run = factory.SubFactory(PatternRunFactory)
    run = factory.SubFactory(GenerationRunFactory, slot_single_shot=True)
    detector = factory.SubFactory(PatternDetectorFactory)
    feature_key = factory.LazyAttribute(lambda o: o.detector.feature_key)
    scope = choices.HitScope.SPAN
    status = choices.HitStatus.ABSENT

    class Meta:
        model = PatternHit

    class Params:
        error = factory.Trait(status=choices.HitStatus.ERROR, error_reason=choices.HitErrorReason.TOOL_ERROR)
        present = factory.Trait(
            status=choices.HitStatus.PRESENT,
            matches=factory.LazyFunction(lambda: [{"path": "backend/app.py", "start_line": 3, "end_line": 3}]),
        )


class VariantClusterFactory(DjangoModelFactory[VariantCluster]):
    pattern_run = factory.SubFactory(PatternRunFactory)
    slot_target = factory.SubFactory(SlotTargetFactory)
    method = choices.ClusterMethod.EXACT
    key = _sha_sequence("cluster")
    size = 1
    representative = factory.SubFactory(
        GenerationRunFactory,
        slot_single_shot=True,
        slot_target=factory.SelfAttribute("..slot_target"),
    )

    class Meta:
        model = VariantCluster


class VariantClusterMemberFactory(DjangoModelFactory[VariantClusterMember]):
    """A member of ``cluster``; defaults to its representative."""

    cluster = factory.SubFactory(VariantClusterFactory)
    run = factory.LazyAttribute(lambda o: o.cluster.representative)

    class Meta:
        model = VariantClusterMember


class ClusterLabelFactory(DjangoModelFactory[ClusterLabel]):
    slot_target = factory.SubFactory(SlotTargetFactory)
    normalized_sha = _sha_sequence("label-normalized")
    detector = factory.SubFactory(PatternDetectorFactory, manual=True)
    value = choices.LabelValue.PRESENT
    labeler = factory.SubFactory(UserFactory)

    class Meta:
        model = ClusterLabel


class DetectorAuditFactory(DjangoModelFactory[DetectorAudit]):
    pattern_run = factory.SubFactory(PatternRunFactory)
    feature_key = "py-sql-fstring"
    sample = factory.LazyFunction(lambda: {"hits": [], "non_hits": [], "seed": 1})

    class Meta:
        model = DetectorAudit


# =====================================================================================
# Spend
# =====================================================================================


class SpendLedgerEntryFactory(DjangoModelFactory[SpendLedgerEntry]):
    kind = choices.LedgerKind.USAGE
    user = factory.SubFactory(UserFactory)
    amount_usd = Decimal("0.01")
    list_cost_usd = Decimal("0.01")
    source = choices.LedgerSource.MANUAL
    dedupe_key = factory.Sequence(lambda n: f"test:{n}")

    class Meta:
        model = SpendLedgerEntry


class SpendCapFactory(DjangoModelFactory[SpendCap]):
    """A daily 10 USD cap on a user."""

    scope = choices.CapScope.USER
    user = factory.SubFactory(UserFactory)
    period = choices.CapPeriod.DAY
    unit = choices.CapUnit.USD
    limit = Decimal(10)

    class Meta:
        model = SpendCap


class CostPriorFactory(DjangoModelFactory[CostPrior]):
    key = factory.Sequence(lambda n: f"app_agentic|opencode|model-{n}|M|frugal")
    n = 12
    cost_p10 = 0.05
    cost_p50 = 0.1
    cost_p90 = 0.2
    wall_s_p50 = 300.0
    wall_s_p90 = 600.0

    class Meta:
        model = CostPrior
