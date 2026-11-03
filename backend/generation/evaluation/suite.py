"""EvalSuite v2 orchestration: lock, build, start, tester, probes, adherence (spec sections 4.8, 6.10, 6.11, 8).

``evaluate(run, suite, *, only_checks, probes, mode, image_override, overlay_files)`` writes one
``RunEvaluation`` (append-only; a WP-08 *pending* re-evaluation row is filled instead of creating a second
one). The stages follow the run's evaluation spec (``Design.evaluation`` of its study, else the manifest's
``planned_evaluation``; ``levels.stages_for``):

1. **lock** (``lock.resolve_lock``): ``lock_unresolvable`` -> ``build_fail``; a lock outage -> ``infra_error``.
2. **build** (``sample_builds.build_sample`` through the runtime; never for ``output_invalid``): retried once
   when the failure looks like infra (``infra_suspected``); a repeat is ``infra_error``. A free-choice
   ``FROM`` outside the allowlist is ``build_fail:base_not_allowed`` (model-attributable, not retried); an
   allowlist that cannot be reproduced (``allowed_bases_changed``), a variant that cannot be assembled and an
   incomplete lock left in the context (``lock_incomplete``, WP-05) are ``infra_error``, never the model's.
   ``mode=overlay`` skips lock and build: the parent image runs with the slot files copied in.
3. **start** with a fresh ``ADMIN_PASSWORD``/``SECRET_KEY`` (encrypted record, ``stages.start.detail.
   credentials_ref``, 8.6), ``WEBHOOK_SECRET``, no egress; not healthy within ``start_s`` -> ``start_fail``.
4. **tester** (``acceptance.compile`` -> eval-tester; persistence checks restart the app between phases). A
   tester crash is retried once *inside this stage* (``transitions.infra_retry`` would wipe the collected
   tree); if it repeats, the evaluation is ``error`` and the outcome ``infra_error`` -- never a pass rate of 0.
   ``stages.tester.detail`` holds ``reuse.selection_detail`` (the always-on infra checks run even with
   ``only_checks``).
5. **probes** run inside the tester when the level includes them; unvalidated kinds are ``skipped(disabled)``,
   a level without probes lists the task's probes as ``skipped(level)``. ``plaintext_password_db`` is read here
   from the app's data directory after the run.
6. **adherence** (``adherence.check_adherence``) with what the tester saw (login cookie vs bearer).
7. **baseline**: ``EvalBaseline`` of (starter, task version, auth mode, suite) -> ``baseline_pass_rate`` /
   ``over_baseline`` (``None`` when there is no baseline yet; never 0).

``run_eval_stage(run)`` is the eval worker's entry point: suite and level selection, the slot dispatch
(``slot.evaluate.evaluate_variant``; ``slot.validate.on_reference_evaluated`` for validation references),
eval reuse by tree, the outcome (``outcome_of``), validity notes (``lock_source:*``, ``adherence:*``,
``eval_reused``), the ``metrics.eval`` mirror (with ``slot_pass_rate``) and ``finish_stage``.

Levels without the tester (``none``, ``static``, ``build``) end ``tested`` with ``outcome_reason
not_tested:<level>`` and a result that says ``measured=false``: the pass rate is *not measured*, never 0.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import secrets
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from backend.generation.contracts.evaluation import EvalResult
from backend.generation.contracts.plan import EvaluationSpec
from backend.generation.models import EvalSuite
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models.choices import EvalMode
from backend.generation.models.choices import EvaluationStatus
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import ReferenceOf
from backend.generation.models.choices import RunKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.generation.evaluation.runtime import EvalRuntime

logger = logging.getLogger(__name__)

__all__ = ["evaluate", "outcome_of", "run_eval_stage", "spec_for", "suite_for"]

_SLOT_KINDS: Final = frozenset({RunKind.SLOT_SINGLE_SHOT, RunKind.SLOT_AGENT})
_SLOT_REFERENCES: Final = frozenset({ReferenceOf.SLOT_ORIGINAL, ReferenceOf.SLOT_HOLED})
_SUITE_KEY: Final = "eval_suite"  # execution key of reference runs created by evaluation/baseline.py
_LEVEL_KEY: Final = "eval_level"
DEFAULT_LEVEL: Final = "security"
TESTER_ATTEMPTS: Final = 2
BUILD_ATTEMPTS: Final = 2
# A pending re-evaluation row (WP-08) for the run being evaluated; picked up by ``evaluate`` even when the
# call comes through the slot dispatch.
_PENDING_ROW: contextvars.ContextVar[RunEvaluation | None] = contextvars.ContextVar("wp09_pending_row", default=None)


class _Stop(Exception):  # noqa: N818 - control flow inside one evaluation
    def __init__(self, *, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


# =====================================================================================
# Suite, spec and inputs
# =====================================================================================


def _manifest(run: GenerationRun) -> dict[str, Any]:
    from backend.generation.models import RunManifest

    return RunManifest.objects.filter(run_id=run.pk).values_list("manifest", flat=True).first() or {}


def _suite_from_ref(ref: str) -> EvalSuite:
    from backend.generation.contracts import Ref

    parsed = Ref.parse(ref)
    qs = EvalSuite.objects.filter(slug=parsed.slug, version=parsed.version)
    if parsed.hash12:
        qs = qs.filter(content_hash__startswith=parsed.hash12)
    suite = qs.first()
    if suite is None:
        msg = f"eval suite {ref} not found"
        raise LookupError(msg)
    return suite


def suite_for(run: GenerationRun) -> EvalSuite:
    """The suite of the run's next evaluation: re-evaluation pin, reference pin, manifest, study design."""
    execution = run.execution or {}
    for key in ("reeval_suite", _SUITE_KEY, "slot_validation_suite"):
        if execution.get(key):
            return _suite_from_ref(str(execution[key]))
    planned = (_manifest(run).get("planned_evaluation") or {}).get("eval_suite") or {}
    if planned.get("ref"):
        ref = str(planned["ref"])
        if "#" not in ref and planned.get("content_hash"):
            ref = f"{ref}#{str(planned['content_hash'])[:12]}"
        return _suite_from_ref(ref)
    if run.study_id is not None:
        ref = ((run.study.design or {}).get("evaluation") or {}).get("eval_suite")
        if ref:
            return _suite_from_ref(str(ref))
    msg = f"run {run.pk} has no evaluation suite"
    raise LookupError(msg)


def spec_for(run: GenerationRun, suite: EvalSuite, eval_level: str | None = None) -> EvaluationSpec:
    """The evaluation spec of ``run``: its study's ``Design.evaluation`` (overrides, slot rules), the manifest's
    planned level, the reference pin, else ``security``; ``eval_level`` overrides the level."""
    data: dict[str, Any] = {}
    if run.study_id is not None:
        data = dict(((run.study.design or {}).get("evaluation")) or {})
    planned = _manifest(run).get("planned_evaluation") or {}
    level = eval_level or (run.execution or {}).get(_LEVEL_KEY) or planned.get("eval_level") or data.get("eval_level")
    data["eval_level"] = level or DEFAULT_LEVEL
    data["eval_suite"] = suite.pinned_ref
    if run.kind not in _SLOT_KINDS and run.reference_of not in _SLOT_REFERENCES:
        data.pop("slot", None)
    return EvaluationSpec.model_validate(data)


def _inherited(run: GenerationRun, name: str) -> Any:
    value = getattr(run, name)
    if value is None and run.parent_run_id is not None:
        value = getattr(run.parent_run, name)
    return value


def _auth_mode(run: GenerationRun) -> str:
    mode = run.auth_mode or (run.parent_run.auth_mode if run.parent_run_id else "")
    return mode or "contract_bearer"


# =====================================================================================
# One evaluation
# =====================================================================================


class _Evaluation:
    def __init__(
        self,
        run: GenerationRun,
        suite: EvalSuite,
        row: RunEvaluation,
        spec: EvaluationSpec,
        runtime: EvalRuntime,
        *,
        only_checks: list[str] | None,
        probes: list[str] | None,
        mode: str,
        image_override: str | None,
        overlay_files: dict[str, str] | None,
        force_rebuild: bool,
        cancelled: Callable[[], bool] | None,
    ) -> None:
        from backend.generation.evaluation import levels

        self.run, self.suite, self.row, self.spec, self.runtime = run, suite, row, spec, runtime
        self.content = dict(suite.content or {})
        self.on = levels.stages_for(spec)
        if mode == EvalMode.STATIC_ONLY:
            for name in ("build", "functional", "probes", "baseline", "analysis_dynamic", "adherence"):
                self.on[name] = False
        self.only_checks, self.probe_ids, self.mode = only_checks, probes, mode
        self.image_override, self.overlay_files = image_override, overlay_files
        self.force_rebuild, self.cancelled = force_rebuild, cancelled
        self.stages: dict[str, Any] = {}
        self.checks: list[dict[str, Any]] = []
        self.probe_rows: list[dict[str, Any]] = []
        self.observed: dict[str, Any] = {}
        self.image = image_override or ""
        self.measured = False
        self.not_measured_reason: str | None = None
        self.tester_versions: dict[str, Any] = {}
        timeouts = self.content.get("timeouts") or {}
        self.start_s = float(timeouts.get("start_s", 90))
        self.tester_s = float(timeouts.get("tester_s", 600))

    # helpers ------------------------------------------------------------------------------

    def _stage(self, name: str, status: str, started: float | None = None, **detail: Any) -> None:
        record: dict[str, Any] = {"status": status, "detail": detail}
        if started is not None:
            record["seconds"] = round(time.monotonic() - started, 3)
        self.stages[name] = record

    def _save_progress(self) -> None:
        self.row.stages = dict(self.stages)
        RunEvaluation.objects.filter(pk=self.row.pk).update(stages=self.row.stages)

    # stages -------------------------------------------------------------------------------

    def lock(self) -> None:
        from backend.generation.evaluation import lock as lock_mod

        if self.mode == EvalMode.OVERLAY:
            self._stage("lock", "skipped", reason="overlay", note="the parent image carries the parent's lock")
            return
        if not self.on["lock"]:
            return
        started = time.monotonic()
        try:
            resolved = lock_mod.resolve_lock(self.run)
        except lock_mod.LockUnresolvableError as exc:
            self._stage(
                "lock",
                "fail",
                started,
                **{**exc.detail, "reason": lock_mod.LOCK_UNRESOLVABLE, "error": str(exc)},
            )
            raise _Stop(status="build_fail", reason=lock_mod.LOCK_UNRESOLVABLE) from exc
        except lock_mod.LockInfraError as exc:
            self._stage("lock", "error", started, reason=exc.reason, error=str(exc)[:2000])
            raise _Stop(status="infra", reason=exc.reason) from exc
        self._stage("lock", "ok", started, **lock_mod.lock_summary(resolved, self.run.lock_source))

    def build(self) -> None:
        from backend.generation.build import context as build_context
        from backend.generation.evaluation.runtime import RuntimeInfraError

        if not self.on["build"]:
            return
        if self.mode == EvalMode.OVERLAY:
            if not self.image_override:
                self._stage("build", "error", reason="overlay_without_image")
                raise _Stop(status="infra", reason="overlay_without_image")
            self._stage("build", "skipped", reason="overlay", image=self.image_override)
            return
        started = time.monotonic()
        attempts: list[dict[str, Any]] = []
        outcome = None
        for attempt in range(1, BUILD_ATTEMPTS + 1):
            try:
                outcome = self.runtime.build(self.run, force_rebuild=self.force_rebuild, cancelled=self.cancelled)
            except build_context.BaseNotAllowedError as exc:
                self._stage(
                    "build",
                    "fail",
                    started,
                    reason=build_context.BASE_NOT_ALLOWED,
                    bases=exc.check,
                    error=str(exc),
                )
                raise _Stop(status="build_fail", reason=build_context.BASE_NOT_ALLOWED) from exc
            except build_context.AllowedBasesChangedError as exc:
                self._stage("build", "error", started, reason=build_context.ALLOWED_BASES_CHANGED, error=str(exc))
                raise _Stop(status="infra", reason=build_context.ALLOWED_BASES_CHANGED) from exc
            except build_context.VariantNotAssembledError as exc:
                self._stage("build", "error", started, reason="variant_not_assembled", error=str(exc))
                raise _Stop(status="infra", reason="variant_not_assembled") from exc
            except RuntimeInfraError as exc:
                self._stage("build", "error", started, reason=exc.reason, error=str(exc)[:2000])
                raise _Stop(status="infra", reason=exc.reason) from exc
            attempts.append({"attempt": attempt, "ok": outcome.ok, "infra_suspected": outcome.infra_suspected})
            if outcome.ok or not outcome.infra_suspected:
                break
        assert outcome is not None
        if outcome.ok:
            self.image = outcome.image
            self.row.build_key, self.row.image_id = outcome.build_key[:64], outcome.image[:80]
            self._stage("build", "ok", started, attempts=attempts, **outcome.detail)
            return
        incomplete = self._locks_incomplete()
        if incomplete:
            self._stage(
                "build",
                "error",
                started,
                reason=build_context.LOCK_INCOMPLETE,
                locks=incomplete,
                attempts=attempts,
                log_tail=outcome.log_tail,
            )
            raise _Stop(status="infra", reason=build_context.LOCK_INCOMPLETE)
        if outcome.infra_suspected:
            self._stage("build", "error", started, reason="build_infra", attempts=attempts, log_tail=outcome.log_tail)
            raise _Stop(status="infra", reason="build_infra")
        self._stage(
            "build",
            "fail",
            started,
            reason="build_failed",
            attempts=attempts,
            log_tail=outcome.log_tail,
            timed_out=outcome.timed_out,
        )
        raise _Stop(status="build_fail", reason="")

    def _locks_incomplete(self) -> list[dict[str, Any]]:
        import tempfile
        from pathlib import Path

        from backend.generation.build.context import BuildContextReport
        from backend.generation.build.context import prepare_build_dir

        report = BuildContextReport()
        try:
            with tempfile.TemporaryDirectory(prefix="llmlab-lockcheck-") as tmp:
                prepare_build_dir(self.run, Path(tmp), report=report)
        except Exception:  # noqa: BLE001 - only a diagnostic; the build error stands
            return []
        return report.locks_incomplete

    def functional(self) -> None:
        from backend.generation import blobs
        from backend.generation import evaluation as evaluation_pkg
        from backend.generation.evaluation.runtime import RuntimeInfraError
        from backend.generation.evaluation.runtime import TesterCrashError

        if not self.on["functional"]:
            self._stage("tester", "not_measured", reason="level", level=self.spec.eval_level)
            self.not_measured_reason = f"level:{self.spec.eval_level}"
            return
        stack = _inherited(self.run, "stack_profile")
        contract = ((stack.content if stack is not None else {}) or {}).get("run_contract") or {}
        database = ((stack.content if stack is not None else {}) or {}).get("database")
        task_version = _inherited(self.run, "task_version")
        if task_version is None:
            self._stage("tester", "error", reason="no_task")
            raise _Stop(status="infra", reason="no_task")
        creds = evaluation_pkg.new_credentials()
        ref = evaluation_pkg.store_credentials(creds)
        health = str(contract.get("health_path") or "/api/health")
        env = {
            "ADMIN_PASSWORD": creds["admin_password"],
            "SECRET_KEY": creds["secret_key"],
            "WEBHOOK_SECRET": secrets.token_urlsafe(24),
            "PORT": str(contract.get("port") or 8000),
            "AUTH_MODE": _auth_mode(self.run),
            "LLMLAB_HEALTH_PATH": health,
        }
        overlay = {path: blobs.get_bytes(sha) for path, sha in (self.overlay_files or {}).items()}
        started = time.monotonic()
        if database == "postgres":
            # The database is platform infrastructure: a sidecar that cannot start is never the sample's fault.
            start_database = getattr(self.runtime, "start_database", None)
            try:
                if start_database is None:
                    msg = f"the {getattr(self.runtime, 'name', '')} runtime has no database sidecar"
                    raise RuntimeInfraError(msg)
                env.update(start_database("postgres"))
            except RuntimeInfraError as exc:
                self._stage(
                    "start",
                    "error",
                    started,
                    reason="postgres_sidecar_unavailable",
                    error=str(exc)[:2000],
                    credentials_ref=ref,
                )
                raise _Stop(status="infra", reason="postgres_sidecar_unavailable") from exc
        try:
            handle = self.runtime.start(self.image, env, overlay_files=overlay or None)
        except RuntimeInfraError as exc:
            self._stage("start", "error", started, reason=exc.reason, error=str(exc)[:2000], credentials_ref=ref)
            raise _Stop(status="infra", reason=exc.reason) from exc
        try:
            healthy = self.runtime.wait_healthy(handle, health, self.start_s)
            if not healthy:
                self._stage(
                    "start",
                    "fail",
                    started,
                    credentials_ref=ref,
                    start_s=self.start_s,
                    log_tail=self.runtime.logs(handle)[-4000:],
                )
                raise _Stop(status="start_fail", reason="")
            self._stage("start", "ok", started, credentials_ref=ref)
            self._save_progress()
            self._tester(task_version, stack, handle, env, TesterCrashError)
        finally:
            with contextlib.suppress(Exception):
                self.runtime.stop(handle)

    def _plan(self, task_version: Any, stack: Any) -> dict[str, Any]:
        from backend.generation.evaluation import acceptance

        probes_mode = str(self.content.get("probes") or "task") if self.on["probes"] else "none"
        plan = acceptance.compile(task_version, _auth_mode(self.run), stack, probes=probes_mode, only=self.only_checks)
        data = acceptance.plan_json(plan)
        if self.probe_ids is not None:
            wanted = set(self.probe_ids)
            data["probes"] = [p for p in data["probes"] if p["id"] in wanted]
        if not self.on["probes"]:
            content = task_version.content or {}
            self.probe_rows = [
                {
                    "id": p["id"],
                    "cwe": p["cwe"],
                    "kind": p["kind"],
                    "endpoint": p.get("endpoint"),
                    "status": "skipped",
                    "evidence": None,
                    "skip_reason": "level",
                }
                for p in content.get("probes") or []
                if self.probe_ids is None or p["id"] in set(self.probe_ids)
            ]
        return data

    def _tester(self, task_version: Any, stack: Any, handle: Any, env: dict[str, str], crash: type[Exception]) -> None:
        from backend.generation.evaluation import reuse

        plan = self._plan(task_version, stack)
        detail = reuse.selection_detail(
            only_checks=self.only_checks,
            probes=self.probe_ids,
            image_override=self.image_override,
            overlay_files=self.overlay_files,
        )
        started = time.monotonic()
        raw: dict[str, Any] | None = None
        errors: list[str] = []
        for attempt in range(1, TESTER_ATTEMPTS + 1):
            try:
                raw = self.runtime.run_tester(handle, plan, env, self.tester_s)
            except crash as exc:
                errors.append(f"attempt {attempt}: {exc}")
                raw = None
                continue
            if raw.get("measured"):
                break
            errors.append(f"attempt {attempt}: {raw.get('not_measured_reason') or 'not measured'}")
            if attempt < TESTER_ATTEMPTS:
                self.runtime.restart(handle)
        if raw is None or not raw.get("measured"):
            self._stage("tester", "error", started, reason="tester_crash", errors=errors[-2:], **detail)
            self.not_measured_reason = "tester_error"
            raise _Stop(status="error", reason="tester_crash")
        self.checks = list(raw.get("checks") or [])
        if self.on["probes"]:
            self.probe_rows = list(raw.get("probes") or [])
        self.observed = {
            "auth": "bearer"
            if (raw.get("observed_auth") or {}).get("bearer_token")
            else ("cookie" if (raw.get("observed_auth") or {}).get("set_cookie") else None),
        }
        self.tester_versions = {
            k: raw.get(k) for k in ("tester_version", "tester_rules_version", "probe_rules_version")
        }
        self._plaintext_passwords(handle, raw, stack)
        self.measured = True
        self._stage("tester", "ok", started, attempts=len(errors) + 1, **detail, **self.tester_versions)
        if self.on["probes"]:
            tested = sum(1 for p in self.probe_rows if p.get("status") in ("exploited", "not_exploited"))
            self._stage("probes", "ok", None, n=len(self.probe_rows), tested=tested)
        else:
            self._stage("probes", "not_measured", None, reason="level", n=len(self.probe_rows))

    def _plaintext_passwords(self, handle: Any, raw: dict[str, Any], stack: Any) -> None:
        """``plaintext_password_db``: user_a's password verbatim in the app's data directory (docker cp).

        On ``database: postgres`` stacks the users live in the sidecar, not in ``./data``: whatever the app
        writes there (uploads, logs, a fallback sqlite) says nothing about how passwords are stored, so the
        probe is ``skipped(precondition_failed)`` without reading files, never ``not_exploited``.
        """
        from backend.generation.evaluation import probes as probes_mod

        rows = [
            p
            for p in self.probe_rows
            if p.get("kind") == "plaintext_password_db" and p.get("evidence") == "suite_side"
        ]
        if not rows:
            return
        content = (stack.content if stack is not None else {}) or {}
        if content.get("database") == "postgres":
            for row in rows:
                row.update(
                    status="skipped",
                    evidence="passwords live in the postgres sidecar",
                    skip_reason="precondition_failed",
                )
            return
        password = ((raw.get("actors") or {}).get("user_a") or {}).get("password") or ""
        contract = content.get("run_contract") or {}
        files: dict[str, bytes] = {}
        with contextlib.suppress(Exception):
            files = self.runtime.read_data(handle, str(contract.get("data_dir") or "./data"))
        for row in rows:
            status, evidence, skip = probes_mod.plaintext_password_verdict(files, password)
            row.update(status=status, evidence=evidence, skip_reason=skip)

    def adherence(self) -> None:
        from backend.generation.evaluation import adherence as adherence_mod
        from backend.generation.evaluation.runtime import RuntimeInfraError

        if not (self.on["adherence"] or self.on["adherence_static"]):
            self.row.adherence = adherence_mod.not_measured().model_dump(mode="json")
            return
        started = time.monotonic()
        try:
            record = adherence_mod.check_adherence(self.run, self.runtime, observed=self.observed)
        except (RuntimeInfraError, OSError, ValueError) as exc:
            self._stage(
                "adherence",
                "error",
                started,
                reason=getattr(exc, "reason", "adherence_error"),
                error=str(exc)[:1000],
            )
            self.row.adherence = adherence_mod.not_measured().model_dump(mode="json")
            return
        self.row.adherence = record.model_dump(mode="json")
        self._stage("adherence", "ok", started, record_status=record.status, runtime=self.on["adherence"])

    def baseline_rate(self) -> float | None:
        from backend.generation.models import EvalBaseline

        if not self.on["baseline"] or self.run.reference_of == ReferenceOf.STARTER_BASELINE:
            return None
        starter = _inherited(self.run, "starter")
        task_version = _inherited(self.run, "task_version")
        if starter is None or task_version is None:
            return None
        row = EvalBaseline.objects.filter(
            starter=starter,
            task_version=task_version,
            auth_mode=_auth_mode(self.run),
            eval_suite=self.suite,
        ).first()
        if row is None:
            return None
        self.row.baseline = row
        return row.agent_pass_rate

    # result -------------------------------------------------------------------------------

    def result(self) -> dict[str, Any]:
        from backend.generation.evaluation import acceptance
        from backend.generation.evaluation import metrics as metrics_mod
        from backend.generation.evaluation.tester import tester_module

        flaky = [str(c) for c in self.content.get("flaky_checks") or []]
        computed = metrics_mod.compute(
            self.checks,
            self.probe_rows,
            flaky=flaky,
            only_checks=self.only_checks,
            prose_variant=self.run.prose_variant or None,
            baseline_pass_rate=self.baseline_rate() if self.measured else None,
            probes_on=self.on["probes"],
        )
        result = EvalResult(
            tester_version=int(self.tester_versions.get("tester_version") or tester_module().VERSION),
            compiler_version=acceptance.COMPILER_VERSION,
            auth_mode=_auth_mode(self.run),  # type: ignore[arg-type]
            checks=self.checks,  # type: ignore[arg-type]
            probes=self.probe_rows,  # type: ignore[arg-type]
            metrics=computed,
            measured=self.measured,
            not_measured_reason=None if self.measured else (self.not_measured_reason or "not_measured"),
            flaky_checks=flaky,
        )
        return result.model_dump(mode="json")

    def run_all(self) -> None:
        stop: _Stop | None = None
        try:
            self.lock()
            self.build()
            self.functional()
        except _Stop as exc:
            stop = exc
        self.adherence()
        status = EvaluationStatus.MEASURED if self.measured else EvaluationStatus.NOT_MEASURED
        if stop is not None:
            self.not_measured_reason = self.not_measured_reason or (
                stop.status if not stop.reason else f"{stop.status}:{stop.reason}"
            )
            if stop.status in ("infra", "error"):
                status = EvaluationStatus.ERROR
        self.row.result = self.result()
        self.row.stages = dict(self.stages)
        self.row.status = status
        self.row.save()


def _row_for(
    run: GenerationRun,
    suite: EvalSuite,
    spec: EvaluationSpec,
    mode: str,
    evaluation: RunEvaluation | None,
    retest_of: RunEvaluation | None = None,
) -> RunEvaluation:
    pending = evaluation or (None if retest_of is not None else _PENDING_ROW.get())
    if (
        pending is not None
        and str(pending.run_id) == str(run.pk)
        and pending.status in (EvaluationStatus.PENDING, EvaluationStatus.RUNNING)
    ):
        pending.eval_suite = suite
        pending.eval_level = spec.eval_level
        pending.mode = mode
        pending.status = EvaluationStatus.RUNNING
        pending.save(update_fields=["eval_suite", "eval_level", "mode", "status"])
        _PENDING_ROW.set(None)
        return pending
    return RunEvaluation.objects.create(
        run=run,
        eval_suite=suite,
        eval_level=spec.eval_level,
        mode=mode,
        status=EvaluationStatus.RUNNING,
        retest_of=retest_of,
    )


def evaluate(
    run: GenerationRun,
    suite: EvalSuite,
    *,
    only_checks: list[str] | None = None,
    probes: list[str] | None = None,
    mode: str = "full",
    image_override: str | None = None,
    overlay_files: dict[str, str] | None = None,
    eval_level: str | None = None,
    evaluation: RunEvaluation | None = None,
    runtime: EvalRuntime | None = None,
    force_rebuild: bool = False,
    cancelled: Callable[[], bool] | None = None,
    retest_of: RunEvaluation | None = None,
) -> RunEvaluation:
    """Evaluate ``run`` under ``suite`` into a new (append-only) ``RunEvaluation`` (or the given pending row).

    The keyword arguments after ``overlay_files`` are additive (WP-09): ``eval_level`` overrides the spec's
    level, ``evaluation`` is a pending row to fill, ``runtime`` an ``EvalRuntime`` (default: settings),
    ``retest_of`` marks a test-retest re-evaluation (``retest.run_retests``).
    """
    from backend.generation.evaluation.runtime import get_runtime

    if run.outcome == Outcome.OUTPUT_INVALID:
        msg = f"run {run.pk} is output_invalid: it is never built (section 4.8)"
        raise ValueError(msg)
    spec = spec_for(run, suite, eval_level)
    row = _row_for(run, suite, spec, mode, evaluation, retest_of)
    owns = runtime is None
    runtime = runtime or get_runtime(suite.content or {})
    try:
        _Evaluation(
            run,
            suite,
            row,
            spec,
            runtime,
            only_checks=list(only_checks) if only_checks is not None else None,
            probes=list(probes) if probes is not None else None,
            mode=mode,
            image_override=image_override,
            overlay_files=dict(overlay_files) if overlay_files else None,
            force_rebuild=force_rebuild,
            cancelled=cancelled,
        ).run_all()
    except Exception:
        logger.exception("Evaluation %s of run %s failed", row.pk, run.pk)
        row.refresh_from_db()
        if row.status not in RunEvaluation.FINAL_STATUSES:
            row.status = EvaluationStatus.ERROR
            row.save(update_fields=["status"])
        raise
    finally:
        if owns:
            runtime.close()
    return row


# =====================================================================================
# Outcome and the eval stage
# =====================================================================================


def outcome_of(evaluation: RunEvaluation) -> tuple[str, str]:
    """``(outcome, reason)`` of a run from its evaluation (section 6.12).

    Lock or build ``fail`` -> ``build_fail``; start ``fail`` -> ``start_fail``; a measured tester ->
    ``tested``; a level without the tester -> ``tested`` / ``not_tested:<level>`` (the pass rate stays not
    measured); anything that errored -> ``infra_error`` (never ``tested`` with a pass rate of 0).
    """
    stages = evaluation.stages or {}
    for name in ("lock", "build"):
        stage = stages.get(name) or {}
        if stage.get("status") == "fail":
            reason = str((stage.get("detail") or {}).get("reason") or "")
            return Outcome.BUILD_FAIL, "" if reason == "build_failed" else reason
    if (stages.get("start") or {}).get("status") == "fail":
        return Outcome.START_FAIL, ""
    if evaluation.status == EvaluationStatus.MEASURED and (evaluation.result or {}).get("measured"):
        return Outcome.TESTED, ""
    tester = stages.get("tester") or {}
    if evaluation.status == EvaluationStatus.NOT_MEASURED and (tester.get("detail") or {}).get("reason") == "level":
        return Outcome.TESTED, f"not_tested:{evaluation.eval_level}"
    for stage in stages.values():
        detail = (stage or {}).get("detail") or {}
        if (stage or {}).get("status") == "error" and detail.get("reason"):
            return Outcome.INFRA_ERROR, str(detail["reason"])[:64]
    return Outcome.INFRA_ERROR, "evaluation_error"


def _reuse_copy(run: GenerationRun, suite: EvalSuite, spec: EvaluationSpec, mode: str) -> RunEvaluation | None:
    from backend.generation.evaluation import reuse

    found = reuse.find_reusable(run, suite, eval_level=spec.eval_level, mode=mode)
    if found is None:
        return None
    return RunEvaluation.objects.create(
        run=run,
        eval_suite=found.eval_suite,
        eval_level=found.eval_level,
        mode=found.mode,
        status=found.status,
        stages=found.stages,
        result=found.result,
        adherence=found.adherence,
        build_key=found.build_key,
        image_id=found.image_id,
        reused_from=found.reused_from or found,
        baseline=found.baseline,
    )


def _reuse_enabled(run: GenerationRun) -> bool:
    from backend.generation.agentic.validity import policy_knob

    effective = ((_manifest(run).get("resources") or {}).get("effective")) or {}
    content = effective.get("content", effective) if isinstance(effective, dict) else {}
    value = policy_knob(content, "caches.reuse_eval_by_tree")
    return bool(value) if value is not None else False


def _notes(run: GenerationRun, evaluation: RunEvaluation, *, reused: bool) -> None:
    from backend.generation.agentic import validity

    notes = []
    if run.lock_source:
        notes.append(validity.note(f"lock_source:{run.lock_source}"))
    adherence = (evaluation.adherence or {}).get("status") if isinstance(evaluation.adherence, dict) else None
    if adherence:
        notes.append(validity.note(f"adherence:{adherence}", level="warn" if adherence == "violated" else "info"))
    if reused or evaluation.reused_from_id:
        notes.append(validity.note("eval_reused"))
    if notes:
        run.validity = validity.add_notes(run.validity, notes)


def run_eval_stage(run: GenerationRun) -> None:
    """The eval worker's entry point (claimed by phase ``eval_queued``)."""
    from backend.execution import transitions
    from backend.generation.evaluation import metrics as metrics_mod

    if run.outcome == Outcome.OUTPUT_INVALID:
        transitions.finish_stage(run, transitions.OUTCOME_DONE)  # never built (4.8)
        return
    execution = dict(run.execution or {})
    pending: RunEvaluation | None = None
    if execution.get("reeval_evaluation_id"):
        pending = RunEvaluation.objects.filter(
            pk=execution["reeval_evaluation_id"],
            run=run,
            status__in=[EvaluationStatus.PENDING, EvaluationStatus.RUNNING],
        ).first()

    def cancelled() -> bool:
        from django.utils import timezone

        if not run.lease_owner:
            return False
        return not GenerationRun.objects.filter(
            pk=run.pk,
            attempt=run.attempt,
            lease_owner=run.lease_owner,
            lease_expires_at__gt=timezone.now(),
        ).exists()

    slot_run = run.slot_target_id is not None and (run.kind in _SLOT_KINDS or run.reference_of in _SLOT_REFERENCES)
    reused = False
    token = _PENDING_ROW.set(pending)
    try:
        if slot_run:
            from backend.generation.slot import evaluate as slot_evaluate

            evaluation = slot_evaluate.evaluate_variant(run)
        else:
            suite = suite_for(run)
            planned_mode = (_manifest(run).get("planned_evaluation") or {}).get("mode") or EvalMode.FULL
            spec = spec_for(run, suite)
            evaluation = None
            if pending is None and _reuse_enabled(run) and not execution.get("force_rebuild"):
                evaluation = _reuse_copy(run, suite, spec, planned_mode)
                reused = evaluation is not None
            if evaluation is None:
                evaluation = evaluate(
                    run,
                    suite,
                    mode=planned_mode,
                    evaluation=pending,
                    force_rebuild=bool(execution.get("force_rebuild")),
                    cancelled=cancelled,
                )
    except LookupError as exc:
        logger.warning("Run %s cannot be evaluated: %s", run.pk, exc)
        run.outcome, run.outcome_reason = Outcome.INFRA_ERROR, "no_eval_suite"
        run.error_message = str(exc)[:2000]
        transitions.finish_stage(run, transitions.OUTCOME_DONE)
        return
    finally:
        _PENDING_ROW.reset(token)
    outcome, reason = outcome_of(evaluation)
    run.refresh_from_db(fields=["validity", "execution", "resolved_lock_blob", "lock_source", "registry_snapshot_at"])
    run.outcome, run.outcome_reason = outcome, reason
    _notes(run, evaluation, reused=reused)
    slot_rate = None
    if run.slot_target_id is not None:
        from backend.generation.slot.evaluate import slot_pass_rate

        slot_rate = slot_pass_rate(evaluation, run.slot_target)
    summary = metrics_mod.eval_summary(evaluation, slot_pass_rate=slot_rate)
    run.metrics = {**(run.metrics or {}), "eval": summary.model_dump(mode="json") if summary else None}
    transitions.finish_stage(run, transitions.OUTCOME_DONE)
    if run.kind == RunKind.REFERENCE and run.reference_of in _SLOT_REFERENCES:
        from backend.generation.slot import validate as slot_validate

        slot_validate.on_reference_evaluated(run)  # the evaluation is final
    if run.kind == RunKind.REFERENCE and run.reference_of in (
        ReferenceOf.STARTER_BASELINE,
        ReferenceOf.ORACLE,
        ReferenceOf.CONFORMANCE,
    ):
        from backend.generation.evaluation import baseline

        baseline.on_reference_evaluated(run, evaluation)
