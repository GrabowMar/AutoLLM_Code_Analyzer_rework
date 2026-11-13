"""Schemas of the progress, responses browser, HTTP log and results endpoints (owner D2).

Every class mirrors a type of ``frontend/src/lib/api/types/human-eval.ts`` field
for field (names in the comments). Class names carry a ``He`` prefix where a
plain name could collide with another module's schema in the OpenAPI components.

The results schemas (:class:`TaskResultsSchema` and below) validate what
``services/analysis.compute_results`` returns against the TS ``TaskResults``
contract. They are lenient only about representation: numbers keep their int /
float type (:data:`Num`), ids and keys accept UUIDs or ints and are sent as
strings (:data:`Key`), timestamps accept datetimes (:data:`IsoStr`). Keys the TS
type marks optional (``?``) default to None and are dropped from the output
when the computation did not set them (views use ``exclude_unset``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated
from typing import Any
from typing import Literal

from ninja import Schema
from pydantic import BeforeValidator
from pydantic import Field


def _to_str(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (uuid.UUID, int)):
        return str(value)
    return value


def _to_iso(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


#: A TS ``number``: int stays int, float stays float.
Num = int | float
#: A TS ``string`` id / key (UUIDs and ints are sent as strings).
Key = Annotated[str, BeforeValidator(_to_str)]
#: A TS ISO 8601 ``string`` (datetimes are formatted).
IsoStr = Annotated[str, BeforeValidator(_to_iso)]


# ---------------------------------------------------------------------------
# Progress (TS TaskProgress)
# ---------------------------------------------------------------------------


class HeTaskCountsSchema(Schema):
    """TS ``TaskCounts``."""

    apps: int
    active_apps: int
    items: int
    participants: int
    participants_active: int
    participants_completed: int
    assignments_submitted: int
    assignments_in_progress: int
    assignments_skipped: int
    target_ratings: int | None


class CoverageBinSchema(Schema):
    ratings: int
    items: int


class TimelinePointSchema(Schema):
    date: str
    submitted: int


class TaskProgressSchema(Schema):
    """TS ``TaskProgress``."""

    counts: HeTaskCountsSchema
    assignments_by_status: dict[str, int]
    participants_by_status: dict[str, int]
    coverage_histogram: list[CoverageBinSchema]
    timeline: list[TimelinePointSchema]
    median_minutes_per_item: float | None


# ---------------------------------------------------------------------------
# Assignments browser (TS AssignmentSummary / AssignmentDetail)
# ---------------------------------------------------------------------------


class AssignmentParticipantRefSchema(Schema):
    id: uuid.UUID
    label: str
    kind: str


class AssignmentAppRefSchema(Schema):
    id: uuid.UUID
    label: str
    slot: Literal["A", "B", "single"]


class AssignmentSummarySchema(Schema):
    """TS ``AssignmentSummary``."""

    id: uuid.UUID
    participant: AssignmentParticipantRefSchema
    item_id: uuid.UUID
    apps: list[AssignmentAppRefSchema]
    status: str
    position: int
    swapped: bool
    skip_reason: str
    started_at: dt.datetime | None
    submitted_at: dt.datetime | None
    active_seconds: int
    wall_seconds: int
    excluded: bool
    exclusion_reason: str
    excluded_at: dt.datetime | None
    observation_count: int
    http_request_count: int
    quality_flags: list[str]


class AssignmentExclusionInputSchema(Schema):
    """TS ``AssignmentExclusionInput``."""

    excluded: bool
    exclusion_reason: str = ""


class HeAttachmentSchema(Schema):
    """TS ``ObservationAttachment`` (researcher URL)."""

    id: uuid.UUID
    content_type: str
    size: int
    original_name: str
    url: str
    created_at: dt.datetime


class ObservationParticipantRefSchema(Schema):
    id: uuid.UUID
    label: str


class ResearcherObservationSchema(Schema):
    """TS ``ResearcherObservation`` (``Observation`` + participant, app id and label)."""

    id: uuid.UUID
    assignment_id: uuid.UUID
    app_slot: str
    block_id: str
    kind: str
    title: str
    description: str
    steps: str
    location: str
    category: str
    severity: str
    cwe: str
    checklist_item_id: str
    attachments: list[HeAttachmentSchema]
    created_at: dt.datetime
    updated_at: dt.datetime
    participant: ObservationParticipantRefSchema
    app_id: uuid.UUID
    app_label: str


class ResponseEntrySchema(Schema):
    answers: dict[str, Any]
    updated_at: dt.datetime


class FindingVerdictRecordSchema(Schema):
    """TS ``FindingVerdictRecord``."""

    finding_key: str
    verdict: str
    note: str
    finding_snapshot: dict[str, Any]
    updated_at: dt.datetime


class AssignmentDetailSchema(AssignmentSummarySchema):
    """TS ``AssignmentDetail``: the summary plus the frozen context, answers, observations and verdicts."""

    context: dict[str, Any]
    responses: dict[str, ResponseEntrySchema]
    observations: list[ResearcherObservationSchema]
    finding_verdicts: list[FindingVerdictRecordSchema]
    skip_note: str


# ---------------------------------------------------------------------------
# HTTP log (TS HttpExchangeRecord)
# ---------------------------------------------------------------------------


class HttpExchangeRecordSchema(Schema):
    """TS ``HttpExchangeRecord``."""

    id: uuid.UUID
    assignment_id: uuid.UUID
    participant_label: str
    app_label: str
    method: str
    path: str
    request_headers: dict[str, str]
    request_body: str
    status_code: int | None
    response_headers: dict[str, str]
    response_body: str
    response_truncated: bool
    elapsed_ms: int | None
    error: str
    created_at: dt.datetime


# ---------------------------------------------------------------------------
# Results (TS TaskResults)
# ---------------------------------------------------------------------------


class CiSchema(Schema):
    low: Num | None
    high: Num | None


class ResultWarningSchema(Schema):
    code: str
    message: str
    block_id: Key | None = None


class ResultGroupSchema(Schema):
    key: Key
    label: str
    n_apps_pool: int
    n_apps_rated: int
    n_apps_unevaluable: int


class ResultAppSchema(Schema):
    app_id: Key
    label: str
    group: Key
    model_name: str
    condition_label: str
    app_slug: str
    run_id: Key | None
    n_submitted: int
    n_unavailable: int


class AgreementSchema(Schema):
    alpha: Num | None
    level: Literal["nominal", "ordinal", "interval"]
    percent_agreement: Num | None
    n_units: int
    n_values: int


class AppScoreSchema(Schema):
    app_id: Key
    label: str
    group: Key
    n: int
    mean: Num | None
    sd: Num | None
    median: Num | None
    na_count: int


class FloorScoreSchema(Schema):
    mean: Num | None
    ci: CiSchema | None
    n_apps: int


class GroupScoreSchema(Schema):
    group: Key
    label: str
    n_apps: int
    n_ratings: int
    mean: Num | None
    ci: CiSchema | None
    floor: FloorScoreSchema | None


class ResultScaleSchema(Schema):
    min: int
    max: int
    step: int | None = None
    min_label: str | None = None
    max_label: str | None = None
    labels: dict[str, str] | None = None


class RatingCriterionResultSchema(Schema):
    criterion_id: Key
    label: str
    scale: ResultScaleSchema
    per_app: list[AppScoreSchema]
    per_group: list[GroupScoreSchema]
    agreement: AgreementSchema


class CompositeResultSchema(Schema):
    per_app: list[AppScoreSchema]
    per_group: list[GroupScoreSchema]


class RatingBlockResultSchema(Schema):
    type: Literal["rating"]
    block_id: Key
    title: str
    criteria: list[RatingCriterionResultSchema]
    composite: CompositeResultSchema


class RequirementAppResultSchema(Schema):
    app_id: Key
    label: str
    group: Key
    n_raters: int
    compliance: Num | None
    compliance_strict: Num | None
    by_source: dict[str, Num | None]
    blocked: int
    cannot_test: int
    conformance_pass_rate: Num | None
    conformance_endpoint_pass_rate: Num | None
    conformance_status: str | None


class RequirementResultSchema(Schema):
    req_id: Key
    source: str
    text: str
    n_ratings: int
    met: int
    partial: int
    not_met: int
    blocked: int
    cannot_test: int


class VsConformanceSchema(Schema):
    scope: Literal["endpoints", "overall"]
    rho: Num | None
    ci: CiSchema | None
    n: int
    excluded_apps: list[str]
    note: str


class EndpointAgreementSchema(Schema):
    n: int
    kappa: Num | None
    both_pass: int
    human_only: int
    auto_only: int
    both_fail: int
    n_unmatched: int


class RequirementsBlockResultSchema(Schema):
    type: Literal["requirements"]
    block_id: Key
    title: str
    per_app: list[RequirementAppResultSchema]
    per_group: list[GroupScoreSchema]
    per_requirement: list[RequirementResultSchema]
    agreement: AgreementSchema
    vs_conformance: list[VsConformanceSchema]
    endpoint_agreement: EndpointAgreementSchema | None


class PrecisionRowSchema(Schema):
    key: Key
    label: str
    tp: Num
    fp: Num
    unsure: Num
    n_findings: int
    n_apps: int
    n_split: int
    precision: Num | None
    ci: CiSchema | None
    majority_precision: Num | None
    weighted: bool


class ChecklistGroupResultSchema(Schema):
    group: Key
    label: str
    n_apps: int
    n: int
    vulnerable: int
    not_vulnerable: int
    not_tested: int
    vulnerable_share: Num | None
    ci: CiSchema | None


class ChecklistItemResultSchema(Schema):
    item_id: Key
    label: str
    category: str
    per_group: list[ChecklistGroupResultSchema]
    agreement: AgreementSchema


class ReportsAppResultSchema(Schema):
    app_id: Key
    label: str
    group: Key
    total: int
    by_severity: dict[str, int]
    by_category: dict[str, int]


class ReportsGroupResultSchema(Schema):
    group: Key
    label: str
    n_apps: int
    total: int
    per_rater_mean: Num | None
    ci: CiSchema | None
    by_severity: dict[str, int]


class ReportsResultSchema(Schema):
    per_app: list[ReportsAppResultSchema]
    per_group: list[ReportsGroupResultSchema]


class FindingReviewResultSchema(Schema):
    n_findings: int
    n_verdicts: int
    overall: PrecisionRowSchema
    per_tool: list[PrecisionRowSchema]
    per_tool_severity: list[PrecisionRowSchema]
    per_group: list[PrecisionRowSchema]
    agreement: AgreementSchema


class ConsoleAppResultSchema(Schema):
    app_id: Key
    label: str
    requests: int


class ConsoleResultSchema(Schema):
    requests: int
    participants_using: int
    per_app: list[ConsoleAppResultSchema]


class SecurityBlockResultSchema(Schema):
    type: Literal["security"]
    block_id: Key
    title: str
    checklist: list[ChecklistItemResultSchema]
    reports: ReportsResultSchema
    finding_review: FindingReviewResultSchema | None
    console: ConsoleResultSchema | None


class ScenarioGroupResultSchema(Schema):
    group: Key
    label: str
    n_apps: int
    n: int
    success_rate: Num | None
    ci: CiSchema | None
    median_seconds: Num | None


class ScenarioResultSchema(Schema):
    scenario_id: Key
    title: str
    n: int
    success: int
    partial: int
    failure: int
    not_attempted: int
    success_rate: Num | None
    median_seconds: Num | None
    ease_mean: Num | None
    per_group: list[ScenarioGroupResultSchema]


class ScenariosBlockResultSchema(Schema):
    type: Literal["scenarios"]
    block_id: Key
    title: str
    scenarios: list[ScenarioResultSchema]


class QuestionResultSchema(Schema):
    question_id: Key
    label: str
    kind: str
    n: int
    mean: Num | None
    distribution: dict[str, Num]


class QuestionnaireBlockResultSchema(Schema):
    type: Literal["questionnaire"]
    block_id: Key
    title: str
    instrument: str
    score: CompositeResultSchema | None
    questions: list[QuestionResultSchema]


class BugsAppResultSchema(Schema):
    app_id: Key
    label: str
    group: Key
    n_raters: int
    total: int
    per_rater_mean: Num | None
    by_severity: dict[str, int]
    none_found: int


class BugsBlockResultSchema(Schema):
    type: Literal["bugs"]
    block_id: Key
    title: str
    per_app: list[BugsAppResultSchema]
    per_group: list[GroupScoreSchema]


class BtEntrySchema(Schema):
    key: Key
    label: str
    component: Key
    strength: Num | None
    log_strength: Num | None
    ci: CiSchema | None
    wins: Num
    losses: Num
    ties: Num
    comparisons: Num
    rank: int | None


class PositionBiasSchema(Schema):
    left: int
    right: int
    ties: int
    p_value: Num | None


class PairwiseCriterionResultSchema(Schema):
    criterion_id: Key
    label: str
    n_comparisons: int
    by_group: list[BtEntrySchema]
    by_app: list[BtEntrySchema]
    position_bias: PositionBiasSchema
    agreement: AgreementSchema


class PairwiseBlockResultSchema(Schema):
    type: Literal["pairwise"]
    block_id: Key
    title: str
    criteria: list[PairwiseCriterionResultSchema]


BlockResultSchema = Annotated[
    RatingBlockResultSchema
    | RequirementsBlockResultSchema
    | SecurityBlockResultSchema
    | ScenariosBlockResultSchema
    | QuestionnaireBlockResultSchema
    | BugsBlockResultSchema
    | PairwiseBlockResultSchema,
    Field(discriminator="type"),
]


class ParticipantQualitySchema(Schema):
    participant_id: Key
    label: str
    kind: str
    excluded: bool
    submitted: int
    skipped: int
    median_seconds: Num | None
    speeding: int
    straightlining: int
    attention_failed: int
    flags: list[str]


class ContrastSchema(Schema):
    block_id: Key
    metric: str
    label: str
    group: Key
    group_label: str
    baseline: Key
    baseline_label: str
    diff: Num | None
    ci: CiSchema | None
    paired: bool
    n_cells: int
    p_value: Num | None
    p_adjusted: Num | None
    primary: bool


class HumanVsAutomatedSchema(Schema):
    metric: str
    label: str
    rho: Num | None
    ci: CiSchema | None
    n: int
    note: str


class ResultCountsSchema(Schema):
    participants: int
    participants_included: int
    assignments_submitted: int
    assignments_included: int
    assignments_skipped: int
    assignments_excluded: int


class TaskResultsSchema(Schema):
    """TS ``TaskResults``."""

    task_id: Key
    generated_at: IsoStr
    provisional: bool
    p_values_withheld: bool
    confirmatory: bool
    deviations: list[str]
    rule_exclusions: dict[str, int]
    group_by: Literal["condition", "model", "app"]
    include_excluded: bool
    counts: ResultCountsSchema
    warnings: list[ResultWarningSchema]
    groups: list[ResultGroupSchema]
    apps: list[ResultAppSchema]
    blocks: list[BlockResultSchema]
    quality: list[ParticipantQualitySchema]
    contrasts: list[ContrastSchema]
    human_vs_automated: list[HumanVsAutomatedSchema]
