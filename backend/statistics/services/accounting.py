"""One source of truth for "how many tools" and "how many executions".

``tool_inventory`` lists every analyzer the platform ships (the catalog,
enabled or not) plus the platform's own runtime evaluators, which are not
catalog tools but do execute against apps. ``run_accounting`` counts what
actually ran for a scope of jobs from the stored results — never
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
from backend.generation.models import GenerationJob

if TYPE_CHECKING:
    from django.db.models import QuerySet

# Evaluators the platform runs itself (not AnalyzerTool rows).
PLATFORM_EVALUATORS: tuple[dict[str, str], ...] = (
    {
        "slug": "conformance-tester",
        "name": "Conformance tester",
        "category": "dynamic",
        "description": "Builds and starts the app on an isolated network, then runs the hidden API/accounts suite.",
        "runs_on": "every evaluated app (build/start failures are outcomes, not skips)",
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
            "languages",
            "version",
            "is_enabled",
        ),
    )
    for tool in tools:
        # v1 had one target language per tool; v2 specs list several.
        languages = tool.get("languages") if isinstance(tool.get("languages"), list) else []
        tool["target_language"] = ", ".join(str(lang) for lang in languages) or "any"
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
    for slug, category, status, job_id in results.values_list(
        "tool_slug",
        "category",
        "status",
        "run__generation_job_id",
    ):
        per_tool[slug][status] += 1
        if job_id is not None:
            apps[slug].add(job_id)
        categories.setdefault(slug, category)
    rows = []
    for slug, counts in sorted(per_tool.items()):
        rows.append(
            {
                "tool": slug,
                "category": categories.get(slug, ""),
                "executions": sum(counts.values()),
                # "completed" kept for v1 readers: v2 calls a clean execution "succeeded".
                "completed": counts.get(ToolResult.Status.SUCCEEDED, 0),
                "succeeded": counts.get(ToolResult.Status.SUCCEEDED, 0),
                "failed": counts.get(ToolResult.Status.FAILED, 0),
                "timed_out": counts.get(ToolResult.Status.TIMED_OUT, 0),
                "not_applicable": counts.get(ToolResult.Status.NOT_APPLICABLE, 0),
                "cancelled": counts.get(ToolResult.Status.CANCELLED, 0),
                "skipped": counts.get(ToolResult.Status.SKIPPED, 0),
                "unfinished": counts.get(ToolResult.Status.PENDING, 0) + counts.get(ToolResult.Status.RUNNING, 0),
                "apps": len(apps[slug]),
            },
        )
    return rows


def _evaluation_counts(jobs: list[GenerationJob]) -> dict[str, Any]:
    conformance = Counter()
    smoke = Counter()
    ablation: dict[str, Counter] = defaultdict(Counter)
    for job in jobs:
        result = job.result_data if isinstance(job.result_data, dict) else {}
        evaluation = result.get("evaluation")
        if isinstance(evaluation, dict) and evaluation.get("status"):
            conformance[evaluation["status"]] += 1
        metrics = job.metrics if isinstance(job.metrics, dict) else {}
        probe = metrics.get("smoke") if isinstance(metrics.get("smoke"), dict) else {}
        if not probe:
            # Before the probe got its own key it overwrote metrics["functional"].
            legacy = metrics.get("functional") if isinstance(metrics.get("functional"), dict) else {}
            if legacy.get("source") != "conformance" and "health_ok" in legacy:
                probe = legacy
        if probe:
            smoke["passed" if probe.get("passed") else "failed"] += 1
        for mode, entry in (result.get("healing_ablation") or {}).items():
            if isinstance(entry, dict) and entry.get("status"):
                ablation[mode][entry["status"]] += 1

    def block(c: Counter) -> dict[str, Any]:
        deployed = c.get("passed", 0) + c.get("failed", 0)
        return {
            "executions": sum(c.values()),
            "by_status": dict(sorted(c.items())),
            "deployed": deployed,
            "not_deployed": c.get("build_failed", 0) + c.get("start_failed", 0),
            "infra_error": c.get("infra_error", 0),
        }

    return {
        "conformance": block(conformance),
        "smoke": {"executions": sum(smoke.values()), "by_status": dict(smoke)},
        "healing_ablation": {mode: block(c) for mode, c in sorted(ablation.items())},
    }


def run_accounting(jobs: QuerySet[GenerationJob], *, run_ids: list[Any] | None = None) -> dict[str, Any]:
    """Apps, analysis runs and per-tool executions actually recorded for *jobs*.

    ``latest`` counts the runs the result tables describe: *run_ids* when
    given (a report's canonical runs under its analysis plan), else each
    job's latest finished run. ``all_runs`` includes re-analyses and
    unfinished runs (self-test canary runs are never counted). ``planned``
    is what the runs asked for (the tools their frozen plans list), so a gap
    between planned and executed is visible instead of assumed away.
    """
    job_list = list(jobs)
    job_ids = [j.id for j in job_list]
    status = Counter(j.status for j in job_list)
    latest_ids = list(run_ids) if run_ids is not None else AnalysisRun.latest_ids_per_job(job_ids)
    all_runs = AnalysisRun.objects.filter(generation_job_id__in=job_ids).exclude(purpose=AnalysisRun.Purpose.CANARY)
    latest_results = ToolResult.objects.filter(run_id__in=latest_ids)
    # v2 runs freeze their tool list in the plan (``AnalysisRun.tool_slugs`` reads it).
    planned = sum(len(run.tool_slugs) for run in AnalysisRun.objects.filter(id__in=latest_ids).only("id", "plan"))

    latest_tools = _tool_rows(latest_results)
    evaluations = _evaluation_counts(job_list)
    static_exec = sum(t["executions"] for t in latest_tools)
    dynamic_exec = evaluations["conformance"]["executions"] + evaluations["smoke"]["executions"]
    return {
        "apps": {
            "jobs": len(job_list),
            "by_status": dict(sorted(status.items())),
            "generated": status.get(GenerationJob.Status.COMPLETED, 0),
            "analyzed": len(latest_ids),
            "evaluated": evaluations["conformance"]["executions"] - evaluations["conformance"]["infra_error"],
            "deployed": evaluations["conformance"]["deployed"],
        },
        "analysis_runs": {
            "latest": len(latest_ids),
            "all": all_runs.count(),
            "by_status": dict(sorted(Counter(all_runs.values_list("status", flat=True)).items())),
        },
        "tools": {
            "latest": latest_tools,
            "all_runs": _tool_rows(ToolResult.objects.filter(run__in=all_runs)),
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
