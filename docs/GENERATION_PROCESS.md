# Generation process

How a sample gets made in v3. The primary mode is **agentic whole-app generation** (`app@1`, run kind
`app_agentic`): a coding-agent harness builds the app in an isolated sandbox, the platform collects what it
wrote, and the eval lane builds, starts and tests it. Slot mode (`slot@1`) reuses the same sandbox for its
restricted agent; see [Slot mode](/docs/SLOT_MODE). This page covers the generation stage, which ends when the
sample is collected; evaluation is in [Evaluation](/docs/EVALUATION), studies in [Studies](/docs/STUDIES) and
budgets in [Budget](/docs/BUDGET). The authoritative design is `docs/design/SAMPLE_GEN_V3.md` (sections 6 and 9.5).

## What runs where

Every unit of work is a `GenerationRun` row run by the executor (`llmlab-executor`, `backend/execution`); the web
process never runs one. The executor's sandbox lane calls `generation.agentic.dispatch.run_stage(run)`, its
only generation entry point:

| Kind | Generation stage | Lane |
| --- | --- | --- |
| `app_agentic` | `agentic.runner.run_app_agentic` | sandbox |
| `slot_agent` | `agentic.runner.run_agentic` with `slot.executors.restricted_agent.prepare(run)` | sandbox |
| `slot_single_shot` | `slot.executors.single_shot.run_stage` (or the batch submitter when the serving tier is `batch` and `BATCH_BACKENDS_VERIFIED`) | llm |
| `reference` | none: created in `running/eval_queued` and claimed by the eval lane | eval |

On the apps daemon (apps-dind), one attempt of an agentic run uses:

```
executor --(apps daemon)--> apps-dind
   llmlab-agent-<run>-a<n>    internal network only     harness + workspace, dummy API key
   llmlab-gateway-<run>-a<n>  internal network + egress the real key / proxy token, GATEWAY_CONFIG
```

`a<n>` is the attempt number (1-based). An infrastructure retry gets fresh containers and networks.
Leftovers of a crashed executor carry the label `llmlab.agent-run` and are removed by
`agentic.sandbox.reap_orphans(older_than_seconds)` (called by system maintenance).

## The manifest freeze (`agentic/manifest.py`)

`build_manifest(run, resolved_cell)` runs inside the launch transaction. It copies the identity keys the
resolver computed (never recomputes them), refuses a manifest whose library refs are not pinned
(`slug@v#hash12`: `factors.*.ref`, `task.ref`, `starter.ref`, `sampling.ref`, `resources.ref`,
`harness.preset_ref`, `planned_evaluation.eval_suite.ref`; see `unpinned_refs`), and mirrors the manifest onto
the run's query columns. Once the manifest exists, `GenerationRun.save()` keeps those columns pinned: the
identity keys, kind, protocol and seed (`MANIFEST_FROZEN_FIELDS`) and the treatment columns
(`MANIFEST_PINNED_COLUMNS`: model, task version, stack, starter, style, guidance, prompt set, harness /
sampling / budget preset, prose variant, auth mode, pinned served name and digest) may only keep their value
or be filled from empty with the manifest's value (`run_column_mismatches`). `QuerySet.update()` refuses the
former and does not check the latter.

## One attempt, step by step (`agentic/runner.py`)

Each step publishes `{type: "step", step, status}` on the SSE channel `run:<id>` and is kept with its timing
in `result.steps`.

1. **resolve.** The frozen `RunManifest` (written at launch by `agentic.manifest.build_manifest`, never
   "latest" anything), the harness adapter from the registry (a harness that is not enabled gives
   `excluded:harness_not_enabled`), the effective resource policy recorded in the manifest, and the rendered
   brief from the manifest's stimuli (blob store). The harness context carries the model's context length
   (`HarnessContext.model_context_window`, from the gateway config's frozen model facts; `None` when unknown).
2. **images** (`agentic/images.py`). The harness image (`FROM agent-base`), the gateway image (plus the
   `llm-gateway-local` overlay for local endpoints) and the starter base images are built or pulled and their
   **digests compared with the manifest**. A mismatch ends the run `infra_error:image_digest_mismatch`
   without running anything: a sample never runs on an environment other than the one it froze.
3. **workspace** (`agentic/workspace.py`). App runs are seeded with the Starter's files from the blob store
   (the starter's own `.gitignore`, no platform excludes). A slot agent gets the parent's frozen tree with the
   hole applied, minus artifact rows, minus the parent's tests unless `include_tests`. Every path goes through
   `sanitize_rel_path`; the seed tree hash is `result.seed_tree_hash`.
4. **gateway.** `budget.gateway_config.build(run)` gives the attempt's `GatewayConfig` (model pin, sampling,
   reasoning, budget caps with pre-call reservation, routing, caching, retries). The credentials come from
   `inference.gateway_wiring.for_job` with the manifest's frozen endpoint.
5. **sandbox** (`agentic/sandbox.py`). The adapter renders files, env and command (the brief is a file or
   stdin, never argv). The agent container runs with the study's resolved `sandbox.memory` and `nano_cpus`,
   `cap_drop ALL`, `no-new-privileges` and `pids_limit 512`; no writable shared volume is mounted (dependency
   caches are reached over the network through the gateway's CONNECT allowlist with `network_policy=packages`).
   The **entry script** commits the seed as `base`, writes only anchored excludes to `.git/info/exclude`
   (`/node_modules/`, `/.venv/`, `/venv/`, `/data/*.db`, `/.tmp/`), runs the harness under
   `timeout -s TERM -k 15 <wall>`, then writes `changes.patch` and an anchored `workspace.tar`. Nested
   directories (`backend/data/`, `src/build/`, `app/dist/`) are never excluded. A tar larger than
   `run.max_workspace_bytes` is replaced by a path manifest.
6. **record** (section 6.9). One `GenerationArtifact` per gateway call (`agent_call_%04d`, with its
   attempt), the trajectory (`adapter.parse_trajectory`, stored in the blob store and as `trajectory.json`),
   the observed inputs (system prompt sha, tool names, calls by request class), the **termination**, served
   providers and models, usage, the time split, the `RunAttempt` row, the collected `RunFile` rows,
   `result`, `metrics` and `validity`. The ledger then settles the attempt (`budget.ledger.settle_attempt`).
7. **decide** (`agentic/outcome.py`): hand the tree to the eval lane, end the run now, retry it as
   infrastructure, or record a cancel.

### The supervisor

The executor thread that runs an attempt supervises it:

- it heartbeats (a run that is no longer this worker's, because it was cancelled or reclaimed, is stopped);
- it polls the gateway's `state.json` and publishes `usage` events (live spend, section 4.5);
- **idle detection**: the idle clock advances only while no gateway call is in flight **and** the agent's CPU
  is below 5% of one CPU over a 10 s window. It resets whenever `last_call_end` changes and whenever the
  workspace changes. After `time.idle_timeout_s` the harness gets SIGTERM (then SIGKILL after 15 s) through its
  `timeout` process, so the partial workspace is still archived. A slow call in flight never counts as idle,
  and unknown facts (no state yet, no CPU sample) never count as idle either;
- it counts install time (package-manager processes), and LLM time comes from the gateway call durations;
- it tails the harness stdout and publishes throttled `trajectory_step` events (2 per second; the adapter's
  optional `parse_line` turns lines into steps).

After exit the backend reads `State.OOMKilled`.

### Termination and outcome

The adapter classifies the termination from facts (`classify_termination`): wall clock by **elapsed time**,
never by exit code; idle and OOM from the supervisor; budget from the gateway. `budget_exhausted` is set when
the gateway reported it **or** when the `main`-class requests reached `run.max_llm_requests`.

| Termination | Generation stage | Final outcome | Usable | Truncated |
| --- | --- | --- | --- | --- |
| `completed` | evaluate | decided by evaluation | yes | no |
| `max_turns`, `budget_exhausted`, `wall_clock`, `idle_timeout` | evaluate the partial tree | decided by evaluation | yes | **yes** (kept in the intention-to-treat analysis) |
| `harness_error` | evaluate (note `harness_error`) | decided by evaluation | yes | no |
| `harness_crash` | retry once; a repeat is evaluated (note `harness_crash`) | decided by evaluation | yes | no |
| `llm_error`, `infra_interrupted` | retry as infrastructure | `infra_error` when retries run out | no | – |
| `oom_killed` | – | `excluded:resource_limit` | no | – |
| `cancelled` | – | `excluded:cancelled` | no | – |

**Empty output rule.** Without a collected file of origin `authored`/`modified`, not excluded and with
`loc > 0`, the outcome is `output_invalid` (`no_model_code`) and the run is not evaluated. A skeleton-only
workspace is never `tested`. An over-size workspace is `output_invalid` (`workspace_too_large`).

`usable = outcome ∈ {tested, build_fail, start_fail, output_invalid}`. Those outcomes are model behaviour and
analysed as data; `infra_error` and `excluded` never are. Infrastructure failures before the harness ran
(daemon, image build, gateway start) are retried and never become a model outcome.

### Attempts and retries

`GenerationRun.attempt` is the executor's 0-based requeue counter; attempt `n` (`RunAttempt.attempt`, the
evidence directory `a<n>/`, `GenerationArtifact.attempt`) runs while it is `n - 1`. An infrastructure retry
(`execution.transitions.infra_retry`) requeues the **same row with the same manifest and seed**, wipes the
attempt's results and `RunFile` rows, and keeps every `RunAttempt` row: the attempt history is never lost.
`retries.infra_retries` of the run's policy bounds it; when retries run out the run ends `failed` with
`outcome=infra_error`.

`POST /runs/{id}/retry/` is allowed **only** for `infra_error` (409 `not_infra` otherwise). Model-caused
outcomes are data and are never retried; a usable run is never superseded. Excluded cells are re-run by a
study top-up. Re-evaluating a run creates a new `RunEvaluation` and never changes which run is current.

## The collected sample (`agentic/collector.py`)

The collector reads `workspace.tar` (never `git ls-files`) and diffs it against the seed:

| Origin | Rule |
| --- | --- |
| `starter` | in the seed with the same bytes |
| `modified` | in the seed with different bytes |
| `authored` | not in the seed |
| `deleted` | in the seed, missing from the final tree (row kept with an empty blob) |
| `artifact` | generated output: `dist/` or `build/` at a frontend root, a `static/` directory the Dockerfile fills from a build stage, `__pycache__`/`*.pyc`, `.tmp/`/`tmp/`, logs, `*.db`/`*.sqlite*` under a data dir, coverage output. `node_modules/` is never collected |

`excluded` labels (first match wins): `non_source`, `lockfile`, `vendored`, `binary` (NUL in the first 8 KB),
`too_large` (> 1 MiB; lockfiles up to 8 MiB). Artifact rows carry `build_output` or `data`. Every row keeps
its blob, so builds see the whole tree. `loc` is `reports.services.loc.source_loc` (tests count 0) and
`is_test` is `is_test_path`. `run.tree_hash` covers the non-deleted, non-artifact, non-binary entries;
`run.workspace_blob` is the workspace tar in the blob store.

Scopes (`RunView`): `model` = `authored ∪ modified ∪ slot`, not excluded; `app` = the whole non-artifact tree
(the parent's rows overlaid for slot variants). Analysis always runs on the app scope; attribution to the
model is by origin.

Static checks (informational, never gating) go to `result.static`: the Python AST compile check, and esbuild
over the model-scope JS/TS files in the `frontend-validator` image (`images/frontend-validator`, no network).
A check that could not run says `not_measured`.

## Validity notes (`agentic/validity.py`)

`GenerationRun.validity = {termination, truncated, notes: [{code, level, detail}]}`. The generation stage
writes: the truncation kind (`wall_clock`, `idle_timeout`, `max_turns`, `budget_exhausted:<kind>`),
`harness_error`, `harness_crash`, `ended_on_llm_error`, `call_truncations:<n>` (calls cut at the output cap),
`served_model_differs` (against the manifest's frozen `served_model_name`, never the requested slug),
`response_model_differs`, `provider_mixed`, `translation_lossy`, `routing_partial`, `vendor_affinity`,
`unsupported_knob:<key>` and `call_cap_without_compaction`. Evaluation adds its own notes.

## Evidence

Everything an attempt produced is kept under `GENERATION_RUNS_ROOT/<run>/a<attempt>/`: the gateway's
`calls.jsonl`, `gateway_events.jsonl` and `state.json`, the harness's logs and artifacts, `changes.patch`,
`workspace.tar` and `trajectory.json`. `GET /runs/{id}/evidence/` zips every attempt.

## Per-call telemetry

Each `agent_call_%04d` artifact's `telemetry` (`services/call_telemetry.py` schema) is one normalized record
of the gateway call: `started_at`, `ended_at`, `duration_s`, `ttft_ms`, `tokens_per_s`, `status`, `provider`,
`served_model`, `response_model`, `finish_reason`, `system_fingerprint`, `request_class`, `front_protocol`,
`protocol_mode`, `serving_tier`, `tokens` (`prompt`, `completion`, `reasoning`, `cached`, `cache_write`),
`cost` (billed), `list_cost` and `translation_dropped`. An absent key means unknown, never 0. After the
generation stage the OpenRouter `/generation` stats are fetched for `gen-` calls
(`services/openrouter_stats.schedule_for_run`, on commit, off under tests).

## API (`/api/generation/runs/...`, `runs/api.py`)

| Method | Path | Response |
| --- | --- | --- |
| GET | `/runs/` | `?q=&status=&study=&condition=&kind=&outcome=&model=&usable=&beyond_plan=&page=&page_size=` → `{items, total, page, page_size}`; rows carry `task` (`slug@v`) and `task_title`; `q` matches task slug/title, model id or a run id prefix; `status` takes one status or a comma list (400 `invalid_filter` for an unknown one) |
| GET | `/runs/{id}/` | the run with identity, study/cell refs, attempts, validity, result, metrics, resolved lock, latest evaluation |
| GET | `/runs/{id}/manifest/` | `{manifest, manifest_hash, stimuli: [{role, template_ref, sha, text, sources: [{template_ref, owner_kind, owner_ref, role, char_start, char_end}]}]}` (`sources` empty when the render recorded no spans) |
| GET | `/runs/{id}/files/` | `?scope=model\|app\|slot&origin=` → `[{path, origin, size, loc, language, is_test, excluded, blob}]` |
| GET | `/runs/{id}/files/content/` | `?path=` → `text/plain` (declared as such in OpenAPI; 415 for binary files) |
| GET | `/runs/{id}/trajectory/` | `?attempt=` → `{attempt, trajectory, observed_inputs}` |
| GET | `/runs/{id}/calls/` | `?attempt=` → one row per LLM call |
| GET | `/runs/{id}/evaluations/` | every `RunEvaluation`, newest first |
| POST | `/runs/{id}/evaluate/` | `{eval_suite?, force_rebuild?}` → `{evaluation_id}` (queued in the eval lane) |
| POST | `/runs/{id}/retry/` | `{run_id, attempt}`; only for `infra_error` (409 `not_infra`) |
| POST | `/runs/{id}/cancel/` | the run |
| GET | `/runs/{id}/evidence/` | zip of the run's evidence |

Errors are `{"detail", "code"}`. Staff see every run; anyone else sees the runs they created and the runs of
their studies. Retry, evaluate and cancel reach the executor only after the request commits. The live stream
is the SSE channel `run:<id>` (`status`, `step`, `usage`, `trajectory_step`, `log`, `evaluation`).

## Configuration (`config/settings/components/generation.py`, WP-08 block)

| Setting | Default | Meaning |
| --- | --- | --- |
| `GENERATION_SANDBOX_BACKEND` | `docker` | The sandbox backend (the apps daemon) |
| `AGENT_SUPERVISOR_TICK_S` | 2 | Supervisor poll interval |
| `AGENT_IDLE_CPU_FRACTION`, `AGENT_IDLE_CPU_WINDOW_S` | 0.05, 10 | Idle means below this share of one CPU over this window |
| `AGENT_SANDBOX_GRACE_S` | 60 | Backstop kill after wall clock + 15 s |
| `AGENT_GATEWAY_MEM_LIMIT` | `256m` | Gateway sidecar memory |
| `GENERATION_FRONTEND_VALIDATOR` | on | Run the esbuild frontend validator on collected samples |

Budgets, timeouts, sandbox size and network policy are **not** settings: they are knobs of the run's
ResourcePolicy (budget preset), frozen in its manifest. Only machine limits (`AGENT_SANDBOX_MEM_LIMIT_MAX`,
`AGENT_SANDBOX_NANO_CPUS_MAX`, `AGENT_MAX_CONCURRENT_RUNS`) and harness versions are deployment settings.
