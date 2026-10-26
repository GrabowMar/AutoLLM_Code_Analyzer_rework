# Automation guide

Automation is part of each experiment: cron schedules and post-run steps under the experiment's **Operations → Automation** section replace the retired pipeline hub.

## Where it lives now

On an experiment (`/experiments/{id}?tab=operations`; the old `?tab=automation` still lands there), section 5 Operations → Automation has two cards:

- **Schedules**: cron triggers in a timezone (`top_up`, `add_replicates`, `clone_and_run`), with an enable switch, the next run, the last result and Run now.
- **Post-run**: the steps that run once the experiment completes (analyzers over the completed jobs, then the experiment report), with the post-run history and Run now.

The composer's Automation section sets the post-run steps and one optional schedule when you create the experiment; add more schedules later in that section. Archived experiments are read-only there. How schedules fire (15-minute minimum interval, no burst catch-up, skip while active) and how post-runs run (once per completion, sequential analyses, owner-scoped report) is in [Experiments → Automation](/docs/EXPERIMENTS#automation).

## Migrating from pipelines

| What you used to build | What to use now |
|---|---|
| A pipeline with a `generate` step on a cron schedule | An experiment with a `top_up`, `add_replicates` or `clone_and_run` schedule |
| A batch fanning a pipeline out over models × templates | One experiment whose conditions are the cross-product (models × harness presets, or models × profiles) |
| `analyze` and `report` steps after generation | The experiment's post-run: analyzers over the completed jobs, then the experiment report |

## What is left of the old app

- `/automation` and every page under it redirect (308) to `/experiments`.
- The `/api/automation/` router is not mounted (`config/api.py`).
- `backend.automation` stays installed for existing data: its models (`Pipeline`, `PipelineStep`, `PipelineRun`, `PipelineStepRun`, `Batch`, `BatchItem`, `Schedule`) and their admin pages, the `engine/` package, the Celery tasks `run_pipeline_task` and `run_scheduler_tick`, and the `runautomationscheduler` management command. No UI or API creates new pipeline runs, and no beat entry for `run_scheduler_tick` ships with the code (beat uses the database scheduler, so only a manually added periodic task would run it).
