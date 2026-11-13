"""Test helpers for the statistics tables: v3 runs with real file trees, studies with cells, analyses."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation import blobs
from backend.generation.runs.view import source_loc
from backend.generation.testing import factories as gf

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import Study


def sha(label: str) -> str:
    """A deterministic sha256 hex digest for identity columns."""
    return hashlib.sha256(label.encode()).hexdigest()


def eval_summary(pass_rate: float | None, *, status: str = "measured", evaluation_id: int | None = None) -> dict:
    """A ``metrics["eval"]`` mirror (``contracts.outcome.EvalSummary``)."""
    return {
        "eval_suite": "eval-v2@1",
        "eval_level": "functional",
        "status": status,
        "measured": status == "measured",
        "agent_pass_rate": pass_rate,
        "evaluation_id": evaluation_id,
    }


def add_files(run: GenerationRun, files: dict[str, str], origins: dict[str, str] | None = None) -> GenerationRun:
    """RunFile rows with real blobs for *files* (``{path: text}``); origin ``authored`` unless *origins* says."""
    for path, text in files.items():
        gf.RunFileFactory(
            run=run,
            path=path,
            blob=blobs.put_text(text),
            size=len(text.encode()),
            origin=(origins or {}).get(path, "authored"),
            loc=source_loc(path, text),
        )
    return run


def make_run(
    user=None,
    *,
    files: dict[str, str] | None = None,
    origins: dict[str, str] | None = None,
    tested: bool = True,
    **kw: Any,
) -> GenerationRun:
    """A completed, usable ``app_agentic`` run (``tested``) with *files* as its collected tree."""
    if user is not None:
        kw["created_by"] = user
    run = gf.GenerationRunFactory(tested=tested, **kw)
    return add_files(run, files or {}, origins)


def analyze(
    run: GenerationRun,
    tools: list[str] | tuple[str, ...],
    *,
    status: str = AnalysisRun.Status.COMPLETED,
    results: bool = True,
    result_status: str = ToolResult.Status.COMPLETED,
) -> AnalysisRun:
    """An analysis of *run* selecting *tools*; with *results*, one result per tool."""
    analysis = AnalysisRunFactory(
        created_by=run.created_by,
        generation_job=run,
        status=status,
        tool_slugs=list(tools),
    )
    if results:
        for tool in tools:
            ToolResultFactory(run=analysis, tool_slug=tool, status=result_status)
    return analysis


class StudyBuilder:
    """A study with conditions, units and main-stage cells; :meth:`run` fills a cell with an attempt."""

    def __init__(self, user, *, repeats: int = 2, status: str = "completed", n_conditions: int = 2, n_units: int = 2):
        self.study: Study = gf.StudyFactory(created_by=user)
        self.study.design = {**self.study.design, "repeats": repeats}
        self.study.status = status
        self.study.save()
        self.plan = gf.StudyPlanFactory(study=self.study)
        self.user = user
        self.conditions = [gf.StudyConditionFactory(plan=self.plan, study=self.study) for _ in range(n_conditions)]
        self.units = [gf.StudyUnitFactory(plan=self.plan, study=self.study) for _ in range(n_units)]
        self.cells: dict[tuple[int, int, int], Any] = {}

    def cell(self, c: int, u: int, repeat: int, *, stage: str = "main"):
        key = (c, u, repeat)
        if key not in self.cells:
            self.cells[key] = gf.StudyCellFactory(
                condition=self.conditions[c],
                plan=self.plan,
                study=self.study,
                unit=self.units[u],
                repeat_index=repeat,
                stage=stage,
            )
        return self.cells[key]

    def run(self, c: int, u: int, repeat: int, *, attempt: int = 1, files=None, origins=None, **kw) -> GenerationRun:
        cell = self.cell(c, u, repeat, stage=kw.pop("cell_stage", "main"))
        condition = self.conditions[c]
        kw.setdefault("config_fingerprint", sha(f"cfp-{c}-{u}"))
        return make_run(
            self.user,
            files=files,
            origins=origins,
            study=self.study,
            condition=condition,
            cell=cell,
            cell_attempt=attempt,
            repeat_index=repeat,
            stage=cell.stage,
            model=condition.model,
            task_version=self.units[u].task_version,
            **kw,
        )
