"""Patterns API (spec section 9.7).

Mounted by ``backend.generation.api`` without a prefix, so routes declare their full path under
``/api/generation/`` (as in the spec's tables). Static paths are registered before parameterised ones.

Owner-scoped like the study routes: a study (and its pattern runs) is visible to its creator and to staff;
a foreign or malformed id is a 404. Errors are ``{"detail", "code"}`` with status 400 (bad request) or 409
(state conflict). Operation ids are ``patterns_*`` (published in SAMPLE_GEN_V3_CHANGES.md).
"""

from __future__ import annotations

import uuid
from typing import Any
from typing import Literal

from django.core.exceptions import ValidationError
from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Field
from ninja import Router
from ninja import Schema
from ninja.responses import Status

from backend.generation.models import ClusterLabel
from backend.generation.models import DetectorAudit
from backend.generation.models import GenerationRun
from backend.generation.models import PatternRun
from backend.generation.models import SlotTarget
from backend.generation.models import Study

router = Router(tags=["generation: patterns"])

HEAVY_SUMMARY_KEYS = ("prevalence", "comparisons", "pooled", "cluster_stats", "unparsed", "records")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PatternRunIn(Schema):
    study: str
    detectors: list[str] | Literal["default"] = "default"
    cluster_params: dict[str, Any] | None = None
    scope: dict[str, Any] | None = None


class SpanIn(Schema):
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)


class FromSelectionIn(Schema):
    run_id: str
    path: str
    span: SpanIn
    engine: Literal["regex", "ts_query"]
    feature_key: str
    polarity: Literal["insecure", "secure", "neutral"] = "insecure"


class ClusterLabelIn(Schema):
    slot_target: str
    normalized_sha: str
    detector: str
    value: Literal["present", "absent", "unsure"]
    note: str = ""


class AuditIn(Schema):
    feature_key: str
    n_hits: int = Field(20, ge=1, le=500)
    n_non_hits: int = Field(20, ge=1, le=500)
    scope: Literal["span", "file"] = "span"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uuid_or_404(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise Http404 from exc


def _bad(detail: str, code: str, status: int = 400) -> Status:
    return Status(status, {"detail": detail, "code": code})


def _studies(user: Any) -> Any:
    qs = Study.objects.all()
    return qs if getattr(user, "is_staff", False) else qs.filter(created_by=user)


def _owned_study(user: Any, study_id: str) -> Study:
    return get_object_or_404(_studies(user), id=_uuid_or_404(study_id))


def _owned_pattern_run(user: Any, run_id: str) -> PatternRun:
    return get_object_or_404(
        PatternRun.objects.select_related("study").filter(study__in=_studies(user)),
        id=_uuid_or_404(run_id),
    )


def _visible_run(user: Any, run_id: str) -> GenerationRun:
    qs = GenerationRun.objects.all()
    if not getattr(user, "is_staff", False):
        qs = qs.filter(created_by=user) | qs.filter(study__created_by=user)
    return get_object_or_404(qs.distinct(), id=_uuid_or_404(run_id))


def _visible_target(user: Any, target_id: str) -> SlotTarget:
    qs = SlotTarget.objects.select_related("slot_set")
    if not getattr(user, "is_staff", False):
        qs = (
            qs.filter(slot_set__created_by=user)
            | qs.filter(created_by=user)
            | qs.filter(study_units__study__created_by=user)
        )
    return get_object_or_404(qs.distinct(), id=_uuid_or_404(target_id))


def pattern_run_out(run: PatternRun, *, full: bool = False) -> dict[str, Any]:
    summary = dict(run.summary or {})
    if not full:
        summary = {k: v for k, v in summary.items() if k not in HEAVY_SUMMARY_KEYS}
    post_run = run.study.post_runs.filter(pattern_run=run).order_by("-created_at").first()
    return {
        "id": str(run.pk),
        "study": str(run.study_id),
        "detectors": list(run.detectors or []),
        "detector_set_hash": run.detector_set_hash,
        "normalizer_version": run.normalizer_version,
        "cluster_params": run.cluster_params,
        "scope": run.scope,
        "is_live": run.is_live,
        "label": run.label,
        "status": run.status,
        "summary": summary,
        "post_run": post_run.pk if post_run else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def audit_out(audit: DetectorAudit) -> dict[str, Any]:
    return {
        "id": audit.pk,
        "pattern_run": str(audit.pattern_run_id),
        "feature_key": audit.feature_key,
        "status": audit.status,
        "sample": audit.sample,
        "eval_task": str(audit.eval_task_id) if audit.eval_task_id else None,
        "precision": audit.precision,
        "npv": audit.npv,
        "summary": audit.summary,
        "created_at": audit.created_at.isoformat() if audit.created_at else None,
    }


def label_out(label: ClusterLabel) -> dict[str, Any]:
    return {
        "id": label.pk,
        "slot_target": str(label.slot_target_id),
        "normalized_sha": label.normalized_sha,
        "detector": label.detector.pinned_ref,
        "feature_key": label.detector.feature_key,
        "value": label.value,
        "note": label.note,
        "labeler": label.labeler.get_username(),
        "created_at": label.created_at.isoformat() if label.created_at else None,
    }


def _completed_or_409(run: PatternRun) -> Status | None:
    if run.is_live:
        return None
    if run.status != "completed":
        return _bad(f"pattern run is {run.status}; results exist once it completes", "pattern_run_not_completed", 409)
    return None


# ---------------------------------------------------------------------------
# Static routes first
# ---------------------------------------------------------------------------


@router.post(
    "/patterns/detectors/from-selection/",
    response={200: dict, 400: dict},
    operation_id="patterns_detector_from_selection",
)
def detector_from_selection(request, payload: FromSelectionIn):
    """A ``DetectorContent`` draft from a code selection (prefill for the DetectorEditor; nothing is saved)."""
    from pydantic import ValidationError as PydanticValidationError

    from backend.generation.patterns import drafts

    run = _visible_run(request.auth, payload.run_id)
    try:
        draft = drafts.draft_for_run(
            run,
            path=payload.path,
            start_line=payload.span.start_line,
            end_line=payload.span.end_line,
            engine=payload.engine,
            feature_key=payload.feature_key,
            polarity=payload.polarity,
        )
    except (drafts.DraftError, PydanticValidationError) as exc:
        return _bad(str(exc)[:500], "invalid_selection")
    return Status(200, {"draft": draft})


@router.post("/patterns/clusters/label/", response={200: dict, 400: dict}, operation_id="patterns_cluster_label")
def cluster_label(request, payload: ClusterLabelIn):
    """Code an exact cluster key for a manual detector (one label per labeler; never alters past runs)."""
    from backend.generation.patterns import detectors as det
    from backend.generation.patterns import manual

    target = _visible_target(request.auth, payload.slot_target)
    try:
        spec = det.resolve_refs([payload.detector])[0]
    except det.DetectorRefError as exc:
        return _bad(str(exc), "unknown_detector")
    from backend.generation.models import PatternDetector

    detector = PatternDetector.objects.get(pk=spec.pk)
    try:
        label = manual.label_cluster(
            user=request.auth,
            slot_target=target,
            normalized_sha=payload.normalized_sha,
            detector=detector,
            value=payload.value,
            note=payload.note,
        )
    except ValidationError as exc:
        return _bad("; ".join(exc.messages), "invalid_label")
    return Status(200, label_out(label))


# ---------------------------------------------------------------------------
# Pattern runs
# ---------------------------------------------------------------------------


@router.post("/patterns/runs/", response={200: dict, 400: dict}, operation_id="patterns_run_create")
def create_run(request, payload: PatternRunIn):
    """Pin a detector set and queue a PatternRun as a patterns post-run (label planned | exploratory)."""
    from pydantic import ValidationError as PydanticValidationError

    from backend.generation.patterns.service import PatternRunError
    from backend.generation.patterns.service import create_pattern_run

    study = _owned_study(request.auth, payload.study)
    try:
        run = create_pattern_run(
            study,
            detectors=payload.detectors,
            cluster_params=payload.cluster_params,
            scope=payload.scope,
            user=request.auth,
        )
    except (PatternRunError, PydanticValidationError, ValidationError) as exc:
        return _bad(str(exc)[:1000], "invalid_pattern_run")
    return Status(200, pattern_run_out(run))


@router.get("/patterns/runs/", response={200: list[dict]}, operation_id="patterns_run_list")
def list_runs(request, study: str, live: bool | None = None):
    """The study's pattern runs, newest first (``live`` filters the provisional run)."""
    owned = _owned_study(request.auth, study)
    qs = PatternRun.objects.filter(study=owned).select_related("study").order_by("-created_at")
    if live is not None:
        qs = qs.filter(is_live=live)
    return Status(200, [pattern_run_out(run) for run in qs])


@router.get("/patterns/runs/{run_id}/", response={200: dict}, operation_id="patterns_run_get")
def get_run(request, run_id: str):
    return Status(200, pattern_run_out(_owned_pattern_run(request.auth, run_id)))


def _live_prevalence(run: PatternRun) -> list[dict[str, Any]]:
    from backend.generation.patterns import live
    from backend.generation.patterns import prevalence as prev

    rows = []
    for (condition_id, target_id), by_feature in sorted(live.live_estimates(run.study).items()):
        for feature, counts in sorted(by_feature.items()):
            row = prev.estimate(counts["k"], counts["n"], n_itt=counts["n"], na=0, errors=0, missing=0)
            row.update(
                {
                    "condition": condition_id,
                    "target": target_id,
                    "feature": feature,
                    "scope": live.PRIMARY_SCOPE,
                    "provisional": True,
                    "n_itt": None,
                },
            )
            rows.append(row)
    return rows


@router.get(
    "/patterns/runs/{run_id}/prevalence/",
    response={200: list[dict], 409: dict},
    operation_id="patterns_run_prevalence",
)
def prevalence(request, run_id: str, target: str | None = None, feature: str | None = None, scope: str | None = None):
    """Prevalence rows with ITT and measured n, Wilson CIs, bounds, ``adaptive_n`` and the audit (if any)."""
    from backend.generation.patterns.audit import audits_by_feature

    run = _owned_pattern_run(request.auth, run_id)
    blocked = _completed_or_409(run)
    if blocked is not None:
        return blocked
    rows = _live_prevalence(run) if run.is_live else list((run.summary or {}).get("prevalence") or [])
    audits = audits_by_feature(run) if not run.is_live else {}
    out = []
    for stored in rows:
        if target is not None and str(stored.get("target")) != target:
            continue
        if feature is not None and stored.get("feature") != feature:
            continue
        if scope is not None and stored.get("scope") != scope:
            continue
        audit = audits.get(stored.get("feature"))
        row = dict(stored)
        if audit and audit["status"] == "complete":
            row["audit"] = {"precision": audit["precision"], "npv": audit["npv"]}
            row["audit_status"] = "complete"
        else:
            row["audit"] = None
            row["audit_status"] = audit["status"] if audit else "not_audited"
        out.append(row)
    return Status(200, out)


@router.get(
    "/patterns/runs/{run_id}/comparisons/",
    response={200: list[dict], 409: dict},
    operation_id="patterns_run_comparisons",
)
def comparisons(request, run_id: str, feature: str | None = None):
    run = _owned_pattern_run(request.auth, run_id)
    blocked = _completed_or_409(run)
    if blocked is not None:
        return blocked
    rows = list((run.summary or {}).get("comparisons") or [])
    return Status(
        200,
        [r for r in rows if feature is None or r.get("feature") == feature or r.get("column") == feature],
    )


@router.get(
    "/patterns/runs/{run_id}/pooled/",
    response={200: dict, 409: dict},
    operation_id="patterns_run_pooled",
)
def pooled(request, run_id: str, feature: str | None = None):
    """Per-target estimates, the MH pooled RD and the permutation p (``few_clusters`` with < 20 targets)."""
    run = _owned_pattern_run(request.auth, run_id)
    blocked = _completed_or_409(run)
    if blocked is not None:
        return blocked
    rows = [
        r
        for r in (run.summary or {}).get("pooled") or []
        if feature is None or r.get("feature") == feature or r.get("column") == feature
    ]
    notes = sorted({note for r in rows for note in r.get("notes") or []})
    method = "mh_stratified" if any(r.get("method") == "mh_stratified" for r in rows) else "per_target"
    return Status(200, {"method": method, "rows": rows, "notes": notes})


@router.get(
    "/patterns/runs/{run_id}/clusters/",
    response={200: dict, 409: dict},
    operation_id="patterns_run_clusters",
)
def clusters(request, run_id: str, target: str | None = None, condition: int | None = None):
    """Clusters with share, representative, ``includes_original``, manual labels and a member sample."""
    from backend.generation.patterns import manual

    run = _owned_pattern_run(request.auth, run_id)
    blocked = _completed_or_409(run)
    if blocked is not None:
        return blocked
    qs = run.clusters.prefetch_related("members").order_by("slot_target_id", "-size", "pk")
    if target is not None:
        qs = qs.filter(slot_target_id=_uuid_or_404(target))
    rows = list(qs)
    condition_runs: set[str] | None = None
    if condition is not None:
        condition_runs = {
            str(pk)
            for pk in GenerationRun.objects.filter(study=run.study, condition_id=condition).values_list(
                "pk",
                flat=True,
            )
        }
    labels = manual.labels_by_key(sorted({str(c.slot_target_id) for c in rows}))
    totals: dict[str, int] = {}
    members: dict[int, list[str]] = {}
    for c in rows:
        ids = [str(m.run_id) for m in c.members.all()]
        if condition_runs is not None:
            ids = [i for i in ids if i in condition_runs]
        members[c.pk] = ids
        totals[str(c.slot_target_id)] = totals.get(str(c.slot_target_id), 0) + len(ids)
    out = []
    for c in rows:
        ids = members[c.pk]
        if not ids:
            continue
        n = totals[str(c.slot_target_id)]
        out.append(
            {
                "cluster_id": c.pk,
                "target": str(c.slot_target_id),
                "method": c.method,
                "key": c.key,
                "size": len(ids),
                "share": round(len(ids) / n, 6) if n else None,
                "representative": str(c.representative_id),
                "includes_original": c.includes_original,
                "labels": labels.get((str(c.slot_target_id), c.key), []),
                "members_sample": ids[:10],
            },
        )
    stats_rows = [
        s
        for s in (run.summary or {}).get("cluster_stats") or []
        if (target is None or s.get("target") == target) and (condition is None or s.get("condition") == condition)
    ]
    stats: Any = stats_rows[0] if target is not None and condition is not None and len(stats_rows) == 1 else stats_rows
    return Status(200, {"clusters": out, "stats": stats})


@router.post(
    "/patterns/runs/{run_id}/audits/",
    response={200: dict, 400: dict, 409: dict},
    operation_id="patterns_audit_create",
)
def create_audit(request, run_id: str, payload: AuditIn):
    """Draw a stratified, seeded audit sample and create its (draft) human-eval task."""
    from backend.generation.patterns.audit import AuditError
    from backend.generation.patterns.audit import create_audit as create

    run = _owned_pattern_run(request.auth, run_id)
    if run.is_live or run.status != "completed":
        return _bad("audits are drawn from a completed, non-live pattern run", "pattern_run_not_completed", 409)
    try:
        audit = create(
            run,
            payload.feature_key,
            n_hits=payload.n_hits,
            n_non_hits=payload.n_non_hits,
            scope=payload.scope,
            user=request.auth,
        )
    except (AuditError, ValueError, ValidationError) as exc:
        return _bad(str(exc)[:500], "invalid_audit")
    return Status(200, audit_out(audit))


@router.get("/patterns/runs/{run_id}/audits/", response={200: list[dict]}, operation_id="patterns_audit_list")
def list_audits(request, run_id: str):
    """The run's audits, refreshed from the human-eval codes."""
    from backend.generation.patterns.audit import refresh_audit

    run = _owned_pattern_run(request.auth, run_id)
    audits = [refresh_audit(a) for a in run.audits.select_related("eval_task").order_by("created_at")]
    return Status(200, [audit_out(a) for a in audits])


@router.get("/patterns/runs/{run_id}/export.zip", response={409: dict}, operation_id="patterns_run_export")
def export_zip(request, run_id: str):
    """The export zip (section 7.9 step 8)."""
    from backend.generation.patterns.export import build_export

    run = _owned_pattern_run(request.auth, run_id)
    blocked = _completed_or_409(run)
    if blocked is not None:
        return blocked
    response = HttpResponse(build_export(run), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="patterns-{str(run.pk)[:8]}.zip"'
    return response
