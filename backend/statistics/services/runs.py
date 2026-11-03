"""The runs a statistics table describes, their identifying labels and their code (all through ``RunView``).

Every study table starts from :func:`study_runs` (the scope) and labels its rows with :func:`run_labels`.
Run content is read only through :mod:`backend.generation.runs.view`: ``current_runs`` for a study's
current run per cell (the **first usable** attempt, ``beyond_plan`` runs excluded), ``run_views`` for the
labels and ``run_views_code`` for the code, in bulk.

Scopes (spec section 3.7, Appendix A #4): **model** code is what the model wrote (authored, modified and
slot files); **app** code is the whole tree the analyzers read (starter files included). Source LOC is the
model scope's non-test source LOC (``reports.services.loc`` rules); it is ``None`` for a run whose tree
was never collected, never a silent 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.models import GenerationRun
from backend.generation.models.choices import Stage
from backend.generation.runs.view import current_runs
from backend.generation.runs.view import run_views
from backend.generation.runs.view import run_views_code

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.contrib.auth.models import AbstractBaseUser
    from django.db.models import QuerySet

    from backend.generation.runs.view import RunView

# The identifying columns of every per-run row (:func:`run_labels`).
RUN_LABEL_KEYS = (
    "run_id",
    "model",
    "model_name",
    "provider",
    "kind",
    "stack",
    "task",
    "unit_key",
    "condition_id",
    "condition_label",
    "repeat_index",
)
# Container prefixes a finding path may carry (the analyzers read the tree under one of them).
_PATH_PREFIXES = ("/work/", "/app/", "/workspace/", "./")


def normalize_path(path: str) -> str:
    """A finding's file path relative to the app root (container prefixes and ``./`` stripped)."""
    p = (path or "").replace("\\", "/")
    for prefix in _PATH_PREFIXES:
        if p.startswith(prefix):
            p = p[len(prefix) :]
    return p.lstrip("/")


def _owner_scoped(qs: QuerySet[GenerationRun], user: AbstractBaseUser | None) -> QuerySet[GenerationRun]:
    if user is not None and getattr(user, "is_authenticated", False) and not getattr(user, "is_staff", False):
        return qs.filter(created_by=user)
    return qs


def _current_ids(qs: QuerySet[GenerationRun], stage: str) -> set[Any]:
    """The current run of every *stage* study cell the runs of *qs* belong to (``runs.view.current_runs``).

    Only cells at *stage* count (Appendix A #11: pilot cells never enter a main-stage sample), and each study
    is resolved once.
    """
    ids: set[Any] = set()
    studies = qs.filter(cell__isnull=False, cell__stage=stage).values_list("study_id", flat=True).distinct()
    for study_id in studies:
        if study_id is not None:
            ids.update(current_runs(study_id, stage).values_list("pk", flat=True))
    return ids


def study_runs(
    user: AbstractBaseUser | None = None,
    *,
    study_id: str | None = None,
    run_ids: Iterable[str] | None = None,
    current_only: bool = True,
    stage: str = "main",
) -> QuerySet[GenerationRun]:
    """The runs a study table describes (owner-scoped unless *user* is None or staff), oldest first.

    With *study_id* and *current_only* (the default): the study's current run per cell at *stage*
    (``current_runs``: the first usable attempt, else the latest attempt so attrition stays visible). A retried
    cell counts once and a later attempt never replaces a usable one. Without a study, runs outside any cell
    (ad hoc) are kept unless marked ``stage=pilot``, and cell runs are their cell's current run at *stage*
    (pilot cells never enter a main-stage sample, Appendix A #11). ``beyond_plan`` runs are never in scope.
    With ``current_only=False`` every attempt is kept (beyond-plan runs still excluded).
    """
    qs = GenerationRun.objects.filter(beyond_plan=False)
    if study_id:
        qs = current_runs(study_id, stage) if current_only else qs.filter(study_id=study_id)
    elif current_only:
        scoped = _owner_scoped(qs, user)
        if run_ids is not None:
            scoped = scoped.filter(id__in=list(run_ids))
        current = _current_ids(scoped, stage)
        qs = qs.filter(cell__isnull=True).exclude(stage=Stage.PILOT) | qs.filter(pk__in=current)
    qs = _owner_scoped(qs.filter(beyond_plan=False), user)
    if run_ids is not None:
        qs = qs.filter(id__in=list(run_ids))
    return qs.select_related("model", "condition").order_by("created_at", "pk")


def condition_label(condition: Any) -> str:
    """A study condition's display name: its label, else its model id."""
    if condition is None:
        return ""
    label = getattr(condition, "label", "") or ""
    if label:
        return label
    model = getattr(condition, "model", None)
    return getattr(model, "model_id", "") or ""


def run_labels(run: GenerationRun, view: RunView | None = None) -> dict[str, Any]:
    """The identifying columns every per-run row starts with (:data:`RUN_LABEL_KEYS`).

    ``condition_id``/``condition_label`` name the study arm: one model can run under several (harness,
    stack or prompt arms), so per-arm tables group by condition, never by model. ``task`` is the pinned task
    version (``slug@version``) and ``unit_key`` the study unit (``task:<slug>@<v>`` or ``slot:<target>``).
    """
    if view is None:
        view = run_views([run])[0]
    model = run.model if run.model_id else None
    return {
        "run_id": str(run.pk),
        "model": model.model_id if model else "",
        "model_name": model.model_name if model else "",
        "provider": model.provider if model else "",
        "kind": run.kind,
        "stack": view.stack.ref if view.stack is not None else "",
        "task": view.task.ref if view.task is not None else "",
        "unit_key": view.unit_key or "",
        "condition_id": run.condition_id,
        "condition_label": condition_label(run.condition) if run.condition_id else "",
        "repeat_index": run.repeat_index,
    }


def labels_by_run(runs: Iterable[GenerationRun]) -> dict[str, dict[str, Any]]:
    """:func:`run_labels` of every run, with one ``run_views`` query for all of them."""
    items = list(runs)
    views = {view.id: view for view in run_views(items)}
    return {str(run.pk): run_labels(run, views.get(str(run.pk))) for run in items if str(run.pk) in views}


def app_unit(row: dict[str, Any]) -> tuple:
    """The app a per-run row measures: its (condition, unit); repeats of one cell are one app.

    Runs outside a study fall back to (model, kind) for the condition, and a run without a unit (no task) is
    its own app.
    """
    return (row.get("condition_id"), row.get("model"), row.get("kind"), row.get("unit_key") or row.get("run_id"))


# ── code and LOC ─────────────────────────────────────────────────────────


def code_loc(files: dict[str, str] | None, *, has_tree: bool) -> dict[str, int | None]:
    """Non-test source LOC (``total_loc``), test LOC and source files of a ``{path: text}`` map.

    ``None`` everywhere when the run has no collected tree (unmeasured, never 0). A collected tree whose scope
    holds no source (a model that changed nothing) is a measured 0.
    """
    from backend.reports.services.loc import loc_from_files
    from backend.reports.services.loc import loc_of_test_files

    if not has_tree:
        return {"total_loc": None, "source_files": None, "test_loc": None, "test_files": None}
    files = files or {}
    loc = loc_from_files(files)
    tests = loc_of_test_files(files)
    return {
        "total_loc": loc["total_loc"],
        "source_files": loc["source_files"],
        "test_loc": tests["test_loc"],
        "test_files": tests["test_files"],
    }


@dataclass
class RunCode:
    """Bulk code of a set of runs: app and model scope ``{run_id: {path: text}}`` (one query per scope)."""

    app: dict[str, dict[str, str]] = field(default_factory=dict)
    model: dict[str, dict[str, str]] = field(default_factory=dict)
    tree: dict[str, bool] = field(default_factory=dict)

    def model_paths(self, run_id: str) -> frozenset[str]:
        """The paths of the model's own files (findings there are attributed to the model)."""
        return frozenset(self.model.get(str(run_id), {}))

    def loc(self, run_id: str) -> dict[str, int | None]:
        """Model-scope source LOC of *run_id* (:func:`code_loc`)."""
        run_id = str(run_id)
        return code_loc(self.model.get(run_id), has_tree=self.tree.get(run_id, False))

    def loc_app(self, run_id: str) -> int | None:
        """App-scope (whole tree, starter included) non-test source LOC; ``None`` without a tree."""
        run_id = str(run_id)
        return code_loc(self.app.get(run_id), has_tree=self.tree.get(run_id, False))["total_loc"]


def load_code(runs: Iterable[GenerationRun] | QuerySet[GenerationRun], *, app: bool = True) -> RunCode:
    """:class:`RunCode` of *runs* through ``run_views_code`` (app scope only when *app*).

    A run has a tree when it recorded a ``tree_hash`` or any of its scopes holds a file.
    """
    items = list(runs)
    model = run_views_code(items, "model")
    app_code = run_views_code(items, "app") if app else {}
    tree = {
        str(run.pk): bool(run.tree_hash) or bool(model.get(str(run.pk))) or bool(app_code.get(str(run.pk)))
        for run in items
    }
    return RunCode(app=app_code, model=model, tree=tree)


__all__ = [
    "RUN_LABEL_KEYS",
    "RunCode",
    "app_unit",
    "code_loc",
    "condition_label",
    "labels_by_run",
    "load_code",
    "normalize_path",
    "run_labels",
    "study_runs",
]
