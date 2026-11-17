"""Recorded execution, task and evaluation evidence for a selected report sample."""

from __future__ import annotations

from collections import Counter
from collections import defaultdict
from statistics import fmean

from backend.generation.models import GenerationRun
from backend.generation.models import RunAttempt
from backend.generation.models import TaskSpecVersion
from backend.generation.runs.view import run_views
from backend.statistics.services.runs import app_unit


def _dict(value):
    return value if isinstance(value, dict) else {}


def _attempt_count(stage):
    value = _dict(_dict(stage).get("detail")).get("attempts")
    return len(value) if isinstance(value, list) else value if isinstance(value, int) else None


def _strata(executions, rows):
    by_id = {r["run_id"]: r for r in rows}
    groups = defaultdict(list)
    for execution in executions:
        groups[(execution["group"], execution["lock_source"] or "unknown")].append(by_id[execution["run_id"]])
    result = []
    for (group, source), items in sorted(groups.items()):
        apps = defaultdict(list)
        for item in items:
            apps[app_unit(item)].append(item)
        measured = [
            fmean(bool(r["deployed"]) for r in repeats if r["evaluated"])
            for repeats in apps.values()
            if any(r["evaluated"] for r in repeats)
        ]
        result.append(
            {
                "group": group,
                "lock_source": source,
                "apps": len(apps),
                "runs": len(items),
                "evaluated_apps": len(measured),
                "deployed_rate": fmean(measured) if measured else None,
            },
        )
    return result


def _task_record(task, oracle):
    structured = _dict(task.get("structured"))
    return {
        "task": task["ref"],
        "pinned_ref": task.get("pinned_ref"),
        "title": task.get("title"),
        "category": task.get("category"),
        "prose_variant": task.get("variant"),
        "structured_hash": task.get("structured_hash"),
        "features": task.get("features", []),
        "surfaces": task.get("surfaces", []),
        "has_frontend": structured.get("has_frontend"),
        "endpoints": structured.get("endpoints", []),
        "ui": structured.get("ui", []),
        "acceptance_ids": [c["id"] for c in structured.get("acceptance", [])],
        "probe_ids": [p["id"] for p in structured.get("probes", [])],
        "oracle": oracle,
        "selected_instruments": [],
    }


def _probe_bounds(records):
    result = []
    for (task, probe_id, cwe, kind), values in sorted(records.items()):
        measured = [
            (app, p) for app, p, valid in values if valid and p.get("status") in {"exploited", "not_exploited"}
        ]
        apps = {app for app, _probe in measured}
        affected = {app for app, probe in measured if probe["status"] == "exploited"}
        result.append(
            {
                "task": task,
                "probe_id": probe_id,
                "cwe": cwe,
                "kind": kind,
                "apps_measured": len(apps),
                "apps_exploited": len(affected),
                "runs_measured": len(measured),
                "statuses": dict(Counter(p.get("status", "unknown") for _app, p, _valid in values)),
                "skip_reasons": dict(Counter(p["skip_reason"] for _app, p, _valid in values if p.get("skip_reason"))),
                "ci95": None,
                "zero_rate_upper95": None,
                "inference_status": "descriptive_fixed_task_endpoint_set",
            },
        )
    return {
        "population": "apps with a measured runtime probe under the selected evaluation suite",
        "by_probe": result,
        "notes": [
            "Skipped, error and unmeasured evaluations are not negative security results.",
            "Repeats count once per app; any exploited repeat makes that app affected.",
            "Each probe row concerns one fixed task across selected endpoints. Those observations are not an "
            "independent random sample of applications; binomial confidence intervals and zero-event population "
            "bounds are withheld.",
            "Counts concern these probes on measured apps, not all vulnerabilities or undeployed apps.",
        ],
    }


def _study_history(study, selected_ids):
    """Account for every recorded study attempt without treating retries as additional samples."""
    if study is None:
        return None
    runs = list(
        GenerationRun.objects.filter(study=study)
        .order_by("created_at", "pk")
        .values(
            "id",
            "cell_id",
            "cell_attempt",
            "cell__stage",
            "cell__plan_id",
            "outcome",
            "outcome_reason",
            "status",
            "usable",
            "beyond_plan",
            "validity",
            "metrics",
        ),
    )
    nonselected = [r for r in runs if str(r["id"]) not in selected_ids]
    costs = [r["metrics"].get("cost_usd") for r in runs if isinstance(r["metrics"], dict)]
    return {
        "study_id": str(study.pk),
        "recorded_runs": len(runs),
        "selected_runs": len(runs) - len(nonselected),
        "nonselected_runs": len(nonselected),
        "recorded_by_outcome": dict(Counter(r["outcome"] or "unknown" for r in runs)),
        "nonselected_by_outcome": dict(Counter(r["outcome"] or "unknown" for r in nonselected)),
        "nonselected_by_reason": dict(Counter(r["outcome_reason"] or "unspecified" for r in nonselected)),
        "recorded_harness_attempts": RunAttempt.objects.filter(run__study=study).count(),
        "recorded_cost_runs": sum(v is not None for v in costs),
        "recorded_cost_sum_usd": (
            sum(float(v) for v in costs if v is not None) if any(v is not None for v in costs) else None
        ),
        "nonselected": [
            {
                "run_id": str(r["id"]),
                "cell_id": r["cell_id"],
                "cell_attempt": r["cell_attempt"],
                "stage": r["cell__stage"],
                "active_plan": r["cell__plan_id"] == study.active_plan_id,
                "outcome": r["outcome"],
                "outcome_reason": r["outcome_reason"],
                "status": r["status"],
                "usable": r["usable"],
                "beyond_plan": r["beyond_plan"],
                "truncated": bool(_dict(r["validity"]).get("truncated")),
            }
            for r in nonselected
        ],
        "note": "History covers all recorded runs of the selected study, including retries and runs outside "
        "this report's scope. Nonselected runs are not additional independent samples. Recorded costs cover "
        "only rows with a cost measurement and do not represent total economic cost.",
    }


def study_provenance(runs, rows, *, study=None, eval_suite=None):
    """Read pinned run content through RunView; export recorded facts, never infer absent attempts."""
    from backend.reports.services.evaluation_selection import evaluation_for_report

    by_id = {r["run_id"]: r for r in rows}
    oracles = dict(
        TaskSpecVersion.objects.filter(
            pk__in={r.task_version_id for r in runs if r.task_version_id},
        ).values_list("pk", "oracle"),
    )
    attempts = list(
        RunAttempt.objects.filter(run_id__in=by_id)
        .order_by("run_id", "attempt")
        .values(
            "run_id",
            "attempt",
            "termination",
            "outcome",
            "infra_error",
            "usage",
            "served",
        ),
    )
    by_run = defaultdict(list)
    attempt_rows = []
    for attempt in attempts:
        run_id = str(attempt["run_id"])
        item = {
            "run_id": run_id,
            "group": by_id[run_id]["group"],
            "attempt": attempt["attempt"],
            "termination": attempt["termination"],
            "outcome": attempt["outcome"],
            "infra_error": attempt["infra_error"],
            "calls_by_class": _dict(attempt["usage"]).get("requests_by_class", {}),
            "served_providers": _dict(attempt["served"]).get("providers", {}),
            "served_models": _dict(attempt["served"]).get("models", {}),
        }
        by_run[run_id].append(item)
        attempt_rows.append(item)
    executions, tasks, checks, probes = [], {}, {}, defaultdict(list)
    study_ids = set()
    for run, view in zip(runs, run_views(runs), strict=True):
        row = by_id[str(run.pk)]
        manifest = view.manifest()
        harness, sampling = _dict(manifest.get("harness")), _dict(manifest.get("sampling"))
        model, starter = _dict(manifest.get("model")), _dict(manifest.get("starter"))
        planned_suite = _dict(_dict(manifest.get("planned_evaluation")).get("eval_suite")).get("ref")
        planned_suite = planned_suite.split("#", 1)[0] if isinstance(planned_suite, str) else None
        task = view.task_dict()
        evaluation = evaluation_for_report(view, eval_suite)
        if task:
            task_key = (task["ref"], task.get("variant") or "")
            task_record = tasks.setdefault(task_key, _task_record(task, _dict(oracles.get(run.task_version_id))))
            instrument = {
                "stack": row.get("stack"),
                "eval_suite": eval_suite.ref
                if eval_suite
                else planned_suite or (evaluation.eval_suite.ref if evaluation else None),
            }
            if instrument not in task_record["selected_instruments"]:
                task_record["selected_instruments"].append(instrument)
        result = _dict(evaluation.result if evaluation else None)
        stages = _dict(evaluation.stages if evaluation else None)
        calls = Counter()
        for attempt in by_run[row["run_id"]]:
            calls.update(attempt["calls_by_class"])
        study_id = str(run.study_id) if run.study_id else None
        study_ids.add(study_id)
        verified_protocol = bool(manifest) and manifest.get("protocol") in {"app@1", "slot@1"}
        execution = {
            "run_id": row["run_id"],
            "group": row["group"],
            "model": row["model"],
            "task": row["task"],
            "study_id": study_id,
            "kind": run.kind,
            "protocol": manifest.get("protocol") or run.protocol,
            "manifest_recorded": bool(manifest),
            "manifest_schema": manifest.get("manifest_schema"),
            "task_hash": run.task_hash,
            "prompt_hash": run.prompt_hash,
            "config_fingerprint": run.config_fingerprint,
            "seed": run.seed,
            "harness": "/".join(view.harness) if view.harness else None,
            "harness_preset": harness.get("preset_ref"),
            "harness_options": harness.get("options"),
            "sampling_policy": sampling.get("policy"),
            "sampling_forced_params": sampling.get("forced_params"),
            "sampling_seed_forwarded": sampling.get("seed_forwarded"),
            "resources_ref": _dict(manifest.get("resources")).get("ref"),
            "resource_policy": _dict(manifest.get("resources")).get("effective"),
            "endpoint_context_limit": _dict(model.get("endpoint")).get("num_ctx"),
            "endpoint_kind": _dict(model.get("endpoint")).get("kind"),
            "cost_basis": _dict(model.get("endpoint")).get("cost_basis"),
            "pricing": model.get("pricing"),
            "stack": row.get("stack"),
            "starter": starter.get("ref") or row.get("starter"),
            "starter_tree_hash": starter.get("tree_hash"),
            "auth_mode": view.auth_mode,
            "source_policy": "collected_source_unchanged" if verified_protocol else "unknown",
            "lock_source": run.lock_source or None,
            "registry_snapshot_at": run.registry_snapshot_at.isoformat() if run.registry_snapshot_at else None,
            "cell_attempt": run.cell_attempt,
            "generation_attempts": len(by_run[row["run_id"]]) or None,
            "calls_by_class": dict(calls) if by_run[row["run_id"]] else None,
            "outcome": run.outcome,
            "outcome_reason": run.outcome_reason,
            "evaluation_id": evaluation.pk if evaluation else None,
            "planned_eval_suite": planned_suite,
            "eval_suite": evaluation.eval_suite.ref if evaluation else None,
            "evaluation_status": evaluation.status if evaluation else None,
            "evaluation_mode": evaluation.mode if evaluation else None,
            "plan_sha256": result.get("plan_sha256"),
            "fixtures_sha256": result.get("fixtures_sha256"),
            "tester_version": result.get("tester_version"),
            "compiler_version": result.get("compiler_version"),
            "build_attempts": _attempt_count(stages.get("build")),
            "tester_attempts": _attempt_count(stages.get("tester")),
            "stage_statuses": {k: _dict(v).get("status") for k, v in stages.items() if v},
            "stage_reasons": {k: _dict(_dict(v).get("detail")).get("reason") for k, v in stages.items() if v},
            "baseline_pass_rate": _dict(result.get("metrics")).get("baseline_pass_rate"),
            "over_baseline": _dict(result.get("metrics")).get("over_baseline"),
        }
        executions.append(execution)
        # Add recorded provenance to the per-run table used by the portable workflow.
        row.update({k: execution[k] for k in ("lock_source", "generation_attempts", "outcome_reason", "study_id")})
        for check in result.get("checks", []):
            key = (row["task"], execution["eval_suite"] or "", result.get("plan_sha256") or "", check["id"])
            record = checks.setdefault(
                key,
                {
                    "task": row["task"],
                    "eval_suite": execution["eval_suite"],
                    "plan_sha256": result.get("plan_sha256"),
                    "check_id": check["id"],
                    "category": check.get("category"),
                    "endpoint": check.get("endpoint"),
                    "stated_in": check.get("stated_in"),
                    "source": check.get("source"),
                    "overlay": check.get("overlay"),
                    "statuses": Counter(),
                    "runs": 0,
                },
            )
            record["statuses"][check["status"]] += 1
            record["runs"] += 1
        for probe in result.get("probes", []):
            key = (row["task"], probe["id"], probe.get("cwe", ""), probe.get("kind", ""))
            probes[key].append((app_unit(row), probe, bool(row["evaluated"])))
    coverage = []
    for group in sorted({r["group"] for r in rows}):
        for task in sorted({r["task"] for r in rows}):
            selected = [r for r in rows if r["group"] == group and r["task"] == task]
            coverage.append(
                {
                    "group": group,
                    "task": task,
                    "apps": len({app_unit(r) for r in selected}),
                    "runs": len(selected),
                    "evaluated_runs": sum(bool(r["evaluated"]) for r in selected),
                    "analyzed_runs": sum(bool(r["analyzed"]) for r in selected),
                },
            )
    return {
        "execution": {
            "runs": executions,
            "attempts": attempt_rows,
            "study_history": _study_history(study, set(by_id)),
            "lock_source_strata": _strata(executions, rows),
            "coverage": {
                "runs": len(rows),
                "manifests_recorded": sum(r["manifest_recorded"] for r in executions),
                "runs_with_attempt_records": sum(bool(value) for value in by_run.values()),
                "runs_with_evaluation": sum(r["evaluation_id"] is not None for r in executions),
                "studies": sorted(s for s in study_ids if s),
                "includes_ad_hoc": None in study_ids,
                "mixed_study_scope": len(study_ids) > 1,
            },
            "notes": [
                "Source policy describes recorded app@1/slot@1 behavior; it is unknown without a manifest.",
                "The build preserves collected source. The platform may materialize missing or incomplete locks.",
                "Lock-source strata are observational: different apps can require platform locks. "
                "They do not estimate repair benefit.",
                "No paired original-versus-materialized deployment outcome is recorded here; "
                "repeated evaluations are not that experiment.",
                "Attempts count harness executions, not agent turns. "
                "A harness can make multiple model/tool calls within one attempt.",
                "Infrastructure retries and one harness-crash retry are permitted; "
                "missing attempt records do not establish no retries.",
                "Endpoint limits and pricing come from frozen manifests. "
                "Catalog-sourced parameters may reflect subsequently edited catalog values.",
                "A zero quoted price can describe a local endpoint or subscription allocation; "
                "it does not establish zero operating cost or economic superiority.",
            ],
        },
        "requirements": {
            "tasks": [tasks[k] for k in sorted(tasks)],
            "checks": list(checks.values()),
            "coverage": coverage,
            "notes": [
                "Tasks list selected immutable specifications and prose variants; "
                "representativeness is not established.",
                "Check inventory covers recorded evaluations under the report's selected instrument; "
                "endpoint linkage is explicit when recorded.",
                "Instrument coverage uses the explicitly selected suite, or the frozen planned suite by default, "
                "even if an invalid output was never evaluated. Evaluation availability is reported separately.",
                "An endpoint or UI requirement without a linked recorded check is not established as tested.",
                "Task oracle evidence is separate from generic probe controls. "
                "An empty oracle map means no task reference validation is recorded; "
                "a pass applies only to the recorded stack and evaluation suite.",
                "Zero cells in the coverage grid are absent samples, not failed apps; "
                "groups may deliberately target different tasks.",
            ],
        },
        "runtime_security": _probe_bounds(probes),
    }
