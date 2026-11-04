# Budget

How a study's money, tokens and machine time are controlled: budget presets and their knobs, the effective
policy and its lint, the cost estimate, reservations and blocks, live spend, spend caps and quotas, stop
rules, pilot gates and early stopping. The design is in `docs/design/SAMPLE_GEN_V3.md` section 4; the code
is `backend/generation/budget/` and the claim hooks in `backend/execution/`.

Three rules hold everywhere:

1. **Budget is enforced outside the model.** No prompt ever mentions requests, tokens, dollars or minutes.
   The gateway enforces each run's caps (with a pre-call reservation); the scheduler enforces cell, study,
   user, endpoint and quota caps through the reservation ledger.
2. **Hard caps are hard.** Before a run starts, its worst case is reserved in the ledger; a study can never
   commit more than its cap. A run never gets a smaller cap mid-study (that would change the treatment).
3. **Budget stops are balanced.** Runs are claimed in replicate blocks; a budget stop never leaves some
   (condition, unit) pairs at full n and others at zero.

## Budget presets

A budget preset is a versioned `ResourcePolicy` (immutable per `slug@version`). Three tiers ship in
`backend/generation/data/resource_policies/`:

| | frugal (default) | standard | generous |
|---|---|---|---|
| Run cap | $0.30 | $2.00 | $10.00 |
| Cell cap (all attempts) | $0.60 | $4.00 | $20.00 |
| Study cap | auto (p90 × 1.1) | auto (p90 × 1.25) | auto (p90 × 1.5) |
| Parallel runs | 2 | 4 | 8 |
| Reasoning | effort low | provider default | effort high |
| Routing | cheapest provider, max price model × 1.25 | provider default | provider default |
| Serving tier | auto_batch (single-shot slot calls) | standard | standard |
| Pilot | 1 repeat on 3 units, smoke cells first | 1 repeat on all units | off |
| At the cap | stop balanced | pause | pause |

A preset holds about 75 knobs (`contracts/resources.py:KNOB_META`), grouped as in the editor:

- **run** (one attempt): `max_cost_usd`, `max_total_tokens`, `max_uncached_input_tokens`, `max_output_tokens`,
  `max_llm_requests` (main calls), `max_aux_requests` (subagent / compaction / auxiliary), `max_turns`,
  `max_workspace_bytes`;
- **call** (forced on every request): `max_output_tokens`, `max_input_tokens` (refused above, never shrunk),
  plus `context.max_context_tokens` / `context.compaction` for the harness;
- **reasoning**: `{mode: provider_default | effort | max_tokens, effort?, max_tokens?}` and `exclude`;
- **caching**: `prompt_cache` (auto / on / off), `ttl` (5m / 1h), `sticky_session`;
- **routing** (OpenRouter `provider`): `provider_sort`, `max_price` (`{prompt, completion}` or `"model×k"`),
  `allow_fallbacks`, `only` / `ignore` / `order`, `quantizations`, `require_parameters`, `data_collection`,
  `zdr`, `preferred_min_throughput` / `preferred_max_latency`, `serving_tier` (standard / flex / batch /
  auto_batch);
- **time, retries, sandbox**: wall clock, idle timeout, command timeout, per-call timeout, upstream retries,
  backoff, statuses retried, whole-run infra retries, whether failed calls count, sandbox memory and CPUs,
  network policy;
- **concurrency**: `study_max_parallel`, `per_endpoint`, `per_model`, `per_provider`, `rpm_per_model`,
  `max_resident_models`, `eval_parallel`;
- **cell / study**: `cell.max_cost_usd`; `study.max_cost_usd` (number or `auto`), `max_total_tokens`,
  `max_runs`, `max_wall_hours`, `cap_basis` (billed / list / auto), `budget_basis` (usd / tokens / auto),
  `alert_at`, `on_budget_exhausted` (pause / stop_balanced / stop), `stop_rules`;
- **pilot**: `enabled`, `repeats`, `units`, `smoke_first`, `reuse_as_main`, `auto_expand` and the nine gates;
- **caches, slot calls, estimate**: dependency cache, image / parent-build / evaluation reuse,
  `slot.max_output_tokens`, `estimate.method`, `estimate.require_confirm_above_usd`.

Every knob has a **validity class**: C (changes what the model sees or how it samples) and R (can change the
outcome through truncation) are in the treatment view and so in `generation_policy_hash` and
`config_fingerprint`; O (operational) is recorded in the manifest, never hashed; S (stopping and design
rules) is in `prereg_hash` and frozen by the confirmatory lock. `run.max_cost_usd` is R under a USD budget
basis and O (a backstop) under the tokens basis. The always-visible **key limits** are the study cap, run
cap, cell cap, repeats, max parallel, reasoning, serving tier and pilot on/off.

Editing knobs in the builder stores a new user-owned version (`derived_from` the preset, tier `custom`);
`budget.policy.apply_overrides(base, {"run.max_cost_usd": 0.5, ...})` builds and validates that content.

## Effective policy and lint

`budget.policy.effective_policy(base, overrides, model=, harness=, study_ctx= | study=)` returns
`EffectivePolicy {content, budget_basis, cap_basis, serving_tier, clamps, notes, issues}`:

- `auto` values resolve: `budget_basis` is `tokens` for confirmatory multi-model studies, else `usd`;
  `cap_basis` is `list` when an endpoint is not billed (subscription or local); `serving_tier: auto_batch`
  resolves once per study (`resolve_serving_tier`): batch only when every slot condition is single-shot on a
  batch-capable endpoint and `BATCH_BACKENDS_VERIFIED`, else standard for all (note `batch_unsupported`).
  Agentic runs are always standard.
- The resolver uses `budget.policy.resolve_study_policies(study, [ConditionInput(...)], ...)`: one call per
  study builds the full study context, resolves the serving tier once, resolves every condition and runs the
  cross-condition lint (`resources_vary_by_stack`, `mixed_serving_tier`).
- Clamps, each recorded: call output cap ≤ model max output − reasoning budget; context ≤ model context;
  reasoning dropped for models without it (`reasoning_unsupported`); `model×k` resolved to prices
  (`max_price_unresolved` without prices); sandbox ≤ machine maxima (`sandbox_clamped_to_machine`), then
  raised to the study's largest stack minimum; under the tokens basis the run cap becomes
  `max(policy, p99 × 1.5)`.
- `treatment_view(effective)` is the C and R subset after clamping: two presets that clamp alike hash alike
  (`generation_policy_hash = H(treatment_view)`).

The lint (`lint(content, model=, harness=, study_ctx=)`) reports registered issues:
`context_cap_above_call_cap` (call cap below 1.1 × context, with a fix), `call_cap_without_compaction`,
`reasoning_mode_invalid` (exactly one of effort / max_tokens), `reasoning_budget_too_large`,
`reasoning_budget_too_small` (Anthropic < 1024), `output_cap_below_harness_min`, `slot_output_cap_tight`,
`brief_exceeds_call_cap`, `slot_context_exceeds_model`, `model_lacks_tools`, `batch_requires_single_shot`,
`provider_unpinned` (error in confirmatory unless provider is a random effect), `usd_basis_multi_model`,
`model_alias_in_confirmatory`, `slot_sampling_provider_default`, `adaptive_stop_in_confirmatory`;
`lint_study` adds `resources_vary_by_stack` and `mixed_serving_tier` across conditions. All three presets
pass the lint (tested).

`GET /api/generation/budget/policies/{ref}/explain/?model=&harness=&study=` shows the effective values,
clamps, notes, issues, treatment view, hash, a one-line summary ("Frugal v1 · $0.30/run · auto study cap ·
2 parallel · effort low · no overrides") and the diff against the tier.

## Estimate

`budget.estimate.estimate_cell(cell)` gives `{expected_usd, expected_list_usd, p10, p90, cap_usd,
expected_tokens, expected_wall_s, p90_wall_s, basis}`:

1. **Priors** (`estimate.method=priors`): the first `CostPrior` with n ≥ 5 among
   `kind|harness|model|size|tier`, `kind|harness|model|*|tier`, `kind|harness|family|size|*`,
   `kind|harness|*|size|*`, scaled by the price drift since the prior was computed.
2. **Heuristic** (agentic): `R = min(max_llm_requests, r0[harness])` calls whose prompts grow by 1800
   tokens up to the context ceiling, 900 output tokens and the reasoning average per call, a prompt-cache
   discount (h = 0.8) when caching is on, times the routing calibration (billed / catalog). p10 = 0.4 ×,
   p90 = 2.2 ×. Slot agents halve r0.
3. **Single-shot slot calls**: prompt tokens (tiktoken when its cache is seeded, else chars / 3.6), output
   `min(slot cap, 1.6 × original + 150) + reasoning`, the shared context cached across the N replicates, ×0.5
   for batch serving. p10 = 0.7 ×, p90 = 1.6 ×.
4. `cap_usd` is the theoretical worst case of one attempt (bounded by the run cap): what the scheduler
   reserves. Estimates are truncated at it. A model without prices gets the run cap (never a free run).
5. Failure overhead: `infra_retry_rate × mean partial-run cost` from history.

`estimate_plan(plan)` gives the study `Estimate`: expected, p90 (a sum of cell p90s: conservative), worst
case, list-price and token totals, `by_condition`, `by_level {factor: {level: usd}}`, the `fit`
(`max_repeats_under_cap`, `max_units_under_cap`, `blocks_under_cap`), the time range and evaluation machine
minutes, the resolved `auto` cap, and `estimate_exceeds_cap` (warn when p90 > cap, error when expected > cap,
with "raise cap to X" and "reduce repeats to N" fixes). After a pilot, `reestimate_after_pilot` updates each
condition with its pilot costs (log-normal, n-weighted). `POST /api/generation/budget/estimate/` takes
`{design}`, `{study}` or `{study, plan_hash}`.

`manage.py refresh_cost_priors [--key K] [--window-days N] [--dry-run]` recomputes the priors from finished
runs that recorded a cost (nightly; the executor also refreshes a run's keys when it finishes).

## Blocks and reservations

Cells are claimed block by block: a block is every cell sharing `(stage, repeat_index, unit)`, blocks run
repeat-major in the plan's `sequence`. Before the scheduler claims a cell it locks the study row and:

1. computes `need = min(run.max_cost_usd, cell.cap_usd)` (and the run token cap);
2. if the block has not started: reserves the whole block (Σ need of its cells) or, when that would pass a
   study cap (USD, tokens, `max_runs`, `max_wall_hours`), applies `on_budget_exhausted`:
   - `stop_balanced`: no new block starts; the pending runs of unstarted blocks end `excluded:budget_stop`;
     started blocks finish; the study ends `stopped` (reason `budget`) when idle;
   - `pause`: the study pauses (reason `budget`) until the cap is raised;
   - `stop`: every pending run ends `excluded:budget_stop` and the study stops now;
3. excludes the cell (`excluded:cell_budget`) when its spend plus the need would pass `cell.max_cost_usd`;
4. pauses the study for a blocking SpendCap (see below);
5. moves the need from the block to the run (`res:{run}:{attempt}`). An infra retry reserves afresh from
   what is left, never from the block.

The gateway's cost cap is that run reservation (`budget.gateway_config.build(run)`), so the scheduler's and
the gateway's reservations bound the same amount. The invariant `spent + held ≤ study cap` holds at every
instant (tested, including under concurrent claims). Paired and by-condition results use complete blocks only
(`budget.report.complete_blocks`); after a budget stop, counts per (condition, unit) differ by at most one. Replicate cells
(`beyond_plan=True`) are not part of any block: `complete_blocks` and `block_status` ignore them.

The ledger (`SpendLedgerEntry`) is append-only and idempotent through `dedupe_key`:
`reserve` / `release` rows for runs and blocks, `usage` rows from live polls and the local proxy, and one
`adjust` per attempt at settlement (final billed cost from `calls.jsonl`, else the manifest price table,
else the recorded attempt usage). The reconciler settles any ended attempt that still holds a reservation and
releases the reservations of finished blocks.

## Live spend

The sandbox supervisor calls `budget.live.poll(run, state=...)` with the gateway's `state.json` every
`BUDGET_LIVE_POLL_S × ceil(running / 8)` seconds; the delta becomes a `usage` row. Local endpoints record
one row per proxied request (`record_proxy_usage`). Events on `study:<id>`: `spend {spent, spent_list,
reserved, delta, run_id}`, `budget_alert {fraction}` when an `alert_at` fraction is crossed, `paused
{reason, condition_id?}` and `stopped {reason}`; on `run:<id>`: `usage`.

`GET /api/generation/budget/studies/{id}/` returns the cap, spent (billed and list), reserved, remaining,
expected remaining, tokens, burn rate, ETA, `by_condition` (spent, runs, usable, tested, cost per usable and
per tested sample, exclusions), `by_model`, alerts, the stop rules that currently fire, `blocks {started,
complete, total}` and `deviations {stop_reason, exclusions, stopped_early, paused_conditions, adaptive_n}`.
`/budget/studies/{id}/ledger/` pages the ledger; `/budget/me/` and `/budget/header/` give the user's period
totals and the header chip.

## Spend caps and quotas

`SpendCap(scope = user | endpoint | model | global, period = 5h | day | week | month, unit = usd | list_usd |
tokens, limit, action = block | warn)`. Usage in the rolling window plus what open reservations still hold
counts against it. A blocking cap pauses the study (reason `quota` for 5 h / weekly model caps, such as a
subscription's per-model limits, else `spend_cap`); its runs stay queued and the reconciler resumes the
study once the next run fits. A `warn` cap only logs. Staff set global, endpoint and model caps and caps on
any user; users set their own caps, only below a staff cap (`/api/generation/budget/caps/`).

## Stop rules, pilot gates and early stopping

`study.stop_rules` run after every run completion and every 60 s (`budget.stop_rules.evaluate`). Defaults
in every tier: `infra_error_rate > 0.30` over 20 runs → pause; `no_provider_rate > 0.20` → pause (usually a
too-tight `max_price`); 5 consecutive `infra_error` / `build_fail` in a condition with no tested run →
pause that condition; a condition's mean cost > 2 × its estimate after 5 runs → pause that condition;
`spend_fraction ≥ 1.0` → alert. Optional: `output_invalid_rate` (exploratory), `condition_error_rate`,
`wall_clock_total`, `ci_width_reached`. Actions: `pause`, `stop` (pending runs end
`excluded:stopped_by_rule`), `pause_condition` (only that condition stops being claimed) and `alert`.

`ci_width_reached` (slot, exploratory only) stops a condition × target once every listed primary feature's
Wilson 95% half-width (from the live provisional pattern hits) is below the threshold with n ≥ `n_min`
(or n ≥ `n_max`). Its cells get `adaptive_n`; the number of looks is `K = n_at_stop − n_min + 1`, which the
pattern analysis uses for the conservative interval. Confirmatory studies never apply it and the lint
reports `adaptive_stop_in_confirmatory`.

`budget.stop_rules.pilot_gates(study)` returns, per condition, `max_infra_error_rate`, `min_build_rate` (a
pilot with nothing usable fails it), `max_cost_overrun`, `max_cap_hit_rate`, `max_truncation_rate`,
`max_call_truncation_rate`, `max_wall_clock_rate`, `usd_cap_binding` (tokens basis only), per stack
`max_oom_rate`, and a `cap_hits_by_kind` row; expanding the pilot is blocked unless every gate passes or the
override is recorded.
