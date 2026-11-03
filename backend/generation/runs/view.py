"""``RunView``: the only read API that code outside ``generation`` may use for run content (spec section 3.11).

WP-01 writes the dataclasses and the signatures; WP-02a implements them (including the overlay of the
parent's rows for slot variants and the bulk reader ``run_views_code``). Until then every accessor raises
``NotImplementedError``.

Mapping from the v2 readers (WP-02a/WP-02b checklist):

* ``job.resolved_bundle["config_fingerprint"]`` -> ``run.config_fingerprint``
* ``job.resolved_bundle["stack"]`` / ``job.stack_slug`` -> ``run_view(run).stack``
* ``job.resolved_bundle["app_requirement"]`` -> ``run_view(run).task_dict()``
* ``job.resolved_bundle["harness"]`` -> ``run_view(run).harness``
* ``job.resolved_bundle["endpoint"]`` -> ``run.pinned_served_name`` / ``run.pinned_digest``
* ``result_data["files"]`` -> ``RunFile`` rows and ``code_files()``
* ``result_data["agent"]["validity"]`` -> ``run.validity`` and ``run.usable``
* ``metrics["functional"]`` -> ``latest_evaluation().result.metrics`` (mirrored in ``run.metrics["eval"]``)
* ``job.experiment`` / ``job.condition`` / ``job.app_requirement`` -> ``run.study`` / ``run.condition`` /
  ``run.cell.unit``
* ``current_jobs(experiment)`` -> ``runs.queries.current_runs(study, stage="main")`` (WP-10)
* ``job.mode == "agent"`` -> ``run.kind``
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation
    from backend.generation.models import RunFile

__all__ = ["CodeScope", "RunView", "StackRef", "TaskRef", "run_view", "run_views", "run_views_code"]

_PENDING = "WP-02a: RunView is not implemented yet"

CodeScope = Literal["model", "app", "slot"]
# ``model``: ``origin IN (authored, modified, slot) AND excluded=''``; ``slot``: the variant's ``slot`` rows;
# ``app``: the whole non-artifact tree (the parent's rows overlaid for slot variants, shown as ``parent``).


@dataclass(frozen=True)
class StackRef:
    ref: str
    content_hash: str
    backend_framework: str
    frontend_framework: str
    database: str


@dataclass(frozen=True)
class TaskRef:
    ref: str
    slug: str
    version: int
    variant: str
    task_hash: str
    title: str
    features: list[str] = field(default_factory=list)
    surfaces: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunView:
    """A read-only view of one run: identity, treatment labels, cost and accessors for its content."""

    id: str
    kind: str
    protocol: str
    status: str
    outcome: str
    usable: bool | None
    model_slug: str | None
    harness: tuple[str, str] | None  # (name, version)
    stack: StackRef | None
    task: TaskRef | None
    style_ref: str | None
    auth_mode: str | None
    guidance_ref: str | None
    study_id: str | None
    condition_id: int | None
    cell_id: int | None
    unit_key: str | None
    repeat_index: int | None
    stage: str | None
    seed: int | None
    fingerprints: dict[str, str]  # task_hash, treatment_hash, prompt_hash, config_fingerprint, run_fingerprint
    cost_usd: float | None
    total_tokens: int | None
    # The row the view was built from, for the accessors below (not part of the view's value).
    _run: GenerationRun = field(repr=False, compare=False)

    def code_files(self, scope: CodeScope = "model") -> dict[str, str]:
        """``{path: text}`` of the scope; for slot variants the parent's rows are overlaid for ``app``."""
        raise NotImplementedError(_PENDING)

    def file_rows(self, scope: CodeScope = "model") -> QuerySet[RunFile]:
        """The ``RunFile`` rows of the scope."""
        raise NotImplementedError(_PENDING)

    def loc(self, scope: CodeScope = "model") -> int:
        """Source LOC of the scope; tests and lockfiles are excluded."""
        raise NotImplementedError(_PENDING)

    def latest_evaluation(self, suite_ref: str | None = None) -> RunEvaluation | None:
        """The latest evaluation per suite (retests excluded); ``None`` when never evaluated."""
        raise NotImplementedError(_PENDING)

    def task_dict(self) -> dict[str, Any] | None:
        """A stable dict for human_eval and export: the structured task plus the chosen prose variant."""
        raise NotImplementedError(_PENDING)

    def manifest(self) -> dict[str, Any]:
        """The frozen ``RunManifest.manifest``."""
        raise NotImplementedError(_PENDING)

    def labels(self) -> dict[str, str]:
        """``{factor_key: human label}`` for tables."""
        raise NotImplementedError(_PENDING)


def run_view(run: GenerationRun) -> RunView:
    """The ``RunView`` of one run."""
    raise NotImplementedError(_PENDING)


def run_views(runs: QuerySet[GenerationRun] | Iterable[GenerationRun]) -> list[RunView]:
    """``RunView`` of every run with one query plus prefetches (no N+1)."""
    raise NotImplementedError(_PENDING)


def run_views_code(
    runs: QuerySet[GenerationRun] | Iterable[GenerationRun],
    scope: CodeScope = "model",
) -> dict[str, dict[str, str]]:
    """Bulk reader for statistics: ``{run_id: {path: text}}`` with one query and batched blob reads."""
    raise NotImplementedError(_PENDING)
