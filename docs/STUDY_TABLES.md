# Study tables

Tools for reporting a study's results: the effect of build healing, cross-tool deduplication of findings, a manual-review sample, and one way of counting tools and executions. Every table describes a *scope* of generation jobs (one experiment, one batch, or all of yours) and reads each job's **canonical analysis run under one frozen plan**: its latest finished run with that `plan_hash`, through the run's stored measurement (see [Analysis pipeline](/docs/ANALYSIS_PIPELINE#where-results-go)). Pass `plan_hash` (64 hex characters) to choose the plan; without it the plan with the most jobs that have a static measurement is used. Jobs not measured under the plan are listed in a `coverage` block with the reason (not analysed, analysis failed, another plan, excluded), never counted as clean. A missing count is `null`, never 0, and is left out of sums, rates and densities. AI-reviewer consensus is always reported in separate columns and never added to static counts.

## Build healing

Before a scaffolding or custom job's output is built, the platform can repair it. The setting is `build_healing` on the job, and the same field on an experiment is copied to every job at launch:

| mode | dependency inference | runtime patches | missing-file placeholders |
| --- | --- | --- | --- |
| `full` (default) | requirements inferred from imports are appended to `requirements.txt` | sqlite path, port 8000, `app.app_context()`, unknown lucide icons | a missing entry or component is stubbed |
| `no_deps` | — | yes | yes |
| `none` | — | — | a file the model never wrote is **removed**, including the skeleton's own stub |

Agent-mode output is always built verbatim, so it is never healed (`applicable: false`).

Every build records what it changed: `result_data.evaluation.build.healing` holds `{mode, applicable, changed, added_requirements, patches}`. The conformance outcome adds `metrics.functional.deployed` (the app built and answered its health check), `healing_mode` and `healing_changed`.

**Paired ablation.** Measuring healing by generating twice confounds it with sampling variance. Instead, re-evaluate the *same* output without healing:

```bash
python manage.py healing_ablation --experiment <uuid> --modes none,no_deps
```

Results land in `result_data.healing_ablation[mode]`; the job's own evaluation is not touched. The command builds on apps-dind one job at a time, so don't run it while agent runs are live. `GET /api/statistics/study/healing/?experiment_id=` returns the deploy rate per mode, overall and per model/template:

- `rescued_vs_none`: apps that deploy only because they were healed,
- `broken_vs_none`: apps that healing broke,
- `healing_effect_pp_vs_none`: the net effect, in percentage points,
- `mcnemar_p_vs_none`: McNemar's exact test on the discordant pairs,

plus which packages were added and which patches fired.

## Deduplicated findings

Tools overlap: Ruff's `S` rules mirror Bandit, Semgrep covers much of Bandit, detect-secrets and Gitleaks both flag the same key, and Ruff, Pylint and Vulture all report the same unused import. Raw finding counts therefore count one defect several times.

The study tables use the analysis engine's own deduplication: findings are grouped by the `issue_key` the engine stored at finalize (counted static findings only, measured scope only), and each issue is summarised by the engine's rule (most severe member; a bandit finding's code-based severity wins over ruff's proxy). Findings without a key are clustered with the same algorithm. The algorithm (same file, 2-line window, shared CWE or rule-equivalence class, one line per tool) is in [Analysis methodology](/docs/ANALYSIS_METHODOLOGY#deduplication). `statistics/services/dedup.py` keeps its public functions for existing callers; its issue-class labels come from the engine's equivalence table (`hardcoded-credentials`, `sql-injection`, …), else the CWE, else `tool:rule`.

`GET /api/statistics/study/breakdowns/?experiment_id=&batch_id=&plan_hash=&include_ai=false&include_issues=false` returns:

| key | content |
| --- | --- |
| `per_app` | counted static findings, unique issues, multi-tool issues, unique issues by severity, measured SLOC, issues per kLOC; `static_available`, `plan_hash`, and separately `ai_consensus_issues`, `ai_consensus_per_kloc`, `ai_cost_usd`, `ai_cost_complete` |
| `by_model`, `by_template`, `by_model_template` | the same, aggregated, with mean/median/sd per app; `static_measured_apps`, `security_measured_apps`, `ai_reviewed_apps` |
| `by_tool` | raw findings, unique issues, issues only this tool found (`exclusive`), issues shared with other tools |
| `by_tool_rule` | which tool finds which kind of issue (rule → issue class, apps affected, severity) |
| `tool_pairs` | issues both tools reported, plus their agreement (Jaccard) on the issue classes both can detect |
| `plan_hash`, `coverage` | the plan the numbers come from and the selection funnel |

Counts are `null` for apps without a static measurement. `include_ai=true` adds `ai_by_rule` (one row per AI consensus cluster), and with `include_issues=true` also `ai_issues`; it never changes the static counts. `include_issues=true` returns every static cluster with its finding ids, `issue_key`, `end_line` and CWE. `line_tolerance` is accepted for old clients but ignored for findings that have an `issue_key`. A malformed `experiment_id`, `batch_id` or `plan_hash` is a 422.

## Manual review sample

To check the automated verdicts against a human, export a seeded, stratified sample:

```bash
python manage.py export_review_sample --experiment <uuid> --size 30 --stratify model --seed 0 --out sample.zip
# or: GET /api/export/review-sample.zip?experiment_id=&plan_hash=&size=30&stratify=model,template&seed=0
```

The ZIP holds `apps/S001/code/…`, each app's `REQUIREMENTS.md`, and blank sheets in `labels/`:

- `ai_findings.csv`: TP/FP for each counted AI consensus cluster (one row per cluster, with its replicate count and grounding),
- `static_issues.csv`: TP/FP for deduplicated, counted static issues (engine issue keys), up to 25 per app,
- `requirements.csv`: yes/partial/no for each requirement,
- `apps.csv`: works/partial/broken and a 1–5 quality score.

The automated verdicts come from each app's canonical run under one plan (`plan_hash`, else the plan with the most static measurements). Model, template and the automated outcomes are kept only in `key.csv` (blind by default; `--no-blind` turns that off), which also records each app's `analysis_status` and `plan_hash`; a pass rate that was not recorded is an empty cell. `manifest.json` (format version 2) records the seed, the strata, the population, the `plan_hash` and the analysis coverage.

Score the filled-in sheets:

```bash
python manage.py score_review_sample sample.zip --json scores.json
# or: POST /api/export/review-sample/score/ (multipart "file")
```

The scores report the precision of AI and static findings (with 95% Wilson intervals, by severity, category, tool and issue class), Cohen's κ between the human functional verdict and the conformance outcome, and Spearman ρ between human requirement compliance and the conformance pass rate.

## Tool count and execution accounting

- `GET /api/statistics/study/tools/` lists every catalog analyzer (enabled or not) and the platform's own runtime evaluators (conformance tester, smoke test), with the counts a tool table needs: `catalog_registered`, `catalog_enabled`, deterministic vs AI, by category, and `total_enabled`.
- `GET /api/statistics/study/accounting/?experiment_id=` counts what actually ran, not tools × apps:
  - apps generated, analyzed, evaluated and deployed,
  - per tool: executions, succeeded/failed/skipped and apps covered (the canonical runs, and separately all runs including re-analyses; tool self-test runs are excluded),
  - `planned_static` (tools the runs selected) next to the executed count,
  - dynamic evaluations by status, and the healing-ablation evaluations.

To export everything for a paper:

```bash
python manage.py export_study_tables --experiment <uuid> --out results/
```

This writes one CSV per table above (`--plan-hash` pins the analysis plan; `--include-ai` adds `ai_issue_clusters.csv`), plus `study_summary.json` with the totals, the healing summary, the tool counts, the accounting scoped to the canonical runs, the `plan_hash` and the coverage.

## Pre-registered analysis plan

An experiment can carry an `analysis_plan` (create/PATCH body, draft only). It records:

- the research questions,
- the primary and secondary metrics,
- `alpha`,
- the correction (`holm`, `bh` or `none`),
- the ranking criteria and weights,
- the exclusion rules.

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

Launch freezes the plan. `analysis_plan_frozen_at` and `analysis_plan_hash` (sha256 of the canonical JSON) are set, and the plan cannot be edited afterwards; clone the experiment to change it. The study report treats every metric the plan declared as confirmatory and everything else as exploratory. It also uses the plan's alpha, correction and ranking weights.

## Study report

Report type `study_report` (Reports → New → Study Report, or `manage.py study_report --experiment <uuid> [--plan-hash <hash>] --out study.md`). Scope it to one experiment or batch, or leave it on all of your apps. Its config also takes `plan_hash` and the eligibility rules `exclude_invalid`, `exclude_empty_output`, `exclude_truncated` and `exclude_run_limits`. The data is report version 2: analysis numbers come from the v2 measurements, under one plan. Export it as Markdown, LaTeX (`longtable` + `booktabs`) or JSON with `GET /api/reports/<id>/export/?format=md|tex|json`, or with the buttons on the report page.

| Section | What it answers |
| --- | --- |
| `protocol` | What ran: models, templates, modes, stacks, prompt versions, cells by trial count, evaluator versions, healing modes, seed, the experiment plan hash, a fingerprint of the data, and `analysis_plan`: the frozen analysis plan's tools with versions, reviewer, analyzer image, engine versions, mapping hashes, `plan_hash` and the rule that chose it |
| `definitions` | *Generated*, *deployed* and *functionally correct* are different outcomes; each is defined |
| `funnel` | generated → analyzed → static measured → evaluated → deployed → passes every check, per model and template, with Wilson 95% intervals; also correct given deployed |
| `issues` | Deduplicated issue tables (see above), with the analysis coverage table and AI consensus columns kept apart |
| `security_bounds` | Per severity, over apps with a static measurement: issues, apps affected (with CI) and, for a zero count, the exact 95% upper bound on the per-app rate (zero in 20 apps is ≤ 13.9%, not 0) |
| `code_metrics` | LOC (backend/frontend) and measured SLOC, files, backend share and complexity (from the measurement) per app, model and template; how much models differ on the same template (CV); app-level Spearman of size/complexity vs issues |
| `correlations` | Model parameters vs outcomes: Spearman ρ, exact (n ≤ 8) or 10k-draw permutation p, bootstrap CI, Holm and BH across the whole family, leave-one-out range with the most influential model, and a sign-flip flag. Nominal and effective (endpoint-limited) context windows are separate predictors. Always marked exploratory |
| `ranking` | TOPSIS and WSM under the plan's (or default) weights. Sensitivity: equal weights, ±50% one-at-a-time, Monte Carlo weights (near the chosen ones and arbitrary) with 95% rank intervals and P(top 3), and app-bootstrap rank intervals. LOC is not a criterion, so verbosity is not rewarded twice |
| `planned_comparisons` | Experiments with a plan only: each declared metric, each condition vs the pre-registered baseline, blocked on the app; sign and Wilcoxon tests adjusted as the plan says. Marked interim while the experiment runs |
| `healing`, `accounting`, `tool_inventory` | The healing ablation and what actually ran |
| `validity` | Threats found in *this* data: few models or apps, imbalance, truncation, missing evaluations, mixed prompts or modes, single stack, healing without an ablation, zero severe findings, tool failures (timeouts included), effective ≠ nominal context, unstable correlations, duplicate share, no plan; and for the analysis data `not_measured` (apps without a measurement under the plan), `mixed_analysis_plans`, `static_unavailable`, `ai_separate` (AI consensus is never in the static counts) and `ai_cost_incomplete` |

Every resampling step takes the report's `seed`, so a report regenerates identically. `pass_rate` is `null` when it was not recorded (0 only for apps that failed to build or start); model criteria and security bounds use only apps with a static measurement, with issues divided by measured SLOC.

The funnel, deploy and functional rates still read the live conformance evaluation of each job, not the analysis snapshot: the funnel describes every generated job, not only analysed ones.

### Study Report vs analysis studies

The Study Report answers the experiment's pre-registered plan (deploy rates, functional correctness, per-app issue counts, rankings and planned comparisons against the experiment's baseline). An [analysis study](/docs/RESEARCH_GUIDE) pre-registers an analysis-side protocol (targets, a frozen engine plan, a design and confirmatory outcomes) and tests groups against each other on the v2 outcomes. A study targeting an experiment with a frozen plan inherits its research questions, α, correction and primary outcome where they map. Both read the same measurements, so pinning the post-run's `plan_hash` in both makes their analysis numbers identical; see [Research guide → Studies, experiment plans and the Study Report](/docs/RESEARCH_GUIDE#studies-experiment-plans-and-the-study-report).
