"""``RunView``: the only read API that code outside ``generation`` may use for run content (spec section 3.11).

Scopes (section 3.7):

* ``model``: the model's own contribution, ``origin IN (authored, modified, slot) AND excluded=''``;
* ``slot``: the variant's ``slot`` rows (empty for every other run);
* ``app``: the whole non-artifact tree (``deleted`` paths removed). A slot variant stores only its ``slot``
  rows; ``app`` overlays them on the parent's ``app`` tree and reports the parent's rows with origin
  ``parent`` (a view-only origin, never stored).

``loc()`` counts source LOC with the reports rules: test files, lockfiles and other non-source files and
every ``excluded`` row are not counted. A run whose tree was never collected has ``loc() is None`` (never a
silent 0).

Typical reads: ``run.config_fingerprint``; ``run_view(run).stack`` / ``.task_dict()`` / ``.harness``;
``run.pinned_served_name`` / ``run.pinned_digest``; ``code_files()`` / ``files()`` (``RunFile`` rows);
``run.validity`` / ``run.usable``; ``latest_evaluation().result.metrics`` (mirrored in ``run.metrics["eval"]``);
``run.study`` / ``run.condition`` / ``run.cell.unit``; ``run.kind``; ``current_runs(study, stage="main")``
(``runs.queries.current_runs``); ``run_views_files(runs, scope)`` (bulk ``files()``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Case
from django.db.models import F
from django.db.models import Q
from django.db.models import QuerySet
from django.db.models import Value
from django.db.models import When
from django.db.models.functions import Collate

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation
    from backend.generation.models import RunFile
    from backend.generation.models import Study

__all__ = [
    "PARENT_ORIGIN",
    "CodeScope",
    "FileEntry",
    "RunView",
    "StackRef",
    "TaskRef",
    "current_runs",
    "run_view",
    "run_views",
    "run_views_code",
    "run_views_files",
    "source_loc",
]

CodeScope = Literal["model", "app", "slot"]
_SCOPES: tuple[str, ...] = ("model", "app", "slot")

# The view-only origin of a parent's row overlaid under a slot variant (never stored in RunFile).
PARENT_ORIGIN = "parent"
_MODEL_ORIGINS = frozenset({"authored", "modified", "slot"})
_NOT_IN_TREE = frozenset({"artifact", "deleted"})
_SLOT_KINDS = frozenset({"slot_single_shot", "slot_agent"})
_BINARY = "binary"

# Foreign keys every view reads (``run_views`` fetches them in the same query).
_SELECT_RELATED: tuple[str, ...] = (
    "model",
    "task_version__task",
    "stack_profile",
    "starter",
    "style",
    "guidance",
    "harness_preset",
    "prompt_set",
    "sampling_preset",
    "resource_policy",
    "cell__unit",
    "manifest",
    "parent_run__stack_profile",
    "parent_run__starter",
)

# Factor key -> run foreign key of the library entity (labels fall back to these when the manifest lacks one).
_FACTOR_FKS: dict[str, str] = {
    "model": "model",
    "harness_preset": "harness_preset",
    "stack_profile": "stack_profile",
    "style": "style",
    "guidance": "guidance",
    "prompt_set": "prompt_set",
    "sampling_preset": "sampling_preset",
    "resource_policy": "resource_policy",
}


# ---------------------------------------------------------------------------
# Source LOC
# ---------------------------------------------------------------------------


def source_loc(path: str, text: str | None) -> int:
    """Source LOC of one file: 0 for tests, lockfiles, manifests, docs and other non-source files.

    Delegates to ``reports.services.loc.source_loc`` (the one rule set every study table uses).
    """
    from backend.reports.services import loc as rules

    return int(rules.source_loc(path, text))


def _counts_as_source(entry: FileEntry) -> bool:
    from backend.reports.services import loc as rules

    return (
        not entry.excluded
        and not entry.is_test
        and rules.is_source_file(entry.path)
        and not rules.is_test_path(entry.path)
    )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


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
class FileEntry:
    """One path of a scope. ``origin`` is the stored origin, or ``"parent"`` for an overlaid parent row."""

    path: str
    origin: str
    blob: str
    size: int
    mode: int
    language: str
    loc: int
    is_test: bool
    excluded: str
    run_id: str

    @classmethod
    def from_row(cls, row: RunFile, origin: str | None = None) -> FileEntry:
        return cls(
            path=row.path,
            origin=origin or row.origin,
            blob=row.blob,
            size=row.size,
            mode=row.mode,
            language=row.language,
            loc=row.loc,
            is_test=row.is_test,
            excluded=row.excluded,
            run_id=str(row.run_id),
        )


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

    # -- content -------------------------------------------------------------------------------------

    @property
    def parent_run_id(self) -> str | None:
        """The parent of a slot variant (whose tree ``app`` overlays), else ``None``."""
        run = self._run
        return str(run.parent_run_id) if run.kind in _SLOT_KINDS and run.parent_run_id else None

    def files(self, scope: CodeScope = "model") -> list[FileEntry]:
        """The scope's paths sorted by path, with their origin (``parent`` for overlaid parent rows)."""
        _check_scope(scope)
        parent_id = self.parent_run_id if scope == "app" else None
        rows = _load_rows([self.id, parent_id])
        return _select(scope, rows.get(self.id, []), rows.get(parent_id, []) if parent_id else None)

    def code_files(self, scope: CodeScope = "model") -> dict[str, str]:
        """``{path: text}`` of the scope; for slot variants the parent's rows are overlaid for ``app``.

        Binary rows have no text and are left out; a missing blob raises ``BlobNotFoundError``.
        """
        return _read_texts(self.files(scope), _BlobCache())

    def file_rows(self, scope: CodeScope = "model") -> QuerySet[RunFile]:
        """The ``RunFile`` rows of the scope, annotated with ``view_origin`` (``parent`` for overlaid rows)."""
        from backend.generation.models import RunFile

        _check_scope(scope)
        run_id = self._run.pk
        own = Q(run_id=run_id)
        parent_id = self.parent_run_id if scope == "app" else None
        if scope == "model":
            condition = own & Q(origin__in=_MODEL_ORIGINS, excluded="")
        elif scope == "slot":
            condition = own & Q(origin="slot")
        else:
            condition = own & ~Q(origin__in=_NOT_IN_TREE)
            if parent_id:
                shadowed = RunFile.objects.filter(run_id=run_id).values("path")
                condition |= Q(run_id=parent_id) & ~Q(origin__in=_NOT_IN_TREE) & ~Q(path__in=shadowed)
        origin: Any = F("origin")
        if parent_id:
            origin = Case(When(run_id=parent_id, then=Value(PARENT_ORIGIN)), default=F("origin"))
        # Byte order (collation "C"), the order ``files()`` uses.
        return RunFile.objects.filter(condition).annotate(view_origin=origin).order_by(Collate(F("path"), "C"))

    def loc(self, scope: CodeScope = "model") -> int | None:
        """Source LOC of the scope (tests, lockfiles, non-source and excluded rows not counted).

        ``None`` when the run has no collected tree (no ``tree_hash`` and no files): missing, not zero.
        """
        entries = self.files(scope)
        if not entries and not self._has_tree():
            return None
        return sum(entry.loc for entry in entries if _counts_as_source(entry))

    def _has_tree(self) -> bool:
        from backend.generation.models import RunFile

        if self._run.tree_hash:
            return True
        return RunFile.objects.filter(run_id=self._run.pk).exists()

    # -- evaluation, task, manifest ------------------------------------------------------------------

    def latest_evaluation(self, suite_ref: str | None = None) -> RunEvaluation | None:
        """The latest evaluation (any status) under ``suite_ref`` (``slug@version``), retests excluded.

        Without ``suite_ref`` the manifest's planned suite is used, else the latest row of any suite.
        ``None`` when the run was never evaluated under that suite.
        """
        qs = self._run.evaluations.filter(retest_of__isnull=True).select_related("eval_suite")
        ref = suite_ref or _planned_suite_ref(self.manifest())
        if ref:
            slug, _, version = ref.split("#", 1)[0].partition("@")
            qs = qs.filter(eval_suite__slug=slug)
            if version:
                if not version.isdigit():
                    return None
                qs = qs.filter(eval_suite__version=int(version))
        return qs.order_by("-created_at", "-pk").first()

    def task_dict(self) -> dict[str, Any] | None:
        """A stable dict for human_eval and export: the structured task plus the chosen prose variant."""
        version = self._run.task_version
        if version is None or self.task is None:
            return None
        content = dict(version.content or {})
        prose = content.pop("prose", {}) or {}
        return {
            "ref": version.ref,
            "pinned_ref": version.pinned_ref,
            "slug": version.slug,
            "version": version.version,
            "variant": self.task.variant,
            "title": content.get("title", ""),
            "summary": content.get("summary", ""),
            "category": content.get("category", ""),
            "features": list(content.get("features") or []),
            "surfaces": list(content.get("surfaces") or []),
            "structured_hash": version.structured_hash,
            "task_hash": self.task.task_hash,
            "prose": prose.get(self.task.variant, ""),
            "structured": content,
        }

    def manifest(self) -> dict[str, Any]:
        """The frozen ``RunManifest.manifest`` (``{}`` when the run has none, e.g. a bare reference run)."""
        record = _manifest_row(self._run)
        return dict(record.manifest) if record is not None and isinstance(record.manifest, dict) else {}

    def labels(self) -> dict[str, str]:
        """``{factor_key: human label}`` for tables: the manifest's factors, completed from the run's columns."""
        labels: dict[str, str] = {}
        for key, record in (self.manifest().get("factors") or {}).items():
            if isinstance(record, dict):
                label = record.get("value") if record.get("value") is not None else record.get("ref")
                if label is not None:
                    labels[key] = _short_ref(str(label))
        run = self._run
        for key, attr in _FACTOR_FKS.items():
            obj = getattr(run, attr, None)
            if key in labels or obj is None:
                continue
            labels[key] = obj.model_id if key == "model" else obj.ref
        if self.stack is not None:
            labels.setdefault("stack_profile", self.stack.ref)
        if self.auth_mode:
            labels.setdefault("auth_mode", self.auth_mode)
        if self.task is not None and self.task.variant:
            labels.setdefault("prose_variant", self.task.variant)
        return labels


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def _check_scope(scope: str) -> None:
    if scope not in _SCOPES:
        msg = f"unknown code scope {scope!r} (expected one of {', '.join(_SCOPES)})"
        raise ValueError(msg)


def _short_ref(ref: str) -> str:
    return ref.split("#", 1)[0]


def _manifest_row(run: GenerationRun) -> Any:
    try:
        return run.manifest
    except ObjectDoesNotExist:
        return None


def _planned_suite_ref(manifest: dict[str, Any]) -> str | None:
    planned = manifest.get("planned_evaluation") or {}
    suite = planned.get("eval_suite") or {}
    ref = suite.get("ref")
    return ref if isinstance(ref, str) and ref else None


def _parent(run: GenerationRun) -> GenerationRun | None:
    return run.parent_run if run.kind in _SLOT_KINDS and run.parent_run_id else None


def _stack_ref(run: GenerationRun) -> StackRef | None:
    stack = run.stack_profile
    parent = _parent(run) if stack is None else None
    if parent is not None:
        stack = parent.stack_profile
    if stack is None:
        return None
    return StackRef(
        ref=stack.ref,
        content_hash=stack.content_hash,
        backend_framework=stack.backend_framework,
        frontend_framework=stack.frontend_framework,
        database=stack.database,
    )


def _task_ref(run: GenerationRun, manifest: dict[str, Any]) -> TaskRef | None:
    version = run.task_version
    if version is None:
        return None
    content = version.content or {}
    variant = run.prose_variant or (manifest.get("task") or {}).get("variant") or ""
    return TaskRef(
        ref=version.ref,
        slug=version.slug,
        version=version.version,
        variant=variant,
        task_hash=run.task_hash,
        title=str(content.get("title", version.name)),
        features=list(content.get("features") or []),
        surfaces=list(content.get("surfaces") or []),
    )


def _harness(run: GenerationRun, manifest: dict[str, Any]) -> tuple[str, str] | None:
    block = manifest.get("harness")
    if isinstance(block, dict) and block.get("name"):
        return (str(block["name"]), str(block.get("version", "")))
    preset = run.harness_preset
    if preset is not None and isinstance(preset.content, dict) and preset.content.get("harness"):
        return (str(preset.content["harness"]), str(preset.content.get("harness_version", "")))
    return None


def _model_slug(run: GenerationRun, manifest: dict[str, Any]) -> str | None:
    slug = (manifest.get("model") or {}).get("slug")
    if slug:
        return str(slug)
    return run.model.model_id if run.model is not None else None


def _unit_key(run: GenerationRun) -> str | None:
    if run.cell_id is not None and run.cell is not None:
        return run.cell.unit.unit_key
    if run.kind == "app_agentic" and run.task_version is not None:
        return run.task_version.unit_key
    if run.slot_target_id is not None:
        return f"slot:{run.slot_target_id}"
    return None


def _metric(run: GenerationRun, key: str) -> Any:
    metrics = run.metrics if isinstance(run.metrics, dict) else {}
    return metrics.get(key)


def run_view(run: GenerationRun) -> RunView:
    """The ``RunView`` of one run."""
    manifest_row = _manifest_row(run)
    manifest = manifest_row.manifest if manifest_row is not None and isinstance(manifest_row.manifest, dict) else {}
    auth_mode = run.auth_mode
    parent = _parent(run) if not auth_mode else None
    if parent is not None:
        auth_mode = parent.auth_mode
    return RunView(
        id=str(run.pk),
        kind=run.kind,
        protocol=run.protocol,
        status=run.status,
        outcome=run.outcome,
        usable=run.usable,
        model_slug=_model_slug(run, manifest),
        harness=_harness(run, manifest),
        stack=_stack_ref(run),
        task=_task_ref(run, manifest),
        style_ref=run.style.ref if run.style is not None else None,
        auth_mode=auth_mode or None,
        guidance_ref=run.guidance.ref if run.guidance is not None else None,
        study_id=str(run.study_id) if run.study_id is not None else None,
        condition_id=run.condition_id,
        cell_id=run.cell_id,
        unit_key=_unit_key(run),
        repeat_index=run.repeat_index,
        stage=run.stage or None,
        seed=run.seed,
        fingerprints=dict(run.fingerprints),
        cost_usd=_metric(run, "cost_usd"),
        total_tokens=_metric(run, "total_tokens"),
        _run=run,
    )


def _with_related(runs: QuerySet[GenerationRun] | Iterable[GenerationRun]) -> list[GenerationRun]:
    from backend.generation.models import GenerationRun as Run

    if isinstance(runs, QuerySet):
        return list(runs.select_related(*_SELECT_RELATED))
    items = list(runs)
    by_id = Run.objects.select_related(*_SELECT_RELATED).in_bulk([run.pk for run in items])
    return [by_id[run.pk] for run in items if run.pk in by_id]


def run_views(runs: QuerySet[GenerationRun] | Iterable[GenerationRun]) -> list[RunView]:
    """``RunView`` of every run with one query (all treatment foreign keys joined; no N+1)."""
    return [run_view(run) for run in _with_related(runs)]


# ---------------------------------------------------------------------------
# Rows, scopes and blob reads (shared by the single and the bulk readers)
# ---------------------------------------------------------------------------


def _load_rows(run_ids: Iterable[str | None]) -> dict[str, list[RunFile]]:
    from backend.generation.models import RunFile

    ids = {str(run_id) for run_id in run_ids if run_id}
    grouped: dict[str, list[RunFile]] = {run_id: [] for run_id in ids}
    for row in RunFile.objects.filter(run_id__in=ids).order_by("run_id", "path"):
        grouped[str(row.run_id)].append(row)
    return grouped


def _select(scope: str, own: list[RunFile], parent: list[RunFile] | None) -> list[FileEntry]:
    """The entries of ``scope`` from a run's own rows (and, for ``app`` of a slot variant, its parent's)."""
    if scope == "model":
        entries = [FileEntry.from_row(row) for row in own if row.origin in _MODEL_ORIGINS and not row.excluded]
    elif scope == "slot":
        entries = [FileEntry.from_row(row) for row in own if row.origin == "slot"]
    else:
        tree: dict[str, FileEntry] = {}
        for row in parent or ():
            if row.origin not in _NOT_IN_TREE:
                tree[row.path] = FileEntry.from_row(row, PARENT_ORIGIN)
        for row in own:
            if row.origin in _NOT_IN_TREE:
                tree.pop(row.path, None)
            else:
                tree[row.path] = FileEntry.from_row(row)
        entries = list(tree.values())
    return sorted(entries, key=lambda entry: entry.path)


class _BlobCache:
    """Reads each distinct blob once per call (starter files and parent rows are shared between runs)."""

    def __init__(self) -> None:
        self._texts: dict[str, str] = {}

    def text(self, sha: str) -> str:
        if sha not in self._texts:
            from backend.generation import blobs

            self._texts[sha] = blobs.get_bytes(sha).decode("utf-8", errors="replace")
        return self._texts[sha]


def _read_texts(entries: Iterable[FileEntry], cache: _BlobCache) -> dict[str, str]:
    return {entry.path: cache.text(entry.blob) for entry in entries if entry.excluded != _BINARY and entry.blob}


def _bulk_entries(
    runs: QuerySet[GenerationRun] | Iterable[GenerationRun],
    scope: str,
) -> dict[str, list[FileEntry]]:
    """``{run_id: files(scope)}`` with one ``RunFile`` query (parents of slot variants included for ``app``)."""
    _check_scope(scope)
    if isinstance(runs, QuerySet):
        triples = list(runs.values_list("pk", "kind", "parent_run_id"))
    else:
        triples = [(run.pk, run.kind, run.parent_run_id) for run in runs]
    parents = {
        str(pk): str(parent_id)
        for pk, kind, parent_id in triples
        if scope == "app" and kind in _SLOT_KINDS and parent_id
    }
    rows = _load_rows([str(pk) for pk, _, _ in triples] + list(parents.values()))
    result: dict[str, list[FileEntry]] = {}
    for pk, _, _ in triples:
        run_id = str(pk)
        parent_id = parents.get(run_id)
        result[run_id] = _select(scope, rows.get(run_id, []), rows.get(parent_id, []) if parent_id else None)
    return result


def run_views_files(
    runs: QuerySet[GenerationRun] | Iterable[GenerationRun],
    scope: CodeScope = "model",
) -> dict[str, list[FileEntry]]:
    """Bulk ``RunView.files(scope)``: ``{run_id: [FileEntry, ...]}`` with one ``RunFile`` query.

    Same entries and order as ``files()`` (parent rows overlaid with origin ``parent`` for ``app`` of a
    slot variant). Every run is a key; a run without files maps to ``[]``.
    """
    return _bulk_entries(runs, scope)


def run_views_code(
    runs: QuerySet[GenerationRun] | Iterable[GenerationRun],
    scope: CodeScope = "model",
) -> dict[str, dict[str, str]]:
    """Bulk reader for statistics: ``{run_id: {path: text}}`` with one row query and batched blob reads.

    Every run is a key; a run without files maps to ``{}`` (the caller reports "not measured", never 0).
    """
    cache = _BlobCache()
    return {run_id: _read_texts(entries, cache) for run_id, entries in _bulk_entries(runs, scope).items()}


# ---------------------------------------------------------------------------
# Current runs (``runs.queries.current_runs``)
# ---------------------------------------------------------------------------


def current_runs(study: Study | Any, stage: str = "main") -> QuerySet[GenerationRun]:
    """The current run of every cell of ``study`` at ``stage`` (``runs.queries.current_runs``).

    Imported lazily: ``runs.queries`` imports the models at module level, ``runs.view`` must not.
    """
    from backend.generation.runs.queries import current_runs as _current_runs

    return _current_runs(study, stage)
