"""Synthetic studies for the report/export tests (owner: reports stage).

``measurement()`` builds an ``AnalysisRun.measurement`` dict shaped per
contract §4.4 and ``fake_outcomes()`` is a local stand-in for
``engine.measurement.outcomes`` (the measure stage's function), reading
that shape: tests patch it in at the module boundary so the report code
is exercised end to end without depending on another stage's progress.
"""

from __future__ import annotations

import hashlib
from typing import Any

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalysisStudy
from backend.analysis.research import report as report_mod
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalysisStudyFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ReviewSampleFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.tests.factories import GenerationJobFactory

PLAN_HASH = "a" * 64
IMAGE_TAG = "llmlab/analyzer:abc123def456"
PLAN: dict[str, Any] = {
    "schema_version": 1,
    "profile": {"id": None, "slug": "research-default", "version": 1, "content_hash": "f" * 64},
    "tools": [
        {
            "slug": "bandit",
            "name": "Bandit",
            "kind": "static",
            "category": "security",
            "required": True,
            "config": {},
            "config_hash": "c1" * 32,
            "spec_hash": "s1" * 32,
            "version": "1.9.4",
            "languages": ["python"],
        },
        {
            "slug": "semgrep",
            "name": "Semgrep",
            "kind": "static",
            "category": "security",
            "required": True,
            "config": {},
            "config_hash": "c2" * 32,
            "spec_hash": "s2" * 32,
            "version": "1.180.0",
            "languages": ["python", "javascript"],
        },
    ],
    "reviewer": {
        "enabled": True,
        "required": False,
        "tool_slug": "ai_review",
        "engine": "single_shot",
        "harness": None,
        "model": {
            "slug": "deepseek/deepseek-v4.1-flash",
            "canonical_slug": "deepseek/deepseek-v4.1-flash-1017",
            "pricing": {"input_per_token": 3e-07, "output_per_token": 1.2e-06, "snapshot_at": "2026-10-22"},
        },
        "params": {"temperature": 0.2, "max_tokens": 16000, "reasoning": None, "seed_base": 7},
        "samples": 3,
        "prompt_version": "review-v1",
        "prompt_sha256": "p" * 64,
        "schema_version": "findings-v1",
        "schema_sha256": "q" * 64,
        "budget": {"max_cost_usd": 0.5},
    },
    "scope": {"measured_origins": ["authored", "added", "modified"]},
    "outcomes": {"min_sloc_for_density": 50},
    "analyzer_image": {"tag": IMAGE_TAG},
    "engine_versions": {"sample_resolver": "sample-v1", "measurement": "measure-v1"},
    "mapping_hashes": {"severity": "m" * 64},
}


def measurement(
    *,
    sloc: int | None = 1000,
    issues: int = 5,
    findings: int | None = None,
    security: int = 1,
    high_plus: int = 1,
    has_high: bool = True,
    static_ok: bool = True,
    ai_per_kloc: float | None = 2.0,
    ai_available: bool = True,
    jaccard: float | None = 0.5,
    grounding_rate: float | None = 0.9,
    complexity: float | None = 4.0,
    duplication: float | None = 2.0,
    pass_rate: float | None = 0.8,
    suppression: int = 0,
) -> dict[str, Any]:
    """A §4.4 measurement; ``static_ok=False`` models a failed required static tool."""
    findings = issues + 2 if findings is None else findings
    per_kloc = (lambda n: n * 1000 / sloc) if (static_ok and sloc) else (lambda n: None)
    return {
        "schema_version": 1,
        "version": "measure-v1",
        "plan_hash": PLAN_HASH,
        "sample_hash": "h" * 64,
        "complete": static_ok,
        "required_failed": [] if static_ok else ["semgrep"],
        "tool_status": {"bandit": "succeeded", "semgrep": "succeeded" if static_ok else "failed"},
        "sloc": {
            "measured": sloc,
            "total": (sloc or 0) + 200,
            "by_language": {"python": int((sloc or 0) * 0.6), "javascript": int((sloc or 0) * 0.4)},
            "by_origin": {"added": sloc},
        },
        "files": {"measured": 10, "included": 12, "excluded": 3},
        "static": {
            "available": static_ok,
            "findings": {"total": findings, "by_severity": {}, "by_category": {}, "by_tool": {}},
            "issues": {
                "total": issues,
                "by_severity": {"critical": 0, "high": high_plus, "medium": issues - high_plus, "low": 0, "info": 0},
                "by_category": {"security": security},
            },
            "weighted": issues * 2.0,
            "findings_per_kloc": per_kloc(findings),
            "issues_per_kloc": per_kloc(issues),
            "security_issues": security,
            "high_plus_issues": high_plus,
            "has_high_security": has_high,
            "category_available": {"security": static_ok},
            "style_findings": 0,
            "out_of_scope_findings": 0,
            "scaffold_findings": 0,
        },
        "ai": {
            "available": ai_available,
            "status": "succeeded" if ai_available else "failed",
            "samples_ok": 3 if ai_available else 0,
            "samples": 3,
            "threshold": 2,
            "consensus": {"total": 2, "by_severity": {}, "by_category": {}},
            "per_kloc": ai_per_kloc if ai_available else None,
            "grounding": {"exact": 4, "relocated": 1, "ungrounded": 1, "invalid_file": 0, "out_of_range": 0},
            "grounding_rate": grounding_rate,
            "agreement": {"mean_pairwise_jaccard": jaccard},
            "overlap_with_static": {"shared": 1, "ai_only": 1, "static_only": 3, "jaccard": 0.2},
            "cost_usd": 0.01,
            "tokens": {"prompt": 1000, "completion": 300},
        },
        "metrics": {
            "complexity": None
            if complexity is None
            else {"max": complexity * 3, "mean": complexity, "p90": 6, "rank_distribution": {}},
            "duplication": None
            if duplication is None
            else {"percentage": duplication, "clones": 1, "duplicated_lines": 10},
        },
        "coverage": {
            "bandit": {"status": "succeeded", "files_in_scope": 4, "files_scanned": 4, "unparseable": 0},
            "semgrep": {
                "status": "succeeded" if static_ok else "failed",
                "files_in_scope": 10,
                "files_scanned": 9,
                "unparseable": 1,
            },
        },
        "parse_coverage": {"unparseable_files": ["frontend/src/Broken.jsx"], "share_unparseable_sloc": 0.01},
        "suppression_markers": suppression,
        "functional": {"status": "passed", "pass_rate": pass_rate, "source": "conformance", "evaluator_version": 1},
        "flags": {"truncated": False, "empty_output": False, "validity": "valid", "excluded_reasons": []},
    }


def fake_outcomes(m: dict[str, Any]) -> dict[str, Any]:
    """Local stand-in for ``measurement.outcomes``: §4.4 measurement -> flat outcomes, None when NA."""
    sloc = m["sloc"]["measured"]
    static = m["static"]
    ok = static["available"]
    dense = bool(ok and sloc and sloc >= 50)

    def kloc(n):
        return n * 1000.0 / sloc if dense and n is not None else None

    ai = m.get("ai") or {}
    complexity = (m.get("metrics") or {}).get("complexity") or {}
    duplication = (m.get("metrics") or {}).get("duplication") or {}
    return {
        "static_findings_per_kloc": kloc(static["findings"]["total"]),
        "static_issues_per_kloc": kloc(static["issues"]["total"]),
        "security_issues_per_kloc": kloc(static["security_issues"]),
        "high_plus_issues_per_kloc": kloc(static["high_plus_issues"]),
        "has_high_security": static["has_high_security"] if ok else None,
        "ai_consensus_per_kloc": ai.get("per_kloc") if ai.get("available") else None,
        "complexity_max": complexity.get("max"),
        "complexity_mean": complexity.get("mean"),
        "duplication_pct": duplication.get("percentage"),
        "functional_pass_rate": (m.get("functional") or {}).get("pass_rate"),
        "measured_sloc": sloc,
        "suppression_markers_per_kloc": kloc(m.get("suppression_markers")),
    }


def target_meta(job, model: str, app: str, rep: int, **extra: Any) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "mode": "agent",
        "status": "completed",
        "model_slug": model,
        "model_name": model.upper(),
        "harness": "opencode",
        "harness_version": "1.18.35",
        "stack_slug": "flask-react",
        "app_slug": app,
        "repeat_index": rep,
        "validity": {"valid": True, "exclude": False, "notes": []},
        "truncated": False,
        "empty_output": False,
        "functional": {"status": "passed", "pass_rate": 0.8, "source": "conformance", "evaluator_version": 1},
        "generation": {"cost": 0.25, "total_tokens": 12000, "duration_seconds": 300},
        **extra,
    }


def add_results(
    run: AnalysisRun,
    *,
    semgrep_status: str = "succeeded",
    ai_samples: list[dict[str, Any]] | None = None,
    image: str = IMAGE_TAG,
    bandit_version: str = "1.9.4",
) -> dict[str, Any]:
    """Tool results (bandit, semgrep, ai_review) + review samples for *run*."""
    bandit = ToolResultFactory(
        run=run,
        tool_slug="bandit",
        category="security",
        position=0,
        status="succeeded",
        tool_version=bandit_version,
        image_ref=f"{image}@sha256:{'0' * 8}",
        config_hash="c1" * 32,
        duration_seconds=2.0,
        coverage={"files_scanned": 4},
    )
    semgrep = ToolResultFactory(
        run=run,
        tool_slug="semgrep",
        category="security",
        position=1,
        status=semgrep_status,
        failure_reason="" if semgrep_status == "succeeded" else "parse_error",
        tool_version="1.180.0",
        image_ref=f"{image}@sha256:{'0' * 8}",
        config_hash="c2" * 32,
        duration_seconds=5.0,
    )
    ai = ToolResultFactory(
        run=run,
        tool_slug="ai_review",
        tool_kind="ai",
        category="ai",
        position=2,
        required=False,
        status="succeeded",
        duration_seconds=30.0,
    )
    samples = []
    for i, spec in enumerate(ai_samples if ai_samples is not None else [{}, {}, {}]):
        samples.append(
            ReviewSampleFactory(
                result=ai,
                index=i,
                status=spec.get("status", "succeeded"),
                model_slug="deepseek/deepseek-v4.1-flash",
                model_canonical_slug="deepseek/deepseek-v4.1-flash-1017",
                prompt_version="review-v1",
                prompt_sha256="p" * 64,
                schema_sha256="q" * 64,
                usage={"prompt_tokens": 1000, "completion_tokens": 300, "reasoning_tokens": 50, "cost_usd": 0.004},
                providers={"DeepInfra": 1},
                finish_reasons={"stop": 1},
                grounding={"exact": 2, "relocated": 1, "ungrounded": 1, "invalid_file": 0, "out_of_range": 0},
                repaired=spec.get("repaired", False),
            ),
        )
    return {"bandit": bandit, "semgrep": semgrep, "ai": ai, "samples": samples}


def static_finding(result, *, issue_key: str, line: int = 10, cwe: list[str] | None = None, **kwargs: Any):
    defaults = {
        "severity": "high",
        "category": "security",
        "rule_id": "B608" if result.tool_slug == "bandit" else "sql-injection",
        "file_path": "app.py",
        "line_number": line,
        "counted": True,
        "issue_key": issue_key,
        "cwe": cwe if cwe is not None else ["CWE-89"],
        "file_origin": "added",
    }
    return FindingFactory(result=result, **{**defaults, **kwargs})


def ai_finding(sample, *, file_path: str = "app.py", category: str = "security", **kwargs: Any):
    return FindingFactory(
        result=sample.result,
        review_sample=sample,
        tool_slug="ai_review",
        is_ai=True,
        counted=kwargs.pop("counted", False),
        grounding=kwargs.pop("grounding", "exact"),
        file_path=file_path,
        category=category,
        **kwargs,
    )


def make_study(
    user,
    *,
    models: tuple[str, ...] = ("m-a", "m-b", "m-c"),
    apps: tuple[str, ...] = ("app-1", "app-2", "app-3", "app-4"),
    reps: int = 1,
    skip: set[tuple[str, str]] | frozenset = frozenset(),
    planned_skip: set[tuple[str, str]] | frozenset = frozenset(),
    issues=lambda model, app, rep: 3 + 2 * ("m-a", "m-b", "m-c").index(model) + int(app[-1]) % 2 + rep,
    design: dict[str, Any] | None = None,
    lock: bool = True,
    with_findings: bool = True,
) -> tuple[AnalysisStudy, dict[tuple[str, str, int], AnalysisRun]]:
    """A study over models x apps x reps.

    *planned_skip* cells are absent from the protocol (an incomplete design);
    *skip* cells are planned but get no run (attrition).
    """
    jobs = {}
    targets = []
    for model in models:
        for app in apps:
            if (model, app) in planned_skip:
                continue
            for rep in range(reps):
                job = GenerationJobFactory(created_by=user)
                meta = target_meta(job, model, app, rep)
                jobs[(model, app, rep)] = (job, meta)
                targets.append(
                    {
                        "job_id": str(job.id),
                        "label": f"{model}/{app}#{rep}",
                        "meta": meta,
                        "eligible": True,
                        "reasons": [],
                        "files": 12,
                        "sloc_total": 1200,
                        "sloc_measured": 1000,
                        "languages": {"python": 600, "javascript": 400},
                        "sample_hash": "h" * 64,
                        "tools": {"bandit": "run", "semgrep": "run"},
                    },
                )
    study = AnalysisStudyFactory(
        created_by=user,
        name="Synthetic study",
        description="Three models, four apps.",
        status=AnalysisStudy.Status.COMPLETED,
        plan=PLAN,
        plan_hash=PLAN_HASH,
        target_spec={"kind": "jobs", "job_ids": [t["job_id"] for t in targets]},
        research_questions=[
            {"id": "RQ1", "text": "Does issue density differ between models?", "kind": "confirmatory"},
            {"id": "RQ2", "text": "How reliable is the AI reviewer?", "kind": "exploratory"},
        ],
        analysis_design={
            "group_by": "model",
            "block_by": "app",
            "bootstrap_resamples": 200,
            "effect_ci_resamples": 100,
            **(design or {}),
        },
        preflight={
            "generated_at": "2026-10-22T10:00:00+00:00",
            "plan_hash": PLAN_HASH,
            "blocking": [],
            "warnings": [],
            "info": [],
            "targets": targets,
            "tools": [
                {"slug": "bandit", "status": "ready", "canary": {"passed": True, "missing": []}},
                {"slug": "semgrep", "status": "ready", "canary": {"passed": True, "missing": []}},
            ],
            "estimates": {"targets": len(targets), "eligible": len(targets), "ai_cost_usd": 0.12},
            "reviewer": {"cost_estimate": {"low": 0.05, "expected": 0.12, "high": 0.2}},
            "comparability": {"plan_hash": PLAN_HASH, "mixed": {}, "notes": []},
        },
    )
    if lock:
        study.protocol = report_mod.build_protocol(study)
        study.protocol_hash = report_mod.protocol_hash(study.protocol)
        study.save()
    runs = {}
    for (model, app, rep), (job, meta) in jobs.items():
        if (model, app) in skip:
            continue
        n_issues = issues(model, app, rep)
        run = AnalysisRunFactory(
            created_by=user,
            study=study,
            generation_job=job,
            purpose=AnalysisRun.Purpose.RESEARCH,
            plan=PLAN,
            plan_hash=PLAN_HASH,
            target_meta=meta,
            sample_hash="h" * 64,
            status=AnalysisRun.Status.COMPLETED,
            is_complete=True,
            measurement=measurement(issues=n_issues, high_plus=min(1, n_issues), security=n_issues // 2),
            duration_seconds=60.0,
        )
        results = add_results(run)
        if with_findings:
            static_finding(results["bandit"], issue_key=f"iss-{run.id}-1")
            static_finding(results["semgrep"], issue_key=f"iss-{run.id}-1")
            static_finding(results["semgrep"], issue_key=f"iss-{run.id}-2", line=40, cwe=["CWE-79"], severity="medium")
            for sample in results["samples"]:
                ai_finding(sample, file_path="app.py", category="security")
            ai_finding(results["samples"][0], file_path="frontend/src/App.jsx", category="bug")
        runs[(model, app, rep)] = run
    return study, runs


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
