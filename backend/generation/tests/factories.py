"""Re-export of ``backend.generation.testing.factories`` (the v3 factories; spec section 2.1).

Kept so that ``from backend.generation.tests.factories import ...`` stays the import path for tests. The v2
factories (``GenerationJobFactory``, ``ExperimentFactory``, ...) are gone with their models; WP-02b re-points
their consumers to the v3 names listed here.
"""

from backend.generation.testing.factories import BlobFactory
from backend.generation.testing.factories import ClusterLabelFactory
from backend.generation.testing.factories import CostPriorFactory
from backend.generation.testing.factories import DetectorAuditFactory
from backend.generation.testing.factories import EvalBaselineFactory
from backend.generation.testing.factories import EvalSuiteFactory
from backend.generation.testing.factories import GenerationArtifactFactory
from backend.generation.testing.factories import GenerationRunFactory
from backend.generation.testing.factories import GuidanceFactory
from backend.generation.testing.factories import HarnessPresetFactory
from backend.generation.testing.factories import PatternDetectorFactory
from backend.generation.testing.factories import PatternHitFactory
from backend.generation.testing.factories import PatternRunFactory
from backend.generation.testing.factories import PromptSetFactory
from backend.generation.testing.factories import PromptTemplateFactory
from backend.generation.testing.factories import ResourcePolicyFactory
from backend.generation.testing.factories import RunAttemptFactory
from backend.generation.testing.factories import RunEvaluationFactory
from backend.generation.testing.factories import RunFileFactory
from backend.generation.testing.factories import RunManifestFactory
from backend.generation.testing.factories import RunSymbolIndexFactory
from backend.generation.testing.factories import SamplingPresetFactory
from backend.generation.testing.factories import SlotSetFactory
from backend.generation.testing.factories import SlotTargetFactory
from backend.generation.testing.factories import SlotVariantResultFactory
from backend.generation.testing.factories import SpendCapFactory
from backend.generation.testing.factories import SpendLedgerEntryFactory
from backend.generation.testing.factories import StackProfileFactory
from backend.generation.testing.factories import StarterFactory
from backend.generation.testing.factories import StudyCellFactory
from backend.generation.testing.factories import StudyConditionFactory
from backend.generation.testing.factories import StudyFactorFactory
from backend.generation.testing.factories import StudyFactory
from backend.generation.testing.factories import StudyPlanFactory
from backend.generation.testing.factories import StudyPostRunFactory
from backend.generation.testing.factories import StudyPromptOverrideFactory
from backend.generation.testing.factories import StudyScheduleFactory
from backend.generation.testing.factories import StudyUnitFactory
from backend.generation.testing.factories import StyleDirectiveFactory
from backend.generation.testing.factories import TaskSpecFactory
from backend.generation.testing.factories import TaskSpecVersionFactory
from backend.generation.testing.factories import VariantClusterFactory
from backend.generation.testing.factories import VariantClusterMemberFactory
from backend.generation.testing.factories import run_manifest_content

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
