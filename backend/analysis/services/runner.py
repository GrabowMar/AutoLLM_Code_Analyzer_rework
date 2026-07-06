"""Execute an :class:`AnalysisRun`: run each selected tool, persist findings."""

from __future__ import annotations

import logging
from pathlib import Path

from django.db import connection
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.services import ai_runner
from backend.analysis.services import parsers
from backend.analysis.services import workspace_service
from backend.analysis.services.base import AnalyzerOutput
from backend.analysis.services.base import ParsedOutput
from backend.analysis.services.base import build_severity_counts
from backend.common.threading import dispatch_in_thread
from backend.realtime import events as realtime
from backend.runtime.services import docker_manager

logger = logging.getLogger(__name__)

# Stored stdout is a debugging artifact (parsing happens on the full output
# first); keep enough to be useful without bloating rows.
_STDOUT_STORE_LIMIT = 30_000

_EXT_BY_KEY = {
    "backend": ".py",
    "backend_code": ".py",
    "python": ".py",
    "content": ".py",
    "frontend": ".js",
    "frontend_code": ".jsx",
    "javascript": ".js",
    "typescript": ".ts",
}


def dispatch(run_id: str) -> None:
    """Run an analysis on a background daemon thread."""
    dispatch_in_thread(_execute, run_id)


def _publish(run: AnalysisRun) -> None:
    realtime.publish(
        f"analysis:{run.id}",
        {
            "type": "status",
            "status": run.status,
            "summary": run.summary,
            "updated_at": timezone.now().isoformat(),
        },
    )


def _execute(run_id: str) -> None:
    try:
        run = AnalysisRun.objects.select_related("created_by", "workspace").get(id=run_id)
    except AnalysisRun.DoesNotExist:
        logger.warning("AnalysisRun %s vanished before execution", run_id)
        return
    try:
        execute(run)
    except Exception:
        logger.exception("AnalysisRun %s failed unexpectedly", run_id)
        run.status = AnalysisRun.Status.FAILED
        run.error_message = "Internal error during analysis"
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "error_message", "completed_at"])
        _publish(run)
    finally:
        connection.close()


def execute(run: AnalysisRun) -> AnalysisRun:
    """Synchronously execute *run* (also used directly by tests/automation)."""
    run.status = AnalysisRun.Status.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])
    _publish(run)

    code = run.get_code_for_analysis()
    files = _materialize(code)
    truncated_inputs = _truncated_inputs(run)
    tools = _resolve_tools(run.tool_slugs)
    tool_configs = _resolve_tool_configs(run)

    container_name = ""
    needs_container = any(t.kind == "container" for t in tools.values())
    if needs_container and run.workspace_id:
        try:
            container_name = workspace_service.require_ready_container(run.workspace)
            # /work is reused across runs in the long-lived workspace container.
            # Clear it first so files (and tool caches) from a previous run don't
            # leak into this one's findings.
            docker_manager.exec_in(
                container_name,
                f"find {workspace_service.WORK_DIR} -mindepth 1 -delete",
                timeout_s=30,
            )
            if files:
                docker_manager.copy_files_in(
                    container_name,
                    files,
                    dest=workspace_service.WORK_DIR,
                )
        except RuntimeError as exc:
            run.status = AnalysisRun.Status.FAILED
            run.error_message = str(exc)
            run.completed_at = timezone.now()
            run.save(update_fields=["status", "error_message", "completed_at"])
            _publish(run)
            return run

    statuses: list[str] = []
    for slug in run.tool_slugs:
        tool = tools.get(slug)
        result = ToolResult.objects.create(
            run=run,
            tool_slug=slug,
            category=tool.category if tool else "",
            status=ToolResult.Status.RUNNING,
        )
        if tool is None or not tool.is_enabled:
            result.status = ToolResult.Status.SKIPPED
            result.error_message = "Tool unavailable"
            result.save(update_fields=["status", "error_message"])
            statuses.append(result.status)
            continue

        if tool.kind == "ai":
            output = ai_runner.run(tool, files, tool_configs.get(slug) or {}, run.created_by)
        else:
            output = _run_container_tool(tool, container_name)

        _persist_result(result, output)
        statuses.append(result.status)

    _finalize(run, statuses, truncated_inputs)
    return run


def _resolve_tools(slugs: list[str]) -> dict[str, AnalyzerTool]:
    qs = AnalyzerTool.objects.filter(slug__in=list(slugs))
    return {t.slug: t for t in qs}


def _resolve_tool_configs(run: AnalysisRun) -> dict[str, dict]:
    """Per-user tool config, keyed by slug (from the workspace's installed tools)."""
    if not run.workspace_id:
        return {}
    return {it.tool.slug: it.config or {} for it in run.workspace.installed_tools.select_related("tool")}


def _truncated_inputs(run: AnalysisRun) -> list[str]:
    """Which parts of the analyzed code were cut off at generation time."""
    if not run.generation_job_id:
        return []
    data = getattr(run.generation_job, "result_data", None) or {}
    if not isinstance(data, dict):
        return []
    return [part for part in ("backend", "frontend") if data.get(f"{part}_truncated")]


def _normalize_path(path: str) -> str:
    """One file, one identity: container tools report /work-prefixed paths,
    jscpd relative ones — strip the workspace prefix so findings group."""
    path = path.strip()
    path = path.removeprefix(workspace_service.WORK_DIR + "/")
    return path.removeprefix("./")


def _run_container_tool(tool: AnalyzerTool, container_name: str) -> AnalyzerOutput:
    if not container_name:
        return AnalyzerOutput(error="Analyzer workspace unavailable")
    if not tool.run_cmd:
        return AnalyzerOutput(error="Tool has no run command")
    cmd = tool.run_cmd.replace("{target}", workspace_service.WORK_DIR)
    result = docker_manager.exec_in(
        container_name,
        cmd,
        timeout_s=tool.run_timeout,
        workdir=workspace_service.WORK_DIR,
    )
    if result.get("error") and result.get("exit_code") == -1:
        return AnalyzerOutput(error=result["error"])
    raw = result.get("output", "")
    parsed = parsers.parse(tool.parser_key, raw) if tool.parser_key else ParsedOutput()
    return AnalyzerOutput(
        findings=parsed.findings,
        metrics=parsed.metrics,
        raw_output={"exit_code": result.get("exit_code"), "stdout": raw[:_STDOUT_STORE_LIMIT]},
        summary={"finding_count": len(parsed.findings)},
    )


def _persist_result(result: ToolResult, output: AnalyzerOutput) -> None:
    if output.has_error:
        result.status = ToolResult.Status.FAILED
        result.error_message = output.error or ""
        result.raw_output = output.raw_output
        result.save(update_fields=["status", "error_message", "raw_output"])
        return

    Finding.objects.bulk_create(
        [
            Finding(
                result=result,
                severity=f.severity,
                category=f.category,
                confidence=f.confidence,
                title=f.title[:500],
                description=f.description,
                suggestion=f.suggestion,
                file_path=_normalize_path(f.file_path)[:500],
                line_number=f.line_number,
                column_number=f.column_number,
                code_snippet=f.code_snippet,
                rule_id=f.rule_id[:100],
                tool_specific_data=f.tool_specific_data,
            )
            for f in output.findings
        ],
    )
    result.status = ToolResult.Status.COMPLETED
    result.raw_output = output.raw_output
    result.metrics = output.metrics
    result.summary = {
        **output.summary,
        "severity_counts": build_severity_counts(output.findings),
    }
    result.save(update_fields=["status", "raw_output", "metrics", "summary"])


def _finalize(run: AnalysisRun, statuses: list[str], truncated_inputs: list[str] | None = None) -> None:
    findings = Finding.objects.filter(result__run=run)
    severity_counts: dict[str, int] = {}
    for sev in findings.values_list("severity", flat=True):
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    completed = sum(1 for s in statuses if s == ToolResult.Status.COMPLETED)
    failed = sum(1 for s in statuses if s == ToolResult.Status.FAILED)

    if statuses and completed == 0 and failed:
        status = AnalysisRun.Status.FAILED
    elif failed:
        status = AnalysisRun.Status.PARTIAL
    else:
        status = AnalysisRun.Status.COMPLETED

    metrics_by_tool = {slug: metrics for slug, metrics in run.results.values_list("tool_slug", "metrics") if metrics}

    run.status = status
    run.summary = {
        "total_findings": findings.count(),
        "severity_counts": severity_counts,
        "metrics_by_tool": metrics_by_tool,
        "tools_run": len(statuses),
        "tools_completed": completed,
        "tools_failed": failed,
    }
    if truncated_inputs:
        # The generated code was cut off at max_tokens; findings describe an
        # incomplete app, so surface that next to them.
        run.summary["truncated_inputs"] = truncated_inputs
    run.completed_at = timezone.now()
    if run.started_at:
        run.duration_seconds = (run.completed_at - run.started_at).total_seconds()
    run.save(update_fields=["status", "summary", "completed_at", "duration_seconds"])
    _publish(run)


def _materialize(code: dict[str, str]) -> dict[str, str]:
    """Turn a semantic/file-keyed code map into ``{filename: content}``."""
    files: dict[str, str] = {}
    for key, content in code.items():
        if not content or not str(content).strip():
            continue
        if "." in Path(key).name:
            filename = key
        else:
            filename = f"{key}{_EXT_BY_KEY.get(key.lower(), '.py')}"
        files[filename.lstrip("/")] = content
    return files
