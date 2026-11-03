"""One source of truth for "how many tools" and "how many executions".

``tool_inventory`` lists every analyzer the platform ships (the catalog,
enabled or not) plus the platform's own runtime evaluators, which are not
catalog tools but do execute against apps. ``run_accounting`` counts what
actually ran for a scope of generation runs from the stored results — never
``tools x apps``: dynamic checks only run on apps that built, a tool can be
skipped or fail, and a run may select a subset of tools.
"""

from __future__ import annotations

from collections import Counter
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import ToolKind
from backend.analysis.models import ToolResult
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models.choices import RunStatus

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.db.models import QuerySet

# Evaluators the platform runs itself (not AnalyzerTool rows).
PLATFORM_EVALUATORS: tuple[dict[str, str], ...] = (
    {
        "slug": "eval-tester",
        "name": "Evaluation suite",
        "category": "dynamic",
        "description": "Locks, builds and starts the app on an isolated network, then runs the pinned EvalSuite "
        "(acceptance checks and probes).",
        "runs_on": "every evaluated run (build/start failures are outcomes, not skips)",
    },
    {
        "slug": "smoke",
        "name": "Smoke test",
        "category": "dynamic",
        "description": "Health check plus the declared GET endpoints of a manually deployed container.",
        "runs_on": "apps deployed through the Applications page",
    },
)


def tool_inventory() -> dict[str, Any]:
    """Catalog analyzers and platform evaluators, with the counts a paper's tool table needs."""
    tools = list(
        AnalyzerTool.objects.order_by("display_order", "slug").values(
            "slug",
            "name",
            "category",
            "kind",
            "target_language",
            "version",
            "is_enabled",
        ),
    )
    enabled = [t for t in tools if t["is_enabled"]]
    by_category = Counter(t["category"] for t in enabled)
    by_kind = Counter(t["kind"] for t in enabled)
    deterministic = [t for t in enabled if t["kind"] != ToolKind.AI]
    return {
        "analyzers": tools,
        "evaluators": [dict(e) for e in PLATFORM_EVALUATORS],
        "counts": {
            "catalog_registered": len(tools),
            "catalog_enabled": len(enabled),
            "catalog_enabled_deterministic": len(deterministic),
            "catalog_enabled_ai": len(enabled) - len(deterministic),
            "platform_evaluators": len(PLATFORM_EVALUATORS),
            # The number to report as "tools": every enabled analyzer + the platform evaluators.
            "total_enabled": len(enabled) + len(PLATFORM_EVALUATORS),
            "enabled_by_category": dict(sorted(by_category.items())),
            "enabled_by_kind": dict(sorted(by_kind.items())),
        },
    }


def _tool_rows(results: QuerySet[ToolResult]) -> list[dict[str, Any]]:
    per_tool: dict[str, Counter] = defaultdict(Counter)
    apps: dict[str, set] = defaultdict(set)
    categories: dict[str, str] = {}
    for slug, category, status, run_id in results.values_list(
        "tool_slug",
        "category",
        "status",
        "run__generation_job_id",
    ):
        per_tool[slug][status] += 1
        if run_id is not None:
            apps[slug].add(run_id)
        categories.setdefault(slug, category)
    rows = []
    for slug, counts in sorted(per_tool.items()):
        rows.append(
            {
                "tool": slug,
                "category": categories.get(slug, ""),
                "executions": sum(counts.values()),
                "completed": counts.get(ToolResult.Status.COMPLETED, 0),
                "failed": counts.get(ToolResult.Status.FAILED, 0),
                "skipped": counts.get(ToolResult.Status.SKIPPED, 0),
                "unfinished": counts.get(ToolResult.Status.PENDING, 0) + counts.get(ToolResult.Status.RUNNING, 0),
                "apps": len(apps[slug]),
            },
        )
    return rows


# RunEvaluation statuses that say something about the app (an error or "not measured" does not).
_EVALUATED = frozenset({"measured"})


def _evaluation_counts(run_ids: list[Any]) -> dict[str, Any]:
    """Executions of the evaluation suite: every RunEvaluation (retests apart), and each run's latest.

    ``deployed`` counts latest evaluations whose app built and started (``stages.start.status == ok``);
    ``not_deployed`` those whose build or start failed. ``not_measured`` and ``error`` are never evaluated.
    """
    rows = list(
        RunEvaluation.objects.filter(run_id__in=run_ids).values_list(
            "id",
            "run_id",
            "status",
            "stages",
            "retest_of_id",
            "created_at",
        ),
    )
    executions = Counter(status for _id, _run, status, _stages, retest, _at in rows if retest is None)
    retests = sum(1 for row in rows if row[4] is not None)
    latest: dict[Any, tuple] = {}
    for row in rows:
        if row[4] is None and (row[1] not in latest or (row[5], row[0]) > (latest[row[1]][5], latest[row[1]][0])):
            latest[row[1]] = row
    by_status = Counter(row[2] for row in latest.values())

    def stage(row: tuple, name: str) -> str | None:
        stages = row[3] if isinstance(row[3], dict) else {}
        record = stages.get(name) if isinstance(stages.get(name), dict) else {}
        return record.get("status")

    deployed = sum(1 for row in latest.values() if stage(row, "start") == "ok")
    not_deployed = sum(1 for row in latest.values() if "fail" in (stage(row, "build"), stage(row, "start")))
    return {
        "suite": {
            "executions": sum(executions.values()),
            "by_status": dict(sorted(executions.items())),
            "retests": retests,
            "runs": len(latest),
            "latest_by_status": dict(sorted(by_status.items())),
            "deployed": deployed,
            "not_deployed": not_deployed,
            "not_measured": by_status.get("not_measured", 0),
            "error": by_status.get("error", 0),
            # Latest evaluations that say something about the app.
            "evaluated": sum(n for status, n in by_status.items() if status in _EVALUATED),
        },
    }


def run_accounting(runs: QuerySet[GenerationRun] | Iterable[GenerationRun]) -> dict[str, Any]:
    """Runs, analysis runs, per-tool executions and evaluations actually recorded for *runs*.

    ``tools.latest`` counts each run's latest result per tool (what the result tables describe),
    ``analysis_runs.latest`` its latest finished analysis; ``all_runs`` includes re-analyses and unfinished
    analyses. ``planned_static`` is what the analyses asked for: per run, the distinct tools selected across
    its finished analyses (completed, partial or failed, the analyses ``tools.latest`` draws from). It counts
    the same (run, tool) universe as ``static``, so a gap means a tool was requested and never produced a
    result, instead of a partial re-analysis looking like unplanned work.
    """
    run_list = list(runs)
    run_ids = [r.pk for r in run_list]
    status = Counter(r.status for r in run_list)
    latest_ids = AnalysisRun.latest_ids_per_job(run_ids)
    all_analyses = AnalysisRun.objects.filter(generation_job_id__in=run_ids)
    # What the issue tables count: each run's latest result per tool (a later
    # partial re-analysis does not hide the tools it did not re-run).
    latest_results = ToolResult.objects.filter(id__in=ToolResult.latest_ids_per_job(run_ids))
    planned_by_run: dict[Any, set[str]] = defaultdict(set)
    for run_id, slugs in all_analyses.filter(
        status__in=[AnalysisRun.Status.COMPLETED, AnalysisRun.Status.PARTIAL, AnalysisRun.Status.FAILED],
    ).values_list("generation_job_id", "tool_slugs"):
        planned_by_run[run_id].update(slugs or [])
    planned = sum(len(slugs) for slugs in planned_by_run.values())

    latest_tools = _tool_rows(latest_results)
    evaluations = _evaluation_counts(run_ids)
    static_exec = sum(t["executions"] for t in latest_tools)
    dynamic_exec = evaluations["suite"]["executions"]
    return {
        "apps": {
            "runs": len(run_list),
            "by_status": dict(sorted(status.items())),
            "by_outcome": dict(sorted(Counter(r.outcome or "" for r in run_list).items())),
            "usable": sum(1 for r in run_list if r.usable),
            "generated": status.get(RunStatus.COMPLETED, 0),
            "analyzed": len(latest_ids),
            "evaluated": evaluations["suite"]["evaluated"],
            "deployed": evaluations["suite"]["deployed"],
        },
        "analysis_runs": {
            "latest": len(latest_ids),
            "all": all_analyses.count(),
            "by_status": dict(sorted(Counter(all_analyses.values_list("status", flat=True)).items())),
        },
        "tools": {
            "latest": latest_tools,
            "all_runs": _tool_rows(ToolResult.objects.filter(run__generation_job_id__in=run_ids)),
            "distinct_tools_executed": len(latest_tools),
        },
        "evaluations": evaluations,
        "executions": {
            "planned_static": planned,
            "static": static_exec,
            "static_completed": sum(t["completed"] for t in latest_tools),
            "dynamic": dynamic_exec,
            "total": static_exec + dynamic_exec,
        },
    }
