# Experiments

A designed run — apps × conditions (model × harness preset, or model × profile) × repeats — with a name, a hypothesis, and a reproducibility seed. Experiments are the research entry point; the [sample generator](/docs/GENERATION_PROCESS) stays available for one-off, ad-hoc generation.

## Model

- **Experiment** (`backend/generation/models.py`) — name/slug/description/hypothesis, `status` (draft → running → completed/archived), the set of `app_requirements` under test, `repeats` per cell, an optional `base_seed`, a `mode`, and the settings every cell shares:
  - `app_requirements`: only app specs you can see (the catalog's and your own). Create and update answer 404 for any other id; an id is never dropped silently.
  - `stack_slug`: the experiment-wide stack. Every condition runs on it, and a condition profile on another stack is refused (400). It must be a stack you may run (404 unknown, 403 not approved yet). Null keeps the legacy rule: each condition runs on its profile's stack, and an agent condition without a profile on the platform default (`GENERATION_DEFAULT_STACK`).
  - `sampling_preset` and `budget_preset`: pinned [run preset](/docs/GENERATION_PROCESS#run-presets) versions. In agent mode, null means the system default preset (`sampling/provider-default`, `budget/platform-default`).
  - `llm_defaults` (sampling params for every cell) and `agent_config` (explicit budget / sampling-policy / network overrides, plus an optional `harness` for conditions that name none).
  - `continuation_limit` / `enable_repair` are stored but not applied.
- **mode**:
  - `agent` is the default. Every cell is an agent run: a sandboxed harness builds the app and the platform grades it ([Generation process](/docs/GENERATION_PROCESS#agent-mode)).
  - `scaffolding` uses template prompts. Experiments created before agent mode existed keep this mode. A scaffolding experiment takes no budget preset (400), and a sampling preset applies only when one is chosen, and only its `llm_params` (no implicit default).
- **ExperimentCondition**: one cell of the condition matrix. It has:
  - a `model`,
  - agent mode: a `harness_preset` (its harness name is stored with it), or a legacy `harness` name, which runs that harness's system default preset; with neither, the experiment's `agent_config.harness`, else the platform default harness. An optional `profile` contributes only its stack (legacy experiments; with an experiment stack it must match) and its `llm_config`; its prompt blocks do not apply, because the brief is rendered from the app spec,
  - scaffolding mode: a `profile` (required), no harness,
  - an optional `label`,
  - `param_overrides`: sampling params from `llm_params.SAMPLING_KEYS`, overriding the experiment's `llm_defaults` for this condition. This is a declared ablation and always shows in comparability.

  Two conditions that would run the same thing (same model, profile, harness, normalized options and overrides — e.g. harness `aider` and the `aider-default` preset) are refused as `duplicate_condition`.
- **GenerationJob** carries `experiment`, `condition`, and `repeat_index` once launched — a job is one run of one matrix cell (or the retry that replaced a failed one; see Seeds below).

Layer precedence (later wins), resolved by one helper (`services/experiments.py:experiment_layers`) for comparability, the cost estimate, launch and export. Launch copies the result into each job's own fields; the preset foreign keys are provenance only.

- agent config (`job.agent_config`): platform defaults → budget preset → sampling preset `sampling_policy` → experiment `agent_config` → the condition's harness. Explicit experiment overrides beat the presets. This is the same order as inline `agent_config` in an ad-hoc batch (`POST /jobs/agent/`): a preset is a reusable default, an override is a choice made for this experiment. So an experiment whose `agent_config` sets `sampling_policy` runs that policy even when its sampling preset names another one. The preview's `agent_config` and every job snapshot show the policy that applies; the preset is still recorded as provenance.
- LLM params (`job.llm_params`): sampling preset `llm_params` → `llm_defaults` → condition `param_overrides`. The snapshot puts two layers below these: in agent mode the `max_tokens` column (32000 unless a layer above sets it) and then the condition profile's `llm_config`; in scaffolding mode the mode default (`temperature` 0.3, `max_tokens` 32000) and then the profile's `llm_config`.
- harness options (`job.harness_options`): the condition's harness preset (none for a legacy harness name, whose system default preset has default options).
- stack: the experiment's `stack_slug`, else the condition profile's stack, else the platform default.

The full matrix is `conditions × app_requirements × repeats` (`services/experiments.py:expand_matrix`).

## Lifecycle

1. **Draft** — create the experiment, add app requirements and conditions (`api/views/experiments.py`). Editable only while in draft.
2. **Preview** (`POST /experiments/{id}/preview/`): matrix size and a rough cost estimate, the effective presets, stack and agent config, and the problems that would refuse a launch (`launch_errors`). In agent mode it also returns a **comparability** report, resolved exactly as launch will resolve it. Per condition: the harness, its version, preset and non-default options; the sampling policy, forced params (without seed, provider and `max_tokens`), output cap, provider routing, seed support and dropped params; the context window and its source; the stack, profile and `param_overrides`; plus problems such as a param a model cannot take or a tool-less model on a tool-calling harness. `differs_on` names what varies across conditions (`harness`, `harness_options`, `sampling_policy`, `forced_params`, `output_cap`, `provider_routing`, `seed_forwarded`, `stack_slug`, `profile`, `param_overrides`), so an unintended difference is visible before any money is spent. Two conditions that resolve to the same configuration are flagged `duplicate_condition` (a warning: a pseudo-replicated arm). Scaffolding mode estimates from each condition's model pricing (not billing-accurate, just a sanity check before spending real API budget). Agent mode reports the budget cap × jobs as an upper bound (`cost_estimate_kind: "budget_upper_bound"`).
3. **Launch** (`POST /experiments/{id}/launch/`) — resolves every condition first and refuses the whole launch (`400 {detail, code, errors: [{code, detail, condition_id}]}`, nothing written) on a blocking problem: an invalid preset, an unknown or unusable stack or a stack mismatch, a missing profile, an unknown harness or a preset that does not match it, a tool-less model on a tool-calling harness, a reasoning budget above the output cap, or any resolution error. Otherwise it creates one `GenerationJob` per matrix cell, copies the preset values into the job fields (with the preset foreign keys as provenance), and dispatches them through the same machinery as an ad-hoc batch of the experiment's mode. The launch batch records `experiment_id`, the base seed, trials, stack and preset ids. **Idempotent and resumable**: re-launching only creates jobs for cells whose job failed or was cancelled (or never existed), so a partial failure can be retried without duplicating completed work. Moves the experiment to `running`. In agent mode:
   - **Common random numbers** (with a `base_seed`): every condition of an (app, repeat) cell gets the same seed (see Seeds below).
   - **Randomised complete blocks:** all cells of repeat 0 run before any cell of repeat 1, shuffled within each repeat by the base seed (or, without one, a seed derived from the experiment id). The FIFO run queue keeps that order, so conditions are never confounded with time of day.
   - **Automatic grading:** every sample is graded by the hidden conformance suite ([Generation process](/docs/GENERATION_PROCESS#platform-evaluation-hidden-conformance-suite)).
4. **Status** (`GET /experiments/{id}/status/`) — a progress grid (condition × app, each cell showing its repeats' statuses) plus running cost from completed jobs' metrics.
5. **Export** (`GET /experiments/{id}/export/`) — the experiment's config (with the effective agent config and preset provenance), conditions (effective harness, harness preset, options, LLM params, stack), and every job's seed/prompt_hash/bundle_key/config_fingerprint/presets/metrics, for external analysis.

## Seeds

With `base_seed` set, each cell's seed is derived deterministically: `profile_resolver.derive_experiment_seed(base_seed, *parts)` is the first 4 bytes (big-endian) of the SHA-256 of the JSON list `[base_seed, *parts]` (parts as strings), modulo 2³¹−1. The parts are:

- agent mode: `(app_id, repeat_index)`, so every condition of an (app, repeat) cell shares its seed (common random numbers);
- scaffolding mode: `(condition_id, app_id, repeat_index)`, the historical derivation, kept so old launches stay reproducible.

A relaunch re-derives the same seed for every cell it tops up. Without a base seed, each job draws its own random seed (recorded as `experiment_seed`), so conditions are not paired on seed and a relaunch draws new ones.

Retrying a failed or cancelled job from its job page replaces that trial: the retry takes over the job's cell (experiment, condition, repeat index) and runs under the cell's seed, so the pairing holds. It is refused when a live or completed job already fills the cell (a relaunch top-up or an earlier retry). Replay (same snapshot and seed) and rerun (same snapshot, fresh seed) are extra runs outside the design: they keep the presets but join no experiment, so experiment reports and relaunch coverage never count them as trials. A rerun stays in the launch batch; a replay joins no batch. To add trials, raise `repeats` and relaunch.

## Comparing results across conditions

Every `GenerationJob` carries `prompt_hash` (hashes the rendered prompt material: for scaffolding, templates + app spec + block versions; for agent runs, the task brief + contract version, shared across harnesses; never the seed or model) and `bundle_key` (`slug@version`; for agent runs the method key `agent:<harness>@<version>` with content-addressed suffixes for non-default options, sampling and budget — see [Identity keys](/docs/GENERATION_PROCESS#identity-keys)). Agent jobs also carry `config_fingerprint`, which replicates of one condition share. Rankings, statistics, and reports accept optional `prompt_hash`/`bundle_key` query params to scope a comparison to jobs generated from the *same* prompt version — without this, jobs from different profile edits on the same app get silently pooled, which confounds a model comparison with a prompt comparison. Jobs also carry `experiment`/`condition` FKs once launched from an experiment, so the same endpoints also accept `experiment_id` to scope directly to one designed run without knowing its prompt_hash/bundle_key up front.

In the `experiment_report`, a condition's `bundle_key` is its method reference: `profile@v`, the harness preset's `slug@v` (or the harness name), joined by `/`. The `template_comparison` report groups rows by `(model, bundle_key)` rather than by model alone, so a profile that changed mid-experiment shows up as separate rows instead of blended stats; `mixed_bundle_versions` and `mixed_prompt_versions` flags on the report/stats payloads make this visible even when you didn't explicitly filter.

The `experiment_report` report type (`config: {"experiment_id": ...}`) is purpose-built for one designed experiment: one row of trial-level stats per `ExperimentCondition` (reusing the same stats `template_comparison` uses), plus every condition pair diffed on the headline metrics (findings density, critical/high count, functional pass rate, cost, duration) so an A/B swap between two conditions surfaces as a delta instead of two columns to eyeball.

## Frontend

Experiments and workflows share the `/automation` hub. Filter to experiments at `/automation?type=experiment`, and create one at `/automation/create?type=experiment` (name, hypothesis, mode, stack, sampling and budget presets, repeats, base seed, app selection). `/experiments/[id]` adds conditions — model + harness preset in agent mode, model + profile in scaffolding mode, optional param overrides — then preview with the comparability table, launch, and follow the live progress grid. Existing experiment links remain supported. `lib/api/experiments.ts` is the client.
