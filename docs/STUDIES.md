# Studies

A **study** is a factorial design: factors × levels, units (task versions or slot targets), replicates, seeds, an evaluation level and an analysis plan. It replaces the v2 experiments and batches: an exploratory study is what a batch was, a confirmatory study is a pre-registered experiment. The engine lives in `backend/generation/study/` (WP-10); the API is under `/api/generation/studies/`.

The life of a study:

```
draft ──resolve (preview, nothing written)──▶ draft
  │ freeze-plan                                   (optional: persist the plan, status planned)
  ▼
planned ──launch(plan_hash)──▶ piloting ──expand──▶ running ──▶ completed | stopped
                                   │ (pilot_override)  ▲  │ pause/resume
                                   └───────────────────┘  ▼
                                                        paused        cancel ─▶ cancelled
```

## Designs

A design (`contracts.plan.Design`, stored on `Study.design`) names:

| Field | Meaning |
| --- | --- |
| `mode` | `app` (agentic whole-app generation) or `slot` (regenerate one part of frozen parents) |
| `kind` | `exploratory` or `confirmatory` (needs `analysis_plan`; locked at launch) |
| `factors[]` | `{key, role: crossed\|fixed\|fraction, levels}` (vocabulary below) |
| `units` | app: `tasks` (task version refs); slot: `slot_sets` and/or `slot_targets` (one task) |
| `repeats` | replicates per (condition, unit), 1–200 |
| `grid` | `full`, or `fractional` with a generator such as `D=ABC`; `exclude` lists level combinations |
| `base_seed`, `order_seed` | CRN seeds and the claim order |
| `scheduling` | `auto` (blocked when a local endpoint is used), `interleaved` or `blocked` |
| `resource_policy` | the budget preset (also the default level of the `resource_policy` factor) |
| `evaluation` | `{eval_suite, eval_level, overrides, slot, retest_fraction}` — not a budget knob |
| `parent_selection` | slot: `{rule: random\|all\|manual, pool_query: {task, stack, models, harnesses, studies, min_pass_rate}, k, stratify_by, seed}` (below) |
| `slot_primary_features` | detector refs (slot; required for `ci_width_reached` and confirmatory slot studies) |
| `analysis_plan` | the pre-registered plan (below) |

### Factors

| Key | Levels | Modes | Default when absent |
| --- | --- | --- | --- |
| `model` | `LLMModel.model_id` | both | required |
| `stack_profile` | stack refs | app | required |
| `starter_level` | `bare`, `minimal`, `full` | app | `minimal` |
| `style` | style refs | app; slot (component slots only) | `model-choice` (app) |
| `auth_mode` | `contract_bearer`, `cookie_session`, `spec_defined` | app | `contract_bearer` |
| `guidance` | guidance refs | both | `none` |
| `prompt_set` | prompt-set refs (mode must match) | both | `app-default`, `slot-single-shot-default`, `slot-agent-default` |
| `prose_variant` | `minimal`, `standard`, `exhaustive` | app | `standard` |
| `harness_preset` | harness preset refs | app; slot agent | `opencode-native` |
| `sampling_preset` | sampling refs | both | `provider-default` (app), `t07` (slot) |
| `resource_policy` | budget preset refs (class C/R knobs may vary) | both | `Design.resource_policy` |
| `network_policy` | `none`, `packages` | app | the policy's `network_policy` |
| `slot_executor` | `single_shot`, `agent` | slot | `single_shot` |
| `hole_mode` | `stub`, `remove`, `rewrite` | slot | `stub` |
| `keep_docstring` | `true`, `false` | slot | `false` |
| `context_policy` | a mode (`slot_file_only`, `related_files`, `full_app`, `interface_only`) or a `ContextPolicy` JSON | slot | `related_files` |

Library levels may be written as `slug`, `slug@v` or `slug@v#hash12`. **A bare slug takes the latest non-archived version visible to the study owner at plan time** (`library.service.latest`; versioned refs go through `library.service.resolve_ref`, so a wrong `#hash12` or an invisible version is `lint_unknown_library_ref`); the plan pins every level to `slug@v#hash12`, and nothing is ever looked up as "latest" at run time. Every condition records every applicable factor (defaults included), so the manifest's `factors` are complete.

The prose variant is a **factor**, never part of the unit: a unit written as `crud_todo_list@1:minimal` is the legacy shape and is flagged `prose_variant_in_unit` (error).

### Drawn parents (slot)

A slot design names the part to regenerate with **template targets** (`units.slot_targets`, or every target of `units.slot_sets`). With `parent_selection.rule` `random` or `all`, the units are instead that part in each parent drawn from the pool (`study/parents.py`): `slot.parents.draw` with `parent_selection.seed`, or a seed derived from `base_seed` (`int(H({base_seed, "purpose": "parent_selection"})[:12], 16) % 2**31`), stratified by parent model, among the runs the study owner may use. Each drawn parent gets a slot set recorded with this draw (rule and seed; reused when it exists) and each template is matched into it by `match_key`. The preview (`resolve`) only draws and reads what exists, it never freezes parents; `freeze-plan` and `launch` freeze the drawn parents and match the templates first, so the frozen plan has every (parent, template) unit and later previews equal it. A pool with no parent is `invalid_design` (`path: parent_selection.pool_query`). `rule: manual` keeps the listed slot sets and targets.

### Expansion

Conditions are the product of the factors in design order (`study/expand.py`). A fractional grid has one `fraction` factor whose level is generated from two-level crossed factors (`A`, `B`, ... name the crossed factors in order; `D=-ABC` is the complementary half). A word shorter than three letters aliases main effects (`aliasing`, warn). Exclusions and pruned combinations make the grid unbalanced (`unbalanced_grid`); `by_factor` results then use estimated marginal means and are flagged.

Incompatible combinations are **errors** unless the request sets `drop_incompatible`, which removes them and lists each as `combination_pruned` (info): a style whose `compatible_frontends` excludes the stack's frontend (`incompatible_style`), a stack without a starter at the level (`starter_missing`), `spec_defined` without a task `auth_flow` (`auth_flow_missing`), a prose variant the task lacks, a harness that is not enabled (`harness_not_enabled`) or has no gateway mode for the endpoint (`harness_protocol_unavailable_for_endpoint`).

## Plans

`POST /studies/{id}/resolve/` returns the plan without writing anything (cached in-process for 10 minutes by study and design hash). `freeze-plan` and `launch` persist it as an immutable `StudyPlan` whose payload (the full `ResolvedPlan`) and every rendered stimulus live in the blob store.

For every (condition, unit) the resolver renders the stimuli once (they never depend on the repeat), resolves the effective policy, the environment (harness image and command, gateway mode, sandbox, image digests) and, in slot mode, the resolved context. Every cell then gets:

* its **CRN seed** `int(H({"base", "unit", "repeat"})[:12], 16) % 2**31` — equal across conditions, prose variants included, for one (unit, repeat), so conditions are paired;
* its **block** and **sequence**: blocks are `(repeat, unit)` sorted by `(repeat, unit_order)` — every unit gets replicate 0 before any gets replicate 1, which keeps budget stops balanced — and the conditions are shuffled inside each block with a seed from `(order_seed, block_index)` (`blocked` keeps one model's conditions contiguous);
* its identity keys from `identity.compute_keys` (the only implementation).

With a pilot (`policy.pilot.enabled`) the plan also holds `stage="pilot"` cells: `pilot.repeats` repeats of the first `pilot.units` units in unit order. They come first in the claim sequence; the first pilot block is the smoke block. Pilot and main cells of one (condition, unit, repeat) share the seed.

`plan_hash = H({design, conditions, units, cells, prompt_hashes})` changes with any pinned level, seed, unit or rendered stimulus. A launch with a `plan_hash` that no longer matches the design answers **409 `plan_stale`**.

Plan issues come from the registry (`contracts/issues.py`); any `error` blocks launch. Besides the compatibility codes: `oracle_missing` and `conformance_missing` (warn; error in confirmatory studies), `mixed_harness_profiles`, `harness_brief_differs`, `vendor_affinity`, policy lint codes from the budget presets, `estimate_exceeds_cap`, and for slot studies `single_parent` / `manual_parent_selection` (error in confirmatory studies unless `estimand: conditional_on_parent`), `variant_model_equals_parent_model`, `slot_tests_uninformative` (an unvalidated target is an error), `slot_leak_verbatim`, `slot_leak_high`.

## Launch

`POST /studies/{id}/launch/ {plan_hash, confirm_usd?, study_cap?, pilot_override?}`:

* refuses plans with errors (400 `plan_has_errors`) and, when the p90 estimate exceeds `estimate.require_confirm_above_usd`, launches without `confirm_usd ≥ p90_total` (400 `confirmation_required`);
* resolves the study cap (`Study.cap_usd`: the request's `study_cap`, else the preset's `study.max_cost_usd`; `auto` takes the estimate) and fills `max_parallel` and `execution_options` from the effective policy;
* writes the plan's conditions and units, the cells of the launched stage, and one run per cell with its frozen manifest (cells the resolver excluded as `config_invalid` get none); runs are handed to the executor **after commit**;
* locks a confirmatory study in the same save: `locked_at`, `analysis_plan_hash`, `prereg_hash = H({plan, analysis_plan, stop_rules, pilot_gates, evaluation})`. The design, analysis plan, budget preset and active plan can never change afterwards (clone instead);
* attaches a `patterns` post-run to a slot study.

**Pilot → main** (`POST /studies/{id}/expand/`) evaluates the pilot gates and refuses on a failed gate unless `override_gates` with a `reason`. Exploratory studies with `pilot.reuse_as_main` keep the pilot cells as main repeats (same plan, same seeds); confirmatory studies never reuse pilot cells, and pilot cells are never in any analysis.

## Attempts and the current run

A cell's **current run** is its first usable run (by creation time); only when no attempt is usable is it the latest attempt. A usable run is **never superseded** — `runs.queries.refresh_current(cell)` keeps a usable current run, retries reuse the run row (same manifest and seed), and re-evaluations never move the current run. A run created on a cell that already has a usable run is `beyond_plan` and is never in results.

**Top-up** (`POST /studies/{id}/top-up/ {cells: "missing"|"infra"|[ids]}`) creates new attempts only for cells without a run or whose current run is `infra_error`, or `excluded` for `cancelled`, `stopped_by_rule` or `budget_stop`. Cells with a model-caused current run (`tested`, `build_fail`, `start_fail`, `output_invalid`) are refused with `cell_has_usable_run`.

**Added replicates** (`add-replicates`, exploratory only) resolve extra repeats from the current design; every (condition, unit) must keep its `config_fingerprint` (409 `design_drifted` otherwise). They get their own cells marked `beyond_plan` (listed in the cells view with their own current run and state) and their runs are `beyond_plan`; results, `current_runs` and attrition's planned cells leave them out (attrition counts their runs in `beyond_plan_runs`).

The **study cap** typed at launch (`study_cap`: a number or `auto`) is stored in `Study.cap_usd`, which the scheduler's budget claims read first; `auto` is the frozen plan's `estimate.cap_usd` (no USD cap when the estimate has none). `resolved_cap` in the launch response is the cap the claims enforce.

## Results

`GET /studies/{id}/results/?metric=&fingerprint=majority|all&sensitivity=exclude_truncated&baseline=&common_cells=` (`study/results.py`):

* **Sample**: the current run of every main-stage cell of the active plan. Pilot cells and beyond-plan runs never count.
* **Intention-to-treat** (the default): `tested` runs with a measured evaluation contribute their pass rate; `build_fail`, `start_fail` and `output_invalid` contribute 0 when the evaluation level measures functional checks; truncated runs (budget, wall clock, idle, max turns) stay in. `sensitivity=exclude_truncated` repeats the analysis without them and `paired.sensitivity` names conditions whose direction or significance changes. A tested run with an unmeasured evaluation is *not measured* (never 0); `infra_error` and `excluded` runs are attrition.
* **Unit of analysis**: the unit. Repeats collapse to unit means; common cells keep the `(unit, repeat)` cells every condition with data has.
* **Pairing**: `paired_crn` when both conditions forward the seed (deltas paired by `(unit, repeat)`), else `independent_repeats` (unit mean differences). The method is recorded per comparison. Exact sign test, Wilcoxon signed-rank and the unit-level sign-flip permutation test; with fewer than 20 units the permutation test is the primary test and no cluster bootstrap CI is given (`few_clusters`).
* **Gate**: p-values are confirmatory only for a locked confirmatory study whose plan declares the metric, with its pre-registered baseline (`analysis_plan.baseline`) and no deviation: `exclusions_differ`, `common_cells_off`, `fingerprint_filter` (the majority filter dropped cells), `stopped_early`, `multiplicity_family`, `exclusion_rate`, `differential_attrition`. `interim` withholds every p-value until the study ends. `significance_unreachable` tells when the units cannot reach significance under the plan's correction.
* **Also**: `attrition` (per condition), `by_factor` (EMMs, `unbalanced` flag), `by_prose_variant` (stated / unstated pass rates), `common_pass_rate`, `retest_agreement`, and the issues `mixed_config_fingerprint`, `mixed_eval_suite`, `exclusion_rate`, `differential_attrition`, `few_clusters`.

## Analysis plans

`study/analysis_plan.py` normalizes the plan (v2 keys kept for reports):

```json
{
  "primary_metric": "pass_rate",
  "secondary_metrics": [],
  "research_questions": [{"id": "RQ1", "question": "...", "metric": "pass_rate", "direction": "two-sided"}],
  "alpha": 0.05, "correction": "holm", "multiplicity": "all",
  "baseline": {"model": "deepseek/deepseek-v4.1-flash"},
  "exclusions": {"truncated": false, "exclusion_rate": 0.10, "differential_attrition": 0.05},
  "itt": true, "sensitivity": ["exclude_truncated"],
  "estimand": "marginal", "random_effects": [],
  "sequential": {"looks": [20, 40], "alpha_spending": "obrien_fleming", "binding": false},
  "patterns": {"detector_set_hash": "...", "primary_features": ["sqli"], "normalizer_version": 1,
               "cluster_params": {}, "scope": {}}
}
```

Intention-to-treat is the default (`itt: true`, `exclusions.truncated: false`); pre-registering `exclusions.truncated: true` turns it off. The `patterns` block enters `prereg_hash`; a PatternRun whose detector set, normalizer, cluster parameters or scope differ is labelled exploratory (`analysis_plan.pattern_run_label`).

## Clone, ad hoc, schedules and post-runs

* `POST /studies/{id}/clone/ {mode: exact|latest_versions, name}` copies a study as a draft; `latest_versions` re-pins every library level and returns `version_diffs`. An exact clone resolves to the same plan hash as its source and freezes its own plan row (plans are unique per study and hash); its runs share the source's seeds, so change `base_seed` for an independent replication.
* `POST /studies/{id}/prompt-overrides/ {owner_kind, owner_ref, template, role?, prompt_set_ref?}` (draft or planned studies only) saves an inline prompt edit as a forked library version and swaps the factor level that pinned the original. For `prompt` and `auth_fragment` owners the forked prompt set is `prompt_set_ref` (one of the study's prompt-set levels), else the single `prompt_set` level (the mode's default set when there is no `prompt_set` factor), else the one level whose roles or fragments use the edited template; anything else is 400 `prompt_set_ambiguous` with `data.candidates`.
* `POST /studies/adhoc/ {design, launch: true}` creates, resolves and launches in one call (automation, "Generate a parent app") and returns `{study_id, plan_hash, run_ids, status, issues, estimate}`.
* Schedules (`/studies/{id}/schedules/`): `top_up`, `add_replicates` (1–5), `clone_and_run` (exact copy with a fresh derived `base_seed`; straight to main when the source already reached main). Fires never burst after downtime; `skip_if_active` skips while the study or its last clone is active.
* Post-runs (`/studies/{id}/post-runs/`): `analysis` (configured analyzers over usable current runs, then the study conditions report) or `patterns`; `manual` runs now, `completed` when the study completes or stops.

## Export

`GET /studies/{id}/export/?format=zip`: `plan.json`, `manifests/*.json`, `runs.csv` (every attempt with `current`, `beyond_plan`, `started_at`, served providers, `same_author`, request classes, truncation), `attrition.csv`, `files.csv` (app scope), `evaluations.jsonl`, `locks/*.json` (`recorded: false` when no lock was resolved), and `patterns/` for slot studies.

## Identity changelog

Changing any identity algorithm or constant (seed derivation, block order, `plan_hash`, `prereg_hash`, the resolver's pinning) requires a protocol bump and a line here.

| Date | Change |
| --- | --- |
| 2026-11-03 | v3 resolver version 1: CRN seeds per (unit, repeat), repeat-major blocks, `plan_hash` and `prereg_hash` as in spec 3.1. |
