"""Separate recorded check outcomes by exposure in the selected prose variant."""

from collections import defaultdict
from statistics import fmean

from backend.statistics.services.runs import app_unit


def alignment_fields(result, variant, *, measured):
    flaky = set(result.get("flaky_checks") or [])
    counted = [c for c in result.get("checks") or [] if c.get("category") != "infra" and c["id"] not in flaky]
    known = [c for c in counted if isinstance(c.get("stated_in"), list)] if variant else []
    fields = {"prose_variant": variant, "statedness_unknown_checks": len(counted) - len(known)}
    for name, selected in (
        ("stated", [c for c in known if variant in c["stated_in"]]),
        ("unstated", [c for c in known if variant not in c["stated_in"]]),
    ):
        passed = sum(c.get("status") == "pass" for c in selected)
        fields[f"{name}_checks"] = len(selected) if measured else None
        fields[f"{name}_passed"] = passed if measured else None
        fields[f"{name}_pass_rate"] = passed / len(selected) if measured and selected else None
        fields[f"all_{name}_checks_passed"] = passed == len(selected) if measured and selected else None
    return fields


def alignment_section(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["group"]].append(row)

    def summary(items, group):
        apps = defaultdict(list)
        for row in items:
            apps[app_unit(row)].append(row)
        out = {"group": group, "runs": len(items), "apps": len(apps)}
        for name, field in (
            ("strict", "pass_rate"),
            ("stated", "stated_pass_rate"),
            ("unstated", "unstated_pass_rate"),
        ):
            observed = [r for r in items if r.get(field) is not None]
            app_rates, app_all = [], []
            for repeats in apps.values():
                values = [r[field] for r in repeats if r.get(field) is not None]
                if values:
                    app_rates.append(fmean(values))
                    app_all.append(fmean(v == 1 for v in values))
            out.update(
                {
                    f"{name}_measured_runs": len(observed),
                    f"{name}_measured_apps": len(app_rates),
                    f"{name}_all_pass_runs": sum(r[field] == 1 for r in observed),
                    f"{name}_mean_pass_rate": fmean(app_rates) if app_rates else None,
                    f"{name}_all_pass_rate": fmean(app_all) if app_all else None,
                },
            )
        out["statedness_unknown_checks"] = sum(r.get("statedness_unknown_checks") or 0 for r in items)
        return out

    return {
        "by_group": [summary(items, group) for group, items in sorted(groups.items())],
        "overall": summary(rows, "All"),
        "notes": [
            "Strict scores include all counted checks; the stated/unstated split uses recorded stated_in tags "
            "and the selected prose variant. Untagged checks remain unknown, never silently unstated.",
            "These are check-level exposure labels, not proof that every assertion was explicit in the prompt "
            "or that all requested behavior was tested. API checks do not validate interactive UI requirements.",
            "Rates condition on measured checks, averaging repeats within app units before averaging apps. "
            "All-pass run counts retain their run denominator; missing subsets do not count as success.",
            "This descriptive sensitivity retains the original strict instrument score and applies no new "
            "hypothesis tests or selection of successful outputs.",
        ],
    }
