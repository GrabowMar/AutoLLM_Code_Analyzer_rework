# Experiments

A designed run — apps × models × generation profiles × repeats — with a name, a hypothesis, and a reproducibility seed. Experiments are the research entry point; the [sample generator](/docs/GENERATION_PROCESS) stays available for one-off, ad-hoc generation.

## Model

- **Experiment** (`backend/generation/models.py`) — name/slug/description/hypothesis, `status` (draft → running → completed/archived), the set of `app_requirements` under test, `repeats` per cell, an optional `base_seed`, a `mode` and default `temperature`/`max_tokens`/`top_p`/`continuation_limit`/`enable_repair`.
- **mode**:
  - `agent` is the default. Every cell is an agent run: a sandboxed harness builds the app and the platform grades it ([Generation process](/docs/GENERATION_PROCESS#agent-mode)). `agent_config` overrides the harness, budgets, sampling and network policy for all cells. A condition's profile contributes only its stack and `llm_config`.
  - `scaffolding` uses template prompts. Experiments created before agent mode existed keep this mode.
- **ExperimentCondition**: one cell of the condition matrix. It has:
  - a `model` and a `profile`,
  - in agent mode, an optional `harness` (`opencode`, `mini-swe-agent`, `aider`; blank = the experiment's `agent_config` / default), so (model × harness) designs are first-class,
  - an optional `label`,
  - `param_overrides`: any sampling params from `llm_params.SAMPLING_KEYS`, overriding the experiment's `llm_defaults` per condition.
- **GenerationJob** carries `experiment`, `condition`, and `repeat_index` once launched — a job is one run of one matrix cell.

The full matrix is `conditions × app_requirements × repeats` (`services/experiments.py:expand_matrix`).

## Lifecycle

1. **Draft** — create the experiment, add app requirements and conditions (`api/views/experiments.py`). Editable only while in draft.
2. **Preview** (`POST /experiments/{id}/preview/`): matrix size and a rough cost estimate. In agent mode it also returns a **comparability** report listing, per condition, the harness, sampling policy, forced params and seed support, plus problems such as a param a model cannot take or a tool-less model on a tool-calling harness. `differs_on` names what varies across conditions, so an unintended difference is visible before any money is spent. Scaffolding mode estimates from each condition's model pricing (not billing-accurate, just a sanity check before spending real API budget). Agent mode reports the budget cap × jobs as an upper bound (`cost_estimate_kind: "budget_upper_bound"`).
3. **Launch** (`POST /experiments/{id}/launch/`) — creates one `GenerationJob` per matrix cell and dispatches them through the same machinery as an ad-hoc batch of the experiment's mode. **Idempotent and resumable**: re-launching only creates jobs for cells that don't already have a non-failed job, so a partial failure can be retried without duplicating completed work. Moves the experiment to `running`. In agent mode:
   - **Common random numbers:** every condition of an (app, repeat) cell gets the same seed.
   - **Randomised complete blocks:** all cells of repeat 0 run before any cell of repeat 1, shuffled within each repeat by a seed derived from the experiment. The FIFO run queue keeps that order, so conditions are never confounded with time of day.
   - **Automatic grading:** every sample is graded by the hidden conformance suite ([Generation process](/docs/GENERATION_PROCESS#platform-evaluation-hidden-conformance-suite)).
4. **Status** (`GET /experiments/{id}/status/`) — a progress grid (condition × app, each cell showing its repeats' statuses) plus running cost from completed jobs' metrics.
5. **Export** (`GET /experiments/{id}/export/`) — the experiment's config, conditions, and every job's seed/prompt_hash/bundle_key/metrics, for external analysis.

With `base_seed` set, each cell's seed is derived deterministically from `(base_seed, condition_id, app_id, repeat_index)` (`profile_resolver.derive_experiment_seed`) — re-launching reproduces the same seed matrix rather than drawing fresh random seeds.

## Comparing results across conditions

Every `GenerationJob` carries `prompt_hash` (hashes the rendered prompt material: for scaffolding, templates + app spec + block versions; for agent runs, the task brief + contract version, shared across harnesses; never the seed or model) and `bundle_key` (`slug@version`; for agent runs `agent:<harness>@<version>`, the method identity). Rankings, statistics, and reports accept optional `prompt_hash`/`bundle_key` query params to scope a comparison to jobs generated from the *same* prompt version — without this, jobs from different profile edits on the same app get silently pooled, which confounds a model comparison with a prompt comparison. Jobs also carry `experiment`/`condition` FKs once launched from an experiment, so the same endpoints also accept `experiment_id` to scope directly to one designed run without knowing its prompt_hash/bundle_key up front.

The `template_comparison` report groups rows by `(model, bundle_key)` rather than by model alone, so a profile that changed mid-experiment shows up as separate rows instead of blended stats; `mixed_bundle_versions` and `mixed_prompt_versions` flags on the report/stats payloads make this visible even when you didn't explicitly filter.

The `experiment_report` report type (`config: {"experiment_id": ...}`) is purpose-built for one designed experiment: one row of trial-level stats per `ExperimentCondition` (reusing the same stats `template_comparison` uses), plus every condition pair diffed on the headline metrics (findings density, critical/high count, functional pass rate, cost, duration) so an A/B swap between two conditions surfaces as a delta instead of two columns to eyeball.

## Frontend

`/experiments` (list), `/experiments/create` (name, hypothesis, app selection), `/experiments/[id]` (add conditions via the model/profile pickers with per-condition param overrides, preview, launch, live progress grid). `lib/api/experiments.ts` is the client.
