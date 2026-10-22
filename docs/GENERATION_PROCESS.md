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
| `aider` ([Aider](https://aider.chat) 0.86.2) | Edit-format pair programmer, one headless `--message` turn plus its native lint-fix reflection | not needed | Edit format `diff` for every model by default (Aider otherwise picks it per model); weak/editor model = the run's model; the same model-metadata file for every model (context window, output cap), since Aider's own database does not know gateway-routed ids; repo map 1024 tokens by default; no commits or shell-command suggestions; all scaffold sources added to the chat |

**Harness options.** A few pinned values can be changed, but only through a harness preset (below) and only where the pinned source was checked to apply the value identically to every model family (each adapter's docstring records what was checked):

| Harness | Option | Values (default) |
| --- | --- | --- |
| `aider` | `edit_format` | `diff` \| `whole` \| `udiff` \| `diff-fenced` (`diff`) |
| `aider` | `map_tokens` | 0–8192 (1024); 0 disables the repo map |
| `mini-swe-agent` | `command_timeout_seconds` | 10–600 (30), mini's `environment.timeout` |
| `opencode` | none | OpenCode picks its editing tools by model id, so tool toggles would be model-specific |

Options are rendered only into the arguments and config a harness already emits, and only when they differ from the default, so a default section is byte-identical to one from before options existed. `GET /generation/agent/config/` lists every harness with its `option_schema` and `default_options`.

Every harness gets the brief from a file or stdin, never argv. Agents restart their dev server with "kill every process mentioning `app.py`", and a brief in argv once made OpenCode kill itself. Harness artifacts (Aider's `.aider*` caches, histories) never enter the sample.

### The brief and the evaluation contract

The brief (`data/agent/brief.md.jinja2`, `BRIEF_VERSION` 3) is the only task-specific text an agent receives, identical for every harness and model:

- an **autonomy** statement: nobody will answer questions, finish with a summary,
- the **app spec**, verbatim: requirements, data model, every endpoint with its request/response shapes. A task `description` sent with an app spec follows the spec's own description as an **Additional context** section; without an app spec the description *is* the task (a free-form task, which has no conformance suite and is not graded),
- an **accounts contract** (`services/evaluation/contract.py`). Specs say *which* writes need a signed-in user but almost never *how* to sign in, so each agent invented its own scheme and write endpoints were ungradable. Unless the spec defines its own auth endpoints, the app exposes `POST /api/auth/register` and `POST /api/auth/login` (→ `{"token"}`) and accepts `Authorization: Bearer`. Endpoints reserved for signed-in users answer 401 anonymously. An admin account's password comes from `ADMIN_PASSWORD`; the evaluation sets it to a fresh random value, so a hard-coded credential fails,
- the **runtime contract**: entry point and start command, port, `/api/health`, SPA served for non-API paths, SQLite in an initially empty `./data/`, pinned dependencies, platform-owned build files. Builtin stacks take this text from `data/agent/stacks.yaml`; a user-authored stack states the command its generated Dockerfile runs (`python <file>` or `uvicorn <module>:app …`, from its `server_kind`),
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
| Harness switches its system prompt per model family (OpenCode: build agent *and* `general` subagent) | OpenCode's own model-agnostic default prompt, captured verbatim per release and pinned for both; Aider's edit format pinned (`diff` unless a harness preset picks another) for every model |
| Harness or model-family sampling defaults (OpenCode sets per-family temperatures, Aider sends 0, others send nothing) | Sampling policy `provider_default` (default): the gateway strips every sampling/reasoning knob the harness sends. Only params the run configures explicitly reach the model, forced on every call. `harness_native` is available and recorded |
| One harness sends no `max_tokens` and gets a provider default, another sends its own | The gateway forces one per-call output cap (`min(max_tokens, model limit)`) on every call |
| Params a model can't take silently ignored (e.g. `seed`/`temperature` on some closed models) | Forwarded only if OpenRouter lists them for that model; otherwise recorded in `gateway.dropped_params` and in the experiment comparability report. A model with no supported-parameter list in the catalog gets its params forwarded unverified (`llm.supported_params_source: "unknown"`, preview warning `support_unknown`): the default routing's `require_parameters` makes a provider that can't honour one fail the run rather than ignore it, and default runs on such models keep their seed |
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

- Per-model behaviour inside the pinned harness releases, which no setting removes (the limit of "byte-identical treatment within a harness"):
  - OpenCode appends an environment block to its system prompt that names the model and today's date, and gives GPT-family model ids an `apply_patch` tool instead of `edit`/`write`.
  - Aider applies generic settings by model-name substring (reminder placement, lazy/overeager prompt additions, `use_temperature`). Every model reaches it as `openai/<vendor>/<model>`, so those still apply.
  - Capabilities differ by design: a param a model does not take is dropped (and recorded), and the output cap and context window follow the model. The run preview lists this per model before launch.
- Providers within the routing policy may still differ in serving stack; stratify by `agent.providers`.
- Debian packages in `agent-base` resolve when the image is built; the built image id is recorded per run.
- Package registries change over time under policy `packages`. Pinned dependency files limit this; `resolved_python_deps` records what the evaluated build actually installed. Policy `none` removes it.
- mini-swe-agent's native 30 s per-command timeout and Aider's single-turn headless mode are harness properties and part of what is compared. An Aider turn that writes the whole app in one response can hit the per-call output cap and apply nothing; this is observed with DeepSeek V4.1 Flash and flagged `output_truncated`.
- Without a model-metadata file Aider refreshes its model database from GitHub at start-up (blocked, recorded as `egress_denied`); with the metadata file it does not.
- Request budgets are not compute-equal across harnesses (Aider needs few calls, mini-swe-agent many). For cross-harness comparisons, report cost and tokens alongside outcomes.

Agent runs are not deterministic even with a seed (tool timing, provider batching), so **repeats are the unit of analysis**: use `trials` (or experiment `repeats`) of 3 or more, with a `base_seed` so the seed matrix and the run order are reproducible.

### Fairness rules

These hold for every agent batch and experiment; the code enforces them and the tests pin them:

1. **Within a harness**, every model gets a byte-identical harness section except the derived per-model fields (model slug, context window, output cap, step limit). Harness presets carry no per-model keys.
2. **Default runs never change identity.** With the default presets, `prompt_hash`, `run_fingerprint` and the `agent`, `gateway`, `harness` and brief values equal what they were before presets existed (`backend/generation/tests/test_default_run_golden.py`, captured from the pre-preset code).
3. **Batch- or experiment-wide:** the stack, the budget preset (limits and network policy) and the sampling preset (sampling params and sampling policy). What may vary per condition: the model and the harness preset; in experiments also `param_overrides` and the profile, which comparability always reports.
4. The brief stays harness- and model-agnostic.
5. Under `provider_default`, only explicitly configured params reach the model; unsupported ones are dropped and recorded, and the preview shows them per model.
6. Not tunable per run: harness versions, sandbox resources, the egress allowlist, protected files, the evaluator, and the default provider routing. Routing changes only through an explicit inline `llm_params.provider`, never through a preset.
7. Snapshots are immutable. Presets are versioned and immutable in content; jobs record which presets they used.
8. Preset values are copied into the job's own fields at creation (`llm_params`, `agent_config`, `harness_options`), so retry, replay and rerun reproduce the configuration without reading the preset again.

### Run presets

A run preset (`RunPreset`, `services/presets.py`) is a named, versioned piece of agent-run configuration. There are three kinds; each is normalized before it is stored or hashed:

| Kind | Config | Notes |
| --- | --- | --- |
| `harness` | `{"harness": <name>, "options": {<non-default options>}}` | Options at their defaults are dropped, so `{}` and `{"edit_format": "diff"}` are the same preset content. A user preset with every option at its default is refused: use `<harness>-default` |
| `sampling` | `{"llm_params": {...}, "sampling_policy"?: ...}` | Model-agnostic sampling settings (temperature, top_p, reasoning effort, output cap, …) applied identically to every model. `provider`, `stop` and `response_format` are refused: routing and output formatting are not sampling properties |
| `budget` | sparse subset of `wall_clock_seconds`, `max_llm_requests`, `max_total_tokens`, `max_cost_usd`, `network_policy` | `{}` = the platform defaults (the `AGENT_*` settings) |

- **Versions.** A content change creates version + 1; a name or description change does not. `content_hash` = sha256 of the canonical `{kind, config}` (integral floats hash as ints, so `0` and `0.0` agree). Delete archives every version; jobs keep their references (`PROTECT`).
- **Slugs** are global per kind, whoever owns them: `^[a-z0-9][a-z0-9-]{1,80}$`, and `*-default` plus every system preset file's slug are reserved. A taken slug is a plain "slug already in use", never naming the owner.
- **System presets** live in `data/presets/<kind>-<slug>.yaml`: `harness/opencode-default`, `harness/mini-swe-agent-default`, `harness/aider-default`, `sampling/provider-default`, `budget/platform-default`. They are seeded first in `seed_all`, idempotent by content hash (a changed file becomes a new version). A request-time guard (`ensure_system_presets`) recreates missing defaults, so the default run path never depends on seeding having worked.
- **Stored presets are re-validated** whenever a run uses them; a preset a harness release no longer accepts is an `invalid_preset` error in the preview and a 400 on create, never a crash.
- **Starter presets.** The `agent-benchmark-starter` package ([Template packages](/docs/TEMPLATE_PACKAGES)) adds `aider-whole-file`, `aider-no-repo-map`, `mini-long-commands`, `temperature-zero`, `reasoning-high`, `smoke` (30 requests, 60 min, $3) and `extended` (250 requests, 180 min, $25). Their USD and wall-clock caps stay generous safety nets, so the request budget still binds.

How a batch applies them (later wins), copied into job fields at creation:

- `job.agent_config` = budget preset → the sampling preset's `sampling_policy` → inline `agent_config` (+ the harness),
- `job.llm_params` = legacy `max_tokens` → profile `llm_config` → sampling preset `llm_params` → inline `llm_params`,
- `job.harness_options` = the harness preset's options (never part of `agent_config` or the snapshot's `agent` section).

The `harness_preset`, `sampling_preset` and `budget_preset` foreign keys on the job, and `snapshot.presets` (`{id, slug, version, content_hash, name}` per kind), are provenance only. Leaving a preset out means the system default for its kind.

### Identity keys

- `prompt_hash`: brief sha + contract version. The *task* identity, shared across harnesses, models and seeds; the grouping key for comparisons. The brief states the budget, so changing the budget changes it.
- `bundle_key` (`method_key` in the snapshot): `[profile@version/]agent:<harness>@<version>`, plus a content-addressed suffix for each part that departs from the defaults:
  - `+opts:<sha8>`: non-default harness options,
  - `+llm:<sha8>`: explicit LLM params other than `max_tokens` and `seed`, and a non-default sampling policy,
  - `+budget:<sha8>`: an effective budget (limits or network policy) that differs from the platform defaults.

  Hashing content rather than preset names means renaming a preset (or importing it under another slug) never splits a slice, and a default run keeps the key it always had. The *method* identity and condition slicing key.
- `run_fingerprint`: prompt hash + model + gateway policy + harness section + image tags + agent config + stack content hash. Equal fingerprints mean exact replays.
- `config_fingerprint`: the same inputs minus the seed. The *condition* identity that replicates (trials) share, used to group trials in batch views. Jobs created before presets have none. A rerun keeps the original's frozen gateway policy (only the forwarded seed changes, never what today's catalog says the model supports), so it has the original's `config_fingerprint`.
- Numbers enter these keys in their declared type: float sampling params (`temperature`, `top_p`, `min_p`, the penalties) and `max_cost_usd` are frozen as floats, integer limits as ints, whichever layer set them. A preset stored as `temperature: 0` and an inline `0.0` therefore fingerprint alike. (The keys hash plain JSON on purpose: folding `5.0` into `5` would change every default run's fingerprint.)

When you change something, version it:

- `BRIEF_VERSION` (`services/agent/brief.py`) for the brief (v3 added the "Additional context" section and user-stack start commands; briefs without either are byte-identical to v2).
- `CONTRACT_VERSION` for the contract.
- The tester's `VERSION` / `EVALUATOR_VERSION` for grading.
- `SANDBOX_REVISION` (`harnesses/base.py`) when the sandbox environment changes without an image change.
- To move OpenCode to a new release, capture its default prompt into `data/agent/harness_prompts/opencode-<version>-default.md` first; jobs refuse to snapshot otherwise.

### Configuration

| Setting | Default | |
| --- | --- | --- |
| `AGENT_DEFAULT_HARNESS` | `opencode` | |
| `GENERATION_DEFAULT_STACK` | `flask-react` | stack when neither the request nor a profile names one (agent and scaffolding) |
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

API (prefix `/api/generation`):

- `POST /jobs/agent/` takes `{stack_slug?, app_requirement_ids, description, model_ids, harness_preset_ids, sampling_preset_id?, budget_preset_id?, llm_params?, agent_config?, max_tokens?, profile_id?, trials, base_seed?}`. It creates tasks × models × harness presets × trials jobs in one batch, with one seed per (task, trial) cell shared by every model and harness preset (`derive_experiment_seed(base_seed, app slug or "task", trial)`) and blocked random order.
  - The stack is `stack_slug`, else the profile's stack, else `GENERATION_DEFAULT_STACK`; whatever its source, it must exist and be usable by the requester.
  - Without presets or harnesses the default harness's system preset runs. The legacy `harnesses: [<name>]` maps each name to its system default preset; it cannot be combined with `harness_preset_ids`, nor can `agent_config.harness`.
  - Any error-level problem refuses the whole batch with `400 {detail, code, errors: [...]}` before anything is written.
- `POST /jobs/agent/preview/` takes the same body and writes nothing. It returns the run count, the effective agent config and LLM params with the source of each value, every harness preset with its effective options, a per-model table (tool support, context window and its source, output cap, forced and dropped params, whether the seed is forwarded), the maximum cost (budget cap × runs) and warnings grouped by severity: errors `no_tasks`, `no_models`, `models_without_tools`, `missing_api_key`, `duplicate_harness_config`, `invalid_preset`, `reasoning_exceeds_output_cap`, `resolution_failed`; warnings `params_dropped` (the seed never counts), `support_unknown` (the catalog lists no supported parameters for a model, so its params are forwarded unverified), `harness_native_sampling`, `context_window_fallback`, `cost_cap_may_bind`, `wall_clock_may_bind`, `whole_file_small_output`, `large_batch`, `ungraded_task`; infos `non_default_harness_options`, `budget_changes_brief`, `single_trial`, `random_seeds`.
- `GET /agent/config/` returns the defaults, limits, policies, the default stack and output cap, and every harness with its option schema. `GET /agent/harnesses/` lists the harnesses.
- Presets: `GET /presets/?kind=&include_archived=` (latest version per slug: system and your own), `POST /presets/validate/`, `POST /presets/`, `GET /presets/<id>/` (any visible version), `GET /presets/<kind>/<slug>/versions/`, `PUT /presets/<kind>/<slug>/` (content change → new version) and `DELETE /presets/<kind>/<slug>/` (archive).
- `POST /jobs/<id>/evaluate/` re-grades a job.

## Template modes (scaffolding, custom)

The Generation Library concepts still drive scaffolding mode:

- **App specs** (`AppRequirementTemplate`): what to build, validated against `data/requirements/schema.json`. Agent mode uses the same specs.
- **Generation profiles** (`GenerationProfile`): prompt material for template scaffolding — versioned content blocks + stack + `llm_config`. In agent mode a profile contributes only its stack and `llm_config`; its prompt blocks do not apply.
- **Stacks** (`runtime.Stack`): versioned skeletons. The agent's starting workspace and the build recipe for every mode.

System profiles come from `data/blocks/catalog.yaml` (and per-app pilots from `data/requirements/manifests/`). They name system blocks by slug only; the seeder pins each to the latest system block version, so a prompt-file edit reaches every profile that uses it as a new profile version. A new system version always takes the next free version number, so a user's own version of a system block never collides with the seeder. `is_default` marks the default profile *of its stack* (at most one per stack); the global default, used when no stack is given, is the catalog's `default_bundle_slug` (`system-scaffolding-standard`). Profiles, blocks, app specs, stacks and presets travel between installs as [template packages](/docs/TEMPLATE_PACKAGES).

Scaffolding renders prompts from the profile's blocks (`prompt_renderer.py`), calls the model twice (backend, then frontend with a scan of the backend), parses the code blocks into files, and runs one repair round on files that fail static checks. Its snapshot keys (`prompt_hash`, `bundle_key`, `run_fingerprint`) mean the same as in agent mode.

Sampling in the template modes starts from the **mode default** (`temperature` 0.3, `max_tokens` 32000), then layers profile `llm_config` → sampling preset `llm_params` → experiment `llm_defaults` → condition `param_overrides` → per-run `llm_params` (`services/llm_params.py`, `services/sampling.py`). A sampling preset applies only when one is chosen (no implicit default here, since the mode default already differs from provider defaults), and only its `llm_params` count; its `sampling_policy` is an agent-mode setting. The snapshot records the preset as provenance. `POST /jobs/llm-preview/ {mode, profile_id?, sampling_preset_id?, llm_params?}` returns the effective params with the source of each (`mode_default`, `profile`, `preset`, `inline`). An explicit scaffolding `stack_slug` that disagrees with the profile's stack is a 400.

## Job lifecycle (all modes)

1. The API view creates the job(s), freezes the snapshot, and calls `dispatcher.dispatch_job`, which runs `GenerationService.execute` on a daemon thread.
2. Status moves `pending → running → completed | failed | cancelled`. Progress streams over the `generation:<job_id>` SSE channel. While running, `GenerationJob.phase` records `queued` (waiting for a sandbox slot), `running` or `evaluating`, so lists and counts survive a reload; it is cleared when the job ends. A cancel request always wins, even if it races the end of a run.
3. Agent and scaffolding creates, and experiment launches, group their jobs in a `GenerationBatch` that records the request (base seed, trials, stack, preset ids, apps, models), so a batch's seed matrix and run order can be audited. A batch's status is derived from its jobs: `pending`/`running` while any job is active, then `completed` (all completed), `cancelled` (all cancelled), `failed` (none completed) or `partial`. `GET /batches/`, `GET /batches/<id>/`, `GET /batches/<id>/jobs/` and `POST /batches/<id>/cancel/` serve the batch views; `GET /jobs/` filters by `batch_id` and `harness`.
4. The OpenRouter key is the **job owner's**, resolved by `credentials/services/resolver.py`.
5. A completed job can be built and started by the runtime app, then analyzed ([analysis pipeline](/docs/ANALYSIS_PIPELINE)).

For designed runs (models × harness presets × apps × repeats) see [Experiments](/docs/EXPERIMENTS). Experiments run in agent mode by default (`Experiment.mode`), with the stack and the sampling and budget presets set experiment-wide and a harness preset per condition; launches use common random numbers and blocked randomised order. The preview reports the budget cap as an upper-bound cost and a comparability report per condition.
