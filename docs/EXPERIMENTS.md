# Experiments

An experiment is a designed comparison of **apps × conditions × repeats** with a hypothesis, a pre-registered baseline and a reproducibility seed.

A condition is a model plus a harness preset (agent mode) or a model plus a prompt profile (scaffolding mode). Experiments are the research entry point; the [sample generator](/docs/GENERATION_PROCESS) stays available for one-off, ad-hoc generation.

Everything lives under **Experiments** (`/experiments`). Automation (cron schedules, post-run analysis and reports) is part of each experiment; the old `/automation` pages redirect here (see [Automation guide](/docs/AUTOMATION_GUIDE)). Where and in which order jobs run is described in [Execution](/docs/EXECUTION); local models and endpoints in [Local models](/docs/LOCAL_MODELS).

## In the UI

A task-oriented walkthrough of the app is in the [User guide](/docs/USER_GUIDE); this section is the reference.

| Page | What it does |
|---|---|
| `/experiments` | Your experiments, most recently updated first. Filters in the URL (`?q`, `status` = Active / Drafts / Finished / Archived / All, `mode`, `endpoint`, `page`). A capacity strip on top shows the executor, lane usage and endpoints (capacity warnings behind a chip). Active rows update live over SSE; the list refetches every 15 s while any row is active. |
| `/experiments/new` | The composer, in ten numbered sections: Basics, Method, Models (grouped by endpoint), Harness presets or Prompt profiles, Apps, Replication, Sampling & budget, Ablations, Execution, Automation. A sticky rail (on wide screens) holds the section index and the **Plan**: the server preview (per-endpoint load, estimated hours, bottleneck, cost bound, issues, comparability check) with Save draft / Launch. URL parameters: `?from=<id>` clones a design, `?edit=<id>` edits a draft, `?models=<pk,…>` preselects models (from an endpoint page), `?from_batch=<uuid>` starts from a batch's grid. Unsaved create-mode edits persist in the session. `/experiments/create` redirects here. |
| `/experiments/{id}` | A study record. Masthead: accession id (`EXP-` + the first UUID block), title, status and dates, the hypothesis, a spec strip (design formula `k conditions × n apps × r repeats = N cells`, baseline, outcome, randomisation), lifecycle actions (Launch, Pause, Resume, Cancel, Archive, Clone, Top up failed, JSON/CSV export) and, once launched, the data-collection strip (progress, slot lanes, ETA, bottleneck). Five numbered sections, kept in `?tab=`: **1 Abstract** (`summary`, the default: generated abstract, key findings and limitations from the pre-registered analysis, Figure 1 pass rate with 95% CIs, Figure 2 the condition × app grid, why runs failed, the record panel; drafts show a launch estimate), **2 Method** (`method`: factors, conditions, what is held constant, comparability, procedure, and 2.6 the analysis plan: research questions, primary/secondary metrics, α / correction / multiplicity, exclusions, ranking criteria, frozen-at time and sha256 hash, or a statement that no plan was registered — read-only), **3 Results** (`results`: analysis specification, leaderboard, forest plot of paired deltas vs the baseline, per-app table, [run-to-run variation](#run-to-run-variation), notes; options in the URL; the Analysis header states the server's verdict: it reads Pre-registered — confirmatory only when `paired.status` is `confirmatory`; otherwise it reads Exploratory with the reasons (no frozen analysis plan, baseline chosen post hoc, each `paired.deviations` entry; a plan that does not declare pass rate never makes a view the Plan's analysis: the header is Exploratory and always lists the plan does not declare pass rate, next to any other reasons), or Plan's analysis — p-values exploratory here when the only deviation is `multiplicity_family`; URL options only decide whether the reset button shows: Pre-registered analysis, or Default analysis without a plan), **4 Data** (`data`: every job, filterable, current attempts or retried only, Retry failed, the collection log of batches, dataset downloads including the [per-call telemetry CSV](/docs/GENERATION_PROCESS#statistics-and-exports)), **5 Operations** (`operations`: capacity and one lane per endpoint, activity feed, execution limits — priority, concurrency, retries, preloading and repeats can change while it runs —, schedules and post-run). The old names `overview`, `design`, `runs` and `automation` still resolve. Results and Data are disabled for drafts. Archived experiments are read-only. |

Using a model on an endpoint owned by someone else (a shared endpoint) requires ticking a consent note in the Models section: prompts and generated code pass through that owner's machine.

`build_healing` and `analysis_plan` are set through the API only; the composer does not expose them yet. The composer does not edit a plan, but cloning in the UI (`/experiments/new?from=<id>`, Clone, Replicate) copies the source's plan unfrozen. It freezes at launch under a new hash, as `POST /clone/` does. Basics shows the copied plan with a Don't copy the plan button. `build_healing` is not copied by the UI clone. When an experiment has an analysis plan, the post-run Exclude truncated runs checkbox is disabled and reads Set by the analysis plan, because the experiment report applies the plan's rule. Editing a draft that has an analysis plan shows the plan as kept (it freezes at launch) and disables the post-run Exclude truncated runs checkbox with the plan's rule; the edit never sends the plan.

## Design

### The experiment

`Experiment` (`backend/generation/models.py`) holds:

| Field | Meaning |
|---|---|
| `name`, `slug`, `description`, `hypothesis` | `slug` is unique per owner. |
| `status` | `draft → running ⇄ paused → completed \| cancelled`, plus `archived`. A top-up reopens a completed or cancelled experiment. |
| `pause_reason` | `user` or `digest_changed` (set by the endpoint inventory). `endpoint_removed` exists in the enum for data compatibility but is never set: deleting an endpoint that conditions use is refused with a 409. |
| `app_requirements` | App specs you can see (the catalog's and your own). An id you cannot see is a 404, never dropped silently. |
| `repeats`, `base_seed` | Repeats per cell (1..20 at create) and an optional seed (0..2³¹−1). |
| `mode` | `agent` (default; a sandboxed harness builds the app and the platform grades it) or `scaffolding` (template prompts). |
| `stack_slug`, `sampling_preset`, `budget_preset`, `llm_defaults`, `agent_config` | Settings every cell shares. A blank stack keeps the legacy per-profile stack. |
| `build_healing` | `full` (default), `no_deps` or `none`; copied to every job at launch. Scaffolding builds only (agent output is built verbatim). See [Study tables → Build healing](/docs/STUDY_TABLES#build-healing). |
| `continuation_limit` (0..5), `enable_repair` | Scaffolding settings: how many continuation calls a response cut off at `max_tokens` gets, and whether files that fail static checks get a repair round. Launch freezes non-default values into each job's snapshot (`resolved_bundle.generation`, part of `run_fingerprint`); the defaults (1 and true) leave the snapshot unchanged. Agent-mode cells ignore them (the harness has its own budget). |
| `scheduling_policy`, `priority`, `max_concurrency`, `infra_retries`, `execution_options` | Execution settings: policy `auto` / `interleaved` / `blocked` / `grouped`; priority −10..10 (non-staff are clamped to −5..0); a cap on this experiment's running jobs (1..64, null = none); infra retries 0..5 (default 2); options `warmup` (Ollama preloading, default on) and `max_resident_models` (only tightens an endpoint's K). |
| `post_run` | What happens after the runs; see [Automation](#automation). |
| `analysis_plan` | Optional pre-registered analysis plan; editable while draft, frozen at launch (see below). |
| `baseline_condition` | The pre-registered baseline. Defaults to the first condition and is frozen at launch. |
| `cloned_from`, `created_by_schedule` | Provenance of clones. |

### Conditions: one call, a cross-product

`POST /api/generation/experiments/` takes the whole design, conditions included, in one call:

```json
{
  "name": "Harness sweep", "slug": "harness-sweep", "repeats": 3, "base_seed": 7,
  "stack_slug": "flask-react", "app_requirement_ids": [4, 9, 12],
  "conditions": {
    "cross": {"model_ids": [11, 12, 13], "harness_preset_ids": [2, 5]},
    "extra": [{"model_id": 11, "harness_preset_id": 7, "label": "cold", "param_overrides": {"temperature": 0}}],
    "baseline": {"model_id": 11, "harness_preset_id": 2}
  },
  "execution": {"scheduling_policy": "auto", "priority": 0, "options": {"warmup": true}},
  "post_run": {"evaluate": true, "report": {"enabled": true, "exclude_truncated": true}},
  "schedules": [{"action": "top_up", "cron": "0 3 * * *", "timezone": "Europe/Warsaw"}]
}
```

- `cross` expands as a product: agent mode is models × harness choices (`harness_preset_ids` and/or bare `harnesses`, which run that harness's system default preset); scaffolding mode is models × `profile_ids` (explicit profiles, no "Auto").
- `extra` adds single conditions, typically ablations. Sampling parameters in `cross` apply identically to every model; per-model parameter changes exist only as `extra` entries, and `param_overrides` always shows in `differs_on`.
- Conditions get auto-labels: `"{model} · {harness}@{preset}"`, plus `" · ±{hash6}"` for parameter ablations.
- Dedupe: every candidate is resolved once (`experiment_layers`) and keyed on what it runs (model, profile, harness, normalised options, overrides). Two candidates that run the same thing (e.g. harness `aider` and the `aider-default` preset) are one arm. Create drops the duplicate; `POST …/conditions/bulk/` returns it in `skipped` with `reason: "duplicate"`. A profile on another stack is skipped as `"ineligible"`.
- The condition table holds **one row per (model, profile, harness, harness preset)**. A parameter-only ablation of an existing arm therefore needs its own harness preset.
- Models resolve through `LLMModel.objects.visible_to(user)`: an invisible or unknown model is a 404 and nothing is written.
- A tool-less model on a tool-calling harness is not refused when the condition is added; it is the blocking preview issue `models_without_tools`.

`PUT …/conditions/` replaces the set, `POST …/conditions/bulk/` appends, and the single-condition routes (`GET/POST …/conditions/`, `DELETE …/conditions/{cid}/`) remain. All of these are draft-only.

### Layer precedence

Later wins. One helper (`services/experiments.py:experiment_layers`) resolves the layers for comparability, the cost estimate, launch and export; launch copies the result into each job's own fields, and the preset foreign keys are provenance only.

- **Agent config** (`job.agent_config`): platform defaults → budget preset → sampling preset `sampling_policy` → experiment `agent_config` → the condition's harness. Explicit experiment overrides beat the presets.
- **LLM params** (`job.llm_params`): sampling preset `llm_params` → `llm_defaults` → condition `param_overrides`. Below them the snapshot puts the `max_tokens` column (agent) or the mode defaults (scaffolding), then the profile's `llm_config`.
- **Harness options**: the condition's harness preset.
- **Stack**: the experiment's `stack_slug`, else the condition profile's stack, else the platform default.

### Analysis plan

`analysis_plan` (validated by `services/analysis_plan.py`, 400 `invalid_analysis_plan` otherwise) states before launch what will be tested: `research_questions` (up to 20, each with a metric and direction `two-sided` / `greater` / `less`), `primary_metric` and `secondary_metrics` (`deploy_rate`, `pass_rate`, `functional_passed`, `unique_issues_per_app`, `issues_per_kloc`, `security_per_app`, `cost_per_app`), `alpha` (0 < α < 0.5, default 0.05), `correction` (`holm`, `bh`, `none`), `ranking_criteria`, `exclusions`, `min_apps_per_condition` and `notes`. The first launch freezes it with a sha256 of its canonical JSON (`analysis_plan_hash`, `analysis_plan_frozen_at`); PATCH on a launched experiment ignores it. A clone copies the plan unfrozen. The study report labels declared analyses confirmatory and everything else exploratory; see [Study tables](/docs/STUDY_TABLES#pre-registered-analysis-plan).

## Preview

`POST /experiments/preview/` takes an unsaved create body and returns the plan without writing anything (the composer's Plan). `GET /experiments/{id}/preview/` (`POST` is an alias kept for older clients) does the same for a saved experiment. Besides the matrix size, cost estimate and `launch_errors`, the preview has:

- **`comparability`** (agent mode): per condition, what will actually run: harness, version, preset and options, sampling policy, forced params, output cap, provider routing (`n/a` for local models), seed support, dropped params, context window, stack, profile, overrides, and where it runs (`endpoint` slug, `openrouter` for the system endpoint; `digest`, `num_ctx`, `api_flavor`, `quantization`). `differs_on` names the columns that vary, so an unintended difference shows before anything runs.
- **`issues`**, each `{code, detail, blocking, model_id, endpoint, condition_id}`. Blocking ones stop a launch: `missing_api_key`, `endpoint_disabled`, `endpoint_forbidden`, `model_unavailable`, `models_without_tools`, `digest_drift` (launched experiments only), plus every invalid preset, stack mismatch or resolution error. Non-blocking: `endpoint_offline`, `model_not_served`, `foreign_endpoint`, `params_dropped`, `support_unknown`, `num_ctx_small` (agent context below 16 384), `num_ctx_server_default`, `model_thrash_risk`, `time_confounded`, `block_grouped`, `digest_unknown`, `same_digest_multiple_endpoints`, `wall_clock_tight`, `provider_unpinned` (an OpenRouter condition without explicit provider routing: the upstream provider, and with it quantization, may vary between runs), `significance_unreachable` (the tests pair conditions on the app, so with *n* apps the smallest exact sign/Wilcoxon p is 2^(1−n) two-sided or 2^(−n) one-sided; when that floor, multiplied by the Holm family size — the conditions compared with the baseline, times the declared metrics under the plan's `multiplicity: "all"` — is not below alpha, no comparison can be significant whatever the data. The detail names the number of apps needed. Repeats do not help: the app is the unit).
- **`endpoints`**: one row per endpoint the conditions use, with liveness and sessions, `max_concurrency`, `job_cap`, `resident_limit` (K), the models, the pending cells, the median run time (`est_run_s_p50`: the last 50 completed jobs of the model in this mode, else of the endpoint) and `est_hours = cells × p50 / min(job_cap, slots)`.
- **`schedule`**: the effective policy per endpoint, its reason, the order seed and the estimated model loads per endpoint.
- **`capacity`** (slots, `est_parallel_runs`), **`estimate`** (`total_hours`, `bottleneck`: `sandbox` or `endpoint:<slug>`) and **`cost`** (`openrouter_upper_bound_usd`, `local_cells`, `kind`: `budget_upper_bound`, `heuristic`, `local_zero` or `mixed`).

## Launch and execution

`POST /experiments/{id}/launch/` (body `{mode: "launch" | "top_up", condition_ids?}`) or `POST /experiments/?launch=true` (create and launch in one call; a failed launch keeps the draft and returns 400 with `experiment_id`).

1. Every targeted condition is resolved first. A blocking issue refuses the whole launch with `400 {detail, code, errors[]}` and nothing is written. An experiment without conditions (`no_conditions`) or apps (`no_apps`), or an archived one, is refused too.
2. A draft becomes `running`, gets `launched_at`, freezes its analysis plan, its baseline defaults to the first condition, and each local condition pins its model's digest (`pinned_digest`).
3. One job is created per uncovered cell, in bulk (chunks of 500). A cell is covered by a live or completed job; failed and cancelled cells are re-run by a later top-up. A cell whose snapshot cannot be built is created `failed` and runs nothing.
4. Each job gets its frozen snapshot, its cell seed, `build_healing` and its execution fields: `sequence` (its position in the run order), `lane` (`sandbox` for agent runs, `llm` for scaffolding), `endpoint` (NULL for OpenRouter), `priority` and `execution.policy`. Then the jobs are handed to the executor (`execution.api.enqueue_jobs`), which claims them strictly in sequence order per (experiment, endpoint) lane.
5. The batch (`GenerationBatch`, linked by `experiment`; `kind` = `launch`, `top_up`, `retry` when every filled cell was attempted before, or `schedule` for schedule fires) records the design and `request.schedule`: requested and effective policies, order seed, K per endpoint, estimated swaps and a `history` of later policy and digest changes. The launch response carries `jobs_created`, `policy_effective_by_endpoint` and the non-blocking `warnings`.

**Top-up** is the same call on a launched experiment. It is idempotent: only uncovered cells get jobs, optionally only for `condition_ids`. A completed or cancelled experiment is reopened; a paused one stays paused. Raising `repeats` after launch (allowed: repeats only increase) and topping up adds new repeat blocks after the existing ones. A top-up never moves cells to library content updated since launch: each new cell keeps the spec content and stack version the experiment's earliest job of the same (condition, app) froze, or of the same app when its condition has no job yet ([Retry pinning](/docs/GENERATION_PROCESS#job-lifecycle-all-modes)).

### Scheduling policies

The run order is frozen per job at launch (`execution.sequence.cell_sequence`). It is computed over all cells, so a top-up keeps every cell's original position and new repeat blocks come after the current maximum.

| Policy | Order | Use |
|---|---|---|
| `interleaved` | Randomised complete blocks: all of repeat 0 before repeat 1, shuffled within each block. | OpenRouter. Conditions never confound with time of day. |
| `blocked` | Within each repeat block a model's cells run together; the model order is re-randomised per block. | Endpoints that hold few models at once (finite K). |
| `grouped` | Model-major: one model after another. | Fewest model loads, but confounded with time (`time_confounded`). |
| `auto` | Per endpoint: `blocked` where K is finite, `interleaved` elsewhere. | The default. |

The policy can change only while the experiment is paused; the pending jobs are re-sequenced and the change is appended to the schedule history.

### Seeds

With `base_seed` set, each cell's seed is `profile_resolver.derive_experiment_seed(base_seed, *parts)`:

- agent mode: `(app_id, repeat_index)`, so every condition of an (app, repeat) cell gets the same seed, and each repeat a different one;
- scaffolding mode: `(condition_id, app_id, repeat_index)`, the historical derivation.

A shared seed is not common random numbers in effect. That would need one seed to reproduce the same random draws, and agent runs do not: the only exact replay recorded so far (`run_fingerprint` 5b4d75641ae71433: the same snapshot, the same seed forwarded on every call, the same provider) diverged at LLM call 1, and the two runs made 26 and 30 calls. Different models, or one model on different providers, share even less. The shared seed keeps the seed from varying with the condition and documents the cell, but it does not correlate the conditions' runs, so it buys no variance reduction: the analysis pairs conditions by app ([Results and statistics](#results-and-statistics)) and treats repeats as independent draws. The job page's Same setup tab runs the same check for any replay ([Run-to-run variation](#run-to-run-variation)).

Seeds do not depend on the run order or the policy. A top-up re-derives the same seed for every cell it fills; infra retries reuse the same row, snapshot and seed. Retrying a failed or cancelled job from its job page (`POST /api/generation/jobs/{id}/retry/`) takes over the job's cell and its seed (and its run position); it is refused when the cell is already filled again. The retry also keeps the original's spec content and stack version (and, in agent mode, its brief), so library edits made since the launch never reach a retried cell; the rest of the procedure is current ([Retry pinning](/docs/GENERATION_PROCESS#job-lifecycle-all-modes)). Replays and reruns are extra runs outside the design.

### Pause, resume, cancel

- **Pause** (`POST …/pause/`): `running → paused`. The executor stops claiming the experiment's jobs; running jobs finish.
- **Resume** (`POST …/resume/`, body `{accept_digest_drift?}`): `paused → running`. When a local model's weights changed (`pause_reason: digest_changed`), resume needs `accept_digest_drift: true`: the pending jobs' snapshots are re-frozen to the new digest with the same seeds, keeping the spec content and stack version each job had frozen (so accepting a digest never also moves a cell to newer library content than its siblings), the conditions re-pin the digest, and the change is recorded in the schedule history. Results then flag those conditions `mixed_fingerprints`. The alternative is a clone ("Clone with current digest" in the UI).
- **Cancel** (`POST …/cancel/`, body `{scope: "all" | "pending"}`): pending jobs are cancelled at once, running generations are signalled, and jobs already waiting for or in evaluation finish and are graded. Scope `all` also ends the experiment as `cancelled`. A draft, completed or archived experiment has nothing to cancel (400 `not_active`). The response counts `cancelled_pending`, `signalled_running` and `finishing_evaluations`.
- **Archive** is refused while jobs are active (`active_jobs`), and disables the experiment's schedules. **Delete** works on drafts and on finished experiments without active jobs; their jobs stay, unlinked.
- **Requeue legacy jobs** (`POST …/jobs/requeue-legacy/`) hands jobs created before the executor existed (pending or running rows with no `queued_at`) to the executor.
- **Clone** (`POST …/clone/`, body `{name?, slug?, include_schedules, include_post_run, overrides?, launch?}`) copies the design into a new draft (`cloned_from` set; default name `"<name> (copy)"`, default slug `slugify(name)[:40]-YYYYMMDD-HHMM`, with `-2`, `-3`… on collision). Conditions point at the same model rows, whose digests are current.

PATCH on a launched experiment takes only `name`, `description`, `hypothesis`, `priority` (also re-prioritises pending jobs and their queued LLM requests), `max_concurrency`, `infra_retries`, `execution_options`, `post_run`, `repeats` (increase only) and `scheduling_policy` (paused only). Everything else, including the baseline and the analysis plan, is frozen at launch.

## Monitoring

- `GET /experiments/` lists your experiments, paginated (`page`, `per_page` ≤ 100, default 25), filterable by `status` (`active` = running + paused, `finished` = completed + cancelled, or an exact status such as `draft` or `archived`), `mode`, `search` and `endpoint` (a slug, `openrouter` or `local`), ordered by `-updated_at` (default), `name` or `-launched_at`. Each row carries progress over the current attempt of every cell, the cost so far, an ETA, the endpoints it uses and its next scheduled run.
- `GET /experiments/{id}/status/` returns rollups without the grid: counts, cells (`done`, `failed_final`, `in_flight`, `retried`), throughput, ETA and bottleneck, per-condition progress and pass rate, per-endpoint lanes (slots, active jobs, resident and loaded models, queue, tokens/s; other tenants' models appear only as counts and `last_error` only for the endpoint owner), executor state (`online`, `draining`, `code_stale`: the live leader runs older code than the web process) and the latest post-run.
- `GET /experiments/{id}/cells/` returns the compact grid: `[condition_index, app_index, repeat, state, job_id, pass_rate, attempt]` per cell, where the state is one letter:

  | Letter | State | Letter | State |
  |---|---|---|---|
  | `p` | pending | `c` | passed every check |
  | `b` | retry backoff (`run_after` in the future) | `f` | graded, not passed |
  | `w` | waiting for a model prefetch | `u` | completed, ungraded |
  | `r` | running | `x` | failed |
  | `q` | waiting for evaluation | `k` | cancelled |
  | `e` | evaluating | | |

- Live updates arrive over SSE on `experiment:<id>`: `job`, `tick`, `state` and `post_run` events (see [API reference](/docs/api-reference#realtime-stream)).

## Results and statistics

`GET /experiments/{id}/results/?baseline=&exclude_invalid=false&exclude_truncated=&common_cells=true&fingerprint=all|majority&planned_only=true&metric=pass_rate` is computed by `generation/services/experiment_results.py:compute`; `pass_rate` is the only supported metric (anything else is 400 `invalid_results_query`). The Results section, the results matrix and the `experiment_report` all use this one implementation.

- **The unit of analysis is the app.** Repeats of one (condition, app) share the app (each repeat has its own seed), so they are correlated; [Run-to-run variation](#run-to-run-variation) measures how much (ICC, design effect). Each (condition, app) collapses to the mean of its graded repeats; a condition's score is the mean of its app means.
- **Usable cells**: the current attempt (latest job) of a cell counts when it is completed and graded (`metrics.functional.pass_rate` not null). Missing cells are counted by reason in `n_missing`: `failed` (failed or cancelled), `ungraded`, `excluded_invalid`, `excluded_truncated`, `not_common`.
- **Common cells only** (default on): every condition is restricted to the (app, repeat) cells usable for all conditions, so exclusions (invalid, truncated, minority fingerprint) remove a cell for every condition, never one-sidedly. `balanced` says whether that was already true. With it off, the response notes that exclusions may select on the outcome.
- **Pairing is app-blocked**: a condition is compared with the baseline on the apps where both have a mean (`n_pairs` ≤ apps). Agent cells of one (app, repeat) share a seed, but a seed does not reproduce a run even for the same model on the same provider ([Seeds](#seeds)), so the pairing is by app, not by seed.
- **Intervals**: `pass_rate_ci95` and the paired `ci95` are two-stage cluster bootstraps (resample apps, then each app's repeats; 2 000 draws, seeded by the experiment id, so they are reproducible; each interval is cached on its exact inputs, so only conditions whose cells changed are resampled again).
- **Tests**: a two-sided exact sign test and a Wilcoxon signed-rank test (zero deltas dropped, average ranks for ties; exact up to 25 apps, normal approximation above) on the app-level deltas. `p_holm` is the Wilcoxon p, Holm-adjusted across the conditions compared with the baseline. A design with N conditions gives N−1 rows, never all pairs. A paired row with no paired app has `significant: null` (nothing was tested); `untested_reason` is `no_pairs` or `all_ties` (every paired app scored the same as the baseline). Benjamini-Hochberg q-values are compared with alpha unrounded.
- **Ranks**: leaderboard ranks use competition ranking: equal mean pass rates share a rank (1-1-3); ungraded conditions share the last.
- **Interim**: until the experiment is completed, cancelled or archived, every p-value is null and the response says so. Deltas and intervals are still shown.
- **Confirmatory only as planned**: with a frozen analysis plan that declares `pass_rate` and the pre-registered baseline, `paired.status` is `confirmatory` only when the view is the analysis the plan fixed. Anything else makes it `exploratory` (Holm at 0.05) and is listed in `paired.deviations`, with a note: `exclusions_differ` (a truncation rule other than the plan's; leaving `exclude_truncated` unset applies the plan's, else `true`), `common_cells_off`, `fingerprint_filter` (`majority`), `design_extended` (repeats added after launch counted, `planned_only=false`), `stopped_early` (a planned cell's current attempt is cancelled: the experiment or its pending jobs were cancelled, so stopping could have followed the interim deltas; a top-up that fills the cells lifts it) and `multiplicity_family` (the plan corrects across several metrics with `multiplicity: "all"`; the confirmatory p-values are then the study report's) and `invalid_excluded` (invalid runs excluded: not one of the plan's exclusions, so the view is exploratory). The automated `experiment_report` uses the plan's truncation rule too, and the Results tab's sample: each cell's current attempt and the repeats planned at launch. Its `sample` records `truncation_rule` (`analysis_plan` or `report_config`), `exclude_truncated`, `planned_repeats` and the runs left out as superseded attempts or beyond the plan. A note says when the plan overrode the report's `exclude_truncated`. Per-condition code issues come from one `issue_breakdowns` over every condition, so all conditions share one instrument set (`coverage`), and condition names match the leaderboard. The report summary's `leader` is null when conditions tie on the top mean (`tied_leaders`) or nothing is graded.
- **Planned sample**: `planned_only` (default on) counts only the repeats planned at launch (the launch batch's `trials`, also `planned_repeats` in the response). Repeats added afterwards (raising `repeats`, the `add_replicates` schedule) are counted per condition as `n_missing.beyond_plan`; the Results tab shows them as a separate caveat and a separate line in the Missing tooltip, not as missing cells. Extending a design after its results were visible and testing again is optional stopping, so with `planned_only=false` the comparison is exploratory.
- **Truncation sensitivity**: when any graded output was truncated, `paired.sensitivity` repeats the comparison under the other truncation rule (`exclude_truncated` flipped: keeping truncated output is the intention-to-treat analysis, since truncation is caused by the condition). Rows flag `direction_changed` and `significance_changed`, and a note names the conditions whose conclusion depends on the rule.
- **Baseline**: the pre-registered `baseline_condition`. Choosing another one with `?baseline=` labels the result `post_hoc` (p-values exploratory).
- **Fewer than 6 apps**: p-values carry the note "too few apps for inference". Add apps rather than repeats when you need power. The preview flags this before launch as `significance_unreachable` (below).
- **Mixed fingerprints**: conditions whose current jobs span several `config_fingerprint`s (after accepting a digest change, or a prompt version change) report `mixed_fingerprints`; scaffolding snapshots carry one since the generation-library modernization (older ones have none and are not compared); `fingerprint=majority` keeps only the majority fingerprint's cells.
- **Timings** confound the model with the machine and its load; each row names its `timing_endpoint_slug`, and a note warns when conditions span several endpoints.
- **All ties**: when every paired app scored the same as the baseline, the Abstract says Every paired app scored the same as <baseline> (N ties): there is no difference to test, with a ceiling/floor hint when every condition has the same mean, instead of blaming the app count.

`GET /experiments/{id}/export/?format=json|csv` exports the resolved configuration (with preset provenance, build healing and the analysis plan with its hash), the conditions and every job with its seed, fingerprints, endpoint, digest, `num_ctx`, attempt and execution record (relay session details are left out). CSV has one row per job. For paper-ready tables over an experiment (outcome funnel, deduplicated findings, planned comparisons) use the study report; see [Study tables](/docs/STUDY_TABLES).

### Run-to-run variation

The statistics above average repeats away. `GET /experiments/{id}/variability/` (`statistics/services/variability.py`) measures the repeats themselves, on the cells the Results section keeps: it takes the same `exclude_invalid`, `exclude_truncated`, `common_cells`, `fingerprint` and `planned_only` options, plus `format=json|csv`. The Results section shows it as **Run-to-run variation** (3.4, or 3.3 when the results matrix is hidden): KPI tiles, Tables 3–7, Figures 4–5, lettered notes and a **Per-cell CSV** button.

Everything here is descriptive: no p-values, n on every figure. While the experiment is not completed, cancelled or archived, every block carries `interim: true` and the precision planner is withheld. A value that was not measured is null, never 0.

**Metrics**, per run: pass rate (`metrics.functional.pass_rate`), billed cost of this attempt, list-price cost of subscription-billed runs (`metrics.list_cost`, not billed), tokens, LLM calls, wall time (`agent.wall_seconds`, else the job duration; not comparable across endpoints), source LOC and source files (`reports/services/loc.py`, test code apart), code issues and issues per kLOC (one `issue_breakdowns` call over every kept job: one instrument set, each tool's latest result), lizard mean and max CCN, jscpd duplication %, and every numeric `metrics.behaviour` field ([trajectory heuristics](/docs/GENERATION_PROCESS#trajectory-and-behaviour-metrics), labelled so). For subscription-billed runs the billed cost is $0, so Cost then describes billing, not usage, and a note says so.

| Block | Estimator | Shown in |
|---|---|---|
| Cell dispersion | Per (condition, app) cell with r repeats: n, mean, SD (r − 1), min, max, range, IQR (r ≥ 4) and CV = SD / mean (ratio-scale metrics with mean > 0 only; never pass rate or duplication %) | Table 3, Figure 4 (every cell's repeats) |
| Condition summary | Pooled within-cell SD `sqrt(Σ (r_a − 1) s_a² / Σ (r_a − 1))` with its df, median cell CV, the share of cells whose repeats agree on functional pass, deploy agreement, and a ceiling flag when every value is identical (a condition with no within-cell df gets none) | KPI tiles, Tables 3–4 |
| Variance components | Method of moments on unweighted cell means with the harmonic mean repeat count: condition, app, condition × app and repeat (the pooled within-cell variance). Only apps with values under every condition are used; with one condition or one app the design is one-way. Negative estimates are truncated to 0 and flagged; shares are of the summed truncated components. `icc_within_condition` = (app + condition × app) / (that + repeat): how alike two repeats of one condition on one app are | Figure 5 |
| ICC(1), design effect | Per condition, one-way random effects over apps: `ICC = (MSB − MSW) / (MSB + (r0 − 1) MSW)` with the unbalanced `r0 = (N − Σ nᵢ² / N) / (k − 1)`, negative truncated to 0. 95% CI by a two-stage cluster bootstrap (resample apps, then each app's repeats; 1,000 draws, seeded from the experiment id, condition and metric; core metrics only; 3 or more apps). Design effect `DE = 1 + (r0 − 1) ICC` and effective n = N / DE | Table 3 |
| pass@k, pass^k | Per cell with n graded repeats of which c passed: unbiased `pass@k = 1 − C(n − c, k) / C(n, k)` (at least one of k repeats passes) and `pass^k = C(c, k) / C(n, k)` (all k pass), averaged over apps per condition, with a bootstrap CI over apps (3 or more) | Table 4 |
| Check agreement | Whether repeats agree check by check (`evaluation.checks`), compared only within one tester version (`tester_sha256`), and the checks that differ | Table 4 |
| Code similarity | Each run's agent-written source (source files by the LOC rules, no test code, nothing in non-source directories, scaffold files the agent left unchanged removed; template modes subtract the pinned stack skeleton, else marked approximate): path Jaccard, identical files, normalized-line Jaccard, token 5-gram shingle Jaccard and, for Python, an AST node-type 3-gram Jaccard. Within a cell, against a reference: the same app and repeat index under neighbouring conditions, the similarity any two solutions of the task share. Jaccard of two empty sets is null | Table 5, KPI tile |
| Finding overlap | Per tool, the Jaccard of `(rule_id, normalized path)` between repeats, from each job's latest result, over tools that measured both; code issues and dependency advisories apart; test code, non-source directories and DAST findings left out; a pair no common tool measured is null | Table 5 |
| Rank intervals | Joint bootstrap over the apps every condition has, then each app's repeats: observed competition rank, median rank, 95% rank interval (integer order statistics), P(rank 1) and P(top 3), for pass rate and one continuous metric (issues per kLOC, else cost). Needs 3 or more shared apps | Table 6 |
| Precision planner | 95% CI half-width of a condition mean, `z sqrt(s_b² / A + s_w² / (A r))`, and of a paired delta, `z sqrt((2 s_ca² + 2 s_w² / r) / A)`, from the variance components (s_b² between apps within a condition, s_ca² condition × app, s_w² repeat), for A ∈ {5, 10, 20, 40} apps × r ∈ {1, 2, 3, 5} repeats; plus the fewest apps an exact sign test needs to reach p ≤ 0.05 (6). Normal approximation, planning guidance only. Only for completed, cancelled or archived experiments (while running, `planner` is null with an optional-stopping reason); refused when no variance was observed | Table 7 |

Reading it:

- Repeats are the only within-cell degrees of freedom. With 2 repeats each cell SD has 1 df; with 1 repeat per cell nothing here is estimable.
- When every repeat scores the same (a pass rate of 1.0 everywhere) the metric is at a ceiling: dispersion is 0 and the planner refuses. Lean on the continuous metrics, or on harder apps.
- A per-condition ICC over 2 apps (1 between-app df) is shown without a CI; read it as a description, not an estimate to rely on.
- Repeats whose `tester_sha256` was never recorded (jobs graded before the hash existed) are compared as one tester version.

`?format=csv` returns one spreadsheet-safe row per (condition, app) cell: `condition_id`, `condition_label`, `app_slug`, `n_repeats`, `k_passed`, `pass_agree`, `deploy_agree`, `checks_all_agree`, `<metric>_mean|sd|min|max|cv` for every metric with data, `within_shingle_jaccard`, `within_line_jaccard`, `within_path_jaccard`, `code_finding_jaccard`, `dependency_finding_jaccard`, `job_ids` (space-separated) and `interim`.

Payloads are cached for 6 hours, keyed on the state of the experiment's jobs and tool results, so a finished analysis or backfill shows on the next load. Pairwise code similarity and replay probes are cached for 30 days (a finished job's code does not change).

**Outside an experiment.** Two more endpoints group runs by setup instead of by cell:

- `GET /jobs/{id}/replicates/` (the job page's **Same setup** tab, and the analysis run page): every job of yours with the job's `config_fingerprint`; legacy jobs without one are grouped by (mode, model, app, prompt hash, method, stack), marked `approximate`. Each member has a relation to the job (`self`; `replay`: same `run_fingerprint`; `rerun`: an ad-hoc run or a superseded retry; `replicate`: another planned repeat), its metrics (pass rate, source LOC and files, cost, tokens, LLM calls, wall time, code issues, issues per kLOC) and its code similarity to the job, with a summary (n, mean, SD, min, max, CV) per metric. At most 50 members, with a note when capped. `determinism` holds a probe per replay: the first LLM call whose stored response (status, content, tool name and arguments; random ids ignored) differs, call counts and providers, judged on the stored call artifacts (content cut at 8,000 characters).
- `GET /models/{model_id}/variability/` (the model page's **Generation data** tab): the same-setup groups of your completed runs of a model you can see. Per group: n, pass agreement, within-group SD and CV per metric, median shingle similarity, replay probes; overall: pooled SD, median CV, pass-agreement share, median similarity and replays reproduced (x of y). The within-group spread pools seeded replays with independent replicates.

## Automation

### Schedules

A schedule (`ExperimentSchedule`) fires an action on a cron expression in its own timezone. The minimum interval is 15 minutes (`SCHEDULE_MIN_INTERVAL_S`).

| Action | Effect |
|---|---|
| `top_up` | Re-run failed and cancelled cells (reopens a finished experiment). |
| `add_replicates` | `repeats += params.replicates` (1..5), then top up: new repeat blocks run after the existing ones. |
| `clone_and_run` | Clone the experiment (name from `params.name_template`, default `"{name} {date}"`; `created_by_schedule` set) and launch the clone. |

The executor checks for due schedules every 30 s. It claims them with `SKIP LOCKED` (two ticks never fire one schedule twice) and computes the next run from `max(now, next_run_at)` (DST-safe), so an executor that was down never fires a burst of catch-up runs. With `skip_if_active` (default) a fire is recorded as `{outcome: "skipped"}` when the experiment still has pending or running jobs or is paused, and for `clone_and_run` also while a clone it launched earlier is still active. The action runs on the executor's own single-thread schedule pool. Errors are recorded in `last_result` (`{outcome: fired|skipped|error, batch_id?, experiment_id?, jobs_created?, detail}`), never raised; `top_up` and `add_replicates` record `digest_drift` when a local model's weights changed since launch, and a refused launch also rolls back the replicates it would have added. `top_up` and `add_replicates` never launch a draft (`skipped`, "experiment not launched yet"), and a `top_up` with no failed, cancelled or new cell is `skipped` ("nothing to top up") instead of creating an empty batch. Draining the executor stops schedule firing.

Routes: `GET/POST …/schedules/`, `PATCH/DELETE …/schedules/{sid}/`, `POST …/schedules/{sid}/run-now/` (fires the action now), and `GET /api/generation/cron-preview/?cron=&tz=` (validity and the next five runs).

### Post-run

`post_run` configures what happens once the runs finish:

```json
{"evaluate": true,
 "analysis": {"enabled": true, "tool_slugs": ["bandit", "semgrep"], "only_passing": false},
 "report": {"enabled": true, "exclude_truncated": true}}
```

- `evaluate` grades each job right after its run (the hidden conformance suite); it is a per-job step, not a post-run step.
- Analysis needs at least one analyzer (`no_analyzers`); unknown slugs are a 400 (`unknown_analyzer`), and so are dynamic analyzers (`analysis_mode: dynamic`, e.g. ZAP, Nuclei, Wapiti; `dynamic_analyzer`): they need a running app, which post-run analysis does not start. The UI only offers installed static and AI analyzers. See [Analysis pipeline](/docs/ANALYSIS_PIPELINE).
- When the experiment completes (exactly once per completion, even under concurrent job ends) an `ExperimentPostRun` (trigger `completed`) is created with a frozen copy of the configuration, if analysis or the report is enabled. `POST …/post-run/run/` queues one by hand (trigger `manual`; 400 while one is active).
- The executor claims post-runs one at a time (leased; status `pending → running → completed | failed | cancelled`). Analyses run **sequentially** (the analyzer workspace is shared) over the completed current-attempt jobs, each with only the configured analyzers that have not measured it yet (latest result completed, or skipped as not applicable; `only_passing`: pass rate 1 only). A failed or interrupted run, a failed tool or an ad-hoc run of other tools does not count as analysis, and analyzers added to the configuration later reach jobs analyzed before; a failed analysis is counted (`progress.analysis_failed`) and the rest continue. Then the `experiment_report` is generated synchronously, owned by the experiment's owner. Progress is published as `post_run` events. A running post-run whose lease expired (executor restart) is claimed again and resumed: the run its dead executor left running (heartbeat older than 60 s) is failed as interrupted and that job is analyzed again.
- When the apps daemon's memory cannot fit the analyzer workspace next to full sandbox and evaluation lanes (see [Execution](/docs/EXECUTION#slots-and-the-memory-rule)), post-runs wait until those lanes have room.

`GET/PUT …/post-run/` reads and sets the configuration and lists recent post-runs.

### Reports

The `experiment_report` (`config: {"experiment_id", "exclude_truncated"}`) is owner-scoped: only the experiment's owner (or staff) can generate it. It embeds the leaderboard and the paired-vs-baseline analysis from `experiment_results.compute`, plus per-condition code statistics (code findings counted with one instrument set over every condition, non-test LOC, cost, duration; see [Analysis pipeline](/docs/ANALYSIS_PIPELINE#measurement-rules-for-comparisons)). Its Markdown/LaTeX 'Code issues per condition' table shows apps and runs analyzed, code issues and their density, code security issues, test-code and runtime (DAST) issues apart, and dependency advisories split into versions the model chose and inherited ones; the Experiment table states the planned repeats analyzed and the truncation rule (frozen plan or report setting). It does not diff every condition pair. The broader `study_report` (same scoping) is described in [Study tables](/docs/STUDY_TABLES#study-report).

## API summary

All routes are under `/api/generation/` and owner-scoped (another user's experiment, condition or schedule is a 404). Full request and response shapes are in [API reference](/docs/api-reference).

| Route | Purpose |
|---|---|
| `GET/POST /experiments/` | list (paginated) / create with conditions (`?launch=true`) |
| `POST /experiments/preview/` | stateless preview of an unsaved design |
| `GET/PATCH/DELETE /experiments/{id}/` | detail / update / delete |
| `GET/POST/PUT /experiments/{id}/conditions/`, `POST …/conditions/bulk/`, `DELETE …/conditions/{cid}/` | conditions (draft only) |
| `GET/POST /experiments/{id}/preview/` | preview |
| `POST /experiments/{id}/launch/` | launch or top up |
| `POST /experiments/{id}/pause/`, `resume/`, `cancel/`, `archive/`, `clone/` | lifecycle |
| `GET /experiments/{id}/status/`, `cells/`, `results/`, `variability/`, `batches/`, `export/` | read models |
| `POST /experiments/{id}/jobs/requeue-legacy/` | hand legacy jobs to the executor |
| `GET/POST /experiments/{id}/schedules/`, `PATCH/DELETE …/{sid}/`, `POST …/{sid}/run-now/` | schedules |
| `GET/PUT /experiments/{id}/post-run/`, `POST …/post-run/run/` | post-run |
| `GET /cron-preview/` | cron validation |

`GET /experiments/{id}/` returns `analysis_plan` (`{}` without one), `analysis_plan_hash` (`''` until launch) and `analysis_plan_frozen_at` (null until launch). A post-run's `report_id` (in the post-run `runs` and in `latest_post_run`) is the report's public id (`rpt_…`), the id `/reports/{id}` and `/api/reports/{id}/` take.
