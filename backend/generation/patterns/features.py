"""Feature matrix of variants x features (spec section 7.9 step 5).

Values are ``present``, ``absent``, ``error`` and ``n/a`` (plus the numeric ``leak_score``). A planned cell
without usable output has **no** detector values: it is attrition, counted against the ITT denominator,
never a value. The exception is correctness: a usable run whose outcome is a model-attributable failure
(``build_fail``, ``start_fail``, ``output_invalid``; spec section 6.13) is data with 0 pass in
intention-to-treat, so its ``slot_tests_all_pass`` is ``absent`` (an ``output_invalid`` variant's row holds
only that value; its detector features stay missing).

Columns:

- one per (detector feature, scope): ``<feature_key>@span`` / ``<feature_key>@file``;
- derived features (scope ``derived``): ``equal_to_original`` (normalized part equals the normalized
  original), ``uses_new_dependency``, ``out_of_slot_edit``, ``slot_tests_all_pass`` (every discriminating,
  non-flaky check passes; ``absent`` for a build_fail / start_fail / output_invalid variant; ``n/a`` only
  for an uninformative target or an eval level that skipped the checks; ``error`` when the evaluation
  errored or a check result is missing) and ``exploited_any`` (any probe exploited);
- ``leak_score`` (numeric, from the target's leak report; not a prevalence feature).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import Sequence

    from backend.generation.patterns.detectors import DetectorSpec
    from backend.generation.patterns.detectors import Verdict
    from backend.generation.patterns.variants import Dataset
    from backend.generation.patterns.variants import TargetInfo
    from backend.generation.patterns.variants import VariantRecord

PRESENT, ABSENT, ERROR, NA = "present", "absent", "error", "n/a"
VALUES = (PRESENT, ABSENT, ERROR, NA)
DERIVED = ("equal_to_original", "uses_new_dependency", "out_of_slot_edit", "slot_tests_all_pass", "exploited_any")
DERIVED_SCOPE = "derived"
CORRECTNESS = "slot_tests_all_pass"
_STATUS_VALUE = {"present": PRESENT, "absent": ABSENT, "error": ERROR, "not_applicable": NA}


@dataclass(frozen=True)
class Column:
    key: str
    feature: str
    scope: str
    kind: str  # detector | derived
    detector_ref: str = ""
    polarity: str = "neutral"
    engine: str = ""


@dataclass
class Matrix:
    columns: list[Column]
    values: dict[str, dict[str, str]] = field(default_factory=dict)  # run id -> column key -> value
    severities: dict[str, dict[str, str]] = field(default_factory=dict)  # run id -> column key -> tool severity
    leak_score: dict[str, float | None] = field(default_factory=dict)

    def column(self, key: str) -> Column:
        for column in self.columns:
            if column.key == key:
                return column
        raise KeyError(key)

    def value(self, run_id: str, key: str) -> str | None:
        return self.values.get(run_id, {}).get(key)


def column_key(feature: str, scope: str) -> str:
    return feature if scope == DERIVED_SCOPE else f"{feature}@{scope}"


def detector_columns(specs: Sequence[DetectorSpec], scope_features: Sequence[str]) -> list[Column]:
    from backend.generation.patterns.detectors import requested_scopes

    columns: list[Column] = []
    seen: set[str] = set()
    for spec in specs:
        for scope in requested_scopes(spec, scope_features):
            key = column_key(spec.feature_key, scope)
            if key in seen:
                continue  # two versions of one feature in a run: the first one listed wins the column
            seen.add(key)
            columns.append(
                Column(
                    key=key,
                    feature=spec.feature_key,
                    scope=scope,
                    kind="detector",
                    detector_ref=spec.ref,
                    polarity=str(spec.content.get("polarity", "neutral")),
                    engine=spec.engine,
                ),
            )
    return columns


def derived_columns() -> list[Column]:
    return [Column(key=name, feature=name, scope=DERIVED_SCOPE, kind="derived") for name in DERIVED]


# ---------------------------------------------------------------------------
# Derived values
# ---------------------------------------------------------------------------


def equal_to_original(record: VariantRecord, info: TargetInfo) -> str:
    original = info.original_norm
    if not record.normalized_sha or original is None or not original.sha:
        return NA
    return PRESENT if record.normalized_sha == original.sha else ABSENT


def slot_tests_all_pass(record: VariantRecord, info: TargetInfo) -> str:  # noqa: PLR0911
    if not info.informative:
        return NA
    if record.model_failed:
        return ABSENT  # 0 pass in ITT (spec 6.13): the variant did not build, start or parse
    if record.eval_status == "error":
        return ERROR
    if record.checks is None:
        return NA
    wanted = [c for c in info.discriminating_checks if c not in set(record.flaky_checks)]
    if not wanted:
        return NA
    statuses = [record.checks.get(check) for check in wanted]
    if any(status == "fail" for status in statuses):
        return ABSENT
    if all(status == "pass" for status in statuses):
        return PRESENT
    return ERROR  # a discriminating check errored, was skipped or is missing: unknown, never "passes"


def exploited_any(record: VariantRecord) -> str:
    if record.probes is None:
        return NA
    statuses = {str(p.get("status")) for p in record.probes}
    if "exploited" in statuses:
        return PRESENT
    if "error" in statuses:
        return ERROR
    if "not_exploited" in statuses:
        return ABSENT
    return NA


def derived_values(record: VariantRecord, info: TargetInfo) -> dict[str, str]:
    return {
        "equal_to_original": equal_to_original(record, info),
        "uses_new_dependency": PRESENT if record.new_dependencies else ABSENT,
        "out_of_slot_edit": PRESENT if record.out_of_slot_edits else ABSENT,
        "slot_tests_all_pass": slot_tests_all_pass(record, info),
        "exploited_any": exploited_any(record),
    }


def leak_score(info: TargetInfo) -> float | None:
    leak = info.target.leak or {}
    score = leak.get("score")
    return None if score is None else float(score)


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


def build(
    dataset: Dataset,
    specs: Sequence[DetectorSpec],
    verdicts: Mapping[tuple[str, str], Sequence[Verdict]],
    scope_features: Sequence[str],
) -> Matrix:
    """The variant x feature matrix of every record with output or a model-attributable failure.

    A failure without output (``output_invalid``) gets a row holding only ``slot_tests_all_pass``.
    """
    columns = detector_columns(specs, scope_features) + derived_columns()
    matrix = Matrix(columns=columns)
    by_column = {c.key: c for c in columns if c.kind == "detector"}
    for record in dataset.in_matrix():
        info = dataset.targets[record.target_id]
        if not record.has_output:
            matrix.values[record.run_id] = {CORRECTNESS: slot_tests_all_pass(record, info)}
            matrix.severities[record.run_id] = {}
            matrix.leak_score[record.run_id] = leak_score(info)
            continue
        row: dict[str, str] = {}
        severities: dict[str, str] = {}
        for spec in specs:
            for verdict in verdicts.get((record.run_id, spec.ref), ()):
                key = column_key(spec.feature_key, verdict.scope)
                column = by_column.get(key)
                if column is None or column.detector_ref != spec.ref:
                    continue
                row[key] = _STATUS_VALUE[verdict.status]
                if verdict.tool_severity:
                    severities[key] = verdict.tool_severity
        row.update(derived_values(record, info))
        matrix.values[record.run_id] = row
        matrix.severities[record.run_id] = severities
        matrix.leak_score[record.run_id] = leak_score(info)
    return matrix


def freeze(dataset: Dataset, matrix: Matrix) -> list[dict[str, Any]]:
    """``PatternRun.summary["records"]``: the analysed records (``variants.freeze``) plus, for each record
    with a matrix row, the derived values as computed at analysis time (``derived``)."""
    from backend.generation.patterns.variants import freeze as freeze_records

    out = freeze_records(dataset)
    for entry in out:
        row = matrix.values.get(entry["run"]) if entry["run"] else None
        if row is not None:
            entry["derived"] = {k: row[k] for k in DERIVED if k in row}
    return out


def apply_frozen(matrix: Matrix, frozen: Sequence[Mapping[str, Any]]) -> Matrix:
    """Replace the matrix's derived values by the stored ones (a later evaluation changes nothing)."""
    for entry in frozen:
        derived = entry.get("derived")
        run_id = str(entry.get("run") or "")
        if derived is None or not run_id:
            continue
        row = matrix.values.setdefault(run_id, {})
        for key in DERIVED:
            row.pop(key, None)
        row.update({str(k): str(v) for k, v in derived.items()})
    return matrix


def from_hits(
    dataset: Dataset,
    specs: Sequence[DetectorSpec],
    hits: Sequence[Any],
    scope_features: Sequence[str],
) -> Matrix:
    """The matrix rebuilt from stored ``PatternHit`` rows (export and API after the run)."""
    from backend.generation.patterns.detectors import Verdict

    verdicts: dict[tuple[str, str], list[Verdict]] = {}
    ref_by_pk = {spec.pk: spec.ref for spec in specs}
    for hit in hits:
        ref = ref_by_pk.get(hit.detector_id)
        if ref is None:
            continue
        verdicts.setdefault((str(hit.run_id), ref), []).append(
            Verdict(
                scope=hit.scope,
                status=hit.status,
                error_reason=hit.error_reason,
                matches=list(hit.matches or []),
                tool_severity=hit.tool_severity,
            ),
        )
    return build(dataset, specs, verdicts, scope_features)
