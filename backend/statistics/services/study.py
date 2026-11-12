"""Study-level tables for reporting: deduplicated issue breakdowns, tool coverage and code metrics.

Everything here describes one *scope* of generation runs (a study's current runs, an explicit id list, or
everything a user owns; :func:`backend.statistics.services.runs.study_runs`) and counts each run's latest
result per tool only (``ToolResult.latest_ids_per_job``), so re-analyses never double-count and re-running
one tool keeps the others. AI-reviewer findings are opinions, not measurements, and are left out unless
asked for.

Analysis reads the **app** scope (starter files included, spec section 2.3); attribution happens here: an
issue in a file of the run's **model** scope (authored, modified or slot files) is the model's
(``code_unique_model``), and the per-kLOC density divides those by the model's own non-test source LOC
(Appendix A #4). Each deduplicated issue of an app is one *kind*: a dependency advisory (a
known-vulnerability scanner's finding about a package), a runtime issue (only dynamic scanners reported
it), an issue in test code (:func:`is_test_path`), or a code issue (everything else, deptry/depcheck
manifest findings included).

Severities in these tables are the *normalized* ones (``Finding.severity``, a heuristic for tools that rate
nothing) and every key holding them says so (``unique_normalized_<sev>``, ``raw_normalized_<sev>``,
``security_normalized_<sev>``); ``reported_levels`` are the levels the tools printed.
"""

from __future__ import annotations

import re
import statistics as pystats
from collections import Counter
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.models import AnalysisMode
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
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
from backend.statistics.services.dependencies import starter_specs
from backend.statistics.services.runs import RUN_LABEL_KEYS
from backend.statistics.services.runs import app_unit
from backend.statistics.services.runs import condition_label
from backend.statistics.services.runs import labels_by_run
from backend.statistics.services.runs import load_code
from backend.statistics.services.runs import normalize_path
from backend.statistics.services.runs import run_labels
from backend.statistics.services.runs import study_runs

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.db.models import QuerySet

    from backend.generation.models import GenerationRun

SEVERITIES = ("critical", "high", "medium", "low", "info")
# Kinds of deduplicated issue (:func:`issue_kind`).
CODE, TEST, RUNTIME, DEPENDENCY = "code", "test", "runtime", "dependency"
# The columns of each issue_breakdowns table, for exports of an empty scope.
PER_APP_KEYS = (
    *RUN_LABEL_KEYS,
    "analysis_run_id",
    "loc",
    "loc_app",
    "test_loc",
    "raw_findings",
    "unique_issues",
    "multi_tool_issues",
    "code_unique",
    "code_unique_model",
    "code_unique_tests",
    "code_unique_auxiliary",
    "formatting_unique_model",
    "low_confidence_unique_model",
    "code_unique_inherited",
    "manifest_unique",
    "runtime_unique",
    "runtime_unique_low_confidence",
    "dependency_unique",
    "dependency_added",
    "dependency_inherited",
    "manifest_unused_starter",
    "security_unique",
    "security_unique_model",
    "security_unique_tests",
    "unique_per_kloc",
    "unique_per_kloc_without_formatting",
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
    "run_id",
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
    "model_scope",
    "dependency_added",
    "auxiliary",
    "formatting",
    "inherited_from_starter",
    "low_confidence",
)
# Per-app code metrics exactly as the metric tools printed them (``ToolResult.metrics``
# of each tool's latest result for the app): column -> (tool, metrics key). radon is
# Python-only cyclomatic complexity; lizard covers Python and JS/JSX/TS functions
# (``functions_over_15``: CCN above lizard's warning threshold 15); jscpd's duplication
# is over the files it read. They read the analyzed (app-scope) tree, test files included.
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
    *RUN_LABEL_KEYS,
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


def tool_measured(status: str, summary: Any) -> bool:
    """Whether a tool result measured the app: it completed and did not record ``measurement: not_measured``.

    A tool skipped as not applicable (nothing in the app's tree it reads), failed, timed out or that found
    the app not running measured nothing (spec section 8.8): its zero findings are not a zero.
    """
    from backend.analysis.services.runner import NOT_MEASURED

    data = summary if isinstance(summary, dict) else {}
    return status == ToolResult.Status.COMPLETED and data.get("measurement") != NOT_MEASURED


def _skip_reason(status: str, summary: Any) -> str:
    data = summary if isinstance(summary, dict) else {}
    return str(data.get("skip_reason") or status)


def _finding_records(result_to_run: dict[str, str], *, include_ai: bool) -> list[dict[str, Any]]:
    """Dedup records of the findings in the given results; ``run_id`` is the GenerationRun they describe."""
    qs = Finding.objects.filter(result_id__in=list(result_to_run))
    if not include_ai:
        qs = qs.exclude(result__tool_slug__in=AnalyzerTool.ai_slugs())
    rows = qs.values("result_id", *RECORD_FIELDS)
    return [finding_record(row, result_to_run[str(row["result_id"])]) for row in rows]


def dynamic_slugs() -> set[str]:
    """Slugs of the dynamic tools (DAST scanners probing the running app)."""
    return set(AnalyzerTool.objects.filter(analysis_mode=AnalysisMode.DYNAMIC).values_list("slug", flat=True))


def _measurement(result_ids: list, *, include_ai: bool) -> tuple[dict[str, Any], dict[str, set[str]]]:
    """:func:`tool_coverage`, plus the apps (GenerationRun ids) each tool measured."""
    ai = AnalyzerTool.ai_slugs() if not include_ai else set()
    measured: dict[str, set[str]] = defaultdict(set)
    reasons: dict[str, Counter] = defaultdict(Counter)
    all_tools: set[str] = set()
    apps: set[str] = set()
    for run_id, slug, status, summary in ToolResult.objects.filter(id__in=result_ids).values_list(
        "run__generation_job_id",
        "tool_slug",
        "status",
        "summary",
    ):
        apps.add(str(run_id))
        if slug in ai:
            continue
        all_tools.add(slug)
        if tool_measured(status, summary):
            measured[slug].add(str(run_id))
        else:
            reasons[slug][_skip_reason(status, summary)] += 1
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

    A tool *measured* an app when its result completed (:func:`tool_measured`). A not-applicable, failed,
    timed-out or app-not-running result measured nothing; comparing that app's counts with apps where the
    tool ran would make it look cleaner than it is. So a cross-stack scope counts only the tools applicable
    to every compared app (spec section 8.8). Pass every result of the compared scope at once (all conditions
    together), so every compared group is measured with one instrument set.
    """
    return _measurement(result_ids, include_ai=include_ai)[0]


def _metric_number(value: Any) -> int | float | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def code_metrics_by_run(result_ids: list) -> dict[str, dict[str, Any]]:
    """Per GenerationRun id: the :data:`CODE_METRIC_COLUMNS` of its metric tools' latest results, and instruments.

    *result_ids* are the scope's ``ToolResult.latest_ids_per_job``. A column is None when its tool did not
    measure the app (no result, not applicable, failed, app not running) or did not print that metric. Per
    tool, ``<tool>_status`` says which (``measured`` | the skip reason or status), and ``<tool>_version`` /
    ``_parser_sha`` / ``_command_sha`` name the instrument (None when the result did not record it), so
    metrics of different instruments are never pooled unknowingly. Runs without any metric-tool result are
    absent.
    """
    empty = {
        **dict.fromkeys(CODE_METRIC_COLUMNS),
        **{f"{tool}_{key}": None for tool in CODE_METRIC_TOOLS for key in _CODE_METRIC_PROVENANCE},
    }
    out: dict[str, dict[str, Any]] = {}
    for run_id, tool, status, raw_summary, raw_metrics in ToolResult.objects.filter(
        id__in=result_ids,
        tool_slug__in=CODE_METRIC_TOOLS,
    ).values_list("run__generation_job_id", "tool_slug", "status", "summary", "metrics"):
        summary = raw_summary if isinstance(raw_summary, dict) else {}
        metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
        measured = tool_measured(status, summary)
        entry = out.setdefault(str(run_id), dict(empty))
        entry[f"{tool}_status"] = "measured" if measured else _skip_reason(status, summary)
        for key in ("version", "parser_sha", "command_sha"):
            value = summary.get("tool_version" if key == "version" else key)
            entry[f"{tool}_{key}"] = str(value) if value else None
        if not measured:
            continue
        for column, (slug, key) in CODE_METRIC_COLUMNS.items():
            if slug == tool:
                entry[column] = _metric_number(metrics.get(key))
    return out


def code_metrics_per_app(runs: Iterable[GenerationRun]) -> list[dict[str, Any]]:
    """One row per run with a metric-tool result (:data:`CODE_METRIC_PER_APP_KEYS`): labels, source LOC, metrics.

    ``loc`` is the model scope's non-test source LOC (``reports.services.loc`` rules), the denominator the
    issue tables use; the tools' own size figures (lizard ``total_nloc``, jscpd ``total_lines``) cover the
    app tree they read, tests included.
    """
    runs = list(runs)
    metrics = code_metrics_by_run(ToolResult.latest_ids_per_job([r.pk for r in runs]))
    measured = [r for r in runs if str(r.pk) in metrics]
    if not measured:
        return []
    labels = labels_by_run(measured)
    code = load_code(measured, app=False)
    rows = []
    for run in measured:
        run_id = str(run.pk)
        loc = code.loc(run_id)
        rows.append({**labels[run_id], "loc": loc["total_loc"], "test_loc": loc["test_loc"], **metrics[run_id]})
    return rows


def issue_kind(cluster: IssueCluster, dynamic: set[str]) -> str:
    """What a deduplicated issue describes: ``dependency``, ``runtime``, ``test`` or ``code``.

    A dependency advisory is about a package, a runtime issue (every member
    from a dynamic scanner) about the deployed app's HTTP behaviour, and a
    ``test`` issue about code that is not the delivered app: test code and,
    under the default scope, an agent's scratch and verification scripts
    (``reports.services.loc.is_outside_app``; the scope is switchable). None
    of them is a line of app code the model wrote.
    """
    from backend.reports.services.loc import is_outside_app

    if cluster.is_advisory:
        return DEPENDENCY
    if cluster.members and all(m["tool"] in dynamic for m in cluster.members):
        return RUNTIME
    if is_outside_app(cluster.file_path):
        return TEST
    return CODE


# pycodestyle's layout codes (E1 indentation, E2 whitespace, E3 blank lines, E5 line length, E7xx statement
# layout is left out: it holds real smells such as bare except, W whitespace warnings) and pylint's
# equivalents. Their counts follow an arbitrary limit (100 or 120 characters) more than the code's quality.
_FORMATTING_RULE_RE = re.compile(r"^(E[1235]\d\d|W[1-6]\d\d)$")
_FORMATTING_PYLINT = frozenset(
    {
        "line-too-long",
        "trailing-whitespace",
        "missing-final-newline",
        "trailing-newlines",
        "multiple-statements",
        "superfluous-parens",
        "bad-indentation",
        "mixed-line-endings",
        "unexpected-line-ending-format",
        "wrong-import-order",
        "wrong-import-position",
        "ungrouped-imports",
        "C0301",
        "C0303",
        "C0304",
        "C0305",
    },
)


def is_formatting(cluster: IssueCluster) -> bool:
    """Whether every member of *cluster* is a layout rule (line length, whitespace, blank lines, import order)."""
    return bool(cluster.members) and all(
        m.get("category") == "style"
        or bool(_FORMATTING_RULE_RE.match(m.get("rule_id") or ""))
        or (m.get("tool") == "pylint" and (m.get("rule_id") or "") in _FORMATTING_PYLINT)
        for m in cluster.members
    )


def is_low_confidence_static(cluster: IssueCluster) -> bool:
    """Whether every member of a code issue carries its tool's lowest stated confidence.

    vulture's 60% class ("unused attribute 'row_factory'" on ``conn.row_factory = sqlite3.Row``, in every
    app of the 2026-11 study) and bandit's LOW. A tool that states no confidence is never low.
    """
    return bool(cluster.members) and all(
        str(m.get("tool_confidence") or "").lower() in _LOW_CONFIDENCE for m in cluster.members
    )


def _inherited_from_starter(
    clusters: list[IssueCluster],
    kind: dict[int, str],
    in_model: dict[int, bool],
    starter_of: dict[str, Any],
) -> set[int]:
    """Ids of the model-scope code issues that the starter's own file already has.

    A finding is attributed by file, so an issue on a line the starter wrote counts as the model's once
    the model edits that file (the starter Dockerfile's ``RUN cd``, missing HEALTHCHECK and root user: 186
    findings in the 31 apps of the 2026-11 study that touched the Dockerfile). The evidence is the scope
    itself: what the same tools report, under the same path and issue class, in apps that kept that
    starter's file unmodified. Per app, at most that many issues of the class in the edited file are
    inherited (the lowest lines first); anything beyond is the model's.
    """
    evidence: dict[tuple, int] = {}
    per_run: Counter = Counter()
    for c in clusters:
        if kind[id(c)] == CODE and c.members and all(m.get("file_origin") == "starter" for m in c.members):
            per_run[(c.run_id, starter_of.get(c.run_id), normalize_path(c.file_path), c.issue_class)] += 1
    for (_run, starter, path, cls), n in per_run.items():
        key = (starter, path, cls)
        evidence[key] = max(evidence.get(key, 0), n)
    candidates: dict[tuple, list[IssueCluster]] = defaultdict(list)
    for c in clusters:
        if (
            kind[id(c)] == CODE
            and in_model.get(id(c))
            and c.members
            and all(m.get("file_origin") == "modified" for m in c.members)
        ):
            key = (starter_of.get(c.run_id), normalize_path(c.file_path), c.issue_class)
            if key in evidence:
                candidates[(c.run_id, *key)].append(c)
    inherited: set[int] = set()
    for (_run, *key), group in candidates.items():
        group.sort(key=lambda c: (c.line or 0, min(str(m["id"]) for m in c.members)))
        inherited.update(id(c) for c in group[: evidence[tuple(key)]])
    return inherited


# What a scanner says when it is least sure, or reports an observation rather than a flaw.
_LOW_CONFIDENCE = frozenset({"low", "false positive", "60%"})
_INFORMATIONAL_LEVELS = frozenset({"informational", "info"})


def is_low_confidence(cluster: IssueCluster) -> bool:
    """Whether every member is the scanner's lowest confidence or an informational observation.

    ZAP raises "Path Traversal" and "Cross Site Scripting Weakness (Persistent in JSON Response)" at
    low confidence when a request merely succeeds or stored input comes back in JSON; in the 2026-11
    study's sample none of them was a flaw. A finding without a stated confidence is not low.
    """
    return bool(cluster.members) and all(
        str(m.get("tool_confidence") or "").lower() in _LOW_CONFIDENCE
        or str(m.get("tool_severity") or "").lower() in _INFORMATIONAL_LEVELS
        for m in cluster.members
    )


def _per_kloc(count: int, loc: int | None) -> float | None:
    return round(count * 1000 / loc, 2) if loc else None


def _total(apps: list[dict[str, Any]], key: str) -> int | None:
    """Sum over the rows that measured *key*; ``None`` when none did (unmeasured is not zero)."""
    values = [a[key] for a in apps if a.get(key) is not None]
    return sum(values) if values else None


# Per-app counts a group summary sums.
_SUMMED = (
    "loc",
    "loc_app",
    "test_loc",
    "code_unique",
    "code_unique_model",
    "code_unique_tests",
    "code_unique_auxiliary",
    "formatting_unique_model",
    "low_confidence_unique_model",
    "code_unique_inherited",
    "manifest_unique",
    "runtime_unique",
    "runtime_unique_low_confidence",
    "dependency_unique",
    "dependency_added",
    "dependency_inherited",
    "manifest_unused_starter",
    "security_unique",
    "security_unique_model",
    "security_unique_tests",
    *(f"unique_normalized_{s}" for s in SEVERITIES),
    *(f"security_normalized_{s}" for s in SEVERITIES),
)


def group_summary(apps: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-run rows of :func:`issue_breakdowns` (``per_app``) into one group's row.

    The unit is the app: repeats of one (condition, unit) are averaged first (:func:`app_unit`), so ``apps``
    counts apps and ``runs`` counts runs; the per-app mean/median/sd and ``unique_per_kloc`` are over app
    means (``unique_per_kloc``: the model's code issues per model kLOC, over the runs with a measured LOC).
    Count columns are totals over runs, ``None`` when no run measured them.
    """
    units: dict[tuple, list] = defaultdict(list)
    for a in apps:
        units[app_unit(a)].append(a)
    unique = [pystats.fmean(r["unique_issues"] for r in rows) for rows in units.values()]
    code = 0.0
    formatting = 0.0
    loc = 0.0
    for rows in units.values():
        sized = [r for r in rows if r.get("loc") is not None]
        if sized:
            code += pystats.fmean(r["code_unique_model"] for r in sized)
            formatting += pystats.fmean(r.get("formatting_unique_model") or 0 for r in sized)
            loc += pystats.fmean(r["loc"] for r in sized)
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
        # The same density without formatting issues (line length, whitespace): see :func:`is_formatting`.
        "unique_per_kloc_without_formatting": round((code - formatting) * 1000 / loc, 2) if loc else None,
    }


GROUP_SUMMARY_KEYS = tuple(group_summary([]))


def _levels(counter: Counter) -> dict[str, int]:
    """Reported levels by count, most frequent first."""
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def _sort_key(key: tuple) -> tuple:
    return tuple((v is None, v if isinstance(v, int) else str(v)) for v in key)


def _runtime_issues(runtime: list[IssueCluster]) -> list[tuple[str, set[str], bool]]:
    """One (highest normalized severity, tools, low confidence) per issue class: a header missing on every
    crawled URL is one issue. A class is low-confidence when every cluster of it is (:func:`is_low_confidence`)."""
    by_class: dict[str, list[IssueCluster]] = defaultdict(list)
    for c in runtime:
        by_class[c.issue_class].append(c)
    return [
        (
            max((c.severity for c in group), key=lambda s: SEVERITY_RANK.get(s, 0)),
            {t for c in group for t in c.tools},
            all(is_low_confidence(c) for c in group),
        )
        for _cls, group in sorted(by_class.items())
    ]


def _unused_starter_dependency(cluster: IssueCluster, starter: dict[str, str]) -> bool:
    """Whether a manifest-hygiene issue calls a package the run's starter declares unused."""
    return cluster_package(cluster) in starter and any(
        m["tool"] in _MANIFEST_TOOLS and (m.get("rule_id") or "") in _UNUSED_DEPENDENCY_RULES for m in cluster.members
    )


def issue_breakdowns(
    runs: QuerySet[GenerationRun] | Iterable[GenerationRun],
    *,
    include_ai: bool = False,
    line_tolerance: int = DEFAULT_LINE_TOLERANCE,
    common_tools_only: bool = True,
    code_scope: str | None = None,
) -> dict[str, Any]:
    """Raw vs deduplicated issue counts per app, model, condition, task, model x task and tool.

    *code_scope* (one of ``reports.services.loc.CODE_SCOPES``; None: ``settings.STUDY_CODE_SCOPE``) says
    what counts as the delivered app: by default tests and an agent's scratch scripts are counted apart
    (``code_unique_tests``, of which ``code_unique_auxiliary`` are scratch scripts), in findings and in
    the LOC denominator alike. ``params.code_scope`` records the scope the tables were computed with.

    Only runs with a finished analysis appear; ``by_tool_rule`` is the "which tool finds which kind of
    issue" table. With *common_tools_only* (the default) findings count only from tools that measured every
    app in scope (:func:`tool_coverage`), so every app is measured with the same instrument set;
    ``coverage`` lists the tools left out and why. Call it once over every compared group (``by_condition``
    splits the result), never once per group, or each group gets its own instrument set.

    ``code_unique``/``security_unique`` count the app's code issues (:func:`issue_kind`), starter files
    included (analysis reads the app scope); ``code_unique_model``/``security_unique_model`` are the ones in
    the model's own files (authored, modified, slot), and ``unique_per_kloc`` divides them by the model's
    non-test source LOC (``loc``; ``loc_app`` is the whole tree's). Dependency advisories
    (``dependency_unique``, split into ``dependency_added`` and ``dependency_inherited``), runtime issues
    (``runtime_unique``) and test code (``code_unique_tests``, ``security_unique_tests``; its lines are
    ``test_loc``, outside ``loc``) are reported apart. A runtime issue is one per issue class per app, also in
    ``unique_issues``/``multi_tool_issues``/``unique_normalized_<sev>``, so ``unique_issues`` is the sum of its
    kinds; ``issues`` and ``tool_pairs`` stay per cluster (one per crawled URL for runtime issues).
    Severities here are the normalized ones (a heuristic for tools that rate nothing); ``reported_levels``
    are the levels the tools printed.
    """
    from backend.reports.services.loc import use_code_scope

    with use_code_scope(code_scope) as scope:
        return _issue_breakdowns(
            runs,
            include_ai=include_ai,
            line_tolerance=line_tolerance,
            common_tools_only=common_tools_only,
            scope=scope,
        )


def _issue_breakdowns(
    runs: QuerySet[GenerationRun] | Iterable[GenerationRun],
    *,
    include_ai: bool,
    line_tolerance: int,
    common_tools_only: bool,
    scope: str,
) -> dict[str, Any]:
    from backend.reports.services.loc import is_auxiliary_path

    runs_by_id = {str(r.pk): r for r in runs}
    result_ids = ToolResult.latest_ids_per_job(runs_by_id.keys())
    result_rows = list(
        ToolResult.objects.filter(id__in=result_ids).values_list(
            "id",
            "run_id",
            "run__generation_job_id",
            "created_at",
        ),
    )
    result_to_run = {str(r): str(g) for r, _analysis, g, _at in result_rows}
    # The analysis run each app was most recently analyzed in (results may span analysis runs).
    latest_analysis: dict[str, tuple] = {}
    for _r, analysis, gen, at in result_rows:
        if str(gen) not in latest_analysis or at > latest_analysis[str(gen)][1]:
            latest_analysis[str(gen)] = (str(analysis), at)

    coverage, measured_by_tool = _measurement(result_ids, include_ai=include_ai)
    records = _finding_records(result_to_run, include_ai=include_ai)
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
    in_table = sorted(measured_apps & latest_analysis.keys() & runs_by_id.keys())
    analyzed = sorted(set(result_to_run.values()) & runs_by_id.keys())
    labels = labels_by_run([runs_by_id[r] for r in analyzed])
    # App-scope code (what the analyzers read: manifests, lockfiles, starter files) and the model scope, in bulk.
    code = load_code([runs_by_id[r] for r in in_table])
    model_paths = {r: code.model_paths(r) for r in in_table}
    in_model = {id(c): normalize_path(c.file_path) in model_paths.get(c.run_id, frozenset()) for c in clusters}
    # Issues the starter's own file has are the starter's even in a file the model edited.
    starter_of = {r: getattr(runs_by_id[r], "starter_id", None) for r in in_table}
    inherited = _inherited_from_starter(clusters, kind, in_model, starter_of)
    in_model = {key: value and key not in inherited for key, value in in_model.items()}
    starter_memo: dict = {}
    # Only advisories in versions the model chose are its own: not the
    # starter's pins, nor transitive packages the starter pulls in. What a
    # starter spec installs is read from every app in scope (lockfiles,
    # exact pins, scanner-reported versions), so it is linked in one scope.
    advisories = [c for c in clusters if kind[id(c)] == DEPENDENCY and c.run_id in measured_apps]
    ownerships = (
        {r: dependency_ownership(runs_by_id[r], starter_memo, files=code.app.get(r, {})) for r in in_table}
        if advisories
        else {}
    )
    dependency_scope(ownerships, advisories)
    model_added: dict[int, bool] = {
        id(c): ownerships[c.run_id].is_models(c) for c in advisories if c.run_id in ownerships
    }
    apps: list[dict[str, Any]] = []
    for run_id in in_table:
        analysis_run_id = latest_analysis[run_id][0]
        run = runs_by_id[run_id]
        own = clusters_by_run.get(run_id, [])
        by_kind: dict[str, list] = defaultdict(list)
        for c in own:
            by_kind[kind[id(c)]].append(c)
        code_issues, tests, runtime, deps = by_kind[CODE], by_kind[TEST], by_kind[RUNTIME], by_kind[DEPENDENCY]
        tools_here = {t for t in counted if run_id in measured_by_tool.get(t, ())}
        # Source LOC of the model's own files (not metrics, which may count lockfiles): the denominator must
        # mean the same for all apps. Test files are outside it, as their issues are outside code_unique.
        loc_info = code.loc(run_id)
        loc = loc_info["total_loc"]
        added = sum(1 for c in deps if model_added.get(id(c)))
        # Unmeasured is not zero: no vulnerability scanner (DAST tool, manifest checker) counted for this app.
        deps_measured = bool(deps) or bool(tools_here & VULN_TOOLS)
        runtime_measured = bool(runtime) or bool(tools_here & dynamic)
        manifest = [c for c in code_issues if c.category == "dependency"]
        manifest_measured = bool(manifest) or bool(tools_here & _MANIFEST_TOOLS)
        starter = starter_specs(run, starter_memo) if manifest else {}
        security = [c for c in code_issues if c.category in _SECURITY_CATEGORIES]
        model_code = [c for c in code_issues if in_model[id(c)]]
        # Runtime issues once per issue class, as runtime_unique counts them.
        runtime_issues = _runtime_issues(runtime)
        static = [*code_issues, *tests, *deps]
        sev = Counter([*(c.severity for c in static), *(level for level, _tools, _low in runtime_issues)])
        formatting_model = sum(1 for c in model_code if is_formatting(c))
        security_sev = Counter(c.severity for c in security)
        apps.append(
            {
                **labels[run_id],
                "analysis_run_id": analysis_run_id,
                "loc": loc,
                "loc_app": code.loc_app(run_id),
                "test_loc": loc_info["test_loc"],
                "raw_findings": raw_by_run.get(run_id, 0),
                "unique_issues": len(static) + len(runtime_issues),
                "multi_tool_issues": sum(1 for c in static if len(c.tools) > 1)
                + sum(1 for _level, tools, _low in runtime_issues if len(tools) > 1),
                "code_unique": len(code_issues),
                "code_unique_model": len(model_code),
                "code_unique_tests": len(tests),
                # Of code_unique_tests: issues in scratch and verification scripts (not test code proper).
                "code_unique_auxiliary": sum(1 for c in tests if is_auxiliary_path(c.file_path)),
                # Of code_unique_model: layout rules (line length, whitespace, import order).
                "formatting_unique_model": formatting_model,
                # Of code_unique_model: every reporting tool at its lowest stated confidence (vulture 60%, bandit LOW).
                "low_confidence_unique_model": sum(1 for c in model_code if is_low_confidence_static(c)),
                # Of code_unique, outside code_unique_model: issues of the starter's own lines in a file the model
                # edited (the same tools report them in apps that kept the starter's file).
                "code_unique_inherited": sum(1 for c in code_issues if id(c) in inherited),
                # deptry/depcheck: the app's own manifest mistakes, part of code_unique.
                "manifest_unique": len(manifest) if manifest_measured else None,
                # One per issue class: a header missing on every crawled URL is one issue.
                "runtime_unique": len(runtime_issues) if runtime_measured else None,
                # Of runtime_unique: classes the scanners raised only at their lowest confidence or as information.
                "runtime_unique_low_confidence": sum(1 for _level, _tools, low in runtime_issues if low)
                if runtime_measured
                else None,
                "dependency_unique": len(deps) if deps_measured else None,
                "dependency_added": added if deps_measured else None,
                "dependency_inherited": len(deps) - added if deps_measured else None,
                # Of manifest_unique: "unused" calls on packages the starter preinstalls (informational).
                "manifest_unused_starter": sum(1 for c in manifest if _unused_starter_dependency(c, starter))
                if manifest_measured
                else None,
                "security_unique": len(security),
                "security_unique_model": sum(1 for c in security if in_model[id(c)]),
                "security_unique_tests": sum(1 for c in tests if c.category in _SECURITY_CATEGORIES),
                "unique_per_kloc": _per_kloc(len(model_code), loc),
                "unique_per_kloc_without_formatting": _per_kloc(len(model_code) - formatting_model, loc),
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
    unit_of = {run_id: app_unit(row) for run_id, row in labels.items()}

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
        # Whether the issue is in a file of the model's own scope (None: the run's code was not read).
        row["model_scope"] = in_model[id(c)] if c.run_id in model_paths else None
        row["dependency_added"] = model_added.get(id(c)) if row["kind"] == DEPENDENCY else None
        row["auxiliary"] = row["kind"] == TEST and is_auxiliary_path(c.file_path)
        row["formatting"] = row["kind"] in (CODE, TEST) and is_formatting(c)
        row["inherited_from_starter"] = id(c) in inherited
        if row["kind"] in (CODE, TEST):
            row["low_confidence"] = is_low_confidence_static(c)
            return row
        row["low_confidence"] = is_low_confidence(c) if row["kind"] == RUNTIME else None
        return row

    return {
        "params": {
            "include_ai": include_ai,
            "line_tolerance": line_tolerance,
            "common_tools_only": common_tools_only,
            # What counts as the delivered app (reports.services.loc.CODE_SCOPES).
            "code_scope": scope,
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
        "by_task": grouped("task"),
        "by_model_task": grouped("model", "task"),
        "by_tool": by_tool,
        "by_tool_rule": by_tool_rule,
        "tool_pairs": overlap["pairs"],
        "issues": [cluster_row(c) for c in clusters],
    }


# Re-exported for the other consumers (reports, export, analysis, rankings, human_eval).
__all__ = [
    "BY_TOOL_KEYS",
    "BY_TOOL_RULE_KEYS",
    "CODE_METRIC_COLUMNS",
    "CODE_METRIC_PER_APP_KEYS",
    "CODE_METRIC_TOOLS",
    "GROUP_SUMMARY_KEYS",
    "ISSUE_KEYS",
    "PER_APP_KEYS",
    "RUN_LABEL_KEYS",
    "SEVERITIES",
    "TOOL_PAIR_KEYS",
    "app_unit",
    "code_metrics_by_run",
    "code_metrics_per_app",
    "condition_label",
    "dynamic_slugs",
    "group_summary",
    "is_formatting",
    "is_low_confidence",
    "is_low_confidence_static",
    "issue_breakdowns",
    "issue_kind",
    "run_labels",
    "study_runs",
    "tool_coverage",
    "tool_measured",
]
