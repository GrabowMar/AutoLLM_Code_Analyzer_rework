"""Pre-run protocol and post-run report builders (owner: reports stage).

Protocol (:func:`build_protocol`)
    The frozen pre-run record of a study, built at lock time from the
    study's preflight, frozen plan, target spec, exclusion rules, analysis
    design and research questions, plus the canary controls and the
    platform git SHA. :func:`protocol_hash` fingerprints it.

Report (:func:`build_report`)
    Generated from stored data only (runs, tool results, review samples,
    findings, measurements), never hand-edited. It is JSON-serializable,
    deterministic (the same data gives the same ``report_hash``;
    ``generated_at`` is not hashed) and schema-versioned::

        {"schema_version", "kind": "analysis_study_report", "generated_at", "report_hash",
         "protocol_hash", "plan_hash", "study": {...}, "settings": {...}, "summary": {...},
         "sections": [{"key", "title", "description", "notes": [str],
                       "tables": [{"key", "title", "description", "notes",
                                   "columns": [{"key", "label", "kind"}], "rows": [{...}]}],
                       "series": [{"key", "kind", "title", ...chart data...}]}]}

    Section keys (stable, in order): ``overview``, ``deviations``,
    ``data_quality``, ``sample``, ``descriptives``, ``findings_profile``,
    ``inferential``, ``ai_reliability``, ``tool_overlap``, ``cost``,
    ``provenance``. Column kinds: ``text``, ``int``, ``float``, ``pct``
    (share in [0, 1]), ``p`` (p-value), ``ci`` ([lo, hi] or null),
    ``pct_ci`` (a ci of shares), ``usd``, ``bool``, ``hash``, ``list``,
    ``status``, ``code`` (JSON-ish value shown verbatim). ``None`` always
    means missing or undefined, never zero.

    Series kinds (chart-ready): ``funnel`` (steps), ``status_matrix``
    (rows x columns tool statuses + failure reasons), ``heatmap`` (rows,
    columns, values), ``box`` / ``proportion`` (per-group distribution with
    quartiles, whiskers, outliers and raw values), ``mean_ranks``
    (Friedman), ``forest`` (Cliff's delta with CI per pair), ``bar``,
    ``upset`` (issue counts per detecting tool set) and ``scatter``
    (cost vs primary outcome).

Counting rules
    * One canonical run per target (generation job): the latest finished
      run made with the study's plan hash (else its latest run, for data
      quality only). Superseded runs are listed, never counted twice.
    * The analysis set holds canonical runs that finished (completed or
      partial), match the study plan and whose target was eligible in the
      protocol. Everything else is attrition and is reported as such.
    * Every outcome value comes from ``measurement.outcomes(run.measurement)``;
      a ``None`` outcome is missing data (complete-case analysis per
      outcome, with ``n_missing`` reported).
    * A target is its generation job: ``generation_job_id``, else the
      ``job_id`` snapshot in ``target_meta`` (the job may have been deleted).
    * Groups follow ``analysis_design.group_by`` over ``run.target_meta``
      (a condition is identified by its id; its free-text label is display only).
      The design is blocked (Friedman + Wilcoxon signed-rank on per-cell
      means, blocks = ``block_by``) only when the protocol's design was
      complete (every group has every block); otherwise Kruskal-Wallis +
      Mann-Whitney on the same per-(group, block) cell values (replicates
      are correlated, so they are aggregated, never counted as independent
      runs; a run without a known block is its own unit; without a
      blocking factor every run is a unit). Pairwise p-values are Holm-adjusted within an
      outcome; omnibus p-values are Holm-adjusted across confirmatory
      outcomes and BH-adjusted across exploratory ones. Blocked designs also
      get an unblocked Kruskal-Wallis sensitivity table.
    * A locked protocol's design settings win over later edits of
      ``analysis_design`` (pre-registration); a target without a known block
      is never paired.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import datetime
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from typing import Any
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from backend.analysis.engine import measurement
from backend.analysis.engine.reviewer.prompt import CATEGORIES as REVIEW_CATEGORIES
from backend.analysis.engine.types import SEVERITIES
from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.models import ReviewSample
from backend.analysis.models import ToolResult
from backend.analysis.research import stats

REPORT_SCHEMA_VERSION = 1
PROTOCOL_SCHEMA_VERSION = 1
REPORT_KIND = "analysis_study_report"
PROTOCOL_KIND = "analysis_study_protocol"
# Keys left out of the report hash: they change on every regeneration of
# otherwise identical data, or (``status``) record the stored report's
# lifecycle rather than its content.
VOLATILE_REPORT_KEYS = ("generated_at", "report_hash", "status")

# Display glyphs as named escapes (ruff RUF001 flags the confusable literals).
TIMES = "\N{MULTIPLICATION SIGN}"
ALPHA = "\N{GREEK SMALL LETTER ALPHA}"
NONE_LABEL = "(none)"
ALL_LABEL = "All"
# target_meta keys a factor reads, first non-empty wins. ``condition`` is
# special-cased in :func:`factor_value`: its identity is the condition id
# (labels are free text and need not be unique), its label is display only.
FACTORS: dict[str, tuple[str, ...]] = {
    "model": ("model_slug", "model_name", "model_id"),
    "condition": ("condition_id", "condition_label"),
    "harness": ("harness",),
    "mode": ("mode",),
    "stack": ("stack_slug",),
    "app": ("app_slug", "app_name"),
}
DEFAULT_PRIMARY_OUTCOME = "static_issues_per_kloc"
DEFAULT_DESIGN: dict[str, Any] = {
    "group_by": ["model"],
    "block_by": "app",
    "alpha": 0.05,
    "correction": "holm",
    "exploratory_correction": "bh",
    "bootstrap_resamples": 10_000,
    "effect_ci_resamples": 2_000,
    "ci_level": 0.95,
    "seed": 0,
    "primary_outcome": DEFAULT_PRIMARY_OUTCOME,
    "outcomes": None,
    "cell_aggregate": "mean",
}
# Multiple-comparison corrections understood by the report (``correction`` /
# ``exploratory_correction``); the study API validates against the same tuple.
CORRECTIONS = ("holm", "bh", "none")

# Labels for the outcomes the measurement contract requires (§4.4); the
# measure stage's ``measurement.OUTCOMES`` takes precedence when present.
FALLBACK_OUTCOMES: dict[str, dict[str, Any]] = {
    "static_findings_per_kloc": {
        "label": "Static findings / KLOC",
        "kind": "rate",
        "unit": "per KLOC",
        "higher_is_better": False,
        "description": "Counted static findings per 1000 measured SLOC.",
    },
    "static_issues_per_kloc": {
        "label": "Static issues / KLOC",
        "kind": "rate",
        "unit": "per KLOC",
        "higher_is_better": False,
        "description": "Deduplicated static issues per 1000 measured SLOC.",
    },
    "security_issues_per_kloc": {
        "label": "Security issues / KLOC",
        "kind": "rate",
        "unit": "per KLOC",
        "higher_is_better": False,
        "description": "Security/secrets issues per 1000 measured SLOC.",
    },
    "high_plus_issues_per_kloc": {
        "label": "High+ issues / KLOC",
        "kind": "rate",
        "unit": "per KLOC",
        "higher_is_better": False,
        "description": "High or critical issues per 1000 measured SLOC.",
    },
    "has_high_security": {
        "label": "Has high/critical security issue",
        "kind": "binary",
        "unit": "",
        "higher_is_better": False,
        "description": "At least one high or critical security issue.",
    },
    "ai_consensus_per_kloc": {
        "label": "AI consensus findings / KLOC",
        "kind": "rate",
        "unit": "per KLOC",
        "higher_is_better": False,
        "description": "Grounded AI consensus findings per 1000 measured SLOC.",
    },
    "complexity_max": {
        "label": "Max cyclomatic complexity",
        "kind": "metric",
        "unit": "",
        "higher_is_better": False,
        "description": "Highest function complexity.",
    },
    "complexity_mean": {
        "label": "Mean cyclomatic complexity",
        "kind": "metric",
        "unit": "",
        "higher_is_better": False,
        "description": "Mean function complexity.",
    },
    "duplication_pct": {
        "label": "Duplication %",
        "kind": "metric",
        "unit": "%",
        "higher_is_better": False,
        "description": "Share of duplicated lines.",
    },
    "functional_pass_rate": {
        "label": "Functional pass rate",
        "kind": "metric",
        "unit": "share",
        "higher_is_better": True,
        "description": "Conformance-test pass rate of the generated app.",
    },
    "measured_sloc": {
        "label": "Measured SLOC",
        "kind": "count",
        "unit": "lines",
        "higher_is_better": None,
        "description": "Code lines in the measured scope.",
    },
    "suppression_markers_per_kloc": {
        "label": "Suppression markers / KLOC",
        "kind": "rate",
        "unit": "per KLOC",
        "higher_is_better": False,
        "description": "Linter/scanner suppression comments per 1000 measured SLOC.",
    },
}

TOOL_STATUSES = ("succeeded", "not_applicable", "failed", "timed_out", "skipped", "cancelled", "pending", "running")
_OK_TOOL_STATUSES = ("succeeded", "not_applicable")
SAMPLE_STATUSES = (
    "succeeded",
    "failed",
    "invalid_output",
    "truncated",
    "budget_exhausted",
    "timed_out",
    "cancelled",
    "pending",
)
GROUNDING_KINDS = ("exact", "relocated", "ungrounded", "invalid_file", "out_of_range")
_FINISHED = tuple(str(s) for s in AnalysisRun.FINISHED_STATUSES)
_SEVERITY_ORDER = {"major": 0, "warn": 1, "info": 2}
_MAX_ITEMS = 50

THREATS: list[dict[str, str]] = [
    {
        "kind": "construct",
        "threat": "Static findings are not vulnerabilities; tools produce false positives.",
        "mitigation": "Report raw findings, deduplicated issues and grounded AI consensus separately; "
        "estimate precision on a labelled sample.",
    },
    {
        "kind": "construct",
        "threat": "Platform-authored or scaffold code attributed to the model.",
        "mitigation": "Line-level origin attribution; only authored/added/modified lines are measured.",
    },
    {
        "kind": "internal",
        "threat": "LLM nondeterminism and provider variance (generator and reviewer).",
        "mitigation": "Replicates per cell, k review samples with consensus, pinned provider routing, "
        "recorded providers and seeds.",
    },
    {
        "kind": "internal",
        "threat": "Failed or timed-out tools read as clean code.",
        "mitigation": "Explicit tool statuses; a failed required tool makes dependent outcomes missing (NA).",
    },
    {
        "kind": "external",
        "threat": "App specs, stacks and model snapshots limit generalisation.",
        "mitigation": "Report the exact specs, stacks and model snapshots; do not generalise beyond them.",
    },
    {
        "kind": "conclusion",
        "threat": "Multiple comparisons and small samples.",
        "mitigation": "Pre-registered primary outcome; Holm (confirmatory) and BH (exploratory) adjustment; "
        "effect sizes with intervals.",
    },
]


# --------------------------------------------------------------------------
# JSON, hashing, small helpers
# --------------------------------------------------------------------------


def jsonable(value: Any) -> Any:  # noqa: PLR0911 - one return per value type
    """A JSON-safe, deterministic copy of *value*.

    UUIDs/datetimes/Decimals become strings/floats, sets become sorted
    lists, non-finite floats become ``None`` and floats are rounded to 12
    significant digits so the hash does not depend on last-bit noise.
    """
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return float(f"{value:.12g}")
    if isinstance(value, Decimal):
        return jsonable(float(value))
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((jsonable(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return str(value)


def canonical_json(value: Any) -> str:
    """Compact, key-sorted JSON (the input of every hash in this module)."""
    return json.dumps(jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_hash(value: Any, *, exclude: tuple[str, ...] = ()) -> str:
    """sha256 of :func:`canonical_json`, leaving out top-level *exclude* keys of a dict."""
    if isinstance(value, dict) and exclude:
        value = {k: v for k, v in value.items() if k not in exclude}
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def protocol_hash(protocol: dict[str, Any]) -> str:
    """Fingerprint of a frozen protocol (everything but a stored ``protocol_hash``)."""
    return content_hash(protocol, exclude=("protocol_hash",))


def report_hash(report: dict[str, Any]) -> str:
    """Fingerprint of a report's content (``generated_at`` / ``report_hash`` / ``status`` excluded)."""
    return content_hash(report, exclude=VOLATILE_REPORT_KEYS)


def protocol_integrity(study) -> dict[str, Any]:
    """``{stored, recomputed, match}`` of the study's protocol (``match`` None without protocol or stored hash)."""
    protocol = study.protocol if isinstance(study.protocol, dict) else {}
    stored = study.protocol_hash or None
    recomputed = protocol_hash(protocol) if protocol else None
    return {
        "stored": stored,
        "recomputed": recomputed,
        "match": (recomputed == stored) if protocol and stored else None,
    }


def is_report(value: Any) -> bool:
    """Whether a stored ``AnalysisStudy.report`` is a finished report document (not a lifecycle marker)."""
    return isinstance(value, dict) and value.get("kind") == REPORT_KIND and value.get("status") in (None, "ready")


def _num(value: Any) -> float | None:
    """Outcome value as float: bool -> 1/0, numbers as is, anything else missing."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return stats.to_float(value)


def as_dict(value: Any) -> dict[str, Any]:
    """*value* if it is a dict, else ``{}`` (stored JSON is never trusted to have the expected shape)."""
    return value if isinstance(value, dict) else {}


def cwe_list(value: Any) -> list[str]:
    """CWE ids of a finding as strings (tolerates a bare string instead of a list)."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return []


def _int(value: Any) -> int | None:
    f = stats.to_float(value)
    return None if f is None else int(f)


def _dig(obj: Any, *path: str, default: Any = None) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key)
    return default if obj is None else obj


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, (datetime, date)) else (str(value) if value else None)


def _label_key(label: str) -> tuple[bool, str, str]:
    return (label == NONE_LABEL, label.lower(), label)


def _sorted_labels(labels: Any) -> list[str]:
    return sorted(set(labels), key=_label_key)


def _share(part: float | None, whole: float | None) -> float | None:
    if part is None or not whole:
        return None
    return part / whole


def _limited(items: list[str]) -> list[str]:
    if len(items) <= _MAX_ITEMS:
        return items
    return [*items[:_MAX_ITEMS], f"... and {len(items) - _MAX_ITEMS} more"]


def _col(key: str, label: str, kind: str = "text") -> dict[str, str]:
    return {"key": key, "label": label, "kind": kind}


def _table(
    key: str,
    title: str,
    columns: list[dict[str, str]],
    rows: list[dict[str, Any]],
    *,
    description: str = "",
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "description": description,
        "columns": columns,
        "rows": rows,
        "notes": list(notes or []),
    }


def _section(
    key: str,
    title: str,
    *,
    description: str = "",
    tables: list[dict[str, Any]] | None = None,
    series: list[dict[str, Any]] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "description": description,
        "notes": list(notes or []),
        "tables": list(tables or []),
        "series": list(series or []),
    }


def get_section(report: dict[str, Any], key: str) -> dict[str, Any] | None:
    """The section *key* of a built report (``None`` if absent)."""
    return next((s for s in report.get("sections", []) if s.get("key") == key), None)


def get_table(report: dict[str, Any], section_key: str, table_key: str) -> dict[str, Any] | None:
    section = get_section(report, section_key)
    if section is None:
        return None
    return next((t for t in section.get("tables", []) if t.get("key") == table_key), None)


# --------------------------------------------------------------------------
# Platform provenance
# --------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _resolve_ref(git_dir: Path, common_dir: Path, ref: str) -> str | None:
    for base in (git_dir, common_dir):
        sha = _read_text(base / ref)
        if sha:
            return sha
    packed = _read_text(common_dir / "packed-refs") or ""
    for line in packed.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].strip() == ref:
            return parts[0]
    return None


def platform_git_sha() -> dict[str, Any]:
    """``{sha, ref, source}`` of the running platform code, read without spawning git.

    An env var (``LLMLAB_GIT_SHA``/``GIT_SHA``/``GIT_COMMIT``/``SOURCE_COMMIT``)
    wins (images built without ``.git``); otherwise ``.git`` is read
    directly (worktrees and packed refs included). Unreadable -> ``sha=None``.
    """
    for var in ("LLMLAB_GIT_SHA", "GIT_SHA", "GIT_COMMIT", "SOURCE_COMMIT"):
        value = os.environ.get(var, "").strip()
        if value:
            return {"sha": value, "ref": None, "source": f"env:{var}"}
    root = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[3]))
    dot_git = root / ".git"
    git_dir: Path | None = None
    if dot_git.is_dir():
        git_dir = dot_git
    elif dot_git.is_file():
        pointer = _read_text(dot_git) or ""
        if pointer.startswith("gitdir:"):
            candidate = Path(pointer.split(":", 1)[1].strip())
            git_dir = candidate if candidate.is_absolute() else (root / candidate)
    if git_dir is None or not git_dir.is_dir():
        return {"sha": None, "ref": None, "source": None}
    common = _read_text(git_dir / "commondir")
    common_dir = (git_dir / common).resolve() if common else git_dir
    head = _read_text(git_dir / "HEAD") or ""
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        return {"sha": _resolve_ref(git_dir, common_dir, ref), "ref": ref, "source": "git"}
    return {"sha": head or None, "ref": None, "source": "git"}


def engine_provenance() -> dict[str, Any]:
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "protocol_schema_version": PROTOCOL_SCHEMA_VERSION,
        "stats_version": stats.STATS_VERSION,
        "git": platform_git_sha(),
    }


# --------------------------------------------------------------------------
# Design and outcomes
# --------------------------------------------------------------------------


def resolve_design(analysis_design: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """Normalize ``AnalysisStudy.analysis_design`` (defaults filled in) + notes on ignored values."""
    raw = dict(analysis_design or {})
    design = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_DESIGN.items()}
    notes: list[str] = []

    group_by = raw.get("group_by", design["group_by"])
    if isinstance(group_by, str):
        group_by = [group_by]
    if not isinstance(group_by, list) or not group_by or any(g not in FACTORS for g in group_by):
        notes.append(f"group_by {raw.get('group_by')!r} is not a list of {sorted(FACTORS)}; using ['model'].")
        group_by = ["model"]
    design["group_by"] = list(dict.fromkeys(group_by))

    block_by = raw.get("block_by", design["block_by"])
    if block_by in ("", "none"):
        block_by = None
    if block_by is not None and block_by not in FACTORS:
        notes.append(f"block_by {block_by!r} is unknown; no blocking.")
        block_by = None
    if block_by in design["group_by"]:
        if "block_by" in raw:
            notes.append(f"block_by {block_by!r} is also a grouping factor; no blocking.")
        block_by = None
    design["block_by"] = block_by

    for key, lo, hi in (("alpha", 0.0, 0.5), ("ci_level", 0.5, 0.9999)):
        value = stats.to_float(raw.get(key, design[key]))
        if value is None or not lo < value < hi:
            notes.append(f"{key} {raw.get(key)!r} is out of range; using {design[key]}.")
        else:
            design[key] = value
    for key, hi in (("bootstrap_resamples", 100_000), ("effect_ci_resamples", 100_000)):
        value = _int(raw.get(key, design[key]))
        if value is None or not 0 <= value <= hi:
            notes.append(f"{key} {raw.get(key)!r} is out of range; using {design[key]}.")
        else:
            design[key] = value
    seed = raw.get("seed", design["seed"])
    design["seed"] = seed if isinstance(seed, int) and not isinstance(seed, bool) else 0
    for key in ("correction", "exploratory_correction"):
        value = raw.get(key, design[key])
        if value not in CORRECTIONS:
            notes.append(f"{key} {value!r} is not one of {CORRECTIONS}; using {design[key]}.")
        else:
            design[key] = value
    aggregate = raw.get("cell_aggregate", design["cell_aggregate"])
    design["cell_aggregate"] = aggregate if aggregate in ("mean", "median") else "mean"
    primary = raw.get("primary_outcome") or design["primary_outcome"]
    design["primary_outcome"] = str(primary)
    outcomes = raw.get("outcomes")
    design["outcomes"] = [str(o) for o in outcomes] if isinstance(outcomes, list) and outcomes else None
    return design, notes


def _condition_level(meta: dict[str, Any]) -> str:
    """``label (#id)``: two conditions sharing a free-text label stay two groups."""
    cid, label = meta.get("condition_id"), meta.get("condition_label")
    if cid in (None, ""):
        return str(label) if label not in (None, "") else NONE_LABEL
    return f"{label} (#{cid})" if label not in (None, "") else str(cid)


def factor_value(meta: dict[str, Any] | None, factor: str) -> str:
    """The level of *factor* for one target (``"(none)"`` when unknown)."""
    if factor == "condition":
        return _condition_level(meta or {})
    for key in FACTORS.get(factor, ()):
        value = (meta or {}).get(key)
        if value not in (None, ""):
            return str(value)
    return NONE_LABEL


def group_label(meta: dict[str, Any] | None, group_by: list[str]) -> str:
    return f" {TIMES} ".join(factor_value(meta, f) for f in group_by)


def block_label(meta: dict[str, Any] | None, block_by: str | None) -> str | None:
    """The block of one target, ``None`` when unblocked or unknown.

    An unknown block is never a shared "(none)" block: pairing runs of
    different apps would make the paired tests meaningless.
    """
    if not block_by:
        return None
    value = factor_value(meta, block_by)
    return None if value == NONE_LABEL else value


def _confirmatory_outcomes(research_questions: Any, primary: str) -> set[str]:
    keys = {primary}
    for rq in research_questions if isinstance(research_questions, list) else []:
        if not isinstance(rq, dict) or rq.get("kind") != "confirmatory":
            continue
        named = rq.get("outcomes") or ([rq["outcome"]] if rq.get("outcome") else [])
        keys.update(str(k) for k in named if k)
    return keys


def outcome_definitions(
    design: dict[str, Any],
    research_questions: Any = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Outcome definitions in report order (+ notes about unknown requested outcomes)."""
    source = dict(getattr(measurement, "OUTCOMES", None) or {}) or FALLBACK_OUTCOMES
    notes: list[str] = []
    keys = list(source)
    if design.get("outcomes"):
        unknown = [k for k in design["outcomes"] if k not in source]
        if unknown:
            notes.append(f"Unknown outcomes ignored: {', '.join(unknown)}.")
        keys = [k for k in design["outcomes"] if k in source]
    primary = design["primary_outcome"]
    if primary not in source:
        notes.append(f"Primary outcome {primary!r} is not a known outcome.")
    elif primary not in keys:
        keys.insert(0, primary)
    confirmatory = _confirmatory_outcomes(research_questions, primary)
    defs = []
    for key in keys:
        meta = dict(source[key])
        defs.append(
            {
                "key": key,
                "label": str(meta.get("label") or key),
                "kind": str(meta.get("kind") or "metric"),
                "unit": meta.get("unit") or "",
                "higher_is_better": meta.get("higher_is_better"),
                "description": meta.get("description") or "",
                "requires": list(meta.get("requires") or []),
                "primary": key == primary,
                "family": "confirmatory" if key in confirmatory else "exploratory",
            },
        )
    return defs, notes


def design_matrix(entries: list[tuple[str, str | None]], design: dict[str, Any]) -> dict[str, Any]:
    """Cell structure of (group, block) pairs and whether the blocked design is complete."""
    groups = Counter(g for g, _ in entries)
    block_by = design.get("block_by")
    blocks = Counter(b for _, b in entries if b is not None) if block_by else Counter()
    cells: dict[str, dict[str, int]] = defaultdict(dict)
    for g, b in entries:
        if block_by and b is not None:
            cells[g][b] = cells[g].get(b, 0) + 1
    group_labels = _sorted_labels(groups)
    block_labels = _sorted_labels(blocks)
    unblocked = sum(1 for _, b in entries if b is None) if block_by else 0
    if not block_by:
        complete, reason = False, "no blocking factor"
    elif len(group_labels) < 2:
        complete, reason = False, "fewer than 2 groups"
    elif unblocked:
        complete, reason = False, f"{unblocked} target(s) without a known {block_by}"
    elif len(block_labels) < 2:
        complete, reason = False, f"fewer than 2 {block_by} blocks"
    else:
        missing = [(g, b) for g in group_labels for b in block_labels if b not in cells.get(g, {})]
        complete = not missing
        reason = "every group has every block" if complete else f"{len(missing)} empty group {TIMES} block cells"
    replicates = [n for g in cells.values() for n in g.values()]
    return {
        "group_by": list(design["group_by"]),
        "block_by": block_by,
        "groups": {g: groups[g] for g in group_labels},
        "blocks": {b: blocks[b] for b in block_labels},
        "cells": {g: {b: cells[g][b] for b in _sorted_labels(cells[g])} for g in group_labels if g in cells},
        "complete": complete,
        "mode": "blocked" if complete else "independent",
        "reason": reason,
        "replicates": {"min": min(replicates), "max": max(replicates)} if replicates else None,
    }


def inferential_unit(design: dict[str, Any], mode: str) -> str:
    """What one value in an omnibus/pairwise test is."""
    block_by = design.get("block_by")
    if not block_by:
        return "run (no blocking factor: replicates are not identified)"
    unknown = (
        f"runs without a known {block_by} are left out of paired tests"
        if mode == "blocked"
        else f"a run without a known {block_by} is its own unit"
    )
    return f"group {TIMES} {block_by} cell ({design['cell_aggregate']} of its replicates); {unknown}"


def _analysis_plan(design: dict[str, Any], mode: str) -> dict[str, Any]:
    blocked = mode == "blocked"
    return {
        "unit": "run (one canonical run per target)",
        # Replicates of one group x block cell share the generator's spec-specific behaviour, so
        # the tests use one aggregated value per cell in both modes (never runs as independent).
        "test_unit": inferential_unit(design, mode),
        "mode": mode,
        "omnibus": "friedman" if blocked else "kruskal_wallis",
        "omnibus_effect_size": "kendalls_w" if blocked else "epsilon_squared",
        "pairwise": "wilcoxon_signed_rank" if blocked else "mann_whitney_u",
        "pairwise_effect_sizes": ["cliffs_delta", "vargha_delaney_a12"]
        + (["matched_pairs_rank_biserial"] if blocked else []),
        "cell_aggregate": design["cell_aggregate"] if blocked or design.get("block_by") else None,
        "alpha": design["alpha"],
        "pairwise_correction": design["correction"],
        "confirmatory_correction": "holm",
        "exploratory_correction": design["exploratory_correction"],
        "descriptives": "n, n_missing, median, IQR, mean with percentile bootstrap CI",
        "ci": {
            "method": "percentile bootstrap",
            "level": design["ci_level"],
            "resamples": design["bootstrap_resamples"],
            "cluster": design["block_by"],
            "seed": design["seed"],
        },
        "effect_ci": {
            "method": "percentile bootstrap of Cliff's delta (primary outcome)",
            "resamples": design["effect_ci_resamples"],
        },
        "proportions": "Wilson score interval",
        "reliability": "Krippendorff's alpha (nominal) on per-(target, file, category) presence across AI samples",
        "missing_data": "complete-case per outcome; missing outcomes are NA (never 0) and counted as n_missing",
        "magnitude_thresholds": {label: t for t, label in stats.CLIFF_THRESHOLDS},
    }


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------


def _effective_exclusion_rules(rules: dict[str, Any] | None) -> dict[str, Any]:
    from backend.analysis.engine.sample import DEFAULT_EXCLUSION_RULES

    return {**DEFAULT_EXCLUSION_RULES, **(rules or {})}


def _target_entry(target: dict[str, Any], design: dict[str, Any]) -> dict[str, Any]:
    meta = dict(as_dict(target.get("meta")))
    block_by = design.get("block_by")
    # Protocol entries keep only the identity part of target_meta (no quality payloads).
    return {
        "job_id": str(target.get("job_id") or ""),
        "label": target.get("label") or "",
        "eligible": bool(target.get("eligible")),
        "reasons": list(target.get("reasons") or []),
        "group": group_label(meta, design["group_by"]),
        "block": block_label(meta, block_by),
        "meta": {
            k: meta.get(k)
            for k in (
                "mode",
                "model_id",
                "model_slug",
                "model_name",
                "app_name",
                "generator_family",
                "harness",
                "harness_version",
                "stack_slug",
                "app_slug",
                "experiment_id",
                "condition_id",
                "condition_label",
                "batch_id",
                "repeat_index",
                "prompt_hash",
                "bundle_key",
            )
            if k in meta
        },
        "sample_hash": target.get("sample_hash") or "",
        "files": target.get("files"),
        "sloc_total": target.get("sloc_total"),
        "sloc_measured": target.get("sloc_measured"),
        "languages": target.get("languages") or {},
        "tools": target.get("tools") or {},
    }


def _toolchain(plan: dict[str, Any]) -> list[dict[str, Any]]:
    keys = ("slug", "name", "kind", "category", "required", "version", "config_hash", "spec_hash", "languages")
    return [{k: t.get(k) for k in keys} for t in plan.get("tools") or [] if isinstance(t, dict)]


def _overall_passed(verdicts: list[Any]) -> bool | None:
    """All controls passed: True only when every planned tool passed; any failure is False, else None.

    A tool without evidence (``None``: never self-tested, no expectations, or
    only another configuration was self-tested) never counts as a pass.
    """
    if not verdicts:
        return None
    if any(v is False for v in verdicts):
        return False
    return True if all(v is True for v in verdicts) else None


def _control_row(slug: Any, status: Any, canary: dict[str, Any], config_hash: Any) -> dict[str, Any]:
    other = canary.get("other_config") if isinstance(canary.get("other_config"), dict) else None
    passed = canary.get("passed")
    row = {
        "slug": slug,
        "status": status,
        "passed": passed,
        "verdict": canary.get("verdict") or {True: "passed", False: "failed"}.get(passed, "untested"),
        "config_hash": config_hash or canary.get("config_hash") or "",
        "missing": canary.get("missing") or [],
        "unexpected_in_clean": canary.get("unexpected_in_clean") or [],
    }
    if other:
        row["other_config"] = {k: other.get(k) for k in ("run_id", "config_hash", "verdict", "evaluated_at")}
    return row


def _controls(preflight: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Canary results of the plan's tools at the plan's configurations.

    The preflight's per-tool canary rows are the evidence whenever the
    preflight listed tools: a row with ``passed: None`` (``config_untested``,
    ``untested``) stays no evidence and keeps the overall ``passed`` from
    being True. Only a preflight without tool rows falls back to the newest
    self-tests of the plan's image, matched on each tool's ``config_hash``
    (a default-config self-test never vouches for a narrowed configuration).
    """
    plan_tools = [t for t in plan.get("tools") or [] if isinstance(t, dict) and t.get("slug")]
    hashes = {str(t["slug"]): str(t.get("config_hash") or "") for t in plan_tools}
    tools = [t for t in preflight.get("tools") or [] if isinstance(t, dict)]
    rows = []
    for t in tools:
        canary = t.get("canary") if isinstance(t.get("canary"), dict) else {}
        rows.append(_control_row(t.get("slug"), t.get("status"), canary, t.get("config_hash")))
    if rows:
        return {"source": "preflight", "passed": _overall_passed([r["passed"] for r in rows]), "tools": rows}
    if not hashes:
        return {"source": None, "passed": None, "tools": []}
    try:
        from backend.analysis.engine import canary as canary_mod

        per_tool = canary_mod.latest_per_tool(_dig(plan, "analyzer_image", "tag") or None, config_hashes=hashes)
    except Exception:  # noqa: BLE001 - controls are optional evidence; never block the protocol on them
        return {"source": None, "passed": None, "tools": []}
    per_tool = per_tool if isinstance(per_tool, dict) else {}
    rows = [
        _control_row(
            slug,
            (per_tool.get(slug) or {}).get("status") or "untested",
            per_tool.get(slug) or {"passed": None, "verdict": "untested"},
            config_hash,
        )
        for slug, config_hash in hashes.items()
    ]
    return {
        "source": "canary.latest_per_tool",
        "passed": _overall_passed([r["passed"] for r in rows]),
        "tools": rows,
    }


def _research_questions(value: Any) -> list[dict[str, Any]]:
    out = []
    for i, rq in enumerate(value if isinstance(value, list) else []):
        if isinstance(rq, dict):
            kind = rq.get("kind") if rq.get("kind") in ("confirmatory", "exploratory") else "exploratory"
            out.append({**rq, "id": rq.get("id") or f"RQ{i + 1}", "kind": kind, "text": rq.get("text") or ""})
        elif isinstance(rq, str):
            out.append({"id": f"RQ{i + 1}", "text": rq, "kind": "exploratory"})
    return out


def build_protocol(study) -> dict[str, Any]:
    """The frozen pre-run protocol of *study* (call at lock time; hash with :func:`protocol_hash`)."""
    design, design_notes = resolve_design(study.analysis_design)
    preflight = study.preflight or {}
    plan = study.plan or {}
    targets = [_target_entry(t, design) for t in preflight.get("targets") or [] if isinstance(t, dict)]
    targets.sort(key=lambda t: (_label_key(t["group"]), _label_key(t["block"] or ""), t["label"], t["job_id"]))
    excluded = [t for t in targets if not t["eligible"]]
    reason_counts = Counter(
        str(r.get("code") or "unknown")
        for t in excluded
        for r in t["reasons"]
        if isinstance(r, dict) and r.get("severity", "exclude") == "exclude"
    )
    eligible = [t for t in targets if t["eligible"]]
    matrix = design_matrix([(t["group"], t["block"]) for t in eligible], design)
    outcome_defs, outcome_notes = outcome_definitions(design, study.research_questions)
    reviewer = plan.get("reviewer") if isinstance(plan.get("reviewer"), dict) else None
    protocol = {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "kind": PROTOCOL_KIND,
        "generated_at": timezone.now().isoformat(),
        "study": {
            "id": str(study.id),
            "name": study.name,
            "description": study.description,
            # The researcher by id and display name only (no e-mail in a shareable document).
            "researcher": {"id": study.created_by_id, "name": getattr(study.created_by, "name", "") or ""},
            "created_at": _iso(study.created_at),
        },
        "research_questions": _research_questions(study.research_questions),
        "targets": {
            "spec": study.target_spec or {},
            "resolved": targets,
            "counts": {
                "total": len(targets),
                "eligible": len(eligible),
                "excluded": len(excluded),
                "by_reason": dict(sorted(reason_counts.items())),
            },
        },
        "exclusion_rules": _effective_exclusion_rules(study.exclusion_rules),
        "design": {**matrix, "settings": design, "notes": design_notes},
        "plan_hash": study.plan_hash or plan.get("plan_hash") or "",
        "plan": plan,
        "toolchain": _toolchain(plan),
        "analyzer_image": plan.get("analyzer_image") or {},
        "engine_versions": plan.get("engine_versions") or {},
        "reviewer": reviewer,
        "controls": _controls(preflight, plan),
        "outcomes": {
            "primary": design["primary_outcome"],
            "confirmatory": [d["key"] for d in outcome_defs if d["family"] == "confirmatory"],
            "exploratory": [d["key"] for d in outcome_defs if d["family"] == "exploratory"],
            "definitions": outcome_defs,
            "config": plan.get("outcomes") or {},
            "notes": outcome_notes,
        },
        "analysis_plan": _analysis_plan(design, matrix["mode"]),
        "estimates": {
            **(preflight.get("estimates") or {}),
            "reviewer_cost": _dig(preflight, "reviewer", "cost_estimate"),
            "budget": _dig(reviewer or {}, "budget"),
        },
        "comparability": preflight.get("comparability") or {},
        "preflight": {
            "generated_at": preflight.get("generated_at"),
            "plan_hash": preflight.get("plan_hash"),
            "blocking": preflight.get("blocking") or [],
            "warnings": preflight.get("warnings") or [],
            "info": preflight.get("info") or [],
            "image": preflight.get("image") or {},
        },
        "threats": THREATS,
        "platform": engine_provenance(),
        "timestamps": {
            "created_at": _iso(study.created_at),
            "preflight_generated_at": preflight.get("generated_at"),
            "locked_at": _iso(study.locked_at),
        },
    }
    return jsonable(protocol)


# --------------------------------------------------------------------------
# Loading a study's data
# --------------------------------------------------------------------------


@dataclass
class TargetRecord:
    """One analysis target (a generation job) and the runs made for it."""

    key: str
    job_id: str | None
    label: str
    group: str
    block: str | None
    meta: dict[str, Any]
    runs: list[Any] = field(default_factory=list)  # oldest first
    canonical: Any | None = None
    planned: dict[str, Any] | None = None
    in_analysis: bool = False
    exclusion: str = ""


@dataclass
class StudyData:
    """Everything a report or export reads, loaded once."""

    study: Any
    design: dict[str, Any]
    design_notes: list[str]
    plan_hash: str
    plan_hash_source: str  # study | protocol | observed | none
    runs: list[Any]
    results: dict[Any, list[Any]]
    samples: dict[Any, list[Any]]
    targets: list[TargetRecord]
    planned: dict[str, dict[str, Any]]
    planned_source: str  # protocol | preflight | none
    outcome_defs: list[dict[str, Any]]
    outcome_notes: list[str]
    values: dict[Any, dict[str, float | None]]  # analysis run id -> outcome -> value
    outcome_errors: dict[Any, str]
    planned_mode: str | None
    mode: str
    realized: dict[str, Any]
    # The questions the outcome families come from (the locked protocol's when it has them).
    research_questions: list[dict[str, Any]] = field(default_factory=list)
    research_questions_source: str = "study"  # protocol | study
    research_questions_changed: bool = False

    @property
    def analysis(self) -> list[TargetRecord]:
        return [t for t in self.targets if t.in_analysis]

    @property
    def groups(self) -> list[str]:
        return _sorted_labels(t.group for t in self.analysis)

    @property
    def blocked(self) -> bool:
        return self.mode == "blocked"

    def tool_slugs(self) -> list[str]:
        plan = self.study.plan or {}
        slugs = [t["slug"] for t in plan.get("tools") or [] if isinstance(t, dict) and t.get("slug")]
        reviewer = plan.get("reviewer") if isinstance(plan.get("reviewer"), dict) else {}
        if reviewer.get("enabled"):
            slugs.append(reviewer.get("tool_slug") or "ai_review")
        observed = sorted({r.tool_slug for rs in self.results.values() for r in rs} - set(slugs))
        return list(dict.fromkeys([*slugs, *observed]))

    def static_tool_slugs(self) -> list[str]:
        kinds: dict[str, str] = {}
        for rs in self.results.values():
            for r in rs:
                kinds.setdefault(r.tool_slug, r.tool_kind)
        plan_kinds = {t.get("slug"): t.get("kind") for t in (self.study.plan or {}).get("tools") or []}
        return [s for s in self.tool_slugs() if (plan_kinds.get(s) or kinds.get(s)) != "ai"]


def target_job_id(run) -> str | None:
    """The generation job *run* analysed (``None`` for an inline sample).

    ``AnalysisRun.generation_job`` is SET_NULL: deleting the job later nulls
    the foreign key (without touching ``updated_at``) but leaves the run's
    data intact. The ``job_id`` snapshot in ``target_meta`` keeps the
    target's identity, so a deleted job never turns a completed, analysed
    target into an unplanned run plus "not run" attrition.
    """
    if run.generation_job_id:
        return str(run.generation_job_id)
    job_id = as_dict(run.target_meta).get("job_id")
    return str(job_id) if job_id not in (None, "") else None


def _target_key(run) -> str:
    return target_job_id(run) or f"inline:{run.id}"


def _planned_targets(study) -> tuple[dict[str, dict[str, Any]], str]:
    resolved = _dig(study.protocol or {}, "targets", "resolved")
    if isinstance(resolved, list) and resolved:
        return {str(t.get("job_id")): t for t in resolved if isinstance(t, dict)}, "protocol"
    targets = (study.preflight or {}).get("targets")
    if isinstance(targets, list) and targets:
        return {str(t.get("job_id")): t for t in targets if isinstance(t, dict)}, "preflight"
    return {}, "none"


def _expected_plan_hash(study, runs: list[Any]) -> tuple[str, str]:
    if study.plan_hash:
        return study.plan_hash, "study"
    locked = (study.protocol or {}).get("plan_hash")
    if locked:
        return str(locked), "protocol"
    observed = Counter(r.plan_hash for r in runs if r.plan_hash and r.status in _FINISHED)
    if observed:
        # Most frequent plan, ties broken by hash so the choice is deterministic.
        return min(observed.items(), key=lambda kv: (-kv[1], kv[0]))[0], "observed"
    return "", "none"


def _target_label(meta: dict[str, Any], planned: dict[str, Any] | None, key: str) -> str:
    if planned and planned.get("label"):
        return str(planned["label"])
    parts = [meta.get("model_slug") or meta.get("model_name"), meta.get("app_slug")]
    label = " · ".join(str(p) for p in parts if p)
    repeat = meta.get("repeat_index")
    if repeat is not None:
        label = f"{label} #{repeat}" if label else f"#{repeat}"
    short = key.split(":", 1)[-1][:8]
    return f"{label} ({short})" if label else short


def _select_canonical(runs: list[Any], plan_hash: str) -> Any | None:
    matching = [r for r in runs if r.plan_hash == plan_hash] if plan_hash else list(runs)
    finished = [r for r in matching if r.status in _FINISHED]
    if finished:
        return finished[-1]
    if matching:
        return matching[-1]
    return runs[-1] if runs else None


def _run_outcomes(run, keys: list[str]) -> tuple[dict[str, float | None], str | None]:
    data = as_dict(run.measurement)
    if not data:
        return dict.fromkeys(keys), "measurement_missing"
    try:
        raw = measurement.outcomes(data)
    except Exception as exc:  # noqa: BLE001 - one malformed measurement must not break the whole report
        return dict.fromkeys(keys), f"outcomes_error:{type(exc).__name__}"
    raw = raw if isinstance(raw, dict) else {}
    return {k: _num(raw.get(k)) for k in keys}, None


def study_research_questions(study) -> tuple[list[dict[str, Any]], str, bool]:
    """``(research questions, source, changed)``: the locked protocol's when it has them, else the study's.

    The protocol froze the questions (and with them which outcomes are
    confirmatory); the live ``research_questions`` field can only change
    after lock through a direct database edit. ``changed`` is True when the
    live field differs from the locked one, which the report records as a
    deviation while still analysing the pre-registered questions.
    """
    current = jsonable(_research_questions(study.research_questions))
    protocol = study.protocol if isinstance(study.protocol, dict) else {}
    locked = protocol.get("research_questions")
    if protocol and isinstance(locked, list):
        return [rq for rq in locked if isinstance(rq, dict)], "protocol", jsonable(locked) != current
    return current, "study", False


def _study_design(study) -> tuple[dict[str, Any], list[str]]:
    """The locked protocol's design settings when present (pre-registered), else the study's."""
    current, notes = resolve_design(study.analysis_design)
    locked = _dig(study.protocol or {}, "design", "settings")
    if not isinstance(locked, dict) or not locked:
        return current, notes
    design, notes = resolve_design(locked)
    if jsonable(design) != jsonable(current):
        notes.append("analysis_design changed after the protocol was locked; the locked design is used.")
    return design, notes


def collect(study) -> StudyData:
    """Load runs, tool results, review samples and outcome values of *study* (no findings)."""
    design, design_notes = _study_design(study)
    runs = list(
        AnalysisRun.objects.filter(study=study)
        .exclude(purpose=AnalysisRun.Purpose.CANARY)
        .order_by("created_at", "id"),
    )
    run_ids = [r.id for r in runs]
    results: dict[Any, list[Any]] = defaultdict(list)
    for result in ToolResult.objects.filter(run_id__in=run_ids).order_by("run_id", "position", "tool_slug"):
        results[result.run_id].append(result)
    samples: dict[Any, list[Any]] = defaultdict(list)
    for sample in (
        ReviewSample.objects.filter(result__run_id__in=run_ids)
        .select_related("result")
        .order_by("result__run_id", "result__tool_slug", "index")
    ):
        samples[sample.result.run_id].append(sample)

    plan_hash, plan_source = _expected_plan_hash(study, runs)
    planned, planned_source = _planned_targets(study)
    by_target: dict[str, list[Any]] = defaultdict(list)
    for run in runs:
        by_target[_target_key(run)].append(run)
    # Eligible targets that never got a run are attrition and must stay visible.
    for job_id, entry in planned.items():
        if entry.get("eligible"):
            by_target.setdefault(job_id, [])

    block_by = design["block_by"]
    targets: list[TargetRecord] = []
    for key, truns in by_target.items():
        job_id = None if key.startswith("inline:") else key
        plan_entry = planned.get(key)
        canonical = _select_canonical(truns, plan_hash)
        meta = dict(as_dict(canonical.target_meta)) if canonical is not None else {}
        if not meta and plan_entry:
            meta = dict(as_dict(plan_entry.get("meta")))
        target = TargetRecord(
            key=key,
            job_id=job_id,
            label=_target_label(meta, plan_entry, key),
            group=group_label(meta, design["group_by"]),
            block=block_label(meta, block_by),
            meta=meta,
            runs=truns,
            canonical=canonical,
            planned=plan_entry,
        )
        if canonical is None:
            target.exclusion = "not_run" if (plan_entry or {}).get("eligible", True) else "ineligible"
        elif planned and plan_entry is None:
            target.exclusion = "not_planned"
        elif plan_entry is not None and not plan_entry.get("eligible", False):
            target.exclusion = "ineligible"
        elif plan_hash and canonical.plan_hash != plan_hash:
            target.exclusion = "plan_mismatch"
        elif canonical.status not in _FINISHED:
            target.exclusion = f"run_{canonical.status}"
        else:
            target.in_analysis = True
        targets.append(target)
    targets.sort(key=lambda t: (_label_key(t.group), _label_key(t.block or ""), t.label, t.key))

    research_questions, rq_source, rq_changed = study_research_questions(study)
    outcome_defs, outcome_notes = outcome_definitions(design, research_questions)
    keys = [d["key"] for d in outcome_defs]
    values: dict[Any, dict[str, float | None]] = {}
    errors: dict[Any, str] = {}
    for target in targets:
        if target.in_analysis:
            vals, error = _run_outcomes(target.canonical, keys)
            values[target.canonical.id] = vals
            if error:
                errors[target.canonical.id] = error

    planned_design = _dig(study.protocol or {}, "design")
    has_planned = isinstance(planned_design, dict) and planned_design.get("mode") in ("blocked", "independent")
    planned_mode = planned_design["mode"] if has_planned else None
    if has_planned and (
        planned_design.get("group_by") != design["group_by"] or planned_design.get("block_by") != design["block_by"]
    ):
        # Only reachable for a protocol without settings (built elsewhere): its mode was for other factors.
        design_notes.append("The protocol's design used other factors; its planned mode is ignored.")
        planned_mode = None
    realized = design_matrix([(t.group, t.block) for t in targets if t.in_analysis], design)
    mode = planned_mode if planned_mode in ("blocked", "independent") else realized["mode"]
    if mode == "blocked" and not design["block_by"]:
        mode = "independent"
    return StudyData(
        study=study,
        design=design,
        design_notes=design_notes,
        plan_hash=plan_hash,
        plan_hash_source=plan_source,
        runs=runs,
        results=dict(results),
        samples=dict(samples),
        targets=targets,
        planned=planned,
        planned_source=planned_source,
        outcome_defs=outcome_defs,
        outcome_notes=outcome_notes,
        values=values,
        outcome_errors=errors,
        planned_mode=planned_mode,
        mode=mode,
        realized=realized,
        research_questions=research_questions,
        research_questions_source=rq_source,
        research_questions_changed=rq_changed,
    )


def _findings(run_ids: list[Any]) -> list[dict[str, Any]]:
    fields = (
        "id",
        "run_id",
        "tool_slug",
        "is_ai",
        "counted",
        "severity",
        "category",
        "cwe",
        "rule_id",
        "issue_key",
        "file_path",
        "grounding",
        "is_consensus",
        "review_sample_id",
    )
    return list(Finding.objects.filter(run_id__in=run_ids).order_by("run_id", "severity_rank", "id").values(*fields))


def _issue_id(finding: dict[str, Any]) -> tuple[str, str]:
    return str(finding["run_id"]), finding["issue_key"] or f"finding:{finding['id']}"


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


def _add(devs: list[dict[str, Any]], code: str, severity: str, detail: str, *, items: list[str] | None = None) -> None:
    devs.append(
        {
            "code": code,
            "severity": severity,
            "detail": detail,
            "count": len(items) if items is not None else None,
            "items": _limited(items or []),
        },
    )


def _observed(values: Any) -> list[str]:
    return sorted({str(v) for v in values if v})


def run_engine_versions(run) -> dict[str, str]:
    """Engine stage versions *run* was actually processed with, as recorded on it.

    The plan freezes ``engine_versions`` at lock time, but the sample is
    resolved at submission and the measurement computed at finalize time by
    whatever code is deployed then (a retry re-finalizes with today's code).
    Sources, every one of them read: ``measurement.engine_versions`` (stamped
    by the measure stage with the code that finalized the run: normalizer,
    dedup, measurement, ...), ``summary.procedure.engine_versions`` (the code
    the executor verified before running), and the sample manifest's
    ``resolver_version`` / ``sloc_version`` plus ``measurement.version``.
    A stage the sources disagree on is reported as ``"a / b"`` (sorted), so
    it can never equal the planned version and always shows up as drift.
    """
    m = as_dict(run.measurement)
    manifest = as_dict(run.sample_manifest)
    procedure = as_dict(as_dict(run.summary).get("procedure"))
    sources: list[dict[str, Any]] = [
        as_dict(m.get("engine_versions")),
        as_dict(procedure.get("engine_versions")),
        {
            "sample_resolver": manifest.get("resolver_version"),
            "sloc": manifest.get("sloc_version"),
            "measurement": m.get("version"),
        },
    ]
    seen: dict[str, set[str]] = defaultdict(set)
    for source in sources:
        for stage, version in source.items():
            if version not in (None, "") and not isinstance(version, (dict, list)):
                seen[str(stage)].add(str(version))
    return {stage: " / ".join(sorted(versions)) for stage, versions in sorted(seen.items())}


def _engine_drift(data: StudyData) -> list[tuple[str, str | None, list[str], list[str]]]:
    """``(stage, planned, observed versions, affected runs)`` for every drifting engine stage."""
    planned_versions = as_dict((data.study.plan or {}).get("engine_versions"))
    by_stage: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for t in data.analysis:
        for stage, version in run_engine_versions(t.canonical).items():
            by_stage[stage].append((t.label, version))
    out = []
    for stage in sorted(by_stage):
        planned = planned_versions.get(stage)
        planned = str(planned) if planned not in (None, "") else None
        seen = _observed(v for _, v in by_stage[stage])
        if len(seen) <= 1 and (planned is None or seen == [planned]):
            continue
        # Runs off the planned version (or off the most common one when none was planned).
        reference = planned or Counter(v for _, v in by_stage[stage]).most_common(1)[0][0]
        affected = sorted(f"{label}: {v}" for label, v in by_stage[stage] if v != reference)
        out.append((stage, planned, seen, affected))
    return out


def _launch_skipped(study) -> dict[str, dict[str, Any]]:
    """``{job_id: {reason, detail?, ...}}`` from ``study.preflight["launch"]["skipped"]``."""
    launch = as_dict(as_dict(study.preflight).get("launch"))
    skipped = launch.get("skipped")
    return {
        str(entry["job_id"]): entry
        for entry in (skipped if isinstance(skipped, list) else [])
        if isinstance(entry, dict) and entry.get("job_id")
    }


def _deviations(data: StudyData) -> list[dict[str, Any]]:
    study = data.study
    protocol = study.protocol or {}
    plan = study.plan or {}
    devs: list[dict[str, Any]] = []
    if not protocol:
        _add(devs, "no_protocol", "major", "No locked protocol: the analysis was not pre-registered.")
    elif protocol.get("plan_hash") and study.plan_hash and protocol["plan_hash"] != study.plan_hash:
        _add(devs, "plan_changed", "major", "The study plan hash differs from the locked protocol's.")
    integrity = protocol_integrity(study)
    if integrity["match"] is False:
        _add(
            devs,
            "protocol_integrity",
            "major",
            f"The stored protocol does not match its sha256 (stored {str(integrity['stored'])[:12]}, "
            f"recomputed {str(integrity['recomputed'])[:12]}): the pre-registration this report cites "
            "cannot be verified.",
        )
    elif protocol and not integrity["stored"]:
        _add(devs, "protocol_integrity", "major", "The protocol has no stored sha256: it cannot be verified.")
    if data.research_questions_changed:
        _add(
            devs,
            "research_questions_changed",
            "major",
            "The study's research questions differ from the locked protocol's; the protocol's questions "
            "(and their confirmatory outcomes) are analysed.",
        )
    if data.plan_hash_source != "study":
        _add(
            devs,
            "no_locked_plan",
            "major",
            f"The study has no frozen plan hash; comparing runs of plan {data.plan_hash[:12] or 'none'} "
            f"(source: {data.plan_hash_source}).",
        )

    mismatched = [str(r.id) for r in data.runs if data.plan_hash and r.plan_hash != data.plan_hash]
    if mismatched:
        _add(devs, "plan_mismatch", "major", "Runs made with a different plan were excluded.", items=mismatched)
    # Targets launch could not submit carry the reason it recorded; any other
    # eligible target without a run stays "not_run".
    skipped = _launch_skipped(study)
    launch_skipped: list[str] = []
    by_reason: dict[str, list[str]] = defaultdict(list)
    for t in data.targets:
        if t.exclusion == "not_run" and t.job_id in skipped:
            entry = skipped[t.job_id]
            item = f"{t.label}: {entry.get('reason') or 'unknown'}"
            launch_skipped.append(f"{item} ({entry['detail']})" if entry.get("detail") else item)
        elif t.exclusion:
            by_reason[t.exclusion].append(t.label)
    if launch_skipped:
        _add(
            devs,
            "launch_skipped",
            "major",
            "Eligible protocol targets that launch did not submit, with the reason (job_missing, "
            "job_not_completed, sample_unavailable, sample_changed or submit_failed).",
            items=launch_skipped,
        )
    reason_text = {
        "not_run": ("major", "Eligible targets in the protocol without any run."),
        "not_planned": ("warn", "Runs for targets that are not in the protocol (excluded)."),
        "ineligible": ("warn", "Runs for targets the protocol excluded (excluded)."),
        "plan_mismatch": ("major", "Targets without a run of the study plan (excluded)."),
        "run_failed": ("major", "Targets whose run failed (excluded)."),
        "run_cancelled": ("major", "Targets whose run was cancelled (excluded)."),
        "run_queued": ("warn", "Targets whose run is still queued: the report predates completion."),
        "run_running": ("warn", "Targets whose run is still running: the report predates completion."),
    }
    for reason in sorted(by_reason):
        severity, text = reason_text.get(reason, ("warn", f"Targets excluded ({reason})."))
        _add(devs, f"target_{reason}", severity, text, items=by_reason[reason])

    # Runs of another plan are already reported as plan_mismatch.
    superseded = [
        str(r.id)
        for t in data.targets
        for r in t.runs
        if t.canonical is not None and r.id != t.canonical.id and r.plan_hash == t.canonical.plan_hash
    ]
    if superseded:
        _add(
            devs,
            "superseded_runs",
            "info",
            "Earlier runs of a target superseded by its canonical run.",
            items=superseded,
        )

    failed_required: Counter[str] = Counter()
    for t in data.analysis:
        for r in data.results.get(t.canonical.id, []):
            if r.required and r.status in ("failed", "timed_out", "skipped", "cancelled"):
                failed_required[r.tool_slug] += 1
    partial = [t.label for t in data.analysis if not t.canonical.is_complete]
    if partial:
        detail = "Analysed runs where a required tool did not succeed (dependent outcomes are NA)"
        if failed_required:
            detail += ": " + ", ".join(f"{slug} x{n}" for slug, n in sorted(failed_required.items()))
        _add(devs, "required_tool_failed", "warn", detail + ".", items=partial)

    analysed_results = [r for t in data.analysis for r in data.results.get(t.canonical.id, [])]
    planned_versions = {t.get("slug"): t.get("version") for t in plan.get("tools") or [] if isinstance(t, dict)}
    for slug in sorted({r.tool_slug for r in analysed_results}):
        seen = _observed(r.tool_version for r in analysed_results if r.tool_slug == slug)
        planned = planned_versions.get(slug)
        if len(seen) > 1 or (planned and seen and seen != [str(planned)]):
            _add(
                devs,
                "tool_version_changed",
                "major",
                f"{slug}: planned version {planned or 'unknown'}, observed {', '.join(seen)}.",
            )
    tag = _dig(plan, "analyzer_image", "tag")
    refs = _observed(r.image_ref for r in analysed_results if r.tool_kind != "ai")
    # A bare image id ("sha256:...") cannot be compared with the tag, but two distinct refs are two images.
    named = [ref for ref in refs if not ref.startswith("sha256:")]
    if (tag and any(tag not in ref for ref in named)) or len(refs) > 1:
        _add(
            devs,
            "image_changed",
            "major",
            f"Planned analyzer image {tag or 'unknown'}; observed {', '.join(refs)}.",
        )
    reviewer = plan.get("reviewer") if isinstance(plan.get("reviewer"), dict) else {}
    analysed_samples = [s for t in data.analysis for s in data.samples.get(t.canonical.id, [])]
    planned_model = _dig(reviewer, "model", "canonical_slug")
    models = _observed(s.model_canonical_slug for s in analysed_samples)
    if planned_model and any(m != planned_model for m in models):
        _add(
            devs,
            "reviewer_model_changed",
            "major",
            f"Planned reviewer {planned_model}; observed {', '.join(models)}.",
        )
    planned_prompt = reviewer.get("prompt_sha256")
    prompts = _observed(s.prompt_sha256 for s in analysed_samples)
    if planned_prompt and any(p != planned_prompt for p in prompts):
        _add(devs, "reviewer_prompt_changed", "major", "Review samples used a prompt other than the planned one.")
    # Equal plan_hash only means comparable when every run was resolved and measured by the
    # frozen engine stages; a code update mid-study (e.g. before a retry) breaks that silently.
    for stage, planned_version, seen, affected in _engine_drift(data):
        _add(
            devs,
            "engine_version_changed",
            "major",
            f"{stage}: planned {planned_version or 'unknown'}, observed {', '.join(seen)}; "
            "outcomes of the listed runs were computed by other engine rules.",
            items=affected,
        )
    locked_sha = _dig(protocol, "platform", "git", "sha")
    current_sha = engine_provenance()["git"]["sha"]
    if locked_sha and current_sha and locked_sha != current_sha:
        _add(
            devs,
            "platform_changed",
            "info",
            f"The platform code changed between lock ({str(locked_sha)[:12]}) and this report "
            f"({str(current_sha)[:12]}); per-run engine stage versions are checked separately.",
        )

    if data.planned_mode == "blocked" and not data.realized["complete"]:
        _add(
            devs,
            "design_incomplete",
            "warn",
            f"The planned design was complete, the analysed one is not ({data.realized['reason']}); "
            "paired tests use only blocks present in every compared group.",
        )
    if study.status not in ("completed",):
        _add(devs, "study_not_completed", "info", f"The study status is {study.status!r} at report time.")
    for note in data.design_notes:
        _add(devs, "design_note", "info", note)
    errors = sorted(f"{rid}: {err}" for rid, err in data.outcome_errors.items())
    if errors:
        _add(
            devs,
            "outcome_extraction",
            "warn",
            "Outcomes could not be read from some measurements (NA).",
            items=errors,
        )
    devs.sort(key=lambda d: (_SEVERITY_ORDER.get(d["severity"], 9), d["code"]))
    return devs


def _deviations_section(devs: list[dict[str, Any]]) -> dict[str, Any]:
    columns = [
        _col("severity", "Severity", "status"),
        _col("code", "Code"),
        _col("detail", "Detail"),
        _col("count", "Count", "int"),
        _col("items", "Items", "list"),
    ]
    notes = [] if devs else ["No deviations from the protocol were detected."]
    return _section(
        "deviations",
        "Deviations from protocol",
        description="Everything that differs from the locked protocol, with reasons (T10).",
        tables=[_table("deviations", "Deviations", columns, devs)],
        notes=notes,
    )


def tool_coverage(run, result) -> tuple[int | None, int | None, int | None]:
    """(files_in_scope, files_scanned, unparseable) from the measurement, else the tool result."""
    cov = _dig(run.measurement or {}, "coverage", result.tool_slug)
    cov = cov if isinstance(cov, dict) else {}
    raw = result.coverage if isinstance(result.coverage, dict) else {}
    in_scope = _int(cov.get("files_in_scope", raw.get("files_in_scope")))
    scanned = _int(cov.get("files_scanned", raw.get("files_scanned")))
    unparseable = cov.get("unparseable", raw.get("unparseable"))
    if isinstance(unparseable, list):
        unparseable = len(unparseable)
    return in_scope, scanned, _int(unparseable)


def _run_stopped_note(data: StudyData) -> str | None:
    """How many analysed targets ended at a run-level limit (kept unless the study excludes run limits)."""
    from backend.analysis.engine.sample import truncation_flags

    stopped = [t for t in data.analysis if truncation_flags(t.meta)["run_stopped"]]
    if not stopped:
        return None
    rules = as_dict(_dig(data.study.protocol or {}, "exclusion_rules")) or _effective_exclusion_rules(
        data.study.exclusion_rules,
    )
    rule = (
        "exclude_run_limits is on, but these targets were eligible when the protocol was locked"
        if rules.get("exclude_run_limits")
        else "the protocol keeps such targets (exclude_run_limits off)"
    )
    return (
        f"{len(stopped)} analysed target(s) ended at a run-level limit (run_stopped: wall clock, budget or "
        f"memory cap; not output truncation): {rule}."
    )


def _data_quality_section(data: StudyData) -> dict[str, Any]:
    tables: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []
    notes: list[str] = []
    targets = data.targets
    primary = data.design["primary_outcome"]

    def stages(ts: list[TargetRecord]) -> dict[str, int]:
        # Nested stages over eligible targets (all targets without a protocol), so every step is a
        # subset of the previous one; unplanned/ineligible runs are reported under exclusions.
        analysed = [t for t in ts if t.in_analysis]
        return {
            "eligible": len(ts),
            "with_run": sum(1 for t in ts if t.runs),
            "analysed": len(analysed),
            "complete": sum(1 for t in analysed if t.canonical.is_complete),
            "primary_available": sum(1 for t in analysed if data.values[t.canonical.id].get(primary) is not None),
        }

    base_targets = [t for t in targets if (t.planned or {}).get("eligible")] if data.planned else targets
    overall = stages(base_targets)
    steps = [
        ("planned", "Targets in protocol", len(data.planned) if data.planned else None),
        ("eligible", "Eligible", overall["eligible"] if data.planned else None),
        ("submitted", "With a run", overall["with_run"]),
        ("analysed", "Analysed (finished run of the study plan)", overall["analysed"]),
        ("complete", "All required tools OK", overall["complete"]),
        ("primary_available", "Primary outcome available", overall["primary_available"]),
    ]
    base = overall["eligible"]
    planned_n = len(data.planned) if data.planned else None
    # "Share of eligible" is undefined for the protocol step (a superset of the eligible
    # targets, so it would exceed 100%); every step also gets its share of the protocol targets.
    funnel_rows = [
        {
            "step": label,
            "key": key,
            "n": n,
            "share": None if key == "planned" else _share(n, base),
            "share_of_protocol": _share(n, planned_n),
        }
        for key, label, n in steps
    ]
    tables.append(
        _table(
            "funnel",
            "Sample funnel",
            [
                _col("step", "Step"),
                _col("n", "n", "int"),
                _col("share", "Share of eligible", "pct"),
                _col("share_of_protocol", "Share of protocol targets", "pct"),
            ],
            funnel_rows,
            notes=[] if data.planned else ["No protocol targets: shares are relative to targets with a run."],
        ),
    )
    series.append({"key": "funnel", "kind": "funnel", "title": "Sample funnel", "steps": funnel_rows})

    # Attrition per group makes differential loss (survivorship bias) visible.
    attrition = [
        {"group": g, **stages([t for t in base_targets if t.group == g])}
        for g in _sorted_labels(t.group for t in base_targets)
    ]
    tables.append(
        _table(
            "attrition",
            "Attrition by group",
            [
                _col("group", "Group"),
                _col("eligible", "Eligible", "int"),
                _col("with_run", "With run", "int"),
                _col("analysed", "Analysed", "int"),
                _col("complete", "Complete", "int"),
                _col("primary_available", "Primary available", "int"),
            ],
            attrition,
        ),
    )

    planned_reasons: Counter[str] = Counter()
    for entry in data.planned.values():
        if entry.get("eligible"):
            continue
        # Only reasons that exclude (as the protocol counts them): a warn/info flag such as
        # functional_unknown on an excluded target is not why it was excluded.
        codes = {
            str(r.get("code") or "unknown")
            for r in entry.get("reasons") or []
            if isinstance(r, dict) and r.get("severity", "exclude") == "exclude"
        }
        planned_reasons.update(codes or {"unknown"})
    realized_reasons = Counter(t.exclusion for t in targets if t.exclusion)
    exclusion_rows = [{"stage": "protocol", "reason": k, "n": v} for k, v in sorted(planned_reasons.items())]
    exclusion_rows += [{"stage": "analysis", "reason": k, "n": v} for k, v in sorted(realized_reasons.items())]
    tables.append(
        _table(
            "exclusions",
            "Exclusions",
            [_col("stage", "Stage"), _col("reason", "Reason"), _col("n", "Targets", "int")],
            exclusion_rows,
            notes=["A target can have several protocol exclusion reasons."],
        ),
    )
    stopped_note = _run_stopped_note(data)
    if stopped_note:
        notes.append(stopped_note)

    status_counts = Counter(r.status for r in data.runs)
    tables.append(
        _table(
            "run_status",
            "Run status (all runs, including superseded)",
            [_col("status", "Status", "status"), _col("n", "Runs", "int")],
            [{"status": s, "n": n} for s, n in sorted(status_counts.items())],
        ),
    )

    # Tool x target status matrix and per-tool health, over canonical runs.
    slugs = data.tool_slugs()
    matrix_rows, cells, reasons = [], [], []
    # Coverage numerators and denominators are only summed together (same executions), never mixed.
    per_tool: dict[str, dict[str, Any]] = {
        s: {"statuses": Counter(), "reasons": Counter(), "versions": set(), "cov": [0, 0], "parse": [0, 0]}
        for s in slugs
    }
    required_by_slug: dict[str, bool] = {}
    for t in targets:
        run = t.canonical
        by_slug = {r.tool_slug: r for r in data.results.get(run.id, [])} if run is not None else {}
        matrix_rows.append(
            {"key": t.key, "label": t.label, "group": t.group, "block": t.block, "in_analysis": t.in_analysis},
        )
        row_cells, row_reasons = [], []
        for slug in slugs:
            result = by_slug.get(slug)
            if run is None:
                row_cells.append("not_run")
                row_reasons.append("")
                continue
            if result is None:
                row_cells.append("missing")
                row_reasons.append("")
                continue
            row_cells.append(result.status)
            row_reasons.append(result.failure_reason or "")
            info = per_tool[slug]
            info["statuses"][result.status] += 1
            required_by_slug.setdefault(slug, result.required)
            if result.failure_reason:
                info["reasons"][result.failure_reason] += 1
            if result.tool_version:
                info["versions"].add(result.tool_version)
            if result.status == "succeeded":
                in_scope, scanned, unparse = tool_coverage(run, result)
                if in_scope is not None and scanned is not None:
                    info["cov"][0] += scanned
                    info["cov"][1] += in_scope
                if in_scope is not None and unparse is not None:
                    info["parse"][0] += unparse
                    info["parse"][1] += in_scope
        cells.append(row_cells)
        reasons.append(row_reasons)
    series.append(
        {
            "key": "tool_status_matrix",
            "kind": "status_matrix",
            "title": f"Tool {TIMES} target status",
            "rows": matrix_rows,
            "columns": slugs,
            "cells": cells,
            "reasons": reasons,
            "legend": [*TOOL_STATUSES, "missing", "not_run"],
        },
    )
    tool_rows = []
    for slug in slugs:
        info = per_tool[slug]
        st = info["statuses"]
        executed = sum(st[s] for s in ("succeeded", "failed", "timed_out"))
        tool_rows.append(
            {
                "tool": slug,
                "required": required_by_slug.get(slug),
                "versions": sorted(info["versions"]),
                "runs": sum(st.values()),
                **{s: st[s] for s in TOOL_STATUSES},
                "success_rate": _share(st["succeeded"], executed),
                "file_coverage": _share(*info["cov"]),
                "parse_error_rate": _share(*info["parse"]),
                "failure_reasons": [f"{k}: {v}" for k, v in sorted(info["reasons"].items())],
            },
        )
    tables.append(
        _table(
            "tools",
            "Tool health (canonical runs)",
            [
                _col("tool", "Tool"),
                _col("required", "Required", "bool"),
                _col("versions", "Versions", "list"),
                _col("runs", "Results", "int"),
                *[_col(s, s.replace("_", " ").capitalize(), "int") for s in TOOL_STATUSES],
                _col("success_rate", "Success rate", "pct"),
                _col("file_coverage", "File coverage", "pct"),
                _col("parse_error_rate", "Parse-error rate", "pct"),
                _col("failure_reasons", "Failure reasons", "list"),
            ],
            tool_rows,
            notes=[
                "Success rate = succeeded / (succeeded + failed + timed out); not-applicable is not a success.",
                "Coverage = files scanned / files in scope over succeeded executions.",
            ],
        ),
    )

    # Outcome NA counts: a failed tool makes its outcomes missing, never 0.
    na_rows, na_matrix = [], []
    analysis_groups = data.groups
    for d in data.outcome_defs:
        vals = [data.values[t.canonical.id][d["key"]] for t in data.analysis]
        missing = sum(1 for v in vals if v is None)
        na_rows.append(
            {
                "outcome": d["label"],
                "key": d["key"],
                "n": len(vals),
                "available": len(vals) - missing,
                "missing": missing,
                "missing_share": _share(missing, len(vals)),
            },
        )
        na_matrix.append(
            [
                sum(1 for t in data.analysis if t.group == g and data.values[t.canonical.id][d["key"]] is None)
                for g in analysis_groups
            ],
        )
    tables.append(
        _table(
            "outcome_missing",
            "Outcome availability (analysis set)",
            [
                _col("outcome", "Outcome"),
                _col("n", "Runs", "int"),
                _col("available", "Available", "int"),
                _col("missing", "Missing (NA)", "int"),
                _col("missing_share", "Missing share", "pct"),
            ],
            na_rows,
        ),
    )
    series.append(
        {
            "key": "outcome_missing_matrix",
            "kind": "heatmap",
            "title": "Missing outcomes by group",
            "rows": [d["key"] for d in data.outcome_defs],
            "row_labels": [d["label"] for d in data.outcome_defs],
            "columns": analysis_groups,
            "values": na_matrix,
        },
    )

    # AI reviewer data quality.
    ai_samples = [s for t in targets if t.canonical is not None for s in data.samples.get(t.canonical.id, [])]
    if ai_samples:
        sample_status = Counter(s.status for s in ai_samples)
        tables.append(
            _table(
                "ai_samples",
                "AI review samples",
                [_col("status", "Status", "status"), _col("n", "Samples", "int"), _col("share", "Share", "pct")],
                [
                    {"status": s, "n": sample_status[s], "share": _share(sample_status[s], len(ai_samples))}
                    for s in [*SAMPLE_STATUSES, *sorted(set(sample_status) - set(SAMPLE_STATUSES))]
                    if sample_status[s]
                ],
                notes=[f"Repaired (one schema-repair retry): {sum(1 for s in ai_samples if s.repaired)}."],
            ),
        )
        grounding: Counter[str] = Counter()
        providers: Counter[str] = Counter()
        finish: Counter[str] = Counter()
        failure: Counter[str] = Counter()
        for s in ai_samples:
            for k, v in as_dict(s.grounding).items():
                grounding[k] += _int(v) or 0
            for k, v in as_dict(s.providers).items():
                providers[str(k)] += _int(v) or 0
            for k, v in as_dict(s.finish_reasons).items():
                finish[str(k)] += _int(v) or 0
            if s.failure_reason:
                failure[s.failure_reason] += 1
        total_grounding = sum(grounding.values())
        tables.append(
            _table(
                "ai_grounding",
                "AI finding grounding",
                [_col("grounding", "Grounding"), _col("n", "Findings", "int"), _col("share", "Share", "pct")],
                [
                    {"grounding": k, "n": grounding[k], "share": _share(grounding[k], total_grounding)}
                    for k in [*GROUNDING_KINDS, *sorted(set(grounding) - set(GROUNDING_KINDS))]
                ],
            ),
        )
        tables.append(
            _table(
                "ai_calls",
                "AI providers, finish reasons and failures",
                [_col("kind", "Kind"), _col("value", "Value"), _col("n", "n", "int")],
                [{"kind": "provider", "value": k, "n": v} for k, v in sorted(providers.items())]
                + [{"kind": "finish_reason", "value": k, "n": v} for k, v in sorted(finish.items())]
                + [{"kind": "failure_reason", "value": k, "n": v} for k, v in sorted(failure.items())],
            ),
        )
    else:
        notes.append("No AI review samples were recorded.")
    return _section(
        "data_quality",
        "Data quality and coverage",
        description="Attrition, tool health and missing data come first (T4, F2, F3).",
        tables=tables,
        series=series,
        notes=notes,
    )


def measured_summary(run) -> dict[str, Any]:
    """Measured SLOC/files, SLOC by language and functional status of one run (measurement first)."""
    m = as_dict(run.measurement)
    totals = as_dict(_dig(run.sample_manifest or {}, "totals"))
    functional = m.get("functional") if isinstance(m.get("functional"), dict) else None
    if functional is None:
        functional = as_dict(_dig(run.target_meta or {}, "functional"))
    return {
        "sloc": _num(_dig(m, "sloc", "measured", default=totals.get("measured_sloc"))),
        "files": _num(_dig(m, "files", "measured", default=totals.get("measured"))),
        "by_language": _dig(m, "sloc", "by_language", default={}),
        "functional_status": (functional or {}).get("status"),
        "functional_rate": _num((functional or {}).get("pass_rate")),
    }


def _sample_section(data: StudyData) -> dict[str, Any]:
    rows = []
    languages: dict[str, Counter[str]] = defaultdict(Counter)
    groups = data.groups
    for g in [*groups, ALL_LABEL]:
        ts = [t for t in data.analysis if g in (ALL_LABEL, t.group)]
        info = [measured_summary(t.canonical) for t in ts]
        sloc = stats.describe(i["sloc"] for i in info)
        files = stats.describe(i["files"] for i in info)
        rates = stats.finite(i["functional_rate"] for i in info)
        statuses = Counter(str(i["functional_status"]) for i in info if i["functional_status"])
        for i in info:
            for lang, n in as_dict(i["by_language"]).items():
                languages[g][str(lang)] += _int(n) or 0
        rows.append(
            {
                "group": g,
                "runs": len(ts),
                "sloc_n": sloc["n"],
                "sloc_median": sloc["median"],
                "sloc_q1": sloc["q1"],
                "sloc_q3": sloc["q3"],
                "sloc_mean": sloc["mean"],
                "sloc_total": sum(stats.finite(i["sloc"] for i in info)) if sloc["n"] else None,
                "files_median": files["median"],
                "functional_known": len(rates),
                "functional_pass_rate_mean": stats.mean(rates),
                "functional_status": [f"{k}: {v}" for k, v in sorted(statuses.items())],
            },
        )
    lang_keys = sorted({lang for c in languages.values() for lang in c})
    lang_rows = [{"group": g, **{lang: languages[g][lang] for lang in lang_keys}} for g in [*groups, ALL_LABEL]]
    matrix = data.realized
    return _section(
        "sample",
        "Sample description",
        description="Size and functional status of the analysed apps per group.",
        tables=[
            _table(
                "sample_by_group",
                "Measured code per group",
                [
                    _col("group", "Group"),
                    _col("runs", "Runs", "int"),
                    _col("sloc_n", "SLOC known", "int"),
                    _col("sloc_median", "SLOC median", "float"),
                    _col("sloc_q1", "SLOC Q1", "float"),
                    _col("sloc_q3", "SLOC Q3", "float"),
                    _col("sloc_mean", "SLOC mean", "float"),
                    _col("sloc_total", "SLOC total", "int"),
                    _col("files_median", "Files median", "float"),
                    _col("functional_known", "Functional known", "int"),
                    _col("functional_pass_rate_mean", "Mean pass rate", "pct"),
                    _col("functional_status", "Functional status", "list"),
                ],
                rows,
            ),
            _table(
                "sloc_by_language",
                "Measured SLOC by language",
                [_col("group", "Group"), *[_col(lang, lang, "int") for lang in lang_keys]],
                lang_rows,
            ),
        ],
        series=[
            {
                "key": "design_matrix",
                "kind": "heatmap",
                "title": f"Runs per group {TIMES} {data.design['block_by'] or 'block'}",
                "rows": list(matrix["groups"]),
                "columns": list(matrix["blocks"]),
                "values": [[matrix["cells"].get(g, {}).get(b, 0) for b in matrix["blocks"]] for g in matrix["groups"]],
            },
        ],
        notes=[f"Analysed design: {matrix['mode']} ({matrix['reason']})."],
    )


def _group_values(data: StudyData, key: str) -> dict[str, list[tuple[str | None, float | None]]]:
    out: dict[str, list[tuple[str | None, float | None]]] = {g: [] for g in data.groups}
    for t in data.analysis:
        out[t.group].append((t.block, data.values[t.canonical.id][key]))
    return out


def _mean_ci(values: list[tuple[str | None, float | None]], data: StudyData, seed: int) -> tuple[Any, str]:
    design = data.design
    resamples = design["bootstrap_resamples"]
    if not resamples:
        return None, "disabled"
    if design["block_by"]:
        clusters: dict[str, list[float]] = defaultdict(list)
        singletons: list[list[float]] = []  # a value without a known block is its own cluster
        for block, v in values:
            if v is not None:
                if block is None:
                    singletons.append([v])
                else:
                    clusters[block].append(v)
        ordered = [clusters[b] for b in _sorted_labels(clusters)] + singletons
        ci = stats.cluster_bootstrap_ci(ordered, resamples=resamples, level=design["ci_level"], seed=seed)
        return ci, f"cluster bootstrap ({design['block_by']})"
    ci = stats.bootstrap_ci([v for _, v in values], resamples=resamples, level=design["ci_level"], seed=seed)
    return ci, "bootstrap"


def _box(values: list[float]) -> dict[str, Any]:
    d = stats.describe(values)
    if not d["n"]:
        return {**d, "whisker_low": None, "whisker_high": None, "outliers": [], "values": []}
    lo_fence = d["q1"] - 1.5 * d["iqr"]
    hi_fence = d["q3"] + 1.5 * d["iqr"]
    inside = [v for v in values if lo_fence <= v <= hi_fence]
    return {
        **d,
        "whisker_low": min(inside),
        "whisker_high": max(inside),
        "outliers": sorted(v for v in values if v < lo_fence or v > hi_fence),
        "values": sorted(values),
    }


def _descriptives_section(data: StudyData) -> dict[str, Any]:
    tables, series = [], []
    design = data.design
    for d in data.outcome_defs:
        key = d["key"]
        by_group = _group_values(data, key)
        rows = []
        boxes = []
        for g in [*data.groups, ALL_LABEL]:
            values = [pair for gg in data.groups if g in (ALL_LABEL, gg) for pair in by_group[gg]]
            present = [v for _, v in values if v is not None]
            seed = stats.derive_seed(design["seed"], "descriptives", key, g)
            if d["kind"] == "binary":
                successes = sum(1 for v in present if v >= 0.5)
                rows.append(
                    {
                        "group": g,
                        "n": len(present),
                        "n_missing": len(values) - len(present),
                        "successes": successes,
                        "share": _share(successes, len(present)),
                        "share_ci": stats.wilson_ci(successes, len(present), level=design["ci_level"])
                        if present
                        else None,
                    },
                )
            else:
                desc = stats.describe(present)
                ci, ci_method = _mean_ci(values, data, seed)
                rows.append(
                    {
                        "group": g,
                        "n": desc["n"],
                        "n_missing": len(values) - len(present),
                        "median": desc["median"],
                        "q1": desc["q1"],
                        "q3": desc["q3"],
                        "iqr": desc["iqr"],
                        "mean": desc["mean"],
                        "mean_ci": ci,
                        "sd": desc["sd"],
                        "min": desc["min"],
                        "max": desc["max"],
                        "ci_method": ci_method if ci is not None else None,
                    },
                )
            if g != ALL_LABEL:
                # _box sees only the present values; the group's NA count comes from all its runs.
                box = {"group": g, **_box(present), "n_missing": len(values) - len(present)}
                if d["kind"] == "binary":
                    box.update({k: rows[-1][k] for k in ("successes", "share", "share_ci")})
                boxes.append(box)
        if d["kind"] == "binary":
            columns = [
                _col("group", "Group"),
                _col("n", "n", "int"),
                _col("n_missing", "Missing", "int"),
                _col("successes", "Yes", "int"),
                _col("share", "Share", "pct"),
                _col("share_ci", f"{design['ci_level']:.0%} CI (Wilson)", "pct_ci"),
            ]
        else:
            columns = [
                _col("group", "Group"),
                _col("n", "n", "int"),
                _col("n_missing", "Missing", "int"),
                _col("median", "Median", "float"),
                _col("q1", "Q1", "float"),
                _col("q3", "Q3", "float"),
                _col("mean", "Mean", "float"),
                _col("mean_ci", f"Mean {design['ci_level']:.0%} CI", "ci"),
                _col("sd", "SD", "float"),
                _col("min", "Min", "float"),
                _col("max", "Max", "float"),
            ]
        title = d["label"] + (" (primary)" if d["primary"] else "")
        tables.append(
            _table(
                f"outcome.{key}",
                title,
                columns,
                rows,
                description=d["description"],
                notes=[*([f"Unit: {d['unit']}."] if d["unit"] else []), f"{d['family'].capitalize()} outcome."],
            ),
        )
        series.append(
            {
                "key": f"box.{key}",
                "kind": "box" if d["kind"] != "binary" else "proportion",
                "title": title,
                "outcome": key,
                "unit": d["unit"],
                "higher_is_better": d["higher_is_better"],
                "groups": boxes,
            },
        )
    return _section(
        "descriptives",
        "Descriptive results",
        description="Per-group n, missing, median, IQR and mean with a bootstrap CI for every outcome (T5, F4).",
        tables=tables,
        series=series,
        notes=[
            "Missing outcomes (NA) are excluded per outcome (complete-case); they are never counted as 0.",
            f"Bootstrap: {design['bootstrap_resamples']} percentile resamples, seed {design['seed']}"
            + (f", clustered by {design['block_by']}." if design["block_by"] else "."),
        ],
    )


def _aggregate(values: list[float], how: str) -> float | None:
    return stats.median(values) if how == "median" else stats.mean(values)


def _cell_units(pairs: list[tuple[str | None, float]], design: dict[str, Any]) -> list[float]:
    """Test units of one group: one aggregated value per block (cell); a run without a known block is its own unit.

    Replicates of one (group, block) cell share the generator's
    spec-specific behaviour (a model x app interaction), so treating them as
    independent runs inflates n and makes unblocked p-values anti-conservative.
    Without a blocking factor replicates cannot be identified: every run is a unit.
    """
    if not design["block_by"]:
        return [v for _, v in pairs]
    per_block: dict[str, list[float]] = defaultdict(list)
    singles: list[float] = []
    for block, value in pairs:
        if block is None:
            singles.append(value)
        else:
            per_block[block].append(value)
    cells = [_aggregate(per_block[b], design["cell_aggregate"]) for b in _sorted_labels(per_block)]
    return [v for v in cells if v is not None] + singles


def _better(delta: float | None, higher_is_better: Any, a: str, b: str) -> str | None:
    if delta is None or delta == 0 or higher_is_better is None:
        return None
    a_higher = delta > 0
    return a if a_higher == bool(higher_is_better) else b


def _compare_outcome(data: StudyData, d: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict | None]:
    design = data.design
    key = d["key"]
    by_group = _group_values(data, key)
    present = {g: [(b, v) for b, v in pairs if v is not None] for g, pairs in by_group.items()}
    groups = [g for g in data.groups if present[g]]
    empty = [g for g in data.groups if not present[g]]
    n_missing = sum(1 for pairs in by_group.values() for _, v in pairs if v is None)
    omnibus: dict[str, Any] = {
        "outcome": d["label"],
        "key": key,
        "family": d["family"],
        "groups": len(groups),
        "n": sum(len(present[g]) for g in groups),
        "n_missing": n_missing,
        "units": None,  # values entering the test (cells, not runs)
        "test": None,
        "statistic": None,
        "df": None,
        "p": None,
        "effect": None,
        "effect_name": None,
        "blocks_used": None,
        "blocks_dropped": None,
        "note": "",
    }
    notes = [f"No data: {', '.join(empty)}"] if empty else []
    pairs_out: list[dict[str, Any]] = []
    extras: dict[str, Any] | None = None
    blocked = data.blocked
    cells: dict[str, dict[str, float]] = {}
    # Unblocked tests use one value per group x block cell, never replicate runs (see _cell_units).
    units = {g: _cell_units(present[g], design) for g in data.groups}
    if blocked:
        unblocked = 0
        for g in groups:
            per_block: dict[str, list[float]] = defaultdict(list)
            for b, v in present[g]:
                if b is None:
                    unblocked += 1  # cannot be paired with anything
                else:
                    per_block[b].append(v)
            cells[g] = {b: _aggregate(vs, design["cell_aggregate"]) for b, vs in per_block.items()}
        if unblocked:
            notes.append(f"{unblocked} value(s) without a known {design['block_by']} left out of paired tests")
        all_blocks = _sorted_labels(b for g in groups for b in cells[g])
        complete_blocks = [b for b in all_blocks if all(b in cells[g] for g in groups)]
        omnibus["blocks_used"] = len(complete_blocks)
        omnibus["blocks_dropped"] = len(all_blocks) - len(complete_blocks)
        omnibus["units"] = len(complete_blocks) * len(groups)
        omnibus["test"] = "friedman"
        omnibus["effect_name"] = "kendalls_w"
        if len(groups) >= 2:
            result = stats.friedman([[cells[g][b] for g in groups] for b in complete_blocks])
            if result is not None:
                omnibus.update(
                    statistic=result["statistic"],
                    df=result["df"],
                    p=result["p"],
                    effect=result["kendalls_w"],
                )
                extras = {
                    "key": f"ranks.{key}",
                    "kind": "mean_ranks",
                    "title": f"Mean ranks: {d['label']}",
                    "outcome": key,
                    "groups": [
                        {"group": g, "mean_rank": r} for g, r in zip(groups, result["mean_ranks"], strict=True)
                    ],
                    "n_blocks": result["n_blocks"],
                }
            elif len(complete_blocks) < 2:
                notes.append(f"Friedman undefined: {len(complete_blocks)} complete block(s), at least 2 needed")
            else:
                notes.append("Friedman undefined: every block is tied (no variation)")
        else:
            notes.append("fewer than 2 groups with data")
    else:
        omnibus["test"] = "kruskal_wallis"
        omnibus["effect_name"] = "epsilon_squared"
        omnibus["units"] = sum(len(units[g]) for g in groups)
        if len(groups) >= 2:
            result = stats.kruskal_wallis([units[g] for g in groups])
            if result is not None:
                omnibus.update(
                    statistic=result["statistic"],
                    df=result["df"],
                    p=result["p"],
                    effect=result["epsilon_squared"],
                )
            else:
                notes.append("Kruskal-Wallis undefined: all values tied (no variation)")
        else:
            notes.append("fewer than 2 groups with data")

    effect_resamples = design["effect_ci_resamples"] if d["primary"] else 0
    for a, b in combinations(data.groups, 2):
        row: dict[str, Any] = {
            "outcome": d["label"],
            "key": key,
            "group_a": a,
            "group_b": b,
            "test": "wilcoxon_signed_rank" if blocked else "mann_whitney_u",
            "n_a": len(present[a]),
            "n_b": len(present[b]),
            "n_missing_a": len(by_group[a]) - len(present[a]),
            "n_missing_b": len(by_group[b]) - len(present[b]),
            "n_pairs": None,
            "units_a": None,  # values of A entering the test (cells, not runs)
            "units_b": None,
            "statistic": None,
            "p": None,
            "delta": None,
            "delta_ci": None,
            "magnitude": None,
            "a12": None,
            "rank_biserial": None,
            "median_diff": None,
            "better": None,
            "method": None,
            "note": "",
        }
        if not present[a] or not present[b]:
            row["note"] = "no data in one group: not compared"
            pairs_out.append(row)
            continue
        seed = stats.derive_seed(design["seed"], "delta", key, a, b)
        if blocked:
            common = [blk for blk in _sorted_labels(cells.get(a, {})) if blk in cells.get(b, {})]
            xa = [cells[a][blk] for blk in common]
            xb = [cells[b][blk] for blk in common]
            row["n_pairs"] = row["units_a"] = row["units_b"] = len(common)
            if not common:
                row["note"] = "no shared blocks: not compared"
                pairs_out.append(row)
                continue
            test = stats.wilcoxon_signed_rank(xa, xb)
            row["median_diff"] = stats.median([x - y for x, y in zip(xa, xb, strict=True)])
            if test is not None:
                row["rank_biserial"] = stats.matched_pairs_rank_biserial(test["r_plus"], test["r_minus"])
            else:
                row["note"] = "all paired differences are zero"
            if effect_resamples:
                row["delta_ci"] = stats.bootstrap_delta_ci(
                    xa,
                    xb,
                    is_paired=True,
                    resamples=effect_resamples,
                    level=design["ci_level"],
                    seed=seed,
                )
        else:
            xa, xb = units[a], units[b]
            row["units_a"], row["units_b"] = len(xa), len(xb)
            test = stats.mann_whitney_u(xa, xb)
            ma, mb = stats.median(xa), stats.median(xb)
            row["median_diff"] = None if ma is None or mb is None else ma - mb
            if effect_resamples:
                row["delta_ci"] = stats.bootstrap_delta_ci(
                    xa,
                    xb,
                    resamples=effect_resamples,
                    level=design["ci_level"],
                    seed=seed,
                )
        if test is not None:
            row["statistic"] = test["statistic"] if blocked else test["u"]
            row["p"] = test["p"]
            row["method"] = test["method"]
        delta = stats.cliffs_delta(xa, xb)
        row["delta"] = delta
        row["magnitude"] = stats.cliffs_magnitude(delta)
        row["a12"] = stats.vargha_delaney_a12(xa, xb)
        row["better"] = _better(delta, d["higher_is_better"], a, b)
        pairs_out.append(row)
    adjusted = stats.adjust([r["p"] for r in pairs_out], design["correction"])
    for row, p_adj in zip(pairs_out, adjusted, strict=True):
        row["p_adj"] = p_adj
        row["significant"] = None if p_adj is None else p_adj < design["alpha"]
    omnibus["note"] = "; ".join(notes)
    return omnibus, pairs_out, extras


def _inferential_section(data: StudyData) -> tuple[dict[str, Any], dict[str, Any]]:
    design = data.design
    omnibus_rows, pairwise_rows, series = [], [], []
    for d in data.outcome_defs:
        omnibus, pairs, extras = _compare_outcome(data, d)
        omnibus_rows.append(omnibus)
        pairwise_rows.extend(pairs)
        if extras:
            series.append(extras)
        if d["primary"]:
            series.append(
                {
                    "key": f"forest.{d['key']}",
                    "kind": "forest",
                    "title": f"Cliff's delta: {d['label']}",
                    "outcome": d["key"],
                    "items": [
                        {
                            "label": f"{r['group_a']} vs {r['group_b']}",
                            "delta": r["delta"],
                            "lo": r["delta_ci"][0] if r["delta_ci"] else None,
                            "hi": r["delta_ci"][1] if r["delta_ci"] else None,
                            "p_adj": r["p_adj"],
                        }
                        for r in pairs
                    ],
                },
            )
    # Family-wise control across confirmatory outcomes, FDR across exploratory ones.
    for family, method in (("confirmatory", "holm"), ("exploratory", design["exploratory_correction"])):
        rows = [r for r in omnibus_rows if r["family"] == family]
        for row, p_adj in zip(rows, stats.adjust([r["p"] for r in rows], method), strict=True):
            row["p_adj"] = p_adj
            row["correction"] = method
            row["significant"] = None if p_adj is None else p_adj < design["alpha"]
    blocked = data.blocked
    unit_text = (
        f"one {design['cell_aggregate']} per group {TIMES} {design['block_by']} cell; replicates are aggregated, "
        f"a run without a known {design['block_by']} is its own unit"
        if design["block_by"]
        else "runs; without a blocking factor replicates are not identified"
    )
    omnibus_table = _table(
        "omnibus",
        "Omnibus tests",
        [
            _col("outcome", "Outcome"),
            _col("family", "Family"),
            _col("test", "Test"),
            _col("groups", "Groups", "int"),
            _col("n", "n", "int"),
            _col("n_missing", "Missing", "int"),
            _col("units", "Units", "int"),
            _col("blocks_used", "Blocks used", "int"),
            _col("blocks_dropped", "Blocks dropped", "int"),
            _col("statistic", "Statistic", "float"),
            _col("df", "df", "int"),
            _col("p", "p", "p"),
            _col("p_adj", "p (adjusted)", "p"),
            _col("effect", "Kendall's W" if blocked else "ε²", "float"),
            _col("significant", f"p_adj < {design['alpha']}", "bool"),
            _col("note", "Note"),
        ],
        omnibus_rows,
        notes=[
            f"Friedman over complete blocks (per-cell {design['cell_aggregate']} of replicates), effect Kendall's W."
            if blocked
            else f"Kruskal-Wallis over units ({unit_text}), effect ε² = H / (units - 1).",
            "n counts runs with a value; units counts the values entering the test.",
            f"Adjustment: Holm across confirmatory outcomes, {design['exploratory_correction']} across exploratory.",
        ],
    )
    pairwise_table = _table(
        "pairwise",
        "Pairwise comparisons",
        [
            _col("outcome", "Outcome"),
            _col("group_a", "A"),
            _col("group_b", "B"),
            _col("n_a", "n A", "int"),
            _col("n_b", "n B", "int"),
            _col("n_missing_a", "Missing A", "int"),
            _col("n_missing_b", "Missing B", "int"),
            *(
                [_col("n_pairs", "Pairs", "int")]
                if blocked
                else [_col("units_a", "Units A", "int"), _col("units_b", "Units B", "int")]
            ),
            _col("statistic", "T (signed-rank)" if blocked else "U", "float"),
            _col("p", "p", "p"),
            _col("p_adj", f"p ({design['correction']})", "p"),
            _col("delta", "Cliff's δ", "float"),
            _col("delta_ci", "δ CI", "ci"),
            _col("magnitude", "Magnitude"),
            _col("a12", "Â12", "float"),
            *([_col("rank_biserial", "r (rank-biserial)", "float")] if blocked else []),
            _col("median_diff", "Median diff (A - B)", "float"),
            _col("better", "Better"),
            _col("note", "Note"),
        ],
        pairwise_rows,
        notes=[
            "δ > 0 means A tends to be larger. 'Better' uses the outcome's direction (lower is better for densities).",
            "Wilcoxon signed-rank on paired block means (zero differences dropped)."
            if blocked
            else "Mann-Whitney U on the same units as the omnibus test (two-sided, continuity-corrected; "
            "exact for small tie-free samples); δ, Â12 and the median difference use those units too.",
            "δ confidence intervals (percentile bootstrap) are computed for the primary outcome only.",
            "For binary outcomes Cliff's δ equals the risk difference.",
        ],
    )
    primary = next((r for r in omnibus_rows if r["key"] == design["primary_outcome"]), None)
    tables = [omnibus_table, pairwise_table]
    if data.blocked:
        tables.append(_unblocked_sensitivity(data))
    notes = [f"Design: {data.mode}. {ALPHA} = {design['alpha']}."]
    replicates = data.realized.get("replicates") or {}
    if not data.blocked and design["block_by"] and (replicates.get("max") or 0) > 1:
        notes.append(
            f"Replicates of one group {TIMES} {design['block_by']} cell are aggregated "
            f"({design['cell_aggregate']}) to one value before the unblocked tests: the cell, not the run, "
            "is the unit, so correlated replicates do not inflate n.",
        )
    elif not data.blocked and not design["block_by"]:
        notes.append(
            "No blocking factor: every run is a test unit. Replicates of one target spec (for example repeated "
            "generations of one app) are not identified and would be treated as independent.",
        )
    section = _section(
        "inferential",
        "Inferential results",
        description="Omnibus tests with effect sizes and pairwise comparisons with adjusted p-values (T6, F7, F8).",
        tables=tables,
        series=series,
        notes=notes,
    )
    return section, primary or {}


def _unblocked_sensitivity(data: StudyData) -> dict[str, Any]:
    """Kruskal-Wallis over every cell, ignoring the pairing of blocks: a sensitivity check for blocked designs.

    Complete-block analysis drops every block a group lacks; with attrition
    this can leave few blocks, so the unblocked result shows how much the
    conclusion depends on that deletion. Its units are the same group x
    block cells (replicates aggregated), never replicate runs. It is not
    part of either family.
    """
    rows = []
    for d in data.outcome_defs:
        by_group = _group_values(data, d["key"])
        present = [[(b, v) for b, v in by_group[g] if v is not None] for g in data.groups]
        present = [p for p in present if p]
        samples = [_cell_units(p, data.design) for p in present]
        result = stats.kruskal_wallis(samples) if len(samples) >= 2 else None
        rows.append(
            {
                "outcome": d["label"],
                "key": d["key"],
                "groups": len(samples),
                "n": sum(len(p) for p in present),
                "units": sum(len(s) for s in samples),
                "statistic": result["statistic"] if result else None,
                "df": result["df"] if result else None,
                "p": result["p"] if result else None,
                "epsilon_squared": result["epsilon_squared"] if result else None,
            },
        )
    return _table(
        "omnibus_unblocked",
        "Sensitivity: Kruskal-Wallis ignoring blocks",
        [
            _col("outcome", "Outcome"),
            _col("groups", "Groups", "int"),
            _col("n", "n", "int"),
            _col("units", "Units", "int"),
            _col("statistic", "H", "float"),
            _col("df", "df", "int"),
            _col("p", "p (unadjusted)", "p"),
            _col("epsilon_squared", "ε²", "float"),
        ],
        rows,
        notes=[
            f"Units are group {TIMES} {data.design['block_by']} cells ({data.design['cell_aggregate']} of "
            "replicates), including blocks the Friedman test drops; the pairing of blocks is ignored. "
            "Read next to the blocked Friedman results only.",
        ],
    )


def _findings_profile_section(data: StudyData, findings: list[dict[str, Any]]) -> dict[str, Any]:
    run_group = {str(t.canonical.id): t.group for t in data.analysis}
    groups = data.groups
    runs_per_group = Counter(t.group for t in data.analysis)
    static = [f for f in findings if not f["is_ai"] and f["counted"] and str(f["run_id"]) in run_group]

    severity_rows = []
    static_ok = Counter()
    sev_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for t in data.analysis:
        m = t.canonical.measurement or {}
        if _dig(m, "static", "available") is not True:
            continue
        static_ok[t.group] += 1
        for sev in SEVERITIES:
            sev_counts[t.group][sev] += _int(_dig(m, "static", "issues", "by_severity", sev)) or 0
    for g in groups:
        severity_rows.append(
            {
                "group": g,
                "runs": static_ok[g],
                **{s: sev_counts[g][s] if static_ok[g] else None for s in SEVERITIES},
                "total": sum(sev_counts[g].values()) if static_ok[g] else None,
            },
        )

    cwe_issues: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    cwe_runs: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    rule_findings: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    rule_runs: dict[str, dict[tuple[str, str], set]] = defaultdict(lambda: defaultdict(set))
    for f in static:
        g = run_group[str(f["run_id"])]
        for cwe in cwe_list(f["cwe"]):
            cwe_issues[g][str(cwe)].add(_issue_id(f))
            cwe_runs[g][str(cwe)].add(str(f["run_id"]))
        rule = (f["tool_slug"], f["rule_id"] or "")
        rule_findings[g][rule] += 1
        rule_runs[g][rule].add(str(f["run_id"]))
    cwe_rows, rule_rows = [], []
    for g in groups:
        ranked = sorted(cwe_issues[g].items(), key=lambda kv: (-len(kv[1]), kv[0]))[:10]
        for rank, (cwe, issues) in enumerate(ranked, start=1):
            cwe_rows.append(
                {
                    "group": g,
                    "rank": rank,
                    "cwe": cwe,
                    "issues": len(issues),
                    "runs_affected": len(cwe_runs[g][cwe]),
                    "share_of_runs": _share(len(cwe_runs[g][cwe]), runs_per_group[g]),
                },
            )
        ranked_rules = sorted(rule_findings[g].items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        for rank, ((tool, rule), n) in enumerate(ranked_rules, start=1):
            rule_rows.append(
                {
                    "group": g,
                    "rank": rank,
                    "tool": tool,
                    "rule": rule,
                    "findings": n,
                    "runs_affected": len(rule_runs[g][(tool, rule)]),
                },
            )
    totals = Counter()
    for g in groups:
        for cwe, issues in cwe_issues[g].items():
            totals[cwe] += len(issues)
    top = [cwe for cwe, _ in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:15]]
    heatmap = {
        "key": "cwe_heatmap",
        "kind": "heatmap",
        "title": "Issues per CWE and group",
        "rows": groups,
        "columns": top,
        "values": [[len(cwe_issues[g].get(c, ())) for c in top] for g in groups],
        "per_run": [[_share(len(cwe_issues[g].get(c, ())), runs_per_group[g]) for c in top] for g in groups],
    }
    return _section(
        "findings_profile",
        "Findings profile",
        description="Issue severities, top CWEs and top rules per group (counted static findings, F5).",
        tables=[
            _table(
                "severity_by_group",
                "Static issues by severity",
                [
                    _col("group", "Group"),
                    _col("runs", "Runs (static available)", "int"),
                    *[_col(s, s.capitalize(), "int") for s in SEVERITIES],
                    _col("total", "Total", "int"),
                ],
                severity_rows,
                notes=["Only runs whose required static tools succeeded (static.available) are summed."],
            ),
            _table(
                "top_cwes",
                "Top CWEs per group",
                [
                    _col("group", "Group"),
                    _col("rank", "#", "int"),
                    _col("cwe", "CWE"),
                    _col("issues", "Issues", "int"),
                    _col("runs_affected", "Runs affected", "int"),
                    _col("share_of_runs", "Share of runs", "pct"),
                ],
                cwe_rows,
            ),
            _table(
                "top_rules",
                "Top rules per group",
                [
                    _col("group", "Group"),
                    _col("rank", "#", "int"),
                    _col("tool", "Tool"),
                    _col("rule", "Rule"),
                    _col("findings", "Findings", "int"),
                    _col("runs_affected", "Runs affected", "int"),
                ],
                rule_rows,
            ),
        ],
        series=[heatmap],
    )


def _files_sent(sample) -> set[str] | None:
    """Files the reviewer showed *sample* (``coverage.files_sent``); ``None`` when it was not recorded."""
    sent = as_dict(sample.coverage).get("files_sent")
    if not isinstance(sent, list):
        return None
    return {str(p) for p in sent if p}


def _alpha_interpretation(alpha: float | None) -> str | None:
    if alpha is None:
        return None
    if alpha >= 0.8:
        return "reliable"
    if alpha >= 0.667:
        return "tentative"
    return "unreliable"


def _ai_reliability_section(data: StudyData, findings: list[dict[str, Any]]) -> dict[str, Any]:
    reviewer = (data.study.plan or {}).get("reviewer")
    analysed = [t for t in data.analysis if data.samples.get(t.canonical.id)]
    if not analysed:
        note = "The AI reviewer is disabled in the plan." if not (reviewer or {}).get("enabled") else "No AI samples."
        return _section("ai_reliability", "AI reviewer reliability", notes=[note])
    groups = data.groups
    per_sample: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for f in findings:
        if f["is_ai"] and f["review_sample_id"] and f["grounding"] != "invalid_file":
            per_sample[str(f["review_sample_id"])].add((f["file_path"] or "", f["category"] or ""))
    # The unit universe is fixed independently of what the samples reported: every file sent to
    # the reviewer x every category of the findings vocabulary. Building it only from reported
    # (file, category) pairs conditions on the outcome (no unit could be all-absent), which caps
    # alpha below 1 - 1/(k - 1) and turns perfect agreement into NA.
    reported_categories = {c for pairs in per_sample.values() for _, c in pairs}
    categories = [*REVIEW_CATEGORIES, *sorted(reported_categories - set(REVIEW_CATEGORIES))]
    units_by_group: dict[str, list[list[int | None]]] = defaultdict(list)
    runs_by_group: Counter[str] = Counter()
    files_by_group: Counter[str] = Counter()
    flagged_by_group: Counter[str] = Counter()
    sample_rows = []
    for t in analysed:
        samples = data.samples[t.canonical.id]
        ok = [s for s in samples if s.status == "succeeded"]
        if len(ok) < 2:
            continue
        runs_by_group[t.group] += 1
        shown = {str(s.id): _files_sent(s) for s in ok}
        reported = set().union(*(per_sample[str(s.id)] for s in ok))
        files = set().union(*(f for f in shown.values() if f is not None), {path for path, _ in reported})
        files_by_group[t.group] += len(files)
        for path in sorted(files):
            for category in categories:
                unit = (path, category)
                values: list[int | None] = []
                for s in samples:
                    sent = shown.get(str(s.id))
                    if s.status != "succeeded":
                        values.append(None)  # a failed sample is missing, not "absent"
                    elif unit in per_sample[str(s.id)]:
                        values.append(1)
                    elif sent is None or path in sent:
                        values.append(0)
                    else:
                        values.append(None)  # the sample never saw this file
                units_by_group[t.group].append(values)
                if unit in reported:
                    flagged_by_group[t.group] += 1
    for g in [*groups, ALL_LABEL]:
        ts = [t for t in analysed if g in (ALL_LABEL, t.group)]
        samples = [s for t in ts for s in data.samples[t.canonical.id]]
        status = Counter(s.status for s in samples)
        ai = [as_dict(_dig(t.canonical.measurement, "ai")) for t in ts]
        units = [u for gg in groups if g in (ALL_LABEL, gg) for u in units_by_group[gg]]
        alpha = stats.krippendorff_alpha_nominal(units)
        jaccard = stats.describe(_num(_dig(a, "agreement", "mean_pairwise_jaccard")) for a in ai)
        grounding = stats.describe(_num(a.get("grounding_rate")) for a in ai)
        sample_rows.append(
            {
                "group": g,
                "runs": len(ts),
                "samples": len(samples),
                "succeeded": status["succeeded"],
                "failed": len(samples) - status["succeeded"],
                "repaired": sum(1 for s in samples if s.repaired),
                "quorum_runs": sum(1 for a in ai if a.get("available") is True),
                "alpha_runs": sum(runs_by_group[gg] for gg in groups if g in (ALL_LABEL, gg)),
                "alpha_files": sum(files_by_group[gg] for gg in groups if g in (ALL_LABEL, gg)),
                "alpha_units": len(units),
                "alpha_flagged_units": sum(flagged_by_group[gg] for gg in groups if g in (ALL_LABEL, gg)),
                "alpha": alpha,
                "alpha_reading": _alpha_interpretation(alpha),
                "jaccard_n": jaccard["n"],
                "jaccard_median": jaccard["median"],
                "jaccard_mean": jaccard["mean"],
                "grounding_n": grounding["n"],
                "grounding_rate_median": grounding["median"],
                "grounding_rate_mean": grounding["mean"],
            },
        )
    overlap_values: dict[str, list[float]] = defaultdict(list)
    for t in analysed:
        overlap = _dig(t.canonical.measurement or {}, "ai", "overlap_with_static", default={})
        for k, v in as_dict(overlap).items():
            if not isinstance(v, bool) and _num(v) is not None:
                overlap_values[str(k)].append(_num(v))
    overlap_rows = [
        {"metric": k, "runs": len(vs), "mean": stats.mean(vs), "median": stats.median(vs), "total": math.fsum(vs)}
        for k, vs in sorted(overlap_values.items())
    ]
    run_group = {str(t.canonical.id): t.group for t in analysed}
    category_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for f in findings:
        if f["is_ai"] and f["counted"] and str(f["run_id"]) in run_group:
            category_counts[run_group[str(f["run_id"])]][f["category"] or "other"] += 1
    categories = sorted({c for cnt in category_counts.values() for c in cnt})
    return _section(
        "ai_reliability",
        "AI reviewer reliability",
        description="Sample success, inter-sample agreement, grounding and agreement with static tools (T9, F9).",
        tables=[
            _table(
                "reliability_by_group",
                "Reliability per group",
                [
                    _col("group", "Group"),
                    _col("runs", "Runs", "int"),
                    _col("samples", "Samples", "int"),
                    _col("succeeded", "Succeeded", "int"),
                    _col("failed", "Not succeeded", "int"),
                    _col("repaired", "Repaired", "int"),
                    _col("quorum_runs", "Runs with quorum", "int"),
                    _col("alpha_runs", f"{ALPHA} runs", "int"),
                    _col("alpha_files", f"{ALPHA} files", "int"),
                    _col("alpha_units", f"{ALPHA} units", "int"),
                    _col("alpha_flagged_units", "Units flagged", "int"),
                    _col("alpha", f"Krippendorff's {ALPHA}", "float"),
                    _col("alpha_reading", f"{ALPHA} reading"),
                    _col("jaccard_median", "Jaccard median", "float"),
                    _col("jaccard_mean", "Jaccard mean", "float"),
                    _col("grounding_rate_median", "Grounding median", "pct"),
                    _col("grounding_rate_mean", "Grounding mean", "pct"),
                ],
                sample_rows,
                notes=[
                    f"{ALPHA}: nominal presence (1) / absence (0) per (target, file, category) unit, over runs with "
                    ">= 2 successful samples. Units = every file sent to the reviewer (sample coverage; plus any "
                    f"file a sample reported) {TIMES} every category of the findings vocabulary "
                    f"({len(categories)}), so the universe does not depend on what was reported. Failed samples "
                    "and files a sample was not sent are missing values.",
                    "Units flagged = units at least one successful sample reported; the rest are agreed absences.",
                    f"Readings: {ALPHA} >= 0.8 reliable, >= 0.667 tentative (Krippendorff).",
                    "Jaccard and grounding rate come from each run's measurement.",
                ],
            ),
            _table(
                "overlap_with_static",
                "AI vs static overlap (per-run measurement)",
                [
                    _col("metric", "Metric"),
                    _col("runs", "Runs", "int"),
                    _col("mean", "Mean", "float"),
                    _col("median", "Median", "float"),
                    _col("total", "Total", "float"),
                ],
                overlap_rows,
            ),
            _table(
                "ai_categories",
                "Counted AI findings by category",
                [_col("group", "Group"), _col("total", "Total", "int"), *[_col(c, c, "int") for c in categories]],
                [
                    {
                        "group": g,
                        "total": sum(category_counts[g].values()),
                        **{c: category_counts[g][c] for c in categories},
                    }
                    for g in groups
                ],
            ),
        ],
        series=[
            {
                "key": "agreement",
                "kind": "bar",
                "title": f"Krippendorff's {ALPHA} by group",
                "items": [{"group": r["group"], "value": r["alpha"]} for r in sample_rows],
            },
        ],
    )


def _tool_overlap_section(data: StudyData, findings: list[dict[str, Any]]) -> dict[str, Any]:
    tools = data.static_tool_slugs()
    ok: dict[str, set[str]] = defaultdict(set)  # tool -> runs where it succeeded / was not applicable
    analysed_ids = [str(t.canonical.id) for t in data.analysis]
    for t in data.analysis:
        for r in data.results.get(t.canonical.id, []):
            if r.status in _OK_TOOL_STATUSES:
                ok[r.tool_slug].add(str(t.canonical.id))
    issues: dict[str, set[tuple[str, str]]] = defaultdict(set)
    detected_by: dict[tuple[str, str], set[str]] = defaultdict(set)
    for f in findings:
        if f["is_ai"] or not f["counted"] or f["tool_slug"] not in tools:
            continue
        issue = _issue_id(f)
        issues[f["tool_slug"]].add(issue)
        detected_by[issue].add(f["tool_slug"])
    pair_rows = []
    matrix = [[None] * len(tools) for _ in tools]
    for i, a in enumerate(tools):
        matrix[i][i] = 1.0 if issues[a] else None
        for j in range(i + 1, len(tools)):
            b = tools[j]
            runs = ok[a] & ok[b]
            ia = {x for x in issues[a] if x[0] in runs}
            ib = {x for x in issues[b] if x[0] in runs}
            value = stats.jaccard(ia, ib)
            matrix[i][j] = matrix[j][i] = value
            pair_rows.append(
                {
                    "tool_a": a,
                    "tool_b": b,
                    "runs": len(runs),
                    "issues_a": len(ia),
                    "issues_b": len(ib),
                    "shared": len(ia & ib),
                    "jaccard": value,
                },
            )
    all_ok = {rid for rid in analysed_ids if all(rid in ok[t] for t in tools)}
    in_scope = {issue: by for issue, by in detected_by.items() if issue[0] in all_ok}
    total = len(in_scope)
    unique_rows = []
    for t in tools:
        found = [issue for issue, by in in_scope.items() if t in by]
        unique = [issue for issue in found if in_scope[issue] == {t}]
        unique_rows.append(
            {
                "tool": t,
                "issues": len(found),
                "unique": len(unique),
                "unique_share": _share(len(unique), len(found)),
                "share_of_all": _share(len(found), total),
            },
        )
    upset = Counter(tuple(sorted(by)) for by in in_scope.values())
    # A pair of tools that both found nothing has an undefined Jaccard and says nothing about
    # overlap: the table keeps pairs where at least one tool has issues (the heatmap series
    # keeps the full matrix and every pair).
    shown = [r for r in pair_rows if r["issues_a"] or r["issues_b"]]
    pair_notes = ["Only runs where both tools succeeded (or were not applicable) are compared."]
    if len(shown) < len(pair_rows):
        silent = sorted({t for t in tools if not issues[t]})
        pair_notes.append(
            f"{len(pair_rows) - len(shown)} pair(s) where neither tool found a counted issue are omitted "
            f"(tools without issues: {', '.join(silent) or 'none'}); the full matrix is in the chart data.",
        )
    return _section(
        "tool_overlap",
        "Tool overlap",
        description="Pairwise Jaccard of counted issue sets and unique contributions per static tool (F6).",
        tables=[
            _table(
                "pairwise_jaccard",
                "Pairwise Jaccard of issue sets",
                [
                    _col("tool_a", "Tool A"),
                    _col("tool_b", "Tool B"),
                    _col("runs", "Runs (both OK)", "int"),
                    _col("issues_a", "Issues A", "int"),
                    _col("issues_b", "Issues B", "int"),
                    _col("shared", "Shared", "int"),
                    _col("jaccard", "Jaccard", "float"),
                ],
                shown,
                notes=pair_notes,
            ),
            _table(
                "unique_contributions",
                "Unique contributions",
                [
                    _col("tool", "Tool"),
                    _col("issues", "Issues detected", "int"),
                    _col("unique", "Only this tool", "int"),
                    _col("unique_share", "Unique share", "pct"),
                    _col("share_of_all", "Share of all issues", "pct"),
                ],
                unique_rows,
                notes=[
                    f"Over the {len(all_ok)} analysed runs where every static tool succeeded or was not applicable.",
                ],
            ),
        ],
        series=[
            {
                "key": "jaccard_matrix",
                "kind": "heatmap",
                "title": "Jaccard",
                "rows": tools,
                "columns": tools,
                "values": matrix,
                "pairs": pair_rows,
            },
            {
                "key": "upset",
                "kind": "upset",
                "title": "Issues by detecting tool set",
                "sets": tools,
                "intersections": [
                    {"tools": list(combo), "count": n}
                    for combo, n in sorted(upset.items(), key=lambda kv: (-kv[1], kv[0]))
                ],
            },
        ],
    )


_TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens")


def run_ai_cost(run, samples: list[Any], results: list[Any]) -> tuple[float | None, bool | None]:
    """``(AI review spend in USD, complete)`` of one run; ``(None, None)`` when no review spend is recorded.

    Spend is never assumed: a replicate whose ``usage.cost_usd`` is None
    (crashed, lost gateway log) is unknown, one flagged ``cost_complete:
    False`` / ``gateway_log_missing`` is a lower bound, and a review the
    executor marked ``cost_unknown`` (interrupted mid-review) spent an
    unknown amount. Each makes the run's figure a lower bound
    (``complete=False``); with no known amount at all the cost is None,
    never 0. Attempts replaced by retries count too
    (``summary.prior_attempts_cost_usd``).
    """
    known: list[float] = []
    complete = True
    evidence = False
    for s in samples:
        evidence = True
        usage = as_dict(s.usage)
        cost = _num(usage.get("cost_usd"))
        if cost is None:
            complete = False
        else:
            known.append(cost)
        if usage.get("cost_complete") is False or usage.get("gateway_log_missing"):
            complete = False
    sampled = {s.result_id for s in samples}
    for result in results:
        if result.tool_kind != "ai":
            continue
        summary = as_dict(result.summary)
        if summary.get("cost_unknown"):
            evidence, complete = True, False
        if result.id not in sampled and _num(summary.get("cost_usd")) is not None:
            # Spend kept only on the result (its replicate rows were never written).
            evidence = True
            known.append(_num(summary["cost_usd"]))
        if summary.get("prior_attempts"):
            evidence = True
            prior = _num(summary.get("prior_attempts_cost_usd"))
            if prior is not None:
                known.append(prior)
            if prior is None or summary.get("prior_attempts_cost_unknown"):
                complete = False
    if not evidence:
        ai = as_dict(as_dict(run.measurement).get("ai"))
        cost = _num(ai.get("cost_usd"))
        if cost is None:
            return None, None
        return cost, ai.get("cost_complete") is not False
    return (math.fsum(known) if known else None), complete


def _cost_total(costs: list[tuple[float | None, bool | None]]) -> tuple[float | None, bool | None, list[float]]:
    """``(total, complete, known per-run costs)`` over runs with recorded spend."""
    recorded = [(c, ok) for c, ok in costs if ok is not None]
    if not recorded:
        return None, None, []
    known = [c for c, _ in recorded if c is not None]
    complete = all(ok and c is not None for c, ok in recorded)
    return (math.fsum(known) if known else None), complete, known


def _cost_section(data: StudyData, findings: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    primary = data.design["primary_outcome"]
    groups = _sorted_labels(t.group for t in data.targets if t.runs)
    run_costs = {
        r.id: run_ai_cost(r, data.samples.get(r.id, []), data.results.get(r.id, [])) for r in data.runs
    }
    rows = []
    for g in [*groups, ALL_LABEL]:
        ts = [t for t in data.targets if t.runs and g in (ALL_LABEL, t.group)]
        runs = [r for t in ts for r in t.runs]
        total, complete, known = _cost_total([run_costs[r.id] for r in runs])
        tokens: dict[str, int | None] = dict.fromkeys(_TOKEN_KEYS)
        for r in runs:
            for s in data.samples.get(r.id, []):
                for k in _TOKEN_KEYS:
                    value = _int(as_dict(s.usage).get(k))
                    if value is not None:
                        # A count no replicate reported stays NA, never 0.
                        tokens[k] = (tokens[k] or 0) + value
        generation = stats.finite(_dig(t.meta, "generation", "cost") for t in ts)
        tool_seconds = stats.finite(res.duration_seconds for r in runs for res in data.results.get(r.id, []))
        wall = stats.finite(r.duration_seconds for r in runs)
        row = {
            "group": g,
            "targets": len(ts),
            "runs": len(runs),
            "ai_cost_total": total,
            "ai_cost_complete": complete,
            "ai_cost_per_run": stats.mean(known),
            "prompt_tokens": tokens["prompt_tokens"],
            "completion_tokens": tokens["completion_tokens"],
            "reasoning_tokens": tokens["reasoning_tokens"],
            "generation_cost_total": math.fsum(generation) if generation else None,
            "generation_cost_per_target": stats.mean(generation),
            "tool_seconds_total": math.fsum(tool_seconds) if tool_seconds else None,
            "run_seconds_median": stats.median(wall),
        }
        rows.append(row)
    counted_ai = sum(1 for f in findings if f["is_ai"] and f["counted"])
    all_row = rows[-1]
    analysed_ai, analysed_complete, _ = _cost_total([run_costs[t.canonical.id] for t in data.analysis])
    pareto = []
    for g in data.groups:
        row = next(r for r in rows if r["group"] == g)
        values = [data.values[t.canonical.id][primary] for t in data.analysis if t.group == g]
        pareto.append(
            {
                "group": g,
                "ai_cost_per_run": row["ai_cost_per_run"],
                "generation_cost_per_target": row["generation_cost_per_target"],
                "primary_median": stats.median(values),
            },
        )
    section = _section(
        "cost",
        "Cost and efficiency",
        description="AI review and generation spend, tokens and tool time (T9, F10).",
        tables=[
            _table(
                "cost_by_group",
                "Cost per group",
                [
                    _col("group", "Group"),
                    _col("targets", "Targets", "int"),
                    _col("runs", "Runs", "int"),
                    _col("ai_cost_total", "AI review cost", "usd"),
                    _col("ai_cost_complete", "AI cost complete", "bool"),
                    _col("ai_cost_per_run", "AI cost / run", "usd"),
                    _col("prompt_tokens", "Prompt tokens", "int"),
                    _col("completion_tokens", "Completion tokens", "int"),
                    _col("reasoning_tokens", "Reasoning tokens", "int"),
                    _col("generation_cost_total", "Generation cost", "usd"),
                    _col("generation_cost_per_target", "Generation cost / target", "usd"),
                    _col("tool_seconds_total", "Tool time (s)", "float"),
                    _col("run_seconds_median", "Run time median (s)", "float"),
                ],
                rows,
                notes=[
                    "Includes every run of the study (superseded and failed runs cost money too).",
                    "Where 'AI cost complete' is no, the AI cost is a lower bound: some replicate's spend is "
                    "unknown (crashed or interrupted review, calls without usage); NA means no spend is known.",
                ],
            ),
            _table(
                "cost_summary",
                "Cost summary",
                [_col("metric", "Metric"), _col("value", "Value", "usd"), _col("complete", "Complete", "bool")],
                [
                    {
                        "metric": "AI review cost (all runs)",
                        "value": all_row["ai_cost_total"],
                        "complete": all_row["ai_cost_complete"],
                    },
                    {
                        "metric": "AI review cost (analysis set)",
                        "value": analysed_ai,
                        "complete": analysed_complete,
                    },
                    {
                        "metric": "AI cost per counted AI finding (analysis set)",
                        "value": analysed_ai / counted_ai if counted_ai and analysed_ai is not None else None,
                        "complete": analysed_complete if counted_ai else None,
                    },
                    {
                        "metric": "Generation cost (targets with runs)",
                        "value": all_row["generation_cost_total"],
                        "complete": None,
                    },
                ],
                notes=["Complete = no: the value is a lower bound (some AI review spend is unknown)."],
            ),
        ],
        series=[
            {
                "key": "cost_vs_quality",
                "kind": "scatter",
                "title": "Cost vs primary outcome",
                "outcome": primary,
                "points": pareto,
            },
        ],
    )
    return section, {
        "ai_cost_total": all_row["ai_cost_total"],
        "ai_cost_complete": all_row["ai_cost_complete"],
        "generation_cost_total": all_row["generation_cost_total"],
    }


def _provenance_section(data: StudyData) -> dict[str, Any]:
    study = data.study
    plan = study.plan or {}
    engine = engine_provenance()
    planned_tools = {t.get("slug"): t for t in plan.get("tools") or [] if isinstance(t, dict)}
    analysed_results = [r for t in data.analysis for r in data.results.get(t.canonical.id, [])]
    tool_rows = []
    for slug in data.tool_slugs():
        rs = [r for r in analysed_results if r.tool_slug == slug]
        p = planned_tools.get(slug, {})
        tool_rows.append(
            {
                "tool": slug,
                "planned_version": p.get("version"),
                "observed_versions": _observed(r.tool_version for r in rs),
                "config_hashes": _observed(r.config_hash for r in rs) or _observed([p.get("config_hash")]),
                "spec_hash": p.get("spec_hash"),
                "image_refs": _observed(r.image_ref for r in rs),
            },
        )
    reviewer = plan.get("reviewer") if isinstance(plan.get("reviewer"), dict) else {}
    samples = [s for t in data.analysis for s in data.samples.get(t.canonical.id, [])]
    reviewer_rows = [
        {"key": "engine", "planned": reviewer.get("engine"), "observed": _observed(s.engine for s in samples)},
        {"key": "harness", "planned": reviewer.get("harness"), "observed": _observed(s.harness for s in samples)},
        {
            "key": "harness_version",
            "planned": reviewer.get("harness_version"),
            "observed": _observed(s.harness_version for s in samples),
        },
        {
            "key": "model",
            "planned": _dig(reviewer, "model", "slug"),
            "observed": _observed(s.model_slug for s in samples),
        },
        {
            "key": "model_canonical",
            "planned": _dig(reviewer, "model", "canonical_slug"),
            "observed": _observed(s.model_canonical_slug for s in samples),
        },
        {
            "key": "prompt_version",
            "planned": reviewer.get("prompt_version"),
            "observed": _observed(s.prompt_version for s in samples),
        },
        {
            "key": "prompt_sha256",
            "planned": reviewer.get("prompt_sha256"),
            "observed": _observed(s.prompt_sha256 for s in samples),
        },
        {
            "key": "schema_sha256",
            "planned": reviewer.get("schema_sha256"),
            "observed": _observed(s.schema_sha256 for s in samples),
        },
        {"key": "samples (k)", "planned": reviewer.get("samples"), "observed": []},
        {"key": "temperature", "planned": _dig(reviewer, "params", "temperature"), "observed": []},
        {"key": "pricing", "planned": _dig(reviewer, "model", "pricing"), "observed": []},
    ]
    run_rows = []
    canonical_ids = {t.canonical.id for t in data.targets if t.canonical is not None}
    in_analysis = {t.canonical.id for t in data.analysis}
    target_of = {r.id: t for t in data.targets for r in t.runs}
    for r in data.runs:
        t = target_of.get(r.id)
        run_rows.append(
            {
                "run_id": str(r.id),
                "job_id": target_job_id(r),
                "group": t.group if t else None,
                "block": t.block if t else None,
                "status": r.status,
                "is_complete": r.is_complete,
                "plan_hash": r.plan_hash,
                "sample_hash": r.sample_hash,
                "attempt": r.attempt,
                "started_at": _iso(r.started_at),
                "completed_at": _iso(r.completed_at),
                "canonical": r.id in canonical_ids,
                "in_analysis": r.id in in_analysis,
            },
        )
    started = sorted(_iso(r.started_at) for r in data.runs if r.started_at)
    finished = sorted(_iso(r.completed_at) for r in data.runs if r.completed_at)
    observed_by_stage: dict[str, set[str]] = defaultdict(set)
    for t in data.analysis:
        for stage, version in run_engine_versions(t.canonical).items():
            observed_by_stage[stage].add(version)
    engine_observed = {stage: sorted(vs) for stage, vs in sorted(observed_by_stage.items())} or None
    kv = [
        {"key": "study_id", "value": str(study.id)},
        {"key": "protocol_hash", "value": study.protocol_hash or None},
        {"key": "plan_hash", "value": data.plan_hash or None},
        {"key": "plan_hash_source", "value": data.plan_hash_source},
        {"key": "analyzer_image", "value": _dig(plan, "analyzer_image", "tag")},
        {"key": "engine_versions", "value": _dig(plan, "engine_versions")},
        {"key": "engine_versions_observed", "value": engine_observed},
        {"key": "mapping_hashes", "value": _dig(plan, "mapping_hashes")},
        {"key": "report_schema_version", "value": REPORT_SCHEMA_VERSION},
        {"key": "stats_version", "value": stats.STATS_VERSION},
        {"key": "platform_git_sha", "value": engine["git"]["sha"]},
        {"key": "platform_git_ref", "value": engine["git"]["ref"]},
        {"key": "first_run_started", "value": started[0] if started else None},
        {"key": "last_run_completed", "value": finished[-1] if finished else None},
        {"key": "targets_source", "value": data.planned_source},
    ]
    return _section(
        "provenance",
        "Provenance appendix",
        description="Exact toolchain, reviewer, plan and runs behind every number (appendix).",
        tables=[
            _table("provenance", "Study provenance", [_col("key", "Key"), _col("value", "Value", "code")], kv),
            _table(
                "toolchain",
                "Toolchain (T2)",
                [
                    _col("tool", "Tool"),
                    _col("planned_version", "Planned version"),
                    _col("observed_versions", "Observed versions", "list"),
                    _col("config_hashes", "Config hashes", "list"),
                    _col("spec_hash", "Spec hash", "hash"),
                    _col("image_refs", "Image", "list"),
                ],
                tool_rows,
            ),
            _table(
                "reviewer",
                "AI reviewer",
                [_col("key", "Setting"), _col("planned", "Planned", "code"), _col("observed", "Observed", "list")],
                reviewer_rows,
            ),
            _table(
                "runs",
                "Runs",
                [
                    _col("run_id", "Run", "hash"),
                    _col("job_id", "Job", "hash"),
                    _col("group", "Group"),
                    _col("block", "Block"),
                    _col("status", "Status", "status"),
                    _col("is_complete", "Complete", "bool"),
                    _col("plan_hash", "Plan", "hash"),
                    _col("sample_hash", "Sample", "hash"),
                    _col("attempt", "Attempt", "int"),
                    _col("started_at", "Started"),
                    _col("completed_at", "Completed"),
                    _col("canonical", "Canonical", "bool"),
                    _col("in_analysis", "Analysed", "bool"),
                ],
                run_rows,
            ),
        ],
    )


def _overview_section(data: StudyData, summary: dict[str, Any]) -> dict[str, Any]:
    rows = [
        {"metric": "Study", "value": data.study.name},
        {"metric": "Status", "value": data.study.status},
        {"metric": "Grouping", "value": f" {TIMES} ".join(data.design["group_by"])},
        {"metric": "Blocking", "value": data.design["block_by"] or "none"},
        {"metric": "Design", "value": data.mode},
        {"metric": "Targets (planned / with run / analysed)", "value": summary["targets_text"]},
        {"metric": "Complete runs in analysis set", "value": summary["complete"]},
        {"metric": "Groups", "value": ", ".join(data.groups) or "none"},
        {"metric": "Primary outcome", "value": summary["primary_label"]},
        {"metric": "Primary omnibus", "value": summary["primary_text"]},
        {"metric": "Deviations (major / warn / info)", "value": summary["deviations_text"]},
    ]
    return _section(
        "overview",
        "Summary",
        description="Key figures; read together with the deviations and data-quality sections.",
        tables=[_table("key_figures", "Key figures", [_col("metric", "Metric"), _col("value", "Value")], rows)],
    )


def _p_text(p: Any) -> str:
    if p is None:
        return "NA"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def build_report(study) -> dict[str, Any]:
    """The post-run report of *study* (see the module docstring for layout and rules)."""
    data = collect(study)
    analysis_ids = [t.canonical.id for t in data.analysis]
    findings = _findings(analysis_ids)
    devs = _deviations(data)
    inferential, primary_omnibus = _inferential_section(data)
    cost, cost_totals = _cost_section(data, findings)
    sections = [
        _deviations_section(devs),
        _data_quality_section(data),
        _sample_section(data),
        _descriptives_section(data),
        _findings_profile_section(data, findings),
        inferential,
        _ai_reliability_section(data, findings),
        _tool_overlap_section(data, findings),
        cost,
        _provenance_section(data),
    ]
    severity_counts = Counter(d["severity"] for d in devs)
    primary_def = next((d for d in data.outcome_defs if d["primary"]), None)
    planned = len(data.planned) if data.planned else None
    with_run = sum(1 for t in data.targets if t.runs)
    summary = {
        "targets_planned": planned,
        "targets_eligible": sum(1 for p in data.planned.values() if p.get("eligible")) if data.planned else None,
        "targets_with_run": with_run,
        "analysed": len(data.analysis),
        "complete": sum(1 for t in data.analysis if t.canonical.is_complete),
        "groups": data.groups,
        "mode": data.mode,
        "primary_outcome": data.design["primary_outcome"],
        "primary_label": primary_def["label"] if primary_def else data.design["primary_outcome"],
        "primary_omnibus": {
            k: primary_omnibus.get(k) for k in ("test", "statistic", "df", "p", "p_adj", "effect", "effect_name", "n")
        },
        "deviations": {s: severity_counts.get(s, 0) for s in ("major", "warn", "info")},
        **cost_totals,
    }
    summary["targets_text"] = f"{planned if planned is not None else 'NA'} / {with_run} / {len(data.analysis)}"
    po = summary["primary_omnibus"]
    summary["primary_text"] = (
        f"{po['test']}: statistic {po['statistic']:.3f}, p = {_p_text(po['p'])}, "
        f"{po['effect_name']} = {po['effect']:.3f}"
        if po.get("statistic") is not None and po.get("effect") is not None
        else f"{po.get('test') or 'test'} not computable"
    )
    summary["deviations_text"] = " / ".join(str(summary["deviations"][s]) for s in ("major", "warn", "info"))
    sections.insert(0, _overview_section(data, summary))
    stored = study.protocol_hash or None
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "generated_at": timezone.now().isoformat(),
        "study": {
            "id": str(study.id),
            "name": study.name,
            "description": study.description,
            "status": study.status,
            "locked_at": _iso(study.locked_at),
            "launched_at": _iso(study.launched_at),
            "completed_at": _iso(study.completed_at),
        },
        "protocol_hash": stored,
        "protocol_integrity": protocol_integrity(study),
        "plan_hash": data.plan_hash or None,
        "settings": {
            **data.design,
            "mode": data.mode,
            "planned_mode": data.planned_mode,
            "outcomes": [d["key"] for d in data.outcome_defs],
            "outcome_definitions": data.outcome_defs,
            "analysis_plan": _analysis_plan(data.design, data.mode),
            "notes": data.outcome_notes,
            "research_questions": data.research_questions,
            "research_questions_source": data.research_questions_source,
        },
        "inputs": {
            "runs": len(data.runs),
            "analysis_run_ids": sorted(str(i) for i in analysis_ids),
            "findings_read": len(findings),
        },
        "summary": summary,
        "sections": sections,
    }
    report = jsonable(report)
    report["report_hash"] = report_hash(report)
    return report


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def _fmt_float(value: float) -> str:
    if value == 0:
        return "0"
    if float(value).is_integer() and abs(value) < 1e15:
        return f"{int(value)}"
    size = abs(value)
    if size >= 100:
        return f"{value:.1f}"
    if size >= 1:
        return f"{value:.2f}"
    if size >= 0.001:
        return f"{value:.3f}"
    return f"{value:.2e}"


def format_cell(value: Any, kind: str = "text") -> str:  # noqa: PLR0911 - one return per kind
    """Thesis-ready text for one table cell (``NA`` for missing, never 0)."""
    if value is None or value in ("", []):
        return "NA" if kind not in ("text", "list", "code") or value is None else ""
    if kind == "pct" and isinstance(value, (int, float)):
        return f"{value * 100:.1f}%"
    if kind == "p" and isinstance(value, (int, float)):
        return _p_text(value)
    if kind in ("ci", "pct_ci") and isinstance(value, (list, tuple)) and len(value) == 2:
        lo, hi = value
        if lo is None or hi is None:
            return "NA"
        if kind == "pct_ci":
            return f"[{lo * 100:.1f}%, {hi * 100:.1f}%]"
        return f"[{_fmt_float(lo)}, {_fmt_float(hi)}]"
    if kind == "usd" and isinstance(value, (int, float)):
        return f"${value:,.2f}" if abs(value) >= 1 else f"${value:.4f}"
    if kind == "hash" and isinstance(value, str):
        return value[:12]
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return _fmt_float(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(format_cell(v, "text") for v in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _md_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def table_to_markdown(table: dict[str, Any]) -> str:
    """A GitHub-flavoured Markdown table (title, rows, notes)."""
    columns = table.get("columns") or []
    lines = [f"**{_md_escape(table.get('title') or table.get('key') or '')}**", ""]
    if table.get("description"):
        lines += [table["description"], ""]
    rows = table.get("rows") or []
    if not rows:
        lines.append("_No rows._")
    else:
        lines.append("| " + " | ".join(_md_escape(c["label"]) for c in columns) + " |")
        lines.append(
            "| "
            + " | ".join("---:" if c.get("kind") in ("int", "float", "pct", "p", "usd") else "---" for c in columns)
            + " |",
        )
        lines.extend(
            "| "
            + " | ".join(_md_escape(format_cell(row.get(c["key"]), c.get("kind", "text"))) for c in columns)
            + " |"
            for row in rows
        )
    notes = [n for n in table.get("notes") or [] if n]
    if notes:
        lines.append("")
        lines.extend(f"_{_md_escape(n)}_" for n in notes)
    return "\n".join(lines)


EXCLUSION_RULE_TEXT: dict[str, tuple[str, str]] = {
    # rule: (text when on, text when off)
    "exclude_invalid": (
        "Targets the generator rated invalid (validity: exclude) are excluded.",
        "Targets the generator rated invalid are kept (flagged).",
    ),
    "exclude_empty_output": (
        "Targets where the model wrote no source code are excluded.",
        "Targets where the model wrote no source code are kept (flagged).",
    ),
    "exclude_truncated": (
        "Targets whose generation output was cut off (truncated: responses that hit the output cap) are "
        "excluded.",
        "Targets whose generation output was cut off are kept (flagged).",
    ),
    "exclude_run_limits": (
        "Agent runs that ended at a run-level limit (run_stopped: wall clock, request/token/cost budget, "
        "memory cap) are excluded (e.g. a sensitivity analysis).",
        "Agent runs that ended at a run-level limit (run_stopped: wall clock, request/token/cost budget, "
        "memory cap) are kept: they are valid samples, and dropping them would favour models that finish "
        "quickly. run_stopped is not output truncation.",
    ),
    "require_functional": (
        "Only targets with a known functional (conformance) pass rate are included.",
        "Targets without a functional pass rate are kept (functional outcomes are NA for them).",
    ),
}


def _exclusion_rules_markdown(protocol: dict[str, Any]) -> list[str]:
    """The effective exclusion rules in words, with how many targets stopped at a run-level limit."""
    rules = as_dict(protocol.get("exclusion_rules"))
    lines = ["**Exclusion rules**", ""]
    for rule, (on, off) in EXCLUSION_RULE_TEXT.items():
        if rule in rules:
            lines.append(f"- `{rule}` = {format_cell(bool(rules[rule]), 'bool')}: {on if rules[rule] else off}")
    minimum = rules.get("min_measured_sloc")
    if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
        lines.append(
            f"- `min_measured_sloc` = {minimum}: "
            + ("targets with fewer measured SLOC are excluded." if minimum else "no minimum size."),
        )
    resolved = [t for t in _dig(protocol, "targets", "resolved", default=[]) or [] if isinstance(t, dict)]
    stopped = [
        t
        for t in resolved
        if any(isinstance(r, dict) and r.get("code") == "run_stopped" for r in t.get("reasons") or [])
    ]
    if stopped:
        kept = sum(1 for t in stopped if t.get("eligible"))
        lines.append(
            f"- {len(stopped)} target(s) ended at a run-level limit (run_stopped): {kept} kept, "
            f"{len(stopped) - kept} excluded.",
        )
    return lines


def protocol_to_markdown(protocol: dict[str, Any]) -> str:
    """The locked protocol as thesis-ready Markdown (design, toolchain, analysis plan, controls)."""
    study = protocol.get("study") or {}
    lines = [f"# Study protocol: {study.get('name', '')}", ""]
    lines.append(f"- Protocol schema: {protocol.get('schema_version')} · generated {protocol.get('generated_at')}")
    lines.append(f"- Plan hash: `{protocol.get('plan_hash') or 'NA'}`")
    git = _dig(protocol, "platform", "git", default={})
    lines.append(f"- Platform git SHA: `{git.get('sha') or 'unknown'}`")
    if study.get("description"):
        lines += ["", study["description"]]
    rqs = protocol.get("research_questions") or []
    if rqs:
        lines += ["", "## Research questions", ""]
        lines += [f"- **{rq.get('id')}** ({rq.get('kind')}): {rq.get('text')}" for rq in rqs]
    design = protocol.get("design") or {}
    counts = _dig(protocol, "targets", "counts", default={})
    lines += ["", "## Design (T1)", ""]
    grouping = f" {TIMES} ".join(design.get("group_by") or [])
    lines.append(
        f"Grouping: {grouping}; blocking: {design.get('block_by') or 'none'}; "
        f"mode: {design.get('mode')} ({design.get('reason')}).",
    )
    lines.append(
        f"Targets: {counts.get('total', 0)} resolved, {counts.get('eligible', 0)} eligible, "
        f"{counts.get('excluded', 0)} excluded.",
    )
    group_rows = [{"group": g, "n": n} for g, n in (design.get("groups") or {}).items()]
    lines += [
        "",
        table_to_markdown(
            {
                "title": "Eligible targets per group",
                "columns": [_col("group", "Group"), _col("n", "n", "int")],
                "rows": group_rows,
            },
        ),
    ]
    lines += ["", *_exclusion_rules_markdown(protocol)]
    reason_rows = [{"reason": k, "n": v} for k, v in (counts.get("by_reason") or {}).items()]
    if reason_rows:
        lines += [
            "",
            table_to_markdown(
                {
                    "title": "Exclusion reasons",
                    "columns": [_col("reason", "Reason"), _col("n", "Targets", "int")],
                    "rows": reason_rows,
                },
            ),
        ]
    outcomes = protocol.get("outcomes") or {}
    lines += ["", "## Outcomes", ""]
    lines.append(f"Primary outcome: **{outcomes.get('primary')}**.")
    lines += [
        "",
        table_to_markdown(
            {
                "title": "Outcome definitions",
                "columns": [
                    _col("key", "Key", "code"),
                    _col("label", "Outcome"),
                    _col("family", "Family"),
                    _col("unit", "Unit"),
                    _col("higher_is_better", "Higher is better", "bool"),
                ],
                "rows": outcomes.get("definitions") or [],
            },
        ),
    ]
    lines += [
        "",
        "## Toolchain (T2)",
        "",
        table_to_markdown(
            {
                "title": f"Analyzer image {_dig(protocol, 'analyzer_image', 'tag') or 'NA'}",
                "columns": [
                    _col("slug", "Tool"),
                    _col("version", "Version"),
                    _col("category", "Category"),
                    _col("required", "Required", "bool"),
                    _col("config_hash", "Config hash", "hash"),
                ],
                "rows": protocol.get("toolchain") or [],
            },
        ),
    ]
    reviewer = protocol.get("reviewer") or {}
    if reviewer.get("enabled"):
        rows = [
            {"key": "engine / harness", "value": f"{reviewer.get('engine')} / {reviewer.get('harness') or '-'}"},
            {"key": "model", "value": _dig(reviewer, "model", "canonical_slug") or _dig(reviewer, "model", "slug")},
            {"key": "samples (k)", "value": reviewer.get("samples")},
            {"key": "temperature", "value": _dig(reviewer, "params", "temperature")},
            {
                "key": "prompt",
                "value": f"{reviewer.get('prompt_version')} ({str(reviewer.get('prompt_sha256'))[:12]})",
            },
            {"key": "budget", "value": reviewer.get("budget")},
        ]
        lines += [
            "",
            table_to_markdown(
                {"title": "AI reviewer", "columns": [_col("key", "Setting"), _col("value", "Value")], "rows": rows},
            ),
        ]
    controls = protocol.get("controls") or {}
    lines += ["", "## Controls (canary)", ""]
    lines.append(
        f"Source: {controls.get('source') or 'none'}; overall passed: {format_cell(controls.get('passed'), 'bool')}.",
    )
    if controls.get("tools"):
        untested = [
            str(t.get("slug"))
            for t in controls["tools"]
            if isinstance(t, dict) and t.get("passed") is None and t.get("slug")
        ]
        lines += [
            "",
            table_to_markdown(
                {
                    "title": "Canary per tool",
                    "columns": [
                        _col("slug", "Tool"),
                        _col("status", "Status"),
                        _col("verdict", "Verdict"),
                        _col("passed", "Passed", "bool"),
                        _col("config_hash", "Config hash", "hash"),
                        _col("missing", "Missing", "list"),
                    ],
                    "rows": controls["tools"],
                    "notes": [
                        f"No passing control at the plan's configuration for: {', '.join(untested)} "
                        "(config_untested: only another configuration was self-tested; untested: no self-test). "
                        "Zero findings from these tools carry no positive control.",
                    ]
                    if untested
                    else [],
                },
            ),
        ]
    plan = protocol.get("analysis_plan") or {}
    lines += ["", "## Analysis plan", ""]
    lines += [f"- {k}: {format_cell(v)}" for k, v in plan.items()]
    estimates = protocol.get("estimates") or {}
    if estimates:
        lines += ["", "## Estimates", ""]
        lines += [f"- {k}: {format_cell(v)}" for k, v in estimates.items()]
    threats = protocol.get("threats") or []
    if threats:
        lines += [
            "",
            "## Anticipated threats",
            "",
            table_to_markdown(
                {
                    "title": "Threats and mitigations",
                    "columns": [_col("kind", "Kind"), _col("threat", "Threat"), _col("mitigation", "Mitigation")],
                    "rows": threats,
                },
            ),
        ]
    return "\n".join(lines).rstrip() + "\n"


def _integrity_text(integrity: Any) -> str:
    integrity = as_dict(integrity)
    match = integrity.get("match")
    if match is True:
        return "the protocol matched its sha256"
    if match is False:
        return (
            f"**the protocol DID NOT MATCH its sha256** (stored `{str(integrity.get('stored'))[:12]}`, "
            f"recomputed `{str(integrity.get('recomputed'))[:12]}`): the cited pre-registration cannot be verified"
        )
    if integrity.get("recomputed") and not integrity.get("stored"):
        return "the protocol had no stored sha256 (unverifiable)"
    return "no locked protocol"


def to_markdown(report: dict[str, Any], protocol: dict[str, Any] | None = None) -> str:
    """Thesis-ready Markdown of a report (and of its protocol, when given)."""
    study = report.get("study") or {}
    lines = [f"# Analysis report: {study.get('name') or (protocol or {}).get('study', {}).get('name', '')}", ""]
    if report:
        lines.append(f"- Report hash: `{report.get('report_hash')}` (schema {report.get('schema_version')})")
        lines.append(f"- Protocol hash: `{report.get('protocol_hash') or 'none'}`")
        lines.append(f"- Protocol integrity at generation: {_integrity_text(report.get('protocol_integrity'))}")
        lines.append(f"- Plan hash: `{report.get('plan_hash') or 'none'}`")
        lines.append(f"- Generated: {report.get('generated_at')}")
        lines.append("- Missing values are shown as NA; they are never counted as 0.")
    if protocol:
        body = protocol_to_markdown(protocol).split("\n", 1)[1]
        lines += ["", "## Protocol", "", body.replace("\n## ", "\n### ")]
    for section in report.get("sections") or []:
        lines += ["", f"## {section.get('title')}", ""]
        if section.get("description"):
            lines += [section["description"], ""]
        lines.extend(f"- {n}" for n in section.get("notes") or [] if n)
        for table in section.get("tables") or []:
            lines += ["", table_to_markdown(table)]
    return "\n".join(lines).rstrip() + "\n"
