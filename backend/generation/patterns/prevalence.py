"""Prevalence with ITT and measured denominators, CIs, comparisons and pooling (spec section 7.9 step 6).

Per (condition, target, feature, scope):

- ``n_itt`` = every planned variant (attrition counted as missing), ``n_measured`` = present + absent,
  ``errors`` and ``na`` counted separately, ``missing`` = planned variants without usable output (for
  ``slot_tests_all_pass`` a build_fail / start_fail / output_invalid variant is ``absent``, not missing:
  0 pass in ITT, spec section 6.13);
- ``p = k / n_measured`` with a Wilson 95% CI (``method="wilson"``); Clopper-Pearson is added when
  ``n_measured < 30``; rows of ``adaptive_n`` cells use the conservative repeated-look interval, Wilson at
  ``alpha / K`` with ``K`` the number of looks evaluated (``method="wilson_repeated_looks"``);
- when missing + errors exceed 5% of ``n_itt`` the worst/best-case ``bounds`` (all unknown absent / all
  present) over ``n_itt - na`` are given;
- security features carry the correctness rate (``slot_tests_all_pass``) next to them, and a conditional
  row ``p(feature | slot_tests_all_pass)`` is added (BaxBench sec_pass logic).

Rows with ``target="all"`` pool a condition's variants over its targets naively (labelled ``pooled``); the
stratified estimate is :func:`pooled`.

Comparisons between conditions use paired methods by ``(target, repeat)`` when ``seed_forwarded`` holds for
both conditions (Newcombe paired method 10, exact McNemar p), else independent ones (Newcombe hybrid score,
Fisher exact p). The method is recorded. A p-value is reported only for a planned comparison of a
confirmatory study (``planned=True``): the raw ``p`` next to ``p_adjusted``, corrected over the planned
family with the study plan's ``correction`` (Holm by default) and judged against its ``alpha``
(``significant``); exploratory comparisons carry Benjamini-Hochberg q-values and the family size. While the
study is still running (``interim``) p- and q-values are withheld.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.contracts.issues import make_issue
from backend.generation.patterns import features as feat
from backend.generation.patterns import stats
from backend.statistics.services.inference import benjamini_hochberg
from backend.statistics.services.inference import family_size
from backend.statistics.services.inference import holm

if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import Sequence

    from backend.generation.patterns.features import Column
    from backend.generation.patterns.features import Matrix
    from backend.generation.patterns.variants import Dataset
    from backend.generation.patterns.variants import VariantRecord

ATTRITION_BOUNDS_SHARE = 0.05
DIFFERENTIAL_ATTRITION_POINTS = 0.05
SMALL_N_EXACT = 30
FEW_CLUSTERS = 20
SECURITY_POLARITIES = frozenset({"insecure", "secure"})
ALL_TARGETS = "all"


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def prevalence_features(matrix: Matrix) -> list[Column]:
    """Every binary column (``leak_score`` is numeric and has no prevalence)."""
    return list(matrix.columns)


def _counts(records: Sequence[VariantRecord], matrix: Matrix, key: str) -> dict[str, int]:
    counts = {"k": 0, "absent": 0, "errors": 0, "na": 0, "missing": 0}
    for record in records:
        value = matrix.value(record.run_id, key) if record.run_id else None
        if value is None and not record.has_output:
            # Attrition. A model-attributable failure has a correctness value (absent, ITT), never this.
            counts["missing"] += 1
            continue
        if value == feat.PRESENT:
            counts["k"] += 1
        elif value == feat.ABSENT:
            counts["absent"] += 1
        elif value == feat.NA:
            counts["na"] += 1
        else:  # error, or no verdict at all: unknown, never clean
            counts["errors"] += 1
    return counts


def estimate(
    k: int,
    n_measured: int,
    *,
    n_itt: int,
    na: int,
    errors: int,
    missing: int,
    adaptive_n: bool = False,
    looks: int = 1,
) -> dict[str, Any]:
    """The interval fields of one prevalence row."""
    z = stats.z_for(0.05, looks) if adaptive_n else stats.Z95
    ci = stats.wilson(k, n_measured, z)
    row: dict[str, Any] = {
        "k": k,
        "n_measured": n_measured,
        "n_itt": n_itt,
        "errors": errors,
        "na": na,
        "missing": missing,
        "p": _r(k / n_measured) if n_measured else None,
        "ci_low": _r(ci[0]) if ci else None,
        "ci_high": _r(ci[1]) if ci else None,
        "method": "wilson_repeated_looks" if adaptive_n else "wilson",
        "adaptive_n": adaptive_n,
        "looks": looks if adaptive_n else None,
        "attrition": _r(missing / n_itt) if n_itt else None,
    }
    if 0 < n_measured < SMALL_N_EXACT:
        cp = stats.clopper_pearson(k, n_measured, alpha=0.05 / looks if adaptive_n else 0.05)
        row["cp_low"], row["cp_high"] = (_r(cp[0]), _r(cp[1])) if cp else (None, None)
    unknown = missing + errors
    denominator = n_itt - na
    if n_itt and unknown / n_itt > ATTRITION_BOUNDS_SHARE and denominator > 0:
        row["bounds"] = {"low": _r(k / denominator), "high": _r((k + unknown) / denominator)}
    return row


def prevalence(
    dataset: Dataset,
    matrix: Matrix,
    *,
    looks: Mapping[str, int] | None = None,
    cluster_stats: Mapping[tuple[int, str], Mapping[str, Any]] | None = None,
    n_min: int = 10,
) -> list[dict[str, Any]]:
    """Every prevalence row (per target, then the pooled ``target="all"`` row of each condition).

    ``looks`` holds the recorded number of looks per ``"<condition>:<target>"``; without a record, an
    ``adaptive_n`` group uses ``K = n_at_stop - n_min + 1`` (the stop rule evaluates once per finished
    variant from ``n_min`` on; WP-11's convention).
    """
    looks = looks or {}
    cluster_stats = cluster_stats or {}
    groups = dataset.by_group()
    by_condition: dict[int, list[VariantRecord]] = defaultdict(list)
    for record in dataset.records:
        by_condition[record.condition_id].append(record)
    rows: list[dict[str, Any]] = []
    columns = prevalence_features(matrix)
    correctness_key = feat.CORRECTNESS

    def group_looks(condition_id: int, target: str) -> int:
        recorded = looks.get(f"{condition_id}:{target}")
        if recorded is not None:
            return max(1, int(recorded))
        group = groups.get((condition_id, target), [])
        if not any(r.adaptive_n for r in group):
            return 1
        n_at_stop = sum(1 for r in group if r.run is not None)
        return max(1, n_at_stop - n_min + 1)

    def make(condition_id: int, target: str, records: Sequence[VariantRecord], column: Column) -> dict[str, Any]:
        counts = _counts(records, matrix, column.key)
        adaptive = any(r.adaptive_n for r in records)
        if target != ALL_TARGETS:
            k_looks = group_looks(condition_id, target)
        else:
            k_looks = max([group_looks(condition_id, t) for t in {r.target_id for r in records}] or [1])
        row = {
            "condition": condition_id,
            "condition_label": dataset.conditions.get(condition_id, {}).get("label", ""),
            "target": target,
            "feature": column.feature,
            "scope": column.scope,
            "column": column.key,
            "detector": column.detector_ref or None,
            "polarity": column.polarity,
            "pooled": target == ALL_TARGETS,
            **estimate(
                counts["k"],
                counts["k"] + counts["absent"],
                n_itt=len(records),
                na=counts["na"],
                errors=counts["errors"],
                missing=counts["missing"],
                adaptive_n=adaptive,
                looks=max(1, k_looks),
            ),
        }
        stats_row = cluster_stats.get((condition_id, target)) if target != ALL_TARGETS else None
        row["low_effective_n"] = bool(stats_row and stats_row.get("low_effective_n"))
        if column.polarity in SECURITY_POLARITIES and column.kind == "detector":
            c = _counts(records, matrix, correctness_key)
            n_c = c["k"] + c["absent"]
            row["correctness"] = {"k": c["k"], "n_measured": n_c, "p": _r(c["k"] / n_c) if n_c else None}
        return row

    def conditional(
        condition_id: int,
        target: str,
        records: Sequence[VariantRecord],
        column: Column,
    ) -> dict[str, Any]:
        passing = [r for r in records if r.has_output and matrix.value(r.run_id, correctness_key) == feat.PRESENT]
        counts = _counts(passing, matrix, column.key)
        adaptive = any(r.adaptive_n for r in records)
        return {
            "condition": condition_id,
            "condition_label": dataset.conditions.get(condition_id, {}).get("label", ""),
            "target": target,
            "feature": column.feature,
            "scope": column.scope,
            "column": column.key,
            "detector": column.detector_ref or None,
            "polarity": column.polarity,
            "pooled": target == ALL_TARGETS,
            "conditional_on": correctness_key,
            "low_effective_n": False,
            **estimate(
                counts["k"],
                counts["k"] + counts["absent"],
                n_itt=len(passing),
                na=counts["na"],
                errors=counts["errors"],
                missing=0,
                adaptive_n=adaptive,
                looks=group_looks(condition_id, target),
            ),
        }

    for (condition_id, target), records in sorted(
        groups.items(),
        key=lambda item: (dataset.conditions.get(item[0][0], {}).get("index", 0), item[0][1]),
    ):
        for column in columns:
            rows.append(make(condition_id, target, records, column))
            if column.polarity in SECURITY_POLARITIES and column.kind == "detector":
                rows.append(conditional(condition_id, target, records, column))
    if len(dataset.targets) > 1:
        for condition_id in sorted(by_condition, key=lambda c: dataset.conditions.get(c, {}).get("index", 0)):
            for column in columns:
                rows.append(make(condition_id, ALL_TARGETS, by_condition[condition_id], column))
    return rows


# ---------------------------------------------------------------------------
# Attrition
# ---------------------------------------------------------------------------


def attrition_issues(dataset: Dataset, study_kind: str) -> list[dict[str, Any]]:
    """``differential_attrition`` when compared conditions' attrition differs by more than 5 points (per target)."""
    issues: list[dict[str, Any]] = []
    per_target: dict[str, dict[int, tuple[int, int]]] = defaultdict(dict)
    for (condition_id, target), records in dataset.by_group().items():
        missing = sum(1 for r in records if not r.has_output)
        per_target[target][condition_id] = (missing, len(records))
    for target, by_condition in sorted(per_target.items()):
        rates = {c: m / n for c, (m, n) in by_condition.items() if n}
        if len(rates) < 2:
            continue
        spread = max(rates.values()) - min(rates.values())
        if spread > DIFFERENTIAL_ATTRITION_POINTS:
            issues.append(
                make_issue(
                    "differential_attrition",
                    study_kind,  # type: ignore[arg-type]
                    message=f"attrition differs by {spread * 100:.1f} points between conditions on target {target}",
                    data={"target": target, "attrition": {str(c): round(r, 6) for c, r in rates.items()}},
                ).model_dump(mode="json"),
            )
    return issues


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------


def _measured(value: str | None) -> bool:
    return value in {feat.PRESENT, feat.ABSENT}


def compare(
    dataset: Dataset,
    matrix: Matrix,
    column: Column,
    a: int,
    b: int,
    *,
    paired: bool,
) -> dict[str, Any]:
    """One condition-pair difference ``p_a - p_b`` with the method used; ``p_value`` is the raw test p."""
    records_a = [r for r in dataset.records if r.condition_id == a and r.in_matrix]
    records_b = [r for r in dataset.records if r.condition_id == b and r.in_matrix]
    out: dict[str, Any] = {
        "feature": column.feature,
        "scope": column.scope,
        "column": column.key,
        "a": a,
        "b": b,
        "a_label": dataset.conditions.get(a, {}).get("label", ""),
        "b_label": dataset.conditions.get(b, {}).get("label", ""),
        "method": "paired" if paired else "independent",
    }
    if paired:
        index_b = {(r.target_id, r.repeat_index): r for r in records_b}
        table = {"a": 0, "b": 0, "c": 0, "d": 0}
        for record in records_a:
            other = index_b.get((record.target_id, record.repeat_index))
            if other is None:
                continue
            va, vb = matrix.value(record.run_id, column.key), matrix.value(other.run_id, column.key)
            if not (_measured(va) and _measured(vb)):
                continue
            if va == feat.PRESENT and vb == feat.PRESENT:
                table["a"] += 1
            elif va == feat.PRESENT:
                table["b"] += 1
            elif vb == feat.PRESENT:
                table["c"] += 1
            else:
                table["d"] += 1
        estimate_ = stats.newcombe_paired(table["a"], table["b"], table["c"], table["d"])
        out.update(
            {
                "n_pairs": sum(table.values()),
                "table": table,
                "test": "mcnemar_exact",
                "p_value": stats.mcnemar_exact(table["b"], table["c"]),
            },
        )
    else:
        ka = sum(1 for r in records_a if matrix.value(r.run_id, column.key) == feat.PRESENT)
        na_ = sum(1 for r in records_a if _measured(matrix.value(r.run_id, column.key)))
        kb = sum(1 for r in records_b if matrix.value(r.run_id, column.key) == feat.PRESENT)
        nb = sum(1 for r in records_b if _measured(matrix.value(r.run_id, column.key)))
        estimate_ = stats.newcombe_independent(ka, na_, kb, nb)
        out.update(
            {
                "k_a": ka,
                "n_a": na_,
                "k_b": kb,
                "n_b": nb,
                "test": "fisher_exact",
                "p_value": stats.fisher_exact(ka, na_ - ka, kb, nb - kb),
            },
        )
    out["diff"], out["ci_low"], out["ci_high"] = (
        (_r(estimate_[0]), _r(estimate_[1]), _r(estimate_[2])) if estimate_ else (None, None, None)
    )
    return out


def comparisons(
    dataset: Dataset,
    matrix: Matrix,
    *,
    planned_features: set[str],
    reports_p: bool,
    interim: bool,
    correction: str = "holm",
    alpha: float = 0.05,
) -> list[dict[str, Any]]:
    """Every condition pair x feature column; ``planned_features`` holds the locked primary column keys.

    ``correction``/``alpha`` are the study plan's (``analysis_plan.correction``/``alpha``), applied over the
    planned rows."""
    condition_ids = sorted(dataset.conditions, key=lambda c: dataset.conditions[c].get("index", 0))
    rows: list[dict[str, Any]] = []
    for column in matrix.columns:
        for a, b in combinations(condition_ids, 2):
            paired = bool(dataset.conditions[a].get("seed_forwarded") and dataset.conditions[b].get("seed_forwarded"))
            row = compare(dataset, matrix, column, a, b, paired=paired)
            row["planned"] = reports_p and column.key in planned_features
            rows.append(row)
    _finish_p(rows, interim=interim, correction=correction, alpha=alpha)
    return rows


def adjust(p_values: Sequence[float | None], correction: str) -> list[float | None]:
    """Unrounded adjusted p-values under the plan's ``correction`` (``holm`` | ``bh`` | ``none``)."""
    if correction == "none":
        return list(p_values)
    if correction == "bh":
        return benjamini_hochberg(p_values, digits=None)
    return holm(p_values, digits=None)


def _finish_p(
    rows: list[dict[str, Any]],
    *,
    interim: bool,
    raw_key: str = "p_value",
    correction: str = "holm",
    alpha: float = 0.05,
) -> None:
    """Planned rows show the raw ``p`` and the plan-corrected ``p_adjusted`` over the planned family
    (condition pairs x primary features x scopes), with the plan's ``correction`` and ``alpha`` and
    ``significant = p_adjusted < alpha``; exploratory rows show BH ``q`` over their family; interim withholds
    every p, q and decision."""
    exploratory = [row for row in rows if not row["planned"]]
    planned = [row for row in rows if row["planned"]]
    q_values = benjamini_hochberg([row.get(raw_key) for row in exploratory])
    exploratory_family = family_size([row.get(raw_key) for row in exploratory])
    planned_raw = [row.get(raw_key) for row in planned]
    planned_family = family_size(planned_raw)
    planned_adjusted = adjust(planned_raw, correction)
    for row, q in zip(exploratory, q_values, strict=True):
        row["p"] = None
        row["p_adjusted"] = None
        row["q"] = None if interim else q
        row["family_size"] = exploratory_family
        row["correction"] = "bh"
        row["alpha"] = None
        row["significant"] = None
    for row, adjusted in zip(planned, planned_adjusted, strict=True):
        row["p"] = None if interim else stats_round(row.get(raw_key))
        row["p_adjusted"] = None if interim else stats_round(adjusted)
        row["q"] = None
        row["family_size"] = planned_family
        row["correction"] = correction
        row["alpha"] = alpha
        row["significant"] = None if interim or adjusted is None else bool(adjusted < alpha)
    for row in rows:
        row.pop(raw_key, None)
        row["interim"] = interim


def stats_round(value: float | None) -> float | None:
    from backend.statistics.services.inference import round_p

    return round_p(value) if value is not None else None


# ---------------------------------------------------------------------------
# Pooled across targets
# ---------------------------------------------------------------------------


def pooled(
    dataset: Dataset,
    matrix: Matrix,
    *,
    planned_features: set[str],
    reports_p: bool,
    interim: bool,
    exact_max_strata: int = 20,
    draws: int = 10_000,
    seed: int = 0,
    correction: str = "holm",
    alpha: float = 0.05,
) -> list[dict[str, Any]]:
    """Per condition pair x feature: per-target estimates, the MH pooled RD and the permutation p.

    With fewer than 20 targets (the normal case) no cluster-robust SE or bootstrap is used; the rows are
    labelled ``few_clusters``. One target gives ``method="per_target"`` only.
    """
    condition_ids = sorted(dataset.conditions, key=lambda c: dataset.conditions[c].get("index", 0))
    out: list[dict[str, Any]] = []
    for column in matrix.columns:
        for a, b in combinations(condition_ids, 2):
            strata: list[stats.Stratum] = []
            per_target: list[dict[str, Any]] = []
            for target in sorted(dataset.targets):
                ra = [r for r in dataset.records if r.condition_id == a and r.target_id == target and r.in_matrix]
                rb = [r for r in dataset.records if r.condition_id == b and r.target_id == target and r.in_matrix]
                k1 = sum(1 for r in ra if matrix.value(r.run_id, column.key) == feat.PRESENT)
                n1 = sum(1 for r in ra if _measured(matrix.value(r.run_id, column.key)))
                k0 = sum(1 for r in rb if matrix.value(r.run_id, column.key) == feat.PRESENT)
                n0 = sum(1 for r in rb if _measured(matrix.value(r.run_id, column.key)))
                stratum = stats.Stratum(key=target, k1=k1, n1=n1, k0=k0, n0=n0)
                strata.append(stratum)
                est = stats.newcombe_independent(k1, n1, k0, n0)
                per_target.append(
                    {
                        "target": target,
                        "k_a": k1,
                        "n_a": n1,
                        "k_b": k0,
                        "n_b": n0,
                        "rd": _r(est[0]) if est else None,
                        "ci_low": _r(est[1]) if est else None,
                        "ci_high": _r(est[2]) if est else None,
                    },
                )
            usable = [s for s in strata if s.usable]
            notes: list[str] = []
            if len(usable) < FEW_CLUSTERS:
                notes.append("few_clusters")
            row: dict[str, Any] = {
                "feature": column.feature,
                "scope": column.scope,
                "column": column.key,
                "a": a,
                "b": b,
                "per_target": per_target,
                "n_targets": len(usable),
                "planned": reports_p and column.key in planned_features,
            }
            if len(usable) >= 2:
                mh = stats.mantel_haenszel_rd(usable)
                perm = stats.stratified_permutation_p(
                    usable,
                    exact_max_strata=exact_max_strata,
                    draws=draws,
                    seed=seed,
                )
                row["method"] = "mh_stratified"
                row["pooled"] = (
                    {"rd": _r(mh["rd"]), "ci": [_r(mh["ci_low"]), _r(mh["ci_high"])], "se": _r(mh["se"])}
                    if mh
                    else None
                )
                row["permutation"] = {"method": perm["method"], "arrangements": perm["arrangements"]} if perm else None
                row["p_value"] = perm["p"] if perm else None
            else:
                row["method"] = "per_target"
                row["pooled"] = None
                row["p_value"] = None
                notes.append("single_target")
            row["notes"] = notes
            out.append(row)
    _finish_p(out, interim=interim, correction=correction, alpha=alpha)
    for row in out:
        row["permutation_p"] = row.pop("p")
    return out
