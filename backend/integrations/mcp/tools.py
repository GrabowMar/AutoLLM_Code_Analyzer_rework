"""MCP tools: a curated set over the most useful API operations, docs, and a generic API escape hatch.

Curated tools carry hand-written names and descriptions but take their input
schema from the operation's OpenAPI definition. The three generic tools
(``find_api_operations`` / ``describe_api_operation`` / ``call_api``) reach
every other operation, so an agent can do anything the UI can.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from backend.integrations.mcp import openapi
from backend.integrations.mcp.dispatch import ApiResult
from backend.integrations.mcp.dispatch import DispatchError
from backend.integrations.mcp.dispatch import call_api

if TYPE_CHECKING:
    from django.http import HttpRequest

MAX_RESULT_CHARS = 100_000


@dataclass(frozen=True)
class Curated:
    name: str
    operation_id: str  # without the "backend_" prefix
    description: str


# (tool name, operation, description). Keep descriptions about *when* to use the
# tool and how ids flow between tools; parameter detail comes from the schema.
CURATED: tuple[Curated, ...] = (
    # ── account & catalog ───────────────────────────────────────────────
    Curated("whoami", "users_api_views_retrieve_current_user", "The account this MCP connection acts as."),
    Curated(
        "search_models",
        "llm_models_api_views_list_models",
        "Search the LLM catalog (OpenRouter models plus models on your own endpoints). "
        "Each item's numeric `id` is the `model_id` / `model_ids` value generation tools take.",
    ),
    Curated("get_model", "llm_models_api_views_get_model", "Full details of one model by its slug."),
    Curated(
        "compare_models",
        "llm_models_api_views_get_model_comparison",
        "Side-by-side comparison of several models (pricing, context, capabilities, scores).",
    ),
    Curated(
        "list_app_templates",
        "generation_api_views_templates_list_app_templates",
        "Application requirement templates (what to build). Their ids are `app_requirement_ids`.",
    ),
    Curated(
        "list_stacks",
        "generation_api_views_templates_list_stacks",
        "Technology stacks a generated app can target (`stack_slug`).",
    ),
    # ── generation ──────────────────────────────────────────────────────
    Curated(
        "generate_apps",
        "generation_api_views_custom_create_scaffolding_jobs",
        "Generate complete applications: one job per (template x model x trial) using the "
        "scaffolding pipeline. Costs LLM credits. Poll get_generation_job for progress.",
    ),
    Curated(
        "generate_custom",
        "generation_api_views_custom_create_custom_job",
        "One free-form generation job from your own system + user prompt for a single model.",
    ),
    Curated(
        "preview_agent_jobs",
        "generation_api_views_custom_preview_agent_jobs",
        "Free dry-run of agent-mode jobs: shows resolved harness, budgets and warnings without spending.",
    ),
    Curated(
        "create_agent_jobs",
        "generation_api_views_custom_create_agent_jobs",
        "Start agent-mode generation (a coding agent builds the app in a sandbox). Costs LLM credits.",
    ),
    Curated("list_generation_jobs", "generation_api_views_jobs_list_jobs", "List/filter generation jobs."),
    Curated("get_generation_job", "generation_api_views_jobs_get_job", "Status, metrics and output of one job."),
    Curated(
        "get_job_artifacts",
        "generation_api_views_jobs_get_job_artifacts",
        "Generated files and run artifacts of a job.",
    ),
    Curated("generation_stats", "generation_api_views_jobs_job_stats", "Aggregate generation job counts."),
    Curated("cancel_generation_job", "generation_api_views_jobs_cancel_job", "Cancel a queued/running job."),
    Curated("retry_generation_job", "generation_api_views_jobs_retry_job", "Retry a failed job."),
    # ── runtime (run the generated app) ────────────────────────────────
    Curated(
        "build_app",
        "runtime_api_views_build_container_for_job",
        "Build and start the container for a generated app so it can be clicked through.",
    ),
    Curated("smoke_test_app", "runtime_api_views_smoke_test_job", "Run the smoke test against a built app."),
    Curated("list_containers", "runtime_api_views_list_containers", "Running/stopped sample-app containers."),
    Curated("container_logs", "runtime_api_views_get_container_logs", "Recent logs of an app container."),
    Curated("start_container", "runtime_api_views_start_container", "Start an app container."),
    Curated("stop_container", "runtime_api_views_stop_container", "Stop an app container."),
    # ── analysis ────────────────────────────────────────────────────────
    Curated(
        "list_analyzers",
        "analysis_api_views_tools_list_tools",
        "Static-analysis tools available (their slugs are `tool_slugs`).",
    ),
    Curated(
        "analyze",
        "analysis_api_views_runs_create_run",
        "Run analyzers on a generated job (`generation_job_id`) or on pasted `source_code` "
        "({path: content}). Returns a run id; poll get_analysis_run.",
    ),
    Curated("list_analysis_runs", "analysis_api_views_runs_list_runs", "List analysis runs."),
    Curated("get_analysis_run", "analysis_api_views_runs_get_run", "Status and summary of one analysis run."),
    Curated(
        "list_findings",
        "analysis_api_views_runs_list_findings",
        "Findings of a run, filterable by severity/tool.",
    ),
    Curated("cancel_analysis_run", "analysis_api_views_runs_cancel_run", "Cancel an analysis run."),
    # ── results ─────────────────────────────────────────────────────────
    Curated("rankings", "rankings_api_views_list_rankings", "Model leaderboard by measured code quality."),
    Curated("top_models", "rankings_api_views_top_models", "Best models right now (short list)."),
    Curated("dashboard", "statistics_api_views_get_dashboard", "Platform dashboard: KPIs, trends, recent activity."),
    Curated("top_findings", "statistics_api_views_get_top_findings", "Most frequent findings across all apps."),
    Curated("list_reports", "reports_api_views_list_reports", "Generated reports."),
    Curated("generate_report", "reports_api_views_generate_report", "Generate a report (async)."),
    Curated("get_report_data", "reports_api_views_get_report_data", "The data of a finished report."),
    # ── experiments ─────────────────────────────────────────────────────
    Curated("list_experiments", "generation_api_views_experiments_list_experiments", "Controlled experiments."),
    Curated(
        "preview_experiment",
        "generation_api_views_experiments_preview_design",
        "Free dry-run of an experiment design: cells, cost estimate, warnings.",
    ),
    Curated(
        "create_experiment",
        "generation_api_views_experiments_create_experiment",
        "Create an experiment (models x templates x repeats). Set launch=true to start it.",
    ),
    Curated("get_experiment", "generation_api_views_experiments_get_experiment", "One experiment's definition."),
    Curated("experiment_status", "generation_api_views_experiments_status", "Live progress of an experiment."),
    Curated("experiment_results", "generation_api_views_experiments_results", "Statistical results of an experiment."),
    Curated("launch_experiment", "generation_api_views_experiments_launch", "Launch a draft experiment."),
    Curated("pause_experiment", "generation_api_views_experiments_pause", "Pause a running experiment."),
    Curated("resume_experiment", "generation_api_views_experiments_resume", "Resume a paused experiment."),
    Curated("cancel_experiment", "generation_api_views_experiments_cancel", "Cancel an experiment."),
    # ── infrastructure ──────────────────────────────────────────────────
    Curated(
        "list_endpoints",
        "inference_api_views_list_endpoints",
        "LLM endpoints (OpenRouter, cloud providers, local).",
    ),
    Curated("execution_queue", "execution_api_views_get_queue", "What the executor is running and what is queued."),
)

# ``?toolset=core`` (on the MCP URL) trims the list to these for clients with a small tool budget.
CORE_TOOLS = frozenset(
    {
        "whoami",
        "search_models",
        "list_app_templates",
        "generate_apps",
        "list_generation_jobs",
        "get_generation_job",
        "list_analyzers",
        "analyze",
        "get_analysis_run",
        "list_findings",
        "rankings",
        "dashboard",
        "list_experiments",
        "experiment_status",
        "experiment_results",
        "find_api_operations",
        "describe_api_operation",
        "call_api",
        "search_docs",
        "read_doc",
    },
)

GENERIC_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "find_api_operations",
        "description": (
            "Search all LLM Lab REST operations by keyword and/or tag. Use when no dedicated "
            "tool fits; then describe_api_operation and call_api."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, e.g. 'experiment schedule'."},
                "tag": {"type": "string", "description": "Optional tag, e.g. generation, analysis, inference."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 40},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "describe_api_operation",
        "description": (
            "Parameters and JSON body schema of one operation (by operation_id, short_id or 'METHOD /path')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"operation": {"type": "string"}},
            "required": ["operation"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "call_api",
        "description": (
            "Call any LLM Lab REST endpoint as the connected user. `path` is the concrete path "
            "(e.g. /api/generation/jobs/42/), `query` the query parameters, `body` the JSON body."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                "path": {"type": "string"},
                "query": {"type": "object", "additionalProperties": True},
                "body": {"description": "JSON body (object or array)."},
            },
            "required": ["method", "path"],
        },
        "annotations": {"destructiveHint": True, "openWorldHint": False},
    },
    {
        "name": "search_docs",
        "description": "Keyword search over the LLM Lab documentation.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "read_doc",
        "description": "Read one documentation page (Markdown) by slug, e.g. EXPERIMENTS or GENERATION_PROCESS.",
        "inputSchema": {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
)
GENERIC_NAMES = frozenset(t["name"] for t in GENERIC_TOOLS)


class ToolError(Exception):
    """A tool failed in a way the model should see (returned with ``isError``)."""


@dataclass
class Caller:
    """Who is calling: the origin request, raw bearer token, its user and whether it is read-only."""

    request: HttpRequest
    raw_token: str
    user: Any
    read_only: bool
    core_only: bool = False


def _curated_index() -> dict[str, tuple[Curated, openapi.Operation]]:
    ops = openapi.operations()
    index: dict[str, tuple[Curated, openapi.Operation]] = {}
    for item in CURATED:
        op = ops.get(f"backend_{item.operation_id}")
        if op is not None:  # an operation renamed upstream just drops its tool
            index[item.name] = (item, op)
    return index


def _annotations(op: openapi.Operation) -> dict[str, Any]:
    return {
        "title": op.summary or op.short_id,
        "readOnlyHint": op.read_only,
        "destructiveHint": op.method == "DELETE" or "cancel" in op.path or "delete" in op.operation_id,
        "idempotentHint": op.method in {"GET", "PUT", "DELETE"},
        "openWorldHint": False,
    }


def list_tools(caller: Caller) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for name, (item, op) in _curated_index().items():
        if caller.read_only and not op.read_only:
            continue
        tools.append(
            {
                "name": name,
                "description": f"{item.description}\n\n[{op.method} {op.path}]",
                "inputSchema": openapi.input_schema(op),
                "annotations": _annotations(op),
            },
        )
    tools.extend(GENERIC_TOOLS)
    if caller.core_only:
        tools = [t for t in tools if t["name"] in CORE_TOOLS]
    return tools


def _format(result: ApiResult) -> str:
    if result.data is not None:
        text = json.dumps(result.data, separators=(",", ":"), ensure_ascii=False, default=str)
    else:
        text = result.text or ""
        if result.content_type and "json" not in result.content_type:
            text = f"[{result.content_type}]\n{text}"
    if result.truncated or len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n… (truncated; narrow the query or paginate)"
    return text


def _result(result: ApiResult) -> dict[str, Any]:
    text = _format(result)
    if not result.ok:
        return {"content": [{"type": "text", "text": f"HTTP {result.status}\n{text}"}], "isError": True}
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _text(value: Any) -> dict[str, Any]:
    text = value if isinstance(value, str) else json.dumps(value, indent=1, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": text[:MAX_RESULT_CHARS]}], "isError": False}


def _run_operation(caller: Caller, op: openapi.Operation, arguments: dict[str, Any]) -> dict[str, Any]:
    if caller.read_only and not op.read_only:
        msg = "this token is read-only (scope 'read'); write operations are disabled"
        raise ToolError(msg)
    try:
        path = openapi.render_path(op, arguments)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    result = call_api(
        caller.request,
        caller.raw_token,
        caller.user,
        op.method,
        path,
        query=openapi.query_from(op, arguments),
        body=arguments.get("body"),
    )
    return _result(result)


def _generic(caller: Caller, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "find_api_operations":
        hits = openapi.search(
            str(arguments.get("query") or ""),
            str(arguments.get("tag") or ""),
            int(arguments.get("limit") or 40),
        )
        if caller.read_only:
            hits = [op for op in hits if op.read_only]
        return _text(
            [{"operation": op.short_id, "method": op.method, "path": op.path, "summary": op.summary} for op in hits],
        )
    if name == "describe_api_operation":
        op = openapi.find(str(arguments.get("operation") or ""))
        if op is None:
            msg = "unknown operation; use find_api_operations"
            raise ToolError(msg)
        return _text(openapi.describe(op))
    if name == "call_api":
        method = str(arguments.get("method") or "GET").upper()
        if caller.read_only and method != "GET":
            msg = "this token is read-only (scope 'read'); only GET is allowed"
            raise ToolError(msg)
        query = arguments.get("query") or None
        if query is not None and not isinstance(query, dict):
            msg = "query must be an object"
            raise ToolError(msg)
        try:
            result = call_api(
                caller.request,
                caller.raw_token,
                caller.user,
                method,
                str(arguments.get("path") or ""),
                query=query,
                body=arguments.get("body"),
            )
        except DispatchError as exc:
            raise ToolError(str(exc)) from exc
        return _result(result)
    if name == "search_docs":
        from backend.docs import services as docs

        return _text(docs.search_docs(str(arguments.get("query") or ""))[:20])
    if name == "read_doc":
        from backend.docs import services as docs

        page = docs.get_doc(str(arguments.get("slug") or ""))
        if not page:
            msg = "no such doc; use search_docs"
            raise ToolError(msg)
        return _text(page["raw"])
    msg = f"unknown tool {name}"
    raise ToolError(msg)


def call_tool(caller: Caller, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    arguments = arguments or {}
    if not isinstance(arguments, dict):
        msg = "arguments must be an object"
        raise ToolError(msg)
    if name in GENERIC_NAMES:
        return _generic(caller, name, arguments)
    entry = _curated_index().get(name)
    if entry is None:
        msg = f"unknown tool {name}"
        raise ToolError(msg)
    return _run_operation(caller, entry[1], arguments)
