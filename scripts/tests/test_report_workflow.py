"""Execute the portable notebook workflow against complete and incomplete reports."""

# Standalone unittest runner also works outside the Django project's test environment.
# ruff: noqa: INP001, PT009

import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")

SOURCE = Path(__file__).resolve().parents[2] / "frontend/src/lib/components/reports/analysis_workflow.py"
SPEC = importlib.util.spec_from_file_location("analysis_workflow", SOURCE)
workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow)


def sample_report():
    rows = []
    for group in ("alpha", "beta"):
        for task in range(3):
            for repeat in range(3 if task == 0 else 1):
                rows.append(
                    {
                        "run_id": f"{group}-{task}-{repeat}",
                        "model": group,
                        "group": group,
                        "kind": "task",
                        "condition_id": group,
                        "unit_key": f"task:{task}@1",
                        "task": f"task-{task}@1",
                        "generated": True,
                        "analyzed": True,
                        "evaluated": True,
                        "deployed": task != 2,
                        "outcome": "tested" if task != 2 else "build_fail",
                        "pass_rate": 0.2 if task == 0 else 1 if task == 1 else 0,
                        "functional_passed": task == 1,
                        "loc": 100 + task * 50,
                        "code_unique_model": 2,
                        "code_unique_tests": 1,
                        "dependency_added": 1,
                        "dependency_inherited": 0,
                        "runtime_unique": 1,
                        "unique_issues": 5,
                        "avg_complexity": 1 + task,
                        "lizard_avg_ccn": 2 + task,
                        "cost": 0.1,
                        **{f"unique_normalized_{s}": 1 for s in workflow.SEVERITIES},
                    },
                )
    outcomes = [
        {"model": g, "pass_rate": 0.5, "deploy_rate": 2 / 3, "issues_per_kloc": 13.3, "cost_per_app": 0.1}
        for g in ("alpha", "beta")
    ]
    rates = [
        {
            "model": g,
            **{
                k: {"rate": 0.8, "ci95": [0.5, 1], "n": 3, "apps": 3}
                for k in ("generation_rate", "deploy_rate", "functional_rate", "end_to_end_deploy_rate")
            },
            "end_to_end_functional_bounds": {
                "apps": 3,
                "runs": 5,
                "unknown_apps": 1,
                "unknown_runs": 1,
                "lower": 1 / 3,
                "upper": 2 / 3,
            },
        }
        for g in ("alpha", "beta")
    ]
    sensitivity = {
        "draws": 200,
        "ranks": [{"model": g, "median_rank": 1, "rank_ci95": [1, 2], "p_rank_1": 0.5} for g in ("alpha", "beta")],
    }
    return {
        "report_id": "rpt_test",
        "report_type": "study_report",
        "tables": [],
        "report_data": {
            "per_app": rows,
            "model_outcomes": outcomes,
            "funnel": {"by_model": rates},
            "protocol": {
                "instrument_coverage": {"tools_common": ["lint"]},
                "settings": {"seed": 0},
                "execution": {
                    "runs": [{"run_id": "alpha-0-0", "lock_source": "model"}],
                    "lock_source_strata": [
                        {
                            "group": g,
                            "lock_source": "model",
                            "apps": 3,
                            "runs": 5,
                            "evaluated_apps": 3,
                            "deployed_rate": 2 / 3,
                        }
                        for g in ("alpha", "beta")
                    ],
                },
                "requirements": {
                    "tasks": [{"task": "task-0@1", "title": "Task 0"}],
                    "checks": [{"task": "task-0@1", "id": "creates-record"}],
                },
            },
            "issues": {
                "by_group_tool": [
                    {
                        "group": g,
                        "tool": tool,
                        "analysis_mode": "static",
                        "is_ai": False,
                        "is_dependency_scanner": tool == "dependency-scanner",
                        "raw_findings_per_app": value,
                        "apps_measured": 3,
                    }
                    for g in ("alpha", "beta")
                    for tool, value in (("lint", 2), ("clean", 0), ("dependency-scanner", 300))
                ],
                "tool_pairs": [
                    {"tool_a": "lint", "tool_b": "clean", "agreement": 0.25, "both": 1, "only_a": 2, "only_b": 1},
                ],
            },
            "correlations": {
                "model_parameters": [
                    {"model": "alpha", "context_window_effective": 1000},
                    {"model": "beta", "context_window_effective": 2000},
                ],
                "tests": [
                    {
                        "predictor": "context_window_effective",
                        "outcome": "deploy_rate",
                        "rho": 0.4,
                        "n_models": 2,
                        "p_holm": 0.9,
                        "q_bh": 0.9,
                        "ci95_bootstrap": [-1, 1],
                        "leave_one_out": {"sign_flips": []},
                    },
                ],
            },
            "ranking": {
                "ranking": [{"model": g, "topsis_rank": 1, "wsm_rank": 1} for g in ("alpha", "beta")],
                "equal_weights": {"ranks": {"alpha": 1, "beta": 1}},
                "monte_carlo_near_weights": sensitivity,
                "monte_carlo_any_weights": sensitivity,
                "data_uncertainty": sensitivity,
                "one_at_a_time": [
                    {
                        "criterion": "pass_rate",
                        "factor": 0.5,
                        "kendall_tau": 0.5,
                        "rank_reversals": [["alpha", "beta"]],
                        "leaders": ["beta"],
                    },
                ],
            },
            "planned_comparisons": {
                "baseline_condition": "alpha",
                "families": [
                    {
                        "metric": "pass_rate",
                        "status": "exploratory",
                        "comparisons": [
                            {
                                "condition": "beta",
                                "n_pairs": 3,
                                "mean_delta": 0.1,
                                "mean_delta_ci95": [-0.1, 0.3],
                                "p_adjusted": 0.8,
                                "p_wilcoxon": 0.4,
                            },
                        ],
                    },
                ],
            },
            "security_bounds": {"overall": [{"severity": "critical", "apps": 6, "zero_rate_upper95": 0.39}]},
            "runtime_security": {
                "by_probe": [
                    {
                        "probe_id": "authorization-boundary",
                        "apps_measured": 6,
                        "apps_exploited": 0,
                        "ci95": [0, 0.39],
                        "zero_rate_upper95": 0.39,
                    },
                ],
            },
            "validity": [{"code": "small_n", "level": "warning", "message": "Small number of models"}],
        },
    }


def selection_report(groups=("alpha", "beta", "gamma"), tasks=("one@1", "two@1", "three@1", "four@1")):
    """A cross-experiment report: no single experiment in scope, units pooled from two experiments."""
    report = sample_report()
    data = report["report_data"]
    data["protocol"]["scope"] = {
        "study_id": None,
        "selection": "latest_unique",
        "study_ids": ["s-1", "s-2"],
        "models": None,
        "tasks": None,
        "common_tasks_only": False,
    }
    data.pop("planned_comparisons")
    matrix = []
    superseded = []
    for g, group in enumerate(groups):
        for t, task in enumerate(tasks):
            if (g, t) == (1, 3):
                continue  # no attempt: the payload omits the unit
            status, outcome = "selected", "tested"
            if (g, t) == (0, 1):
                outcome = "build_fail"
            elif (g, t) == (1, 2):
                status, outcome = "attrition", "infra_error"
            elif (g, t) == (2, 0):
                status, outcome = "in_flight", None
            older = [f"old-{g}-{t}"] if t == 0 and g < 2 else []
            matrix.append(
                {
                    "group": group,
                    "task": task,
                    "status": status,
                    "run_id": f"run-{g}-{t}",
                    "study_id": "s-2" if older else "s-1",
                    "study_name": "Second" if older else "First",
                    "outcome": outcome,
                    "created_at": "2026-11-10T10:00:00Z",
                    "attempts": 1 + len(older),
                    "superseded_run_ids": older,
                },
            )
            superseded.extend(
                {
                    "run_id": run,
                    "group": group,
                    "task": task,
                    "study_id": "s-1",
                    "study_name": "First",
                    "outcome": "tested",
                    "usable": True,
                    "created_at": "2026-11-01T10:00:00Z",
                    "replaced_by": f"run-{g}-{t}",
                    "reason": "newer_usable_run",
                }
                for run in older
            )
    data["selection"] = {
        "mode": "latest_unique",
        "rule": "Per group and task, the newest usable run.",
        "filters": {"study_ids": ["s-1", "s-2"], "models": None, "tasks": None, "common_tasks_only": False},
        "studies": [
            {"id": "s-1", "name": "First", "status": "completed", "runs_considered": 12, "runs_selected": 9},
            {"id": "s-2", "name": "Second", "status": "completed", "runs_considered": 2, "runs_selected": 2},
        ],
        "counts": {"candidates": 14, "selected": 9, "superseded": len(superseded), "attrition_cells": 1},
        "groups": [
            {
                "group": group,
                "model": group,
                "treatment_hash": "924d5065",
                "conditions": [g],
                "studies": ["s-1", "s-2"],
                "resource_policies": ["final@1"],
                "tasks_selected": sum(c["group"] == group and c["status"] == "selected" for c in matrix),
                "tasks_in_scope": len(tasks),
                "outcomes": {"tested": 1},
            }
            for g, group in enumerate(groups)
        ],
        "tasks": list(tasks),
        "matrix": matrix,
        "superseded": superseded,
        "analyses": {
            "rule": "Newest measured result per run and tool.",
            "by_launch": [
                {
                    "launch_id": "l-1",
                    "example_name": "FINAL static",
                    "analysis_runs": 4,
                    "results_selected": 9,
                    "results_superseded": 2,
                },
            ],
            "by_label": [
                {
                    "label": "FINAL static",
                    "example_name": "FINAL static 1a2b3c4d",
                    "analysis_runs": 4,
                    "results_selected": 9,
                    "results_superseded": 0,
                },
                {
                    "label": "SHALLOW dynamic",
                    "example_name": "SHALLOW dynamic 5e6f7a8b",
                    "analysis_runs": 1,
                    "results_selected": 0,
                    "results_superseded": 2,
                },
            ],
            "tools_mixed_launches": [],
            "tools_mixed_labels": [],
        },
        "common_tasks": [],
        "dropped_tasks": [],
    }
    return report


class WorkflowTests(unittest.TestCase):
    def test_selection_categories_keep_measured_failures_apart_from_attrition(self):
        category = workflow.selection_category
        self.assertEqual(category({"status": "selected", "outcome": "tested"}), "tested")
        for outcome in ("build_fail", "start_fail", "output_invalid"):
            self.assertEqual(category({"status": "selected", "outcome": outcome}), "failed")
        self.assertEqual(category({"status": "selected", "outcome": None}), "selected")
        self.assertEqual(category({"status": "attrition", "outcome": "infra_error"}), "attrition")
        self.assertEqual(category({"status": "in_flight"}), "in_flight")
        self.assertEqual(category({"status": "missing"}), "missing")
        self.assertEqual(category(None), "missing")
        # Selected but outside the analysed sample: truncated, or its task dropped for complete cases.
        left_out = {"status": "selected", "outcome": "tested", "in_sample": False, "excluded": "truncated"}
        self.assertEqual(category(left_out), "excluded")
        self.assertEqual(category({"status": "selected", "outcome": "tested", "task": "x@1"}, {"x@1"}), "excluded")
        self.assertEqual(category({"status": "selected", "outcome": "tested", "task": "y@1"}, {"x@1"}), "tested")

    def test_cross_experiment_selection_exports_every_unit_attempt_and_coverage_figure(self):
        report = selection_report()
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(report, directory)
            manifest = result["manifest"]
            self.assertEqual(manifest["status"], "completed", manifest["steps"])
            step = next(s for s in manifest["steps"] if s["name"] == "sample_selection")
            self.assertEqual(step["status"], "completed", step)
            root = Path(directory)
            matrix = workflow.pd.read_csv(root / "selection_matrix.csv", keep_default_na=False)
            # The omitted unit is a missing attempt, not a dropped row.
            self.assertEqual(len(matrix), 12)
            units = matrix.set_index(["group", "task"])
            self.assertEqual(units.loc[("beta", "four@1"), "category"], "missing")
            self.assertEqual(units.loc[("alpha", "two@1"), "category"], "failed")
            self.assertEqual(units.loc[("beta", "three@1"), "category"], "attrition")
            self.assertEqual(units.loc[("gamma", "one@1"), "category"], "in_flight")
            self.assertEqual(units.loc[("alpha", "one@1"), "superseded_run_ids"], "old-0-0")
            self.assertEqual(units.loc[("alpha", "one@1"), "attempts"], 2)
            superseded = workflow.pd.read_csv(root / "selection_superseded.csv")
            self.assertEqual(sorted(superseded["run_id"]), ["old-0-0", "old-1-0"])
            self.assertEqual(set(superseded["reason"]), {"newer_usable_run"})
            coverage = workflow.pd.read_csv(root / "selection_coverage.csv").set_index("group")
            self.assertEqual(list(coverage.columns), ["one@1", "two@1", "three@1", "four@1"])
            self.assertEqual(list(coverage.index), ["alpha", "beta", "gamma"])
            self.assertEqual(coverage.loc["beta", "four@1"], "missing")
            for ext in ("png", "svg"):
                self.assertGreater((root / f"selection_coverage.{ext}").stat().st_size, 100)
            svg = (root / "selection_coverage.svg").read_text()
            self.assertIn("2 experiments pooled", svg)
            self.assertTrue((root / "selection_groups.csv").exists())
            self.assertTrue((root / "selection_studies.csv").exists())
            self.assertTrue((root / "selection_analyses_by_launch.csv").exists())
            by_label = workflow.pd.read_csv(root / "selection_analyses_by_label.csv")
            self.assertEqual(list(by_label["label"]), ["FINAL static", "SHALLOW dynamic"])
            self.assertFalse((root / "selection_tools_mixed_labels.csv").exists())
            self.assertIn("## Sample selection", (root / "methods.md").read_text())
            evidence = {r["check"]: r for r in manifest["evidence_checks"]}
            self.assertEqual(evidence["sample_selection"]["status"], "warning")
            self.assertIn("different numbers of tasks", evidence["sample_selection"]["detail"])
            with zipfile.ZipFile(result["archive"]) as bundle:
                names = set(bundle.namelist())
            self.assertTrue({"selection.json", "selection_matrix.csv", "selection_superseded.csv"} <= names)

    def test_complete_case_selection_labels_dropped_tasks_and_keeps_an_empty_superseded_table(self):
        report = selection_report()
        selection = report["report_data"]["selection"]
        selection["filters"]["common_tasks_only"] = True
        selection["superseded"] = []
        selection["dropped_tasks"] = ["four@1"]
        selection["tasks"] = ["one@1", "two@1", "three@1"]
        for group in selection["groups"]:
            group["tasks_selected"] = 3
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(report, directory)
            self.assertEqual(result["manifest"]["status"], "completed", result["manifest"]["steps"])
            root = Path(directory)
            superseded = workflow.pd.read_csv(root / "selection_superseded.csv")
            self.assertTrue(superseded.empty)
            self.assertEqual(list(superseded.columns), list(workflow.SELECTION_SUPERSEDED_COLUMNS))
            coverage = workflow.pd.read_csv(root / "selection_coverage.csv")
            self.assertIn("four@1", coverage.columns)
            self.assertIn("four@1 (dropped)", (root / "selection_coverage.svg").read_text())
            evidence = {r["check"]: r["status"] for r in result["manifest"]["evidence_checks"]}
            self.assertEqual(evidence["sample_selection"], "recorded")
            # The selected runs of the dropped task are outside the sample: their own category, never "tested".
            matrix = workflow.pd.read_csv(root / "selection_matrix.csv", keep_default_na=False)
            dropped = matrix[matrix["task"] == "four@1"]
            self.assertEqual(set(dropped["category"]), {"excluded", "missing"})
            self.assertEqual(set(dropped.loc[dropped["category"] == "excluded", "excluded"]), {"dropped_task"})
            self.assertEqual(set(dropped["in_sample"].astype(str)), {"False"})
            self.assertEqual(coverage.set_index("group").loc["alpha", "four@1"], "excluded")

    def test_truncated_runs_left_out_after_selection_are_counted_apart_and_named_in_methods(self):
        report = selection_report()
        selection = report["report_data"]["selection"]
        for cell in selection["matrix"]:
            cell["in_sample"] = cell["status"] == "selected"
            cell["excluded"] = None
        left_out = next(c for c in selection["matrix"] if (c["group"], c["task"]) == ("alpha", "three@1"))
        left_out.update(in_sample=False, excluded="truncated")
        selection["counts"].update(selected=8, truncated_excluded=1)
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(report, directory)
            self.assertEqual(result["manifest"]["status"], "completed", result["manifest"]["steps"])
            root = Path(directory)
            matrix = workflow.pd.read_csv(root / "selection_matrix.csv", keep_default_na=False)
            units = matrix.set_index(["group", "task"])
            self.assertEqual(units.loc[("alpha", "three@1"), "category"], "excluded")
            self.assertEqual(units.loc[("alpha", "three@1"), "excluded"], "truncated")
            # The selected categories add up to the analysed sample.
            in_sample = matrix["category"].isin(["tested", "failed", "selected"]).sum()
            self.assertEqual(in_sample, selection["counts"]["selected"])
            self.assertEqual(int(matrix["in_sample"].astype(str).eq("True").sum()), selection["counts"]["selected"])
            svg = (root / "selection_coverage.svg").read_text()
            self.assertIn("Selected, left out", svg)
            methods = (root / "methods.md").read_text()
            self.assertIn("runs in the analysed sample: 8", methods)
            self.assertIn("left out as truncated (sensitivity analysis): 1", methods)

    def test_many_group_selection_renders_without_single_experiment_scope(self):
        groups = [f"endpoint/model-{i}" for i in range(10)]
        tasks = [f"task_{i:02d}@1" for i in range(30)]
        report = selection_report(groups=groups, tasks=tasks)
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(report, directory)
            self.assertEqual(result["manifest"]["status"], "completed", result["manifest"]["steps"])
            coverage = workflow.pd.read_csv(Path(directory) / "selection_coverage.csv")
            self.assertEqual(coverage.shape, (10, 31))
            self.assertGreater((Path(directory) / "selection_coverage.png").stat().st_size, 1000)

    def test_absent_or_empty_selection_is_skipped_without_selection_files(self):
        for data in ({}, {"selection": None}, {"selection": {"mode": "latest_unique"}}, {"selection": []}):
            with tempfile.TemporaryDirectory() as directory:
                result = workflow.run_report_workflow({"report_data": data}, directory)
                self.assertEqual(result["manifest"]["status"], "completed", (data, result["manifest"]["steps"]))
                step = next(s for s in result["manifest"]["steps"] if s["name"] == "sample_selection")
                self.assertEqual(step["status"], "skipped")
                self.assertFalse(any(f.startswith("selection") for f in result["manifest"]["files"]))
                self.assertNotIn("sample_selection", {r["check"] for r in result["manifest"]["evidence_checks"]})
                self.assertNotIn("## Sample selection", (Path(directory) / "methods.md").read_text())

    def test_final_assessment_metrics_are_plotted_exported_and_named_in_methods(self):
        report = sample_report()
        data = report["report_data"]
        for outcome in data["model_outcomes"]:
            outcome.update(deploy_rate_itt=0.6, pass_rate_itt=0.4, functional_passed_itt=0.2, list_cost_per_app=1.25)
        data["model_outcomes"][1]["list_cost_per_app"] = None  # unpriced: unknown, not free
        for row in data["funnel"]["by_model"]:
            row["deploy_rate_itt"] = {"rate": 0.6, "ci95": [0.3, 0.9], "n": 5, "apps": 3, "unknown_runs": 0}
        data["ranking"]["criteria"] = [
            {"key": "deploy_rate_itt", "label": "Deploy rate, failures counted as 0", "direction": "benefit"},
            {"key": "list_cost_per_app", "label": "Cost / app at list price (USD)", "direction": "cost"},
        ]
        data["protocol"]["ranking_preset"] = "final_assessment"
        data["protocol"]["cost_basis"] = {
            "source": "OpenRouter list prices of the model's catalog row / catalog twin as synced 2026-11-09",
            "unit": "USD per million tokens",
            "formula": "cost = uncached x prompt + cached x input_cache_read + ...",
            "per_model": [
                {"model": "alpha", "priced_as": "vendor/alpha", "prompt": 2.0, "completion": 10.0, "priced": True},
                {"model": "beta", "priced_as": None, "prompt": None, "priced": False, "missing": "no catalog twin"},
            ],
            "notes": ["Billed cost (cost_per_app, run.metrics.cost_usd) is kept for reference."],
        }
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(report, directory)
            manifest = result["manifest"]
            self.assertEqual(manifest["status"], "completed", manifest["steps"])
            basis = workflow.pd.read_csv(Path(directory) / "cost_basis_per_model.csv")
            self.assertEqual(basis["priced_as"].tolist()[0], "vendor/alpha")
            self.assertTrue((Path(directory) / "cost_basis.json").exists())
            scorecard = workflow.pd.read_csv(Path(directory) / "normalized_scorecard.csv")
            for column in ("Pass rate, failures counted as 0", "Deployment, failures counted as 0"):
                self.assertIn(column, scorecard)
            self.assertIn("Inverse cost at list price", scorecard)
            self.assertTrue(scorecard["Inverse cost at list price"].isna().iloc[1])
            # Beside them, the conditional pass rate and the billed cost say what they are.
            for column in ("Functional pass rate | measured evaluation", "Inverse billed cost"):
                self.assertIn(column, scorecard)
            self.assertNotIn("Inverse cost", scorecard)
            methods = (Path(directory) / "methods.md").read_text()
            self.assertIn("## Whole-cohort rates and cost at list price", methods)
            self.assertIn("as synced 2026-11-09", methods)
            self.assertIn("Ranking criteria: final_assessment (deploy_rate_itt, list_cost_per_app)", methods)
            evidence = {r["check"]: r for r in manifest["evidence_checks"]}
            self.assertIn("cost_basis_per_model.csv", evidence["cost_interpretation"]["detail"])

    def test_reports_without_final_assessment_metrics_keep_their_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(sample_report(), directory)
            self.assertFalse(any(f.startswith("cost_basis") for f in result["manifest"]["files"]))
            self.assertNotIn("Whole-cohort rates", (Path(directory) / "methods.md").read_text())
            scorecard = workflow.pd.read_csv(Path(directory) / "normalized_scorecard.csv")
            self.assertNotIn("Pass rate, failures counted as 0", scorecard)
            self.assertIn("Functional pass rate", scorecard)
            self.assertNotIn("Inverse billed cost", scorecard)
        self.assertEqual(workflow.final_assessment_methods({}), [])

    def test_unmeasured_prompt_subsets_export_missing_instead_of_plotting_zero(self):
        row = {
            "group": "model",
            **{
                f"{scope}_{metric}": None
                for scope in ("strict", "stated", "unstated")
                for metric in ("mean_pass_rate", "all_pass_rate")
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(
                {"report_data": {"requirement_alignment": {"by_group": [row]}}},
                directory,
            )
            self.assertEqual(result["manifest"]["status"], "completed")
            alignment = next(step for step in result["manifest"]["steps"] if step["name"] == "requirement_alignment")
            self.assertEqual(alignment["status"], "skipped")
            self.assertFalse((Path(directory) / "requirement_alignment_mean_pass_rate.svg").exists())
            self.assertTrue((Path(directory) / "requirement_alignment_by_group.csv").exists())

    def test_supplemental_experiments_export_unknowns_and_prompt_tagged_sensitivity(self):
        transitions = {"pass_to_pass": 1, "fail_to_fail": 0, "fail_to_pass": 0, "pass_to_fail": 0, "pairs_missing": 1}
        observations = [
            {
                "run_id": run,
                "arm": arm,
                "functional_measured": run == "measured",
                "functional_pass_rate": 0.5 if run == "measured" else None,
            }
            for run in ("measured", "unknown")
            for arm in ("collected", "materialized")
        ]
        alignment = {
            "group": "model",
            **{
                f"{scope}_{metric}": value
                for scope, value in (("strict", 0.5), ("stated", 1), ("unstated", 0))
                for metric in ("mean_pass_rate", "all_pass_rate")
            },
        }
        report = {
            "report_data": {
                "requirement_alignment": {"by_group": [alignment]},
                "per_app": [{"run_id": "measured", "stated_pass_rate": 1, "unstated_pass_rate": 0}],
                "supplemental_evidence": {
                    "lock_materialization": {
                        "complete": True,
                        "observations": observations,
                        "pairs": [{"run_id": "measured"}],
                        "summary": dict.fromkeys(("build", "deployment", "all_checks"), transitions),
                    },
                    "feature_lifecycle": {
                        "complete": True,
                        "observations": [
                            {
                                "run_id": "measured",
                                "checks": [
                                    {"id": "logout", "status": "pass"},
                                    {"id": "logout", "status": "blocked"},
                                    {"id": "reset", "status": "not_applicable"},
                                ],
                            },
                        ],
                    },
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(report, directory)
            self.assertEqual(result["manifest"]["status"], "completed", result["manifest"]["steps"])
            root = Path(directory)
            rates = workflow.pd.read_csv(root / "lock_materialization_matched_rates.csv")
            self.assertEqual(rates["run_id"].tolist(), ["measured"])
            counts = workflow.pd.read_csv(root / "lock_materialization_transitions.csv")
            self.assertEqual(counts["pairs_missing"].tolist(), [1, 1, 1])
            features = workflow.pd.read_csv(root / "feature_lifecycle_status_counts.csv").set_index("id")
            self.assertEqual(features.loc["logout", "blocked"], 1)
            self.assertEqual(features.loc["logout", "pass"], 1)
            self.assertEqual(features.loc["reset", "not_applicable"], 1)
            for name in (
                "lock_materialization_matched_rates",
                "lock_materialization_transitions",
                "feature_lifecycle_status_counts",
                "requirement_alignment_mean_pass_rate",
                "requirement_alignment_all_pass_rate",
            ):
                self.assertGreater((root / f"{name}.svg").stat().st_size, 100)

    def test_incomplete_supplemental_experiment_marks_archive_error(self):
        data = {
            "supplemental_evidence": {
                "lock_materialization": {"complete": False},
                "feature_lifecycle": {"complete": False},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow({"report_data": data}, directory)
            errors = {s["name"] for s in result["manifest"]["steps"] if s["status"] == "error"}
            self.assertEqual(errors, {"matched_lock_experiment", "supplemental_feature_checks"})
            self.assertEqual(result["manifest"]["status"], "error")

    def test_numeric_fingerprint_ignores_equivalent_json_number_forms_not_value_changes(self):
        native = {"values": [1.0, 0.0, -0.0, 0.125, None], "nested": {"count": 3.0, "measured": True}}
        browser = {"nested": {"measured": True, "count": 3}, "values": [1, 0, 0, 0.125, None]}
        self.assertEqual(workflow.numeric_data_fingerprint(native), workflow.numeric_data_fingerprint(browser))
        self.assertNotEqual(
            workflow.numeric_data_fingerprint(native),
            workflow.numeric_data_fingerprint({**browser, "values": [1, 0, 0, 0.126, None]}),
        )
        self.assertNotEqual(workflow.numeric_data_fingerprint(True), workflow.numeric_data_fingerprint(1))  # noqa: FBT003 - test numeric/boolean distinction

    def test_collected_zero_loc_survives_invalid_output_while_uncollected_loc_stays_missing(self):
        base = sample_report()["report_data"]["per_app"][0]
        rows = [
            dict(base, unit_key="collected", generated=False, loc=0, loc_backend=0, loc_frontend=0, cost=5),
            dict(base, unit_key="uncollected", generated=False, loc=None, loc_backend=None, loc_frontend=None),
            dict(base, unit_key="repeated", generated=True, loc=10, cost=2),
            dict(base, unit_key="repeated", generated=False, loc=0, cost=7),
        ]
        apps = workflow.app_observations(rows).set_index("unit_key")
        self.assertTrue(workflow.pd.isna(apps.loc["collected", "loc"]))
        self.assertEqual(apps.loc["collected", "collected_loc"], 0)
        self.assertTrue(workflow.pd.isna(apps.loc["uncollected", "loc"]))
        self.assertTrue(workflow.pd.isna(apps.loc["uncollected", "collected_loc"]))
        self.assertEqual(apps.loc["repeated", "loc"], 10)
        self.assertEqual(apps.loc["repeated", "collected_loc"], 5)
        self.assertEqual(apps.loc["repeated", "cost"], 2)
        self.assertTrue(workflow.pd.isna(apps.loc["collected", "cost"]))

    def test_archive_retains_executed_source_and_reports_partial_step_failure(self):
        duplicate = {
            "group": "model",
            "tool": "lint",
            "analysis_mode": "static",
            "category": "quality",
            "raw_findings_per_app": 0,
        }
        report = {"report_data": {"issues": {"by_group_tool": [duplicate, duplicate]}}}
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(report, directory)
            self.assertEqual(result["manifest"]["status"], "error")
            failed = [s for s in result["manifest"]["steps"] if s["status"] == "error"]
            self.assertEqual([s["name"] for s in failed], ["static_tool_findings"])
            with zipfile.ZipFile(result["archive"]) as archive:
                source = archive.read("analysis_workflow.py")
                self.assertEqual(source, SOURCE.read_bytes())
                self.assertEqual(hashlib.sha256(source).hexdigest(), result["manifest"]["workflow_source_sha256"])
                self.assertEqual(json.loads(archive.read("report.json")), report)
                self.assertEqual(json.loads(archive.read("manifest.json"))["status"], "error")

    def test_runtime_plot_does_not_claim_an_interval_when_report_withholds_it(self):
        report = {
            "report_data": {
                "runtime_security": {
                    "by_probe": [
                        {
                            "task": "task",
                            "probe_id": "probe",
                            "apps_measured": 3,
                            "apps_exploited": 0,
                            "ci95": None,
                            "zero_rate_upper95": None,
                        },
                    ],
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(report, directory)
            self.assertEqual(result["manifest"]["status"], "completed")
            svg = (Path(directory) / "runtime_security_bounds.svg").read_text()
            self.assertIn("Descriptive rates; no inferential interval", svg)
            self.assertNotIn("recorded 95% CI", svg)
            self.assertNotIn("one-sided zero-count upper bound", svg)

    def test_observed_deployment_retains_failures_and_requires_complete_repeat_evidence(self):
        base = sample_report()["report_data"]["per_app"][0]
        rows = [
            dict(base, unit_key="ungraded", task="ungraded", evaluated=False, deployed=None),
            dict(base, unit_key="failed", task="failed", outcome="build_fail", evaluated=False, deployed=None),
            dict(base, unit_key="mixed", task="mixed"),
            dict(base, unit_key="mixed", task="mixed", outcome="start_fail"),
            dict(base, unit_key="unknown", task="unknown", outcome="tested", outcome_reason="not_tested:build_only"),
            dict(
                base,
                unit_key="started",
                task="started",
                outcome_reason="not_tested:tester_absent",
                start_stage_status="ok",
            ),
            dict(base, unit_key="partial", task="partial"),
            dict(base, unit_key="partial", task="partial", outcome="infra_error"),
            dict(base, unit_key="partial-known", task="partial"),
        ]
        apps = workflow.app_observations(rows).set_index("unit_key")
        self.assertEqual(apps.loc["ungraded", "observed_deployment"], 1)
        self.assertTrue(workflow.pd.isna(apps.loc["ungraded", "deployed"]))
        self.assertEqual(apps.loc["failed", "observed_deployment"], 0)
        self.assertEqual(apps.loc["mixed", "observed_deployment"], 0.5)
        self.assertEqual(apps.loc["started", "observed_deployment"], 1)
        self.assertTrue(workflow.pd.isna(apps.loc["unknown", "observed_deployment"]))
        self.assertTrue(workflow.pd.isna(apps.loc["partial", "observed_deployment"]))
        self.assertEqual(apps.loc["partial", "deployment_unknown_runs"], 1)
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow({"report_data": {"per_app": rows}}, directory)
            self.assertEqual(result["manifest"]["status"], "completed")
            matrix = workflow.pd.read_csv(Path(directory) / "task_deployed.csv")
            self.assertEqual(matrix["failed"].iloc[0], 0)
            self.assertEqual(matrix["ungraded"].iloc[0], 1)
            self.assertEqual(matrix["mixed"].iloc[0], 0.5)
            self.assertTrue(matrix["partial"].isna().all())
            coverage = workflow.pd.read_csv(Path(directory) / "task_deployed_coverage.csv").set_index("task")
            self.assertEqual(coverage.loc["partial", "unknown_runs"], 1)
            self.assertEqual(coverage.loc["partial", "apps_known"], 1)
            self.assertEqual(coverage.loc["partial", "apps"], 2)

    def test_exploratory_fallback_retains_effects_when_tests_withheld(self):
        report = {
            "report_data": {
                "exploratory_comparisons": {
                    "families": [
                        {
                            "metric": "pass_rate",
                            "status": "exploratory",
                            "comparisons": [
                                {
                                    "condition": "beta vs alpha",
                                    "condition_a": "alpha",
                                    "condition_b": "beta",
                                    "n_pairs": 3,
                                    "mean_delta": 0.2,
                                    "mean_delta_ci95": [0.1, 0.3],
                                    "p_adjusted": None,
                                    "p_sign_adjusted": None,
                                    "skipped": "below_min_pairs",
                                },
                            ],
                        },
                    ],
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(report, directory)
            self.assertEqual(result["manifest"]["status"], "completed")
            self.assertTrue((Path(directory) / "exploratory_effect_1.svg").exists())
            self.assertFalse((Path(directory) / "planned_comparison_tests.csv").exists())
            tests = workflow.pd.read_csv(Path(directory) / "exploratory_comparison_tests.csv")
            self.assertEqual(tests.iloc[0]["skipped"], "below_min_pairs")
            self.assertTrue(workflow.pd.isna(tests.iloc[0]["p_adjusted"]))
            self.assertIn("p_sign_adjusted", tests)

    def test_task_oracle_records_do_not_manufacture_validation(self):
        protocol = {
            "requirements": {
                "tasks": [
                    {
                        "task": "one@1",
                        "oracle": {},
                        "selected_instruments": [{"stack": "stack@1", "eval_suite": "suite@1"}],
                    },
                ],
            },
        }
        self.assertEqual(workflow.task_oracle_evidence(protocol)["status"], "unavailable")
        protocol["requirements"]["tasks"][0]["oracle"] = {"stack@1": {"status": "fail", "eval_suite_ref": "suite@1"}}
        self.assertEqual(workflow.task_oracle_evidence(protocol)["status"], "warning")
        protocol["requirements"]["tasks"][0]["oracle"]["stack@1"]["status"] = "pass"
        self.assertEqual(workflow.task_oracle_evidence(protocol)["status"], "recorded")
        protocol["requirements"]["tasks"][0]["oracle"]["stack@1"]["eval_suite_ref"] = "suite@2"
        self.assertEqual(workflow.task_oracle_evidence(protocol)["status"], "unavailable")
        protocol["requirements"]["tasks"][0]["oracle"] = {
            "kit:stack@1": {"status": "pass", "eval_suite_ref": "suite@1"},
        }
        self.assertEqual(workflow.task_oracle_evidence(protocol)["status"], "unavailable")

    def test_thresholds_classify_runs_before_averaging_repeats(self):
        base = sample_report()["report_data"]["per_app"][0]
        rows = [
            dict(base, pass_rate=0),
            dict(base, pass_rate=1),
            dict(base, unit_key="second-task", pass_rate=1),
            dict(base, unit_key="missing-task", pass_rate=None),
        ]
        result = workflow.threshold_sensitivity(rows, [0.7, 1])
        self.assertEqual(result["rate"].tolist(), [0.75, 0.75])
        self.assertEqual(result["apps"].tolist(), [2, 2])
        self.assertEqual(result["measured_runs"].tolist(), [3, 3])
        boundary = workflow.threshold_sensitivity([dict(base, pass_rate=0.85)])
        self.assertEqual(boundary.loc[boundary["threshold"] == 0.85, "rate"].tolist(), [1.0])

    def test_task_deletion_uses_app_means_not_run_counts(self):
        frame = workflow.task_sensitivity(workflow.app_observations(sample_report()["report_data"]["per_app"]))
        rows = frame.loc[(frame["metric"] == "pass_rate") & (frame["group"] == "alpha")]
        self.assertEqual(len(rows), 3)
        self.assertAlmostEqual(rows["mean_without_task"].min(), 0.1)
        self.assertAlmostEqual(rows["mean_without_task"].max(), 0.6)
        self.assertTrue((rows["n_apps_remaining"] == 2).all())

    def test_model_observations_collapse_shared_model_arms(self):
        data = {
            "correlations": {"model_parameters": [{"model": "one", "context_window_effective": 100}]},
            "model_outcomes": [
                {"model": "first-arm", "model_id": "one", "pass_rate": 0.2},
                {"model": "second-arm", "model_id": "one", "pass_rate": 0.8},
            ],
        }
        observations = workflow.model_observations(data)
        self.assertEqual(len(observations), 1)
        self.assertAlmostEqual(observations.iloc[0]["pass_rate"], 0.5)

    def test_app_units_preserve_arms_average_repeats_and_missing(self):
        rows = sample_report()["report_data"]["per_app"]
        apps = workflow.app_observations(rows)
        self.assertEqual(len(apps), 6)
        self.assertEqual(apps["lizard_avg_ccn"].tolist(), [2, 3, 4, 2, 3, 4])
        for value in apps.groupby("group")["pass_rate"].mean():
            self.assertAlmostEqual(value, 0.4)
        self.assertNotAlmostEqual(sum(r["pass_rate"] for r in rows[:5]) / 5, 0.4)
        a = dict(
            rows[0],
            model="shared",
            condition_id="arm-a",
            group="arm-a",
            pass_rate=None,
            evaluated=False,
            analyzed=False,
            unique_issues=123,
        )
        b = dict(a, condition_id="arm-b", group="arm-b")
        separated = workflow.app_observations([a, b])
        self.assertEqual(len(separated), 2)
        self.assertTrue(separated["unique_issues"].isna().all())
        self.assertTrue(separated["pass_rate"].isna().all())

    def test_complete_report_renders_figures_and_exports_recorded_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(sample_report(), directory)
            manifest = result["manifest"]
            self.assertEqual(manifest["status"], "completed", manifest["steps"])
            self.assertEqual(len(manifest["files"]), len(set(manifest["files"])))
            skipped = {s["name"] for s in manifest["steps"] if s["status"] == "skipped"}
            self.assertEqual(
                skipped,
                {
                    "predictor_collinearity",
                    "matched_lock_experiment",
                    "supplemental_feature_checks",
                    "requirement_alignment",
                    "sample_selection",
                },
                manifest["steps"],
            )
            for name in (
                "normalized_scorecard",
                "static_tool_findings",
                "size_and_density",
                "severity_per_app",
                "functional_pass_rate",
                "task_deployed",
                "correlations",
            ):
                for ext in ("png", "svg"):
                    self.assertGreater((Path(directory) / f"{name}.{ext}").stat().st_size, 100)
            matrix = workflow.pd.read_csv(Path(directory) / "static_tool_findings.csv")
            self.assertNotIn("dependency-scanner", matrix)
            self.assertTrue((matrix["clean"] == 0).all())
            functional = workflow.pd.read_csv(Path(directory) / "functional_pass_rate.csv")
            self.assertEqual(functional["count"].tolist(), [3, 3])
            for value in functional["mean"]:
                self.assertAlmostEqual(value, 0.4)
            tests = workflow.pd.read_csv(Path(directory) / "correlation_tests.csv")
            self.assertEqual(tests["p_holm"].tolist(), [0.9])
            self.assertEqual(tests["n_models"].tolist(), [2])
            pairs = workflow.pd.read_csv(Path(directory) / "tool_overlap_pairs.csv")
            self.assertEqual(pairs["both"].tolist(), [1])
            self.assertTrue((Path(directory) / "methods.md").exists())
            self.assertTrue((Path(directory) / "execution_lock_source_strata.csv").exists())
            self.assertTrue((Path(directory) / "requirements_checks.csv").exists())
            self.assertTrue((Path(directory) / "runtime_security_bounds.svg").exists())
            effects = workflow.pd.read_csv(Path(directory) / "planned_comparison_tests.csv")
            self.assertIn("mean_delta_ci95", effects)
            bounds = workflow.pd.read_csv(Path(directory) / "full_cohort_functional_bounds.csv")
            self.assertAlmostEqual(bounds.iloc[0]["lower"], 1 / 3)
            self.assertAlmostEqual(bounds.iloc[0]["upper"], 2 / 3)
            self.assertEqual(bounds.iloc[0]["unknown_apps"], 1)
            populations = workflow.pd.read_csv(Path(directory) / "issue_populations_per_app.csv")
            self.assertEqual(populations["dependency_inherited"].tolist(), [0, 0])
            evidence = {r["check"]: r["status"] for r in manifest["evidence_checks"]}
            self.assertEqual(evidence["human_evaluator_validation"], "unavailable")
            self.assertEqual(evidence["task_oracle_validation"], "unavailable")
            self.assertEqual(evidence["paired_repair_effect"], "unavailable")
            with zipfile.ZipFile(result["archive"]) as bundle:
                self.assertEqual(set(bundle.namelist()), set(manifest["files"]))
                self.assertEqual(json.loads(bundle.read("manifest.json"))["data_sha256"], manifest["data_sha256"])

    def test_empty_report_is_explicitly_skipped_and_excludes_stale_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "old_graph.png").write_text("stale", encoding="utf-8")
            result = workflow.run_report_workflow({"report_data": {}}, directory)
            self.assertEqual(result["manifest"]["status"], "completed")
            self.assertGreater(result["manifest"]["steps_skipped"], 10)
            self.assertFalse(any(s["status"] == "error" for s in result["manifest"]["steps"]))
            with zipfile.ZipFile(result["archive"]) as bundle:
                self.assertNotIn("old_graph.png", bundle.namelist())

    def test_unmeasured_tool_is_missing_while_measured_zero_is_zero(self):
        report = sample_report()
        report["report_data"]["issues"]["by_group_tool"][0]["raw_findings_per_app"] = None
        report["report_data"]["model_outcomes"][0]["cost_per_app"] = None
        rows = report["report_data"]["per_app"]
        for row in rows:
            row["unique_normalized_critical"] = None
            if row["group"] == "alpha":
                row["dependency_added"] = None
        with tempfile.TemporaryDirectory() as directory:
            result = workflow.run_report_workflow(report, directory)
            self.assertEqual(result["manifest"]["status"], "completed", result["manifest"]["steps"])
            matrix = workflow.pd.read_csv(Path(directory) / "static_tool_findings.csv").set_index("group")
            self.assertTrue(workflow.pd.isna(matrix.loc["alpha", "lint"]))
            self.assertEqual(matrix.loc["alpha", "clean"], 0)
            populations = workflow.pd.read_csv(Path(directory) / "issue_populations_per_app.csv").set_index("group")
            self.assertTrue(workflow.pd.isna(populations.loc["alpha", "dependency_added"]))
            self.assertEqual(populations.loc["alpha", "dependency_inherited"], 0)
            self.assertIn("N/A", (Path(directory) / "normalized_scorecard.svg").read_text())
            self.assertFalse((Path(directory) / "severity_per_app.png").exists())
            severity = next(s for s in result["manifest"]["steps"] if s["name"] == "severity_per_app")
            self.assertEqual(severity["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
