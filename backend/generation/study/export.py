"""The study export zip (spec section 9.4: ``GET /studies/{id}/export/?format=zip``).

Contents: ``plan.json`` (the active plan's ``ResolvedPlan``), ``manifests/<run>.json``, ``runs.csv`` (one row
per run, every attempt and beyond-plan run included and labelled; ``started_at``, served providers,
``same_author``, request classes and truncation), ``attrition.csv`` (``runs.queries.attrition``),
``files.csv`` (app scope through ``RunView.files``), ``evaluations.jsonl`` (every ``RunEvaluation``, retests
included), ``locks/<run>.json`` (the recorded ``resolved_lock``; ``recorded: false`` when none was resolved,
never "no dependencies") and, for slot studies, ``patterns/`` from ``export.services.patterns`` (the latest
completed pattern run's export; ``patterns/README.txt`` always states ``exported``, ``not_measured`` or ``error``).
Missing measurements are empty cells, never 0.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import tempfile
import zipfile
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

if TYPE_CHECKING:
    from backend.generation.models import Study

logger = logging.getLogger(__name__)

__all__ = ["RUN_COLUMNS", "export_zip"]

RUN_COLUMNS: Final = (
    "run_id",
    "study_id",
    "cell_id",
    "condition_id",
    "condition_label",
    "unit_key",
    "stage",
    "repeat_index",
    "cell_attempt",
    "attempt",
    "current",
    "beyond_plan",
    "kind",
    "status",
    "outcome",
    "outcome_reason",
    "usable",
    "truncated",
    "termination",
    "model",
    "seed",
    "config_fingerprint",
    "run_fingerprint",
    "started_at",
    "completed_at",
    "served_providers",
    "same_author",
    "requests_by_class",
    "cost_usd",
    "list_cost_usd",
    "eval_status",
    "agent_pass_rate",
)
_SPOOL_BYTES: Final = 32 * 1024 * 1024


def _csv(rows: list[dict[str, Any]], columns: tuple[str, ...] | list[str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: ("" if value is None else value) for key, value in row.items()})
    return out.getvalue()


def _run_rows(study: Study) -> list[dict[str, Any]]:
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunAttempt
    from backend.generation.runs.queries import current_runs

    current = set(current_runs(study, "main").values_list("pk", flat=True)) | set(
        current_runs(study, "pilot").values_list("pk", flat=True),
    )
    providers: dict[Any, dict[str, int]] = {}
    for run_id, served in RunAttempt.objects.filter(run__study=study).values_list("run_id", "served"):
        bucket = providers.setdefault(run_id, {})
        for name, n in ((served or {}).get("providers") or {}).items():
            bucket[name] = bucket.get(name, 0) + int(n or 0)
    rows = []
    runs = GenerationRun.objects.filter(study=study).select_related("condition", "cell__unit", "model", "parent_run")
    for run in runs.order_by("sequence", "created_at"):
        metrics = run.metrics or {}
        evaluation = metrics.get("eval") or {}
        validity = run.validity or {}
        rows.append(
            {
                "run_id": str(run.pk),
                "study_id": str(study.pk),
                "cell_id": run.cell_id,
                "condition_id": run.condition_id,
                "condition_label": run.condition.label if run.condition_id else "",
                "unit_key": run.cell.unit.unit_key if run.cell_id else "",
                "stage": run.stage,
                "repeat_index": run.repeat_index,
                "cell_attempt": run.cell_attempt,
                "attempt": run.attempt,
                "current": run.pk in current,
                "beyond_plan": run.beyond_plan,
                "kind": run.kind,
                "status": run.status,
                "outcome": run.outcome,
                "outcome_reason": run.outcome_reason,
                "usable": run.usable,
                "truncated": bool(validity.get("truncated")),
                "termination": validity.get("termination"),
                "model": run.model.model_id if run.model_id else "",
                "seed": run.seed,
                "config_fingerprint": run.config_fingerprint,
                "run_fingerprint": run.run_fingerprint,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                "served_providers": json.dumps(providers.get(run.pk, {}), sort_keys=True),
                "same_author": (run.parent_run.model_id == run.model_id) if run.parent_run_id else None,
                "requests_by_class": json.dumps(metrics.get("requests_by_class") or {}, sort_keys=True),
                "cost_usd": metrics.get("cost_usd"),
                "list_cost_usd": metrics.get("list_cost_usd"),
                "eval_status": evaluation.get("status") or "not_measured",
                "agent_pass_rate": evaluation.get("agent_pass_rate") if evaluation.get("measured") else None,
            },
        )
    return rows


def _attrition_rows(study: Study) -> tuple[list[dict[str, Any]], list[str]]:
    from backend.generation.runs.queries import attrition

    rows = attrition(study, "main")
    columns = [
        "condition_id",
        "label",
        "planned_cells",
        "cells",
        "usable",
        "truncated",
        "excluded",
        "infra_error",
        "infra_retries",
        "beyond_plan_runs",
        "attrition_rate",
        "runs_by_outcome",
        "truncated_by_kind",
        "excluded_by_reason",
        "install_share_by_stack",
    ]
    flat = [
        {key: (json.dumps(value, sort_keys=True) if isinstance(value, dict) else value) for key, value in row.items()}
        for row in rows
    ]
    return flat, columns


def _files_rows(study: Study) -> list[dict[str, Any]]:
    from backend.generation.models import GenerationRun
    from backend.generation.runs.view import run_view

    rows = []
    for run in GenerationRun.objects.filter(study=study).exclude(tree_hash="").order_by("created_at"):
        try:
            entries = run_view(run).files("app")
        except Exception:
            logger.exception("export: files of run %s", run.pk)
            rows.append({"run_id": str(run.pk), "path": "", "origin": "error"})
            continue
        rows.extend(
            {
                "run_id": str(run.pk),
                "path": entry.path,
                "origin": entry.origin,
                "sha": entry.blob,
                "loc": entry.loc,
                "size": entry.size,
                "language": entry.language,
                "is_test": entry.is_test,
                "excluded": entry.excluded,
            }
            for entry in entries
        )
    return rows


def export_zip(study: Study) -> tempfile.SpooledTemporaryFile:
    """The export archive of ``study`` (rewound, ready to stream)."""
    from backend.generation import blobs
    from backend.generation.models import RunEvaluation
    from backend.generation.models import RunManifest
    from backend.generation.study.plan import load_bundle

    spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES)  # noqa: SIM115 - returned to the caller
    with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if study.active_plan is not None:
            archive.writestr("plan.json", load_bundle(study.active_plan).plan.model_dump_json(indent=1))
        for run_id, manifest in RunManifest.objects.filter(run__study=study).values_list("run_id", "manifest"):
            archive.writestr(f"manifests/{run_id}.json", json.dumps(manifest, sort_keys=True, indent=1))
        archive.writestr("runs.csv", _csv(_run_rows(study), RUN_COLUMNS))
        attrition_rows, attrition_columns = _attrition_rows(study)
        archive.writestr("attrition.csv", _csv(attrition_rows, attrition_columns))
        archive.writestr(
            "files.csv",
            _csv(
                _files_rows(study),
                ["run_id", "path", "origin", "sha", "loc", "size", "language", "is_test", "excluded"],
            ),
        )
        lines = []
        for row in (
            RunEvaluation.objects.filter(run__study=study)
            .order_by("run_id", "created_at")
            .values(
                "id",
                "run_id",
                "eval_suite__slug",
                "eval_suite__version",
                "eval_level",
                "mode",
                "status",
                "result",
                "adherence",
                "retest_of_id",
                "reused_from_id",
                "created_at",
            )
        ):
            row["run_id"] = str(row["run_id"])
            row["created_at"] = row["created_at"].isoformat()
            lines.append(json.dumps(row, sort_keys=True, default=str))
        archive.writestr("evaluations.jsonl", "\n".join(lines) + ("\n" if lines else ""))
        from backend.generation.models import GenerationRun

        for run in GenerationRun.objects.filter(study=study).only("pk", "resolved_lock_blob", "lock_source"):
            record: dict[str, Any] = {"recorded": bool(run.resolved_lock_blob), "lock_source": run.lock_source}
            if run.resolved_lock_blob:
                try:
                    record["lock"] = blobs.get_json(run.resolved_lock_blob)
                except Exception:  # noqa: BLE001 - recorded as unreadable, never as "no dependencies"
                    record |= {"recorded": True, "lock": None, "error": "lock blob unreadable"}
            archive.writestr(f"locks/{run.pk}.json", json.dumps(record, sort_keys=True, indent=1))
        if study.mode == "slot":
            _patterns(archive, study)
    spool.seek(0)
    return spool


def _patterns(archive: zipfile.ZipFile, study: Study) -> None:
    """``patterns/`` from the pattern exporter: always at least ``README.txt`` (exported / not_measured / error)."""
    from backend.export.services.patterns import study_pattern_files

    for name, data in study_pattern_files(study).items():
        archive.writestr(f"patterns/{name}", data)
