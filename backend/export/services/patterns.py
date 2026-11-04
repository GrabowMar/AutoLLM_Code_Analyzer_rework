"""The pattern-analysis part of a study export (WP-13 ask; spec 9.4 study export).

``study_pattern_files(study)`` returns the files to write under ``patterns/`` of the study export zip:
the frozen export of the study's latest *completed, non-live* PatternRun
(``generation.patterns.export.build_export``) as ``<pattern run id>.zip``, plus ``README.txt`` saying
which run it is. The live (provisional) run is never exported: it is not a frozen record.

No silent empty result: a study without a completed run, or whose export failed, gets a README that
says so (``status: not_measured`` / ``error``), never an empty directory that reads as "no patterns".
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import PatternRun
    from backend.generation.models import Study

logger = logging.getLogger(__name__)

__all__ = ["latest_exportable_pattern_run", "study_pattern_files"]


def latest_exportable_pattern_run(study: Study) -> PatternRun | None:
    """The study's latest completed non-live PatternRun (by completion time), or ``None``."""
    from backend.generation.models import PatternRun
    from backend.generation.models.choices import WorkStatus

    return (
        PatternRun.objects.filter(study=study, is_live=False, status=WorkStatus.COMPLETED)
        .order_by("-completed_at", "-created_at")
        .first()
    )


def _readme(record: dict) -> bytes:
    return (json.dumps(record, indent=1, sort_keys=True) + "\n").encode()


def study_pattern_files(study: Study) -> dict[str, bytes]:
    """``{name: bytes}`` to write under ``patterns/`` of the study export."""
    from backend.generation.patterns import export as pattern_export

    pattern_run = latest_exportable_pattern_run(study)
    if pattern_run is None:
        return {
            "README.txt": _readme(
                {
                    "status": "not_measured",
                    "reason": "The study has no completed (non-live) pattern run; nothing was exported.",
                },
            ),
        }
    meta = {
        "pattern_run": str(pattern_run.pk),
        "label": pattern_run.label,
        "completed_at": pattern_run.completed_at.isoformat() if pattern_run.completed_at else None,
        "detector_set_hash": pattern_run.detector_set_hash,
    }
    try:
        data = pattern_export.build_export(pattern_run)
    except Exception as exc:
        logger.exception("pattern export failed for pattern run %s", pattern_run.pk)
        return {"README.txt": _readme({**meta, "status": "error", "error": f"{type(exc).__name__}: {exc}"})}
    name = f"{pattern_run.pk}.zip"
    return {name: data, "README.txt": _readme({**meta, "status": "exported", "file": name})}
