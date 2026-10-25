# Automation guide

Automation is now part of each experiment: **Schedules** and **Post-run** on the experiment's Automation tab (`/experiments/{id}`). The `/automation` pages redirect to `/experiments`, and the `/api/automation/` router is no longer mounted.

| What you used to build | What to use now |
|---|---|
| A pipeline with a `generate` step on a cron schedule | An experiment with a `top_up`, `add_replicates` or `clone_and_run` schedule |
| A batch fanning a pipeline out over models × templates | One experiment whose conditions are the cross-product (models × harness presets, or models × profiles) |
| `analyze` and `report` steps after generation | The experiment's post-run: analyzers over the completed jobs, then the experiment report |

See [Experiments → Automation](/docs/EXPERIMENTS#automation) for how schedules fire (cron in a timezone, a 15-minute minimum interval, no burst catch-up, skip while active) and how post-runs run (once per completion, sequential analyses, owner-scoped report).

The `backend.automation` app, its tables and its engine stay in the codebase for existing data; nothing creates new pipeline runs.
