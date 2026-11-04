"""PatternRun creation, labelling against the pre-registration, and execution (spec sections 7.9 and 9.7).

``create_pattern_run`` pins the detector set (``"default"`` or explicit refs), the normalizer version, the
cluster parameters and the scope, derives ``detector_set_hash`` and the label (``planned`` only when every
locked field of a confirmatory study's ``analysis_plan.patterns`` matches; otherwise ``exploratory``) and
queues the work as a ``StudyPostRun(kind=patterns)`` (the executor's post_run lane; WP-10's
``study.automation.run_post_run`` calls :func:`execute`). Re-running costs no LLM money and always creates
a new, immutable PatternRun.

``execute(pattern_run)`` (also ``patterns.run``) normalizes, clusters, runs every detector, writes the
hits and clusters, and stores features, prevalence, comparisons, pooled estimates, cluster statistics and
issues in ``PatternRun.summary``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.slot import ClusterParams
from backend.generation.contracts.slot import PatternScope
from backend.generation.patterns import cluster as clustering
from backend.generation.patterns import detectors as det
from backend.generation.patterns import features as feat
from backend.generation.patterns import normalize as norm
from backend.generation.patterns import prevalence as prev
from backend.generation.patterns import variants as var

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Sequence

    from backend.generation.models import PatternRun
    from backend.generation.models import Study

logger = logging.getLogger(__name__)

SUMMARY_VERSION = 2  # 2: summary["records"] (the analysed record set) and plan-corrected planned p
FINAL_STUDY_STATUSES = frozenset({"completed", "stopped", "cancelled"})


class PatternRunError(ValueError):
    """A pattern run request that cannot be honoured (not a slot study, unknown detectors, bad params)."""


# ---------------------------------------------------------------------------
# Detector selection
# ---------------------------------------------------------------------------


def visible_detectors(user: Any) -> Any:
    """Non-archived detector versions the user may use (system, shared, or their own)."""
    from backend.generation.models import PatternDetector

    qs = PatternDetector.objects.filter(is_archived=False)
    if user is None or getattr(user, "is_staff", False):
        return qs
    return qs.filter(Q(is_system=True) | Q(visibility="shared") | Q(created_by=user))


def study_targets(study: Study, scope_targets: list[str] | str = "all") -> list[Any]:
    """The slot targets that are units of the study (optionally limited to ``scope_targets``)."""
    from backend.generation.models import SlotTarget

    qs = SlotTarget.objects.filter(study_units__study=study).distinct()
    if scope_targets != "all":
        qs = qs.filter(pk__in=[str(t) for t in scope_targets])
    return list(qs.select_related("slot_set"))


def default_detector_refs(study: Study, targets: Sequence[Any], user: Any = None) -> list[str]:
    """``default`` (section 7.9): every visible detector covering a target language, the probe-mapped
    detectors when a target links probes, and the manual detectors with labels on the targets.

    The latest non-archived version of each slug is pinned (once, here); the PatternRun stores the pinned
    refs, so nothing is read as "latest" afterwards.
    """
    from backend.generation.models import ClusterLabel

    languages = {norm.canonical_language(t.language) for t in targets}
    any_probes = any(t.linked_probes for t in targets)
    labelled = set(
        ClusterLabel.objects.filter(slot_target__in=list(targets)).values_list("detector__slug", "detector__version"),
    )
    latest: dict[str, Any] = {}
    for row in visible_detectors(user).order_by("slug", "-version"):
        latest.setdefault(row.slug, row)
    refs: list[str] = []
    for slug in sorted(latest):
        row = latest[slug]
        spec = det.DetectorSpec.from_row(row)
        if spec.engine == "probe":
            if any_probes:
                refs.append(row.pinned_ref)
        elif spec.engine == "manual":
            continue
        elif any(spec.applies_to(lang) for lang in languages):
            refs.append(row.pinned_ref)
    # Manual detectors: exactly the labelled versions (labels name a version).
    from backend.generation.models import PatternDetector

    for slug, version in sorted(labelled):
        row = PatternDetector.objects.filter(slug=slug, version=version).first()
        if row is not None and row.pinned_ref not in refs:
            refs.append(row.pinned_ref)
    return refs


# ---------------------------------------------------------------------------
# Pre-registration
# ---------------------------------------------------------------------------


def locked_patterns(study: Study) -> dict[str, Any] | None:
    """The locked ``analysis_plan.patterns`` of a confirmatory study (``None`` when nothing is locked)."""
    if study.kind != "confirmatory" or not study.locked_at or not study.prereg_hash:
        return None
    plan = study.analysis_plan or {}
    patterns = plan.get("patterns")
    return patterns if isinstance(patterns, dict) else None


def _norm_params(params: Any) -> dict[str, Any]:
    return ClusterParams.model_validate(params or {}).model_dump(mode="json")


def _norm_scope(scope: Any) -> dict[str, Any]:
    return PatternScope.model_validate(scope or {}).model_dump(mode="json")


def label_for(
    study: Study,
    *,
    detector_set_hash: str,
    normalizer_version: int,
    cluster_params: dict[str, Any],
    scope: dict[str, Any],
    is_live: bool = False,
) -> str:
    """``planned`` iff the study is confirmatory, locked, and every locked pattern field matches."""
    locked = locked_patterns(study)
    if locked is None or is_live:
        return "exploratory"
    try:
        locked_hash = locked.get("detector_set_hash") or ""
        if not locked_hash and locked.get("detectors"):
            # WP-10's plan may fix the set by pinned refs instead of the hash.
            locked_hash = det.detector_set_hash_for_refs(locked["detectors"])
        same = (
            bool(locked_hash)
            and locked_hash == detector_set_hash
            and int(locked.get("normalizer_version", -1)) == int(normalizer_version)
            and _norm_params(locked.get("cluster_params")) == _norm_params(cluster_params)
            and _norm_scope(locked.get("scope")) == _norm_scope(scope)
        )
    except (ValueError, TypeError):  # includes DetectorRefError (a ValueError)
        return "exploratory"
    return "planned" if same else "exploratory"


def plan_multiplicity(study: Study) -> tuple[str, float]:
    """``(correction, alpha)`` of the study's analysis plan (``holm`` and 0.05 when it states none)."""
    from backend.generation.study.analysis_plan import CORRECTIONS

    plan = study.analysis_plan or {}
    correction = str(plan.get("correction") or "holm")
    if correction not in CORRECTIONS:
        correction = "holm"
    try:
        alpha = float(plan.get("alpha", 0.05))
    except (TypeError, ValueError):
        alpha = 0.05
    return correction, alpha


def planned_columns(study: Study, specs: Sequence[det.DetectorSpec], scope: dict[str, Any]) -> set[str]:
    """Column keys of the locked primary features (detector refs or feature keys in the plan)."""
    locked = locked_patterns(study) or {}
    wanted = [str(x) for x in locked.get("primary_features") or []]
    keys: set[str] = set()
    by_ref = {s.ref: s for s in specs}
    by_pinned = {s.pinned_ref: s for s in specs}
    for entry in wanted:
        spec = by_pinned.get(entry) or by_ref.get(entry.split("#", 1)[0])
        feature = spec.feature_key if spec is not None else entry
        if feature in feat.DERIVED:
            keys.add(feature)
            continue
        for scope_name in scope.get("features") or det.SCOPES:
            keys.add(feat.column_key(feature, scope_name))
    return keys


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def create_pattern_run(
    study: Study,
    *,
    detectors: list[str] | str = "default",
    cluster_params: dict[str, Any] | None = None,
    scope: dict[str, Any] | None = None,
    user: Any = None,
    queue: bool = True,
    trigger: str = "manual",
) -> PatternRun:
    """A pending PatternRun with its pinned detector set and label, queued as a patterns post-run."""
    from backend.generation.models import PatternRun
    from backend.generation.models import StudyPostRun

    if study.mode != "slot":
        msg = "pattern analysis needs a slot study"
        raise PatternRunError(msg)
    params = _norm_params(cluster_params)
    scope_doc = _norm_scope(scope)
    targets = study_targets(study, scope_doc["targets"])
    if detectors == "default":
        refs = default_detector_refs(study, targets, user or study.created_by)
    elif isinstance(detectors, list) and detectors:
        refs = [str(r) for r in detectors]
    else:
        msg = 'detectors must be "default" or a non-empty list of refs'
        raise PatternRunError(msg)
    try:
        specs = det.resolve_refs(refs)
    except det.DetectorRefError as exc:
        raise PatternRunError(str(exc)) from exc
    set_hash = det.detector_set_hash(specs)
    label = label_for(
        study,
        detector_set_hash=set_hash,
        normalizer_version=norm.NORMALIZER_VERSION,
        cluster_params=params,
        scope=scope_doc,
    )
    with transaction.atomic():
        pattern_run = PatternRun(
            study=study,
            detectors=[s.pinned_ref for s in specs],
            detector_set_hash=set_hash,
            normalizer_version=norm.NORMALIZER_VERSION,
            cluster_params=params,
            scope=scope_doc,
            label=label,
            created_by=user,
        )
        pattern_run.full_clean()
        pattern_run.save()
        if queue:
            StudyPostRun.objects.create(
                study=study,
                kind="patterns",
                trigger=trigger,
                config={
                    "detectors": detectors if detectors == "default" else refs,
                    "cluster_params": params,
                    "scope": scope_doc,
                },
                pattern_run=pattern_run,
            )
    return pattern_run


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _looks(study: Study) -> dict[str, int]:
    from backend.generation.models import PatternRun

    live = PatternRun.objects.filter(study=study, is_live=True).order_by("-created_at").first()
    if live is None:
        return {}
    return {str(k): int(v) for k, v in (live.summary or {}).get("looks", {}).items()}


def _ci_rule_n_min(study: Study) -> int:
    """``n_min`` of the study's ``ci_width_reached`` stop rule (its policy), 10 when there is none."""
    policy = getattr(study, "resource_policy", None)
    rules = ((getattr(policy, "content", None) or {}).get("study") or {}).get("stop_rules") or []
    for rule in rules:
        if isinstance(rule, dict) and rule.get("code") == "ci_width_reached":
            return int(rule.get("n_min") or 10)
    return 10


def _record_variant_results(dataset: var.Dataset) -> None:
    """Fill the pattern pass's fields of ``SlotVariantResult`` that WP-12 leaves empty (never overwrites).

    ``normalized_sha`` / ``normalizer_version`` and ``diff_vs_original.{normalized_equal,
    ast_edit_distance}``; an unparsable variant keeps ``normalized_sha=""`` (its reason is in the summary).
    """
    from backend.generation.models import SlotVariantResult
    from backend.generation.patterns.drafts import diff_vs_original

    for record in dataset.with_output():
        result = getattr(record.run, "variant_result", None)
        if result is None or record.part_text is None:
            continue
        info = dataset.targets[record.target_id]
        fields: dict[str, Any] = {}
        if not result.normalized_sha and record.normalized_sha:
            fields["normalized_sha"] = record.normalized_sha
            fields["normalizer_version"] = norm.NORMALIZER_VERSION
        diff = dict(result.diff_vs_original or {})
        if info.original_text is not None and (
            diff.get("normalized_equal") is None or diff.get("ast_edit_distance") is None
        ):
            computed = diff_vs_original(info.original_text, record.part_text, info.target.language)
            merged = {**computed, **{k: v for k, v in diff.items() if v is not None}}
            if merged != diff:
                fields["diff_vs_original"] = merged
        if fields:
            SlotVariantResult.objects.filter(pk=result.pk).update(**fields)


def _hit_rows(
    pattern_run: PatternRun,
    specs: Sequence[det.DetectorSpec],
    verdicts: dict,
    *,
    provisional: bool,
) -> list[Any]:
    from backend.generation.models import PatternHit

    rows = []
    by_ref = {s.ref: s for s in specs}
    for (run_id, ref), items in verdicts.items():
        spec = by_ref[ref]
        for verdict in items:
            reason = verdict.error_reason if verdict.status == "error" else ""
            if verdict.status == "error" and not reason:
                reason = "tool_error"
            rows.append(
                PatternHit(
                    pattern_run=pattern_run,
                    run_id=run_id,
                    detector_id=spec.pk,
                    feature_key=spec.feature_key,
                    scope=verdict.scope,
                    status=verdict.status,
                    error_reason=reason,
                    matches=verdict.matches,
                    tool_severity=verdict.tool_severity,
                    provisional=provisional,
                ),
            )
    return rows


def detector_inputs(dataset: var.Dataset, specs: Sequence[det.DetectorSpec]) -> list[det.VariantInput]:
    manual_refs = [s.ref for s in specs if s.engine == "manual"]
    labels = var.labels_for(dataset, manual_refs) if manual_refs else {}
    return [
        var.variant_input(
            record,
            dataset.targets[record.target_id],
            labels.get((record.target_id, record.normalized_sha)),
        )
        for record in dataset.with_output()
    ]


def compute_clusters(dataset: var.Dataset, params: dict[str, Any]) -> dict[str, list[clustering.Cluster]]:
    """Clusters per target (all conditions together), with ``includes_original`` marked."""
    out: dict[str, list[clustering.Cluster]] = {}
    by_target: dict[str, list[clustering.Item]] = defaultdict(list)
    for record in dataset.with_output():
        result = record.norm
        by_target[record.target_id].append(
            clustering.Item(
                key=record.run_id,
                sha=result.sha if result else "",
                tokens=result.tokens if result else (),
                group=record.condition_id,
            ),
        )
    for target_id, items in by_target.items():
        clusters = clustering.cluster_items(items, params)
        info = dataset.targets[target_id]
        original = None
        if info.original_norm is not None and info.original_norm.ok:
            original = clustering.Item(key="original", sha=info.original_norm.sha, tokens=info.original_norm.tokens)
        clustering.mark_original(clusters, original, items, params)
        out[target_id] = clusters
    return out


def cluster_statistics(
    dataset: var.Dataset,
    clusters: dict[str, list[clustering.Cluster]],
) -> dict[tuple[int, str], dict[str, Any]]:
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for (condition_id, target_id), records in dataset.by_group().items():
        items = [
            clustering.Item(key=r.run_id, sha=r.normalized_sha, tokens=r.norm.tokens if r.norm else ())
            for r in records
            if r.has_output
        ]
        out[(condition_id, target_id)] = clustering.group_stats(items, clusters.get(target_id, []))
    return out


def _write_clusters(
    pattern_run: PatternRun,
    dataset: var.Dataset,
    clusters: dict[str, list[clustering.Cluster]],
) -> None:
    from backend.generation.models import VariantCluster
    from backend.generation.models import VariantClusterMember

    for target_id, items in clusters.items():
        for item in items:
            row = VariantCluster.objects.create(
                pattern_run=pattern_run,
                slot_target=dataset.targets[target_id].target,
                method=item.method,
                key=item.key,
                size=item.size,
                representative_id=item.representative,
                includes_original=item.includes_original,
            )
            VariantClusterMember.objects.bulk_create(
                [
                    VariantClusterMember(cluster=row, run_id=m, distance=item.distances.get(m, 0.0))
                    for m in item.members
                ],
            )


def _target_summary(dataset: var.Dataset) -> dict[str, Any]:
    out = {}
    for target_id, info in dataset.targets.items():
        target = info.target
        out[target_id] = {
            "label": target.label,
            "kind": target.kind,
            "language": target.language,
            "path": info.path,
            "match_key": target.match_key,
            "slot_set": str(target.slot_set_id),
            "parent_run": str(target.slot_set.parent_run_id),
            "parent_model": target.slot_set.parent_model,
            "parent_harness": target.slot_set.parent_harness,
            "original_normalized_sha": info.original_norm.sha if info.original_norm else "",
            "original_parse_reason": info.original_norm.reason if info.original_norm else "original_unavailable",
            "informative": info.informative,
            "leak_score": feat.leak_score(info),
        }
    return out


def build_summary(
    pattern_run: PatternRun,
    dataset: var.Dataset,
    specs: Sequence[det.DetectorSpec],
    matrix: feat.Matrix,
    clusters: dict[str, list[clustering.Cluster]],
    tool_versions: dict[str, str],
) -> dict[str, Any]:
    study = dataset.study
    scope = _norm_scope(pattern_run.scope)
    interim = study.status not in FINAL_STUDY_STATUSES
    reports_p = pattern_run.label == "planned" and study.kind == "confirmatory"
    planned = planned_columns(study, specs, scope) if reports_p else set()
    correction, alpha = plan_multiplicity(study)
    group_stats = cluster_statistics(dataset, clusters)
    issues: list[dict[str, Any]] = []
    for (condition_id, target_id), row in sorted(group_stats.items()):
        if row["low_effective_n"]:
            issues.append(
                make_issue(
                    "low_effective_n",
                    study.kind,
                    message=(
                        f"exact-duplicate share {row['exact_dup_share']:.2f} > 0.5 for condition "
                        f"{dataset.conditions.get(condition_id, {}).get('label', condition_id)} on target {target_id}"
                    ),
                    data={"condition": condition_id, "target": target_id},
                ).model_dump(mode="json"),
            )
    issues.extend(prev.attrition_issues(dataset, study.kind))
    pooled_rows = prev.pooled(
        dataset,
        matrix,
        planned_features=planned,
        reports_p=reports_p,
        interim=interim,
        exact_max_strata=int(getattr(settings, "PATTERNS_EXACT_PERMUTATION_MAX_TARGETS", 20)),
        draws=int(getattr(settings, "PATTERNS_PERMUTATION_DRAWS", 10_000)),
        seed=int(study.base_seed or 0),
        correction=correction,
        alpha=alpha,
    )
    if len(dataset.conditions) > 1 and 0 < len(dataset.targets) < prev.FEW_CLUSTERS:
        issues.append(
            make_issue(
                "few_clusters",
                study.kind,
                message=f"{len(dataset.targets)} targets: per-target, MH-pooled and permutation results only",
                data={"targets": len(dataset.targets)},
            ).model_dump(mode="json"),
        )
    unparsed = [
        {"run_id": r.run_id, "target": r.target_id, "reason": r.norm.reason if r.norm else "file_missing"}
        for r in dataset.with_output()
        if not r.normalized_sha
    ]
    return {
        "version": SUMMARY_VERSION,
        "label": pattern_run.label,
        "interim": interim,
        "normalizer_version": pattern_run.normalizer_version,
        "cluster_params": _norm_params(pattern_run.cluster_params),
        "scope": scope,
        "detector_set_hash": pattern_run.detector_set_hash,
        "detectors": [
            {"ref": s.pinned_ref, "engine": s.engine, "feature_key": s.feature_key, "tool": s.tool} for s in specs
        ],
        "tool_versions": tool_versions,
        "planned_features": sorted(planned),
        "n_planned": len(dataset.records),
        "n_with_output": len(dataset.with_output()),
        "n_unparsed": len(unparsed),
        "unparsed": unparsed,
        "conditions": {str(k): v for k, v in dataset.conditions.items()},
        "targets": _target_summary(dataset),
        "features": [
            {
                "key": c.key,
                "feature": c.feature,
                "scope": c.scope,
                "kind": c.kind,
                "detector": c.detector_ref,
                "polarity": c.polarity,
            }
            for c in matrix.columns
        ],
        "cluster_stats": [
            {"condition": c, "target": t, **row}
            for (c, t), row in sorted(group_stats.items(), key=lambda i: (i[0][0], i[0][1]))
        ],
        "prevalence": prev.prevalence(
            dataset,
            matrix,
            looks=_looks(study),
            cluster_stats=group_stats,
            n_min=_ci_rule_n_min(study),
        ),
        "comparisons": prev.comparisons(
            dataset,
            matrix,
            planned_features=planned,
            reports_p=reports_p,
            interim=interim,
            correction=correction,
            alpha=alpha,
        ),
        "pooled": pooled_rows,
        "multiplicity": {"correction": correction, "alpha": alpha},
        "records": feat.freeze(dataset, matrix),
        "issues": issues,
    }


def _semgrep_user(pattern_run: PatternRun) -> Any:
    return pattern_run.created_by or pattern_run.study.created_by


def execute(pattern_run: PatternRun, *, runner: Any = None) -> PatternRun:
    """Run the whole analysis for a pending PatternRun; a failure leaves it ``failed`` with the reason."""
    from backend.generation.models import PatternHit
    from backend.generation.models import PatternRun as PatternRunModel

    if pattern_run.status == "completed":
        msg = f"pattern run {pattern_run.pk} is completed; create a new one to re-run"
        raise PatternRunError(msg)
    if pattern_run.is_live:
        msg = "the live pattern run is fed by patterns.live.score_variant, not executed"
        raise PatternRunError(msg)
    PatternRunModel.objects.filter(pk=pattern_run.pk).update(status="running")
    pattern_run.status = "running"
    try:
        study = pattern_run.study
        scope = _norm_scope(pattern_run.scope)
        specs = det.resolve_refs(pattern_run.detectors)
        dataset = var.load(study, scope["targets"])
        inputs = detector_inputs(dataset, specs)
        verdicts, versions = det.run_all(
            specs,
            inputs,
            scope["features"],
            user=_semgrep_user(pattern_run),
            runner=runner,
        )
        clusters = compute_clusters(dataset, _norm_params(pattern_run.cluster_params))
        matrix = feat.build(dataset, specs, verdicts, scope["features"])
        summary = build_summary(pattern_run, dataset, specs, matrix, clusters, versions)
        with transaction.atomic():
            PatternHit.objects.filter(pattern_run=pattern_run).delete()
            PatternHit.objects.bulk_create(_hit_rows(pattern_run, specs, verdicts, provisional=False), batch_size=500)
            pattern_run.clusters.all().delete()
            _write_clusters(pattern_run, dataset, clusters)
            _record_variant_results(dataset)
            pattern_run.summary = summary
            pattern_run.status = "completed"
            pattern_run.completed_at = timezone.now()
            pattern_run.save(update_fields=["summary", "status", "completed_at"])
    except Exception as exc:
        logger.exception("pattern run %s failed", pattern_run.pk)
        pattern_run.status = "failed"
        pattern_run.summary = {"version": SUMMARY_VERSION, "error": f"{type(exc).__name__}: {exc}"[:2000]}
        pattern_run.save(update_fields=["status", "summary"])
        raise
    return pattern_run


def run_post_run(post_run: Any, *, runner: Any = None) -> PatternRun:
    """Execute a ``StudyPostRun(kind=patterns)``: create its PatternRun from ``config`` when the launch
    attached the post-run without one (``{"detectors": "default" | [refs], "cluster_params"?, "scope"?}``)."""
    pattern_run = post_run.pattern_run
    if pattern_run is None:
        config = post_run.config or {}
        pattern_run = create_pattern_run(
            post_run.study,
            detectors=config.get("detectors", "default"),
            cluster_params=config.get("cluster_params"),
            scope=config.get("scope"),
            user=post_run.study.created_by,
            queue=False,
        )
        post_run.pattern_run = pattern_run
        post_run.save(update_fields=["pattern_run"])
    return execute(pattern_run, runner=runner)


def runs_for(study: Study, *, live: bool | None = None) -> Iterable[PatternRun]:
    from backend.generation.models import PatternRun

    qs = PatternRun.objects.filter(study=study)
    if live is not None:
        qs = qs.filter(is_live=live)
    return qs.order_by("-created_at")
