"""Per-variant provisional scoring of the primary features on the eval lane (spec section 7.9 step 0, 7.8).

After each variant's evaluation, :func:`score_variant` runs the study's ``Design.slot_primary_features``
detectors over that variant (in-process engines immediately, Semgrep through the rulepack) and writes
``provisional`` hits into the study's **live** PatternRun (``is_live=True``; one per study, never
completed, always ``exploratory``). The final PatternRun recomputes everything.

:func:`ci_width_reached` is the ``ci_width_reached`` stop-rule check (section 4.6; called by WP-11's
``budget.stop_rules``): a condition x target is reached when, for every listed primary feature, the Wilson
95% half-width of the live estimate is below ``rule.threshold`` and the measured n is at least
``rule.n_min``. Every call counts as one *look* per still-running (condition, target); the count ``K`` is
kept in the live run's ``summary["looks"]`` and the final prevalence of ``adaptive_n`` cells uses Wilson at
``alpha / K`` (the conservative repeated-look interval).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction

from backend.generation.patterns import detectors as det
from backend.generation.patterns import normalize as norm
from backend.generation.patterns import stats
from backend.generation.patterns import variants as var

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import PatternRun
    from backend.generation.models import Study

logger = logging.getLogger(__name__)

PRIMARY_SCOPE = "span"


def primary_specs(study: Study) -> list[det.DetectorSpec]:
    refs = list((study.design or {}).get("slot_primary_features") or [])
    return det.resolve_refs(refs) if refs else []


def live_run(study: Study, *, lock: bool = False) -> PatternRun | None:
    """The study's live PatternRun (created on first use); ``None`` without primary features."""
    from backend.generation.contracts.slot import ClusterParams
    from backend.generation.contracts.slot import PatternScope
    from backend.generation.models import PatternRun

    qs = PatternRun.objects.filter(study=study, is_live=True)
    if lock:
        qs = qs.select_for_update()
    existing = qs.order_by("created_at").first()
    if existing is not None:
        return existing
    specs = primary_specs(study)
    if not specs:
        return None
    run = PatternRun(
        study=study,
        detectors=[s.pinned_ref for s in specs],
        detector_set_hash=det.detector_set_hash(specs),
        normalizer_version=norm.NORMALIZER_VERSION,
        cluster_params=ClusterParams().model_dump(mode="json"),
        scope=PatternScope().model_dump(mode="json"),
        is_live=True,
        label="exploratory",
        status="running",
        summary={"looks": {}},
    )
    run.full_clean()
    run.save()
    return run


def score_variant(run: GenerationRun, *, runner: Any = None) -> list[Any]:
    """Write provisional hits of the study's live pattern run for one variant; returns the hit rows.

    A variant without usable output gets no hits (it is attrition). Never raises for detector problems:
    those are ``error`` hits.
    """
    from backend.generation.models import PatternHit
    from backend.generation.models import SlotTarget
    from backend.generation.models import StudyCell
    from backend.generation.runs.view import run_views_code

    if not run.is_slot_variant or run.study_id is None or run.cell_id is None:
        return []
    study = run.study
    if study.mode != "slot":
        return []
    with transaction.atomic():
        pattern_run = live_run(study, lock=True)
        if pattern_run is None:
            return []
        specs = det.resolve_refs(pattern_run.detectors)
        cell = StudyCell.objects.select_related("condition", "unit").get(pk=run.cell_id)
        target = SlotTarget.objects.select_related("slot_set", "slot_set__parent_run").get(pk=run.slot_target_id)
        info = var.load_targets([target])[str(target.pk)]
        code = run_views_code([run], "app").get(str(run.pk)) if run.usable else None
        record = var.build_record(cell, run, info, code)
        if not record.has_output:
            return []
        dataset = var.Dataset(study=study, targets={info.id: info}, records=[record], conditions={})
        manual = [s.ref for s in specs if s.engine == "manual"]
        labels = var.labels_for(dataset, manual) if manual else {}
        inputs = [var.variant_input(record, info, labels.get((record.target_id, record.normalized_sha)))]
        # One hit per (variant, detector): the primary scope only (WP-11 reads them as such).
        verdicts, _versions = det.run_all(specs, inputs, [PRIMARY_SCOPE], user=study.created_by, runner=runner)
        rows = []
        by_ref = {s.ref: s for s in specs}
        for (run_id, ref), items in verdicts.items():
            spec = by_ref[ref]
            for verdict in items:
                reason = (verdict.error_reason or "tool_error") if verdict.status == "error" else ""
                hit, _ = PatternHit.objects.update_or_create(
                    pattern_run=pattern_run,
                    run_id=run_id,
                    detector_id=spec.pk,
                    scope=verdict.scope,
                    defaults={
                        "feature_key": spec.feature_key,
                        "status": verdict.status,
                        "error_reason": reason,
                        "matches": verdict.matches,
                        "tool_severity": verdict.tool_severity,
                        "provisional": True,
                    },
                )
                rows.append(hit)
    return rows


def _current_run_ids(study: Study) -> set[str]:
    from backend.generation.runs.view import current_runs

    return {str(pk) for pk in current_runs(study, "main").values_list("pk", flat=True)}


def live_estimates(
    study: Study,
    feature_refs: list[str] | None = None,
) -> dict[tuple[int, str], dict[str, dict[str, Any]]]:
    """``{(condition id, target id): {feature_key: {k, n, half_width}}}`` from the live hits of current runs."""
    from backend.generation.models import GenerationRun
    from backend.generation.models import PatternHit

    pattern_run = live_run(study)
    if pattern_run is None:
        return {}
    specs = det.resolve_refs(feature_refs) if feature_refs else det.resolve_refs(pattern_run.detectors)
    wanted = {s.pk: s for s in specs}
    current = _current_run_ids(study)
    meta = {
        str(pk): (condition_id, str(target_id))
        for pk, condition_id, target_id in GenerationRun.objects.filter(pk__in=current).values_list(
            "pk",
            "condition_id",
            "slot_target_id",
        )
    }
    counts: dict[tuple[int, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    hits = PatternHit.objects.filter(pattern_run=pattern_run, detector_id__in=list(wanted), scope=PRIMARY_SCOPE)
    for run_id, detector_id, status in hits.values_list("run_id", "detector_id", "status"):
        key = meta.get(str(run_id))
        if key is None:
            continue
        feature = wanted[detector_id].feature_key
        if status == "present":
            counts[key][feature][0] += 1
            counts[key][feature][1] += 1
        elif status == "absent":
            counts[key][feature][1] += 1
    out: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
    for key, by_feature in counts.items():
        out[key] = {
            feature: {"k": k, "n": n, "half_width": stats.wilson_half_width(k, n)}
            for feature, (k, n) in by_feature.items()
        }
    return out


def ci_width_reached(study: Study, rule: Any) -> list[dict[str, Any]]:
    """The (condition, target) pairs whose primary features all reached the precision ``rule`` asks for.

    ``rule`` is a ``contracts.resources.StopRule`` (or its dict) with ``code="ci_width_reached"``,
    ``threshold`` (Wilson 95% half-width), ``n_min`` and ``features`` (detector refs; empty = all primary
    features). Refused (``ValueError``) for confirmatory studies. Records one look per evaluated pair.
    """
    if study.kind == "confirmatory":
        msg = "ci_width_reached is refused in confirmatory studies (adaptive stopping)"
        raise ValueError(msg)
    data = rule.model_dump() if hasattr(rule, "model_dump") else dict(rule)
    threshold = float(data["threshold"])
    n_min = int(data.get("n_min") or 10)
    refs = list(data.get("features") or [])
    specs = det.resolve_refs(refs) if refs else primary_specs(study)
    features = [s.feature_key for s in specs]
    if not features:
        return []
    estimates = live_estimates(study, [s.pinned_ref for s in specs])
    reached: list[dict[str, Any]] = []
    with transaction.atomic():
        pattern_run = live_run(study, lock=True)
        if pattern_run is None:
            return []
        summary = dict(pattern_run.summary or {})
        looks = dict(summary.get("looks") or {})
        stopped = set(summary.get("stopped") or [])
        for (condition_id, target_id), by_feature in sorted(estimates.items()):
            key = f"{condition_id}:{target_id}"
            if key in stopped:
                continue
            looks[key] = int(looks.get(key, 0)) + 1
            ok = all(
                feature in by_feature
                and by_feature[feature]["n"] >= n_min
                and by_feature[feature]["half_width"] is not None
                and by_feature[feature]["half_width"] < threshold
                for feature in features
            )
            if ok:
                stopped.add(key)
                reached.append(
                    {
                        "condition": condition_id,
                        "target": target_id,
                        "looks": looks[key],
                        "features": {f: by_feature[f] for f in features},
                    },
                )
        summary["looks"] = looks
        summary["stopped"] = sorted(stopped)
        pattern_run.summary = summary
        pattern_run.save(update_fields=["summary"])
    return reached
