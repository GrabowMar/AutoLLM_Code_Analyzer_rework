"""Pattern export zip (spec section 7.9 step 8): ``variants.csv``, ``features.csv`` and the offline-model README.

``build_export(pattern_run) -> bytes`` (``GET /patterns/runs/{id}/export.zip``; WP-02b's study export
includes it). Contents:

- ``variants.csv``: one row per planned variant (``VARIANT_COLUMNS`` then one ``f:<column>`` per feature);
- ``features.csv``: long format for offline mixed models (``FEATURE_COLUMNS``);
- ``clusters.json``, ``prevalence.csv`` (``PREVALENCE_COLUMNS``), ``comparisons.csv``
  (``COMPARISON_COLUMNS``), ``pooled.json``, ``audit.csv`` (``AUDIT_COLUMNS``);
- ``variants/<target>/<run_id>.<ext>``: each variant's regenerated part (the raw replacement when stored);
- ``original/<target>.<ext>``: each target's original part;
- ``manifest.json``: study plan, detector set, normalizer version, tool versions, PatternRun label;
- ``README.md``: the files, every column of every CSV and the offline model formula.

The column lists are a published contract (header snapshot test in ``tests/wp13_patterns``). The README's
column section is generated from the headers the writers actually emit (:func:`columns_markdown` over
``CSV_COLUMNS`` plus this export's feature columns) and :data:`COLUMN_DOCS`, which must document every
fixed column (a missing entry fails the export; ``test_export_readme`` checks every written header).
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import TYPE_CHECKING
from typing import Any

from backend.generation import blobs
from backend.generation.patterns import detectors as det
from backend.generation.patterns import features as feat
from backend.generation.patterns import normalize as norm
from backend.generation.patterns import variants as var

if TYPE_CHECKING:
    from backend.generation.models import PatternRun

EXPORT_VERSION = 1
VARIANT_COLUMNS = (
    "run_id",
    "study_id",
    "cell_id",
    "condition_id",
    "condition_index",
    "condition_label",
    "condition_levels",
    "target_id",
    "target_label",
    "match_key",
    "slot_set_id",
    "parent_run",
    "parent_model",
    "parent_harness",
    "model",
    "same_author",
    "repeat_index",
    "outcome",
    "usable",
    "has_output",
    "parse_status",
    "eval_status",
    "normalized_sha",
    "normalize_reason",
    "exact_cluster",
    "cluster_id",
    "cluster_size",
    "leak_score",
    "equal_to_original",
    "slot_tests_all_pass",
    "exploited_any",
    "uses_new_dependency",
    "out_of_slot_edit",
    "adaptive_n",
    "started_at",
)
FEATURE_COLUMNS = (
    "run_id",
    "condition_id",
    "condition_label",
    "target_id",
    "match_key",
    "parent_run",
    "parent_model",
    "same_author",
    "repeat_index",
    "feature",
    "scope",
    "column",
    "kind",
    "detector",
    "value",
    "y",
    "tool_severity",
    "adaptive_n",
)
PREVALENCE_COLUMNS = (
    "condition",
    "condition_label",
    "target",
    "pooled",
    "feature",
    "scope",
    "column",
    "conditional_on",
    "k",
    "n_measured",
    "n_itt",
    "errors",
    "na",
    "missing",
    "p",
    "ci_low",
    "ci_high",
    "method",
    "cp_low",
    "cp_high",
    "bounds_low",
    "bounds_high",
    "attrition",
    "adaptive_n",
    "looks",
    "low_effective_n",
    "correctness_p",
    "correctness_n",
    "audit_precision",
    "audit_npv",
)
COMPARISON_COLUMNS = (
    "feature",
    "scope",
    "column",
    "a",
    "a_label",
    "b",
    "b_label",
    "method",
    "test",
    "diff",
    "ci_low",
    "ci_high",
    "p",
    "q",
    "family_size",
    "planned",
    "interim",
    "n_pairs",
    "k_a",
    "n_a",
    "k_b",
    "n_b",
    "p_adjusted",
    "correction",
    "alpha",
    "significant",
)
AUDIT_COLUMNS = (
    "audit_id",
    "feature_key",
    "status",
    "scope",
    "n_hits",
    "n_non_hits",
    "precision",
    "precision_ci_low",
    "precision_ci_high",
    "npv",
    "npv_ci_low",
    "npv_ci_high",
    "unsure",
    "uncoded",
    "seed",
    "eval_task",
)

# What each fixed column of each CSV holds (E2E-16). Keys must equal the column tuples above; the README's
# column section is rendered from the written headers with these texts, so a new column needs a text here.
_VALUES = "`present` / `absent` / `error` (unknown, never clean) / `n/a`"
COLUMN_DOCS: dict[str, dict[str, str]] = {
    "variants.csv": {
        "run_id": "The variant's run (GenerationRun id); the file `variants/<target_id>/<run_id>.<ext>`.",
        "study_id": "The study.",
        "cell_id": "The study cell (StudyCell id) the variant ran in.",
        "condition_id": "The condition (StudyCondition id).",
        "condition_index": "The condition's position in the plan (0-based).",
        "condition_label": "The condition's label (its factor levels in words).",
        "condition_levels": "The condition's factor levels as JSON (`{factor: level}`).",
        "target_id": "The slot target (the regenerated part; SlotTarget id).",
        "target_label": "The target's label (e.g. the endpoint or function it holds).",
        "match_key": "The part's key shared across parents (same part in different parent apps).",
        "slot_set_id": "The slot set (frozen parent tree) the target belongs to.",
        "parent_run": "The parent app run the part was cut from.",
        "parent_model": "The model that wrote the parent app.",
        "parent_harness": "The harness that wrote the parent app.",
        "model": "The model that regenerated the part (the condition's model level).",
        "same_author": "`true` when `model` equals `parent_model`; empty when the model is unknown.",
        "repeat_index": "The repeat of the cell (0-based); with `target_id` the pairing key of paired designs.",
        "outcome": "The run's outcome (e.g. `tested`, `output_invalid`, `build_fail`, `infra_error`).",
        "usable": "`true` when the run counts in results (infrastructure failures do not).",
        "has_output": "`true` when the variant produced a part; `false` rows are attrition (ITT, never absent).",
        "parse_status": "The single-shot / agent output parse status (`ok`, `truncated`, ...).",
        "eval_status": "The variant evaluation's status.",
        "normalized_sha": "sha256 of the normalized part; empty when it could not be normalized.",
        "normalize_reason": "Why the part could not be normalized (`parse_error`, `empty`, "
        "`unsupported_language`); empty when it was.",
        "exact_cluster": "The exact-duplicate cluster key (equals `normalized_sha`).",
        "cluster_id": "The cluster in `clusters.json` holding the variant (exact or MinHash).",
        "cluster_size": "The number of variants in that cluster.",
        "leak_score": "The share of the original part's 8-token shingles found in what the model was shown "
        "(the target's leak report); numeric, not a prevalence feature.",
        "equal_to_original": f"Derived: the normalized part equals the normalized original ({_VALUES}).",
        "slot_tests_all_pass": "Derived: every discriminating, non-flaky check passes; `absent` for a "
        f"build_fail / start_fail / output_invalid variant ({_VALUES}).",
        "exploited_any": f"Derived: any probe exploited the variant ({_VALUES}).",
        "uses_new_dependency": f"Derived: the variant added a dependency the parent did not have ({_VALUES}).",
        "out_of_slot_edit": f"Derived: the variant edited outside its slot ({_VALUES}).",
        "adaptive_n": "`true` when the cell used adaptive N (repeats added by looks; see `prevalence.csv`).",
        "started_at": "When the run started (ISO 8601).",
    },
    "features.csv": {
        "run_id": "The variant's run (as in `variants.csv`).",
        "condition_id": "The condition (StudyCondition id).",
        "condition_label": "The condition's label.",
        "target_id": "The slot target.",
        "match_key": "The part's key shared across parents.",
        "parent_run": "The parent app run the part was cut from.",
        "parent_model": "The model that wrote the parent app.",
        "same_author": "`true` when the variant's model wrote the parent.",
        "repeat_index": "The repeat of the cell (0-based).",
        "feature": "The feature key (detector feature or derived feature).",
        "scope": "`span` (the regenerated part), `file` (the assembled file) or `derived`.",
        "column": "`<feature>@<scope>` for detector features, the feature key for derived ones; the "
        "`variants.csv` column is `f:<column>`.",
        "kind": "`detector` or `derived`.",
        "detector": "The pinned detector ref (`slug@version#hash12`); empty for derived features.",
        "value": f"The variant's value: {_VALUES}.",
        "y": "`1` (present), `0` (absent) or empty (error, n/a): the binary outcome of the offline model.",
        "tool_severity": "The detector tool's own severity label, verbatim (never a normalized severity).",
        "adaptive_n": "`true` when the cell used adaptive N.",
    },
    "prevalence.csv": {
        "condition": "The condition (StudyCondition id).",
        "condition_label": "The condition's label.",
        "target": "The slot target, or `all` for the pooled row over every target.",
        "pooled": "`true` for the row over all targets.",
        "feature": "The feature key.",
        "scope": "`span`, `file` or `derived`.",
        "column": "The feature column (`<feature>@<scope>` or the derived feature key).",
        "conditional_on": "Set (`slot_tests_all_pass`) on the rows restricted to variants that pass the "
        "slot tests; empty on unconditional rows.",
        "k": "Variants with the feature present.",
        "n_measured": "Variants measured (present + absent).",
        "n_itt": "Planned variants in the group (intention to treat; attrition included).",
        "errors": "Variants whose value is `error` (unknown, never counted clean).",
        "na": "Variants where the feature does not apply (`n/a`).",
        "missing": "Variants without output (attrition).",
        "p": "`k / n_measured`.",
        "ci_low": "Lower bound of the Wilson 95% CI (alpha/K for adaptive N).",
        "ci_high": "Upper bound of the Wilson 95% CI (alpha/K for adaptive N).",
        "method": "`wilson`, or `wilson_repeated_looks` for adaptive-N groups.",
        "cp_low": "Clopper-Pearson lower bound, when `n_measured` < 30.",
        "cp_high": "Clopper-Pearson upper bound, when `n_measured` < 30.",
        "bounds_low": "Best-case bound `k / (n_itt - na)` when attrition + errors exceed 5%.",
        "bounds_high": "Worst-case bound `(k + missing + errors) / (n_itt - na)` when attrition + errors exceed 5%.",
        "attrition": "`missing / n_itt`.",
        "adaptive_n": "`true` when any cell of the group used adaptive N.",
        "looks": "The number of interim looks K of an adaptive-N group (empty otherwise).",
        "low_effective_n": "`true` when more than half of the group's variants are exact duplicates.",
        "correctness_p": "For security features: the share of the group's measured variants passing the slot tests.",
        "correctness_n": "For security features: the variants with a measured `slot_tests_all_pass`.",
        "audit_precision": "The detector's audited precision (complete human audits only; `audit.csv`).",
        "audit_npv": "The detector's audited negative predictive value (complete audits only).",
    },
    "comparisons.csv": {
        "feature": "The feature key.",
        "scope": "`span`, `file` or `derived`.",
        "column": "The feature column.",
        "a": "The first condition (StudyCondition id).",
        "a_label": "The first condition's label.",
        "b": "The second condition.",
        "b_label": "The second condition's label.",
        "method": "`paired` (both conditions forward the seed; pairs by target and repeat) or `independent`.",
        "test": "`mcnemar_exact` (paired) or `fisher_exact` (independent).",
        "diff": "Prevalence difference a - b (Newcombe paired or hybrid score estimate).",
        "ci_low": "Lower bound of the 95% CI of `diff`.",
        "ci_high": "Upper bound of the 95% CI of `diff`.",
        "p": "Raw p of planned comparisons (confirmatory primary features); empty for exploratory rows and "
        "at interim.",
        "q": "Benjamini-Hochberg q of exploratory rows over their family; empty at interim.",
        "family_size": "The number of comparisons in the row's correction family.",
        "planned": "`true` for comparisons of the study plan's locked primary features.",
        "interim": "`true` while the study is not finished: every p, q and decision is withheld.",
        "n_pairs": "Paired: the measured pairs.",
        "k_a": "Independent: variants of `a` with the feature present.",
        "n_a": "Independent: measured variants of `a`.",
        "k_b": "Independent: variants of `b` with the feature present.",
        "n_b": "Independent: measured variants of `b`.",
        "p_adjusted": "Planned rows: p adjusted with the plan's `correction` over the planned family.",
        "correction": "`bh` for exploratory rows, the plan's correction (`holm`, `bh`, `none`) for planned ones.",
        "alpha": "The plan's alpha (planned rows only).",
        "significant": "Planned rows: `p_adjusted < alpha`; empty for exploratory rows and at interim.",
    },
    "audit.csv": {
        "audit_id": "The audit.",
        "feature_key": "The audited feature.",
        "status": "The audit's status (`complete` audits feed `prevalence.csv`).",
        "scope": "The audited scope.",
        "n_hits": "Sampled variants the detector flagged.",
        "n_non_hits": "Sampled variants the detector did not flag.",
        "precision": "Share of coded hits confirmed by the human coders.",
        "precision_ci_low": "Lower Wilson 95% bound of `precision`.",
        "precision_ci_high": "Upper Wilson 95% bound of `precision`.",
        "npv": "Share of coded non-hits confirmed clean (negative predictive value).",
        "npv_ci_low": "Lower Wilson 95% bound of `npv`.",
        "npv_ci_high": "Upper Wilson 95% bound of `npv`.",
        "unsure": "Sampled items coded unsure.",
        "uncoded": "Sampled items not coded yet.",
        "seed": "The sampling seed.",
        "eval_task": "The human-evaluation task that codes the sample.",
    },
}
CSV_COLUMNS: dict[str, tuple[str, ...]] = {
    "variants.csv": VARIANT_COLUMNS,
    "features.csv": FEATURE_COLUMNS,
    "prevalence.csv": PREVALENCE_COLUMNS,
    "comparisons.csv": COMPARISON_COLUMNS,
    "audit.csv": AUDIT_COLUMNS,
}


def _feature_column_doc(column: feat.Column) -> str:
    detector = f", detector `{column.detector_ref}`" if column.detector_ref else ""
    return (
        f"{column.kind.capitalize()} feature `{column.feature}`, scope `{column.scope}`{detector}, polarity "
        f"`{column.polarity}`: {_VALUES}."
    )


def columns_markdown(headers: dict[str, list[str]], feature_columns: list[feat.Column]) -> str:
    """The README's column section for the headers as written (every column of every CSV).

    ``headers`` maps a CSV name to its written header; fixed columns take their text from
    :data:`COLUMN_DOCS` (``KeyError`` when one is missing, so an undocumented column fails the export) and
    ``variants.csv``'s ``f:<column>`` columns are described from this export's feature columns.
    """
    by_header = {f"f:{c.key}": c for c in feature_columns}
    lines = ["## Columns", ""]
    for name, header in headers.items():
        docs = COLUMN_DOCS[name]
        lines += [f"### `{name}`", "", "| column | meaning |", "| --- | --- |"]
        for column in header:
            text = _feature_column_doc(by_header[column]) if column in by_header else docs[column]
            lines.append(f"| `{column}` | {text.replace('|', '/')} |")
        lines.append("")
    return "\n".join(lines)


README = """# Pattern analysis export

PatternRun `{pattern_run}` of study `{study}` (label **{label}**, normalizer v{normalizer}, detector set
`{detector_set_hash}`). Generated by `backend/generation/patterns/export.py` (export v{version}).

## Files

- `variants.csv` -- one row per planned variant (main stage), exactly the records this PatternRun analysed
  (later retests, retries or top-ups do not change it). `has_output=false` rows are attrition (counted in
  ITT denominators, never as absent), except that a usable `build_fail` / `start_fail` / `output_invalid`
  variant has `slot_tests_all_pass=absent` (0 pass in ITT). Feature columns are named `f:<feature>@<scope>`
  (`span`: the regenerated part; `file`: the assembled file) or `f:<derived feature>`; values are
  `present`, `absent`, `error` (a detector or measurement failed: unknown, never clean) or `n/a`.
- `features.csv` -- the same values in long format, one row per (variant, feature column), with
  `y = 1` (present), `0` (absent) or empty (error, n/a). `tool_severity` is the tool's own label, verbatim.
- `prevalence.csv` -- per (condition, target, feature, scope): `k / n_measured` with a Wilson 95% CI
  (`method=wilson_repeated_looks` for adaptive_n rows: Wilson at alpha/K), Clopper-Pearson when n < 30,
  ITT n, errors, n/a, missing, worst/best-case bounds when attrition + errors exceed 5%.
- `comparisons.csv` -- condition differences; `method=paired` (Newcombe paired, exact McNemar) when both
  conditions forward the seed, else `independent` (Newcombe hybrid score, Fisher exact). `p` (raw) and
  `p_adjusted` (the study plan's `correction`, Holm by default, over the planned family) with the plan's
  `alpha` and `significant` only for planned comparisons of a confirmatory study; exploratory rows carry
  BH `q` and `family_size`.
- `pooled.json` -- per-target estimates, the Mantel-Haenszel risk difference stratified by target, and an
  exact target-level permutation p (`few_clusters`: no cluster-robust SEs with fewer than 20 targets).
- `clusters.json` -- exact (normalized sha) or MinHash clusters per target with members and statistics.
- `audit.csv` -- human audits of detector precision and NPV (Wilson CIs).
- `variants/<target>/<run_id>.<ext>`, `original/<target>.<ext>` -- the regenerated parts and originals.
- `manifest.json` -- study plan hash, detector set, normalizer version, tool versions, label.

Every column of every CSV is described under **Columns** below.

## Offline mixed model

Fit per binary feature on `features.csv` (rows with empty `y` excluded; ITT sensitivity: impute the
worst/best case for missing variants from `variants.csv`):

    y ~ condition + same_author + (1 | target_id) + (1 | parent_run)

e.g. in R: `lme4::glmer(y ~ condition_label + same_author + (1|target_id) + (1|parent_run),
family = binomial, data = subset(features, column == "<feature>@span"))`. Targets that match across parents
share `match_key`; add `(1 | match_key)` when several parents contribute the same part. With fewer than 20
targets prefer the in-app stratified estimates (`pooled.json`).
"""


def _csv(columns: tuple[str, ...], rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _cell(row.get(k)) for k in columns})
    return buffer.getvalue()


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return value


def _cluster_index(pattern_run: PatternRun) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cluster in pattern_run.clusters.prefetch_related("members"):
        for member in cluster.members.all():
            out[str(member.run_id)] = {"cluster_id": cluster.pk, "size": cluster.size, "key": cluster.key}
    return out


def variant_rows(pattern_run: PatternRun, dataset: var.Dataset, matrix: feat.Matrix) -> list[dict[str, Any]]:
    clusters = _cluster_index(pattern_run)
    rows = []
    for record in dataset.records:
        info = dataset.targets[record.target_id]
        target = info.target
        run = record.run
        values = matrix.values.get(record.run_id, {})
        cluster = clusters.get(record.run_id, {})
        row: dict[str, Any] = {
            "run_id": record.run_id,
            "study_id": str(dataset.study.pk),
            "cell_id": record.cell_id,
            "condition_id": record.condition_id,
            "condition_index": record.condition_index,
            "condition_label": record.condition_label,
            "condition_levels": record.condition_levels,
            "target_id": record.target_id,
            "target_label": target.label,
            "match_key": target.match_key,
            "slot_set_id": str(target.slot_set_id),
            "parent_run": str(target.slot_set.parent_run_id),
            "parent_model": target.slot_set.parent_model,
            "parent_harness": target.slot_set.parent_harness,
            "model": record.model_slug,
            "same_author": (record.model_slug == target.slot_set.parent_model) if record.model_slug else None,
            "repeat_index": record.repeat_index,
            "outcome": record.outcome,
            "usable": record.usable,
            "has_output": record.has_output,
            "parse_status": record.parse_status,
            "eval_status": record.eval_status,
            "normalized_sha": record.normalized_sha,
            "normalize_reason": record.norm.reason if record.norm else "",
            "exact_cluster": record.normalized_sha,
            "cluster_id": cluster.get("cluster_id"),
            "cluster_size": cluster.get("size"),
            "leak_score": matrix.leak_score.get(record.run_id) if record.has_output else feat.leak_score(info),
            "adaptive_n": record.adaptive_n,
            "started_at": run.started_at.isoformat() if run and run.started_at else "",
        }
        for name in feat.DERIVED:
            row[name] = values.get(name, "")
        for column in matrix.columns:
            row[f"f:{column.key}"] = values.get(column.key, "")
        rows.append(row)
    return rows


def feature_rows(dataset: var.Dataset, matrix: feat.Matrix) -> list[dict[str, Any]]:
    rows = []
    for record in dataset.in_matrix():
        target = dataset.targets[record.target_id].target
        same_author = (record.model_slug == target.slot_set.parent_model) if record.model_slug else None
        for column in matrix.columns:
            value = matrix.value(record.run_id, column.key)
            if value is None:
                if not record.has_output:
                    continue  # a failure without output: only its correctness is data (ITT), the rest is missing
                value = feat.ERROR  # no verdict for a variant with output: unknown, never clean
            rows.append(
                {
                    "run_id": record.run_id,
                    "condition_id": record.condition_id,
                    "condition_label": record.condition_label,
                    "target_id": record.target_id,
                    "match_key": target.match_key,
                    "parent_run": str(target.slot_set.parent_run_id),
                    "parent_model": target.slot_set.parent_model,
                    "same_author": same_author,
                    "repeat_index": record.repeat_index,
                    "feature": column.feature,
                    "scope": column.scope,
                    "column": column.key,
                    "kind": column.kind,
                    "detector": column.detector_ref,
                    "value": value,
                    "y": 1 if value == feat.PRESENT else 0 if value == feat.ABSENT else None,
                    "tool_severity": matrix.severities.get(record.run_id, {}).get(column.key, ""),
                    "adaptive_n": record.adaptive_n,
                },
            )
    return rows


def prevalence_rows(summary: dict[str, Any], audits: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in summary.get("prevalence") or []:
        audit = audits.get(row["feature"]) or {}
        flat = dict(row)
        bounds = row.get("bounds") or {}
        flat["bounds_low"], flat["bounds_high"] = bounds.get("low"), bounds.get("high")
        correctness = row.get("correctness") or {}
        flat["correctness_p"], flat["correctness_n"] = correctness.get("p"), correctness.get("n_measured")
        flat["audit_precision"] = (
            (audit.get("precision") or {}).get("estimate") if audit.get("status") == "complete" else None
        )
        flat["audit_npv"] = (audit.get("npv") or {}).get("estimate") if audit.get("status") == "complete" else None
        rows.append(flat)
    return rows


def audit_rows(pattern_run: PatternRun) -> list[dict[str, Any]]:
    rows = []
    for audit in pattern_run.audits.order_by("created_at"):
        summary = audit.summary or {}
        precision, npv = summary.get("precision") or {}, summary.get("npv") or {}
        rows.append(
            {
                "audit_id": audit.pk,
                "feature_key": audit.feature_key,
                "status": audit.status,
                "scope": summary.get("scope", ""),
                "n_hits": len((audit.sample or {}).get("hits") or []),
                "n_non_hits": len((audit.sample or {}).get("non_hits") or []),
                "precision": precision.get("estimate"),
                "precision_ci_low": precision.get("ci_low"),
                "precision_ci_high": precision.get("ci_high"),
                "npv": npv.get("estimate"),
                "npv_ci_low": npv.get("ci_low"),
                "npv_ci_high": npv.get("ci_high"),
                "unsure": summary.get("unsure"),
                "uncoded": summary.get("uncoded"),
                "seed": (audit.sample or {}).get("seed"),
                "eval_task": str(audit.eval_task_id) if audit.eval_task_id else "",
            },
        )
    return rows


def clusters_doc(pattern_run: PatternRun, summary: dict[str, Any]) -> dict[str, Any]:
    clusters = []
    for cluster in pattern_run.clusters.prefetch_related("members").order_by("slot_target_id", "-size", "pk"):
        clusters.append(
            {
                "cluster_id": cluster.pk,
                "target": str(cluster.slot_target_id),
                "method": cluster.method,
                "key": cluster.key,
                "size": cluster.size,
                "representative": str(cluster.representative_id),
                "includes_original": cluster.includes_original,
                "members": [{"run_id": str(m.run_id), "distance": m.distance} for m in cluster.members.all()],
            },
        )
    return {"clusters": clusters, "stats": summary.get("cluster_stats") or []}


def load_matrix(pattern_run: PatternRun) -> tuple[var.Dataset, feat.Matrix]:
    """The dataset and matrix exactly as the run analysed them (no detector re-runs, no current data).

    A completed run stores its analysed records in ``summary["records"]`` (cell, run, outcome, output,
    evaluation status and the derived values): the dataset is rebuilt from those run ids
    (``variants.load_frozen``), the detector values from the run's own hits and the derived values from
    the stored ones. A run without stored records (summary version 1) falls back to the study's current
    data; the manifest says so (``records_frozen: false``).
    """
    summary = pattern_run.summary or {}
    scope = summary.get("scope") or pattern_run.scope or {}
    specs = det.resolve_refs(pattern_run.detectors)
    frozen = summary.get("records")
    if frozen is not None:
        dataset = var.load_frozen(pattern_run.study, frozen)
    else:
        dataset = var.load(pattern_run.study, scope.get("targets", "all"))
    matrix = feat.from_hits(dataset, specs, list(pattern_run.hits.all()), scope.get("features") or list(det.SCOPES))
    if frozen is not None:
        feat.apply_frozen(matrix, frozen)
    return dataset, matrix


def build_export(pattern_run: PatternRun) -> bytes:
    from backend.generation.patterns.audit import audits_by_feature

    summary = pattern_run.summary or {}
    dataset, matrix = load_matrix(pattern_run)
    feature_cols = tuple(f"f:{c.key}" for c in matrix.columns)
    study = pattern_run.study
    manifest = {
        "export_version": EXPORT_VERSION,
        "pattern_run": str(pattern_run.pk),
        "label": pattern_run.label,
        "is_live": pattern_run.is_live,
        "status": pattern_run.status,
        "completed_at": pattern_run.completed_at.isoformat() if pattern_run.completed_at else None,
        "study": {
            "id": str(study.pk),
            "name": study.name,
            "kind": study.kind,
            "status": study.status,
            "design_hash": study.design_hash,
            "prereg_hash": study.prereg_hash,
            "plan_hash": study.active_plan.plan_hash if study.active_plan_id else None,
        },
        "detectors": pattern_run.detectors,
        "detector_set_hash": pattern_run.detector_set_hash,
        "normalizer_version": pattern_run.normalizer_version,
        "cluster_params": pattern_run.cluster_params,
        "scope": pattern_run.scope,
        "tool_versions": summary.get("tool_versions") or {},
        "interim": summary.get("interim"),
        "issues": summary.get("issues") or [],
        "records_frozen": summary.get("records") is not None,
    }
    headers = {name: list(columns) for name, columns in CSV_COLUMNS.items()}
    headers["variants.csv"] += feature_cols
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("variants.csv", _csv(tuple(headers["variants.csv"]), variant_rows(pattern_run, dataset, matrix)))
        zf.writestr("features.csv", _csv(tuple(headers["features.csv"]), feature_rows(dataset, matrix)))
        zf.writestr(
            "prevalence.csv",
            _csv(tuple(headers["prevalence.csv"]), prevalence_rows(summary, audits_by_feature(pattern_run))),
        )
        zf.writestr("comparisons.csv", _csv(tuple(headers["comparisons.csv"]), list(summary.get("comparisons") or [])))
        zf.writestr("pooled.json", json.dumps(summary.get("pooled") or [], indent=2, sort_keys=True))
        zf.writestr("audit.csv", _csv(tuple(headers["audit.csv"]), audit_rows(pattern_run)))
        zf.writestr("clusters.json", json.dumps(clusters_doc(pattern_run, summary), indent=2, sort_keys=True))
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        for record in dataset.with_output():
            info = dataset.targets[record.target_id]
            ext = norm.extension_for(info.target.language)
            text = None
            if record.replacement_blob:
                try:
                    text = blobs.get_text(record.replacement_blob)
                except Exception:  # noqa: BLE001 - fall back to the part cut from the assembled file
                    text = None
            if text is None:
                text = record.part_text
            if text is not None:
                zf.writestr(f"variants/{record.target_id}/{record.run_id}.{ext}", text)
        for target_id, info in dataset.targets.items():
            if info.original_text is not None:
                zf.writestr(f"original/{target_id}.{norm.extension_for(info.target.language)}", info.original_text)
        zf.writestr(
            "README.md",
            README.format(
                pattern_run=pattern_run.pk,
                study=study.pk,
                label=pattern_run.label,
                normalizer=pattern_run.normalizer_version,
                detector_set_hash=pattern_run.detector_set_hash,
                version=EXPORT_VERSION,
            )
            + "\n"
            + columns_markdown(headers, list(matrix.columns)),
        )
    return buffer.getvalue()
