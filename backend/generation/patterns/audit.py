"""Detector precision/NPV audit samples through human evaluation (spec section 7.9 step 4).

``create_audit(pattern_run, feature_key)`` draws a **stratified, seeded** random sample of the feature's
verdicts in a completed (non-live) PatternRun: about ``n_hits`` hits (``present``) and ``n_non_hits``
non-hits (``absent``), allocated across (condition, target) strata in proportion to their size (largest
remainder), sampled within each stratum with ``random.Random(seed)``. The seed is
``int(H({pattern_run, feature, scope})[:12], 16)``, so the same request gives the same sample. A blinded
human-eval task is created through ``human_eval.services.detector_audit``.

``refresh_audit(audit)`` reads the codes back: precision = hits coded present / hits coded present-or-absent,
NPV = non-hits coded absent / non-hits coded present-or-absent, each with a Wilson 95% CI in ``summary``.
``unsure`` codes are excluded and counted. A feature without a complete audit is "detector not audited".
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction

from backend.generation import identity
from backend.generation.patterns import stats

if TYPE_CHECKING:
    from collections.abc import Sequence

    from backend.generation.models import DetectorAudit
    from backend.generation.models import PatternRun


class AuditError(ValueError):
    pass


def audit_seed(pattern_run: PatternRun, feature_key: str, scope: str) -> int:
    return int(identity.H({"pattern_run": str(pattern_run.pk), "feature": feature_key, "scope": scope})[:12], 16)


def allocate(sizes: dict[Any, int], total: int) -> dict[Any, int]:
    """Proportional allocation of ``total`` draws over strata (largest remainder; never above a stratum size)."""
    population = sum(sizes.values())
    if population <= 0 or total <= 0:
        return dict.fromkeys(sizes, 0)
    total = min(total, population)
    quotas = {key: total * size / population for key, size in sizes.items()}
    alloc = {key: min(sizes[key], int(q)) for key, q in quotas.items()}
    remaining = total - sum(alloc.values())
    order = sorted(sizes, key=lambda key: (-(quotas[key] - int(quotas[key])), str(key)))
    while remaining > 0:
        progressed = False
        for key in order:
            if remaining == 0:
                break
            if alloc[key] < sizes[key]:
                alloc[key] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return alloc


def stratified_sample(strata: dict[Any, Sequence[str]], n: int, rng: random.Random) -> list[str]:
    """``n`` ids drawn across ``strata`` (``{stratum: ids}``) by proportional allocation, sorted per stratum."""
    sizes = {key: len(ids) for key, ids in strata.items()}
    alloc = allocate(sizes, n)
    picked: list[str] = []
    for key in sorted(strata, key=str):
        ids = sorted(strata[key])
        picked.extend(rng.sample(ids, alloc[key]))
    return picked


def draw_sample(
    pattern_run: PatternRun,
    feature_key: str,
    *,
    scope: str,
    n_hits: int,
    n_non_hits: int,
) -> dict[str, Any]:
    """``{hits, non_hits, seed}`` for the feature (``contracts.slot.AuditSample``)."""
    from backend.generation.models import PatternHit

    seed = audit_seed(pattern_run, feature_key, scope)
    rng = random.Random(seed)  # noqa: S311 - a seeded study sample, not a secret
    rows = PatternHit.objects.filter(
        pattern_run=pattern_run,
        feature_key=feature_key,
        scope=scope,
        status__in=["present", "absent"],
    ).values_list("run_id", "status", "run__condition_id", "run__slot_target_id")
    present: dict[Any, list[str]] = defaultdict(list)
    absent: dict[Any, list[str]] = defaultdict(list)
    for run_id, status, condition_id, target_id in rows:
        (present if status == "present" else absent)[(condition_id, str(target_id))].append(str(run_id))
    return {
        "hits": stratified_sample(present, n_hits, rng),
        "non_hits": stratified_sample(absent, n_non_hits, rng),
        "seed": seed,
    }


def create_audit(
    pattern_run: PatternRun,
    feature_key: str,
    *,
    n_hits: int = 20,
    n_non_hits: int = 20,
    scope: str = "span",
    user: Any = None,
) -> DetectorAudit:
    """A stratified, seeded audit sample and its human-eval task."""
    from backend.generation.models import DetectorAudit
    from backend.generation.models import GenerationRun
    from backend.generation.models import PatternDetector
    from backend.human_eval.services import detector_audit

    if pattern_run.is_live or pattern_run.status != "completed":
        msg = "audits are drawn from a completed, non-live pattern run"
        raise AuditError(msg)
    if n_hits < 1 or n_non_hits < 1:
        msg = "n_hits and n_non_hits must be at least 1"
        raise AuditError(msg)
    sample = draw_sample(pattern_run, feature_key, scope=scope, n_hits=n_hits, n_non_hits=n_non_hits)
    if not sample["hits"] and not sample["non_hits"]:
        msg = f"no measured verdicts of {feature_key!r} at scope {scope!r} in this pattern run"
        raise AuditError(msg)
    detector = (
        PatternDetector.objects.filter(hits__pattern_run=pattern_run, feature_key=feature_key).distinct().first()
    )
    description = (detector.content or {}).get("description", feature_key) if detector else feature_key
    ids = sample["hits"] + sample["non_hits"]
    runs = list(GenerationRun.objects.filter(pk__in=ids))
    owner = user or pattern_run.created_by or pattern_run.study.created_by
    with transaction.atomic():
        task = detector_audit.create_audit_task(
            user=owner,
            study=pattern_run.study,
            feature_key=feature_key,
            description=str(description),
            runs=runs,
            seed=sample["seed"],
        )
        audit = DetectorAudit(
            pattern_run=pattern_run,
            feature_key=feature_key,
            sample=sample,
            eval_task=task,
            status="pending",
            summary={"scope": scope, "n_hits": len(sample["hits"]), "n_non_hits": len(sample["non_hits"])},
            created_by=user,
        )
        audit.full_clean()
        audit.save()
    return audit


def _rate(k: int, n: int) -> dict[str, Any]:
    ci = stats.wilson(k, n)
    return {
        "k": k,
        "n": n,
        "estimate": round(k / n, 6) if n else None,
        "ci_low": round(ci[0], 6) if ci else None,
        "ci_high": round(ci[1], 6) if ci else None,
    }


def score(sample: dict[str, Any], codes: dict[str, str]) -> dict[str, Any]:
    """Precision and NPV of the sample under ``codes`` (``{run id: present|absent|unsure}``)."""
    hits, non_hits = list(sample.get("hits") or []), list(sample.get("non_hits") or [])
    tp = sum(1 for r in hits if codes.get(r) == "present")
    fp = sum(1 for r in hits if codes.get(r) == "absent")
    tn = sum(1 for r in non_hits if codes.get(r) == "absent")
    fn = sum(1 for r in non_hits if codes.get(r) == "present")
    unsure = sum(1 for r in hits + non_hits if codes.get(r) == "unsure")
    uncoded = sum(1 for r in hits + non_hits if r not in codes)
    return {
        "precision": _rate(tp, tp + fp),
        "npv": _rate(tn, tn + fn),
        "coded": len(hits) + len(non_hits) - uncoded,
        "uncoded": uncoded,
        "unsure": unsure,
        "complete": uncoded == 0,
    }


def refresh_audit(audit: DetectorAudit) -> DetectorAudit:
    """Recompute precision/NPV from the human-eval codes and move the status along."""
    from backend.human_eval.services import detector_audit

    if audit.eval_task is None:
        return audit
    codes = detector_audit.collect_codes(audit.eval_task)
    result = score(audit.sample, codes)
    audit.summary = {**(audit.summary or {}), **result}
    audit.precision = result["precision"]["estimate"]
    audit.npv = result["npv"]["estimate"]
    if result["complete"]:
        audit.status = "complete"
    elif result["coded"]:
        audit.status = "in_progress"
    audit.save(update_fields=["summary", "precision", "npv", "status"])
    return audit


def audits_by_feature(pattern_run: PatternRun) -> dict[str, dict[str, Any]]:
    """The latest complete audit per feature key: ``{feature: {precision, npv, status, audit_id}}``."""
    out: dict[str, dict[str, Any]] = {}
    for audit in pattern_run.audits.order_by("created_at"):
        entry = {
            "audit_id": audit.pk,
            "status": audit.status,
            "precision": (audit.summary or {}).get("precision"),
            "npv": (audit.summary or {}).get("npv"),
        }
        if audit.status == "complete" or audit.feature_key not in out:
            out[audit.feature_key] = entry
    return out
