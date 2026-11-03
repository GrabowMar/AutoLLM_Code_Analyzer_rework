"""Tester conformance kit, oracle validation and probe validity (spec sections 8.3 and 8.5).

* ``coverage() -> {framework: bool}``: for every backend framework offered as a fixed stack, whether the
  conformance kit passed on it: every kit task (``KIT_TASKS``) has a ``kit:<stack pinned ref>`` entry with
  ``status=pass`` in its latest ``TaskSpecVersion.oracle`` for a stack of that framework (written by
  ``manage.py conformance_kit`` through ``baseline.on_reference_evaluated``). The study resolver reports
  ``conformance_missing`` for a framework that is not covered (warn exploratory, error confirmatory).
* ``status_rows()``: ``GET /eval/conformance/``.
* ``run_kit(stack, suite)``: one conformance reference run per kit task from the task's reference tree
  (``data/tasks/<slug>/reference/<stack slug>/``), evaluated inline; a missing tree is recorded ``missing``.
* ``validate_oracle(task_version, stack, suite, runs=3)``: the reference must pass 100% in every run, the
  bare starter must score ``agent_pass_rate < 0.5``; checks with mixed results across the runs become the
  suite's ``flaky_checks`` (a new suite version). The verdict goes to ``TaskSpecVersion.oracle``.
* ``validate_probe_kinds()``: each probe kind's vulnerable mutant of the bundled reference app
  (``probe_mutants/app.py``, ``LLMLAB_VULN=<kind>``) must be ``exploited`` and the fixed app
  ``not_exploited``; the verdicts go to ``probe_validation.json`` (``probes.kind_validated``). Runs on the
  local runtime (the app is standard-library Python).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable

    from backend.generation.evaluation.runtime import EvalRuntime
    from backend.generation.models import EvalSuite
    from backend.generation.models import StackProfile
    from backend.generation.models import TaskSpecVersion

__all__ = [
    "KIT_TASKS",
    "PROBE_MUTANTS",
    "coverage",
    "fixed_stacks",
    "kit_task_content",
    "run_kit",
    "status_rows",
    "validate_oracle",
    "validate_probe_kinds",
]

KIT_TASKS: Final = ("crud_todo_list", "team_roles_multitenant", "file_share_multipart")
PROBE_MUTANTS: Final = Path(__file__).resolve().parent / "probe_mutants"
_ADMIN_PASSWORD: Final = "kit-admin-pw-7Q"  # noqa: S105 - the local kit app's throwaway admin password


def kit_task_content() -> dict[str, Any]:
    """The probe-kit task (``probe_mutants/kit_task.yaml``): ``crud_todo_list`` plus one endpoint per probe surface."""
    return yaml.safe_load((PROBE_MUTANTS / "kit_task.yaml").read_text(encoding="utf-8"))


def fixed_stacks() -> list[StackProfile]:
    """The latest non-archived fixed-choice stack profiles (``choice_mode=fixed``)."""
    from backend.generation.models import StackProfile

    rows = StackProfile.objects.filter(is_archived=False).order_by("slug", "-version")
    seen: set[str] = set()
    out: list[StackProfile] = []
    for row in rows:
        if row.slug in seen:
            continue
        seen.add(row.slug)
        if (row.content or {}).get("choice_mode") == "fixed":
            out.append(row)
    return out


def _framework(stack: StackProfile) -> str:
    return str(((stack.content or {}).get("backend") or {}).get("framework") or "")


def _latest_kit_versions() -> dict[str, TaskSpecVersion | None]:
    from backend.generation.models import TaskSpecVersion

    out: dict[str, TaskSpecVersion | None] = {}
    for slug in KIT_TASKS:
        out[slug] = TaskSpecVersion.objects.filter(task__slug=slug).order_by("-version").first()
    return out


def status_rows() -> list[dict[str, Any]]:
    """``[{framework, tasks: [{task, status, runs}], ok}]`` (``GET /eval/conformance/``)."""
    from backend.generation.evaluation.baseline import oracle_key

    versions = _latest_kit_versions()
    by_framework: dict[str, list[StackProfile]] = {}
    for stack in fixed_stacks():
        by_framework.setdefault(_framework(stack), []).append(stack)
    rows: list[dict[str, Any]] = []
    for framework, stacks in sorted(by_framework.items()):
        tasks: list[dict[str, Any]] = []
        for slug, version in versions.items():
            oracle = (version.oracle or {}) if version is not None else {}
            entries = [oracle.get(oracle_key(stack, kit=True)) for stack in stacks]
            entries = [e for e in entries if e]
            status = "missing"
            if any(e.get("status") == "pass" for e in entries):
                status = "pass"
            elif entries:
                status = str(entries[0].get("status") or "fail")
            tasks.append(
                {"task": slug, "status": status, "runs": [e.get("run_id") for e in entries if e.get("run_id")]},
            )
        rows.append({"framework": framework, "tasks": tasks, "ok": all(t["status"] == "pass" for t in tasks)})
    return rows


def coverage() -> dict[str, bool]:
    """``{backend framework: covered}`` for every framework offered as a fixed stack (spec 8.5)."""
    return {row["framework"]: bool(row["ok"]) for row in status_rows()}


def run_kit(
    stack: StackProfile,
    suite: EvalSuite,
    *,
    runtime: EvalRuntime | None = None,
    tasks: Iterable[str] = KIT_TASKS,
    auth_mode: str = "contract_bearer",
) -> list[dict[str, Any]]:
    """Evaluate the stack's kit references inline; returns ``[{task, status, run}]`` (entries are stored)."""
    from django.utils import timezone

    from backend.generation.evaluation import baseline
    from backend.generation.evaluation import suite as suite_mod
    from backend.generation.models import GenerationRun
    from backend.generation.models import TaskSpecVersion
    from backend.generation.models.choices import ReferenceOf
    from backend.generation.models.choices import RunStatus

    out: list[dict[str, Any]] = []
    for slug in tasks:
        version = TaskSpecVersion.objects.filter(task__slug=slug).order_by("-version").first()
        if version is None:
            out.append({"task": slug, "status": "missing", "run": None})
            continue
        tree = baseline.reference_tree(slug, stack.slug)
        if tree is None:
            baseline._set_oracle(  # noqa: SLF001
                version,
                baseline.oracle_key(stack, kit=True),
                {"status": "missing", "eval_suite_ref": suite.pinned_ref, "at": timezone.now().isoformat()},
            )
            out.append({"task": slug, "status": "missing", "run": None})
            continue
        run = baseline.reference_run(
            reference_of=ReferenceOf.CONFORMANCE,
            task_version=version,
            stack=stack,
            starter=None,
            auth_mode=auth_mode,
            files=tree,
            suite=suite,
        )
        evaluation = suite_mod.evaluate(run, suite, eval_level="security", runtime=runtime)
        outcome, reason = suite_mod.outcome_of(evaluation)
        GenerationRun.objects.filter(pk=run.pk).update(
            status=RunStatus.COMPLETED,
            outcome=outcome,
            outcome_reason=reason,
        )
        baseline.on_reference_evaluated(run, evaluation)
        version.refresh_from_db(fields=["oracle"])
        entry = (version.oracle or {}).get(baseline.oracle_key(stack, kit=True)) or {}
        out.append({"task": slug, "status": entry.get("status"), "run": str(run.pk)})
    return out


def validate_oracle(
    task_version: TaskSpecVersion,
    stack: StackProfile,
    suite: EvalSuite,
    *,
    runs: int = 3,
    runtime: EvalRuntime | None = None,
    starter_tree: dict[str, tuple[bytes, int]] | None = None,
    reference: dict[str, tuple[bytes, int]] | None = None,
    auth_mode: str = "contract_bearer",
    write_flaky: bool = True,
) -> dict[str, Any]:
    """Run the task's reference ``runs`` times and the bare starter once (spec 8.5); store the verdict."""
    from django.utils import timezone

    from backend.generation.evaluation import baseline
    from backend.generation.evaluation import suite as suite_mod
    from backend.generation.models import GenerationRun
    from backend.generation.models import Starter
    from backend.generation.models.choices import FileOrigin
    from backend.generation.models.choices import ReferenceOf
    from backend.generation.models.choices import RunStatus

    tree = reference if reference is not None else baseline.reference_tree(task_version.task.slug, stack.slug)
    key = baseline.oracle_key(stack)
    if tree is None:
        entry = {"status": "missing", "eval_suite_ref": suite.pinned_ref, "at": timezone.now().isoformat()}
        baseline._set_oracle(task_version, key, entry)  # noqa: SLF001
        return {"status": "missing", "runs": [], "flaky": [], "starter_pass_rate": None}
    statuses: dict[str, list[str]] = {}
    run_ids: list[str] = []
    rates: list[float | None] = []
    for round_no in range(1, runs + 1):
        run = baseline.reference_run(
            reference_of=ReferenceOf.ORACLE,
            task_version=task_version,
            stack=stack,
            starter=None,
            auth_mode=auth_mode,
            files=tree,
            suite=suite,
            extra={"validation_round": round_no},
        )
        evaluation = suite_mod.evaluate(run, suite, eval_level="security", runtime=runtime)
        outcome, reason = suite_mod.outcome_of(evaluation)
        GenerationRun.objects.filter(pk=run.pk).update(
            status=RunStatus.COMPLETED,
            outcome=outcome,
            outcome_reason=reason,
        )
        run_ids.append(str(run.pk))
        result = evaluation.result or {}
        rates.append((result.get("metrics") or {}).get("agent_pass_rate") if result.get("measured") else None)
        for check in result.get("checks") or []:
            statuses.setdefault(check["id"], []).append(check["status"])
    flaky = sorted(cid for cid, seen in statuses.items() if len(set(seen)) > 1)
    starter_rate: float | None = None
    starter_files = starter_tree
    if starter_files is None:
        starter = Starter.objects.filter(stack_profile=stack, is_archived=False).order_by("level", "-version").first()
        starter_files = baseline.starter_tree(starter) if starter is not None else None
    if starter_files:
        bare = baseline.reference_run(
            reference_of=ReferenceOf.STARTER_BASELINE,
            task_version=task_version,
            stack=stack,
            starter=None,
            auth_mode=auth_mode,
            files=starter_files,
            suite=suite,
            origin=FileOrigin.STARTER,
            extra={"validation_round": "starter"},
        )
        bare_eval = suite_mod.evaluate(bare, suite, eval_level="functional", runtime=runtime)
        result = bare_eval.result or {}
        # A starter that does not even start scores nothing: it satisfies "< 0.5" but has no pass rate.
        starter_rate = (result.get("metrics") or {}).get("agent_pass_rate") if result.get("measured") else None
    passing = all(r == 1.0 for r in rates if r is not None) and len([r for r in rates if r is not None]) == runs
    ok = passing and (starter_rate is None or starter_rate < 0.5)
    status = "pass" if ok else "fail"
    baseline._set_oracle(  # noqa: SLF001
        task_version,
        key,
        {
            "status": status,
            "run_id": run_ids[-1] if run_ids else None,
            "eval_suite_ref": suite.pinned_ref,
            "at": timezone.now().isoformat(),
        },
    )
    if flaky and write_flaky:
        _write_flaky(suite, flaky)
    return {"status": status, "runs": run_ids, "rates": rates, "flaky": flaky, "starter_pass_rate": starter_rate}


def _write_flaky(suite: EvalSuite, flaky: list[str]) -> Any:
    """A new suite version whose ``flaky_checks`` adds ``flaky`` (excluded from every pass rate)."""
    from backend.generation.library import service

    content = dict(suite.content or {})
    merged = sorted(set(content.get("flaky_checks") or []) | set(flaky))
    if merged == sorted(content.get("flaky_checks") or []):
        return suite
    content["flaky_checks"] = merged
    return service.new_version(
        suite,
        base_version=suite.version,
        content=content,
        user=None,
        system=service.is_system_row(suite),
    )


def validate_probe_kinds(
    *,
    kinds: Iterable[str] | None = None,
    write: bool = True,
    auth_modes: Iterable[str] = ("contract_bearer", "cookie_session"),
) -> dict[str, dict[str, Any]]:
    """Run each probe kind on its vulnerable mutant and on the fixed reference app (local runtime).

    Returns ``{kind: {mutant, fixed, auth_mode, validated}}``; ``validated`` needs ``exploited`` on the mutant and
    ``not_exploited`` on the fixed app. With ``write`` the verdicts go to ``probe_validation.json``.
    """
    from django.utils import timezone

    from backend.generation.evaluation import acceptance
    from backend.generation.evaluation import probes as probes_mod
    from backend.generation.evaluation.runtime import LocalProcessRuntime

    content = kit_task_content()
    wanted = set(kinds) if kinds is not None else set(probes_mod.catalog())
    stack = {
        "run_contract": {"health_path": "/api/health", "static_serving": "backend"},
        "frontend": {"framework": "react"},
    }
    verdicts: dict[str, dict[str, Any]] = {}
    runtime = LocalProcessRuntime()
    try:
        for kind in sorted(wanted):
            mode = "cookie_session" if kind == "csrf_missing" else next(iter(auth_modes))
            plan = acceptance.plan_json(acceptance.compile_content(content, mode, stack, enable_unvalidated=True))
            plan["probes"] = [p for p in plan["probes"] if p["kind"] == kind]
            row: dict[str, Any] = {"auth_mode": mode}
            for variant, vuln in (("mutant", kind), ("fixed", "")):
                env = {"ADMIN_PASSWORD": _ADMIN_PASSWORD, "SECRET_KEY": "kit-secret", "AUTH_MODE": mode}
                if vuln:
                    env["LLMLAB_VULN"] = vuln
                handle = runtime.start(str(PROBE_MUTANTS), env)
                try:
                    if not runtime.wait_healthy(handle, "/api/health", 15):
                        row[variant] = "start_fail"
                        continue
                    raw = runtime.run_tester(handle, plan, env, 120)
                    probe = next(iter(raw.get("probes") or []), None)
                    if probe and probe.get("kind") == "plaintext_password_db":
                        password = ((raw.get("actors") or {}).get("user_a") or {}).get("password") or ""
                        status, _evidence, _skip = probes_mod.plaintext_password_verdict(
                            runtime.read_data(handle, "./data"),
                            password,
                        )
                        probe = {**probe, "status": status}
                    row[variant] = probe.get("status") if probe else "missing"
                finally:
                    runtime.stop(handle)
            row["validated"] = row.get("mutant") == "exploited" and row.get("fixed") == "not_exploited"
            verdicts[kind] = row
    finally:
        runtime.close()
    if write:
        probes_mod.write_validation(verdicts, at=timezone.now().isoformat())
        probes_mod.catalog.cache_clear()
    return verdicts
