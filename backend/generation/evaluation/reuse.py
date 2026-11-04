"""Reuse of ``RunEvaluation`` rows by ``(tree_hash, suite, check selection)`` (spec 7.8, 8.5).

A candidate is reused only when every one of these holds:

- it is ``measured`` and its result says ``measured: true``; it is not a retest;
- the tree is byte-identical (``tree_hash``) and the suite row is the same (same pinned content);
- the check selection (``stages.tester.detail.only_checks``) and the probe selection
  (``stages.tester.detail.probes``) equal the request;
- its evaluation level covers every stage the request needs (``levels.LEVEL_TABLE``), so a ``build``-level
  row never fills the functional or probe stages of a ``security`` request with data that was never measured;
- its mode equals the requested mode, and it was not evaluated with an ``image_override`` or
  ``overlay_files`` (an overlay result describes the parent image plus a file, not a built tree);
- the locks agree **when both are set**. A new run (for example a slot variant whose tree equals an
  earlier one) has a blank lock until its own evaluation; it then adopts the source's lock
  (``adopt_lock``), so the reuse never re-resolves dependencies (13.4).

The caller records the ``eval_reused`` validity note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import EvalSuite
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation

__all__ = ["adopt_lock", "covers", "find_reusable", "selection_detail", "selection_key"]

_SCAN_LIMIT = 50
_DEFAULT_LEVEL = "security"  # EvaluationSpec's default level


def selection_key(selection: list[str] | None) -> list[str] | None:
    """The normalised check or probe selection stored with an evaluation (``None`` = everything)."""
    return None if selection is None else sorted(set(selection))


def selection_detail(
    *,
    only_checks: list[str] | None,
    probes: list[str] | None,
    image_override: str | None = None,
    overlay_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """What ``suite.evaluate`` writes to ``stages.tester.detail`` so that reuse can compare selections."""
    return {
        "only_checks": selection_key(only_checks),
        "probes": selection_key(probes),
        "image_override": bool(image_override),
        "overlay": bool(overlay_files),
    }


def covers(candidate_level: str, requested_level: str) -> bool:
    """True when every stage of ``requested_level`` is also a stage of ``candidate_level`` (level table)."""
    from backend.generation.evaluation.levels import LEVEL_TABLE

    have, want = LEVEL_TABLE.get(candidate_level), LEVEL_TABLE.get(requested_level)
    if have is None or want is None:
        return False
    return all(have[name] for name, on in want.items() if on)


def _requested_level(run: GenerationRun) -> str:
    from backend.generation.models import RunManifest

    payload = RunManifest.objects.filter(run_id=run.pk).values_list("manifest", flat=True).first()
    planned = payload.get("planned_evaluation") if isinstance(payload, dict) else None
    level = planned.get("eval_level") if isinstance(planned, dict) else None
    return level or _DEFAULT_LEVEL


def _detail(candidate: RunEvaluation) -> dict[str, Any]:
    stages = candidate.stages if isinstance(candidate.stages, dict) else {}
    tester = stages.get("tester")
    detail = tester.get("detail") if isinstance(tester, dict) else None
    return detail if isinstance(detail, dict) else {}


def find_reusable(
    run: GenerationRun,
    suite: EvalSuite,
    *,
    only_checks: list[str] | None = None,
    probes: list[str] | None = None,
    eval_level: str | None = None,
    mode: str = "full",
    adopt: bool = True,
) -> RunEvaluation | None:
    """A measured evaluation of an identical tree that covers this request, else ``None``.

    ``eval_level`` defaults to the run's planned level (manifest), else ``security``. With ``adopt`` (the
    default) a run whose lock is blank takes the source run's lock (see ``adopt_lock``).
    """
    from backend.generation.models import RunEvaluation
    from backend.generation.models import choices

    if not run.tree_hash or mode == choices.EvalMode.OVERLAY:
        return None  # an overlay request runs the parent image; it is never served from another tree's row
    level = eval_level or _requested_level(run)
    candidates = (
        RunEvaluation.objects.filter(
            run__tree_hash=run.tree_hash,
            eval_suite=suite,
            status=choices.EvaluationStatus.MEASURED,
            retest_of__isnull=True,
            mode=mode,
        )
        .exclude(run_id=run.pk)
        .select_related("run")
        .order_by("-created_at", "-pk")
    )
    wanted_checks, wanted_probes = selection_key(only_checks), selection_key(probes)
    for candidate in candidates[:_SCAN_LIMIT]:
        result = candidate.result if isinstance(candidate.result, dict) else {}
        if not result.get("measured") or not covers(candidate.eval_level, level):
            continue
        detail = _detail(candidate)
        if detail.get("image_override") or detail.get("overlay"):
            continue
        if selection_key(detail.get("only_checks")) != wanted_checks:
            continue
        if selection_key(detail.get("probes")) != wanted_probes:
            continue
        source_lock = candidate.run.resolved_lock_blob
        if run.resolved_lock_blob and source_lock and run.resolved_lock_blob != source_lock:
            continue
        if adopt:
            adopt_lock(run, candidate.run)
        return candidate
    return None


def adopt_lock(run: GenerationRun, source: GenerationRun) -> bool:
    """Copy ``source``'s resolved lock onto ``run`` when ``run`` has none (a reuse never re-resolves)."""
    from backend.generation.models import GenerationRun

    if run.resolved_lock_blob or not source.resolved_lock_blob:
        return False
    updated = GenerationRun.objects.filter(pk=run.pk, resolved_lock_blob="").update(
        resolved_lock_blob=source.resolved_lock_blob,
        lock_source=source.lock_source,
        registry_snapshot_at=source.registry_snapshot_at,
    )
    if updated:
        run.resolved_lock_blob = source.resolved_lock_blob
        run.lock_source = source.lock_source
        run.registry_snapshot_at = source.registry_snapshot_at
    return bool(updated)
