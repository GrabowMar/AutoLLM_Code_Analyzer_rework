"""Results computation (owner D1): per-block statistics, agreement, Bradley-Terry, contrasts, warnings.

:func:`compute_results` returns TS ``TaskResults`` (``types/human-eval.ts``). It reads
only frozen data: ``EvalTaskApp.snapshot`` / ``finding_frame``, ``Assignment.context``
and the stored answers, never live run, finding or conformance rows. The
normalized ``Finding.severity`` is never read; automated findings are grouped by
the tool's own label (``tool_severity``, "unrated" when blank).

Data included
    Submitted assignments of non-preview participants. Unless ``include_excluded``:
    no manually excluded assignment or participant, and no assignment removed by a
    pre-registered rule (``design.analysis.exclusion_rules``, computed by
    ``services/quality.py`` and reported in ``rule_exclusions``, never written to rows).
    ``counts.assignments_excluded`` / ``rule_exclusions`` describe the plan's exclusions in
    both views; ``include_excluded`` only stops them from being applied.

Unit of analysis: the app
    Every per-group value is the mean of per-app values (an app's value is the mean over
    its raters, or its share of rater judgements); CIs are seeded percentile bootstraps
    over apps (2000 draws), null below three apps. Rater-level counts stay descriptive.
    An app is *unevaluable* when it has no included submission and at least one
    ``app_broken`` skip (attributed to the failing slot via ``context.skip_hosting`` when
    known). ``GroupScore.floor`` puts unevaluable apps at the floor (rating scale minimum,
    composite 0, compliance 0); with ``design.analysis.unevaluable_policy == "floor"`` the
    primary ``mean`` / ``ci`` / ``n_apps`` are the floored ones.

Agreement
    Krippendorff's alpha: rating criteria over apps (ordinal up to 10 scale points, else
    interval); requirements over (app, requirement) ordinal (not_met = blocked < partial <
    met, cannot_test excluded); security checklist items over apps, nominal (not_tested
    excluded); finding review over findings, nominal tp/fp (unsure excluded); pairwise per
    criterion over items in canonical orientation (+ = item.app_a better), ordinal.

Pairwise
    Displayed sides map back to apps with ``assignment.swapped``. Bradley-Terry with a
    prior of 0.5 win + 0.5 loss against a fixed virtual opponent; ``by_app`` per connected
    component (rank only within one), CI by resampling items with all their comparisons;
    ``by_group`` on between-group comparisons, CI by resampling apps within each group
    (1000 draws). Position bias: displayed-left vs right wins, exact binomial p.

Contrasts (``group_by == "condition"``)
    Every block metric of the analysis-plan vocabulary, each non-baseline condition vs the
    baseline (``pairing.baseline_condition_id`` or the study's): paired on common
    (app_slug, repeat_index) cells when at least three exist (bootstrap CI of the mean
    delta, Wilcoxon signed-rank p), else unpaired (bootstrap CI of the difference, no p).
    Holm across all contrasts. Pairwise metrics use each app's preference share
    ((wins + ties / 2) / comparisons).

Contrast deltas are rounded to ``DELTA_DIGITS`` decimals first, so float residue between equal
means is a tie (dropped by the signed-rank test), not a tiny difference.

Confirmatory gate (``deviations``)
    The planned sample is checked against every item built at first open (items deactivated
    later included); a participant with data deleted after opening (``protocol_log`` field
    ``participant_deleted:<number>``) is a post-hoc exclusion like a late manual one.

Validity warnings (besides low n / agreement / coverage)
    ``shared_app_state``: reset "none" and an app with several submissions, or reset
    "per_assignment" and an included submission whose hosting provenance has
    ``prior_users > 0`` (the reset could not run). ``review_items_differ``: raters of one app
    were shown different requirement / finding lists, or finding lists that differ from the
    app's frozen ``finding_sample`` (content deleted after the task opened).

P-values are withheld (null) while the task is provisional (not closed).
"""

from __future__ import annotations

import math
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Count
from django.utils import timezone

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.models import FindingVerdict
from backend.human_eval.models import HttpExchange
from backend.human_eval.models import Observation
from backend.human_eval.models import Participant
from backend.human_eval.models import Response
from backend.human_eval.services import quality as quality_service
from backend.human_eval.services import snapshot as snapshot_service
from backend.human_eval.services import stats
from backend.human_eval.services import studies
from backend.human_eval.services.design import block_by_id
from backend.human_eval.services.design import merged_settings
from backend.human_eval.services.design import scale_values
from backend.human_eval.services.errors import invalid
from backend.human_eval.services.instruments import INSTRUMENTS
from backend.human_eval.services.instruments import instrument_questions
from backend.human_eval.services.instruments import score as instrument_score
from backend.statistics.services.inference import cohens_kappa
from backend.statistics.services.inference import holm
from backend.statistics.services.inference import spearman
from backend.statistics.services.inference import spearman_bootstrap_ci

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Collection
    from collections.abc import Iterable

GROUP_BY = ("condition", "model", "app")
LOW_AGREEMENT_ALPHA = 0.667
LOW_AGREEMENT_MIN_UNITS = 5
LOW_N_RATERS = 3
LOW_N_APPS = 5
POSITION_BIAS_ALPHA = 0.05
ORDINAL_MAX_POINTS = 10
#: Paired contrast deltas are rounded to this many decimals before the signed-rank test (float residue).
DELTA_DIGITS = 12
UNAVAILABLE_REASONS = ("app_broken", "platform_unavailable")
REQUIREMENT_SCORES = {"met": 1.0, "partial": 0.5, "not_met": 0.0, "blocked": 0.0}
REQUIREMENT_ORDINAL = {"met": 2, "partial": 1, "not_met": 0, "blocked": 0}
REQUIREMENT_VERDICTS = ("met", "partial", "not_met", "blocked", "cannot_test")
ENDPOINT_SOURCES = ("api", "admin_api")
ENDPOINT_CHECK_CATEGORY = {"api": "api", "admin_api": "admin"}
_SOURCE_PREFIXES = {"f": "frontend", "ep": "api", "aep": "admin_api", "c": "custom"}
CHECKLIST_OUTCOMES = ("vulnerable", "not_vulnerable", "not_tested")
SCENARIO_OUTCOMES = ("success", "partial", "failure", "not_attempted")
FINDING_VOTES = ("tp", "fp", "unsure")
#: ``task.protocol_log`` field (``"participant_deleted:<number>"``) of a participant deleted after opening.
PROTOCOL_PARTICIPANT_DELETED = "participant_deleted"
DEVIATION_ORDER = (
    "provisional",
    "reopened_after_close",
    "stopped_early",
    "no_planned_sample",
    "post_hoc_exclusions",
    "no_primary",
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _r(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _round_p(p: float | None) -> float | None:
    """Four significant figures (a tiny p must not round to 0)."""
    if p is None or p == 0:
        return p
    return round(p, 3 - math.floor(math.log10(abs(p))))


def _ci(bounds: tuple[float, float] | list[float] | None) -> dict[str, float | None] | None:
    if bounds is None:
        return None
    return {"low": _r(bounds[0]), "high": _r(bounds[1])}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value if isinstance(value, int) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def reported_level(label: Any) -> str:
    """A finding's level exactly as its tool printed it (``tool_severity``), ``"unrated"`` when blank.

    Same rule as ``statistics.services.dedup.reported_level``; kept local so this module only
    depends on helpers present in the committed tree.
    """
    return str(label or "").strip() or "unrated"


def _tester_severity(value: str) -> str:
    return value or "unrated"


# ---------------------------------------------------------------------------
# Loaded data
# ---------------------------------------------------------------------------


@dataclass
class _App:
    id: str
    label: str
    obj: EvalTaskApp
    snapshot: dict[str, Any]
    group: str
    group_label: str


@dataclass
class _Record:
    """One included submitted assignment."""

    id: str
    assignment: Assignment
    participant_id: str
    item_id: str
    slots: list[tuple[str, str]]
    answers: dict[str, dict[str, Any]]
    context: dict[str, Any]

    def app_for_slot(self, slot: str) -> str | None:
        for name, app_id in self.slots:
            if name == slot:
                return app_id
        return None

    def block_context(self, block_id: str) -> dict[str, Any]:
        return _dict(_dict(self.context.get("blocks")).get(block_id))


@dataclass
class _Group:
    key: str
    label: str
    apps: list[str] = field(default_factory=list)


@dataclass
class _Metric:
    """Per-app values of one plan metric, for contrasts."""

    block_id: str
    metric: str
    label: str
    values: dict[str, float]
    floor_value: float | None


@dataclass
class _Context:
    task: EvalTask
    design: dict[str, Any]
    settings: dict[str, Any]
    group_by: str
    include_excluded: bool
    provisional: bool
    seed: int
    policy: str
    apps: dict[str, _App]
    pool: list[str]
    groups: list[_Group]
    records: list[_Record]
    records_by_app: dict[str, list[_Record]]
    unevaluable: set[str]
    observations: dict[str, list[Observation]]
    verdicts: dict[str, list[FindingVerdict]]
    http_counts: dict[tuple[str, str], int]
    metrics: list[_Metric] = field(default_factory=list)
    agreements: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def group_of(self, app_id: str) -> str:
        return self.apps[app_id].group

    def seed_for(self, *parts: Any) -> int:
        return stats.derive_seed(self.seed, *parts)

    def warn(self, code: str, message: str, block_id: str | None = None) -> None:
        entry: dict[str, Any] = {"code": code, "message": message}
        if block_id is not None:
            entry["block_id"] = block_id
        self.warnings.append(entry)


def _slots(assignment: Assignment) -> list[tuple[str, str]]:
    """Display slot → app id (as ``context.slot_apps``, from ids only)."""
    item = assignment.item
    if item.app_b_id is None:
        return [("single", str(item.app_a_id))]
    if assignment.swapped:
        return [("A", str(item.app_b_id)), ("B", str(item.app_a_id))]
    return [("A", str(item.app_a_id)), ("B", str(item.app_b_id))]


def _group_identity(app: EvalTaskApp, snapshot: dict[str, Any], group_by: str) -> tuple[str, str]:
    model_id = snapshot.get("model_id") or ""
    model_name = snapshot.get("model_name") or model_id
    if group_by == "app":
        return str(app.pk), app.label
    if group_by == "condition" and snapshot.get("condition_id") is not None:
        condition_id = snapshot["condition_id"]
        return str(condition_id), snapshot.get("condition_label") or f"Condition {condition_id}"
    if model_id:
        key = model_id if group_by == "model" else f"model:{model_id}"
        return key, model_name
    return "unknown", "Unknown"


def _skip_slots(assignment: Assignment) -> list[str]:
    """Slots an app_broken skip blames: the ones whose hosting failed at skip time, else all."""
    slots = [slot for slot, _app in _slots(assignment)]
    if assignment.skip_reason != "app_broken":
        return slots
    hosting = _dict(_dict(assignment.context).get("skip_hosting"))
    failed = [
        slot
        for slot in slots
        if _dict(hosting.get(slot)).get("state") == "failed" or _dict(hosting.get(slot)).get("error")
    ]
    return failed or slots


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def compute_results(task: EvalTask, *, group_by: str = "condition", include_excluded: bool = False) -> dict[str, Any]:
    """TS ``TaskResults`` for the task (see the module docstring for every rule)."""
    if group_by not in GROUP_BY:
        msg = "group_by must be condition, model or app"
        raise invalid(msg, [{"path": "group_by", "message": msg}])
    design = task.design if isinstance(task.design, dict) else {}
    settings = merged_settings(task.settings)
    analysis_plan = _dict(design.get("analysis"))
    provisional = task.status not in (EvalTask.Status.CLOSED, EvalTask.Status.ARCHIVED)

    participants = {str(row.pk): row for row in Participant.objects.filter(task=task).exclude(kind="preview")}
    assignments = list(
        Assignment.objects.filter(task=task).exclude(participant__kind="preview").select_related("item"),
    )
    submitted = [row for row in assignments if row.status == Assignment.Status.SUBMITTED]
    responses: dict[str, dict[str, dict[str, Any]]] = {}
    if submitted:
        rows = Response.objects.filter(assignment__in=[row.pk for row in submitted])
        for assignment_id, block_id, answers in rows.values_list("assignment_id", "block_id", "answers"):
            responses.setdefault(str(assignment_id), {})[block_id] = _dict(answers)

    report = quality_service.quality_report(
        task,
        participants=participants.values(),
        assignments=assignments,
        responses=responses,
    )
    rules = _dict(analysis_plan.get("exclusion_rules"))
    rule_hits: Counter = Counter()
    plan_excluded: set[str] = set()
    for row in submitted:
        participant = participants[str(row.participant_id)]
        if row.excluded or participant.excluded:
            plan_excluded.add(str(row.pk))
            continue
        matched = quality_service.rule_matches(report, rules, row)
        rule_hits.update(matched)
        if matched:
            plan_excluded.add(str(row.pk))

    def in_scope(row: Assignment) -> bool:
        return include_excluded or not (row.excluded or participants[str(row.participant_id)].excluded)

    included = [row for row in submitted if include_excluded or str(row.pk) not in plan_excluded]

    task_apps = {str(app.pk): app for app in EvalTaskApp.objects.filter(task=task)}
    used_apps = {app_id for row in assignments for _slot, app_id in _slots(row)}
    pool = sorted(
        (app_id for app_id, app in task_apps.items() if app.is_active or app_id in used_apps),
        key=lambda app_id: (task_apps[app_id].label, app_id),
    )
    apps: dict[str, _App] = {}
    groups: dict[str, _Group] = {}
    for app_id in pool:
        app = task_apps[app_id]
        snapshot = _dict(app.snapshot)
        key, label = _group_identity(app, snapshot, group_by)
        apps[app_id] = _App(app_id, app.label, app, snapshot, key, label)
        groups.setdefault(key, _Group(key, label)).apps.append(app_id)
    ordered_groups = sorted(groups.values(), key=lambda group: (group.label.lower(), group.key))

    records = []
    records_by_app: dict[str, list[_Record]] = defaultdict(list)
    for row in included:
        record = _Record(
            id=str(row.pk),
            assignment=row,
            participant_id=str(row.participant_id),
            item_id=str(row.item_id),
            slots=[(slot, app_id) for slot, app_id in _slots(row) if app_id in apps],
            answers=responses.get(str(row.pk), {}),
            context=_dict(row.context),
        )
        records.append(record)
        for _slot, app_id in record.slots:
            records_by_app[app_id].append(record)

    skips = [row for row in assignments if row.status == Assignment.Status.SKIPPED and in_scope(row)]
    broken: Counter = Counter()
    unavailable: Counter = Counter()
    for row in skips:
        if row.skip_reason not in UNAVAILABLE_REASONS:
            continue
        for slot in _skip_slots(row):
            app_id = dict(_slots(row)).get(slot)
            if app_id not in apps:
                continue
            unavailable[app_id] += 1
            if row.skip_reason == "app_broken":
                broken[app_id] += 1
    unevaluable = {app_id for app_id in pool if not records_by_app.get(app_id) and broken[app_id]}

    observations: dict[str, list[Observation]] = defaultdict(list)
    verdicts: dict[str, list[FindingVerdict]] = defaultdict(list)
    http_counts: dict[tuple[str, str], int] = {}
    record_ids = [record.assignment.pk for record in records]
    if record_ids:
        for observation in Observation.objects.filter(assignment__in=record_ids):
            observations[str(observation.assignment_id)].append(observation)
        for verdict in FindingVerdict.objects.filter(assignment__in=record_ids):
            verdicts[str(verdict.assignment_id)].append(verdict)
        exchanges = (
            HttpExchange.objects.filter(assignment__in=record_ids)
            .order_by()
            .values("assignment_id", "task_app_id")
            .annotate(n=Count("id"))
        )
        for row in exchanges:
            http_counts[(str(row["assignment_id"]), str(row["task_app_id"]))] = row["n"]

    ctx = _Context(
        task=task,
        design=design,
        settings=settings,
        group_by=group_by,
        include_excluded=include_excluded,
        provisional=provisional,
        seed=int(task.seed),
        policy=analysis_plan.get("unevaluable_policy") or "exclude",
        apps=apps,
        pool=pool,
        groups=ordered_groups,
        records=records,
        records_by_app=records_by_app,
        unevaluable=unevaluable,
        observations=observations,
        verdicts=verdicts,
        http_counts=http_counts,
    )

    blocks = []
    for block in design.get("blocks") or []:
        analyzer = _BLOCK_ANALYZERS.get(block.get("type", ""))
        if analyzer is not None:
            blocks.append(analyzer(ctx, block))

    contrasts = _contrasts(ctx)
    human_vs_automated = _human_vs_automated(ctx)
    deviations = _deviations(task, design, settings, participants, assignments)
    _warnings(ctx, assignments, skips)

    included_participants = {record.participant_id for record in records}
    return {
        "task_id": str(task.pk),
        "generated_at": timezone.now().isoformat(),
        "provisional": provisional,
        "p_values_withheld": provisional,
        "confirmatory": not deviations,
        "deviations": deviations,
        "rule_exclusions": {rule: rule_hits[rule] for rule in quality_service.RULES if rules.get(rule)},
        "group_by": group_by,
        "include_excluded": include_excluded,
        "counts": {
            "participants": len(participants),
            "participants_included": len(included_participants),
            "assignments_submitted": len(submitted),
            "assignments_included": len(records),
            "assignments_skipped": sum(1 for row in assignments if row.status == Assignment.Status.SKIPPED),
            "assignments_excluded": len(plan_excluded),
        },
        "warnings": ctx.warnings,
        "groups": [
            {
                "key": group.key,
                "label": group.label,
                "n_apps_pool": len(group.apps),
                "n_apps_rated": sum(1 for app_id in group.apps if records_by_app.get(app_id)),
                "n_apps_unevaluable": sum(1 for app_id in group.apps if app_id in unevaluable),
            }
            for group in ordered_groups
        ],
        "apps": [
            {
                "app_id": app_id,
                "label": apps[app_id].label,
                "group": apps[app_id].group,
                "model_name": apps[app_id].snapshot.get("model_name") or "",
                "condition_label": apps[app_id].snapshot.get("condition_label") or "",
                "app_slug": apps[app_id].snapshot.get("app_slug") or "",
                "run_id": str(apps[app_id].obj.job_id) if apps[app_id].obj.job_id else None,
                "n_submitted": len(records_by_app.get(app_id, [])),
                "n_unavailable": unavailable[app_id],
            }
            for app_id in pool
        ],
        "blocks": blocks,
        "quality": _quality_rows(participants, assignments, report),
        "contrasts": contrasts,
        "human_vs_automated": human_vs_automated,
    }


# ---------------------------------------------------------------------------
# Shared result builders
# ---------------------------------------------------------------------------


def _agreement(ctx: _Context, block_id: str, label: str, units: Iterable[list[Any]], level: str) -> dict[str, Any]:
    unit_values = [list(values) for values in units]
    n_units, n_values = stats.pairable_counts(unit_values)
    result = {
        "alpha": _r(stats.krippendorff_alpha(unit_values, level)),
        "level": level,
        "percent_agreement": _r(stats.percent_agreement(unit_values)),
        "n_units": n_units,
        "n_values": n_values,
    }
    ctx.agreements.append((block_id, label, result))
    return result


def _app_score(ctx: _Context, app_id: str, values: list[float], na_count: int = 0) -> dict[str, Any]:
    app = ctx.apps[app_id]
    return {
        "app_id": app_id,
        "label": app.label,
        "group": app.group,
        "n": len(values),
        "mean": _r(stats.mean(values)),
        "sd": _r(stats.sd(values)),
        "median": _r(stats.median(values)),
        "na_count": na_count,
    }


def _summary(values: dict[str, float], seed: int) -> tuple[float | None, dict[str, Any] | None, int]:
    return (
        _r(stats.mean(values.values())),
        _ci(stats.cluster_bootstrap_mean_ci(values, seed)),
        len(values),
    )


def _policy_values(ctx: _Context, values: dict[str, float], floor_value: float | None) -> dict[str, float]:
    """App values with unevaluable apps at ``floor_value`` when the plan's policy is ``floor``."""
    if ctx.policy != "floor" or floor_value is None:
        return dict(values)
    floored = dict(values)
    for app_id in ctx.unevaluable:
        floored.setdefault(app_id, floor_value)
    return floored


def _group_scores(
    ctx: _Context,
    app_values: dict[str, float],
    n_ratings: dict[str, int],
    *,
    floor_value: float | None,
    seed_parts: tuple[Any, ...],
) -> list[dict[str, Any]]:
    """TS ``GroupScore`` per group: mean of app values, bootstrap CI over apps, floor sensitivity."""
    rows = []
    for group in ctx.groups:
        values = {app_id: app_values[app_id] for app_id in group.apps if app_id in app_values}
        seed = ctx.seed_for(*seed_parts, group.key)
        complete = _summary(values, seed)
        floor = None
        if floor_value is not None:
            unevaluable = [app_id for app_id in group.apps if app_id in ctx.unevaluable]
            if unevaluable:
                floored = {**values, **dict.fromkeys(unevaluable, floor_value)}
                floor_summary = _summary(floored, ctx.seed_for(*seed_parts, group.key, "floor"))
            else:
                floor_summary = complete
            floor = {"mean": floor_summary[0], "ci": floor_summary[1], "n_apps": floor_summary[2]}
        primary = complete
        if ctx.policy == "floor" and floor is not None:
            primary = (floor["mean"], floor["ci"], floor["n_apps"])
        rows.append(
            {
                "group": group.key,
                "label": group.label,
                "n_apps": primary[2],
                "n_ratings": sum(n_ratings.get(app_id, 0) for app_id in group.apps),
                "mean": primary[0],
                "ci": primary[1],
                "floor": floor,
            },
        )
    return rows


def _means(values_by_app: dict[str, list[float]]) -> dict[str, float]:
    return {app_id: sum(values) / len(values) for app_id, values in values_by_app.items() if values}


def _single_app(record: _Record) -> str | None:
    return record.slots[0][1] if record.slots else None


# ---------------------------------------------------------------------------
# Rating
# ---------------------------------------------------------------------------


def _scale_value(value: Any, allowed: set[int]) -> int | None:
    number = _int_value(value)
    return number if number is not None and number in allowed else None


def _rating_block(ctx: _Context, block: dict[str, Any]) -> dict[str, Any]:
    block_id = block["id"]
    criteria_results = []
    criterion_metrics = []
    composite_values: dict[str, list[float]] = defaultdict(list)
    composite_na: Counter = Counter()
    criteria = block.get("criteria") or []
    allowed = {criterion["id"]: set(scale_values(criterion["scale"])) for criterion in criteria}
    for criterion in criteria:
        criterion_id = criterion["id"]
        values: dict[str, list[float]] = defaultdict(list)
        na: Counter = Counter()
        for record in ctx.records:
            app_id = _single_app(record)
            answers = _dict(record.answers.get(block_id)).get("criteria")
            if app_id is None or not isinstance(answers, dict) or criterion_id not in answers:
                continue
            raw = answers[criterion_id]
            if raw == "na":
                na[app_id] += 1
                continue
            value = _scale_value(raw, allowed[criterion_id])
            if value is not None:
                values[app_id].append(float(value))
        scale = criterion["scale"]
        app_means = _means(values)
        criterion_metrics.append(
            _Metric(block_id, criterion_id, f"{block['title']}: {criterion['label']}", app_means, float(scale["min"])),
        )
        level = "ordinal" if len(scale_values(scale)) <= ORDINAL_MAX_POINTS else "interval"
        criteria_results.append(
            {
                "criterion_id": criterion_id,
                "label": criterion["label"],
                "scale": scale,
                "per_app": [_app_score(ctx, app_id, values.get(app_id, []), na[app_id]) for app_id in ctx.pool],
                "per_group": _group_scores(
                    ctx,
                    app_means,
                    {app_id: len(items) for app_id, items in values.items()},
                    floor_value=float(scale["min"]),
                    seed_parts=("rating", block_id, criterion_id),
                ),
                "agreement": _agreement(ctx, block_id, criterion["label"], values.values(), level),
            },
        )
    for record in ctx.records:
        app_id = _single_app(record)
        answers = _dict(record.answers.get(block_id)).get("criteria")
        if app_id is None or not isinstance(answers, dict):
            continue
        parts = []
        for criterion in criteria:
            value = _scale_value(answers.get(criterion["id"]), allowed[criterion["id"]])
            if value is None:
                continue
            low, high = criterion["scale"]["min"], criterion["scale"]["max"]
            parts.append((value - low) / (high - low))
        if parts:
            composite_values[app_id].append(sum(parts) / len(parts))
        else:
            composite_na[app_id] += 1
    composite_means = _means(composite_values)
    ctx.metrics.append(_Metric(block_id, "composite", f"{block['title']}: composite", composite_means, 0.0))
    ctx.metrics.extend(criterion_metrics)
    return {
        "type": "rating",
        "block_id": block_id,
        "title": block["title"],
        "criteria": criteria_results,
        "composite": {
            "per_app": [
                _app_score(ctx, app_id, composite_values.get(app_id, []), composite_na[app_id]) for app_id in ctx.pool
            ],
            "per_group": _group_scores(
                ctx,
                composite_means,
                {app_id: len(items) for app_id, items in composite_values.items()},
                floor_value=0.0,
                seed_parts=("rating", block_id, "composite"),
            ),
        },
    }


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------


def _source_from_id(requirement_id: str) -> str:
    prefix = requirement_id.split("_", 1)[0]
    return _SOURCE_PREFIXES.get(prefix, "custom")


def _requirement_items(record: _Record, block: dict[str, Any], slot: str) -> list[dict[str, str]]:
    """The requirement items the rater saw (frozen context), else the answered ids (legacy fallback)."""
    frozen = _dict(record.block_context(block["id"]).get("requirements")).get(slot)
    if isinstance(frozen, list):
        return [
            {"id": str(item.get("id")), "source": str(item.get("source") or ""), "text": str(item.get("text") or "")}
            for item in frozen
            if isinstance(item, dict) and item.get("id")
        ]
    answered = _dict(_dict(record.answers.get(block["id"])).get("items"))
    custom = {entry.get("id"): entry.get("text", "") for entry in block.get("custom") or []}
    items = []
    for requirement_id in answered:
        source = _source_from_id(requirement_id)
        text = custom.get(requirement_id[2:], "") if source == "custom" else ""
        items.append({"id": requirement_id, "source": source, "text": text})
    return items


def _endpoint_key(text: str) -> str:
    """``"METHOD /path"`` from an endpoint requirement text ``"METHOD /path — description"``."""
    head = text.split(" — ", 1)[0].strip()
    parts = head.split()
    if len(parts) >= 2 and parts[0].isalpha() and parts[0].isupper() and parts[1].startswith("/"):
        return f"{parts[0]} {parts[1]}"
    return head


def _endpoint_categories(block: dict[str, Any]) -> frozenset[str]:
    """Conformance check categories matching the block's endpoint sources (api → api, admin_api → admin)."""
    return frozenset(
        ENDPOINT_CHECK_CATEGORY[source] for source in block.get("sources") or [] if source in ENDPOINT_CHECK_CATEGORY
    )


def _endpoint_pass_rate(snapshot: dict[str, Any], categories: Collection[str]) -> float | None:
    """Share of passed conformance checks in ``categories`` (the endpoint sources the raters were shown)."""
    checks = [
        check
        for check in snapshot.get("checks") or []
        if isinstance(check, dict) and check.get("category") in categories
    ]
    if not checks:
        return None
    return sum(1 for check in checks if check.get("passed")) / len(checks)


def _conformance(snapshot: dict[str, Any]) -> dict[str, Any]:
    functional = _dict(snapshot.get("functional"))
    return functional if functional.get("source") == snapshot_service.EVAL_SOURCE else {}


def _conformance_version(snapshot: dict[str, Any]) -> tuple[str, str] | None:
    functional = _conformance(snapshot)
    if not functional or functional.get("pass_rate") is None:
        return None
    return (
        str(snapshot.get("evaluator_version") or functional.get("evaluator_version") or ""),
        str(snapshot.get("tester_version") or functional.get("tester_version") or ""),
    )


def _modal_version(ctx: _Context) -> tuple[str, str] | None:
    counts = Counter(
        version for app_id in ctx.pool if (version := _conformance_version(ctx.apps[app_id].snapshot)) is not None
    )
    if not counts:
        return None
    return sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))[0][0]


def _requirements_block(ctx: _Context, block: dict[str, Any]) -> dict[str, Any]:
    block_id = block["id"]
    sources = list(block.get("sources") or [])
    if block.get("custom"):
        sources.append("custom")
    # the automated side covers only the endpoint kinds the raters were shown (api and / or admin_api)
    endpoint_categories = _endpoint_categories(block)
    rater_compliance: dict[str, list[float]] = defaultdict(list)
    rater_strict: dict[str, list[float]] = defaultdict(list)
    rater_endpoint: dict[str, list[float]] = defaultdict(list)
    rater_by_source: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    blocked: Counter = Counter()
    cannot_test: Counter = Counter()
    per_requirement: dict[str, dict[str, Any]] = {}
    units: dict[tuple[str, str], list[int]] = defaultdict(list)
    endpoint_votes: dict[tuple[str, str], list[bool]] = defaultdict(list)
    endpoint_items: dict[tuple[str, str], dict[str, str]] = {}
    for record in ctx.records:
        app_id = _single_app(record)
        if app_id is None:
            continue
        answered = _dict(_dict(record.answers.get(block_id)).get("items"))
        scored: list[float] = []
        strict: list[float] = []
        endpoint: list[float] = []
        by_source: dict[str, list[float]] = defaultdict(list)
        for item in _requirement_items(record, block, record.slots[0][0]):
            source = item["source"] or _source_from_id(item["id"])
            if source in ENDPOINT_SOURCES:
                # every shown endpoint item, so ones with no usable verdict count as unmatched
                endpoint_items.setdefault((app_id, item["id"]), {"source": source, "text": item["text"]})
            verdict = _dict(answered.get(item["id"])).get("verdict")
            if verdict not in REQUIREMENT_VERDICTS:
                continue
            entry = per_requirement.setdefault(
                item["id"],
                {
                    "req_id": item["id"],
                    "source": source,
                    "text": item["text"],
                    "n_ratings": 0,
                    **dict.fromkeys(REQUIREMENT_VERDICTS, 0),
                },
            )
            entry["n_ratings"] += 1
            entry[verdict] += 1
            if verdict == "cannot_test":
                cannot_test[app_id] += 1
                strict.append(0.0)
                continue
            if verdict == "blocked":
                blocked[app_id] += 1
            value = REQUIREMENT_SCORES[verdict]
            scored.append(value)
            strict.append(value)
            by_source[entry["source"]].append(value)
            units[(app_id, item["id"])].append(REQUIREMENT_ORDINAL[verdict])
            if entry["source"] in ENDPOINT_SOURCES:
                endpoint.append(value)
                endpoint_votes[(app_id, item["id"])].append(verdict == "met")
        if scored:
            rater_compliance[app_id].append(sum(scored) / len(scored))
        if strict:
            rater_strict[app_id].append(sum(strict) / len(strict))
        if endpoint:
            rater_endpoint[app_id].append(sum(endpoint) / len(endpoint))
        for source, values in by_source.items():
            rater_by_source[app_id][source].append(sum(values) / len(values))

    compliance = _means(rater_compliance)
    endpoint_compliance = _means(rater_endpoint)
    ctx.metrics.append(_Metric(block_id, "compliance", f"{block['title']}: compliance", compliance, 0.0))
    per_app = []
    for app_id in ctx.pool:
        app = ctx.apps[app_id]
        functional = _conformance(app.snapshot)
        strict = rater_strict.get(app_id, [])
        per_app.append(
            {
                "app_id": app_id,
                "label": app.label,
                "group": app.group,
                "n_raters": len(ctx.records_by_app.get(app_id, [])),
                "compliance": _r(compliance.get(app_id)),
                "compliance_strict": _r(stats.mean(strict)),
                "by_source": {source: _r(stats.mean(rater_by_source[app_id].get(source, []))) for source in sources},
                "blocked": blocked[app_id],
                "cannot_test": cannot_test[app_id],
                "conformance_pass_rate": _r(functional.get("pass_rate")),
                "conformance_endpoint_pass_rate": _r(_endpoint_pass_rate(app.snapshot, endpoint_categories)),
                "conformance_status": _dict(app.snapshot.get("functional")).get("status"),
            },
        )
    modal = _modal_version(ctx)
    return {
        "type": "requirements",
        "block_id": block_id,
        "title": block["title"],
        "per_app": per_app,
        "per_group": _group_scores(
            ctx,
            compliance,
            {app_id: len(values) for app_id, values in rater_compliance.items()},
            floor_value=0.0,
            seed_parts=("requirements", block_id),
        ),
        "per_requirement": list(per_requirement.values()),
        "agreement": _agreement(ctx, block_id, block["title"], units.values(), "ordinal"),
        "vs_conformance": [
            _vs_conformance(
                ctx,
                "endpoints",
                endpoint_compliance,
                {app_id: _endpoint_pass_rate(ctx.apps[app_id].snapshot, endpoint_categories) for app_id in ctx.pool},
                modal,
                ("vs_conformance", block_id, "endpoints"),
                checks=" and ".join(sorted(endpoint_categories)) or "endpoint",
            ),
            _vs_conformance(
                ctx,
                "overall",
                compliance,
                {app_id: _conformance(ctx.apps[app_id].snapshot).get("pass_rate") for app_id in ctx.pool},
                modal,
                ("vs_conformance", block_id, "overall"),
            ),
        ],
        "endpoint_agreement": _endpoint_agreement(ctx, endpoint_items, endpoint_votes),
    }


_VS_NOTES = {
    "endpoints": (
        "Primary: human compliance on the API endpoint requirements vs the conformance pass rate on the {checks} "
        "checks (only the categories matching the endpoint sources the raters were shown), correlated on "
        "within-spec percentile ranks."
    ),
    "overall": (
        "Secondary, different constructs: overall human compliance (mostly prose requirements) vs the overall "
        "conformance pass rate (health, frontend, auth and endpoint checks), on within-spec percentile ranks."
    ),
}


def _correlate_within_specs(
    ctx: _Context,
    xs: dict[str, float | None],
    ys: dict[str, float | None],
    *,
    version: tuple[str, str] | None,
    check_version: bool,
    seed: int,
    missing_y: str,
) -> tuple[float | None, tuple[float, float] | None, int, list[str], Counter]:
    """Spearman on within-group_key percentile ranks; returns rho, ci, n, excluded labels, reasons."""
    reasons: Counter = Counter()
    excluded: list[str] = []
    candidates: dict[str, tuple[float, float]] = {}
    for app_id in ctx.pool:
        app = ctx.apps[app_id]
        x, y = xs.get(app_id), ys.get(app_id)
        app_version = _conformance_version(app.snapshot) if check_version else None
        reason = None
        if x is None:
            reason = "not rated"
        elif y is None or (check_version and app_version is None):
            reason = missing_y
        elif check_version and app_version != version:
            reason = "another evaluator version"
        if reason:
            reasons[reason] += 1
            excluded.append(app.label)
            continue
        candidates[app_id] = (float(x), float(y))
    spec_of = {app_id: ctx.apps[app_id].obj.group_key or app_id for app_id in candidates}
    spec_sizes = Counter(spec_of.values())
    for app_id in list(candidates):
        if spec_sizes[spec_of[app_id]] < 2:
            reasons["only rated app of its spec"] += 1
            excluded.append(ctx.apps[app_id].label)
            del candidates[app_id]
    keys = list(candidates)
    rank_x = stats.within_group_percentile_ranks({k: candidates[k][0] for k in keys}, spec_of)
    rank_y = stats.within_group_percentile_ranks({k: candidates[k][1] for k in keys}, spec_of)
    px = [rank_x[k] for k in keys]
    py = [rank_y[k] for k in keys]
    rho = spearman(px, py) if keys else None
    ci = spearman_bootstrap_ci(px, py, seed=seed) if rho is not None else None
    return rho, ci, len(keys), sorted(excluded), reasons


def _excluded_note(reasons: Counter) -> str:
    if not reasons:
        return ""
    parts = [f"{count} {reason}" for reason, count in sorted(reasons.items())]
    return " Excluded apps: " + ", ".join(parts) + "."


def _vs_conformance(
    ctx: _Context,
    scope: str,
    human: dict[str, float],
    automated: dict[str, float | None],
    version: tuple[str, str] | None,
    seed_parts: tuple[Any, ...],
    *,
    checks: str = "",
) -> dict[str, Any]:
    rho, ci, n, excluded, reasons = _correlate_within_specs(
        ctx,
        dict(human),
        automated,
        version=version,
        check_version=True,
        seed=ctx.seed_for(*seed_parts),
        missing_y="no conformance result",
    )
    note = _VS_NOTES[scope].format(checks=checks)
    if version is not None:
        note += f" Restricted to evaluator version {version[0] or 'unknown'} (tester {version[1][:12] or 'unknown'})."
    else:
        note += " No app has a graded conformance result."
    return {
        "scope": scope,
        "rho": rho,
        "ci": _ci(ci),
        "n": n,
        "excluded_apps": excluded,
        "note": note + _excluded_note(reasons),
    }


def _endpoint_agreement(
    ctx: _Context,
    items: dict[tuple[str, str], dict[str, str]],
    votes: dict[tuple[str, str], list[bool]],
) -> dict[str, Any] | None:
    """Human majority (met = pass; partial / not met / blocked = fail) vs the matching conformance check."""
    if not items:
        return None
    counts = {"both_pass": 0, "human_only": 0, "auto_only": 0, "both_fail": 0}
    pairs: list[tuple[bool, bool]] = []
    unmatched = 0
    for key, item in items.items():
        app_id, _requirement_id = key
        passes = sum(1 for vote in votes.get(key, []) if vote)
        fails = len(votes.get(key, [])) - passes
        name = _endpoint_key(item["text"])
        checks = [check for check in ctx.apps[app_id].snapshot.get("checks") or [] if isinstance(check, dict)]
        category = ENDPOINT_CHECK_CATEGORY.get(item["source"])
        match = next((c for c in checks if c.get("category") == category and c.get("name") == name), None)
        if match is None:
            match = next(
                (c for c in checks if c.get("category") in ("api", "admin") and c.get("name") == name),
                None,
            )
        if passes == fails or match is None:
            unmatched += 1
            continue
        human, auto = passes > fails, bool(match.get("passed"))
        pairs.append((human, auto))
        if human and auto:
            counts["both_pass"] += 1
        elif human:
            counts["human_only"] += 1
        elif auto:
            counts["auto_only"] += 1
        else:
            counts["both_fail"] += 1
    return {"n": len(pairs), "kappa": cohens_kappa(pairs), **counts, "n_unmatched": unmatched}


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def _report_block_ids(ctx: _Context, block: dict[str, Any]) -> set[str]:
    """Observation block ids attributed to this security block (blank ids go to the first one with reports)."""
    ids = {block["id"]}
    reporting = [b for b in ctx.design.get("blocks") or [] if b.get("type") == "security" and b.get("reports")]
    if reporting and reporting[0]["id"] == block["id"]:
        known = {b.get("id") for b in ctx.design.get("blocks") or []}
        ids.add("")
        ids.update({observation.block_id for items in ctx.observations.values() for observation in items} - known)
    return ids


def _security_block(ctx: _Context, block: dict[str, Any]) -> dict[str, Any]:
    block_id = block["id"]
    checklist = []
    item_shares: dict[str, list[float]] = defaultdict(list)
    for item in block.get("checklist") or []:
        outcomes: dict[str, Counter] = defaultdict(Counter)
        units: dict[str, list[str]] = defaultdict(list)
        for record in ctx.records:
            app_id = _single_app(record)
            answers = _dict(_dict(record.answers.get(block_id)).get("checklist"))
            outcome = _dict(answers.get(item["id"])).get("outcome")
            if app_id is None or outcome not in CHECKLIST_OUTCOMES:
                continue
            outcomes[app_id][outcome] += 1
            if outcome != "not_tested":
                units[app_id].append(outcome)
        shares = {
            app_id: counts["vulnerable"] / (counts["vulnerable"] + counts["not_vulnerable"])
            for app_id, counts in outcomes.items()
            if counts["vulnerable"] + counts["not_vulnerable"]
        }
        for app_id, share in shares.items():
            item_shares[app_id].append(share)
        per_group = []
        for group in ctx.groups:
            group_shares = {app_id: shares[app_id] for app_id in group.apps if app_id in shares}
            totals = Counter()
            for app_id in group.apps:
                totals.update(outcomes.get(app_id, Counter()))
            per_group.append(
                {
                    "group": group.key,
                    "label": group.label,
                    "n_apps": len(group_shares),
                    "n": sum(totals[outcome] for outcome in CHECKLIST_OUTCOMES),
                    "vulnerable": totals["vulnerable"],
                    "not_vulnerable": totals["not_vulnerable"],
                    "not_tested": totals["not_tested"],
                    "vulnerable_share": _r(stats.mean(group_shares.values())),
                    "ci": _ci(
                        stats.cluster_bootstrap_mean_ci(
                            group_shares,
                            ctx.seed_for("security", block_id, item["id"], group.key),
                        ),
                    ),
                },
            )
        checklist.append(
            {
                "item_id": item["id"],
                "label": item["label"],
                "category": item.get("category") or "",
                "per_group": per_group,
                "agreement": _agreement(ctx, block_id, item["label"], units.values(), "nominal"),
            },
        )
    if block.get("checklist"):
        ctx.metrics.append(
            _Metric(block_id, "vulnerable_share", f"{block['title']}: vulnerable share", _means(item_shares), None),
        )
    return {
        "type": "security",
        "block_id": block_id,
        "title": block["title"],
        "checklist": checklist,
        "reports": _security_reports(ctx, block),
        "finding_review": _finding_review(ctx, block) if _dict(block.get("finding_review")).get("enabled") else None,
        "console": _console(ctx, block) if block.get("http_console") else None,
    }


def _rated_apps(ctx: _Context) -> list[str]:
    return [app_id for app_id in ctx.pool if ctx.records_by_app.get(app_id)]


def _security_reports(ctx: _Context, block: dict[str, Any]) -> dict[str, Any]:
    block_ids = _report_block_ids(ctx, block)
    totals: Counter = Counter()
    by_severity: dict[str, Counter] = defaultdict(Counter)
    by_category: dict[str, Counter] = defaultdict(Counter)
    for record in ctx.records:
        apps_here = {app_id for _slot, app_id in record.slots}
        for observation in ctx.observations.get(record.id, []):
            app_id = str(observation.task_app_id)
            if observation.kind != Observation.Kind.VULNERABILITY or observation.block_id not in block_ids:
                continue
            if app_id not in apps_here:
                continue
            totals[app_id] += 1
            by_severity[app_id][_tester_severity(observation.severity)] += 1
            by_category[app_id][observation.category or "uncategorized"] += 1
    rated = _rated_apps(ctx)
    per_rater = {app_id: totals[app_id] / len(ctx.records_by_app[app_id]) for app_id in rated}
    per_group = []
    for group in ctx.groups:
        group_rated = [app_id for app_id in group.apps if app_id in per_rater]
        severity = Counter()
        for app_id in group_rated:
            severity.update(by_severity[app_id])
        per_group.append(
            {
                "group": group.key,
                "label": group.label,
                "n_apps": len(group_rated),
                "total": sum(totals[app_id] for app_id in group_rated),
                "per_rater_mean": _r(stats.mean(per_rater[app_id] for app_id in group_rated)),
                "ci": _ci(
                    stats.cluster_bootstrap_mean_ci(
                        [per_rater[app_id] for app_id in group_rated],
                        ctx.seed_for("security_reports", block["id"], group.key),
                    ),
                ),
                "by_severity": dict(sorted(severity.items())),
            },
        )
    return {
        "per_app": [
            {
                "app_id": app_id,
                "label": ctx.apps[app_id].label,
                "group": ctx.apps[app_id].group,
                "total": totals[app_id],
                "by_severity": dict(sorted(by_severity[app_id].items())),
                "by_category": dict(sorted(by_category[app_id].items())),
            }
            for app_id in rated
        ],
        "per_group": per_group,
    }


@dataclass
class _Finding:
    app_id: str
    key: str
    tool: str
    tool_name: str
    level: str
    weight: float | None
    votes: Counter = field(default_factory=Counter)

    @property
    def soft(self) -> float | None:
        decided = self.votes["tp"] + self.votes["fp"]
        return self.votes["tp"] / decided if decided else None

    @property
    def majority(self) -> float | None:
        if self.votes["tp"] > self.votes["fp"]:
            return 1.0
        if self.votes["fp"] > self.votes["tp"]:
            return 0.0
        return None


def _frame_weight(app: _App, tool: str) -> float | None:
    entry = _dict(_dict(app.obj.finding_frame).get(tool))
    population, sampled = _number(entry.get("population")), _number(entry.get("sampled"))
    if not population or not sampled or sampled <= 0:
        return None
    return population / sampled


def _reviewed_findings(ctx: _Context, block_id: str) -> tuple[list[_Finding], int]:
    """Findings with ≥ 1 counted verdict (only verdicts on findings in the rater's frozen context)."""
    findings: dict[tuple[str, str], _Finding] = {}
    n_verdicts = 0
    for record in ctx.records:
        shown: dict[str, tuple[str, dict[str, Any]]] = {}
        for slot, entries in _dict(record.block_context(block_id).get("findings")).items():
            app_id = record.app_for_slot(slot)
            if app_id is None or not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and entry.get("id") is not None:
                    shown[str(entry["id"])] = (app_id, entry)
        for verdict in ctx.verdicts.get(record.id, []):
            if verdict.finding_key not in shown or verdict.verdict not in FINDING_VOTES:
                continue
            app_id, entry = shown[verdict.finding_key]
            tool = str(entry.get("tool") or "")
            finding = findings.get((app_id, verdict.finding_key))
            if finding is None:
                finding = _Finding(
                    app_id=app_id,
                    key=verdict.finding_key,
                    tool=tool,
                    tool_name=str(entry.get("tool_name") or tool),
                    level=reported_level(entry.get("tool_severity")),
                    weight=_frame_weight(ctx.apps[app_id], tool),
                )
                findings[(app_id, verdict.finding_key)] = finding
            finding.votes[verdict.verdict] += 1
            n_verdicts += 1
    return list(findings.values()), n_verdicts


def _app_precision(findings: list[_Finding], weights: Callable[[_Finding], float], label: str) -> dict[str, float]:
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for finding in findings:
        value = finding.soft if label == "soft" else finding.majority
        if value is None:
            continue
        weight = weights(finding)
        sums[finding.app_id][0] += weight * value
        sums[finding.app_id][1] += weight
    return {app_id: num / den for app_id, (num, den) in sums.items() if den > 0}


def _precision_row(
    ctx: _Context,
    key: str,
    label: str,
    findings: list[_Finding],
    *,
    weighted: bool,
    seed_parts: tuple[Any, ...],
) -> dict[str, Any]:
    """TS ``PrecisionRow``: mean over apps of each app's (IPW-weighted) soft-label precision."""
    use_weights = weighted and bool(findings) and all(finding.weight is not None for finding in findings)

    def weights(finding: _Finding) -> float:
        return float(finding.weight) if use_weights and finding.weight is not None else 1.0

    soft = _app_precision(findings, weights, "soft")
    majority = _app_precision(findings, weights, "majority")
    return {
        "key": key,
        "label": label,
        "tp": sum(finding.votes["tp"] for finding in findings),
        "fp": sum(finding.votes["fp"] for finding in findings),
        "unsure": sum(finding.votes["unsure"] for finding in findings),
        "n_findings": len(findings),
        "n_apps": len({finding.app_id for finding in findings}),
        "n_split": sum(1 for finding in findings if finding.votes["tp"] and finding.votes["fp"]),
        "precision": _r(stats.mean(soft.values())),
        "ci": _ci(stats.cluster_bootstrap_mean_ci(soft, ctx.seed_for(*seed_parts))),
        "majority_precision": _r(stats.mean(majority.values())),
        "weighted": use_weights,
    }


def _finding_review(ctx: _Context, block: dict[str, Any]) -> dict[str, Any]:
    block_id = block["id"]
    findings, n_verdicts = _reviewed_findings(ctx, block_id)
    by_tool: dict[str, list[_Finding]] = defaultdict(list)
    by_level: dict[str, list[_Finding]] = defaultdict(list)
    by_group: dict[str, list[_Finding]] = defaultdict(list)
    for finding in findings:
        by_tool[finding.tool].append(finding)
        by_level[finding.level].append(finding)
        by_group[ctx.group_of(finding.app_id)].append(finding)
    tool_names = {finding.tool: finding.tool_name for finding in findings}
    units = [
        [vote for vote in ("tp", "fp") for _n in range(finding.votes[vote])]
        for finding in findings
        if finding.votes["tp"] + finding.votes["fp"]
    ]
    return {
        "n_findings": len(findings),
        "n_verdicts": n_verdicts,
        "overall": _precision_row(
            ctx,
            "overall",
            "All reviewed findings",
            findings,
            weighted=True,
            seed_parts=("findings", block_id, "overall"),
        ),
        "per_tool": [
            _precision_row(
                ctx,
                tool,
                tool_names[tool] or tool,
                rows,
                weighted=False,
                seed_parts=("findings", block_id, "tool", tool),
            )
            for tool, rows in sorted(by_tool.items())
        ],
        "per_tool_severity": [
            _precision_row(
                ctx,
                level,
                level,
                rows,
                weighted=True,
                seed_parts=("findings", block_id, "level", level),
            )
            for level, rows in sorted(by_level.items())
        ],
        "per_group": [
            _precision_row(
                ctx,
                group.key,
                group.label,
                by_group[group.key],
                weighted=True,
                seed_parts=("findings", block_id, "group", group.key),
            )
            for group in ctx.groups
            if by_group.get(group.key)
        ],
        "agreement": _agreement(ctx, block_id, f"{block['title']}: finding review", units, "nominal"),
    }


def _console(ctx: _Context, block: dict[str, Any]) -> dict[str, Any]:
    per_app: Counter = Counter()
    users: set[str] = set()
    for record in ctx.records:
        for _slot, app_id in record.slots:
            count = ctx.http_counts.get((record.id, app_id), 0)
            per_app[app_id] += count
            if count:
                users.add(record.participant_id)
    return {
        "requests": sum(per_app.values()),
        "participants_using": len(users),
        "per_app": [
            {"app_id": app_id, "label": ctx.apps[app_id].label, "requests": per_app[app_id]}
            for app_id in _rated_apps(ctx)
        ],
    }


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def _scenarios_block(ctx: _Context, block: dict[str, Any]) -> dict[str, Any]:
    block_id = block["id"]
    results = []
    app_scenario_shares: dict[str, list[float]] = defaultdict(list)
    for scenario in block.get("scenarios") or []:
        scenario_id = scenario["id"]
        outcomes: dict[str, Counter] = defaultdict(Counter)
        seconds_by_app: dict[str, list[float]] = defaultdict(list)
        ease: list[float] = []
        for record in ctx.records:
            app_id = _single_app(record)
            entry = _dict(_dict(_dict(record.answers.get(block_id)).get("scenarios")).get(scenario_id))
            outcome = entry.get("outcome")
            if app_id is None or outcome not in SCENARIO_OUTCOMES:
                continue
            outcomes[app_id][outcome] += 1
            seconds = _number(entry.get("seconds"))
            if seconds is not None and seconds >= 0:
                seconds_by_app[app_id].append(seconds)
            ease_value = _int_value(entry.get("ease"))
            if ease_value is not None and 1 <= ease_value <= 7:
                ease.append(float(ease_value))
        totals: Counter = Counter()
        for counts in outcomes.values():
            totals.update(counts)
        shares = {}
        for app_id, counts in outcomes.items():
            attempted = counts["success"] + counts["partial"] + counts["failure"]
            if attempted:
                shares[app_id] = counts["success"] / attempted
                app_scenario_shares[app_id].append(shares[app_id])
        attempted_total = totals["success"] + totals["partial"] + totals["failure"]
        per_group = []
        for group in ctx.groups:
            group_shares = {app_id: shares[app_id] for app_id in group.apps if app_id in shares}
            group_seconds = [s for app_id in group.apps for s in seconds_by_app.get(app_id, [])]
            per_group.append(
                {
                    "group": group.key,
                    "label": group.label,
                    "n_apps": len(group_shares),
                    "n": sum(sum(outcomes.get(app_id, Counter()).values()) for app_id in group.apps),
                    "success_rate": _r(stats.mean(group_shares.values())),
                    "ci": _ci(
                        stats.cluster_bootstrap_mean_ci(
                            group_shares,
                            ctx.seed_for("scenarios", block_id, scenario_id, group.key),
                        ),
                    ),
                    "median_seconds": _r(stats.median(group_seconds), 1),
                },
            )
        all_seconds = [s for values in seconds_by_app.values() for s in values]
        results.append(
            {
                "scenario_id": scenario_id,
                "title": scenario["title"],
                "n": sum(totals[outcome] for outcome in SCENARIO_OUTCOMES),
                **{outcome: totals[outcome] for outcome in SCENARIO_OUTCOMES},
                "success_rate": _r(totals["success"] / attempted_total) if attempted_total else None,
                "median_seconds": _r(stats.median(all_seconds), 1),
                "ease_mean": _r(stats.mean(ease)),
                "per_group": per_group,
            },
        )
    ctx.metrics.append(
        _Metric(block_id, "success_rate", f"{block['title']}: success rate", _means(app_scenario_shares), None),
    )
    return {"type": "scenarios", "block_id": block_id, "title": block["title"], "scenarios": results}


# ---------------------------------------------------------------------------
# Questionnaire
# ---------------------------------------------------------------------------


def _distribution_key(value: Any) -> str:
    number = _number(value)
    if number is not None and number.is_integer():
        return str(int(number))
    return str(value)


def _question_summary(question: dict[str, Any], answers: list[Any]) -> dict[str, Any]:
    kind = question.get("kind", "text")
    answered = [value for value in answers if value is not None and value not in ("", [])]
    distribution: Counter = Counter()
    numeric: list[float] = []
    for value in answered:
        if kind == "multi" and isinstance(value, list):
            distribution.update(str(option) for option in value)
        elif kind in ("single", "attention", "scale", "number"):
            distribution[_distribution_key(value)] += 1
        if kind in ("scale", "number") and (number := _number(value)) is not None:
            numeric.append(number)
    return {
        "question_id": question["id"],
        "label": question.get("label", ""),
        "kind": kind,
        "n": len(answered),
        "mean": _r(stats.mean(numeric)) if kind in ("scale", "number") else None,
        "distribution": dict(sorted(distribution.items())),
    }


def _questionnaire_block(ctx: _Context, block: dict[str, Any]) -> dict[str, Any]:
    block_id = block["id"]
    instrument = block.get("instrument") or "custom"
    standard = instrument in INSTRUMENTS
    questions = instrument_questions(instrument) if standard else list(block.get("questions") or [])
    answers_by_question: dict[str, list[Any]] = defaultdict(list)
    scores: dict[str, list[float]] = defaultdict(list)
    for record in ctx.records:
        app_id = _single_app(record)
        answers = _dict(_dict(record.answers.get(block_id)).get("answers"))
        if app_id is None:
            continue
        for question in questions:
            answers_by_question[question["id"]].append(answers.get(question["id"]))
        if standard and (value := instrument_score(instrument, answers)) is not None:
            scores[app_id].append(value)
    score = None
    if standard:
        app_means = _means(scores)
        ctx.metrics.append(_Metric(block_id, "score", f"{block['title']}: score", app_means, None))
        score = {
            "per_app": [_app_score(ctx, app_id, scores.get(app_id, [])) for app_id in ctx.pool],
            "per_group": _group_scores(
                ctx,
                app_means,
                {app_id: len(values) for app_id, values in scores.items()},
                floor_value=None,
                seed_parts=("questionnaire", block_id),
            ),
        }
    return {
        "type": "questionnaire",
        "block_id": block_id,
        "title": block["title"],
        "instrument": instrument,
        "score": score,
        "questions": [_question_summary(question, answers_by_question[question["id"]]) for question in questions],
    }


# ---------------------------------------------------------------------------
# Bugs
# ---------------------------------------------------------------------------


def _none_found(answers: dict[str, Any], slot: str) -> bool:
    value = answers.get("none_found")
    if isinstance(value, dict):
        return value.get(slot) is True
    return value is True and slot == "single"


def _bugs_block(ctx: _Context, block: dict[str, Any]) -> dict[str, Any]:
    block_id = block["id"]
    totals: Counter = Counter()
    severities: dict[str, Counter] = defaultdict(Counter)
    none_found: Counter = Counter()
    for record in ctx.records:
        answers = _dict(record.answers.get(block_id))
        apps_here = {app_id for _slot, app_id in record.slots}
        for slot, app_id in record.slots:
            if _none_found(answers, slot):
                none_found[app_id] += 1
        for observation in ctx.observations.get(record.id, []):
            app_id = str(observation.task_app_id)
            if observation.kind == Observation.Kind.BUG and app_id in apps_here:
                totals[app_id] += 1
                severities[app_id][_tester_severity(observation.severity)] += 1
    per_rater = {
        app_id: totals[app_id] / len(ctx.records_by_app[app_id])
        for app_id in ctx.pool
        if ctx.records_by_app.get(app_id)
    }
    ctx.metrics.append(_Metric(block_id, "bugs_per_rater", f"{block['title']}: bugs per rater", per_rater, None))
    return {
        "type": "bugs",
        "block_id": block_id,
        "title": block["title"],
        "per_app": [
            {
                "app_id": app_id,
                "label": ctx.apps[app_id].label,
                "group": ctx.apps[app_id].group,
                "n_raters": len(ctx.records_by_app.get(app_id, [])),
                "total": totals[app_id],
                "per_rater_mean": _r(per_rater.get(app_id)),
                "by_severity": dict(sorted(severities[app_id].items())),
                "none_found": none_found[app_id],
            }
            for app_id in ctx.pool
        ],
        "per_group": _group_scores(
            ctx,
            per_rater,
            {app_id: len(ctx.records_by_app.get(app_id, [])) for app_id in per_rater},
            floor_value=None,
            seed_parts=("bugs", block_id),
        ),
    }


# ---------------------------------------------------------------------------
# Pairwise
# ---------------------------------------------------------------------------


def displayed_score(value: Any, scale: str) -> int | None:
    """The answer as displayed, signed so that + means the displayed-left (A) app is better."""
    if scale == "five_point":
        number = _int_value(value)
        return -number if number is not None and -2 <= number <= 2 else None
    return {"A": 1, "B": -1, "tie": 0}.get(value) if isinstance(value, str) else None


def canonical_score(value: Any, scale: str, *, swapped: bool) -> int | None:
    """The answer in canonical orientation: + means ``item.app_a`` is better (``swapped`` shows app_b as A)."""
    score = displayed_score(value, scale)
    if score is None:
        return None
    return -score if swapped else score


@dataclass
class _Comparison:
    item_id: str
    app_a: str
    app_b: str
    canonical: int

    @property
    def score_a(self) -> float:
        return 1.0 if self.canonical > 0 else 0.0 if self.canonical < 0 else 0.5


def _competition_ranks(strengths: dict[str, float]) -> dict[str, int]:
    rounded = {key: round(value, 9) for key, value in strengths.items()}
    return {key: 1 + sum(1 for other in rounded.values() if other > value) for key, value in rounded.items()}


def _tallies(pairs: Iterable[tuple[str, str, float]]) -> dict[str, Counter]:
    tallies: dict[str, Counter] = defaultdict(Counter)
    for a, b, score in pairs:
        for key, points in ((a, score), (b, 1 - score)):
            tallies[key]["comparisons"] += 1
            tallies[key]["wins" if points == 1 else "losses" if points == 0 else "ties"] += 1
    return tallies


def _bt_entry(
    key: str,
    label: str,
    component: str,
    strength: float,
    ci: tuple[float, float] | None,
    tally: Counter,
    rank: int,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "component": component,
        "strength": _r(strength),
        "log_strength": _r(math.log(strength)),
        "ci": _ci(ci),
        "wins": tally["wins"],
        "losses": tally["losses"],
        "ties": tally["ties"],
        "comparisons": tally["comparisons"],
        "rank": rank,
    }


def _bt_by_app(ctx: _Context, comparisons: list[_Comparison], seed_parts: tuple[Any, ...]) -> list[dict[str, Any]]:
    triples = [(c.app_a, c.app_b, c.score_a) for c in comparisons]
    tallies = _tallies(triples)
    clusters: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    for comparison in comparisons:
        clusters[comparison.item_id].append((comparison.app_a, comparison.app_b, comparison.score_a))
    entries = []
    components = stats.bt_components(triples, sorted(tallies))
    components.sort(key=lambda members: min(ctx.apps[app_id].label for app_id in members))
    for number, members in enumerate(components, start=1):
        member_set = set(members)
        own = [triple for triple in triples if triple[0] in member_set]
        strengths = stats.bradley_terry(own, members)
        item_clusters = [rows for rows in clusters.values() if rows[0][0] in member_set]
        cis: dict[Any, tuple[float, float] | None] = dict.fromkeys(members)
        if len(item_clusters) >= stats.MIN_CLUSTERS_FOR_CI:

            def resample(rng, pool=item_clusters):
                k = len(pool)
                drawn: list[tuple[str, str, float]] = []
                for _ in range(k):
                    drawn.extend(pool[int(rng.random() * k)])
                return drawn

            cis = stats.bootstrap_bt(
                resample,
                members,
                seed=ctx.seed_for(*seed_parts, "by_app", number),
                init=strengths,
            )
        ranks = _competition_ranks(strengths)
        entries.extend(
            _bt_entry(
                app_id,
                ctx.apps[app_id].label,
                str(number),
                strengths[app_id],
                cis[app_id],
                tallies[app_id],
                ranks[app_id],
            )
            for app_id in sorted(members, key=lambda key: (ranks[key], ctx.apps[key].label))
        )
    return entries


def _bt_by_group(ctx: _Context, comparisons: list[_Comparison], seed_parts: tuple[Any, ...]) -> list[dict[str, Any]]:
    cross = [c for c in comparisons if ctx.group_of(c.app_a) != ctx.group_of(c.app_b)]
    triples = [(ctx.group_of(c.app_a), ctx.group_of(c.app_b), c.score_a) for c in cross]
    tallies = _tallies(triples)
    labels = {group.key: group.label for group in ctx.groups}
    participating: dict[str, set[str]] = defaultdict(set)
    pair_totals: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for comparison in cross:
        participating[ctx.group_of(comparison.app_a)].add(comparison.app_a)
        participating[ctx.group_of(comparison.app_b)].add(comparison.app_b)
        totals = pair_totals[(comparison.app_a, comparison.app_b)]
        totals[0] += comparison.score_a
        totals[1] += 1
    app_pairs = [
        (a, b, ctx.group_of(a), ctx.group_of(b), points / games, games)
        for (a, b), (points, games) in pair_totals.items()
    ]
    entries = []
    for number, members in enumerate(stats.bt_components(triples, sorted(tallies)), start=1):
        member_set = set(members)
        own = [triple for triple in triples if triple[0] in member_set]
        strengths = stats.bradley_terry(own, members)
        own_pairs = [row for row in app_pairs if row[2] in member_set]
        pools = {group: sorted(participating[group]) for group in members}

        def resample(rng, pools=pools, own_pairs=own_pairs):
            multiplicity: Counter = Counter()
            for apps_in_group in pools.values():
                k = len(apps_in_group)
                for _ in range(k):
                    multiplicity[apps_in_group[int(rng.random() * k)]] += 1
            return [
                (ga, gb, share, games * multiplicity[a] * multiplicity[b])
                for a, b, ga, gb, share, games in own_pairs
                if multiplicity[a] and multiplicity[b]
            ]

        cis: dict[Any, tuple[float, float] | None] = dict.fromkeys(members)
        if any(len(apps_in_group) >= stats.MIN_CLUSTERS_FOR_CI for apps_in_group in pools.values()):
            cis = stats.bootstrap_bt(
                resample,
                members,
                seed=ctx.seed_for(*seed_parts, "by_group", number),
                init=strengths,
            )
        ranks = _competition_ranks(strengths)
        entries.extend(
            _bt_entry(
                group,
                labels.get(group, group),
                str(number),
                strengths[group],
                cis[group] if len(pools[group]) >= stats.MIN_CLUSTERS_FOR_CI else None,
                tallies[group],
                ranks[group],
            )
            for group in sorted(members, key=lambda key: (ranks[key], labels.get(key, key)))
        )
    return entries


def _pairwise_block(ctx: _Context, block: dict[str, Any]) -> dict[str, Any]:
    block_id = block["id"]
    scale = block.get("scale") or "ab_tie"
    criteria = [("overall", "Overall preference")] if block.get("overall") else []
    criteria += [(criterion["id"], criterion["label"]) for criterion in block.get("criteria") or []]
    results = []
    for criterion_id, label in criteria:
        comparisons: list[_Comparison] = []
        position = Counter()
        for record in ctx.records:
            answers = _dict(record.answers.get(block_id))
            raw = (
                answers.get("overall")
                if criterion_id == "overall"
                else _dict(answers.get("criteria")).get(criterion_id)
            )
            item = record.assignment.item
            if item.app_b_id is None:
                continue
            shown = displayed_score(raw, scale)
            if shown is None:
                continue
            position["left" if shown > 0 else "right" if shown < 0 else "ties"] += 1
            app_a, app_b = str(item.app_a_id), str(item.app_b_id)
            if app_a not in ctx.apps or app_b not in ctx.apps:
                continue
            canonical = -shown if record.assignment.swapped else shown
            comparisons.append(_Comparison(record.item_id, app_a, app_b, canonical))
        p_value = None
        if not ctx.provisional:
            p_value = _round_p(stats.binomial_two_sided(position["left"], position["left"] + position["right"]))
            if p_value is not None and p_value < POSITION_BIAS_ALPHA:
                ctx.warn(
                    "position_bias",
                    f"{block['title']} / {label}: the left-hand app won {position['left']} times and the right-hand "
                    f"app {position['right']} times (exact binomial p = {p_value}).",
                    block_id,
                )
        units: dict[str, list[int]] = defaultdict(list)
        for comparison in comparisons:
            units[comparison.item_id].append(comparison.canonical)
        tallies = _tallies((c.app_a, c.app_b, c.score_a) for c in comparisons)
        ctx.metrics.append(
            _Metric(
                block_id,
                criterion_id,
                f"{block['title']}: {label} (preference share)",
                {
                    app_id: (tally["wins"] + tally["ties"] / 2) / tally["comparisons"]
                    for app_id, tally in tallies.items()
                    if tally["comparisons"]
                },
                None,
            ),
        )
        seed_parts = ("pairwise", block_id, criterion_id)
        results.append(
            {
                "criterion_id": criterion_id,
                "label": label,
                "n_comparisons": len(comparisons),
                "by_group": _bt_by_group(ctx, comparisons, seed_parts),
                "by_app": _bt_by_app(ctx, comparisons, seed_parts),
                "position_bias": {
                    "left": position["left"],
                    "right": position["right"],
                    "ties": position["ties"],
                    "p_value": p_value,
                },
                "agreement": _agreement(ctx, block_id, label, units.values(), "ordinal"),
            },
        )
    return {"type": "pairwise", "block_id": block_id, "title": block["title"], "criteria": results}


_BLOCK_ANALYZERS: dict[str, Callable[[_Context, dict[str, Any]], dict[str, Any]]] = {
    "rating": _rating_block,
    "requirements": _requirements_block,
    "security": _security_block,
    "scenarios": _scenarios_block,
    "questionnaire": _questionnaire_block,
    "bugs": _bugs_block,
    "pairwise": _pairwise_block,
}


# ---------------------------------------------------------------------------
# Contrasts
# ---------------------------------------------------------------------------


def _baseline_key(ctx: _Context) -> str | None:
    baseline = _dict(ctx.settings.get("pairing")).get("baseline_condition_id")
    if baseline is None:
        baseline = studies.baseline_condition_id(ctx.task.study_id)
    return None if baseline is None else str(baseline)


def _cells(ctx: _Context, apps: Iterable[str], values: dict[str, float]) -> dict[tuple[str, Any], list[float]]:
    cells: dict[tuple[str, Any], list[float]] = defaultdict(list)
    for app_id in apps:
        snapshot = ctx.apps[app_id].snapshot
        slug = snapshot.get("app_slug") or ""
        if app_id in values and slug:
            cells[(slug, snapshot.get("repeat_index"))].append(values[app_id])
    return cells


def _paired_deltas(
    group_cells: dict[tuple[str, Any], list[float]],
    base_cells: dict[tuple[str, Any], list[float]],
    common: Iterable[tuple[str, Any]],
) -> list[float]:
    """Per-cell differences of means, rounded to :data:`DELTA_DIGITS` decimals.

    Mathematically equal means (composites, compliance shares) often differ by ~1e-16 after
    floating-point summation; the signed-rank test drops only exact zeros and ranks ``|d|``
    exactly, so that residue would count as a real difference and split true ties. Rounding
    snaps it to an exact tie without changing any reported (4-decimal) value.
    """
    return [round(stats.mean(group_cells[cell]) - stats.mean(base_cells[cell]), DELTA_DIGITS) for cell in common]


def _contrasts(ctx: _Context) -> list[dict[str, Any]]:
    if ctx.group_by != "condition" or not ctx.metrics:
        return []
    baseline = _baseline_key(ctx)
    groups = {group.key: group for group in ctx.groups}
    if baseline is None or baseline not in groups:
        message = (
            "No baseline condition is set (pairing.baseline_condition_id or the study's baseline): "
            "between-condition contrasts are not computed."
            if baseline is None
            else "The baseline condition has no apps in this task: between-condition contrasts are not computed."
        )
        ctx.warn("no_baseline", message)
        return []
    primary = _dict(_dict(ctx.design.get("analysis")).get("primary"))
    base = groups[baseline]
    rows: list[dict[str, Any]] = []
    raw_p: list[float | None] = []
    for metric in ctx.metrics:
        values = _policy_values(ctx, metric.values, metric.floor_value)
        base_cells = _cells(ctx, base.apps, values)
        base_values = [values[app_id] for app_id in base.apps if app_id in values]
        for group in ctx.groups:
            if group.key == baseline:
                continue
            group_cells = _cells(ctx, group.apps, values)
            common = sorted(set(group_cells) & set(base_cells), key=str)
            seed = ctx.seed_for("contrast", metric.block_id, metric.metric, group.key)
            p_value = None
            if len(common) >= stats.MIN_CLUSTERS_FOR_CI:
                deltas = _paired_deltas(group_cells, base_cells, common)
                diff = stats.mean(deltas)
                ci = stats.cluster_bootstrap_mean_ci(deltas, seed)
                p_value = stats.wilcoxon_p(deltas)
                paired = True
            else:
                group_values = [values[app_id] for app_id in group.apps if app_id in values]
                diff = stats.mean(group_values) - stats.mean(base_values) if group_values and base_values else None
                ci = stats.bootstrap_diff_ci(group_values, base_values, seed)
                paired = False
            raw_p.append(None if ctx.provisional else p_value)
            rows.append(
                {
                    "block_id": metric.block_id,
                    "metric": metric.metric,
                    "label": metric.label,
                    "group": group.key,
                    "group_label": group.label,
                    "baseline": base.key,
                    "baseline_label": base.label,
                    "diff": _r(diff),
                    "ci": _ci(ci),
                    "paired": paired,
                    "n_cells": len(common),
                    "p_value": None,
                    "p_adjusted": None,
                    "primary": primary.get("block_id") == metric.block_id and primary.get("metric") == metric.metric,
                },
            )
    adjusted = holm(raw_p)
    for row, p_value, p_adjusted in zip(rows, raw_p, adjusted, strict=True):
        row["p_value"] = _round_p(p_value)
        row["p_adjusted"] = p_adjusted
    return rows


# ---------------------------------------------------------------------------
# Human vs automated
# ---------------------------------------------------------------------------


def _static_density(snapshot: dict[str, Any], common: set[str] | None) -> tuple[set[str], float | None]:
    """The app's static tools, and its findings per kLOC over ``common`` tools (None when not computable)."""
    static = _dict(snapshot.get("static_findings"))
    loc = _number(static.get("loc"))
    raw = static.get("by_tool") if isinstance(static.get("by_tool"), dict) else static.get("tools")
    per_tool: dict[str, float] | None = None
    tools: set[str] = set()
    if isinstance(raw, dict):
        per_tool = {str(tool): _number(count) or 0.0 for tool, count in raw.items()}
        tools = set(per_tool)
    elif isinstance(raw, list):
        dict_entries = [entry for entry in raw if isinstance(entry, dict)]
        if dict_entries:
            per_tool = {
                str(entry.get("slug") or entry.get("tool")): _number(entry.get("count", entry.get("findings"))) or 0.0
                for entry in dict_entries
                if entry.get("slug") or entry.get("tool")
            }
            tools = set(per_tool)
        else:
            tools = {str(entry) for entry in raw if isinstance(entry, str)}
    if common is None or not loc or loc <= 0 or not common or not common <= tools:
        return tools, None
    if per_tool is not None:
        count = sum(per_tool.get(tool, 0.0) for tool in common)
    elif tools == common and _number(static.get("count")) is not None:
        count = float(static["count"])
    else:
        return tools, None
    return tools, count / (loc / 1000)


def _human_vs_automated(ctx: _Context) -> list[dict[str, Any]]:
    rating = next((block for block in ctx.design.get("blocks") or [] if block.get("type") == "rating"), None)
    if rating is None:
        return []
    composite = next(
        (metric.values for metric in ctx.metrics if metric.block_id == rating["id"] and metric.metric == "composite"),
        {},
    )
    version = _modal_version(ctx)
    rho, ci, n, _excluded, reasons = _correlate_within_specs(
        ctx,
        dict(composite),
        {app_id: _conformance(ctx.apps[app_id].snapshot).get("pass_rate") for app_id in ctx.pool},
        version=version,
        check_version=True,
        seed=ctx.seed_for("human_vs_automated", "conformance"),
        missing_y="no conformance result",
    )
    note = "Composite rating (first rating block) vs the conformance pass rate, on within-spec percentile ranks."
    if version is not None:
        note += f" Restricted to evaluator version {version[0] or 'unknown'} (tester {version[1][:12] or 'unknown'})."
    rows = [
        {
            "metric": "composite_vs_conformance",
            "label": f"{rating['title']}: composite vs conformance pass rate",
            "rho": rho,
            "ci": _ci(ci),
            "n": n,
            "note": note + _excluded_note(reasons),
        },
    ]
    tool_sets = []
    for app_id in ctx.pool:
        tools, _density = _static_density(ctx.apps[app_id].snapshot, None)
        static = _dict(ctx.apps[app_id].snapshot.get("static_findings"))
        if tools and (_number(static.get("loc")) or 0) > 0:
            tool_sets.append(tools)
    common = set.intersection(*tool_sets) if tool_sets else set()
    densities = {app_id: _static_density(ctx.apps[app_id].snapshot, common)[1] for app_id in ctx.pool}
    rho, ci, n, _excluded, reasons = _correlate_within_specs(
        ctx,
        dict(composite),
        densities,
        version=None,
        check_version=False,
        seed=ctx.seed_for("human_vs_automated", "static"),
        missing_y="no static analysis result over the common tools",
    )
    tools_text = ", ".join(sorted(common)) if common else "none"
    rows.append(
        {
            "metric": "composite_vs_static_findings_per_kloc",
            "label": f"{rating['title']}: composite vs static findings per kLOC",
            "rho": rho,
            "ci": _ci(ci),
            "n": n,
            "note": (
                "Static analysis findings per kLOC counted over the tools that measured every app "
                f"({tools_text}); not deduplicated; AI, dynamic and dependency tools excluded; on within-spec "
                "percentile ranks." + _excluded_note(reasons)
            ),
        },
    )
    return rows


# ---------------------------------------------------------------------------
# Confirmatory status, warnings, quality table
# ---------------------------------------------------------------------------


def _deviations(
    task: EvalTask,
    design: dict[str, Any],
    settings: dict[str, Any],
    participants: dict[str, Participant],
    assignments: list[Assignment],
) -> list[str]:
    """Why the results are not confirmatory (TS ``TaskResults.deviations``); empty when confirmatory.

    Planned sample: balanced → every item has ``planned_ratings_per_item`` (default: the frozen
    ``allocation.ratings_per_item``) submitted, not manually excluded ratings, counting
    ``app_broken`` skips (a broken app can never be rated); all → ``planned_participants``
    participants (not excluded) completed or did every item. "Every item" is the plan frozen at
    first open: items are built once, then, so an item deactivated later (its app taken out of
    the pool) still counts, and dropping under-rated apps cannot hide an unreached plan. Rule
    exclusions do not count against the plan (they are analysis-time decisions fixed in advance).

    ``post_hoc_exclusions``: a manual exclusion with ``excluded_at`` after ``opened_at``, or a
    participant with data deleted after opening (a ``participant_deleted`` protocol-log entry):
    deleting a rater removes their rows, which is a post-hoc exclusion that leaves no row behind.
    """
    analysis_plan = _dict(design.get("analysis"))
    reasons: set[str] = set()
    closed = task.status in (EvalTask.Status.CLOSED, EvalTask.Status.ARCHIVED)
    if not closed:
        reasons.add("provisional")
    if task.first_closed_at is not None and (not closed or task.closed_at != task.first_closed_at):
        reasons.add("reopened_after_close")
    if analysis_plan.get("primary") is None:
        reasons.add("no_primary")
    if task.opened_at is not None:
        late = [
            row
            for row in [*participants.values(), *assignments]
            if row.excluded and row.excluded_at is not None and row.excluded_at > task.opened_at
        ]
        if late or _participants_deleted_after_open(task):
            reasons.add("post_hoc_exclusions")

    planned_items = set(EvalItem.objects.filter(task=task).values_list("id", flat=True))
    counted = [
        row
        for row in assignments
        if not row.excluded and not participants[str(row.participant_id)].excluded and row.item_id in planned_items
    ]
    if settings["allocation"].get("strategy") == "all":
        planned = analysis_plan.get("planned_participants")
        if planned is None:
            reasons.add("no_planned_sample")
        else:
            done_items: dict[str, set[Any]] = defaultdict(set)
            for row in counted:
                if row.status in (Assignment.Status.SUBMITTED, Assignment.Status.SKIPPED):
                    done_items[str(row.participant_id)].add(row.item_id)
            complete = sum(
                1
                for key, participant in participants.items()
                if not participant.excluded
                and participant.status != Participant.Status.WITHDRAWN
                and (
                    participant.status == Participant.Status.COMPLETED
                    or (planned_items and done_items.get(key, set()) >= planned_items)
                )
            )
            if complete < planned:
                reasons.add("stopped_early")
    else:
        planned = analysis_plan.get("planned_ratings_per_item") or settings["allocation"].get("ratings_per_item")
        per_item: Counter = Counter()
        for row in counted:
            if row.status == Assignment.Status.SUBMITTED or (
                row.status == Assignment.Status.SKIPPED and row.skip_reason == "app_broken"
            ):
                per_item[row.item_id] += 1
        if not planned_items or any(per_item[item_id] < planned for item_id in planned_items):
            reasons.add("stopped_early")
    return [reason for reason in DEVIATION_ORDER if reason in reasons]


def _participants_deleted_after_open(task: EvalTask) -> int:
    """How many participants with data were deleted after the task opened (``protocol_log`` entries).

    ``participants.delete_participant`` logs ``{"field": "participant_deleted:<number>", ...}`` when
    it deletes, after opening, a non-preview participant with a submitted or skipped assignment.
    """
    return sum(1 for entry in task.protocol_log or [] if is_participant_deletion(entry))


def is_participant_deletion(entry: Any) -> bool:
    """Whether a ``task.protocol_log`` entry records a participant deleted after opening."""
    if not isinstance(entry, dict):
        return False
    return str(entry.get("field") or "").split(":", 1)[0] == PROTOCOL_PARTICIPANT_DELETED


def _warnings(ctx: _Context, assignments: list[Assignment], skips: list[Assignment]) -> None:
    if ctx.provisional:
        ctx.warnings.insert(
            0,
            {
                "code": "provisional",
                "message": "The task is still collecting data: results are provisional and p-values are withheld.",
            },
        )
    rated = _rated_apps(ctx)
    ratings = sum(len(ctx.records_by_app[app_id]) for app_id in rated)
    if not rated:
        ctx.warn("low_n", "No included submissions yet.")
    elif len(rated) < LOW_N_APPS or ratings / len(rated) < LOW_N_RATERS:
        ctx.warn(
            "low_n",
            f"{len(rated)} rated apps with {ratings / len(rated):.1f} raters per app on average "
            f"(fewer than {LOW_N_APPS} apps or {LOW_N_RATERS} raters per app): estimates are imprecise.",
        )
    low: dict[str, list[str]] = defaultdict(list)
    for block_id, label, agreement in ctx.agreements:
        alpha = agreement["alpha"]
        if alpha is not None and alpha < LOW_AGREEMENT_ALPHA and agreement["n_units"] >= LOW_AGREEMENT_MIN_UNITS:
            low[block_id].append(f"{label} (alpha = {alpha})")
    for block_id, labels in low.items():
        block = block_by_id(ctx.design, block_id) or {}
        ctx.warn(
            "low_agreement",
            f"{block.get('title', block_id)}: Krippendorff's alpha below {LOW_AGREEMENT_ALPHA} for "
            + "; ".join(labels),
            block_id,
        )

    per_item: Counter = Counter(record.item_id for record in ctx.records)
    broken_items = {
        str(row.item_id) for row in skips if row.skip_reason == "app_broken" and not per_item.get(str(row.item_id))
    }
    active_items = [
        str(item_id)
        for item_id in EvalItem.objects.filter(task=ctx.task, is_active=True).values_list("id", flat=True)
        if str(item_id) not in broken_items
    ]
    if active_items:
        counts = [per_item.get(item_id, 0) for item_id in active_items]
        if max(counts) > 2 * max(min(counts), 1):
            ctx.warn(
                "unbalanced_coverage",
                f"Ratings per item range from {min(counts)} to {max(counts)}: items are unevenly covered.",
            )
    reasons = Counter(row.skip_reason for row in skips if row.skip_reason in UNAVAILABLE_REASONS)
    if reasons:
        parts = []
        if reasons["app_broken"]:
            parts.append(f"{reasons['app_broken']} because the app was broken")
        if reasons["platform_unavailable"]:
            parts.append(f"{reasons['platform_unavailable']} because the platform could not serve the app")
        message = "Skipped assignments: " + ", ".join(parts) + "."
        if ctx.unevaluable:
            policy = "counted at the floor" if ctx.policy == "floor" else "left out (complete-case)"
            message += f" {len(ctx.unevaluable)} app(s) could not be evaluated at all and are {policy}."
        ctx.warn("app_unavailable", message)
    versions = {
        version for app_id in ctx.pool if (version := _conformance_version(ctx.apps[app_id].snapshot)) is not None
    }
    if len(versions) > 1:
        ctx.warn(
            "mixed_evaluator_versions",
            f"Apps were graded by {len(versions)} conformance evaluator/tester versions; comparisons with the "
            "conformance suite use the most common one only.",
        )
    reset = _dict(ctx.settings.get("hosting")).get("reset", "none")
    if reset == "none":
        submitted_per_app: Counter = Counter()
        for row in assignments:
            if row.status == Assignment.Status.SUBMITTED:
                for _slot, app_id in _slots(row):
                    submitted_per_app[app_id] += 1
        if any(count > 1 for count in submitted_per_app.values()):
            ctx.warn(
                "shared_app_state",
                "Apps are not reset between assignments: later raters may have seen data or changes left by "
                "earlier raters of the same app.",
            )
    elif reset == "per_assignment":
        unreset = _unreset_records(ctx)
        if unreset:
            ctx.warn(
                "shared_app_state",
                f"{len(unreset)} included submitted assignment(s) used an app whose state earlier assignments had "
                "already used (hosting provenance prior_users > 0): the per-assignment reset could not run, e.g. "
                "while another open assignment or task shared the container. Those raters may have seen data or "
                "changes left by others (see the _prior_users columns of assignments.csv).",
            )
    _review_item_warnings(ctx)
    hashes = {
        str(_dict(row.context).get("instructions_sha256"))
        for row in assignments
        if _dict(row.context).get("instructions_sha256")
    }
    if len(hashes) > 1:
        ctx.warn(
            "instructions_changed",
            f"Participants saw {len(hashes)} different versions of the task instructions (see the protocol log).",
        )


def _unreset_records(ctx: _Context) -> list[_Record]:
    """Included submissions with a slot whose recorded hosting provenance has ``prior_users > 0``."""
    unreset = []
    for record in ctx.records:
        hosting = _dict(record.context.get("hosting"))
        if any((_int_value(_dict(hosting.get(slot)).get("prior_users")) or 0) > 0 for slot, _app in record.slots):
            unreset.append(record)
    return unreset


def _shown_item_sets(ctx: _Context, block_id: str, key: str) -> dict[str, set[frozenset[Any]]]:
    """app id → the distinct item sets the included raters were shown in ``context.blocks[block_id][key]``.

    Requirements compare ``(id, text)``, findings their ids. Contexts without a frozen list for a
    slot (legacy rows) are skipped.
    """
    shown: dict[str, set[frozenset[Any]]] = defaultdict(set)
    for record in ctx.records:
        lists = _dict(record.block_context(block_id).get(key))
        for slot, app_id in record.slots:
            raw = lists.get(slot)
            if not isinstance(raw, list):
                continue
            entries = [entry for entry in raw if isinstance(entry, dict) and entry.get("id") is not None]
            if key == "requirements":
                items = frozenset((str(entry["id"]), str(entry.get("text") or "")) for entry in entries)
            else:
                items = frozenset(str(entry["id"]) for entry in entries)
            shown[app_id].add(items)
    return shown


def _review_item_warnings(ctx: _Context) -> None:
    """``review_items_differ``: raters of one app saw different requirement / finding lists.

    Each assignment's list is built when it is first opened; content deleted or changed after the
    task opened (a deleted analysis run or generation run) gives later raters a different or shorter list,
    so agreement and per-app values mix item sets. Finding lists are also checked against the
    app's frozen sample (``finding_sample``): fewer shown findings than sampled means the
    sampling frame's weights describe findings no rater could review.
    """
    for block in ctx.design.get("blocks") or []:
        block_type = block.get("type")
        if block_type == "requirements":
            key, noun = "requirements", "requirement"
        elif block_type == "security" and _dict(block.get("finding_review")).get("enabled"):
            key, noun = "findings", "finding"
        else:
            continue
        shown = _shown_item_sets(ctx, block["id"], key)
        differ = sorted(ctx.apps[app_id].label for app_id, sets in shown.items() if len(sets) > 1)
        partial: list[str] = []
        if key == "findings":
            for app_id, sets in shown.items():
                app = ctx.apps[app_id].obj
                if app.finding_sample_at is None:
                    continue
                sample = {str(finding_id) for finding_id in app.finding_sample or []}
                if any(items != sample for items in sets):
                    partial.append(ctx.apps[app_id].label)
            partial.sort()
        parts = []
        if differ:
            parts.append(
                f"raters of {len(differ)} app(s) saw different {noun} lists ({', '.join(differ)}): content "
                "changed or was deleted after the task opened, so these apps' values and agreement mix item sets",
            )
        if partial:
            parts.append(
                f"{len(partial)} app(s) showed raters a different finding set than their frozen sample "
                f"({', '.join(partial)}): findings were deleted after sampling, so precision and its weights rest "
                "on a partial sample",
            )
        if parts:
            title = block.get("title") or block["id"]
            ctx.warn("review_items_differ", f"{title}: " + "; ".join(parts) + ".", block["id"])


def _quality_rows(
    participants: dict[str, Participant],
    assignments: list[Assignment],
    report: quality_service.QualityReport,
) -> list[dict[str, Any]]:
    by_participant: dict[str, list[Assignment]] = defaultdict(list)
    for row in assignments:
        by_participant[str(row.participant_id)].append(row)
    rows = []
    for key, participant in sorted(participants.items(), key=lambda entry: entry[1].number):
        own = by_participant.get(key, [])
        submitted = [row for row in own if row.status == Assignment.Status.SUBMITTED]
        flags_of = [report.assignments.get(str(row.pk), []) for row in submitted]
        seconds = [quality_service.item_seconds(row) for row in submitted]
        rows.append(
            {
                "participant_id": key,
                "label": participant.label,
                "kind": participant.kind,
                "excluded": participant.excluded,
                "submitted": len(submitted),
                "skipped": sum(1 for row in own if row.status == Assignment.Status.SKIPPED),
                "median_seconds": _r(stats.median(seconds), 1),
                "speeding": sum(1 for flags in flags_of if quality_service.SPEEDING in flags),
                "straightlining": sum(1 for flags in flags_of if quality_service.STRAIGHTLINING in flags),
                "attention_failed": report.participant_attention_failures.get(key, 0)
                + sum(report.assignment_attention_failures.get(str(row.pk), 0) for row in submitted),
                "flags": report.participants.get(key, []),
            },
        )
    return rows
