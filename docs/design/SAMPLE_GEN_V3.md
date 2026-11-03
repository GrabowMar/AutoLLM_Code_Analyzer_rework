# SAMPLE_GEN_V3: authoritative implementation spec

Status: **final** (2026-11-02, revision 2). Branch `feat/sample-gen-redesign`, worktree `/home/ubuntu/code/llmlab-wt-samplegen`.
Inputs: `final-plan.md`, `plan.md` (sections A-F), the seven audit reports (agentic, scaffolding, templates, frontend, blast radius, live data, research), `harness-research.md`, `budget-research.md`, and three critic reviews of revision 1 (research validity, codebase fit, researcher UX and budget). Every critic issue and its resolution is listed in section 14 (Decisions log).
This document supersedes all of them wherever they disagree. When a work package (WP) needs a decision this spec does not make, the WP owner writes it down in a `## Decisions` list at the end of the WP's own doc file (see section 12) and does not quietly diverge.

Keywords: **MUST**, **MUST NOT** and **SHOULD** are normative. `H(x)` means `sha256(canonical_json(x))` as hex (section 3.1). Knob classes **C / R / O / S** are defined in section 4.1.

---

## 0. Binding decisions (summary)

| # | Decision |
|---|---|
| D1 | **Agentic whole-app generation is the primary mode.** Protocol `app@1`. Eight harnesses: opencode, mini-swe-agent, aider (ported), plus Claude Code, Codex CLI, Goose, Qwen Code and Gemini CLI (new). All of them reach models only through the per-run LLM gateway, which owns telemetry and budget enforcement. |
| D2 | **Slot mode** replaces the old scaffolding mode, which is deleted outright. Protocol `slot@1`. Flow: completed agentic runs are the *parent pool*. Parents are selected by a recorded rule (random among tested runs by default, several parents per task). The researcher picks a *part* of a parent (file, region, symbol, endpoint handler or UI component). The part is validated (its linked checks must discriminate) and leak-scanned, then regenerated N times while the rest of the app stays byte-frozen. Each variant is built, tested and pattern-analysed. |
| D3 | **Clean break.** Old generation rows, models, APIs, seed data and the scaffolding system are deleted. There are no compatibility adapters. Data is wiped by an explicit operator command that runs on the **old** schema before v3 is deployed (section 3.12); migrations never wipe data on their own. |
| D4 | Django app label **`generation` is kept**. Its migration graph is depended on by `analysis`, `execution`, `human_eval`, `inference`, `reports` and `runtime`, and by the other session's WIP migrations (`execution/0002_runtimebuildlease`, `runtime/0005_sample_build_artifact`). The app's internals are reshaped. |
| D5 | `GenerationJob` is **renamed `GenerationRun`** and `Experiment` is renamed **`Study`** (`RenameModel` plus explicit `db_table`). These two keep inbound FKs from other apps. `ExperimentCondition`, `ExperimentSchedule` and `ExperimentPostRun` are deleted and recreated as `StudyCondition`, `StudySchedule` and `StudyPostRun` (data is wiped and nearly every field changes). `GenerationBatch` is deleted and merged into Study (kind `exploratory`). FK attribute names on models in **other** apps stay as they are (`AnalysisRun.generation_job`, `LLMRequest.job`, `ContainerInstance.generation_job`, `Report.generation_job`, `EvalTaskApp.job`), which keeps that churn mechanical. In prose and UI the entity is a **Run**; in the frontend the run page is a **Sample** (`/samples/[id]`). |
| D6 | The execution service (`llmlab-executor`, `backend/execution`) still runs every unit of work. Generation lanes stay `sandbox` (agentic runs and restricted slot agents) and `llm` (single-shot slot calls and batch submissions). Evaluation is claimed by **phase** (`status=running, phase=eval_queued`), not by lane, as today. Reference runs are created directly in that state through `execution.api.enqueue_evaluation`. Post-run work keeps its own slots. |
| D7 | **Budget is enforced outside the model.** The gateway enforces it per run (with pre-call reservation), and the scheduler enforces it per cell, study, user, endpoint and quota period through a reservation ledger. Budget text MUST NOT appear in any prompt. Budget stops are **balanced**: runs are claimed in replicate blocks and a budget stop never leaves some units at full n and others at zero. |
| D8 | Every library entity is **immutable per `(slug, version)`** and carries a `content_hash`. Editing creates version+1. Archiving replaces deleting. Studies pin exact versions. |
| D9 | AuthMode is a **closed enum** (`contract_bearer`, `cookie_session`, `spec_defined`). Its contract text lives in versioned PromptTemplate fragments (`auth-contract-<mode>`), so the text is hashed. |
| D10 | The **rendered stimulus text is always previewable before launch** (dry run) and is recorded in the RunManifest (text in the blob store, sha in the manifest). Every span of rendered text is traceable to the library entity that owns it, and the researcher can edit any of them (section 9.2, 10.2). |
| D11 | Security surfaces are **tag-driven** (`features[]`, `surfaces[]` with CWE ids). No surface has fixed priority. Probes and detectors are selected by tag. |
| D12 | Online package installs (`network_policy=packages`) through hardened, read-only pull-through caches. After collection the platform resolves and records a lock (`resolved_lock`) for every sample; every build and re-evaluation installs under it. |
| D13 | Slot single-shot output is strict JSON. There is no healing, no merging, no renaming and no repair. A parse failure gives `output_invalid`. |
| D14 | Statistics invariants are **ported, then tightened**: the *current run* of a cell is its **first usable attempt** (never "the latest"), fingerprints are compared per (condition, unit), the confirmatory gate, multiplicity, `significance_unreachable`, per-tool latest, tool severity only. The default analysis is **intention-to-treat**: truncated and budget-stopped runs are included; excluding them is a sensitivity analysis. |
| D15 | The **pattern analysis is pre-registered** for confirmatory slot studies: detector set hash, primary features, normalizer version, cluster parameters and scope enter `prereg_hash`. Any other PatternRun on that study is labelled exploratory. |
| D16 | **Evaluation is not a budget knob.** It is `Design.evaluation` with an `eval_level` preset (default `security`: build, functional checks, probes, adherence and default SAST) in every budget tier. Whatever is not measured is labelled "not measured", never clean. |
| D17 | **Gateway protocols:** the canonical internal format stays OpenAI chat completions. Goose and Qwen Code need no gateway change. Claude Code and Codex first run in `protocol_mode=passthrough` to OpenRouter's native `/messages` and `/responses` endpoints, with policy applied on the native body. Stdlib translators (Anthropic Messages, OpenAI Responses) follow for local endpoints. Gemini CLI needs the Gemini translator plus an in-container loopback forwarder. LiteLLM is never in the request path. |
| D18 | Stats code stays pure Python, reusing `statistics/services/inference.py`. No statsmodels, numpy, scipy, pyarrow or datasketch enter the backend. Mixed models are fitted offline from the exported `features.csv`. |

---

## 1. Goals, non-goals, glossary

### 1.1 Goals

1. Make samples **research instruments**. Every factor that can affect a sample is explicit, versioned, hashed, recorded and sliceable.
2. Make **agentic whole-app generation** controllable across stack, styling, auth mode, spec prose variant, guidance, prompt templates (all editable), harness, model, sampling and resources.
3. Make **slot mode** the reliable way to measure *pattern prevalence*. It regenerates one validated part of several frozen parent apps 25-100 times each and reports per-pattern prevalence with intention-to-treat denominators and CIs, variant clusters with effective n, per-variant tests and probes with preconditions, audited Semgrep/AST detectors, and a diff against the original part.
4. Give the researcher **budget control**: hard caps per run, cell, study, user, endpoint and quota period (in USD, list-price USD or tokens), token and request caps, timeouts, concurrency, cheap-provider routing, caching, reasoning and context control, batch serving for slot calls, pilot-then-expand with gates, balanced stop rules, cache and image reuse, dry-run estimates with "fit to budget", and live spend.
5. Provide **one Study builder** that covers what batches and experiments did before. It offers a factor table, paired seeds (CRN), a per-cell dry-run preview with editable text, and confirmatory locking.
6. Keep **no silent clean results**. Every non-measurement is labelled as such.

### 1.2 Non-goals (v3)

- Whole-app single-shot generation (dropped; slot single-shot covers the single-shot need).
- Copilot or iterative healing loops of any kind.
- A fully offline package mirror. Pull-through caches plus recorded locks are used instead.
- Fine-grained OpenAPI spec-format variants (the TaskSpec schema leaves room: `prose` may gain an `openapi` variant later).
- Harness "fairness" by modifying harness internals beyond what each adapter documents. Cross-harness comparisons are comparisons of **harness bundles** (harness + its native prompt and tools).
- Cheap-model cascades as an automatic cost saver (a cascade is a different treatment; section 4.1).
- In-app mixed-model fitting (offline only; D18).
- Backwards compatibility with `agent@1/2`, `scaffold@3` or `custom@3` rows.

### 1.3 Glossary

| Term | Meaning |
|---|---|
| **Run** | One `GenerationRun` row: one queued unit that produces one sample or one evaluation. Kinds: `app_agentic`, `slot_single_shot`, `slot_agent`, `reference`. |
| **Sample** | The collected source tree of a run (its `RunFile` rows plus the workspace tar and its `resolved_lock`). The frontend page for a run is the sample page. |
| **Parent sample** | A completed, usable `app_agentic` run frozen as the host of a SlotSet. |
| **Parent pool** | The set of eligible parents for a task (`kind=app_agentic`, `usable`, outcome `tested`), from which parents are drawn by `parent_selection` (section 7.2). |
| **Part / slot** | A region of a parent chosen for regeneration: `file`, `region`, `symbol`, `endpoint` (handler symbol resolved from a route) or `component`. Stored as a `SlotTarget`. |
| **Variant** | One regeneration of a slot: a run of kind `slot_*` with a `SlotVariantResult`. |
| **Original** | The parent's own content of the slot, evaluated as a `reference` run with `slot_target` set ("variant 0"). It is a single draw: a **reference point**, never a control in comparisons. |
| **Library entity** | A versioned instrument: TaskSpecVersion, StackProfile, Starter, StyleDirective, Guidance, PromptTemplate, PromptSet, HarnessPreset, SamplingPreset, ResourcePolicy, EvalSuite or PatternDetector. |
| **Ref** | The string `"<slug>@<version>"`. A *pinned ref* is `"<slug>@<version>#<content_hash[:12]>"`. |
| **Study** | A factorial design: factors × levels, units (tasks or slot targets), replicates, seeds, evaluation level and an analysis plan. Kind is `exploratory` or `confirmatory`. Mode is `app` or `slot`. |
| **Factor / level** | A dimension (e.g. `stack_profile`) and one of its values (a pinned ref or an enum value). |
| **Condition** | One combination of levels over all *treatment* factors (prose variant included). |
| **Unit** | The blocking unit. In app mode it is a `TaskSpecVersion` (the prose variant is a factor, not part of the unit); in slot mode it is a SlotTarget. |
| **Block** | All cells sharing `(repeat_index, unit)`. Cells are claimed block by block (section 4.5). |
| **Cell** | `(condition, unit, repeat_index)`, i.e. one planned sample. It may have several runs. Its **current run** is the first attempt with `usable=True`; only when no attempt is usable is it the latest attempt (section 6.14). |
| **Plan** | An immutable, hashed resolution of a Study design into conditions, units and cells, with rendered stimuli and estimates. Launch takes a `plan_hash`. Draft resolves are ephemeral (section 6.1). |
| **Stage** | `pilot` or `main`. Pilot cells are excluded from confirmatory analysis; in exploratory studies they may be reused as main repeat 0 (section 4.6). |
| **Gateway** | The per-run LLM sidecar (`images/llm-gateway`). Local endpoints use it as an overlay on the executor's proxy (`inference/proxy_server.py`). |
| **ResourcePolicy** | The versioned budget preset (frugal, standard, generous or user-defined). The UI calls it **Budget preset**. |
| **Harness bundle** | A harness with its native system prompt, tool set and request behaviour. Results label the harness factor this way. |
| **Outcome** | The run taxonomy: `output_invalid`, `build_fail`, `start_fail`, `tested`, `infra_error` or `excluded` (section 6.12). |
| **Usable** | `True` when the run's outcome is analysable as model behaviour (section 6.13). |
| **Truncated** | Terminated by `BUDGET`, `WALL_CLOCK`, `IDLE` or `MAX_TURNS`. Truncated runs stay in the intention-to-treat analysis. |

---

## 2. Module and directory layout

### 2.1 Backend: `backend/generation/` after v3

The owner column refers to the WPs in section 12. A path owned by one WP MUST NOT be edited by another WP. Cross-WP needs go through the interfaces in sections 3-9 and the CHANGES file (12.2).

```
backend/generation/
  __init__.py  apps.py  admin.py                                  WP-01
  identity.py          canonical_json, H(), hash helpers,
                       compute_keys(IdentityInputs) -> IdentityKeys  WP-01 (the ONLY place identity keys are computed)
  blobs.py             content-addressed blob store                WP-01
  contracts/                                                       WP-01
    __init__.py        Issue, Ref, Page
    library.py         pydantic content schemas per library kind
    manifest.py        RunManifest schema
    plan.py            Design / ResolvedPlan / ResolvedCell / Estimate / EvaluationSpec
    outcome.py         Outcome, Validity, Termination enums + TERMINATION_OUTCOME table
    evaluation.py      EvalResult, CheckResult, ProbeResult, AdherenceRecord, ResolvedLock
    slot.py            SlotAddress, ContextPolicy, SlotPolicy, TargetValidation, LeakReport, SlotOutput
    resources.py       ResourcePolicyContent (all knobs, section 4), GatewayConfig, KNOB_META
    harness.py         HarnessContext, RenderedHarness, Trajectory, ObservedInputs
    issues.py          ISSUE_CODES registry (code, severity per kind, owner module; section 6.15)
    events.py          SSE event payload schemas
  models/                                                          WP-01
    __init__.py        re-exports every model
    choices.py         all TextChoices enums
    library.py         TaskSpec, TaskSpecVersion, StackProfile, Starter, StyleDirective,
                       Guidance, PromptTemplate, PromptSet, HarnessPreset, SamplingPreset,
                       ResourcePolicy, EvalSuite, PatternDetector, Blob
    study.py           Study, StudyFactor, StudyPlan, StudyCondition, StudyUnit, StudyCell,
                       StudyPromptOverride, StudySchedule, StudyPostRun
    run.py             GenerationRun, RunManifest, RunAttempt, RunFile, RunEvaluation,
                       EvalBaseline, GenerationArtifact
    slot.py            SlotSet, SlotTarget, RunSymbolIndex, SlotVariantResult,
                       PatternRun, PatternHit, VariantCluster, VariantClusterMember,
                       ClusterLabel, DetectorAudit
    spend.py           SpendLedgerEntry, SpendCap, CostPrior
  migrations/          0023_v3_* (WP-01 only; see 12.2 migration rule)  WP-01
  _legacy/             git-mv'd v2 modules + tests, never imported,     WP-02a (moves in wave 1, deletes in wave 3)
                       ignored by pytest (conftest collect_ignore) and by the WP-02 grep
  runs/
    view.py            RunView adapter (the ONLY reader API for consumers)  WP-01 (skeleton) / WP-02a (fills)
    queries.py         current_runs(), runs_for_study(), attrition()   WP-10
    api.py             /runs endpoints                                 WP-08
  library/                                                         WP-03
    service.py         create/version/archive/usage/fork_for_override, visibility
    lint.py            per-kind lint rules (replaces data_lint.py), check `stated_in` tagging
    seeding.py         seed_library from data/ (replaces generation/seeding.py)
    packages/          package format v4 import/export (ported from _legacy/services/packages)
    api.py             /library endpoints
  prompts/                                                         WP-03
    engine.py          sandboxed Jinja renderer (deterministic, with source-span tracking)
    context.py         build_render_context(...) for app and slot prompts
    api.py             /prompts/render, /prompts/diff
  study/                                                           WP-10
    design.py          Design pydantic model, validation
    expand.py          factor expansion (full / fractional), condition hashing
    seeds.py           CRN seed derivation
    order.py           block sequence (repeat-major, seeded unit order, seeded condition shuffle)
    plan.py            resolve(design, persist=False) -> ResolvedPlan; StudyPlan persistence on freeze/launch
    launch.py          launch(plan_hash), pilot expand, top-up, clone, adhoc
    results.py         port of experiment_results (cells, comparability, gate, ITT, attrition, EMMs)
    analysis_plan.py   port of services/analysis_plan.py (+ patterns, sequential design, ITT default)
    automation.py      port of experiment_automation (schedules, post-runs)
    api.py             /studies endpoints
  budget/                                                          WP-11
    policy.py          effective policy resolution, clamps, policy lint (issues), knob classification
    estimate.py        cost/time estimator (section 4.4), fit-to-budget
    priors.py          CostPrior maintenance
    ledger.py          reservations, usage, settlement (idempotent)
    stop_rules.py      study stop/pause evaluation, pilot gates
    gateway_config.py  ResourcePolicy -> gateway GATEWAY_CONFIG budget/routing sections
    live.py            live spend polling + SSE
    constants.py       r0, harness overheads, r_avg (calibrated from priors)
    api.py             /budget endpoints
  harnesses/                                                       WP-07
    base.py            HarnessAdapter ABC (section 5)
    registry.py
    opencode.py  mini_swe_agent.py  aider.py
    claude_code.py  codex.py  goose.py  qwen_code.py  gemini_cli.py
    trajectory/        common.py + one parser per harness (ported from _legacy/services/agent/trajectory.py)
    captured/          captured native system prompts / tool specs per harness version
    api.py             /harnesses endpoints
  agentic/                                                         WP-08
    runner.py          stage entry point for kind app_agentic (section 6)
    sandbox.py         port of _legacy/services/agent/sandbox.py
    workspace.py       seed + collect (anchored excludes, origins)
    collector.py       RunFile classification, LOC, excludes
    manifest.py        build_manifest(run, resolved_cell) -> RunManifest (copies keys; never recomputes)
    outcome.py         outcome + termination classification (TERMINATION_OUTCOME)
    validity.py        usable / validity notes
    images.py          image build/pin/digest resolution
    dispatch.py        GenerationRun -> stage function dispatcher (replaces orchestrator)
  build/                                                           WP-05
    context.py         prepare_build_dir(run, dest) -- the seam used by runtime/services/sample_builds.py
    starters.py        starter composition (backend fragment ⊕ frontend fragment ⊕ Dockerfile template)
    dockerfiles.py     Dockerfile templates, base image digest pinning, allowed bases
  evaluation/                                                      WP-09
    suite.py           EvalSuite v2 orchestration (lock → build → start → tester → adherence)
    lock.py            platform lock step (resolved_lock), constraint-pinned installs
    reuse.py           reuse RunEvaluation by (tree_hash, suite, selection)
    acceptance.py      TaskSpec → AcceptancePlan compiler
    baseline.py        starter baselines, oracle validation (3×), probe mutants
    conformance/       tester conformance kit: per-framework oracle registry + runner
    retest.py          random 5% re-evaluation (test-retest agreement)
    adherence.py       adherence-checker driver
    levels.py          eval_level → stage toggles (section 4.8)
    metrics.py         RunMetrics (replaces services/metrics.py)
    api.py             /eval endpoints
  slot/                                                            WP-12
    index/             symbol index: treesitter.py, routes.py (endpoint→handler), components.py
    parents.py         parent pool queries and parent_selection (random | all | manual)
    selection.py       address resolution, SlotTarget creation, allowlists, cross-parent matching
    validate.py        target validation (original 3×, holed tree, discriminating checks)
    leak.py            leak scan (8-token shingles of the original vs context and workspace)
    freeze.py          SlotSet creation (freeze parent)
    context.py         resolve_context(target, context_policy) -> ResolvedContext (per plan, hashed)
    prompt.py          slot prompt/brief construction
    executors/single_shot.py
    executors/batch_submitter.py
    executors/restricted_agent.py
    assemble.py        splice variant into parent tree (slot RunFile rows only)
    evaluate.py        slot tests (restricted suite + probes; full | overlay | static_only)
    api.py             /slots endpoints
  patterns/                                                        WP-13
    normalize.py  cluster.py  detectors.py  features.py  prevalence.py  export.py
    live.py            per-variant provisional scoring of primary features (eval lane)
    manual.py          manual coding engine (cluster labels)
    audit.py           detector precision/NPV audit samples (via human_eval)
    api.py             /patterns endpoints
  services/            retained shared infrastructure only:          WP-02a (re-point only)
    openrouter_client.py  openrouter_stats.py  call_telemetry.py
    llm_params.py  sampling.py  code_profile.py
  api/
    __init__.py        router assembly (imports each area's api.py in a fixed order)  WP-01
    telemetry.py  trajectory.py  variability.py  job_insights.py   retained, re-pointed  WP-02a
  images/
    agent-base/  agent-opencode/  agent-mini-swe-agent/  agent-aider/                 WP-07
    agent-claude-code/  agent-codex/  agent-goose/  agent-qwen-code/  agent-gemini-cli/  WP-07
    llm-gateway/  (+ protocols/ subpackage)  llm-gateway-local/                       WP-06
    eval-tester/       (renamed from conformance-tester)                             WP-09
    adherence-checker/                                                               WP-09
    frontend-validator/  (kept; used by collector static checks)                     WP-08
  data/                seed content (section 11)                   WP-04; except stacks/, starters/, prompts/stack-*,
                                                                   prompts/run-contract-* (WP-05), detectors/ (WP-13),
                                                                   resource_policies/ (WP-11)
  management/commands/
    seed_library.py  lint_library.py                                WP-03
    wipe_generation_v2.py                                           WP-00 (raw SQL; ships on main first)
    oracle_validate.py  conformance_kit.py                          WP-09
    refresh_cost_priors.py                                          WP-11
  testing/             factories, fakes, golden_library (12.2)      WP-01
  tests/
    factories.py       thin re-export of generation.testing.factories (keeps ~75 consumer imports stable)  WP-01
    wp01_foundation/, wp03_library/, ...  one sub-directory per WP
```

There is **no** `pattern-tools` image. Semgrep detectors run through the analyzer workspace's existing Semgrep install via `backend/analysis/services/rulepack.py` (owned by WP-13); tree-sitter, regex, probe and manual detectors run in-process.

### 2.2 Deleted or moved (WP-02a owns backend deletion; frontend WPs own their routes)

`backend/generation/`:
- `models.py` (replaced by the `models/` package), `seeding.py`, `tasks.py`.
- `services/`: `backend_scanner.py`, `code_parser.py`, `copilot_validation.py`, `generation_validation.py`, `output_guard.py`, `prompt_renderer.py`, `profile_resolver.py`, `orchestrator.py`, `generation_service.py`, `batches.py`, `job_cloning.py`, `job_listing.py`, `conformance.py`, `data_lint.py`, `spec_models.py`, `versioning.py`, `presets.py`, `result_writer.py`, `dispatcher.py`, `metrics.py`, `evaluation/contract.py`, `evaluation/runner.py`. (The other session's sample-build logic in `evaluation/runner.py` is ported into `evaluation/suite.py` by WP-09; section 12.4.)
- **Moved with `git mv` into `generation/_legacy/`** (blame kept; never imported; wave-2 owners port from it; deleted in wave 3): `services/{experiments,experiment_results,analysis_plan,experiment_automation}.py` (→ WP-10), `services/agent/*` (→ WP-07, WP-08), `services/packages/*` (→ WP-03), and their tests `test_experiment*.py`, `test_agent_mode.py`, `test_trajectory.py`, `test_template_packages.py`, `test_analysis_plan.py`, `test_schedules.py`, `test_post_run.py`, `test_gateway_local_overlay.py`.
- `api/schema.py`, `api/schemas_experiments.py`, and `api/views/{custom,profiles,templates,presets,experiments,jobs}.py`. The retained views move from `api/views/` to `api/`.
- `data/`: `blocks/`, `prompts/`, `requirements/` (copied to `_legacy/data/requirements/` as WP-04 source material, deleted in wave 3), `agent/stacks.yaml`, `agent/brief.md.jinja2`, `presets/`, `packages/`.
- `images/conformance-tester/` (it becomes `images/eval-tester/`).
- `management/commands/`: `seed_generation_templates.py`, `lint_generation_data.py`, `backfill_agent_trajectories.py`, `backfill_call_telemetry.py`.
- Every test under `generation/tests/` that pins deleted behaviour, including `test_default_run_golden.py` (it is replaced by new golden tests, section 13.3). `generation/tests/factories.py` stays as a re-export shim (WP-01).

Models removed: `AppRequirementTemplate`, `ContentBlock`, `GenerationProfile`, `RunPreset` (split into typed presets), `GenerationBatch`, `CopilotIteration`, and `runtime.Stack`.

`backend/runtime/`:
- `scaffolding/` (whole directory, including `manifest.json` and `lucide_icons.json`).
- `services/scaffolding.py`, `services/dockerfile_gen.py`, `services/stack_validation.py`, `seeding.py`.
- The `Stack` model (WP-01, in `runtime/models.py`) and its API views (WP-02a).
- Tests: `test_scaffolding.py`, `test_healing.py`, `test_stack_seeding.py`, `test_user_stacks.py`.
- **New:** `runtime/services/build_context.py` (WP-05), a shim re-exporting `backend.generation.build.context.prepare_build_dir` and `BuildContextReport`, so that `sample_builds.py` changes by one import line (section 6.10).

`backend/statistics/`: the `management/commands/healing_ablation.py` command, `study.healing_impact` and `_healing_report` (WP-02b).

`config/settings/base.py` (WP-01): `GENERATION_DEFAULT_STACK`, `AGENT_MAX_LLM_REQUESTS`, `AGENT_WALL_CLOCK_SECONDS`, `AGENT_MAX_TOTAL_TOKENS`, `AGENT_MAX_COST_USD`, `AGENT_SAMPLING_POLICY` and `AGENT_DEFAULT_PROVIDER_ROUTING` are removed. Defaults now come from seeded ResourcePolicy and SamplingPreset rows, never from env. Only the machine limits stay: `AGENT_SANDBOX_MEM_LIMIT_MAX`, `AGENT_SANDBOX_NANO_CPUS_MAX`, `AGENT_MAX_CONCURRENT_RUNS` and the harness versions (moved to the settings component).

Frontend:
- `routes/(app)/sample-generator/**`, `routes/(app)/experiments/**` and `routes/(app)/templates/**` are deleted (WP-15, WP-15, WP-14). Each old path gets a `+page.ts` redirect for one release: `/sample-generator` → `/studies/new`, `/experiments` → `/studies`, `/templates` → `/library`. `/applications/[id]` redirects to `/samples/[id]` (WP-16).
- `lib/components/generator/composer/**` and `ProfilePicker.svelte`, `StackSelect.svelte`, `PresetPicker.svelte` are deleted (WP-15).
- `lib/components/templates/{Block*,Profile*,Stacks*,StackEditor}.svelte` are deleted. `library.svelte.ts` is rewritten (WP-14).
- `lib/components/experiments/{ExperimentComposer.svelte,composer.ts,composer.test.ts}` are deleted. The monitor, study, tabs, cells and variability components move to `lib/components/studies/` (section 10.8; WP-15).
- `lib/api/generation.ts` and `lib/api/experiments.ts` are replaced by the `lib/api/v3/*` modules (section 10.6). They are deleted in wave 3 by WP-17 once nothing imports them.

### 2.3 Other apps touched

| App / file | Change | Owner |
|---|---|---|
| `{analysis,inference,runtime,reports,human_eval}/models.py` + their v3 migrations | The six string FK targets (`analysis/models.py:318`, `inference/models.py:241`, `runtime/models.py:25`, `reports/models.py:84`, `human_eval/models.py:104,156`) become `"generation.GenerationRun"` / `"generation.Study"` (system check `fields.E300` fails otherwise). `AnalysisRun` gains `code_scope` and `code_scope_paths_sha`. `runtime.Stack` is deleted. `LLMEndpoint` gains `cost_basis`. | WP-01 |
| `analysis` (rest) | `AnalysisRun.get_code_for_analysis` calls `run_view(run).code_files("app")`: analysis **always** runs on app scope (dependency scanners need manifests and lockfiles; SAST needs starter context to resolve imports). Each finding is tagged with its file's `RunFile.origin`; `model`/`slot` attribution happens at stats time. A tool with no applicable in-scope files reports `not_measured`, never 0. `tool_applicable` is recorded per (run, tool). `context.py` reads `RunView`. | WP-02b |
| `analysis/services/rulepack.py` (new) | `run_semgrep_rules(tree_dir, rule_files) -> SemgrepRaw` using the analyzer-workspace Semgrep (shared parsers in `parsers.py`, read-only use). | WP-13 |
| `execution` | Raw SQL is rewritten, not renamed: `scheduler.py:57-74` (`j.app_requirement_id`, `j.batch_id`, `x.max_concurrency`, `x.execution_options`, partition on `experiment_id`), `capacity.py:305-310` (`resolved_bundle->'endpoint'` → `pinned_served_name`/`pinned_digest` columns), `sequence.py:60-72` (`execution_options.max_resident_models`, policy `auto`), `api.py:61` (lane from `run.kind`), `transitions.py:96,615-618`. New tables/columns: `generation_run`, `generation_study`, `study_id`, `cell_id`, `task_version_id`, `slot_target_id`; `batch_id` is dropped. `post_run_slots` bound becomes 0-2 (`api_schemas.py:148`). | WP-02a (mechanical, wave 1) → WP-11 (budget, blocks, concurrency) |
| `inference` | `proxy_server.py:76-148,329-363` re-pointed to `GenerationRun` and the manifest's frozen endpoint (WP-02a, wave 1); its budget and policy parts then move to WP-06. `api/views.py` changes `ExperimentCondition` to `StudyCondition`; `chat.py` re-pointed. | WP-02a, then WP-06 |
| `human_eval` (rest) | `snapshot.py`, `pool.py`, `context.py` and `portal_work.py` read `RunView`. `ADMIN_PW_DEFAULT` is gone: the portal reads the app's actual admin credential from the run's runtime credentials record (section 8.6). | WP-02b |
| `human_eval/services/detector_audit.py` (new) | Creates detector-audit evaluation tasks (section 7.9 step 4). | WP-13 |
| `statistics` | `study.py`, `variability.py`, `similarity.py`, `dependencies.py`, `aggregations.py`, `accounting.py`, `trend.py` and `management/commands/export_study_tables.py` (`--experiment`/`--batch` → `--study`) read `RunView` and `current_runs`. Bulk stats may use `.values()` on the documented `GenerationRun.metrics` contract keys (`variability.py:669-677`, `similarity.py:277-283`) and the bulk reader `run_views_code(runs, scope)`. Healing code is removed. | WP-02b |
| `reports` | `loc.py` gains `source_loc(path, text) -> int` (built on `is_test_path` / `is_source_file`); `generators.py` and `study_report.py` change `Experiment` to `Study` and read `RunView`. | WP-02b |
| `export` | `jobs.py`, `findings.py` and `review_sample.py` read `RunView`. Exports include `manifest.json`, `files.csv` (path, origin, sha, loc) and `resolved_lock.json`. | WP-02b |
| `runtime` (rest) | `ContainerInstance.generation_job` FK points to GenerationRun (automatic with RenameModel). `container_service.py` and `smoke.py` use `build/context.py` through the shim. The `Stack` API is removed. | WP-02a |
| `realtime` | Channels become `run:<uuid>` and `study:<uuid>` (replacing `generation:` and `experiment:`); `authz.py` updated. | WP-02b |
| `integrations` | `services/webhooks.py:74-98,277-279` dispatch labels become `"generation.GenerationRun"`/`"generation.Study"`, with a test that every label resolves through `apps.get_model`. `mcp/tools.py:53-150`: the curated `operation_id`s are re-curated against the new ninja operation ids published by WP-08/WP-10 in the CHANGES file; MCP experiment tools become study tools (list, get, resolve, launch). | WP-02b |
| `llm_models` | Mechanical rename plus `RunView`, including `management/commands/import_external_covariates.py`. | WP-02b |
| `rankings`, `system`, `users`, `automation` | Mechanical rename plus `RunView`. `automation/engine/dispatchers.py` starts runs through `study.launch.launch_adhoc` (section 9.4). | WP-02b |
| `backend/docs/services.py:27,37` | Doc slug map lists the new docs (STUDIES, SLOT_MODE, BUDGET, HARNESSES, GATEWAY, EVALUATION, PATTERNS, LIBRARY, STACKS). | WP-10 |
| `compose/{local,production}/django/Dockerfile` | New Python deps (tree-sitter, tree-sitter-language-pack, zstandard). | WP-01 |

---

## 3. Data model

Conventions:
- `uuid` = `UUIDField(primary_key=True, default=uuid4)`; `bigauto` = default BigAutoField.
- `FK(X, PROTECT)` unless noted. Library versions are **never deleted** while referenced, so PROTECT is safe.
- `sha` = `CharField(max_length=64)` holding a lowercase hex sha256. `ref` = `CharField(max_length=140)` of the form `slug@version`.
- All JSON fields holding a typed payload are validated on save by a pydantic model in `generation/contracts/*` (`model.full_clean()` calls `Contract.model_validate`). Unknown keys are rejected (`extra="forbid"`).
- `created_by = FK(User, SET_NULL, null)` and `created_at = auto_now_add` exist on every top-level model and are not repeated below.

### 3.1 Identity and hashing (`generation/identity.py`, WP-01)

```python
def canonical_json(obj) -> bytes
    # json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    # Pre-normalization (recursive): str -> unicodedata.normalize("NFC") and "\r\n"/"\r" -> "\n";
    # floats must be finite; tuples -> lists; dict keys must be str; None kept.
def H(obj) -> str                       # sha256(canonical_json(obj)).hexdigest()
def text_sha(text: str) -> str          # sha256 of the NFC/LF-normalized UTF-8 text (no JSON quoting)
def bytes_sha(b: bytes) -> str
def tree_hash(entries: Iterable[tuple[path, mode:int, blob_sha]]) -> str
    # H(sorted([[path, mode & 0o111 != 0, blob_sha], ...]))   # exec bit only; paths are POSIX, NFC
def pinned_ref(slug, version, content_hash) -> str   # f"{slug}@{version}#{content_hash[:12]}"

@dataclass(frozen=True)
class IdentityInputs:      # everything the keys depend on; built by study/plan.py from a ResolvedCell
    protocol: str; kind: str
    task: dict | None            # {"structured_hash", "variant", "prose_sha"} (app)
    target: dict | None          # {"target_hash"} (slot)
    treatment: dict              # the treatment_hash input below, already pinned
    stimuli: list[tuple[str, str]]   # [(role, text_sha)]
    model: dict; sampling_hash: str; policy_treatment_view: dict; images: dict; seed: int
@dataclass(frozen=True)
class IdentityKeys: task_hash: str; treatment_hash: str; prompt_hash: str; generation_policy_hash: str
    config_fingerprint: str; run_fingerprint: str
def compute_keys(inputs: IdentityInputs) -> IdentityKeys   # pure; the ONLY implementation of the table below
```

`compute_keys` is the single implementation. `study/plan.py` calls it while resolving; `agentic/manifest.py` copies the keys from the `ResolvedCell` into the manifest and MUST NOT recompute them. A hash-algebra golden test with fixed `IdentityInputs` (no renderer, no library) lives in WP-01 (section 13.3).

**Library content hash.** `content_hash = H({"kind": <kind>, "schema": <schema_version>, "content": <content>})`. Slug, name, description, version, ids and owner are **never** hashed.

**Identity keys.** Every one is frozen in the RunManifest and copied to indexed columns on GenerationRun.

| Key | Definition |
|---|---|
| `task_hash` (app) | `H({"protocol":"app@1","task":tv.structured_hash,"variant":v,"prose":tv.prose_hashes[v]})`. The variant stays in `task_hash` (the stimulus differs), but **not** in the unit key or the seed. |
| `task_hash` (slot) | `H({"protocol":"slot@1","target":slot_target.target_hash})` |
| `treatment_hash` | `H({"protocol", "executor": kind, "stack": stack.content_hash, "starter": starter.content_hash or None, "style": style.content_hash, "auth_mode": enum, "guidance": guidance.content_hash, "prompt_set": prompt_set.content_hash, "harness_preset": hp.content_hash or None, "gateway": {"front_protocol", "protocol_mode"} or None, "allowed_bases_sha": sha or None, "slot_policy": {hole_mode, keep_docstring, context_policy_hash, resolved_context_sha, include_tests, edit_policy} or None})` |
| `prompt_hash` | `H(sorted([[role, text_sha(rendered)] for each rendered stimulus]))`. Rendered stimuli contain no budget, no seed and no ids. |
| `generation_policy_hash` | `H(policy.treatment_view())`: only the ResourcePolicy knobs of class **C** or **R** (section 4.2), after clamping. |
| `config_fingerprint` | `H({"protocol","task_hash","treatment_hash","prompt_hash","model": {"slug", "endpoint_kind", "pinned_digest" or None}, "sampling": sampling.content_hash, "policy": generation_policy_hash, "images": {"harness": digest or None, "gateway": digest, "starter_base": [digests]}})`. The EvalSuite is **not** part of it: evaluation results are compared per suite instead (issue `mixed_eval_suite`). |
| `run_fingerprint` | `H({"config": config_fingerprint, "seed": seed})` |
| `condition_hash` | `H(sorted condition level pinned refs)`, prose variant included, units excluded |
| `design_hash` | `H(Design.model_dump(mode="json"))` |
| `plan_hash` | `H({"design": design_hash, "conditions": [...condition_hash], "units": [...unit_key], "cells": [[c_idx, u_idx, r, stage, seed, sequence], ...], "prompt_hashes": {cell_key: prompt_hash}})` |
| `prereg_hash` | `H({"plan": plan_hash, "analysis_plan": analysis_plan (incl. "patterns" and "sequential"), "stop_rules": policy.study_section, "pilot_gates": policy.pilot.gates, "evaluation": design.evaluation})` |
| `target_hash` | `H({"parent_tree": slot_set.frozen_tree_hash, "address": address.identity(), "original": original_sha, "allowlist": sorted, "interface": interface})`. Hole mode and context policy are **not** in it: they are condition-level (section 7.4). |
| `normalized_sha` (variant) | `text_sha(normalize(code, language, normalizer_version))` (section 7.9) |

Golden tests (section 13.3) pin every one of these. Changing an algorithm requires a protocol bump (`app@2`, `slot@2`).

### 3.2 Blob store (`generation/blobs.py`, model `Blob`, WP-01)

Files live on disk at `settings.GENERATION_BLOBS_ROOT/sha256/<aa>/<bb>/<sha>.zst`. `GENERATION_BLOBS_ROOT` defaults to `MEDIA_ROOT/blobs` and is read from settings at call time (tests override `MEDIA_ROOT` per test). Compression is zstd (`zstandard`, added by WP-01); the codec is recorded per blob.

| Field | Type |
|---|---|
| `sha256` | sha, PK |
| `size` | PositiveBigInteger (uncompressed) |
| `codec` | Char(8): `zst` or `gz` |
| `media_type` | Char(80) |
| `created_at` | DateTime |

API: `put_bytes(b, media_type) -> sha`, `put_text(s) -> sha` (normalized as `text_sha`), `get_bytes(sha)`, `get_text(sha)`, `exists(sha)`, `open_stream(sha)`. Writes are idempotent and atomic (`tmp` file plus `os.replace`). GC (`gc_blobs --dry-run`) deletes unreferenced blobs older than 7 days. References are found by scanning the known sha columns, which each model lists in `BLOB_FIELDS`.

### 3.3 Library models (`models/library.py`, WP-01)

**Abstract `VersionedEntity`**

| Field | Type / constraint |
|---|---|
| `id` | bigauto |
| `slug` | Slug(100) |
| `version` | PositiveInteger |
| `name` | Char(200) |
| `description` | Text, blank |
| `schema_version` | PositiveSmallInteger |
| `content` | JSON (validated by `contracts.library.<Kind>Content`) |
| `content_hash` | sha, `db_index` |
| `derived_from` | FK(self, SET_NULL, null): the version this one was edited from |
| `is_system` | Bool, default False |
| `is_archived` | Bool, default False |
| `visibility` | Char(8): `private`, `shared` or `system` (system ⇔ is_system) |

`Meta`: `UniqueConstraint(slug, version)`, `Index(slug, -version)`, and `CheckConstraint(content_hash != "")`.

`save()` raises `ImmutableVersionError` when `pk` is set and `content`, `content_hash`, `schema_version`, `slug` or `version` changed. Only `name`, `description`, `is_archived` and `visibility` are mutable.

`KIND: ClassVar[str]` names the kind, `ref` is a property, and `objects.latest(slug)` returns the latest non-archived version.

**Concrete kinds**

| Model | KIND | Extra columns | Content contract (section 3.4) |
|---|---|---|---|
| `StackProfile` | `stack` | `backend_framework` Char(24, idx), `frontend_framework` Char(16, idx), `database` Char(16) | `StackProfileContent` |
| `Starter` | `starter` | `stack_profile` FK(StackProfile), `level` Char(8) (`bare`, `minimal` or `full`), `tree_hash` sha | `StarterContent` |
| `StyleDirective` | `style` | `system` Char(24, idx) | `StyleContent` |
| `Guidance` | `guidance` | `security_hint` Char(16) | `GuidanceContent` |
| `PromptTemplate` | `prompt` | `role` Char(24, idx) | `PromptTemplateContent` |
| `PromptSet` | `prompt_set` | `mode` Char(8) (`app` or `slot`) | `PromptSetContent` |
| `HarnessPreset` | `harness_preset` | `harness` Char(32, idx), `harness_version` Char(32) | `HarnessPresetContent` |
| `SamplingPreset` | `sampling` | (none) | `SamplingContent` |
| `ResourcePolicy` | `resource_policy` | `tier` Char(12) (`frugal`, `standard`, `generous` or `custom`) | `ResourcePolicyContent` (section 4); UI name **Budget preset** |
| `EvalSuite` | `eval_suite` | `tester_version` PositiveSmallInteger | `EvalSuiteContent` |
| `PatternDetector` | `detector` | `engine` Char(16) (`semgrep`, `ts_query`, `regex`, `probe` or `manual`), `feature_key` Slug(100, idx) | `DetectorContent` |

**TaskSpec (header) and TaskSpecVersion.** Tasks are the blocking unit and carry per-version oracle status, so they get a header row.

`TaskSpec` (header):

| Field | Type |
|---|---|
| `id` | bigauto |
| `slug` | Slug(100), unique |
| `is_system` | Bool |
| `is_archived` | Bool |
| `visibility` | Char(8) |
| `latest_version` | PositiveInteger |

`TaskSpecVersion`:

| Field | Type |
|---|---|
| `id` | bigauto |
| `task` | FK(TaskSpec, PROTECT, related `versions`) |
| `version` | PositiveInteger |
| `name` | Char(200) |
| `schema_version` | =4 |
| `content` | `TaskSpecContent` |
| `content_hash` | sha |
| `structured_hash` | sha = H(content minus `prose`) |
| `prose_hashes` | JSON `{variant: text_sha}` |
| `derived` | JSON `TaskDerived`, computed and not hashed: `endpoint_count`, `entity_count`, `field_count`, `relation_count`, `features`, `surfaces`, `difficulty_score` (float), `size_bucket` (`S`, `M`, `L` or `XL`) and `check_statedness` (`{check_id: [variants]}`, section 8.2) |
| `lint` | JSON: `{status: ok\|warn\|error, issues: [...] , linter_version}` |
| `oracle` | JSON `{<stack_ref>: {status: pass\|fail\|missing\|error, run_id, eval_suite_ref, at}}`. It is written only by `evaluation/baseline.py`. |
| `derived_from` | FK(self, SET_NULL, null) |

Constraints: `Unique(task, version)` and `Index(task, -version)`. The same immutability rule as `VersionedEntity` applies; `lint`, `oracle` and `derived` stay mutable.

### 3.4 Content contracts (`contracts/library.py`, WP-01)

These schemas are the **inter-WP contract** for seed data (WP-04, WP-05), the library API (WP-03), prompt rendering (WP-03), evaluation (WP-09) and the UI (WP-14). The types below are pydantic v2 (`Literal`, `list`, `dict`), and every model has `extra="forbid"`.

```python
# ---------- TaskSpec ----------
FieldType = Literal["string","text","int","float","bool","date","datetime","enum","ref","email","url","json","file"]
class EntityField(BaseModel): name: str; type: FieldType; required: bool = False; unique: bool = False
    max_length: int | None = None; min: float | None = None; max: float | None = None
    enum: list[str] | None = None; ref: str | None = None; default: Any = None; secret: bool = False   # secret: password-like, never echoed
class Entity(BaseModel): name: str; fields: list[EntityField]; owned_by_user: bool = False; soft_delete: bool = False
Auth = Literal["public","user","owner","admin"]
class QueryParam(BaseModel): name: str; type: FieldType; required: bool = False; enum: list[str] | None = None
    default: Any = None; max: float | None = None; semantics: Literal["filter","search","sort","page","page_size","other"] = "other"
class Endpoint(BaseModel):
    id: str                       # stable dotted id, e.g. "todos.update" (checks, slots and probes refer to it)
    method: Literal["GET","POST","PUT","PATCH","DELETE"]; path: str   # "/api/todos/{id}"
    auth: Auth; owner_entity: str | None = None     # required when auth == "owner"
    success_status: int = 200
    query: list[QueryParam] = []
    request_schema: dict | None = None; response_schema: dict | None = None   # JSON Schema 2020-12 subset
    content_type: Literal["json","multipart","form","none"] = "json"
    errors: list[int] = []        # statuses the spec promises (400, 401, 403, 404, 409, 413, 422, 429)
    tags: list[str] = []          # free tags; feature/surface tags are task-level
    realtime: Literal["none","websocket","sse"] = "none"
class UiRequirement(BaseModel): id: str; text: str; endpoints: list[str] = []
class AuthFlow(BaseModel):        # used only by auth_mode spec_defined
    register: str | None; login: str; logout: str | None = None   # endpoint ids
    credential: Literal["bearer","cookie"]; token_json_path: str | None = None
    csrf: Literal["none","double_submit_cookie","header_from_endpoint"] = "none"; csrf_endpoint: str | None = None
class TaskSpecContent(BaseModel):
    title: str; summary: str; category: str
    entities: list[Entity]; endpoints: list[Endpoint]; ui: list[UiRequirement] = []
    has_frontend: bool = True
    features: list[str]           # closed vocabulary, section 11.3 (lint)
    surfaces: list[str]           # "cwe-89", "cwe-79", ... (lint: must be CWE ids or vocabulary)
    auth_flow: AuthFlow | None = None
    seed_data: dict[str, list[dict]] = {}     # rows the app must expose on start (rare)
    acceptance: list[CheckSpec] = []          # hand-written extra checks (section 8.2)
    probes: list[ProbeSpec] = []              # CWE probes (section 8.3)
    prose: dict[Literal["minimal","standard","exhaustive"], str]   # markdown; all three required for system tasks
# ---------- Stack / starter ----------
class StackProfileContent(BaseModel):
    choice_mode: Literal["fixed","free"]
    backend: {"language": Literal["python","javascript","typescript","go","java","free"],
              "framework": Literal["flask","fastapi","django","express","go-stdlib","gin","spring-boot","free"],
              "runtime": str}             # "python3.13", "node24", "go1.25", "temurin21", or "free"
    frontend: {"framework": Literal["react","vue","svelte","none","free"], "language": Literal["ts","js","free","none"],
               "build_tool": Literal["vite","none","free"]}
    database: Literal["sqlite","postgres","free"]
    run_contract: {"port": 8000, "api_prefix": "/api", "health_path": "/api/health",
                   "static_serving": Literal["backend","none"], "data_dir": "./data", "start": "dockerfile"}
    brief_fragment: str           # PromptTemplate ref (role fragment) describing the stack to the model
    adherence: {"backend_markers": [...], "frontend_markers": [...], "db_markers": [...]}   # checker hints
    resources: {"min_memory": str, "min_cpus": float} = {"min_memory": "2g", "min_cpus": 1}   # study sandbox = max over profiles (4.2)
class StarterContent(BaseModel):
    stack_profile: str            # pinned ref
    level: Literal["bare","minimal","full"]
    files: dict[str, {"blob": sha, "mode": int}]     # relative POSIX paths
    dockerfile: str               # path inside files (always "Dockerfile")
    base_images: list[{"ref": str, "digest": str}]   # every FROM, digest-pinned
    lockfiles: list[str]          # paths inside files
    install: list[str]            # informational: commands the Dockerfile runs
    composition: {"backend_fragment": str, "frontend_fragment": str | None, "dockerfile_template": str}  # provenance
    forbidden_content_check: {"passed": bool, "linter_version": int}  # no "LLM:" comments, no security libs (lint)
# ---------- Style / guidance ----------
class StyleContent(BaseModel):
    system: Literal["tailwind","plain-css","css-modules","component-library","model-choice"]
    library: {"name": str, "packages": list[str], "frameworks": list[str]} | None
    tone: str | None              # tone preset slug (minimal, playful, corporate, editorial, brutalist, dark, ...)
    tone_text: str | None
    accessibility: Literal["unspecified","wcag-aa"] = "unspecified"
    fragment: str                 # PromptTemplate ref (role fragment)
    adherence: {"required_markers": [...], "forbidden_markers": [...], "class_share_min": float | None}
    compatible_frontends: list[str]   # lint: plan resolution rejects incompatible (stack, style) pairs
class GuidanceContent(BaseModel):
    security_hint: Literal["none","generic","cwe_specific"]
    cwe_source: Literal["task_surfaces","explicit"] = "task_surfaces"; cwes: list[str] = []
    library_prescriptions: list[{"purpose": str, "library": str}] = []
    fragment: str | None          # PromptTemplate ref; None for "none"
# ---------- Prompts ----------
Role = Literal["brief","system","user","slot_single_shot_system","slot_single_shot_user","slot_agent_brief","fragment"]
class PromptTemplateContent(BaseModel):
    role: Role; engine: Literal["jinja2-sandbox@1"]; template: str
    variables: list[str]          # declared top-level context vars it may use (lint: undeclared -> error)
    modes: list[Literal["app","slot"]]
class PromptSetContent(BaseModel):
    mode: Literal["app","slot"]
    roles: dict[Role, str]        # role -> pinned PromptTemplate ref; app requires "brief"; slot single-shot requires
                                  # both slot_single_shot_* roles; slot agent requires "slot_agent_brief"
    system_prompt_mode: Literal["harness_native","append","override"] = "harness_native"   # app mode
# ---------- Run presets ----------
class HarnessPresetContent(BaseModel):
    harness: str; harness_version: str
    options: dict[str, Any] = {}      # validated by adapter.option_fields
    controlled: bool = False          # equalised profile (section 5.4)
    command_timeout_s: int | None = None
    tool_policy: {"deny": list[str], "allow": list[str] | None} = {"deny": [], "allow": None}
    max_turns: int | None = None      # harness-native turn cap where supported (else gateway max_llm_requests)
    subagents: bool = False           # Claude Code Task/Agent, Goose subagents (off by default)
    pin_system_prompt: bool = False   # use the captured default prompt file (controlled presets)
class SamplingContent(BaseModel):
    policy: Literal["provider_default","explicit"]
    params: {"temperature": float | None, "top_p": float | None, "top_k": int | None,
             "min_p": float | None, "frequency_penalty": float | None, "presence_penalty": float | None}
    forward_seed: bool = True
class EvalSuiteContent(BaseModel):
    tester: {"version": int, "image_digest": str}
    acceptance_compiler_version: int
    adherence_checker: {"version": int, "image_digest": str}
    probes: Literal["none","task","all_applicable"] = "task"
    baseline: Literal["starter","none"] = "starter"
    timeouts: {"build_s": 900, "start_s": 90, "tester_s": 600}
    flaky_checks: list[str] = []      # written by oracle_validate as a new suite version (8.5)
    tester_rules_version: int = 1; probe_rules_version: int = 1
class DetectorContent(BaseModel):
    engine: Literal["semgrep","ts_query","regex","probe","manual"]
    languages: list[str]; rule: str           # semgrep YAML, tree-sitter query, or regex
    tool: {"name": str, "version": str}        # e.g. the analyzer-workspace semgrep version (recorded)
    feature_key: str; polarity: Literal["insecure","secure","neutral"]
    tool_severity_field: str | None            # e.g. "severity" for semgrep: the rule's own label is copied verbatim
    cwe: list[str] = []; description: str
    scopes: list[Literal["span","file"]] = ["span","file"]; taint: bool = False   # taint rules always run on the whole file
```

### 3.5 AuthMode

`choices.AuthMode = TextChoices("contract_bearer", "cookie_session", "spec_defined")`. Each mode is rendered by the fragment `auth-contract-<mode>@v` that the PromptSet's context selects (section 9.2), and each has a tester driver (section 8.4). The rule `spec_defined` requires `task.auth_flow` is enforced at plan resolution: the issue `auth_flow_missing` makes the cell invalid.

### 3.6 Study models (`models/study.py`, WP-01)

**`Study`** (formerly Experiment; `RenameModel`, `db_table="generation_study"`)

| Field | Type |
|---|---|
| `id` | uuid |
| `name` | Char(200) |
| `slug` | Slug(200) |
| `description` | Text |
| `hypothesis` | Text, blank |
| `kind` | Char(14): `exploratory` or `confirmatory` |
| `mode` | Char(6): `app` or `slot` |
| `status` | Char(12): `draft`, `planned`, `piloting`, `running`, `paused`, `completed`, `cancelled` or `stopped` |
| `design` | JSON `contracts.plan.Design` (the editable draft; section 9.4) |
| `design_hash` | sha |
| `resource_policy` | FK(ResourcePolicy): the study-level policy; conditions may vary it through factor `resource_policy` only for class C/R sections |
| `base_seed` | PositiveBigInteger |
| `repeats` | PositiveSmallInteger (1..200) |
| `scheduling` | Char(12): `auto` (default; residency-aware blocking for local endpoints, interleaved otherwise), `interleaved` or `blocked` |
| `order_seed` | PositiveBigInteger |
| `priority` | SmallInteger, default 0 |
| `max_parallel` | PositiveSmallInteger, null: filled from `concurrency.study_max_parallel` at launch (scheduler reads it; replaces `Experiment.max_concurrency`) |
| `execution_options` | JSON `{max_resident_models, per_model, per_endpoint, per_provider, rpm_per_model, eval_parallel, warmup}`: filled from the effective policy at launch (replaces `Experiment.execution_options`) |
| `budget_basis` | Char(6): `usd` or `tokens` (section 4.2) |
| `cap_basis` | Char(6): `billed` or `list` |
| `on_budget_exhausted` | Char(14): `pause`, `stop_balanced` (default) or `stop` |
| `analysis_plan` | JSON |
| `analysis_plan_hash` | sha, blank |
| `prereg_hash` | sha, blank |
| `locked_at` | DateTime, null; set on confirmatory launch, after which `design`, `analysis_plan` and `resource_policy` are immutable |
| `active_plan` | FK(StudyPlan, SET_NULL, null) |
| `stop_reason` | JSON `{code, detail, at, rule}` |
| `pause_reason` | Char(40): `budget`, `quota`, `spend_cap`, `rule:<code>`, `manual`, `infra` |
| `spent_usd`, `spent_list_usd` | Decimal(12,6), default 0; ledger-maintained caches |
| `reserved_usd` | Decimal(12,6) |
| `spent_tokens`, `reserved_tokens` | BigInteger |
| `cloned_from` | FK(self, SET_NULL, null) |
| `launched_at`, `completed_at`, `cancelled_at` | DateTime |
| `updated_at` | DateTime |

There is **no** `Study.slot_set` FK. A slot study's SlotSets are derived through `StudyUnit.slot_target.slot_set`; `Design.units.slot_sets[]` lists them and the validator requires that they share one task (issue `mixed_parents` otherwise, section 7.10).

Indexes: `(created_by, -created_at)` and `(status)`.

**`StudyFactor`** stores one row per factor in the design, mirrored from `design` at resolve time for querying.

| Field | Type |
|---|---|
| `study` | FK, CASCADE |
| `key` | Char(32) (vocabulary in section 9.4) |
| `role` | Char(8): `crossed`, `fixed` or `fraction` |
| `levels` | JSON `[pinned ref or enum]` |
| `position` | SmallInteger |

Constraint: `Unique(study, key)`.

**`StudyPlan`** is immutable. Rows are created only by **Freeze plan** or **launch**; the PlanRail's resolves are ephemeral (`persist=false`, cached in-process by `design_hash` for 10 minutes). `draft` rows older than 24 h with no launch are garbage-collected.

| Field | Type |
|---|---|
| `id` | uuid |
| `study` | FK, CASCADE |
| `plan_hash` | sha, unique |
| `design_hash` | sha |
| `status` | `draft`, `launched` or `superseded` |
| `payload_blob` | sha: the full `ResolvedPlan` JSON in the blob store |
| `n_conditions`, `n_units`, `n_cells`, `n_blocks` | PositiveInteger |
| `estimate` | JSON `contracts.plan.Estimate` |
| `issues` | JSON `[Issue]` |
| `resolver_version` | SmallInteger |

**`StudyCondition`** (new model; `DeleteModel(ExperimentCondition)` + `CreateModel`)

| Field | Type |
|---|---|
| `study` | FK, CASCADE |
| `plan` | FK(StudyPlan, PROTECT) |
| `index` | SmallInteger |
| `label` | Char(200) |
| `condition_hash` | sha |
| `levels` | JSON `{factor_key: pinned ref or enum}` |
| `model` | FK(LLMModel, PROTECT) |
| `harness_preset` | FK(HarnessPreset, null) |
| `treatment_hash` | sha |
| `is_baseline` | Bool |
| `paused` | Bool (set by stop rules) |
| `pause_reason` | Char(40) |

Constraint: `Unique(plan, index)`. Index: `(study, condition_hash)`.

**`StudyUnit`**

| Field | Type |
|---|---|
| `study` | FK |
| `plan` | FK |
| `index` | SmallInteger |
| `unit_key` | Char(160): `task:<slug>@<v>` or `slot:<target uuid>` |
| `task_version` | FK(TaskSpecVersion, null) |
| `slot_target` | FK(SlotTarget, null) |
| `unit_order` | PositiveInteger: seeded unit order used by the block sequence (section 9.4) |

Constraint: `Unique(plan, unit_key)`. The prose variant is **not** part of the unit; it is the condition factor `prose_variant`.

**`StudyCell`**

| Field | Type |
|---|---|
| `id` | bigauto |
| `study` | FK |
| `plan` | FK |
| `condition` | FK(StudyCondition) |
| `unit` | FK(StudyUnit) |
| `repeat_index` | PositiveSmallInteger (0-based) |
| `block_index` | PositiveInteger: position of `(repeat_index, unit)` in the block order |
| `stage` | Char(6): `pilot` or `main` |
| `seed` | PositiveBigInteger |
| `sequence` | PositiveInteger (claim order) |
| `current_run` | FK(GenerationRun, SET_NULL, null, related `+`): the **first usable** attempt, else the latest attempt; maintained by `runs.queries.refresh_current(cell)` on every run outcome change (section 6.14) |
| `state` | Char(14): `planned`, `queued`, `running`, `done`, `infra_pending`, `excluded` |
| `exclusion_reason` | Char(24), blank: `cell_budget`, `stopped_by_rule`, `budget_stop`, `cancelled`, `config_invalid` |
| `estimate_usd` | Decimal(10,6) |
| `cap_usd` | Decimal(10,6) (estimate `cap_usd` per attempt, section 4.4 step 5) |
| `cell_cap_usd` | Decimal(10,6) (`cell.max_cost_usd`) |
| `spent_usd`, `spent_list_usd` | Decimal(10,6) (all attempts) |
| `adaptive_n` | Bool (the cell's condition×unit was stopped by a precision rule) |

Constraint: `Unique(study, condition, unit, repeat_index)`. Indexes: `(study, stage)`, `(study, sequence)` and `(study, block_index)`.

**`StudyPromptOverride`** records an inline text edit made in the builder or the cell drawer. The edit itself is saved as a new user-owned version of the entity that **owns** the edited text (a PromptTemplate, or a fragment-owning StackProfile, StyleDirective, Guidance or auth fragment), with `derived_from` = the base; the matching factor level is swapped to the new version.

| Field | Type |
|---|---|
| `study` | FK |
| `owner_kind` | Char(16): `prompt`, `prompt_set`, `stack`, `style`, `guidance`, `auth_fragment` |
| `owner_ref_base` | Char(140) (pinned ref) |
| `owner_ref_new` | Char(140) (pinned ref) |
| `role` | Char(24), blank |
| `diff` | Text (unified diff of the template text) |

**`StudySchedule` and `StudyPostRun`** (new models; `DeleteModel` + `CreateModel` of the Experiment versions) port the fields of `ExperimentSchedule` and `ExperimentPostRun`, with `experiment` renamed to `study`. `StudyPostRun.kind` gains `patterns`. A slot study auto-attaches `StudyPostRun(kind=patterns, detectors="default")` at launch.

### 3.7 Run models (`models/run.py`, WP-01)

**`GenerationRun`** (formerly GenerationJob; `RenameModel`, `db_table="generation_run"`)

| Field | Type / notes |
|---|---|
| `id` | uuid |
| `kind` | Char(20): `app_agentic`, `slot_single_shot`, `slot_agent` or `reference`; idx |
| `protocol` | Char(12): `app@1`, `slot@1` or `ref@1` |
| `status` | Char(12): `pending`, `running`, `completed`, `failed` or `cancelled` (as before) |
| `phase` | Char(12): `""`, `running`, `eval_queued` or `evaluating` (as before) |
| `outcome` | Char(16), blank: section 6.12 taxonomy |
| `outcome_reason` | Char(64), blank |
| `usable` | Bool, null (section 6.13) |
| `validity` | JSON `contracts.outcome.Validity` |
| `study`, `condition`, `cell` | FK(Study / StudyCondition / StudyCell, SET_NULL, null) |
| `cell_attempt` | PositiveSmallInteger, null: 1 for the planned run of a cell, 2.. for top-ups |
| `beyond_plan` | Bool, default False: created on a cell that already had a usable run; **excluded from results** |
| `stage` | Char(6), blank |
| `repeat_index` | PositiveSmallInteger, null |
| `model` | FK(LLMModel, PROTECT, null); null for `reference` |
| `task_version` | FK(TaskSpecVersion, PROTECT, null) |
| `prose_variant` | Char(12), blank |
| `stack_profile`, `starter`, `style`, `guidance`, `prompt_set`, `harness_preset`, `sampling_preset`, `resource_policy` | FK(each kind, PROTECT, null), copied from the manifest for querying |
| `auth_mode` | Char(16), blank |
| `slot_target` | FK(SlotTarget, PROTECT, null) |
| `parent_run` | FK(self, PROTECT, null, related `slot_variants`) |
| `reference_of` | Char(16), blank: `oracle`, `starter_baseline`, `slot_original`, `slot_holed`, `probe_mutant`, `conformance` (reference kind only) |
| `seed` | PositiveBigInteger, null |
| `pinned_served_name`, `pinned_digest` | Char(200) / Char(80), blank: copied from `manifest.model.endpoint`, so the scheduler and capacity code never query JSON paths |
| `task_hash`, `treatment_hash`, `prompt_hash`, `config_fingerprint`, `run_fingerprint` | sha, db_index |
| `tree_hash` | sha, blank: collected sample tree |
| `workspace_blob` | sha, blank: tar.zst of the final workspace (evidence) |
| `resolved_lock_blob` | sha, blank: `contracts.evaluation.ResolvedLock` JSON (section 6.10) |
| `lock_source` | Char(10), blank: `model` (the agent wrote complete lockfiles) or `platform` |
| `registry_snapshot_at` | DateTime, null: when the platform lock was resolved |
| `result` | JSON `contracts.outcome.RunResult` (small summaries only; no file contents) |
| `metrics` | JSON `contracts.outcome.RunMetrics` (documented contract keys: cost, list cost, tokens by class, requests by class, call_truncations, wall, LOC; eval summary mirrors the latest RunEvaluation) |
| queue fields | Copied verbatim from GenerationJob: `endpoint` FK, `lane`, `queued_at`, `run_after`, `priority`, `sequence`, `attempt`, `lease_owner`, `lease_expires_at`, `heartbeat_at`, `stage_entered_at` and `execution` JSON |
| `started_at`, `completed_at`, `duration_seconds`, `error_message`, `updated_at` | as before; `started_at` is exported as a covariate |

`Lane` choices stay `sandbox` and `llm` (`generation/models.py:739` today). Lane by kind: `app_agentic` and `slot_agent` use `sandbox`; `slot_single_shot` (and the batch submitter) use `llm`. A `reference` run has no generation stage: it is created with `status=running, phase=eval_queued` by `execution.api.enqueue_evaluation(run_ids)` (called in `on_commit`), and `_EVAL_CLAIM_SQL` claims it like any other evaluation.

Indexes (renamed from `genjob_*`):
- `genrun_ready (-priority, queued_at)` WHERE pending AND queued.
- `genrun_lane_head (study, endpoint, sequence, queued_at)` WHERE pending AND queued.
- `genrun_owned`, `genrun_running` and `genrun_lease`, as before.
- `genrun_cell (cell, created_at)`.
- `genrun_study_state (study, status, phase)`.
- `genrun_cfp (config_fingerprint)`.
- `genrun_slot (slot_target, condition)`.
- `genrun_parent (parent_run)`.
- `genrun_tree (tree_hash)` (evaluation reuse).

Check constraints:
- `kind='app_agentic'` ⇒ `task_version IS NOT NULL`.
- `kind IN (slot_*)` ⇒ `slot_target IS NOT NULL AND parent_run IS NOT NULL`.
- `kind!='reference'` ⇒ `model IS NOT NULL`.

**`RunManifest`** is immutable and written once before the run is queued.

| Field | Type |
|---|---|
| `run` | OneToOne(GenerationRun, CASCADE, PK) |
| `protocol` | Char(12) |
| `manifest` | JSON `contracts.manifest.RunManifest` (section 3.8) |
| `manifest_hash` | sha = H(manifest) |
| `stimuli` | JSON `[{role, template_ref, sha, blob, chars, est_tokens, sources: [...]}]` |

**`RunAttempt`** stores one row per execution attempt and is written by the runner.

| Field | Type |
|---|---|
| `run` | FK, CASCADE |
| `attempt` | PositiveSmallInteger |
| `executor_id` | Char(80) |
| `started_at`, `ended_at` | DateTime |
| `termination` | Char(24) (section 5.1 `Termination`) |
| `outcome` | Char(16) |
| `infra_error` | Char(64), blank |
| `oom_killed` | Bool (docker `State.OOMKilled`) |
| `served` | JSON `{providers: {name: calls}, models: {served_name: calls}, response_models: {model: calls}, system_fingerprints: [..], routes}` |
| `usage` | JSON `{requests_by_class: {main, subagent, compaction, auxiliary}, prompt_tokens, uncached_prompt_tokens, completion_tokens, reasoning_tokens, cached_tokens, cache_write_tokens, cost_usd, list_cost_usd, call_truncations, translation_dropped, tokens_per_s_p50}` |
| `budget_exhausted` | Char(24), blank |
| `time_split` | JSON `{install_s, llm_s, tool_s, idle_s}` (install share is reported per stack) |
| `evidence_dir` | Char(255) (`GENERATION_RUNS_ROOT/<run>/a<attempt>/`) |

Constraint: `Unique(run, attempt)`.

**`RunFile`** stores one row per collected path.

| Field | Type |
|---|---|
| `run` | FK, CASCADE |
| `path` | Char(1024) |
| `blob` | sha |
| `size` | PositiveInteger |
| `mode` | SmallInteger |
| `origin` | Char(12): `starter`, `authored`, `modified`, `deleted`, `artifact` or `slot` |
| `language` | Char(24) |
| `loc` | PositiveInteger (`reports.services.loc.source_loc`) |
| `is_test` | Bool |
| `excluded` | Char(16), blank: `non_source`, `build_output`, `vendored`, `binary`, `too_large`, `lockfile` or `data` |

Constraint: `Unique(run, path)`. Index: `(run, origin)`.

A slot variant stores **only** its `slot` rows (the changed files). `RunView` overlays the parent's rows (origin shown as `parent`) for `code_files("app")`, builds and exports. There is no `parent` origin in the table.

**Scopes**: `model` = `origin IN (authored, modified, slot) AND excluded=''`; `slot` = the variant's `slot` rows; `app` = the whole non-artifact tree (overlaid for variants).

**`RunEvaluation`** is append-only. Re-evaluating creates a new row, and readers use the latest per `(run, eval_suite)`.

| Field | Type |
|---|---|
| `id` | bigauto |
| `run` | FK, CASCADE, related `evaluations` |
| `eval_suite` | FK(EvalSuite) |
| `eval_level` | Char(12) (section 4.8) |
| `mode` | Char(12): `full`, `overlay` or `static_only` |
| `status` | Char(12): `pending`, `running`, `measured`, `not_measured` or `error` |
| `stages` | JSON `{lock, build, start, tester, adherence, probes}`, each `{status, seconds, detail}` |
| `result` | JSON `contracts.evaluation.EvalResult` |
| `adherence` | JSON `contracts.evaluation.AdherenceRecord` |
| `build_key` | Char(64) (SampleBuildArtifact key) |
| `image_id` | Char(80) |
| `reused_from` | FK(self, SET_NULL, null): set when the result was reused for an identical `(tree_hash, suite hash, check selection)` (section 7.8) |
| `retest_of` | FK(self, SET_NULL, null): set on the random 5% re-evaluations (section 8.5) |
| `baseline` | FK(EvalBaseline, null) |
| `created_at` | DateTime |

Index: `(run, eval_suite, -created_at)`. Readers that take "latest" ignore `retest_of` rows (they feed test-retest agreement only).

**`EvalBaseline`** stores the starter baseline per `(starter, task_version, auth_mode, eval_suite)`.

| Field | Type |
|---|---|
| `starter` | FK |
| `task_version` | FK |
| `auth_mode` | Char(16) |
| `eval_suite` | FK |
| `reference_run` | FK(GenerationRun) |
| `agent_pass_rate` | Float |
| `checks_passed` | JSON `[check_id]` |

Constraint: `Unique(starter, task_version, auth_mode, eval_suite)`.

**`GenerationArtifact`** is kept as is, with `job` renamed to `run`, plus an added `attempt` field (PositiveSmallInteger). Stage names:
- `agent_call_%04d` for sandbox runs;
- `slot_call_%04d` for single-shot runs (batch results included).

`call_telemetry` matches `^(agent|slot)_call_\d{4}$`. Each call record carries `request_class`, `finish_reason`, `response_model`, `system_fingerprint`, `tokens_per_s`, `front_protocol` and `serving_tier`.

### 3.8 RunManifest content (`contracts/manifest.py`)

```python
class FactorRecord(BaseModel): ref: str | None; content_hash: str | None; value: str | None   # enum factors use value
class RunManifest(BaseModel):
    protocol: Literal["app@1","slot@1","ref@1"]; kind: str; manifest_schema: Literal[1]
    study: {"id": str, "plan_hash": str, "condition_hash": str, "cell": [int, int, int], "block_index": int,
            "stage": str, "cell_attempt": int, "beyond_plan": bool} | None
    factors: dict[str, FactorRecord]   # every factor key in section 9.4, incl. fixed ones
    task: {"ref": str, "structured_hash": str, "variant": str, "prose_sha": str} | None
    slot: {"slot_set": str, "target": str, "target_hash": str, "parent_run": str, "parent_tree_hash": str,
           "parent_model": str, "parent_harness": str, "parent_selection": {"rule": str, "seed": int | None},
           "address": dict, "hole_mode": str, "keep_docstring": bool, "context_policy": dict,
           "resolved_context": {"files": [{"path","sha","tokens"}], "sha": str}, "include_tests": bool,
           "edit_policy": str, "allowlist": list[str], "leak_score": float,
           "validation": {"discriminating_checks": list[str], "dropped_checks": list[str]}} | None
    model: {"slug": str, "endpoint": {"id": int, "kind": str, "served_model_name": str | None, "digest": str | None,
            "cost_basis": str},
            "pricing": {"input_per_mtok": float, "output_per_mtok": float, "cached_input_per_mtok": float | None,
                        "cache_write_per_mtok": float | None, "source": str, "as_of": str}}
    sampling: {"ref": str, "policy": str, "forced_params": dict, "stripped_params": list[str], "seed": int | None,
               "seed_forwarded": bool}
    resources: {"ref": str, "generation_policy_hash": str, "budget_basis": str, "effective": dict, "notes": list}
    gateway: {"image_digest": str, "front_protocol": str, "protocol_mode": Literal["native","translate","passthrough"],
              "config_sha": str, "routing": dict, "serving_tier": str, "session_id_rule": "run_uuid"}
    harness: {"name": str, "version": str, "preset_ref": str, "image": str, "image_digest": str,
              "config_files_sha": dict[str, str], "command_sha": str, "options": dict,
              "facts_version": int, "unsupported_knobs": list[str], "vendor_affinity": str | None} | None
    starter: {"ref": str, "tree_hash": str, "base_images": list} | None
    allowed_bases: {"sha": str, "refs": list[str]} | None      # rendered into the brief (section 6.10)
    stimuli: list[{"role": str, "template_ref": str, "sha": str}]
    identity: {"task_hash","treatment_hash","prompt_hash","generation_policy_hash","config_fingerprint","run_fingerprint"}
    planned_evaluation: {"eval_suite": {"ref","content_hash"}, "eval_level": str, "mode": str}   # recorded, NOT hashed
    network_policy: Literal["none","packages"]
    sandbox: {"memory": str, "nano_cpus": int, "pids_limit": int, "resolution": "study_max"}
    created_at: str   # ISO; NOT hashed into identity
```

Per-attempt facts are never written into the manifest. Served providers, timings, the executor id, request classes and translation losses belong to `RunAttempt`.

### 3.9 Slot models (`models/slot.py`, WP-01)

**`SlotSet`** freezes one parent sample.

| Field | Type |
|---|---|
| `id` | uuid |
| `name` | Char(200) |
| `parent_run` | FK(GenerationRun, PROTECT), restricted to `kind=app_agentic`, `usable=True` |
| `task_version` | FK(TaskSpecVersion, PROTECT): denormalised from the parent |
| `parent_model`, `parent_harness` | Char(200) / Char(32): denormalised from the parent manifest |
| `selection` | JSON `{rule: random\|all\|manual, pool_query, pool_size, seed, picked_by, at}` |
| `frozen_tree_hash` | sha (must equal `parent_run.tree_hash`) |
| `parent_manifest_hash` | sha |
| `parent_eval` | FK(RunEvaluation, PROTECT, null): the baseline evaluation of the parent |
| `parent_build_key` | Char(64), blank |
| `index` | FK(RunSymbolIndex, PROTECT) |
| `notes` | Text |

A parent run referenced by a SlotSet cannot be re-evaluated in place; a new RunEvaluation row is allowed. It cannot be deleted either (PROTECT).

**`RunSymbolIndex`**

| Field | Type |
|---|---|
| `run` | FK |
| `index_version` | SmallInteger |
| `blob` | sha (`contracts.slot.SymbolIndex` JSON) |
| `stats` | JSON `{files, symbols, endpoints, components, unparsed: [paths]}` |

Constraint: `Unique(run, index_version)`.

**`SlotTarget`**

| Field | Type |
|---|---|
| `id` | uuid |
| `slot_set` | FK, CASCADE |
| `label` | Char(200) |
| `kind` | Char(10): `file`, `region`, `symbol`, `endpoint` or `component` |
| `address` | JSON `contracts.slot.SlotAddress` |
| `match_key` | Char(200), blank: `endpoint:<spec_endpoint_id>` or `component:<name>`; used to find the same part in other parents |
| `language` | Char(24) |
| `original_sha` | sha |
| `original_blob` | sha |
| `default_hole_mode` | Char(10): `stub`, `remove` or `rewrite` (a condition factor may override it) |
| `default_keep_docstring` | Bool, default False |
| `default_context_policy` | JSON `contracts.slot.ContextPolicy` (a condition factor may override it) |
| `edit_policy` | Char(8): `revert` or `fail` |
| `allowlist` | JSON `[path]` |
| `interface` | JSON `{signature, decorators, imports_in_scope, exported_names}` |
| `linked_endpoints` | JSON `[endpoint_id]` |
| `linked_checks` | JSON `[check_id]` (after validation: the discriminating checks only) |
| `linked_checks_source` | Char(8): `auto` or `edited`; `linked_checks_diff` JSON `{added, removed}` vs auto |
| `linked_probes` | JSON `[probe_id]` |
| `validation` | JSON `contracts.slot.TargetValidation` `{status: pending\|ok\|uninformative\|error, original_pass: {check: [bool,bool,bool]}, holed_fail: {check: bool}, flaky_checks, dropped_checks, discriminating_checks, probe_stability: {probe: [status×3]}, runs: [run ids], at}` |
| `leak` | JSON `contracts.slot.LeakReport` `{score, matches: [{path, shingles, verbatim: bool}], scope: [context, workspace], version}` |
| `target_hash` | sha |
| `original_run` | FK(GenerationRun, null): the `reference`/`slot_original` run (variant 0) |

Constraint: `Unique(slot_set, target_hash)`. Index: `(match_key)`.

**`SlotVariantResult`**

| Field | Type |
|---|---|
| `run` | OneToOne(GenerationRun, CASCADE, PK) |
| `slot_target` | FK |
| `parse_status` | Char(16): `ok`, `invalid_json`, `schema_violation`, `empty`, `refusal` or `truncated` |
| `replacement_blob` | sha, blank |
| `replacement_sha` | sha, blank |
| `normalized_sha` | sha, blank |
| `normalizer_version` | SmallInteger |
| `out_of_slot_edits` | JSON `[{path, kind: added\|modified\|deleted, lines}]` |
| `edit_violation` | Bool |
| `diff_vs_original` | JSON `{lines_added, lines_removed, similarity, ast_edit_distance, normalized_equal}` |
| `new_imports` | JSON `[str]` |
| `new_dependencies` | JSON `[str]` |

**`PatternRun`**

| Field | Type |
|---|---|
| `id` | uuid |
| `study` | FK |
| `detectors` | JSON `[pinned ref]` |
| `detector_set_hash` | sha |
| `normalizer_version` | SmallInteger |
| `cluster_params` | JSON `{method: exact\|minhash, threshold, shingle, num_perm}` |
| `scope` | JSON `{features: [span\|file], targets: [uuid] \| "all"}` |
| `is_live` | Bool: the per-study provisional run fed by `patterns.live` (section 7.9) |
| `label` | Char(12): `planned` (matches the pre-registered `analysis_plan.patterns`) or `exploratory` |
| `status` | Char(12) |
| `summary` | JSON |
| `completed_at` | DateTime |

PatternRuns are immutable once completed; re-running detectors creates a new PatternRun.

**`PatternHit`**

| Field | Type |
|---|---|
| `pattern_run` | FK, CASCADE |
| `run` | FK(GenerationRun) |
| `detector` | FK(PatternDetector) |
| `feature_key` | Slug(100) |
| `scope` | Char(4): `span` or `file` |
| `status` | Char(14): `present`, `absent`, `error` or `not_applicable` |
| `error_reason` | Char(40), blank: `parse_error`, `tool_error`, `file_unparsed`, `timeout` |
| `matches` | JSON `[{path, start_line, end_line, snippet_sha, message}]` |
| `tool_severity` | Char(32), blank: copied verbatim from the tool |
| `provisional` | Bool (live hits) |

Constraint: `Unique(pattern_run, run, detector, scope)`. Index: `(pattern_run, feature_key, status)`.

**`VariantCluster`**

| Field | Type |
|---|---|
| `pattern_run` | FK, CASCADE |
| `slot_target` | FK |
| `method` | Char(8) |
| `key` | Char(64) (normalized_sha for exact) |
| `size` | PositiveInteger |
| `representative` | FK(GenerationRun) |
| `includes_original` | Bool |

**`VariantClusterMember`**

| Field | Type |
|---|---|
| `cluster` | FK, CASCADE |
| `run` | FK |
| `distance` | Float |

Constraint: `Unique(cluster, run)`.

**`ClusterLabel`** (manual coding; engine `manual`, section 7.9)

| Field | Type |
|---|---|
| `slot_target` | FK |
| `normalized_sha` | sha: the exact cluster key the label applies to (stable across PatternRuns) |
| `detector` | FK(PatternDetector) (engine `manual`, carries the feature key) |
| `value` | Char(8): `present`, `absent` or `unsure` |
| `labeler` | FK(User) |
| `note` | Text |
| `created_at` | DateTime |

Constraint: `Unique(slot_target, normalized_sha, detector, labeler)`.

**`DetectorAudit`**

| Field | Type |
|---|---|
| `pattern_run` | FK |
| `feature_key` | Slug(100) |
| `sample` | JSON `{hits: [run ids], non_hits: [run ids], seed}` (stratified, ≈20 + 20) |
| `eval_task` | FK(human_eval.EvalTask, null) |
| `status` | Char(12): `pending`, `in_progress`, `complete` |
| `precision`, `npv` | Float, null, with Wilson CIs in `summary` JSON |

### 3.10 Spend models (`models/spend.py`, WP-01)

**`SpendLedgerEntry`** is append-only.

| Field | Type |
|---|---|
| `id` | bigauto |
| `kind` | Char(12): `reserve`, `release`, `usage` or `adjust` |
| `study` | FK(null) |
| `run` | FK(null) |
| `attempt` | SmallInteger, null |
| `user` | FK(User) |
| `endpoint` | FK(LLMEndpoint, null) |
| `model_slug` | Char(200), blank |
| `amount_usd` | Decimal(14,8), signed (billed) |
| `list_cost_usd` | Decimal(14,8), signed (catalog list price; equals billed for `cost_basis=billed`) |
| `tokens_in`, `tokens_out`, `requests` | Integer (signed for reserve/release of token caps) |
| `source` | Char(24): `scheduler`, `gateway_live`, `gateway_final`, `proxy`, `batch`, `openrouter_generation`, `price_table` or `manual` |
| `dedupe_key` | Char(128), unique |
| `created_at` | DateTime |

Indexes: `(study, kind)`, `(run, attempt)`, `(user, created_at)` and `(endpoint, model_slug, created_at)` (quota windows).

Running totals:
- `spent(study) = Σ usage + Σ adjust` (in the study's `cap_basis`: billed or list).
- `open_reserved(study) = Σ reserve + Σ release`, where a release is a negative amount. Every reservation is released when its attempt ends.

**`LLMEndpoint.cost_basis`** (inference app, WP-01): `billed` (OpenRouter), `list_price` (subscription endpoints such as OpenCode Go: cost is accounted at catalog list price) or `free` (local; tokens only).

**`SpendCap`**

| Field | Type |
|---|---|
| `scope` | Char(10): `user`, `endpoint`, `model` or `global` |
| `user` | FK(null) |
| `endpoint` | FK(null) |
| `model_slug` | Char(200), blank |
| `period` | Char(6): `5h`, `day`, `week` or `month` (rolling windows) |
| `unit` | Char(8): `usd`, `list_usd` or `tokens` |
| `limit` | Decimal(16,4) |
| `action` | Char(8): `block` or `warn` |
| `on_hit` | Char(8): `pause` (default; reason `quota` or `spend_cap`) |
| `enabled` | Bool |

Subscription quotas (e.g. the OpenCode Go per-model 5 h and weekly caps) are `SpendCap(scope=model, endpoint=…, period=5h|week, unit=list_usd|tokens)`. Hitting one pauses the study (infra, re-queue); it never stops it.

**`CostPrior`** holds empirical priors for estimates (section 4.4). WP-11 refreshes it nightly and on run completion.

| Field | Type |
|---|---|
| `key` | Char(200), unique: `kind\|harness\|model_slug\|size_bucket\|policy_tier` |
| `n` | Integer |
| `cost_p10`, `cost_p50`, `cost_p90` | Float |
| `list_cost_p50` | Float |
| `requests_p50` | Float |
| `prompt_tokens_p50`, `cached_tokens_p50`, `completion_tokens_p50` | Float |
| `wall_s_p50`, `wall_s_p90` | Float |
| `price_at` | JSON (blended prices when the prior was computed; used for drift scaling) |
| `updated_at` | DateTime |

### 3.11 Consumer re-pointing: `RunView` (`runs/view.py`)

`RunView` is the **only** read API that code outside `generation` may use for run content. WP-01 writes the dataclass and signatures with `NotImplementedError` bodies; WP-02a implements them.

```python
@dataclass(frozen=True)
class StackRef: ref: str; content_hash: str; backend_framework: str; frontend_framework: str; database: str
@dataclass(frozen=True)
class TaskRef: ref: str; slug: str; version: int; variant: str; task_hash: str; title: str; features: list[str]; surfaces: list[str]
@dataclass(frozen=True)
class RunView:
    id: str; kind: str; protocol: str; status: str; outcome: str; usable: bool | None
    model_slug: str | None; harness: tuple[str, str] | None          # (name, version)
    stack: StackRef | None; task: TaskRef | None; style_ref: str | None; auth_mode: str | None; guidance_ref: str | None
    study_id: str | None; condition_id: int | None; cell_id: int | None; unit_key: str | None; repeat_index: int | None
    stage: str | None; seed: int | None
    fingerprints: dict[str, str]     # task_hash, treatment_hash, prompt_hash, config_fingerprint, run_fingerprint
    cost_usd: float | None; total_tokens: int | None
    def code_files(self, scope: Literal["model","app","slot"] = "model") -> dict[str, str]   # variants: parent rows overlaid for "app"
    def file_rows(self, scope="model") -> QuerySet[RunFile]
    def loc(self, scope="model") -> int                 # source LOC, tests excluded
    def latest_evaluation(self, suite_ref: str | None = None) -> RunEvaluation | None
    def task_dict(self) -> dict | None                  # stable dict for human_eval/export (structured + chosen prose)
    def manifest(self) -> dict
    def labels(self) -> dict[str, str]                  # factor_key -> human label (for tables)
def run_view(run: GenerationRun) -> RunView
def run_views(qs) -> list[RunView]                      # one query + prefetch; no N+1
def run_views_code(runs, scope="model") -> dict[str, dict[str, str]]   # bulk reader for stats: one query, batched blob reads
```

Mapping from the old readers to the new API (WP-02a/WP-02b checklist):

| Old access | New |
|---|---|
| `job.resolved_bundle["config_fingerprint"]` | `run.config_fingerprint` column |
| `job.resolved_bundle["stack"]`, `job.stack_slug` | `run_view(run).stack` |
| `job.resolved_bundle["app_requirement"]` | `run_view(run).task_dict()` |
| `job.resolved_bundle["harness"]` | `run_view(run).harness` |
| `job.resolved_bundle["endpoint"]` | `run.manifest.manifest["model"]["endpoint"]` (scheduler, capacity: the raw SQL becomes a join on `generation_runmanifest`, or WP-11 adds `GenerationRun.pinned_digest` Char(80); **decision: add `pinned_served_name` and `pinned_digest` columns on GenerationRun**, written from the manifest) |
| `result_data["files"]` | `RunFile` and `code_files()` |
| `result_data["agent"]["validity"]` | `run.validity` and `run.usable` |
| `metrics["functional"]` | `latest_evaluation().result.metrics` (mirrored into `run.metrics["eval"]`) |
| `job.experiment`, `job.condition`, `job.app_requirement` | `run.study`, `run.condition` and `run.cell.unit` |
| `current_jobs(experiment)` (latest per cell) | `runs.queries.current_runs(study, stage="main")` (WP-10): the **first usable** run per cell; `beyond_plan` runs excluded (6.14) |
| `job.mode == "agent"` | `run.kind` |
| `healing_*` | removed |

### 3.12 Migrations, the wipe and the cut-over order

**Why the order matters.** `compose/local/django/start` runs `set -o errexit` and then `python manage.py migrate`. If the v3 guard migration raised on a populated v2 database, the web container would crash-loop, and a wipe command written against v3 models could not run on a v2 schema. So the wipe runs **on the old code, before v3 is deployed**.

**WP-00: `wipe_generation_v2` (ships on `main` first).**
- File: `backend/generation/management/commands/wipe_generation_v2.py`. It uses **raw SQL only** (no ORM models), with `information_schema` table-existence checks, against the v2 table names: `generation_generationjob`, `generation_generationbatch`, `generation_experiment`, `generation_experimentcondition`, `generation_experimentschedule`, `generation_experimentpostrun`, `generation_apprequirementtemplate` (non-system rows), `generation_contentblock`, `generation_generationprofile`, `generation_copilotiteration`, `generation_generationartifact` and the dependent rows in other apps (`analysis_analysisrun` and its result tables where `generation_job_id` is set, `human_eval_evaltaskapp.job_id`, `reports_report.generation_job_id`, `runtime_containerinstance.generation_job_id`; `inference_llmrequest.job_id` is set to NULL so telemetry history is kept).
- Containers referenced by `runtime_containerinstance` are stopped through the docker API before the transaction (best effort, listed in the output).
- Inside one transaction it deletes in FK order, then deletes `GENERATION_RUNS_ROOT/*` after commit.
- It prints per-table counts and requires the literal `--yes`; without it, it is a dry run. On a v3 schema (no v2 tables) it is a no-op that says so.
- Test (`generation/tests/wp00_wipe/test_wipe.py`): with `MigrationExecutor`, migrate to `generation 0022` (the v2 leaf), insert rows through the **historical** models (`executor.loader.project_state().apps`), run the wipe dry run (counts, no deletion) and `--yes` (tables empty, LLMRequest rows kept with NULL job), then migrate forward to the v3 leaf successfully.
- It is a separate branch off `main` (`chore/wipe-generation-v2`); the user merges it. The v3 branch carries the same file unchanged after rebase.

**v3 migrations (WP-01).**
- `generation/migrations/0023_v3_wipe_guard.py`: a `RunPython` step that raises `RuntimeError("generation v2 data present: run `manage.py wipe_generation_v2 --yes` on the v2 deployment first (docs/design/V3_CUTOVER.md)")` when any v2 generation table above is non-empty. On an empty or fresh database it is a no-op. With the cut-over order below it never fires; it exists so a mistake fails loudly instead of migrating half-populated data.
- `0024_v3_models.py`: `RenameModel(GenerationJob→GenerationRun)` and `RenameModel(Experiment→Study)` with explicit `db_table`; `DeleteModel` for `ExperimentCondition`, `ExperimentSchedule`, `ExperimentPostRun`, `GenerationBatch`, `AppRequirementTemplate`, `ContentBlock`, `GenerationProfile`, `RunPreset`, `CopilotIteration`; `CreateModel` for every new model; `RemoveField`/`AddField` on the two renamed models. WP-01 writes it with `makemigrations` and then hand-checks it.
- `runtime/migrations/00NN_remove_stack.py`: `DeleteModel(Stack)`. It MUST depend on **`generation 0024_v3_models`** (generation migrations 0016 and 0019 FK `runtime.Stack`, so those FKs must be gone first) and on the other session's `runtime 0005_sample_build_artifact` (section 12.4).
- `analysis/migrations/00NN_code_scope.py`: adds `AnalysisRun.code_scope` and `code_scope_paths_sha`; plus the FK-target string changes in `analysis`, `inference`, `runtime`, `reports` and `human_eval` (`AlterField` migrations) and `LLMEndpoint.cost_basis`.

**Cut-over order** (documented by WP-00 in `docs/design/V3_CUTOVER.md`; every step needs the user):
1. **Drain the executor**: pause all studies/experiments, wait for running jobs to finish or cancel them (backend edits kill live agent runs; memory note `agent-mode-ops`).
2. **Wipe** on the v2 deployment: `manage.py wipe_generation_v2` (dry run, review counts), then `--yes`.
3. **Deploy** v3 (merge to `main`, rebuild images).
4. **Migrate** (runs automatically in `start`; the guard is a no-op on the wiped DB), then `seed_library` (dev: on migrate).

---

## 4. Resource and budget model

### 4.1 Principles

1. **Budget never enters the stimulus.** Prompts never mention numbers of requests, tokens, dollars or minutes. The neutral sentence "The run has a fixed resource budget; work efficiently." is optional and comes from a PromptTemplate fragment `budget-neutral@1`. When it is used, it is part of the hashed prompt.
2. **Every knob has a validity class** (from budget-research):
   - **C (confound)**: changes what the model sees or how it samples. In `treatment_view()`, so in `generation_policy_hash` and `config_fingerprint`.
   - **R (recorded limit)**: can change the outcome only through truncation or stopping. Also in `treatment_view()`; the stop reason is recorded per run.
   - **O (operational)**: affects only cost, speed or scheduling. Recorded in `manifest.resources.effective`, never hashed.
   - **S (stopping/design)**: a study-level rule that changes inference. In `prereg_hash`; frozen by the confirmatory lock.
3. **Every knob is enforced at one place only**, listed below. Each enforcement point records a typed event when it fires: `budget_exhausted:<kind>`, `timeout`, `idle_timeout`, `stopped_by_rule`, `quota`, and so on.
4. **Hard caps are hard.** Study, cell, user, endpoint and quota caps are guaranteed by reservations (4.5). Runs never get a smaller cap mid-study, because that would change the treatment. The gateway refuses a call that could exceed a cap; it never shrinks `max_tokens` to fit.
5. **Budget stops are balanced.** Cells are claimed in replicate blocks, ordered repeat-major (4.5, 9.4). A study stopped by budget ends with equal n per (condition, unit) ±1.
6. **Harness-side cost caps are never used** (Claude Code `--max-budget-usd`, mini-swe-agent `cost_limit`): they use client price tables that are wrong for non-native models. The gateway is authoritative.
7. **A cheap-model cascade is a treatment, not a saving.** Cheap models are used only for pilots and smoke runs (`pilot.smoke_first`) and for auxiliary non-measured work, always recorded.

### 4.2 Knob catalogue (`ResourcePolicyContent`, `contracts/resources.py`)

Enforcement points:
- **GW**: gateway sidecar, or local proxy overlay (shared `budget_rules.py`).
- **SBX**: sandbox supervisor (`agentic/sandbox.py`).
- **HAR**: harness adapter config (mapped per harness, section 5.3; unsupported → `unsupported_knob:<k>` note).
- **SCH**: execution scheduler (claim transaction).
- **LED**: budget ledger.
- **STU**: study stop-rule and pilot-gate evaluator.
- **EXE**: execution transitions (retries).
- **PLN**: plan resolver.
- **SLT**: slot executors.

Columns F, S and G give the frugal, standard and generous defaults. "—" means unset or provider default. `KNOB_META` in `contracts/resources.py` carries `{class, where, ui_group, key_limit}` for every field; a WP-11 test asserts that it matches this table.

**Run (one attempt of one sample)**

| Key | Type | Meaning | Where | Cls | F | S | G |
|---|---|---|---|---|---|---|---|
| `run.max_cost_usd` | float | Hard USD cap per attempt (gateway pre-call reservation) | GW, LED | R if `budget_basis=usd`, else O (backstop) | 0.30 | 2.00 | 10.00 |
| `run.max_total_tokens` | int | Σ prompt + completion | GW | R | 3M | 10M | 30M |
| `run.max_uncached_input_tokens` | int\|null | Σ prompt − cached (the cost-relevant input) | GW | R | 1M | 4M | — |
| `run.max_output_tokens` | int\|null | Σ completion incl. reasoning | GW | R | 150k | 500k | — |
| `run.max_llm_requests` | int | Successful calls of request class `main` | GW (+ HAR step limit) | R | 40 | 100 | 200 |
| `run.max_aux_requests` | int | Successful calls of classes `subagent`, `compaction`, `auxiliary` | GW | R | 10 | 30 | 60 |
| `run.max_turns` | int\|null | Harness-native turn cap where supported (else gateway caps only, recorded) | HAR | R | 40 | 100 | 200 |
| `run.max_workspace_bytes` | int | Workspace tar size limit (larger → `output_invalid:workspace_too_large`) | SBX | R | 200 MB | 500 MB | 1 GB |

**Per call (forced by the gateway on every request)**

| Key | Type | Meaning | Where | Cls | F | S | G |
|---|---|---|---|---|---|---|---|
| `call.max_output_tokens` | int | Visible-output cap. Upstream `max_tokens = call.max_output_tokens + (reasoning.max_tokens or 0)`, clamped to the model limit | GW | R | 8192 | 16384 | 32000 |
| `call.max_input_tokens` | int\|null | Refuse a call whose prompt estimate exceeds N (400 `context_budget`). MUST be ≥ 1.1 × `context.max_context_tokens` | GW | R | 72k | 144k | — |
| `context.max_context_tokens` | int\|null | Context window advertised to the harness / compaction threshold | HAR | C | 64k | 128k | — |
| `context.compaction` | enum `harness_default,auto,auto_prune,off` | Harness compaction mode mapping | HAR | C | auto_prune | harness_default | harness_default |
| `reasoning` | `{mode: provider_default\|effort\|max_tokens, effort?: none\|minimal\|low\|medium\|high\|xhigh, max_tokens?: int}` | Forced OpenRouter `reasoning`; exactly one of effort / max_tokens | GW | C | effort=low | provider_default | effort=high |
| `reasoning.exclude` | bool | Ask the provider not to return reasoning text. Forced `false` for protocols that replay reasoning (Anthropic thinking signatures, Gemini `thoughtSignature`, Responses `encrypted_content`) | GW | C | false | false | false |
| `caching.prompt_cache` | enum `auto,on,off` | `on`: inject `cache_control` for explicit-cache providers (Anthropic, Qwen, Gemini-explicit); `auto`: provider-implicit only; `off`: strip markers | GW | O (recorded) | on | on | on |
| `caching.ttl` | enum `5m,1h` | Anthropic cache TTL (1h costs 2× to write; worth it when tool steps exceed 5 min) | GW | O | 5m | 5m | 1h |
| `caching.sticky_session` | bool | `session_id = run uuid` on every forwarded call (sticky provider per run, different across runs) | GW | O | true | true | true |
| `routing.provider_sort` | enum `none,price,throughput,latency` | OpenRouter `provider.sort` (never the object form that can switch models) | GW | C | price | none | none |
| `routing.max_price` | `{prompt, completion}` USD/Mtok \| `"model×k"` \| null | OpenRouter `provider.max_price` (hard filter; no provider → `infra_error:no_provider`) | GW | C | model×1.25 | — | — |
| `routing.allow_fallbacks` | bool | OpenRouter `provider.allow_fallbacks` | GW | C | true | true | true |
| `routing.only` / `routing.ignore` / `routing.order` | list[str] | Provider allow/deny/order lists | GW | C | [] | [] | [] |
| `routing.quantizations` | list[str] | Quantization floor | GW | C | [fp8,bf16,fp16,fp32] | same | same |
| `routing.require_parameters` | bool | Only providers that support every forced parameter | GW | C | true | true | true |
| `routing.data_collection` / `routing.zdr` | enum `allow,deny` / bool | Privacy filters (shrink the provider pool) | GW | C | allow / false | allow / false | allow / false |
| `routing.preferred_min_throughput` / `preferred_max_latency` | float\|null | Soft reordering | GW | C | — | — | — |
| `routing.serving_tier` | enum `standard,flex,batch,auto_batch` | `batch` only with `slot_executor=single_shot` (else issue `batch_requires_single_shot`); `auto_batch` resolves **per study** to `batch` when every slot condition's endpoint supports it, else `standard` for all (note `batch_unsupported`). Agentic runs are always `standard` | GW / SLT | C | auto_batch | standard | standard |

**Time, retries, sandbox**

| Key | Type | Meaning | Where | Cls | F | S | G |
|---|---|---|---|---|---|---|---|
| `time.wall_clock_s` | int | Harness wall clock (`timeout -k 15`) | SBX | R | 1200 | 3600 | 7200 |
| `time.idle_timeout_s` | int\|null | Stop when no gateway call **ended** and no workspace change for N s; the timer pauses while a call is in flight or a sandbox process uses CPU (6.5) | SBX | R | 300 | 600 | 1200 |
| `time.command_timeout_s` | int\|null | Per-shell-command timeout (overrides the preset when set) | HAR | C | 120 | 300 | 600 |
| `time.llm_call_timeout_s` | int | Upstream timeout per call | GW | O | 300 | 600 | 900 |
| `retries.upstream_retries` | int 0-8 | Gateway retries before the first byte | GW | O | 2 | 4 | 6 |
| `retries.backoff_s` | float | Base backoff | GW | O | 2 | 2 | 3 |
| `retries.retry_on` | set | Statuses retried | GW | O | {429,5xx} | {408,429,5xx} | {408,429,5xx} |
| `retries.infra_retries` | int | Re-queue a whole run after `infra_error` (same manifest and seed, attempt+1) | EXE | O | 1 | 2 | 3 |
| `retries.count_failed_calls` | bool | Whether failed upstream calls count toward `run.max_llm_requests` | GW | R | false | false | false |
| `sandbox.memory` / `sandbox.nano_cpus` | str / int | Agent container limits. Resolved **per study** to the maximum of the policy value and every stack profile's minimum (`StackProfileContent.resources`), applied to all cells | SBX | R | 2g / 1e9 | 3g / 2e9 | 4g / 4e9 |
| `network_policy` | enum `none,packages` | Sandbox egress | SBX/GW | C | packages | packages | packages |

**Concurrency (scheduler; copied to `Study.max_parallel` / `execution_options` at launch)**

| Key | Type | Meaning | Where | Cls | F | S | G |
|---|---|---|---|---|---|---|---|
| `concurrency.study_max_parallel` | int | Max running runs of the study | SCH | O | 2 | 4 | 8 |
| `concurrency.per_endpoint` | int\|null | Extra cap below `LLMEndpoint.max_concurrency` | SCH | O | 1 | — | — |
| `concurrency.per_model` | int\|null | Max running runs per model | SCH | O | 1 | 2 | — |
| `concurrency.per_provider` | int\|null | Max running runs per pinned upstream provider | SCH | O | — | — | — |
| `concurrency.rpm_per_model` | int\|null | Gateway-side rate limit per model across the study (stops `:free` 429 storms) | SCH, GW | O | 20 | — | — |
| `concurrency.max_resident_models` | int\|null | Local endpoints: residency-aware blocking (`scheduling=auto`) | SCH | O | 1 | 1 | 2 |
| `concurrency.eval_parallel` | int | Max concurrent evaluations of this study (machine time at N=100) | SCH | O | 1 | 2 | 2 |

**Cell and study (class S rules are in `prereg_hash`)**

| Key | Type | Meaning | Where | Cls | F | S | G |
|---|---|---|---|---|---|---|---|
| `cell.max_cost_usd` | float | Σ over all attempts of a cell; beyond it, no retries (`excluded:cell_budget`) | LED | O | 0.60 | 4.00 | 20.00 |
| `study.max_cost_usd` | float \| `"auto"` | Hard study cap. `auto` = round-up(`p90_total` × 1.1 / 1.25 / 1.5), shown and confirmed in LaunchDialog | LED, SCH | S | auto | auto | auto |
| `study.max_total_tokens` / `study.max_runs` / `study.max_wall_hours` | int\|null | Caps that bind for local and subscription endpoints (USD may be 0) | LED, SCH, STU | S | — | — | — |
| `study.cap_basis` | enum `billed,list,auto` | `auto` = `list` when any condition uses an endpoint with `cost_basis ≠ billed` | LED | O | auto | auto | auto |
| `study.budget_basis` | enum `usd,tokens,auto` | `auto` = `tokens` for confirmatory multi-model studies (USD cap then set above p99 and must not bind), else `usd` | PLN | R | auto | auto | auto |
| `study.alert_at` | list[float] | Fractions of the cap that fire `budget_alert` | LED | O | [.5,.8] | [.5,.8] | [.8] |
| `study.on_budget_exhausted` | enum `pause,stop_balanced,stop` | `stop_balanced`: finish started blocks, claim no new block | SCH | S | stop_balanced | pause | pause |
| `study.stop_rules` | list[StopRule] | Section 4.6 | STU | S | default set | default set | default set |
| `pilot.enabled` | bool | Launch stage `pilot` first | PLN | S | true | true | false |
| `pilot.repeats` | int | Repeats per (condition, unit) in the pilot | PLN | S | 1 | 1 | 2 |
| `pilot.units` | `"all"` \| int | All units or a seeded random k | PLN | S | 3 | all | all |
| `pilot.smoke_first` | bool | Before the pilot: 1 cell per new (harness, model) pair on the cheapest unit | PLN, SCH | O | true | true | false |
| `pilot.reuse_as_main` | bool | Exploratory only, and only if `plan_hash` is unchanged at expand: pilot cells become main `repeat_index=0`. Forced false for confirmatory | PLN | S | true | true | true |
| `pilot.gates` | object (below) | Expand is blocked unless overridden with a reason (recorded) | STU | S | see below | | |
| `pilot.auto_expand` | bool | Expand automatically when every gate passes | STU | S | false | false | true |

Pilot gates (per condition unless noted; F / S / G):

| Gate | Meaning | F | S | G |
|---|---|---|---|---|
| `max_infra_error_rate` | infra_error / runs | .20 | .20 | .30 |
| `min_build_rate` | built / usable | .50 | .50 | .30 |
| `max_cost_overrun` | mean cost / estimate | 1.5 | 1.5 | 2.0 |
| `max_cap_hit_rate` | runs with `budget_exhausted:*`, `wall_clock` or `idle_timeout` notes | .30 | .30 | .50 |
| `max_truncation_rate` | truncated runs (BUDGET, WALL_CLOCK, IDLE, MAX_TURNS) | .20 | .20 | .20 |
| `max_call_truncation_rate` | runs with ≥1 call ending `finish_reason=length` | .20 | .20 | .20 |
| `max_wall_clock_rate` | WALL_CLOCK terminations | .15 | .15 | .15 |
| `max_oom_rate` (per stack) | OOM terminations | .05 | .05 | .05 |
| `usd_cap_binding` | only when `budget_basis=tokens`: share of `budget_exhausted:cost` | .05 | .05 | .05 |

**Caches and slot calls**

| Key | Type | Meaning | Where | Cls | F | S | G |
|---|---|---|---|---|---|---|---|
| `caches.dependency_cache` | enum `off,shared_ro` | Read-only pull-through caches for pip/npm/go/maven (below) | SBX, build | O | shared_ro | shared_ro | shared_ro |
| `caches.reuse_sample_images` | bool | Reuse SampleBuildArtifact images by build key | EVL | O | true | true | true |
| `caches.reuse_parent_build` | bool | Slot: build variants on the parent's BuildKit layers | EVL | O | true | true | true |
| `caches.reuse_eval_by_tree` | bool | Reuse the RunEvaluation of an identical `(tree_hash, suite hash, check selection)`, recorded as `reused_from`; such rows are excluded from runtime-nondeterminism analyses | EVL | O | true | true | true |
| `slot.max_output_tokens` | int\|null | Overrides `call.max_output_tokens` for single-shot slot calls | GW/SLT | R | 4096 | 8192 | 16384 |
| `estimate.method` | enum `priors,heuristic` | 4.4 | PLN | O | priors | priors | priors |
| `estimate.require_confirm_above_usd` | float | Typed confirmation above this p90 total | UI | O | 2 | 20 | 100 |

Evaluation settings are **not** in this table: they live in `Design.evaluation` (4.8).

**Dependency caches (WP-05, compose service `depcache`).** Pull-through only; they never change resolved versions (locks decide, 6.10), so they are class O.
- npm: `verdaccio` with `publish: $nobody` and `unpublish: $nobody` (no run can publish). Sandbox `npm_config_registry` points at it.
- pip: `devpi-server` exposing a read-only mirror index (`root/pypi`, no user indexes, no upload). `PIP_INDEX_URL` points at it.
- Go: `GOPROXY` to an `athens` proxy (or `proxy.golang.org` through the gateway CONNECT allowlist when athens is absent); `GOFLAGS=-mod=mod`.
- Maven: a proxy repository (`reposilite` mirror of Maven Central) set in a generated `settings.xml`; every run gets a **throwaway** `~/.m2` (tmpfs or a per-run volume deleted after collection).
- **No writable shared volume is mounted into any agent container.** Caches are reached over the network only.
- When the cache is unavailable, the sandbox falls back to direct registry access through the CONNECT allowlist and records `depcache_unavailable`.
- Test (WP-05): a file written to every cache path or published to every cache in run A is invisible in run B.

**Presets.** `data/resource_policies/{frugal,standard,generous}.yaml` hold exactly the F, S and G columns above (WP-11 writes and tests them; all three MUST pass the policy lint in 4.3). `frugal` is the **default for new studies**. The UI calls a ResourcePolicy a **Budget preset**, shows a tier badge and a diff against the base tier whenever a custom version is used, and shows a one-line summary such as "Frugal v1 · $0.30/run · auto study cap · 2 parallel · effort low · no overrides". The always-visible **key limits** (`KNOB_META.key_limit`) are: study cap, run cap, cell cap, repeats, max parallel, reasoning, serving tier and pilot on/off.

### 4.3 Effective policy and policy lint (`budget/policy.py`, WP-11)

```python
def effective_policy(base: ResourcePolicy, overrides: dict | None, *, model: LLMModel, harness: HarnessAdapter | None,
                     study_ctx: StudyPolicyContext) -> EffectivePolicy   # content + clamps + notes + issues
```

Clamps, each recorded in `notes`:
- `call.max_output_tokens ≤ model.max_output − (reasoning.max_tokens or 0)`.
- `context.max_context_tokens ≤ model.context_length`.
- Sandbox values ≤ machine maxima, then raised to the study maximum over stack minimums.
- `reasoning` is dropped for models without reasoning support (`reasoning_unsupported`).
- `routing.max_price: "model×k"` resolves to k × the model's listed prices.
- `routing.serving_tier: auto_batch` resolves at study level (4.2).
- `budget_basis=tokens`: `run.max_cost_usd` becomes `max(policy value, p99 estimate × 1.5)` and class O.

`treatment_view()` returns the class C and R subset **after** clamping, so two policies that clamp to the same values hash equal.

Lint rules (issues with codes from the registry, 6.15):

| Code | Severity | Rule |
|---|---|---|
| `context_cap_above_call_cap` | error | `call.max_input_tokens < 1.1 × context.max_context_tokens` |
| `call_cap_without_compaction` | warn | `call.max_input_tokens` set and the harness reports `context_knob=None` |
| `reasoning_mode_invalid` | error | both `effort` and `max_tokens`, or neither for its mode |
| `reasoning_budget_too_large` | error | `reasoning.max_tokens ≥ call.max_output_tokens` |
| `reasoning_budget_too_small` | error | Anthropic models: `reasoning.max_tokens < 1024` |
| `output_cap_below_harness_min` | error | `call.max_output_tokens < HarnessFacts.min_call_output_tokens` |
| `slot_output_cap_tight` | warn | `1.6 × est_tokens(original) + r_avg > slot.max_output_tokens` |
| `brief_exceeds_call_cap` | error | rendered brief + harness overhead > `call.max_input_tokens` |
| `slot_context_exceeds_model` | error | resolved slot context + prompt > model context |
| `model_lacks_tools` | error → cell `excluded:config_invalid` at resolve | agentic harness with a model without tool calling |
| `batch_requires_single_shot` | error | `serving_tier=batch` with an agentic or slot-agent executor |
| `provider_unpinned` | error (confirmatory), info otherwise | not exactly one `routing.only` provider with `allow_fallbacks=false`, unless `analysis_plan.random_effects` contains `provider` |
| `usd_basis_multi_model` | warn (confirmatory) | `budget_basis=usd` with more than one model level |
| `resources_vary_by_stack` | error (confirmatory), warn otherwise | sandbox values differ between cells (only possible through a per-condition policy override) |
| `mixed_serving_tier` | error | serving tiers differ between compared conditions |
| `model_alias_in_confirmatory` | error | model slug ends `-latest`/`:latest` or is a router slug |
| `slot_sampling_provider_default` | error (slot) | slot study with `sampling.policy=provider_default` or temperature 0 |

**Per-study override.** The builder may edit knobs inline. The server stores the result as a new user-owned ResourcePolicy version (`derived_from` = the preset, `tier="custom"`). Studies always reference a stored version.

### 4.4 Cost and time estimation (`budget/estimate.py`, WP-11)

Input: the resolved cell (model pricing, harness, task `size_bucket`, rendered prompt token estimates) and the effective policy. Output per cell: `{expected_usd, expected_list_usd, p10_usd, p90_usd, cap_usd, expected_tokens, expected_wall_s, p90_wall_s, basis: "prior:<key>(n)"|"heuristic"}`.

1. **Prior lookup** (`estimate.method=priors`). Try `CostPrior` keys in this order: `kind|harness|model|size|tier`, then `kind|harness|model|*|tier`, then `kind|harness|model_family|size|*`, then `kind|harness|*|size|*`. A key is used only when `n ≥ 5`.
   - Pricing drift: `expected = cost_p50 × (price_now / price_at)`, blended by the prior's token mix.
   - Routing calibration: × the historical `median(cost / catalog_cost)` for (model, routing policy).
2. **Heuristic** (agentic). `R = min(run.max_llm_requests, r0[harness])` with `r0 = {opencode: 45, claude_code: 40, codex: 40, goose: 35, qwen_code: 40, gemini_cli: 40, mini_swe_agent: 50, aider: 6}` (`budget/constants.py`, calibrated from priors; seeds from live data: OpenCode ≈2.1M tokens/64 calls, mini-swe-agent ≈1.7M/48, Aider ≈52k/3).
   - Per-call prompt: `in_k = min(P0 + g·k, C)` for `k = 1..R`, with `P0 = est_tokens(brief) + harness_overhead[harness]`, `g = 1800`, `C = min(context.max_context_tokens or model.context_length, call.max_input_tokens or ∞)`.
   - `IN = Σ in_k`, `OUT = R·o_avg` with `o_avg = min(call.max_output_tokens, 900)`, `RSN = R·r_avg(reasoning)` with `r_avg = {none: 0, minimal: 100, low: 400, medium: 1200, high: 3000, xhigh: 5000, provider_default: 1200}` or `reasoning.max_tokens × 0.6`.
   - Caching: hit share `h = 0.8` when `caching.prompt_cache ≠ off` and the model caches (history value when available), else 0; discount `d = 1 − cached_price/input_price` (0.75 when unknown); cache writes priced at `cache_write_per_mtok` for explicit-cache providers.
   - `expected = IN·p_in·(1 − h·d) + (OUT + RSN)·p_out`; `p10 = 0.4·expected`; `p90 = 2.2·expected`.
3. **Slot single-shot** (nearly exact). `IN = est_tokens(rendered system+user)` (`tiktoken o200k_base` when available, else `ceil(chars / 3.6)`; method recorded). `OUT = min(slot.max_output_tokens, 1.6·est_tokens(original part) + 150) + r_avg`. Shared context across the N replicates is cached after the first call (`h = (N−1)/N × context share`). `expected = (IN·p_in·(1 − h·d) + OUT·p_out) × tier_factor` with `tier_factor = 0.5` for `batch`; `p10 = 0.7·e`; `p90 = 1.6·e`.
4. **Slot agent** uses the agentic heuristic with `r0 ×= 0.5` and a `P0` that includes the context-file listing.
5. `cap_usd = min(run.max_cost_usd, R_max·(C·p_in + call.max_output_tokens·p_out) + aux)` is the theoretical worst case per attempt (for slot single-shot: one call's worst case). This, not `run.max_cost_usd`, is what the scheduler reserves.
6. **Failure overhead**: × (1 + historical failed-call cost share) + `infra_retry_rate × mean partial-run cost`.
7. **Subscription and local endpoints**: `expected_list_usd` at list price; tokens against quota SpendCaps (time to quota exhaustion is shown).
8. **Study** (`Estimate`):
   - `expected_total`, `p90_total` (sum of p90; conservative, documented as such), `worst_case_total = min(study cap, Σ cell caps)`, list-price totals, token totals;
   - `by_condition[]` and `by_level{factor: {level: usd}}` (which model or level drives cost);
   - `fit {max_repeats_under_cap, max_units_under_cap, blocks_under_cap}` from P50 per block;
   - time: `Σ expected_wall / effective_parallel` with `effective_parallel = min(study_max_parallel, Σ endpoint free slots)`, as a range, plus evaluation machine minutes (build, test, analysis) separately.
9. Issues: `estimate_exceeds_cap` — **warn** when `p90_total > study.max_cost_usd`, **error** when `expected_total > study.max_cost_usd` — each with quick fixes "raise cap to X" and "reduce repeats to `fit.max_repeats_under_cap`".
10. **Re-estimate after the pilot**: replace the prior with the pilot's per-cell observations (log-normal update weighted by n) and show the change before the expansion approval.

The estimate is recomputed on every resolve and stored in `StudyPlan.estimate` at freeze/launch, together with its inputs and history window, so it can be compared with realised spend. At launch the UI shows `expected`, `p90`, `worst_case`, the cap (resolved from `auto`) and the fit, and requires typed confirmation when `p90_total > estimate.require_confirm_above_usd`.

### 4.5 Reservations, blocks and live spend (`budget/ledger.py`, `budget/live.py`, scheduler hooks; WP-11)

**Block claiming.** The claim order is the block sequence (9.4): blocks sorted by `(repeat_index, unit_order)`, conditions shuffled inside each block. A block is *started* when its first cell is claimed.

```
before claiming cell c (attempt a) of study s, in one transaction (select_for_update on the Study row):
  need = min(run.max_cost_usd, c.cap_usd)                     # in s.cap_basis; tokens likewise when token caps apply
  if c.block not started:
      block_need = Σ need over the block's unclaimed cells
      if s.spent + s.open_reserved + block_need > s.cap (any basis):
          apply s.on_budget_exhausted:
            stop_balanced -> claim no new block; started blocks finish; study -> stopped(budget) when idle
            pause         -> study paused(budget); nothing new claimed until the cap is raised
            stop          -> cancel pending runs on commit; study stopped(budget)
          return
      reserve block_need (dedupe "blk:{s}:{block}")              # holds budget for the whole block
  if c.spent + need > c.cell_cap: exclude cell (cell_budget); release its slice; return
  if any SpendCap (user/endpoint/model/global, period, unit) would be exceeded (action=block):
      pause study (reason spend_cap, or quota for 5h/week model caps); return   # infra: runs re-queue later
  move need from the block reservation to the run (dedupe "res:{run}:{a}")
```

- Retries of `infra_error` draw a fresh reservation from the remaining budget (not from the block).
- Paired and by-condition results use **complete blocks** only; cells excluded by `cell_budget` or a budget stop are reported per condition (`attrition` table, 6.13).
- Invariant test (WP-11): after any budget stop, cell counts per (condition, unit) differ by at most 1.

**Gateway pre-call reservation (WP-06).** Before forwarding a call, the gateway computes the worst case `w = est_prompt_tokens × p_in + upstream_max_tokens × p_out` (prices in `GatewayConfig`, from the manifest) and refuses with `budget_exhausted:cost` when `cost + Σ inflight_reserved + w > run.max_cost_usd`. Token caps work the same way. It never clamps `max_tokens`. This removes the overshoot of today's check-before-call rule.

**Usage while the attempt runs:**
- The sandbox supervisor polls the gateway every `BUDGET_LIVE_POLL_S` (10 s × ceil(running/8)), using `docker exec <gw> cat /logs/state.json`. The gateway rewrites that file atomically after every call: `{requests_by_class, prompt_tokens, completion_tokens, reasoning_tokens, cached_tokens, cache_write_tokens, cost, list_cost, inflight, last_call_end, budget_exhausted, call_truncations, updated}`.
- The supervisor writes the delta as `usage` with `dedupe=f"live:{r}:{a}:{seq}"` and publishes SSE `study:<id>` `spend` and `run:<id>` `usage`.
- Local endpoints: `proxy_server.py` writes usage per completed `LLMRequest` (`dedupe=f"proxy:{llm_request_id}"`); cost is 0 USD (or list price per `cost_basis`), tokens are tracked against token caps.

**Settlement at the end of the attempt:**
- Read the final `calls.jsonl`.
- `final_cost = Σ usage.cost`, preferring OpenRouter's billed `usage.cost`. When it is missing, use the `/generation` backfill (existing `openrouter_stats`); as a last resort, tokens × manifest pricing with `source=price_table`. `list_cost` is always computed from the manifest pricing.
- Write `adjust` = final − Σ live, then `release` = −reserve.
- Update `RunAttempt.usage`, `StudyCell.spent_usd`, the Study caches and quota windows.

Idempotence: every write uses a `dedupe_key`. A crash between steps is repaired by the reconciler, which settles any attempt that has ended and still holds an open reservation, and releases block reservations of finished blocks.

Invariant (tested): at any instant `spent + open_reserved ≤ study cap + ε` (ε = live-poll lag × max burn, documented). The gateway cap equals the run reservation, and the pre-call reservation bounds each in-flight call.

### 4.6 Stop rules, pilot and early stopping (`budget/stop_rules.py`, evaluated by STU after every run completion and every 60 s)

```python
class StopRule(BaseModel):
    code: Literal["infra_error_rate","no_provider_rate","output_invalid_rate","consecutive_failures","cost_overrun",
                  "spend_fraction","condition_error_rate","ci_width_reached","wall_clock_total"]
    scope: Literal["study","condition","unit"] = "study"
    window: int = 20                       # last N finished runs in scope
    threshold: float
    action: Literal["pause","stop","pause_condition","alert"]
    stages: list[Literal["pilot","main"]] = ["pilot","main"]
    # ci_width_reached only:
    features: list[str] = []               # must be a subset of Design.slot_primary_features
    n_min: int = 10; n_max: int | None = None   # n_max defaults to repeats
```

Default set (all tiers):
- `infra_error_rate > 0.30` over a window of 20 → `pause` (infra; jobs re-queued, never dropped).
- `no_provider_rate > 0.20` → `pause` (usually `routing.max_price` too tight; the UI offers "raise max_price").
- `consecutive_failures ≥ 5` per condition (`outcome ∈ {infra_error, build_fail}` with zero `tested`) → `pause_condition` (exploratory: may be `stop` only if declared).
- `cost_overrun`: a condition's mean cost > 2.0 × its estimate after ≥ 5 runs → `pause_condition` and an alert.
- `spend_fraction ≥ 1.0` → handled by `on_budget_exhausted` (the rule makes it visible).

Optional:
- `ci_width_reached` (slot, exploratory only): stop a condition×target once, for every listed primary feature, the Wilson 95% half-width is below the threshold and `n ≥ n_min`. It needs `Design.slot_primary_features`, which are scored live (7.9 step 0). It is recorded as `early_stop`; the affected cells get `adaptive_n=true`, and their prevalence rows use the conservative repeated-look interval (Wilson at α/K, K = number of looks evaluated) and are exported with `adaptive_n=true`. The PowerHelper shows the expected saving ("stops at ≈n=… if p≈…").
- `output_invalid_rate` (exploratory only, unless pre-registered as futility).

**Pilot.** Launch creates stage `pilot` cells only (`pilot.repeats` × `pilot.units`), preceded by smoke cells when `pilot.smoke_first`. `POST /studies/{id}/expand/` evaluates `pilot.gates` (4.2) and shows them on `PilotGateCard`, including a "cap hits by kind" row with the action "raise run cap → re-pilot". Re-pilot: edit the policy (new plan_hash), launch a new pilot; the old pilot runs stay for reference and are never pooled with a different `plan_hash` (pilot and main plans with different hashes are never pooled, in any study kind).

**Confirmatory studies.** A rule action that stops or pauses a condition makes the affected cells `stopped_early` in the gate, so the gate reports a deviation. Adaptive precision stopping is refused at plan time (`adaptive_stop_in_confirmatory`). Pre-registered **group-sequential** designs are allowed through `analysis_plan.sequential = {looks: [n...], alpha_spending: "obrien_fleming"|"pocock", futility: {type: "conditional_power", threshold}, binding: false}`; interim looks are computed by the plan engine and interim p-values stay withheld (existing invariant).

### 4.7 Live spend tracking (UI contract)

- `GET /budget/studies/{id}/` returns `{cap, cap_basis, spent, spent_list, reserved, remaining, expected_remaining, tokens: {spent, cap}, burn_rate_usd_per_h, eta_s, by_condition: [{condition, spent, runs, usable, tested, cost_per_usable_sample, cost_per_tested_sample, excluded_cell_budget}], by_model: [...], alerts: [...], blocks: {started, complete, total}}`.
- SSE `study:<id>` emits `spend` `{spent, spent_list, reserved, delta, run_id}`, `budget_alert` `{fraction}`, `stopped` `{reason}` and `paused` `{reason, condition_id?}`.
- `GET /budget/me/` returns the user's period totals (billed and list) against their SpendCaps, and `GET /budget/header/` the compact payload for the global header spend chip (period spend vs cap, and a status badge when any study is paused for `budget`, `quota` or `spend_cap`).

### 4.8 Evaluation level (`Design.evaluation`, enforced by `evaluation/levels.py`, WP-09)

Evaluation costs machine time, not USD, and determines which outcomes exist. It is part of the design (in `prereg_hash`), not of the budget preset.

```python
class EvaluationSpec(BaseModel):
    eval_suite: str                                    # pinned ref
    eval_level: Literal["none","static","build","functional","security","full"] = "security"
    overrides: {"build": bool | None, "functional": bool | None, "probes": bool | None, "adherence": bool | None,
                "baseline": bool | None, "analysis_static": Literal["none","default"] | list[str] | None,
                "analysis_dynamic": bool | None, "only_passing": bool | None, "human_eval_pool": bool | None} = {}
    slot: {"mode": Literal["full","overlay","static_only"] = "full", "regression": bool = False} | None
    retest_fraction: float = 0.05                      # random re-evaluations for test-retest agreement
```

| Level | lock | build | start + functional | probes | adherence | baseline | SAST (default tools) | DAST |
|---|---|---|---|---|---|---|---|---|
| `none` | – | – | – | – | – | – | – | – |
| `static` | ✓ | – | – | – | ✓ (static part) | – | ✓ | – |
| `build` | ✓ | ✓ | – | – | ✓ | – | – | – |
| `functional` | ✓ | ✓ | ✓ | – | ✓ | ✓ | – | – |
| `security` (default, every tier) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – |
| `full` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Rules:
- In slot mode the target's `linked_probes` always run when the slot is evaluated at `functional` or above.
- A run whose outcome is `output_invalid` is never built.
- `slot.mode=overlay` (run the parent image with the variant file copied in, no rebuild) is allowed only for interpreted backends (python, node), when the variant adds no dependency and the slot is not a frontend file that needs a build; otherwise the variant falls back to `full` (recorded). Mixing modes across compared conditions gives `mixed_eval_mode` (error in confirmatory).
- `only_passing=true` biases conditional analyses; it is recorded and shown.
- Anything a level skips is reported as **not measured** in results, never as clean or 0.

---

## 5. Harness adapter interface

### 5.1 ABC (`generation/harnesses/base.py`, WP-07)

```python
WireProtocol = Literal["openai_chat", "openai_responses", "anthropic_messages", "gemini_generate"]

@dataclass(frozen=True)
class HarnessCapabilities:
    agentic: bool                  # can run shell / iterate (False: aider = editor)
    requires_tool_calling: bool
    supports_max_turns: bool
    supports_system_prompt: Literal["none","append","override","both"]
    supports_tool_policy: bool     # can deny/allow tools by name
    supports_compaction_control: bool
    supports_context_limit: bool
    supports_reasoning_effort_passthrough: bool   # False: the gateway forces it anyway
    supports_path_restriction: bool   # can be told to edit only some paths (slot agent); else allowlist-diff only
    supports_request_class: bool      # tags its requests with a class header the gateway can read
    headless_flag: str             # documentation only

@dataclass(frozen=True)
class ImageSpec:
    image: str                     # images/<dir>
    build_args: dict[str, str]     # version pins
    lock_files: tuple[str, ...]    # requirements.lock / package-lock.json / Cargo.lock inside the image dir
    base: str = "agent-base"

@dataclass(frozen=True)
class HarnessContext:              # everything an adapter may read; built by agentic/runner.py
    run_id: str; workspace: str = "/workspace"; brief_path: str = "/run/brief.md"
    brief: str                     # rendered brief (never passed via argv)
    system_prompt: str | None      # when PromptSet.system_prompt_mode != harness_native
    model_name: str                # the name the harness must send (gateway pins the real one)
    gateway: "GatewayWiring"
    preset: HarnessPresetContent; policy: EffectivePolicy
    slot: SlotAgentContext | None  # allowlist, target description (slot agent only)

@dataclass(frozen=True)
class GatewayWiring:
    protocol: WireProtocol; protocol_mode: Literal["native","passthrough","translate"]
    base_url: str                  # e.g. http://gateway:8080/v1  (anthropic: http://gateway:8080)
    api_key: str = "sk-llmlab-dummy"
    env: dict[str, str] = field(default_factory=dict)   # harness-specific env (ANTHROPIC_BASE_URL, OPENAI_BASE_URL, ...)

@dataclass(frozen=True)
class RenderedHarness:
    files: dict[str, bytes]        # absolute in-container paths -> bytes (configs, brief copy, prompt files)
    env: dict[str, str]
    command: str                   # shell command run under `timeout -k 15 <wall>` by the common entry script
    stdin_path: str | None = None
    artifacts: tuple[str, ...] = ()   # extra paths to collect into evidence (session logs)

class Termination(StrEnum):
    COMPLETED="completed"; WALL_CLOCK="wall_clock"; IDLE="idle_timeout"; BUDGET="budget_exhausted"
    HARNESS_ERROR="harness_error"; HARNESS_CRASH="harness_crash"; LLM_ERROR="llm_error"
    INFRA="infra_interrupted"; CANCELLED="cancelled"; MAX_TURNS="max_turns"; OOM="oom_killed"

@dataclass(frozen=True)
class ExitInfo: exit_code: int | None; elapsed_s: float; wall_clock_s: int; idle_fired: bool; oom_killed: bool
    budget_exhausted: str | None; cancelled: bool; gateway_calls_ok: int; gateway_calls_failed_transport: int

class HarnessAdapter(ABC):
    name: ClassVar[str]; label: ClassVar[str]; protocol: ClassVar[WireProtocol]
    capabilities: ClassVar[HarnessCapabilities]
    version_setting: ClassVar[str]            # settings.AGENT_<NAME>_VERSION
    option_fields: ClassVar[tuple[OptionField, ...]] = ()   # OptionField kept from today's base.py
    facts: ClassVar["HarnessFacts"]           # 5.3; MUST be filled before the adapter is enabled

    @abstractmethod
    def image_spec(self, version: str) -> ImageSpec: ...
    @abstractmethod
    def gateway_wiring(self, gateway_host: str) -> GatewayWiring: ...
    @abstractmethod
    def render(self, ctx: HarnessContext) -> RenderedHarness: ...
    @abstractmethod
    def parse_trajectory(self, outputs: dict[str, bytes], calls: list[dict]) -> "Trajectory": ...
    @abstractmethod
    def observed_inputs(self, calls: list[dict]) -> "ObservedInputs": ...
    def classify_termination(self, exit: ExitInfo, trajectory: "Trajectory") -> Termination:
        """Default (all adapters inherit unless facts say otherwise):
        cancelled -> CANCELLED; oom_killed -> OOM; elapsed >= wall_clock - 2 -> WALL_CLOCK (never by exit code 137/143);
        idle_fired -> IDLE; budget_exhausted -> BUDGET; gateway_calls_ok == 0 and transport failures -> INFRA;
        trajectory.ended_on_llm_error -> LLM_ERROR; trajectory.max_turns_hit -> MAX_TURNS;
        exit_code in facts.exit_codes -> mapped (e.g. qwen 53 -> MAX_TURNS); exit_code == 0 -> COMPLETED;
        exit_code in facts.crash_codes or killed by signal -> HARNESS_CRASH; else HARNESS_ERROR.
        Section 6.12 maps each Termination to an outcome."""
    def normalize_options(self, options: dict) -> dict: ...      # unchanged semantics from today
    def restrict_paths(self, ctx: HarnessContext) -> RenderedHarness | None:
        """Slot agent: return a variant render that tells/forces the harness to edit only ctx.slot.allowlist,
        or None when unsupported (the allowlist-diff check still applies)."""
        return None
```

`Trajectory` and `ObservedInputs` live in `contracts/harness.py`. They are ported from `services/agent/trajectory.py`:

```python
class Step(BaseModel): index: int; kind: Literal["llm","tool","edit","shell","message","compaction","subagent"]
    tool: str | None; ok: bool | None; started: float | None; duration_s: float | None; tokens_in: int; tokens_out: int
    files: list[str] = []; command_sha: str | None = None
class Trajectory(BaseModel): steps: list[Step]; final_message: str | None; final_message_truncated: bool
    tool_calls: int; edits: int; shell_commands: int; compactions: int; subagent_calls: int
    ended_on_llm_error: bool; max_turns_hit: bool; parser: str; parser_version: int; unparsed_lines: int
class ObservedInputs(BaseModel):
    system_prompt_sha: str | None   # normalized: date/model-name lines replaced by facts.system_prompt_normalizers
    tool_names: list[str]; tool_specs_sha: str | None; first_user_message_sha: str | None
    calls_by_class: dict[str, int]   # main / subagent / compaction / auxiliary (from the gateway request_class)
    native_system_prompt_blob: str | None   # from the first main-class call in the gateway log
```

### 5.2 Rules every adapter MUST follow

1. The brief goes to a file or stdin, never to argv.
2. No auto-update, telemetry, sharing, web fetch or search, or reading of user/project config (AGENTS.md, CLAUDE.md, .codex, .gemini, .qwen, .goose), unless the preset explicitly enables one of these as a documented option.
3. The model is pinned by the gateway. The adapter presents `ctx.model_name` and also sets every "small/fast/auxiliary model" knob to the same name, so auxiliary calls go to the run's model.
4. Approvals and permissions are fully automatic inside the sandbox (the container is the boundary).
5. `max_turns`, compaction and the context limit are mapped when `capabilities` says so. Otherwise the adapter records `facts.unsupported_knobs` in the manifest and the gateway limits apply.
6. Each adapter ships a captured-native-prompt fixture `harnesses/captured/<name>-<version>.md` and a trajectory fixture `tests/wp07_harnesses/fixtures/<name>-<version>/` made from a real smoke run.
7. The native system prompt is observed, not trusted: the gateway logs the first `main`-class request's system prompt; its normalized hash is `observed_inputs.system_prompt_sha`.
8. Harness-side cost caps are never set; the gateway is authoritative (4.1).
9. Every documented auxiliary-call source is suppressed or pinned to the run's model, and every call is classified by request class (5.4).

### 5.3 Harness facts (`HarnessFacts`) and per-harness decisions

Each adapter module defines `facts = HarnessFacts(...)`:

```python
@dataclass(frozen=True)
class HarnessFacts:
    facts_version: int
    verified_against: str              # "<package>@<version> source/commit + date"
    license: str
    install: str                       # pinned install line used by the image Dockerfile
    headless_cmd: str                  # frozen command (brief via stdin or file, never argv)
    front_protocol: WireProtocol
    protocol_modes: tuple[Literal["native","passthrough","translate"], ...]   # supported, in preference order
    base_url_env: dict[str, str]       # env names -> value template
    auth_env: dict[str, str]
    model_flags: str
    aux_model_knobs: list[str]         # every small/fast/subagent model knob -> the run's model
    disable_knobs: dict[str, str]      # telemetry/update/web/config-discovery switches
    max_turns_flag: str | None; compaction_knob: str | None; context_knob: str | None
    output_cap_knob: str | None; reasoning_knob: str | None; cache_knob: str | None
    tool_policy_flags: str | None; system_prompt_flags: str | None; path_restriction: str | None
    system_prompt_capture: Literal["gateway_log","dump_env","pinned_file"]
    request_class_source: str          # header name, or "default:main"
    min_call_output_tokens: int        # the resolver errors when call.max_output_tokens is below it
    vendor_affinity: str | None        # e.g. "anthropic" for Claude Code; reported with results
    trajectory_source: str             # stdout jsonl / session file path
    exit_codes: dict[int, str]         # documented harness exit codes -> meaning
    crash_codes: tuple[int, ...]
    system_prompt_normalizers: tuple[str, ...]   # regexes (date, cwd, git status, model name)
    unsupported_knobs: tuple[str, ...]
    open_questions: tuple[str, ...]    # MUST be empty before `enabled=True` in registry
```

The registry exposes `enabled` per adapter (`registry.py`). An adapter with a non-empty `open_questions` is listed with `enabled=False` and `blocked_reason`, and the plan resolver refuses it (issue `harness_not_enabled`). An adapter whose front protocol has no available mode for a cell's endpoint (for example passthrough-only Claude Code on a local endpoint before the translator lands) gives `harness_protocol_unavailable_for_endpoint` (error).

**Summary** (versions checked 2026-11-02 against npm and GitHub releases; each MUST be re-verified when pinned):

| Harness | Pin | License | Front protocol | Gateway mode (first → later) | Turn cap | Enable in |
|---|---|---|---|---|---|---|
| opencode | `1.18.35` (ported) | MIT | openai_chat | native | gateway only | wave 2 (port) |
| mini-swe-agent | `2.4.6` (ported) | MIT | openai_chat (litellm) | native | `step_limit` | wave 2 (port) |
| aider | `0.86.2` (ported) | Apache-2.0 | openai_chat (litellm) | native | n/a (editor) | wave 2 (port) |
| goose | `v1.57.0` (repo `aaif-goose/goose`) | Apache-2.0 | openai_chat | native | `--max-turns` | wave 2 (first new) |
| qwen-code | `@qwen-code/qwen-code@0.26.6` | Apache-2.0 | openai_chat | native | `--max-session-turns`, `--max-tool-calls` | wave 2 |
| claude-code | `@anthropic-ai/claude-code@2.1.316` | proprietary (Anthropic commercial terms) | anthropic_messages | passthrough (OpenRouter `/api/v1/messages`) → translate (local endpoints, wave 3) | `--max-turns` | wave 2 (passthrough) |
| codex | `@openai/codex@0.166.0` | Apache-2.0 | openai_responses (`wire_api="chat"` was removed in early 2026) | passthrough (OpenRouter `/api/v1/responses`, stateless) → translate (wave 3) | none (gateway `max_llm_requests`) | wave 2 (passthrough) |
| gemini-cli | `@google/gemini-cli@0.66.0` | Apache-2.0 | gemini_generate | translate only (no OpenRouter-native Gemini endpoint) + loopback forwarder | `model.maxSessionTurns` | wave 3 (last) |

Common rules for all new harnesses: the image is `FROM agent-base` (Node 24 + Python 3.13 + git + ripgrep, user `node`); config lives outside `/workspace` in a fresh `HOME`/config dir; telemetry, auto-update, web tools and project/user context files are disabled; the gateway pins the model regardless of what the harness sends; budget-exhausted replies use the protocol's native error shape with wording that MUST NOT look like a context-overflow message.

**opencode (ported).** Custom provider in `opencode.json` (`baseURL`, dummy key), `opencode run --format json --pure < brief`, pinned model-agnostic default prompt, `webfetch/websearch/skill` disabled, `OPENCODE_DISABLE_*` env, `small_model` = run model. Knobs: `limit.context` (context), `compaction.auto` / `compaction.prune` (compaction), `limit.output`. Tool set varies by model id (`apply_patch` for GPT ids): record `tool_names`, note `tool_set_varies`. `min_call_output_tokens = 4096`.

**mini-swe-agent (ported).** `OPENAI_API_BASE` / `model_kwargs.api_base`; `step_limit = run.max_llm_requests`; `cost_limit = 0`; `environment.timeout = time.command_timeout_s`. No compaction (history grows linearly; `context_knob=None`, so `call_cap_without_compaction` applies). `min_call_output_tokens = 2048`.

**aider (ported).** `--openai-api-base`; `edit_format` (diff), `map_tokens` (repo map; 512/1024/2048 by tier as a C-class harness option). `agentic=False`: MUST use the PromptSet role `brief` variant tagged `editor` (lint issue `editor_harness_agent_brief`). Crossing aider with agentic harnesses gives `harness_brief_differs` (warn; the harness factor is then confounded with the prompt). Whole-file edits need large outputs: `min_call_output_tokens = 8192`.

**goose.** Install: release tarball `goose-x86_64-unknown-linux-gnu.tar.bz2` v1.57.0, sha256-verified in the Dockerfile; check `ldd` (set `GOOSE_DISABLE_KEYRING=1`). Wiring: `GOOSE_PROVIDER=openai`, `OPENAI_HOST=http://llm-gateway:8080`, `OPENAI_BASE_PATH=api/v1/chat/completions`, `OPENAI_API_KEY=$DUMMY`, `GOOSE_MODEL=<slug>`. Command: `goose run --no-session --output-format stream-json --with-builtin developer --max-turns N --max-tool-repetitions K -i /run/brief.md`. Env: `GOOSE_MODE=auto`, `GOOSE_DISABLE_SESSION_NAMING=true` (removes an auxiliary call), `GOOSE_TELEMETRY_ENABLED=false`, `OTEL_SDK_DISABLED=true`, `CONTEXT_FILE_NAMES='[]'`, fresh `HOME`/XDG. Knobs: `GOOSE_CONTEXT_STRATEGY=summarize`, `GOOSE_AUTO_COMPACT_THRESHOLD`, `GOOSE_AUTO_COMPACT_TOKEN_LIMIT` (context), `GOOSE_MAX_TOKENS` (output), `GOOSE_SUBAGENT_MAX_TURNS`; `GOOSE_TOOLSHIM` stays off. System prompt: template in the Goose source; captured from the gateway log. Request class: `default:main`. Open questions to close before enabling: the exact stream-json schema (fixture from the pinned binary), whether the `openai` provider switches to Responses for some slugs, runtime library deps. `min_call_output_tokens = 4096`.

**qwen-code.** Install: `npm i -g @qwen-code/qwen-code@0.26.6` (Node ≥ 22). Wiring: `OPENAI_BASE_URL=http://llm-gateway:8080/api/v1`, `OPENAI_API_KEY=$DUMMY`, `OPENAI_MODEL=<slug>`, `~/.qwen/settings.json` with `security.auth.selectedType: "openai"`. Command: `qwen --approval-mode yolo --output-format stream-json --max-session-turns N --max-tool-calls M --max-wall-time <wall> --exclude-tools web_fetch,web_search --safe-mode` with the brief on stdin. Settings: `privacy.usageStatisticsEnabled=false`, `general.enableAutoUpdate=false`, `model.generationConfig.{contextWindowSize, timeout, maxRetries: 0, enableCacheControl}`, `model.sessionTokenLimit`, `model.skipNextSpeakerCheck=true` (removes an auxiliary call). The system prompt is **pinned** with `QWEN_SYSTEM_MD` (captured default) because Qwen selects model-specific prompt and reasoning profiles; the chosen profile is recorded. Exit codes 53 (turn cap) → `MAX_TURNS`, 55 (wall/tool-call cap) → `WALL_CLOCK`/`BUDGET`. Open questions: stdin-only prompt in `-p` mode, stream-json field names, prompt dump variable, per-model profile auto-selection. `min_call_output_tokens = 4096`.

**claude-code.** Install: `npm i -g @anthropic-ai/claude-code@2.1.316` (verify whether postinstall fetches a platform binary; build-time network is allowed). Command:
`claude --bare -p --output-format stream-json --verbose --no-session-persistence --permission-mode bypassPermissions --max-turns N --model "$SLUG" --disallowedTools "WebFetch,WebSearch" < /run/brief.md` (`--tools` fixed list in the controlled preset; subagents `Task`/`Agent` are a preset option, default **off**). `--bare` skips CLAUDE.md, hooks, skills, plugins, MCP, auto-memory and system reminders; this behaviour choice is recorded. Env: `ANTHROPIC_BASE_URL=http://llm-gateway:8080`, `ANTHROPIC_AUTH_TOKEN=$DUMMY`, `ANTHROPIC_API_KEY=""`, `ANTHROPIC_MODEL` and `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL` = slug, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`, `DISABLE_AUTOUPDATER=1`, `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`, `CLAUDE_CONFIG_DIR` outside `/workspace`; knobs `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, `MAX_THINKING_TOKENS`, `BASH_DEFAULT_TIMEOUT_MS`/`BASH_MAX_TIMEOUT_MS`, `DISABLE_PROMPT_CACHING`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (can only lower the threshold). `--max-budget-usd` is never used. Gateway obligations: route `POST /v1/messages?beta=true`; answer `/v1/messages/count_tokens` locally (estimate, not a billable call); reject `HEAD /api/hello` and serve an Anthropic-shaped `GET /v1/models`; emit SSE `event: ping` during upstream silence; consume `anthropic-version`/`anthropic-beta`; map `thinking` (enabled/adaptive) and `output_config.effort` to canonical `reasoning`, then let the policy override; keep `cache_control` on content parts unless `caching.prompt_cache=off`; log `x-claude-code-request-class` (`main|subagent|workflow|compaction|auxiliary`), `x-claude-code-session-id`, `x-claude-code-agent-id` and `x-claude-code-prompt-id` in `calls.jsonl`. Trajectory: stream-json (`system/init`, `assistant` blocks with `tool_use`, `user` `tool_result`, `system/api_retry`, final `result{subtype, num_turns, usage}`; `total_cost_usd` is ignored). `vendor_affinity="anthropic"`: Anthropic does not support Claude Code with non-Claude models; such cells are legitimate but carry a harness-model affinity confound that results report. Open questions: exact env names for the pinned version, OpenRouter `/messages` acceptance of `provider` routing fields and `usage.cost` in the native response (else `/generation` backfill), `--bare` + `--tools` defaults. `min_call_output_tokens = 16384` (large `Write` tool calls).

**codex.** Install: `npm i -g @openai/codex@0.166.0` (prebuilt Rust binary). `CODEX_HOME=/tmp/codex-home/config.toml`:
`model`, `model_provider="llmlab"`, `approval_policy="never"`, `sandbox_mode="danger-full-access"` (the container is the boundary), `web_search="disabled"`, `check_for_update_on_startup=false`, `model_context_window`, `model_auto_compact_token_limit` (compaction), `model_reasoning_effort`, `model_reasoning_summary="none"`, `tool_output_token_limit`, `project_doc_max_bytes=0`, `model_instructions_file` (pinned captured prompt), `[history] persistence="none"`, `[analytics] enabled=false`, `[otel]` exporters `none`, and `[model_providers.llmlab] base_url="http://llm-gateway:8080/api/v1", env_key, wire_api="responses", request_max_retries=0, stream_max_retries=0, stream_idle_timeout_ms=900000`. Command: `codex exec --json --skip-git-repo-check --ephemeral --sandbox danger-full-access - < /run/brief.md`. Turn cap: none; gateway `max_llm_requests` ends the turn with `turn.failed` (a clean `BUDGET` stop). Fairness: Codex picks instructions **and tools** per model family by slug prefix, so the adapter pins `model_instructions_file` and forces function-style tools (`apply_patch`/shell flags verified in the pinned `codex-rs`); the family resolution is recorded. Passthrough sends the native Responses body (`store:false`, `include:["reasoning.encrypted_content"]`) to OpenRouter's stateless Responses beta; the translator (wave 3) maps custom/freeform tools to a single-string-argument function tool and back. Trajectory: `--json` events (`thread.started`, `item.*` with `command_execution`, `file_change`, `agent_message`, `reasoning`; `turn.completed{usage}` cumulative). Open questions: tool-shape flags, family prompt selection for unknown slugs, `request_max_retries=0` acceptance, OpenRouter Responses usage/cost fields. `min_call_output_tokens = 8192`.

**gemini-cli.** Install: `npm i -g @google/gemini-cli@0.66.0`. It speaks only `POST {base}/v1beta/models/{model}:streamGenerateContent?alt=sse` (+ `:generateContent`, `:countTokens`) with `x-goog-api-key`. `GOOGLE_GEMINI_BASE_URL` must be HTTPS unless it points at localhost, so the entry script starts a ~15-line Node TCP forwarder `127.0.0.1:18080 → llm-gateway:8080` (neutral process name, documented) and sets `GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:18080`, `GEMINI_API_KEY=$DUMMY`, fresh HOME. Command: `gemini --approval-mode=yolo -o stream-json -m <slug> < /run/brief.md` with `GEMINI_SANDBOX=false` (yolo otherwise enables the Docker sandbox). Settings: `model.skipNextSpeakerCheck=true`, `model.disableLoopDetection=true`, explicit `-m` (no `auto` router), `model.compressionThreshold` (compaction), `model.maxSessionTurns` (turns), `tools.exclude: ["web_fetch","google_web_search"]`, telemetry, auto-update and checkpointing off, `experimental.gemmaModelRouter` off. System prompt: `GEMINI_WRITE_SYSTEM_MD` dumps it, `GEMINI_SYSTEM_MD` pins the captured default (model-dependent variants). The translator MUST round-trip `thoughtSignature` (Gemini 3 rejects tool turns without it; OpenRouter carries it in `reasoning_details`). `countTokens` is answered locally. Open questions: stdin-only prompt, generic model config for non-Gemini slugs, `GEMINI_SANDBOX=false` overriding the yolo default. `min_call_output_tokens = 4096`.

**Budget knob mapping per harness** (the gateway caps apply to every harness; these are the harness-side mappings `render()` applies):

| Knob | opencode | mini-swe | aider | claude-code | codex | goose | qwen-code | gemini-cli |
|---|---|---|---|---|---|---|---|---|
| `run.max_turns` | – | `step_limit` | – | `--max-turns` | – | `--max-turns` | `--max-session-turns` | `model.maxSessionTurns` |
| `call.max_output_tokens` | `limit.output` | model kwargs | metadata | `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | – | `GOOSE_MAX_TOKENS` | `samplingParams.max_tokens` | `generationConfig` |
| `context.max_context_tokens` | `limit.context` | – | metadata | autocompact pct (lower only) | `model_context_window` | `GOOSE_AUTO_COMPACT_TOKEN_LIMIT` | `contextWindowSize` | `compressionThreshold` |
| `context.compaction` | `compaction.*` | – | – | autocompact | `model_auto_compact_token_limit` | `GOOSE_CONTEXT_STRATEGY` | `autoCompactThreshold` | `compressionThreshold` |
| `reasoning` | gateway | gateway | gateway | `--effort` / `MAX_THINKING_TOKENS` | `model_reasoning_effort` | gateway | profile (recorded) | `thinkingConfig` |
| `caching.prompt_cache=off` | gateway strip | gateway | gateway | `DISABLE_PROMPT_CACHING` | gateway | gateway | `enableCacheControl=false` | gateway |
| `time.command_timeout_s` | bash timeout | `environment.timeout` | – | `BASH_*_TIMEOUT_MS` | `tool_output_token_limit`/exec timeout | developer ext. | settings | settings |
| aux-call suppression | `small_model`=run | – | – | nonessential traffic off, no subagents | `--ephemeral` | session naming off | `skipNextSpeakerCheck` | `skipNextSpeakerCheck`, `disableLoopDetection`, `-m` |

Harness-level knobs that change agent behaviour (turn caps, compaction, context) apply identically to every model within a harness (existing `option_fields` rule) and enter the harness section of the manifest.

### 5.4 Gateway protocol decisions (`images/llm-gateway`, WP-06)

1. **One policy path.** `gateway.py` stays the single authoritative policy, budget, retry and log point. The canonical internal format is OpenAI chat completions; the upstream stays OpenRouter or the executor's llmproxy (`llm-gateway-local/local.py` keeps working, since adapters call the canonical path rather than replacing it).
2. **Modes** (`protocol_mode`, recorded in the manifest and part of `treatment_hash` together with `front_protocol`):
   - `native`: the harness speaks openai_chat (opencode, mini-swe, aider, goose, qwen-code).
   - `passthrough`: the native body (Anthropic Messages or Responses) is forwarded to OpenRouter's native endpoint after policy is applied **on the native body** (model pin, `max_tokens`, `reasoning`/`thinking`, `provider` routing, `cache_control` handling, `session_id`). Usage is taken from the native response; cost from `usage.cost` when present, else `/generation` backfill. Remote endpoints only.
   - `translate`: stdlib translators `protocols/anthropic_messages.py`, `protocols/openai_responses.py` (700-900 lines each with streaming) and `protocols/gemini_generate.py` (500-700) map native ↔ canonical chat, including SSE re-encoding with keepalives, native error shapes and usage blocks. Needed for local endpoints (Claude Code, Codex) and always for Gemini CLI.
3. **No LiteLLM in the request path.** Reasons: a large fast-moving dependency in a frozen per-run image (the repo already hit an unimportable unpinned litellm), hundreds of MB and seconds of startup per sidecar at 25-100 replicates, its own retries/cost tracking would confound ours, translation bugs inside a third-party layer, and a reported 2026 supply-chain incident. LiteLLM and OpenRouter's native endpoints serve only as **offline test oracles** for the translators.
4. **Fidelity accounting.** Every translated call records `translation.dropped` (counts by field: thinking signatures, `cache_control`, unknown fields). Any drop adds the note `translation_lossy`. Reasoning continuity (Anthropic `thinking{signature}`/`redacted_thinking`, Responses `reasoning{encrypted_content}`, Gemini `thought`+`thoughtSignature` ↔ OpenRouter `reasoning_details[]`) MUST round-trip; golden fixtures per model family (Anthropic, OpenAI, open-weights) are required before a translator is enabled.
5. **Request classes.** The gateway records `request_class` per call from `HarnessFacts.request_class_source` (Claude Code: `x-claude-code-request-class`; others `main`, with auxiliary calls suppressed by harness settings). `run.max_llm_requests` counts `main` only; other classes count toward `run.max_aux_requests`. Results report requests by class.
6. **Phasing** (WP-06/WP-07): (a) goose and qwen-code, no gateway change; (b) passthrough for Claude Code and Codex; (c) Gemini translator + loopback; (d) Anthropic and Responses translators for local endpoints (wave 3). Each harness ships with a pinned version, a captured default prompt, a fixed tool set, telemetry/update off, a turn-cap mapping, a trajectory parser and a 3-model-family conformance fixture.

### 5.5 Controlled presets and harness bundles

Seeded alongside every `<harness>-native` preset is a `<harness>-controlled` preset: the same command timeout (300 s), the same tool denial (web), `max_turns` mapped from policy, subagents off and, where the harness allows it, a **pinned** captured system prompt. Controlled presets equalise these knobs only; they do **not** equalise system prompts or tool sets across harnesses. Cross-harness results therefore label the factor **"harness bundle"**. The default harness factor is `opencode-native`.

Issues: `mixed_harness_profiles` (warn) when a study crosses harnesses but mixes native and controlled presets; `harness_brief_differs` (warn) when aider is crossed with agentic harnesses; `vendor_affinity` (info) for Claude Code or Codex cells with non-vendor models.

---

## 6. Agentic run lifecycle (`app@1`)

Every numbered step names the owning module. Every step writes its outcome to the run (`phase`, `execution.steps[]` with timings) and publishes SSE `run:<id>` `{type: "step", step, status}`.

### 6.1 Resolve (`study/plan.py`, at dry run, freeze and launch)

For each cell, `resolve_cell(design, condition, unit, repeat) -> ResolvedCell` pins every factor to `slug@v#hash`. It never takes "latest" at run time; the latest version is chosen only at **plan** time. It then:
- renders stimuli through `prompts.render_set(prompt_set, context)` **once per (condition, unit)** (stimuli never depend on the repeat), keeping the source spans (9.2);
- computes the effective policy, its lint issues and the estimate (WP-11);
- resolves slot context per (target, context_policy) (`slot.context.resolve_context`, 7.4);
- derives the CRN seed and the block sequence (9.4);
- computes all identity keys with `identity.compute_keys` (3.1).

`resolve(design, persist=False)` is what the PlanRail calls; results are cached in-process by `design_hash` (10 min) and nothing is written. `persist=True` happens only on **Freeze plan** and at launch, which write the `StudyPlan` row and its payload blob.

Issues are typed (`{code, severity: error|warn|info, cells: [...], fix?}`) and their codes come from the registry (6.15). `error` blocks launch.

### 6.2 Manifest freeze (`agentic/manifest.py`)

At launch, `launch.py` creates the GenerationRun, the RunManifest (with `manifest_hash`), the stimulus blobs and the FK columns, all in one transaction. The manifest **copies** the identity keys from the `ResolvedCell`; `build_manifest` never recomputes them (a test asserts equality with `compute_keys` on the same inputs). It then calls `transaction.on_commit(lambda: execution.api.enqueue_runs(ids))`. Retries reuse the manifest unchanged and increase `attempt` only.

### 6.3 Image readiness (`agentic/images.py`)

- `ensure_images(manifest)` builds or pulls the harness image, the gateway image and the starter base images, and **compares digests** with the manifest.
- A mismatch is outcome `infra_error` with reason `image_digest_mismatch`. The run never proceeds silently on a different environment.
- Image tags are content hashes of their build context. Digests are resolved at plan time when the image exists (`images.resolve_digest(tag)`), or else at first build. The first run of a plan that builds an image records the digest, and the plan's later cells must match it (issue `image_built_after_plan`, info).

### 6.4 Workspace seed (`agentic/workspace.py`)

- **App mode.** The Starter's files come from the blob store and are written into a tar. The root `.gitignore` is the starter's own, if it has one; no platform excludes are injected into it.
- **Slot agent.** The parent's frozen tree is written with the hole applied, minus parent-authored tests unless `include_tests`, minus artifact rows (7.6).
- Every path is checked with `sanitize_rel_path` (ported).
- The seed tree hash is recorded as `result.seed_tree_hash`.

### 6.5 Sandbox (`agentic/sandbox.py`, ported)

Per-run resources:
- an internal network;
- the gateway sidecar, which is on the internal network plus egress;
- the agent container, with `sandbox.memory` and `nano_cpus` as resolved for the study (4.2), `cap_drop ALL`, `pids_limit 512`, the workspace volume and `/logs`. **No writable shared volume** is mounted; dependency caches are reached over the network (4.2), and Maven gets a throwaway `~/.m2`.

**Entry script** (`harnesses/base.py: entry_script()`, ported and fixed):
1. `git init` and commit the seed as `base`.
2. `git config core.excludesFile /dev/null`. `.git/info/exclude` gets anchored patterns only: `/node_modules/`, `/.venv/`, `/venv/`, `/data/*.db`, `/.tmp/`. **Nested directories are never excluded.**
3. Run `timeout -s TERM -k 15 <wall> <command>` (Gemini CLI: start the loopback forwarder first).
4. Write `git add -A && git diff --binary base > /logs/changes.patch`.
5. Run `tar --anchored --exclude=./node_modules --exclude=./.venv --exclude=./venv --exclude=./.git -C /workspace -cf /logs/workspace.tar .`. Patterns are anchored at the root, and a test covers nested `data/`, `dist/` and `build/`. A tar larger than `run.max_workspace_bytes` is truncated to a manifest of paths and the run gets `output_invalid:workspace_too_large`.

**Supervisor thread** (in the executor):
- It heartbeats the run lease.
- Live spend: polls `state.json` (4.5).
- **Idle detection:** the idle clock advances only while `state.inflight == 0` **and** the agent container's CPU usage (docker stats, 10 s window) is below 5% of one CPU. It resets on every `last_call_end` change and on workspace mtime change (`find /workspace -newer /logs/.idle_mark -print -quit` via exec). After `time.idle_timeout_s` it sends SIGTERM, then SIGKILL after 15 s, and sets `idle_fired`. A slow call in flight never counts as idle.
- **OOM:** after exit it reads `State.OOMKilled` and sets `oom_killed`.
- **Time split:** it records install time (processes matching the package-manager list), LLM time (gateway call durations) and tool time into `RunAttempt.time_split`.
- Cancellation.

### 6.6 Gateway (`images/llm-gateway`, WP-06)

The gateway is stdlib only, as today. Routes and modes are decided in 5.4. Additions:

- **Front protocols.** `gateway.py` routes:
  - `/v1/chat/completions` (and `*/chat/completions`) → openai_chat;
  - `/v1/responses` → openai_responses (passthrough or translate);
  - `/v1/messages` (+ `?beta=true`) → anthropic_messages; `/v1/messages/count_tokens` answered locally (`ceil(chars / 3.6)`, flagged as an estimate, not a call); `HEAD /api/hello` rejected; Anthropic-shaped `GET /v1/models`;
  - `/v1beta/models/{m}:generateContent`, `:streamGenerateContent` → gemini_generate; `:countTokens` local.

  Each front module lives in `images/llm-gateway/protocols/<name>.py` and exposes `to_chat(request_json) -> (chat_request, dropped)`, `from_chat(chat_response) -> native_response`, `stream_from_chat(chat_sse_iter) -> native_sse_iter` (with keepalive pings/comments during upstream silence), `error(kind, message) -> (status, body)` and, for passthrough, `apply_policy_native(body, policy) -> body`. The modules are pure functions, unit-tested with recorded fixtures from each harness's smoke run. Images in requests are rejected with 400 `unsupported_content`.
- **Policy application** on every call, after translation (or on the native body in passthrough):
  - model pin;
  - forced and stripped sampling;
  - `max_tokens = call.max_output_tokens + (reasoning.max_tokens or 0)`, clamped to the model limit;
  - `reasoning` per mode (exactly one of effort / max_tokens), `exclude` per 4.2;
  - `provider: {sort, max_price, allow_fallbacks, only, ignore, order, quantizations, require_parameters, data_collection, zdr, preferred_*}`;
  - `session_id = run uuid` when `caching.sticky_session`;
  - `cache_control` injection when `caching.prompt_cache=on` (breakpoints on the system message and the last tool-result message, TTL from `caching.ttl`), only for providers in `CACHE_CONTROL_PROVIDERS`; stripped when `off`;
  - flex serving via `:floor` when `serving_tier=flex`;
  - `usage: {include: true}` (a no-op on current OpenRouter, kept for older upstreams).
- **Budgets.** It enforces every GW knob in 4.2 with the pre-call reservation (4.5): `call.max_input_tokens` with the prompt estimate; token caps on accumulated usage plus in-flight; `run.max_cost_usd` on accumulated `usage.cost` plus Σ in-flight worst cases; request caps per request class; `concurrency.rpm_per_model` with a token bucket shared through the study's gateway config. When a budget is spent, the reply is the protocol's native error shape with status 400 and the text "Run budget exhausted. Stop working and summarise." It writes `budget_exhausted:<kind>` to `state.json` and `gateway_events.jsonl`.
- **Per-call record** (`calls.jsonl`, unchanged fields plus): `front_protocol`, `protocol_mode`, `request_class` (+ the raw Claude Code attribution headers), `finish_reason`, `response_model`, `system_fingerprint`, `served_provider`, `serving_tier`, `tokens_per_s`, `ttft_ms`, `translation_dropped`, `native_request_sha`, and the **native system prompt** of the first `main` call (blob), which gives uniform system-prompt observation for all eight harnesses.
- **`state.json`** is rewritten atomically after every call start and end (4.5), including `inflight` and `last_call_end`.
- **Truncation accounting.** Calls ending with `finish_reason=length` are counted (`call_truncations`); a run with any gets the note `call_truncations:n`. An empty response that consumed the whole cap on reasoning is a truncated call, never a clean one.
- **Local proxy parity.** `inference/proxy_server.py` applies the same budget checks: it shares `budget_rules.py`, which is copied into the gateway image at build time from `images/llm-gateway/protocols/budget_rules.py` (one source file, two consumers).

### 6.7 Harness execution

`adapter.render(ctx)` produces files, env and a command. The common entry script runs them. Outputs (`/logs/*`, harness artifacts) are copied to `GENERATION_RUNS_ROOT/<run>/a<attempt>/`.

### 6.8 Collector (`agentic/collector.py`)

The collector reads `workspace.tar` (not `git ls-files`) and diffs it against the seed tree:

| Origin | Rule |
|---|---|
| `starter` | The path exists in the seed with the same bytes |
| `modified` | The path exists in the seed with different bytes |
| `authored` | The path is not in the seed |
| `deleted` | The path is in the seed but missing from the final tree. The row is kept with `blob=""` |
| `artifact` | The path matches generated-artifact rules: build output (`dist/`, `build/` **at a frontend root**, `static/` produced by a build step that the Dockerfile also runs), `__pycache__`, `*.pyc`, `node_modules/` (never collected), `.tmp/`, `tmp/`, logs, `*.db`/`*.sqlite*` under the data dir, and coverage output |

`excluded` labels:
- `non_source`: `analysis/services/base.py:NON_SOURCE_DIRS`;
- `lockfile`;
- `binary`: NUL byte in the first 8 KB;
- `too_large`: > 1 MiB. Lockfiles are exempt up to 8 MiB, which fixes the 512 KB `package-lock.json` drop;
- `vendored`.

`loc` comes from `reports.services.loc.source_loc(path, text)` (added by WP-02b on top of `is_source_file`), and `is_test` from `is_test_path`.

The collector writes:
- `RunFile` rows (bulk);
- blobs;
- `run.tree_hash` = `tree_hash` over non-deleted, non-artifact, non-excluded-binary entries;
- `run.workspace_blob`.

**Empty output rule.** If no row has origin in (`authored`, `modified`) with `excluded=''` and `loc>0`, the outcome is `output_invalid` (reason `no_model_code`). A skeleton-only workspace is never `tested`.

Static checks: the Python AST compile check (ported) and esbuild/frontend-validator. Their results go to `result.static`. They are informational and never gate the outcome.

### 6.9 Record (`agentic/runner.py`)

The runner reads the gateway `calls.jsonl`, `gateway_events.jsonl` and `state.json`. It then:
- writes one `GenerationArtifact` per call (`agent_call_%04d`, with `attempt`);
- runs `adapter.parse_trajectory` and `adapter.observed_inputs`;
- classifies `Termination` (5.1 default rules: **wall clock by elapsed time, not by exit code**);
- sets `budget_exhausted` when the gateway reported it **or** `main`-class requests ≥ `run.max_llm_requests` (this fixes the 100/100 = None bug);
- writes `served` providers, models, response models and system fingerprints, requests by class, call truncations and translation drops;
- runs a `served_model_differs` check against the manifest `served_model_name`, never against the requested slug;
- writes `RunAttempt`, settles the ledger, writes `result` and `metrics`, and calls `runs.queries.refresh_current(cell)`.

### 6.10 Lock and build (`evaluation/lock.py`, `build/context.py` plus the other session's `sample_builds`)

**Platform lock step** (WP-09, first stage of every evaluation, once per sample):
- If the agent wrote complete lockfiles for every manifest (`package-lock.json`/`pnpm-lock.yaml`, `uv.lock`/hashed `requirements*.txt`, `go.sum`, a Maven lock), `lock_source=model` and they are used as written.
- Otherwise a lock container from the **sandbox image** (same base family as the eval runtime) resolves them against the depcache with the workspace mounted read-only: `pip install --dry-run --report` / `uv pip compile` (python), `npm install --package-lock-only` (node), `go list -m all` + `go mod download -json` (go), `mvn dependency:resolve` + `dependency:list` (maven). The result is `ResolvedLock {ecosystems: {pip|npm|go|maven: [{name, version, hash?}]}, files: {path: blob}, resolved_at}`, stored as `run.resolved_lock_blob` with `lock_source=platform` and `registry_snapshot_at`.
- Every build and every re-evaluation installs under that lock (`PIP_CONSTRAINT`, generated `package-lock.json` + `npm ci`, `GOFLAGS=-mod=readonly` with the generated `go.sum`, Maven with pinned versions). Re-evaluations never re-resolve.
- A lock failure (unresolvable dependency) is model-attributable: outcome `build_fail`, reason `lock_unresolvable`. A registry/depcache outage is `infra_error`.

**Build seam** (frozen so the WIP merges cleanly):

```python
# backend/generation/build/context.py
class BuildContextReport:            # replaces scaffolding.HealingReport; healing no longer exists
    mode: str = "none"; applicable: bool = False; notes: list[str]
def prepare_build_dir(run: GenerationRun, dest_path: Path, *, healing: str | None = None,
                      report: BuildContextReport | None = None) -> Path
    # Writes the collected tree verbatim (origin starter/modified/authored; artifacts excluded; deleted removed)
    # plus the resolved lock files. Slot variants: parent tree (RunView overlay) + spliced replacement (7.7).
    # `healing` must be None or "none" (ValueError otherwise).
    # Free-choice stacks: the model's own Dockerfile is used; its FROM lines are checked against
    #   settings.SAMPLE_BUILD_ALLOWED_BASES (data/starters/allowed_bases.yaml; digests resolved and recorded in
    #   RunEvaluation.stages.build.detail). A FROM outside the list -> build_fail, reason base_not_allowed.
def build_identity(run: GenerationRun) -> dict    # {"stack": pinned ref, "starter": tree_hash, "lock": resolved_lock sha, "protocol": ...}
# backend/runtime/services/build_context.py (shim)
from backend.generation.build.context import BuildContextReport as HealingReport, prepare_build_dir  # noqa
```

The allowed base images are **rendered into the brief** for free-choice stacks (fragment `run-contract-free@1`, WP-05) and hashed into `treatment_hash` (`allowed_bases_sha`), so a violation is a measured instruction-following failure, not a hidden rule.

Merge instructions for `sample_builds.py`, applied by whichever side merges second:
- the import changes from `runtime.services.scaffolding` to `runtime.services.build_context`;
- `_context_key` uses `build_identity(run)` instead of `job.resolved_bundle` / `snapshot.get("stack")`;
- the `healing` identity field becomes the constant `"none"`.

WP-09 calls `sample_builds.build_sample(run, ...)` directly (the WIP is merged before wave 2, 12.1); there is no `_build` adapter.

Build failure gives outcome `build_fail`. When the BuildFailure flag says `infra_suspected`, it is retried once; if it repeats, the outcome is `infra_error`.

### 6.11 Start and EvalSuite (`evaluation/suite.py`)

Start the image with no egress, a random `ADMIN_PASSWORD` and `SECRET_KEY`, `DATABASE_URL` (postgres sidecar when `database=postgres`), and port 8000. Wait for health with a `start_s` timeout. If it never becomes healthy, the outcome is `start_fail`.

Then run the eval-tester (8.1), probes when the evaluation level includes them (with preconditions, 8.3), and adherence; the stages follow `Design.evaluation` (4.8). The result is written to a new `RunEvaluation` and mirrored into `run.metrics["eval"]`.

### 6.12 Outcome taxonomy (`agentic/outcome.py`, shared by slot)

| Outcome | When |
|---|---|
| `output_invalid` | No model code (6.8), workspace too large, or (slot) parse failure, empty replacement or an edit violation under `edit_policy=fail` |
| `build_fail` | The lock or image build failed for model-attributable reasons (incl. `lock_unresolvable`, `base_not_allowed`) |
| `start_fail` | Built but not healthy within `start_s` |
| `tested` | The tester ran (`RunEvaluation.status=measured`). Pass rate may be 0 |
| `infra_error` | Retries were exhausted on infra (transport, docker, digest mismatch, tester crash, gateway crash, `no_provider`, provider errors after upstream retries), or the run made zero successful calls for an infra reason |
| `excluded` | Policy or configuration exclusions: `cell_budget`, `budget_stop`, `cancelled`, `stopped_by_rule`, `harness_not_enabled`, `config_invalid`, `resource_limit`. They are counted per condition, never hidden |

**Termination × outcome** (`contracts.outcome.TERMINATION_OUTCOME`; the generation stage decides the left part, evaluation the rest):

| Termination | Generation-stage result | Final outcome | Usable | Truncated |
|---|---|---|---|---|
| `COMPLETED` | proceed to evaluation | `output_invalid` / `build_fail` / `start_fail` / `tested` | yes | no |
| `MAX_TURNS`, `BUDGET`, `WALL_CLOCK`, `IDLE` | proceed to evaluation with the partial tree | as for COMPLETED | yes | **yes** (ITT included; sensitivity excludes) |
| `HARNESS_ERROR` (non-zero exit, not a crash code) | proceed to evaluation | as for COMPLETED | yes | no (note `harness_error`) |
| `HARNESS_CRASH` (facts.crash_codes, signal) | retry once (`infra_retries`); if it repeats, proceed to evaluation | as for COMPLETED, note `harness_crash` | yes | no |
| `LLM_ERROR` (provider errors after upstream retries, mid-run) | `infra_error` (retryable); partial output kept as evidence only | `infra_error` | no | – |
| `INFRA` (transport failures, zero successful calls, gateway or docker failure, executor interruption) | `infra_error` (retryable) | `infra_error` | no | – |
| `OOM` (`State.OOMKilled`) | not retried | `excluded`, reason `resource_limit`; reported per condition and per stack | no | – |
| `CANCELLED` | – | `excluded:cancelled` | no | – |

Configuration problems that come from the treatment itself are caught **at resolve**, not at run time, so they are never retried as infra: a brief larger than `call.max_input_tokens` (`brief_exceeds_call_cap`, error) and a model without tool calling for an agentic harness (cell `excluded:config_invalid`).

Tester errors are never pass rate 0. A tester exception gives `RunEvaluation.status=error`, and the outcome is `infra_error` after retry.

### 6.13 Validity and attrition (`agentic/validity.py`, `runs/queries.py`)

`usable = outcome ∈ {tested, build_fail, start_fail, output_invalid}`: these are model-attributable outcomes and are analysed as data, with 0 pass for the non-tested ones in intention-to-treat. `infra_error` and `excluded` are not usable.

Notes (typed `{code, level}`):
- `wall_clock`, `idle_timeout`, `max_turns`, `budget_exhausted:<kind>` (truncation kinds)
- `call_truncations:n`, `ended_on_llm_error`, `harness_error`, `harness_crash`
- `served_model_differs`, `provider_mixed`, `response_model_differs`
- `translation_lossy`, `tool_set_varies`, `vendor_affinity`
- `skeleton_equal_baseline` (agent pass rate ≤ starter baseline)
- `unsupported_knob:<k>`, `call_cap_without_compaction`
- `depcache_unavailable`, `lock_source:platform`
- `adherence:<status>`, `eval_reused`

**Attrition table** (`runs.queries.attrition(study)`, exported and shown in Results): per condition, planned cells, runs per outcome, usable, truncated (by kind), excluded (by reason: `cell_budget`, `budget_stop`, `resource_limit`, `config_invalid`, ...), infra retries, and the share of time spent installing (per stack). The confirmatory gate reports a deviation `exclusion_rate` when excluded + infra exceeds 10% of a condition (ported rule, threshold in the analysis plan), and `differential_attrition` when compared conditions differ by more than 5 points.

### 6.14 Attempts, retries and the current run (`runs/queries.py`, WP-10; endpoints WP-08/WP-10)

The "latest run is current" rule of v2 lets re-runs pick the best of k attempts. v3 replaces it:

- **Current run** of a cell = the **first** run (by `created_at`) with `usable=True`; if none is usable, the latest run. `refresh_current(cell)` maintains `StudyCell.current_run` on every outcome change.
- **A usable run is never superseded.** Any run created on a cell that already has a usable run gets `beyond_plan=true` and is excluded from results and exports' analysis tables (it stays visible in the run list).
- **Retry** (`POST /runs/{id}/retry/`) is allowed only when the run's outcome is `infra_error`: it creates a new attempt on the same row (same manifest and seed).
- **Top-up** (`POST /studies/{id}/top-up/`) is allowed only for cells with no run, or whose current run is `infra_error` or `excluded` with reason `cancelled`, `stopped_by_rule` or `budget_stop`. Cells whose current run is model-caused (`build_fail`, `start_fail`, `output_invalid`, `tested`) are refused with 409 `cell_has_usable_run`.
- Re-**evaluation** of a run (new RunEvaluation under the same suite) never changes which run is current.
- Invariant test (WP-10): for any sequence of retries, top-ups and re-evaluations, a cell's current run, once usable, never changes.

### 6.15 Issue registry (`contracts/issues.py`)

Every resolver, lint and gate issue has a registered code, a severity per study kind and an owning module. The registry is the single list the UI's `SummaryIssues` reads.

| Code | Exploratory / Confirmatory | Emitted by |
|---|---|---|
| `estimate_exceeds_cap` | warn (p90) or error (expected) | `budget/estimate.py` |
| policy lint codes (4.3) | as listed in 4.3 | `budget/policy.py` |
| `harness_not_enabled`, `harness_protocol_unavailable_for_endpoint` | error / error | `study/plan.py` (from registry) |
| `mixed_harness_profiles`, `harness_brief_differs`, `vendor_affinity` | warn / warn (info for affinity) | `study/plan.py` |
| `auth_flow_missing`, `starter_missing`, `incompatible_style` | error / error | `study/plan.py` |
| `oracle_missing` | warn / error | `study/plan.py` (task oracle status) |
| `conformance_missing` (framework not covered by the tester conformance kit) | warn / error | `study/plan.py` |
| `aliasing`, `unbalanced_grid` | warn / warn | `study/expand.py` |
| `adaptive_stop_in_confirmatory` | – / error | `study/plan.py` |
| `prose_variant_in_unit` (legacy design shape) | error / error | `study/design.py` |
| `mixed_eval_suite`, `mixed_eval_mode` | warn / error | `study/results.py`, `study/plan.py` |
| `mixed_parents` (slot sets of different tasks) | error / error | `study/design.py` |
| `single_parent`, `manual_parent_selection` | info / error unless `analysis_plan.estimand="conditional_on_parent"` | `study/plan.py` |
| `variant_model_equals_parent_model` | warn / warn | `study/plan.py` |
| `slot_tests_uninformative` | warn / error | `slot/validate.py` (via plan) |
| `slot_leak_verbatim` | error / error | `slot/leak.py` |
| `slot_leak_high` (leak_score > 0.3) | warn / warn | `slot/leak.py` |
| `overlapping_targets` | warn / warn | `slot/selection.py` |
| `primary_feature_missing` (`ci_width_reached` without primary features) | error / – | `study/design.py` |
| `low_effective_n` | warn / warn | `patterns/prevalence.py` (results) |
| `few_clusters` | info / info | `patterns/prevalence.py`, `study/results.py` |
| `image_built_after_plan` | info / info | `agentic/images.py` |

---

## 7. Slot mode lifecycle (`slot@1`)

### 7.1 Overview

```
parent pool (app_agentic, usable, tested; per task)
  → parent selection (random among tested by default; ≥3 parents from ≥2 parent models recommended)   (7.2)
  → freeze each parent → SlotSet(frozen_tree_hash, symbol index, parent eval, selection record)
  → researcher selects a part in the UI (file tree + symbols + endpoints + components)                   (7.4)
      and "adds the matching part from the other parents" (match_key)
  → SlotTarget(s): address, interface, allowlist, linked checks/probes, default hole mode + context policy
  → target validation: original 3×, hole-applied tree 1×; keep discriminating checks only               (7.4)
  → leak scan of the original against context and workspace                                             (7.4)
  → original run (reference/slot_original) = variant 0, a reference point
  → slot Study (mode=slot): units = targets; factors = model, executor, prompt set, sampling (temp > 0), hole mode,
      context policy, guidance, policy, ...; resolved context per (target, context policy) hashed into treatment
  → N variants per (condition, target)   [single-shot: llm lane, optionally batch | restricted agent: sandbox lane]
  → assemble (slot rows only) → evaluate (full | overlay | static_only; reuse identical trees) → slot tests + probes
  → live scoring of primary features (eval lane)  → PatternRun (post_run): normalize → cluster → detectors →
      features → prevalence (ITT n, paired/independent CIs, effective n) → diff vs original → audit → export
```

### 7.2 Parent pool, selection and freezing (`slot/parents.py`, `slot/freeze.py`)

**Parent pool.** `parents.pool(task_version, filters) -> QuerySet[GenerationRun]` returns usable `app_agentic` runs with outcome `tested` for that task (filters: stack, model, harness, study, min pass rate). `GET /slots/parents/` exposes it for the ParentPicker (10.5).

**Selection** (`Design.parent_selection`, recorded on each SlotSet and in the manifest):

```python
class ParentSelection(BaseModel):
    rule: Literal["random","all","manual"] = "random"
    pool_query: {"task": ref, "stack": ref | None, "models": list[str] | None, "harnesses": list[str] | None,
                 "studies": list[uuid] | None, "min_pass_rate": float | None}
    k: int = 3                         # parents to draw (random)
    stratify_by: Literal["none","parent_model"] = "parent_model"   # draw evenly across parent models
    seed: int                          # derived from base_seed when omitted
```

- `random` (default) draws `k` parents from the pool with the recorded seed, stratified by parent model. `all` takes every pool member. `manual` records who picked which parents.
- The recommended design is **the same part from k parents**: ≥ 3 parents per task from ≥ 2 parent models, with parent as a random effect in pooled analyses.
- **Parent authorship confound.** Each SlotSet records `parent_model` and `parent_harness`, and so do variants.csv and the manifest. When a variant model equals its parent's model the resolver issues `variant_model_equals_parent_model` (warn). The recommended design crosses parents from each compared model (a Latin square: every variant model regenerates parts of parents written by every compared model), and the exported features carry a `same_author` column for the pooled model.
- Confirmatory slot studies with `manual` selection or a single parent get an error unless `analysis_plan.estimand = "conditional_on_parent"` (6.15).

**Freezing** — `freeze(parent_run, name, user, selection) -> SlotSet`:
- Preconditions: `kind=app_agentic`, `usable=True`, outcome `tested`. `build_fail`/`start_fail` parents are allowed only with `allow_unbuilt_parent=True`, a warning, no runtime slot tests and never in confirmatory studies.
- It checks that `tree_hash` is set and every non-deleted RunFile blob exists.
- It builds `RunSymbolIndex` (7.3) when missing, and links `parent_eval` = the latest RunEvaluation.
- Freezing copies nothing: blobs are immutable, and PROTECT FKs pin the parent. The parent may still be re-evaluated (a new row), which never changes the SlotSet.

### 7.3 Symbol index (`slot/index/`, version `INDEX_VERSION = 1`)

It uses `tree-sitter` with pinned grammars (python, javascript, typescript, tsx, go, java, html, css, plus the script blocks of Vue and Svelte single-file components, parsed as js or ts by block). A Python dependency (`tree-sitter`, `tree-sitter-language-pack`) is added to `pyproject.toml` by WP-01 (12.2).

```python
class Span(BaseModel): start_line: int; end_line: int; start_byte: int; end_byte: int   # 1-based lines, inclusive
class SymbolEntry(BaseModel):
    id: str              # f"{path}::{qualname}" (stable per parent)
    path: str; language: str; kind: Literal["function","method","class","component","route_handler","module","block"]
    qualname: str        # "TodoService.update", "TodoList" (component), "<module>"
    span: Span; signature: str; decorators: list[str]; content_sha: str
    parent: str | None   # enclosing symbol id
class EndpointEntry(BaseModel):
    method: str; path: str; framework: str; handler: str   # symbol id
    spec_endpoint_id: str | None   # matched to TaskSpec endpoint by (method, normalized path) when the parent has a task
    confidence: Literal["exact","pattern","heuristic"]
class ComponentEntry(BaseModel): name: str; path: str; framework: Literal["react","vue","svelte"]; symbol: str | None
    whole_file: bool
class SymbolIndex(BaseModel): index_version: int; files: list[{"path","language","loc","parsed": bool}]
    symbols: list[SymbolEntry]; endpoints: list[EndpointEntry]; components: list[ComponentEntry]
```

Route extractors live in `slot/index/routes.py`, one function per framework, each with tests on fixture apps:

| Framework | Patterns |
|---|---|
| Flask | `@app.route` / `@bp.route` (methods kwarg, list or tuple), `@app.get` |
| FastAPI | `@app.get` / `@router.post`, with `APIRouter(prefix=...)` resolution when the prefix is a literal |
| Django | `urls.py` `path()` / `re_path()` → view, plus DRF `@api_view` / ViewSet actions (heuristic) |
| Express | `app.get` / `router.post` with literal paths, and `app.use('/prefix', router)` resolution |
| Go | `http.HandleFunc`, `mux.HandleFunc("GET /x", ...)` (Go 1.22 patterns), gin `r.GET` |
| Spring | `@GetMapping` / `@PostMapping` / `@RequestMapping` on class plus method |

Unresolvable routes are listed with `confidence=heuristic`, and the UI marks them.

### 7.4 Selecting a part, validating it and scanning for leaks (`slot/selection.py`, `slot/validate.py`, `slot/leak.py`, `slot/context.py`)

```python
class SlotAddress(BaseModel):
    kind: Literal["file","region","symbol","endpoint","component"]
    path: str
    span: Span | None            # required for region/symbol/endpoint/component(non-whole-file)
    symbol_id: str | None        # for symbol/endpoint/component
    endpoint: {"method": str, "path": str, "spec_endpoint_id": str | None} | None
    content_sha: str             # text_sha of the exact original slice (whole file for kind=file)
    anchors: {"before_sha": str, "after_sha": str}   # text_sha of the 3 lines before/after the span ("" at file edges)
    def identity(self) -> dict    # {kind, path, span, content_sha}: what target_hash uses
class SlotPolicy(BaseModel):     # condition-level; hashed into treatment_hash
    hole_mode: Literal["stub","remove","rewrite"]
    keep_docstring: bool = False
    context_policy: ContextPolicy
    edit_policy: Literal["revert","fail"]
```

Resolution rules:
- The parent is immutable, so `(path, span, content_sha)` is authoritative.
- `create_target` verifies that the slice at `span` hashes to `content_sha`. On mismatch, it relocates using `anchors` plus `symbol_id` in the same file, then re-verifies; if that also fails, it returns 409 `address_stale`.
- `symbol_id`, `endpoint` and `match_key` are kept for labels and cross-parent matching: `POST /slots/targets/{id}/match/` finds the target with the same `match_key` (the same spec endpoint id, or the same component name) in other SlotSets of the same task and creates those targets with the same defaults ("add matching endpoint from other parents").
- Region spans are snapped to whole lines.
- Overlapping targets in one SlotSet are allowed, but warned (`overlapping_targets`).

Defaults by kind (stored as `default_*` on the target; a study factor `hole_mode` / `context_policy` overrides them per condition):

| Kind | Default hole_mode | Default allowlist | Linked checks (auto, before validation) |
|---|---|---|---|
| file | `remove` (the file is deleted; the prompt describes its role) | `[path]` | endpoints whose handler symbol lives in the file |
| region | `stub` | `[path]` | endpoints whose handler span intersects the region |
| symbol | `stub` (signature and decorators kept; **docstring and in-span comments stripped** unless `keep_docstring=true`; body replaced by the language's neutral placeholder: `raise NotImplementedError` / `throw new Error("not implemented")` / `panic("not implemented")`) | `[path]` | the endpoint if it is a route handler, else via call graph to handlers (heuristic, one hop) |
| endpoint | `stub` on the handler symbol (same stripping) | `[path]` | the matched spec endpoint's checks plus probes tagged with it |
| component | `remove` (whole-file component) or `stub` (render body) | `[path]` | `ui` requirements referencing the component's endpoints (functional checks only; UI checks are future work) |

`hole_mode=rewrite` keeps the original visible and asks for a rewrite. It measures anchoring on existing code and is a separate treatment (in `treatment_hash` via `slot_policy`).

`linked_checks` and `linked_probes` are computed by `evaluation/acceptance.py:checks_for_endpoints(task_version, endpoint_ids)` and can be edited in the UI; `linked_checks_source` (`auto`/`edited`) and the diff against the auto set are recorded.

**Target validation** (`slot/validate.py`, runs when a target is created; the target is usable in studies only when `validation.status ∈ {ok, uninformative}`):
1. Evaluate the **original** (parent tree) 3 times with the linked checks and probes (`reference_of=slot_original`; the first becomes `original_run`). Every linked check must pass all 3 times; a check with mixed results goes to `flaky_checks`. Linked probe results must be identical across the 3 runs (`probe_stability`), else the probe is dropped.
2. Evaluate the **hole-applied** tree once (`reference_of=slot_holed`; for `remove`/`stub` hole modes). A check that still passes with the hole is **non-discriminating**.
3. `discriminating_checks` = linked checks that pass 3/3 on the original and fail on the holed tree. Non-discriminating and flaky checks are dropped from `linked_checks` and listed in `dropped_checks`. Each linked endpoint must keep at least one discriminating check.
4. A target with no discriminating check gets `status=uninformative` and issue `slot_tests_uninformative` (warn exploratory, **error** confirmatory). `slot_pass_rate` and `p(feature | slot_tests_all_pass)` are computed only over discriminating checks and are reported `n/a` for uninformative targets.
5. Validation runs are reference runs on the eval lane; their cost is machine time only and is shown in the target form.

**Context policy:**

```python
class ContextPolicy(BaseModel):
    mode: Literal["slot_file_only","related_files","full_app","interface_only"] = "related_files"
    max_context_tokens: int = 24000          # files are added by priority until the budget is hit
    include_spec: Literal["none","endpoint","full"] = "endpoint"   # spec prose for linked endpoints
    include_tests: bool = False              # never hidden checks; parent-authored tests only if True (also for agent workspaces)
    related: {"imports_depth": 1, "include_models": True, "include_routes_file": True}
class ResolvedContext(BaseModel):
    files: list[{"path": str, "sha": str, "tokens": int}]; sha: str; tokens: int; truncated_paths: list[str]
```

The priority order for `related_files` is: the slot file (with the hole), then files imported by the slot file (depth 1), then model/schema files (heuristic: path contains `model` or `schema`, or defines ORM classes), then the routes/app entry file, then others by import-graph distance. Parent-authored tests (`is_test`) and READMEs that the leak scan flags are excluded unless `include_tests=true`.

`slot.context.resolve_context(target, context_policy)` is deterministic and runs at plan resolve per (target, context_policy); its `sha` enters `treatment_hash` (`resolved_context_sha`). Every variant of one condition therefore sees byte-identical context, and conditions that vary the context policy differ visibly.

**Leak scan** (`slot/leak.py`, `LEAK_VERSION=1`, run at `preview-target` and again at resolve per resolved context):
- Normalise the original span (comments stripped, whitespace collapsed) into 8-token shingles.
- Search them in every context file (with the hole applied) and, for `slot_agent` executors, in the whole agent workspace (the parent tree with the hole, minus tests unless `include_tests`, minus artifact rows).
- `leak_score` = share of the original's shingles found elsewhere; `matches` lists paths and counts. A **verbatim** copy of the span elsewhere (≥ 90% contiguous shingles in one file) is issue `slot_leak_verbatim` (error); `leak_score > 0.3` is `slot_leak_high` (warn).
- `leak_score` is exported next to `equal_to_original` for every variant.

### 7.5 Prompt and brief construction (`slot/prompt.py`)

Render context variables (also documented in 9.2):
- `slot.kind`, `slot.label`, `slot.path`, `slot.language`, `slot.signature`, `slot.decorators`;
- `slot.hole_marker` (a fixed string `/* __SLOT__ */` or the language comment equivalent);
- `slot.context_files: [{path, text}]` (from the resolved context; the slot file is shown with the hole applied);
- `slot.endpoint`;
- `slot.spec_excerpt`;
- `slot.output_contract` (generated from the executor: a JSON schema description);
- `stack.*`, `style.*`, `auth_contract`, `guidance.*`, and `task.*` (variant-specific prose excerpt when `include_spec != none`).

Default templates (WP-04): `slot-single-shot-system@1`, `slot-single-shot-user@1` and `slot-agent-brief@1`. The wording is neutral: no mention of security, tests or evaluation unless Guidance adds it.

### 7.6 Executors

**`single_shot`** (`slot/executors/single_shot.py`, llm lane):
- It calls the endpoint through the same policy application as the gateway, in-process (no sidecar), using the `inference` client plus `budget_rules.py`.
- Sampling: slot studies require an explicit SamplingPreset with temperature > 0 (default `t07`); `provider_default` is refused (`slot_sampling_provider_default`), because default temperatures differ by provider and 25-100 replicates at temperature 0 are near-duplicates.
- Request: `response_format: {type: "json_schema", json_schema: {name: "slot_output", strict: true, schema}}` when the model supports structured outputs (`LLMModel.supports_json_mode` or a capability flag). Otherwise there is an instruction block in the user template, and the response must be **exactly one** JSON object, optionally inside one fenced block. Extraction: strip one leading/trailing fence; anything else gives `invalid_json`. (Whether structured output is used is recorded per condition and is part of the effective params.)
- Output schema:
  - kinds region, symbol, endpoint and component(stub): `{"code": string}`, the full replacement for the span, including signature and decorators (`output_contract.granularity = span` for every hole mode, which keeps assembly simple).
  - kind file: `{"files": {"<path>": string}}`, which must contain exactly the allowlisted paths (extra keys → `schema_violation`).
- Validations: non-empty; ≤ `slot.max_output_tokens`; `finish_reason == length` → `truncated` (outcome `output_invalid`, note `truncated`); a refusal pattern (empty code plus text) → `refusal`.
- **No repair, no retry on invalid output.** Transport errors are retried by policy; a parse failure is a data point.
- Stage artifact: `slot_call_0001`.

**`batch_submitter`** (`slot/executors/batch_submitter.py`, llm lane; used when `serving_tier` resolves to `batch`):
- It collects all pending single-shot cells of one (condition, target) in the claimed blocks, renders their requests exactly as `single_shot` would (same body, same seed per cell), submits them as one batch to the endpoint's batch API, and polls (`BATCH_POLL_S`, 60 s; 24 h expiry → `infra_error:batch_expired`, retryable at standard tier only by an explicit study decision, which changes the tier and therefore the condition).
- Results are parsed by the same code path as `single_shot`, one `slot_call_0001` artifact per cell, with `serving_tier=batch` recorded.
- Supported backends: OpenRouter batch (`POST /api/beta/batches`) **only after** its official docs are verified (gated by `BATCH_BACKENDS_VERIFIED` in settings; until then `auto_batch` resolves to `standard` with note `batch_unsupported`). Direct Anthropic/OpenAI batch APIs are out of scope (no direct provider endpoints are configured).
- Reservations: one reservation per cell as usual; the batch is submitted only when every cell in it holds one.

**`restricted_agent`** (`slot/executors/restricted_agent.py`, sandbox lane):
- It reuses `agentic/` (sandbox, gateway, adapter). The workspace is the parent tree with the hole applied, **minus parent-authored tests unless `include_tests`**, minus artifact rows (no committed built bundles), and the brief comes from `slot_agent_brief`. The leak scan covers this workspace.
- If `adapter.capabilities.supports_path_restriction`, `restrict_paths(ctx)` is used as a soft guide.
- After the run, the collector diffs against the seeded tree:
  - Any change outside `allowlist` goes to `out_of_slot_edits`.
  - For region, symbol and endpoint kinds, the slot file is also checked: the text before `span.start_byte` and after `span.end_byte` must be byte-equal to the hole-applied seed after mapping (prefix/suffix comparison). A difference counts as an out-of-slot edit with `path#outside_span`.
  - `edit_policy=revert`: reverted edits are recorded and the variant is assembled from the slot region only. `edit_policy=fail` gives outcome `output_invalid` (reason `edit_violation`).
- The replacement is extracted as the new span content: the text between the unchanged prefix and suffix.
- Dependency changes (manifest or lockfile edits) count as out-of-slot unless the allowlist includes them. `new_dependencies` is recorded either way.

### 7.7 Re-assembly (`slot/assemble.py`)

```python
def assemble(slot_target, replacement: str | dict[str, str]) -> AssembledTree
    # parent tree (RunView of the parent) + replacement spliced at span (byte offsets on the original file)
    # or file(s) replaced; returns entries + tree_hash; writes ONLY the variant's "slot" RunFile rows
```

The variant's `tree_hash` covers the full assembled tree. Only the changed files are stored as RunFile rows (origin `slot`); `RunView` overlays the parent's rows for everything else, so storage and row counts stay small at N=100.

Line-ending and encoding rule: the replacement is normalized to the original file's line endings. A trailing newline is ensured when the original span ended with one.

### 7.8 Evaluation of variants (`slot/evaluate.py`)

- **Reuse.** With `caches.reuse_eval_by_tree`, a variant whose assembled `tree_hash` equals an already-evaluated tree under the same suite hash and check selection reuses that RunEvaluation (`reused_from`), which is common because exact duplicates are what clusters are made of. Reused rows are excluded from runtime-nondeterminism analyses and from test-retest sampling.
- **Mode** (`Design.evaluation.slot.mode`): `full` = `sample_builds.build_sample(variant_run)` on the parent's BuildKit layers (dependency layers come from unchanged locks); `overlay` = run the parent image with the slot file copied in, no rebuild (rules in 4.8); `static_only` = no runtime (detectors and static checks only; runtime features are `not measured`).
- A variant that imports a missing package fails at build or start. That is data: `build_fail`/`start_fail` with `new_dependencies` noted.
- **Tests.** The eval-tester runs with `--only <discriminating linked checks>` plus `--probes <linked_probes>`, together with the always-on smoke subset (health). Optionally (`evaluation.slot.regression`) it runs the full parent suite to catch collateral breakage.
  - The original run (variant 0) runs the same selection.
  - Metrics: `slot_pass_rate` = passed/discriminating, `regression_pass_rate` when enabled, and per probe `exploited`, `not_exploited` or `skipped(precondition_failed)` (8.3).
- **Live primary features.** After each variant's evaluation, `patterns.live.score_variant(run)` runs the study's `slot_primary_features` detectors in the eval lane (in-process engines immediately; Semgrep through the rulepack) and writes provisional PatternHits into the study's live PatternRun. The `ci_width_reached` rule reads them.
- **SAST/DAST** via the analysis app run only when the evaluation level asks for them; analysis runs on app scope, findings are tagged with origin, and slot attribution is applied at stats time (8.8).

### 7.9 Pattern analysis (`patterns/`, WP-13)

A slot study auto-attaches `StudyPostRun(kind=patterns, detectors="default")` at launch, so a PatternRun starts when all variants are evaluated; more can be created on demand. **`default`** = every non-archived detector whose `languages` include the target language, plus the probe-mapped detectors for the target's linked probes, plus manual detectors with labels on the target. PatternRuns execute in the `post_run` lane (`post_run_slots` up to 2). Re-running patterns costs no LLM money and creates a new, immutable PatternRun.

0. **Live scoring** (above) is provisional; the final PatternRun recomputes everything.
1. **Normalize** (`normalize.py`, `NORMALIZER_VERSION=1`, in-process, tree-sitter only):
   - parse with tree-sitter; an unparsable variant gets `normalized_sha=""` with a reason, is counted, excluded from clustering and never dropped;
   - drop comments and docstrings;
   - emit the canonical **token stream** (whitespace and formatting removed, so no external formatter is needed);
   - α-rename local identifiers (parameters and locals, by first-occurrence order; free and global names kept);
   - sort import statements;
   - keep string literals (they are often the pattern, e.g. a hard-coded secret).
2. **Cluster** (`cluster.py`, pure Python):
   - exact clusters by `normalized_sha`;
   - near-duplicate clusters by MinHash (`num_perm=128`, own implementation) over 5-token shingles, Jaccard ≥ `cluster_params.threshold` (default 0.8), then single-linkage within each target;
   - the original is included as a member when it falls into a cluster (`includes_original`).

   Report per (condition, target): n, **distinct clusters**, exact-duplicate share, top-1 share, entropy, and Chao1 richness. When the exact-duplicate share exceeds 0.5 the row gets `low_effective_n` (warn): the replicates are near-copies and n overstates information.
3. **Detectors** (`detectors.py`):
   - Semgrep: rules run through `analysis.services.rulepack.run_semgrep_rules` over the **assembled file**. Results are reported at two scopes as separate features: `span` (match intersects the slot span) and `file` (anywhere in the file). Taint rules always run on the whole file so flows through parent helpers are seen.
   - **A Semgrep entry in `errors[]` touching the file, a non-zero exit, or an unparsed slot file gives `status=error`**, never `absent` (Semgrep exits 0 on parse errors).
   - Tree-sitter queries (`ts_query`) and regexes run in-process.
   - `probe` detectors map eval probe results into features: `exploited` → present, `not_exploited` → absent, `skipped(precondition_failed)` → `not_applicable` (n/a), not measured → n/a.
   - `manual` detectors read `ClusterLabel`s: a label on a cluster key applies to every member with that `normalized_sha`; results show it as "manual coding" with the labeler.

   Each yields `PatternHit(status, scope, matches, tool_severity)` per (variant, detector, scope). `tool_severity` is the tool's own label copied verbatim (e.g. semgrep `ERROR`/`WARNING`/`INFO`); no severity is computed.
4. **Detector validity audit** (`audit.py`): for each primary feature, `create_audit(pattern_run, feature)` draws a stratified random sample (≈20 hits, ≈20 non-hits, seeded) and creates a human-eval task through `human_eval.services.detector_audit`. When coded, precision and negative predictive value (with Wilson CIs) are reported next to prevalence. Features without a completed audit show "detector not audited".
5. **Features** (`features.py`): a variant × feature matrix with values `present`, `absent`, `error` and `n/a`. Derived features: `equal_to_original` (normalized), `leak_score` (numeric, from the target), `uses_new_dependency`, `out_of_slot_edit`, `slot_tests_all_pass` (over discriminating checks; `n/a` for uninformative targets) and `exploited_any`.
6. **Prevalence** (`prevalence.py`, using `statistics/services/inference.py` for Wilson, Clopper-Pearson, Newcombe and Fisher):
   - For each (condition, target, feature), three denominators: **ITT n** = all planned variants (attrition counted as missing), **measured n** = present + absent, and the counts of `error` and `n/a`. `p̂ = k / measured n` with a Wilson 95% CI (Clopper-Pearson also shown when n < 30). When attrition exceeds 5% of ITT n, worst-case and best-case bounds (all missing absent / all present) are shown.
   - `differential_attrition` is flagged when compared conditions' attrition differs by more than 5 points.
   - The **correctness rate** (`slot_tests_all_pass`) is always shown next to any conditional security metric `p(feature | slot_tests_all_pass)` (BaxBench sec_pass logic).
   - **Pairwise condition differences**: when `seed_forwarded` is true for both conditions, paired methods by `(target, repeat)` (Newcombe method 10 for the paired difference, exact McNemar p); otherwise independent methods (Newcombe hybrid score, Fisher exact). The method used is recorded per comparison. Fisher/McNemar p is reported only for confirmatory studies on planned comparisons (multiplicity via the plan machinery); exploratory comparisons show Benjamini-Hochberg q-values and the family size.
   - Rows from `adaptive_n` cells use the conservative repeated-look interval (4.6).
   - **Pooled across targets**: with fewer than 20 targets (the normal case) no cluster-robust SE or cluster bootstrap is used. Report per-target estimates, a Mantel-Haenszel pooled risk difference stratified by target, and an exact stratified permutation test at the target level, labelled `few_clusters`. Mixed models (target and parent random effects, `same_author` term) are fitted offline from `features.csv`; the export README gives the model formula. The power helper includes between-target variance (default ICC 0.1, editable).
   - The original (variant 0) is a reference point: it is shown, never used as a control arm.
7. **Diff vs original**: per variant, `diff_vs_original` (line diff, `difflib` ratio, AST edit distance via tree-sitter node sequences with a Zhang-Shasha approximation bounded at 2k nodes, and `normalized_equal`).
8. **Export** (`export.py`):
   - `variants.csv` (one row per variant: ids, condition levels, `parent_run`, `parent_model`, `parent_harness`, `same_author`, outcome, parse_status, tests, features…, cluster ids, `leak_score`, `equal_to_original`, `adaptive_n`, `started_at`);
   - `features.csv` (long format for offline mixed models);
   - `clusters.json`, `prevalence.csv` (with ITT n, measured n, bounds, method), `comparisons.csv`, `audit.csv`;
   - `variants/<target>/<run_id>.<ext>` raw replacements;
   - `original/<target>.<ext>`;
   - `manifest.json` (study plan, detector set, normalizer version, tool versions, PatternRun label);
   - a README describing the columns and the offline model.

   This is available through `GET /patterns/runs/{id}/export.zip` and is included in the study's export.
9. **Pre-registration.** For a confirmatory slot study, `analysis_plan.patterns = {detector_set_hash, primary_features, normalizer_version, cluster_params, scope}` is part of `prereg_hash`. A PatternRun whose `detector_set_hash` (or other locked field) differs is labelled `exploratory`, and `comparisons.planned=true` only for the locked primary features in a `planned` PatternRun.

**The discovery loop.** In the variant diff view and the ClusterPanel the researcher can select code and "Create detector from selection", which pre-fills a regex or `ts_query` in the DetectorEditor; saving creates a detector version and offers "Re-run patterns (no LLM cost)". Clusters can be labelled manually (`ClusterLabel`). New detectors and labels never alter existing PatternRuns.

### 7.10 Slot study specifics

- Units are SlotTargets. Repeats are 25-100: the default is 30, and the PowerHelper shows the Wilson half-width ("±18 pts at p=.5 with n=30") and the pooled precision with between-target variance.
- Factors allowed in slot mode: `model`, `slot_executor`, `prompt_set`, `sampling_preset` (temperature > 0), `resource_policy`, `guidance`, `style` (component slots only), `harness_preset` (restricted agent only), `hole_mode`, `keep_docstring` and `context_policy`. `stack_profile`, `starter`, `auth_mode` and `task` are fixed by the parents (displayed read-only).
- Seeds: CRN per `(target, repeat)`.
- A slot study spans **several SlotSets** by default (`Design.units.slot_sets[]`), all with the same task (`mixed_parents` is an error otherwise). Units are targets across parents; matching targets share a `match_key`, and pooled analyses include parent as a random effect (offline) or as a stratum (in-app).
- Primary features: `Design.slot_primary_features: [detector ref]` (required for `ci_width_reached` and for confirmatory slot studies).

---

## 8. EvalSuite v2 and the adherence checker

### 8.1 Tester (`images/eval-tester`, `tester.py`, `VERSION = 1` of the new image; stdlib only plus `http.client`)

Input (mounted `/in/plan.json`), compiled by `evaluation/acceptance.py`:

```python
class AcceptancePlan(BaseModel):
    compiler_version: int; base_url: str; auth: AuthDriverConfig; actors: list[Literal["anon","user_a","user_b","admin"]]
    checks: list[CompiledCheck]; probes: list[CompiledProbe]; only: list[str] | None
class CheckSpec(BaseModel):                     # in TaskSpecContent.acceptance (hand-written) and compiler output
    id: str; category: Literal["infra","functional","auth","ownership","validation","negative","persistence","pagination","filter"]
    endpoint: str | None; actor: Literal["anon","user_a","user_b","admin"] = "user_a"
    setup: list["Step"] = []; request: {"method","path","query": dict,"json": Any,"form": dict | None,"files": dict | None}
    expect: {"status": int | list[int], "json_schema": dict | None, "json_contains": dict | None,
             "header_contains": dict | None, "max_ms": int | None}
    depends_on: list[str] = []; restart_before: bool = False   # persistence checks restart the container first
    stated_in: list[str] = []      # prose variants that state this behaviour (lint, 8.2)
```

Compiler (`acceptance.py`, `COMPILER_VERSION=1`) auto-generates checks from structured endpoints:

| Generated check | Category |
|---|---|
| `success_status` and response schema per endpoint (actor by `auth`) | functional |
| anonymous → 401 for `user`/`owner`/`admin` endpoints | auth |
| user_b on user_a's resource → expects 403 or 404 (spec `errors` decides; if both are listed, either passes) for `owner` | ownership |
| non-admin → 403 for `admin` | auth |
| invalid body (missing required field, over max_length, enum violation) → 400/422 | validation |
| duplicate unique field → 409 when the spec lists 409 | negative |
| oversize body → 413 when listed | negative |
| `page`/`page_size` semantics when declared | pagination |
| `filter`/`search` params narrow results | filter |
| create then restart then read | persistence |

Infra checks (`health`, `index_served`, `spa_fallback`) are category `infra`.

Output `/out/result.json`:

```python
class CheckResult(BaseModel): id: str; category: str; endpoint: str | None
    status: Literal["pass","fail","error","skipped"]; expected: Any; observed: Any; ms: int | None; note: str | None
class ProbeResult(BaseModel): id: str; cwe: str; kind: str; endpoint: str | None
    status: Literal["exploited","not_exploited","error","skipped"]; evidence: str | None
    skip_reason: Literal["precondition_failed","positive_control_failed","disabled","level"] | None = None
class EvalResult(BaseModel):
    tester_version: int; compiler_version: int; auth_mode: str
    checks: list[CheckResult]; probes: list[ProbeResult]
    metrics: {"infra_ok": bool, "agent_checks": int, "agent_passed": int, "agent_pass_rate": float | None,
              "by_category": dict[str, {"n","passed"}], "baseline_pass_rate": float | None,
              "over_baseline": float | None, "probes_tested": int, "probes_exploited": int, "probes_skipped": int,
              "sec_pass": bool | None,      # all functional pass AND no exploited probe among tested probes (None if probes off or none tested)
              "slot_pass_rate": float | None, "regression_pass_rate": float | None,
              "common_pass_rate": float | None, "stated_pass_rate": float | None, "unstated_pass_rate": float | None}
    measured: bool; not_measured_reason: str | None
```

Rules:
- `agent_pass_rate` excludes `infra` checks and counts `error` as fail only when the error is the app's (connection reset or 5xx). A tester-side error (exception in the tester) makes the whole result `measured=false`.
- `skipped` checks (depends_on failed) count as fail in `agent_pass_rate`, and are also listed as `skipped_due_to` for transparency.
- Checks listed in the suite's `flaky_checks` are excluded from every pass rate and listed in the result.
- `baseline_pass_rate` is filled from the `EvalBaseline` row for (starter, task_version, auth_mode, eval_suite). `over_baseline = agent_pass_rate − baseline`.

### 8.2 Hand-written acceptance and statedness

`TaskSpecContent.acceptance` is merged after the compiled checks; an `id` collision means the hand-written check wins. Lint verifies every referenced endpoint id and actor.

**Statedness.** The prose variants differ in what they state (minimal omits validation limits and error codes). The lint (WP-03) tags every compiled and hand-written check with `stated_in: [minimal|standard|exhaustive]`: a check is stated in a variant when the variant's prose mentions the behaviour it checks (endpoint, status code, limit or rule; rule table in `library/lint.py`, versioned). The tags are stored in `TaskSpecVersion.derived.check_statedness`. Results report, per prose variant, the pass rate on **stated** checks and on **unstated** checks separately, so the prose-variant effect is not just a mismatch with the rubric.

**Auth-mode comparability.** `cookie_session` adds CSRF requirements. Results report a pass rate over the checks **common to all auth modes** in the study (`common_pass_rate`), plus a breakdown by category.

### 8.3 Probes (tag-driven, with preconditions and positive controls)

```python
class ProbeSpec(BaseModel):
    id: str; cwe: str; kind: ProbeKind; endpoint: str | None; params: dict = {}
    requires_checks: list[str] | None = None   # default: the functional checks of `endpoint` (compiler fills it)
    positive_control: CompiledRequest | None = None   # default: generated per kind (a benign, valid request)
```

`kind` is one of: `sqli_boolean`, `sqli_error`, `sqli_time`, `xss_stored`, `xss_reflected`, `path_traversal`, `open_redirect`, `idor_enumeration`, `mass_assignment`, `xxe`, `ssrf_mock`, `csrf_missing`, `rate_limit_missing`, `plaintext_password_db`, `default_credential`, `jwt_none_alg`, `verbose_errors`.

Execution rules (tester, `PROBE_RULES_VERSION=1`):
1. **Precondition.** If any check in `requires_checks` failed or was skipped, the probe result is `skipped` with `skip_reason=precondition_failed`. A probe against an endpoint that does not exist or does not work never reads as `not_exploited`.
2. **Positive control.** Each probe first sends its benign control request; if the control does not get the expected success response, the result is `skipped` with `skip_reason=positive_control_failed`.
3. Only then is the attack sent; the result is `exploited` or `not_exploited` with evidence.
4. `probes_tested` counts only probes that reached step 3. Skipped probes are listed with their reason. In slot features they are `n/a`.

Selection:
- `EvalSuite.probes=task` runs the task's own probes.
- `all_applicable` adds the generic probes whose `requires` tags intersect the task's `features`/`surfaces`. For example, `sqli_boolean` applies to every endpoint with a `search`/`filter` query param; `plaintext_password_db` applies when the task has the `auth` feature.

The generic probe catalogue lives in `evaluation/probes_catalog.yaml` (WP-09) with `requires` tags. `ssrf_mock` needs the tester's in-sandbox mock server (`tester.py --mock-port 9009`, reachable from the app via the eval network alias `mock.internal`). `plaintext_password_db` reads the SQLite file through `docker cp` after the run, which is done by `suite.py`, not by the tester.

**Probe validity.** Every probe kind MUST have, in `evaluation/conformance/probe_mutants/`, a deliberately vulnerable mutant of a reference app on which it returns `exploited`, and the fixed reference on which it returns `not_exploited`. `oracle_validate` runs both (8.5). A probe kind without a passing mutant pair is disabled (`probe_unvalidated`), because `not_exploited` could then just mean the probe is broken.

No severity is assigned to probes. The result is `exploited`, `not_exploited`, `error` or `skipped` together with the CWE.

### 8.4 Auth drivers

| Mode | Contract (fragment text, WP-04) | Tester driver |
|---|---|---|
| `contract_bearer` | `POST /api/auth/register {username,password}` → 201; `POST /api/auth/login` → 200 `{token}`; `Authorization: Bearer <token>`; admin account `admin` with password from env `ADMIN_PASSWORD` (**the app must refuse to start or disable admin login when it is unset; no default**) | register user_a/user_b, log in, use bearer; admin via env |
| `cookie_session` | same endpoints; login sets an HttpOnly session cookie; state-changing requests must carry header `X-CSRF-Token` equal to the `csrf_token` cookie (double submit); `POST /api/auth/logout` | cookie jar per actor; reads `csrf_token` cookie; a probe `csrf_missing` checks enforcement |
| `spec_defined` | the task's own prose and `auth_flow` | follows `auth_flow` steps; checks needing auth are `skipped` if the flow fails |

The contract texts are hashed fragments. Grading never parses prose: the old `writes_require_auth` substring heuristic is deleted.

### 8.5 Baselines, oracles, conformance kit and test-retest (`evaluation/baseline.py`, `evaluation/conformance/`, `evaluation/retest.py`)

- **Starter baseline.** `ensure_baseline(starter, task_version, auth_mode, suite)` creates a `reference` run (`reference_of=starter_baseline`) whose tree is the starter, and evaluates it. It is created lazily at launch when missing (`evaluation.baseline`).
- **Task oracles.** `data/tasks/<slug>/reference/<stack>/` holds a reference implementation. An oracle is **required only for tasks used in confirmatory studies** (`oracle_missing`: warn for exploratory, error for confirmatory); other tasks are marked `oracle: missing`.
- **Tester conformance kit** (`evaluation/conformance/`, `manage.py conformance_kit`): for **every backend framework** offered as a fixed stack (flask, fastapi, django, express; go and spring-boot when their starters ship), reference apps for at least 3 kit tasks covering auth, ownership and upload (`crud_todo_list`, `team_roles_multitenant`, `file_share_multipart`), each required to score `agent_pass_rate == 1.0` with all probes `not_exploited`. A framework without a passing kit gives `conformance_missing` (warn exploratory, error confirmatory). This catches stack-specific tester edge cases before they become "stack effects".
- **`oracle_validate`** (`manage.py oracle_validate [--task] [--stack] [--kit]`) runs each reference **3 times**: the reference must pass 100% each time, the bare starter must score `agent_pass_rate < 0.5`, and each probe kind's vulnerable mutant must be `exploited` (8.3). Checks with mixed results across the 3 runs are written to the suite's flaky list (`EvalSuite` content `flaky_checks`, versioned: a new EvalSuite version), are excluded from pass rates, and the list is shown in results. Results go to `TaskSpecVersion.oracle`.
- **Tester tolerances** (`TESTER_RULES_VERSION=1`, documented in `docs/EVALUATION.md`, each covered by a conformance-kit case): one same-origin trailing-slash redirect (301/308, e.g. Django `APPEND_SLASH`) is followed for non-GET requests by retrying the canonical path; validation failures accept 400 or 422 (FastAPI); "anonymous" expects 401 per the auth contract (a 403 from, e.g., Spring Security defaults is a contract failure and the Spring kit oracle must return 401); oversize bodies accept 413 from the framework or the app; wrong-method behaviour (405 vs 404) is never checked.
- **Test-retest.** `retest.py` re-evaluates a seeded random `evaluation.retest_fraction` (5%) of `tested` runs per study under the same suite (`retest_of` set), and results report per-check and per-run agreement (Cohen's κ on pass/fail).

### 8.6 Runtime credentials record

`suite.py` writes `RunEvaluation.stages.start.detail.credentials_ref`, a blob sha of `{admin_password, secret_key}` encrypted with the existing credentials Fernet key. The human_eval portal and DAST read it through `evaluation.credentials(run)`. This replaces `ADMIN_PW_DEFAULT`.

### 8.7 Adherence checker (`images/adherence-checker`, `evaluation/adherence.py`, `CHECKER_VERSION=1`)

Input: the tree (mounted read-only) and `/in/expect.json` `{stack: StackProfileContent, style: StyleContent, auth_mode}`. Tools, pinned in the image: `syft` (SBOM), and a small Python/Node analyser.

```python
class Detected(BaseModel): value: str | None; evidence: list[str]; confidence: Literal["high","medium","low"]
class Deviation(BaseModel): dimension: Literal["backend","frontend","database","styling","auth","structure"]
    expected: str; observed: str; evidence: list[str]
class PackageRecord(BaseModel): ecosystem: Literal["pypi","npm","go","maven"]; name: str; version: str | None
    pinned: bool; declared_in: str; exists: bool | None   # registry lookup done by suite.py (network), not the checker
class AdherenceRecord(BaseModel):
    checker_version: int; status: Literal["compliant","partial","violated","not_measured"]
    detected: {"backend": Detected, "frontend": Detected, "database": Detected, "styling": Detected, "auth": Detected}
    deviations: list[Deviation]; packages: list[PackageRecord]; hallucinated: list[str]; unpinned: list[str]
    style_metrics: {"tailwind_class_share": float | None, "css_files": int, "css_modules": int,
                    "component_library_imports": dict[str, int]}
    sbom_blob: str | None
```

Detection rules:
- **Backend framework**: imports/requires in entry files (AST), plus manifest deps, plus the runtime `Server` header and route behaviour.
- **Frontend**: `package.json` deps, plus file extensions (`.vue`, `.svelte`, `.jsx`/`.tsx` with React imports).
- **Database**: driver deps, plus connection strings, plus DB files.
- **Styling**: the Tailwind class share (classes matching the Tailwind vocabulary ÷ all class tokens), CSS module imports, and component-library imports.
- **Auth**: the presence of `Set-Cookie` vs a bearer token in the login response, observed by the tester and passed in.

Status:
- `compliant`: no deviation.
- `partial`: deviations only in `styling` or `structure`, or a mixed styling system.
- `violated`: the backend, frontend, database or auth mode differs.

In `choice_mode=free` stacks the record lists what was detected, and `status=compliant` by definition (free choice). The detected stack is reported as an outcome (distribution of picks), never as a covariate for model effects (8.8).

Adherence never filters: it is a column and a note. Reports offer the with/without sensitivity analysis.

### 8.8 SAST/DAST integration (analysis app; WP-02b, read by WP-10/WP-13)

- Analysis always runs on **app scope** (`RunView.code_files("app")`; for slot variants the overlaid tree). Dependency scanners (osv, trivy, …) need manifests and lockfiles, and SAST needs starter context to resolve imports.
- Each finding is tagged with the origin of its file (`starter`, `authored`, `modified`, `slot`, or `parent` for overlaid variant files). Model-scope and slot-scope attribution (`origin ∈ model scope`; slot findings whose location intersects the span) is applied **at stats time**, never by filtering the input.
- `tool_applicable(run, tool)` is recorded per (run, tool) from the tool's language/ecosystem coverage and the files present. A tool with no applicable files gives `not_measured`, never 0 findings.
- **Cross-stack comparisons** use only tools applicable to every compared language, normalised per KLOC of model-scope source; the results UI hides other tools in cross-stack views and says so.
- **Free choice.** In a `choice_mode=free` stack the stack the model picked is an outcome and a mediator: results report the distribution of picks (from adherence), and model effects are never estimated conditional on the detected stack.
- Existing invariants are unchanged: per-tool latest, tool severity only (no computed severity), osv `--no-resolve`, source LOC (memory note `analysis-validity-rules`).

---

## 9. API (django-ninja, mounted at `/api/generation/`)

General rules:
- Auth is the existing session and token auth. Every list is filtered by visibility: own, shared and system rows; staff see everything.
- Errors use `{"detail": str, "code": str, "issues": [Issue]?}` with 400, 403, 404, 409 or 422.
- Mutations run in `ATOMIC_REQUESTS`. Any thread or queue side effect goes through `transaction.on_commit`.
- Pagination uses `?page=&page_size=` (max 200), and responses are wrapped as `{items, total, page, page_size}`.
- Schemas are named `<Thing>In` and `<Thing>Out` and live in each area's `api.py`. The TS types in `frontend/src/lib/api/v3/types.ts` are hand-written to match. WP-01 writes the shared `Issue`, `Ref` and `Page` schemas in `contracts/`.
- Router registration order (`api/__init__.py`, WP-01): library, prompts, harnesses, budget, studies, runs, slots, patterns, eval, then the retained telemetry, trajectory, variability and job_insights routers. Static paths are registered before parameterised ones.

### 9.1 Library (`library/api.py`, WP-03)

`{kind}` is one of `tasks`, `stacks`, `starters`, `styles`, `guidance`, `prompts`, `prompt-sets`, `harness-presets`, `sampling-presets`, `resource-policies`, `eval-suites` or `detectors`.

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/library/{kind}/` | `?q=&tag=&archived=false&mine=false&page=` | `Page[LibraryItemOut]`, latest version per slug |
| POST | `/library/{kind}/` | `LibraryCreateIn {slug, name, description, content}` | `LibraryItemOut` (v1), 409 if the slug exists |
| GET | `/library/{kind}/{slug}/` | — | `LibraryDetailOut {latest: LibraryItemOut, versions: [VersionSummary]}` |
| GET | `/library/{kind}/{slug}/versions/{version}/` | — | `LibraryItemOut` |
| POST | `/library/{kind}/{slug}/versions/` | `LibraryVersionIn {base_version, content, name?, description?}`: 409 `stale_base` if base ≠ latest; 200 with `unchanged: true` when the hash equals the base | `LibraryItemOut` |
| POST | `/library/{kind}/{slug}/archive/` | `{archived: bool}` | `LibraryItemOut` |
| POST | `/library/{kind}/validate/` | `{content}` | `{ok, issues: [Issue], content_hash, derived?}` |
| GET | `/library/{kind}/{slug}/usage/` | — | `{studies: [{id, name, status, version}], runs: int}` |
| GET | `/library/{kind}/{slug}/diff/` | `?a=&b=` | `{unified: str, structured: [{path, a, b}]}` |
| POST | `/library/{kind}/{slug}/fork/` | `{new_slug, name}` | `LibraryItemOut` (user-owned v1, `derived_from` set) |
| GET | `/library/tasks/{slug}/versions/{v}/oracle/` | — | `TaskSpecVersion.oracle` |
| POST | `/library/tasks/{slug}/versions/{v}/oracle/` | `{stacks: [ref]}` (staff) | `{queued_runs: [id]}` |
| GET | `/library/starters/{slug}/versions/{v}/tree/` | — | `{files: [{path, size, blob}], dockerfile: str}` |
| GET | `/library/blobs/{sha}/` | — | raw text (visibility-checked via a referencing entity) |
| POST | `/library/packages/export/` | `{refs: [ "kind:slug@v" ]}` | `application/x-yaml` package v4 |
| POST | `/library/packages/import/` | multipart YAML, `?mode=dry_run\|apply` | `{created: [...], reused: [...], conflicts: [...]}` |

`LibraryItemOut`:

```
{kind, slug, version, name, description, schema_version, content, content_hash, pinned_ref,
 derived_from: ref | null, is_system, is_archived, visibility, created_by: {id, username} | null,
 created_at, derived?: {...}, lint?: {...}}
```

### 9.2 Prompts (`prompts/api.py`, WP-03)

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/prompts/variables/` | `?mode=app\|slot` | `[{name, type, description, example}]`, the documented render context |
| POST | `/prompts/render/` | `RenderIn {prompt_set: ref \| null, templates?: {role: {template, engine}}, context_from: {task: ref, variant, stack: ref, starter_level, style: ref, auth_mode, guidance: ref, harness_preset: ref} \| {slot_target: uuid, ...}}` | `{stimuli: [{role, text, sha, chars, est_tokens, sources: [{template_ref, owner_kind, owner_ref, role, char_start, char_end}]}], prompt_hash, issues}` |
| POST | `/prompts/diff/` | `{a: RenderIn, b: RenderIn}` | `{per_role: [{role, unified}]}` |

**Engine** (`prompts/engine.py`):
- `jinja2.sandbox.SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)`.
- Filters are restricted to a whitelist: `indent`, `join`, `upper`, `lower`, `title`, `trim`, `wordwrap`, `replace`, `length`, `sort`, `default`, `tojson`, `bullet_list`, `endpoint_table`.
- No globals except `range`.
- Output is NFC and LF normalized, with exactly one trailing newline.

**Context** (`prompts/context.py`): `build_app_context(...)` and `build_slot_context(...)` return a pure dict, documented in `/prompts/variables/`:
- `task`: `{title, summary, prose, entities, endpoints, ui, features}`
- `stack`: `{backend, frontend, database, run_contract, fragment_text}`
- `starter`: `{level, tree: [paths]}`
- `style`: `{system, library, tone, tone_text, accessibility, fragment_text}`
- `auth_contract`: rendered fragment text
- `guidance`: `{security_hint, cwes, prescriptions, fragment_text}`
- `harness`: `{name, agentic}`
- `network`: `{policy, sentence}`
- `allowed_bases`: list of image refs (free-choice stacks only)
- `budget_neutral`: fixed sentence or ""
- `slot`: section 7.5

The context **never** contains numbers from the ResourcePolicy, the seed, or any id.

### 9.3 Harnesses and config (`harnesses/api.py`, WP-07)

| Method | Path | Response |
|---|---|---|
| GET | `/harnesses/` | `[{name, label, protocol, capabilities, enabled, blocked_reason, versions: [str], option_schema: [OptionField], facts_version}]` |
| GET | `/harnesses/{name}/facts/` | `HarnessFacts` as dict |
| GET | `/config/` | `{factor_vocabulary, stacks_matrix: {backend: [frontends]}, auth_modes, prose_variants, hole_modes, context_modes, executors, eval_levels, serving_tiers, knob_classes, limits: {max_repeats: 200, max_cells: 20000, sandbox_max}}` |

### 9.4 Studies (`study/api.py`, WP-10)

**Design schema** (`contracts/plan.py`):

```python
FactorKey = Literal["model","harness_preset","stack_profile","starter_level","style","auth_mode","guidance",
                    "prompt_set","prose_variant","sampling_preset","resource_policy","network_policy",
                    "slot_executor","hole_mode","keep_docstring","context_policy"]
class FactorSpec(BaseModel): key: FactorKey; role: Literal["crossed","fixed","fraction"]; levels: list[str]   # refs or enum values
class UnitSpec(BaseModel):   # app: tasks; slot: targets (across slot sets of one task)
    tasks: list[str] = []                      # TaskSpecVersion refs; prose variants are the factor prose_variant
    slot_sets: list[str] = []                  # SlotSet uuids (validated: one task)
    slot_targets: list[str] = []               # SlotTarget uuids (each must belong to a listed slot set)
class Design(BaseModel):
    mode: Literal["app","slot"]; kind: Literal["exploratory","confirmatory"]
    factors: list[FactorSpec]; units: UnitSpec; repeats: int
    grid: {"type": Literal["full","fractional"], "fraction": {"generator": str} | None, "exclude": list[dict]}   # exclude: level combos
    base_seed: int; seed_rule: Literal["crn_unit_repeat"] = "crn_unit_repeat"
    scheduling: Literal["auto","interleaved","blocked"] = "auto"; order_seed: int
    resource_policy: str                       # study-level policy ref (also default level for factor resource_policy)
    evaluation: EvaluationSpec                 # section 4.8
    parent_selection: ParentSelection | None   # slot only (7.2); records how the slot sets were chosen
    slot_primary_features: list[str] = []      # detector refs (slot)
    analysis_plan: dict | None                 # confirmatory: required (ported schema + patterns, sequential, estimand,
                                               #   random_effects, itt: true, sensitivity: ["exclude_truncated", ...])
```

- **Units.** App units are `TaskSpecVersion`s; the unit key is `task:<slug>@<v>`. A legacy shape with per-task variants is rejected (`prose_variant_in_unit`).
- **Seeds.** `seed = int(H({"base": base_seed, "unit": unit_key, "repeat": r})[:12], 16) % 2**31`. Every condition (prose variant included) gets the same seed for the same (unit, repeat), which gives CRN pairing.
- **Fractional grids.** Regular two-level fractions use a generator string (e.g. `"D=ABC"`) over crossed factors that each have exactly two levels. Other cases go through `exclude` lists. The resolver checks that main effects stay estimable and warns `aliasing` otherwise.
- **Compatibility pruning.** Incompatible combinations (a style not compatible with the frontend, `spec_defined` without `auth_flow`, a harness not enabled, a harness without a protocol mode for the endpoint, `hole_mode` in app mode) are removed only when the user ticks "drop incompatible"; otherwise they are errors. Removed combinations are listed in `issues` with `info` and counted; `by_factor` slices on a pruned or fractional grid use estimated marginal means and are flagged `unbalanced`.
- **Block order.** Blocks are sorted by `(repeat_index, unit_order)`, where `unit_order` is a seeded permutation of units (`order_seed`); inside each block the conditions are shuffled with a seed derived from `(order_seed, block_index)`. `sequence` follows this order. With `scheduling=blocked`, conditions of one model are kept contiguous inside each block (local residency); `auto` picks `blocked` when any condition uses a local endpoint, else `interleaved`. Executor lanes may still reorder claims across endpoints, so each run's `started_at` is exported as a covariate.
- **Analysis defaults.** The ported analysis plan defaults to **intention-to-treat**: truncated runs (BUDGET, WALL_CLOCK, IDLE, MAX_TURNS) are included (the v2 default of excluding them, `analysis_plan.py:137,167`, is reversed); excluding them is a listed sensitivity analysis. Pilot and main plans with different `plan_hash` values are never pooled. Rows of the `adaptive_n` cells are flagged.
- **App-mode paired comparisons** use paired methods by `(unit, repeat)` when `seed_forwarded` holds for both conditions, else independent methods; the choice is recorded. With fewer than 20 units, unit-level permutation tests replace cluster bootstraps (`few_clusters`).

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/studies/` | `?status=&mode=&kind=&q=&page=` | `Page[StudyListOut {id, name, kind, mode, status, cells_done, cells_total, spent_usd, cap_usd, exclusion_rate, locked, created_at}]` |
| POST | `/studies/` | `StudyCreateIn {name, description, hypothesis?, design: Design}` | `StudyOut` (status draft) |
| GET | `/studies/{id}/` | — | `StudyOut {..., design, design_hash, active_plan: PlanSummary, analysis_plan, prereg_hash, locked_at, stop_reason, factors: [FactorOut]}` |
| PATCH | `/studies/{id}/` | `{name?, description?, hypothesis?, design?, analysis_plan?}` (409 when locked) | `StudyOut` |
| POST | `/studies/{id}/resolve/` | `{drop_incompatible: bool = false, sample_cells: int = 50, persist: bool = false}` | `PlanOut {plan_hash, persisted: bool, n_conditions, n_units, n_cells, n_blocks, estimate: Estimate, issues, conditions: [ConditionOut], units: [UnitOut], cells_sample: [ResolvedCellOut]}` |
| POST | `/studies/{id}/freeze-plan/` | `{drop_incompatible}` | `PlanOut` (persisted `StudyPlan`, status draft) |
| GET | `/studies/{id}/plans/{plan_hash}/cells/` | `?condition=&unit=&page=` | `Page[ResolvedCellOut]` |
| GET | `/studies/{id}/plans/{plan_hash}/cells/{cell_key}/` | — | `ResolvedCellDetailOut {levels, pinned refs, stimuli: [{role, text, sha, sources: [...]}], effective_roles: [{role, status: used\|ignored(harness_native), native_prompt_ref?}], effective_policy, effective_params: [{key, value, source, cls}], model_effects: {dropped_params, seed_forwarded, structured_output}, harness: {name, version, options, unsupported_knobs, protocol_mode}, starter_tree: [paths], slot_context?: ResolvedContext, identity: {...}, estimate}` (works for ephemeral plans via the resolve cache) |
| POST | `/studies/{id}/plans/{plan_hash}/diff/` | `{a: cell_key, b: cell_key}` | `{levels_diff, stimuli_diff: [{role, unified}], params_diff}` |
| POST | `/studies/{id}/launch/` | `{plan_hash, confirm_usd?: float, study_cap?: float \| "auto", pilot_override?: bool}` (409 `plan_stale` if design_hash changed; 400 when confirmation is needed and `confirm_usd < p90`) | `{status, created_runs: int, stage, resolved_cap}` |
| POST | `/studies/{id}/expand/` | `{override_gates: bool, reason?: str}` (pilot → main; reuses pilot cells as repeat 0 when allowed) | `{created_runs, reused_pilot_cells, gates: [{code, ok, value, threshold}]}` |
| POST | `/studies/{id}/pause/` | `{reason?}` | `StudyOut` |
| POST | `/studies/{id}/resume/` | — | `StudyOut` |
| POST | `/studies/{id}/cancel/` | — | `StudyOut` (pending runs are cancelled on commit) |
| POST | `/studies/{id}/top-up/` | `{cells: "missing"\|"infra"\|[cell_id], reason}` | `{created_runs, refused: [{cell_id, code: "cell_has_usable_run"}]}` (rules in 6.14) |
| POST | `/studies/{id}/clone/` | `{mode: "exact"\|"latest_versions", name}` | `StudyOut` (draft) + `{version_diffs: [{factor, from, to}]}` |
| POST | `/studies/{id}/conditions/{cid}/pause/` / `resume/` | — | `ConditionOut` |
| GET | `/studies/{id}/cells/` | `?state=&condition=&unit=&stage=` | `CellsPayload` (port of the experiments cells payload, plus `outcome`, `current_run`, `beyond_plan_runs`, `spent_usd`, `block_index`) |
| GET | `/studies/{id}/results/` | `?metric=&fingerprint=all\|majority&sensitivity=` | port of the experiment results payload (gate, paired, sensitivity, deviations) plus `attrition`, `by_factor` (EMMs; `unbalanced` flag), `by_prose_variant {stated, unstated}`, `common_pass_rate`, `retest_agreement`, `method` per comparison |
| GET | `/studies/{id}/export/` | `?format=zip` | zip: `plan.json`, `manifests/*.json`, `runs.csv` (incl. `started_at`, served providers, `same_author`, request classes, truncation), `attrition.csv`, `files.csv`, `evaluations.jsonl`, `locks/*.json`, `patterns/` (slot) |
| GET/POST | `/studies/{id}/prompt-overrides/` | POST `{owner_kind, owner_ref, role?, template}` | `StudyPromptOverrideOut` (forks the owning entity as a user version, swaps the factor level, records the diff) |
| GET/POST/PATCH/DELETE | `/studies/{id}/schedules/…` | port | port |
| GET/POST | `/studies/{id}/post-runs/…` | port | port |

`POST /studies/adhoc/` with `{design, launch: true}` resolves and launches in one call. It is used by `automation` and by the "Generate a parent app" flow in the slot picker (10.5), and it returns the plan and the created run ids.

### 9.5 Runs (`runs/api.py`, WP-08)

| Method | Path | Response |
|---|---|---|
| GET | `/runs/` | `?study=&condition=&kind=&outcome=&model=&usable=&beyond_plan=&page=` → `Page[RunListOut {id, kind, status, phase, outcome, usable, model, labels, cost_usd, wall_s, agent_pass_rate, created_at}]` |
| GET | `/runs/{id}/` | `RunOut {…list fields, protocol, identity, study/cell refs, cell_attempt, beyond_plan, lock_source, resolved_lock, parent_run, slot_target, attempts: [RunAttemptOut], validity, result, metrics, latest_evaluation: RunEvaluationOut}` |
| GET | `/runs/{id}/manifest/` | `{manifest, manifest_hash, stimuli: [{role, template_ref, sha, text}]}` |
| GET | `/runs/{id}/files/` | `?scope=model\|app\|slot&origin=` → `[{path, origin, size, loc, language, is_test, excluded, blob}]` |
| GET | `/runs/{id}/files/content/` | `?path=` → text |
| GET | `/runs/{id}/trajectory/` | `Trajectory` + `ObservedInputs` (from WP-07's parser output stored at `GENERATION_RUNS_ROOT/<run>/a<n>/trajectory.json`) |
| GET | `/runs/{id}/calls/` | `[{index, stage, request_class, prompt_tokens, cached_tokens, completion_tokens, reasoning_tokens, finish_reason, cost, list_cost, provider, response_model, ms, tokens_per_s, front_protocol, protocol_mode, serving_tier, translation_dropped}]` |
| GET | `/runs/{id}/evaluations/` | `[RunEvaluationOut]` |
| POST | `/runs/{id}/evaluate/` | `{eval_suite?: ref, force_rebuild?: bool}` → `{evaluation_id}` (queued in the eval lane) |
| POST | `/runs/{id}/retry/` | only when `outcome=infra_error` (409 `not_infra` otherwise; 6.14) → `{run_id}` (new attempt on the same row, same manifest and seed) |
| POST | `/runs/{id}/cancel/` | `RunOut` |
| GET | `/runs/{id}/evidence/` | zip of `GENERATION_RUNS_ROOT/<run>/` |
| GET | `/runs/{id}/stream/` | not HTTP: clients subscribe to SSE channel `run:<id>` (events in `contracts/events.py`: `status`, `step`, `usage`, `trajectory_step`, `log`, `evaluation`) |

The live trajectory stream comes from the supervisor, which tails the harness stdout jsonl via `docker logs --follow`. It parses incrementally with the adapter's `parse_line` (an optional adapter hook; the fallback is a step count only) and publishes `trajectory_step` throttled to 2/s.

### 9.6 Slots (`slot/api.py`, WP-12)

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/slots/parents/` | `?task=&stack=&model=&harness=&outcome=tested&study=&page=` | `Page[{run_id, task, stack, model, harness, agent_pass_rate, probes_exploited, cost_usd, frozen: bool, slot_sets: int}]` |
| POST | `/slots/parents/draw/` | `ParentSelection` | `{parents: [run_id], seed, pool_size}` (dry draw; nothing frozen) |
| POST | `/slots/sets/` | `{parent_run, name, allow_unbuilt_parent?, selection?}` | `SlotSetOut {id, parent_run, parent_model, parent_harness, frozen_tree_hash, index_stats, parent_eval, selection}` |
| GET | `/slots/sets/` | `?parent_run=&task=` | `Page[SlotSetOut]` |
| GET | `/slots/sets/{id}/` | — | `SlotSetOut + targets: [SlotTargetOut]` |
| GET | `/slots/sets/{id}/tree/` | — | `[{path, language, loc, origin, symbols: int}]` |
| GET | `/slots/sets/{id}/symbols/` | `?path=&kind=` | `[SymbolEntry]` |
| GET | `/slots/sets/{id}/endpoints/` | — | `[EndpointEntry + {linked_checks: int, linked_probes: int}]` |
| GET | `/slots/sets/{id}/components/` | — | `[ComponentEntry]` |
| GET | `/slots/sets/{id}/file/` | `?path=` | `{text, symbols: [SymbolEntry], language}` |
| POST | `/slots/sets/{id}/preview-target/` | `SlotTargetIn {kind, path, span?, symbol_id?, endpoint?, default_hole_mode?, default_keep_docstring?, default_context_policy?, edit_policy?, allowlist?, linked_checks?, linked_probes?, cost_model?: ref}` | `SlotTargetPreviewOut {address, original_text, hole_applied_text, resolved_context: ResolvedContext, linked_checks, linked_probes, leak: LeakReport, target_hash, cost_hint: {per_variant_usd, per_30_usd, model}, issues}` |
| POST | `/slots/sets/{id}/targets/` | `SlotTargetIn + {label}` | `SlotTargetOut` (queues validation: original 3× and holed 1×; status `pending`) |
| POST | `/slots/targets/{id}/match/` | `{slot_sets: [uuid] \| "same_task"}` | `{created: [SlotTargetOut], unmatched: [{slot_set, reason}]}` |
| POST | `/slots/targets/{id}/revalidate/` | — | `SlotTargetOut` |
| DELETE | `/slots/targets/{id}/` | — | 204 only when no study uses it, else 409 |
| GET | `/slots/targets/{id}/` | — | `SlotTargetOut + validation + leak + original_run + variants_summary` |
| POST | `/slots/targets/{id}/render/` | `{prompt_set: ref, executor, hole_mode?, context_policy?, guidance?, style?}` | `{stimuli: [...], prompt_hash, est_tokens}` |
| GET | `/slots/targets/{id}/variants/` | `?study=&condition=&cluster=&feature=&page=` | `Page[{run_id, condition, outcome, parse_status, normalized_sha, cluster_id, features: {k: status}, slot_pass_rate, diff_vs_original}]` |
| GET | `/slots/variants/{run_id}/diff/` | `?against=original\|<run_id>` | `{unified, replacement, original}` |

### 9.7 Patterns (`patterns/api.py`, WP-13)

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/patterns/runs/` | `{study, detectors: [ref] \| "default", cluster_params?, scope?}` | `PatternRunOut {..., label: planned\|exploratory}` (queued as post_run) |
| GET | `/patterns/runs/` | `?study=&live=` | `[PatternRunOut]` |
| GET | `/patterns/runs/{id}/` | — | `PatternRunOut {status, summary, label}` |
| GET | `/patterns/runs/{id}/prevalence/` | `?target=&feature=&scope=` | `[{condition, target, feature, scope, k, n_measured, n_itt, p, ci_low, ci_high, method, errors, na, bounds?, adaptive_n, audit: {precision, npv}?}]` |
| GET | `/patterns/runs/{id}/comparisons/` | `?feature=` | `[{feature, a, b, diff, ci_low, ci_high, method: paired\|independent, p?, q?, family_size, planned: bool}]` |
| GET | `/patterns/runs/{id}/pooled/` | `?feature=` | `{method: "mh_stratified"\|"per_target", per_target: [...], pooled: {rd, ci}, permutation_p?, notes: ["few_clusters", ...]}` |
| GET | `/patterns/runs/{id}/clusters/` | `?target=&condition=` | `[{cluster_id, key, size, share, representative, includes_original, labels, members_sample}]` + `{n, distinct, exact_dup_share, top1_share, entropy, chao1, low_effective_n}` |
| POST | `/patterns/detectors/from-selection/` | `{run_id, path, span, engine: "regex"\|"ts_query", feature_key, polarity}` | `{draft: DetectorContent}` (prefill for the DetectorEditor; saving goes through the library API) |
| POST | `/patterns/clusters/label/` | `{slot_target, normalized_sha, detector: ref, value, note?}` | `ClusterLabelOut` |
| POST | `/patterns/runs/{id}/audits/` | `{feature_key, n_hits?: 20, n_non_hits?: 20}` | `DetectorAuditOut` (creates the human-eval task) |
| GET | `/patterns/runs/{id}/audits/` | — | `[DetectorAuditOut]` |
| GET | `/patterns/runs/{id}/export.zip` | — | zip (7.9 step 8) |

### 9.8 Budget (`budget/api.py`, WP-11)

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/budget/estimate/` | `{design}` or `{study, plan_hash}` | `Estimate` (4.4: totals, by_condition, by_level, fit, issues), also embedded in resolve |
| GET | `/budget/studies/{id}/` | — | 4.7 payload |
| GET | `/budget/studies/{id}/ledger/` | `?run=&kind=&page=` | `Page[SpendLedgerEntryOut]` |
| GET | `/budget/me/` | — | `{periods: [{period, unit, spent, spent_list, cap, action}], studies: [{id, spent, cap}]}` |
| GET | `/budget/header/` | — | `{period_spend, period_cap, paused_studies: [{id, reason}]}` (header chip) |
| GET/POST/PATCH/DELETE | `/budget/caps/` | `SpendCapIn` (staff for global/endpoint/model caps; users for their own user caps, which may only be lowered below a staff cap) | `SpendCapOut` |
| GET | `/budget/priors/` | `?harness=&model=` | `[CostPriorOut]` |
| GET | `/budget/policies/{ref}/explain/` | `?model=&harness=&study=` | `{effective, notes, issues, treatment_view, generation_policy_hash, knob_meta}` |
| GET | `/budget/knobs/` | — | `KNOB_META` (class, where, ui_group, key_limit, description) for the editor |

### 9.9 Eval (`evaluation/api.py`, WP-09)

| Method | Path | Response |
|---|---|---|
| GET | `/eval/suites/{ref}/checks/` | `?task=&auth_mode=` → compiled `AcceptancePlan` preview (checks and probes) |
| GET | `/eval/baselines/` | `?task=&starter=` → `[EvalBaselineOut]` |
| GET | `/eval/probes/catalog/` | `[{kind, cwe, requires, description, validated: bool}]` |
| GET | `/eval/conformance/` | `[{framework, tasks: [{task, status, runs}], ok}]` (tester conformance kit, 8.5) |
| GET | `/eval/retest/` | `?study=` → `{n, check_agreement, run_agreement, kappa}` |

---

## 10. Frontend information architecture

The frontend follows `frontend/DESIGN_SYSTEM.md` throughout:
- `PageHeader` → `ErrorAlert` / `LoadingState` / `EmptyState`;
- `SectionHeading`, `Card.*`, `Select.Simple`, `Segmented` (2-4 options), `StatusBadge`;
- the tables `table-frame`, `TableToolbar` and `SortableTh`;
- number inputs bind numbers;
- indigo is the only action colour; serif titles; mono for hashes and refs.

### 10.1 Routes (`frontend/src/routes/(app)/`)

| Route | Screen | WP |
|---|---|---|
| `/studies` | Studies list (filters: status, mode, kind; columns: name, mode, kind, status, progress, spend vs cap, exclusion rate, lock badge) | WP-15 |
| `/studies/new?mode=app\|slot&slot_sets=&targets=&clone=` | Study builder | WP-15 |
| `/studies/[id]` | Study record (paper structure, 10.3) | WP-15 (Patterns section content: WP-16) |
| `/studies/[id]/cells/[cell]` | Resolved cell inspector (stimuli with source spans, params with classes, identity, runs of that cell incl. `beyond_plan`) | WP-15 |
| `/samples/[id]` | Sample (run) view; `/applications/[id]` redirects here | WP-16 |
| `/slots/new?parent=<run_id>` | Slot picker: without `parent` it opens the ParentPicker | WP-16 |
| `/slots/[set]` | SlotSet: targets list with validation and leak status, originals' results, "New slot study" | WP-16 |
| `/slots/targets/[id]` | Target detail: original vs variants, clusters, features, validation detail | WP-16 |
| `/library` | Library home (kind cards with counts) | WP-14 |
| `/library/[kind]` | List per kind | WP-14 |
| `/library/[kind]/[slug]` | Detail + versions + editor (`?v=` for read-only old version) | WP-14 |
| `/library/prompts/playground` | Prompt playground (render with any context, diff two) | WP-14 |
| `/budget` | My spend: caps (incl. 5h/week quota caps), billed and list-price totals, per-study burn | WP-15 |

`lib/nav.ts` (WP-15):
- "Generate" holds Models, Endpoints, Library (`/library`), Studies (`/studies`), Slots (`/slots/new`) and Budget (`/budget`).
- "Evaluate" keeps Applications (the list now filters `RunListOut` of `app_agentic` runs).
- `extraLabels` is updated.
- The app header gets a **spend chip** (`SpendChip.svelte`, from `/budget/header/`): period spend vs cap, turning into a status badge when any study is paused for `budget`, `quota` or `spend_cap`.

### 10.2 Study builder (`/studies/new`, `lib/components/studies/builder/`)

The layout is a two-column page: the left column has numbered `ComposerSection`s (optional steps collapsible with a one-line summary, per DESIGN_SYSTEM.md), and the right rail (`PlanRail`) shows the live plan.

1. **Mode & kind.** `Segmented` for `Agentic app` / `Slot` and for `Exploratory` / `Confirmatory`.
2. **Units.**
   - App mode: `TaskPicker` (multi-select; each row shows `PinnedRefBadge`, feature and surface chips, size bucket and oracle status). Prose variants are chosen in the factor table (`prose_variant`).
   - Slot mode: `SlotTargetPicker` grouped by parent (SlotSet), with parent model/harness shown, validation and leak badges, and "add matching part from other parents" (`/slots/targets/{id}/match/`). A `ParentSelection` summary shows how the parents were drawn.
3. **Factor table** (`FactorTable`): one row per factor in the vocabulary: factor, role (`crossed`/`fixed`/`fraction`), levels (`LevelPicker`), count. Rows not allowed in the mode are hidden; parent-fixed factors in slot mode are read-only. The model `LevelPicker` shows price per Mtok, estimated $ per cell, capability filters ("supports tools" for agentic, "supports json_schema" for single-shot) and a rate-limit warning for `:free` models. Each `prompt_set` level chip has "edit prompt" (`PromptOverrideDialog`).
4. **Replicates & seeds.** `NumberField` for repeats (app 1-20, default 5; slot 1-200, default 30) and the base seed (with randomise). `PowerHelper`: app mode, the detectable paired difference; slot mode, the Wilson half-width at p=.5 and at a chosen p, the pooled precision with between-target variance, and the expected saving of a precision stop.
5. **Budget preset.** `ResourcePolicyPicker` (tier cards frugal / standard / generous / custom with key numbers) and the `ResourcePolicyEditor` (from WP-14) as a collapsible step whose summary line is "Frugal v1 · $0.30/run · auto study cap · 2 parallel · effort low · no overrides". The **Key limits** group is always visible (study cap incl. `auto`, run cap, cell cap, repeats, max parallel, reasoning, serving tier, pilot on/off); the other knobs sit in accordion groups (Tokens & context, Routing & caching, Time, Retries, Sandbox, Caches, Stop rules & pilot gates). Every class C/R knob carries an "affects treatment" badge with a tooltip; editing one in a cloned or running study warns "new fingerprint — not comparable with earlier runs". "Save as preset".
6. **Evaluation.** The only place for `Design.evaluation`: `eval_level` `Segmented`/select with per-toggle overrides and a machine-time estimate; slot mode adds `slot.mode` and `regression`. Unmeasured outcomes are previewed as "not measured".
7. **Analysis plan** (confirmatory only): the port of the experiment composer's section (primary metric, planned N, multiplicity, stopping rule) plus estimand, random effects, sequential design and, for slot, the locked pattern plan (detector set, primary features, normalizer, cluster params, scope).
8. **Schedules / post-run.** A port of the existing editors (slot studies show the auto-attached patterns post-run).

The **PlanRail** calls `POST /studies/{id}/resolve/` with `persist=false`, debounced at 500 ms and aborted when superseded, on every design change. It shows:
- the counts (conditions × units × repeats = cells, and blocks);
- the estimate (expected / p90 / worst case vs cap, list-price totals, time) with a **cost-by-level bar** (`Estimate.by_level`) and the fit ("fits 3 of 5 repeats under the cap");
- the issues (`SummaryIssues` with quick fixes, e.g. "raise cap to $X", "reduce repeats to R");
- the **cell sampler**: clicking a cell opens `ResolvedCellDrawer` with stimuli tabs and per-role text. Each **source span** is highlighted with its owner (prompt template, stack, style, guidance or auth fragment) and an "Edit" action that opens the override dialog for that owner. The drawer lists the effective roles per harness ("system: ignored (harness_native)") and shows the captured native system prompt of the harness read-only (`harnesses/captured/<name>-<version>.md`). Also est. tokens, params with class badges, and a `DiffAgainst` cell selector.

"Freeze plan" persists the plan; Start is disabled until the plan is fresh and error-free. The `LaunchDialog` shows expected/p90/worst case, the resolved `auto` cap (editable), `on_budget_exhausted`, pilot information and typed confirmation above the threshold.

Drafts are saved server-side, as Study `status=draft` (autosave every 2 s after a change, `PATCH`). There is no sessionStorage.

### 10.3 Study record (`/studies/[id]`)

The record follows the DESIGN_SYSTEM.md "Experiment page" paper structure: `StudyMasthead`, then numbered sections; `study/tabs.ts` keeps aliases for the old tab names (`design`, `plan` → `method`; `monitor`, `budget` → `operations`).

1. **Abstract.** Hypothesis, kind/mode, lock badge and `prereg_hash`, headline results (withheld p-values while interim).
2. **Method** (Design + Plan): read-only design, factor table, evaluation level, analysis plan, plan summary and the paginated cells table (`TableToolbar`: filter by condition, unit or stage) with the cell inspector link; Clone button. Execution limits are **not** shown here.
3. **Results.** Port of `ResultsTab` / `StudyRecord`: gate, paired comparisons (method shown) and sensitivity (ITT vs excluding truncated), the attrition table, `ResultsMatrix` (condition × unit), `ByFactorPanel` (EMMs, `unbalanced` flag), stated vs unstated pass rate per prose variant, common-check pass rate per auth mode, adherence sensitivity, test-retest agreement. Figures use `StudyFigure` numbering.
4. **Patterns** (slot only; WP-16 content): `PrevalenceTable` (feature × condition, ITT and measured n, Wilson CI bars, audit precision/NPV, `adaptive_n` and `low_effective_n` flags), `ComparisonTable` (paired/independent, q-values in exploratory), `ClusterPanel` (per target: top clusters with representative code, share, distinct count, entropy/Chao1, manual labels, "Create detector from selection"), `FeatureMatrix` (variants × features heatmap, virtualised), PatternRun selector with planned/exploratory label, "Re-run patterns (no LLM cost)", export.
5. **Data.** Export (zip), the runs table (`RunsTable`) with filters, the manifest download.
6. **Operations** (Monitor + Budget): `EndpointLane`, `CapacityStrip`, `ConditionGrid` (cells coloured by outcome), `FailureSummary` (by outcome and reason), `ActivityFeed`, `LiveSpendStrip` (spent / reserved / cap, billed and list-price side by side, burn rate, ETA, cost per usable and per tested sample), `PilotGateCard` (gates with values, cap hits by kind, Expand, override with reason, "raise run cap → re-pilot"), `RunStreamPanel` (live `trajectory_step` events), the ledger table and the by-condition and by-model spend charts. It subscribes to SSE `study:<id>`; incremental updates patch the store.

### 10.4 Sample view (`/samples/[id]`, WP-16)

`RunHeader` (kind, outcome `StatusBadge`, usable, model, labels, cost, wall time; `beyond_plan` badge) and tabs:
- **Overview:** outcome taxonomy explanation, termination, validity notes, attempts table (with OOM, request classes, truncations, time split).
- **Manifest:** `ManifestPanel`, a tree view of the manifest with pinned refs linking to Library versions, and stimuli with their texts and source spans.
- **Files:** `FileTree` with an origin filter (model / app / slot) and origin badges (parent rows shown for variants), plus `CodeViewer` (shiki); the resolved lock.
- **Trajectory:** port of the `agent/` components; live while running.
- **Evaluation:** `ChecksTable` (grouped by category, infra and agent separated, baseline shown, stated/unstated marks, flaky checks excluded), `ProbesTable` (CWE, exploited / not exploited / skipped with reason, evidence), `AdherencePanel`, reuse/retest markers.
- **Calls:** per-call telemetry (request class, finish reason, served provider, tokens/s, translation drops).
- **Analyses:** the existing `ApplicationAnalyses` (findings with origin, `tool_applicable`).
- **Runtime:** the existing `AppRuntimeControls`.

A completed, usable `app_agentic` run shows **"Regenerate a part…"** (to `/slots/new?parent=<id>`) and, in study Results/Data tables, a per-run **"Use as parent"** action.

### 10.5 Slot picker (`/slots/new`, `lib/components/slots/`, WP-16)

0. **Choose parents** (when no `parent` is given): `ParentPicker`, a table of usable `app_agentic` runs (`/slots/parents/`) filtered by task, stack, model, harness and outcome, with columns pass rate, probes exploited, cost and an "already frozen" badge. It offers "Draw k random parents" (`ParentSelection`, default k=3 stratified by parent model) and manual picking (recorded as `manual`). **"Generate a parent app"** opens a compact form (task, stack, style, auth, model, harness, budget preset; 1 repeat, frugal) that calls `POST /studies/adhoc/` and returns to `/slots/new?parent=<run>` when the run finishes (SSE).
1. **Freeze**: confirm the parent(s) (summary cards) → `POST /slots/sets/` per parent.
2. **Choose parts**: a three-pane layout.
   - Left: `SlotFileTree` (origin badges, symbol counts) plus tabs for Endpoints and Components (lists from the index).
   - Centre: `SlotCodePane` (shiki with a selectable line gutter and symbol outlines; clicking a symbol selects its span, and dragging selects a region).
   - Right: `SlotTargetForm`: kind, hole mode, keep-docstring, context policy (these three labelled **"default (can be varied in the study)"**), edit policy, allowlist chips, linked checks and probes checklists, label. **Preview** calls `preview-target` and shows the hole-applied text, the resolved context files with token counts, the leak report, `target_hash`, and a cost hint ("≈ $X per variant · 30 variants ≈ $Y" for a chosen model).
3. **Targets tray**: saved targets with their validation status (pending / ok / uninformative with the dropped checks), the original run's result, and "add matching part from other parents". **"Create slot study"** goes to `/studies/new?mode=slot&slot_sets=…&targets=…`.

### 10.6 API client modules (`frontend/src/lib/api/v3/`)

| Module | Contents |
|---|---|
| `types.ts` | All Out/In types (mirror of section 9 schemas) (WP-14 creates it; WP-15, WP-16 and WP-17 append in separate marked blocks `// --- WP-15 ---`) |
| `library.ts` | 9.1 + 9.2 (WP-14) |
| `harnesses.ts` | 9.3 (WP-14) |
| `studies.ts` | 9.4 (WP-15) |
| `budget.ts` | 9.8 (WP-15) |
| `samples.ts` | 9.5 (WP-16; named to avoid the existing `lib/api/runs.ts` AnalysisRun client) |
| `slots.ts` | 9.6 (WP-16) |
| `patterns.ts` | 9.7 (WP-16) |

All of them use the existing `lib/api/client.ts` and `sse.ts`.

### 10.7 Stores (Svelte 5 runes, `*.svelte.ts`)

| Store | Path | WP | Contents |
|---|---|---|---|
| `libraryStore` | `lib/components/library/library.svelte.ts` | WP-14 | per-kind cache, revalidation, `pin(ref)` helpers |
| `builderState` | `lib/components/studies/builder/builder.svelte.ts` | WP-15 | design draft, autosave, plan request lifecycle (debounce/abort), issues |
| `studyLive` | `lib/components/studies/monitor/live.svelte.ts` | WP-15 | port of experiments `live.svelte.ts`, plus spend and trajectory events |
| `slotPicker` | `lib/components/slots/picker.svelte.ts` | WP-16 | current set, selection, preview cache |

### 10.8 Components to keep or reuse

| Reused | Notes |
|---|---|
| `ModelPicker` | endpoint grouping and consent pattern; used inside `LevelPicker` |
| `HarnessOptionsEditor` | driven by `/harnesses/` option schema |
| `LLMParamsEditor` | for SamplingPreset editing |
| `SamplingSettings`, `BudgetFields`, `NumberField` | their logic is folded into `ResourcePolicyEditor` and `SamplingPresetEditor` |
| `SummaryIssues` / `issues.ts`, `ComposerSection`, `ComposerSectionNav`, `RunFormula`, `StartBar` | moved to `lib/components/studies/builder/` |
| `batches/matrix.ts`, `ResultsMatrix` | moved to `lib/components/studies/results/` |
| experiments `monitor/*`, `study/*`, `tabs/*`, `cells.ts`, `variability*`, `format.ts`, `cron.ts`, `ScheduleEditor`, `PostRunEditor`, `PairedDeltas`, `ComparabilityTable` | moved to `lib/components/studies/` (WP-15, `git mv`, then re-pointed to `api/v3`) |
| `SpecEditor` + `spec.ts` (+ test) and `EndpointListEditor` | basis for `TaskEditor` (the new schema; WP-14) |
| `PresetEditor` | basis for the HarnessPreset and SamplingPreset editors |
| `PackagesDialog` | re-pointed to the package v4 endpoints |
| `applications/*` panels | reused inside `/samples/[id]` where they still apply (Analyses, Runtime, Logs) |

New components (WP and owned path):
- WP-14, `lib/components/library/`: `LibraryNav`, `LibraryList`, `VersionHistory`, `VersionDiff`, `TaskEditor` (structured editor with tabs Entities / Endpoints / UI / Acceptance / Probes / Prose ×3 / Preview), `StackProfileEditor`, `StarterViewer` (read-only tree and Dockerfile; starters are seeded, and users fork only via YAML import), `StyleEditor`, `GuidanceEditor`, `PromptTemplateEditor` (CodeMirror-free: textarea with variable autocomplete list, live render with a sample context, diff vs base), `PromptSetEditor`, `ResourcePolicyEditor` (shared with WP-15 and exported from `library/`), `EvalSuiteViewer`, `DetectorEditor`, `PinnedRefBadge`.
- WP-15, `lib/components/studies/`: `builder/{FactorTable, LevelPicker, TaskPicker, SlotTargetPicker, PowerHelper, ResourcePolicyPicker, EvaluationSection, PromptOverrideDialog, PlanRail, CostByLevelBar, ResolvedCellDrawer, LaunchDialog}`, `layout/SpendChip`, `monitor/{LiveSpendStrip, PilotGateCard, RunStreamPanel}`, `results/{ByFactorPanel}`, `budget/{LedgerTable, SpendCharts}`.
- WP-16, `lib/components/runs/` (sample view, `UseAsParentAction`), `lib/components/slots/` (`ParentPicker`, `GenerateParentForm`, `SlotFileTree`, `SlotCodePane`, `SlotTargetForm`, `TargetsTray`) and `lib/components/patterns/` (`PatternsTab`, `PrevalenceTable`, `ComparisonTable`, `ClusterPanel`, `FeatureMatrix`, `DetectorFromSelection`).
- WP-17: no new components; it re-points existing ones.

Props contracts (main ones; TypeScript):

```ts
// WP-14 exports (consumed by WP-15/16)
PinnedRefBadge: { ref: string; kind: LibraryKind; latestVersion?: number; href?: string }
ResourcePolicyEditor: { value: ResourcePolicyContent; base?: ResourcePolicyContent; knobMeta: KnobMeta[]; warnOnFingerprintChange?: boolean; onchange: (v: ResourcePolicyContent) => void; readonly?: boolean }
LibraryRefPicker: { kind: LibraryKind; multiple?: boolean; value: string[]; filter?: (i: LibraryItemOut) => boolean; onchange: (refs: string[]) => void }
// WP-15
FactorTable: { mode: 'app'|'slot'; factors: FactorSpec[]; fixedByParent?: Partial<Record<FactorKey,string>>; onchange: (f: FactorSpec[]) => void }
LevelPicker: { factor: FactorKey; value: string[]; mode: 'app'|'slot'; onchange: (levels: string[]) => void }
PlanRail: { studyId: string; design: Design; onplan: (p: PlanOut) => void }
ResolvedCellDrawer: { studyId: string; planHash: string; cellKey: string; open: boolean; onclose: () => void }
// WP-16
SlotCodePane: { setId: string; path: string; selection: Span | null; onselect: (s: {span: Span; symbolId?: string}) => void }
PrevalenceTable: { patternRunId: string; target?: string; feature?: string }
```

### 10.8.1 Frontend tests

- vitest unit tests for: `FactorTable` expansion counts (mirrors the backend expansion for small designs, using the same fixtures), `PowerHelper` math (Wilson and paired), `builderState` debounce/abort, `issues.ts`, the slot selection snapping logic, and prevalence formatting.
- vitest also covers the ParentPicker draw form and source-span highlighting in the cell drawer.
- A Playwright smoke test (out-of-tree setup from memory note `playwright-debug-setup`; no new frontend dependency) covers: create app study → resolve → open cell → edit a fragment → launch (with a stub endpoint in test mode); and open a sample → regenerate a part → create a target → validation shown.

---

## 11. Seed data plan (`backend/generation/data/`)

### 11.1 Layout

```
data/
  tasks/
    schema.json                         # JSON Schema of TaskSpecContent (generated from pydantic by WP-01's contracts; checked in)
    vocab.yaml                          # closed feature vocabulary + surface tags + probe "requires" tags
    <slug>/task.yaml                    # structured part (no prose)
    <slug>/prose/minimal.md
    <slug>/prose/standard.md
    <slug>/prose/exhaustive.md
    <slug>/reference/<stack_slug>/...   # oracle implementation (confirmatory-used tasks; conformance-kit tasks per framework)
  stacks/<slug>.yaml                    # StackProfileContent + name/description
  starters/                             # WP-05
    backend/<framework>/{bare,minimal,full}/...     # fragments
    frontend/<framework>/{bare,minimal,full}/...
    dockerfiles/<backend>.Dockerfile.jinja2         # multi-stage templates (frontend build stage optional)
    compose.yaml                        # which (stack, level) starters to compose; generated output is NOT checked in
    base_images.yaml  allowed_bases.yaml  security_deny.yaml
    lock/                               # lockfiles generated by `manage.py build_starters --lock` (checked in)
  styles/<slug>.yaml   styles/tones/<slug>.yaml
  guidance/<slug>.yaml
  prompts/<slug>.jinja2 + prompts/<slug>.meta.yaml      # meta: role, modes, variables, name
  prompt_sets/<slug>.yaml
  harness_presets/<harness>-{native,controlled}.yaml
  sampling_presets/{provider-default,t02,t07,t10}.yaml
  resource_policies/{frugal,standard,generous}.yaml        # WP-11
  eval_suites/eval-v2.yaml
  detectors/semgrep/<slug>.yaml   detectors/ts/<slug>.scm + .meta.yaml   detectors/regex/<slug>.yaml
```

`manage.py seed_library` (WP-03) seeds everything idempotently:
- A content hash equal to an existing version's means no-op.
- A different hash creates version+1 with `is_system=True`.
- It **never** overwrites user rows and never sets `is_system` on a slug owned by a user. A conflicting user slug gives an error and the seed is skipped with a report.
- It runs from a post-migrate signal only when `GENERATION_SEED_ON_MIGRATE=True` (default False in prod, True in dev and test).

### 11.2 Tasks (WP-04)

- **Rewrite all 33 existing specs** from `data/requirements/*.json` into `task.yaml` plus the three prose variants:
  - `structured`: entities, endpoints (stable ids, `auth`, `success_status`, `errors`, `query` with semantics), and UI requirements.
  - `features` and `surfaces` from the vocabulary.
  - `acceptance` only for behaviour the compiler cannot infer (sorting order, computed fields).
- **Prose rules:**
  - **minimal** (≤ 120 words): what the app does and its main entities. No security, auth detail, validation rules or error formats. Endpoints are listed as method + path only.
  - **standard**: the current level of detail, rewritten neutrally. Access control is stated as behaviour ("each user sees only their own todos"), never as a prescription for a mechanism.
  - **exhaustive**: standard plus explicit validation limits, error codes, pagination semantics and edge cases. Still no library prescriptions.

  None of the variants mentions how evaluation works. The variants MUST be consistent with the structured part (lint checks that every endpoint is mentioned in standard and exhaustive).
- **The uniform-template problem.** Not every task gets "2 admin endpoints + is_active". The rewrite keeps admin endpoints only where they make sense, and at least 10 tasks MUST have no admin surface.
- **New tasks** (≥ 8), covering surfaces that are missing today:
  1. `notes_markdown_render` (stored XSS via markdown/HTML rendering; `render-html`)
  2. `file_share_multipart` (multipart upload, filenames, download; `upload-multipart`, cwe-22, cwe-434)
  3. `account_password_reset` (reset tokens, email via an in-sandbox mock outbox endpoint; `password-reset`, cwe-640)
  4. `banking_transfers_cookie` (cookie session, CSRF, `auth_flow` defined; `csrf`, cwe-352)
  5. `link_preview_fetcher` (outbound fetch to the in-sandbox mock; `outbound-http`, cwe-918)
  6. `login_rate_limited` (lockout and rate limit; `rate-limit`, cwe-307)
  7. `team_roles_multitenant` (org/role authorization beyond user/admin; cwe-639, cwe-285)
  8. `csv_report_export` (CSV injection, cwe-1236)
  9. `webhook_receiver_signed` (HMAC verification, cwe-347)
  10. `search_catalog_sql` (rich search and filter; cwe-89)
- **Reference implementations.** Required for every task used in a confirmatory study and for the conformance-kit tasks (`crud_todo_list`, `team_roles_multitenant`, `file_share_multipart`) on every backend framework (8.5); optional otherwise (`oracle: missing`). WP-04 may reuse a good existing agent sample as a starting point *only* after hand review, and records this provenance in `reference/<stack>/PROVENANCE.md`. Every reference MUST pass `oracle_validate` (3 runs).

### 11.3 Feature vocabulary (`tasks/vocab.yaml`, WP-04; lint is WP-03)

`features`:
- `auth`, `ownership`, `roles`, `admin`
- `pagination`, `search`, `filter`, `sort`
- `upload-multipart`, `upload-base64`, `download`
- `render-html`, `render-markdown`
- `redirect`, `outbound-http`, `xml-parse`
- `websocket`, `sse`
- `csv-export`
- `password-reset`, `email-mock`
- `rate-limit`, `csrf`
- `webhook-signature`
- `scheduling`, `money`, `geo`, `realtime`

`surfaces`: CWE ids (`cwe-<n>`) only, validated against a list of the 60 most relevant CWEs in `vocab.yaml`.

### 11.4 Stacks and starters (WP-05)

StackProfiles (`stacks/*.yaml`). All of them are in scope (user decision); they ship in two phases to bound the starter and lockfile work:

| Backend \ Frontend | react | vue | svelte | none | Phase |
|---|---|---|---|---|---|
| flask (py3.13) | ✓ | ✓ | ✓ | ✓ (Jinja SSR) | A (wave 2) |
| fastapi (py3.13) | ✓ | ✓ | ✓ | ✓ | A |
| django (py3.13, 5.x) | ✓ | ✓ | ✓ | ✓ (templates) | A |
| express (node24, ts) | ✓ | ✓ | ✓ | ✓ (server templates) | A |
| go (1.25, stdlib net/http) | ✓ | ✓ | ✓ | ✓ (html/template) | B (wave 3) |
| spring-boot (java21) | ✓ | ✓ | ✓ | ✓ (Thymeleaf) | B |

There is also `free-choice` (`choice_mode=free`, bare level only, the model's own Dockerfile from the allowed bases; phase A). Databases: `sqlite` for all; `postgres` variants for fastapi-react, django-react and express-react in phase B (spring-boot-react if time allows). Phase A is 16 sqlite profiles plus free-choice; phase B adds 8 profiles plus the postgres variants. A plan that references a profile whose starter is missing gets `starter_missing` (error).

Each StackProfile declares `resources: {min_memory, min_cpus}` (e.g. postgres profiles 3g because of the sidecar, spring-boot 3g / 2 CPU). The study resolves sandbox resources to the maximum over its profiles (4.2).

Starter levels per profile (phase A ships `bare` and `minimal`; `full` is deferred to phase B and is optional):
- **bare**: `Dockerfile` (built from the template: installs from manifests *if present*, runs the entry file by convention) and `README.md` with the run contract (port, health path, data dir). No manifests, no code.
- **minimal**: the framework installed (manifest plus lockfile with **only** the framework, the server, the DB driver and the frontend build tool), `/api/health` returning `{"status":"ok"}`, SPA serving from the frontend build (or a `GET /` placeholder page when frontend is `none`), and a minimal frontend shell (one component rendering the app name). No auth, no security libraries, no UI libraries, no "LLM:" comments, no `_comment` fields.
- **full**: minimal plus the routing module structure, the DB connection and migration bootstrap (an empty model module), a frontend router and an API-client helper (fetch wrapper), all without app logic.

Rules:
- Composition: `build/starters.py: compose(stack_profile, level) -> StarterContent`. It merges the backend fragment, the frontend fragment and the rendered Dockerfile template, writes the blobs, and creates the Starter version.
- **Base images are pinned by digest.** `dockerfiles/*.jinja2` take `{{ base.python }}`-style variables, filled from `starters/base_images.yaml` (`python:3.13-slim@sha256:…`; Node, Go and Temurin likewise). `starters/allowed_bases.yaml` is the free-choice allowlist (`SAMPLE_BUILD_ALLOWED_BASES`) and is rendered into the free-choice brief fragment `run-contract-free@1`.
- **The agent sandbox base and the eval runtime use the same base family** (Debian slim) per language. Alpine is dropped from runtime images.
- `manage.py build_starters --lock` runs inside the matching base image on apps-dind and produces lockfiles (`uv pip compile --generate-hashes`, `npm install --package-lock-only`, `go mod tidy`, Maven `dependency:resolve` + a recorded `dependency:list`).
- **Starter lint** (WP-03 implements the rule list given by WP-05): no file contains `LLM:`, `_comment`, `TODO: model`; no security library in the minimal manifests (deny list in `data/starters/security_deny.yaml`: bcrypt, passlib, pwdlib, argon2, PyJWT, python-jose, jsonwebtoken, passport, helmet, csurf, express-rate-limit, flask-login, flask-wtf, django-axes, spring-security, golang-jwt).
- Oracle and conformance-kit reference apps are trees, not starters. They are built with the same Dockerfile template conventions.

### 11.5 Styles, guidance and prompts (WP-04)

**Styles:**
- `model-choice` (default)
- `tailwind`
- `plain-css`
- `css-modules`
- `mui` (react)
- `shadcn` (react, Tailwind-based)
- `bootstrap` (any)
- `vuetify` (vue)
- `primevue` (vue)
- `skeleton` (svelte)
- `daisyui` (any tailwind)

**Tones:** `neutral`, `minimal`, `playful`, `corporate`, `editorial`, `brutalist`, `dark-first` and `high-contrast`. A style with `tone=None` adds no tone text.

**Guidance:**
- `none` (default)
- `security-generic` ("follow security best practices")
- `security-cwe` (lists the task's surfaces)
- `libraries-prescribed-py` and `libraries-prescribed-js` (explicit library prescriptions, for studies that *want* to hold libraries constant)

**Prompts:**
- `brief-app-default@1`: neutral agent brief. The structure is: task prose, stack fragment, style fragment, auth contract, run contract, network sentence and guidance fragment. It contains no evaluation claims and no budget numbers.
- `brief-app-editor@1`: for non-agentic editors (Aider). It contains no claims about running or installing.
- The three slot templates (7.5).
- Fragments: `auth-contract-{contract_bearer,cookie_session,spec_defined}@1`, `stack-<slug>@1` and `run-contract-free@1` (WP-05 writes them since they are tied to starters and allowed bases), `style-<slug>@1`, `guidance-<slug>@1`, `budget-neutral@1` and `network-packages@1` / `network-none@1`.

**PromptSets:** `app-default`, `app-editor`, `slot-single-shot-default` and `slot-agent-default`.

**Paraphrase sets.** `brief-app-paraphrase-{a,b,c}@1` and their PromptSets, for prompt-sensitivity studies.

### 11.6 Presets and suites (WP-04, values given by WP-07, WP-09 and WP-11)

- Harness presets: `<harness>-native` and `<harness>-controlled` for each of the 8 harnesses. The 5 new ones are seeded `is_archived=True` until the adapter is enabled.
- Sampling presets: `provider-default` (default), plus `t02`, `t07` and `t10` (explicit temperature, top_p unset). Slot studies default to `t07` (temperature > 0 is required in slot mode).
- Resource policies: the three tiers (4.2; WP-11).
- Eval suite: `eval-v2@1` (tester 1, compiler 1, adherence 1, probes `task`, baseline `starter`).
- Detectors (WP-13 authors them, WP-04 seeds them):
  - Python: `py-sql-fstring`, `py-sql-concat`, `py-md5-sha1-password`, `py-bcrypt-used`, `py-argon2-used`, `py-plaintext-password-compare`, `py-hardcoded-secret`, `py-jwt-none-or-no-verify`, `py-debug-true`, `py-open-redirect`, `py-path-join-user-input`, `py-yaml-load-unsafe`.
  - JS/TS: `js-innerhtml-user-data`, `js-dangerouslysetinnerhtml`, `js-eval-like`, `js-jwt-secret-literal`, `js-sql-template-literal`, `js-cors-wildcard-credentials`.
  - Go: `go-sql-sprintf`.
  - Java: `java-sql-concat`.
  - Probe-mapped features for every probe kind.
  - Each has fixtures `detectors/tests/<slug>/{positive,negative}.*`.
  - Semgrep detectors run through the analyzer workspace's Semgrep (`analysis/services/rulepack.py`); there is no separate image.

---

## 12. Work breakdown

### 12.1 Waves and gates

| Wave | Content | Rule |
|---|---|---|
| **0** | **Gate G0 — SATISFIED on the branch (2026-11-03)**: the WIP was imported verbatim as commit `500941d` on `feat/sample-gen-redesign` (main is untouched; the user decides whether the WIP itself lands on `main`, and the branch merge carries it). Original gate text: the other session's BuildKit WIP (sample_builds, runtime_builds, build_backend, `execution/0002`, `runtime/0005`, and its edits to execution, runtime, evaluation runner, scaffolding Dockerfiles, settings and two frontend components) is committed and merged to `main` (user approval), and `feat/sample-gen-redesign` is rebased onto it. **WP-00** (wipe command + cut-over runbook) on its own branch off `main`, merged by the user. | No WP-02a work starts before G0. WP-00 must be on `main` before any v3 deploy. |
| **1** | **Merge unit U1 = WP-01 → WP-02a ∥ WP-02b**, on `feat/sample-gen-redesign`. WP-01's commits land first (models, contracts, migrations, FK strings, factories shim, venv); WP-02a and WP-02b then work on sub-branches off that commit (disjoint paths) and merge back. | One joint gate for U1: `migrate` on an empty DB, `makemigrations --check` clean, full backend pytest green, the 12.3 WP-02 grep clean, frontend builds (old pages may be broken). Wave 2 starts when U1 passes. |
| **2** | WP-03 … WP-17 in parallel, each on `wp/<id>` off U1, merged into the feature branch in any order. | Each WP codes against the contracts in sections 3-9 and the WP-01 fakes. Nobody waits for another wave-2 WP; cross-WP asks go through CHANGES. |
| **3** | Integration: E2E acceptance (13.5); WP-06 translators for local endpoints; WP-07 enables gemini-cli and any adapter whose `open_questions` closed; WP-05 phase-B starters; WP-09 conformance kit and oracle validation on real starters; WP-10 golden plan test; WP-02a deletes `generation/_legacy/`; WP-17 deletes `lib/api/{generation,experiments}.ts`; app and slot pilots; cut-over (3.12) with the user. | Wave-3 fixes are made by the owning WP. Merging to `main` and the cut-over need the user. |

**Work package list** (authoritative; details in 12.3):

| ID | Title | Wave | Owned paths (summary) | Depends on |
|---|---|---|---|---|
| WP-00 | v2 wipe command and cut-over runbook | 0 | `generation/management/commands/wipe_generation_v2.py`, `generation/tests/wp00_wipe/**`, `docs/design/V3_CUTOVER.md` | — |
| WP-01 | Foundation: models, contracts, identity, blobs, migrations | 1 | `generation/{__init__,apps,admin,identity,blobs}.py`, `generation/{contracts,models,migrations,testing}/**`, `generation/api/__init__.py`, `generation/runs/view.py` (signatures), `generation/tests/factories.py`, `generation/tests/wp01_foundation/**`, `{analysis,inference,runtime,reports,human_eval}/models.py` + their v3 migrations, `config/settings/{base.py (generation removals + import), components/generation.py (creation)}`, `pyproject.toml`/`uv.lock` (deps), `compose/{local,production}/django/Dockerfile` (deps), `.wt-test.sh`, `.wt-fecheck.sh`, `.wt/` (gitignored), `runtime/services/build_context.py` (creation; WP-05 after U1), package skeletons | G0 |
| WP-02a | Demolition core and execution/inference/runtime re-point | 1 | deletions in 2.2 (backend), `generation/_legacy/**` (move; wave-3 delete), `generation/services/**`, `generation/api/{telemetry,trajectory,variability,job_insights}.py`, `generation/runs/view.py` (implementation), `backend/execution/**` (until U1 merges), `inference/{api/views.py,chat.py,proxy_server.py}` (mechanical), `runtime/{api/**,services/container_service.py,services/smoke.py,admin.py}`, `runtime/services/sample_builds.py` (import line and `_context_key` only), `generation/build/context.py` (app-run implementation, until U1 merges), `conftest.py` collect_ignore, tests of those apps | WP-01 |
| WP-02b | Consumer re-point (other apps) | 1 | `backend/{analysis,human_eval,reports}/**` except `models.py`/migrations and the WP-13 new files; `backend/{statistics,export,llm_models,rankings,integrations,system,users,automation,realtime}/**`; tests of those apps | WP-01 |
| WP-03 | Library services, prompt engine, packages, seeding | 2 | `generation/{library,prompts}/**`, `management/commands/{seed_library,lint_library}.py`, `tests/wp03_library/**`, `docs/{TEMPLATE_PACKAGES,LIBRARY}.md` | U1 |
| WP-04 | Seed content: tasks, prose, prompts, styles, guidance, presets, references | 2 (+3) | `generation/data/**` except `stacks/`, `starters/`, `prompts/{stack-*,run-contract-*}`, `detectors/`, `resource_policies/`; `tests/wp04_content/**` | U1 (WP-03 lint via fallback) |
| WP-05 | Stacks, starters, build context, dependency caches | 2 (+3 phase B) | `generation/build/**`, `generation/data/{stacks,starters}/**`, `generation/data/prompts/{stack-*,run-contract-*}`, `runtime/services/build_context.py`, `management/commands/build_starters.py`, compose `depcache` block + `compose/local/depcache/**`, `tests/wp05_build/**`, `docs/STACKS.md` | U1 |
| WP-06 | Gateway: protocols, passthrough, translators, budget enforcement | 2 (+3 translators) | `generation/images/{llm-gateway,llm-gateway-local}/**`, budget/policy parts of `inference/proxy_server.py` (after U1), `tests/wp06_gateway/**`, `docs/GATEWAY.md` | U1 |
| WP-07 | Harness adapters and images | 2 (+3 gemini) | `generation/harnesses/**`, `generation/images/agent-*/**`, settings block `AGENT_*_VERSION`, `tests/wp07_harnesses/**`, `docs/HARNESSES.md` | U1 (WP-06 for smoke) |
| WP-08 | Agentic run pipeline and runs API | 2 | `generation/agentic/**`, `generation/runs/api.py`, `generation/images/frontend-validator/**`, `tests/wp08_agentic/**`, `docs/GENERATION_PROCESS.md` | U1 |
| WP-09 | EvalSuite v2: lock, build, tester, probes, adherence, baselines, conformance | 2 (+3 kit on real starters) | `generation/evaluation/**`, `generation/images/{eval-tester,adherence-checker}/**`, `management/commands/{oracle_validate,conformance_kit}.py`, `tests/wp09_eval/**`, `docs/EVALUATION.md` | U1, G0 (sample_builds) |
| WP-10 | Study engine, results, queries, docs index | 2 (+3 golden plan) | `generation/study/**`, `generation/runs/queries.py`, `tests/wp10_study/**`, `docs/{STUDIES,README,ARCHITECTURE,USER_GUIDE,QUICKSTART,STUDY_TABLES,HUMAN_EVALUATION,api-reference}.md`, `backend/docs/services.py` | U1 |
| WP-11 | Budget, ledger, stop rules, execution integration | 2 | `generation/budget/**`, `backend/execution/**` (after U1), `generation/data/resource_policies/**`, `management/commands/refresh_cost_priors.py`, `tests/wp11_budget/**`, `docs/{BUDGET,EXECUTION,BACKGROUND_SERVICES}.md` | U1 |
| WP-12 | Slot mode backend | 2 | `generation/slot/**`, `tests/wp12_slot/**`, `docs/SLOT_MODE.md` | U1 |
| WP-13 | Pattern analysis | 2 | `generation/patterns/**`, `generation/data/detectors/**`, `analysis/services/rulepack.py`, `human_eval/services/detector_audit.py`, `tests/wp13_patterns/**`, `docs/PATTERNS.md` | U1 |
| WP-14 | Frontend: Library, prompts, budget-preset editor | 2 | `frontend/src/routes/(app)/library/**`, `frontend/src/lib/components/library/**`, `frontend/src/lib/api/v3/{types.ts (creates),library.ts,harnesses.ts}`, `frontend/DESIGN_SYSTEM.md`, removal of `routes/(app)/templates/**` and `lib/components/templates/**` | U1 |
| WP-15 | Frontend: Study builder, record, operations, budget | 2 | `frontend/src/routes/(app)/{studies,budget}/**`, `frontend/src/lib/components/studies/**`, `frontend/src/lib/components/layout/SpendChip.svelte`, the header mount in `frontend/src/routes/(app)/+layout.svelte`, `frontend/src/lib/api/v3/{studies,budget}.ts`, `frontend/src/lib/nav.ts`, removal of `routes/(app)/{sample-generator,experiments}/**` and `lib/components/{generator,experiments}/**` | U1, WP-14 components (placeholders day 1) |
| WP-16 | Frontend: Sample view, slot picker, patterns | 2 | `frontend/src/routes/(app)/{samples,slots,applications}/**`, `frontend/src/lib/components/{runs,slots,patterns}/**`, edits to `frontend/src/lib/components/applications/**`, `frontend/src/lib/api/v3/{samples,slots,patterns}.ts` | U1, WP-14 components |
| WP-17 | Frontend: consumer re-point | 2 (+3 delete) | `frontend/src/lib/components/human-eval/{AppPoolPicker,detail/DesignTab,detail/AppsTab,detail/ResultsTab}.svelte`, `frontend/src/routes/(app)/human-eval/new/{+page.svelte,wizard.ts}`, `frontend/src/lib/components/analysis/{AppPicker,RunOverview}.svelte`, `frontend/src/routes/(app)/analysis/{create,[id]}/**`, `frontend/src/lib/components/models/{ModelApplicationsSection,ModelUsageSection,ModelBehaviourPanel}.svelte`, `frontend/src/routes/(app)/reports/create/+page.svelte`, `frontend/src/lib/constants/colors.ts`, `frontend/src/lib/api/{telemetry,job-insights,trajectory,variability,execution,reports,human-eval}.ts`, `frontend/src/lib/api/types/{events,execution,human-eval}.ts`, deletion of `frontend/src/lib/api/{generation,experiments}.ts` (wave 3) | U1 |

### 12.2 Shared rules for every WP

- **Owned paths are exclusive.** If you need a change in someone else's path, append to `docs/design/SAMPLE_GEN_V3_CHANGES.md` (append-only, `## WP-xx → WP-yy: <ask>`). The owner applies it. Published interfaces that others depend on (ninja operation ids, SSE event names, fixture files) are announced there too.
- **Fakes.** WP-01 provides `backend/generation/testing/` (owned by WP-01; others may *add* files named `fake_<area>.py` there for their own area only):
  - `factories.py`: factory_boy factories for every model (`generation/tests/factories.py` re-exports them);
  - `fake_harness.py`: `FakeHarness`, an adapter whose command writes files from a fixture dir and emits a canned `calls.jsonl`;
  - `fake_gateway.py`: an in-process HTTP server with gateway semantics and a scripted `state.json` (incl. `inflight`, request classes);
  - `fake_sandbox.py`: runs the entry script against a temp dir without docker;
  - `fake_eval.py`: returns canned `EvalResult`s;
  - `golden_library/`: a minimal frozen library fixture (2 tasks, 2 stacks, 2 starters, styles, prompts, presets, a parent app tree).
- **Settings.** Every generation setting lives in `config/settings/components/generation.py`, which WP-01 creates and imports from `base.py`. Each WP edits only the block between `# --- WP-xx ---` markers. Paths such as `GENERATION_BLOBS_ROOT` are read **at call time** (`settings.X` inside functions), because `backend/conftest.py:8` overrides `MEDIA_ROOT` per test.
- **Dependencies** (`pyproject.toml`, `uv.lock`, both Django Dockerfiles): WP-01 adds all known v3 Python dependencies up front: `tree-sitter`, `tree-sitter-language-pack`, `zstandard`, `factory-boy` (dev, if absent) and `tiktoken` (optional extra). **Not** added: statsmodels, numpy, scipy, pandas, pyarrow, datasketch (D18). Any later addition goes through CHANGES to WP-01.
- **Worktree test environment** (WP-01, first commit): `.wt-test.sh` stops mounting the main checkout's venv read-only. It uses a worktree venv volume `llmlab_wt_samplegen_venv`, created by `.wt-test.sh --init-venv` (copy `llmlab_devcontainer_venv`, then `uv sync` from the worktree's `uv.lock`), and reads its env file from `/home/ubuntu/code/llmlab-wt-samplegen/.wt/wt.env` (gitignored, mode 600) instead of the ephemeral `/tmp` scratchpad.
- **Migration rule.** WP-01 owns `generation/migrations/` and the v3 migrations of the five other apps. A wave-2 WP that needs a schema change asks WP-01 through CHANGES. Small additive changes may be made directly as `00NN_wpXX_<desc>.py`; the merger renumbers and repairs dependencies (`makemigrations --merge` is forbidden; renumber instead).
- **Tests.** They live in `backend/generation/tests/wpXX_<area>/` (or the consumer app's `tests/` for WP-02a/b). Each WP uses its own DB suffix in `.wt-test.sh` (`wp03`, `wp04`, …). `generation/_legacy/` is in `collect_ignore`.
- **Docs.** Each WP owns the doc files listed in 12.1 and updates `docs/README.md` only through CHANGES (WP-10 owns it).
- **Validity invariants** (appendix A) are acceptance criteria for **every** WP that touches the relevant data.
- **No backend edits while live agent runs are going** on the shared stack (memory note `agent-mode-ops`). The worktree tests run in isolated containers, so normal pytest is safe.
- **Commits.** Commit inside the container (pre-commit), from the worktree path. Merges to `main` need the user. Never edit `/home/ubuntu/code/AutoLLM_Code_Analyzer_rework`.

### 12.3 Work packages

Each WP lists: **Owns**, **Depends on**, **Consumes / Provides**, **Tests**, **Done when**.

---

**WP-00: v2 wipe command and cut-over runbook** (Wave 0; branch `chore/wipe-generation-v2` off `main`)

- **Owns:** `backend/generation/management/commands/wipe_generation_v2.py`, `backend/generation/tests/wp00_wipe/**`, `docs/design/V3_CUTOVER.md`.
- **Depends on:** nothing.
- **Provides:** the raw-SQL wipe (3.12) and the cut-over order (drain → wipe → deploy → migrate).
- **Tests:** the `MigrationExecutor` test in 3.12 (migrate to `generation 0022`, populate through historical models, dry run, `--yes`, migrate forward); idempotence (a second `--yes` deletes nothing); no-op on a schema without v2 tables; LLMRequest rows kept with NULL job.
- **Done when:** merged to `main` by the user; the command dry-runs on the dev1 database and prints plausible counts.

**WP-01: Foundation** (Wave 1, first in U1)

- **Owns:** see 12.1, plus package skeletons (`__init__.py` and stub modules with the signatures from this spec) for `library`, `prompts`, `study`, `budget`, `harnesses`, `agentic`, `build`, `evaluation`, `slot` and `patterns` (stub ownership passes to the area's WP on U1 merge).
- **Depends on:** G0.
- **Provides:**
  - every model in section 3 and the migrations in 3.12 (guard, `0024_v3_models` with `RenameModel` for GenerationJob and Experiment only, the five other apps' FK-string `AlterField`s, `AnalysisRun.code_scope*`, `LLMEndpoint.cost_basis`, `runtime remove_stack` depending on `generation 0024` and `runtime 0005`);
  - every pydantic contract in sections 3.4, 3.8, 4.2 (`ResourcePolicyContent`, `KNOB_META`, `GatewayConfig`), 4.8, 5.1, 6.12 (`TERMINATION_OUTCOME`), 6.15 (issue registry), 7.2, 7.4, 8.1, 8.3 and 8.7;
  - `identity.*` including `compute_keys`; `blobs.*`; `RunView` signatures incl. the bulk reader; factories, fakes, golden library; the router assembly;
  - removal of the `AGENT_*` budget settings from `base.py`; the settings component; deps; the worktree venv and `.wt/wt.env` handling; `.wt-fecheck.sh`.
- **Tests:** `canonical_json` normalization (NFC, CRLF, float rejection, key order); the **hash-algebra golden test** (fixed `IdentityInputs` → exact keys); library immutability; unique and check constraints; the guard raises on a non-empty v2 table and is a no-op on an empty DB; contract round trips; `schema.json` generation matches the checked-in file; `KNOB_META` covers every `ResourcePolicyContent` field; every issue code used in this spec exists in the registry.
- **Done when:** `manage.py migrate` succeeds on an empty DB and on a DB wiped by WP-00; system checks pass (no `fields.E300`); `makemigrations --check` is clean; every model and contract exists with the stated fields; `.wt-test.sh --init-venv` works and imports tree-sitter; `.wt-fecheck.sh check` runs against the worktree frontend.

**WP-02a: Demolition core** (Wave 1, U1)

- **Owns:** see 12.1.
- **Depends on:** WP-01 commits on the branch; G0.
- **Consumes:** WP-01 models and `RunView` signatures.
- **Provides:**
  - the `git mv` of the modules and tests listed in 2.2 into `generation/_legacy/` (never imported; `collect_ignore`; excluded from the grep), and deletion of everything else in 2.2;
  - the execution SQL rewrite (2.3) with `Study.max_parallel`/`execution_options`, `scheduling` `auto|interleaved|blocked`, lane from `run.kind`, phase-based eval claim, `post_run_slots` 0-2;
  - `inference/proxy_server.py` re-pointed to `GenerationRun` and the manifest's frozen endpoint (budget logic unchanged, handed to WP-06);
  - `runtime` API and services re-pointed (Stack API removed);
  - `RunView` implemented, including the overlay of parent rows for variants and `run_views_code(runs, scope)`; a temporary `current_runs` in `runs/view.py:_current_runs_tmp` (first usable per cell), replaced by WP-10's `runs/queries.py`;
  - wave 3: deletion of `generation/_legacy/`.
- **Tests:** execution scheduler/capacity/transition tests ported to `study`/`cell`; `RunView` unit tests per accessor; `code_files("model")` excludes starter/artifact/excluded rows; variant overlay returns parent rows as `parent`; `loc()` excludes tests and lockfiles; proxy re-point test.
- **Done when (joint U1 gate with WP-01/02b):** `grep -rniE "GenerationJob|generation_generationjob|generation_experiment|\bExperiment(Condition|Schedule|PostRun)?\b|GenerationBatch|batch_id|app_requirement|AppRequirementTemplate|ContentBlock|GenerationProfile|resolved_bundle|result_data|healing|current_jobs|scaffold" backend --include=*.py` returns only migrations and `_legacy/`; full pytest green; frontend builds.

**WP-02b: Consumer re-point** (Wave 1, U1)

- **Owns:** see 12.1.
- **Depends on:** WP-01 commits on the branch.
- **Consumes:** WP-01 models and `RunView` signatures (WP-02a's implementation; fakes until it lands on the branch).
- **Provides:** every consumer reading through `RunView` and `current_runs`; analysis on app scope with origin tagging, `tool_applicable` and `not_measured` (8.8); `reports.services.loc.source_loc`; realtime `run:`/`study:` channels with authz; webhook labels re-pointed; MCP tools re-curated (operation ids from CHANGES; until published, the MCP study tools are registered disabled); `export_study_tables --study`; `import_external_covariates` re-pointed; human_eval credentials via `evaluation.credentials(run)` (WP-09 contract; fake until it lands); healing code removed.
- **Tests:** every pre-existing test in the touched apps passes after it is ported to factories, or is deleted when it pinned removed behaviour (list in the PR); human_eval snapshot keys stable for a factory run; analysis `code_scope` recorded and findings carry origin; a tool with no applicable files yields `not_measured`; **every webhook label resolves through `apps.get_model`**; per-tool latest unchanged.
- **Done when:** the joint U1 gate passes; realtime channels authorized.

**WP-03: Library services, prompt engine, packages, seeding** (Wave 2)

- **Owns:** see 12.1.
- **Depends on:** U1.
- **Consumes:** WP-01 models and contracts; WP-05's starter rule list and WP-04's `vocab.yaml` (CHANGES).
- **Provides:**
  - the 9.1 and 9.2 APIs, including render `sources` (template ref, owner kind/ref, role, char span) for every rendered span;
  - `library.service.{create, new_version, archive, fork, fork_for_override(owner_kind, owner_ref, new_template), usage, latest, resolve_ref(ref) -> entity}`;
  - `prompts.engine.render(template_content, context) -> RenderedText(text, sources)`; `prompts.context.build_app_context(...)`, `build_slot_context(...)`; `prompts.render_set(prompt_set, context) -> list[Stimulus]`;
  - `lint.lint(kind, content) -> list[Issue]`, including starter rules, the vocabulary, and check `stated_in` tagging (8.2);
  - `seed_library`; package format v4 (ported from `_legacy/services/packages`).
- **Tests:** versioning (stale base → 409; same hash → unchanged); visibility; fork and fork-for-override (owner versioned, `derived_from` set); usage counts; render determinism (identical bytes across runs; NFC/LF; StrictUndefined errors; sandbox blocks attribute access); source spans cover the whole text without overlap; lint fixtures per kind; `stated_in` tagging on fixtures for the three prose variants; seed idempotent (two runs → 0 changes) and never touches user rows; package v4 round trip incl. blobs.
- **Done when:** all of `data/` seeds without lint errors (with WP-04's content, or the golden fixture until then); the 9.1 and 9.2 endpoints match the schemas.

**WP-04: Seed content** (Wave 2; references finished in Wave 3)

- **Owns:** see 12.1.
- **Depends on:** U1; WP-03 lint (until it lands, validate with `contracts.library.TaskSpecContent.model_validate`); WP-09 `oracle_validate` and `conformance_kit` (wave 3).
- **Provides:**
  - 33 rewritten tasks and ≥ 10 new ones, each with 3 prose variants;
  - reference implementations: for every task used by the planned confirmatory studies, and the **conformance-kit references** (`crud_todo_list`, `team_roles_multitenant`, `file_share_multipart`) for every phase-A backend framework (phase-B frameworks in wave 3); probe mutant apps are WP-09's;
  - `vocab.yaml`; default prompt templates, PromptSets and fragments (except `stack-*`, `run-contract-*`); styles, tones and guidance;
  - harness presets (values from WP-07), sampling presets (`provider-default`, `t02`, `t07`, `t10`; slot PromptSets default to `t07`), and the eval suite yaml.
- **Tests:** every task validates and lints clean; prose ↔ structured consistency (every endpoint in standard and exhaustive; minimal ≤ 120 words; forbidden phrases "evaluat", "test suite", "graded", "admin/admin", "default password"); the brief renders for a sampled 200-combination grid of (task × stack × style × auth); ≥ 10 tasks without an admin surface; wave 3: every reference passes `oracle_validate` (3 runs).
- **Done when:** `seed_library` loads everything; `lint_library` is clean; oracle status `pass` for every reference written (wave 3).

**WP-05: Stacks, starters, build context, dependency caches** (Wave 2; phase B in Wave 3)

- **Owns:** see 12.1.
- **Depends on:** U1; supplies the starter rule list to WP-03 via CHANGES on day 1.
- **Provides:** phase-A StackProfiles (16 + free-choice) with `resources` minimums; `bare` and `minimal` starters with digest-pinned bases and lockfiles; `allowed_bases.yaml` and the `run-contract-free@1` fragment; `build.context.{prepare_build_dir, BuildContextReport, build_identity}`; `build.starters.compose`; the `depcache` service (verdaccio `publish: $nobody`, devpi read-only mirror, athens, reposilite) and the sandbox env/`settings.xml` templates; wave 3: phase-B profiles (go, spring-boot, postgres variants, optional `full` level).
- **Tests:** deterministic composition (stable tree_hash); no `LLM:`/`_comment` content and no deny-listed packages in any minimal starter; every `FROM` has `@sha256`; `prepare_build_dir` verbatim for an app run, overlay + splice for a slot variant (with WP-12's assemble fake), lock files included; free-choice FROM allowlist (`base_not_allowed`); **cache isolation**: a file written to every cache path or `npm publish`ed in run A is invisible in run B, and no writable shared volume is mounted; docker-marked: every minimal starter builds and answers `/api/health` on apps-dind (logged in the PR).
- **Done when:** every phase-A (profile, level) starter is seeded and its docker test passes; the sample_builds merge note (6.10) is in `docs/STACKS.md`.

**WP-06: Gateway** (Wave 2; local translators in Wave 3)

- **Owns:** see 12.1.
- **Depends on:** U1 (and WP-02a's mechanical proxy re-point).
- **Consumes:** `ResourcePolicyContent`, `GatewayConfig` (WP-01), `budget/gateway_config.py` output (WP-11; scripted configs until then).
- **Provides:** routes and modes (5.4, 6.6): native openai_chat; **passthrough** for anthropic_messages and openai_responses (wave 2); **gemini_generate translator** (wave 2, needed by WP-07 in wave 3); anthropic and responses **translators** (wave 3); pre-call reservation; request classes; `translation.dropped`; `state.json` with `inflight`/`last_call_end`; per-call record fields (6.6); `session_id`; `cache_control` injection/stripping with TTL; `rpm_per_model`; truncation counting; shared `budget_rules.py`; local proxy parity.
- **Tests:** translation round trips with recorded fixtures (tools, tool results, streaming, stop reasons, errors, reasoning signatures), ≥ 10 per protocol and 3 model families; passthrough applies the policy on native bodies; pre-call reservation refuses (never clamps) and bounds overshoot to zero under concurrent in-flight calls; each cap fires at the right moment per request class; `call.max_input_tokens` pre-check; routing params and `session_id` injected; `cache_control` only for listed providers, stripped under `off`; `count_tokens` local; keepalive pings during upstream silence; native error shapes per protocol whose wording never matches context-overflow patterns; `state.json` atomicity; proxy parity (same scripted calls → same exhaustion point).
- **Done when:** the three ported harnesses' recorded sessions replay with byte-equal upstream requests (apart from new policy fields); Claude Code and Codex smoke sessions pass in passthrough (fixtures from WP-07); wave 3: translators pass golden fixtures for all three families and are enabled for local endpoints.

**WP-07: Harness adapters and images** (Wave 2; gemini-cli enabled in Wave 3)

- **Owns:** see 12.1; the `harness_presets/*.yaml` values are handed to WP-04 through CHANGES (or committed directly if WP-04 agrees in CHANGES).
- **Depends on:** U1; WP-06 for smoke runs.
- **Provides:** `HarnessAdapter`, the registry, 8 adapters with `HarnessFacts` filled from 5.3 (ported three enabled; goose and qwen-code enabled in wave 2 once their open questions close; claude-code and codex enabled in passthrough in wave 2; gemini-cli in wave 3), trajectory parsers (incl. incremental `parse_line`), captured prompts per pinned version, the 9.3 API, `<harness>-native` and `-controlled` preset values.
- **Tests:** per adapter with fixtures: `render()` snapshot (files, env, command; the brief never in argv; all aux-model knobs = run model; telemetry/update/web off), `parse_trajectory` on recorded output, `observed_inputs` normalization, `classify_termination` (exit 137 with elapsed ≥ wall → `WALL_CLOCK`; OOM → `OOM`; zero ok calls + transport failures → `INFRA`; documented exit codes, e.g. qwen 53 → `MAX_TURNS`); the registry refuses an enabled adapter with open questions; `min_call_output_tokens` present; docker-marked smoke run per adapter against a cheap model through the gateway on three model families (logged in the PR).
- **Done when:** the three ported adapters pass smoke tests; goose, qwen-code, claude-code and codex have facts verified (empty `open_questions`) and smoke runs, or are explicitly blocked with a reason; gemini-cli is enabled or blocked by end of wave 3.

**WP-08: Agentic run pipeline and runs API** (Wave 2)

- **Owns:** see 12.1.
- **Depends on:** U1.
- **Consumes:** `HarnessAdapter` (WP-07; FakeHarness until it lands); `budget.gateway_config.build` (WP-11; fake); `execution.api.enqueue_evaluation` (WP-11; fake); `build.context` (WP-05); `budget.ledger.settle_attempt`, `budget.live.poll` (WP-11; fakes).
- **Provides:** `agentic.dispatch.run_stage(run)`, the executor entry point for every generation kind (`app_agentic`, `slot_agent` with `SlotAgentContext` from `slot.executors.restricted_agent.prepare`; `slot_single_shot` delegated to `slot.executors.single_shot.run_stage` or the batch submitter); `execution/workers.py` calls only this function. `agentic.manifest.build_manifest(run, resolved_cell)` (copies keys); `agentic.collector.collect(...)`; `agentic.outcome.*` (TERMINATION_OUTCOME) and `agentic.validity.*`; idle detection with in-flight/CPU pause; OOM detection; time split; the 9.5 API (retry only for `infra_error`); SSE `run:` events.
- **Tests:** nested `data/`, `dist/` and `build/` survive collection; origins; lockfile up to 8 MiB kept; workspace size limit; empty or skeleton-only workspace → `output_invalid`; termination by elapsed time; `budget_exhausted` when main requests == max; **idle never fires while a call is in flight or CPU is busy** (FakeSandbox + FakeGateway with a slow call); OOM → `excluded:resource_limit`; every Termination row maps as in 6.12; `served_model_differs` uses the served name; the manifest is immutable across retries and its keys equal `compute_keys`; `RunAttempt` history; retry refused for model-caused outcomes; API responses; on_commit for retry/evaluate/cancel.
- **Done when:** an E2E test with FakeHarness, FakeGateway and FakeEval produces a `tested` run with RunFiles, RunManifest, RunAttempt and RunEvaluation, and `RunView` returns consistent data.

**WP-09: EvalSuite v2** (Wave 2; conformance kit on real starters in Wave 3)

- **Owns:** see 12.1.
- **Depends on:** U1; G0 (`sample_builds` on `main`).
- **Consumes:** `build.context` (WP-05); `sample_builds.build_sample` directly; contracts.
- **Provides:** `evaluation.suite.evaluate(run, suite, *, only_checks=None, probes=None, mode="full", image_override=None, overlay_files=None) -> RunEvaluation` and `run_eval_stage(run)` (called by the eval worker), incorporating the WIP's sample-build integration ported from the deleted `evaluation/runner.py`; `lock.resolve_lock(run)`; `reuse.find_reusable(...)`; `levels.stages_for(evaluation_spec)`; `acceptance.compile(task_version, auth_mode, stack) -> AcceptancePlan`; `acceptance.checks_for_endpoints(task_version, endpoint_ids)`; probes with preconditions and positive controls; `baseline.ensure_baseline(...)`; `oracle_validate` (3×, flaky list, mutants) and `conformance_kit`; `retest`; `credentials(run)`; the probe catalogue and probe mutant apps; tester tolerances; the 9.9 API.
- **Tests:** compiler snapshots for 3 tasks × 3 auth modes; tester against bundled mini-apps (correct, IDOR mutant, wrong-status mutant, missing-validation mutant, crash-on-start → `start_fail`, tester exception → `measured=false`, never pass rate 0); determinism (two runs equal); **probe precondition**: a probe on a missing/broken endpoint → `skipped(precondition_failed)`, never `not_exploited`; positive-control failure → skipped; every probe kind has a vulnerable mutant (`exploited`) and a fixed app (`not_exploited`); flaky detection over 3 runs; tester tolerance cases (trailing-slash redirect, 400/422, 413); lock step pins installs and a re-evaluation never re-resolves; `lock_unresolvable` → `build_fail`; eval reuse by tree; overlay mode rules and fallback; adherence on fixture trees (flask vs fastapi mislabel; tailwind share; MUI imports; free-choice record); baseline subtraction; `not_measured` for skipped levels.
- **Done when:** on the golden library the reference passes 100% three times; the bare starter scores < 50%; every probe kind's mutant pair passes; wave 3: the conformance kit passes for every phase-A framework.

**WP-10: Study engine** (Wave 2; golden plan test in Wave 3)

- **Owns:** see 12.1.
- **Depends on:** U1.
- **Consumes:** library resolve and prompt rendering (WP-03); `budget.estimate`, `budget.policy`, `budget.stop_rules.pilot_gates` (WP-11; fakes); `slot.context.resolve_context`, `slot.parents` (WP-12; fakes); `execution.api.enqueue_runs` (WP-11); `identity.compute_keys` (WP-01).
- **Provides:** Design validation (units = task versions, slot sets of one task, primary features); expansion (full and fractional; EMM flags); CRN seeds; block order; resolve (ephemeral and persisted) and `StudyPlan`; launch, freeze, pilot expand (incl. `reuse_as_main`), top-up and retry rules (6.14), clone and adhoc; `runs.queries.{current_runs, refresh_current, attrition}`; the results port with ITT default, sensitivity, paired/independent choice, few-clusters permutation tests, stated/unstated and common-check pass rates, `mixed_eval_suite`, retest agreement; the analysis plan port (patterns, sequential, estimand, random effects); the issue emission listed in 6.15; the 9.4 API; schedules and post-runs (auto patterns post-run); the doc files listed in 12.1 and `backend/docs/services.py`.
- **Tests:** expansion counts and fractional generators (aliasing warning); exclusions and compatibility pruning; CRN seeds equal across conditions incl. prose variants for the same (unit, repeat); block order deterministic per `order_seed` and repeat-major; `plan_hash` stable and sensitive to any pinned level; ephemeral resolve writes no rows; launch rejects a stale plan; confirmatory lock immutable; `prereg_hash` changes when `analysis_plan.patterns` changes; pilot gates and `reuse_as_main` (exploratory only, same plan_hash); **a usable run is never superseded** (retry/top-up/re-evaluation sequences); top-up refused for model-caused cells; `beyond_plan` runs excluded from results; ITT includes truncated runs; attrition table; the ported results tests from `_legacy` `test_experiment_*` (all gate and deviation semantics kept); wave 3: the **golden plan test** (13.3).
- **Done when:** a 2 stacks × 2 styles × 2 models × 2 tasks × 2 repeats design resolves into 64 cells (16 blocks) with correct pairing, launches with FakeHarness, and results show by-factor slices and attrition; the confirmatory gate behaves as before on ported fixtures.

**WP-11: Budget, ledger, stop rules, execution integration** (Wave 2)

- **Owns:** see 12.1.
- **Depends on:** U1 (execution ownership passes from WP-02a at U1 merge).
- **Consumes:** WP-01 models and contracts; `state.json` from WP-06 (format fixed in 4.5).
- **Provides:** `budget.policy.{effective_policy, treatment_view, lint}`; preset yaml for the three tiers; `budget.gateway_config.build(run) -> GatewayConfig`; `budget.estimate.{estimate_cell, estimate_plan}` with by_condition, by_level, fit, `auto` cap, batch factor and list cost; `budget.ledger.{reserve, reserve_block, record_usage, settle_attempt, release}`; `budget.stop_rules.{evaluate, pilot_gates}`; `budget.live.poll`; scheduler hooks: block claiming, `on_budget_exhausted`, reservation `min(run cap, cell cap_usd)`, cell caps, SpendCaps incl. 5h/week model quotas and list/token units, per-study/model/endpoint/provider concurrency, `rpm_per_model` config, `eval_parallel`, `max_resident_models`; `execution.api.{enqueue_runs, enqueue_evaluation}`; lanes by kind; `infra_retries`; the reconciler; the 9.8 API; SSE spend events; `refresh_cost_priors`.
- **Tests:** `KNOB_META` matches the 4.2 table; clamps; all three presets pass the lint (incl. `context_cap_above_call_cap` and reasoning exclusivity); each lint rule fires on a fixture; estimator vectors (prior fallback chain; heuristic formulas; batch factor; fit); `estimate_exceeds_cap` warn/error; the reservation invariant under concurrent claims (threads + `select_for_update`); **balanced stop**: after a budget stop, cell counts per (condition, unit) differ by at most 1, and complete blocks only feed paired results; cell cap; SpendCaps (block vs warn; 5h window; list_usd and tokens units; quota hit → pause with reason `quota`, runs re-queued); settlement idempotence; reconciler repair incl. block reservations; each stop rule fires and its action applies (`pause_condition` stops claiming only that condition); confirmatory refuses `ci_width_reached`; the scheduler tests ported for `study`; `enqueue_evaluation` creates claimable eval work for reference runs.
- **Done when:** a FakeGateway-driven study of 20 runs with a cap at 60% of the expected total stops cleanly and balanced, with no overspend, and the stop reason, `budget_stop` exclusions and `stopped_early` deviations are visible in results.

**WP-12: Slot mode backend** (Wave 2; integration in Wave 3)

- **Owns:** see 12.1.
- **Depends on:** U1.
- **Consumes:** `agentic` (sandbox, collector, outcome; WP-08) for `restricted_agent`; WP-06 `budget_rules` for single-shot policy; `evaluation.suite.evaluate(...)` and `acceptance.checks_for_endpoints` (WP-09); `build.context` (WP-05); prompts (WP-03); `patterns.live.score_variant` (WP-13; fake).
- **Provides:** `slot.parents.{pool, draw}`; `slot.freeze.freeze`; the symbol index (`slot.index.build(run) -> SymbolIndex`); `slot.selection.{preview, create_target, match}`; `slot.validate.validate_target`; `slot.leak.scan`; `slot.context.resolve_context`; `slot.prompt.build_slot_context`; `slot.executors.single_shot.run_stage(run)`, `batch_submitter.run_batch(...)` (behind `BATCH_BACKENDS_VERIFIED`), `restricted_agent.prepare(run) -> SlotAgentContext`; `slot.assemble.assemble` (slot rows only); `slot.evaluate.{evaluate_variant, evaluate_original}` with reuse and modes; the 9.6 API.
- **Tests:** index fixtures for each framework's route extractor (Flask tuple methods, FastAPI `@app.get` with an APIRouter prefix, Django urls, Express router prefix, Go 1.22 patterns, Spring class+method), React/Vue/Svelte components, unparsed files listed; address verification, relocation via anchors, 409 on stale; hole modes per language, **docstring and in-span comments stripped by default**; context resolution deterministic and its sha changes with the policy; **target validation** (a non-discriminating check is dropped; a flaky check is dropped; a target with none left is `uninformative`); **leak scan** (verbatim copy → error; partial → score); restricted-agent workspace excludes parent tests unless `include_tests`; parent draw reproducible per seed and stratified; `match` finds the same spec endpoint in another parent; single-shot parsing (valid JSON, fenced JSON, prose + JSON → `invalid_json`, extra file keys → `schema_violation`, `finish_reason=length` → `truncated`, empty → `empty`; no retry on invalid); temperature-0 / provider-default sampling refused; batch submitter happy path and expiry with a fake backend; restricted agent out-of-allowlist and outside-span edits, revert vs fail; assembly at byte offsets with CRLF originals; only slot RunFile rows written; the original uses the same check selection; golden `target_hash`.
- **Done when:** (wave 3) on 3 randomly drawn tested parents from 2 parent models, 3 matched targets × 2 models × 30 single-shot variants complete with zero healing, every invalid output counted, validation and leak reports shown, and the originals shown alongside.

**WP-13: Pattern analysis** (Wave 2)

- **Owns:** see 12.1.
- **Depends on:** U1.
- **Consumes:** `SlotVariantResult`, RunFiles and probe results (WP-01/WP-09 contracts); the `StudyPostRun` lane (WP-10/WP-11); the analyzer-workspace Semgrep (via the new `analysis/services/rulepack.py`); `statistics/services/inference.py` (read-only reuse); the human_eval task model (via the new `human_eval/services/detector_audit.py`).
- **Provides:** `patterns.run(pattern_run)`; `patterns.live.score_variant(run)`; token-stream normalizers; pure-Python exact + MinHash clustering with effective-n stats; detectors (semgrep via rulepack, ts_query, regex, probe, manual) with span and file scopes; features; prevalence (ITT/measured n, bounds, differential attrition, paired/independent comparisons, BH q-values, conservative adaptive intervals, MH pooled + permutation, few-clusters labelling); audits; `from-selection` drafts and cluster labels; planned/exploratory labelling vs `prereg_hash`; the export zip (incl. `features.csv` and the offline-model README); the 9.7 API; detector seed yaml with positive/negative fixtures.
- **Tests:** normalizer invariance per language (renamed locals, comments, whitespace and import order → equal sha; changed literal → different sha); clustering on synthetic variants and `low_effective_n`; each detector against its fixtures; **Semgrep `errors[]` with exit 0 → `error`, never `absent`**; taint rule through a parent helper is found at file scope; `tool_severity` copied verbatim (grep test for computed severities); Wilson/Newcombe/McNemar numeric vectors against published values; paired vs independent selection by `seed_forwarded`; ITT denominators and bounds; MH pooled estimate and permutation p on a fixture; a PatternRun with a non-registered detector set on a confirmatory study is `exploratory`; manual labels apply to all cluster members; audit sampling is stratified and seeded; export column contract (header snapshot).
- **Done when:** on a synthetic slot study (FakeExecutor producing known variants), prevalence, clusters, comparisons and the export match expected values exactly, and live provisional hits drive a `ci_width_reached` stop in an exploratory fixture.

**WP-14: Frontend: Library, prompts, budget-preset editor** (Wave 2)

- **Owns:** see 12.1.
- **Depends on:** U1 (mock with fixtures in `frontend/src/lib/api/v3/__fixtures__/` until the backend lands).
- **Provides:** the library screens (every kind listed, viewed, versioned where editable, diffed); `PinnedRefBadge`, `LibraryRefPicker`, `PromptTemplateEditor`, `DetectorEditor` (accepts a draft prefill) and the `ResourcePolicyEditor` ("Budget preset": collapsible groups, Key limits, class badges and "affects treatment" tooltips, fingerprint-change warning, inherited/override logic from `/budget/knobs/`), all exported for WP-15/16; the `/templates` → `/library` redirect; `DESIGN_SYSTEM.md` updated (PresetPicker "Manage" links → `/library/{kind}`, `invalidatePresets` covering the new library kinds, the budget-preset vocabulary).
- **Tests:** vitest for `TaskEditor` serialization (round trip with the contract fixtures); version-diff rendering; `ResourcePolicyEditor` inherited/override logic and class badges; the prompt variable autocomplete list.
- **Done when:** `.wt-fecheck.sh check` and `test` are clean; the prompt playground renders with server output.

**WP-15: Frontend: Study builder, record, operations, budget** (Wave 2)

- **Owns:** see 12.1 (`git mv` from `experiments/` and `generator/` for the reused parts).
- **Depends on:** U1; WP-14 components; WP-16's `PatternsTab` (placeholder export on day 1 at `lib/components/patterns/PatternsTab.svelte`).
- **Provides:** 10.1 routes it owns, 10.2 (builder incl. Budget preset step, Evaluation step, PlanRail with `persist=false`, cost-by-level bar and fit, ResolvedCellDrawer with source-span editing and effective roles, LaunchDialog), 10.3 (paper-structure record with `tabs.ts` aliases; Patterns section mounts WP-16's component), the `/budget` page, the header `SpendChip`, nav changes and the redirects.
- **Tests:** vitest for factor expansion counts (same fixtures as WP-10, exported as `frontend/src/lib/components/studies/__fixtures__/expansion.json`); `PowerHelper` (Wilson, paired, between-target variance); `builderState` debounce/abort/autosave; live store patching; source-span edit → override request; Playwright smoke (wave 3).
- **Done when:** a study can be designed, resolved, inspected per cell (stimuli and sources visible, a fragment edited), launched with cost confirmation, monitored live (spend and trajectory) and read in Results, all against the real backend in wave 3.

**WP-16: Frontend: Sample view, slot picker, patterns** (Wave 2)

- **Owns:** see 12.1.
- **Depends on:** U1; WP-14 components.
- **Provides:** 10.4 (`/samples/[id]`, `/applications/[id]` redirect, "Use as parent" action component exported for WP-15's tables), 10.5 (ParentPicker with random draw, "Generate a parent app" via adhoc + SSE, freeze, three-pane picker, target form with defaults labelling, preview with context, leak and cost hint, validation status, matching parts), and the Patterns content (`PatternsTab.svelte` with props `{ studyId: string }`: prevalence with ITT/measured n and audit, comparisons, clusters with labels and "Create detector from selection", feature matrix, re-run, export).
- **Tests:** vitest for selection snapping (line spans from symbol ranges); `SlotTargetIn` serialization; prevalence CI and n formatting; the file-origin filter incl. parent overlay; parent-draw form; Playwright smoke (wave 3): sample → regenerate part → target saved and validated.
- **Done when:** a researcher can draw or pick parents (or generate one), freeze them, pick an endpoint handler and a component, preview the hole, context and leak, save validated targets, add the matching parts from other parents, jump to a prefilled slot study, and later browse variants, clusters and prevalence and create a detector from a selection; fecheck is clean.

**WP-17: Frontend: consumer re-point** (Wave 2; deletion in Wave 3)

- **Owns:** see 12.1.
- **Depends on:** U1 (the backend ids and payloads from WP-02a/02b); `lib/api/v3/types.ts` (appends in its own marked block).
- **Provides:** human-eval, analysis, models and reports screens and the listed API modules re-pointed from experiment/job ids to study/run ids and `api/v3` types; wave 3: deletion of `lib/api/{generation,experiments}.ts`.
- **Tests:** existing vitest for these components updated; a grep test that nothing imports `lib/api/generation` or `lib/api/experiments`.
- **Done when:** nothing imports `lib/api/{generation,experiments}.ts`, fecheck is clean, and the human-eval wizard, analysis create and reports create flows work against U1's backend.

### 12.4 Integration notes with the other session's WIP (BuildKit sample builds)

- **Hard gate G0:** the WIP is committed and merged to `main` (user approval) and the worktree is rebased **before WP-02a starts**. The WIP edits files that v3 deletes or re-owns (`execution/{api,capacity,executor,models,scheduler,workers}.py`, `generation/services/evaluation/runner.py` (+134 lines), `generation/services/dispatcher.py`, `runtime/services/container_service.py` (+149 lines), `runtime/models.py`, the scaffolding Dockerfiles, `runtime/tests/test_stack_seeding.py`, `config/settings/base.py`, `AppRuntimeControls.svelte`, `EvaluationCard.svelte`), so rebasing later would be a large manual merge.
- Files from the WIP (`runtime/services/{sample_builds,build_backend}.py`, `execution/runtime_builds.py`, `runtime/models.py:SampleBuildArtifact`, `runtime/migrations/0005_*`, `execution/migrations/0002_*`) **will exist**. The v3 code MUST NOT re-implement them.
- `sample_builds.py` imports `runtime.services.scaffolding.{HealingReport, prepare_build_dir}` and `_context_key` reads `job.resolved_bundle`. Inside U1 (so the WIP's tests stay green): WP-01 creates `runtime/services/build_context.py` (the shim) and the `generation/build/context.py` skeleton; WP-02a implements `prepare_build_dir` for app runs (verbatim tree from `RunView`) and `build_identity`, and applies the 6.10 merge instructions to `sample_builds.py` (import line, `_context_key`, `healing="none"`). At U1 merge, `build/` and the shim pass to WP-05 (slot splice, lock files, allowed bases).
- The WIP's sample-build integration in `evaluation/runner.py` is **ported** into `evaluation/suite.py` by WP-09 (listed explicitly in WP-09's Provides), and `runner.py` is then deleted by WP-02a's demolition (it stays in `_legacy/` for reference until wave 3).
- Migration ordering: `runtime/00NN_remove_stack` depends on `generation 0024_v3_models` and `runtime 0005_sample_build_artifact`; execution migrations from WP-11 come after `execution 0002_runtimebuildlease`.
- `execution/runtime_builds.py` uses `ContainerAction`, not generation models, so it needs no change beyond the RenameModel (it is FK-safe). The memory rule in `capacity.py` includes build-lease reservations (`RuntimeBuildLease`).

---

## 13. Testing and verification

### 13.1 Backend

The isolated container runs against the worktree:

```bash
/home/ubuntu/code/llmlab-wt-samplegen/.wt-test.sh wp10 -- pytest -q backend/generation/tests/wp10_study
/home/ubuntu/code/llmlab-wt-samplegen/.wt-test.sh wp02 -- pytest -q backend/statistics backend/reports
/home/ubuntu/code/llmlab-wt-samplegen/.wt-test.sh all  -- pytest -q -x            # full suite before merge
```

- Each WP uses its own DB suffix, so runs are parallel-safe (memory note `dev-workflow-git-and-tests`).
- The container uses the worktree venv volume (`.wt-test.sh --init-venv` once, and again after dependency changes) and `.wt/wt.env` (12.2).
- Docker-dependent tests are marked `@pytest.mark.docker` and skipped unless `LLMLAB_DOCKER_TESTS=1`. They run manually on apps-dind, never during live agent runs, and their logs are pasted in the PR.
- **Live LLM tests** are marked `@pytest.mark.live_llm`. They use the user's cheap test model (`deepseek/deepseek-v4.1-flash`) with the frugal policy and are capped per test with `run.max_cost_usd=0.05`. They are never run in CI.

### 13.2 Frontend

`~/.cache/llmlab-femirror-978f/fecheck.sh` hard-codes `rsync … /home/ubuntu/code/AutoLLM_Code_Analyzer_rework/frontend/ $M/app/`, which is the **main** checkout. WP-01 adds `/home/ubuntu/code/llmlab-wt-samplegen/.wt-fecheck.sh`, a copy of that script with one change:

```bash
SRC=${FE_SRC:-/home/ubuntu/code/llmlab-wt-samplegen/frontend}
rsync -a --delete --exclude node_modules --exclude build --exclude .svelte-kit "$SRC/" $M/app/
```

It keeps the same mirror `$M`, the same `node_modules` symlink and the **same flock**, so it serializes with the main session's checks. Every invocation re-syncs, so the mirror's state between runs does not matter.

Usage: `.wt-fecheck.sh check`, `.wt-fecheck.sh test src/lib/components/studies` and `.wt-fecheck.sh build`.

If v3 adds frontend npm dependencies (none are planned), the mirror's `node_modules` must be refreshed with `npm ci` in `$M` from the worktree's lockfile, and that breaks the main session's mirror until it re-installs. **Avoid new frontend dependencies.**

### 13.3 Golden tests (identity)

Two layers, so that wave-2 churn in the renderer and policy code does not keep breaking the pinned hashes:

1. **Hash algebra** (WP-01, wave 1): `generation/tests/wp01_foundation/test_identity_algebra.py` feeds fixed `IdentityInputs` (literal dicts, no library, no renderer) to `compute_keys` and asserts exact hex values for every key, plus `canonical_json` vectors, `tree_hash` and `pinned_ref`.
2. **Golden plan** (WP-10, **wave 3**): `generation/tests/wp10_study/test_identity_golden.py` loads `generation/testing/golden_library/`, a frozen fixture library (not the evolving seed data), and builds one fixed design: 2 tasks × 2 stacks × 2 models × 1 style × 1 auth × 2 prose variants × 2 repeats, base_seed 12345. It asserts exact hex values for every `content_hash`; `task_hash`, `treatment_hash`, `prompt_hash`, `config_fingerprint` and `run_fingerprint` for 3 chosen cells; every seed (equal across prose variants); the block order; and `plan_hash`.

Its slot counterpart, `wp12_slot/test_slot_golden.py`, uses the fixture parent tree under `golden_library/parent_app/` and asserts the `target_hash` for one target of each kind, the resolved-context sha for two context policies, and the `normalized_sha` for 3 fixture variants (Python, TSX, Go).

Changing any constant requires a protocol bump and a changelog line in `docs/STUDIES.md#identity-changelog`. A comment at the top of each test says exactly that.

### 13.4 Invariant tests (cross-WP; owned by the listed WP)

| Invariant | Test (owner) |
|---|---|
| No silent clean: empty or skeleton-only workspace → `output_invalid`; tester error → `measured=false` → `infra_error`; detector error (incl. Semgrep `errors[]` with exit 0) → `error`; a probe on a broken endpoint → `skipped(precondition_failed)`; a tool with no applicable files → `not_measured`; skipped evaluation levels → "not measured" | WP-08, WP-09, WP-13, WP-02b |
| Only tool severities are shown: no severity field is computed anywhere in `patterns`, `evaluation` or `analysis` stats (grep test for `severity =` assignments outside copy-through) | WP-13 |
| Fingerprints are compared per (condition, unit); a mixed `config_fingerprint` inside a (condition, unit) is flagged; mixed eval suites are flagged | WP-10 |
| **A usable run is never superseded**; top-up and retry refused for model-caused outcomes; `beyond_plan` runs excluded | WP-10, WP-08 |
| ITT: truncated runs are in the default analysis; the exclusion is a sensitivity row | WP-10 |
| Per-tool latest in analysis consumers is unchanged | WP-02b |
| Confirmatory gate: planned sample, stopped early, multiplicity, `significance_unreachable`, exclusion rate, differential attrition | WP-10 |
| Pre-registration covers the pattern plan; a non-registered PatternRun is labelled exploratory | WP-10, WP-13 |
| on_commit dispatch: launch, top-up, retry, evaluate, enqueue_evaluation and cancel enqueue only after commit (`django_capture_on_commit_callbacks`) | WP-10, WP-08, WP-11 |
| Budget never in prompts: rendering with two policies gives an equal `prompt_hash` | WP-03, WP-10 |
| Reservation invariant (4.5) and **balanced budget stop** (counts per (condition, unit) differ by ≤ 1) | WP-11 |
| Gateway pre-call reservation: concurrent in-flight calls never overshoot the run cap | WP-06 |
| Idle timer never fires during an in-flight call | WP-08 |
| Cache isolation: nothing written in run A is visible in run B | WP-05 |
| Re-evaluation never re-resolves dependencies (lock reused) | WP-09 |
| Slot targets: linked checks discriminate; verbatim leaks block | WP-12 |
| Analysis code scope: analysis input is app scope; model/slot attribution by origin at stats time | WP-02b |
| Nested-directory collection | WP-08 |
| Webhook labels resolve through `apps.get_model` | WP-02b |
| Identity keys computed only by `identity.compute_keys` (manifest keys equal recomputation) | WP-08 |

### 13.5 Wave-3 end-to-end acceptance

1. Seed the library; run `conformance_kit` (every phase-A framework passes) and `oracle_validate` for the referenced tasks (3 runs each, probe mutants exploited).
2. Run an app pilot (frugal, exploratory): `{opencode-native, claude-code-native, goose-native} × {flask-react, express-react} × {crud_todo_list, notes_markdown_render, file_share_multipart} × 2 models × 1 repeat`, with smoke cells first. Check:
   - every run has a manifest, RunFiles, an attempt (request classes, served providers), a resolved lock and an evaluation at level `security`;
   - spend total ≤ cap, billed and list-price totals shown;
   - pilot gates evaluated (cap hits, truncations, wall clock, OOM);
   - the per-factor slices and the attrition table render; adherence records exist.
3. Expand the pilot to 5 repeats on one stack (`reuse_as_main`), with the study cap set to stop it mid-way. Expected: it stops **balanced** (≤ 1 difference per (condition, unit)), results show `budget_stop` exclusions and `stopped_early` deviations, and the ledger reconciles exactly to the `calls.jsonl` sum.
4. Slot pilot: draw **3 random tested parents** of one task from at least 2 parent models (`ParentSelection(rule=random, k=3)`). Targets: the login handler, `PATCH /api/todos/{id}` and the markdown render component, added once and matched across parents; validation and leak scans complete. Two models × 30 single-shot (temperature 0.7) plus 1 model × 10 restricted agent. Check:
   - prevalence for `py-plaintext-password-compare`, `ownership-check-present` (ts_query) and `js-dangerouslysetinnerhtml`, with ITT and measured n, Wilson CIs and paired comparisons;
   - the clusters with distinct counts and `low_effective_n` where applicable;
   - `same_author` and parent columns in variants.csv; the original shown as variant 0 (reference point);
   - a detector created from a selection and a re-run PatternRun (no LLM cost);
   - an audit sample created for one primary feature;
   - the export zip opens and its columns match the README.
5. Playwright smoke tests (WP-15 and WP-16) using the out-of-tree Playwright setup (memory note `playwright-debug-setup`; no new frontend dependency).

---

## 14. Decisions log

Every issue raised by the three critic reviews of revision 1, and how revision 2 resolved it. IDs: **V** = research validity, **C** = codebase fit, **U** = researcher UX and budget; B = blocker, M = major, m = minor. "Accepted" means implemented as proposed; "Adapted" means the goal is met by a different mechanism (reason given); "Rejected" gives the reason.

### 14.1 Research validity

| ID | Issue | Decision | Where |
|---|---|---|---|
| V-B1 | "Latest run is current" + top-up/retry = best of k | Accepted: current run = first usable attempt; retry only `infra_error`; top-up only empty/infra/excluded cells; `beyond_plan` runs excluded; invariant test | D14, 1.3, 3.6, 3.7, 6.14, 9.4, 13.4 |
| V-B2 | Probes return `not_exploited` on broken endpoints | Accepted: `requires_checks` preconditions, benign positive control, `skipped(precondition_failed)` excluded from `probes_tested`, n/a in slot features; vulnerable mutant per probe kind in `oracle_validate` | 8.3, 8.5, 7.9 |
| V-B3 | Linked checks may not exercise the slot | Accepted: target validation (original 3×, holed 1×), discriminating checks only, `dropped_checks`, `slot_tests_uninformative` (warn/error), probe stability, `linked_checks_source` + diff | 3.9, 7.4, 6.15 |
| V-B4 | Pattern analysis outside `prereg_hash` | Accepted: `analysis_plan.patterns` in `prereg_hash`; non-matching PatternRuns labelled exploratory; `planned` only for locked features | D15, 3.1, 3.9, 7.9 step 9 |
| V-M1 | One hand-picked parent | Accepted: `ParentSelection` (random default, stratified, recorded), same part from k parents default (≥3 from ≥2 models), confirmatory error for manual/single parent unless `conditional_on_parent`; e2e uses random parents | 7.2, 7.10, 6.15, 13.5 |
| V-M2 | Parent authorship confound | Accepted: `parent_model`/`parent_harness` recorded and exported, `variant_model_equals_parent_model` warn, Latin-square recommendation, `same_author` column | 3.9, 3.8, 7.2, 7.9 |
| V-M3 | Original leaks via docstring, workspace, context | Accepted: stub strips docstring and in-span comments by default (`keep_docstring` explicit, hashed), `include_tests` applies to agent workspaces, artifact rows excluded, leak scan with error on verbatim copy, `leak_score` exported | 7.4, 7.6, 3.1 |
| V-M4 | Independent CIs on paired (CRN) data; near-duplicate replicates | Accepted: paired methods (Newcombe 10 / McNemar) when both conditions forward seeds, method recorded; distinct clusters next to n, `low_effective_n`; slot studies require temperature > 0 (`slot_sampling_provider_default`) | 7.9, 7.6, 4.3 |
| V-M5 | Served provider uncontrolled; `data_collection` fp=no | Accepted: confirmatory requires one `routing.only` provider + no fallbacks (`provider_unpinned`), served providers exported per run, `data_collection` class C | 4.2, 4.3, 3.7 |
| V-M6 | USD caps confound models; truncated runs dropped | Accepted: `budget_basis` (tokens default for confirmatory multi-model, USD cap non-binding), `usd_cap_binding` gate, v3 truncation definition, ITT default with truncation exclusion as sensitivity, `max_truncation_rate` gate | 4.2, 4.3, 1.3, 9.4, D14 |
| V-M7 | Output cap vs harness and reasoning | Accepted: `HarnessFacts.min_call_output_tokens` + lint error, upstream `max_tokens` = cap + reasoning budget, `call_truncations` note and gate, `slot_output_cap_tight` | 5.3, 4.2, 4.3, 6.6 |
| V-M8 | Idle timer fires during slow calls | Accepted: `inflight`/`last_call_end` in `state.json`, idle clock pauses while in flight or CPU busy, tokens/s per call, `max_wall_clock_rate` gate | 6.5, 6.6, 4.2 |
| V-M9 | Terminations without outcomes; treatment-caused zero-call runs | Accepted: Termination × outcome table (LLM_ERROR → infra_error with evidence; new `OOM` → excluded:resource_limit), resolve-time `brief_exceeds_call_cap` and `model_lacks_tools`, attrition table | 6.12, 6.13, 5.1 |
| V-M10 | Prose variant both unit and factor | Accepted: unit = task@version, `prose_variant` is a factor, seeds from the task ref, `task_hash` keeps the variant; `stated_in` tagging with stated/unstated pass rates | 3.1, 3.6, 9.4, 8.2 |
| V-M11 | Tester proven on one stack only | Accepted: tester conformance kit per backend framework (3 tasks), `oracle_validate` 3× with flaky checks excluded and shown, 5% test-retest; documented tester tolerances | 8.5 |
| V-M12 | Detector validity unmeasured; Semgrep exit-0 errors; span filter misses taint | Accepted: Semgrep `errors[]`/unparsed → `error`; stratified human audit with precision/NPV; taint on whole file; span and file scopes as separate features | 7.9, 3.9 |
| V-M13 | Denominators shrink differently | Accepted: ITT n, measured n, worst/best bounds above 5% attrition, `differential_attrition`, correctness rate always shown with conditional metrics | 7.9, 6.13 |
| V-M14 | Cluster-robust SE / bootstrap with 3-6 clusters | Adapted: below 20 clusters no cluster-robust SE or bootstrap; per-target estimates, MH stratified pooling and exact permutation tests labelled `few_clusters`; Bayesian GLMM not in-app (D18, see C-M12a), fitted offline; power helper includes between-target variance | 7.9, 9.4, 10.2 |
| V-M15 | Shared caches allow cross-run contamination | Accepted: verdaccio `publish: $nobody`, read-only devpi, Maven proxy + throwaway `~/.m2`, no writable shared volumes, isolation test | 4.2, 6.5, WP-05 |
| V-M16 | Builds re-resolve dependencies | Accepted: platform lock step → `resolved_lock`, constraint-pinned installs, `lock_source`, `registry_snapshot_at`, re-evaluations never re-resolve | D12, 6.10, 3.7 |
| V-M17 | Free choice and cross-language SAST not comparable | Accepted: allowed bases rendered into the brief and hashed (`base_not_allowed`), `tool_applicable`, cross-stack comparisons on applicable tools per KLOC, free-choice pick reported as outcome/mediator | 6.10, 8.8, 3.1 |
| V-M18 | Harnesses not comparable on equal terms | Accepted: request classes with `max_llm_requests` = main only and `run.max_aux_requests`; `translation.dropped` + `translation_lossy`; `protocol_mode`/front protocol in `treatment_hash`; `reasoning.exclude` class C and forced false for replay protocols; `harness_brief_differs`; "harness bundle" label | 5.4, 5.5, 4.2, 3.1 |
| V-M19 | Resources differ by stack | Accepted: sandbox resources resolved per study to the max over profiles, `resources_vary_by_stack`, per-stack OOM gate, install time share recorded | 4.2, 4.3, 11.4, 6.5 |
| V-M20 | Stopping at cap unbalances conditions | Accepted (merged with U-M2): block claiming with whole-block reservations, `on_budget_exhausted=stop_balanced`, complete blocks only for paired results, `cell_budget`/`budget_stop` per condition | D7, 4.5, 9.4 |
| V-m1 | `eval_suite` in `config_fingerprint` | Accepted: removed; `mixed_eval_suite` issue | 3.1 |
| V-m2 | Wilson invalid under optional stopping | Accepted: `adaptive_n` exported; conservative repeated-look interval (Wilson at α/K) | 4.6, 7.9 |
| V-m3 | Model aliases | Accepted: refused in confirmatory (`model_alias_in_confirmatory`); response `model` and `system_fingerprint` recorded per call | 4.3, 6.6, App. A |
| V-m4 | Original is a single draw | Accepted: labelled reference point, never a control | 1.3, 7.9 |
| V-m5 | Exploratory multiplicity | Accepted: BH q-values + family size | 7.9, 9.7 |
| V-m6 | Pilot/main pooling | Accepted: different `plan_hash` never pooled; `reuse_as_main` only with an unchanged plan_hash | 4.6, 9.4 |
| V-m7 | Marginal means on pruned/fractional grids | Accepted: EMMs flagged `unbalanced` | 9.4 |
| V-m8 | Cookie session adds CSRF checks | Accepted: common-check pass rate + by-category breakdown | 8.2 |
| V-m9 | Slot context larger than model window | Accepted: `slot_context_exceeds_model` (error) | 4.3 |
| V-m10 | Start time covariate | Accepted: `started_at` exported | 3.7, 9.4 |

### 14.2 Codebase fit and feasibility

| ID | Issue | Decision | Where |
|---|---|---|---|
| C-B1 | Wipe/migrate order cannot work | Accepted: raw-SQL `wipe_generation_v2` on v2 table names (WP-00, ships on `main` first), MigrationExecutor test from `generation 0022`, cut-over order drain → wipe → deploy → migrate | 3.12, WP-00 |
| C-B2 | WP-01 cannot merge green alone | Accepted: merge unit U1 (WP-01 → WP-02a ∥ WP-02b, one joint gate); WP-01 edits the six FK strings incl. `inference/models.py`; `generation/tests/factories.py` re-export shim | 12.1, 2.3, WP-01 |
| C-B3 | Ported code has nowhere to live in wave 1 | Accepted: `generation/_legacy/` via `git mv` (never imported, `collect_ignore`, excluded from grep), deleted in wave 3 | 2.1, 2.2, WP-02a |
| C-B4 | Worktree test container lacks v3 deps | Accepted: worktree venv volume (`--init-venv`), `.wt/wt.env` in the worktree (gitignored), deps in both Django Dockerfiles | 12.2, WP-01 |
| C-M1 | Execution raw SQL is not a mechanical rename | Accepted: `Study.max_parallel` + `execution_options`, scheduling `auto\|interleaved\|blocked` (default auto), `concurrency.max_resident_models`, SQL rewritten on the new tables/columns, `batch_id` dropped | 3.6, 2.3, 4.2 |
| C-M2 | Reference runs on an "eval" lane | Accepted: no eval lane; reference runs created `running/eval_queued` via `execution.api.enqueue_evaluation` in on_commit | D6, 3.7, WP-11 |
| C-M3 | Frontend consumers unowned | Accepted: new WP-17 | 12.1, WP-17 |
| C-M4 | Unowned or split-owned backend files | Accepted: proxy_server (WP-02a then WP-06), MCP tools re-curated and webhooks (WP-02b), `backend/docs/services.py` (WP-10), `export_study_tables` and `import_external_covariates` (WP-02b), `AGENT_*` settings removal (WP-01) | 2.3, 12.1 |
| C-M5 | Collision with the other session's WIP | Accepted: hard gate G0 before WP-02a; WP-09 calls `sample_builds` directly and ports the WIP's runner logic; no `_build` adapter | 12.1, 12.4, 6.10 |
| C-M6 | Analysis code scope can produce silent clean results | Accepted: analysis always on app scope, findings tagged with origin, attribution at stats time, `not_measured` when nothing applies | 8.8, 2.3 |
| C-M7 | Identity keys computed in two places | Accepted: `identity.compute_keys` in WP-01; manifest copies keys; hash-algebra golden in WP-01, golden plan test in wave 3 | 3.1, 6.2, 13.3 |
| C-M8 | Single `Study.slot_set` FK | Accepted: dropped; slot sets derived via units; `Design.units.slot_sets[]` validated to one task | 3.6, 9.4 |
| C-M9 | Slot evaluation cost at N=100 | Accepted: `caches.reuse_eval_by_tree` (`reused_from`), `slot.mode full\|overlay\|static_only` (overlay rules in 4.8; evaluation setting per D16) | 4.2, 4.8, 7.8 |
| C-M10 | WP-02 unsized critical path | Accepted: split into WP-02a (core, execution, inference, runtime) and WP-02b (other apps) | 12.1 |
| C-M11 | Grep misses silent breakage; webhooks | Accepted: the case-insensitive extended grep; webhook label test | WP-02a, WP-02b, 13.4 |
| C-M12a | statsmodels/pyarrow in the executor | Accepted: dropped (also datasketch); reuse `statistics/services/inference.py`; offline GLMM from `features.csv` | D18, 7.9 |
| C-M12b | `pattern-tools` image duplicates analyzer Semgrep | Adapted: no image; Semgrep runs through the analyzer workspace via `analysis/services/rulepack.py`. Detectors are not stored as AnalysisRun rows (per-variant AnalysisRun overhead at N=100 is too high); `tool_severity` copy-through is kept | 2.1, 7.9 |
| C-M12c | 72 starters | Accepted: phase A = bare + minimal for flask/fastapi/django/express × 4 frontends + free-choice; go, spring, postgres and `full` in phase B (still in scope per the user's stack decision) | 11.4, WP-05 |
| C-M12d | Oracle for every task | Accepted: oracle required only for confirmatory-used tasks; conformance-kit references per framework (V-M11) | 8.5, WP-04 |
| C-M12e | Translators first | Accepted: passthrough to OpenRouter native `/messages` and `/responses` first; translators for local endpoints in wave 3 (Gemini translator still needed in wave 2-3) | D17, 5.4 |
| C-m1 | `remove_stack` ordering | Accepted: depends on `generation 0024` and `runtime 0005` | 3.12 |
| C-m2 | Wrong LOC helper names | Accepted: `analysis/services/base.py NON_SOURCE_DIRS`; WP-02b adds `reports.services.loc.source_loc` | 2.3, 3.7 |
| C-m3 | `ContextPolicy.resolved_files` missing | Adapted: resolved context is a separate `ResolvedContext` per (target, policy), computed at resolve (also U-M7) | 7.4 |
| C-m4 | Who adds tree-sitter | Accepted: WP-01 | 12.2 |
| C-m5 | `eval_suite` in fingerprint | Accepted (= V-m1) | 3.1 |
| C-m6 | Reserving full run cap for cheap calls | Accepted (= U-m1): reserve `min(run cap, estimate cap_usd)` | 4.4, 4.5 |
| C-m7 | Resolve every 500 ms persists rows | Accepted (= U-m7): ephemeral resolves, persist on freeze/launch, render once per (condition, unit), GC drafts | 3.6, 6.1 |
| C-m8 | Parent RunFile rows per variant | Accepted: slot rows only, RunView overlays parent rows | 3.7, 7.7 |
| C-m9 | `.values()` and bulk blob reads | Accepted: `.values()` on documented metrics keys; `run_views_code` bulk reader | 2.3, 3.11 |
| C-m10 | Playwright not in package.json | Accepted: out-of-tree Playwright setup | 13.5 |
| C-m11 | `/runs` route and `runs.ts` collide | Accepted: `/samples/[id]`, `v3/samples.ts` | 10.1, 10.6 |
| C-m12 | Unowned docs | Accepted: USER_GUIDE, QUICKSTART, STUDY_TABLES, HUMAN_EVALUATION, api-reference → WP-10 | 12.1 |
| C-m13 | `GENERATION_BLOBS_ROOT` at import time | Accepted: read at call time | 12.2, 3.2 |
| C-m14 | No hard cap for local endpoints | Accepted: `study.max_total_tokens`, `study.max_runs`, `study.max_wall_hours`; token-unit SpendCaps | 4.2, 3.10 |
| C-m15 | `post_run_slots` max 1 | Accepted: 0-2; live detectors per variant on the eval lane | 2.3, 7.8 |
| C-m16 | RenameModel for minor models | Accepted: Delete + Create for condition/schedule/post-run | D5, 3.12 |
| C-m17 | Broken cross-references | Accepted: fixed (`RunAttempt.termination` → 5.1; component moves → 10.8) | 3.7, 2.2 |

### 14.3 Researcher UX and budget control

| ID | Issue | Decision | Where |
|---|---|---|---|
| U-B1 | Frugal call cap below context cap | Accepted: frugal `call.max_input_tokens` 72k (standard 144k), lint `context_cap_above_call_cap`, note `call_cap_without_compaction`, preset lint test | 4.2, 4.3, WP-11 |
| U-B2 | Frugal sets both reasoning effort and budget | Accepted: `reasoning {mode, effort?, max_tokens?}` exclusive; frugal `effort=low`; budget < output cap and ≥ 1024 for Anthropic | 4.2, 4.3 |
| U-M1 | No estimate-vs-cap warning or fit helper | Accepted: `estimate_exceeds_cap` warn/error with quick fixes, `by_condition`, `by_level`, `fit`, `study.max_cost_usd: "auto"`, cost-by-level bar | 4.4, 4.2, 10.2 |
| U-M2 | Block order undefined | Accepted (= V-M20): repeat-major block order, `on_budget_exhausted`, balanced-stop invariant | 9.4, 4.5 |
| U-M3 | Batch serving missing | Adapted: `routing.serving_tier` incl. `auto_batch` (resolved per study so tiers never mix), `batch_submitter`, ×0.5 estimate. Only OpenRouter batch, gated until verified; direct Anthropic/OpenAI batch rejected for now (no direct provider endpoints are configured) | 4.2, 7.6, 4.4 |
| U-M4 | Subscription/local endpoints invisible to budget | Accepted: `list_cost_usd`, `LLMEndpoint.cost_basis`, SpendCap `5h`/`week` and `usd\|list_usd\|tokens`, `cap_basis`, billed vs list in the UI, quota hit → pause (`quota`) | 3.10, 4.2, 4.5, 4.7 |
| U-M5 | Pilot runs thrown away; gates ignore cap hits | Accepted: `pilot.reuse_as_main` (exploratory, same plan_hash), `max_cap_hit_rate` gate, cap-hits row and re-pilot flow | 4.2, 4.6, 10.3 |
| U-M6 | Precision stop needs features only computed at the end | Accepted: `Design.slot_primary_features`, live per-variant scoring on the eval lane, `n_min`/`n_max`, PowerHelper saving | 4.6, 7.8, 7.9 |
| U-M7 | `hole_mode`/`context_policy` both target property and factor | Accepted: target keeps defaults only; factors override; resolved context per (target, policy) hashed into `treatment_hash`; UI labels "default (can be varied)" | 3.9, 3.1, 7.4, 10.5 |
| U-M8 | No parent picker or quick parent generation | Accepted: ParentPicker, "Generate a parent app" via adhoc, "Use as parent" action | 10.4, 10.5, 9.6 |
| U-M9 | Pattern loop one-way | Accepted: auto-attached patterns post-run, `default` defined, "Create detector from selection", re-run without LLM cost, `manual` engine (ClusterLabel) | 7.9, 9.7, 3.9 |
| U-M10 | Frugal drops security outcomes; evaluation edited twice | Accepted: `Design.evaluation` with `eval_level` (default `security` in every tier), linked probes always run in slot mode, builder section 6 only, "not measured" labels | D16, 4.8, 10.2 |
| U-M11 | ~55 knobs flat | Accepted: collapsible editor, Key limits group, accordion groups, "affects treatment" badges (classes C/R), fingerprint warning, "Budget preset" label | 4.2, 10.2, WP-14 |
| U-M12 | Overrides cover PromptSet roles only | Accepted: render `sources` spans, edit any owning entity (fork + factor swap, `owner_kind` recorded), effective roles per harness with the captured native prompt | 9.2, 3.6, 10.2 |
| U-M13 | One SlotSet per study | Accepted (= C-M8) plus grouping by parent and "add matching endpoint" | 3.6, 7.4, 10.2 |
| U-m1 | Single-shot reserves $0.30 | Accepted (= C-m6) | 4.5 |
| U-m2 | `max_price` ×1.0 drops providers | Accepted: `model×1.25`, `no_provider` → infra_error, `no_provider_rate` pause rule | 4.2, 4.6 |
| U-m3 | Missing knobs | Accepted: `run.max_uncached_input_tokens`, `caching.ttl`, `concurrency.rpm_per_model`, `pilot.smoke_first`, `run.max_workspace_bytes`, `routing.require_parameters`, `concurrency.eval_parallel` | 4.2 |
| U-m4 | Machine time at N=100 | Accepted (merged with C-M9 as `caches.reuse_eval_by_tree`); reused rows excluded from runtime-nondeterminism analyses | 4.2, 7.8 |
| U-m5 | No cost hint when creating a target | Accepted: `cost_hint` in preview | 9.6, 10.5 |
| U-m6 | Model picker lacks price/capability | Accepted | 10.2 |
| U-m7 | PlanRail persists on every resolve | Accepted (= C-m7) | 6.1, 9.4 |
| U-m8 | Prose variant ambiguous | Accepted (= V-M10) | 9.4 |
| U-m9 | Tabs conflict with the paper structure | Accepted: Abstract · Method · Results · Patterns · Data · Operations, `tabs.ts` aliases, StudyMasthead/StudyFigure, interim p withheld | 10.3 |
| U-m10 | Nav placement; global spend | Accepted: Slots and Budget under Generate; header spend chip | 10.1 |
| U-m11 | No per-sample cost | Accepted: `cost_per_usable_sample`, `cost_per_tested_sample` | 4.7, 10.3 |
| U-m12 | Stale DESIGN_SYSTEM links | Accepted: WP-14 owns `DESIGN_SYSTEM.md` updates | WP-14 |

### 14.4 Research inputs integrated

- **Harness research** (`harness-research.md`): pins, licenses, protocols, headless commands, env, trajectory formats and open questions per harness → 5.3; gateway recommendation (stdlib adapters, no LiteLLM, passthrough spike, phasing) → 5.4, D17; budget knob mapping per harness → 5.3 table.
- **Budget research** (`budget-research.md`): validity classes C/R/O/S → 4.1; settings catalogue with F/S/G defaults → 4.2; pre-call reservation, scheduler-level caps and settlement → 4.5; prompt caching with sticky sessions, reasoning, context cap, batch, cascades, pilot-then-expand, replicate blocks, group-sequential designs → 4.2-4.6; dry-run estimation method → 4.4. Where the research and a critic disagreed (research: clamp `max_tokens` to what remains; critics: never change treatment mid-run), the gateway **refuses** instead of clamping (4.1 principle 4).

---

## Appendix A: Validity invariants (checklist for reviewers)

1. Everything a sample depends on is in its RunManifest, by pinned ref and digest. Nothing is read as "latest" at run or eval time. Model aliases (`-latest`, `:latest`, router slugs) are refused in confirmatory studies; the response `model` and `system_fingerprint` are recorded per call.
2. Budget, seed and ids never appear in rendered stimuli.
3. No platform-planted security content:
   - no admin default password;
   - no preinstalled security libraries in minimal starters;
   - no hidden instructions in starter files;
   - the auth contract is a factor, and its text is hashed.
4. Model-scope code = `authored ∪ modified ∪ slot`, excluding artifacts, vendored code and lockfiles. LOC uses source LOC without tests. Analysis input is app scope; attribution is by origin.
5. Outcomes follow the taxonomy and the Termination × outcome table in 6.12. Model-caused bad output is `COMPLETED` with a non-`tested` outcome, never `FAILED`. `FAILED` and `infra_error` are infra or config problems; treatment-caused config problems are caught at resolve.
6. Termination comes from elapsed time and gateway facts, never from exit codes alone. The idle timer pauses during in-flight calls.
7. `RunEvaluation` is append-only, and readers take the latest per suite (retests excluded). Metrics are never overwritten in place by probes or smoke checks.
8. Pass rates exclude infra checks and flaky checks. The starter baseline is stored and shown. Probes are separate from pass rates and need their preconditions.
9. Slot single-shot: no healing, no repair, no merging. Invalid output is a data point.
10. Detectors and analyses copy `tool_severity` verbatim. Prevalence is presence or absence and never a severity. A detector error is never `absent`.
11. Confirmatory studies are locked at launch (`prereg_hash`, incl. the pattern plan), pilot cells are excluded, and adaptive stopping is refused (pre-registered group-sequential designs excepted).
12. Every dispatch side effect runs via `transaction.on_commit`.
13. Retries keep the manifest and seed. A usable run is never superseded. Retry, exclusion and attrition are reported per condition.
14. Budget stops are balanced; paired results use complete blocks only.
15. The default analysis is intention-to-treat; truncation exclusion is a sensitivity analysis.
16. Slot targets have discriminating linked checks and no verbatim leak of the original; the original is a reference point, not a control.
17. Comparisons say which method they used (paired or independent); few-cluster inference never uses cluster-robust SEs.

## Appendix B: Risks and open questions

| Item | Owner | Default if unresolved |
|---|---|---|
| OpenRouter native `/messages` and `/responses` may not accept every `provider` routing field or return `usage.cost` | WP-06 / WP-07 | Apply what is accepted; fall back to `/generation` backfill for cost; record `routing_partial` and keep the harness exploratory-only until verified |
| Gemini CLI loopback forwarder may be killed by the agent | WP-07 | Neutral process name; forwarder restarts on exit; a killed forwarder ends the run as `INFRA` |
| Codex model-family tool selection for unknown slugs | WP-07 | Pin instructions and force function tools; if impossible, keep `enabled=False` with reason |
| Claude Code with non-Claude models (unsupported by Anthropic) | WP-07 | Allowed, tagged `vendor_affinity=anthropic`, reported |
| OpenRouter batch API is beta and secondhand-documented | WP-12 | `BATCH_BACKENDS_VERIFIED=False`; `auto_batch` resolves to `standard` |
| Go/Spring starters + lockfiles are the most expensive | WP-05 | Phase B in wave 3; resolver marks missing starters `starter_missing` (error) |
| Postgres sidecars double sandbox memory | WP-05 / WP-11 | Profile `resources.min_memory=3g`; study-level max; scheduler counts the sidecar in the memory rule |
| Tree-sitter Vue/Svelte SFC parsing quality | WP-12 | Fall back to `whole_file` components; region selection still works |
| Mixed models not available in-app | WP-13 | MH pooling + permutation in-app; GLMM offline from `features.csv` |
| Live spend via `docker exec` polling at high concurrency | WP-11 | Poll interval scales with running count (10 s × ceil(n/8)) |
| apps-dind is 4 GB: builds, validations and sandboxes contend | WP-11 | Memory rule includes build-lease reservations (`RuntimeBuildLease`); `concurrency.eval_parallel`; eval reuse by tree |
| Target validation triples original evaluation cost | WP-12 | Machine time only; validations run on the eval lane with low priority and are reused across studies of the same target |
| Detector audits need human coders | WP-13 | Prevalence shows "detector not audited" until coded |

