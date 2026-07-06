# Automation guide

Compose generation and analysis into pipelines, fan them out over batches of models and templates, and run them on a cron schedule.

## Concepts

- **Pipeline** — a named definition made of ordered **steps**. Step kinds: `generate`, `analyze`, `report`, `wait`, `notify`, `script`.
- **PipelineRun / PipelineStepRun** — one execution of a pipeline and of each step, with per-step retries and logs.
- **Batch / BatchItem** — a fan-out: one pipeline run per combination of a parameter matrix (models × templates × …).
- **Schedule** — a cron expression attached to a pipeline; when due, it triggers a run.

Steps don't reimplement anything: a `generate` step runs the same generation service, and an `analyze` step calls the same `execute(run)` as the interactive UI — see [Generation process](/docs/GENERATION_PROCESS) and [Analysis pipeline](/docs/ANALYSIS_PIPELINE).

## The pipeline DSL

A pipeline's definition is a JSON config with a `steps` list. Each step needs a unique `name`, a known `kind`, kind-specific config fields, and optionally `depends_on` (referencing other steps by name):

```json
{
  "steps": [
    {"name": "gen", "kind": "generate", "config": {"model": "{{params.model}}"}},
    {"name": "scan", "kind": "analyze", "depends_on": ["gen"],
     "config": {"job_id": "{{steps.gen.output.job_id}}", "tools": ["bandit", "ruff"]}}
  ]
}
```

Two substitution scopes are available in step configs (`engine/params.py`): `{{params.<key>}}` for run parameters and `{{steps.<name>.output.<key>}}` for upstream step outputs. Unresolvable references pass through unchanged.

`validate_pipeline_dsl` (`backend/automation/services.py`) checks the structure on create/update; the API returns the errors as a structured 400 payload rather than saving a broken pipeline.

## Building pipelines in the UI

The `/automation` section has a node-based editor: steps are nodes, `depends_on` edges are drawn between them, and the run view overlays live statuses on the same graph.

## Execution and scheduling

Triggering a run creates the `PipelineRun` plus pending step runs, then dispatches via Celery (`run_pipeline_task`) — falling back to a daemon thread if the broker is unreachable. Schedules additionally need something ticking the scheduler: Celery beat or the fallback command, see [Background services](/docs/BACKGROUND_SERVICES).

Batches put the matrix in `Batch.config` (`{"pipeline_id": ..., "matrix": {"models": [...], "templates": [...]}}`) and expand to one run per combination.

## API

Everything is under `/api/automation/` ([API reference](/docs/api-reference)): CRUD plus clone for `/pipelines/`, run triggering and inspection (`/pipelines/{id}/runs/`, `/runs/{id}/`, `/runs/{id}/logs/`, `cancel`, `retry`), `/batches/` with cancel, and `/schedules/` with an enabled toggle.
