"""Payload builders for the analysis API (plain dicts matching ``api/schema.py``)."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from django.conf import settings
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch
from django.utils import timezone

from backend.analysis.engine import preflight as preflight_mod
from backend.analysis.models import AnalysisRun
from backend.analysis.models import ReviewSample
from backend.analysis.models import ToolResult

logger = logging.getLogger(__name__)

# target_meta keys surfaced on run list items (the full snapshot is on the detail).
TARGET_KEYS = (
    "model_slug",
    "model_name",
    "app_slug",
    "app_name",
    "harness",
    "mode",
    "stack_slug",
    "experiment_id",
    "condition_id",
    "condition_label",
    "batch_id",
    "repeat_index",
)


def iso(value) -> str | None:
    return value.isoformat() if value else None


def _str_or_none(value) -> str | None:
    return str(value) if value else None


def _stale(run: AnalysisRun) -> bool:
    if run.status != AnalysisRun.Status.RUNNING or run.heartbeat_at is None:
        return False
    stale_after = int(getattr(settings, "ANALYSIS_STALE_SECONDS", 180))
    return (timezone.now() - run.heartbeat_at).total_seconds() > stale_after


def profile_ref(profile) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {"id": str(profile.id), "slug": profile.slug, "version": profile.version, "name": profile.name}


def tool_counts(results) -> dict[str, int]:
    counter = Counter(r.status for r in results)
    out = {status: counter.get(status, 0) for status in ToolResult.Status.values}
    out["total"] = sum(counter.values())
    return out


def target_summary(run: AnalysisRun) -> dict[str, Any]:
    meta = run.target_meta or {}
    out = {key: meta.get(key) for key in TARGET_KEYS}
    out["label"] = preflight_mod.target_label(meta) if run.generation_job_id or meta.get("job_id") else "inline source"
    return out


# Tool states that leave a gap in the run's data (a not-applicable tool is no problem: nothing to scan).
PROBLEM_STATUSES = (
    ToolResult.Status.FAILED,
    ToolResult.Status.TIMED_OUT,
    ToolResult.Status.CANCELLED,
    ToolResult.Status.SKIPPED,
)


def run_problems(results) -> list[dict[str, Any]]:
    """Tool executions that did not produce data, in plan order (from already loaded results)."""
    return [
        {
            "tool_slug": r.tool_slug,
            "status": r.status,
            "failure_reason": r.failure_reason,
            "required": r.required,
            "message": (r.error_message or "")[:300],
        }
        for r in results
        if r.status in PROBLEM_STATUSES
    ]


def run_list_item(run: AnalysisRun) -> dict[str, Any]:
    # ``results`` is prefetched by the views: counts reflect live tool states,
    # not only the summary written at finalize. Results are created in plan
    # order (AI last), so they also give the tool list without loading the plan.
    results = list(run.results.all())
    meta = run.target_meta or {}
    summary = dict(run.summary or {})
    if not isinstance(summary.get("problems"), list):
        summary["problems"] = run_problems(results)
    return {
        "id": str(run.id),
        "name": run.name,
        "purpose": run.purpose,
        "status": run.status,
        "phase": run.phase,
        "is_complete": run.is_complete,
        "cancel_requested": run.cancel_requested,
        "attempt": run.attempt,
        "source": "job" if run.generation_job_id or meta.get("job_id") else "inline",
        "generation_job_id": _str_or_none(run.generation_job_id),
        "study_id": _str_or_none(run.study_id),
        "profile": profile_ref(run.profile),
        "plan_hash": run.plan_hash,
        "sample_hash": run.sample_hash,
        "target": target_summary(run),
        "tool_slugs": [r.tool_slug for r in results] or run.tool_slugs,
        "tools": tool_counts(results),
        # ``problems`` is passed through when the executor stored it, else derived from the
        # prefetched results (no extra query), so the list can flag gaps without the detail.
        "summary": summary,
        "error_message": run.error_message,
        "stale": _stale(run),
        "heartbeat_at": iso(run.heartbeat_at),
        "created_by_id": run.created_by_id,
        "created_at": iso(run.created_at),
        "started_at": iso(run.started_at),
        "completed_at": iso(run.completed_at),
        "duration_seconds": run.duration_seconds,
    }


def artifact_url(run_id, owner_id, name: str) -> str:
    try:
        return reverse(
            "api:analysis_artifact",
            kwargs={"run_id": run_id, "result_id": owner_id, "name": name},
        )
    except NoReverseMatch:
        return f"/api/analysis/runs/{run_id}/artifacts/{owner_id}/{name}"


def artifact_list(run_id, owner_id, artifacts: dict[str, Any] | None) -> list[dict[str, Any]]:
    out = []
    for name, raw in sorted((artifacts or {}).items()):
        entry = raw if isinstance(raw, dict) else {}
        out.append(
            {
                "name": name,
                "bytes": entry.get("bytes"),
                "sha256": entry.get("sha256") or "",
                "compressed": bool(entry.get("compressed")),
                "url": artifact_url(run_id, owner_id, name),
            },
        )
    return out


def tool_result_payload(result: ToolResult, finding_counts: dict[str, int] | None = None) -> dict[str, Any]:
    counts = finding_counts or {}
    return {
        "id": str(result.id),
        "tool_slug": result.tool_slug,
        "tool_kind": result.tool_kind,
        "category": result.category,
        "position": result.position,
        "required": result.required,
        "status": result.status,
        "failure_reason": result.failure_reason,
        "attempt": result.attempt,
        "tool_version": result.tool_version,
        "image_ref": result.image_ref,
        "config": result.config or {},
        "config_hash": result.config_hash,
        "argv": result.argv or [],
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "started_at": iso(result.started_at),
        "finished_at": iso(result.finished_at),
        "coverage": result.coverage or {},
        "metrics": result.metrics or {},
        "summary": result.summary or {},
        "diagnostics": result.diagnostics or {},
        "artifacts": artifact_list(result.run_id, result.id, result.artifacts),
        "error_message": result.error_message,
        "findings": {"total": counts.get("total", 0), "counted": counts.get("counted", 0)},
    }


def review_sample_payload(sample, run_id) -> dict[str, Any]:
    return {
        "id": str(sample.id),
        "result_id": str(sample.result_id),
        "index": sample.index,
        "engine": sample.engine,
        "harness": sample.harness,
        "harness_version": sample.harness_version,
        "model_slug": sample.model_slug,
        "model_canonical_slug": sample.model_canonical_slug,
        "status": sample.status,
        "failure_reason": sample.failure_reason,
        "error_message": sample.error_message,
        "prompt_version": sample.prompt_version,
        "prompt_sha256": sample.prompt_sha256,
        "schema_sha256": sample.schema_sha256,
        "file_order_seed": sample.file_order_seed,
        "files_reviewed": sample.files_reviewed or [],
        "coverage": sample.coverage or {},
        "usage": sample.usage or {},
        "providers": sample.providers or {},
        "finish_reasons": sample.finish_reasons or {},
        "grounding": sample.grounding or {},
        "finding_count": sample.finding_count,
        "repaired": sample.repaired,
        "latency_seconds": sample.latency_seconds,
        "artifacts": artifact_list(run_id, sample.id, sample.artifacts),
        "created_at": iso(sample.created_at),
    }


def manifest_summary(run: AnalysisRun) -> dict[str, Any]:
    manifest = run.sample_manifest or {}
    return {
        "source": manifest.get("source") or ("inline" if run.source_code is not None else ""),
        "resolver_version": manifest.get("resolver_version") or "",
        "sloc_version": manifest.get("sloc_version") or "",
        "totals": manifest.get("totals") or {},
        "warnings": manifest.get("warnings") or [],
        "files": len(manifest.get("files") or []),
    }


def _outcomes(measurement: dict[str, Any]) -> dict[str, Any] | None:
    if not measurement:
        return None
    try:
        from backend.analysis.engine import measurement as measurement_mod

        return measurement_mod.outcomes(measurement)
    except Exception as exc:  # noqa: BLE001 - derived view; the raw measurement is still returned
        logger.warning("outcomes() failed: %s", exc)
        return None


def run_detail(run: AnalysisRun) -> dict[str, Any]:
    from django.db.models import Count
    from django.db.models import Q

    payload = run_list_item(run)
    counts = {
        row["result_id"]: row
        for row in run.findings.order_by()
        .values("result_id")
        .annotate(
            total=Count("id"),
            counted=Count("id", filter=Q(counted=True)),
        )
    }
    results = list(run.results.all())
    samples = ReviewSample.objects.filter(result__run=run).order_by("result__position", "index")
    payload.update(
        {
            "plan": run.plan or {},
            "target_meta": run.target_meta or {},
            "manifest": manifest_summary(run),
            "results": [tool_result_payload(r, counts.get(r.id)) for r in results],
            "review_samples": [review_sample_payload(s, run.id) for s in samples],
            "measurement": run.measurement or {},
            "outcomes": _outcomes(run.measurement or {}),
            "lease_owner": run.lease_owner,
            "updated_at": iso(run.updated_at),
            "canary": None,
        },
    )
    if run.purpose == AnalysisRun.Purpose.CANARY:
        try:
            from backend.analysis.engine import canary

            payload["canary"] = canary.evaluate(run)
        except Exception as exc:  # noqa: BLE001 - evaluation is advisory on the detail page
            logger.warning("canary.evaluate failed for %s: %s", run.id, exc)
    return payload


def finding_payload(finding) -> dict[str, Any]:
    sample = finding.review_sample
    return {
        "id": str(finding.id),
        "result_id": str(finding.result_id),
        "tool_slug": finding.tool_slug,
        "rule_id": finding.rule_id,
        "title": finding.title,
        "description": finding.description,
        "suggestion": finding.suggestion,
        "severity": finding.severity,
        "severity_rank": finding.severity_rank,
        "native_severity": finding.native_severity,
        "category": finding.category,
        "cwe": list(finding.cwe or []),
        "confidence": finding.confidence,
        "file_path": finding.file_path,
        "line_number": finding.line_number,
        "end_line": finding.end_line,
        "column_number": finding.column_number,
        "code_snippet": finding.code_snippet,
        "file_origin": finding.file_origin,
        "in_measured_scope": finding.in_measured_scope,
        "counted": finding.counted,
        "fingerprint": finding.fingerprint,
        "issue_key": finding.issue_key,
        "is_ai": finding.is_ai,
        "review_sample_id": _str_or_none(finding.review_sample_id),
        "review_sample_index": sample.index if sample is not None else None,
        "grounding": finding.grounding,
        "is_consensus": finding.is_consensus,
        "tool_specific_data": finding.tool_specific_data or {},
        "created_at": iso(finding.created_at),
    }


def profile_summary(config: dict[str, Any]) -> dict[str, Any]:
    tools = [t.get("slug") for t in (config or {}).get("tools", []) if isinstance(t, dict) and t.get("slug")]
    reviewer = (config or {}).get("reviewer") or {}
    return {
        "tools": tools,
        "tool_count": len(tools),
        "reviewer": {
            "enabled": bool(reviewer.get("enabled")),
            "engine": reviewer.get("engine") or "",
            "harness": reviewer.get("harness") or "",
            "model": reviewer.get("model") or "",
            "samples": reviewer.get("samples"),
        },
    }


def profile_payload(profile, *, user, latest_version: int | None = None) -> dict[str, Any]:
    return {
        "id": str(profile.id),
        "slug": profile.slug,
        "version": profile.version,
        "latest_version": latest_version or profile.version,
        "name": profile.name,
        "description": profile.description,
        "config": profile.config or {},
        "content_hash": profile.content_hash,
        "is_system": profile.is_system,
        "is_archived": profile.is_archived,
        "is_owner": profile.created_by_id is not None and profile.created_by_id == getattr(user, "id", None),
        "created_by_id": profile.created_by_id,
        "summary": profile_summary(profile.config or {}),
        "created_at": iso(profile.created_at),
        "updated_at": iso(profile.updated_at),
    }


def _launch_summary(state: dict[str, Any], *, pending: bool, submitted: int) -> dict[str, Any] | None:
    """``preflight.launch`` without the run id list; *submitted* is the live run count.

    A launch without a status predates background launches and was complete.
    """
    if not state:
        return None
    return {
        "status": state.get("status") or "submitted",
        "pending": pending,
        "planned": state.get("planned", state.get("eligible_targets")),
        "submitted": submitted,
        "skipped": len(state.get("skipped") or []),
        "attempts": state.get("attempts"),
        "launched_at": state.get("launched_at"),
        "requested_at": state.get("requested_at"),
        "finished_at": state.get("finished_at"),
        "error": state.get("error") or None,
    }


def study_payload(
    study,
    *,
    run_counts: dict[str, int] | None = None,
    complete_runs: int | None = None,
    detail: bool = False,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A study; *report* stands in for ``study.report`` (list rows load only its lifecycle keys)."""
    from backend.analysis.research import study as study_mod

    preflight = study.preflight or {}
    estimates = preflight.get("estimates") or {}
    payload: dict[str, Any] = {
        "id": str(study.id),
        "name": study.name,
        "description": study.description,
        "status": study.status,
        "research_questions": study.research_questions or [],
        "target_spec": study.target_spec or {},
        "profile": profile_ref(study.profile),
        "exclusion_rules": study.exclusion_rules or {},
        "analysis_design": study.analysis_design or {},
        "plan_hash": study.plan_hash,
        "protocol_hash": study.protocol_hash,
        "report_hash": study.report_hash,
        "preflight_summary": {
            "generated_at": preflight.get("generated_at"),
            "plan_hash": preflight.get("plan_hash") or "",
            "blocking": len(preflight.get("blocking") or []),
            "warnings": len(preflight.get("warnings") or []),
            "targets": estimates.get("targets"),
            "eligible": estimates.get("eligible"),
            "ai_cost_usd": estimates.get("ai_cost_usd"),
        }
        if preflight
        else None,
        "run_counts": run_counts or {},
        # Runs with every required tool succeeded / not applicable (None when not counted).
        "complete_runs": complete_runs,
        "launch": _launch_summary(
            study_mod.launch_state(study),
            pending=study_mod.launch_pending(study),
            submitted=sum((run_counts or {}).values()),
        ),
        "report_state": study_mod.report_state(study, report=report),
        "actions": study_mod.allowed_actions(study),
        "created_by_id": study.created_by_id,
        "locked_at": iso(study.locked_at),
        "launched_at": iso(study.launched_at),
        "completed_at": iso(study.completed_at),
        "report_generated_at": iso(study.report_generated_at),
        "created_at": iso(study.created_at),
        "updated_at": iso(study.updated_at),
    }
    if detail:
        payload["preflight"] = preflight
        payload["plan"] = study.plan or {}
        payload["protocol_verified"] = study_mod.verify_protocol(study) if study.protocol else None
    return payload
