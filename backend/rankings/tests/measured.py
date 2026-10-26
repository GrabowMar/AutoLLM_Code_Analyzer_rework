"""Test helpers: analysis runs whose stored measurement follows contract §4.4.

The measurement and sample engines belong to other stages. Consumer tests
fake them at their public boundary (``measurement.outcomes``,
``sample.check_eligibility``, ``sample.target_meta``, ``sample.resolve_job``)
so these suites are deterministic whatever state those modules are in; the
fakes implement the documented contract, not any one implementation.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from backend.analysis.engine.types import Sample
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import SampleUnavailable
from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.users.tests.factories import UserFactory

SEVERITIES = ("critical", "high", "medium", "low", "info")
PLAN_A = "a" * 64
PLAN_B = "b" * 64
MIN_SLOC_FOR_DENSITY = 50
_LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "uv.lock"}


def sev(**counts: int) -> dict[str, int]:
    """Zero-filled severity dict: ``sev(high=2)``."""
    return {s: int(counts.get(s, 0)) for s in SEVERITIES}


def measurement_json(
    *,
    plan_hash: str = PLAN_A,
    findings: dict[str, int] | None = None,
    issues: dict[str, int] | None = None,
    measured_sloc: int = 1000,
    total_sloc: int | None = None,
    static_available: bool = True,
    complete: bool = True,
    ai: dict[str, int] | None = None,
    ai_available: bool = True,
    ai_cost: float = 0.0,
    functional_pass_rate: float | None = None,
    functional_status: str | None = None,
    complexity_max: float | None = None,
    duplication_pct: float | None = None,
    security_issues: int = 0,
    has_high_security: bool = False,
    suppression_markers: int = 0,
    tool_status: dict[str, str] | None = None,
    by_tool: dict[str, int] | None = None,
    by_category: dict[str, int] | None = None,
) -> dict[str, Any]:
    """A measurement JSON shaped exactly like contract §4.4 (schema_version 1)."""
    findings = sev(**(findings or {}))
    issues = sev(**(issues if issues is not None else findings))
    total_findings = sum(findings.values())
    total_issues = sum(issues.values())
    high_plus = issues["critical"] + issues["high"]

    def per_kloc(n: float) -> float | None:
        if not static_available or measured_sloc < MIN_SLOC_FOR_DENSITY:
            return None
        return round(n / measured_sloc * 1000, 4)

    ai_block = None
    if ai is not None:
        consensus = sev(**ai)
        ai_block = {
            "available": ai_available,
            "status": "succeeded" if ai_available else "failed",
            "samples_ok": 3 if ai_available else 0,
            "samples": 3,
            "threshold": 2,
            "consensus": {"total": sum(consensus.values()), "by_severity": consensus, "by_category": {}},
            "per_kloc": per_kloc(sum(consensus.values())) if ai_available else None,
            "grounding": {"exact": 1, "relocated": 0, "ungrounded": 0, "invalid_file": 0, "out_of_range": 0},
            "grounding_rate": 1.0,
            "agreement": {"mean_pairwise_jaccard": 0.5},
            "overlap_with_static": {},
            "cost_usd": ai_cost,
            "tokens": {},
        }
    return {
        "schema_version": 1,
        "version": "measure-v1",
        "plan_hash": plan_hash,
        "sample_hash": "s" * 64,
        "complete": complete,
        "required_failed": [] if complete else ["bandit"],
        "tool_status": tool_status or {"bandit": "succeeded"},
        "sloc": {
            "measured": measured_sloc,
            "total": total_sloc if total_sloc is not None else measured_sloc,
            "by_language": {},
            "by_origin": {},
        },
        "files": {"measured": 3, "included": 4, "excluded": 1},
        "static": {
            "available": static_available,
            "findings": {
                "total": total_findings,
                "by_severity": findings,
                "by_category": by_category or {},
                "by_tool": by_tool or {},
            },
            "issues": {"total": total_issues, "by_severity": issues, "by_category": by_category or {}},
            "weighted": float(total_issues),
            "findings_per_kloc": per_kloc(total_findings),
            "issues_per_kloc": per_kloc(total_issues),
            "security_issues": security_issues,
            "high_plus_issues": high_plus,
            "has_high_security": has_high_security,
            "category_available": {},
            "style_findings": 0,
            "out_of_scope_findings": 0,
            "scaffold_findings": 0,
        },
        "ai": ai_block,
        "metrics": {
            "complexity": {"max": complexity_max, "mean": complexity_max, "p90": None, "rank_distribution": {}}
            if complexity_max is not None
            else None,
            "duplication": {"percentage": duplication_pct, "clones": 0, "duplicated_lines": 0}
            if duplication_pct is not None
            else None,
        },
        "coverage": {},
        "parse_coverage": {"unparseable_files": [], "share_unparseable_sloc": 0.0},
        "suppression_markers": suppression_markers,
        "functional": {
            "status": functional_status or ("unknown" if functional_pass_rate is None else "passed"),
            "pass_rate": functional_pass_rate,
            "source": "conformance",
            "evaluator_version": 1,
        },
        "flags": {"truncated": False, "empty_output": False, "validity": "valid", "excluded_reasons": []},
    }


def fake_outcomes(measurement: dict[str, Any]) -> dict[str, Any]:
    """``measurement.outcomes`` per contract §4.4: flat values, None when unavailable."""
    static = measurement.get("static") or {}
    available = bool(static.get("available"))
    sloc = (measurement.get("sloc") or {}).get("measured") or 0
    dense = available and sloc >= MIN_SLOC_FOR_DENSITY

    def per_kloc(n: Any) -> float | None:
        return round(float(n) / sloc * 1000, 4) if dense and n is not None else None

    ai = measurement.get("ai") or {}
    metrics = measurement.get("metrics") or {}
    complexity = metrics.get("complexity") or {}
    duplication = metrics.get("duplication") or {}
    return {
        "static_findings_per_kloc": per_kloc((static.get("findings") or {}).get("total")),
        "static_issues_per_kloc": per_kloc((static.get("issues") or {}).get("total")),
        "security_issues_per_kloc": per_kloc(static.get("security_issues")),
        "high_plus_issues_per_kloc": per_kloc(static.get("high_plus_issues")),
        "has_high_security": static.get("has_high_security") if available else None,
        "ai_consensus_per_kloc": ai.get("per_kloc") if ai.get("available") else None,
        "complexity_max": complexity.get("max"),
        "complexity_mean": complexity.get("mean"),
        "duplication_pct": duplication.get("percentage"),
        "functional_pass_rate": (measurement.get("functional") or {}).get("pass_rate"),
        "measured_sloc": sloc if sloc else None,
        "suppression_markers_per_kloc": per_kloc(measurement.get("suppression_markers")),
    }


def fake_check_eligibility(
    meta: dict[str, Any],
    sample: Any,
    rules: dict[str, Any] | None = None,
    *,
    manifest: dict[str, Any] | None = None,
) -> dict:
    """``sample.check_eligibility`` for the rules consumers use (meta + measured SLOC)."""
    rules = rules or {}
    reasons = []
    measured = ((manifest or {}).get("totals") or {}).get("measured_sloc")
    if rules.get("min_measured_sloc") and measured is not None and measured < rules["min_measured_sloc"]:
        reasons.append({"code": "below_min_sloc", "severity": "exclude", "detail": "too small"})
    validity = meta.get("validity") or {}
    if rules.get("exclude_invalid") and isinstance(validity, dict) and validity.get("exclude"):
        reasons.append({"code": "invalid", "severity": "exclude", "detail": "agent validity says exclude"})
    if rules.get("exclude_empty_output") and meta.get("empty_output"):
        reasons.append({"code": "empty_output", "severity": "exclude", "detail": "nothing written by the model"})
    if rules.get("exclude_truncated") and meta.get("truncated"):
        reasons.append({"code": "truncated", "severity": "exclude", "detail": "generation output cut off"})
    return {"eligible": not reasons, "reasons": reasons}


def fake_target_meta(job) -> dict[str, Any]:
    """``sample.target_meta`` reduced to the keys consumers read."""
    data = job.result_data if isinstance(job.result_data, dict) else {}
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    functional = metrics.get("functional") if isinstance(metrics.get("functional"), dict) else {}
    truncated = any(data.get(f"{part}_truncated") for part in ("backend", "frontend"))
    return {
        "job_id": str(job.id),
        "mode": job.mode,
        "status": job.status,
        "model_id": job.model.model_id if job.model_id else "",
        "experiment_id": str(job.experiment_id) if job.experiment_id else None,
        "condition_id": job.condition_id,
        "app_slug": job.app_requirement.slug if job.app_requirement_id else "",
        "repeat_index": job.repeat_index,
        "prompt_hash": job.prompt_hash,
        "bundle_key": job.bundle_key,
        "truncated": truncated,
        "truncation_reasons": ["output_truncated"] if truncated else [],
        "empty_output": bool(data.get("empty_output")),
        "validity": {"valid": True, "exclude": bool(data.get("invalid")), "notes": []},
        "functional": {
            "status": functional.get("status", "unknown"),
            "pass_rate": functional.get("pass_rate"),
            "source": functional.get("source", ""),
            "evaluator_version": functional.get("evaluator_version"),
        },
    }


def fake_resolve_job(job, scope: dict[str, Any] | None = None) -> Sample:
    """``sample.resolve_job`` for flat ``files`` / legacy results: SLOC = non-blank lines, lockfiles excluded."""
    data = job.result_data if isinstance(job.result_data, dict) else {}
    if job.status != "completed":
        msg = "job not completed"
        raise SampleUnavailable(msg, code="job_not_completed")
    files = dict(data.get("files") or {})
    if not files:
        if data.get("backend_code"):
            files["app.py"] = data["backend_code"]
        if data.get("frontend_code"):
            files["frontend/src/App.jsx"] = data["frontend_code"]
    if not files:
        msg = "no code"
        raise SampleUnavailable(msg, code="no_code")
    out = []
    for path, text in sorted(files.items()):
        content = str(text).encode()
        lockfile = path.rsplit("/", 1)[-1] in _LOCKFILES
        sloc = 0 if lockfile else sum(1 for line in str(text).splitlines() if line.strip())
        out.append(
            SampleFile(
                path=path,
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
                language="json" if lockfile else "python",
                kind="lockfile" if lockfile else "source",
                origin="authored",
                included=not lockfile,
                exclude_reason="lockfile" if lockfile else "",
                sloc=sloc,
                measured=not lockfile,
                measured_sloc=sloc,
            ),
        )
    return Sample(files=out, source="custom", sample_hash="h" * 64)


@pytest.fixture
def fake_engine(monkeypatch):
    """Patch the other stages' engine functions consumers call with contract fakes."""
    from backend.analysis.engine import measurement
    from backend.analysis.engine import sample

    monkeypatch.setattr(measurement, "outcomes", fake_outcomes)
    monkeypatch.setattr(sample, "check_eligibility", fake_check_eligibility)
    monkeypatch.setattr(sample, "target_meta", fake_target_meta)
    monkeypatch.setattr(sample, "resolve_job", fake_resolve_job)


def summary_json(measurement: dict[str, Any], *, tools: dict[str, str] | None = None) -> dict[str, Any]:
    """``AnalysisRun.summary`` (contract §4.5) derived from a measurement."""
    tools = tools or {"bandit": "succeeded"}
    counts = dict.fromkeys(("succeeded", "failed", "timed_out", "not_applicable", "skipped", "cancelled"), 0)
    counts |= {"pending": 0, "running": 0}
    for status in tools.values():
        counts[status] = counts.get(status, 0) + 1
    static = measurement.get("static") or {}
    ai = measurement.get("ai") or {}
    return {
        "schema_version": 2,
        "tools": {"total": len(tools), **counts},
        "complete": measurement.get("complete", True),
        "total_findings": (static.get("findings") or {}).get("total", 0),
        "severity_counts": sev(**((static.get("findings") or {}).get("by_severity") or {})),
        "ai_findings": (ai.get("consensus") or {}).get("total", 0),
        "ai_severity_counts": sev(**((ai.get("consensus") or {}).get("by_severity") or {})),
        "issues": (static.get("issues") or {}).get("total", 0),
        "sloc": {"measured": measurement["sloc"]["measured"], "total": measurement["sloc"]["total"]},
        "cost_usd": ai.get("cost_usd", 0.0),
        "warnings": [],
        "tools_run": len(tools),
        "tools_completed": counts["succeeded"] + counts["not_applicable"],
        "tools_failed": counts["failed"] + counts["timed_out"],
    }


def make_run(
    job=None,
    *,
    user=None,
    plan_hash: str = PLAN_A,
    status: str = AnalysisRun.Status.COMPLETED,
    is_complete: bool | None = None,
    measurement: dict[str, Any] | None = None,
    target_meta: dict[str, Any] | None = None,
    tools: dict[str, str] | None = None,
    purpose: str = AnalysisRun.Purpose.ADHOC,
    **measure: Any,
) -> AnalysisRun:
    """An analysis run of *job* (or inline) with a §4.4 measurement and §4.5 summary.

    ``tools`` maps tool slug -> ToolResult status (one result row each).
    """
    if measurement is None and status in AnalysisRun.FINISHED_STATUSES:
        measure.setdefault("complete", is_complete if is_complete is not None else True)
        measurement = measurement_json(plan_hash=plan_hash, **measure)
    complete = is_complete if is_complete is not None else bool((measurement or {}).get("complete", False))
    meta = target_meta if target_meta is not None else (fake_target_meta(job) if job is not None else {})
    tools = tools or {"bandit": ToolResult.Status.SUCCEEDED}
    run = AnalysisRunFactory(
        created_by=user or (job.created_by if job is not None else None) or UserFactory(),
        generation_job=job,
        source_code=None if job is not None else {"app.py": "print('hi')"},
        plan_hash=plan_hash,
        plan={"schema_version": 1, "tools": [{"slug": s, "required": True} for s in tools]},
        status=status,
        is_complete=complete,
        purpose=purpose,
        measurement=measurement or {},
        summary=summary_json(measurement, tools=tools) if measurement else {},
        target_meta=meta,
    )
    for position, (slug, tool_status) in enumerate(tools.items()):
        ToolResultFactory(
            run=run,
            tool_slug=slug,
            position=position,
            status=tool_status,
            tool_kind="ai" if slug == "ai_review" else "static",
            category="ai" if slug == "ai_review" else "security",
            tool_version="1.2.3",
        )
    return run
