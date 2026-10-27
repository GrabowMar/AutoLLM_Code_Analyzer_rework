# Generation process

How a sample app gets made: in the primary **agent** mode a coding-agent harness builds the app in a sandbox and the platform grades it with a hidden conformance suite; the secondary template modes are also covered, along with what each run records so results stay comparable.

## Modes

`GenerationJob.mode` (`backend/generation/models.py`) selects the strategy. The Sample Generator (`/sample-generator?mode=agent|scaffolding|custom`) has one composer per live mode.

| Mode | UI label | What it does | Use it for |
| --- | --- | --- | --- |
| `agent` (default) | Agent | A coding-agent **harness** (OpenCode, mini-swe-agent or Aider) works on the stack skeleton inside an isolated sandbox until it finishes or its budget runs out. The platform then builds and grades the result (`services/agent/`, `services/evaluation/`) | (model × harness) comparisons on realistic, multi-file, self-tested apps |
| `scaffolding` | Template scaffolding | A generation profile renders backend/frontend prompts, and two LLM calls fill a known skeleton | One-shot "how well does a model write code from a prompt" comparisons |
| `custom` | Single prompt | Your system and user prompt go straight to one model. Nothing is added or graded | Ad-hoc probing |
| `copilot` | none | *Retired.* The in-process aider loop. Kept only so historical jobs still load, and `POST /jobs/copilot/` answers 410 `mode_retired`. Aider itself lives on as a sandboxed agent harness | none |

Every mode runs on the **executor**, not in the web process (see [Job lifecycle](#job-lifecycle-all-modes) and [Execution engine](/docs/EXECUTION)).

## Agent mode

### What runs where

```
executor ──(apps daemon, mTLS)──▶ apps-dind
   run:       llmlab-gateway-<job>-a<n>   bridge + job net   holds the real API key
              llmlab-agent-<job>-a<n>     job net only       harness + workspace, no key
   evaluate:  llmlab-eval-app-<job>       eval net only      the built sample, no egress
              llmlab-eval-tester-<job>    eval net only      hidden conformance suite
```

`-a<n>` is the attempt number. An infrastructure retry gets fresh containers and networks, so the leftovers of an earlier attempt never collide with it.

- **Per-job sandbox** (`services/agent/sandbox.py`). Each job gets its own internal Docker network. The agent container is attached only to that network, so its only route is the gateway. It runs unprivileged (all caps dropped, `no-new-privileges`, 1024 PIDs), and its memory and CPU caps are frozen into the snapshot. Everything runs as a nested container on apps-dind, never on the host daemon. Resources are labelled with the job id and removed in `finally`. Leftovers of a crashed process are reaped by the executor's reconciler and by *Purge Orphan Containers* in System → Maintenance.
- **Images** (`backend/generation/images/`, `services/agent/images.py`). Every harness image builds `FROM agent-base`, which is `node:24.22.1-trixie-slim` plus Debian's Python 3 (3.13 on trixie), git and ripgrep, so the agent works on the same runtimes the builtin stacks build with (Node 24, Python 3.13). The tools an agent finds are therefore byte-identical across harnesses, and only the harness layer differs. Python harnesses install a fully locked dependency set (`requirements.lock`) into a private venv that the agent's own `python3`/`pip` never sees. Aider's venv runs on its own pinned CPython 3.12, copied from `python:3.12.14-slim-trixie`, because no `aider-chat` release up to 0.86.2 supports Python 3.13. Tags (`llmlab/<dir>:<hash>`) hash the image directory and its build args, and a harness tag covers the base tag. The snapshot records the tags at job creation. A job refuses to run if an image definition has changed since then.
- **LLM gateway** (`images/llm-gateway/gateway.py`, stdlib only). The harness gets a dummy key and the gateway as its OpenAI/OpenRouter base URL. The gateway:
  - pins the model,
  - applies the job's sampling policy (below),
  - forces the job's seed, provider routing and per-call output cap,
  - retries transient upstream failures itself (408/429/5xx and connection errors, with backoff),
  - counts only successful calls toward the request budget,
  - answers a non-retryable 400 `budget_exhausted` once the budget is spent (a 400 rather than a 429, because harnesses retry 429s),
  - logs every exchange.
- **Egress.** Under network policy `packages` (the default) the gateway also tunnels HTTPS `CONNECT` to `pypi.org`, `files.pythonhosted.org` and `registry.npmjs.org`. Everything else is refused and logged. Under `none`, nothing is reachable.
- **Local models.** For a model on a local endpoint (Ollama or an OpenAI-compatible server), the gateway is the `llm-gateway-local` overlay on the dedicated `llmlab-gw-egress` network. Its only upstream is the executor's internal LLM proxy (`backend/inference/gateway_wiring.py`). Such runs need apps-dind (`LLM_LOCAL_AGENT_RUNS`, on when `APPS_DOCKER_HOST` is set), and they fail fast without it. See [Local models](/docs/LOCAL_MODELS).

### Harnesses (`services/agent/harnesses/`)

A harness is the agent program that drives the model. Benchmarks compare (harness, model) pairs:

- **Within a harness**, every model gets byte-identical treatment. Anything a harness would normally switch per model family is pinned: prompts, edit formats, temperatures.
- **Across harnesses**, each keeps its own native prompts, frozen, because the harness's design is what is being compared. All of them get the same sandbox image, brief, gateway policy and budgets.

| Harness | Paradigm | Tool calling | What is pinned beyond the release |
| --- | --- | --- | --- |
| `opencode` ([OpenCode](https://opencode.ai) 1.18.35) | Full IDE-style agent with read/edit/write/grep/glob/bash/todo/task tools | required | Its own model-agnostic default prompt, captured verbatim in `data/agent/harness_prompts/`, for the `build` agent **and** the `general` subagent (OpenCode otherwise picks a per-family prompt for both). Web fetch/search and skills are off, as are config/AGENTS.md discovery, plugins and auto-update. The session title is passed in, so there is no hidden title call, and `doom_loop` is explicitly denied |
| `mini-swe-agent` ([mini-swe-agent](https://mini-swe-agent.com) 2.4.6) | Minimal research baseline: one `bash` tool, a fresh subshell per command | required | Native `mini.yaml` templates, `yolo`, `step_limit` set to the request budget, and the cost limit off (the gateway owns cost) |
| `aider` ([Aider](https://aider.chat) 0.86.2) | Edit-format pair programmer: one headless `--message` turn plus its native lint-fix reflection | not needed | Edit format `diff` for every model by default (Aider otherwise picks it per model), with the weak/editor model set to the run's model. Every model gets the same model-metadata file (context window, output cap), because Aider's own database does not know gateway-routed ids. Repo map is 1024 tokens by default. No commits or shell-command suggestions, and all scaffold sources are added to the chat |

**Harness options.** A few pinned values can be changed, but only through a harness preset (below). An option is offered only where the pinned source was checked to apply it identically to every model family; each adapter's docstring records the check.

| Harness | Option | Values (default) |
| --- | --- | --- |
| `aider` | `edit_format` | `diff` \| `whole` \| `udiff` \| `diff-fenced` (`diff`) |
| `aider` | `map_tokens` | 0–8192 (1024); 0 disables the repo map |
| `mini-swe-agent` | `command_timeout_seconds` | 10–600 (30): mini's `environment.timeout` |
| `opencode` | none | OpenCode picks its editing tools by model id, so tool toggles would be model-specific |

Options are rendered only into the arguments and config a harness already emits, and only when they differ from the default, so a default harness section is byte-identical to one from before options existed. `GET /api/generation/agent/config/` lists every harness with its `option_schema` and `default_options`.

Every harness reads the brief from a file or stdin, never from argv. Agents restart their dev server with "kill every process mentioning `app.py`", and a brief in argv once made OpenCode kill itself. Harness artifacts (Aider's `.aider*` caches, histories) never enter the sample.

### The brief and the evaluation contract

The brief (`data/agent/brief.md.jinja2`, `BRIEF_VERSION` 4) is the only task-specific text an agent receives. It is identical for every harness and model:

- an **autonomy** statement: nobody will answer questions, and the agent finishes with a summary,
- the **app spec**, verbatim: requirements, the data model (every model, under "Data models", when the spec lists several), and every endpoint with its request/response shapes, plus its access level (`Access: anyone, without signing in` / `signed-in users` / `admin accounts only`), query parameters and success status when the spec gives them. A task `description` sent along with an app spec appears after the spec's own description as an **Additional context** section. Without an app spec the description *is* the task: a free-form task, which has no conformance suite and is not graded,
- an **accounts contract** (`services/evaluation/contract.py`). Specs say *which* writes need a signed-in user but almost never *how* to sign in, so agents used to invent their own schemes and write endpoints could not be graded. Unless the spec defines its own auth endpoints, the app must expose `POST /api/auth/register` and `POST /api/auth/login` (returning `{"token"}`) and accept `Authorization: Bearer`. Endpoints reserved for signed-in users answer 401 to anonymous requests. Admin endpoints live under `/api/admin/` and use an admin account whose password comes from `ADMIN_PASSWORD`. The evaluation sets it to a fresh random value, so a hard-coded credential fails,
- the **runtime contract**: entry point and start command, port, `GET /api/health`, the SPA served for non-API paths, SQLite in an initially empty `./data/`, pinned dependencies, and platform-owned build files. Builtin stacks take this text from `data/agent/stacks.yaml`, whose summaries name the runtimes (for example "React 19 + React Router 8 + Vite 8 + Tailwind CSS 4, built with Node 24"). A user-authored stack states the command its generated Dockerfile runs (`python <file>` or `uvicorn <module>:app …`, from its `server_kind`),
- a notice that the result is **checked automatically** against all of the above (the checks themselves stay hidden),
- the **budget**, with the request budget stated as the primary limit.

There are no length targets and no style coaching.

### One run, step by step (`services/agent/runner.py`)

1. **Snapshot at job creation** (`services/agent/snapshot.py`). Everything is rendered and frozen into `resolved_bundle`: brief, contract, harness section, image tags, sampling and gateway policy, routing, budget, sandbox caps, the stack version (`{slug, version, content_hash}`), the app spec (`app_requirement`, with the spec row's `version` and `content_hash`) and, for local models, the endpoint section. A run never re-reads a template. Snapshots older than `AGENT_SNAPSHOT_SCHEMA_VERSION` (2) are refused rather than replayed approximately; *Retry* re-creates them under the current procedure.
2. **Queueing.** The job joins the executor's **sandbox lane** and is admitted in its frozen `sequence` order. The order an experiment or batch shuffles its jobs into is therefore the order they run in. Concurrency is `ExecutorSettings.sandbox_slots`, which starts at `AGENT_MAX_CONCURRENT_RUNS`, can be edited by staff at runtime, and is clamped by apps-dind memory.
3. **Seeding.** The stack skeleton is copied into `/workspace` and committed as `scaffold`. Dependency/build directories and `data/` go into `.git/info/exclude`.
4. **The agent run.** `/out/harness.sh` runs under `timeout`. Once a minute the run checks whether it still owns the job (not cancelled, requeued or reclaimed) and touches the job's `updated_at`. Lease renewal is the executor heartbeat's job.
5. **Collection.** The run records the patch, diffstat and name-status, and classifies the workspace against the seed (`added` / `modified` / `scaffold`). Binaries, oversized files and harness artifacts are dropped, and tampering with platform-owned files is flagged.
6. **Evidence.** Everything is written to `GENERATION_RUNS_ROOT/<job>/`, or `<job>/a<attempt>/` for a retried attempt, with sha256s recorded in the job row. The files are `llm_calls.jsonl.gz` (the authoritative log of every request and response, with the policy diff per call), `gateway_events.jsonl`, `harness_events.jsonl.gz`, `harness.log`, `harness_report.tar.gz` (the harness's own trajectory or chat log), `changes.patch`, `name_status.txt`, `workspace.tar.gz` and `container.log`. Each call is also stored as a compact `GenerationArtifact` (`agent_call_<nnnn>`). Download a file at `GET /api/generation/jobs/<id>/run-record/<name>`. The request is refused (409) if the file no longer matches its sha.
7. **Evaluation.** With `AGENT_AUTO_EVALUATE` the sandbox slot is released and the job waits (`phase=eval_queued`) for the executor's eval pool (`ExecutorSettings.eval_slots`), which grades it (`phase=evaluating`, below).

### Platform evaluation (hidden conformance suite)

Every sample is graded the same way by the platform, never by its own report (`services/evaluation/runner.py`):

1. **Build:** `prepare_build_dir(job)` plus the stack's own Dockerfile on apps-dind. The build is retried once, and only when the log shows a registry/network signature. A repeated infra failure is `infra_error`, not a failed sample.
2. **Run:** the image runs on an internal network with no egress, under memory/CPU caps and with a random `ADMIN_PASSWORD`.
3. **Grade:** `images/conformance-tester/tester.py` (stdlib, `VERSION` 3, in its own container on the same network) checks only what the brief states, in a fixed order:
   - runtime: health,
   - frontend: `/` and a deep link serve the SPA,
   - auth: register, login, admin login, anonymous writes rejected,
   - api/admin: every contract endpoint answers 2xx with the documented fields.

   Ids come from the app's own responses. Each destructive or state-changing check gets a freshly created resource, so no check depends on another's side effects. Request bodies are synthesised deterministically from the spec's samples.
4. **Record:**
   - `result_data.evaluation`: status, summary, every check, build log tail and healing report, the dependencies pip actually resolved, the app log tail, and the grader identity (`evaluator_version`, `tester_version`, `tester_sha256`, `contract_version`),
   - `metrics.functional`: `source: "conformance"`, `status`, `passed`, `pass_rate` (null for `infra_error`), `health_ok`, `endpoints_ok` / `endpoints_checked`, `deployed`, `healing_mode` / `healing_changed`, and the grader identity. A later container-start smoke probe writes `metrics.smoke` and never replaces a conformance verdict.

| Status | Meaning |
| --- | --- |
| `passed` / `failed` | The suite ran (`deployed: true`) |
| `build_failed` / `start_failed` | A sample outcome: it does not build, or never answers its health check (`deployed: false`, pass rate 0) |
| `infra_error` | The platform could not evaluate it. Exclude it and re-evaluate |
| `not_applicable` | No app spec to grade against |

`POST /api/generation/jobs/<id>/evaluate/` re-grades any completed job with an app spec through the same eval pool, for example after a grader change, after an `infra_error`, or for an ad-hoc template-mode sample. `result_data.evaluation.contract_stated_in_prompt` says whether the sample's prompt stated the contract it is graded against: true for agent jobs (the snapshot freezes the contract) and for scaffolding jobs whose frozen backend templates use the `contract` variable (`prompt_renderer.templates_state_contract`), which the current system prompts do. Scaffolding jobs from before those prompts, and user prompts that leave `contract` out, were never told about the accounts endpoints (`false`), so do not compare their scores with ones that were.

### Outcome semantics

A run that made at least one successful LLM call is a **sample**: the job is `completed`, and `result_data.agent.termination` says how it ended.

| termination | Meaning |
| --- | --- |
| `completed` | The agent stopped by itself |
| `budget_exhausted` | A budget was hit; `agent.budget_exhausted` names which one (`max_requests`, `max_total_tokens`, `max_cost_usd`, `max_attempts`) |
| `timeout` | Wall-clock limit hit |
| `resource_limit` | Killed at the sandbox memory cap |
| `harness_killed` | A process inside the sandbox (the agent's own commands) signalled the harness |
| `harness_error` | The harness exited non-zero for another reason |

Do not drop budget stops or timeouts from an analysis: that biases comparisons toward models that finish quickly.

A run with **no** successful LLM call ends in one of two ways. A transport or platform failure (upstream 502/503/504, proxy or endpoint unavailable, apps daemon down) is **retried** by the executor as a new attempt with the same snapshot and seed: up to 2 times for ad-hoc jobs, `Experiment.infra_retries` times for experiments, with exponential backoff. Any other failure (a bad key, no provider matching the routing) fails the job, with the upstream error and a remediation hint in `result_data`.

**Validity notes** (`result_data.agent.validity.notes`, `{code, severity, detail}`) list what an analysis must know before pooling a sample. `valid` is false only when there is an `exclude` note.

| Severity | Codes |
| --- | --- |
| `exclude`: not a valid sample of the condition | `evaluation_infra_error`, `infra_interrupted` (a local-endpoint run kept being interrupted by infrastructure errors) |
| `warn`: valid, but not model-neutral or not as configured | `budget:max_cost_usd`, `budget:max_total_tokens`, `budget:max_attempts`, `wall_clock`, `resource_limit`, `harness_error`, `params_not_applied` (a configured param the model cannot take), `served_model_differs`, `output_truncated` (responses cut off at the per-call output cap; single-turn harnesses such as Aider are most exposed), `not_evaluated`, `infra_retried` (attempt 2 or later), `contended` (more than 10% of the wall clock spent waiting for endpoint request slots) |
| `info`: context | `budget:max_requests` (the model-neutral limit), `params_not_applied` when only the seed was dropped, `harness_killed`, `llm_errors`, `protected_files_modified`, `egress_denied`, `relay_interruptions` |

### Building and running the result

Agent results set `result_data.materialize = "verbatim"`. The runtime writes the collected files over the pinned skeleton exactly as the agent left them: no entry renaming, no Flask patching, no dependency inference. Build healing never applies to them. The runtime does restore the platform-owned `Dockerfile`, `.dockerignore` and `docker-compose.yml`. Builtin stack Dockerfiles copy the whole backend tree and honour `package-lock.json`.

## Research design: what keeps agent samples comparable

| Threat | Control |
| --- | --- |
| Harness switches its system prompt per model family (OpenCode does this for the build agent *and* the `general` subagent) | OpenCode's own model-agnostic default prompt, captured verbatim per release and pinned for both. Aider's edit format pinned for every model (`diff` unless a harness preset picks another) |
| Harness or model-family sampling defaults (OpenCode sets per-family temperatures, Aider sends 0, others send nothing) | Sampling policy `provider_default` (the default): the gateway strips every sampling/reasoning knob the harness sends. Only params the run configures explicitly reach the model, and they are forced on every call. `harness_native` is available and recorded |
| One harness sends no `max_tokens` and gets a provider default, another sends its own | The gateway forces one per-call output cap, `min(max_tokens, model limit)` (default 32000), on every call |
| Params a model can't take are silently ignored (e.g. `seed`/`temperature` on some closed models) | Params are forwarded only if OpenRouter lists them for that model; otherwise they are recorded in `gateway.dropped_params` and in the experiment comparability report. A model with no supported-parameter list in the catalog gets its params forwarded unverified (`llm.supported_params_source: "unknown"`, preview warning `support_unknown`). The default routing's `require_parameters` then makes a provider that cannot honour one fail the run rather than ignore it. Local models use the endpoint's static parameter table (`endpoint_static`) |
| Model "X" served by different providers at fp4…bf16, some ignoring `seed` | Default routing (`AGENT_DEFAULT_PROVIDER_ROUTING`): `require_parameters`, quantization fp8 or better (or `unknown`, for closed models), fallbacks allowed. The serving provider of every call is recorded (`agent.providers`) |
| Model alias repointed mid-study | The dated canonical slug is recorded per job and the served model id per call. Mismatches are flagged |
| Hidden auxiliary calls (titles, "small model", weak model) | The title is passed in, and the small/weak/editor model is the run's model. The gateway pins the model on every call and counts override attempts |
| A flaky provider costs a model steps, and harnesses retry differently | The gateway retries transient failures for every harness. Only successful calls count toward `max_llm_requests`, and attempts are capped separately |
| Budgets that favour some models (USD favours cheap ones, tokens terse ones, wall clock fast providers) | The **request budget** binds by default (100). USD (5), tokens (30M) and wall clock (60 min) are safety nets, and a run stopped by one carries a `warn` note |
| Time confounds (provider load, routing and rate limits drift over hours) | Randomised complete blocks: repeat by repeat, with cells shuffled within a repeat by a seeded RNG. The executor keeps dispatch order equal to run order, and each job records its run order and queue time (`agent.schedule`) |
| Seeds confounded with conditions | Common random numbers: every condition of an (app, repeat) cell shares one seed |
| Unequal sandboxes across harnesses | One base image with an exact pinned release. Harness packages are locked and isolated in a private venv, and memory/CPU caps are frozen per job |
| Dependency drift inside a harness (an unpinned install once resolved a LiteLLM that would not import) | A fully locked `requirements.lock` per Python harness. Content-addressed image tags are recorded per job, and a changed definition makes old jobs refuse to run |
| Agent kills its own harness while restarting its server | The brief goes in via file/stdin only, and `harness.sh` is exec'd under `timeout`. Remaining self-kills are recorded as `harness_killed` |
| The agent's requests to its own app captured by the egress proxy | Only `HTTPS_PROXY` is set, and loopback is in `NO_PROXY` |
| Agent looks things up online, or the run depends on internet state | No web tools. Egress is limited to package registries (or none) |
| Agent reconfigures its harness or reads stray instructions | Config/AGENTS.md/CLAUDE.md discovery, plugins and external skills are off |
| Agent games its own evaluation | A hidden, platform-run conformance suite. Platform-owned build files are restored, the admin password is random, and the app is evaluated with no egress |
| Ungradable or inconsistent requirements (no auth API in the specs) | A shared, versioned accounts/runtime contract, stated in every brief (and in the current scaffolding backend prompts) and checked by the suite |
| Library edits reaching a running study (spec rows are updated in place, builtin stacks gain versions) | Snapshots embed the spec's `version` and `content_hash` and the stack's `{slug, version, content_hash}`. The spec hash enters `run_fingerprint`, and retries and digest re-freezes keep the original's spec content, stack version and brief ([Cloning a finished job](#job-lifecycle-all-modes)) |
| Grading artefacts (one check's side effects failing another) | Fresh resources for destructive checks, a deterministic order and request bodies, and a grader that is versioned and hashed in every result |
| Infrastructure failures counted as model failures | Transport failures with no successful call are retried under the same snapshot and seed, and evaluation `infra_error`s are excluded, not scored |
| Prompt coaching that favours some models (length targets, style rules) | The brief states only the spec and the contract |
| Key exfiltration into generated code | The agent never holds the real key |

Known residual variation, recorded but not controlled:

- Per-model behaviour inside the pinned harness releases, which no setting removes (this is the limit of "byte-identical treatment within a harness"):
  - OpenCode appends an environment block to its system prompt that names the model and today's date, and gives GPT-family model ids an `apply_patch` tool instead of `edit`/`write`.
  - Aider applies generic settings by model-name substring (reminder placement, lazy/overeager prompt additions, `use_temperature`). Every model reaches it as `openai/<vendor>/<model>`, so those settings still apply.
  - Capabilities differ by design: a param a model does not take is dropped (and recorded), and the output cap and context window follow the model. The run preview lists this per model before launch.
- Providers within the routing policy may still differ in serving stack, so stratify by `agent.providers`.
- Debian packages in `agent-base` resolve when the image is built. The built image id is recorded per run.
- Package registries change over time under policy `packages`. Pinned dependency files limit this, and `resolved_python_deps` records what the evaluated build actually installed. Policy `none` removes the variation.
- mini-swe-agent's native 30 s per-command timeout and Aider's single-turn headless mode are harness properties and part of what is compared. An Aider turn that writes the whole app in one response can hit the per-call output cap and apply nothing. This has been observed with DeepSeek V4.1 Flash and is flagged `output_truncated`.
- Without a model-metadata file, Aider refreshes its model database from GitHub at start-up (blocked, and recorded as `egress_denied`). With the metadata file it does not.
- Request budgets are not compute-equal across harnesses: Aider needs few calls, mini-swe-agent many. For cross-harness comparisons, report cost and tokens alongside outcomes.

Agent runs are not deterministic even with a seed (tool timing, provider batching), so **repeats are the unit of analysis**. Use `trials` (or experiment `repeats`) of 3 or more, with a `base_seed` so that the seed matrix and the run order are reproducible.

### Fairness rules

These hold for every agent batch and experiment. The code enforces them and the tests pin them:

1. **Within a harness**, every model gets a byte-identical harness section except for the derived per-model fields (model slug, context window, output cap, step limit). Harness presets carry no per-model keys.
2. **Default runs never change identity.** With the default presets, `prompt_hash`, `run_fingerprint` and the `agent`, `gateway`, `harness` and brief values equal what they were before presets existed (`backend/generation/tests/test_default_run_golden.py`). The golden values were recaptured once, on purpose, for the generation-library modernization (`BRIEF_VERSION` 4, the agent-base move to Node 24 / Python 3.13, the modernized flask-react skeleton and the spec `content_hash` in the fingerprint). The brief, `prompt_hash` and `run_fingerprint` moved; the `agent`, `gateway` and `harness` sections did not. Runs from before it form separate conditions. The local-endpoint additions are absent for OpenRouter models, so those snapshots are byte-identical too.
3. **Batch- or experiment-wide:** the stack, the budget preset (limits and network policy) and the sampling preset (sampling params and sampling policy). What may vary per condition: the model and the harness preset, and in experiments also `param_overrides` and the profile, which comparability always reports.
4. The brief stays harness- and model-agnostic.
5. Under `provider_default`, only explicitly configured params reach the model. Unsupported ones are dropped and recorded, and the preview shows them per model.
6. Not tunable per run: harness versions, sandbox resources, the egress allowlist, protected files, the evaluator, and the default provider routing. Routing changes only through an explicit inline `llm_params.provider`, never through a preset.
7. Snapshots are immutable. Presets are versioned and immutable in content, and jobs record which presets they used.
8. Preset values are copied into the job's own fields at creation (`llm_params`, `agent_config`, `harness_options`), so retry, replay and rerun reproduce the configuration without reading the preset again.

### Run presets

A run preset (`RunPreset`, `services/presets.py`) is a named, versioned piece of agent-run configuration. There are three kinds, and each is normalized before it is stored or hashed:

| Kind | Config | Notes |
| --- | --- | --- |
| `harness` | `{"harness": <name>, "options": {<non-default options>}}` | Options at their defaults are dropped, so `{}` and `{"edit_format": "diff"}` are the same content. A user preset with every option at its default is refused: use `<harness>-default` |
| `sampling` | `{"llm_params": {...}, "sampling_policy"?: ...}` | Model-agnostic sampling settings (temperature, top_p, reasoning effort, output cap, …) applied identically to every model. `provider`, `stop` and `response_format` are refused, because routing and output formatting are not sampling properties |
| `budget` | a sparse subset of `wall_clock_seconds`, `max_llm_requests`, `max_total_tokens`, `max_cost_usd`, `network_policy` | `{}` means the platform defaults (the `AGENT_*` settings) |

- **Versions.** A content change creates version + 1; a name or description change does not. `content_hash` is the sha256 of the canonical `{kind, config}`, with integral floats hashed as ints so that `0` and `0.0` agree. Delete archives every version, and jobs keep their references (`PROTECT`).
- **Slugs** are global per kind, whoever owns them, and must match `^[a-z0-9][a-z0-9-]{1,80}$`. Slugs ending in `-default` and every system preset slug are reserved. A taken slug gets a plain "slug already in use" error that never names the owner.
- **System presets** live in `data/presets/<kind>-<slug>.yaml`: `harness/opencode-default`, `harness/mini-swe-agent-default`, `harness/aider-default`, `sampling/provider-default` and `budget/platform-default`. They are seeded by `seed_all`, idempotently by content hash (a changed file becomes a new version). A request-time guard (`ensure_system_presets`) recreates missing defaults, so the default run path never depends on seeding having worked.
- **Stored presets are re-validated** whenever a run uses them. A preset that a harness release no longer accepts is an `invalid_preset` error in the preview and a 400 on create, never a crash.
- **Starter presets.** The `agent-benchmark-starter` package ([Template packages](/docs/TEMPLATE_PACKAGES)) adds `aider-whole-file`, `aider-no-repo-map`, `mini-long-commands`, `temperature-zero`, `reasoning-high`, `smoke` (30 requests, 60 min, $3) and `extended` (250 requests, 180 min, $25). Their USD and wall-clock caps stay generous safety nets, so the request budget still binds.
- In the UI, presets live in Library → Presets (Harness / Sampling / Budget), and the agent composer can save its current settings as a new preset.

How a batch applies them, copied into the job's fields at creation (later layers win):

- `job.agent_config` = budget preset → the sampling preset's `sampling_policy` → inline `agent_config` (+ the harness),
- `job.llm_params` = legacy `max_tokens` → profile `llm_config` → sampling preset `llm_params` → inline `llm_params`,
- `job.harness_options` = the harness preset's options (never part of `agent_config` or of the snapshot's `agent` section).

The `harness_preset`, `sampling_preset` and `budget_preset` foreign keys on the job, and `snapshot.presets` (`{id, slug, version, content_hash, name}` per kind), are provenance only. Leaving a preset out means the system default for its kind.

### Identity keys

- `prompt_hash`: the brief sha plus the contract version. This is the *task* identity, shared across harnesses, models and seeds, and the grouping key for comparisons. The brief states the budget, so changing the budget changes it.
- `bundle_key` (`method_key` in the snapshot): `[profile@version/]agent:<harness>@<version>`, plus a content-addressed suffix for each part that departs from the defaults:
  - `+opts:<sha8>`: non-default harness options,
  - `+llm:<sha8>`: explicit LLM params other than `max_tokens` and `seed`, or a non-default sampling policy,
  - `+budget:<sha8>`: an effective budget (limits or network policy) that differs from the platform defaults.

  This is the *method* identity and the condition slicing key. It hashes content rather than preset names, so renaming a preset (or importing it under another slug) never splits a slice, and a default run keeps the key it always had.
- `run_fingerprint`: prompt hash + model + gateway policy + harness section + image tags + agent config + stack slug and content hash + the spec's `content_hash` (`app_content_hash`; absent from snapshots frozen before specs carried one, so their fingerprints stay stable), plus, for local models, `llm_source` (which weights on which server flavour). Equal fingerprints mean exact replays.
- `config_fingerprint`: the same inputs minus the seed. This is the *condition* identity that replicates (trials) share, used to group trials in batch views. A rerun keeps the original's frozen gateway policy (only the forwarded seed changes, never what today's catalog says the model supports), so it has the original's `config_fingerprint`.
- Numbers enter these keys in their declared type, whichever layer set them. Float sampling params (`temperature`, `top_p`, `min_p`, the penalties) and `max_cost_usd` are frozen as floats, and integer limits as ints, so a preset stored as `temperature: 0` and an inline `0.0` fingerprint alike. The keys hash plain JSON on purpose: folding `5.0` into `5` would change every default run's fingerprint.

When you change something, version it:

- `BRIEF_VERSION` (`services/agent/brief.py`, currently 4) for the brief. v3 added the "Additional context" section and user-stack start commands. v4 lists every model of a multi-model spec under "Data models", states an endpoint's access level, query parameters and success status when the spec gives them, and names the current runtimes in the builtin stack summaries, and prints its JSON blocks without HTML escaping (keys stay sorted, so a brief does not depend on how the spec was loaded); briefs of single-model specs without the new endpoint fields differ from v3 only in those summaries.
- `CONTRACT_VERSION` (`services/evaluation/contract.py`, 1) for the contract.
- The tester's `VERSION` (3) or `EVALUATOR_VERSION` (2) for grading. Build healing (`runtime/services/scaffolding.py`: patches, inferred requirements, placeholder entry files) is part of the grading procedure, so a change to it bumps `EVALUATOR_VERSION` too; 2 is the generation-library modernization's healing.
- `SANDBOX_REVISION` (`harnesses/base.py`, 4) when the sandbox environment changes without an image change.
- To move OpenCode to a new release, first capture its default prompt into `data/agent/harness_prompts/opencode-<version>-default.md`. Jobs refuse to snapshot otherwise.

### Configuration

| Setting | Default | |
| --- | --- | --- |
| `AGENT_DEFAULT_HARNESS` | `opencode` | |
| `GENERATION_DEFAULT_STACK` | `flask-react` | The stack used when neither the request nor a profile names one (agent and scaffolding) |
| `AGENT_OPENCODE_VERSION` / `AGENT_MINI_SWE_AGENT_VERSION` / `AGENT_AIDER_VERSION` | `1.18.35` / `2.4.6` / `0.86.2` | Releases baked into the images |
| `AGENT_MAX_LLM_REQUESTS` | 100 | The binding budget (successful calls) |
| `AGENT_WALL_CLOCK_SECONDS` | 3600 | Safety net |
| `AGENT_MAX_TOTAL_TOKENS` | 30,000,000 | Safety net |
| `AGENT_MAX_COST_USD` | 5.0 | Safety net |
| `AGENT_SAMPLING_POLICY` | `provider_default` | Or `harness_native` |
| `AGENT_NETWORK_POLICY` | `packages` | Or `none` |
| `AGENT_AUTO_EVALUATE` | true | Grade every sample right after its run |
| `AGENT_MAX_CONCURRENT_RUNS` | 1 | *Initial* `ExecutorSettings.sandbox_slots`, editable at runtime by staff; not frozen into snapshots |
| `AGENT_SANDBOX_MEM_LIMIT` / `AGENT_SANDBOX_NANO_CPUS` | `3g` / 2 CPUs | Frozen into snapshots. Each sandbox must fit apps-dind's own cap (`APPS_DIND_MEM_LIMIT`), so raise both together |
| `AGENT_DEFAULT_PROVIDER_ROUTING` | `require_parameters`, quantizations fp8/fp16/bf16/fp32/unknown, fallbacks on | Used when a run sets no explicit `provider` |
| `LLM_LOCAL_AGENT_RUNS` | on when `APPS_DOCKER_HOST` is set | Allow agent runs on local-endpoint models |
| `GENERATION_RUNS_ROOT` | `MEDIA_ROOT/generation_runs` | Run evidence |

Executor tuning (`EXECUTOR_*`, eval and LLM slots) is covered in [Execution engine](/docs/EXECUTION).

### API

All paths are under `/api/generation` ([API reference](/docs/api-reference)).

- `POST /jobs/agent/` takes `{stack_slug?, app_requirement_ids, description, model_ids, harness_preset_ids, sampling_preset_id?, budget_preset_id?, llm_params?, agent_config?, max_tokens?, profile_id?, trials (1–10), base_seed?}`. It creates (app specs, or one free-form task) × models × harness presets × trials jobs in one `GenerationBatch`. With a `base_seed`, every (task, trial) cell shares one seed across all models and harness presets (`batch_cell_seed`). Jobs are enqueued trial by trial, in a seeded random order within each trial; the order seed is `base_seed`, or a random seed when none is given.
  - The stack is `stack_slug`, else the profile's stack, else `GENERATION_DEFAULT_STACK`. Whatever its source, it must exist and be usable by the requester.
  - Without presets or harnesses, the default harness's system preset runs. The legacy `harnesses: [<name>]` maps each name to that harness's system default preset. It cannot be combined with `harness_preset_ids`, and neither can `agent_config.harness`.
  - Any error-level problem refuses the whole batch with `400 {detail, code, errors: [...]}` before anything is written.
- `POST /jobs/agent/preview/` takes the same body and writes nothing. It returns:
  - the run count, the effective agent config and LLM params with the source of each value, and every harness preset with its effective options,
  - a per-model table: tool support, context window and its source, output cap, forced and dropped params, and whether the seed is forwarded,
  - the maximum cost (budget cap × runs),
  - issues grouped by severity:
    - errors: `no_tasks`, `no_models`, `models_without_tools`, `missing_api_key`, `duplicate_harness_config`, `invalid_preset`, `reasoning_exceeds_output_cap`, `resolution_failed`, and for local models `endpoint_disabled`, `endpoint_forbidden` and `model_unavailable`,
    - warnings: `params_dropped` (the seed never counts), `support_unknown`, `harness_native_sampling`, `context_window_fallback`, `cost_cap_may_bind`, `wall_clock_may_bind`, `whole_file_small_output`, `large_batch`, `ungraded_task`,
    - local-model warnings: `endpoint_offline`, `model_not_served`, `foreign_endpoint`,
    - infos: `non_default_harness_options`, `budget_changes_brief`, `single_trial`, `random_seeds`.
- `GET /agent/config/` returns the defaults, limits, network and sampling policies, the default provider routing, the default stack and output cap, and every harness with its option schema. `GET /agent/harnesses/` lists the harnesses.
- Presets: `GET /presets/?kind=&include_archived=` (the latest version per slug, system and your own), `POST /presets/validate/`, `POST /presets/`, `GET /presets/<id>/` (any visible version), `GET /presets/<kind>/<slug>/versions/`, `PUT /presets/<kind>/<slug>/` (a content change creates a new version) and `DELETE /presets/<kind>/<slug>/` (archives).
- `POST /jobs/<id>/evaluate/` re-grades a completed job. `GET /jobs/<id>/run-record/<name>` downloads run evidence.

## Template modes (scaffolding, custom)

The Generation Library (`/templates`) holds the concepts that drive scaffolding mode:

- **App specs** (`AppRequirementTemplate`) say what to build: requirements, data models, API endpoints, admin endpoints, difficulty. There is one mutable row per slug, and the slug is unique across owners. A content change (seeding, `PUT /app-specs/<slug>/`, an `overwrite` import) bumps the row's `version`; `content_hash` hashes the spec content. System specs are seeded from `data/requirements/*.json` and checked against `data/requirements/schema.json` by the data lint; `POST`/`PUT /app-specs/` and package import check the same schema. Users see the catalog plus their own specs. Agent mode uses the same specs. See [App spec format](#app-spec-format-schema-v3).
- **Generation profiles** (`GenerationProfile`) are the prompt material for template scaffolding: versioned content blocks, a stack and an `llm_config`. In agent mode a profile contributes only its stack and `llm_config`; its prompt blocks do not apply.
- **Content blocks** (`ContentBlock`) are versioned prompt fragments, keyed by `(slug, version)`, of type `prompt_stage`, `prompt_tone`, `prompt_rules`, `requirement`, `api_schema`, `scaffold_hint`, `validation` or `eval_rubric`.
- **Stacks** (`runtime.Stack`) are versioned skeletons: the agent's starting workspace and the build recipe for every mode. The builtins are `flask-react`, `fastapi-react`, `fastapi-vue` and `generic-python` ([Builtin stacks](#builtin-stacks)). Users can author their own stacks with a generated Dockerfile, from base images in `STACK_ALLOWED_BASE_IMAGES` (by default `python:3.11-slim`, `python:3.12-slim`, `python:3.13-slim`, `python:3.14-slim` and `node:20-alpine`, `node:22-alpine`, `node:24-alpine`; the older tags stay listed so existing stacks and exported packages still validate). With `STACK_REQUIRE_APPROVAL` on, a non-staff stack needs staff approval before other users' jobs can run on it.

System profiles come from `data/blocks/catalog.yaml`, and per-app pilots from `data/requirements/manifests/`. They name system blocks by slug only, and the seeder pins each to the latest system block version, so a prompt-file edit reaches every profile that uses it as a new profile version. A new system block version always takes the next free version number, so a user's own version of a system block never collides with the seeder. `is_default` marks the default profile *of its stack* (at most one per stack). The global default, used when no stack is given, is the catalog's `default_bundle_slug` (`system-scaffolding-standard`, on `flask-react`). Profiles, blocks, app specs, user stacks and presets travel between installs as [template packages](/docs/TEMPLATE_PACKAGES).

### App spec format (schema v3)

`spec_schema_version` 3 is the current format; every shipped spec uses it. v1/v2 specs, stored or in old packages, still validate and import.

| Key | v3 | Older specs |
| --- | --- | --- |
| `data_models` | Every model of the app, primary model first: a list (at least one) of `{name, description?, fields: {field: "type text"}}` | Not present |
| `data_model` | Not allowed | The single `{name, fields}` object |
| Endpoint `auth` | `public` \| `user` \| `admin` (required by the v3 lint, optional in the schema) | Absent |
| Endpoint `query` | Optional `{param: "what it does"}` | Absent |
| Endpoint `success_status` | Optional integer 200–399 (200 when absent) | Absent |

A spec must carry `data_model` or `data_models`. The `data_model` column stores the spec's own form (the legacy object or the v3 list), so a spec round-trips unchanged. Every reader goes through `services/spec_models.py` (`normalize_data_models`). The app dict that prompts, the brief, the evaluation and snapshots read (`profile_resolver.app_requirement_to_dict`) always carries `data_models` (a legacy model wrapped in a one-item list), `data_model` (the first model, for v2-shaped readers), `spec_schema_version`, and the row's `version` and `content_hash`. The API returns the same keys. The template variable `data_model` renders the single model as before, or a JSON list of all of them when there are several.

v3 specs must also meet conventions the hidden evaluation relies on (`data_lint.spec_convention_issues`), checked by the lint, `POST`/`PUT /app-specs/` and package import:

- unique endpoints, every `auth` set, admin endpoints under `/api/admin/` with `auth: admin`, and no `/api/admin` paths in `api_endpoints`,
- a public `GET /api/health`,
- `backend_requirements[0]` starts with `Access control: …` and says what it restricts. The first non-parameterised `POST` (the write the suite sends anonymously, expecting 401/403) must not be `public`,
- a `GET` without path parameters that returns a collection answers the envelope `{"items": [...], "total": <int>}`, and a `success_status` of 204 has `response: null`,
- every `/api/...` path and `?param=` the requirement prose mentions is a listed endpoint or a documented `query` parameter.

Warnings, printed by `lint_generation_data` without failing it: request samples the conformance tester would send verbatim although they read like type descriptors, and 3xx success statuses (the tester follows redirects and counts only 2xx). In the Library's spec editor, **Spec format** picks v3 (current) or a legacy version, and a legacy spec is checked against the schema only.


**The v3 rewrite is a condition boundary.** The shipped specs kept their slugs but were rewritten for v3 (the seeder bumped every row's `version`), so per-app or per-category comparisons across the rewrite compare different task texts under one `app_requirement_id`. Split them by the frozen spec `content_hash` (or `spec_schema_version`). Some subjects changed beyond wording:

- `api_weather_display` was an outside weather lookup with a cache (category `API`); v3 serves weather from the app's own store of reports and calls no outside service (category `Data Lookup`).
- Several specs whose records used to be publicly readable or had no per-user ownership rule (todos, expenses, reservations, notifications, cart items, quiz attempts, saved checks and conversions) now give each signed-in user their own records. The specs state ownership as a functional rule only. They do not prescribe the status a foreign record answers, whether public summaries leave out names, how stored text is rendered, or which URL schemes are accepted, so those stay measurements of the model.
- `api_url_shortener`'s redirect moved from `GET /api/r/:code` (a listed endpoint) to `GET /r/<short_code>`, outside `/api` and ahead of the SPA fallback. It is in the requirements text only, so it is not graded.

### Builtin stacks

The skeletons live in `backend/runtime/scaffolding/<slug>/`. Every builtin stack keeps the entry file `app.py`, port 8000, `GET /api/health` and the built SPA in `./static`, with `index.html` for every other non-API path. The FastAPI stacks mount `./static` under the `/static/` URL prefix (their Vite `base`); `flask-react` keeps Vite's default base `/`, and its `_serve_spa` catch-all serves every built file by path, so the assets load whatever static route the generated Flask app keeps (also with `static_url_path=''`). Dependencies are pinned exactly (`==` in `requirements.txt`, exact npm versions without `^`).

| Stack | Backend (`python:3.13-slim`) | Frontend (built on `node:24-alpine`) | Start command |
| --- | --- | --- | --- |
| `flask-react` | Flask 3.1.3, Flask-SQLAlchemy 3.1.1, Flask-CORS 6.0.5, SQLAlchemy 2.1.6, PyJWT 2.15.1, bcrypt 5.0.0, flask-sock 0.7.0 + simple-websocket 1.1.0 (WebSocket routes on the Werkzeug server) | React 19.2.9 + react-dom, react-router 8.5.0, Vite 8.3.5, `@vitejs/plugin-react` 6.1.2, Tailwind CSS 4.3.3 (`@tailwindcss/vite`), lucide-react 1.61.0, recharts 3.10.1, date-fns 4.4.0, clsx 2.1.1, axios 1.20.0, react-hot-toast 2.6.1 | `python app.py` |
| `fastapi-react` | FastAPI 0.162.0, uvicorn[standard] 0.54.0, Pydantic 2.14.1, email-validator 2.3.0, SQLAlchemy 2.1.6, PyJWT 2.15.1, pwdlib[argon2] 0.3.1, python-multipart 0.0.32 | Same as `flask-react` | `uvicorn app:app --host 0.0.0.0 --port 8000` |
| `fastapi-vue` | Same as `fastapi-react` | Vue 3.5.44, vue-router 5.4.0, Vite 8.3.5, `@vitejs/plugin-vue` 6.0.10, Tailwind CSS 4.3.3, `@lucide/vue` 1.61.0, axios 1.20.0 | `uvicorn app:app --host 0.0.0.0 --port 8000` |
| `generic-python` | Flask 3.1.3, Flask-CORS 6.0.5 (the agent picks any framework) | none | `python app.py` |

- **React stacks.** `main.jsx` wraps `<App />` in `<BrowserRouter>` from `react-router` (there is no `react-router-dom`) and mounts react-hot-toast's `<Toaster />`. Tailwind 4 needs no `tailwind.config.js` or `postcss.config.js`: `App.css` holds `@import "tailwindcss"` and the `primary-50`…`primary-900` scale as `@theme` tokens.
- **Vue stack.** `main.js` always installs vue-router 5 (`createWebHistory()`). When `src/routes.js` exists, its default export is the route table and `App.vue` renders `<RouterView />`. Otherwise the conventional router module is used when present: `src/router/index.js` or `src/router.js` (or `.ts`), whose default export is a `createRouter()` router or a route array (a named `routes` export works too). Without any of them every URL renders `App.vue` alone. Tailwind 4 is loaded from `src/style.css`.
- `manifest.json` (schema 2) records each stack's `server_kind` (`python` or `uvicorn`), which is part of the stack's content hash. It picks the healing placeholder backend: FastAPI for `uvicorn` stacks, Flask otherwise. Builtin rows seeded before the field existed all say `python`; for them the old rule still applies (FastAPI for `fastapi-vue` only), so retries and re-evaluations pinned to those versions keep the stub they got then.
- The agent brief's stack summaries (`data/agent/stacks.yaml`) and the scaffolding stage prompts name these same versions and packages.
- Generated user-stack Dockerfiles copy only `frontend/package.json` before `npm install --silent`, so a shipped `package-lock.json` is not honoured. The rendered Dockerfile is not part of a user stack's `content_hash`, so its shape stays fixed: changing it would rebuild every pinned user-stack version differently.
- Pins cover direct dependencies only. Transitive packages (starlette under FastAPI, wsproto under simple-websocket, Vite's own dependency tree) resolve at build time, since no stack ships a pip constraints file or `package-lock.json`, so a stack version's builds can drift between upstream releases under an unchanged `content_hash`.
- Samples built on the stack versions from before this modernization (React 18, Tailwind 3 config files, Python 3.11 / Node 20) differ in skeleton, line counts and lint settings (ESLint now assumes React 19.2), so compare them as separate conditions, not as one pool.

**Scaffolding** renders prompts from the profile's blocks (`prompt_renderer.py`) and calls the model twice: once for the backend, then for the frontend with a scan of the generated backend. When the backend scanner finds no endpoints, the frontend stage gets a summary instead whose procedure is versioned: snapshots record `frontend_context_version` (2: Flask or FastAPI route decorators and router prefixes without the SPA catch-all, ORM models and Pydantic schemas with their field names), which enters `run_fingerprint`. Snapshots frozen without it (before the modernization), and retries pinned to them, keep version 1 (`@app.route(` and `(db.Model)` lines only). The current backend user prompts state the evaluator contract from `contract.describe`, the same one the agent brief states: `GET /api/health` answers 200 with JSON; `POST /api/auth/register` and `POST /api/auth/login` take JSON `{<identity>, password}` and login returns a top-level `token`; requests authenticate with `Authorization: Bearer`; an admin account (`admin`, or `admin@example.com` when the identity is an email) with the password from `ADMIN_PASSWORD` (default `"admin"`); the SPA served from `./static` with an `index.html` fallback for client-side routes; port 8000. A spec that defines its own auth endpoints keeps them. The stage templates get this as the `contract` variable. A response cut off at `max_tokens` gets one continuation call. The code blocks are parsed into files, and files that fail static checks get one repair round (a fix is kept only if it validates). An experiment's `continuation_limit` and `enable_repair` change both; non-default values are frozen into the job snapshot (`generation`) and its `run_fingerprint`. Its snapshot keys (`prompt_hash`, `bundle_key` = `profile-slug@version`, `run_fingerprint`) mean the same as in agent mode. New scaffolding snapshots also carry `config_fingerprint` (`run_fingerprint` without the seed), so the experiment results' mixed-fingerprint check covers template mode; snapshots from before it have none and are not checked. `POST /jobs/scaffolding/` takes `{stack_slug?, app_requirement_ids, model_ids, profile_id?, sampling_preset_id?, temperature, max_tokens, llm_params?, trials, build_healing}` and creates app specs × models × trials jobs in one batch. Without a `profile_id`, each app spec gets its per-app pilot profile for the stack if one is seeded, else the stack's default profile. An explicit `stack_slug` that disagrees with the profile's stack is a 400, and so is a stack whose automatic choice falls back to a profile for another stack.

**Single prompt** (`POST /jobs/custom/`, `{model_id, system_prompt, user_prompt, temperature, max_tokens, sampling_preset_id?, llm_params?, seed?}`) sends one request and stores the raw response. Nothing is parsed, built or graded.

**Sampling** in the template modes starts from the **mode default** (`temperature` 0.3, `max_tokens` 32000). It then layers profile `llm_config` → sampling preset `llm_params` → experiment `llm_defaults` → condition `param_overrides` → per-run `llm_params` (`services/llm_params.py`, `services/sampling.py`). A sampling preset applies only when one is chosen: there is no implicit default here, because the mode default already differs from provider defaults. Only the preset's `llm_params` count; its `sampling_policy` is an agent-mode setting. The snapshot records the preset as provenance. `POST /jobs/llm-preview/ {mode, profile_id?, sampling_preset_id?, llm_params?}` returns the effective params with the source of each value (`mode_default`, `profile`, `preset`, `inline`).

**Build healing** (`GenerationJob.build_healing`, `Experiment.build_healing`) decides how much the build repairs template-mode output before Docker sees it:

| Value | What the build does |
| --- | --- |
| `full` (default) | Infers missing requirements (imports of the app's own modules and of the running Python's standard library never add a package), applies the runtime patches (sqlite path, port, app context; an unknown lucide icon is imported as `CircleAlert` under its own name, checked against `lucide_icons.json`, the pinned lucide-react's exports, in React files and, for `@lucide/vue`, in Vue stacks' `.vue`/`.js`/`.ts` files; React imports from `react-router-dom` point at `react-router` unless a package.json installs `react-router-dom`; a model-written `App.css`/`style.css` that lacks `@import "tailwindcss"` gets the skeleton's Tailwind 4 import and `@theme` tokens back, its Tailwind 3 `@tailwind` directives dropped), and fills a missing entry file with a placeholder (FastAPI for `uvicorn` stacks, Flask otherwise) |
| `no_deps` | Applies the patches but leaves `requirements.txt` as the model wrote it |
| `none` | Builds the output exactly as written, so the deploy rate is attributable to the model alone |

Healing is not part of the snapshot: healed and raw builds share a fingerprint. Every evaluation records what changed (`metrics.functional.healing_mode` / `healing_changed`, `result_data.evaluation.build.healing`). `manage.py healing_ablation --experiment <uuid> [--modes none,no_deps]` re-evaluates unchanged outputs under other modes into `result_data.healing_ablation`, for a paired estimate of the pipeline's contribution ([Study tables](/docs/STUDY_TABLES)).

**Grading.** Scaffolding jobs of an experiment are graded by the conformance suite after generation, unless the experiment's `post_run.evaluate` is off. Ad-hoc scaffolding jobs are graded on demand through `POST /jobs/<id>/evaluate/`.

## Job lifecycle (all modes)

1. The API view creates the job(s) and freezes the snapshot. `dispatcher.dispatch_job` / `enqueue_jobs` then marks the rows as executor-owned (`queued_at`, lane, endpoint, priority, `sequence`) and wakes the executor after the transaction commits. Agent jobs go to the `sandbox` lane, and scaffolding and custom jobs to the `llm` lane. The web process never runs a job: the executor (`manage.py run_executor`) claims jobs under a lease and runs `GenerationService.execute` (`services/orchestrator.py`) in a worker thread. See [Execution engine](/docs/EXECUTION).
2. Status moves `pending → running → completed | failed | cancelled`. While a job is `running`, `GenerationJob.phase` is `running` (sandbox or LLM calls), `eval_queued` (generation done, waiting for an eval slot) or `evaluating`. `queued` appears only on legacy thread-dispatch rows. Progress streams over the `generation:<job_id>` SSE channel. A cancel always wins, even if it races the end of a run, but a job that is already being evaluated finishes on its own.
3. Agent and scaffolding creates, and experiment launches, group their jobs in a `GenerationBatch`. The batch records the request (base seed, run-order seed, trials, stack, preset ids, apps, models, inline overrides), so its seed matrix and run order can be audited. `GenerationBatch.kind` is `adhoc`, `launch`, `top_up`, `retry` or `schedule`. A batch's status is derived from its jobs: `pending`/`running` while any job is active, then `completed` (all completed, or no jobs), `cancelled` (all cancelled), `failed` (none completed) or `partial`.
4. LLM access is the **job owner's**, resolved by `inference.access.resolve_access`: the owner's OpenRouter key (`credentials/services/resolver.py`), or a local endpoint the owner may use.
5. A completed job can be built and started by the runtime app, then analyzed ([Analysis pipeline](/docs/ANALYSIS_PIPELINE)).

**Cloning a finished job:**

| Action | Endpoint | Allowed from | Snapshot | Seed | Batch / experiment cell |
| --- | --- | --- | --- | --- | --- |
| Retry | `POST /jobs/<id>/retry/` | `failed`, `cancelled` | Re-resolved from current templates, except the spec content and stack version (below) | The cell's seed (common random numbers), or a fresh one if the job never had a seed | Takes over the original's cell and batch; refuses if the cell is already filled; reopens a finished experiment |
| Replay | `POST /jobs/<id>/replay/` | Any finished job | Copied verbatim | Same seed | No batch and no cell (a reproducibility check) |
| Rerun | `POST /jobs/<id>/rerun/` | Any finished job | Copied, reseeded | Fresh seed; gateway policy kept | Joins the batch, no cell (an extra, unpaired trial) |

**Retry pinning** (`job_cloning.library_pins`). Spec rows are updated in place and builtin stacks gain versions when the library changes, so a retry re-resolved from the job's fields alone would run an experiment cell on newer spec text and a newer skeleton than its siblings. A retry (agent or scaffolding) therefore keeps from the original's frozen snapshot:

- the app dict, verbatim (snapshots from before specs carried `version`/`content_hash` keep their dict without them, like their siblings),
- the stack's `{slug, version, content_hash}`, when that exact version still exists on the stack the job resolves to; otherwise the latest version is used,
- for agent jobs, the frozen brief, when both the spec and the stack are kept (the brief describes the skeleton). Scaffolding retries re-render the backend prompts from the kept spec, and keep version 1 of the frontend-stage backend summary when the original was frozen before `frontend_context_version`.

Experiment top-ups (relaunch, *Retry failed*, `top_up` schedules, added repeats or conditions) pin the same way: each new cell keeps what the earliest job of its (condition, app) froze, or, for a condition with no job yet, the earliest job of its app under another condition (then the brief is re-rendered, since it states its own condition's budget). Their `library_pin` carries `via: experiment_top_up`.

Everything else is re-resolved as before: the current harness, images and procedure. Retries therefore always run on the current sandbox images, so retrying an agent cell frozen before the Node 24 / Python 3.13 agent-base gives it a new `run_fingerprint` even when spec, stack and brief are kept. The new snapshot records what was kept and why under `library_pin` (`{source_job_id, app_requirement: pinned|live|none, stack: pinned|latest, stack_reason?, brief?: pinned, …}`), as provenance that enters no key. Re-freezing an experiment's pending jobs after a digest change is accepted uses the same pinning ([Experiments](/docs/EXPERIMENTS)). The repair block version a scaffolding retry uses is not recorded in its snapshot.

Other job and batch endpoints: `GET /jobs/` (filters include `mode`, `status`, `model_id`, `batch_id`, `harness`, `experiment_id`, `condition_id`, `endpoint_id`, `app_requirement_id`, `repeat_index`, `phase`, `current`), `GET /jobs/stats/`, `GET /jobs/<id>/`, `POST /jobs/<id>/cancel/`, `DELETE /jobs/<id>/` (not while running), `GET /jobs/<id>/artifacts/`, `GET /jobs/<id>/export/`, `GET /batches/?mode=&experiment=`, `GET /batches/<id>/`, `GET /batches/<id>/jobs/` and `POST /batches/<id>/cancel/`.

## In the UI

| Page | What it does |
| --- | --- |
| `/sample-generator` | The composer, with one tab per mode (Agent, Template scaffolding, Single prompt) and a live run preview. `?model=<slug>` and `?app=<slug>` preselect, `?from_job=<uuid>` starts from a job's settings, and `?from_batch=<uuid>` reproduces a batch's whole grid with a new seed. The draft survives a reload |
| `/sample-generator/batches` | Every batch with its progress, mean pass rate and cost |
| `/sample-generator/batches/<id>` | The results matrix and every run of one batch, with Cancel and a link to re-generate the grid |
| `/templates` | The Generation Library: app specs, stacks, presets (harness / sampling / budget), profiles and blocks, plus the Packages dialog |
| `/applications/<id>` | One job: overview, Agent run summary (agent jobs), prompts, files, container, tools, analyses, logs, artifacts and metrics, with retry/replay/rerun and evaluate actions |
| `/applications/<id>/failure` | Error details and LLM artifacts of a failed job |

For designed runs (models × harness presets × apps × repeats) see [Experiments](/docs/EXPERIMENTS). Experiments run in agent mode by default (`Experiment.mode`, either `agent` or `scaffolding`). The stack and the sampling and budget presets are set experiment-wide, with a harness preset per condition. Launches use common random numbers and a blocked randomised order, and the preview reports the budget cap as an upper-bound cost along with a comparability report per condition.
