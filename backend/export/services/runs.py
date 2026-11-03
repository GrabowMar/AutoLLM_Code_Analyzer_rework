"""Generation run exporters: run tables (CSV/JSON) and the per-run evidence bundle (ZIP).

Every row and bundle reads the run through ``generation.runs.view`` (``RunView``): identity, labels,
files and the frozen manifest. The bundle carries what is needed to re-check a sample outside the
platform: ``manifest.json`` (the frozen RunManifest), ``files.csv`` (every path of the app tree with
its origin, blob sha and source LOC), ``resolved_lock.json`` (the dependency lock the build used),
``task.json`` and the app's code under ``code/``.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import TYPE_CHECKING
from typing import Any

from backend.export.services.csv_safe import safe_row

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.db.models import QuerySet

    from backend.generation.models import GenerationRun
    from backend.generation.runs.view import RunView


RUN_HEADERS = [
    "id",
    "kind",
    "model",
    "model_id",
    "task",
    "unit",
    "stack",
    "starter",
    "harness",
    "study_id",
    "condition_id",
    "condition_label",
    "cell_id",
    "repeat_index",
    "stage",
    "status",
    "outcome",
    "usable",
    "truncated",
    "started_at",
    "completed_at",
    "tokens_used",
    "cost_usd",
    "duration",
    "config_fingerprint",
    "run_fingerprint",
]
# files.csv: the four contract columns first (path, origin, sha, loc), then the stored facts.
FILE_HEADERS = ["path", "origin", "sha", "loc", "size", "mode", "language", "is_test", "excluded"]
BUNDLE_FORMAT_VERSION = 1


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def run_record(run: GenerationRun, view: RunView) -> dict[str, Any]:
    """One exported run: identity, treatment labels, outcome and cost (never code or prompts)."""
    from backend.statistics.services.runs import condition_label

    model = run.model
    condition = run.condition
    metrics = run.metrics if isinstance(run.metrics, dict) else {}
    validity = run.validity if isinstance(run.validity, dict) else {}
    return {
        "id": view.id,
        "kind": view.kind,
        "model": model.model_name if model is not None else None,
        "model_id": view.model_slug,
        "task": view.task.ref if view.task is not None else None,
        "unit": view.unit_key,
        "stack": view.stack.ref if view.stack is not None else None,
        "starter": run.starter.ref if run.starter_id else None,
        "harness": "/".join(view.harness) if view.harness else None,
        "study_id": view.study_id,
        "condition_id": view.condition_id,
        "condition_label": condition_label(condition) if condition is not None else None,
        "cell_id": view.cell_id,
        "repeat_index": view.repeat_index,
        "stage": view.stage,
        "status": view.status,
        "outcome": view.outcome or None,
        "usable": view.usable,
        "truncated": bool(validity.get("truncated")),
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        # Unknown (no usage recorded) is None, never 0.
        "tokens_used": view.total_tokens,
        "cost_usd": metrics.get("cost_usd"),
        "duration": run.duration_seconds,
        "config_fingerprint": run.config_fingerprint or None,
        "run_fingerprint": run.run_fingerprint or None,
    }


def _records(queryset: QuerySet[Any] | Iterable[Any]) -> list[dict[str, Any]]:
    from backend.generation.models import GenerationRun
    from backend.generation.runs.view import run_views

    runs = list(queryset)
    by_id = GenerationRun.objects.select_related("model", "condition__model", "starter").in_bulk([r.pk for r in runs])
    runs = [by_id[r.pk] for r in runs if r.pk in by_id]
    return [run_record(run, view) for run, view in zip(runs, run_views(runs), strict=True)]


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def generation_runs_csv(queryset: QuerySet[Any], *, spreadsheet_safe: bool = True) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(RUN_HEADERS)
    for record in _records(queryset):
        writer.writerow(safe_row([_csv_value(record[h]) for h in RUN_HEADERS], enabled=spreadsheet_safe))
    return buf.getvalue()


def generation_runs_json(queryset: QuerySet[Any]) -> list[dict[str, Any]]:
    return _records(queryset)


# ── per-run bundle ───────────────────────────────────────────────────────────


def files_csv(view: RunView, *, spreadsheet_safe: bool = True) -> str:
    """``files.csv`` of a run's app tree: path, origin (``parent`` for a variant's overlaid rows), sha, loc."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(FILE_HEADERS)
    for entry in view.files("app"):
        writer.writerow(
            safe_row(
                [
                    entry.path,
                    entry.origin,
                    entry.blob,
                    entry.loc,
                    entry.size,
                    oct(entry.mode),
                    entry.language,
                    _csv_value(entry.is_test),
                    entry.excluded,
                ],
                enabled=spreadsheet_safe,
            ),
        )
    return buf.getvalue()


def resolved_lock(run: GenerationRun) -> dict[str, Any]:
    """The dependency lock the run's build used (``ResolvedLock``); a slot variant builds with its parent's.

    ``recorded`` is False when no lock was resolved yet (never evaluated): unknown, not "no dependencies".
    """
    from backend.generation import blobs

    source = run.parent_run if run.is_slot_variant and run.parent_run_id else run
    payload = None
    if source.resolved_lock_blob:
        payload = blobs.get_json(source.resolved_lock_blob)
    return {
        "run_id": str(run.pk),
        "from_parent_run": str(source.pk) if source is not run else None,
        "recorded": payload is not None,
        "lock_source": source.lock_source or None,
        "registry_snapshot_at": _iso(source.registry_snapshot_at),
        "blob": source.resolved_lock_blob or None,
        "lock": payload,
    }


def _run_json(run: GenerationRun, view: RunView) -> dict[str, Any]:
    evaluation = view.latest_evaluation()
    return {
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        **run_record(run, view),
        "protocol": view.protocol,
        "parent_run_id": view.parent_run_id,
        "fingerprints": view.fingerprints,
        "tree_hash": run.tree_hash or None,
        "loc_model": view.loc("model"),
        "loc_app": view.loc("app"),
        "labels": view.labels(),
        "latest_evaluation": None
        if evaluation is None
        else {
            "id": evaluation.pk,
            "eval_suite": evaluation.eval_suite.ref,
            "eval_level": evaluation.eval_level,
            "mode": evaluation.mode,
            "status": evaluation.status,
            "result": evaluation.result,
            "created_at": _iso(evaluation.created_at),
        },
    }


def run_bundle(run: GenerationRun) -> bytes:
    """The evidence ZIP of one run: manifest, files.csv, resolved lock, task, run facts and the app code."""
    from backend.generation.runs.view import run_view

    view = run_view(run)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(view.manifest() or None, indent=2, sort_keys=True, default=str))
        zf.writestr("files.csv", files_csv(view))
        zf.writestr("resolved_lock.json", json.dumps(resolved_lock(run), indent=2, sort_keys=True, default=str))
        zf.writestr("task.json", json.dumps(view.task_dict(), indent=2, sort_keys=True, default=str))
        zf.writestr("run.json", json.dumps(_run_json(run, view), indent=2, sort_keys=True, default=str))
        for path, text in sorted(view.code_files("app").items()):
            zf.writestr(f"code/{path}", text)
    return buf.getvalue()
