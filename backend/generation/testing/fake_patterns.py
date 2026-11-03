"""A synthetic slot study and a fake slot executor for pattern tests (WP-13's area fake).

``build_slot_study`` creates a slot study the way WP-10/WP-12 would leave it after launch: a frozen
parent per target (a tested app run with the target's file as a ``RunFile``), the ``SlotTarget`` rows, a
launched plan with conditions, units and main-stage cells. ``FakeSlotExecutor.produce`` then plays the
executor for one cell: it splices the given part into the parent file, writes the variant run (kind
``slot_single_shot``), its ``slot`` RunFile, ``SlotVariantResult``, ``RunManifest`` (``seed_forwarded`` as
asked) and a measured ``RunEvaluation`` with the given check and probe results. No LLM, docker or network.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

from django.utils import timezone

from backend.generation import blobs
from backend.generation import identity
from backend.generation.models import EvalSuite
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import RunFile
from backend.generation.models import RunManifest
from backend.generation.models import SlotTarget
from backend.generation.models import SlotVariantResult
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models import StudyCondition
from backend.generation.testing import content as samples
from backend.generation.testing import factories as f

PARENT_MODEL = "parent/model-a"


@dataclass
class TargetSpec:
    """One part of one parent: ``span`` = 1-based inclusive lines of ``file_text`` (``None``: whole file)."""

    path: str
    file_text: str
    span: tuple[int, int] | None = None
    language: str = "python"
    discriminating_checks: tuple[str, ...] = ("c1",)
    linked_probes: tuple[str, ...] = ()
    leak_score: float = 0.0
    match_key: str = ""


@dataclass
class SyntheticSlotStudy:
    study: Study
    user: Any
    targets: list[SlotTarget]
    specs: list[TargetSpec]
    conditions: list[StudyCondition]
    cells: dict[tuple[int, int, int], StudyCell]
    suite: EvalSuite
    runs: dict[tuple[int, int, int], GenerationRun] = field(default_factory=dict)


def _part(spec: TargetSpec) -> str:
    if spec.span is None:
        return spec.file_text
    lines = spec.file_text.splitlines(keepends=True)
    return "".join(lines[spec.span[0] - 1 : spec.span[1]])


def splice(spec: TargetSpec, part: str) -> str:
    """The parent file with ``part`` in place of the original span (or the whole file)."""
    if spec.span is None:
        return part
    lines = spec.file_text.splitlines(keepends=True)
    if part and not part.endswith("\n"):
        part += "\n"
    return "".join(lines[: spec.span[0] - 1]) + part + "".join(lines[spec.span[1] :])


def _parent(spec: TargetSpec, user: Any) -> GenerationRun:
    parent = f.GenerationRunFactory.create(tested=True, created_by=user)
    sha = blobs.put_text(spec.file_text)
    f.RunFileFactory.create(
        run=parent,
        path=spec.path,
        blob=sha,
        size=len(spec.file_text.encode()),
        origin="authored",
        language=spec.language,
    )
    return parent


def _target(spec: TargetSpec, user: Any) -> SlotTarget:
    parent = _parent(spec, user)
    slot_set = f.SlotSetFactory.create(parent_run=parent, parent_model=PARENT_MODEL, created_by=user)
    original = _part(spec)
    original_sha = blobs.put_text(original)
    address: dict[str, Any] = {"kind": "file", "path": spec.path, "content_sha": identity.text_sha(original)}
    if spec.span is not None:
        start, end = spec.span
        prefix = "".join(spec.file_text.splitlines(keepends=True)[: start - 1]).encode()
        address = {
            "kind": "region",
            "path": spec.path,
            "span": {
                "start_line": start,
                "end_line": end,
                "start_byte": len(prefix),
                "end_byte": len(prefix) + len(original.encode()),
            },
            "content_sha": identity.text_sha(original),
        }
    return f.SlotTargetFactory.create(
        slot_set=slot_set,
        label=f"{spec.path}:{spec.span}",
        address=address,
        language=spec.language,
        original_sha=original_sha,
        original_blob=original_sha,
        allowlist=[spec.path],
        linked_checks=list(spec.discriminating_checks),
        linked_probes=list(spec.linked_probes),
        validation={"status": "ok", "discriminating_checks": list(spec.discriminating_checks)},
        leak={"score": spec.leak_score, "matches": [], "scope": ["context"], "version": 1},
        match_key=spec.match_key,
        created_by=user,
    )


def build_slot_study(
    targets: list[TargetSpec],
    *,
    n_conditions: int = 2,
    repeats: int = 3,
    kind: str = "exploratory",
    status: str = "completed",
    user: Any = None,
    primary_features: list[str] | None = None,
) -> SyntheticSlotStudy:
    from backend.users.tests.factories import UserFactory

    user = user or UserFactory.create()
    policy = f.ResourcePolicyFactory.create()
    design = samples.design_content(resource_policy=policy.ref, mode="slot", kind=kind, repeats=repeats)
    if primary_features:
        design["slot_primary_features"] = list(primary_features)
    if kind == "confirmatory":
        design["analysis_plan"] = {"itt": True}
    study = f.StudyFactory.create(resource_policy=policy, design=design, created_by=user, status=status)
    plan = f.StudyPlanFactory.create(study=study, status="launched")
    Study.objects.filter(pk=study.pk).update(active_plan=plan)
    study.refresh_from_db()
    suite = EvalSuite.objects.filter(slug="eval-v2", version=1).first() or f.EvalSuiteFactory.create(
        slug="eval-v2",
        version=1,
    )
    target_rows = [_target(spec, user) for spec in targets]
    conditions = [f.StudyConditionFactory.create(plan=plan, index=i, label=f"cond-{i}") for i in range(n_conditions)]
    units = [
        f.StudyUnitFactory.create(plan=plan, index=i, task_version=None, slot_target=t, unit_key=t.unit_key)
        for i, t in enumerate(target_rows)
    ]
    cells: dict[tuple[int, int, int], StudyCell] = {}
    for c, condition in enumerate(conditions):
        for t, unit in enumerate(units):
            for r in range(repeats):
                cells[(c, t, r)] = f.StudyCellFactory.create(
                    condition=condition,
                    unit=unit,
                    repeat_index=r,
                    stage="main",
                    state="done",
                )
    return SyntheticSlotStudy(
        study=study,
        user=user,
        targets=target_rows,
        specs=list(targets),
        conditions=conditions,
        cells=cells,
        suite=suite,
    )


class FakeSlotExecutor:
    """Produces variant runs for the cells of a :class:`SyntheticSlotStudy`."""

    def __init__(self, synthetic: SyntheticSlotStudy) -> None:
        self.s = synthetic

    def produce(
        self,
        condition: int,
        target: int,
        repeat: int,
        part: str | None,
        *,
        parse_status: str = "ok",
        checks: dict[str, str] | None = None,
        probes: list[dict[str, Any]] | None = None,
        seed_forwarded: bool = True,
        evaluated: bool = True,
        new_dependencies: list[str] | None = None,
        model: Any = None,
        outcome: str | None = None,
    ) -> GenerationRun:
        """One variant; ``part=None`` (or a parse status other than ``ok``) gives an ``output_invalid`` run.

        ``outcome="build_fail"``/``"start_fail"`` gives a parsed variant whose evaluation stopped at that
        stage (status ``measured``, no check results)."""
        s = self.s
        cell = s.cells[(condition, target, repeat)]
        slot_target = s.targets[target]
        spec = s.specs[target]
        ok = part is not None and parse_status == "ok"
        run = f.GenerationRunFactory.create(
            slot_single_shot=True,
            slot_target=slot_target,
            study=s.study,
            condition=s.conditions[condition],
            cell=cell,
            cell_attempt=1,
            stage="main",
            repeat_index=repeat,
            model=model or s.conditions[condition].model,
            status="completed",
            outcome=outcome or ("tested" if ok else "output_invalid"),
            started_at=timezone.now(),
            completed_at=timezone.now(),
            created_by=s.user,
        )
        manifest = f.run_manifest_content(run)
        manifest["sampling"]["seed_forwarded"] = seed_forwarded
        RunManifest.objects.create(run=run, manifest=manifest)
        replacement_sha = ""
        if ok:
            assembled = splice(spec, part)
            file_sha = blobs.put_text(assembled)
            RunFile.objects.create(
                run=run,
                path=spec.path,
                blob=file_sha,
                size=len(assembled.encode()),
                origin="slot",
                language=spec.language,
            )
            replacement_sha = blobs.put_text(part)
        SlotVariantResult.objects.create(
            run=run,
            slot_target=slot_target,
            parse_status=parse_status if part is not None else "invalid_json",
            replacement_blob=replacement_sha,
            replacement_sha=replacement_sha,
            new_dependencies=list(new_dependencies or []),
        )
        if ok and evaluated and outcome in {"build_fail", "start_fail"}:
            failed = "build" if outcome == "build_fail" else "start"
            stages = {"lock": {"status": "ok"}, "build": {"status": "ok" if failed == "start" else "fail"}}
            if failed == "start":
                stages["start"] = {"status": "fail"}
            result = samples.eval_result_content()
            result.update({"measured": False, "checks": [], "probes": []})
            RunEvaluation.objects.create(
                run=run,
                eval_suite=s.suite,
                eval_level="security",
                status="measured",
                result=result,
                stages=stages,
            )
        elif ok and evaluated:
            result = samples.eval_result_content()
            result["checks"] = [
                {"id": cid, "category": "functional", "status": status} for cid, status in (checks or {}).items()
            ]
            result["probes"] = list(probes or [])
            RunEvaluation.objects.create(
                run=run,
                eval_suite=s.suite,
                eval_level="security",
                status="measured",
                result=result,
                stages={stage: {"status": "ok"} for stage in ("lock", "build", "start", "tester")},
            )
        s.runs[(condition, target, repeat)] = run
        return run
