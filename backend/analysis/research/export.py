"""Tidy tables and a replication package for a study (owner: reports stage).

:func:`tables` returns one list of row dicts per table, using exactly the
target selection of :mod:`backend.analysis.research.report` (``canonical``
/ ``in_analysis`` flags), so exported data and the report agree:

* ``runs`` - one row per study run (superseded and failed runs included),
  with the target's identity snapshot from ``run.target_meta``.
* ``tool_executions`` - one row per ToolResult.
* ``findings`` - one row per Finding (static and AI, counted or not).
* ``issues`` - one row per (run, issue key) over counted findings.
* ``review_samples`` - one row per AI review replicate.
* ``measurements`` - one row per run with a measurement, flattened to dotted columns;
  sparse count maps (``by_category`` / ``by_tool`` / ``by_language`` / ``by_origin``) are
  filled to every key of the study: 0 where the zero was measured, empty (NA) where not.
* ``outcomes`` - one row per analysis-set run: group, block and every outcome.

CSV conventions (:func:`to_csv`): UTF-8, comma separated, ``\\n`` line
ends, a header row; an empty cell is missing data (NA), never zero;
booleans are ``true``/``false``; lists and objects are JSON.

:func:`replication_zip` bundles the tables with the locked protocol, the
stored report (JSON + Markdown; never rebuilt at export time), the frozen
plan, a codebook, a README and ``SHA256SUMS``.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import zipfile
from collections import defaultdict
from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Iterable
from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Count
from django.db.models import Q
from django.utils import timezone

from backend.analysis.engine.normalize import FINDING_CATEGORIES
from backend.analysis.engine.reviewer.prompt import CATEGORIES as REVIEW_CATEGORIES
from backend.analysis.engine.sample import truncation_flags
from backend.analysis.models import SEVERITY_RANK
from backend.analysis.models import Finding
from backend.analysis.research import report as report_mod

EXPORT_SCHEMA_VERSION = 1
TABLE_NAMES = ("runs", "tool_executions", "findings", "issues", "review_samples", "measurements", "outcomes")
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)  # fixed member timestamps: identical inputs give identical members
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_OK_TOOL_STATUSES = ("succeeded", "not_applicable")
# Free-text columns whose content comes from analysed apps, tools, LLM output or user-entered
# labels. Pass this as ``to_csv(..., escape_formulas=UNTRUSTED_TEXT_COLUMNS)`` for a file meant
# to be opened in a spreadsheet: only these cells are neutralised, identifiers and numbers stay
# verbatim. Research tables (the replication package) are written verbatim.
UNTRUSTED_TEXT_COLUMNS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "suggestion",
        "code_snippet",
        "file_path",
        "rule_id",
        "rule_ids",
        "native_severity",
        "error_message",
        "failure_reason",
        "target_label",
        "model_name",
        "app_name",
        "condition_label",
        "termination",
    },
)

META_COLUMNS: tuple[str, ...] = (
    "mode",
    "model_id",
    "model_slug",
    "model_name",
    "model_provider",
    "generator_family",
    "harness",
    "harness_version",
    "stack_slug",
    "app_slug",
    "app_name",
    "prompt_hash",
    "bundle_key",
    "config_fingerprint",
    "experiment_id",
    "condition_id",
    "condition_label",
    "batch_id",
    "repeat_index",
    "seed",
    "termination",
)

CODEBOOK: dict[str, dict[str, str]] = {
    "runs": {
        "run_id": "AnalysisRun id.",
        "study_id": "AnalysisStudy id.",
        "target_key": "Generation job id (or inline:<run id> for an inline sample).",
        "job_id": "Generation job id (from the target_meta snapshot when the job was deleted later).",
        "target_label": "Human-readable target label used in the report.",
        "group": "Group label per the study's group_by over target_meta.",
        "block": "Block label per the study's block_by (empty when not blocked).",
        "canonical": "This run is the target's canonical run (latest finished run of the study plan).",
        "in_analysis": "This run is in the report's analysis set.",
        "exclusion": "Why the target is not in the analysis set (on its canonical run).",
        "status": "Run status: completed | partial | failed | cancelled | queued | running.",
        "is_complete": "Every required tool succeeded or was not applicable.",
        "plan_hash": "Frozen plan hash; equal hashes are comparable.",
        "sample_hash": "sha256 over the analysed files (path + sha256).",
        "measured_sloc": "Code lines in the measured scope (from the measurement).",
        "validity_valid": "Generation validity flag from target_meta.",
        "validity_exclude": "Generation validity says exclude.",
        "truncated": "Generation output was truncated (output caps only; a run-level stop is run_stopped).",
        "truncation_reasons": "Output truncation reason codes, ';'-separated.",
        "run_stopped": "The agent run ended at a run-level limit (wall clock or request/token/cost budget).",
        "run_stop_reasons": "Run-level stop reason codes (termination:* / budget_exhausted:*), ';'-separated.",
        "empty_output": "Nothing authored/added/modified by the model.",
        "functional_status": "Conformance-test status.",
        "functional_pass_rate": "Conformance-test pass rate (empty = unknown, not 0).",
        "generation_cost": "Generation cost in USD.",
        "*": "Other columns copy target_meta keys of the same name (identity snapshot at submission).",
    },
    "tool_executions": {
        "status": "succeeded | failed | timed_out | not_applicable | skipped | cancelled | pending | running.",
        "failure_reason": "tool_error | timeout | oom | parse_error | infra_error | unavailable | ...",
        "required": "The tool is required by the plan (its failure makes the run partial).",
        "tool_version": "Version from the analyzer image manifest.",
        "image_ref": "Analyzer image the tool ran in.",
        "config_hash": "sha256 of the effective tool config.",
        "argv": "Exact command line (JSON list).",
        "files_in_scope": "Files of the tool's languages in the measured scope.",
        "files_scanned": "Files the tool reports as scanned.",
        "unparseable": "Files the tool could not parse (coverage, not findings).",
        "findings": "Findings stored for this execution.",
        "counted_findings": "Findings that count toward outcomes.",
    },
    "findings": {
        "severity": "Normalized severity (critical | high | medium | low | info).",
        "native_severity": "The tool's own severity token.",
        "category": "Normalized category.",
        "cwe": "CWE ids, ';'-separated.",
        "file_origin": "Who wrote the line: authored | added | modified | scaffold | platform | unknown.",
        "in_measured_scope": "The finding points at measured code.",
        "counted": "Counts toward outcomes (static: in scope and not style; AI: grounded consensus).",
        "fingerprint": "Content-based fingerprint (stable across line shifts).",
        "issue_key": "Cross-tool issue (dedup cluster) key.",
        "is_ai": "Produced by the AI reviewer (an opinion, not a measurement).",
        "review_sample_index": "AI replicate index (empty for consensus rows and static findings).",
        "grounding": "AI grounding: exact | relocated | ungrounded | invalid_file | out_of_range.",
        "is_consensus": "AI finding is in the consensus of the samples.",
    },
    "issues": {
        "issue_key": "Dedup cluster key (finding:<id> when the finding has none).",
        "tools": "Tools detecting the issue, ';'-separated.",
        "n_findings": "Counted findings in the issue.",
        "severity": "Highest severity among the issue's findings.",
    },
    "review_samples": {
        "status": "succeeded | failed | invalid_output | truncated | budget_exhausted | timed_out | cancelled.",
        "file_order_seed": "Seed of the per-sample file order shuffle.",
        "cost_usd": "Cost of the sample's LLM calls in USD.",
        "repaired": "One schema-repair retry was used.",
    },
    "measurements": {
        "*": "Dotted path into AnalysisRun.measurement (schema_version 1); lists/objects as JSON.",
        "*.by_category.*, *.by_tool.*, sloc.by_language.*, sloc.by_origin.*": (
            "Count maps, stored sparsely in the measurement, are filled to every key of the study: 0 where "
            "the zero was measured (the category's tools ran / the tool succeeded or was not applicable / the "
            "AI review was available / SLOC was measured), empty (NA) where it was not."
        ),
    },
    "outcomes": {
        "*": "Outcome values from measurement.outcomes(); empty = NA (a required tool failed or SLOC too small).",
    },
}


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------


def _cell(value: Any, *, escape_formulas: bool) -> str:  # noqa: PLR0911 - one return per value type
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else ""
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple, dict, set)):
        return report_mod.canonical_json(value)
    text = str(value)
    if escape_formulas and text.startswith(_FORMULA_PREFIXES):
        # Spreadsheet formula injection guard (tool messages and file paths are untrusted text).
        return "'" + text
    return text


def to_csv(
    rows: list[dict[str, Any]],
    *,
    columns: list[str] | None = None,
    escape_formulas: bool | Collection[str] = False,
) -> str:
    """CSV text for *rows*; columns are *columns* or the union of row keys in first-seen order.

    ``escape_formulas`` prefixes text cells starting with ``= + - @`` (or a
    tab / carriage return) with ``'`` for spreadsheet safety: ``True``
    escapes every text cell, a collection of column names (for example
    :data:`UNTRUSTED_TEXT_COLUMNS`) only those columns. Leave it off for
    research data: escaping alters values.
    """
    if columns is None:
        columns = list(dict.fromkeys(key for row in rows for key in row))
    if isinstance(escape_formulas, bool):
        escaped = [escape_formulas] * len(columns)
    else:
        selected = set(escape_formulas)
        escaped = [c in selected for c in columns]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(c), escape_formulas=e) for c, e in zip(columns, escaped, strict=True)])
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and value:
        out: dict[str, Any] = {}
        for key in sorted(value, key=str):
            out.update(_flatten(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return out
    return {prefix: value} if prefix else {}


def _filled(counts: dict[str, Any], keys: Iterable[str], measured: Callable[[str], bool]) -> dict[str, Any]:
    """*counts* (a sparse count map) with every key in *keys*: a missing key is 0 when measured, else None."""
    out = dict(counts)
    for key in keys:
        if key not in out:
            out[key] = 0 if measured(key) else None
    return {k: out[k] for k in sorted(out, key=str)}


def _dig_dict(obj: Any, *path: str) -> dict[str, Any] | None:
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj if isinstance(obj, dict) else None


# Sparse count maps of a §4.4 measurement (only non-zero keys are stored), by key universe.
_COUNT_MAPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "static_categories": (("static", "findings", "by_category"), ("static", "issues", "by_category")),
    "static_tools": (("static", "findings", "by_tool"),),
    "ai_categories": (("ai", "consensus", "by_category"),),
    "languages": (("sloc", "by_language"),),
    "origins": (("sloc", "by_origin"),),
}


def _count_map_keys(data: report_mod.StudyData) -> dict[str, set[str]]:
    """Key universe of every sparse count map: its vocabulary plus every key seen in the study."""
    keys: dict[str, set[str]] = {
        "static_categories": set(FINDING_CATEGORIES),
        "static_tools": set(data.static_tool_slugs()),
        "ai_categories": set(REVIEW_CATEGORIES),
        "languages": set(),
        "origins": set(),
    }
    for run in data.runs:
        for name, paths in _COUNT_MAPS.items():
            for path in paths:
                keys[name].update(str(k) for k in _dig_dict(run.measurement, *path) or {})
    return keys


def _dense_measurement(measurement: dict[str, Any], keys: dict[str, set[str]]) -> dict[str, Any]:
    """A copy of *measurement* whose sparse count maps hold every key of the study.

    The measurement stores ``by_category`` / ``by_tool`` / ``by_language`` /
    ``by_origin`` sparsely (an absent key means 0), but in a CSV an empty
    cell is NA. A missing key becomes 0 only where the zero was measured
    (the category's tools ran, the tool succeeded or was not applicable,
    the AI review was available, SLOC was measured) and None otherwise, so
    a tool's real 0 stays distinguishable from its failure. Stored values
    are never changed.
    """
    m = copy.deepcopy(measurement)
    static = _dig_dict(m, "static")
    if static is not None:
        availability = _dig_dict(static, "category_available") or {}
        static_ok = static.get("available") is True

        def category_measured(category: str) -> bool:
            return availability.get(category, static_ok) is True

        for part in ("findings", "issues"):
            section = _dig_dict(static, part)
            if section is not None and isinstance(section.get("by_category"), dict):
                section["by_category"] = _filled(section["by_category"], keys["static_categories"], category_measured)
        findings = _dig_dict(static, "findings")
        if findings is not None and isinstance(findings.get("by_tool"), dict):
            status = _dig_dict(m, "tool_status") or {}
            findings["by_tool"] = _filled(
                findings["by_tool"],
                keys["static_tools"],
                lambda slug: status.get(slug) in _OK_TOOL_STATUSES,
            )
    ai = _dig_dict(m, "ai")
    consensus = _dig_dict(ai, "consensus")
    if ai is not None and consensus is not None and isinstance(consensus.get("by_category"), dict):
        ai_ok = ai.get("available") is True
        consensus["by_category"] = _filled(consensus["by_category"], keys["ai_categories"], lambda _: ai_ok)
    sloc = _dig_dict(m, "sloc")
    if sloc is not None:
        for part, total, universe in (("by_language", "measured", "languages"), ("by_origin", "total", "origins")):
            if isinstance(sloc.get(part), dict):
                known = sloc.get(total) is not None
                sloc[part] = _filled(sloc[part], keys[universe], lambda _, known=known: known)
    return m


def _meta_row(meta: dict[str, Any]) -> dict[str, Any]:
    row = {k: meta.get(k) for k in META_COLUMNS}
    validity = meta.get("validity") if isinstance(meta.get("validity"), dict) else {}
    functional = meta.get("functional") if isinstance(meta.get("functional"), dict) else {}
    generation = meta.get("generation") if isinstance(meta.get("generation"), dict) else {}
    # One rule for old and new target_meta rows: older rows listed run-level stops among the
    # truncation reasons (and set ``truncated`` for them); the report applies the same split.
    flags = truncation_flags(meta) if meta else {}
    row.update(
        {
            "validity_valid": validity.get("valid"),
            "validity_exclude": validity.get("exclude"),
            "truncated": flags.get("truncated"),
            "truncation_reasons": ";".join(flags.get("truncation_reasons") or []) or None,
            "run_stopped": flags.get("run_stopped"),
            "run_stop_reasons": ";".join(flags.get("run_stop_reasons") or []) or None,
            "empty_output": meta.get("empty_output"),
            "functional_status": functional.get("status"),
            "functional_pass_rate": functional.get("pass_rate"),
            "generation_cost": generation.get("cost"),
            "generation_tokens": generation.get("total_tokens"),
            "generation_duration_seconds": generation.get("duration_seconds"),
        },
    )
    return row


def _coverage(run, result) -> dict[str, Any]:
    cov = report_mod.tool_coverage(run, result)
    return {"files_in_scope": cov[0], "files_scanned": cov[1], "unparseable": cov[2]}


def _tables_from(data: report_mod.StudyData) -> dict[str, list[dict[str, Any]]]:
    study = data.study
    target_of = {r.id: t for t in data.targets for r in t.runs}
    canonical_ids = {t.canonical.id for t in data.targets if t.canonical is not None}
    analysis_ids = {t.canonical.id for t in data.analysis}
    runs_by_id = {r.id: r for r in data.runs}

    def ident(run_id) -> dict[str, Any]:
        t = target_of.get(run_id)
        run = runs_by_id[run_id]
        # Every table repeats the run identity so it can be filtered without a join.
        return {
            "run_id": run_id,
            "job_id": report_mod.target_job_id(run),
            "group": t.group if t else None,
            "block": t.block if t else None,
            "in_analysis": run_id in analysis_ids,
        }

    runs = []
    for run in data.runs:
        t = target_of.get(run.id)
        measured = report_mod.measured_summary(run)
        runs.append(
            {
                "run_id": run.id,
                "study_id": study.id,
                "target_key": t.key if t else None,
                "job_id": report_mod.target_job_id(run),
                "target_label": t.label if t else None,
                "group": t.group if t else None,
                "block": t.block if t else None,
                "canonical": run.id in canonical_ids,
                "in_analysis": run.id in analysis_ids,
                "exclusion": (t.exclusion if t and run.id in canonical_ids else None) or None,
                "status": run.status,
                "is_complete": run.is_complete,
                "purpose": run.purpose,
                "plan_hash": run.plan_hash,
                "sample_hash": run.sample_hash,
                "attempt": run.attempt,
                "created_at": run.created_at,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "duration_seconds": run.duration_seconds,
                "measured_sloc": measured["sloc"],
                "measured_files": measured["files"],
                "error_message": run.error_message or None,
                **_meta_row(report_mod.as_dict(run.target_meta)),
            },
        )

    run_ids = [r.id for r in data.runs]
    counts = {
        row["result_id"]: row
        for row in Finding.objects.filter(run_id__in=run_ids)
        .values("result_id")
        .annotate(n=Count("id"), counted_n=Count("id", filter=Q(counted=True)))
    }
    tool_rows = []
    result_version: dict[Any, str] = {}
    for run in data.runs:
        for result in data.results.get(run.id, []):
            result_version[result.id] = result.tool_version
            count = counts.get(result.id, {})
            tool_rows.append(
                {
                    **ident(run.id),
                    "result_id": result.id,
                    "tool_slug": result.tool_slug,
                    "tool_kind": result.tool_kind,
                    "category": result.category,
                    "position": result.position,
                    "required": result.required,
                    "status": result.status,
                    "failure_reason": result.failure_reason or None,
                    "attempt": result.attempt,
                    "tool_version": result.tool_version or None,
                    "image_ref": result.image_ref or None,
                    "config_hash": result.config_hash or None,
                    "argv": result.argv or None,
                    "exit_code": result.exit_code,
                    "duration_seconds": result.duration_seconds,
                    "started_at": result.started_at,
                    "finished_at": result.finished_at,
                    **_coverage(run, result),
                    "findings": count.get("n", 0),
                    "counted_findings": count.get("counted_n", 0),
                    "error_message": result.error_message or None,
                },
            )

    sample_index = {s.id: s.index for samples in data.samples.values() for s in samples}
    finding_rows = []
    issues: dict[tuple[Any, bool, str], dict[str, Any]] = {}
    issue_tools: dict[tuple[Any, bool, str], set[str]] = defaultdict(set)
    queryset = (
        Finding.objects.filter(run_id__in=run_ids)
        .order_by("run__created_at", "run_id", "severity_rank", "file_path", "line_number", "id")
        .values()
    )
    for f in queryset:
        cwe = ";".join(report_mod.cwe_list(f["cwe"]))
        finding_rows.append(
            {
                **ident(f["run_id"]),
                "finding_id": f["id"],
                "result_id": f["result_id"],
                "tool_slug": f["tool_slug"],
                "tool_version": result_version.get(f["result_id"]) or None,
                "is_ai": f["is_ai"],
                "review_sample_index": sample_index.get(f["review_sample_id"]),
                "severity": f["severity"],
                "severity_rank": f["severity_rank"],
                "native_severity": f["native_severity"] or None,
                "category": f["category"],
                "cwe": cwe or None,
                "confidence": f["confidence"] or None,
                "rule_id": f["rule_id"] or None,
                "title": f["title"],
                "file_path": f["file_path"] or None,
                "line": f["line_number"],
                "end_line": f["end_line"],
                "column": f["column_number"],
                "file_origin": f["file_origin"],
                "in_measured_scope": f["in_measured_scope"],
                "counted": f["counted"],
                "fingerprint": f["fingerprint"] or None,
                "issue_key": f["issue_key"] or None,
                "grounding": f["grounding"] or None,
                "is_consensus": f["is_consensus"],
                "description": f["description"] or None,
                "suggestion": f["suggestion"] or None,
                "code_snippet": f["code_snippet"] or None,
            },
        )
        if not f["counted"]:
            continue
        key = (f["run_id"], f["is_ai"], f["issue_key"] or f"finding:{f['id']}")
        issue = issues.get(key)
        if issue is None:
            issue = issues[key] = {
                **ident(f["run_id"]),
                "issue_key": key[2],
                "is_ai": f["is_ai"],
                "n_findings": 0,
                "severity": f["severity"],
                "categories": set(),
                "cwes": set(),
                "rule_ids": set(),
                "file_path": f["file_path"] or None,
                "line_min": f["line_number"],
                "line_max": f["line_number"],
            }
        issue["n_findings"] += 1
        if SEVERITY_RANK.get(f["severity"], 4) < SEVERITY_RANK.get(issue["severity"], 4):
            issue["severity"] = f["severity"]
        issue["categories"].add(f["category"])
        issue["cwes"].update(report_mod.cwe_list(f["cwe"]))
        if f["rule_id"]:
            issue["rule_ids"].add(f"{f['tool_slug']}:{f['rule_id']}")
        issue_tools[key].add(f["tool_slug"])
        line = f["line_number"]
        if line is not None:
            issue["line_min"] = line if issue["line_min"] is None else min(issue["line_min"], line)
            issue["line_max"] = line if issue["line_max"] is None else max(issue["line_max"], line)
    issue_rows = []
    for key, issue in issues.items():
        issue_rows.append(
            {
                **{k: v for k, v in issue.items() if k not in ("categories", "cwes", "rule_ids")},
                "tools": ";".join(sorted(issue_tools[key])),
                "categories": ";".join(sorted(issue["categories"])),
                "cwes": ";".join(sorted(issue["cwes"])) or None,
                "rule_ids": ";".join(sorted(issue["rule_ids"])) or None,
            },
        )

    sample_rows = []
    for run in data.runs:
        for s in data.samples.get(run.id, []):
            usage = report_mod.as_dict(s.usage)
            coverage = report_mod.as_dict(s.coverage)
            grounding = report_mod.as_dict(s.grounding)
            not_sent = coverage.get("files_not_sent")
            sample_rows.append(
                {
                    **ident(run.id),
                    "sample_id": s.id,
                    "index": s.index,
                    "engine": s.engine,
                    "harness": s.harness or None,
                    "harness_version": s.harness_version or None,
                    "model_slug": s.model_slug or None,
                    "model_canonical_slug": s.model_canonical_slug or None,
                    "status": s.status,
                    "failure_reason": s.failure_reason or None,
                    "prompt_version": s.prompt_version or None,
                    "prompt_sha256": s.prompt_sha256 or None,
                    "schema_sha256": s.schema_sha256 or None,
                    "file_order_seed": s.file_order_seed,
                    "files_reviewed": len(s.files_reviewed or []),
                    "files_in_scope": coverage.get("files_in_scope"),
                    "files_sent": coverage.get("files_sent"),
                    "files_not_sent": len(not_sent) if isinstance(not_sent, list) else not_sent,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "reasoning_tokens": usage.get("reasoning_tokens"),
                    "cached_tokens": usage.get("cached_tokens"),
                    "cost_usd": usage.get("cost_usd"),
                    "llm_calls": usage.get("llm_calls"),
                    "llm_calls_failed": usage.get("llm_calls_failed"),
                    "providers": s.providers or None,
                    "finish_reasons": s.finish_reasons or None,
                    **{f"grounding_{k}": grounding.get(k) for k in report_mod.GROUNDING_KINDS},
                    "finding_count": s.finding_count,
                    "repaired": s.repaired,
                    "latency_seconds": s.latency_seconds,
                    "error_message": s.error_message or None,
                },
            )

    count_keys = _count_map_keys(data)
    flat = [
        (run, _flatten(_dense_measurement(run.measurement, count_keys)))
        for run in data.runs
        if report_mod.as_dict(run.measurement)
    ]
    measure_columns = sorted({k for _, f in flat for k in f})
    measurement_rows = [{**ident(run.id), **{c: f.get(c) for c in measure_columns}} for run, f in flat]

    keys = [d["key"] for d in data.outcome_defs]
    outcome_rows = [
        {
            **ident(t.canonical.id),
            "target_label": t.label,
            "is_complete": t.canonical.is_complete,
            **{k: data.values[t.canonical.id].get(k) for k in keys},
        }
        for t in data.analysis
    ]
    return {
        "runs": runs,
        "tool_executions": tool_rows,
        "findings": finding_rows,
        "issues": issue_rows,
        "review_samples": sample_rows,
        "measurements": measurement_rows,
        "outcomes": outcome_rows,
    }


def tables(study) -> dict[str, list[dict[str, Any]]]:
    """{runs, tool_executions, findings, issues, review_samples, measurements, outcomes}."""
    return _tables_from(report_mod.collect(study))


# --------------------------------------------------------------------------
# Replication package
# --------------------------------------------------------------------------


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(report_mod.jsonable(value), sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _codebook_rows() -> list[dict[str, str]]:
    return [
        {"table": table, "column": column, "description": text}
        for table, columns in CODEBOOK.items()
        for column, text in columns.items()
    ]


def _readme(manifest: dict[str, Any]) -> str:
    lines = [
        f"# Replication package: {manifest['study']['name']}",
        "",
        f"Study `{manifest['study']['id']}`, exported {manifest['generated_at']} "
        f"(export schema {EXPORT_SCHEMA_VERSION}, report schema {report_mod.REPORT_SCHEMA_VERSION}).",
        "",
        "## Contents",
        "",
        "| File | What |",
        "| --- | --- |",
        "| `protocol.json`, `protocol.md` | The locked pre-run protocol (absent when the study was never locked). |",
        "| `report.json`, `report.md` | The post-run report as generated and stored on the study (not rebuilt). |",
        "| `plan.json` | The frozen analysis plan (tools, versions, configs, reviewer, hashes). |",
        "| `tables/*.csv` | Tidy tables (one row per run / tool execution / finding / issue / review sample). |",
        "| `tables/codebook.csv` | Column descriptions. |",
        "| `manifest.json` | Hashes, row counts and provenance of this export. |",
        "| `SHA256SUMS` | sha256 of every other file (`sha256sum -c SHA256SUMS`). |",
        "",
        "## Hashes",
        "",
        f"- Protocol hash: `{manifest['protocol_hash'] or 'none (not locked)'}`",
        f"- Plan hash: `{manifest['plan_hash'] or 'none'}`",
        f"- Report hash: `{manifest['report_hash']}` (generated {manifest['report_generated_at'] or 'unknown'})",
        f"- Report hash (stored on the study): `{manifest['stored_report_hash'] or 'none'}`",
        f"- Report verified against the stored hash: {manifest['report_verified']}",
        f"- Study data changed since the report was generated: {manifest['data_changed_since_report']}",
        "",
        "## Tables",
        "",
        "| Table | Rows |",
        "| --- | ---: |",
        *[f"| `tables/{name}.csv` | {info['rows']} |" for name, info in manifest["tables"].items()],
        "",
        "## Conventions",
        "",
        "- An empty CSV cell is missing data (NA). Missing is never written as 0: a failed or timed-out tool makes",
        "  the outcomes that depend on it missing.",
        "- Booleans are `true`/`false`; lists and objects are JSON; CWE lists are `;`-separated.",
        "- Only runs with `in_analysis = true` (`tables/runs.csv`) enter the report's statistics; the other runs are",
        "  attrition (failed, cancelled, superseded, ineligible or of another plan) and are listed for transparency.",
        "- AI findings (`is_ai = true`) are opinions of the reviewer; only grounded consensus findings are `counted`.",
        "- Cells are written verbatim (no spreadsheet formula escaping). Free-text columns (titles, descriptions,",
        "  code snippets, file paths, error messages) hold text from analysed apps and LLM output: import cells as",
        "  text and never let a spreadsheet evaluate them as formulas.",
    ]
    return "\n".join(lines) + "\n"


def _data_changed_since(study, report: dict[str, Any], data: report_mod.StudyData, generated_at) -> bool | None:
    """Whether the current data may differ from what *report* was built from (None: unknown).

    A run updated after the report was generated is a change. So is a
    different run set or analysis set than the report's recorded ``inputs``:
    deleting a run (or anything else that bypasses ``updated_at``) changes
    the tables without touching any remaining run.
    """
    changed: list[bool] = []
    if generated_at is not None:
        changed.append(study.runs.filter(updated_at__gt=generated_at).exists())
    inputs = report_mod.as_dict(report.get("inputs"))
    if isinstance(inputs.get("runs"), int):
        changed.append(inputs["runs"] != len(data.runs))
    if isinstance(inputs.get("analysis_run_ids"), list):
        current = sorted(str(t.canonical.id) for t in data.analysis)
        changed.append(sorted(str(i) for i in inputs["analysis_run_ids"]) != current)
    if any(changed):
        return True
    return False if generated_at is not None else None


class ReportUnavailable(ValueError):  # noqa: N818 - domain wording
    """The study has no finished stored report to package."""


def replication_zip(study, *, report: dict[str, Any] | None = None) -> bytes:
    """A deterministic zip: tables, protocol, report (+ Markdown), plan, codebook, README, SHA256SUMS.

    *report* defaults to the study's stored report (``study.report``): the
    package carries the report the researcher read, it never rebuilds one
    (that is seconds of CPU per hundred runs). Raises
    :class:`ReportUnavailable` when no finished report is stored.
    """
    if report is None:
        if not report_mod.is_report(study.report):
            msg = "The study has no finished report; generate one before exporting the replication package."
            raise ReportUnavailable(msg)
        report = study.report
    data = report_mod.collect(study)
    table_rows = _tables_from(data)
    protocol = study.protocol or {}
    files: dict[str, bytes] = {}
    table_info: dict[str, dict[str, Any]] = {}
    for name in TABLE_NAMES:
        rows = table_rows[name]
        payload = to_csv(rows).encode("utf-8")
        files[f"tables/{name}.csv"] = payload
        table_info[name] = {
            "rows": len(rows),
            "columns": len(rows[0]) if rows else 0,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    files["tables/codebook.csv"] = to_csv(_codebook_rows()).encode("utf-8")
    if protocol:
        files["protocol.json"] = _json_bytes(protocol)
        files["protocol.md"] = report_mod.protocol_to_markdown(protocol).encode("utf-8")
    files["report.json"] = _json_bytes(report)
    files["report.md"] = report_mod.to_markdown(report, protocol or None).encode("utf-8")
    files["plan.json"] = _json_bytes(study.plan or {})
    packaged_hash = report_mod.report_hash(report)
    generated_at = study.report_generated_at
    manifest = {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "generated_at": timezone.now().isoformat(),
        "study": {"id": str(study.id), "name": study.name, "status": study.status},
        "protocol_hash": study.protocol_hash or None,
        "plan_hash": data.plan_hash or None,
        "report_hash": packaged_hash,
        "report_generated_at": report.get("generated_at"),
        "stored_report_hash": study.report_hash or None,
        # The packaged report still matches the hash recorded when it was stored.
        "report_verified": (study.report_hash == packaged_hash) if study.report_hash else None,
        # Tables are exported from the current data; a run changed after the
        # report was generated means the two may disagree.
        "data_changed_since_report": _data_changed_since(study, report, data, generated_at),
        "tables": table_info,
        "engine": report_mod.engine_provenance(),
    }
    files["manifest.json"] = _json_bytes(manifest)
    files["README.md"] = _readme(manifest).encode("utf-8")
    sums = "".join(f"{hashlib.sha256(files[name]).hexdigest()}  {name}\n" for name in sorted(files))
    files["SHA256SUMS"] = sums.encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name])
    return buffer.getvalue()
