"""Findings exporters: CSV, JSON, and SARIF 2.1.0.

Every exported finding carries the level exactly as its tool printed it
(``tool_severity``; ``unrated`` when the tool rates nothing) and, labelled as
such, the normalized heuristic level (``normalized_severity``). It also names
the app it describes (generation run, model, task, study cell), the origin of
its file in the run's app tree (``file_origin``: ``starter``, ``authored``,
``modified``, ``slot`` or ``parent``, from ``RunView.files("app")``; empty for
a URL or a path outside the tree) and says whether its result is the one the
study tables count (``is_latest``: the run's latest result per tool,
``ToolResult.latest_ids_per_job``). Findings of an ad-hoc analysis without a
generation run have no app and no ``is_latest``.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from backend.export.services.csv_safe import safe_row

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.db.models import QuerySet


UNRATED = "unrated"

# A row's result and run, joined in one query (no per-row lookups on large exports).
# Only the columns a row reads are loaded: a result's raw output must not be
# parsed again for every finding. The app a row describes comes from one
# per-export run map instead (:func:`export_context`).
FINDING_SELECT_RELATED = ("result", "result__run")
FINDING_ONLY = (
    "id",
    "result",
    "severity",
    "tool_severity",
    "category",
    "title",
    "description",
    "file_path",
    "line_number",
    "column_number",
    "rule_id",
    "tool_specific_data",
    "created_at",
    "result__run",
    "result__tool_slug",
    "result__status",
    "result__summary",
    "result__run__generation_job",
)

FINDING_HEADERS = [
    "id",
    "task_id",  # the analysis run
    "result_id",
    "result_status",
    "generation_run_id",
    "study_id",
    "condition_id",
    "condition_label",
    "model",
    "task",
    "unit",
    "kind",
    "stack",
    "repeat_index",
    "analyzer",
    "tool_version",
    "tool_severity",
    "normalized_severity",
    "category",
    "file_path",
    "file_origin",
    "line",
    "column",
    "rule_id",
    "message",
    "description",
    "cwe",
    "package",
    "installed_version",
    "fixed_versions",
    "docs_url",
    "is_latest",
    "created_at",
]

# SARIF level of a tool's own label (lower-cased). Numeric labels are CVSS
# scores (osv-scanner prints e.g. "7.0"); a label not listed here, or one that
# says the tool has no rating (trivy's UNKNOWN), gets level "none".
_SARIF_LABEL_LEVEL: dict[str, str] = {
    "critical": "error",
    "high": "error",
    "error": "error",
    "fatal": "error",
    "blocker": "error",
    "medium": "warning",
    "moderate": "warning",
    "warning": "warning",
    "warn": "warning",
    "major": "warning",
    "low": "note",
    "info": "note",
    "informational": "note",
    "information": "note",
    "note": "note",
    "convention": "note",
    "refactor": "note",
    "minor": "note",
    "style": "note",
    "hint": "note",
}
_CVSS_ERROR = 7.0
_CVSS_WARNING = 4.0

SARIF_SCHEMA = "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"


def sarif_level(tool_severity: str) -> str:
    """The SARIF level of a label exactly as the tool printed it; ``none`` when it rates nothing."""
    label = (tool_severity or "").strip().lower()
    if not label:
        return "none"
    if label in _SARIF_LABEL_LEVEL:
        return _SARIF_LABEL_LEVEL[label]
    try:
        score = float(label)
    except ValueError:
        return "none"
    if not math.isfinite(score) or score <= 0:
        return "none"
    if score >= _CVSS_ERROR:
        return "error"
    return "warning" if score >= _CVSS_WARNING else "note"


def dynamic_tool_slugs() -> set[str]:
    """Slugs of the tools that probe the running app (DAST): their 'file path' is a URL path."""
    from backend.analysis.models import AnalysisMode
    from backend.analysis.models import AnalyzerTool

    return set(AnalyzerTool.objects.filter(analysis_mode=AnalysisMode.DYNAMIC).values_list("slug", flat=True))


def latest_result_ids(run_ids: Iterable[Any]) -> set[str]:
    """The results the study tables count for these runs: latest per (run, tool)."""
    from backend.analysis.models import ToolResult

    ids = {str(r) for r in run_ids if r}
    return {str(r) for r in ToolResult.latest_ids_per_job(ids)} if ids else set()


def finding_rows_queryset(queryset: QuerySet[Any]) -> QuerySet[Any]:
    """*queryset* loading only what an exported row reads (see :data:`FINDING_ONLY`)."""
    return queryset.select_related(*FINDING_SELECT_RELATED).only(*FINDING_ONLY)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value if v not in (None, ""))
    return str(value)


def _extra(f: Any) -> dict[str, Any]:
    return f.tool_specific_data if isinstance(f.tool_specific_data, dict) else {}


def _tool_version(result: Any) -> str | None:
    version = result.summary.get("tool_version") if isinstance(result.summary, dict) else None
    return str(version) if version else None


_RUN_KEYS = (
    "generation_run_id",
    "study_id",
    "condition_id",
    "condition_label",
    "model",
    "task",
    "unit",
    "kind",
    "stack",
    "repeat_index",
)


def run_identities(run_ids: Iterable[Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    """Per generation run (by id): the run columns of an exported row, and ``{path: origin}`` of its app tree.

    ``condition_label`` is the study tables' name of the condition (``statistics.services.runs.condition_label``:
    its label, else its model id), so rows join to them by label too.
    Origins come from ``RunView.files("app")`` (``parent`` for a slot variant's overlaid parent rows).
    """
    from backend.generation.models import GenerationRun
    from backend.generation.runs.view import run_views

    ids = {str(r) for r in run_ids if r}
    if not ids:
        return {}, {}
    from backend.statistics.services.runs import condition_label

    runs = list(GenerationRun.objects.filter(id__in=ids).select_related("condition__model"))
    conditions = {str(r.id): r.condition for r in runs}
    identities: dict[str, dict[str, Any]] = {}
    origins: dict[str, dict[str, str]] = {}
    for view in run_views(runs):
        condition = conditions.get(view.id)
        identities[view.id] = {
            "generation_run_id": view.id,
            "study_id": view.study_id,
            "condition_id": str(view.condition_id) if view.condition_id is not None else None,
            "condition_label": condition_label(condition) if condition is not None else None,
            "model": view.model_slug,
            "task": view.task.ref if view.task is not None else None,
            "unit": view.unit_key,
            "kind": view.kind,
            "stack": view.stack.ref if view.stack is not None else None,
            "repeat_index": view.repeat_index,
        }
        origins[view.id] = {entry.path: entry.origin for entry in view.files("app")}
    return identities, origins


@dataclass(frozen=True)
class ExportContext:
    """What the rows of one export share: the results the study tables count, and the apps the rows describe."""

    latest_ids: set[str] = field(default_factory=set)
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    origins: dict[str, dict[str, str]] = field(default_factory=dict)

    def run_fields(self, run_id: Any) -> dict[str, Any]:
        """The run columns of a row of *run_id*'s findings (all ``None`` for an ad-hoc analysis)."""
        if not run_id:
            return dict.fromkeys(_RUN_KEYS)
        return self.runs.get(str(run_id)) or {**dict.fromkeys(_RUN_KEYS), "generation_run_id": str(run_id)}

    def file_origin(self, run_id: Any, path: str) -> str | None:
        """The origin of *path* in the run's app tree; ``None`` for a URL, an unknown path or no run."""
        if not run_id or not path:
            return None
        rel = path[2:] if path.startswith("./") else path
        return self.origins.get(str(run_id), {}).get(rel)

    def is_latest(self, result: Any) -> bool | None:
        """Whether *result* is one the study tables count; ``None`` for an ad-hoc analysis (no run)."""
        return (str(result.id) in self.latest_ids) if result.run.generation_job_id else None


def export_context(run_ids: Iterable[Any]) -> ExportContext:
    """The :class:`ExportContext` of an export whose findings describe these runs."""
    ids = {str(r) for r in run_ids if r}
    identities, origins = run_identities(ids)
    return ExportContext(latest_ids=latest_result_ids(ids), runs=identities, origins=origins)


def finding_row(f: Any, ctx: ExportContext) -> dict[str, Any]:
    """One exported finding, keyed by :data:`FINDING_HEADERS` (``None`` = not applicable / unknown)."""
    result = f.result
    extra = _extra(f)
    fixed = next((extra[k] for k in ("fixed_versions", "fix_versions", "fixed_version") if extra.get(k)), "")
    return {
        "id": str(f.id),
        "task_id": str(result.run_id),
        "result_id": str(result.id),
        "result_status": result.status,
        **ctx.run_fields(result.run.generation_job_id),
        "analyzer": result.tool_slug,
        "tool_version": _tool_version(result),
        "tool_severity": f.tool_severity or UNRATED,
        "normalized_severity": f.severity,
        "category": f.category,
        "file_path": f.file_path,
        # The origin the analysis tagged on the finding, else the run's app tree (RunView.files("app")).
        "file_origin": _text(extra.get("file_origin")) or ctx.file_origin(result.run.generation_job_id, f.file_path),
        "line": f.line_number,
        "column": f.column_number,
        "rule_id": f.rule_id,
        "message": f.title,
        "description": f.description,
        "cwe": _text(extra.get("cwe")),
        "package": _text(extra.get("package")),
        "installed_version": _text(extra.get("installed_version") or extra.get("version")),
        "fixed_versions": _text(fixed),
        "docs_url": _text(extra.get("docs_url")),
        "is_latest": ctx.is_latest(result),
        "created_at": f.created_at.isoformat(),
    }


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def finding_csv_row(f: Any, ctx: ExportContext, *, spreadsheet_safe: bool = True) -> list[Any]:
    """:func:`finding_row` as a CSV row in :data:`FINDING_HEADERS` order."""
    row = finding_row(f, ctx)
    return safe_row([_csv_value(row[h]) for h in FINDING_HEADERS], enabled=spreadsheet_safe)


def _rows_and_context(queryset: QuerySet[Any], ctx: ExportContext | None) -> tuple[list[Any], ExportContext]:
    rows = list(finding_rows_queryset(queryset))
    if ctx is None:
        ctx = export_context(f.result.run.generation_job_id for f in rows)
    return rows, ctx


def findings_csv(
    queryset: QuerySet[Any],
    ctx: ExportContext | None = None,
    *,
    spreadsheet_safe: bool = True,
) -> str:
    """Return CSV string of findings."""
    rows, ctx = _rows_and_context(queryset, ctx)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(FINDING_HEADERS)
    for f in rows:
        writer.writerow(finding_csv_row(f, ctx, spreadsheet_safe=spreadsheet_safe))
    return buf.getvalue()


def findings_json(queryset: QuerySet[Any], ctx: ExportContext | None = None) -> list[dict[str, Any]]:
    """Return list of finding dicts."""
    rows, ctx = _rows_and_context(queryset, ctx)
    return [finding_row(f, ctx) for f in rows]


def _sarif_result(f: Any, *, dynamic: bool) -> dict[str, Any]:
    extra = _extra(f)
    entry: dict[str, Any] = {
        "ruleId": f.rule_id or "unknown",
        # The tool's own label decides the level; "none" when it rates nothing.
        "level": sarif_level(f.tool_severity),
        "message": {"text": f.title},
        "locations": [],
        "properties": {
            "finding_id": str(f.id),
            "tool_severity": f.tool_severity,
            "normalized_severity": f.severity,
            "category": f.category,
        },
    }
    if dynamic:
        # A DAST finding names a URL path on the running app, not a source file.
        # The recorded host is an ephemeral container address, so only the path goes out.
        request: dict[str, Any] = {"target": f.file_path or "/"}
        if isinstance(extra.get("method"), str) and extra["method"]:
            request["method"] = extra["method"]
        entry["webRequest"] = request
        if isinstance(extra.get("param"), str) and extra["param"]:
            entry["properties"]["param"] = extra["param"]
    elif f.file_path:
        location: dict[str, Any] = {"physicalLocation": {"artifactLocation": {"uri": f.file_path}}}
        if f.line_number and f.line_number > 0:
            region: dict[str, Any] = {"startLine": f.line_number}
            if f.column_number and f.column_number > 0:
                region["startColumn"] = f.column_number
            location["physicalLocation"]["region"] = region
        entry["locations"].append(location)
    return entry


def _sarif_rules(findings: list[Any]) -> list[dict[str, Any]]:
    rules: dict[str, dict[str, Any]] = {}
    for f in findings:
        if not f.rule_id or f.rule_id in rules:
            continue
        extra = _extra(f)
        rule: dict[str, Any] = {"id": f.rule_id}
        if isinstance(extra.get("rule_name"), str) and extra["rule_name"]:
            rule["shortDescription"] = {"text": extra["rule_name"]}
        docs = extra.get("docs_url")
        if isinstance(docs, str) and docs.startswith(("http://", "https://")):
            rule["helpUri"] = docs
        rules[f.rule_id] = rule
    return [rules[k] for k in sorted(rules)]


def findings_sarif(
    queryset: QuerySet[Any],
    ctx: ExportContext | None = None,
    *,
    total: int | None = None,
) -> dict[str, Any]:
    """Return a SARIF 2.1.0 log with one run per tool result, i.e. per (analysis run, tool).

    Each run names its tool with the version the analyzer recorded (omitted
    when none was) and, in ``properties``, the app it describes. With *total*
    (findings matching the query before the export limit), the log's
    ``properties`` say whether the export was truncated.
    """
    rows, ctx = _rows_and_context(queryset, ctx)
    dynamic = dynamic_tool_slugs()
    by_result: dict[Any, list[Any]] = {}
    for f in rows:
        by_result.setdefault(f.result_id, []).append(f)

    runs = []
    for findings in by_result.values():
        result = findings[0].result
        driver: dict[str, Any] = {"name": result.tool_slug}
        version = _tool_version(result)
        if version:
            driver["version"] = version
        rules = _sarif_rules(findings)
        if rules:
            driver["rules"] = rules
        identity = {
            "analysis_run_id": str(result.run_id),
            "result_id": str(result.id),
            "result_status": result.status,
            **ctx.run_fields(result.run.generation_job_id),
            "is_latest": ctx.is_latest(result),
        }
        subject = identity["generation_run_id"] or identity["analysis_run_id"]
        runs.append(
            {
                "tool": {"driver": driver},
                "automationDetails": {"id": f"{result.tool_slug}/{subject}/{result.id}"},
                "properties": {k: v for k, v in identity.items() if v is not None},
                "results": [_sarif_result(f, dynamic=result.tool_slug in dynamic) for f in findings],
            },
        )

    log: dict[str, Any] = {"version": "2.1.0", "$schema": SARIF_SCHEMA, "runs": runs}
    if total is not None:
        log["properties"] = {"total_findings": total, "truncated": total > len(rows)}
    return log
