"""Study-level tables for reporting: deduplicated issue breakdowns and the healing ablation.

Everything here describes one *scope* of generation jobs (an experiment, a
batch, an explicit id list, or everything a user owns) and counts each
job's latest result per tool only (``ToolResult.latest_ids_per_job``), so
re-analyses never double-count and re-running one tool keeps the others. AI-reviewer findings are opinions, not
measurements, and are left out unless asked for.
"""

from __future__ import annotations

import math
import statistics as pystats
from collections import Counter
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.generation.models import GenerationJob
from backend.statistics.services.dedup import DEFAULT_LINE_TOLERANCE
from backend.statistics.services.dedup import SEVERITY_RANK
from backend.statistics.services.dedup import cluster_findings
from backend.statistics.services.dedup import issue_class
from backend.statistics.services.dedup import tool_overlap

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from django.db.models import QuerySet

SEVERITIES = ("critical", "high", "medium", "low", "info")


def study_jobs(
    user: AbstractBaseUser | None = None,
    *,
    experiment_id: str | None = None,
    batch_id: str | None = None,
    job_ids: list[str] | None = None,
) -> QuerySet[GenerationJob]:
    """The jobs a study table describes (owner-scoped unless *user* is None or staff)."""
    qs = GenerationJob.objects.select_related("model", "app_requirement")
    if user is not None and getattr(user, "is_authenticated", False) and not getattr(user, "is_staff", False):
        qs = qs.filter(created_by=user)
    if experiment_id:
        qs = qs.filter(experiment_id=experiment_id)
    if batch_id:
        qs = qs.filter(batch_id=batch_id)
    if job_ids:
        qs = qs.filter(id__in=job_ids)
    return qs.order_by("created_at")


def job_labels(job: GenerationJob) -> dict[str, Any]:
    """The identifying columns every per-app row starts with."""
    return {
        "job_id": str(job.id),
        "model": job.model.model_id if job.model_id else "",
        "model_name": job.model.model_name if job.model_id else "",
        "provider": job.model.provider if job.model_id else "",
        "template": job.app_requirement.slug if job.app_requirement_id else "",
        "mode": job.mode,
        "repeat_index": job.repeat_index,
        "build_healing": job.build_healing,
    }


def _finding_records(result_to_job: dict[str, str], *, include_ai: bool) -> list[dict[str, Any]]:
    """Dedup records of the findings in the given results; ``run_id`` is the app (job) they describe."""
    qs = Finding.objects.filter(result_id__in=list(result_to_job))
    if not include_ai:
        qs = qs.exclude(result__tool_slug__in=AnalyzerTool.ai_slugs())
    records = []
    for row in qs.values(
        "id",
        "result_id",
        "result__tool_slug",
        "rule_id",
        "severity",
        "category",
        "file_path",
        "line_number",
        "tool_specific_data",
        "title",
    ):
        extra = row["tool_specific_data"] if isinstance(row["tool_specific_data"], dict) else {}
        records.append(
            {
                "id": row["id"],
                "run_id": result_to_job[str(row["result_id"])],
                "tool": row["result__tool_slug"],
                "rule_id": row["rule_id"],
                "severity": row["severity"],
                "category": row["category"],
                "file_path": row["file_path"],
                "line": row["line_number"],
                "end_line": extra.get("end_line"),
                "cwe": extra.get("cwe") or "",
                "package": extra.get("package") or "",
                "issue_key": extra.get("issue_key") or "",
                "title": row["title"],
            },
        )
    return records


def tool_coverage(result_ids: list, *, include_ai: bool) -> dict[str, Any]:
    """Which tools measured every analyzed app, and which did not.

    A tool *measured* an app when its result completed, or when it was
    skipped as not applicable (the app has none of the files it reads, so
    zero findings is the truth). A failed, timed-out or app-not-running
    result measured nothing; comparing that app's counts with apps where the
    tool ran would make it look cleaner than it is.
    """
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
    n_runs = len(apps)
    common = sorted(t for t in all_tools if len(measured[t]) == n_runs)
    return {
        "apps": n_runs,
        "tools_common": common,
        "tools_partial": {
            t: {"apps_measured": len(measured[t]), "apps_missing": n_runs - len(measured[t]), **reasons[t]}
            for t in sorted(all_tools - set(common))
        },
    }


def _per_kloc(count: int, loc: int) -> float | None:
    return round(count * 1000 / loc, 2) if loc else None


def _group_summary(apps: list[dict[str, Any]]) -> dict[str, Any]:
    unique = [a["unique_issues"] for a in apps]
    raw = sum(a["raw_findings"] for a in apps)
    loc = sum(a["loc"] for a in apps)
    sev = Counter()
    for a in apps:
        for s in SEVERITIES:
            sev[s] += a[f"unique_{s}"]
    return {
        "apps": len(apps),
        "raw_findings": raw,
        "unique_issues": sum(unique),
        "duplicate_findings": raw - sum(unique),
        "mean_unique_per_app": round(pystats.fmean(unique), 2) if unique else None,
        "median_unique_per_app": pystats.median(unique) if unique else None,
        "sd_unique_per_app": round(pystats.stdev(unique), 2) if len(unique) > 1 else None,
        "loc": loc,
        "unique_per_kloc": _per_kloc(sum(unique), loc),
        "security_unique": sum(a["security_unique"] for a in apps),
        **{f"unique_{s}": sev[s] for s in SEVERITIES},
    }


def issue_breakdowns(
    jobs: QuerySet[GenerationJob],
    *,
    include_ai: bool = False,
    line_tolerance: int = DEFAULT_LINE_TOLERANCE,
    common_tools_only: bool = True,
) -> dict[str, Any]:
    """Raw vs deduplicated issue counts per app, model, template, model x template and tool.

    Only jobs with a finished analysis run appear; ``by_tool_rule`` is the
    "which tool finds which kind of issue" table. With *common_tools_only*
    (the default) findings count only from tools that measured every app in
    scope (:func:`tool_coverage`), so every app is measured with the same
    instrument set; ``coverage`` lists the tools left out and why.
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

    coverage = tool_coverage(result_ids, include_ai=include_ai)
    records = _finding_records(result_to_job, include_ai=include_ai)
    if common_tools_only:
        counted = set(coverage["tools_common"])
        records = [r for r in records if r["tool"] in counted]
    clusters = cluster_findings(records, line_tolerance=line_tolerance)

    raw_by_run: Counter = Counter(r["run_id"] for r in records)
    clusters_by_run: dict[str, list] = defaultdict(list)
    for c in clusters:
        clusters_by_run[c.run_id].append(c)

    apps: list[dict[str, Any]] = []
    for job_id, (run_id, _at) in latest_run.items():
        job = jobs_by_id[job_id]
        own = clusters_by_run.get(job_id, [])
        # Source LOC, not metrics.lines_of_code (which counts every generated
        # line, lockfiles included): the denominator must mean the same for all apps.
        loc = loc_from_job(job)["total_loc"]
        sev = Counter(c.severity for c in own)
        apps.append(
            {
                **job_labels(job),
                "run_id": run_id,
                "loc": int(loc or 0),
                "raw_findings": raw_by_run.get(job_id, 0),
                "unique_issues": len(own),
                "multi_tool_issues": sum(1 for c in own if len(c.tools) > 1),
                "security_unique": sum(1 for c in own if c.category in ("security", "secrets")),
                "unique_per_kloc": _per_kloc(len(own), int(loc or 0)),
                **{f"unique_{s}": sev.get(s, 0) for s in SEVERITIES},
            },
        )

    def grouped(*keys: str) -> list[dict[str, Any]]:
        groups: dict[tuple, list] = defaultdict(list)
        for a in apps:
            groups[tuple(a[k] for k in keys)].append(a)
        return [{**dict(zip(keys, k, strict=True)), **_group_summary(v)} for k, v in sorted(groups.items())]

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
                "apps": set(),
                "severity": Counter(),
            },
        )
        row["findings"] += 1
        row["apps"].add(r["run_id"])
        row["severity"][r["severity"]] += 1
    by_tool_rule = [
        {
            **{k: v for k, v in row.items() if k not in ("apps", "severity")},
            "apps": len(row["apps"]),
            "max_severity": max(row["severity"], key=lambda s: SEVERITY_RANK.get(s, 0)),
            **{f"raw_{s}": row["severity"].get(s, 0) for s in SEVERITIES},
        }
        for row in sorted(rule_rows.values(), key=lambda r: (-r["findings"], r["tool"], r["rule_id"]))
    ]

    overlap = tool_overlap(clusters)
    raw_sev_by_tool: dict[str, Counter] = defaultdict(Counter)
    apps_by_tool: dict[str, set] = defaultdict(set)
    for r in records:
        raw_sev_by_tool[r["tool"]][r["severity"]] += 1
        apps_by_tool[r["tool"]].add(r["run_id"])
    by_tool = [
        {
            **t,
            "apps_with_findings": len(apps_by_tool[t["tool"]]),
            **{f"raw_{s}": raw_sev_by_tool[t["tool"]].get(s, 0) for s in SEVERITIES},
        }
        for t in overlap["tools"]
    ]
    return {
        "params": {
            "include_ai": include_ai,
            "line_tolerance": line_tolerance,
            "common_tools_only": common_tools_only,
        },
        "coverage": coverage,
        "totals": {**_group_summary(apps), "multi_tool_issues": overlap["multi_tool_issues"]},
        "per_app": apps,
        "by_model": grouped("model"),
        "by_template": grouped("template"),
        "by_model_template": grouped("model", "template"),
        "by_tool": by_tool,
        "by_tool_rule": by_tool_rule,
        "tool_pairs": overlap["pairs"],
        "issues": [c.to_dict() for c in clusters],
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
    Agent (verbatim) builds are never healed and are counted apart.
    """
    rows: list[dict[str, Any]] = []
    not_applicable = 0
    added = Counter()
    patches = Counter()
    for job in jobs:
        report = _healing_report(job)
        if report.get("applicable") is False or job.mode == GenerationJob.Mode.AGENT:
            not_applicable += 1
            continue
        deployed = _outcomes(job)
        if not deployed:
            continue
        added.update(report.get("added_requirements") or [])
        patches.update({p.split(":", 1)[-1] for p in report.get("patches") or []})
        rows.append(
            {
                **job_labels(job),
                "deployed": deployed,
                "healing_changed": report.get("changed"),
                "added_requirements": report.get("added_requirements") or [],
                "patches": report.get("patches") or [],
            },
        )
    modes = sorted({m for r in rows for m in r["deployed"]}, key=lambda m: (m != baseline, m))

    def grouped(key: str) -> list[dict[str, Any]]:
        groups: dict[str, list] = defaultdict(list)
        for r in rows:
            groups[r[key]].append(r)
        return [{key: k, "apps": len(v), **_rates(v, baseline, modes)} for k, v in sorted(groups.items())]

    return {
        "baseline": baseline,
        "modes": modes,
        "not_applicable": not_applicable,
        "totals": {
            "apps": len(rows),
            "healing_changed": sum(1 for r in rows if r["healing_changed"]),
            **_rates(rows, baseline, modes),
        },
        "by_model": grouped("model"),
        "by_template": grouped("template"),
        "added_requirements": [{"package": p, "apps": n} for p, n in added.most_common()],
        "patches": [{"patch": p, "apps": n} for p, n in patches.most_common()],
        "per_app": [
            {
                **{k: v for k, v in r.items() if k != "deployed"},
                **{f"deployed_{m}": r["deployed"].get(m) for m in modes},
            }
            for r in rows
        ],
    }
