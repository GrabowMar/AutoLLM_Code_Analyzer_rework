"""v3 generation models (spec section 3). Every model is importable from ``backend.generation.models``."""

from backend.generation.models import choices
from backend.generation.models.base import ImmutableVersionError
from backend.generation.models.library import Blob
from backend.generation.models.library import EvalSuite
from backend.generation.models.library import Guidance
from backend.generation.models.library import HarnessPreset
from backend.generation.models.library import PatternDetector
from backend.generation.models.library import PromptSet
from backend.generation.models.library import PromptTemplate
from backend.generation.models.library import ResourcePolicy
from backend.generation.models.library import SamplingPreset
from backend.generation.models.library import StackProfile
from backend.generation.models.library import Starter
from backend.generation.models.library import StyleDirective
from backend.generation.models.library import TaskSpec
from backend.generation.models.library import TaskSpecVersion
from backend.generation.models.library import VersionedEntity
from backend.generation.models.run import EvalBaseline
from backend.generation.models.run import GenerationArtifact
from backend.generation.models.run import GenerationRun
from backend.generation.models.run import RunAttempt
from backend.generation.models.run import RunEvaluation
from backend.generation.models.run import RunFile
from backend.generation.models.run import RunManifest
from backend.generation.models.slot import ClusterLabel
from backend.generation.models.slot import DetectorAudit
from backend.generation.models.slot import PatternHit
from backend.generation.models.slot import PatternRun
from backend.generation.models.slot import RunSymbolIndex
from backend.generation.models.slot import SlotSet
from backend.generation.models.slot import SlotTarget
from backend.generation.models.slot import SlotVariantResult
from backend.generation.models.slot import VariantCluster
from backend.generation.models.slot import VariantClusterMember
from backend.generation.models.spend import CostPrior
from backend.generation.models.spend import SpendCap
from backend.generation.models.spend import SpendLedgerEntry
from backend.generation.models.study import Study
from backend.generation.models.study import StudyCell
from backend.generation.models.study import StudyCondition
from backend.generation.models.study import StudyFactor
from backend.generation.models.study import StudyPlan
from backend.generation.models.study import StudyPostRun
from backend.generation.models.study import StudyPromptOverride
from backend.generation.models.study import StudySchedule
from backend.generation.models.study import StudyUnit

__all__ = [
    "LIBRARY_MODELS",
    "Blob",
    "ClusterLabel",
    "CostPrior",
    "DetectorAudit",
    "EvalBaseline",
    "EvalSuite",
    "GenerationArtifact",
    "GenerationRun",
    "Guidance",
    "HarnessPreset",
    "ImmutableVersionError",
    "PatternDetector",
    "PatternHit",
    "PatternRun",
    "PromptSet",
    "PromptTemplate",
    "ResourcePolicy",
    "RunAttempt",
    "RunEvaluation",
    "RunFile",
    "RunManifest",
    "RunSymbolIndex",
    "SamplingPreset",
    "SlotSet",
    "SlotTarget",
    "SlotVariantResult",
    "SpendCap",
    "SpendLedgerEntry",
    "StackProfile",
    "Starter",
    "Study",
    "StudyCell",
    "StudyCondition",
    "StudyFactor",
    "StudyPlan",
    "StudyPostRun",
    "StudyPromptOverride",
    "StudySchedule",
    "StudyUnit",
    "StyleDirective",
    "TaskSpec",
    "TaskSpecVersion",
    "VariantCluster",
    "VariantClusterMember",
    "VersionedEntity",
    "choices",
    "default_stack_slug",
]

# Library kind -> model (``TaskSpecVersion`` is the versioned row of kind "task").
LIBRARY_MODELS: dict[str, type] = {
    model.KIND: model
    for model in (
        TaskSpecVersion,
        StackProfile,
        Starter,
        StyleDirective,
        Guidance,
        PromptTemplate,
        PromptSet,
        HarnessPreset,
        SamplingPreset,
        ResourcePolicy,
        EvalSuite,
        PatternDetector,
    )
}

# The v2 default stack slug. Kept only because historical migration 0016_profile_default_stack references
# ``backend.generation.models.default_stack_slug`` as a field default; v3 code never calls it.
_V2_DEFAULT_STACK = "flask-react"


def default_stack_slug() -> str:
    """Historical field default of the deleted ``GenerationProfile.scaffolding_slug`` (migration 0016)."""
    return _V2_DEFAULT_STACK
