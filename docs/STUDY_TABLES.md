# Study tables

Tools for reporting a study's results: the effect of build healing, cross-tool deduplication of findings, a manual-review sample, and one way of counting tools and executions. Every table describes a *scope* of generation jobs (one experiment, one batch, or all of yours) and counts each job's latest finished analysis run only.

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

`statistics/services/dedup.py` maps each finding to an *issue class*. The class comes from an explicit rule equivalence where one is known, otherwise from the finding's CWE (Bandit and Semgrep findings record it), otherwise from its own `tool:rule`, which never merges across tools. Within one app, findings of the same class in the same file whose lines are within `line_tolerance` (default 2, Semgrep ranges included) form one issue. A single tool's findings merge only when they are exact duplicates.

`GET /api/statistics/study/breakdowns/?experiment_id=&include_ai=false&line_tolerance=2` returns:

| key | content |
| --- | --- |
| `per_app` | raw findings, unique issues, multi-tool issues, unique issues by severity, issues per kLOC |
| `by_model`, `by_template`, `by_model_template` | the same, aggregated, with mean/median/sd per app |
| `by_tool` | raw findings, unique issues, issues only this tool found (`exclusive`), issues shared with other tools |
| `by_tool_rule` | which tool finds which kind of issue (rule → issue class, apps affected, severity) |
| `tool_pairs` | issues both tools reported, plus their agreement (Jaccard) on the issue classes both can detect |

AI-reviewer findings are left out unless `include_ai=true`. Pass `include_issues=true` to get every cluster with its finding ids.

## Manual review sample

To check the automated verdicts against a human, export a seeded, stratified sample:

```bash
python manage.py export_review_sample --experiment <uuid> --size 30 --stratify model --seed 0 --out sample.zip
# or: GET /api/export/review-sample.zip?experiment_id=&size=30&stratify=model,template&seed=0
```

The ZIP holds `apps/S001/code/…`, each app's `REQUIREMENTS.md`, and blank sheets in `labels/`:

- `ai_findings.csv`: TP/FP for each AI-reviewer finding,
- `static_issues.csv`: TP/FP for deduplicated static issues, up to 25 per app,
- `requirements.csv`: yes/partial/no for each requirement,
- `apps.csv`: works/partial/broken and a 1–5 quality score.

Model, template and the automated outcomes are kept only in `key.csv` (blind by default; `--no-blind` turns that off). `manifest.json` records the seed, the strata and the population.

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
  - per tool: executions, completed/failed/skipped and apps covered (latest runs, and separately all runs including re-analyses),
  - `planned_static` (tools the runs selected) next to the executed count,
  - dynamic evaluations by status, and the healing-ablation evaluations.

To export everything for a paper:

```bash
python manage.py export_study_tables --experiment <uuid> --out results/
```

This writes one CSV per table above, plus `study_summary.json` with the totals, the healing summary, the tool counts and the accounting.

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

Report type `study_report` (Reports → New → Study Report, or `manage.py study_report --experiment <uuid> --out study.md`). Scope it to one experiment or batch, or leave it on all of your apps. Export it as Markdown, LaTeX (`longtable` + `booktabs`) or JSON with `GET /api/reports/<id>/export/?format=md|tex|json`, or with the buttons on the report page.

| Section | What it answers |
| --- | --- |
| `protocol` | What ran: models, templates, modes, stacks, prompt versions, cells by trial count, tool and evaluator versions, healing modes, seed, plan hash, and a fingerprint of the data |
| `definitions` | *Generated*, *deployed* and *functionally correct* are different outcomes; each is defined |
| `funnel` | generated → analyzed → evaluated → deployed → passes every check, per model and template, with Wilson 95% intervals; also correct given deployed |
| `issues` | Deduplicated issue tables (see above) |
| `security_bounds` | Per severity: issues, apps affected (with CI) and, for a zero count, the exact 95% upper bound on the per-app rate (zero in 20 apps is ≤ 13.9%, not 0) |
| `code_metrics` | LOC (backend/frontend), files, backend share and radon complexity per app, model and template; how much models differ on the same template (CV); app-level Spearman of size/complexity vs issues |
| `correlations` | Model parameters vs outcomes: Spearman ρ, exact (n ≤ 8) or 10k-draw permutation p, bootstrap CI, Holm and BH across the whole family, leave-one-out range with the most influential model, and a sign-flip flag. Nominal and effective (endpoint-limited) context windows are separate predictors. Always marked exploratory |
| `ranking` | TOPSIS and WSM under the plan's (or default) weights. Sensitivity: equal weights, ±50% one-at-a-time, Monte Carlo weights (near the chosen ones and arbitrary) with 95% rank intervals and P(top 3), and app-bootstrap rank intervals. LOC is not a criterion, so verbosity is not rewarded twice |
| `planned_comparisons` | Experiments with a plan only: each declared metric, each condition vs the pre-registered baseline, blocked on the app; sign and Wilcoxon tests adjusted as the plan says. Marked interim while the experiment runs |
| `healing`, `accounting`, `tool_inventory` | The healing ablation and what actually ran |
| `validity` | Threats found in *this* data: few models or apps, imbalance, truncation, missing evaluations, mixed prompts or modes, single stack, healing without an ablation, zero severe findings, tool failures, effective ≠ nominal context, unstable correlations, duplicate share, no plan |

Every resampling step takes the report's `seed`, so a report regenerates identically.
