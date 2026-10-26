# Automation guide

Automation is now part of each experiment: **Schedules** and **Post-run** on the experiment's Automation tab (`/experiments/{id}`). The `/automation` pages redirect to `/experiments`, and the `/api/automation/` router is no longer mounted.

| What you used to build | What to use now |
|---|---|
| A pipeline with a `generate` step on a cron schedule | An experiment with a `top_up`, `add_replicates` or `clone_and_run` schedule |
| A batch fanning a pipeline out over models × templates | One experiment whose conditions are the cross-product (models × harness presets, or models × profiles) |
| `analyze` and `report` steps after generation | The experiment's post-run: one analysis plan (a profile and/or tools) over the completed jobs, then the experiment report |

See [Experiments → Automation](/docs/EXPERIMENTS#automation) for how schedules fire (cron in a timezone, a 15-minute minimum interval, no burst catch-up, skip while active) and how post-runs run (once per completion, sequential analyses, owner-scoped report).

The `backend.automation` app, its tables and its engine stay in the codebase for existing data; nothing creates new pipeline runs.

## The analyze step

The engine's `analyze` step (`automation/engine/dispatchers.py:dispatch_analyze`) still runs for existing pipeline data, and its plan rules are the ones an experiment post-run uses. It submits one run through the v2 analysis executor and **waits** for it with a bounded poll; it never runs tools inline. It runs as the pipeline run's trigger, else the pipeline owner, never an arbitrary user.

| Key | Meaning |
| --- | --- |
| `profile` (or `profile_id`, `profile_slug`) | An analysis profile by id or slug (latest version) that you may use: system profiles and your own |
| `tool_slugs` (legacy `analyzers`) | Restricts the profile to these tools; without a profile, restricts the default profile config. `ai_review` (legacy `llm_review`) enables the reviewer. An empty list, unknown slugs or a plan with no tool are rejected |
| `generation_job_id` | The job to analyse (default: the pipeline param); your own jobs only, staff any. Or `source_code`: `{path: content}` for inline files |
| `thresholds` | `{critical\|high\|medium\|low\|info\|total: max}` over **counted static findings** of the run's measurement. A limit that is not a non-negative integer, or a template that did not resolve, fails the step |
| `timeout_seconds` | Wait bound, default 14 400 (4 h), capped at 36 000; on timeout, or when the pipeline is cancelled, the run is asked to cancel |
| `fail_on_partial` | Fail the step when the run is `partial` (a required tool failed) |
| `fail_on_threshold` | Fail the step when a threshold is exceeded, or when thresholds could not be evaluated |

Output keys: `analysis_run_id`, `status` (the run status), `summary` (the run summary; its static counts are `null` when the static measurement is unavailable, never zeros), `threshold_status` (`passed`, `failed` or `not_evaluated`), `is_complete`, `static_available`, `plan_hash`, `profile` (`slug@version`) and `error` when the step failed. `threshold_status` is `not_evaluated` when no thresholds are set, when the static measurement is unavailable, or when a limited count was not recorded: a gate that was not fully evaluated never passes.

Experiment post-runs build their plan the same way (`profile` and/or `tool_slugs`), freeze it once per post-run and analyse every job under it; see [Experiments → Post-run](/docs/EXPERIMENTS#post-run).
