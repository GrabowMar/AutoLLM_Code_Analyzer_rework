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

`GET /api/statistics/study/healing/?experiment_id=` (or `batch_id=`) returns the deploy rate per mode against the `full` baseline, overall (`totals`) and `by_model` / `by_template`. For every other mode `<m>`:

| Key | Meaning |
| --- | --- |
| `n_<m>`, `deploy_rate_<m>` | Apps evaluated under that mode, and the share that deployed (percent) |
| `paired_<m>` | Apps evaluated under both `full` and `<m>` |
| `rescued_vs_<m>` | Apps that deploy only because they were healed |
| `broken_vs_<m>` | Apps that healing broke |
| `healing_effect_pp_vs_<m>` | The net effect, in percentage points |
| `mcnemar_p_vs_<m>` | McNemar's exact test on the discordant pairs |

The response also lists which packages were added (`added_requirements`), which patches fired (`patches`), one row per app (`per_app`), and `not_applicable` (agent builds).

## Deduplicated findings

Tools overlap: Ruff's `S` rules mirror Bandit, Semgrep covers much of Bandit, detect-secrets and Gitleaks both flag the same key, Ruff, flake8, Pylint and Vulture all report the same unused import, and pip-audit, osv-scanner and Trivy report the same vulnerable package under different advisory ids. Raw finding counts therefore count one defect several times.

`statistics/services/dedup.py` maps each finding to an *issue class*, in this order:

1. an `issue_key` the parser already set (DAST findings: `missing-header:<header>`, `cors-misconfiguration`);
2. for the vulnerability scanners (pip-audit, npm audit, osv-scanner, Trivy, retire.js), `vulnerable-dependency:<package>`, matched within one project directory rather than one file, so `requirements.txt`, `package.json` and a lockfile meet. Within a package, tools merge only when they name the same advisory under any of its ids (the rule id plus the CVE/GHSA/PYSEC `aliases` the parser stored), so two different advisories of one package stay two issues;
3. an explicit rule equivalence (Bandit ids and Ruff's `S` mirrors, the shared Ruff/flake8/Pylint/Vulture/Mypy/ESLint lint rules, eslint-react `react/no-danger` and the eslint-security rules Semgrep also covers, keyed by the reporting tool's slug (`eslint-security:security/detect-eval-with-expression`), Semgrep rule-name keywords matched as whole `-`/`_`-separated tokens (specific ones such as `md5` before generic ones such as `secret`), radon CC ranks D–F and lizard `CCN` as `high-complexity`, every secret-scanner finding as `hardcoded-secret`);
4. the finding's CWE (Bandit, Semgrep and the DAST scanners record it);
5. otherwise its own `tool:rule`, which never merges across tools.

Within one app, findings of the same class in the same file whose lines are within `line_tolerance` (default 2, Semgrep ranges included) form one issue; file-level findings merge only with other file-level ones. A single tool's findings merge only when they are exact duplicates (same rule, same line, same package, and for file-level findings the same title), so two separate secrets on adjacent lines, or three unused dependencies in one manifest, stay separate issues. In the tool-overlap table two tools are comparable only on an issue class *and file type* both of them reported, so a JavaScript linter and a Python linter are never compared on `unused-variable`. An issue takes its members' highest severity.

Dependency advisories (category `dependency`) are counted apart from code issues: `code_unique` excludes them and `unique_per_kloc` (the plan's `issues_per_kloc`) divides only code issues by source LOC, since an advisory is about a package, not about lines the model wrote. `dependency_added` counts the advisories in packages the app declares in its own `requirements*.txt`/`package.json` but its stack scaffold does not (the model's choice); the rest are in packages the scaffold pins or that are transitive. When most advisories are of the second kind the study report raises `scaffold_dependencies`: they measure the template.

Only tools that measured every analyzed app in scope are counted, where an analyzed app is one at least one non-AI tool measured: an app whose tools all failed is left out of the issue tables (its counts are missing, listed in `coverage.apps_unmeasured`), not counted as clean (see [Measurement rules](/docs/ANALYSIS_PIPELINE)): a tool that failed, timed out or found the app not running on some apps is dropped, and the `coverage` block says which tools were dropped and why. A dependency audit that could not run because a dependency does not exist counts as `failed` here, even though its `nonexistent-package`/`nonexistent-version` findings are stored on the result; use `--all-tools` (`common_tools_only=false`) to count them. Results of cancelled runs are never counted. In a run reaped after a backend restart, tools that finished before the interruption keep their results, and the interrupted tool counts as `failed`.

`GET /api/statistics/study/breakdowns/?experiment_id=&batch_id=&include_ai=false&line_tolerance=2&common_tools_only=true&include_issues=false` returns:

| Key | Content |
| --- | --- |
| `params`, `coverage` | The settings used; `tools_common` and, per dropped tool, `apps_measured`, `apps_missing` and a count per reason |
| `totals` | The scope-wide summary, including `duplicate_findings` and `multi_tool_issues` |
| `per_app` | Job labels (model, template, mode, repeat, healing), source `loc`, `raw_findings`, `unique_issues`, `multi_tool_issues`, `security_unique`, `unique_<severity>`, `code_unique`, `dependency_unique`, `dependency_added`, `unique_per_kloc` (code issues only) |
| `by_model`, `by_template`, `by_model_template` | The same, aggregated, with mean, median and sd of unique issues per app |
| `by_tool` | Raw findings, unique issues, issues only this tool found (`exclusive`), issues shared with other tools (`shared`), apps with findings, raw counts by severity |
| `by_tool_rule` | Which tool finds which kind of issue: rule, issue class, CWE, example title, findings, apps affected, maximum severity |
| `tool_pairs` | Issues both tools reported (`both`, `only_a`, `only_b`) and their agreement (Jaccard) on the issue classes both reported somewhere |
| `issues` | Only with `include_issues=true`: every cluster with its tools, rule ids and finding ids |

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
| `static_issues.csv` | Deduplicated static issues (all static tools), up to 25 per app drawn with the seed | `human_verdict`: TP, FP or unsure |
| `requirements.csv` | Each backend, frontend and admin requirement of the app's template | `human_met`: yes, partial or no |
| `apps.csv` | One per app | `human_functional`: works, partial or broken; `human_quality`: 1–5 |

Model, template, healing mode and the automated outcomes (conformance status and pass rate, deployed, analysis run id) are kept only in `key.csv`; sample folders are `S001`… in shuffled order (blind by default; `--no-blind` or `blind=false` names them by job id). `manifest.json` records the seed, the sampling method, the strata and the population.

Score the filled-in sheets:

```bash
python manage.py score_review_sample sample.zip --json scores.json   # a ZIP or an unpacked folder
# or: POST /api/export/review-sample/score/ (multipart "file")
```

| Score | What it compares |
| --- | --- |
| `ai_findings`, `static_issues` | Precision of the findings with a 95% Wilson interval, overall and by severity and category (static issues also by tool and issue class) |
| `functional` | Human "works" vs conformance `passed`: agreement, Cohen's κ and the confusion counts (infra errors excluded) |
| `compliance` | Mean human requirement compliance (yes 1, partial 0.5, no 0) and its Spearman ρ with the conformance pass rate |
| `quality` | Mean of the 1–5 scores |

## Tool count and execution accounting

- `GET /api/statistics/study/tools/` lists every catalog analyzer (enabled or not) and the platform's own runtime evaluators (conformance tester, smoke test), with the counts a tool table needs: `catalog_registered`, `catalog_enabled`, `catalog_enabled_deterministic`, `catalog_enabled_ai`, `platform_evaluators`, `enabled_by_category`, `enabled_by_kind`, and `total_enabled` (enabled analyzers plus evaluators: 35 with the shipped catalog of 33). The catalog itself is in the [Analyzer guide](/docs/ANALYZER_GUIDE).
- `GET /api/statistics/study/accounting/?experiment_id=` (or `batch_id=`) counts what actually ran, never tools × apps:
  - `apps`: jobs by status, generated, analyzed, evaluated (infra errors excluded) and deployed;
  - `analysis_runs`: latest and all runs, all runs by status;
  - `tools`: per tool, executions, completed, failed, skipped, unfinished and apps covered, for the latest runs and separately for all runs including re-analyses;
  - `evaluations`: conformance and smoke executions by status, and the healing-ablation evaluations per mode;
  - `executions`: `planned_static` (tool selections of the latest runs) next to the executed `static` count (every analyzer execution, DAST scanners included), `static_completed`, `dynamic` (conformance plus smoke evaluations) and `total`.

To export everything for a paper:

```bash
python manage.py export_study_tables --out results/ [--experiment <uuid>] [--batch <uuid>] [--include-ai] [--line-tolerance 2] [--all-tools]
```

| File | Content |
| --- | --- |
| `issues_per_app.csv`, `issues_by_model.csv`, `issues_by_template.csv`, `issues_by_model_template.csv` | Deduplicated issue tables |
| `issues_by_tool.csv`, `issues_by_tool_rule.csv`, `tool_overlap.csv`, `issue_clusters.csv` | Per-tool tables, tool pairs and every cluster |
| `healing_by_model.csv`, `healing_by_template.csv`, `healing_per_app.csv` | The healing ablation |
| `tool_inventory.csv`, `tool_executions.csv` | Catalog analyzers plus evaluators; per-tool executions of the latest runs |
| `study_summary.json` | Scope, issue params and totals, the healing summary, the tool counts and the full accounting |

## Pre-registered analysis plan

An experiment can carry an `analysis_plan`, set in the experiment create or PATCH body while it is a draft (there is no plan editor in the UI yet; see the [API reference](/docs/api-reference)). The plan is validated and stored in canonical form:

| Field | Values |
| --- | --- |
| `primary_metric` | Required; one of the metrics below |
| `secondary_metrics` | Metrics below |
| `research_questions` | Up to 20 `{id, question, hypothesis, metric, direction}`; `direction` is `two-sided` (default), `greater` or `less`; `metric` defaults to the primary one |
| `alpha` | Between 0 and 0.5, default 0.05 |
| `correction` | `holm` (default), `bh` or `none` |
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

Launch freezes the plan: `analysis_plan_frozen_at` and `analysis_plan_hash` (sha256 of the canonical JSON, sorted keys, no whitespace) are set, and the plan cannot be edited afterwards; clone the experiment to change it. The study report then uses the plan's alpha, correction and ranking weights, marks the planned comparisons and the per-model outcomes of the declared metrics confirmatory, marks the ranking confirmatory when it uses the plan's criteria, and keeps everything else (correlations, code metrics) exploratory.

The plan's `exclusions` decide the study report's sample: with a plan, `exclusions.truncated` replaces the report's `exclude_truncated` setting (a differing setting is ignored and flagged `plan_exclusions` in `validity`; `protocol.settings.exclusions_source` says which applied). Infra errors never count as evaluated. A condition (or the baseline) with fewer apps that have a value for the metric than `min_apps_per_condition` is listed in `planned_comparisons` with `skipped: "below_min_apps"` and no p-values, stays out of the multiple-comparison correction, and is flagged `below_min_apps`.

## Study report

Report type `study_report`: Reports → New → **Study Report**, or `python manage.py study_report --experiment <uuid> --out study.md`. In the UI, scope it to one experiment or leave it on all of your apps, and choose the seed, app-bootstrap rank intervals and whether AI-reviewer findings count. The report config also accepts `batch_id`, `line_tolerance`, `exclude_truncated` (default true) and `ranking_criteria` through `POST /api/reports/generate/`.

The CLI takes `--format md|tex|json` (default: from the `--out` suffix), `--experiment`, `--batch`, `--user <username>` (without it, every user's jobs in scope; with only `--experiment`, the experiment's owner), `--seed`, `--include-ai`, `--no-bootstrap` and `--save` (also store a Report row so it appears in the UI).

Export a stored report as Markdown, LaTeX (`longtable` + `booktabs`) or JSON with `GET /api/reports/<id>/export/?format=md|tex|json`, or with the buttons on the report page.

| Section | What it answers |
| --- | --- |
| `protocol` | What ran: scope, experiment design (hypothesis, repeats, base seed, baseline, healing, plan and its hash), models, templates, modes, stacks, number of prompt versions, apps per model, trials per cell, tool and evaluator versions (per tool, `installed_versions` and `configs` as recorded by the counted results), `instrument_coverage` (the common-instrument coverage block), healing modes, report settings, `truncated_excluded` and `in_flight_excluded` (queued or running jobs left out), and a fingerprint of the data |
| `definitions`, `metric_definitions` | *Generated*, *deployed* and *functionally correct* are different outcomes; each is defined, and so is every plan metric |
| `funnel` | generated -> analyzed -> evaluated -> deployed -> passes every check, per model and template, with Wilson 95% intervals; also correct given deployed |
| `model_outcomes` | The plan metrics per model; `status` per metric (`confirmatory` only for the metrics the plan declares) |
| `issues` | The deduplicated issue tables (see above), without the cluster list |
| `security_bounds` | Per severity: issues, apps affected (with CI) and, for a zero count, the exact 95% upper bound on the per-app rate (zero in 20 apps is at most 13.9%, not 0); per model, critical/high counts and bounds |
| `code_metrics` | Source LOC (backend/frontend), files, backend share and radon complexity per app, model and template; how much models differ on the same template (CV); app-level Spearman of size and complexity vs issues and pass rate |
| `correlations` | Model parameters (nominal and effective context window, output limit, input and output price) vs deploy rate, pass rate, issues per kLOC and security issues per app: Spearman ρ, exact (n ≤ 8) or 10,000-draw permutation p, bootstrap CI, Holm and BH across the whole family, leave-one-out range with the most influential model, and a sign-flip flag. Always exploratory |
| `ranking` | TOPSIS and WSM under the plan's (or default) weights, with Kendall's τ between them. Sensitivity: equal weights, ±50% one-at-a-time with the rank reversals each causes, Monte Carlo weights (Dirichlet near the chosen weights and flat) with median rank, 95% rank intervals, P(rank 1) and P(top 3), and app-bootstrap rank intervals. LOC is not a criterion, so verbosity is not rewarded twice |
| `planned_comparisons` | Experiments with a plan and a baseline condition only: each declared metric (primary, secondary and research-question metrics; `role` `primary`, `secondary` or `research_question`, with the questions' ids and directions), each condition vs the baseline, paired on the app; sign test and Wilcoxon signed-rank test (`alternative`: one-sided `greater`/`less` when every research question on that metric declares that direction, else two-sided; a delta is condition − baseline), adjusted per metric as the plan says. Only (app, repeat) cells present in every condition are compared (`common_cells`), as on the Results tab, so an exclusion in one arm cannot shift its mean. Conditions below `min_apps_per_condition` are skipped. While the experiment is not completed, cancelled or archived it is `interim` and the p-values and `significant` flags are withheld (null) |
| `healing`, `accounting`, `tool_inventory` | The healing ablation, what actually ran, and the tool counts |
| `per_app` | One row per job with every per-app quantity above |
| `validity` | Threats found in *this* data, each with the sections it affects: few models or apps, unequal apps per model, truncation, infra errors, missing evaluations, mixed prompt versions or modes, a single stack, healing without an unhealed ablation, zero severe findings, AI findings excluded, tool failures, effective ≠ nominal context, correlations that flip sign, duplicate share, no plan, a report setting overridden by the plan's exclusions, conditions below the plan's minimum apps, jobs still queued or running (left out: an interim view), apps graded by more than one evaluator/test-suite version, tools whose installed version differs across apps, apps no static tool measured, a pass rate identical for every evaluated app (ceiling or floor: the suite does not discriminate), a model served by more than one upstream provider, most dependency advisories in packages the model did not add (scaffold pins or transitive) |

Default ranking criteria, when neither the plan nor the report config sets them: deploy rate 0.30, pass rate 0.30, issues per kLOC 0.15, security issues per app 0.15 and cost per app 0.10 (the last three as costs). Every resampling step takes the report's `seed`, so a report regenerates identically.

Experiment post-run automation generates an `experiment_report`, not a study report; see [Experiments](/docs/EXPERIMENTS).
