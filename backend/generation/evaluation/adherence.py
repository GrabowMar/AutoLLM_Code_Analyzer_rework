"""Adherence-checker driver (spec section 8.7; ``CHECKER_VERSION = 1``).

``check_adherence(run, runtime=None, observed=None)`` runs the checker (``images/adherence-checker``) on the
run's app tree with ``expect = {stack, style, auth_mode, runtime}`` and returns a validated
``AdherenceRecord``. ``observed`` carries what the tester saw at run time (``{"auth": "cookie"|"bearer",
"server": ...}``); without it the auth dimension is not decided. The SBOM, when the checker image wrote one,
goes to the blob store (``sbom_blob``).

Adherence never filters a sample: it is a column and a validity note (``adherence:<status>``). A checker that
cannot run gives ``status=not_measured``, never ``compliant``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from backend.generation.contracts.evaluation import AdherenceRecord

if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.generation.evaluation.runtime import EvalRuntime
    from backend.generation.models import GenerationRun

__all__ = ["CHECKER_VERSION", "check_adherence", "expect_for", "not_measured"]

CHECKER_VERSION = 1


def not_measured() -> AdherenceRecord:
    return AdherenceRecord(checker_version=CHECKER_VERSION, status="not_measured")


def expect_for(run: GenerationRun, observed: Mapping[str, Any] | None = None) -> dict[str, Any]:
    stack = run.stack_profile or (run.parent_run.stack_profile if run.parent_run_id else None)
    style = run.style or (run.parent_run.style if run.parent_run_id else None)
    return {
        "stack": (stack.content if stack is not None else None) or {},
        "style": (style.content if style is not None else None) or None,
        "auth_mode": run.auth_mode or (run.parent_run.auth_mode if run.parent_run_id else ""),
        "runtime": dict(observed or {}),
    }


def check_adherence(
    run: GenerationRun,
    runtime: EvalRuntime | None = None,
    *,
    observed: Mapping[str, Any] | None = None,
) -> AdherenceRecord:
    """Stack, style and auth adherence of the sample."""
    from backend.generation import blobs
    from backend.generation.evaluation.lock import tree_files
    from backend.generation.evaluation.runtime import LocalProcessRuntime

    files, _origins = tree_files(run)
    if not files:
        return not_measured()
    owns = runtime is None
    runtime = runtime or LocalProcessRuntime()
    try:
        record, sbom = runtime.run_adherence(files, expect_for(run, observed))
    finally:
        if owns:
            runtime.close()
    if sbom:
        record = {**record, "sbom_blob": blobs.put_bytes(sbom, media_type="application/vnd.cyclonedx+json")}
    return AdherenceRecord.model_validate(record)
