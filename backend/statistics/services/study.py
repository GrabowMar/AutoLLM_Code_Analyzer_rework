"""Study-level tables for reporting: deduplicated issue breakdowns and the healing ablation.

Everything here describes one *scope* of generation jobs (an experiment, a
batch, an explicit id list, or everything a user owns). Issue tables read
the analysis engine's measurements, never raw findings recounted with rules
of their own: each job's canonical run under ONE frozen plan
(``rankings.services.measured.select_measured``; ``plan_hash`` equal ⇔
comparable), its stored ``measurement`` for counts, SLOC and densities, and
the engine's deduplication (``Finding.issue_key``) for the per-issue and
tool-overlap tables (``statistics.services.dedup``). So:

* a job analysed several times counts once (its canonical run);
* a job whose static measurement is unavailable (a required tool failed)
  has ``None`` counts, never 0, and is left out of sums and densities;
* counts and SLOC cover the same measured scope (code the model wrote);
* AI-reviewer findings are opinions: their consensus is reported in
  separate ``ai_*`` columns and never added to static issue counts;
* jobs that were not measured under the plan are listed in ``coverage``
  with the reason, instead of counting as clean.
"""

from __future__ import annotations

import math
import statistics as pystats
from collections import Counter
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.models import GenerationJob
from backend.rankings.services.measured import select_measured
from backend.statistics.services.dedup import DEFAULT_LINE_TOLERANCE
from backend.statistics.services.dedup import SEVERITY_RANK
from backend.statistics.services.dedup import cluster_findings
from backend.statistics.services.dedup import issue_class
from backend.statistics.services.dedup import load_records
from backend.statistics.services.dedup import tool_overlap

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from django.db.models import QuerySet

    from backend.rankings.services.measured import MeasuredRun
    from backend.rankings.services.measured import Selection

SEVERITIES = ("critical", "high", "medium", "low", "info")
SECURITY_CATEGORIES = ("security", "secrets")


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


def _per_kloc(count: int | None, loc: int | None) -> float | None:
    return round(count * 1000 / loc, 2) if count is not None and loc else None


def _known_sum(values) -> int | None:
    """Sum of the known values; None when none is known (missing data is never 0)."""
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _security_issues(run: MeasuredRun) -> int | None:
    """Counted static security/secret issues; None when those categories were not measured."""
    if not run.static_available:
        return None
    if "security_issues" in run.static:
        value = run.static["security_issues"]
        # None: a security tool failed, so the category is missing data.
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    by_category = (run.static.get("issues") or {}).get("by_category") or {}
    return sum(int(by_category.get(c) or 0) for c in SECURITY_CATEGORIES)


def _group_summary(apps: list[dict[str, Any]]) -> dict[str, Any]:
    static = [a for a in apps if a["unique_issues"] is not None]
    unique = [a["unique_issues"] for a in static]
    raw = _known_sum(a["raw_findings"] for a in static)
    # Densities pool issues and SLOC of the same (statically measured) apps.
    loc = _known_sum(a["loc"] for a in static if a["loc"] is not None)
    unique_total = sum(unique) if static else None
    security = [a["security_unique"] for a in static if a["security_unique"] is not None]
    ai = [a for a in apps if a["ai_consensus_issues"] is not None]
    ai_total = sum(a["ai_consensus_issues"] for a in ai) if ai else None
    ai_loc = _known_sum(a["loc"] for a in ai if a["loc"] is not None)
    return {
        "apps": len(apps),
        "static_measured_apps": len(static),
        "raw_findings": raw,
        "unique_issues": unique_total,
        "duplicate_findings": raw - unique_total if raw is not None and unique_total is not None else None,
        "mean_unique_per_app": round(pystats.fmean(unique), 2) if unique else None,
        "median_unique_per_app": pystats.median(unique) if unique else None,
        "sd_unique_per_app": round(pystats.stdev(unique), 2) if len(unique) > 1 else None,
        "loc": loc,
        "unique_per_kloc": _per_kloc(unique_total, loc),
        "security_unique": sum(security) if security else None,
        "security_measured_apps": len(security),
        **{f"unique_{s}": (sum(a[f"unique_{s}"] for a in static) if static else None) for s in SEVERITIES},
        "ai_reviewed_apps": len(ai),
        "ai_consensus_issues": ai_total,
        "ai_consensus_per_kloc": _per_kloc(ai_total, ai_loc),
    }


def _app_row(job: GenerationJob, run: MeasuredRun, clusters: list) -> dict[str, Any]:
    issues = run.issues_by_severity()
    static_ok = run.static_available
    ai = run.ai or {}
    return {
        **job_labels(job),
        "run_id": str(run.run_id),
        "plan_hash": run.plan_hash,
        "static_available": static_ok,
        "static_unavailable_reasons": list(run.static.get("unavailable_reasons") or []) if not static_ok else [],
        # Measured SLOC: the scope the counts cover (lockfiles, scaffold and build output excluded).
        "loc": run.measured_sloc(),
        "raw_findings": run.findings_total(),
        "unique_issues": run.issues_total(),
        "multi_tool_issues": sum(1 for c in clusters if len(c.tools) > 1) if static_ok else None,
        "security_unique": _security_issues(run),
        # The measurement's density (None below the plan's min_sloc_for_density).
        "unique_per_kloc": run.static.get("issues_per_kloc") if static_ok else None,
        **{f"unique_{s}": (issues or {}).get(s) if issues is not None else None for s in SEVERITIES},
        "ai_available": run.ai_available,
        "ai_consensus_issues": run.ai_total(),
        "ai_consensus_per_kloc": ai.get("per_kloc") if run.ai_available else None,
        "ai_cost_usd": run.cost_usd(),
        "ai_cost_complete": run.cost_complete(),
    }


def issue_breakdowns(
    jobs: QuerySet[GenerationJob] | list[GenerationJob],
    *,
    include_ai: bool = False,
    line_tolerance: int = DEFAULT_LINE_TOLERANCE,
    plan_hash: str | None = None,
    rules: dict[str, Any] | None = None,
    selection: Selection | None = None,
) -> dict[str, Any]:
    """Counted findings vs deduplicated issues per app, model, template, model x template and tool.

    Only jobs measured under the reference plan (*plan_hash*, else the
    auto-selected one) appear in the tables; the rest are in ``coverage``.
    ``by_tool_rule`` is the "which tool finds which kind of issue" table.
    *include_ai* adds the AI consensus clusters (``ai_issues``) and per-rule
    table (``ai_by_rule``); AI counts are always separate from static ones.
    *line_tolerance* is kept for callers: the engine stored each run's
    grouping (``issue_key``) under its plan, and it is not re-derived.
    *selection* reuses a :func:`select_measured` result of the same jobs.
    """
    jobs_by_id = {j.id: j for j in jobs}
    if selection is None:
        selection = select_measured(list(jobs_by_id), plan_hash=plan_hash, rules=rules)
    measured = selection.runs_for(jobs_by_id)
    static_runs = [r for r in measured if r.static_available]
    run_to_job = {str(r.run_id): r.job_id for r in measured}

    records = load_records([r.run_id for r in static_runs])
    clusters = cluster_findings(records, line_tolerance=line_tolerance)
    clusters_by_run: dict[str, list] = defaultdict(list)
    for c in clusters:
        clusters_by_run[c.run_id].append(c)

    apps = [_app_row(jobs_by_id[r.job_id], r, clusters_by_run.get(str(r.run_id), [])) for r in measured]

    def grouped(*keys: str) -> list[dict[str, Any]]:
        groups: dict[tuple, list] = defaultdict(list)
        for a in apps:
            groups[tuple(a[k] for k in keys)].append(a)
        return [{**dict(zip(keys, k, strict=True)), **_group_summary(v)} for k, v in sorted(groups.items())]

    by_tool_rule = _rule_table(records, run_to_job)
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
    out: dict[str, Any] = {
        "params": {
            "include_ai": include_ai,
            "line_tolerance": line_tolerance,
            "dedup": "engine issue_key (frozen with each run)",
            "ai_counted_in_static": False,
            "plan_hash": selection.plan_hash,
        },
        "plan_hash": selection.plan_hash,
        "coverage": selection.coverage(list(jobs_by_id)),
        "totals": {
            **_group_summary(apps),
            "multi_tool_issues": overlap["multi_tool_issues"] if static_runs else None,
        },
        "per_app": apps,
        "by_model": grouped("model"),
        "by_template": grouped("template"),
        "by_model_template": grouped("model", "template"),
        "by_tool": by_tool,
        "by_tool_rule": by_tool_rule,
        "tool_pairs": overlap["pairs"],
        "issues": [c.to_dict() for c in clusters],
    }
    if include_ai:
        ai_records = load_records([r.run_id for r in measured if r.ai_available], ai=True)
        ai_clusters = cluster_findings(ai_records, ai=True)
        out["ai_issues"] = [c.to_dict() for c in ai_clusters]
        out["ai_by_rule"] = _rule_table(ai_records, run_to_job, clusters=ai_clusters)
    return out


def _rule_table(
    records: list[dict[str, Any]],
    run_to_job: dict[str, Any],
    *,
    clusters: list | None = None,
) -> list[dict[str, Any]]:
    """Counted findings per (tool, rule) (AI: per consensus cluster, by category), most frequent first."""
    rule_rows: dict[tuple[str, str], dict[str, Any]] = {}
    items: list[tuple[dict[str, Any], int]]
    if clusters is not None:
        # One row per AI consensus cluster (its k replicate members are one opinion).
        items = [(c.members[0] | {"severity": c.severity, "category": c.category}, 1) for c in clusters]
    else:
        items = [(r, 1) for r in records]
    for r, weight in items:
        rule = r["rule_id"] or (r["category"] if clusters is not None else "")
        key = (r["tool"], rule)
        row = rule_rows.setdefault(
            key,
            {
                "tool": r["tool"],
                "rule_id": rule,
                "issue_class": issue_class(r["tool"], r["rule_id"] or "", cwe=r["cwe"], category=r["category"]),
                "category": r["category"],
                "cwe": ";".join(r["cwe"]) if isinstance(r["cwe"], list) else r["cwe"],
                "example_title": (r["title"] or "")[:200],
                "findings": 0,
                "apps": set(),
                "severity": Counter(),
            },
        )
        row["findings"] += weight
        row["apps"].add(run_to_job.get(r["run_id"]))
        row["severity"][r["severity"]] += weight
    return [
        {
            **{k: v for k, v in row.items() if k not in ("apps", "severity")},
            "apps": len(row["apps"]),
            "max_severity": max(row["severity"], key=lambda s: SEVERITY_RANK.get(s, 0)),
            **{f"raw_{s}": row["severity"].get(s, 0) for s in SEVERITIES},
        }
        for row in sorted(rule_rows.values(), key=lambda r: (-r["findings"], r["tool"], r["rule_id"]))
    ]


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
