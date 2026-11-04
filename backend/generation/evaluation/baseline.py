"""Starter baselines, task oracles and reference runs (spec section 8.5).

* ``reference_run(...)``: a ``reference`` run (``ref@1``) whose tree is given (a starter's files, a task
  oracle tree under ``data/tasks/<slug>/reference/<stack>/``, a conformance kit app); it carries the suite and
  level it is evaluated under in ``execution`` (``eval_suite``, ``eval_level``).
* ``ensure_baseline(starter, task_version, auth_mode, suite)``: the stored ``EvalBaseline``, evaluating the bare
  starter inline when it is missing (``queue_baseline`` does the same through the eval queue, for launch).
  Only a **measured** baseline evaluation becomes an ``EvalBaseline`` row: a starter that does not build or
  start gives no baseline, so ``baseline_pass_rate`` stays ``None`` (never 0).
* ``queue_oracle_runs(task_version, stacks, *, user)``: one ``oracle`` reference run per stack with a reference
  tree, enqueued for evaluation on commit (``POST /library/tasks/{slug}/versions/{v}/oracle/``, WP-03).
  Stacks without a reference tree are recorded ``missing`` in ``TaskSpecVersion.oracle``.
* ``on_reference_evaluated(run, evaluation)`` (eval stage hook): writes the ``EvalBaseline`` row of a measured
  starter baseline, and the ``TaskSpecVersion.oracle`` entry of an oracle (key: stack pinned ref) or
  conformance kit run (key ``kit:<stack pinned ref>``; ``pass`` needs ``agent_pass_rate == 1.0`` and no
  exploited probe).

``oracle_validate`` (3 runs, flaky list, mutants) and ``conformance_kit`` live in ``conformance`` and the
management commands; they use these helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.db import transaction
from django.utils import timezone

from backend.generation.models import EvalBaseline
from backend.generation.models import EvalSuite
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import RunFile
from backend.generation.models import Starter
from backend.generation.models import TaskSpecVersion
from backend.generation.models.choices import EvaluationStatus
from backend.generation.models.choices import FileOrigin
from backend.generation.models.choices import ReferenceOf
from backend.generation.models.choices import RunKind
from backend.generation.models.choices import RunStatus

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping

    from backend.generation.evaluation.runtime import EvalRuntime
    from backend.generation.models import StackProfile
    from backend.users.models import User

__all__ = [
    "TASKS_ROOT",
    "ensure_baseline",
    "on_reference_evaluated",
    "oracle_key",
    "queue_baseline",
    "queue_oracle_runs",
    "reference_run",
    "reference_tree",
    "starter_tree",
]

TASKS_ROOT: Final = Path(__file__).resolve().parent.parent / "data" / "tasks"
_SKIP: Final = frozenset({"PROVENANCE.md"})


def _language(path: str) -> str:
    return {".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript"}.get(
        Path(path).suffix,
        "",
    )


def starter_tree(starter: Starter) -> dict[str, tuple[bytes, int]]:
    """``{path: (bytes, mode)}`` of a starter's files (``StarterContent.files``)."""
    from backend.generation import blobs

    files = (starter.content or {}).get("files") or {}
    return {path: (blobs.get_bytes(row["blob"]), int(row.get("mode", 0o644))) for path, row in files.items()}


def reference_tree(
    task_slug: str,
    stack_slug: str,
    *,
    root: Path | None = None,
) -> dict[str, tuple[bytes, int]] | None:
    """The oracle tree ``data/tasks/<slug>/reference/<stack_slug>/`` (``None`` when missing)."""
    base = (root or TASKS_ROOT) / task_slug / "reference" / stack_slug
    if not base.is_dir():
        return None
    out: dict[str, tuple[bytes, int]] = {}
    for path in sorted(base.rglob("*")):
        if path.is_file() and path.name not in _SKIP and "__pycache__" not in path.parts:
            out[path.relative_to(base).as_posix()] = (path.read_bytes(), path.stat().st_mode & 0o7777 or 0o644)
    return out or None


def reference_run(
    *,
    reference_of: str,
    task_version: TaskSpecVersion,
    stack: StackProfile | None,
    starter: Starter | None,
    auth_mode: str,
    files: Mapping[str, tuple[bytes, int]],
    suite: EvalSuite,
    eval_level: str = "security",
    user: User | None = None,
    origin: str = FileOrigin.AUTHORED,
    extra: Mapping[str, Any] | None = None,
) -> GenerationRun:
    """A saved ``reference`` run with the given tree (``RunFile`` rows, ``tree_hash``); not yet enqueued."""
    from backend.generation import blobs
    from backend.generation import identity

    run = GenerationRun(
        kind=RunKind.REFERENCE,
        reference_of=reference_of,
        status=RunStatus.PENDING,
        task_version=task_version,
        stack_profile=stack,
        starter=starter,
        auth_mode=auth_mode,
        execution={"eval_suite": suite.pinned_ref, "eval_level": eval_level, **dict(extra or {})},
        created_by=user,
    )
    run.save()
    rows = []
    for path, (data, mode) in sorted(files.items()):
        sha = blobs.put_bytes(data)
        rows.append(
            RunFile(run=run, path=path, blob=sha, size=len(data), mode=mode, origin=origin, language=_language(path)),
        )
    RunFile.objects.bulk_create(rows)
    run.tree_hash = identity.tree_hash((r.path, r.mode, r.blob) for r in rows)
    run.save(update_fields=["tree_hash"])
    return run


def _baseline_run(starter: Starter, task_version: TaskSpecVersion, auth_mode: str, suite: EvalSuite) -> GenerationRun:
    stack = starter.stack_profile if hasattr(starter, "stack_profile") else None
    return reference_run(
        reference_of=ReferenceOf.STARTER_BASELINE,
        task_version=task_version,
        stack=stack,
        starter=starter,
        auth_mode=auth_mode,
        files=starter_tree(starter),
        suite=suite,
        eval_level="functional",
        origin=FileOrigin.STARTER,
    )


def ensure_baseline(
    starter: Starter,
    task_version: TaskSpecVersion,
    auth_mode: str,
    suite: EvalSuite,
    *,
    runtime: EvalRuntime | None = None,
) -> EvalBaseline | None:
    """The stored starter baseline, evaluating the bare starter inline when missing.

    ``None`` when the starter's evaluation was not measured (it did not build or start): there is then no
    baseline, and ``baseline_pass_rate`` stays ``None``.
    """
    from backend.generation.evaluation import suite as suite_mod

    found = EvalBaseline.objects.filter(
        starter=starter,
        task_version=task_version,
        auth_mode=auth_mode,
        eval_suite=suite,
    ).first()
    if found is not None:
        return found
    run = _baseline_run(starter, task_version, auth_mode, suite)
    evaluation = suite_mod.evaluate(run, suite, eval_level="functional", runtime=runtime)
    outcome, reason = suite_mod.outcome_of(evaluation)
    GenerationRun.objects.filter(pk=run.pk).update(
        status=RunStatus.COMPLETED,
        outcome=outcome,
        outcome_reason=reason,
        usable=None,
        completed_at=timezone.now(),
    )
    return _store_baseline(run, evaluation)


def queue_baseline(
    starter: Starter,
    task_version: TaskSpecVersion,
    auth_mode: str,
    suite: EvalSuite,
) -> GenerationRun | None:
    """Create and enqueue (on commit) the baseline run when no baseline exists; ``None`` when one exists."""
    from backend.execution import api as execution_api

    if EvalBaseline.objects.filter(
        starter=starter,
        task_version=task_version,
        auth_mode=auth_mode,
        eval_suite=suite,
    ).exists():
        return None
    run = _baseline_run(starter, task_version, auth_mode, suite)
    run_id = run.pk
    transaction.on_commit(lambda: execution_api.enqueue_evaluation([run_id]))
    return run


def _store_baseline(run: GenerationRun, evaluation: RunEvaluation) -> EvalBaseline | None:
    result = evaluation.result or {}
    rate = (result.get("metrics") or {}).get("agent_pass_rate")
    if evaluation.status != EvaluationStatus.MEASURED or not result.get("measured") or rate is None:
        return None
    passed = sorted(
        c["id"] for c in result.get("checks") or [] if c.get("status") == "pass" and c.get("category") != "infra"
    )
    row, _ = EvalBaseline.objects.get_or_create(
        starter=run.starter,
        task_version=run.task_version,
        auth_mode=run.auth_mode,
        eval_suite=evaluation.eval_suite,
        defaults={"reference_run": run, "agent_pass_rate": float(rate), "checks_passed": passed},
    )
    return row


def oracle_key(stack: StackProfile, *, kit: bool = False) -> str:
    return f"kit:{stack.pinned_ref}" if kit else stack.pinned_ref


def _set_oracle(task_version: TaskSpecVersion, key: str, entry: dict[str, Any]) -> None:
    with transaction.atomic():
        row = TaskSpecVersion.objects.select_for_update().get(pk=task_version.pk)
        oracle = dict(row.oracle or {})
        oracle[key] = entry
        row.oracle = oracle
        row.save(update_fields=["oracle"])


def _oracle_status(evaluation: RunEvaluation, *, kit: bool) -> str:
    result = evaluation.result or {}
    if evaluation.status == EvaluationStatus.ERROR:
        return "error"
    if evaluation.status != EvaluationStatus.MEASURED or not result.get("measured"):
        return "fail"
    metrics = result.get("metrics") or {}
    ok = metrics.get("agent_pass_rate") == 1.0
    if kit:
        ok = ok and not any(p.get("status") == "exploited" for p in result.get("probes") or [])
    return "pass" if ok else "fail"


def on_reference_evaluated(run: GenerationRun, evaluation: RunEvaluation) -> None:
    """Eval-stage hook for starter-baseline, oracle and conformance reference runs."""
    if run.reference_of == ReferenceOf.STARTER_BASELINE:
        _store_baseline(run, evaluation)
        return
    if run.stack_profile is None or run.task_version is None:
        return
    kit = run.reference_of == ReferenceOf.CONFORMANCE
    if run.reference_of not in (ReferenceOf.ORACLE, ReferenceOf.CONFORMANCE):
        return
    if (run.execution or {}).get("validation_round"):
        return  # oracle_validate aggregates its 3 rounds itself
    entry = {
        "status": _oracle_status(evaluation, kit=kit),
        "run_id": str(run.pk),
        "eval_suite_ref": evaluation.eval_suite.pinned_ref,
        "at": timezone.now().isoformat(),
    }
    _set_oracle(run.task_version, oracle_key(run.stack_profile, kit=kit), entry)


def _stack_slug(stack: StackProfile) -> str:
    return stack.slug


def queue_oracle_runs(
    task_version: TaskSpecVersion,
    stacks: Iterable[StackProfile],
    *,
    user: User | None = None,
    suite: EvalSuite | None = None,
    auth_mode: str = "contract_bearer",
) -> list[str]:
    """One oracle reference run per stack that has a reference tree (enqueued on commit); returns run ids.

    A stack without ``data/tasks/<slug>/reference/<stack slug>/`` is recorded ``missing``.
    """
    from backend.execution import api as execution_api

    suite = suite or EvalSuite.objects.filter(is_archived=False).order_by("-version").first()
    if suite is None:
        msg = "no evaluation suite is seeded"
        raise LookupError(msg)
    queued: list[str] = []
    for stack in stacks:
        tree = reference_tree(task_version.task.slug, _stack_slug(stack))
        if tree is None:
            _set_oracle(
                task_version,
                oracle_key(stack),
                {"status": "missing", "eval_suite_ref": suite.pinned_ref, "at": timezone.now().isoformat()},
            )
            continue
        run = reference_run(
            reference_of=ReferenceOf.ORACLE,
            task_version=task_version,
            stack=stack,
            starter=None,
            auth_mode=auth_mode,
            files=tree,
            suite=suite,
            eval_level="security",
            user=user,
        )
        queued.append(str(run.pk))
    if queued:
        ids = list(queued)
        transaction.on_commit(lambda: execution_api.enqueue_evaluation(ids))
    return queued
