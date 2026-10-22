# Generation process

How a sample app gets made: the primary **agent** mode (a coding-agent harness builds the app in a sandbox and the platform grades it with a hidden conformance suite), the secondary template modes, and what each run records so results stay comparable.

## Modes

`GenerationJob.mode` (`backend/generation/models.py`) selects the strategy:

| Mode | What it does | Use it for |
| --- | --- | --- |
| `agent` (default) | A coding-agent **harness** (OpenCode, mini-swe-agent or Aider) works on the stack skeleton inside an isolated sandbox until it finishes or its budget runs out; the platform then builds and grades the result (`services/agent/`, `services/evaluation/`) | (model × harness) comparisons on realistic, multi-file, self-tested apps |
| `scaffolding` | A generation profile renders backend/frontend prompts; two LLM calls fill a known skeleton | One-shot "how well does a model write code from a prompt" comparisons |
| `custom` | Your prompt goes straight to the model | Ad-hoc probing |
| `copilot` | *Retired.* The in-process aider loop. Kept only so historical jobs still load; `POST /jobs/copilot/` answers 410. (Aider itself lives on as a sandboxed agent harness) | — |

## Agent mode

### What runs where

```
Django ──(apps daemon, mTLS)──▶ apps-dind
   run:       llmlab-gateway-<job>   bridge + job net   holds the real OpenRouter key
              llmlab-agent-<job>     job net only       harness + workspace, no key
   evaluate:  llmlab-eval-app-<job>  eval net only      the built sample, no egress
              llmlab-eval-tester-…   eval net only      hidden conformance suite
```

- **Per-job sandbox** (`services/agent/sandbox.py`). There is one internal Docker network per job. The agent container is attached only to that network, so its only route is the gateway. It runs unprivileged (all caps dropped, `no-new-privileges`) with memory, CPU and PID caps that are frozen into the snapshot. Everything is a nested container on apps-dind, never the host daemon; it is labelled with the job id and removed in `finally`. *Purge orphan containers* in System → Maintenance reaps leftovers from a crashed process.
- **Images** (`images/`, `services/agent/images.py`). Every harness image builds `FROM images/agent-base`: Node 22.23.3 + Python 3.11 + git on an exactly pinned base release. So the tools an agent finds are byte-identical across harnesses; only the harness layer differs. Python harnesses install a fully locked dependency set (`requirements.lock`) into a private venv that the agent's own `python3`/`pip` never sees. Tags are content hashes of the image directory and build args; the harness tag covers the base tag. The snapshot records the tags at job creation, and a job refuses to run if an image definition changed since.
- **LLM gateway** (`images/llm-gateway/gateway.py`, stdlib only). The harness is configured with a dummy key and the gateway as its OpenAI/OpenRouter base URL. The gateway:
  - pins the model,
  - applies the job's sampling policy (below),
  - forces the job's seed, provider routing and per-call output cap,
  - retries transient upstream failures itself (408/429/5xx, connection errors, with backoff),
  - counts only successful calls toward the step budget,
  - answers a non-retryable 400 once the budget is spent,
  - logs every exchange.
- **Egress.** Under network policy `packages` (the default), the gateway also tunnels HTTPS `CONNECT` to `pypi.org`, `files.pythonhosted.org` and `registry.npmjs.org`. Everything else is refused and logged. Under policy `none`, nothing is reachable.

### Harnesses (`services/agent/harnesses/`)

A harness is the agent program that drives the model. Benchmarks compare (harness, model) pairs:

- **Within a harness**, every model gets byte-identical treatment. Anything a harness would normally switch per model family is pinned: prompts, edit formats, temperatures.
- **Across harnesses**, each keeps its own native prompts, frozen; the harness's design is what is being compared. All of them get the same sandbox image, the same brief, the same gateway policy and the same budgets.

| Harness | Paradigm | Tool calling | What is pinned beyond the release |
| --- | --- | --- | --- |
| `opencode` ([OpenCode](https://opencode.ai) 1.18.35) | Full IDE-style agent: read/edit/write/grep/glob/bash/todo/task tools | required | Its own model-agnostic default prompt (captured verbatim in `data/agent/harness_prompts/`) for the `build` agent **and** the `general` subagent; OpenCode otherwise picks a per-family prompt for both. Web fetch/search and skills off; config/AGENTS.md discovery, plugins and auto-update off; session title passed in (no hidden title call); `doom_loop` explicitly denied |
| `mini-swe-agent` ([mini-swe-agent](https://mini-swe-agent.com) 2.4.6) | Minimal research baseline: one `bash` tool, fresh subshell per command | required | Native `mini.yaml` templates; `yolo`, `step_limit` = request budget, cost limit off (the gateway owns cost) |
| `aider` ([Aider](https://aider.chat) 0.86.2) | Edit-format pair programmer, one headless `--message` turn plus its native lint-fix reflection | not needed | Edit format `diff` for every model (Aider otherwise picks it per model); weak/editor model = the run's model; the same model-metadata file for every model (context window, output cap), since Aider's own database does not know gateway-routed ids; repo map 1024 tokens; no commits or shell-command suggestions; all scaffold sources added to the chat |

Every harness gets the brief from a file or stdin, never argv. Agents restart their dev server with "kill every process mentioning `app.py`", and a brief in argv once made OpenCode kill itself. Harness artifacts (Aider's `.aider*` caches, histories) never enter the sample.

### The brief and the evaluation contract

The brief (`data/agent/brief.md.jinja2`, `BRIEF_VERSION` 2) is the only task-specific text an agent receives, identical for every harness and model:

- an **autonomy** statement: nobody will answer questions, finish with a summary,
- the **app spec**, verbatim: requirements, data model, every endpoint with its request/response shapes,
- an **accounts contract** (`services/evaluation/contract.py`). Specs say *which* writes need a signed-in user but almost never *how* to sign in, so each agent invented its own scheme and write endpoints were ungradable. Unless the spec defines its own auth endpoints, the app exposes `POST /api/auth/register` and `POST /api/auth/login` (→ `{"token"}`) and accepts `Authorization: Bearer`. Endpoints reserved for signed-in users answer 401 anonymously. An admin account's password comes from `ADMIN_PASSWORD`; the evaluation sets it to a fresh random value, so a hard-coded credential fails,
- the **runtime contract**: entry point, port 8000, `/api/health`, SPA served for non-API paths, SQLite in an initially empty `./data/`, pinned dependencies, platform-owned build files,
- the notice that the result is **checked automatically** against all of the above (the checks themselves stay hidden),
- the **budget**, with the request budget stated as the primary limit.

There are no length targets and no style coaching.

### One run, step by step (`services/agent/runner.py`)

1. **Snapshot at job creation** (`services/agent/snapshot.py`). Everything is rendered and frozen into `resolved_bundle`: brief, contract, harness section, image tags, sampling and gateway policy, routing, budget, sandbox caps and the stack version. A run never re-reads a template, and older snapshot schemas are refused rather than replayed approximately.
2. **Queueing.** `RunQueue` admits runs to the sandbox slots (`AGENT_MAX_CONCURRENT_RUNS`, default 1) in **FIFO order of dispatch**, so the order an experiment shuffles its jobs into is the order they run in. Cancellation is polled while waiting; a dead reservation never blocks the queue.
3. **Seeding.** The stack skeleton is copied into `/workspace` and committed as `scaffold`. Dependency/build directories and `data/` go into `.git/info/exclude`.
4. **The agent run.** `/out/harness.sh` runs under `timeout`. A heartbeat touches the job row every minute.
5. **Collection.** The run records the patch, diffstat and name-status, and the workspace is classified against the seed (`added` / `modified` / `scaffold`). Binaries, oversized files and harness artifacts are dropped. Tampering with platform-owned files is flagged.
6. **Evaluation** (same slot, `services/evaluation/runner.py`; see below).
7. **Evidence.** Everything goes to `GENERATION_RUNS_ROOT/<job>/` with sha256s in the job row:
   - `llm_calls.jsonl.gz`, the authoritative log of every request and response, with the policy diff per call,
   - the harness's own report (event stream / trajectory / chat log),
   - `changes.patch`, `workspace.tar.gz` and the logs.

   Download at `GET /generation/jobs/<id>/run-record/<name>`; this is refused if the file no longer matches its sha.

### Platform evaluation (hidden conformance suite)

Every sample is graded the same way by the platform, never by its own report:

1. **Build:** `prepare_build_dir(job)` + the stack's own Dockerfile on apps-dind. One retry happens only if the log shows a registry/network signature, and repeated infra failures are `infra_error`, not a failed sample.
2. **Run:** the image runs on an internal network (no egress), with memory/CPU caps and `ADMIN_PASSWORD` random.
3. **Grade:** `images/conformance-tester/tester.py` (stdlib, own container on the same network) checks only what the brief states, in a fixed order:
   - runtime: health,
   - frontend: `/` and a deep link serve the SPA,
   - auth: register, login, admin login, anonymous writes rejected,
   - api/admin: every contract endpoint answers 2xx with the documented fields.

   Ids come from the app's own responses. Destructive or state-changing item checks each get a freshly created resource, so no check depends on another's side effects. Request bodies are synthesised deterministically from the spec's samples.
4. **Record:**
   - `result_data.evaluation`: status, summary, every check, build log tail, the dependencies pip actually resolved, the app log tail, and grader identity (`evaluator_version`, `tester_version`, `tester_sha256`, `contract_version`),
   - `metrics.functional`: `pass_rate`, endpoints ok/checked.

Statuses:

- `passed` / `failed`: the suite ran.
- `build_failed` / `start_failed`: a sample outcome.
- `infra_error`: exclude and re-evaluate.
- `not_applicable`: no app spec.

`POST /generation/jobs/<id>/evaluate/` re-grades any completed job through the same queue, after a grader change, an `infra_error`, or for template-mode samples. Template-mode prompts never stated the accounts contract (`contract_stated_in_prompt: false`), so don't compare those scores with agent-mode ones.

### Outcome semantics

A run that made at least one successful LLM call is a **sample**: the job is `completed`, and `result_data.agent.termination` says how it ended.

| termination | meaning |
| --- | --- |
| `completed` | the agent stopped by itself |
| `budget_exhausted` | a budget was hit (`budget_exhausted` names which: `max_requests`, `max_total_tokens`, `max_cost_usd`, `max_attempts`) |
| `timeout` | wall-clock limit hit |
| `resource_limit` | killed at the sandbox memory cap |
| `harness_killed` | a process inside the sandbox (the agent's own commands) signalled the harness |
| `harness_error` | the harness exited non-zero for another reason |

Do not drop budget stops or timeouts from an analysis. That biases comparisons toward models that finish quickly. A run with **no** successful LLM call is an infrastructure failure (bad key, no provider matching the routing, sandbox down): the job is `failed` with the upstream error.

**Validity notes** (`result_data.agent.validity`) list what an analysis must know before pooling a sample:

- `exclude`: the platform could not evaluate it.
- `warn`: valid but not model-neutral or not as configured. Examples: a USD or token cap bound the run (expensive or verbose models get fewer steps), a wall-clock stop (provider latency), a param the model can't take, a different served model id, the memory cap, or responses cut off at the per-call output cap (`output_truncated`; single-turn harnesses such as Aider are most exposed).
- `info`: context, such as the request budget binding, failed LLM calls, harness self-kills, blocked egress, or edits to platform files.

`valid` is false only for `exclude` notes.

### Building and running the result

Agent results set `result_data.materialize = "verbatim"`. The runtime writes the collected files over the pinned skeleton exactly as the agent left them: no entry renaming, no Flask patching, no dependency inference. It restores the platform-owned `Dockerfile`, `.dockerignore` and `docker-compose.yml`. Builtin stack Dockerfiles copy the whole backend tree and honour `package-lock.json`.

## Research design: what keeps agent samples comparable

| Threat | Control |
| --- | --- |
| Harness switches its system prompt per model family (OpenCode: build agent *and* `general` subagent) | OpenCode's own model-agnostic default prompt, captured verbatim per release and pinned for both; Aider's edit format pinned to `diff` for every model |
| Harness or model-family sampling defaults (OpenCode sets per-family temperatures, Aider sends 0, others send nothing) | Sampling policy `provider_default` (default): the gateway strips every sampling/reasoning knob the harness sends. Only params the run configures explicitly reach the model, forced on every call. `harness_native` is available and recorded |
| One harness sends no `max_tokens` and gets a provider default, another sends its own | The gateway forces one per-call output cap (`min(max_tokens, model limit)`) on every call |
| Params a model can't take silently ignored (e.g. `seed`/`temperature` on some closed models) | Forwarded only if OpenRouter lists them for that model; otherwise recorded in `gateway.dropped_params` and in the experiment comparability report |
| Model "X" served by different providers at fp4…bf16, some ignoring `seed` | Default routing: `require_parameters` + quantization ≥ fp8 (or unknown, for closed models); the serving provider of every call is recorded (`agent.providers`) |
| Model alias repointed mid-study | The dated canonical slug is recorded per job; served model ids per call; mismatches are flagged |
| Hidden auxiliary calls (titles, "small model", weak model) | Title passed in; small/weak/editor model = the run's model; the gateway pins the model on every call and counts override attempts |
| A flaky provider costs a model steps; harnesses retry differently | Gateway retries transient failures for every harness; only successful calls count toward `max_llm_requests`; attempts are capped separately |
| Budgets that favour some models (USD favours cheap ones, tokens terse ones, wall clock fast providers) | The **request budget** binds by default (100). USD (5), tokens (30M) and wall clock (60 min) are safety nets; a run stopped by one carries a `warn` note |
| Time confounds (provider load, routing, rate limits drift over hours) | Randomised complete blocks: repeat by repeat, cells shuffled within a repeat with a seed derived from the experiment. The FIFO queue keeps dispatch order = run order, and each job records its run order and queue time |
| Seeds confounded with conditions | Common random numbers: every condition of an (app, repeat) cell shares one seed |
| Unequal sandboxes across harnesses | One base image with an exact pinned release; harness packages locked and isolated in a private venv; memory/CPU caps frozen per job |
| Dependency drift inside a harness (an unpinned install once resolved a LiteLLM that would not import) | Fully locked `requirements.lock` per Python harness; content-addressed image tags recorded per job; a changed definition makes old jobs refuse to run |
| Agent kills its own harness while restarting its server | Brief via file/stdin only; `harness.sh` exec'd under `timeout`; remaining self-kills are recorded as `harness_killed` |
| Agent's requests to its own app captured by the egress proxy | Only `HTTPS_PROXY` is set, and loopback is in `NO_PROXY` |
| Agent looks things up online, or the run depends on internet state | No web tools; egress limited to package registries (or none) |
| Agent reconfigures its harness or reads stray instructions | Config/AGENTS.md/CLAUDE.md discovery, plugins and external skills off |
| Agent games its own evaluation | Hidden, platform-run conformance suite; platform-owned build files restored; random admin password; app evaluated with no egress |
| Ungradable or inconsistent requirements (no auth API in the specs) | A shared, versioned accounts/runtime contract stated in every brief and checked by the suite |
| Grading artefacts (one check's side effects failing another) | Fresh resources for destructive checks; deterministic order and bodies; grader versioned and hashed in every result |
| Prompt coaching that favours some models (length targets, style rules) | The brief states only the spec and the contract |
| Key exfiltration into generated code | The agent never holds the real key |

Known residual variation, recorded but not controlled:

- OpenCode appends an environment block to its system prompt that names the model and today's date.
- Providers within the routing policy may still differ in serving stack; stratify by `agent.providers`.
- Debian packages in `agent-base` resolve when the image is built; the built image id is recorded per run.
- Package registries change over time under policy `packages`. Pinned dependency files limit this; `resolved_python_deps` records what the evaluated build actually installed. Policy `none` removes it.
- mini-swe-agent's native 30 s per-command timeout and Aider's single-turn headless mode are harness properties and part of what is compared. An Aider turn that writes the whole app in one response can hit the per-call output cap and apply nothing; this is observed with DeepSeek V4.1 Flash and flagged `output_truncated`.
- Without a model-metadata file Aider refreshes its model database from GitHub at start-up (blocked, recorded as `egress_denied`); with the metadata file it does not.
- Request budgets are not compute-equal across harnesses (Aider needs few calls, mini-swe-agent many). For cross-harness comparisons, report cost and tokens alongside outcomes.

Agent runs are not deterministic even with a seed (tool timing, provider batching), so **repeats are the unit of analysis**: use `trials` (or experiment `repeats`) of 3 or more, with a `base_seed` so the seed matrix and the run order are reproducible.

### Identity keys

- `prompt_hash`: brief sha + contract version. The *task* identity, shared across harnesses, models and seeds; the grouping key for comparisons. The brief states the budget, so changing the budget changes it.
- `bundle_key` (`method_key` in the snapshot): `agent:<harness>@<version>`, prefixed with `profile@version/` when a profile supplied the stack/`llm_config`. The *method* identity and condition slicing key.
- `run_fingerprint`: prompt hash + model + gateway policy + harness section + image tags + agent config + stack content hash. Equal fingerprints mean replicate runs.

When you change something, version it:

- `BRIEF_VERSION` (`services/agent/brief.py`) for the brief.
- `CONTRACT_VERSION` for the contract.
- The tester's `VERSION` / `EVALUATOR_VERSION` for grading.
- `SANDBOX_REVISION` (`harnesses/base.py`) when the sandbox environment changes without an image change.
- To move OpenCode to a new release, capture its default prompt into `data/agent/harness_prompts/opencode-<version>-default.md` first; jobs refuse to snapshot otherwise.

### Configuration

| Setting | Default | |
| --- | --- | --- |
| `AGENT_DEFAULT_HARNESS` | `opencode` | |
| `AGENT_OPENCODE_VERSION` / `AGENT_MINI_SWE_AGENT_VERSION` / `AGENT_AIDER_VERSION` | `1.18.35` / `2.4.6` / `0.86.2` | releases baked into the images |
| `AGENT_MAX_LLM_REQUESTS` | 100 | the binding budget (successful calls) |
| `AGENT_WALL_CLOCK_SECONDS` | 3600 | safety net |
| `AGENT_MAX_TOTAL_TOKENS` | 30M | safety net |
| `AGENT_MAX_COST_USD` | 5.0 | safety net |
| `AGENT_SAMPLING_POLICY` | `provider_default` | or `harness_native` |
| `AGENT_NETWORK_POLICY` | `packages` | or `none` |
| `AGENT_AUTO_EVALUATE` | true | grade every sample right after its run |
| `AGENT_MAX_CONCURRENT_RUNS` | 1 | sandbox slots per process (runs and evaluations share them) |
| `AGENT_SANDBOX_MEM_LIMIT` / `_NANO_CPUS` | `3g` / 2 CPUs | must fit apps-dind's own cap |

API:

- `POST /generation/jobs/agent/` takes `{stack_slug, app_requirement_ids | description, model_ids, harnesses, trials, base_seed, llm_params, agent_config, profile_id}`. It creates apps × models × harnesses × trials jobs in one batch, with common seeds and blocked random order. Tool-calling harnesses only accept tool-calling models.
- `GET /generation/agent/harnesses/` lists the available harnesses.
- `POST /generation/jobs/<id>/evaluate/` re-grades a job.

## Template modes (scaffolding, custom)

The Generation Library concepts still drive scaffolding mode:

- **App specs** (`AppRequirementTemplate`): what to build, validated against `data/requirements/schema.json`. Agent mode uses the same specs.
- **Generation profiles** (`GenerationProfile`): versioned content blocks + stack + `llm_config`. In agent mode a profile contributes only its stack and `llm_config`; its prompt blocks do not apply.
- **Stacks** (`runtime.Stack`): versioned skeletons. The agent's starting workspace and the build recipe for every mode.

Scaffolding renders prompts from the profile's blocks (`prompt_renderer.py`), calls the model twice (backend, then frontend with a scan of the backend), parses the code blocks into files, and runs one repair round on files that fail static checks. Sampling params layer `profile llm_config → experiment llm_defaults → condition param_overrides → per-run llm_params` (`services/llm_params.py`). Its snapshot keys (`prompt_hash`, `bundle_key`, `run_fingerprint`) mean the same as in agent mode.

## Job lifecycle (all modes)

1. The API view creates the job(s), freezes the snapshot, and calls `dispatcher.dispatch_job`, which runs `GenerationService.execute` on a daemon thread.
2. Status moves `pending → running → completed | failed | cancelled`. Progress streams over the `generation:<job_id>` SSE channel (agent runs also publish `queued`/`running` phases). A cancel request always wins, even if it races the end of a run.
3. The OpenRouter key is the **job owner's**, resolved by `credentials/services/resolver.py`.
4. A completed job can be built and started by the runtime app, then analyzed ([analysis pipeline](/docs/ANALYSIS_PIPELINE)).

For designed runs (models × harnesses × apps × repeats) see [Experiments](/docs/EXPERIMENTS). Experiments run in agent mode by default (`Experiment.mode`). Conditions can name a harness; launches use common random numbers and blocked randomised order. The preview reports the budget cap as an upper-bound cost and a comparability report per condition.
