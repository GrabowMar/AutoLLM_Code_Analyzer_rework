# Study tables

Reporting a study: build-healing ablation, deduplicated findings, manual-review sample, execution accounting, analysis plans and the study report.

Every table describes a *scope* of generation jobs (one experiment, one batch, or every job you own) and counts one tool result per job and tool (the latest that measured the app, `ToolResult.latest_ids_per_job`), so re-analyses never double-count and re-running a single tool only replaces that tool's findings. A retried experiment cell counts once, by its latest attempt (as on the experiment's Results tab). In the API and the UI the scope is limited to your own jobs (staff see everyone's); the `manage.py` commands below see every user's jobs in scope. AI-reviewer findings are opinions, not measurements, and are left out unless asked for. How analyses run and what each tool result status means is in [Analysis pipeline](/docs/ANALYSIS_PIPELINE); experiments themselves are in [Experiments](/docs/EXPERIMENTS).

## Build healing

Before a scaffolding or custom job's output is built, the platform can repair it. The setting is `build_healing` on the job, and the same field on an experiment is copied to every job at launch:

| Mode | Dependency inference | Runtime patches | Missing-file placeholders |
| --- | --- | --- | --- |
| `full` (default) | Requirements inferred from imports are appended to `requirements.txt` | Flask entry file: sqlite path, port 8000, `app.app_context()`; frontend files: unknown lucide icons (React and Vue), `react-router-dom` imports, the skeleton's Tailwind 4 stylesheet entry | A missing entry file or frontend component is stubbed |
| `no_deps` | — | yes | yes |
| `none` | — | — | A file the model never wrote is **removed**, including the skeleton's own stub |

Agent-mode output is always built verbatim, so it is never healed (`applicable: false`).

Every build records what it changed: `result_data.evaluation.build.healing` holds `{mode, applicable, changed, added_requirements, patches}` (patches as `<file>:<patch>`). The conformance outcome adds `metrics.functional.deployed` (the app built and answered its health check), `healing_mode` and `healing_changed`.

**Paired ablation.** Measuring healing by generating twice confounds it with sampling variance. Instead, re-evaluate the *same* output without healing:

```bash
python manage.py healing_ablation --experiment <uuid> --modes none,no_deps
# or: --batch <uuid>, or --jobs <uuid> <uuid> ...; --force re-runs modes a job already has; --limit N
```

`--modes` defaults to `none`. Results land in `result_data.healing_ablation[mode]`; the job's own evaluation is not touched. The command builds on apps-dind one job at a time, so don't run it while agent runs are live ([Execution](/docs/EXECUTION)). Agent jobs are skipped.

`GET /api/statistics/study/healing/?experiment_id=` (or `batch_id=`) returns the deploy rate per mode against the `full` baseline, overall (`totals`) and `by_model` / `by_condition` (per experiment condition) / `by_template`. For every other mode `<m>`:

| Key | Meaning |
| --- | --- |
| `n_<m>`, `deploy_rate_<m>` | Runs evaluated under that mode, and the share that deployed (percent) |
| `paired_<m>` | Runs evaluated under both `full` and `<m>` |
| `rescued_vs_<m>` | Runs that deploy only because they were healed |
| `broken_vs_<m>` | Runs that healing broke |
| `healing_effect_pp_vs_<m>` | The net effect, in percentage points |
| `mcnemar_p_vs_<m>` | McNemar's exact test on the discordant pairs |

Pairs are runs (one model output built under each mode). `totals`, every `by_*` row and each `added_requirements`/`patches` entry carry `apps` (distinct app × condition units) and `runs` (jobs).

The response also lists which packages were added (`added_requirements`), which patches fired (`patches`), one row per run (`per_app`), and `not_applicable` (agent builds).

## Deduplicated findings

Tools overlap: Ruff's `S` rules mirror Bandit, Semgrep covers much of Bandit, detect-secrets and Gitleaks both flag the same key, Ruff, flake8, Pylint and Vulture all report the same unused import, and pip-audit, osv-scanner and Trivy report the same vulnerable package under different advisory ids. Raw finding counts therefore count one defect several times.

`statistics/services/dedup.py` maps each finding to an *issue class*, in this order:

1. an `issue_key` the parser already set (DAST findings: `missing-header:<header>`, `cors-misconfiguration`);
2. for the vulnerability scanners (pip-audit, npm audit, osv-scanner, Trivy, retire.js), `vulnerable-dependency:<package>`, matched within one project directory rather than one file, so `requirements.txt`, `package.json` and a lockfile meet. Within a package, tools merge only when they name the same advisory under any of its ids (the rule id plus the CVE/GHSA/PYSEC `aliases` the parser stored), so two different advisories of one package stay two issues;
3. an explicit rule equivalence (Bandit ids and Ruff's `S` mirrors, the shared Ruff/flake8/Pylint/Vulture/Mypy/ESLint lint rules, eslint-react `react/no-danger` and the eslint-security rules Semgrep also covers, keyed by the reporting tool's slug (`eslint-security:security/detect-eval-with-expression`), Semgrep rule-name keywords matched as whole `-`/`_`-separated tokens (specific ones such as `md5` before generic ones such as `secret`), radon CC ranks D–F and lizard `CCN` as `high-complexity`, every secret-scanner finding as `hardcoded-secret`);
4. the finding's CWE (Bandit, Semgrep and the DAST scanners record it);
5. otherwise its own `tool:rule`, which never merges across tools.

Within one app, findings of the same class in the same file whose lines are within `line_tolerance` (default 2, Semgrep ranges included) form one issue; file-level findings merge only with other file-level ones. A single tool's findings merge only when they are exact duplicates (same rule, same line, same package, and for file-level findings the same title), so two separate secrets on adjacent lines, or three unused dependencies in one manifest, stay separate issues. In the tool-overlap table two tools are comparable only on an issue class *and file type* both of them reported, so a JavaScript linter and a Python linter are never compared on `unused-variable`. An issue's `severity_normalized` is its members' highest normalized severity (a heuristic for tools that rate nothing); `reported_levels` lists what the tools printed (`tool:LEVEL`, `unrated` when a tool printed none). One tool's two reports of one advisory under linked ids (pip-audit's two PYSEC ids for one CVE) are one issue, and cluster membership does not depend on query order.

Each issue is one kind. A *dependency advisory* is every `dependency` finding of a known-vulnerability scanner (pip-audit, npm audit, osv-scanner, Trivy, retire.js), packages that do not exist (`nonexistent-*`) included; deptry and depcheck manifest findings (an import the app never declared, an unused dependency) are code issues of the app (also counted as `manifest_unique`). Which findings are advisories follows `analysis/services/base.py` `is_dependency_advisory`, the rule the run views use: the run pages (`/findings/?scope=dependencies`) count the same findings. `manifest_unique` is empty (not 0) when neither deptry nor depcheck measured the app; `manifest_unused_scaffold` counts, among those, the 'unused dependency' findings (deptry DEP002, depcheck `unused-dependencies`/`unused-devDependencies`) on packages the stack scaffold declares: packages the scaffold preinstalls, which the model did not choose (informational; they stay in `code_unique`). A *runtime* issue is one only dynamic scanners (ZAP, Nuclei, Wapiti) reported, counted once per issue class per app (`runtime_unique`), not once per crawled URL. `unique_issues`, `multi_tool_issues` and `unique_normalized_<severity>` count runtime issues the same way, so `unique_issues` = `code_unique` + `code_unique_tests` + `runtime_unique` + `dependency_unique`; the `issues` list and `tool_pairs` stay per cluster (one per crawled URL). A *test-code* issue is in a test path (`reports/services/loc.py` `is_test_path`: `tests/`, `test/`, `__tests__/`, `e2e/`, `cypress/`, `playwright/` directories; `test_*.py`, `*_test.py`, `conftest.py`; `*.test.*`/`*.spec.*` JS/TS files; `setupTests.*`; any file whose name contains `smoke`). Everything else is a code issue. `code_unique`, `security_unique` (security and secrets categories) and `unique_per_kloc` (the plan's `issues_per_kloc`) count code issues only; test-code issues are `code_unique_tests`/`security_unique_tests`, and source LOC (`loc`) leaves test files out (`test_loc` counts them; `test_loc` is empty for a single-blob result, whose test code cannot be told apart). These definitions are bug fixes: stored reports keep their old numbers until regenerated, and a frozen plan's JSON is unchanged.

`dependency_added` counts the advisories in versions the model chose: in a package the app declares but its scaffold does not; in a transitive package the app's `package-lock.json` shows only model-added packages pulling in; or in a scaffold package whose version spec the model changed (or a transitive package under such a re-pinned scaffold package), when the scaffold's own spec did not install an affected version. What the scaffold's spec installed is read from the scope: an exact scaffold pin installs that version; a range installed what the apps in scope that kept that range installed (their lockfiles, exact pins and the versions the scanners report); if no app kept it, the newest version within the range that other apps installed. So two apps on the same installed version get the same owner whatever their pin style. Only with no evidence in scope (a single app) is a range taken to install its newest version, fixed when a fix lies inside the range. `dependency_inherited` is the rest: the scaffold's own versions and transitive packages. Known limits: an advisory without fixed-version data (npm audit alone) counts as inherited, and in a scope that spans months the versions other apps installed may be newer than what existed when an app was generated. `dependency_unique`/`dependency_added`/`dependency_inherited` are empty (not 0) for an app no vulnerability scanner measured, and `runtime_unique` for an app no dynamic tool measured. When most advisories are inherited the study report raises `scaffold_dependencies`: they measure the template.

The unit is the app: repeats of one (condition, app) are averaged before any group aggregate (a job without an app counts as its own unit). The instrument set is decided over the whole scope, all conditions together, never per condition. Only tools that measured every analyzed app in scope are counted, where an analyzed app is one at least one non-AI tool measured: an app whose tools all failed is left out of the issue tables (its counts are missing, listed in `coverage.apps_unmeasured`), not counted as clean (see [Measurement rules](/docs/ANALYSIS_PIPELINE)): a tool that failed, timed out or found the app not running on some apps is dropped, and the `coverage` block says which tools were dropped and why. A dependency audit that could not run because a dependency does not exist counts as `failed` here, even though its `nonexistent-package`/`nonexistent-version` findings are stored on the result; use `--all-tools` (`common_tools_only=false`) to count them. Results of cancelled runs are never counted. In a run reaped after a backend restart, tools that finished before the interruption keep their results, and the interrupted tool counts as `failed`.

`GET /api/statistics/study/breakdowns/?experiment_id=&batch_id=&include_ai=false&line_tolerance=2&common_tools_only=true&include_issues=false` returns:

| Key | Content |
| --- | --- |
| `params`, `coverage` | The settings used, and `ai_counted` (AI tools whose findings were counted), `ai_partial` (with `include_ai`: AI reviewers dropped because they missed apps), `dynamic_counted`; `tools_common` and, per dropped tool, `apps_measured`, `apps_missing` and a count per reason |
| `totals` | The scope-wide summary, including `duplicate_findings` and `multi_tool_issues`. The unit is the app: repeats of one (condition, app) are averaged first; `apps` counts apps and `runs` jobs; mean, median and sd of unique issues per app and `unique_per_kloc` are over app means; count columns are totals over runs (empty when no run measured them) |
| `per_app` | Job labels (model, template, `app_id`, `condition_id`, `condition_label`, mode, repeat, healing), the latest AnalysisRun `run_id`, source `loc` (test files excluded) and `test_loc` (null for a single-blob result: test code unknown), `raw_findings`, `unique_issues` (every kind), `multi_tool_issues`, `code_unique`, `code_unique_tests`, `manifest_unique`, `runtime_unique`, `dependency_unique`, `dependency_added`, `dependency_inherited`, `manifest_unused_scaffold` (of `manifest_unique`: 'unused' findings on packages the stack scaffold preinstalls), `security_unique`, `security_unique_tests`, `unique_per_kloc` (code issues only), `unique_normalized_<severity>` (every kind, normalized severity; runtime issues once per class), `security_normalized_<severity>` (code security/secret issues by normalized severity, the input of the security bounds) |
| `by_model`, `by_condition`, `by_template`, `by_model_template` | The same, aggregated; `by_condition` is per experiment condition, so arms of one model are never pooled |
| `by_tool` | Every counted tool, including those that found nothing: raw findings, unique issues, `exclusive`, `shared`, `apps_measured`/`runs_measured`, `apps_with_findings`/`runs_with_findings`, `reported_levels` (levels as the tool printed them, with counts), raw counts by normalized severity (`raw_normalized_<severity>`) |
| `by_tool_rule` | Which tool finds which kind of issue: rule, issue class, CWE, example title, findings, apps and runs affected, `reported_levels`, `max_severity_normalized` (heuristic), `raw_normalized_<severity>` |
| `tool_pairs` | Issues both tools reported (`both`, `only_a`, `only_b`) and their agreement (Jaccard) on the issue classes both reported somewhere |
| `issues` | Only with `include_issues=true`: every cluster with `job_id` (the generation job; `per_app.run_id` is the AnalysisRun), `kind` (code/test/runtime/dependency), `dependency_added`, tools, `reported_levels`, `severity_normalized`, rule ids and finding ids |

Every column that holds the platform's normalized severity (a heuristic) has `normalized` in its name; `reported_levels` holds the levels the tools printed. `condition_label` is the condition's label, else `<model name> / <method>`, the name the experiment pages and the leaderboard use.

AI-reviewer findings are left out unless `include_ai=true`; `common_tools_only=false` counts every tool.

## Manual review sample

To check the automated verdicts against a human, export a seeded, stratified sample of completed jobs that have code:

```bash
python manage.py export_review_sample --experiment <uuid> --size 30 --stratify model --seed 0 --out sample.zip
# also: --batch <uuid>, --per-stratum N (instead of proportional allocation), --stratify '' | model | template | model,template
# or:   GET /api/export/review-sample.zip?experiment_id=&size=30&stratify=model,template&seed=0
```

Without `--per-stratum`, `--size` is allocated proportionally across strata (largest remainder, at least one per stratum). The ZIP holds a `README.md` with instructions, `apps/S001/code/…` and each app's `REQUIREMENTS.md`, and blank sheets in `labels/`:

| Sheet | Rows | Label |
| --- | --- | --- |
| `ai_findings.csv` | Every AI-reviewer finding | `human_verdict`: TP, FP or unsure |
| `static_issues.csv` | Deduplicated issues in the app's code (static tools, including deptry/depcheck manifest findings), the issues `code_unique` counts: issues in test code and runtime issues are left out. Up to 25 per app drawn with the seed | `human_verdict`: TP, FP or unsure |
| `dependency_issues.csv` | Known-vulnerability advisories (pip-audit, npm audit, osv-scanner, Trivy, retire.js), deduplicated across scanners, up to 10 per app; advisories in versions the model chose are drawn first. `origin` is `model`, `scaffold` or `transitive`, with the same attribution as `dependency_added` | `human_verdict`: TP (the installed version is affected), FP or unsure |
| `runtime_issues.csv` | Issues only the dynamic scanners (ZAP, Nuclei, Wapiti) reported against the running app, one row per issue class per app, with the URL paths it was seen on (`url_paths`, at most 10 listed), up to 10 per app | `human_verdict`: TP (the running app has the issue, judged from its code), FP or unsure |
| `requirements.csv` | Each backend, frontend and admin requirement of the app's template | `human_met`: yes, partial or no |
| `apps.csv` | One per app | `human_functional`: works, partial or broken; `human_quality`: 1–5 |

Findings come from each app's latest completed result per tool (`ToolResult.latest_ids_per_job`), as in the study tables. Sheets show `tool_severity` (the label each tool printed, `tool:LABEL` per tool for a merged issue, `unrated` when a tool rates nothing) and `normalized_severity` (the heuristic bucket the scores group by). Formula-like cells get a leading apostrophe. Issues are split as in the study tables (dependency advisories by the analyzer's `is_dependency_advisory` rule, runtime issues, test code by `is_test_path`); test-code issues are not sampled, and `manifest.json` counts them (`counts.test_code_issues_not_sampled`). Sample format version 3 (version 2 added `dependency_issues.csv`, version 3 `runtime_issues.csv`); the scorer still reads version-1 and version-2 ZIPs.

Model, template, healing mode and the automated outcomes (conformance status and pass rate, deployed, the analysis runs the findings came from: `analysis_run_ids`) are kept only in `key.csv`; sample folders are `S001`… in shuffled order (blind by default; `--no-blind` or `blind=false` names them by job id). `manifest.json` records the seed, the sampling method, the strata and the population.

Score the filled-in sheets:

```bash
python manage.py score_review_sample sample.zip --json scores.json   # a ZIP or an unpacked folder
# or: POST /api/export/review-sample/score/ (multipart "file")
```

| Score | What it compares |
| --- | --- |
| `ai_findings`, `static_issues`, `dependency_issues`, `runtime_issues` | Precision with a 95% Wilson interval, overall and by normalized severity (`by_normalized_severity`); AI findings and static issues also by category (static issues also by tool and issue class); dependency issues by origin and tool; runtime issues by normalized severity, tool and issue class |
| `functional` | Human "works" vs conformance `passed`: agreement, Cohen's κ and the confusion counts (infra errors excluded) |
| `compliance` | Mean human requirement compliance (yes 1, partial 0.5, no 0) and its Spearman ρ with the conformance pass rate |
| `quality` | Mean of the 1–5 scores |

## Tool count and execution accounting

- `GET /api/statistics/study/tools/` lists every catalog analyzer (enabled or not) and the platform's own runtime evaluators (conformance tester, smoke test), with the counts a tool table needs: `catalog_registered`, `catalog_enabled`, `catalog_enabled_deterministic`, `catalog_enabled_ai`, `platform_evaluators`, `enabled_by_category`, `enabled_by_kind`, and `total_enabled` (enabled analyzers plus evaluators: 35 with the shipped catalog of 33). The catalog itself is in the [Analyzer guide](/docs/ANALYZER_GUIDE).
- `GET /api/statistics/study/accounting/?experiment_id=` (or `batch_id=`) counts what actually ran, never tools × apps:
  - `apps`: jobs by status, generated, analyzed (jobs with a completed or partial run), evaluated (infra errors and not-applicable evaluations excluded) and deployed;
  - `analysis_runs`: latest (the newest completed or partial run per job, `AnalysisRun.latest_ids_per_job`) and all runs, all runs by status;
  - `tools`: per tool, executions, completed, failed, skipped, unfinished and apps covered, for each job's latest result per tool and separately for all runs including re-analyses;
  - `evaluations`: conformance and smoke executions by status (the conformance block also reports `not_applicable` and `evaluated`), and the healing-ablation evaluations per mode;
  - `executions`: `planned_static` (per job, the distinct tools selected across its finished runs: completed, partial or failed; cancelled runs excluded) next to the executed `static` count (each job's latest result per tool, DAST scanners included), so a gap means a requested tool never produced a result; `static_completed`, `dynamic` (conformance plus smoke evaluations) and `total`.

To export everything for a paper:

```bash
python manage.py export_study_tables --out results/ [--experiment <uuid>] [--batch <uuid>] [--include-ai] [--line-tolerance 2] [--all-tools] [--no-spreadsheet-safe]
```

| File | Content |
| --- | --- |
| `issues_per_app.csv`, `issues_by_model.csv`, `issues_by_condition.csv`, `issues_by_template.csv`, `issues_by_model_template.csv` | Deduplicated issue tables |
| `issues_by_tool.csv`, `issues_by_tool_rule.csv`, `tool_overlap.csv`, `issue_clusters.csv` | Per-tool tables, tool pairs and every cluster |
| `tool_coverage.csv` | Per tool: counted, runs measured and missing, and the reasons it missed runs |
| `healing_by_model.csv`, `healing_by_condition.csv`, `healing_by_template.csv`, `healing_per_app.csv` | The healing ablation |
| `tool_inventory.csv`, `tool_executions.csv` | Catalog analyzers plus evaluators; per-tool executions (each job's latest result per tool) |
| `code_metrics_per_app.csv` | One row per job with a radon, lizard or jscpd result: the job labels, `loc` and `test_loc`, the metrics and the instrument behind them (below) |
| `study_summary.json` | Scope, issue params, totals and `coverage`, the healing summary, the tool counts and the full accounting |

`code_metrics_per_app.csv` (`statistics/services/study.py` `code_metrics_per_app`) reads each metric tool's latest result per job:

| Columns | Content |
| --- | --- |
| `job_id` … `build_healing` | The job labels of `issues_per_app.csv` |
| `loc`, `test_loc` | Source LOC and test LOC (`reports/services/loc.py`), the denominators of the issue tables. The tools' own size figures (lizard `total_nloc`, jscpd `total_lines`) cover what they read, test files included |
| `radon_avg_complexity`, `radon_max_complexity` | radon cyclomatic complexity (Python only) |
| `lizard_functions`, `lizard_avg_ccn`, `lizard_max_ccn`, `lizard_functions_over_15` | lizard over Python and JS/JSX/TS functions; `functions_over_15` counts functions with CCN above 15 |
| `jscpd_duplication_percentage`, `jscpd_clones` | jscpd duplication |
| `<tool>_status`, `<tool>_version`, `<tool>_parser_sha`, `<tool>_command_sha` | Per tool (radon, lizard, jscpd): `measured`, `not_applicable` or the skip reason or status, and the instrument (empty when the result did not record it) |

A metric is empty when its tool did not measure the app (no result, failed, app not running) or did not print it. A tool skipped as not applicable (no file it reads) is a measured zero for its counts (`lizard_functions`, `lizard_functions_over_15`, `jscpd_clones`); its averages, maxima and percentages stay empty. The tools read the analyzed tree, test files included. Metrics of different instruments are not pooled silently: the instrument columns name each one, and the study report flags a mix (`mixed_metric_instruments`).

A table with no rows keeps its real header (for example the healing tables of an agent-only scope). Text cells that a spreadsheet would read as a formula (starting with `=`, `+`, `-`, `@`) get a leading apostrophe; numbers are left alone. `--no-spreadsheet-safe` writes them verbatim, for pandas or R. The healing CSVs have `apps` and `runs` columns.

## Pre-registered analysis plan

An experiment can carry an `analysis_plan`, set in the experiment create or PATCH body while it is a draft (there is no plan editor in the UI yet; see the [API reference](/docs/api-reference)). The plan is validated and stored in canonical form:

| Field | Values |
| --- | --- |
| `primary_metric` | Required; one of the metrics below |
| `secondary_metrics` | Metrics below |
| `research_questions` | Up to 20 `{id, question, hypothesis, metric, direction}`; `direction` is `two-sided` (default), `greater` or `less`; `metric` defaults to the primary one |
| `alpha` | Between 0 and 0.5, default 0.05 |
| `correction` | `holm` (default), `bh` or `none` |
| `multiplicity` | `all` (default): one correction over every tested (metric, condition) pair of the declared metrics; `per_metric`: each metric corrected apart (the behaviour of plans frozen before this setting existed, which keep it) |
| `ranking_criteria` | `[{key, direction, weight, label?}]`, keys from the metrics below, `direction` `benefit` or `cost`; weights are renormalized |
| `exclusions` | `{truncated: true}` by default; infra errors are always excluded |
| `min_apps_per_condition` | Default 0 (no minimum); planned comparisons skip a condition with fewer apps |
| `notes` | Recorded with the plan |

Allowed metrics: `deploy_rate`, `pass_rate`, `functional_passed`, `unique_issues_per_app`, `issues_per_kloc`, `security_per_app`, `cost_per_app`.

```json
{
  "research_questions": [{"question": "Does prompt B deploy more often than A?", "metric": "deploy_rate", "direction": "greater"}],
  "primary_metric": "deploy_rate",
  "secondary_metrics": ["pass_rate", "issues_per_kloc"],
  "alpha": 0.05,
  "correction": "holm",
  "ranking_criteria": [
    {"key": "deploy_rate", "direction": "benefit", "weight": 0.4},
    {"key": "pass_rate", "direction": "benefit", "weight": 0.4},
    {"key": "issues_per_kloc", "direction": "cost", "weight": 0.2}
  ]
}
```

Launch freezes the plan: `analysis_plan_frozen_at` and `analysis_plan_hash` (sha256 of the canonical JSON, sorted keys, no whitespace) are set, and the plan cannot be edited afterwards; clone the experiment to change it. The study report then uses the plan's alpha, correction and ranking weights, marks the planned comparisons, the per-group outcomes of the declared metrics and (with the plan's criteria) the ranking confirmatory only for the plan's analysis on the planned sample of a finished design (`protocol.confirmatory`), and keeps everything else (correlations, code metrics) exploratory.

Only the planned sample is confirmatory: planned comparisons use the repeats planned at launch (`planned_repeats`; later repeats are left out and counted in `beyond_plan_excluded`, validity `design_extended`), and when a planned cell's current attempt was cancelled (`stopped_early`: the run was stopped before the design was complete, possibly on what the interim deltas showed) every family is reported `exploratory` (validity `stopped_early`). Each metric family compares only the (app, repeat) cells that have a value for that metric in every condition (`common_cells` per family), so a failed, excluded or unanalyzed run in one arm drops the cell for all. With `multiplicity: "per_metric"` and more than one metric, validity `multiplicity_per_metric` states the family-wise error across them.

The plan's `exclusions` decide the study report's sample: with a plan, `exclusions.truncated` replaces the report's `exclude_truncated` setting (a differing setting is ignored and flagged `plan_exclusions` in `validity`; `protocol.settings.exclusions_source` says which applied). Infra errors never count as evaluated. A condition (or the baseline) with fewer apps that have a value for the metric than `min_apps_per_condition` is listed in `planned_comparisons` with `skipped: "below_min_apps"` and no p-values, stays out of the multiple-comparison correction, and is flagged `below_min_apps`.

## Study report

Report type `study_report`: Reports → New → **Study Report**, or `python manage.py study_report --experiment <uuid> --out study.md`. In the UI, scope it to one experiment or leave it on all of your apps, and choose the seed, app-bootstrap rank intervals and whether AI-reviewer findings count. The report config also accepts `batch_id`, `line_tolerance`, `exclude_truncated` (default true; a truncated app is single-shot output cut at the output cap or an agent run that ended on a cut-off response, as on the Results tab) and `ranking_criteria` through `POST /api/reports/generate/`.

The CLI takes `--format md|tex|json` (default: from the `--out` suffix), `--experiment`, `--batch`, `--user <username>` (without it, every user's jobs in scope; with only `--experiment`, the experiment's owner), `--seed`, `--include-ai`, `--no-bootstrap` and `--save` (also store a Report row so it appears in the UI).

Export a stored report as Markdown, LaTeX (`longtable` + `booktabs`) or JSON with `GET /api/reports/<id>/export/?format=md|tex|json`, or with the buttons on the report page.

| Section | What it answers |
| --- | --- |
| `protocol` | What ran: scope, experiment design (hypothesis, repeats, base seed, baseline, healing, plan and its hash), models, templates, modes, stacks, number of prompt versions, apps per model, trials per cell, tool and evaluator versions (per tool, `installed_versions` and `configs` as recorded by the counted results), `instrument_coverage` (the common-instrument coverage block), healing modes, report settings, `truncated_excluded` and `in_flight_excluded` (queued or running jobs left out), and a fingerprint of the data; `report_version` (2: app-level aggregation, condition groups, code-only security bounds), `apps` (app × condition units), `grouped_by` (`model`, or `condition` when one model ran under several conditions; group rows then hold the condition label in `model` plus `condition_id`/`model_id`), `groups`, `apps_per_model`/`runs_per_model`, `build_healing` (non-agent runs; `build_healing_not_applicable` counts agent runs), `experiment.baseline_condition` (label), `experiment.planned_repeats`, and `confirmatory` ({design, reasons}: the declared metrics and the ranking are confirmatory only with a plan frozen at launch, a finished experiment, no cancelled planned cell and no repeats beyond the plan in the sample). Per tool, `installed_versions`, `commands` (`summary.command_sha`: the rendered command with the app URL normalized, or the AI reviewer's prompt; results stored before the hash existed are hashed from their recorded command) and `parsers` (`summary.parser_sha`), each with the number of counted results, and the distinct `configs`; the Markdown/LaTeX 'Tools run (recorded builds)' row prints the recorded builds, falling back to the catalog pin marked 'build not recorded'. Each counted result also records `summary.patterns_sha` (the file patterns that decide not applicable vs measured) besides the build and the options; the protocol does not list it |
| `definitions`, `metric_definitions` | *Generated*, *deployed* and *functionally correct* are different outcomes; each is defined, and so is every plan metric |
| `funnel` | generated -> analyzed -> evaluated -> deployed -> passes every check, per group and template, as rates per app: each app's repeats averaged, then the mean over apps (`k`/`n` count runs, `apps` counts apps); the 95% interval is Wilson over apps when every app's repeats agree (the plain Wilson interval with one run per app), else a two-stage cluster bootstrap over apps (none below 3 apps); `ci_method` says which. Also correct given deployed |
| `model_outcomes` | The plan metrics per group (model, or condition: `protocol.grouped_by`), each app's repeats averaged first, then the mean over apps; `issues_per_kloc` is the apps' mean code issues over their mean non-test LOC, summed over apps (the issue tables' `unique_per_kloc`); `apps`, `runs`; `status` is `confirmatory` only for declared metrics when `protocol.confirmatory.design` is true |
| `issues` | The deduplicated issue tables (see above), without the cluster list. Under condition grouping, `by_group` repeats the issue table per report group (the label the funnel and ranking use; jobs outside an experiment per model), and the Markdown/LaTeX issue tables use it; a 'Dependency manifest findings' table shows deptry/depcheck counts (code issues) and the unused scaffold packages among them |
| `security_bounds` | Code security issues only (security/secret issues in the app's own non-test code, `security_normalized_<severity>`; dependency advisories, runtime (DAST) issues and test code are left out), per normalized severity (a heuristic, labelled so): issues (total over runs; null without analyzed apps), apps affected (an app is affected when any repeat has one), Wilson CI and, for a zero count, the exact 95% upper bound over apps (zero in 20 apps is at most 13.9%); per group, critical/high counts and bounds. `population: code_security`, `severity: normalized` |
| `code_metrics` | Source LOC (backend/frontend), files, backend share and radon complexity per app, model and template, and the lizard and jscpd metrics (`lizard_functions`, `lizard_avg_ccn`, `lizard_max_ccn`, `lizard_functions_over_15`, `jscpd_duplication_percentage`, `jscpd_clones`; n = the apps each tool measured) overall, per group and per template; how much models differ on the same template (CV); app-level Spearman of size and complexity vs issues and pass rate. The `per_app` rows carry the same lizard and jscpd columns. `definitions.code_metrics_tools` says what each tool covers: radon Python only, lizard Python and JS/JSX/TS, all of them over the analyzed tree with test files, from each tool's latest result per app |
| `correlations` | Model parameters (nominal and effective context window, output limit, input and output price) vs deploy rate, pass rate, issues per kLOC and security issues per app: Spearman ρ, exact (n ≤ 8) or 10,000-draw permutation p, bootstrap CI, Holm and BH across the whole family, leave-one-out range with the most influential model, and a sign-flip flag. One point per model: when a model ran under several conditions, its point is the mean of its conditions' values (`arms_averaged`), so n counts models, never arms. Permutation p-values use the unrounded ρ and are rounded to 4 significant figures (an exact p at n = 8 can be 5e-5); `family_size` is the number of tests with a p-value, the m that Holm and BH correct over. Always exploratory |
| `ranking` | TOPSIS and WSM under the plan's (or default) weights, with Kendall's τ between them. Sensitivity: equal weights, ±50% one-at-a-time with the rank reversals each causes, Monte Carlo weights (Dirichlet near the chosen weights and flat) with median rank, 95% rank intervals, P(rank 1) and P(top 3), and app-bootstrap rank intervals. LOC is not a criterion, so verbosity is not rewarded twice. Ties share the best rank of their group (competition ranking, 1-2-2-4) in the table and in every simulated draw, so P(rank 1) counts every model tied for first and can sum to more than 1; `models_tied`/`tied_groups` flag a tie in the base ranking, the report summary names a `leader` only when exactly one model (or condition) has TOPSIS rank 1 (on a tie `leader` is null and `tied_leaders` counts the tied rows), the one-at-a-time `leader` is empty when several models tie (`leaders` lists them), and rank agreement is Kendall's τ-b (empty when nothing is ordered). `status` is `confirmatory` only with the plan's `ranking_criteria` and `protocol.confirmatory.design` |
| `planned_comparisons` | Experiments with a plan and a baseline condition only (`multiplicity`, `family_size`, `planned_repeats`, `beyond_plan_excluded`, `stopped_early`): each declared metric (primary, secondary and research-question metrics; `role` `primary`, `secondary` or `research_question`, with the questions' ids and directions), each condition vs the baseline, paired on the app; sign test and Wilcoxon signed-rank test (`alternative`: one-sided `greater`/`less` when every research question on that metric declares that direction, else two-sided; a delta is condition − baseline), adjusted over the plan's family (`multiplicity`). Only (app, repeat) cells with a value in every condition are compared (`common_cells`, per metric), as on the Results tab, so an exclusion in one arm cannot shift its mean. Conditions below `min_apps_per_condition` are skipped. While the experiment is not completed, cancelled or archived it is `interim` and the p-values and `significant` flags are withheld (null). Also `baseline_condition` (label); `family_size` is the m the correction used (tests with a p-value: a comparison whose deltas are all zero has none and is `in_family: false`), `comparisons_tested` counts every tested comparison; a comparison that cannot run is listed with `skipped` (`below_min_apps`, `no_baseline_data`: the baseline has no value for the metric, `no_pairs`: no app has a value in both), null p and `significant`, stays out of the correction and is flagged `untested_comparisons`; a family is `confirmatory` only under a plan frozen at launch and without `stopped_early`; significance is decided on unrounded p-values (stored ones are rounded to 4 significant digits) |
| `healing`, `accounting`, `tool_inventory` | The healing ablation (`by_group` per report group under condition grouping), what actually ran, and the tool counts |
| `per_app` | One row per job with every per-app quantity above |
| `validity` | Threats found in *this* data, each with the sections it affects: few models or apps, unequal apps per model, truncation, infra errors, missing evaluations, mixed prompt versions or modes, a single stack, healing without an unhealed ablation, zero critical/high code security issues (`zero_severe`), AI findings excluded, or requested but no AI reviewer reviewed every analyzed app (`ai_excluded`), tools that did not measure every analyzed run and are left out of every count, with their reasons (`tools_dropped`: failed, app not running, not run), an AI reviewer requested but missing apps (`ai_partial`), generated runs without a finished analysis (`not_analyzed`), planned comparisons that could not be run (`untested_comparisons`), a plan never frozen at launch (`plan_not_frozen`), effective ≠ nominal context, correlations that flip sign, duplicate share, no plan, a report setting overridden by the plan's exclusions, conditions below the plan's minimum apps, jobs still queued or running (left out: an interim view), apps graded by more than one evaluator/test-suite version, counted tools whose installed version differs across apps (`mixed_tool_versions`) or whose command, parser or options differ (`mixed_tool_commands`; tools left out of every count are not checked), radon, lizard or jscpd figures in `code_metrics` produced by different versions, commands, parsers, options or file patterns (`mixed_metric_instruments`, checked for every metric tool whose figures are in the rows, counted for issues or not), tied groups in the ranking (`ranking_tied`), apps no static tool measured, a pass rate identical for every evaluated app (ceiling or floor: the suite does not discriminate), a model served by more than one upstream provider, most dependency advisories in packages the model did not add (scaffold pins or transitive), planned cells cancelled before they ran (`stopped_early`), repeats added after launch (`design_extended`), several metrics each corrected apart (`multiplicity_per_metric`). `few_apps` and `unbalanced` count apps (repeats once). Group rows are per model, or per condition when one model ran under several conditions (harness or prompt-profile arms); arms are never pooled |

Default ranking criteria, when neither the plan nor the report config sets them: deploy rate 0.30, pass rate 0.30, issues per kLOC 0.15, security issues per app 0.15 and cost per app 0.10 (the last three as costs). Every resampling step takes the report's `seed`, so a report regenerates identically.

Experiment post-run automation generates an `experiment_report`, not a study report; see [Experiments](/docs/EXPERIMENTS).

The run-to-run variation of repeats (dispersion, variance components, ICC, pass@k, similarity) is not part of either report: it is in an experiment's Results section and its `variability/` endpoint and CSV ([Run-to-run variation](/docs/EXPERIMENTS#run-to-run-variation)). Per-call telemetry exports as `calls.csv` ([Generation process](/docs/GENERATION_PROCESS#per-call-telemetry)), and per-model covariates as the covariates CSV ([Models reference](/docs/MODELS_REFERENCE#research-covariates)).
