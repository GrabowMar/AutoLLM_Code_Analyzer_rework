"""Bind completed imported experiments to their protocols and selected source cohort."""

import copy
import hashlib
import json
import math
import re
from collections import Counter

_BLOCKS = {"lock_materialization", "feature_lifecycle"}
_ARMS = {"collected", "materialized"}
_ENDPOINTS = {
    "build": "known_build_success",
    "deployment": "known_deployment_success",
    "all_checks": "known_end_to_end_all_checks_success",
}


def _require(condition, message):
    if not condition:
        detail = f"supplemental_evidence: {message}"
        raise ValueError(detail)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _ids(value, label):
    _require(isinstance(value, list) and bool(value), f"{label} must be a nonempty list")
    _require(all(isinstance(item, str) and bool(item) for item in value), f"{label} needs string identities")
    _require(len(set(value)) == len(value), f"{label} contains duplicate identities")
    return set(value)


def _protocol(evidence, name):
    expected = evidence.get("protocol_sha256")
    _require(_sha(expected), f"{name} needs a protocol SHA-256")
    raw = evidence.get("protocol_json")
    if raw is not None:
        _require(isinstance(raw, str), f"{name} protocol_json must be text")
        _require(hashlib.sha256(raw.encode()).hexdigest() == expected, f"{name} protocol hash mismatch")
        try:
            protocol = json.loads(raw)
        except ValueError as exc:
            detail = f"supplemental_evidence: {name} protocol is not JSON"
            raise ValueError(detail) from exc
        if "protocol" in evidence:
            _require(evidence["protocol"] == protocol, f"{name} protocol objects differ")
    else:
        _require(name == "feature_lifecycle", "lock experiment needs its original protocol_json")
        protocol = evidence.get("protocol")
        raw = json.dumps(protocol, sort_keys=True, allow_nan=False)
        _require(hashlib.sha256(raw.encode()).hexdigest() == expected, f"{name} protocol hash mismatch")
    _require(isinstance(protocol, dict), f"{name} protocol must be an object")
    try:
        json.dumps(protocol, allow_nan=False)
    except (TypeError, ValueError) as exc:
        detail = "supplemental_evidence: protocol must contain finite JSON values"
        raise ValueError(detail) from exc
    selected = protocol.get("runs" if name == "lock_materialization" else "selected_runs")
    _require(isinstance(selected, list) and bool(selected), f"{name} protocol needs selected runs")
    _require(all(isinstance(row, dict) for row in selected), f"{name} selected runs must be objects")
    _ids([row.get("run_id") for row in selected], f"{name} protocol selection")
    return protocol, {row["run_id"]: row for row in selected}


def _source(observation, planned, name, evidence, protocol, *, by_id):
    run_id = observation["run_id"]
    tree = observation.get("original_source_tree_sha256" if name == "lock_materialization" else "tree_hash")
    _require(_sha(tree), f"{name} source tree is missing for run {run_id}")
    planned_tree = planned.get("original_tree_hash" if name == "lock_materialization" else "tree_hash")
    if name == "lock_materialization" and planned_tree is None:
        # Older frozen protocols omitted this field; preserve that omission and bind the later
        # lookup through their immutable run/file/evaluation fingerprints instead of rewriting them.
        binding = evidence.get("source_tree_bindings")
        _require(isinstance(binding, dict), "lock experiment needs the post-freeze source binding")
        _require(binding.get("passed") is True, "lock source binding did not pass")
        _require(binding.get("protocol_sha256") == evidence["protocol_sha256"], "lock source binding protocol differs")
        state = protocol.get("original_state")
        _require(isinstance(state, dict) and bool(state), "lock protocol has no original state")
        _require(binding.get("expected_state") == binding.get("observed_state") == state, "lock source state changed")
        hashes = binding.get("tree_hashes")
        _require(isinstance(hashes, dict) and hashes.get(run_id) == tree, "lock source binding tree differs")
        _require(
            isinstance(evidence.get("source_binding_note"), str) and bool(evidence["source_binding_note"]),
            "lock source binding needs its provenance note",
        )
    else:
        _require(tree == planned_tree, f"{name} source tree differs from its protocol for run {run_id}")
    if by_id is not None:
        _require(run_id in by_id, f"{name} contains a run outside this report's cohort")
        _require(tree == by_id[run_id].get("tree_hash"), f"{name} source tree differs for run {run_id}")


def _lock_outcomes(row):
    outcome = row.get("outcome")
    _require(
        isinstance(outcome, str) and outcome in {"tested", "build_fail", "start_fail", "infra_error"},
        "lock observation is not terminal",
    )
    for key in ("build_ok", "start_ok", "all_checks_passed"):
        _require(
            key in row and (row[key] is None or isinstance(row[key], bool)),
            f"lock {key} must be boolean or null",
        )
    _require(isinstance(row.get("functional_measured"), bool), "lock measurement status is missing")
    rate = row.get("functional_pass_rate")
    _require(rate is None or (type(rate) in {int, float} and 0 <= rate <= 1), "lock pass rate outside [0, 1]")
    if outcome == "tested":
        _require(
            row["build_ok"] is True and row["start_ok"] is True and row["functional_measured"],
            "tested lock observation lacks successful prerequisites",
        )
        _require(rate is not None and row["all_checks_passed"] is (rate == 1), "lock functional outcomes disagree")
    else:
        _require(
            not row["functional_measured"] and rate is None and row["all_checks_passed"] is None,
            "unmeasured lock observation contains functional grades",
        )
        if outcome == "build_fail":
            _require(row["build_ok"] is False and row["start_ok"] is not True, "lock build failure is inconsistent")
        elif outcome == "start_fail":
            _require(row["build_ok"] is True and row["start_ok"] is False, "lock start failure is inconsistent")
        else:
            _require(isinstance(row.get("error"), str) and bool(row["error"]), "unknown lock outcome needs an error")
    computed = {
        "known_build_success": True if row["build_ok"] is True else False if outcome == "build_fail" else None,
        "known_deployment_success": True
        if row["start_ok"] is True
        else False
        if outcome in {"build_fail", "start_fail"}
        else None,
        "known_end_to_end_all_checks_success": row["all_checks_passed"]
        if row["functional_measured"]
        else False
        if outcome in {"build_fail", "start_fail"}
        else None,
    }
    for key, expected in computed.items():
        _require(key in row and row[key] is expected, f"lock {key} disagrees with observed outcome")
    _require(type(row.get("attempt_count")) is int and row["attempt_count"] > 0, "lock attempt count is invalid")
    _require(
        isinstance(row.get("completed_at"), str) and bool(row["completed_at"]),
        "lock observation has not completed",
    )
    if "attempt_outcomes" in row:
        attempts = row["attempt_outcomes"]
        _require(
            isinstance(attempts, list) and len(attempts) == row["attempt_count"] and attempts[-1] == outcome,
            "lock attempt history disagrees",
        )
    metrics = row.get("metrics")
    if row["functional_measured"] and metrics:
        _require(isinstance(metrics, dict), "lock metrics must be an object")
        total, passed = metrics.get("agent_checks"), metrics.get("agent_passed")
        _require(
            type(total) is int and total > 0 and type(passed) is int and 0 <= passed <= total,
            "lock measured check counts are invalid",
        )
        _require(math.isclose(rate, passed / total, abs_tol=1e-12), "lock pass rate disagrees with check counts")


def _locks(evidence, protocol, selected):
    _require(_ids(protocol.get("arms"), "lock arms") == _ARMS, "unexpected lock experiment arms")
    counts = Counter(row["run_id"] for row in evidence["observations"])
    _require(all(n == 2 for n in counts.values()), "every lock experiment run needs both arms")
    for key in ("planned_pairs", "completed_pairs"):
        _require(
            type(evidence.get(key)) is int and evidence[key] == len(selected),
            "lock experiment is missing planned pairs",
        )
    _require(_ids(evidence.get("run_ids"), "lock selection") == set(selected), "lock selection differs from protocol")
    for row in evidence["observations"]:
        planned = selected[row["run_id"]]
        for observed, key in (("context_hash", row["arm"] + "_context_hash"), ("plan_sha256", "plan_sha256")):
            _require(
                _sha(planned.get(key)) and row.get(observed) == planned[key],
                f"lock {observed} differs from protocol",
            )
        _require(
            row.get("lock_files") == planned.get("lock_files") and isinstance(planned.get("lock_files"), dict),
            "lock manifest differs from protocol",
        )
        _require(
            row.get("suite_ref") == protocol.get("suite") == evidence.get("suite_ref")
            and isinstance(protocol.get("suite"), str)
            and bool(protocol["suite"]),
            "lock suite differs from protocol",
        )
        _require(_sha(row.get("environment_hash")), "lock observation has no environment hash")
        _lock_outcomes(row)
    for run_id in selected:
        pair = [r for r in evidence["observations"] if r["run_id"] == run_id]
        _require(pair[0]["environment_hash"] == pair[1]["environment_hash"], "lock pair environments differ")


def _feature_controls(evidence, protocol, selected):
    controls = evidence.get("controls")
    _require(isinstance(controls, list) and bool(controls), "feature control results are missing")
    for key in ("module_sha256", "control_source_sha256"):
        _require(_sha(protocol.get(key)), f"feature protocol needs {key}")
    policy = protocol.get("policy")
    _require(isinstance(policy, dict), "feature protocol needs its control policy")
    mutants = policy.get("mutants")
    _require(isinstance(mutants, list), "feature protocol needs its declared mutants")
    expected_mutants = _ids(mutants, "feature mutants") if mutants else set()
    positive_tasks, actual_mutants, identities = set(), set(), []
    for control in controls:
        _require(
            isinstance(control, dict) and control.get("status") == "pass",
            "feature instrument controls did not pass",
        )
        task, mutant = control.get("task"), control.get("mutant")
        _require(isinstance(task, str) and isinstance(mutant, str), "feature control identity is missing")
        status = control.get("success_status")
        _require(type(status) is int and 200 <= status < 300, "feature control success status is invalid")
        identities.append((task, mutant, status))
        checks = control.get("checks")
        _require(isinstance(checks, list) and all(isinstance(c, dict) for c in checks), "feature control needs checks")
        observed_ids = _ids([c.get("id") for c in checks], "feature control checks")
        expected_ids = (protocol.get("expected_checks") or {}).get(task)
        if expected_ids is None:
            expected_ids = next((r.get("expected_checks") for r in selected.values() if r.get("task") == task), None)
        _require(
            observed_ids == _ids(expected_ids, "feature control expected checks"),
            "feature control checks are incomplete",
        )
        _require(
            all(
                isinstance(c.get("status"), str) and c["status"] in {"pass", "fail", "blocked", "not_applicable"}
                for c in checks
            ),
            "feature control has invalid check statuses",
        )
        failures = sorted(c["id"] for c in checks if c["status"] == "fail")
        recorded = control.get("actual_failed_checks")
        _require(
            isinstance(recorded, list)
            and all(isinstance(check_id, str) for check_id in recorded)
            and sorted(recorded) == failures,
            "feature control failure list disagrees with checks",
        )
        expected = control.get("expected_failed_check")
        if mutant == "positive":
            _require(
                expected is None and all(c["status"] in {"pass", "not_applicable"} for c in checks),
                "feature positive control did not satisfy its contract",
            )
            positive_tasks.add(task)
        else:
            _require(
                mutant in expected_mutants and isinstance(expected, str) and expected in failures,
                "feature mutant was not detected as declared",
            )
            actual_mutants.add(mutant)
    _require(len(identities) == len(set(identities)), "feature controls contain duplicate identities")
    _require(actual_mutants == expected_mutants, "feature controls are missing declared mutants")
    _require(
        {r.get("task") for r in selected.values()} <= positive_tasks,
        "feature controls are missing positive tasks",
    )


def _features(evidence, protocol, selected):
    declared = protocol.get("expected_checks", {})
    _require(isinstance(declared, dict), "feature expected_checks must be an object")
    _require(all(isinstance(r.get("task"), str) for r in selected.values()), "feature protocol needs task identities")
    _feature_controls(evidence, protocol, selected)
    for row in evidence["observations"]:
        planned = selected[row["run_id"]]
        _require(isinstance(planned.get("task"), str), "feature protocol needs a task")
        for key in ("task", "group", "model", "prose_variant", "auth_mode"):
            if key in planned:
                _require(row.get(key) == planned[key], f"feature {key} differs from protocol")
        expected = _ids(planned.get("expected_checks", declared.get(planned.get("task"))), "feature expected checks")
        _require(row.get("teardown") == "completed", "feature runtime teardown is incomplete")
        _require(
            isinstance(row.get("status"), str) and row["status"] in {"measured", "unavailable"},
            "feature runtime status is not terminal",
        )
        checks = row.get("checks")
        _require(isinstance(checks, list), "feature observations need checks")
        if row["status"] == "unavailable":
            _require(
                not checks and isinstance(row.get("reason"), str) and bool(row["reason"]),
                "unavailable feature observation needs an explicit reason and no measured checks",
            )
            continue
        _require(all(isinstance(c, dict) for c in checks), "feature checks must be objects")
        _require(
            _ids([c.get("id") for c in checks], "feature checks") == expected,
            "feature observation is missing planned checks",
        )
        for check in checks:
            _require(
                isinstance(check.get("status"), str)
                and check["status"] in {"pass", "fail", "blocked", "not_applicable"},
                "invalid feature check status",
            )
            _require(
                not {"run_id", "group", "task", "tree_hash"}.intersection(check),
                "feature check cannot override run identity",
            )
            if check["status"] in {"blocked", "not_applicable"}:
                _require(
                    isinstance(check.get("reason"), str) and bool(check["reason"]),
                    "unmeasured feature check needs a reason",
                )


def validate_evidence(value, rows=None):
    if value is None:
        return {}
    _require(isinstance(value, dict), "expected an object")
    _require(set(value) <= _BLOCKS, "unsupported experiment kind")
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as exc:
        detail = "supplemental_evidence: must contain finite JSON values"
        raise ValueError(detail) from exc
    by_id = {row["run_id"]: row for row in rows} if rows is not None else None
    for name, evidence in value.items():
        _require(isinstance(evidence, dict), f"{name} must be an object")
        _require(
            type(evidence.get("schema_version")) is int and evidence["schema_version"] == 1,
            f"{name} schema version must be 1",
        )
        _require(evidence.get("complete") is True, f"{name} must be completed before inclusion")
        observations = evidence.get("observations")
        _require(isinstance(observations, list) and bool(observations), f"{name} needs observations")
        protocol, selected = _protocol(evidence, name)
        identities = []
        for observation in observations:
            _require(isinstance(observation, dict), f"{name} observation must be an object")
            run_id = observation.get("run_id")
            _require(
                isinstance(run_id, str) and run_id in selected,
                f"{name} observation is outside protocol selection",
            )
            _source(observation, selected[run_id], name, evidence, protocol, by_id=by_id)
            if name == "lock_materialization":
                arm = observation.get("arm")
                _require(isinstance(arm, str) and arm in _ARMS, "unexpected lock experiment arm")
                identities.append((run_id, arm))
            else:
                identities.append(run_id)
        _require(len(identities) == len(set(identities)), f"{name} contains duplicate observations")
        _require({r["run_id"] for r in observations} == set(selected), f"{name} is missing selected runs")
        if name == "lock_materialization":
            _locks(evidence, protocol, selected)
        else:
            _features(evidence, protocol, selected)
    return value


def _lock_summary(observations):
    by_run = {}
    for row in observations:
        by_run.setdefault(row["run_id"], {})[row["arm"]] = row
    summary = {}
    for metric, key in _ENDPOINTS.items():
        cells = Counter()
        for arms in by_run.values():
            left, right = (arms[arm][key] for arm in ("collected", "materialized"))
            cells[
                "missing"
                if left is None or right is None
                else ("pass" if left else "fail") + "_to_" + ("pass" if right else "fail")
            ] += 1
        n = len(by_run) - cells["missing"]
        gain, loss = cells["fail_to_pass"], cells["pass_to_fail"]
        discordant = gain + loss
        exact = min(1.0, 2 * sum(math.comb(discordant, k) for k in range(min(gain, loss) + 1)) / 2**discordant)
        summary[metric] = {
            "pairs_known": n,
            "pairs_missing": cells["missing"],
            **{key: cells[key] for key in ("pass_to_pass", "fail_to_fail", "fail_to_pass", "pass_to_fail")},
            "paired_success_rate_difference": (gain - loss) / n if n else None,
            "interval": None,
            "inference": "Finite-cohort descriptive paired comparison; no independent-pair confidence interval. "
            "Tasks and endpoints recur.",
            "mcnemar_exact_two_sided": exact,
            "mcnemar_status": "Secondary sensitivity only; requires independent pairs, "
            "which this crossed fixed cohort does not establish.",
        }
    differences = [
        arms["materialized"]["functional_pass_rate"] - arms["collected"]["functional_pass_rate"]
        for arms in by_run.values()
        if all(row["functional_measured"] for row in arms.values())
    ]
    summary["conditional_functional"] = {
        "both_measured_pairs": len(differences),
        "mean_within_pair_pass_rate_difference": sum(differences) / len(differences) if differences else None,
        "minimum": min(differences) if differences else None,
        "maximum": max(differences) if differences else None,
        "improved_pairs": sum(x > 0 for x in differences),
        "unchanged_pairs": sum(x == 0 for x in differences),
        "worsened_pairs": sum(x < 0 for x in differences),
        "population": "Pairs measured in both arms only; do not interpret as whole-cohort improvement.",
    }
    summary["arms"] = {}
    for arm in sorted(_ARMS):
        rows = [r for r in observations if r["arm"] == arm]
        rates = [r["functional_pass_rate"] for r in rows if r["functional_measured"]]
        summary["arms"][arm] = {
            "observations": len(rows),
            "outcomes": dict(Counter(r["outcome"] for r in rows)),
            "attempts": sum(r["attempt_count"] for r in rows),
            "functional_measured": len(rates),
            "conditional_mean_pass_rate": sum(rates) / len(rates) if rates else None,
        }
    return summary


def bind_evidence(value, rows):
    evidence = copy.deepcopy(validate_evidence(value, rows))
    for name, payload in evidence.items():
        payload.pop("import_binding", None)
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if name == "lock_materialization":
            payload["summary"] = _lock_summary(payload["observations"])
            by_run = {}
            for row in payload["observations"]:
                by_run.setdefault(row["run_id"], {})[row["arm"]] = row
            payload["pairs"] = [
                {
                    "run_id": run_id,
                    "model": arms["collected"].get("model"),
                    "task": arms["collected"].get("task"),
                    **{metric: {arm: row[key] for arm, row in arms.items()} for metric, key in _ENDPOINTS.items()},
                    "both_functionally_measured": all(row["functional_measured"] for row in arms.values()),
                    "functional_pass_rate_difference": arms["materialized"]["functional_pass_rate"]
                    - arms["collected"]["functional_pass_rate"]
                    if all(row["functional_measured"] for row in arms.values())
                    else None,
                }
                for run_id, arms in by_run.items()
            ]
        else:
            observations = payload["observations"]
            payload["summary"] = {
                "selected_apps": len(observations),
                "observed_apps": len(observations),
                "runtime_status_counts": dict(Counter(r["status"] for r in observations)),
                "check_status_counts": dict(Counter(c["status"] for r in observations for c in r["checks"])),
                "teardowns_completed": len(observations),
            }
        payload["import_binding"] = {
            "source_payload_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "selected_source_trees_verified": True,
            "protocol_hash_verified": True,
            "summaries_recomputed": True,
            "origin": "Imported experiment bound to its protocol and selected source hashes; "
            "kept separate from primary suite outcomes.",
        }
    return evidence
