# Experiments

A designed run — apps × models × prompt bundles × repeats — with a name, a hypothesis, and a reproducibility seed. Experiments are the research entry point; the [sample generator](/docs/GENERATION_PROCESS) stays available for one-off, ad-hoc generation.

## Model

- **Experiment** (`backend/generation/models.py`) — name/slug/description/hypothesis, `status` (draft → running → completed/archived), the set of `app_requirements` under test, `repeats` per cell, an optional `base_seed`, and default `temperature`/`max_tokens`/`top_p`/`continuation_limit`/`enable_repair`.
- **ExperimentCondition** — one cell of the model × template-bundle matrix: a `model`, a `template_bundle`, an optional `label`, and `param_overrides` for per-condition temperature/max_tokens/top_p.
- **GenerationJob** carries `experiment`, `condition`, and `repeat_index` once launched — a job is one run of one matrix cell.

The full matrix is `conditions × app_requirements × repeats` (`services/experiments.py:expand_matrix`).

## Lifecycle

1. **Draft** — create the experiment, add app requirements and conditions (`api/views/experiments.py`). Editable only while in draft.
2. **Preview** (`POST /experiments/{id}/preview/`) — matrix size and a rough cost estimate from each condition's model pricing (not billing-accurate, just a sanity check before spending real API budget).
3. **Launch** (`POST /experiments/{id}/launch/`) — creates one `GenerationJob` per matrix cell and dispatches them through the same machinery as an ad-hoc scaffolding batch. **Idempotent and resumable**: re-launching only creates jobs for cells that don't already have a non-failed job, so a partial failure can be retried without duplicating completed work. Moves the experiment to `running`.
4. **Status** (`GET /experiments/{id}/status/`) — a progress grid (condition × app, each cell showing its repeats' statuses) plus running cost from completed jobs' metrics.
5. **Export** (`GET /experiments/{id}/export/`) — the experiment's config, conditions, and every job's seed/prompt_hash/bundle_key/metrics, for external analysis.

With `base_seed` set, each cell's seed is derived deterministically from `(base_seed, condition_id, app_id, repeat_index)` (`bundle_resolver.derive_experiment_seed`) — re-launching reproduces the same seed matrix rather than drawing fresh random seeds.

## Comparing results across conditions

Every `GenerationJob` carries `prompt_hash` (hashes the rendered prompt material — templates + app spec + block versions, not the seed or model) and `bundle_key` (`slug@version`). Rankings, statistics, and reports accept optional `prompt_hash`/`bundle_key` query params to scope a comparison to jobs generated from the *same* prompt version — without this, jobs from different template edits on the same app get silently pooled, which confounds a model comparison with a prompt comparison. Jobs also carry `experiment`/`condition` FKs once launched from an experiment, so the same endpoints also accept `experiment_id` to scope directly to one designed run without knowing its prompt_hash/bundle_key up front.

The `template_comparison` report groups rows by `(model, bundle_key)` rather than by model alone, so a template that changed mid-experiment shows up as separate rows instead of blended stats; `mixed_bundle_versions` and `mixed_prompt_versions` flags on the report/stats payloads make this visible even when you didn't explicitly filter.

The `experiment_report` report type (`config: {"experiment_id": ...}`) is purpose-built for one designed experiment: one row of trial-level stats per `ExperimentCondition` (reusing the same stats `template_comparison` uses), plus every condition pair diffed on the headline metrics (findings density, critical/high count, functional pass rate, cost, duration) so an A/B swap between two conditions surfaces as a delta instead of two columns to eyeball.

## Frontend

`/experiments` (list), `/experiments/create` (name, hypothesis, app selection), `/experiments/[id]` (add conditions via the existing model/bundle pickers, preview, launch, live progress grid). `lib/api/experiments.ts` is the client.
