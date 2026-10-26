"""Study endpoints: CRUD (edits only in draft), preflight, lock/unlock, launch, cancel, retry,
progress, post-run report, protocol and exports.

The lifecycle rules live in ``research/study.py``; refusals come back as
``409 {detail, code, ...}`` (e.g. ``blocking`` issues on lock). Long work
never runs inside a request: launch verifies the protocol, marks the study
running and answers 202 while a background job submits the runs (progress
shows submitted vs planned; launching again resumes an interrupted launch);
``POST /report/`` answers 202 while a background job builds the report,
which ``GET /report/`` then serves. ``report.md`` and ``replication.zip``
are rendered from the stored report, never rebuilt in the request.
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Collection
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Count
from django.db.models import Q
from django.db.models.fields.json import KT
from django.db.models.functions import JSONObject
from django.http import HttpResponse
from django.http import JsonResponse
from ninja import Query
from ninja import Status
from ninja.errors import HttpError

from backend.analysis.api import common
from backend.analysis.api.schema import StudyCreateSchema
from backend.analysis.api.schema import StudyDetailSchema
from backend.analysis.api.schema import StudyPageSchema
from backend.analysis.api.schema import StudyUpdateSchema
from backend.analysis.api.serializers import study_payload
from backend.analysis.api.views._router import router
from backend.analysis.api.views.runs import remove_run_artifacts
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalysisStudy
from backend.analysis.research import export as export_mod
from backend.analysis.research import report as report_mod
from backend.analysis.research import study as study_mod
from backend.analysis.research.study import StudyError

logger = logging.getLogger(__name__)

EXPORTS = ("tables.zip", "replication.zip", "findings.csv", "runs.csv")
# A fixed timestamp keeps zip bytes reproducible for identical tables.
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def _studies_qs():
    return AnalysisStudy.objects.select_related("profile")


def _study_list_qs():
    # A list row never needs the frozen plan, the protocol or the report (each
    # can be hundreds of KB); the report's lifecycle keys come from the DB.
    report_keys = ("status", "kind", "request_id", "requested_at", "error")
    return (
        _studies_qs()
        .defer("plan", "protocol", "report")
        .annotate(report_meta=JSONObject(**{key: KT(f"report__{key}") for key in report_keys}))
    )


def _run_counts(study_ids: list) -> dict[Any, dict[str, int]]:
    out: dict[Any, dict[str, int]] = {}
    rows = (
        AnalysisRun.objects.filter(study_id__in=study_ids)
        .exclude(purpose=AnalysisRun.Purpose.CANARY)
        .values("study_id", "status")
        .annotate(n=Count("id"))
    )
    for row in rows:
        out.setdefault(row["study_id"], {})[row["status"]] = row["n"]
    return out


def _detail(study: AnalysisStudy) -> dict[str, Any]:
    study = _studies_qs().get(pk=study.pk)
    return study_payload(study, run_counts=_run_counts([study.pk]).get(study.pk, {}), detail=True)


def _validated_fields(
    user,
    data: dict[str, Any],
    study: AnalysisStudy | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Normalize the editable fields present in *data*; returns (values, field errors).

    Research questions and the design are also checked together (a
    confirmatory question's outcomes must be analysed by the design), with
    *study*'s stored value standing in for the one not being edited.
    """
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    validators = {
        "research_questions": study_mod.validate_research_questions,
        "target_spec": lambda v: study_mod.validate_target_spec(v) if v else {},
        "exclusion_rules": study_mod.validate_exclusion_rules,
        "analysis_design": study_mod.validate_design,
    }
    for key, validate in validators.items():
        if key not in data:
            continue
        try:
            values[key] = validate(data[key])
        except ConfigError as exc:
            errors.update(exc.field_errors)
    for key in ("name", "description"):
        if key in data and data[key] is not None:
            values[key] = data[key]
    if "profile_id" in data:
        if data["profile_id"] is None:
            values["profile"] = None
        elif (profile := common.visible_profiles(user).filter(id=data["profile_id"]).first()) is None:
            errors["profile_id"] = "profile not found"
        else:
            values["profile"] = profile
    if not errors and ("research_questions" in values or "analysis_design" in values):
        errors.update(_question_outcome_errors(values, study))
    return values, errors


def _question_outcome_errors(values: dict[str, Any], study: AnalysisStudy | None) -> dict[str, str]:
    questions = values.get("research_questions", study.research_questions if study is not None else [])
    design = values.get("analysis_design")
    if design is None:
        try:
            design = study_mod.validate_design(study.analysis_design if study is not None else {})
        except ConfigError:
            return {}  # an invalid stored design is reported (blocking) by the preflight
    return study_mod.question_outcome_errors(questions, design)


@router.get("/studies/", response=StudyPageSchema)
def list_studies(
    request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: str = Query(""),
    q: str = Query("", max_length=200),
):
    """Your studies (staff: all), newest first."""
    common.maybe_reconcile()
    statuses = common.csv_param(status)
    if bad := [s for s in statuses if s not in AnalysisStudy.Status.values]:
        return common.query_error("status", f"unknown value(s): {', '.join(bad)}")
    qs = common.visible_studies(request.auth, _study_list_qs())
    if statuses:
        qs = qs.filter(status__in=statuses)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
    items, meta = common.paginate(qs.order_by("-created_at", "-id"), page, per_page)
    counts = _run_counts([s.pk for s in items])
    active = {str(status) for status in AnalysisRun.ACTIVE_STATUSES}
    for study in items:
        # Only a running study without active runs can have completed: refresh (a query) just those.
        if study.status == AnalysisStudy.Status.RUNNING and not active & set(counts.get(study.pk, {})):
            study_mod.refresh(study)
    return common.page_payload(
        items,
        meta,
        lambda s: study_payload(
            s,
            run_counts=counts.get(s.pk, {}),
            report={k: v for k, v in (s.report_meta or {}).items() if v is not None},
        ),
    )


@router.post("/studies/", response={201: StudyDetailSchema})
def create_study(request, payload: StudyCreateSchema):
    """A draft study; the default research profile is pinned when none is given."""
    data = payload.model_dump()
    values, errors = _validated_fields(request.auth, data)
    if errors:
        return common.validation_error(errors)
    if data.get("profile_id") is None:
        values["profile"] = common.default_profile()
    study = AnalysisStudy.objects.create(created_by=request.auth, **values)
    return Status(201, _detail(study))


@router.get("/studies/{uuid:study_id}/", response=StudyDetailSchema)
def get_study(request, study_id: UUID):
    study = study_mod.refresh(common.get_study(request.auth, study_id, qs=_studies_qs()))
    return _detail(study)


@router.patch("/studies/{uuid:study_id}/", response=StudyDetailSchema)
def update_study(request, study_id: UUID, payload: StudyUpdateSchema):
    """Edit a draft; any change discards the stored preflight (it described the old configuration)."""
    study = common.get_study(request.auth, study_id, write=True)
    try:
        study_mod.require(study, "edit")
    except StudyError as exc:
        return common.study_error(exc)
    values, errors = _validated_fields(request.auth, payload.model_dump(exclude_unset=True), study)
    if errors:
        return common.validation_error(errors)
    if values:
        with transaction.atomic():
            study = AnalysisStudy.objects.select_for_update().get(pk=study.pk)
            if study.status != AnalysisStudy.Status.DRAFT:
                return common.conflict("The study is no longer a draft.")
            for key, value in values.items():
                setattr(study, key, value)
            study.preflight = {}
            study.save()
    return _detail(study)


@router.delete("/studies/{uuid:study_id}/", response=dict)
def delete_study(request, study_id: UUID):
    """Delete a study that is not running, with its runs, findings and artifacts."""
    study = common.get_study(request.auth, study_id, write=True)
    try:
        study_mod.require(study, "delete")
    except StudyError as exc:
        return common.study_error(exc)
    if study.runs.filter(status__in=AnalysisRun.ACTIVE_STATUSES).exists():
        return common.conflict("The study still has active runs; cancel it first.")
    run_ids = [str(pk) for pk in study.runs.values_list("id", flat=True)]
    study_pk = str(study.pk)
    study.delete()
    transaction.on_commit(lambda: remove_run_artifacts(run_ids))
    return {"deleted": True, "id": study_pk, "runs": len(run_ids)}


@router.post("/studies/{uuid:study_id}/preflight/", response=dict)
def study_preflight(request, study_id: UUID):
    """Pre-run checks for the draft (stored on the study): targets, eligibility, applicability, readiness,
    reviewer, estimates, comparability, design."""
    study = common.get_study(request.auth, study_id, write=True)
    try:
        return study_mod.run_preflight(study)
    except StudyError as exc:
        return common.study_error(exc)


@router.post("/studies/{uuid:study_id}/lock/", response=StudyDetailSchema)
def lock_study(request, study_id: UUID):
    """Freeze the plan and the protocol (sha256); refused (409 + ``blocking``) on blocking issues."""
    study = common.get_study(request.auth, study_id, write=True)
    try:
        study = study_mod.lock(study, user=request.auth)
    except StudyError as exc:
        return common.study_error(exc)
    return _detail(study)


@router.post("/studies/{uuid:study_id}/unlock/", response=StudyDetailSchema)
def unlock_study(request, study_id: UUID):
    """Back to draft; only before launch."""
    study = common.get_study(request.auth, study_id, write=True)
    try:
        study = study_mod.unlock(study, user=request.auth)
    except StudyError as exc:
        return common.study_error(exc)
    return _detail(study)


@router.post("/studies/{uuid:study_id}/launch/", response={202: StudyDetailSchema})
def launch_study(request, study_id: UUID):
    """Verify the protocol and start submitting one run per eligible target in the background (202).

    ``preflight.launch`` tracks the submission (status, planned, submitted,
    skipped with reasons). Calling launch again on a running study whose
    launch is ``submitting``/``interrupted`` resumes it (targets with a run are skipped).
    """
    study = common.get_study(request.auth, study_id, write=True)
    try:
        study = study_mod.launch(study, user=request.auth)
    except StudyError as exc:
        return common.study_error(exc)
    return Status(202, _detail(study))


@router.post("/studies/{uuid:study_id}/cancel/", response=StudyDetailSchema)
def cancel_study(request, study_id: UUID):
    study = common.get_study(request.auth, study_id, write=True)
    try:
        study = study_mod.cancel(study)
    except StudyError as exc:
        return common.study_error(exc)
    return _detail(study)


@router.post("/studies/{uuid:study_id}/retry-failed/", response=dict)
def retry_failed_study(request, study_id: UUID):
    """Requeue the study's failed, partial and cancelled runs."""
    study = common.get_study(request.auth, study_id, write=True)
    try:
        outcome = study_mod.retry_failed(study)
    except StudyError as exc:
        return common.study_error(exc)
    return {**outcome, "study": _detail(study)}


@router.get("/studies/{uuid:study_id}/progress/", response=dict)
def study_progress(request, study_id: UUID):
    """Targets x tools status matrix, counts, cost so far, stale runs, queue and capacity hints."""
    common.maybe_reconcile()
    study = common.get_study(request.auth, study_id, qs=_studies_qs())
    payload = study_mod.progress(study)
    payload["capacity"] = common.capacity() if payload["counts"]["active"] else None
    return payload


# --------------------------------------------------------------------------
# Report / protocol / exports
# --------------------------------------------------------------------------


def _integrity_note(study: AnalysisStudy, *, report: dict[str, Any] | None = None) -> str:
    """One Markdown line with the integrity checks at download time (the JSON endpoints' ``verified``/``stale``).

    A downloaded document carries them itself, so an altered or out-of-date
    protocol/report is visible in the thesis appendix, not only in the UI.
    """
    parts: list[str] = []
    if report is not None:
        if report_mod.report_hash(report) == study.report_hash:
            parts.append("report sha256 verified")
        else:
            parts.append("report sha256 DOES NOT MATCH the stored report")
    if study.protocol:
        if study_mod.verify_protocol(study):
            parts.append("protocol sha256 verified")
        else:
            parts.append("protocol sha256 DOES NOT MATCH the stored protocol")
    else:
        parts.append("no locked protocol")
    if report is not None:
        built_from = (report.get("protocol_integrity") or {}).get("match")
        if built_from is False:
            parts.append("the report was built from a protocol that failed its hash")
        stale = study_mod.report_is_stale(study)
        if stale:
            parts.append("study data CHANGED after the report was generated (generate it again)")
        elif stale is False:
            parts.append("study data unchanged since the report")
    return f"> Integrity at download: {'; '.join(parts)}."


def _with_note(text: str, note: str) -> str:
    """*note* right below the document's title line."""
    head, newline, rest = text.partition("\n")
    if not newline:
        return f"{text}\n\n{note}\n"
    rest = rest if rest.startswith("\n") else f"\n{rest}"
    return f"{head}\n\n{note}\n{rest}"


def _markdown(text: str, filename: str) -> HttpResponse:
    response = HttpResponse(text, content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _slug(study: AnalysisStudy) -> str:
    return f"study-{str(study.pk)[:8]}"


NO_READY_REPORT = "No report is ready for this study; generate one with POST /studies/{id}/report/."


def _report_payload(study: AnalysisStudy, state: dict[str, Any], *, include_report: bool) -> dict[str, Any]:
    """The report state envelope; a ready report adds its integrity checks (and the document)."""
    ready = state.get("status") == "ready"
    return {
        **state,
        "protocol_hash": study.protocol_hash,
        "protocol_verified": study_mod.verify_protocol(study) if study.protocol else None,
        "verified": report_mod.report_hash(study.report) == study.report_hash if ready else None,
        "stale": study_mod.report_is_stale(study) if ready else None,
        "report": study.report if ready and include_report else None,
    }


def _no_ready_report(study: AnalysisStudy) -> JsonResponse:
    state = study_mod.report_state(study)
    status = state["status"] if state else "none"
    return common.conflict(NO_READY_REPORT, report_status=status, error=(state or {}).get("error"))


@router.post("/studies/{uuid:study_id}/report/", response={202: dict})
def generate_report(request, study_id: UUID):
    """Start building the post-run report in the background (202); poll ``GET /report/``.

    A request while one is in flight returns that request's state. The
    report is built from stored data only (schema-versioned, sha256, linked
    to the protocol hash) and replaces any earlier report.
    """
    study = common.get_study(request.auth, study_id, write=True)
    try:
        state = study_mod.request_report(study, user=request.auth)
    except StudyError as exc:
        return common.study_error(exc)
    study.refresh_from_db()
    return Status(202, _report_payload(study, state, include_report=False))


@router.get("/studies/{uuid:study_id}/report/", response=dict)
def get_report(request, study_id: UUID):
    """The report state (``generating`` | ``failed`` | ``ready`` with the report); 404 when never requested.

    ``verified``: the stored report matches its sha256; ``stale``: a study
    run changed after the report was generated (generate it again).
    """
    study = common.get_study(request.auth, study_id)
    state = study_mod.report_state(study)
    if state is None:
        raise HttpError(404, "No report has been requested for this study.")
    return _report_payload(study, state, include_report=True)


@router.get("/studies/{uuid:study_id}/report.md")
def get_report_markdown(request, study_id: UUID):
    """The stored report as thesis-ready Markdown (409 until a report is ready)."""
    study = common.get_study(request.auth, study_id)
    report = study_mod.ready_report(study)
    if report is None:
        return _no_ready_report(study)
    text = report_mod.to_markdown(report, study.protocol or None)
    return _markdown(_with_note(text, _integrity_note(study, report=report)), f"{_slug(study)}-report.md")


@router.get("/studies/{uuid:study_id}/protocol/", response=dict)
def get_protocol(request, study_id: UUID):
    """The frozen pre-run protocol and its sha256 (404 until the study is locked)."""
    study = common.get_study(request.auth, study_id)
    if not study.protocol:
        raise HttpError(404, "The protocol is frozen when the study is locked.")
    return {
        "protocol_hash": study.protocol_hash,
        "locked_at": study.locked_at.isoformat() if study.locked_at else None,
        "plan_hash": study.plan_hash,
        "verified": study_mod.verify_protocol(study),
        "protocol": study.protocol,
    }


@router.get("/studies/{uuid:study_id}/protocol.md")
def get_protocol_markdown(request, study_id: UUID):
    study = common.get_study(request.auth, study_id)
    if not study.protocol:
        raise HttpError(404, "The protocol is frozen when the study is locked.")
    text = _with_note(report_mod.protocol_to_markdown(study.protocol), _integrity_note(study))
    return _markdown(text, f"{_slug(study)}-protocol.md")


def tables_zip(
    tables: dict[str, list[dict[str, Any]]],
    *,
    escape_formulas: bool | Collection[str] = False,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(tables):
            info = zipfile.ZipInfo(f"{name}.csv", date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, export_mod.to_csv(tables[name], escape_formulas=escape_formulas))
    return buffer.getvalue()


@router.get("/studies/{uuid:study_id}/export/{name}")
def export_study(
    request,
    study_id: UUID,
    name: str,
    escape: bool = Query(
        default=False,
        description="Spreadsheet-safe CSVs (findings.csv, runs.csv, tables.zip): untrusted free-text cells "
        "(snippets, titles, descriptions, paths, ...) starting with = + - @ or a tab/CR get a leading "
        "apostrophe. Off by default: research data stays verbatim.",
    ),
):
    """``tables.zip`` (tidy CSVs), ``replication.zip`` (tables + protocol + report + plan + SHA256SUMS),
    ``findings.csv`` or ``runs.csv``.

    Cells hold untrusted text (analysed code snippets, AI reviewer prose)
    written verbatim, so pandas/R read the exact values; opening a CSV in a
    spreadsheet evaluates cells such as ``@app.route(...)`` or ``=HYPERLINK(...)``
    as formulas. ``?escape=true`` neutralises them (as the run CSV export
    does); the replication package always stays verbatim (its README says
    to import cells as text). The response's ``X-Formula-Escaping`` header
    says which variant was served.
    """
    if name not in EXPORTS:
        raise HttpError(404, f"Unknown export; one of: {', '.join(EXPORTS)}")
    study = common.get_study(request.auth, study_id)
    if study.status in (AnalysisStudy.Status.DRAFT, AnalysisStudy.Status.LOCKED):
        return common.conflict("Exports are available once the study has been launched.")
    stem = _slug(study)
    if name == "replication.zip":
        # The package carries the stored report (what the researcher read), never a rebuild.
        report = study_mod.ready_report(study)
        if report is None:
            return _no_ready_report(study)
        response = HttpResponse(export_mod.replication_zip(study, report=report), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{stem}-replication.zip"'
        return response
    tables = export_mod.tables(study)
    # Only untrusted free text is neutralised; identifiers and numbers stay exact.
    escaping = export_mod.UNTRUSTED_TEXT_COLUMNS if escape else False
    if name == "tables.zip":
        response = HttpResponse(tables_zip(tables, escape_formulas=escaping), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{stem}-tables.zip"'
    else:
        table = name.removesuffix(".csv")
        response = HttpResponse(
            export_mod.to_csv(tables.get(table, []), escape_formulas=escaping),
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="{stem}-{name}"'
    response["X-Formula-Escaping"] = "on" if escape else "off (verbatim; import cells as text in spreadsheets)"
    return response
