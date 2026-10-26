"""Canonical measured runs: the one selection every analysis consumer reads.

Rankings, reports, statistics and exports never recount raw ``Finding`` rows
with rules of their own. They pick each generation job's canonical run
(``measurement.canonical_run_ids``) under ONE frozen plan, read the run's
stored ``measurement`` and ``measurement.outcomes()``, and report every job
that could not be measured explicitly (not analysed, analysis failed, only
incomplete runs, analysed under another plan, excluded by the eligibility
rules) instead of letting it count as zero findings.

Plan choice: runs are comparable iff their ``plan_hash`` is equal. Without an
explicit ``plan_hash`` the *reference plan* is the one that can produce the
ranked outcome for the most jobs: most jobs with an available static
measurement first (an AI-reviewer-only re-review of the corpus has none and
never takes over), then the broader static tool set (a narrower re-run of
the same jobs, e.g. a quick check, never replaces the full measurement),
then the most completely measured jobs, then the most recently used plan.

Functional (conformance) results are read the same way everywhere: from the
run's measurement snapshot (``functional_rate``), counted only when the
conformance evaluator produced them (``source``), and ``not_applicable`` /
``infra_error`` / ``not_evaluated`` are missing data, never a 0% pass rate.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Count
from django.db.models import Max
from django.db.models import Q
from django.db.models.fields.json import KT

from backend.analysis.engine import measurement as measurement_engine
from backend.analysis.engine import sample as sample_engine
from backend.analysis.models import AnalysisRun

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

SEVERITIES = ("critical", "high", "medium", "low", "info")
# Measurement JSON schemas consumers read: the one they were written against
# and the engine's current one (stage versions such as ``measure-v2`` change
# rules, not the shape; they are reported through ``engine_versions``).
MEASUREMENT_SCHEMA_VERSIONS = tuple(sorted({1, measurement_engine.MEASUREMENT_SCHEMA_VERSION}))
# Reasons a canonical run's *measurement* is unusable (as opposed to the
# sample being ineligible): the job is still a trial, just not measured.
MEASUREMENT_PROBLEMS = frozenset({"no_measurement", "unsupported_measurement", "measurement_error"})
# The one functional instrument consumers count: the hidden conformance
# evaluator (``generation.services.evaluation``). The runtime smoke probe
# (health + declared GET endpoints) is a different, more lenient instrument.
FUNCTIONAL_SOURCE = "conformance"
# Functional statuses that carry no pass rate (the evaluator could not judge
# the app, or the job has no app spec): missing data, never 0%.
FUNCTIONAL_MISSING_STATUSES = frozenset({"infra_error", "not_applicable", "not_evaluated"})
PLAN_RULE_EXPLICIT = "explicit"
PLAN_RULE_AUTO = "most_static_measured_jobs"


def zero_severities() -> dict[str, int]:
    return dict.fromkeys(SEVERITIES, 0)


def severity_counts(raw: Any) -> dict[str, int]:
    """Zero-filled ``{severity: int}`` from a stored by-severity dict (unknown keys dropped)."""
    out = zero_severities()
    if isinstance(raw, dict):
        for sev in SEVERITIES:
            value = raw.get(sev)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[sev] = int(value)
    return out


def add_severities(into: dict[str, int], other: dict[str, int] | None) -> None:
    for sev in SEVERITIES:
        into[sev] += int((other or {}).get(sev, 0) or 0)


def job_key(value: Any) -> Any:
    """Job ids as the ORM returns them (UUID), whatever form the caller passed."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def functional_is_conformance(block: Any) -> bool:
    """False when a functional record names an instrument other than the conformance evaluator."""
    if not isinstance(block, dict):
        return False
    return block.get("source") in (None, "", FUNCTIONAL_SOURCE)


def functional_rate(block: Any, value: Any = None) -> float | None:
    """A functional record's conformance pass rate in [0, 1], else None (missing data).

    *value* overrides ``block['pass_rate']`` (e.g. the ``outcomes()`` copy).
    None for another instrument's result, for ``not_applicable`` /
    ``infra_error`` / ``not_evaluated`` (whatever number was stored with
    them) and for anything that is not a rate.
    """
    if not functional_is_conformance(block):
        return None
    if str(block.get("status") or "") in FUNCTIONAL_MISSING_STATUSES:
        return None
    rate = _number(block.get("pass_rate") if value is None else value)
    if rate is None or not 0.0 <= rate <= 1.0:
        return None
    return rate


def static_tool_count(plan: Any) -> int:
    """Static tools in a frozen plan (the AI reviewer is configured apart, under ``reviewer``)."""
    tools = plan.get("tools") if isinstance(plan, dict) else None
    if not isinstance(tools, list):
        return 0
    return sum(1 for t in tools if isinstance(t, dict) and t.get("kind") != "ai" and t.get("slug") != "ai_review")


@dataclass(frozen=True)
class MeasuredRun:
    """One job's canonical run, read through its stored measurement (§4.4)."""

    run_id: Any
    job_id: Any
    plan_hash: str
    status: str
    is_complete: bool
    measurement: dict[str, Any]
    outcomes: dict[str, Any]
    target_meta: dict[str, Any]

    @property
    def static(self) -> dict[str, Any]:
        static = self.measurement.get("static")
        return static if isinstance(static, dict) else {}

    @property
    def static_available(self) -> bool:
        """False when a required static tool failed: its totals are missing data, not zeros."""
        return bool(self.static.get("available"))

    def findings_by_severity(self) -> dict[str, int] | None:
        """Counted static findings by severity, or None when the static measurement is unavailable."""
        if not self.static_available:
            return None
        return severity_counts((self.static.get("findings") or {}).get("by_severity"))

    def findings_total(self) -> int | None:
        if not self.static_available:
            return None
        total = _number((self.static.get("findings") or {}).get("total"))
        return int(total) if total is not None else sum(self.findings_by_severity().values())

    def issues_by_severity(self) -> dict[str, int] | None:
        """Counted static issues (deduplicated across tools) by severity."""
        if not self.static_available:
            return None
        return severity_counts((self.static.get("issues") or {}).get("by_severity"))

    def issues_total(self) -> int | None:
        if not self.static_available:
            return None
        total = _number((self.static.get("issues") or {}).get("total"))
        return int(total) if total is not None else sum(self.issues_by_severity().values())

    @property
    def ai(self) -> dict[str, Any] | None:
        ai = self.measurement.get("ai")
        return ai if isinstance(ai, dict) else None

    @property
    def ai_available(self) -> bool:
        return bool(self.ai and self.ai.get("available"))

    def ai_by_severity(self) -> dict[str, int] | None:
        """Consensus-counted AI findings by severity; None when no (successful) AI review."""
        if not self.ai_available:
            return None
        return severity_counts((self.ai.get("consensus") or {}).get("by_severity"))

    def ai_total(self) -> int | None:
        if not self.ai_available:
            return None
        total = _number((self.ai.get("consensus") or {}).get("total"))
        return int(total) if total is not None else sum(self.ai_by_severity().values())

    def measured_sloc(self) -> int | None:
        value = _number((self.measurement.get("sloc") or {}).get("measured"))
        return int(value) if value is not None else None

    def total_sloc(self) -> int | None:
        value = _number((self.measurement.get("sloc") or {}).get("total"))
        return int(value) if value is not None else None

    @property
    def functional(self) -> dict[str, Any]:
        """The measurement's functional snapshot (taken from ``target_meta`` at submission)."""
        block = self.measurement.get("functional")
        return block if isinstance(block, dict) else {}

    def functional_pass_rate(self) -> float | None:
        """The run's conformance pass rate, None when missing (never ``float(None)``, never a guess).

        Another instrument's result, ``not_applicable``, ``infra_error`` and
        ``not_evaluated`` are missing data (see :func:`functional_rate`).
        """
        return functional_rate(self.functional, self.outcomes.get("functional_pass_rate"))

    def functional_status(self) -> str:
        return str(self.functional.get("status") or "")

    def functional_source(self) -> str | None:
        return self.functional.get("source") or None

    def functional_evaluator_version(self) -> Any:
        return self.functional.get("evaluator_version")

    def cost_usd(self) -> float | None:
        """The AI review's spend; None when unknown (never a guessed 0)."""
        return _number((self.ai or {}).get("cost_usd"))

    def cost_complete(self) -> bool | None:
        """Whether :meth:`cost_usd` covers every call; None when the measurement does not say."""
        value = (self.ai or {}).get("cost_complete")
        return value if isinstance(value, bool) else None

    def engine_versions(self) -> dict[str, str]:
        """Engine stage versions that produced this measurement (``measurement.engine_versions``).

        Older measurements only carry their own ``version``; it is reported
        as the ``measurement`` stage.
        """
        raw = self.measurement.get("engine_versions")
        out = {str(k): str(v) for k, v in raw.items() if v} if isinstance(raw, dict) else {}
        version = self.measurement.get("version")
        if version and "measurement" not in out:
            out["measurement"] = str(version)
        return out


@dataclass
class Selection:
    """Which jobs have a usable measurement under one plan, and why the rest do not."""

    plan_hash: str | None
    explicit_plan: bool
    complete_only: bool
    # Completely measured jobs per plan, ordered by the auto-selection rule
    # (the first entry is the plan auto-selection picks).
    plan_counts: dict[str, int] = field(default_factory=dict)
    # Per plan: jobs, jobs with an available static measurement, static tools
    # in the plan, last use — what the auto-selection weighed.
    plan_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    measured: dict[Any, MeasuredRun] = field(default_factory=dict)
    excluded: dict[Any, list[str]] = field(default_factory=dict)
    incomplete: set = field(default_factory=set)
    other_plan: set = field(default_factory=set)
    analysis_failed: set = field(default_factory=set)
    in_progress: set = field(default_factory=set)
    not_analysed: set = field(default_factory=set)
    rules: dict[str, Any] = field(default_factory=dict)

    def runs_for(self, job_ids: Iterable[Any]) -> list[MeasuredRun]:
        """Included measured runs of *job_ids* (in a stable order)."""
        keys = (job_key(j) for j in job_ids)
        out = [self.measured[k] for k in keys if k in self.measured]
        return sorted(out, key=lambda r: str(r.job_id))

    def status_of(self, job_id: Any) -> str:
        """``measured | excluded | incomplete | other_plan | analysis_failed | in_progress | not_analysed``."""
        job_id = job_key(job_id)
        if job_id in self.measured:
            return "measured"
        for name in ("excluded", "incomplete", "other_plan", "analysis_failed", "in_progress"):
            if job_id in getattr(self, name):
                return name
        return "not_analysed"

    def coverage(self, job_ids: Iterable[Any] | None = None) -> dict[str, Any]:
        """Analysis funnel for all (or a subset of) the selected jobs."""
        if job_ids is None:
            ids = (
                set(self.measured)
                | set(self.excluded)
                | self.incomplete
                | self.other_plan
                | self.analysis_failed
                | self.in_progress
                | self.not_analysed
            )
        else:
            ids = {job_key(j) for j in job_ids}
        excluded_reasons: dict[str, int] = {}
        for job_id in ids & set(self.excluded):
            for reason in self.excluded[job_id]:
                excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1
        return {
            "plan_hash": self.plan_hash,
            "plan_explicit": self.explicit_plan,
            # How the plan was chosen: pinned by the caller, or auto-selected
            # (most jobs with a static measurement, then broader tool set,
            # then most measured jobs, then most recent).
            "plan_rule": PLAN_RULE_EXPLICIT if self.explicit_plan else PLAN_RULE_AUTO,
            "complete_only": self.complete_only,
            "plans": dict(self.plan_counts),
            "plan_details": {h: dict(d) for h, d in self.plan_details.items()},
            "mixed_plans": len(self.plan_counts) > 1,
            "jobs": len(ids),
            "measured": len(ids & set(self.measured)),
            "excluded": len(ids & set(self.excluded)),
            "excluded_reasons": dict(sorted(excluded_reasons.items())),
            "incomplete": len(ids & self.incomplete),
            "other_plan": len(ids & self.other_plan),
            "analysis_failed": len(ids & self.analysis_failed),
            "in_progress": len(ids & self.in_progress),
            "not_analysed": len(ids & self.not_analysed),
            "run_ids": sorted(str(self.measured[j].run_id) for j in ids & set(self.measured)),
        }


def exclusion_reasons(
    meta: dict[str, Any] | None,
    rules: dict[str, Any] | None,
    *,
    measured_sloc: int | None = None,
) -> list[str]:
    """Codes of the eligibility rules that exclude a sample (``sample.check_eligibility``).

    Empty *rules* disable exclusion entirely. *measured_sloc* (from a stored
    measurement) lets the ``min_measured_sloc`` rule apply without the sample.
    """
    if not rules:
        return []
    kwargs: dict[str, Any] = {}
    if measured_sloc is not None:
        kwargs["manifest"] = {"totals": {"measured_sloc": measured_sloc}}
    verdict = sample_engine.check_eligibility(meta or {}, None, rules, **kwargs)
    reasons = [
        str(r.get("code") or "excluded")
        for r in (verdict or {}).get("reasons", [])
        if isinstance(r, dict) and r.get("severity") == "exclude"
    ]
    if not reasons and verdict and verdict.get("eligible") is False:
        reasons = ["ineligible"]
    return reasons


def default_rules() -> dict[str, Any]:
    return dict(sample_engine.DEFAULT_EXCLUSION_RULES)


def _measured_run(run: AnalysisRun) -> MeasuredRun | str:
    """The run's measurement + outcomes, or the reason it cannot be used."""
    data = run.measurement if isinstance(run.measurement, dict) else {}
    if not data:
        return "no_measurement"
    if data.get("schema_version") not in MEASUREMENT_SCHEMA_VERSIONS:
        return "unsupported_measurement"
    try:
        outcomes = measurement_engine.outcomes(data)
    except Exception:
        # One malformed measurement must not take the whole report down; it is
        # reported as excluded (with this reason), never counted as zero.
        logger.exception("outcomes() failed for analysis run %s", run.id)
        return "measurement_error"
    return MeasuredRun(
        run_id=run.id,
        job_id=run.generation_job_id,
        plan_hash=run.plan_hash,
        status=run.status,
        is_complete=run.is_complete,
        measurement=data,
        outcomes=dict(outcomes or {}),
        target_meta=run.target_meta if isinstance(run.target_meta, dict) else {},
    )


def _job_runs(job_ids: list[Any]):
    return AnalysisRun.objects.filter(generation_job_id__in=job_ids).exclude(purpose=AnalysisRun.Purpose.CANARY)


def _static_tools_by_plan(runs, plan_hashes: Iterable[str]) -> dict[str, int]:
    """Static tool count of each plan (every run of one plan_hash froze the same plan)."""
    hashes = [h for h in plan_hashes if h]
    if not hashes:
        return {}
    rows = (
        runs.filter(plan_hash__in=hashes)
        .order_by("plan_hash", "-created_at")
        .distinct("plan_hash")
        .values_list("plan_hash", "plan")
    )
    return {plan_hash: static_tool_count(plan) for plan_hash, plan in rows}


def _ranked_plans(pool) -> dict[str, dict[str, Any]]:
    """Plans of *pool* (finished runs), best reference candidate first (see module docstring)."""
    rows = list(
        pool.order_by()
        .values("plan_hash")
        .annotate(
            n=Count("generation_job_id", distinct=True),
            n_static=Count(
                "generation_job_id",
                distinct=True,
                filter=Q(measurement__static__available=True),
            ),
            last=Max("created_at"),
        ),
    )
    static_tools = _static_tools_by_plan(pool, [r["plan_hash"] for r in rows])

    def rank(row: dict[str, Any]) -> tuple:
        last = row["last"].timestamp() if row["last"] is not None else 0.0
        return (-row["n_static"], -static_tools.get(row["plan_hash"], 0), -row["n"], -last, row["plan_hash"])

    return {
        row["plan_hash"]: {
            "jobs": int(row["n"]),
            "static_jobs": int(row["n_static"]),
            "static_tools": static_tools.get(row["plan_hash"], 0),
            "last_used": row["last"].isoformat() if row["last"] is not None else None,
        }
        for row in sorted(rows, key=rank)
    }


def select_measured(
    job_ids: Iterable[Any],
    *,
    plan_hash: str | None = None,
    complete_only: bool = True,
    rules: dict[str, Any] | None = None,
) -> Selection:
    """Canonical measured run per job under one plan (see module docstring).

    *rules* are eligibility rules (``sample.DEFAULT_EXCLUSION_RULES`` when
    None; pass ``{}`` to disable exclusion). They are judged on the run's
    ``target_meta`` snapshot taken at submission.
    """
    ids = list(dict.fromkeys(job_key(j) for j in job_ids))
    rules = default_rules() if rules is None else dict(rules)
    selection = Selection(plan_hash=plan_hash or None, explicit_plan=bool(plan_hash), complete_only=complete_only)
    selection.rules = rules
    if not ids:
        return selection

    runs = _job_runs(ids)
    finished = runs.filter(status__in=list(AnalysisRun.FINISHED_STATUSES)).exclude(plan_hash="")
    pool = finished.filter(is_complete=True) if complete_only else finished
    selection.plan_details = _ranked_plans(pool)
    selection.plan_counts = {plan: details["jobs"] for plan, details in selection.plan_details.items()}
    if selection.plan_hash is None and selection.plan_counts:
        # A plan that cannot produce the static outcome (an AI-only re-review)
        # or a narrower re-run of the same jobs never becomes the reference
        # while a plan with static measurements of as many jobs exists.
        selection.plan_hash = next(iter(selection.plan_counts))

    seen: set[Any] = set()
    if selection.plan_hash:
        run_ids = measurement_engine.canonical_run_ids(
            ids,
            plan_hash=selection.plan_hash,
            complete_only=complete_only,
        )
        for run in AnalysisRun.objects.filter(id__in=run_ids).only(
            "id",
            "generation_job_id",
            "plan_hash",
            "status",
            "is_complete",
            "measurement",
            "target_meta",
        ):
            seen.add(run.generation_job_id)
            measured = _measured_run(run)
            if isinstance(measured, str):
                selection.excluded[run.generation_job_id] = [measured]
                continue
            reasons = exclusion_reasons(measured.target_meta, rules, measured_sloc=measured.measured_sloc())
            if reasons:
                selection.excluded[run.generation_job_id] = reasons
            else:
                selection.measured[run.generation_job_id] = measured
        under_plan = set(finished.filter(plan_hash=selection.plan_hash).values_list("generation_job_id", flat=True))
        selection.incomplete = under_plan - seen
        seen |= under_plan

    analysed_elsewhere = set(pool.values_list("generation_job_id", flat=True)) - seen
    selection.other_plan = analysed_elsewhere
    seen |= analysed_elsewhere
    # Finished but incomplete under another plan only: still "incomplete" data.
    incomplete_elsewhere = set(finished.values_list("generation_job_id", flat=True)) - seen
    selection.incomplete |= incomplete_elsewhere
    seen |= incomplete_elsewhere
    remaining = [j for j in ids if j not in seen]
    if remaining:
        statuses: dict[Any, set[str]] = {}
        for job_id, status in runs.filter(generation_job_id__in=remaining).values_list("generation_job_id", "status"):
            statuses.setdefault(job_id, set()).add(status)
        for job_id in remaining:
            seen_statuses = statuses.get(job_id, set())
            if seen_statuses & {str(s) for s in AnalysisRun.ACTIVE_STATUSES}:
                selection.in_progress.add(job_id)
            elif seen_statuses:
                selection.analysis_failed.add(job_id)
            else:
                selection.not_analysed.add(job_id)
    return selection


@dataclass
class DescriptiveRuns:
    """Per job, the complete runs descriptive platform totals read (any plan).

    ``runs``: the job's representative run — the latest complete run with an
    available static measurement and the broadest static tool set, so a later
    narrower re-run (AI-only review, quick check) never replaces the full
    static measurement; a job with no static measurement at all is
    represented by its latest complete run. ``ai``: the job's latest complete
    run with a successful AI review (AI opinions are totalled apart).
    """

    runs: dict[Any, MeasuredRun] = field(default_factory=dict)
    ai: dict[Any, MeasuredRun] = field(default_factory=dict)

    @property
    def static(self) -> dict[Any, MeasuredRun]:
        """Jobs whose representative run has an available static measurement."""
        return {job_id: run for job_id, run in self.runs.items() if run.static_available}


_MEASURED_FIELDS = ("id", "generation_job_id", "plan_hash", "status", "is_complete", "measurement", "target_meta")


def descriptive_runs(job_ids: Iterable[Any], *, rules: dict[str, Any] | None = None) -> DescriptiveRuns:
    """Representative + AI runs per job for descriptive totals (see :class:`DescriptiveRuns`).

    Only for descriptive platform totals (dashboards): comparisons across
    jobs must use :func:`select_measured`, which holds the plan fixed.
    Jobs excluded by the eligibility *rules* (judged on the representative
    run's snapshot) and runs with an unusable measurement are left out.
    """
    out = DescriptiveRuns()
    ids = list(dict.fromkeys(job_key(j) for j in job_ids))
    if not ids:
        return out
    rules = default_rules() if rules is None else dict(rules)
    candidates = (
        _job_runs(ids).filter(status__in=list(AnalysisRun.FINISHED_STATUSES), is_complete=True).exclude(plan_hash="")
    )
    rows = list(
        candidates.values(
            "id",
            "generation_job_id",
            "plan_hash",
            "created_at",
            static_ok=KT("measurement__static__available"),
            ai_ok=KT("measurement__ai__available"),
        ),
    )
    static_tools = _static_tools_by_plan(candidates, {r["plan_hash"] for r in rows})
    by_job: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        by_job.setdefault(row["generation_job_id"], []).append(row)
    representative: dict[Any, Any] = {}
    ai_pick: dict[Any, Any] = {}
    for job_id, job_rows in by_job.items():
        best = max(
            job_rows,
            key=lambda r: (
                r["static_ok"] == "true",
                static_tools.get(r["plan_hash"], 0),
                r["created_at"],
                str(r["id"]),
            ),
        )
        representative[job_id] = best["id"]
        reviewed = [r for r in job_rows if r["ai_ok"] == "true"]
        if reviewed:
            ai_pick[job_id] = max(reviewed, key=lambda r: (r["created_at"], str(r["id"])))["id"]

    loaded = {
        run.id: _measured_run(run)
        for run in AnalysisRun.objects.filter(id__in={*representative.values(), *ai_pick.values()}).only(
            *_MEASURED_FIELDS,
        )
    }
    for job_id, run_id in representative.items():
        measured = loaded.get(run_id)
        if not isinstance(measured, MeasuredRun):
            continue
        if exclusion_reasons(measured.target_meta, rules, measured_sloc=measured.measured_sloc()):
            continue
        out.runs[job_id] = measured
        reviewed_run = loaded.get(ai_pick.get(job_id))
        if isinstance(reviewed_run, MeasuredRun) and reviewed_run.ai_available:
            out.ai[job_id] = reviewed_run
    return out


def latest_measured(job_ids: Iterable[Any], *, rules: dict[str, Any] | None = None) -> dict[Any, MeasuredRun]:
    """Each job's representative complete measured run, whatever its plan (see :func:`descriptive_runs`).

    Only for descriptive platform totals (dashboards): comparisons across
    jobs must use :func:`select_measured`, which holds the plan fixed.
    """
    return descriptive_runs(job_ids, rules=rules).runs


def weighted_issue_score(issues: dict[str, int] | None, weights: dict[str, int | float]) -> float | None:
    """Severity-weighted issue count (the leaderboard score's numerator)."""
    if issues is None:
        return None
    return float(sum(weights.get(sev, 0) * int(issues.get(sev, 0) or 0) for sev in SEVERITIES))
