# Harness adapters (v3)

Owner: WP-07. Spec: `docs/design/SAMPLE_GEN_V3.md` sections 5 (adapter interface), 6.5 (entry script) and 9.3 (API).
Code: `backend/generation/harnesses/`, images: `backend/generation/images/agent-*/`, tests:
`backend/generation/tests/wp07_harnesses/`.

## What a harness adapter is

A harness is the agent program that drives the model. Cross-harness results compare **harness bundles**: the
harness plus its native system prompt, tool set and request behaviour. Each adapter
(`harnesses/<name>.py`, a `HarnessAdapter` subclass) provides:

| Member | Meaning |
|---|---|
| `facts: HarnessFacts` | verified facts (pins, flags, env names, knobs, exit codes, normalizers, `open_questions`) |
| `capabilities` | what the harness can be told (turn cap, system prompt, tool policy, compaction, context) |
| `versions` | releases whose facts and captured prompt are verified |
| `image_spec(version)` | `images/<image_dir>` + build arg |
| `gateway_wiring(host)` | front protocol, mode (`native` / `passthrough` / `translate`), base URL |
| `render(ctx)` | files, env and command (`RenderedHarness`) |
| `parse_trajectory(outputs, calls)` | `Trajectory` from the harness output + gateway log |
| `observed_inputs(calls)` | `ObservedInputs` from the first `main` call in the gateway log |
| `classify_termination(exit, traj)` | section 5.1 default rules (`base.default_termination`), inherited by all |
| `unsupported_knobs(ctx)` | active knobs the harness cannot map (manifest + `unsupported_knob:<key>` notes) |
| `render_record(rendered)` | `config_files_sha` + `command_sha` for the manifest (brief excluded) |
| `parser()` | fresh incremental parser (`parse_line`) for live `trajectory_step` events |

## Section 5.2 rules, as implemented

1. Brief on stdin or in a file (`/run/brief.md`), never argv (tested per adapter).
2. Fresh `HOME=/run/home` (XDG dirs inside it) for every harness; per-harness disable switches in `facts.disable_knobs`.
3. Every auxiliary-model knob is the run's model (`facts.aux_model_knobs`, tested).
4. Approvals automatic (`bypassPermissions`, `yolo`, `GOOSE_MODE=auto`, `approval_policy=never`).
5. Knob mapping in `render()`; `base.knobs(ctx)` resolves preset + policy (the stricter turn cap wins).
6. Captured prompts in `harnesses/captured/<name>-<version>.md`; smoke fixtures in
   `tests/wp07_harnesses/fixtures/<name>-<version>/`.
7. Native system prompt observed from the gateway log; `system_prompt_sha` is hashed after
   `facts.system_prompt_normalizers`. `native_system_prompt_blob` = `text_sha` of the verbatim prompt, which
   is what `blobs.put_text(common.native_system_prompt_text(calls))` returns.
8. No harness-side cost caps (`--max-budget-usd`, mini `cost_limit` stays 0).

## The sandbox layout and entry script

`base.sandbox_payload(rendered, wall_clock_s=..., max_workspace_bytes=...)` returns the files to put into the
agent container (rendered files + `/run/harness/command.sh` + `/run/entry.sh`), the env and the entrypoint
`sh /run/entry.sh`. The entry script (section 6.5): `git init` + commit + tag `base`; `core.excludesFile=/dev/null`
and anchored `.git/info/exclude` patterns only; `timeout -s TERM -k 15 <wall>` around the command with stdin from
`rendered.stdin_path`; stdout/stderr to `/logs/harness.out` / `/logs/harness.err`; `changes.patch`,
`name_status.txt`; the anchored `workspace.tar` (or `workspace.manifest` when larger than the cap); and
`/logs/exit.json` `{exit_code, started, ended, workspace_bytes, workspace_too_large}`.

`outputs` passed to `parse_trajectory` are keyed by file **basename** under `/logs` (`harness.out`, `traj.json`).

## Status (wave 3, 2026-11-03)

| Harness | Pin | Protocol / mode | Enabled | Notes |
|---|---|---|---|---|
| opencode | 1.18.35 | openai_chat / native | yes | prompt pinned (model-agnostic capture); task subagent only in the native preset |
| mini-swe-agent | 2.4.6 | openai_chat / native | yes | `step_limit = min(max_turns, max_llm_requests)` |
| aider | 0.86.2 | openai_chat / native | yes | editor (`agentic=False`); repo-map tag cache removed after the run |
| goose | 1.57.0 | openai_chat / native | no | open question: built-in skills (web-search) cannot be switched off |
| qwen-code | 0.26.6 | openai_chat / native | no | `--bare` sends usage statistics (Aliyun RUM) and fetches models.dev (egress probe); no verified switch |
| claude-code | 2.1.316 | anthropic_messages / passthrough | no | needs WP-06 `/v1/messages` passthrough verified + real-model smoke |
| codex | 0.166.0 | openai_responses / passthrough | no | needs WP-06 `/v1/responses` passthrough verified + real-model smoke; fairness gaps (no `apply_patch` for unknown slugs, bundled skills listed) are open questions |
| gemini-cli | 0.66.0 | gemini_generate / translate | no | facts verified offline, no open questions; ran end to end through the WP-06 translator (scripted upstream); awaiting a real-model smoke run |

`GET /api/generation/harnesses/` shows the live `enabled` / `blocked_reason` / `open_questions`. The operator kill
switch `AGENT_HARNESSES_DISABLED=<name,...>` disables an adapter without a code change. A translate-only adapter
(Gemini CLI) is also blocked while its front protocol is missing from `settings.GATEWAY_TRANSLATORS`
(`registry.servable`), whatever its facts say.

No adapter's `open_questions` was closed by another WP in wave 3 (CHANGES), so the wave-2 enablement stands:
opencode, mini-swe-agent and aider. Every adapter still blocked waits for a real-model smoke run (no paid
calls were made here); qwen-code and goose also have open questions, and codex has two fairness gaps.

### Gemini CLI (wave 3)

Verified in `llmlab-v3-wt/agent-gemini-cli:dev` against the bundle and its bundled docs, with scripted smoke
runs (`final`, turn cap, compression, capture variants, workspace guard, context file, egress, loopback kill)
and one offline end-to-end run through the real WP-06 gateway image in translate mode
(`smoke/run_gateway_smoke.sh`, fixture `gemini-cli-0.66.0-gateway/`). The spec's open questions are closed:

- **Stdin prompt:** piped stdin is the prompt without `-p` (headless when stdin is not a TTY).
- **Non-Gemini slugs:** vendor-prefixed slugs (all OpenRouter ids) get the generic `chat-base` config
  (temperature 1, topP 0.95, topK 64, `includeThoughts`) and the same tool set and prompt. Bare CLI model names
  differ: `gemini-3-pro-preview` is rewritten to `gemini-3.1-pro-preview` (thinking level HIGH); `gemini-2.5-*`
  gets a legacy prompt variant and `write_todos`.
- **`GEMINI_SANDBOX=false`** keeps yolo out of its Docker sandbox. **`--skip-trust` is required**: without it
  the CLI exits 55 (untrusted workspace).
- **Prompt capture:** `GEMINI_WRITE_SYSTEM_MD` writes exactly the text sent as `systemInstruction`; the
  capture (`captured/gemini-cli-0.66.0.md`, plus a final newline) is the same for vendor-prefixed and bare
  Gemini-3 names. The pin applies to every model.

Deviations from the spec text (please amend 5.3, lead):

- **Settings split.** The CLI reads a *system* settings file only from a root-owned directory, and the sandbox
  writes rendered files as the sandbox user. The run-independent switches are therefore baked into the image
  (`images/agent-gemini-cli/system-settings.json` -> `/etc/gemini-cli/settings.json`, equal to
  `gemini_cli.SYSTEM_SETTINGS`, test-pinned); the per-run values are user settings (`~/.gemini/settings.json`).
- **More switches than the spec lists:** plan mode off (its exit needs an interactive approval), subagents
  off (`experimental.enableAgents`; the `subagents` preset knob is reported unsupported), skills/hooks/MCP/
  extensions off, `general.maxAttempts=1` (retries belong to the gateway), `tools.disableLLMCorrection`,
  `advanced.ignoreLocalEnv`; `activate_skill` and `invoke_agent` (declared but inert) are excluded.
- **Auxiliary models:** 22 internal model aliases (compression, summarizers, edit fixers, classifier, ...)
  are pinned to the run's model with `modelConfigs.overrides`; the run model's override carries
  `maxOutputTokens` (seen in the request).
- **Compaction never fires headless** (the CLI splits history only at user messages that are not tool
  results; a single-prompt run has none after the brief): verified with a 1,000-token cap over five calls.
  `context.compaction` and `context.max_context_tokens` are unsupported knobs; the gateway's input cap
  applies. The CLI also assumes a 1,048,576-token window for every model, so the rendered threshold is the
  native 50 % rule scaled to the real window.
- **`GEMINI.md` cannot be switched off** (`context.fileName` only adds names; a workspace `GEMINI.md` lands
  in the first user message). The command refuses to start (exit 52 + stderr message) when the workspace has
  `.gemini/settings.json`, `.gemini/.env` or any `GEMINI.md` (rule 2; starters have none).
- **Exit codes:** 53 = turn cap (`FatalTurnLimitedError`, mapped to `max_turns`); 41/42/44/52/54/55 are
  fatal auth/input/sandbox/config/tool/trust errors (harness errors).
- **Loopback forwarder:** `pkill llmlab-loopback` and `pkill -f node` both leave the port open again within
  0.6 s (restart loop).
- `tools.exclude` is deprecated in 0.66.0 (Policy Engine replaces it in 1.0); re-check when upgrading.

### Egress probe (all harnesses, 2026-11-03)

`run_smoke.sh` with `<render-dir>/env.extra` = `HTTPS_PROXY`/`HTTP_PROXY` pointing at the scripted upstream
(which logs every `CONNECT` as protocol `connect`), `NO_PROXY=127.0.0.1,localhost,llm-gateway`. Model calls
only: mini-swe-agent, aider, claude-code, codex, gemini-cli, and opencode since the pre-deploy follow-up
(it used to open one `CONNECT registry.npmjs.org:443` ~1 s before the first call, despite `autoupdate: false`
and the `OPENCODE_DISABLE_*` switches: its config loader npm-installs `@opencode-ai/plugin@<version>` into every
config directory that lacks `node_modules` plus a covering `package-lock.json`. The image now pre-bakes that
directory at `/opt/opencode/config` and the command sets up `$XDG_CONFIG_HOME/opencode` from it before
`exec opencode`; probe re-run: 0 CONNECTs, and the same image without the setup line still shows the CONNECT).
Extra hosts: **goose** `models.dev`, **qwen-code** `models.dev` + `*.rum.aliyuncs.com` (usage statistics, twice). Positive
control: Gemini CLI without its system settings contacts `play.googleapis.com` (usage statistics), so the
probe sees proxy-respecting traffic; a harness that ignores proxy variables would not show up here (the
`--internal` network still blocks it).

### Findings from the scripted smoke runs (deviations from the spec text)

- **Claude Code `--bare`** sends Anthropic's minimal SDK prompt ("You are a Claude agent, built on Anthropic's
  Claude Agent SDK." + CWD/date) and only the `Bash`, `Edit`, `Read` tools, and reads only `ANTHROPIC_API_KEY`
  (not `ANTHROPIC_AUTH_TOKEN`). Without `--bare` (`--safe-mode --setting-sources ""`) the prompt is the 3.8k-char
  Claude Code text and 17 tools (incl. `Write`, `Workflow`, `Cron*`, `ScheduleWakeup`). The adapter keeps the
  spec's `--bare` bundle; this is recorded here and in CHANGES for the study designers.
- **Claude Code** does not send `x-claude-code-request-class` on `main` calls (the binary knows `main`,
  `subagent`, `workflow`, `compaction`, `auxiliary`, `side_reply`); a missing header must count as `main`.
- **Qwen Code `--bare`** ignores `~/.qwen/settings.json` (requests carried `max_tokens: 64000`). Without `--bare` the
  pinned prompt is extended (32.7k chars), skills/tool-search tools appear and an extra auxiliary call is made.
  The adapter uses `--bare` and CLI flags only; output cap, context, compaction and caching are unsupported knobs
  (the gateway forces `max_tokens`). Exit 53 = session turn cap (verified); 52 = invalid configuration.
- **Goose v1.57.0** has no `GOOSE_CONTEXT_STRATEGY` (env scan); compaction is `GOOSE_AUTO_COMPACT_THRESHOLD`
  (0 disables) with `GOOSE_CONTEXT_LIMIT`. Its prompt lists built-in skills (`web-search`, `goose-doc-guide`) and
  offers `load_skill` even with `--no-profile`. The user message carries a `<turn-context>` block with the time.
- **Codex 0.166.0** with an unknown slug uses fallback model metadata: no `apply_patch` tool although the
  instructions name it (`include_apply_patch_tool` is not a config key; `--strict-config` refuses it). Features
  `multi_agent`, `goals`, `image_generation`, `apps`, `plugins`, browser/computer use, hooks and client retries
  are switched off (`[features]`); bundled system skills are still listed in a developer message. Codex strips
  trailing whitespace from `model_instructions_file`. Both fairness gaps are `open_questions` (the registry keeps Codex blocked
  until tools can be forced). No default exec timeout exists in 0.166.0 (only the model's per-call `timeout_ms`),
  so `time.command_timeout_s` is an unsupported knob, as is `reasoning` in `max_tokens` mode (only effort maps).
- **OpenCode** gets a constant `--title llm-lab-run` (no run id), so `command_sha` is equal for every run of a cell.

## Images

`images/agent-base` (Node 24 + Python 3.13 + git + ripgrep, plus the phase-B toolchains Go 1.25 with
`GOTOOLCHAIN=local` and Temurin JDK 21 + Maven 3.9, copied from the digests in
`data/starters/base_images.yaml`; user `node`, `HOME=/run/home`; about 1.4 GB) and one layer per harness
(`agent-<name>`, `FROM ${BASE_IMAGE}`). Local verification builds are tagged `llmlab-v3-wt/<image>:dev` (one
build per image; reported versions and image ids in `tests/wp07_harnesses/fixtures/images.json`, checked by
`test_images.py`). Goose's tarball is sha256-verified in its Dockerfile; mini-swe-agent and aider install from
their pinned `requirements.lock`. The Gemini CLI image also carries the run-independent system settings
(`/etc/gemini-cli/settings.json`, root-owned).

## Smoke runs without a real model

`tests/wp07_harnesses/smoke/` runs an adapter against a scripted upstream (`scripted_upstream.py`, stdlib) that
answers as `llm-gateway:8080` on an `--internal` docker network, in all four front protocols:

```sh
./.wt-test.sh wp07 -- python backend/generation/tests/wp07_harnesses/smoke/render.py goose /app/.wt/wp07-smoke/goose
sh backend/generation/tests/wp07_harnesses/smoke/run_smoke.sh .wt/wp07-smoke/goose
python3 backend/generation/tests/wp07_harnesses/smoke/to_calls.py .wt/wp07-smoke/goose/logs/upstream.jsonl calls.jsonl
```

`render.py --capture` renders with the harness's own default prompt (and `QWEN_WRITE_SYSTEM_MD` /
`GEMINI_WRITE_SYSTEM_MD` where available, even when a capture exists) to capture a new version's native
prompt or another model's variant. `render.py --policy dotted.key=<json>` and `--model-window N` render probe
runs; `SMOKE_UPSTREAM_ARGS="--steps N"` makes the scripted upstream issue N shell calls. Offline end to end
through the real gateway image (translate or passthrough fronts):

```sh
./.wt-test.sh wp07 -- python backend/generation/tests/wp07_harnesses/smoke/gateway_config.py gemini-cli /app/.wt/gw.json
sh backend/generation/tests/wp07_harnesses/smoke/run_gateway_smoke.sh .wt/wp07-smoke/gemini-cli .wt/gw.json
```

`to_gateway_session.py gemini-cli` writes the translate-mode session fixture
(`fixtures/gateway-translate/gemini-cli.json`: native requests, the canonical chat SSE upstream answers and the
Gemini SSE the CLI accepted as `native_reference`). It lives outside `fixtures/gateway/`, which WP-06's
passthrough test globs. `fixtures/images.json` records the local builds (reported version, image id per image). The real-model smoke run
(three model families through the WP-06 gateway) is an operator step logged in the PR; it is what closes the
remaining "real-model smoke" open questions.

## Upgrading a harness

1. Bump the setting (`AGENT_<NAME>_VERSION`) and the Dockerfile default; build the image.
2. Re-verify every `HarnessFacts` field against the new release (`--help`, env scan, `--strict-config` for
   Codex) and bump `facts_version`.
3. Capture the native prompt (`render.py --capture`) into `captured/<name>-<version>.md`; record a smoke fixture.
4. Add the version to the adapter's `versions`; seed new preset versions (`data/harness_presets`).

## Decisions

- Turn cap = the stricter of `preset.max_turns` and `run.max_turns`; command timeout = `time.command_timeout_s`
  when set (it overrides the preset, 4 knob table), else `preset.command_timeout_s`.
- `observed_inputs` stays pure: the runner stores `common.native_system_prompt_text(calls)` with `blobs.put_text`.
- `HarnessAdapter.wants_enabled` + `blocked_reason`: an adapter is enabled only when it asks to be, its facts have
  no open questions and the operator has not switched it off. Real-model smoke runs are required before
  `wants_enabled=True` for the new harnesses.
- The goose pin is written `1.57.0` (the release tag `v1.57.0` is built from it).
- Translated gateway records log the canonical chat body as `request`, so `trajectory.common` reads a
  `protocol_mode=translate` record by the body's shape, not by its front protocol.
- Captured prompts and smoke fixtures must stay byte-exact: the pre-commit `trailing-whitespace` hook stripped
  one line of `captured/codex-0.166.0.md` at the wave-2 commit (restored in wave 3); the lead is asked to
  exclude `harnesses/captured/` and `tests/wp07_harnesses/fixtures/` from it (CHANGES).
