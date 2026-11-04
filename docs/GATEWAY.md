# LLM gateway

Every agentic run talks to its model through a per-run **LLM gateway** sidecar
(`backend/generation/images/llm-gateway`). The agent container sits on an internal network whose only other
member is the gateway, so the gateway sees every LLM call and every byte of egress. It is the single place
where the run's model, sampling, reasoning, routing, caching and **budget** are enforced, and where the
authoritative per-call record is written. It is also the agent's only egress: HTTPS `CONNECT` to the package
registries (`network_policy=packages`) and plain-HTTP forward proxying to the dependency caches. Spec: `docs/design/SAMPLE_GEN_V3.md` sections 4.2, 4.5, 5.4 and 6.6.

The image is stdlib-only Python. Nothing in it is a third-party dependency, and LiteLLM is never in the
request path.

## Layout

| File | Role |
|---|---|
| `llm-gateway/gateway.py` | HTTP server, routing, upstream retries, streaming relay with keepalives, rate limit, logs, CONNECT tunnel |
| `llm-gateway/protocols/budget_rules.py` | Budget rules: pre-call reservation, request classes, caps, cost bases, `state.json` payload. **Shared with the executor's local proxy** |
| `llm-gateway/protocols/common.py` | `Policy` (the parsed `GatewayConfig`), hashing, SSE parsing, `cache_control` helpers |
| `llm-gateway/protocols/openai_chat.py` | OpenAI chat completions: the canonical format and the `native` front |
| `llm-gateway/protocols/anthropic_messages.py` | Anthropic Messages (Claude Code): `passthrough` to OpenRouter `/messages`, or `translate` |
| `llm-gateway/protocols/openai_responses.py` | OpenAI Responses (Codex): `passthrough` to OpenRouter `/responses`, or `translate` |
| `llm-gateway/protocols/gemini_generate.py` | Gemini `generateContent` (Gemini CLI): `translate` only |
| `llm-gateway/protocols/translation.py` | What the translators share: `translation_dropped` counts, reasoning tokens, chat stream events, turn aggregation |
| `llm-gateway-local/local.py` | Overlay image for local endpoints: registers the `local_proxy` upstream (the executor's LLM proxy) |

## Configuration

The sandbox starts the gateway with:

| Variable | Meaning |
|---|---|
| `GATEWAY_CONFIG` | The `GatewayConfig` JSON (`backend/generation/contracts/resources.py`, `config_version 1`), built by `budget/gateway_config.py:build(run)`. The sandbox may add three runtime extras next to the contract keys: `listen: {port}`, `connect_allowlist: [...]` and `proxy_hosts: [...]` |
| `OPENROUTER_API_KEY` | The run owner's key (OpenRouter upstream). The agent only ever has a dummy key |
| `LLMLAB_UPSTREAM_TOKEN` | The per-run proxy token (local upstream, `gateway_wiring.proxy_token`) |
| `GATEWAY_LOG_DIR` | Where the logs go (default `/logs`) |
| `GATEWAY_PORT` | Listen port (default `listen.port`, else 8080) |
| `GATEWAY_CONNECT_ALLOWLIST` | Comma list of registry hosts reachable through `CONNECT` on port 443 (default: the config's `connect_allowlist`, else none) |
| `GATEWAY_PROXY_HOSTS` | Comma list of dependency-cache `host:port` pairs (`build.depcache.SandboxDepConfig.proxy_hosts`): plain `http://` absolute-URI `GET`/`HEAD`/`POST` requests and `CONNECT` are forwarded to exactly these (default: the config's `proxy_hosts`, else none) |
| `GATEWAY_PING_INTERVAL_S` | Keepalive interval during upstream silence (default 10; setting `LLM_GATEWAY_PING_INTERVAL_S`) |
| `GATEWAY_RATE_DIR` | Directory holding shared `rpm_per_model` token buckets (unset: one bucket per gateway) |
| `GATEWAY_MAX_BODY_BYTES` | Request body limit (default 64 MiB) |
| `GATEWAY_REFERER` / `GATEWAY_TITLE` | OpenRouter attribution headers |

A config this image cannot run (an unknown front, a front in a mode it does not have, e.g. `openai_chat`
in `translate` or `gemini_generate` in `passthrough`, passthrough to a local endpoint, `serving_tier=batch`,
an upstream kind without its overlay) makes the gateway exit with
status **78** after writing a `config_invalid` event. The sandbox reports that as an infra error; it never
proceeds without a gateway.

## Front protocols and modes

| Front | Harnesses | Modes | Upstream path |
|---|---|---|---|
| `openai_chat` | opencode, mini-swe-agent, aider, goose, qwen-code | `native` | `{base}/chat/completions` |
| `anthropic_messages` | Claude Code | `passthrough` (OpenRouter only), `translate` (any upstream) | `{base}/messages`; translate: `{base}/chat/completions` |
| `openai_responses` | Codex | `passthrough` (OpenRouter only), `translate` (any upstream) | `{base}/responses`; translate: `{base}/chat/completions` |
| `gemini_generate` | Gemini CLI | `translate` | `{base}/chat/completions` |

A gateway serves only its run's front protocol; any other path is a native 404 and a `rejected_path` event.

- **openai_chat** serves `POST */chat/completions` and `GET */models`.
- **anthropic_messages** serves `POST /v1/messages` (with or without `?beta=true`), answers
  `POST /v1/messages/count_tokens` locally (`ceil(chars / 3.6)`, flagged `"estimate": true`, never a billable
  call), rejects `HEAD /api/hello` (404) and serves an Anthropic-shaped `GET /v1/models`. The
  `anthropic-version` and `anthropic-beta` headers are forwarded; the Claude Code attribution headers
  (`x-claude-code-request-class`, `-session-id`, `-agent-id`, `-prompt-id`, `-compaction`) are logged.
- **openai_responses** serves `POST */responses`. The gateway is stateless: `previous_response_id` is refused
  with a native 400.
- **gemini_generate** serves `POST /v1beta/models/{m}:generateContent` and `:streamGenerateContent`
  (`?alt=sse`: `data: {...}\r\n\r\n` events; without it a streamed JSON array), answers `:countTokens`
  locally (`{"totalTokens", "estimate": true}`) and serves `GET /v1beta/models`. The model in the URL
  (everything between `/models/` and the method, so a vendor-prefixed slug such as
  `deepseek/deepseek-v4.1-flash` keeps its prefix) is recorded as `requested_model`; the pin applies as
  everywhere.
- Requests that contain images or files are refused with 400 `unsupported_content`, on every front.

**Passthrough** forwards the harness's native body after applying the policy on that body. Everything that
is not a policy field reaches OpenRouter unchanged: thinking blocks with their signatures, Responses
`encrypted_content` reasoning items, tool definitions, metadata, key order. Usage is read from the native
response (`usage.cost` when OpenRouter returns it).

**Translate** (wave 3) maps the native body to canonical chat, applies the **chat** policy to it (the same
code path as a native chat harness), forwards it to `{base}/chat/completions` and maps the answer back.
It works with the OpenRouter upstream and with the local overlay (`local_proxy`), so Claude Code and Codex
can run on local endpoints and Gemini CLI anywhere. Because the upstream exchange is canonical chat, the
prompt estimate, the reservation, the ledger, request classes, retries and the call record (`request`,
`usage`, `finish_reason`, `served_provider`, `response_id`, ...) are exactly those of a native chat call;
the same conversation exhausts the budget at the same call on every front (`test_translate_http.py`). Only
the bytes the harness sees are native: answers, SSE events, keepalives, errors and budget refusals.

Each translated front module exposes:

| Function | Contract |
|---|---|
| `to_chat(native_body, **options) -> (chat_body, dropped)` | `options` from `request_options(path, query)` (Gemini: `stream`, `sse`, `model`; none for the others). Raises `ValueError` (`translation.TranslationError`) for a request it cannot express (refused natively, 400 `unsupported_request`) |
| `from_chat(chat_completion, native_body=None, *, dropped=None, **options) -> native` | An error payload, or a failed turn (`finish_reason=error`, or an error object on the choice: `translation.completion_failure`), becomes the native error: Anthropic `{"type": "error", ...}`, Gemini `{"error": {code, message, status}}`, Responses a `failed` Response object with the partial output and `error: {code, message}` (as the stream's `response.failed`; an error-only payload keeps the OpenAI error shape). The gateway sends a failed turn with HTTP 502, never a 200 that looks like an answer; the call record stays the canonical one (upstream status, `finish_reason: "error"`) |
| `StreamTranslator(native_body=None, *, dropped=None, **options)` | `.content_type`, `.feed(chat_sse_bytes) -> native_bytes` (whole native events only), `.finish(failure=None) -> closing bytes` (a stream that ended without a finish, or with an error, ends with the native error event), `.fail(status, native_error) -> bytes` |
| `stream_from_chat(chat_sse_iter, native_body=None, **options) -> iterator` | Generator form; a `None` item means "upstream silent for one ping interval" and yields the native keepalive |
| `upstream_error(status, message, code) -> (status, native)` | Upstream chat errors keep their status; a context overflow is worded the native way (Anthropic "prompt is too long: ...", OpenAI `context_length_exceeded`, Gemini "input token count ... exceeds") so the harness compacts |

What maps where:

| | Anthropic Messages | OpenAI Responses | Gemini generateContent |
|---|---|---|---|
| System | `system` (string or blocks; blocks with markers stay text parts) | `instructions`, `system`/`developer` items | `systemInstruction` |
| Tool calls / results | `tool_use` / `tool_result` (tool results go before the user text of the same message) | `function_call` / `function_call_output`; custom (freeform) tools are function tools with one string parameter `input`, and back to `custom_tool_call` | `functionCall` / `functionResponse` (ids kept; responses without an id are matched to the last model turn's calls by name, in order) |
| Tools, choice | `input_schema`; `auto`/`any`/`tool`/`none`, `disable_parallel_tool_use` | `function`, `custom`; `auto`/`none`/`required`/named | `functionDeclarations` (`parameters` types lowered; `parametersJsonSchema` as is); `AUTO`/`ANY`/`NONE` |
| Reasoning request | `thinking {enabled, budget_tokens}` / `adaptive`, `output_config.effort`, `disabled` | `reasoning {effort | max_tokens}` | `thinkingConfig {thinkingBudget, thinkingLevel, includeThoughts}` |
| Other | `max_tokens`, `stop_sequences`, sampling | `max_output_tokens`, `text.format` -> `response_format`, `text.verbosity`, sampling | `generationConfig` (sampling, `maxOutputTokens`, `stopSequences`, `seed`, penalties, JSON mime/schema) |
| Answer | content blocks, `stop_reason`, `usage` with cache reads/writes split out | Responses object / event stream (`response.created` ... `response.completed`/`incomplete`/`failed`), usage with cached/reasoning details | `candidates[0]` parts (thought text only with `includeThoughts`), `finishReason`, `usageMetadata` (`candidatesTokenCount` excludes thoughts) |

**Reasoning continuity.** The upstream's `reasoning_details` (thinking text, summaries, signatures,
encrypted blobs) must come back unchanged in the next request. Each front carries them in a field its
harness replays verbatim, as an `llmlab1.<base64url(JSON)>` token: Anthropic `thinking.signature` (one block
per detail; encrypted details as `redacted_thinking.data`), Responses `reasoning.encrypted_content`, Gemini
`thoughtSignature` on the first `functionCall` part (else the first answer text part, else an empty text
part; Gemini CLI drops `thought` parts from its history but keeps signatures). A native signature the
gateway did not mint is forwarded as a detail of the front's format (`anthropic-claude-v1`,
`openai-responses-v1`, `google-gemini-v1`). Gemini CLI's placeholder `skip_thought_signature_validator`
(and Google's other "skip validation" value `context_engineering_is_the_way_to_go`) carries no reasoning:
it is forwarded only when the requested model is a Google one (`google/...` or a bare `gemini-...`, where it
means what the client asked for) and otherwise dropped and counted as `thoughtSignature.synthetic`.
A plain `reasoning` string (local models) is replayed as `reasoning` text. The local overlay turns
`reasoning_details` into `reasoning` text (signatures mean nothing to a local endpoint).

**`translation_dropped`** counts, per call and by field, what the native request had that canonical chat
cannot carry: unknown request keys (`request.<key>`), hosted/server tools (`tools.<type>`), non-text content
kinds, `tool_result.is_error`, custom-tool grammars (`tools.custom.format`), unmatched Gemini function
responses, Gemini `safetySettings` / `candidateCount` / unsupported `generationConfig` keys, invalid tool-call
arguments in answers (`tool_call.invalid_arguments`), and `cache_control` markers on tool definitions or
tool calls (not counted when `prompt_cache` is `off`/`auto`, since the policy strips every marker anyway).
Keys that never change what the model sees (`stream`, `store`, `include`, `metadata`, `prompt_cache_key`,
`user`, ...) are ignored. Any non-zero count gives the run the validity note `translation_lossy`. The
recorded sessions of the pinned Claude Code, Codex and Gemini CLI binaries translate with nothing dropped.

`settings.GATEWAY_TRANSLATORS` (WP-06 settings block; not env-driven) lists the fronts whose translate mode
is enabled: `anthropic_messages`, `openai_responses`, `gemini_generate` (all must be in the image's
`protocols.common.TRANSLATORS`). `harnesses.registry` and `study.environment` read it to pick
`adapter.protocol_mode(endpoint_kind=..., translators=...)`.

## Policy

Applied on every call, after routing to the front module:

| Knob | Chat (native) | Anthropic (passthrough) | Responses (passthrough) |
|---|---|---|---|
| Model pin | `model` = `model.upstream_model` (`:floor` for `serving_tier=flex`) | same | same |
| Sampling | strip `sampling.stripped`, then force `sampling.forced` | `temperature`, `top_p`, `top_k` only | `temperature`, `top_p` only |
| Seed | `seed` = `sampling.seed` when `forward_seed` | not applicable (noted in `policy_diff`); in translate mode the chat row applies | not applicable (noted); in translate mode the chat row applies |
| Output cap | `max_tokens = call.max_output_tokens + reasoning.max_tokens` (budget mode), clamped to `model.max_output`; harness `max_completion_tokens` stripped | `max_tokens` (same value) | `max_output_tokens` (same value) |
| Reasoning | OpenRouter `reasoning {effort | max_tokens, exclude}` | `thinking {enabled, budget_tokens}` for a token budget; `output_config.effort` for an effort (minimal→low, xhigh→high; `none` disables thinking). `exclude` is ignored: signatures must replay | `reasoning {effort | max_tokens}` (the harness's `summary` kept); `store: false`; `include` gets `reasoning.encrypted_content` |
| Routing | `provider` = `routing.provider` (harness routing fields stripped) | same | same |
| Session | `session_id` = run uuid when `caching.sticky_session` | same | same |
| Cache markers | `off`/`auto`: strip every `cache_control`. `on`: keep the harness's markers, set their TTL from `caching.ttl`, and for listed providers inject markers on the system message and the last tool result (at most 4 in all) | same; injection on the last system block and the last `tool_result` | strip under `off`/`auto` |
| Usage | `usage: {include: true}`; `stream_options.include_usage` for streams | — | — |

"Listed providers" means the run's model vendor (`anthropic/...`) or a pinned/ordered provider is in
`caching.cache_control_providers`. With `provider_default` reasoning the harness's own setting is kept and
recorded (`requested_reasoning`).

For the three ported harnesses the chat policy keeps the v2 gateway's key order, so their upstream request
bytes are identical to v2's apart from new fields (`session_id`); `tests/wp06_gateway/test_v2_replay.py`
pins this with recorded sessions.

## Budget

`protocols/budget_rules.py` holds one `Ledger` per run attempt:

- **Request classes.** The class of a call comes from `request_class_source` (a header name, e.g.
  `x-claude-code-request-class`, or `default:main`). A missing header is `main`; an unknown value is
  `auxiliary`. `run.max_llm_requests` counts successful `main` calls; `run.max_aux_requests` counts
  `subagent`, `compaction` and `auxiliary` calls. In-flight calls count; failed calls count only with
  `count_failed_calls`.
- **Pre-call reservation.** Before a call is forwarded, its worst case is reserved: the prompt estimate
  (`ceil(chars / 3.6)`, scaled by the largest actual/estimate ratio seen in the run, at most 3×) plus the full
  upstream `max_tokens`, priced at the manifest's list prices, with every prompt token at the dearer of
  `input_per_mtok` and `cache_write_per_mtok` (Anthropic cache writes cost 1.25×/2× input, and
  `prompt_cache=on` makes the gateway add the markers itself). The estimate walks every string leaf of
  `system`/`instructions`/`messages`/`input`/`contents` and counts tool-call arguments and
  `tool_use.input` as their JSON, so tool-heavy histories are not under-reserved. A call is refused when accrued usage plus
  every in-flight reservation plus its own worst case would pass any cap: `cost`, `tokens`
  (`run.max_total_tokens`), `uncached_input`, `output_tokens`. **It is refused, never clamped.** Concurrent
  in-flight calls therefore cannot overshoot the cap.
- **Exhaustion is sticky.** The first refusal sets `budget_exhausted:<kind>` (`requests`, `aux_requests`,
  `attempts`, `cost`, `tokens`, `uncached_input`, `output_tokens`); every later call is refused too. The
  `attempts` kind is a loop guard: at most `2 × (max_llm_requests + max_aux_requests) + 20` calls start.
- **The refusal** is the front's native error shape, status 400, with the text
  "Run budget exhausted. Stop working and summarise." It reads nothing like a context-overflow message, so no
  harness compacts and retries.
- **`call.max_input_tokens`** refuses one over-long prompt with 400 in the provider's own context-length
  wording (so the harness compacts) and does **not** exhaust the run.
- **Cost bases.** `billed` accrues `usage.cost` (list price when the response has none, `cost_source:
  price_table`, and `usage.cost` stays `null` for the `/generation` backfill); `list_price` accrues list
  cost; `free` never trips the cost cap. `list_cost` always accrues.
- **No silent zero.** A successful call without usage is settled at its reservation (`usage_estimated`,
  `cost_source: reservation`). So is a failed call the upstream had already accepted (it answered 2xx/3xx):
  a stream that broke mid-way, a body that could not be read, a gateway error after forwarding. It still
  counts as a failed call. Only a call the upstream refused (4xx/5xx without usage) or never answered costs 0.
- **Truncation.** Calls ending `finish_reason=length` (Anthropic `max_tokens`, Responses `incomplete`), and
  empty answers that used the whole cap, are counted in `call_truncations`.

`concurrency.rpm_per_model` is a token bucket (burst of five seconds' worth). The call is reserved first
(so it counts as in flight and the idle timer pauses), then waits for a token for at most
`time.llm_call_timeout_s`.

### Local proxy parity

`backend/inference/proxy_server.py` loads the same `budget_rules.py` file by path and keeps a ledger per
attempt (`ProxyBudgets`), configured from `budget/gateway_config.build(run)` (or the manifest's effective
policy). The `llm-gateway-local` overlay sends `X-LLMLab-Request-Class` and `X-LLMLab-Call-Index` with every
call; a gateway retry or a 504 re-attach of the same call reuses its reservation. The gateway stays
authoritative; the proxy is the backstop that refuses at the same point (`test_proxy_parity.py`).

Each completed proxied request (`LLMRequest` DONE with a non-error answer) is also booked in the spend
ledger through `budget.live.record_proxy_usage` (`record_ledger_usage`; best effort, deduped as
`proxy:{id}` so a re-attached request is booked once): local runs count against token caps and SpendCaps
live, at 0 billed USD for `free`/`list_price` endpoints.

## Logs

Under `GATEWAY_LOG_DIR`:

- **`calls.jsonl`**: one record per forwarded call (refused calls are not records). Keys include `index`,
  `started`, `ended`, `duration_s`, `ttft_ms`, `tokens_per_s`, `path`, `stream`, `request_class`,
  `request_class_header`, `attribution`, `front_protocol`, `protocol_mode`, `requested_model`,
  `requested_reasoning`, `upstream_model`, `response_model`, `response_id` (the generation id for backfill),
  `served_provider`, `system_fingerprint`, `serving_tier`, `status`, `transport_error`, `client_gone`,
  `error`, `finish_reason`, `native_finish_reason`, `truncated`, `usage` (normalized: `prompt_tokens`
  including cache reads and writes, `completion_tokens`, `reasoning_tokens`, `cached_tokens`,
  `cache_write_tokens`, `cost`; `null` = not reported), `usage_estimated`, `cost`, `cost_source`,
  `list_cost`, `reserved_usd`, `est_prompt_tokens`, `reserved_prompt_tokens`, `upstream_max_tokens`,
  `upstream_retries`, `rate_wait_s`, `translation_dropped`, `policy_diff`, `native_request_sha`
  (`identity.H` of the native body), `native_system_prompt` and `native_system_prompt_sha` (first `main`
  call only), `request` (the body sent upstream) and `response` (a summary with the raw native usage). The
  v2 aliases `seq` and `ts` are kept for the telemetry readers.
- **`gateway_events.jsonl`**: `started`, `refused`, `budget_exhausted` (once), `context_budget`,
  `upstream_retry`, `rate_limit_timeout`, `rejected_path`, `rejected_connect`, `connect`, `proxy`,
  `rejected_proxy`, `config_invalid`, `internal_error`.
- **`state.json`**: rewritten atomically (temp file, fsync, rename) after every call start and end, with
  exactly the keys `requests_by_class`, `prompt_tokens`, `completion_tokens`, `reasoning_tokens`,
  `cached_tokens`, `cache_write_tokens`, `cost`, `list_cost`, `inflight`, `last_call_end`,
  `budget_exhausted`, `call_truncations`, `updated`. The sandbox supervisor polls it for live spend and idle
  detection.
- **`blobs/<sha>.txt`**: the native system prompt of the first `main` call (sha = `identity.text_sha`).

`GET /stats` returns the state plus internal counters (failed calls per class, refusals, calibration,
in-flight reservations); `GET /healthz` returns `{"ok": true}`.

## Streaming

Upstream SSE bytes are relayed unchanged (translate: re-encoded event by event; tool calls are emitted
whole when the turn ends, with validated JSON arguments). While the upstream is silent the gateway writes
the front's keepalive, only between events: `: ping` (chat), `event: ping` (Anthropic), `: keepalive`
(Responses), `\r\n` (Gemini; whitespace is valid in both its SSE and JSON-array framings). A translated
stream that breaks or ends without a finish ends with the native error event (Anthropic `error`,
Responses `response.failed`, Gemini a bare `{"error": ...}` object, which the Gemini SDK raises). Before
the first upstream byte of a streamed call (queueing, retries, a long think) the gateway commits a 200 SSE
response and pings; an upstream error after that point is sent as the front's in-stream error event.
Transient upstream failures (`retries.retry_on`) are retried with exponential backoff before any byte reaches
the harness.

## Tests

`backend/generation/tests/wp06_gateway/`: budget rules (including the concurrent no-overshoot invariant),
policy per front, v2 replay byte equality, passthrough sessions for Claude Code and Codex (recorded-shape
fixtures, plus WP-07 smoke fixtures from `tests/wp07_harnesses/fixtures/gateway/*.json` when present), HTTP
behaviour (error shapes, pings, retries, records, `state.json` atomicity, rate limit, startup refusals), the
local overlay as a subprocess, and local proxy parity. Wave 3: `test_translators.py` (pure translators:
reasoning continuity for every front x model family x JSON/stream, tool calls, stop reasons, usage, errors,
dropped counts, the recorded native requests) and `test_translate_http.py` (translate mode through the
gateway with the scripted upstream: canonical upstream body and record, identical ledger across fronts,
native refusals, errors, broken streams, keepalives, request classes, local routes, recorded sessions).

Fixtures: `fixtures/translate/chat_answers.json` holds chat answers in the shapes OpenRouter returns per
model family (Anthropic, OpenAI, open weights, Google) and the executor proxy's shape for local models,
JSON and SSE; values are synthetic (no paid call). `fixtures/translate/gemini_cli_session.json` holds the
real native requests of the pinned Gemini CLI 0.66.0, recorded against the WP-07 scripted upstream.

Smoke (not part of pytest): the pinned Claude Code 2.1.316, Codex 0.166.0 and Gemini CLI 0.66.0 images each
completed the WP-07 smoke task (write `hello.txt`) through `llmlab-v3-wt/llm-gateway:dev` in translate mode
against the scripted chat upstream on an `--internal` network: 2 calls each, all `main`, nothing dropped.

```bash
./.wt-test.sh wp06 -- pytest -q -p no:cacheprovider backend/generation/tests/wp06_gateway
```

### Session fixture format

```json
{"harness": "claude-code", "front_protocol": "anthropic_messages", "request_class_source": "x-claude-code-request-class",
 "calls": [{"path": "/v1/messages?beta=true", "headers": {...}, "body": {...native request...},
            "upstream": {"status": 200, "content_type": "text/event-stream", "events": ["event: ...\ndata: ...\n\n", ...]},
            "expect": {"finish_reason": "tool_calls", "prompt_tokens": 2112, "completion_tokens": 87,
                       "request_class": "main", "cost": 0.0091}}]}
```

`upstream` may carry a JSON `body` instead of `events`. `expect.cost` is the upstream's `usage.cost` (or
`null` when it reported none).

## Decisions

- **Passthrough reasoning uses native fields.** On `/messages` the reasoning knob becomes `thinking` /
  `output_config.effort`; on `/responses`, `reasoning`. OpenRouter's acceptance of `provider` and the
  presence of `usage.cost` in native responses are open (spec appendix B); when `usage.cost` is missing the
  call is accounted at list price and the record keeps `usage.cost: null` for the backfill.
- **Seeds are not sent on native passthrough bodies** (neither API has one); the policy diff records
  `seed_not_applicable`, and `budget.gateway_config.build` sets `forward_seed=false` for them.
  **Translated** Anthropic/Responses calls are canonical chat and carry the seed (lead decision, wave 3):
  `gateway_config.seed_carried(front, mode)` is false only for those two fronts outside `translate`, and
  `forward_seed = manifest.sampling.seed_forwarded and seed_carried(...)`, so a translated run whose model
  accepts a seed stays `paired_crn`. The manifest's `seed_forwarded` must apply the same rule
  (`study/plan.py`, WP-10).
- **`call.max_input_tokens` refusals are worded as context overflow** (on purpose: they exist to make the
  harness compact); budget refusals never are.
- **`caching.prompt_cache=auto` strips explicit markers** (provider-implicit caching only).
- **The reservation's prompt estimate is calibrated** by the largest actual/estimate ratio seen in the run
  (capped at 3×), which bounds the overshoot that a low character estimate would otherwise allow.
- **An `attempts` exhaustion kind** caps calls started at `2 × (main + aux caps) + 20` (the v2 loop guard).
- **The local overlay never retries 500 or 408 and always retries 504** (the proxy's "still waiting").
