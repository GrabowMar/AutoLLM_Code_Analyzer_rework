"""Choose one versioned evaluation instrument without falling back across suites."""

from backend.generation.evaluation.suite import _suite_from_ref


def selected_suite(config):
    ref = config.get("eval_suite")
    if ref is None or ref == "":
        return None
    if not isinstance(ref, str):
        msg = "eval_suite must be a versioned suite reference, such as eval-v2@2"
        raise ValueError(msg)  # noqa: TRY004 - configuration validation consistently raises ValueError
    try:
        return _suite_from_ref(ref.strip())
    except (LookupError, ValueError) as exc:
        msg = f"Invalid evaluation suite: {ref}"
        raise ValueError(msg) from exc


def evaluation_for_report(view, suite=None):
    if suite is None:
        return view.latest_evaluation()
    return view.latest_evaluation(suite.pinned_ref, final_only=True)


def planned_suite_ref(view):
    planned = (view.manifest().get("planned_evaluation") or {}).get("eval_suite") or {}
    return str(planned.get("ref") or "").split("#", 1)[0]


def selection_summary(rows, suite):
    changed = (
        sum(bool(r.get("planned_eval_suite")) and r["planned_eval_suite"] != suite.ref for r in rows) if suite else 0
    )
    return {
        "mode": "specified_suite" if suite else "planned_suite",
        "suite_ref": suite.pinned_ref if suite else None,
        "changed_from_plan_runs": changed,
        "runs_with_evaluation": sum(bool(r.get("eval_suite")) for r in rows),
        "generated_without_evaluation": sum(bool(r["generated"]) and not r.get("eval_suite") for r in rows),
        "note": "An explicit suite selects its latest final non-retest evaluation for every app. "
        "No other suite fills missing evaluations. Generation samples and source trees are unchanged.",
    }
