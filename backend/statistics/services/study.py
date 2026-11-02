"""Study-level tables for reporting: deduplicated issue breakdowns and the healing ablation.

Everything here describes one *scope* of generation jobs (an experiment, a
batch, an explicit id list, or everything a user owns) and counts each
job's latest result per tool only (``ToolResult.latest_ids_per_job``), so
re-analyses never double-count and re-running one tool keeps the others. AI-reviewer findings are opinions, not
measurements, and are left out unless asked for.

Each deduplicated issue of an app is one *kind*: a dependency advisory (a
known-vulnerability scanner's finding about a package), a runtime issue (only
dynamic scanners reported it), an issue in test code (:func:`is_test_path`),
or a code issue (everything else, deptry/depcheck manifest findings included).
Only code issues count as the app's code issues and per-kLOC density.

Severities in these tables are the *normalized* ones (``Finding.severity``, a
heuristic for tools that rate nothing) and every key holding them says so
(``unique_normalized_<sev>``, ``raw_normalized_<sev>``,
``security_normalized_<sev>``); ``reported_levels`` are the levels the tools
printed.
"""

from __future__ import annotations

import math
import statistics as pystats
from collections import Counter
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Exists
from django.db.models import OuterRef

from backend.analysis.models import AnalysisMode
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.generation.models import GenerationJob
from backend.statistics.services.dedup import DEFAULT_LINE_TOLERANCE
from backend.statistics.services.dedup import RECORD_FIELDS
from backend.statistics.services.dedup import SEVERITY_RANK
from backend.statistics.services.dedup import VULN_TOOLS
from backend.statistics.services.dedup import IssueCluster
from backend.statistics.services.dedup import cluster_findings
from backend.statistics.services.dedup import finding_record
from backend.statistics.services.dedup import issue_class
from backend.statistics.services.dedup import reported_level
from backend.statistics.services.dedup import tool_overlap
from backend.statistics.services.dependencies import cluster_package
from backend.statistics.services.dependencies import dependency_ownership
from backend.statistics.services.dependencies import dependency_scope
from backend.statistics.services.dependencies import scaffold_specs

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from django.db.models import QuerySet

SEVERITIES = ("critical", "high", "medium", "low", "info")
# The identifying columns of every per-app row (:func:`job_labels`).
JOB_LABEL_KEYS = (
    "job_id",
    "model",
    "model_name",
    "provider",
    "template",
    "app_id",
    "condition_id",
    "condition_label",
    "mode",
    "repeat_index",
    "build_healing",
)
# Kinds of deduplicated issue (:func:`issue_kind`).
CODE, TEST, RUNTIME, DEPENDENCY = "code", "test", "runtime", "dependency"
# The columns of each issue_breakdowns table, for exports of an empty scope.
PER_APP_KEYS = (
    *JOB_LABEL_KEYS,
    "run_id",
    "loc",
    "test_loc",
    "raw_findings",
    "unique_issues",
    "multi_tool_issues",
    "code_unique",
    "code_unique_tests",
    "manifest_unique",
    "runtime_unique",
    "dependency_unique",
    "dependency_added",
    "dependency_inherited",
    "manifest_unused_scaffold",
    "security_unique",
    "security_unique_tests",
    "unique_per_kloc",
    *(f"unique_normalized_{s}" for s in SEVERITIES),
    *(f"security_normalized_{s}" for s in SEVERITIES),
)
BY_TOOL_KEYS = (
    "tool",
    "raw_findings",
    "unique_issues",
    "exclusive",
    "shared",
    "apps_measured",
    "runs_measured",
    "apps_with_findings",
    "runs_with_findings",
    "reported_levels",
    *(f"raw_normalized_{s}" for s in SEVERITIES),
)
BY_TOOL_RULE_KEYS = (
    "tool",
    "rule_id",
    "issue_class",
    "category",
    "cwe",
    "example_title",
    "findings",
    "apps",
    "runs",
    "reported_levels",
    "max_severity_normalized",
    *(f"raw_normalized_{s}" for s in SEVERITIES),
)
TOOL_PAIR_KEYS = ("tool_a", "tool_b", "comparable_classes", "both", "only_a", "only_b", "agreement")
ISSUE_KEYS = (
    "job_id",
    "issue_class",
    "file_path",
    "line",
    "reported_levels",
    "category",
    "tools",
    "n_findings",
    "rule_ids",
    "finding_ids",
    "severity_normalized",
    "kind",
    "dependency_added",
)
HEALING_PER_APP_KEYS = (*JOB_LABEL_KEYS, "healing_changed", "added_requirements", "patches")
# Per-app code metrics exactly as the metric tools printed them (``ToolResult.metrics``
# of each tool's latest result for the app): column -> (tool, metrics key). radon is
# Python-only cyclomatic complexity; lizard covers Python and JS/JSX/TS functions
# (``functions_over_15``: CCN above lizard's warning threshold 15); jscpd's duplication
# is over the files it read. They read the analyzed tree, test files included.
CODE_METRIC_COLUMNS: dict[str, tuple[str, str]] = {
    "radon_avg_complexity": ("radon", "average_complexity"),
    "radon_max_complexity": ("radon", "max_complexity"),
    "lizard_functions": ("lizard", "functions"),
    "lizard_avg_ccn": ("lizard", "avg_ccn"),
    "lizard_max_ccn": ("lizard", "max_ccn"),
    "lizard_functions_over_15": ("lizard", "functions_over_15"),
    "jscpd_duplication_percentage": ("jscpd", "duplication_percentage"),
    "jscpd_clones": ("jscpd", "clones"),
}
CODE_METRIC_TOOLS = ("radon", "lizard", "jscpd")
# Counts a tool skipped as not applicable (no file it reads) measured as zero; its
# averages, maxima and percentages stay undefined (None).
_ZERO_WHEN_NOT_APPLICABLE = frozenset({"lizard_functions", "lizard_functions_over_15", "jscpd_clones"})
# The instrument behind each tool's metrics, per app (None when the result did not record it).
_CODE_METRIC_PROVENANCE = ("status", "version", "parser_sha", "command_sha")
CODE_METRIC_PER_APP_KEYS = (
    *JOB_LABEL_KEYS,
    "loc",
    "test_loc",
    *CODE_METRIC_COLUMNS,
    *(f"{tool}_{key}" for tool in CODE_METRIC_TOOLS for key in _CODE_METRIC_PROVENANCE),
)
_SECURITY_CATEGORIES = ("security", "secrets")
# Manifest-hygiene tools: the app's own declared-dependency mistakes (code issues, P2).
_MANIFEST_TOOLS = frozenset({"deptry", "depcheck"})
# Their rules that call a declared dependency unused.
_UNUSED_DEPENDENCY_RULES = frozenset({"DEP002", "unused-dependencies", "unused-devDependencies"})


def study_jobs(
    user: AbstractBaseUser | None = None,
    *,
    experiment_id: str | None = None,
    batch_id: str | None = None,
    job_ids: list[str] | None = None,
    current_only: bool = True,
) -> QuerySet[GenerationJob]:
    """The jobs a study table describes (owner-scoped unless *user* is None or staff).

    With *current_only* (the default) a retried experiment cell counts once:
    its latest attempt, as on the experiment's Results tab. Superseded
    attempts would otherwise double-count the cell in every denominator.
    """
    qs = GenerationJob.objects.select_related(
        "model",
        "app_requirement",
        "condition__model",
        "condition__profile",
        "condition__harness_preset",
    )
    if current_only:
        newer_attempt = GenerationJob.objects.filter(
            experiment_id=OuterRef("experiment_id"),
            condition_id=OuterRef("condition_id"),
            app_requirement_id=OuterRef("app_requirement_id"),
            repeat_index=OuterRef("repeat_index"),
            created_at__gt=OuterRef("created_at"),
        )
        # Ad-hoc jobs (no experiment cell) never match: NULL = NULL is not true in SQL.
        qs = qs.exclude(Exists(newer_attempt))
    if user is not None and getattr(user, "is_authenticated", False) and not getattr(user, "is_staff", False):
        qs = qs.filter(created_by=user)
    if experiment_id:
        qs = qs.filter(experiment_id=experiment_id)
    if batch_id:
        qs = qs.filter(batch_id=batch_id)
    if job_ids:
        qs = qs.filter(id__in=job_ids)
    return qs.order_by("created_at")


def condition_label(condition: Any) -> str:
    """An experiment condition's display name, as the experiment's own pages name it (label, else model / method)."""
    if condition is None:
        return ""
    from backend.generation.services.experiments import condition_label as experiment_condition_label

    return experiment_condition_label(condition)


def job_labels(job: GenerationJob) -> dict[str, Any]:
    """The identifying columns every per-app row starts with (:data:`JOB_LABEL_KEYS`).

    ``condition_id``/``condition_label`` name the experiment arm: one model
    can run under several (harness or prompt-profile arms), so per-arm tables
    group by condition, never by model.
    """
    return {
        "job_id": str(job.id),
        "model": job.model.model_id if job.model_id else "",
        "model_name": job.model.model_name if job.model_id else "",
        "provider": job.model.provider if job.model_id else "",
        "template": job.app_requirement.slug if job.app_requirement_id else "",
        "app_id": job.app_requirement_id,
        "condition_id": job.condition_id,
        "condition_label": condition_label(job.condition) if job.condition_id else "",
        "mode": job.mode,
        "repeat_index": job.repeat_index,
        "build_healing": job.build_healing,
    }


def app_unit(row: dict[str, Any]) -> tuple:
    """The app a per-run row measures: its (condition, app); repeats of one cell are one app.

    Jobs outside an experiment fall back to (model, mode) for the condition,
    and a job without an app template is its own app.
    """
    return (row.get("condition_id"), row.get("model"), row.get("mode"), row.get("app_id") or row.get("job_id"))


def _finding_records(result_to_job: dict[str, str], *, include_ai: bool) -> list[dict[str, Any]]:
    """Dedup records of the findings in the given results; ``run_id`` is the app (job) they describe."""
    qs = Finding.objects.filter(result_id__in=list(result_to_job))
    if not include_ai:
        qs = qs.exclude(result__tool_slug__in=AnalyzerTool.ai_slugs())
    rows = qs.values("result_id", *RECORD_FIELDS)
    return [finding_record(row, result_to_job[str(row["result_id"])]) for row in rows]


def dynamic_slugs() -> set[str]:
    """Slugs of the dynamic tools (DAST scanners probing the running app)."""
    return set(AnalyzerTool.objects.filter(analysis_mode=AnalysisMode.DYNAMIC).values_list("slug", flat=True))


def _measurement(result_ids: list, *, include_ai: bool) -> tuple[dict[str, Any], dict[str, set[str]]]:
    """:func:`tool_coverage`, plus the apps (job ids) each tool measured."""
    from backend.analysis.services.runner import SKIP_NOT_APPLICABLE

    ai = AnalyzerTool.ai_slugs() if not include_ai else set()
    measured: dict[str, set[str]] = defaultdict(set)
    reasons: dict[str, Counter] = defaultdict(Counter)
    all_tools: set[str] = set()
    apps: set[str] = set()
    for job_id, slug, status, summary in ToolResult.objects.filter(id__in=result_ids).values_list(
        "run__generation_job_id",
        "tool_slug",
        "status",
        "summary",
    ):
        apps.add(str(job_id))
        if slug in ai:
            continue
        all_tools.add(slug)
        skip_reason = (summary or {}).get("skip_reason") if isinstance(summary, dict) else None
        if status == ToolResult.Status.COMPLETED or (
            status == ToolResult.Status.SKIPPED and skip_reason == SKIP_NOT_APPLICABLE
        ):
            measured[slug].add(str(job_id))
        else:
            reasons[slug][skip_reason or status] += 1
    # An app no (non-AI) tool measured has no measurement at all: it is left
    # out of the scope rather than allowed to empty ``tools_common`` (which
    # would make every other app read as zero issues).
    measured_apps = set().union(*measured.values()) if measured else set()
    unmeasured = sorted(apps - measured_apps)
    n_runs = len(measured_apps)
    common = sorted(t for t in all_tools if n_runs and len(measured[t]) == n_runs)
    coverage = {
        "apps": n_runs,
        "apps_measured": sorted(measured_apps),
        "apps_unmeasured": unmeasured,
        "tools_common": common,
        "tools_partial": {
            t: {"apps_measured": len(measured[t]), "apps_missing": n_runs - len(measured[t]), **reasons[t]}
            for t in sorted(all_tools - set(common))
        },
    }
    return coverage, measured


def tool_coverage(result_ids: list, *, include_ai: bool) -> dict[str, Any]:
    """Which tools measured every analyzed app, and which did not.

    A tool *measured* an app when its result completed, or when it was
    skipped as not applicable (the app has none of the files it reads, so
    zero findings is the truth). A failed, timed-out or app-not-running
    result measured nothing; comparing that app's counts with apps where the
    tool ran would make it look cleaner than it is. Pass every result of the
    compared scope at once (all conditions together), so every compared
    group is measured with one instrument set.
    """
    return _measurement(result_ids, include_ai=include_ai)[0]


def _metric_number(value: Any) -> int | float | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def code_metrics_by_job(result_ids: list) -> dict[str, dict[str, Any]]:
    """Per job id: the :data:`CODE_METRIC_COLUMNS` of its metric tools' latest results, and their instruments.

    *result_ids* are the scope's ``ToolResult.latest_ids_per_job``. A column
    is None when its tool did not measure the app (no result, failed, app not
    running) or did not print that metric; a count is 0 when the tool was
    skipped as not applicable (nothing it reads: a measured zero). Per tool,
    ``<tool>_status`` says which (``measured`` | ``not_applicable`` | the skip
    reason or status), and ``<tool>_version`` / ``_parser_sha`` /
    ``_command_sha`` name the instrument (None when the result did not record
    it), so metrics of different instruments are never pooled unknowingly.
    Jobs without any metric-tool result are absent.
    """
    from backend.analysis.services.runner import SKIP_NOT_APPLICABLE

    empty = {
        **dict.fromkeys(CODE_METRIC_COLUMNS),
        **{f"{tool}_{key}": None for tool in CODE_METRIC_TOOLS for key in _CODE_METRIC_PROVENANCE},
    }
    out: dict[str, dict[str, Any]] = {}
    for job_id, tool, status, raw_summary, raw_metrics in ToolResult.objects.filter(
        id__in=result_ids,
        tool_slug__in=CODE_METRIC_TOOLS,
    ).values_list("run__generation_job_id", "tool_slug", "status", "summary", "metrics"):
        summary = raw_summary if isinstance(raw_summary, dict) else {}
        metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
        measured = status == ToolResult.Status.COMPLETED
        not_applicable = status == ToolResult.Status.SKIPPED and summary.get("skip_reason") == SKIP_NOT_APPLICABLE
        entry = out.setdefault(str(job_id), dict(empty))
        entry[f"{tool}_status"] = (
            "measured" if measured else "not_applicable" if not_applicable else summary.get("skip_reason") or status
        )
        for key in ("version", "parser_sha", "command_sha"):
            value = summary.get("tool_version" if key == "version" else key)
            entry[f"{tool}_{key}"] = str(value) if value else None
        for column, (slug, key) in CODE_METRIC_COLUMNS.items():
            if slug != tool:
                continue
            if measured:
                entry[column] = _metric_number(metrics.get(key))
            elif not_applicable and column in _ZERO_WHEN_NOT_APPLICABLE:
                entry[column] = 0
    return out


def code_metrics_per_app(jobs) -> list[dict[str, Any]]:
    """One row per job with a metric-tool result (:data:`CODE_METRIC_PER_APP_KEYS`): labels, source LOC, metrics.

    ``loc`` is the study's non-test source LOC (``reports.services.loc``), the
    denominator the issue tables use; the tools' own size figures (lizard
    ``total_nloc``, jscpd ``total_lines``) cover what they read, tests included.
    """
    from backend.reports.services.loc import loc_from_job

    jobs = list(jobs)
    metrics = code_metrics_by_job(ToolResult.latest_ids_per_job([j.id for j in jobs]))
    rows = []
    for job in jobs:
        entry = metrics.get(str(job.id))
        if entry is None:
            continue
        loc = loc_from_job(job)
        rows.append({**job_labels(job), "loc": loc["total_loc"], "test_loc": loc.get("test_loc"), **entry})
    return rows


def issue_kind(cluster: IssueCluster, dynamic: set[str]) -> str:
    """What a deduplicated issue describes: ``dependency``, ``runtime``, ``test`` or ``code``.

    A dependency advisory is about a package, a runtime issue (every member
    from a dynamic scanner) about the deployed app's HTTP behaviour, and a
    test-code issue about code that is not the delivered app; none of them is
    a line of app code the model wrote.
    """
    from backend.reports.services.loc import is_test_path

    if cluster.is_advisory:
        return DEPENDENCY
    if cluster.members and all(m["tool"] in dynamic for m in cluster.members):
        return RUNTIME
    if is_test_path(cluster.file_path):
        return TEST
    return CODE


def _per_kloc(count: int, loc: int) -> float | None:
    return round(count * 1000 / loc, 2) if loc else None


def _total(apps: list[dict[str, Any]], key: str) -> int | None:
    """Sum over the rows that measured *key*; ``None`` when none did (unmeasured is not zero)."""
    values = [a[key] for a in apps if a.get(key) is not None]
    return sum(values) if values else None


# Per-app counts a group summary sums.
_SUMMED = (
    "loc",
    "test_loc",
    "code_unique",
    "code_unique_tests",
    "manifest_unique",
    "runtime_unique",
    "dependency_unique",
    "dependency_added",
    "dependency_inherited",
    "manifest_unused_scaffold",
    "security_unique",
    "security_unique_tests",
    *(f"unique_normalized_{s}" for s in SEVERITIES),
    *(f"security_normalized_{s}" for s in SEVERITIES),
)


def group_summary(apps: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-run rows of :func:`issue_breakdowns` (``per_app``) into one group's row.

    The unit is the app: repeats of one (condition, app) are averaged first
    (:func:`app_unit`), so ``apps`` counts apps and ``runs`` counts jobs; the
    per-app mean/median/sd and ``unique_per_kloc`` are over app means. Count
    columns are totals over runs, ``None`` when no run measured them.
    """
    units: dict[tuple, list] = defaultdict(list)
    for a in apps:
        units[app_unit(a)].append(a)
    unique = [pystats.fmean(r["unique_issues"] for r in rows) for rows in units.values()]
    code = sum(pystats.fmean(r["code_unique"] for r in rows) for rows in units.values())
    loc = sum(pystats.fmean(r["loc"] for r in rows) for rows in units.values())
    raw = _total(apps, "raw_findings")
    total_unique = _total(apps, "unique_issues")
    return {
        "apps": len(units),
        "runs": len(apps),
        "raw_findings": raw,
        "unique_issues": total_unique,
        "duplicate_findings": raw - total_unique if raw is not None and total_unique is not None else None,
        "mean_unique_per_app": round(pystats.fmean(unique), 2) if unique else None,
        "median_unique_per_app": round(pystats.median(unique), 2) if unique else None,
        "sd_unique_per_app": round(pystats.stdev(unique), 2) if len(unique) > 1 else None,
        **{key: _total(apps, key) for key in _SUMMED},
        "unique_per_kloc": round(code * 1000 / loc, 2) if loc else None,
    }


# Kept for callers of the old private name.
_group_summary = group_summary
GROUP_SUMMARY_KEYS = tuple(group_summary([]))


def _levels(counter: Counter) -> dict[str, int]:
    """Reported levels by count, most frequent first."""
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def _sort_key(key: tuple) -> tuple:
    return tuple((v is None, v if isinstance(v, int) else str(v)) for v in key)


def _runtime_issues(runtime: list[IssueCluster]) -> list[tuple[str, set[str]]]:
    """One (highest normalized severity, tools) per issue class: a header missing on every crawled URL is one issue."""
    by_class: dict[str, list[IssueCluster]] = defaultdict(list)
    for c in runtime:
        by_class[c.issue_class].append(c)
    return [
        (
            max((c.severity for c in group), key=lambda s: SEVERITY_RANK.get(s, 0)),
            {t for c in group for t in c.tools},
        )
        for _cls, group in sorted(by_class.items())
    ]


def _unused_scaffold_dependency(cluster: IssueCluster, scaffold: dict[str, str]) -> bool:
    """Whether a manifest-hygiene issue calls a package the stack scaffold declares unused."""
    return cluster_package(cluster) in scaffold and any(
        m["tool"] in _MANIFEST_TOOLS and (m.get("rule_id") or "") in _UNUSED_DEPENDENCY_RULES for m in cluster.members
    )


def issue_breakdowns(
    jobs: QuerySet[GenerationJob],
    *,
    include_ai: bool = False,
    line_tolerance: int = DEFAULT_LINE_TOLERANCE,
    common_tools_only: bool = True,
) -> dict[str, Any]:
    """Raw vs deduplicated issue counts per app, model, condition, template, model x template and tool.

    Only jobs with a finished analysis run appear; ``by_tool_rule`` is the
    "which tool finds which kind of issue" table. With *common_tools_only*
    (the default) findings count only from tools that measured every app in
    scope (:func:`tool_coverage`), so every app is measured with the same
    instrument set; ``coverage`` lists the tools left out and why. Call it
    once over every compared group (``by_condition`` splits the result), never
    once per group, or each group gets its own instrument set.

    ``code_unique``/``unique_per_kloc``/``security_unique`` count code issues
    only (:func:`issue_kind`): dependency advisories (``dependency_unique``,
    split into ``dependency_added`` and ``dependency_inherited``), runtime
    issues (``runtime_unique``) and test code (``code_unique_tests``,
    ``security_unique_tests``; its lines are ``test_loc``, outside ``loc``)
    are reported apart. A runtime issue is one per issue class per app, also
    in ``unique_issues``/``multi_tool_issues``/``unique_normalized_<sev>``, so
    ``unique_issues`` is the sum of its kinds; ``issues`` and ``tool_pairs``
    stay per cluster (one per crawled URL for runtime issues). Severities here
    are the normalized ones (a heuristic for tools that rate nothing);
    ``reported_levels`` are the levels the tools printed.
    """
    jobs_by_id = {str(j.id): j for j in jobs}
    result_ids = ToolResult.latest_ids_per_job(jobs_by_id.keys())
    result_rows = list(
        ToolResult.objects.filter(id__in=result_ids).values_list(
            "id",
            "run_id",
            "run__generation_job_id",
            "created_at",
        ),
    )
    result_to_job = {str(r): str(j) for r, _run, j, _at in result_rows}
    # The run each app was most recently analyzed in (results may span runs).
    latest_run: dict[str, tuple] = {}
    for _r, run, job, at in result_rows:
        if str(job) not in latest_run or at > latest_run[str(job)][1]:
            latest_run[str(job)] = (str(run), at)
    # Lazy: backend.reports.services imports this module (study report).
    from backend.reports.services.loc import loc_from_job

    coverage, measured_by_tool = _measurement(result_ids, include_ai=include_ai)
    records = _finding_records(result_to_job, include_ai=include_ai)
    if common_tools_only:
        counted = set(coverage["tools_common"])
        records = [r for r in records if r["tool"] in counted]
    else:
        counted = {t for t, apps in measured_by_tool.items() if apps} | {r["tool"] for r in records}
    clusters = cluster_findings(records, line_tolerance=line_tolerance)
    dynamic = dynamic_slugs()
    ai = AnalyzerTool.ai_slugs()
    kind = {id(c): issue_kind(c, dynamic) for c in clusters}

    raw_by_run: Counter = Counter(r["run_id"] for r in records)
    clusters_by_run: dict[str, list] = defaultdict(list)
    for c in clusters:
        clusters_by_run[c.run_id].append(c)

    # Only apps the counted instrument set measured: an app whose tools all
    # failed (or that only an AI reviewer saw) has unknown counts, not zero.
    if common_tools_only:
        measured_apps = set(coverage["apps_measured"]) if coverage["tools_common"] else set()
    else:
        measured_apps = set(coverage["apps_measured"])
    stack_memo: dict = {}
    # Only advisories in versions the model chose are its own: not the
    # scaffold's pins, nor transitive packages the scaffold pulls in. What a
    # scaffold spec installs is read from every app in scope (lockfiles,
    # exact pins, scanner-reported versions), so it is linked in one scope.
    advisories = [c for c in clusters if kind[id(c)] == DEPENDENCY and c.run_id in measured_apps]
    ownerships = (
        {j: dependency_ownership(jobs_by_id[j], stack_memo) for j in sorted(measured_apps & latest_run.keys())}
        if advisories
        else {}
    )
    dependency_scope(ownerships, advisories)
    model_added: dict[int, bool] = {id(c): ownerships[c.run_id].is_models(c) for c in advisories}
    apps: list[dict[str, Any]] = []
    for job_id, (run_id, _at) in latest_run.items():
        if job_id not in measured_apps:
            continue
        job = jobs_by_id[job_id]
        own = clusters_by_run.get(job_id, [])
        by_kind: dict[str, list] = defaultdict(list)
        for c in own:
            by_kind[kind[id(c)]].append(c)
        code, tests, runtime, deps = by_kind[CODE], by_kind[TEST], by_kind[RUNTIME], by_kind[DEPENDENCY]
        tools_here = {t for t in counted if job_id in measured_by_tool.get(t, ())}
        # Source LOC, not metrics.lines_of_code (which counts every generated
        # line, lockfiles included): the denominator must mean the same for
        # all apps. Test files are outside it, as their issues are outside code_unique.
        loc_info = loc_from_job(job)
        loc = int(loc_info["total_loc"] or 0)
        added = sum(1 for c in deps if model_added[id(c)])
        # Unmeasured is not zero: no vulnerability scanner (DAST tool, manifest checker) counted for this app.
        deps_measured = bool(deps) or bool(tools_here & VULN_TOOLS)
        runtime_measured = bool(runtime) or bool(tools_here & dynamic)
        manifest = [c for c in code if c.category == "dependency"]
        manifest_measured = bool(manifest) or bool(tools_here & _MANIFEST_TOOLS)
        scaffold = scaffold_specs(job, stack_memo) if manifest else {}
        security = [c for c in code if c.category in _SECURITY_CATEGORIES]
        # Runtime issues once per issue class, as runtime_unique counts them.
        runtime_issues = _runtime_issues(runtime)
        static = [*code, *tests, *deps]
        sev = Counter([*(c.severity for c in static), *(level for level, _tools in runtime_issues)])
        security_sev = Counter(c.severity for c in security)
        apps.append(
            {
                **job_labels(job),
                "run_id": run_id,
                "loc": loc,
                # None: a single-blob result has no paths, so its test code is unknown.
                "test_loc": loc_info.get("test_loc"),
                "raw_findings": raw_by_run.get(job_id, 0),
                "unique_issues": len(static) + len(runtime_issues),
                "multi_tool_issues": sum(1 for c in static if len(c.tools) > 1)
                + sum(1 for _level, tools in runtime_issues if len(tools) > 1),
                "code_unique": len(code),
                "code_unique_tests": len(tests),
                # deptry/depcheck: the app's own manifest mistakes, part of code_unique.
                "manifest_unique": len(manifest) if manifest_measured else None,
                # One per issue class: a header missing on every crawled URL is one issue.
                "runtime_unique": len(runtime_issues) if runtime_measured else None,
                "dependency_unique": len(deps) if deps_measured else None,
                "dependency_added": added if deps_measured else None,
                "dependency_inherited": len(deps) - added if deps_measured else None,
                # Of manifest_unique: "unused" calls on packages the scaffold preinstalls (informational).
                "manifest_unused_scaffold": sum(1 for c in manifest if _unused_scaffold_dependency(c, scaffold))
                if manifest_measured
                else None,
                "security_unique": len(security),
                "security_unique_tests": sum(1 for c in tests if c.category in _SECURITY_CATEGORIES),
                "unique_per_kloc": _per_kloc(len(code), loc),
                **{f"unique_normalized_{s}": sev.get(s, 0) for s in SEVERITIES},
                **{f"security_normalized_{s}": security_sev.get(s, 0) for s in SEVERITIES},
            },
        )

    def grouped(*keys: str) -> list[dict[str, Any]]:
        groups: dict[tuple, list] = defaultdict(list)
        for a in apps:
            groups[tuple(a[k] for k in keys)].append(a)
        return [
            {**dict(zip(keys, k, strict=True)), **group_summary(v)}
            for k, v in sorted(groups.items(), key=lambda kv: _sort_key(kv[0]))
        ]

    # Distinct apps (not runs) a tool or rule reached, per P5's unit of analysis.
    unit_of = {job_id: app_unit(job_labels(job)) for job_id, job in jobs_by_id.items()}

    rule_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for r in records:
        key = (r["tool"], r["rule_id"] or "")
        row = rule_rows.setdefault(
            key,
            {
                "tool": r["tool"],
                "rule_id": r["rule_id"] or "",
                "issue_class": issue_class(
                    r["tool"],
                    r["rule_id"] or "",
                    cwe=r["cwe"],
                    category=r["category"],
                    package=r["package"],
                    issue_key=r["issue_key"],
                ),
                "category": r["category"],
                "cwe": r["cwe"],
                "example_title": r["title"][:200],
                "findings": 0,
                "runs": set(),
                "severity": Counter(),
                "levels": Counter(),
            },
        )
        # The lowest title, not the first one the (unordered) query returned: a report regenerates identically.
        row["example_title"] = min(row["example_title"], r["title"][:200])
        row["findings"] += 1
        row["runs"].add(r["run_id"])
        row["severity"][r["severity"]] += 1
        row["levels"][reported_level(r.get("tool_severity"))] += 1
    by_tool_rule = [
        {
            **{k: v for k, v in row.items() if k not in ("runs", "severity", "levels")},
            "apps": len({unit_of[j] for j in row["runs"]}),
            "runs": len(row["runs"]),
            # What the tool printed; the normalized maximum is a heuristic and labelled so.
            "reported_levels": _levels(row["levels"]),
            "max_severity_normalized": max(row["severity"], key=lambda s: SEVERITY_RANK.get(s, 0)),
            **{f"raw_normalized_{s}": row["severity"].get(s, 0) for s in SEVERITIES},
        }
        for row in sorted(rule_rows.values(), key=lambda r: (-r["findings"], r["tool"], r["rule_id"]))
    ]

    overlap = tool_overlap(clusters)
    raw_sev_by_tool: dict[str, Counter] = defaultdict(Counter)
    levels_by_tool: dict[str, Counter] = defaultdict(Counter)
    runs_by_tool: dict[str, set] = defaultdict(set)
    for r in records:
        raw_sev_by_tool[r["tool"]][r["severity"]] += 1
        levels_by_tool[r["tool"]][reported_level(r.get("tool_severity"))] += 1
        runs_by_tool[r["tool"]].add(r["run_id"])
    # Every counted tool gets a row: one that measured every app and found
    # nothing is a measured zero, not a tool that was left out.
    overlap_by_tool = {t["tool"]: t for t in overlap["tools"]}
    by_tool = []
    for tool in sorted(counted | set(overlap_by_tool)):
        measured = measured_by_tool.get(tool, set()) & measured_apps
        by_tool.append(
            {
                "tool": tool,
                "raw_findings": 0,
                "unique_issues": 0,
                "exclusive": 0,
                "shared": 0,
                **overlap_by_tool.get(tool, {}),
                "apps_measured": len({unit_of[j] for j in measured}),
                "runs_measured": len(measured),
                "apps_with_findings": len({unit_of[j] for j in runs_by_tool[tool]}),
                "runs_with_findings": len(runs_by_tool[tool]),
                "reported_levels": _levels(levels_by_tool[tool]),
                **{f"raw_normalized_{s}": raw_sev_by_tool[tool].get(s, 0) for s in SEVERITIES},
            },
        )

    def cluster_row(c: IssueCluster) -> dict[str, Any]:
        row = c.to_dict()
        row["severity_normalized"] = row.pop("severity")
        row["kind"] = kind[id(c)]
        row["dependency_added"] = model_added.get(id(c)) if row["kind"] == DEPENDENCY else None
        return row

    return {
        "params": {
            "include_ai": include_ai,
            "line_tolerance": line_tolerance,
            "common_tools_only": common_tools_only,
            # Asking for AI findings counts them only where the reviewer is in
            # the instrument set; a reviewer that missed apps is dropped (ai_partial).
            "ai_counted": sorted(ai & counted),
            "ai_partial": sorted(ai & set(coverage["tools_partial"])) if include_ai else [],
            "dynamic_counted": sorted(dynamic & counted),
        },
        "coverage": coverage,
        # Per app, as unique_issues (runtime issues once per class); tool_pairs stay per cluster.
        "totals": {**group_summary(apps), "multi_tool_issues": _total(apps, "multi_tool_issues")},
        "per_app": apps,
        "by_model": grouped("model"),
        "by_condition": grouped("condition_id", "condition_label"),
        "by_template": grouped("template"),
        "by_model_template": grouped("model", "template"),
        "by_tool": by_tool,
        "by_tool_rule": by_tool_rule,
        "tool_pairs": overlap["pairs"],
        "issues": [cluster_row(c) for c in clusters],
    }


# ── healing ablation ─────────────────────────────────────────────────────


def _binomial_two_sided(k: int, n: int) -> float | None:
    """Exact two-sided sign test p for *k* of *n* discordant pairs (McNemar's exact test)."""
    if n == 0:
        return None
    lo = min(k, n - k)
    tail = sum(math.comb(n, i) for i in range(lo + 1)) / 2**n
    return round(min(1.0, 2 * tail), 4)


def _outcomes(job: GenerationJob) -> dict[str, bool]:
    """deployed (True/False) per healing mode this job was evaluated under."""
    result = job.result_data if isinstance(job.result_data, dict) else {}
    out: dict[str, bool] = {}
    functional = (job.metrics or {}).get("functional") or {}
    evaluation = result.get("evaluation") or {}
    if functional.get("deployed") is not None:
        mode = functional.get("healing_mode") or job.build_healing
        out[mode] = bool(functional["deployed"])
    elif evaluation.get("status") in ("passed", "failed", "build_failed", "start_failed"):
        # Evaluated before deploy/healing were recorded: the job's own mode.
        out[job.build_healing] = evaluation["status"] in ("passed", "failed")
    for mode, entry in (result.get("healing_ablation") or {}).items():
        if isinstance(entry, dict) and entry.get("deployed") is not None:
            out.setdefault(mode, bool(entry["deployed"]))
    return out


def _healing_report(job: GenerationJob) -> dict[str, Any]:
    result = job.result_data if isinstance(job.result_data, dict) else {}
    return ((result.get("evaluation") or {}).get("build") or {}).get("healing") or {}


def _rates(rows: list[dict[str, Any]], baseline: str, modes: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for mode in modes:
        have = [r["deployed"][mode] for r in rows if mode in r["deployed"]]
        summary[f"n_{mode}"] = len(have)
        summary[f"deploy_rate_{mode}"] = round(100 * sum(have) / len(have), 1) if have else None
    for mode in modes:
        if mode == baseline:
            continue
        paired = [r for r in rows if baseline in r["deployed"] and mode in r["deployed"]]
        rescued = sum(1 for r in paired if r["deployed"][baseline] and not r["deployed"][mode])
        broken = sum(1 for r in paired if r["deployed"][mode] and not r["deployed"][baseline])
        n = len(paired)
        summary[f"paired_{mode}"] = n
        # Apps that deploy only because the build repaired them.
        summary[f"rescued_vs_{mode}"] = rescued
        summary[f"broken_vs_{mode}"] = broken
        summary[f"healing_effect_pp_vs_{mode}"] = round(100 * (rescued - broken) / n, 1) if n else None
        summary[f"mcnemar_p_vs_{mode}"] = _binomial_two_sided(rescued, rescued + broken)
    return summary


def healing_impact(jobs: QuerySet[GenerationJob], *, baseline: str = "full") -> dict[str, Any]:
    """Deploy rate per healing mode, paired on the same model output, overall and per model/template.

    Rates come from each job's own evaluation (under its ``build_healing``)
    and from ``result_data.healing_ablation`` (``manage.py healing_ablation``).
    Agent (verbatim) builds are never healed and are counted apart. Pairs are
    runs (one model output built under each mode): ``runs``, ``n_<mode>`` and
    the paired counts count jobs, ``apps`` the distinct apps (:func:`app_unit`).
    """
    rows: list[dict[str, Any]] = []
    not_applicable = 0
    added: dict[str, list] = defaultdict(list)
    patches: dict[str, list] = defaultdict(list)
    for job in jobs:
        report = _healing_report(job)
        if report.get("applicable") is False or job.mode == GenerationJob.Mode.AGENT:
            not_applicable += 1
            continue
        deployed = _outcomes(job)
        if not deployed:
            continue
        labels = job_labels(job)
        for package in set(report.get("added_requirements") or []):
            added[package].append(labels)
        for patch in {p.split(":", 1)[-1] for p in report.get("patches") or []}:
            patches[patch].append(labels)
        rows.append(
            {
                **labels,
                "deployed": deployed,
                "healing_changed": report.get("changed"),
                "added_requirements": report.get("added_requirements") or [],
                "patches": report.get("patches") or [],
            },
        )
    modes = sorted({m for r in rows for m in r["deployed"]}, key=lambda m: (m != baseline, m))

    def counts(items: list[dict[str, Any]]) -> dict[str, int]:
        return {"apps": len({app_unit(r) for r in items}), "runs": len(items)}

    def grouped(*keys: str) -> list[dict[str, Any]]:
        groups: dict[tuple, list] = defaultdict(list)
        for r in rows:
            groups[tuple(r[k] for k in keys)].append(r)
        return [
            {**dict(zip(keys, k, strict=True)), **counts(v), **_rates(v, baseline, modes)}
            for k, v in sorted(groups.items(), key=lambda kv: _sort_key(kv[0]))
        ]

    def tally(key: str, found: dict[str, list]) -> list[dict[str, Any]]:
        return [
            {key: name, **counts(items)} for name, items in sorted(found.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        ]

    return {
        "baseline": baseline,
        "modes": modes,
        "not_applicable": not_applicable,
        "totals": {
            **counts(rows),
            "healing_changed": sum(1 for r in rows if r["healing_changed"]),
            **_rates(rows, baseline, modes),
        },
        "by_model": grouped("model"),
        # One model can run under several conditions (prompt profiles): never pooled.
        "by_condition": grouped("condition_id", "condition_label"),
        "by_template": grouped("template"),
        "added_requirements": tally("package", added),
        "patches": tally("patch", patches),
        "per_app": [
            {
                **{k: v for k, v in r.items() if k != "deployed"},
                **{f"deployed_{m}": r["deployed"].get(m) for m in modes},
            }
            for r in rows
        ],
    }
