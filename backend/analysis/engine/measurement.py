"""Per-run measurement: the one record reports, rankings and studies read.

(owner: sample/measure stage.) ``compute(run)`` is called by the executor at
finalize; ``outcomes(measurement)`` turns it into named research outcomes.
Missing data is ``None``, never 0: an outcome whose tools failed is NA.

Rules (``measure-v2``):

* **Scope.** Numerators and denominators cover the same code: counted
  findings are those in the measured scope (``normalize``), SLOC is the
  manifest's measured SLOC (model-written source lines; line-level for
  agent-modified files). Style findings, out-of-scope and scaffold findings
  are reported (``style_findings`` / ``out_of_scope_findings`` /
  ``scaffold_findings``) but never counted.
* **Availability.** A tool is OK when it succeeded or was not applicable.
  ``required`` only decides the run status (``complete``); whether an outcome
  is measured depends on what is missing from it. ``static.available`` is
  False (``static.unavailable_reasons``) when a REQUIRED static tool is not
  OK, when an OPTIONAL one is not OK that could have reported counted
  findings on the measured code (it emits a counted category and covers a
  measured file's language: ``static.incomplete_tools``), when no static tool
  ran OK, or when the integrity checks below fail. ``category_available[c]``
  is False when a tool that can emit ``c`` (``categories.yaml`` ``emits``)
  is not OK - required, or covering a measured language - or when no OK
  tool covers ``c``. Two runs of one plan thus never compare a count made
  with a tool against one made without it. Outcomes over an unavailable
  scope are None. ``has_high_security`` is True as soon as one high/critical
  security issue is found (a failure can only hide more), None when none was
  found and the security categories are unavailable.
* **Parseability.** A file a static tool could not parse yields no findings
  from that tool while its lines stay in the denominator, so broken code
  would look clean. When the measured SLOC in files some static tool could
  not parse exceeds ``outcomes.max_unparseable_share`` (default 0.1) of the
  measured SLOC, the static outcomes are NA (``unparseable_code``); the
  functional outcome still measures such an app.
* **Integrity.** Findings are normalized against the sample re-resolved at
  execution, SLOC comes from the manifest stored at submit. Every finding's
  line origin is checked against the stored attribution (changed-line
  ranges) and its ``mapping_digest`` against the plan's ``mapping_hashes``;
  a mismatch (``attribution_mismatch`` / ``mapping_drift``) makes the
  affected densities NA instead of mixing two scopes or two rule tables.
* **Density.** ``x / measured KLOC``; None when measured SLOC is below the
  plan's ``outcomes.min_sloc_for_density`` (default 50) or is 0.
* **Issues.** Distinct ``issue_key`` among counted static findings (``dedup``);
  an issue's severity is its most severe member's. AI consensus clusters are
  distinct ``issue_key`` among counted AI findings (severity = the most
  frequent label among the replicates). ``counted`` is set by the reviewer's
  consensus under the static rules (grounded, in the measured scope, and a
  category the plan counts: style / complexity / duplication only with
  ``count_style`` / ``count_metric_findings``), so the AI and static
  numerators count the same kinds of issue.
* **AI scope.** ``ai.per_kloc`` divides by the measured SLOC, so it is NA
  (``ai.per_kloc_unavailable_reasons``) when the reviewer was not shown all
  of it (``reviewer.include_origins`` / ``include_kinds`` narrower than the
  measured files, e.g. a not-applicable review of measured code).
* **AI replicates and spend.** The consensus threshold is the reviewer's
  (a strict majority of the k planned samples, ``k // 2 + 1``);
  ``ai.replicates_complete`` False marks a review whose m < k successful
  samples make its count a lower bound. ``ai.cost_usd`` / ``ai.tokens`` sum
  what the replicates reported: unknown is None, a partly known sum is a
  lower bound (``cost_complete`` / ``tokens_complete`` False), never 0.
  Spend of attempts replaced by retries is ``ai.replaced``.
* **Provenance.** ``engine_versions`` records the engine stage versions
  of the code that computed the measurement (drift against the plan's
  ``engine_versions`` is detectable per run). In the summary, static counts
  are None when no static tool succeeded (nothing was scanned).
* **Metrics.** Complexity from the plan's first complexity tool (lizard, else
  radon when lizard is not planned): a failed one is NA, never replaced by
  another instrument with another scope. Duplication from jscpd. Per-function
  / per-file data in ``ToolResult.metrics`` is restricted to measured files
  when present (``scope: measured_files``) and, for line-attributed modified
  files, to the measured lines (``attribution: line_level``: a function
  counts when one of its lines is in the measured scope, a duplicated line
  when it is a measured line); otherwise the tool's totals are used
  (``scope: all_scanned``).

Expected ``ToolResult.metrics`` shapes (toolchain adapters): complexity tools
``{"functions": [{"path", "name", "line", "complexity"}], ...}`` (``file`` /
``ccn`` accepted), jscpd ``{"percentage", "clones", "duplicated_lines",
"total_lines", "files": {path: {"lines", "duplicated_lines"}}}``. The AI
ToolResult keeps the reviewer's ``agreement`` dict in ``metrics["agreement"]``.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from backend.analysis.engine import dedup
from backend.analysis.engine.catalog import AI_TOOL_SLUG
from backend.analysis.engine.normalize import FINDING_CATEGORIES
from backend.analysis.engine.normalize import emitted_categories
from backend.analysis.engine.normalize import finding_path
from backend.analysis.engine.normalize import mapping_digest
from backend.analysis.engine.normalize import uncounted_categories
from backend.analysis.engine.sample import check_eligibility
from backend.analysis.engine.sample import effective_scope
from backend.analysis.engine.sample import expand_ranges
from backend.analysis.engine.reviewer.consensus import THRESHOLD_RULE
from backend.analysis.engine.reviewer.consensus import majority
from backend.analysis.engine.sample import truncation_flags
from backend.analysis.engine.types import DEDUP_VERSION
from backend.analysis.engine.types import MEASUREMENT_VERSION
from backend.analysis.engine.types import NORMALIZER_VERSION
from backend.analysis.engine.types import SAMPLE_RESOLVER_VERSION
from backend.analysis.engine.types import SEVERITIES
from backend.analysis.engine.types import SLOC_VERSION

MEASUREMENT_SCHEMA_VERSION = 1
SUMMARY_SCHEMA_VERSION = 2
OK_STATUSES = ("succeeded", "not_applicable")
TOOL_STATUSES = (
    "succeeded",
    "failed",
    "timed_out",
    "not_applicable",
    "skipped",
    "cancelled",
    "pending",
    "running",
)
SECURITY_CATEGORIES = ("security", "secrets")
HIGH_PLUS = ("critical", "high")
COMPLEXITY_TOOLS = ("lizard", "radon")
DUPLICATION_TOOLS = ("jscpd",)
DEFAULT_OUTCOME_CONFIG: dict[str, Any] = {
    "severity_weights": {"critical": 10, "high": 5, "medium": 2, "low": 1, "info": 0},
    "count_style": False,
    "count_metric_findings": False,
    "min_sloc_for_density": 50,
    # Static outcomes are NA when more than this share of the measured SLOC
    # is in files a static tool could not parse (their findings are missing,
    # their lines are not): broken code must not look clean.
    "max_unparseable_share": 0.1,
}
# What the reviewer is shown by default (reviewer.prompt.DEFAULT_INCLUDE_*).
_REVIEWER_DEFAULT_ORIGINS = ("authored", "added", "modified")
_REVIEWER_DEFAULT_KINDS = ("source", "config", "manifest", "env")
_RANKS = (("A", 5), ("B", 10), ("C", 20), ("D", 30), ("E", 40))
_FINDING_FIELDS = (
    "id",
    "tool_slug",
    "rule_id",
    "severity",
    "category",
    "cwe",
    "counted",
    "in_measured_scope",
    "file_origin",
    "is_ai",
    "is_consensus",
    "grounding",
    "issue_key",
    "fingerprint",
    "file_path",
    "line_number",
    "end_line",
    "review_sample_id",
    "tool_specific_data",
)

# key -> {label, kind: rate|count|binary|metric, unit, higher_is_better, description, requires}
# ``requires`` names the availability the value depends on: "static" (every
# required static tool OK), "static:<category>" (that finding category
# available), "ai", "complexity", "duplication", "functional", "sample".
OUTCOMES: dict[str, dict[str, Any]] = {
    "static_findings_per_kloc": {
        "label": "Static findings / KLOC",
        "kind": "rate",
        "unit": "findings per 1000 measured SLOC",
        "higher_is_better": False,
        "description": "Counted static findings (all tools, before dedup) per measured KLOC.",
        "requires": ["static"],
    },
    "static_issues_per_kloc": {
        "label": "Static issues / KLOC",
        "kind": "rate",
        "unit": "issues per 1000 measured SLOC",
        "higher_is_better": False,
        "description": "Distinct counted static issues (cross-tool dedup) per measured KLOC.",
        "requires": ["static"],
    },
    "security_issues_per_kloc": {
        "label": "Security issues / KLOC",
        "kind": "rate",
        "unit": "issues per 1000 measured SLOC",
        "higher_is_better": False,
        "description": "Distinct counted static issues of category security or secrets per measured KLOC.",
        "requires": ["static:security", "static:secrets"],
    },
    "high_plus_issues_per_kloc": {
        "label": "High+ issues / KLOC",
        "kind": "rate",
        "unit": "issues per 1000 measured SLOC",
        "higher_is_better": False,
        "description": "Distinct counted static issues of severity high or critical per measured KLOC.",
        "requires": ["static"],
    },
    "has_high_security": {
        "label": "Has a high/critical security issue",
        "kind": "binary",
        "unit": None,
        "higher_is_better": False,
        "description": "At least one counted static security/secrets issue of severity high or critical.",
        "requires": ["static:security", "static:secrets"],
    },
    "ai_consensus_per_kloc": {
        "label": "AI consensus findings / KLOC",
        "kind": "rate",
        "unit": "findings per 1000 measured SLOC",
        "higher_is_better": False,
        "description": "Grounded, in-scope AI findings reported by a majority of review samples, per measured KLOC.",
        "requires": ["ai"],
    },
    "complexity_max": {
        "label": "Max cyclomatic complexity",
        "kind": "metric",
        "unit": "cyclomatic complexity",
        "higher_is_better": False,
        "description": "Highest cyclomatic complexity of a function in the measured files.",
        "requires": ["complexity"],
    },
    "complexity_mean": {
        "label": "Mean cyclomatic complexity",
        "kind": "metric",
        "unit": "cyclomatic complexity",
        "higher_is_better": False,
        "description": "Mean cyclomatic complexity of the functions in the measured files.",
        "requires": ["complexity"],
    },
    "duplication_pct": {
        "label": "Duplicated lines %",
        "kind": "metric",
        "unit": "%",
        "higher_is_better": False,
        "description": "Share of duplicated lines (jscpd) in the measured files.",
        "requires": ["duplication"],
    },
    "functional_pass_rate": {
        "label": "Functional pass rate",
        "kind": "metric",
        "unit": "share of conformance checks passed",
        "higher_is_better": True,
        "description": "Hidden conformance suite pass rate; None when not evaluated, not applicable or infra error.",
        "requires": ["functional"],
    },
    "measured_sloc": {
        "label": "Measured SLOC",
        "kind": "count",
        "unit": f"SLOC ({SLOC_VERSION})",
        "higher_is_better": None,
        "description": "Model-written source lines in scope (the density denominator).",
        "requires": ["sample"],
    },
    "suppression_markers_per_kloc": {
        "label": "Suppression markers / KLOC",
        "kind": "rate",
        "unit": "markers per 1000 measured SLOC",
        "higher_is_better": False,
        "description": "noqa / nosec / eslint-disable / nosemgrep / gitleaks:allow / ... on model-written lines.",
        "requires": ["sample"],
    },
    "static_issues": {
        "label": "Static issues",
        "kind": "count",
        "unit": "issues",
        "higher_is_better": False,
        "description": "Distinct counted static issues.",
        "requires": ["static"],
    },
    "security_issues": {
        "label": "Security issues",
        "kind": "count",
        "unit": "issues",
        "higher_is_better": False,
        "description": "Distinct counted static issues of category security or secrets.",
        "requires": ["static:security", "static:secrets"],
    },
    "high_plus_issues": {
        "label": "High+ issues",
        "kind": "count",
        "unit": "issues",
        "higher_is_better": False,
        "description": "Distinct counted static issues of severity high or critical.",
        "requires": ["static"],
    },
    "weighted_per_kloc": {
        "label": "Severity-weighted issues / KLOC",
        "kind": "rate",
        "unit": "weighted issues per 1000 measured SLOC",
        "higher_is_better": False,
        "description": "Secondary leaderboard score: issues weighted by severity (plan severity_weights) per KLOC.",
        "requires": ["static"],
    },
    "ai_grounding_rate": {
        "label": "AI grounding rate",
        "kind": "metric",
        "unit": "share of AI findings",
        "higher_is_better": True,
        "description": "Share of AI findings (all samples) whose evidence was found in the cited code.",
        "requires": ["ai"],
    },
    "ai_mean_pairwise_jaccard": {
        "label": "AI sample agreement",
        "kind": "metric",
        "unit": "mean pairwise Jaccard",
        "higher_is_better": True,
        "description": "Agreement between review samples (mean pairwise Jaccard of their finding clusters).",
        "requires": ["ai"],
    },
}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def outcome_config(plan: dict[str, Any] | None) -> dict[str, Any]:
    cfg = {**DEFAULT_OUTCOME_CONFIG, **((plan or {}).get("outcomes") or {})}
    weights = {**DEFAULT_OUTCOME_CONFIG["severity_weights"], **(cfg.get("severity_weights") or {})}
    cfg["severity_weights"] = weights
    return cfg


def _zero_severities() -> dict[str, int]:
    return dict.fromkeys(SEVERITIES, 0)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        return None
    return float(value)


def _density(count: float | None, sloc: int | None, minimum: int) -> float | None:
    if count is None or not sloc or sloc <= 0 or sloc < (minimum or 0):
        return None
    return round(count * 1000.0 / sloc, 4)


def _rank(complexity: float) -> str:
    for letter, upper in _RANKS:
        if complexity <= upper:
            return letter
    return "F"


def _percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolation percentile (numpy's default), None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _is_ai_result(result) -> bool:
    return result.tool_kind == "ai" or result.tool_slug == AI_TOOL_SLUG


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _load(run) -> tuple[list, list[dict[str, Any]], list[dict[str, Any]]]:
    from backend.analysis.models import Finding
    from backend.analysis.models import ReviewSample

    results = list(run.results.all().order_by("position", "tool_slug"))
    findings = list(Finding.objects.filter(run=run).values(*_FINDING_FIELDS))
    samples = list(
        ReviewSample.objects.filter(result__run=run)
        .order_by("index")
        .values("id", "index", "status", "usage", "grounding", "failure_reason", "result_id"),
    )
    return results, findings, samples


def _manifest_totals(manifest: dict[str, Any]) -> dict[str, Any]:
    return manifest.get("totals") if isinstance(manifest.get("totals"), dict) else {}


def _measured_files(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        e["path"]: e
        for e in manifest.get("files") or []
        if isinstance(e, dict) and e.get("path") and e.get("included") and e.get("measured")
    }


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


def _sloc_section(manifest: dict[str, Any]) -> dict[str, Any]:
    totals = _manifest_totals(manifest)
    by_language: Counter[str] = Counter()
    by_origin: Counter[str] = Counter()
    for entry in manifest.get("files") or []:
        if not isinstance(entry, dict) or not entry.get("included"):
            continue
        sloc_total = int(entry.get("sloc") or 0)
        measured_sloc = int(entry.get("measured_sloc") or 0)
        if entry.get("measured"):
            by_language[entry.get("language") or "other"] += measured_sloc
        origin = entry.get("origin") or "unknown"
        split = entry.get("sloc_by_origin")
        if isinstance(split, dict) and split:
            for key, value in split.items():
                by_origin[str(key)] += int(_num(value) or 0)
        elif origin == "modified" and entry.get("changed_lines") is not None:
            # Line-level attribution: the unchanged lines of a modified file
            # are the scaffold's.
            by_origin["modified"] += measured_sloc
            by_origin["scaffold"] += max(sloc_total - measured_sloc, 0)
        else:
            by_origin[origin] += sloc_total
    measured = totals.get("measured_sloc") if isinstance(totals.get("measured_sloc"), int) else None
    total = totals.get("sloc") if isinstance(totals.get("sloc"), int) else None
    return {
        "measured": measured,
        "total": total,
        "by_language": dict(sorted((k, v) for k, v in by_language.items() if v)),
        "by_origin": dict(sorted((k, v) for k, v in by_origin.items() if v)),
    }


def _files_section(manifest: dict[str, Any]) -> dict[str, Any]:
    entries = [e for e in manifest.get("files") or [] if isinstance(e, dict)]
    if not entries:
        return {"measured": None, "included": None, "excluded": None}
    included = [e for e in entries if e.get("included")]
    return {
        "measured": sum(1 for e in included if e.get("measured")),
        "included": len(included),
        "excluded": len(entries) - len(included),
    }


def _covers_measured_code(result, tool_languages: dict[str, Any], measured_languages: set[str] | None) -> bool:
    """Whether a tool could have scanned a measured file (unknown -> True)."""
    languages = tool_languages.get(result.tool_slug)
    if not isinstance(languages, (list, tuple)) or not languages or "*" in languages or measured_languages is None:
        return True
    return bool(set(languages) & measured_languages)


def _category_availability(static_results: list, covers) -> dict[str, bool]:
    """A category is available when an OK tool covers it and no tool that can emit it is
    missing: a required one, or an optional one that covers the measured code."""
    covered: set[str] = set()
    broken: set[str] = set()
    for result in static_results:
        emits = set(emitted_categories(result.tool_slug, result.category or ""))
        if result.status in OK_STATUSES:
            covered |= emits
        elif result.required or covers(result):
            broken |= emits
    return {category: category in covered and category not in broken for category in FINDING_CATEGORIES}


def _static_section(
    static_results: list,
    findings: list[dict[str, Any]],
    sloc: int | None,
    cfg: dict[str, Any],
    *,
    tool_languages: dict[str, Any] | None = None,
    measured_languages: set[str] | None = None,
    blockers: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """The static block. *blockers* are integrity reasons (``unparseable_code``,
    ``attribution_mismatch``, ``mapping_drift``) that make every static outcome NA."""
    tool_languages = tool_languages or {}

    def covers(result) -> bool:
        return _covers_measured_code(result, tool_languages, measured_languages)

    counted_categories = set(FINDING_CATEGORIES) - uncounted_categories(cfg)
    required_failed = [r.tool_slug for r in static_results if r.required and r.status not in OK_STATUSES]
    # An optional tool that is missing but could have reported counted
    # findings on the measured code: the counts would be made with a smaller
    # tool set than other runs of the same plan.
    incomplete = [
        r.tool_slug
        for r in static_results
        if not r.required
        and r.status not in OK_STATUSES
        and covers(r)
        and set(emitted_categories(r.tool_slug, r.category or "")) & counted_categories
    ]
    reasons: list[str] = []
    if not any(r.status in OK_STATUSES for r in static_results):
        reasons.append("no_tool_ok")
    if required_failed:
        reasons.append("required_tool_failed")
    if incomplete:
        reasons.append("optional_tool_failed")
    reasons += [b for b in blockers if b not in reasons]
    available = not reasons
    category_available = _category_availability(static_results, covers)
    static = [f for f in findings if not f["is_ai"]]
    counted = [f for f in static if f["counted"]]

    by_severity = _zero_severities()
    by_category: Counter[str] = Counter()
    by_tool: Counter[str] = Counter()
    for f in counted:
        by_severity[f["severity"] if f["severity"] in by_severity else "info"] += 1
        by_category[f["category"]] += 1
        by_tool[f["tool_slug"]] += 1

    issues = [dedup.issue_summary(members) for members in dedup.group_issues(counted).values()]
    issue_severity = _zero_severities()
    issue_category: Counter[str] = Counter()
    for issue in issues:
        issue_severity[issue["severity"] if issue["severity"] in issue_severity else "info"] += 1
        issue_category[issue["category"]] += 1
    weights = cfg["severity_weights"]
    weighted = float(sum(float(weights.get(i["severity"], 0) or 0) for i in issues))
    security = [i for i in issues if i["category"] in SECURITY_CATEGORIES]
    high_plus = [i for i in issues if i["severity"] in HIGH_PLUS]
    security_ok = all(category_available.get(c) for c in SECURITY_CATEGORIES) and not blockers
    minimum = int(cfg.get("min_sloc_for_density") or 0)

    has_high_security: bool | None
    if any(i["severity"] in HIGH_PLUS for i in security):
        has_high_security = True
    else:
        has_high_security = False if security_ok else None

    security_count = len(security) if security_ok else None
    high_plus_count = len(high_plus) if available else None
    return {
        "available": available,
        "unavailable_reasons": reasons,
        "required_failed": required_failed,
        "incomplete_tools": incomplete,
        "findings": {
            "total": len(counted),
            "by_severity": by_severity,
            "by_category": dict(sorted(by_category.items())),
            "by_tool": dict(sorted(by_tool.items())),
        },
        "issues": {
            "total": len(issues),
            "by_severity": issue_severity,
            "by_category": dict(sorted(issue_category.items())),
        },
        "weighted": weighted if available else None,
        "weighted_per_kloc": _density(weighted, sloc, minimum) if available else None,
        "findings_per_kloc": _density(len(counted), sloc, minimum) if available else None,
        "issues_per_kloc": _density(len(issues), sloc, minimum) if available else None,
        "security_issues": security_count,
        "security_issues_per_kloc": _density(security_count, sloc, minimum),
        "high_plus_issues": high_plus_count,
        "high_plus_issues_per_kloc": _density(high_plus_count, sloc, minimum),
        "has_high_security": has_high_security,
        "category_available": category_available,
        "style_findings": sum(1 for f in static if f["category"] == "style"),
        "out_of_scope_findings": sum(1 for f in static if not f["in_measured_scope"]),
        "scaffold_findings": sum(1 for f in static if f["file_origin"] == "scaffold"),
        "uncounted_findings": len(static) - len(counted),
    }


def _reviewer_uncovered_sloc(reviewer: dict[str, Any], manifest: dict[str, Any]) -> int | None:
    """Measured SLOC in files the reviewer is not shown (its include_origins/kinds), None if unknown."""
    entries = [e for e in manifest.get("files") or [] if isinstance(e, dict)]
    if not entries:
        return None
    origins = set(reviewer.get("include_origins") or _REVIEWER_DEFAULT_ORIGINS)
    kinds = set(reviewer.get("include_kinds") or _REVIEWER_DEFAULT_KINDS)
    return sum(
        int(_num(e.get("measured_sloc")) or 0)
        for e in entries
        if e.get("included") and e.get("measured") and (e.get("origin") not in origins or e.get("kind") not in kinds)
    )


def _ai_section(
    run,
    ai_result,
    findings: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    static_counted: list[dict[str, Any]],
    sloc: int | None,
    cfg: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
    blockers: list[str] | tuple[str, ...] = (),
) -> dict[str, Any] | None:
    reviewer = (run.plan or {}).get("reviewer") or {}
    if ai_result is None and not reviewer.get("enabled"):
        return None
    status = ai_result.status if ai_result is not None else "missing"
    available = status in OK_STATUSES
    # The density divides by the measured SLOC: it is only defined when the
    # reviewer was shown all of it (a not-applicable review of measured code,
    # or a reviewer scope narrower than the measured scope, is not "0 issues").
    uncovered = _reviewer_uncovered_sloc(reviewer, manifest or {})
    per_kloc_reasons: list[str] = []
    if uncovered or (status == "not_applicable" and sloc):
        per_kloc_reasons.append("reviewer_scope")
    per_kloc_reasons += [b for b in blockers if b not in per_kloc_reasons]
    own_samples = [s for s in samples if ai_result is not None and s["result_id"] == ai_result.id]
    ok_samples = [s for s in own_samples if s["status"] == "succeeded"]
    metrics = (ai_result.metrics or {}) if ai_result is not None else {}
    agreement = metrics.get("agreement") if isinstance(metrics.get("agreement"), dict) else {}

    ai_findings = [f for f in findings if f["is_ai"]]
    # Consensus marks ``counted`` (grounded, in scope, counted category); never re-derived here.
    counted = [f for f in ai_findings if f["counted"]]
    clusters = [dedup.issue_summary(m, mode="consensus") for m in dedup.group_issues(counted).values()]
    consensus_keys = {f["issue_key"] or f"single:{f['id']}" for f in ai_findings if f["is_consensus"]}
    counted_keys = {f["issue_key"] or f"single:{f['id']}" for f in counted}
    by_severity = _zero_severities()
    by_category: Counter[str] = Counter()
    for cluster in clusters:
        by_severity[cluster["severity"] if cluster["severity"] in by_severity else "info"] += 1
        by_category[cluster["category"]] += 1

    grounding = dict.fromkeys(("exact", "relocated", "ungrounded", "invalid_file", "out_of_range"), 0)
    for sample in ok_samples:
        for key, value in (sample.get("grounding") or {}).items():
            if key in grounding and isinstance(value, int):
                grounding[key] += value
    grounded_total = sum(grounding.values())
    spend = _ai_spend(ai_result, own_samples)
    planned = _planned_samples(reviewer, agreement, own_samples)
    threshold = agreement.get("threshold")
    if not _is_int(threshold):
        # Rows stored without agreement.threshold: the reviewer's rule (a strict
        # majority of the k planned samples), never a majority of the m that succeeded.
        threshold = majority(planned) if ok_samples and planned else None
    complete = agreement.get("replicates_complete")
    if not isinstance(complete, bool):
        complete = None if status == "not_applicable" or not planned else len(ok_samples) >= planned
    minimum = int(cfg.get("min_sloc_for_density") or 0)
    jaccard = _num(agreement.get("mean_pairwise_jaccard"))
    return {
        "available": available,
        "status": status,
        "failure_reason": getattr(ai_result, "failure_reason", "") or "",
        "engine": reviewer.get("engine"),
        "harness": reviewer.get("harness"),
        "model": (reviewer.get("model") or {}).get("slug") if isinstance(reviewer.get("model"), dict) else None,
        "samples_ok": len(ok_samples),
        "samples": planned,
        "planned_samples": planned,
        # False: fewer than k replicates succeeded, so the consensus count is a
        # lower bound of the full review's (a missing replicate votes for nothing).
        "replicates_complete": complete,
        "threshold": threshold,
        "threshold_rule": agreement.get("threshold_rule") or THRESHOLD_RULE,
        "consensus": {
            "total": len(clusters),
            "by_severity": by_severity,
            "by_category": dict(sorted(by_category.items())),
        },
        "per_kloc": _density(len(clusters), sloc, minimum) if available and not per_kloc_reasons else None,
        "per_kloc_unavailable_reasons": per_kloc_reasons,
        "uncovered_measured_sloc": uncovered,
        "findings_total": len(ai_findings),
        # Consensus clusters left uncounted (ungrounded, out of scope, or an uncounted category).
        "uncounted_consensus": len(consensus_keys - counted_keys),
        "grounding": grounding,
        "grounding_rate": round((grounding["exact"] + grounding["relocated"]) / grounded_total, 4)
        if grounded_total
        else None,
        "agreement": {
            "mean_pairwise_jaccard": jaccard,
            "planned_samples": planned,
            "successful_samples": len(ok_samples),
            "replicates_complete": complete,
        },
        "overlap_with_static": dedup.ai_static_overlap(static_counted, counted),
        **spend,
    }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _planned_samples(reviewer: dict[str, Any], agreement: dict[str, Any], own_samples: list) -> int | None:
    """k: the replicates the review asked for (agreement, else the frozen plan, else the rows)."""
    for value in (agreement.get("planned_samples"), reviewer.get("samples")):
        if _is_int(value) and value > 0:
            return value
    return len(own_samples) or None


def _ai_spend(ai_result, own_samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Cost and tokens of the review's replicates; unknown stays None, never 0.

    ``cost_usd`` sums the replicates whose spend is known (None when none is):
    a lower bound flagged ``cost_complete=False`` when a replicate's spend is
    unknown or itself incomplete, when the reviewer's usage totals say so
    (``ToolResult.metrics.usage.cost_complete``), or when the executor lost the
    spend of an interrupted review (``summary.cost_unknown``; the budget's
    upper bound is ``cost_upper_bound_usd``). Token counts follow the same rule
    per key (``tokens_complete``). ``replaced``: the attempts that user retries
    replaced (their replicates are gone, their spend is kept on the result's
    summary), so the money spent on the run is ``cost_usd`` + ``replaced.cost_usd``.
    """
    summary = (ai_result.summary or {}) if ai_result is not None else {}
    metrics = (ai_result.metrics or {}) if ai_result is not None else {}
    usage_totals = metrics.get("usage") if isinstance(metrics.get("usage"), dict) else {}
    known: list[float] = []
    incomplete = 0
    tokens: dict[str, int | None] = dict.fromkeys(("prompt", "completion", "reasoning", "cached"), None)
    tokens_missing = 0
    for sample in own_samples:
        usage = sample.get("usage") if isinstance(sample.get("usage"), dict) else {}
        cost = _num(usage.get("cost_usd"))
        if cost is not None:
            known.append(cost)
        if cost is None or usage.get("cost_complete") is False or usage.get("gateway_log_missing"):
            incomplete += 1
        for key in tokens:
            value = _num(usage.get(f"{key}_tokens"))
            if value is not None:
                tokens[key] = (tokens[key] or 0) + int(value)
        if _num(usage.get("prompt_tokens")) is None or _num(usage.get("completion_tokens")) is None:
            tokens_missing += 1
    lost = bool(summary.get("cost_unknown"))
    prompt, completion = tokens["prompt"], tokens["completion"]
    prior = summary.get("prior_attempts")
    replaced = prior if _is_int(prior) and prior > 0 else 0
    return {
        "cost_usd": round(math.fsum(known), 6) if known else None,
        "cost_complete": incomplete == 0 and usage_totals.get("cost_complete") is not False and not lost,
        "cost_incomplete_samples": incomplete,
        "cost_upper_bound_usd": _num(summary.get("max_cost_usd")) if lost else None,
        "tokens": {**tokens, "total": prompt + completion if prompt is not None and completion is not None else None},
        "tokens_complete": tokens_missing == 0,
        "replaced": {
            "attempts": replaced,
            "cost_usd": _num(summary.get("prior_attempts_cost_usd")) if replaced else 0.0,
            "cost_unknown": bool(summary.get("prior_attempts_cost_unknown")) if replaced else False,
        },
    }


# --------------------------------------------------------------------------
# Line scope (line-attributed modified files)
# --------------------------------------------------------------------------


def _line_scopes(manifest: dict[str, Any], measured_origins: set[str]) -> dict[str, Any]:
    """path -> predicate(line) for measured files attributed line by line.

    A line of such a file is measured when its origin (``modified`` for the
    stored changed lines, else ``scaffold``) is a measured origin - the rule
    ``sample`` sizes the denominator with. Files absent here are measured
    whole.
    """
    out: dict[str, Any] = {}
    for path, entry in _measured_files(manifest).items():
        if entry.get("origin") != "modified" or entry.get("attribution") != "line":
            continue
        changed = expand_ranges(entry.get("changed_line_ranges"))
        if changed is None:
            continue
        with_changed = "modified" in measured_origins
        with_unchanged = "scaffold" in measured_origins
        out[path] = lambda line, c=changed, m=with_changed, u=with_unchanged: m if line in c else u
    return out


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def _function_entries(metrics: dict[str, Any]) -> list[dict[str, Any]] | None:
    functions = metrics.get("functions")
    if not isinstance(functions, list):
        return None
    out = []
    for fn in functions:
        if not isinstance(fn, dict):
            continue
        path = fn.get("path") or fn.get("file") or fn.get("filename")
        value = _num(fn.get("complexity", fn.get("ccn", fn.get("cyclomatic_complexity"))))
        if path and value is not None:
            line = fn.get("line")
            end = fn.get("end_line")
            line = line if isinstance(line, int) and not isinstance(line, bool) else None
            valid_end = isinstance(end, int) and not isinstance(end, bool) and line is not None and end >= line
            end = end if valid_end else line
            out.append({"path": finding_path(str(path)), "complexity": value, "line": line, "end_line": end})
    return out


def _function_in_scope(fn: dict[str, Any], scope) -> bool:
    """A function counts when one of its lines is a measured line (no line range: it counts)."""
    if scope is None or fn["line"] is None:
        return True
    return any(scope(line) for line in range(fn["line"], fn["end_line"] + 1))


def _complexity_section(
    results_by_slug: dict[str, Any],
    measured: dict[str, dict[str, Any]],
    line_scopes: dict[str, Any] | None = None,
) -> dict | None:
    """Complexity from the plan's first complexity tool only.

    A planned tool that did not succeed means NA: falling back to the next
    instrument (radon: Python only) would silently change what the outcome
    measures within one plan.
    """
    line_scopes = line_scopes or {}
    for slug in COMPLEXITY_TOOLS:
        result = results_by_slug.get(slug)
        if result is None:
            continue
        if result.status != "succeeded":
            return None
        metrics = result.metrics or {}
        functions = _function_entries(metrics)
        if functions is not None:
            kept = [
                fn
                for fn in functions
                if fn["path"] in measured and _function_in_scope(fn, line_scopes.get(fn["path"]))
            ]
            values = [fn["complexity"] for fn in kept]
            ranks = Counter(_rank(v) for v in values)
            return {
                "source": slug,
                "scope": "measured_files",
                "attribution": "line_level" if any(fn["path"] in line_scopes for fn in functions) else "file_level",
                "functions": len(values),
                "max": max(values) if values else None,
                "mean": round(sum(values) / len(values), 4) if values else None,
                "p90": round(_percentile(values, 0.9), 4) if values else None,
                "rank_distribution": {letter: ranks.get(letter, 0) for letter in "ABCDEF"},
            }
        maximum = _num(metrics.get("max", metrics.get("max_complexity")))
        mean = _num(metrics.get("mean", metrics.get("average_complexity")))
        if maximum is None and mean is None:
            return None
        return {
            "source": slug,
            "scope": "all_scanned",
            "functions": metrics.get("functions_count") or metrics.get("total_blocks"),
            "max": maximum,
            "mean": mean,
            "p90": _num(metrics.get("p90")),
            "rank_distribution": metrics.get("rank_distribution") or None,
        }
    return None


def _clone_lines(findings: list[dict[str, Any]], slug: str) -> dict[str, set[int]]:
    """path -> lines covered by the tool's clone findings (both sides of each clone)."""
    covered: dict[str, set[int]] = {}
    for f in findings:
        if f["tool_slug"] != slug or f["is_ai"]:
            continue
        start, end = f["line_number"], f["end_line"]
        if isinstance(start, int):
            stop = end if isinstance(end, int) and end >= start else start
            covered.setdefault(finding_path(f["file_path"] or ""), set()).update(range(start, stop + 1))
        extra = f.get("tool_specific_data") if isinstance(f.get("tool_specific_data"), dict) else {}
        other, s2, e2 = extra.get("second_file"), extra.get("second_start"), extra.get("second_end")
        if other and isinstance(s2, int):
            stop = e2 if isinstance(e2, int) and e2 >= s2 else s2
            covered.setdefault(finding_path(str(other)), set()).update(range(s2, stop + 1))
    return covered


def _duplication_section(
    results_by_slug: dict[str, Any],
    measured: dict[str, dict[str, Any]],
    line_scopes: dict[str, Any] | None = None,
    findings: list[dict[str, Any]] | None = None,
) -> dict | None:
    line_scopes = line_scopes or {}
    for slug in DUPLICATION_TOOLS:
        result = results_by_slug.get(slug)
        if result is None or result.status != "succeeded":
            continue
        metrics = result.metrics or {}
        per_file = metrics.get("files")
        if isinstance(per_file, dict):
            covered = _clone_lines(findings or [], slug) if line_scopes else {}
            lines = 0
            duplicated = 0
            line_level = False
            for path, entry in per_file.items():
                clean = finding_path(str(path))
                if clean not in measured or not isinstance(entry, dict):
                    continue
                file_lines = int(_num(entry.get("lines")) or 0)
                file_duplicated = int(_num(entry.get("duplicated_lines")) or 0)
                scope = line_scopes.get(clean)
                clones = {n for n in covered.get(clean, set()) if 1 <= n <= file_lines}
                if scope is not None and len(clones) == file_duplicated:
                    # The clone findings reproduce jscpd's own count for the
                    # file, so the duplicated lines can be restricted to the
                    # measured lines (scaffold lines of a modified file excluded).
                    line_level = True
                    lines += sum(1 for n in range(1, file_lines + 1) if scope(n))
                    duplicated += sum(1 for n in clones if scope(n))
                else:
                    lines += file_lines
                    duplicated += file_duplicated
            return {
                "source": slug,
                "scope": "measured_files",
                "attribution": "line_level" if line_level else "file_level",
                "percentage": round(100.0 * duplicated / lines, 4) if lines else None,
                "clones": metrics.get("clones"),
                "duplicated_lines": duplicated,
                "total_lines": lines,
            }
        percentage = _num(metrics.get("percentage", metrics.get("duplication_percentage")))
        return {
            "source": slug,
            "scope": "all_scanned",
            "percentage": percentage,
            "clones": metrics.get("clones"),
            "duplicated_lines": metrics.get("duplicated_lines"),
            "total_lines": metrics.get("total_lines"),
        }
    return None


def _coverage_section(results: list) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for result in results:
        coverage = result.coverage or {}
        in_scope = coverage.get("files_in_scope")
        if not isinstance(in_scope, int):
            selected = coverage.get("selected")
            in_scope = len(selected) if isinstance(selected, list) else None
        scanned = coverage.get("files_scanned")
        if not isinstance(scanned, int):
            listed = coverage.get("scanned")
            scanned = len(listed) if isinstance(listed, list) else None
        unparseable = coverage.get("unparseable")
        out[result.tool_slug] = {
            "status": result.status,
            "files_in_scope": in_scope,
            "files_scanned": scanned,
            "unparseable": len(unparseable) if isinstance(unparseable, list) else 0,
        }
    return out


def _parse_coverage(
    static_results: list,
    manifest: dict[str, Any],
    measured_sloc: int | None,
    max_share: float | None = None,
) -> dict[str, Any]:
    """Measured files some static tool could not parse, and their share of the measured SLOC.

    ``exceeds_limit``: the share is above *max_share* (the static outcomes are then NA).
    """
    paths: set[str] = set()
    by_tool: dict[str, list[str]] = {}
    for result in static_results:
        for entry in (result.coverage or {}).get("unparseable") or []:
            path = entry.get("path") if isinstance(entry, dict) else entry
            if path:
                clean = finding_path(str(path))
                paths.add(clean)
                by_tool.setdefault(result.tool_slug, []).append(clean)
    measured = _measured_files(manifest)
    lost = sum(int(measured[p].get("measured_sloc") or 0) for p in paths if p in measured)
    share = round(lost / measured_sloc, 4) if measured_sloc else None
    limit = _num(max_share)
    return {
        "unparseable_files": sorted(paths),
        "unparseable_by_tool": {slug: sorted(set(v)) for slug, v in sorted(by_tool.items())},
        "share_unparseable_sloc": share,
        "max_unparseable_share": limit,
        "exceeds_limit": bool(share is not None and limit is not None and share > limit),
    }


# --------------------------------------------------------------------------
# Integrity: findings vs the stored manifest and the plan
# --------------------------------------------------------------------------


def _attribution_mode(manifest: dict[str, Any]) -> str:
    """line_level | whole_file | disabled | not_applicable | unknown (from the stored manifest)."""
    summary = manifest.get("attribution")
    if isinstance(summary, dict) and summary.get("mode"):
        return str(summary["mode"])
    entries = [e for e in manifest.get("files") or [] if isinstance(e, dict)]
    if not entries:
        return "unknown"
    modified = [e for e in entries if e.get("included") and e.get("origin") == "modified"]
    if not modified:
        return "not_applicable"
    return "whole_file" if any(e.get("changed_lines") is None for e in modified) else "line_level"


def _attribution_mismatches(
    manifest: dict[str, Any],
    findings: list[dict[str, Any]],
    measured_origins: set[str],
) -> dict[str, list[str]] | None:
    """Findings attributed differently from the stored manifest.

    The denominator was sized at submit from the stored attribution; the
    findings were attributed against the sample re-resolved at execution
    (which may have lost its scaffold baseline, or got another one).
    ``{"files": paths with any line-origin disagreement, "static"/"ai": paths
    where it changes whether a finding is in the measured scope}`` - only the
    latter puts numerator and denominator over different lines. None when the
    manifest predates the stored line ranges (cannot be checked).
    """
    entries = {e["path"]: e for e in manifest.get("files") or [] if isinstance(e, dict) and e.get("path")}
    if not entries or not any("attribution" in e for e in entries.values()):
        return None
    ranges: dict[str, set[int] | None] = {}
    differing: set[str] = set()
    out: dict[str, set[str]] = {"static": set(), "ai": set()}
    for f in findings:
        path = f["file_path"] or ""
        entry = entries.get(path)
        if entry is None:
            continue
        origin = entry.get("origin") or "unknown"
        expected = origin
        if origin == "modified" and entry.get("attribution") == "line":
            if path not in ranges:
                ranges[path] = expand_ranges(entry.get("changed_line_ranges"))
            changed = ranges[path]
            line = f["line_number"]
            if changed is not None and isinstance(line, int):
                expected = "modified" if line in changed else "scaffold"
        if f["file_origin"] == expected:
            continue
        differing.add(path)
        expected_in_scope = bool(entry.get("included") and entry.get("measured") and expected in measured_origins)
        if bool(f["in_measured_scope"]) != expected_in_scope:
            out["ai" if f["is_ai"] else "static"].add(path)
    return {"files": sorted(differing), **{key: sorted(paths) for key, paths in out.items()}}


def _mapping_drift(plan: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, bool] | None:
    """{static, ai}: whether findings were mapped under tables other than the plan's.

    None when it cannot be checked (a plan without ``mapping_hashes``, or no
    finding carrying the ``mapping_digest`` stamp).
    """
    hashes = plan.get("mapping_hashes")
    if not isinstance(hashes, dict) or not hashes:
        return None
    expected = mapping_digest(hashes)
    out = {"static": False, "ai": False}
    checked = False
    for f in findings:
        extra = f.get("tool_specific_data") if isinstance(f.get("tool_specific_data"), dict) else {}
        stamp = extra.get("mapping_digest")
        if not stamp:
            continue
        checked = True
        if stamp != expected:
            out["ai" if f["is_ai"] else "static"] = True
    return out if checked else None


def _functional(meta: dict[str, Any]) -> dict[str, Any]:
    functional = meta.get("functional") if isinstance(meta.get("functional"), dict) else {}
    status = functional.get("status") or "not_evaluated"
    rate = _num(functional.get("pass_rate"))
    if status in ("infra_error", "not_applicable", "not_evaluated") or rate is None or not 0.0 <= rate <= 1.0:
        rate = None
    return {
        "status": status,
        "pass_rate": rate,
        "source": functional.get("source"),
        "evaluator_version": functional.get("evaluator_version"),
    }


def _integrity(run, findings: list[dict[str, Any]], static_results: list, cfg: dict[str, Any]) -> dict[str, Any]:
    """Checks that make outcomes NA: parse coverage, attribution and mapping consistency."""
    plan = run.plan or {}
    manifest = run.sample_manifest if isinstance(run.sample_manifest, dict) else {}
    measured_sloc = _sloc_section(manifest)["measured"]
    measured_origins = set(effective_scope(plan.get("scope")).get("measured_origins") or ())
    parse = _parse_coverage(static_results, manifest, measured_sloc, cfg.get("max_unparseable_share"))
    attribution = _attribution_mismatches(manifest, findings, measured_origins)
    drift = _mapping_drift(plan, findings)
    static: list[str] = []
    ai: list[str] = []
    if parse["exceeds_limit"]:
        static.append("unparseable_code")
    if attribution is not None and attribution["static"]:
        static.append("attribution_mismatch")
    if attribution is not None and attribution["ai"]:
        ai.append("attribution_mismatch")
    if drift is not None and drift["static"]:
        static.append("mapping_drift")
    if drift is not None and drift["ai"]:
        ai.append("mapping_drift")
    return {
        "parse": parse,
        "attribution_mode": _attribution_mode(manifest),
        "attribution": attribution,
        "mapping_drift": drift,
        "static_blockers": static,
        "ai_blockers": ai,
        "measured_origins": measured_origins,
    }


def _flags(run, meta: dict[str, Any], manifest: dict[str, Any], integrity: dict[str, Any]) -> dict[str, Any]:
    validity = meta.get("validity") if isinstance(meta.get("validity"), dict) else {}
    rules = None
    study = getattr(run, "study", None) if getattr(run, "study_id", None) else None
    if study is not None and isinstance(study.exclusion_rules, dict):
        rules = study.exclusion_rules
    excluded: list[str] = []
    if meta:
        eligibility = check_eligibility(meta, None, rules, manifest=manifest)
        excluded = [r["code"] for r in eligibility["reasons"] if r["severity"] == "exclude"]
    truncation = truncation_flags(meta)
    attribution = integrity["attribution"]
    drift = integrity["mapping_drift"]
    return {
        # Output truncation only; a run stopped at a run-level limit is run_stopped.
        "truncated": truncation["truncated"],
        "truncation_reasons": truncation["truncation_reasons"],
        "run_stopped": truncation["run_stopped"],
        "run_stop_reasons": truncation["run_stop_reasons"],
        "empty_output": bool(meta.get("empty_output")),
        "validity": {"valid": validity.get("valid", True), "exclude": bool(validity.get("exclude"))},
        "excluded_reasons": excluded,
        # Static outcomes NA: too much of the measured code could not be parsed.
        "unparseable_code": bool(integrity["parse"]["exceeds_limit"]),
        "attribution": integrity["attribution_mode"],
        # None = not checkable (a manifest without stored line ranges / a plan without mapping hashes).
        "attribution_mismatch": None if attribution is None else bool(attribution["static"] or attribution["ai"]),
        "mapping_drift": None if drift is None else bool(drift["static"] or drift["ai"]),
    }


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def engine_versions() -> dict[str, str]:
    """The engine stage versions of the code computing a measurement (``engine/types.py``).

    Stamped on every measurement so a study can detect runs processed by
    another engine than its plan froze (``plan.engine_versions``): the plan
    records the versions at lock time, the runs are normalized, deduplicated
    and measured by whatever is deployed when they finalize (a retry
    re-finalizes with today's code). The sample manifest keeps the resolver
    and SLOC versions it was built with (``resolver_version`` / ``sloc_version``).
    """
    return {
        "sample_resolver": SAMPLE_RESOLVER_VERSION,
        "sloc": SLOC_VERSION,
        "normalizer": NORMALIZER_VERSION,
        "dedup": DEDUP_VERSION,
        "measurement": MEASUREMENT_VERSION,
    }


def compute(run) -> dict[str, Any]:
    """Build AnalysisRun.measurement from its results, findings, review samples,
    sample manifest, plan and target_meta."""
    results, findings, samples = _load(run)
    plan = run.plan or {}
    cfg = outcome_config(plan)
    manifest = run.sample_manifest if isinstance(run.sample_manifest, dict) else {}
    meta = run.target_meta if isinstance(run.target_meta, dict) else {}
    sloc = _sloc_section(manifest)
    measured_sloc = sloc["measured"]
    minimum = int(cfg.get("min_sloc_for_density") or 0)

    static_results = [r for r in results if not _is_ai_result(r)]
    ai_result = next((r for r in results if _is_ai_result(r)), None)
    required_failed = [r.tool_slug for r in results if r.required and r.status not in OK_STATUSES]
    results_by_slug = {r.tool_slug: r for r in results}
    measured_files = _measured_files(manifest)
    integrity = _integrity(run, findings, static_results, cfg)
    line_scopes = _line_scopes(manifest, integrity["measured_origins"])
    measured_languages = {str(e.get("language") or "other") for e in measured_files.values()} if manifest else None
    tool_languages = {
        t.get("slug"): t.get("languages") for t in plan.get("tools") or [] if isinstance(t, dict) and t.get("slug")
    }

    static = _static_section(
        static_results,
        findings,
        measured_sloc,
        cfg,
        tool_languages=tool_languages,
        measured_languages=measured_languages,
        blockers=integrity["static_blockers"],
    )
    static_counted = [f for f in findings if not f["is_ai"] and f["counted"]]
    totals = _manifest_totals(manifest)
    markers = totals.get("suppression_markers") if isinstance(totals.get("suppression_markers"), int) else None
    return {
        "schema_version": MEASUREMENT_SCHEMA_VERSION,
        "version": MEASUREMENT_VERSION,
        "engine_versions": engine_versions(),
        "plan_hash": run.plan_hash,
        "sample_hash": run.sample_hash,
        "complete": not required_failed and bool(results),
        "required_failed": required_failed,
        "tool_status": {r.tool_slug: r.status for r in results},
        "sloc": sloc,
        "files": _files_section(manifest),
        "static": static,
        "ai": _ai_section(
            run,
            ai_result,
            findings,
            samples,
            static_counted,
            measured_sloc,
            cfg,
            manifest=manifest,
            blockers=integrity["ai_blockers"],
        ),
        "metrics": {
            "complexity": _complexity_section(results_by_slug, measured_files, line_scopes),
            "duplication": _duplication_section(results_by_slug, measured_files, line_scopes, findings),
        },
        "coverage": _coverage_section(results),
        "parse_coverage": integrity["parse"],
        "suppression_markers": markers,
        "suppression_markers_per_kloc": _density(markers, measured_sloc, minimum),
        "functional": _functional(meta),
        "flags": _flags(run, meta, manifest, integrity),
        "integrity": {
            "attribution": integrity["attribution"],
            "mapping_drift": integrity["mapping_drift"],
        },
        "outcome_config": {
            "min_sloc_for_density": minimum,
            "count_style": bool(cfg.get("count_style")),
            "count_metric_findings": bool(cfg.get("count_metric_findings")),
            "max_unparseable_share": _num(cfg.get("max_unparseable_share")),
            "severity_weights": cfg["severity_weights"],
        },
    }


def summarize(run) -> dict[str, Any]:
    """Build AnalysisRun.summary (UI-oriented, schema_version 2; keeps the v1
    keys total_findings / severity_counts / tools_run / tools_completed /
    tools_failed for automation thresholds and exports)."""
    results, findings, samples = _load(run)
    manifest = run.sample_manifest if isinstance(run.sample_manifest, dict) else {}
    meta = run.target_meta if isinstance(run.target_meta, dict) else {}
    statuses = Counter(r.status for r in results)
    tools = {"total": len(results), **{s: statuses.get(s, 0) for s in TOOL_STATUSES}}

    static_results = [r for r in results if not _is_ai_result(r)]
    # Static counts are measured only when a static tool actually scanned
    # something: with every static tool failed / not applicable (or none
    # planned) a zero would read as a clean scan, so they are None.
    static_measured = any(r.status == "succeeded" for r in static_results)
    static_counted = [f for f in findings if not f["is_ai"] and f["counted"]]
    severity_counts = _zero_severities()
    for f in static_counted:
        severity_counts[f["severity"] if f["severity"] in severity_counts else "info"] += 1
    ai_counted = [f for f in findings if f["is_ai"] and f["counted"]]
    clusters = [dedup.issue_summary(m, mode="consensus") for m in dedup.group_issues(ai_counted).values()]
    ai_severity_counts = _zero_severities()
    for cluster in clusters:
        ai_severity_counts[cluster["severity"] if cluster["severity"] in ai_severity_counts else "info"] += 1
    ai_results = [r for r in results if _is_ai_result(r)]
    spends = [_ai_spend(r, [s for s in samples if s["result_id"] == r.id]) for r in ai_results]
    known_costs = [s["cost_usd"] for s in spends if s["cost_usd"] is not None]
    if known_costs:
        cost: float | None = round(math.fsum(known_costs), 6)
    else:
        # No AI review in the plan: nothing was spent. An AI review without a known spend: unknown.
        cost = None if ai_results else 0.0

    warnings: list[str] = []
    for result in results:
        if result.status in ("failed", "timed_out", "cancelled", "skipped"):
            reason = f" ({result.failure_reason})" if result.failure_reason else ""
            detail = f": {result.error_message[:200]}" if result.error_message else ""
            required = " [required]" if result.required else ""
            warnings.append(f"{result.tool_slug}{required} {result.status}{reason}{detail}")
    if meta.get("empty_output"):
        warnings.append("empty output: the model wrote no source code")
    truncation = truncation_flags(meta)
    if truncation["truncated"]:
        reasons = ", ".join(truncation["truncation_reasons"]) or "truncated"
        warnings.append(f"generation output truncated: {reasons}")
    if truncation["run_stopped"]:
        reasons = ", ".join(truncation["run_stop_reasons"]) or "stopped"
        warnings.append(f"agent run ended at a run-level limit: {reasons}")
    if (meta.get("validity") or {}).get("exclude"):
        warnings.append("sample validity: exclude")
    if static_results and not static_measured:
        warnings.append("no static tool succeeded: static finding counts are NA")
    integrity = _integrity(run, findings, static_results, outcome_config(run.plan))
    parse = integrity["parse"]
    if parse["exceeds_limit"]:
        warnings.append(
            f"unparseable code: {parse['share_unparseable_sloc']:.0%} of the measured SLOC could not be parsed "
            f"(limit {parse['max_unparseable_share']:.0%}): static outcomes are NA",
        )
    if integrity["attribution"] is not None and (integrity["attribution"]["static"] or integrity["attribution"]["ai"]):
        paths = sorted(set(integrity["attribution"]["static"]) | set(integrity["attribution"]["ai"]))
        warnings.append(
            "line attribution at execution differs from the stored sample manifest ("
            + ", ".join(paths[:5])
            + "): affected densities are NA",
        )
    drift = integrity["mapping_drift"]
    if drift is not None and (drift["static"] or drift["ai"]):
        warnings.append("findings were mapped with tables other than the plan's mapping_hashes: densities are NA")
    warnings.extend(str(w) for w in (manifest.get("warnings") or [])[:20])

    totals = _manifest_totals(manifest)
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "tools": tools,
        "complete": bool(results) and all(r.status in OK_STATUSES for r in results if r.required),
        "total_findings": len(static_counted) if static_measured else None,
        "severity_counts": severity_counts if static_measured else None,
        "static_measured": static_measured,
        "ai_findings": len(clusters),
        "ai_severity_counts": ai_severity_counts,
        "issues": len({f["issue_key"] or f"single:{f['id']}" for f in static_counted}) if static_measured else None,
        "sloc": {
            "measured": totals.get("measured_sloc") if isinstance(totals.get("measured_sloc"), int) else None,
            "total": totals.get("sloc") if isinstance(totals.get("sloc"), int) else None,
        },
        "cost_usd": cost,
        # False: cost_usd is a lower bound (a replicate's spend unknown) or unknown.
        "cost_complete": all(s["cost_complete"] for s in spends),
        "warnings": warnings[:50],
        "tools_run": len(results),
        "tools_completed": statuses.get("succeeded", 0) + statuses.get("not_applicable", 0),
        "tools_failed": statuses.get("failed", 0) + statuses.get("timed_out", 0),
    }


def outcomes(measurement: dict[str, Any]) -> dict[str, Any]:
    """``{outcome_key: value | None}`` for every key in OUTCOMES."""
    m = measurement if isinstance(measurement, dict) else {}
    static = m.get("static") if isinstance(m.get("static"), dict) else {}
    ai = m.get("ai") if isinstance(m.get("ai"), dict) else {}
    metrics = m.get("metrics") if isinstance(m.get("metrics"), dict) else {}
    complexity = metrics.get("complexity") if isinstance(metrics.get("complexity"), dict) else {}
    duplication = metrics.get("duplication") if isinstance(metrics.get("duplication"), dict) else {}
    functional = m.get("functional") if isinstance(m.get("functional"), dict) else {}
    sloc = m.get("sloc") if isinstance(m.get("sloc"), dict) else {}
    static_ok = bool(static.get("available"))
    ai_ok = bool(ai.get("available"))
    issues = static.get("issues") if isinstance(static.get("issues"), dict) else {}
    agreement = ai.get("agreement") if isinstance(ai.get("agreement"), dict) else {}
    values: dict[str, Any] = {
        "static_findings_per_kloc": static.get("findings_per_kloc") if static_ok else None,
        "static_issues_per_kloc": static.get("issues_per_kloc") if static_ok else None,
        "security_issues_per_kloc": static.get("security_issues_per_kloc"),
        "high_plus_issues_per_kloc": static.get("high_plus_issues_per_kloc") if static_ok else None,
        "has_high_security": static.get("has_high_security"),
        "ai_consensus_per_kloc": ai.get("per_kloc") if ai_ok else None,
        "complexity_max": _num(complexity.get("max")),
        "complexity_mean": _num(complexity.get("mean")),
        "duplication_pct": _num(duplication.get("percentage")),
        "functional_pass_rate": _num(functional.get("pass_rate")),
        "measured_sloc": sloc.get("measured") if isinstance(sloc.get("measured"), int) else None,
        "suppression_markers_per_kloc": _num(m.get("suppression_markers_per_kloc")),
        "static_issues": issues.get("total") if static_ok and isinstance(issues.get("total"), int) else None,
        "security_issues": static.get("security_issues"),
        "high_plus_issues": static.get("high_plus_issues") if static_ok else None,
        "weighted_per_kloc": static.get("weighted_per_kloc") if static_ok else None,
        "ai_grounding_rate": _num(ai.get("grounding_rate")) if ai_ok else None,
        "ai_mean_pairwise_jaccard": _num(agreement.get("mean_pairwise_jaccard")) if ai_ok else None,
    }
    return {key: values.get(key) for key in OUTCOMES}


def canonical_run_ids(job_ids=None, *, plan_hash: str | None = None, complete_only: bool = False) -> list:
    from backend.analysis.models import AnalysisRun

    return AnalysisRun.latest_ids_per_job(job_ids, plan_hash=plan_hash, complete_only=complete_only)
