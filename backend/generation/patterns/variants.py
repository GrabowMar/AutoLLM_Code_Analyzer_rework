"""The variant dataset of a slot study: one record per planned main-stage cell (spec section 7.9).

A record carries the cell's identity (condition, target, repeat), its current run (the first usable attempt,
``runs.view.current_runs``), whether it produced usable output, the assembled slot file, the line span of
the regenerated part inside it, the normalized part (sha, tokens or the reason it did not parse), the probe
and check results of its latest evaluation and the manifest facts the analysis needs (``seed_forwarded``).

Denominators (ITT): every main-stage cell of the active plan is planned, except cells excluded because a
rule, a budget stop or a cancellation meant they never ran (``NOT_PLANNED_REASONS``): those reduce the
planned sample, they are not attrition. A planned cell without usable output is attrition (missing).
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from backend.generation import blobs
from backend.generation.patterns import normalize as norm
from backend.generation.patterns.detectors import Label
from backend.generation.patterns.detectors import VariantInput

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping
    from collections.abc import Sequence

    from backend.generation.models import GenerationRun
    from backend.generation.models import SlotTarget
    from backend.generation.models import Study
    from backend.generation.models import StudyCell

NOT_PLANNED_REASONS = frozenset({"stopped_by_rule", "budget_stop", "cancelled"})
# Usable outcomes that are model-attributable failures (spec section 6.13): data, with 0 pass in ITT.
MODEL_FAILURE_OUTCOMES = frozenset({"build_fail", "start_fail", "output_invalid"})


@dataclass
class TargetInfo:
    target: SlotTarget
    original_text: str | None  # the original part (``original_blob``)
    original_file: str | None  # the parent's whole slot file
    original_norm: norm.NormalizeResult | None

    @property
    def id(self) -> str:
        return str(self.target.pk)

    @property
    def span(self) -> tuple[int, int] | None:
        span = (self.target.address or {}).get("span")
        if not span:
            return None
        return int(span["start_line"]), int(span["end_line"])

    @property
    def path(self) -> str:
        return str((self.target.address or {}).get("path", ""))

    @property
    def discriminating_checks(self) -> list[str]:
        validation = self.target.validation or {}
        checks = validation.get("discriminating_checks")
        return list(checks if checks is not None else self.target.linked_checks or [])

    @property
    def informative(self) -> bool:
        status = (self.target.validation or {}).get("status")
        return status != "uninformative" and bool(self.discriminating_checks)


@dataclass
class VariantRecord:
    cell_id: int
    condition_id: int
    condition_index: int
    condition_label: str
    condition_levels: dict[str, Any]
    target_id: str
    repeat_index: int
    adaptive_n: bool
    run: GenerationRun | None = None
    has_output: bool = False
    parse_status: str = ""
    file_text: str | None = None
    span: tuple[int, int] | None = None
    part_text: str | None = None
    norm: norm.NormalizeResult | None = None
    probes: list[dict[str, Any]] | None = None
    checks: dict[str, str] | None = None  # check id -> status (None: not measured)
    flaky_checks: list[str] = field(default_factory=list)
    eval_status: str = ""
    seed_forwarded: bool | None = None
    model_slug: str = ""
    new_dependencies: list[str] = field(default_factory=list)
    out_of_slot_edits: list[dict[str, Any]] = field(default_factory=list)
    replacement_blob: str = ""
    outcome: str = ""
    usable: bool | None = None

    @property
    def run_id(self) -> str:
        return str(self.run.pk) if self.run is not None else ""

    @property
    def model_failed(self) -> bool:
        """A usable run whose outcome is a model-attributable failure (``build_fail``/``start_fail``/
        ``output_invalid``): it counts as data with 0 pass for correctness (ITT, spec section 6.13)."""
        return bool(self.usable) and self.outcome in MODEL_FAILURE_OUTCOMES

    @property
    def in_matrix(self) -> bool:
        """The record has a feature-matrix row: usable output, or a model-attributable failure."""
        return self.has_output or self.model_failed

    @property
    def normalized_sha(self) -> str:
        return self.norm.sha if self.norm is not None else ""


@dataclass
class Dataset:
    study: Study
    targets: dict[str, TargetInfo]
    records: list[VariantRecord]
    conditions: dict[int, dict[str, Any]]  # id -> {index, label, levels, seed_forwarded}

    def with_output(self) -> list[VariantRecord]:
        return [r for r in self.records if r.has_output]

    def in_matrix(self) -> list[VariantRecord]:
        return [r for r in self.records if r.in_matrix]

    def by_group(self) -> dict[tuple[int, str], list[VariantRecord]]:
        groups: dict[tuple[int, str], list[VariantRecord]] = defaultdict(list)
        for record in self.records:
            groups[(record.condition_id, record.target_id)].append(record)
        return groups


# ---------------------------------------------------------------------------
# Span arithmetic
# ---------------------------------------------------------------------------


def _lines(text: str) -> list[str]:
    return text.splitlines()


def locate_span(  # noqa: PLR0911
    original_file: str | None,
    original_span: tuple[int, int] | None,
    assembled: str,
) -> tuple[int, int] | None:
    """The 1-based line range of the regenerated part inside ``assembled``.

    ``None`` for a whole-file target. When the lines around the original span are unchanged (a splice),
    the part starts at the original start line and ends where the unchanged suffix begins; otherwise (an
    agent edited around it) the smallest range covering every changed line is used.
    """
    if original_span is None:
        return None
    new = _lines(assembled)
    if not new:
        return (1, 1)
    if original_file is None:
        start = min(original_span[0], len(new))
        return (start, max(start, min(len(new), original_span[1])))
    old = _lines(original_file)
    start, end = original_span
    prefix, suffix = old[: start - 1], old[end:]
    if new[: len(prefix)] == prefix and (not suffix or new[len(new) - len(suffix) :] == suffix):
        last = len(new) - len(suffix)
        if last < start:  # the part became empty: a zero-width span at its position
            return (start, start)
        return (start, last)
    changed: list[int] = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag != "equal":
            changed.extend(range(j1 + 1, max(j1 + 1, j2) + 1))
    if not changed:
        return (start, min(end, len(new)))
    return (max(1, min(changed)), min(len(new), max(changed)))


def part_of(assembled: str, span: tuple[int, int] | None) -> str:
    if span is None:
        return assembled
    lines = assembled.splitlines(keepends=True)
    return "".join(lines[span[0] - 1 : span[1]])


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _safe_text(sha: str) -> str | None:
    if not sha:
        return None
    try:
        return blobs.get_text(sha)
    except Exception:  # noqa: BLE001 - a missing blob is reported as "not readable", never as empty code
        return None


def _manifest_flag(run: GenerationRun) -> bool | None:
    manifest = getattr(run, "manifest", None)
    if manifest is None:
        return None
    sampling = (manifest.manifest or {}).get("sampling") or {}
    value = sampling.get("seed_forwarded")
    return bool(value) if value is not None else None


def _evaluation(run: GenerationRun) -> tuple[str, list[dict[str, Any]] | None, dict[str, str] | None, list[str]]:
    """``(status, probes, checks, flaky)`` of the run's latest evaluation; probes/checks ``None`` = not measured."""
    from backend.generation.runs.view import run_view

    evaluation = run_view(run).latest_evaluation()
    if evaluation is None:
        return "", None, None, []
    result = evaluation.result or {}
    if evaluation.status != "measured" or not result.get("measured"):
        return evaluation.status, None, None, []
    probes = [dict(p) for p in result.get("probes") or []]
    checks = {str(c["id"]): str(c["status"]) for c in result.get("checks") or [] if c.get("id")}
    return evaluation.status, probes, checks, list(result.get("flaky_checks") or [])


def target_scope(study: Study, targets: list[str] | str | None) -> Iterable[str] | None:
    if targets in (None, "all"):
        return None
    return [str(t) for t in targets]  # type: ignore[union-attr]


def _cells(study: Study, target_ids: Iterable[str] | None) -> list[StudyCell]:
    from backend.generation.models import StudyCell

    qs = StudyCell.objects.filter(study=study, stage="main", unit__slot_target__isnull=False).select_related(
        "condition",
        "unit",
        "unit__slot_target",
    )
    plan_id = study.active_plan_id
    if plan_id is None:
        plan_id = qs.order_by("-plan__created_at", "-pk").values_list("plan_id", flat=True).first()
    if plan_id is not None:
        qs = qs.filter(plan_id=plan_id)
    if target_ids is not None:
        qs = qs.filter(unit__slot_target_id__in=list(target_ids))
    cells = list(qs.order_by("condition__index", "unit__index", "repeat_index", "pk"))
    return [c for c in cells if not (c.state == "excluded" and c.exclusion_reason in NOT_PLANNED_REASONS)]


def load_targets(target_rows: Iterable[SlotTarget]) -> dict[str, TargetInfo]:
    from backend.generation.runs.view import run_views_code

    rows = list(target_rows)
    parents = {}
    for target in rows:
        parents[target.slot_set.parent_run_id] = target.slot_set.parent_run
    parent_code = run_views_code(list(parents.values()), "app") if parents else {}
    infos: dict[str, TargetInfo] = {}
    for target in rows:
        original_text = _safe_text(target.original_blob)
        original_file = parent_code.get(str(target.slot_set.parent_run_id), {}).get(
            str((target.address or {}).get("path", "")),
        )
        original_norm = norm.normalized_sha(original_text, target.language) if original_text is not None else None
        infos[str(target.pk)] = TargetInfo(
            target=target,
            original_text=original_text,
            original_file=original_file,
            original_norm=original_norm,
        )
    return infos


def build_record(
    cell: StudyCell,
    run: GenerationRun | None,
    info: TargetInfo,
    code: dict[str, str] | None,
    *,
    frozen: Mapping[str, Any] | None = None,
) -> VariantRecord:
    """One record; ``code`` is the run's app-scope tree (``run_views_code``).

    ``frozen`` is the record as a completed PatternRun analysed it (:func:`freeze`): its outcome, output and
    evaluation status are taken from there and the latest evaluation is not read.
    """
    condition = cell.condition
    record = VariantRecord(
        cell_id=cell.pk,
        condition_id=condition.pk,
        condition_index=condition.index,
        condition_label=condition.label,
        condition_levels=dict(condition.levels or {}),
        target_id=info.id,
        repeat_index=cell.repeat_index,
        adaptive_n=bool(cell.adaptive_n),
        run=run,
    )
    if run is None:
        return record
    record.model_slug = run.model.model_id if run.model_id else ""
    record.outcome = run.outcome or ""
    record.usable = run.usable
    record.seed_forwarded = _manifest_flag(run)
    try:
        result = run.variant_result
    except Exception:  # noqa: BLE001 - RelatedObjectDoesNotExist: a run without a variant result
        result = None
    if result is not None:
        record.parse_status = result.parse_status
        record.new_dependencies = list(result.new_dependencies or [])
        record.out_of_slot_edits = list(result.out_of_slot_edits or [])
        record.replacement_blob = result.replacement_blob
    record.has_output = bool(run.usable) and result is not None and result.parse_status == "ok"
    if frozen is not None:
        record.outcome = str(frozen.get("outcome") or "")
        record.usable = frozen.get("usable")
        record.has_output = bool(frozen.get("has_output"))
        record.parse_status = str(frozen.get("parse_status") or "")
    if not record.has_output:
        return record
    file_text = (code or {}).get(info.path)
    record.file_text = file_text
    if file_text is not None:
        record.span = locate_span(info.original_file, info.span, file_text)
        record.part_text = part_of(file_text, record.span)
        record.norm = norm.normalized_sha(record.part_text, info.target.language)
    else:
        record.norm = norm.NormalizeResult(sha="", tokens=(), reason="file_missing")
    if frozen is not None:
        record.eval_status = str(frozen.get("eval_status") or "")
        return record
    record.eval_status, record.probes, record.checks, record.flaky_checks = _evaluation(run)
    return record


def load(study: Study, targets: list[str] | str | None = "all") -> Dataset:
    """The dataset of ``study``'s slot variants (main stage, active plan), optionally limited to targets."""
    from backend.generation.models import GenerationRun
    from backend.generation.models import SlotTarget
    from backend.generation.runs.view import current_runs
    from backend.generation.runs.view import run_views_code

    cells = _cells(study, target_scope(study, targets))
    target_ids = {str(c.unit.slot_target_id) for c in cells}
    target_rows = SlotTarget.objects.filter(pk__in=target_ids).select_related("slot_set", "slot_set__parent_run")
    infos = load_targets(target_rows)
    runs = {
        r.cell_id: r
        for r in GenerationRun.objects.filter(pk__in=current_runs(study, "main"))
        .select_related("model", "manifest", "variant_result", "slot_target")
        .filter(cell_id__in=[c.pk for c in cells])
    }
    with_output = [r for r in runs.values() if r.usable]
    code = run_views_code(with_output, "app") if with_output else {}
    records = []
    for cell in cells:
        run = runs.get(cell.pk)
        tree = code.get(str(run.pk)) if run is not None else None
        records.append(build_record(cell, run, infos[str(cell.unit.slot_target_id)], tree))
    return _dataset(study, infos, records)


def _dataset(study: Study, infos: dict[str, TargetInfo], records: list[VariantRecord]) -> Dataset:
    conditions: dict[int, dict[str, Any]] = {}
    for record in records:
        entry = conditions.setdefault(
            record.condition_id,
            {
                "index": record.condition_index,
                "label": record.condition_label,
                "levels": record.condition_levels,
                "flags": [],
            },
        )
        if record.run is not None:
            entry["flags"].append(record.seed_forwarded)
    for entry in conditions.values():
        flags = entry.pop("flags")
        entry["seed_forwarded"] = bool(flags) and all(flag is True for flag in flags)
    return Dataset(study=study, targets=infos, records=records, conditions=conditions)


# ---------------------------------------------------------------------------
# The analysed record set of a completed PatternRun
# ---------------------------------------------------------------------------

FROZEN_FIELDS = ("cell", "run", "condition", "target", "repeat", "outcome", "usable", "has_output")


def freeze(dataset: Dataset) -> list[dict[str, Any]]:
    """The analysed records as stored in ``PatternRun.summary["records"]``: one entry per planned cell with
    the run it analysed (``""`` when none) and the facts the analysis read at that time."""
    return [
        {
            "cell": r.cell_id,
            "run": r.run_id,
            "condition": r.condition_id,
            "target": r.target_id,
            "repeat": r.repeat_index,
            "outcome": r.outcome,
            "usable": r.usable,
            "has_output": r.has_output,
            "parse_status": r.parse_status,
            "eval_status": r.eval_status,
        }
        for r in dataset.records
    ]


def load_frozen(study: Study, frozen: Sequence[Mapping[str, Any]]) -> Dataset:
    """The dataset of exactly the records a completed PatternRun analysed (never ``current_runs``).

    Cells and runs are fetched by the stored ids; a run's files are immutable, so the assembled file and
    the normalized part are re-derived from them; outcome, output and evaluation status come from the
    stored record (a later retest, infra retry, top-up or replicate changes nothing here).
    """
    from backend.generation.models import GenerationRun
    from backend.generation.models import SlotTarget
    from backend.generation.models import StudyCell
    from backend.generation.runs.view import run_views_code

    cell_rows = {
        c.pk: c
        for c in StudyCell.objects.filter(pk__in=[int(f["cell"]) for f in frozen]).select_related(
            "condition",
            "unit",
            "unit__slot_target",
        )
    }
    run_ids = [str(f["run"]) for f in frozen if f.get("run")]
    runs = {
        str(r.pk): r
        for r in GenerationRun.objects.filter(pk__in=run_ids).select_related(
            "model",
            "manifest",
            "variant_result",
            "slot_target",
        )
    }
    target_ids = {str(f["target"]) for f in frozen}
    infos = load_targets(
        SlotTarget.objects.filter(pk__in=target_ids).select_related("slot_set", "slot_set__parent_run"),
    )
    with_output = [runs[str(f["run"])] for f in frozen if f.get("has_output") and str(f.get("run")) in runs]
    code = run_views_code(with_output, "app") if with_output else {}
    records = []
    for entry in frozen:
        cell = cell_rows.get(int(entry["cell"]))
        info = infos.get(str(entry["target"]))
        if cell is None or info is None:
            msg = f"the analysed cell {entry['cell']} or target {entry['target']} no longer exists"
            raise LookupError(msg)
        run = runs.get(str(entry.get("run") or ""))
        if entry.get("run") and run is None:
            msg = f"the analysed run {entry['run']} no longer exists"
            raise LookupError(msg)
        tree = code.get(str(run.pk)) if run is not None else None
        records.append(build_record(cell, run, info, tree, frozen=entry))
    return _dataset(study, infos, records)


def labels_for(dataset: Dataset, manual_refs: Iterable[str]) -> dict[tuple[str, str], dict[str, list[Label]]]:
    """``{(target_id, normalized_sha): {detector ref: [Label]}}`` for the manual detectors."""
    from backend.generation.models import ClusterLabel
    from backend.generation.patterns.detectors import parse_ref

    pairs = [parse_ref(ref)[:2] for ref in manual_refs]
    if not pairs or not dataset.targets:
        return {}
    out: dict[tuple[str, str], dict[str, list[Label]]] = defaultdict(lambda: defaultdict(list))
    rows = ClusterLabel.objects.filter(slot_target_id__in=list(dataset.targets)).select_related("detector", "labeler")
    wanted = set(pairs)
    for row in rows:
        if (row.detector.slug, row.detector.version) not in wanted:
            continue
        ref = f"{row.detector.slug}@{row.detector.version}"
        out[(str(row.slot_target_id), row.normalized_sha)][ref].append(
            Label(value=row.value, labeler=row.labeler.get_username()),
        )
    return out


def variant_input(
    record: VariantRecord,
    info: TargetInfo,
    labels: dict[str, list[Label]] | None = None,
) -> VariantInput:
    file_error = record.file_text is not None and norm.has_parse_error(record.file_text, info.target.language)
    return VariantInput(
        run_id=record.run_id,
        path=info.path,
        language=info.target.language,
        file_text=record.file_text,
        span=record.span,
        normalized_sha=record.normalized_sha,
        parse_reason=record.norm.reason if record.norm is not None else "",
        file_parse_error=file_error,
        probes=record.probes,
        linked_probes=list(info.target.linked_probes or []),
        labels=dict(labels or {}),
    )
