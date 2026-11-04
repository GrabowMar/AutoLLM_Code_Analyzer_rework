"""Pattern analysis (spec section 7.9): normalizers, clustering, detectors, prevalence, audits and export.

Entry points other WPs call:

- ``patterns.run(pattern_run)``: execute a pending PatternRun (the post_run lane; WP-10's
  ``study.automation.run_post_run`` calls it for ``StudyPostRun(kind=patterns)``);
- ``patterns.create_pattern_run(study, detectors="default", ...)``: pin a detector set and queue a run (the
  slot-study launch auto-attaches one, section 7.9);
- ``patterns.live.score_variant(run)`` and ``patterns.live.ci_width_reached(study, rule)``: live provisional
  scoring after each variant's evaluation and the ``ci_width_reached`` stop-rule check (WP-12, WP-11);
- ``patterns.detectors.detector_set_hash(...)``: the value ``analysis_plan.patterns.detector_set_hash`` holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import PatternRun
    from backend.generation.models import Study

__all__ = ["create_pattern_run", "run", "run_post_run"]


def run(pattern_run: PatternRun, *, runner: Any = None) -> PatternRun:
    """Run every detector of ``pattern_run`` and compute clusters and prevalence."""
    from backend.generation.patterns.service import execute

    return execute(pattern_run, runner=runner)


def create_pattern_run(study: Study, **kwargs: Any) -> PatternRun:
    """Pin a detector set (``"default"`` or refs) and queue a PatternRun as a patterns post-run."""
    from backend.generation.patterns.service import create_pattern_run as create

    return create(study, **kwargs)


def run_post_run(post_run: Any, *, runner: Any = None) -> PatternRun:
    """Execute a ``StudyPostRun(kind=patterns)`` (creates its PatternRun from ``config`` when missing)."""
    from backend.generation.patterns.service import run_post_run as execute_post_run

    return execute_post_run(post_run, runner=runner)
