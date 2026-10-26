# Local models: endpoints, the relay and direct servers

LLM Lab runs experiments on OpenRouter models and on models you host
yourself: Ollama or any OpenAI-compatible server (vLLM, llama.cpp server,
LM Studio). Every place models run is an **endpoint** (`/endpoints` in the
UI, `/api/inference/endpoints/` in the API). The OpenRouter endpoint is
built in; you add your own.

This page covers setup, the settings that matter for fair experiments, how
requests flow, the security model and troubleshooting. Execution internals
(the executor, slots, retries) are in `docs/EXECUTION.md`.

## 1. Two ways to connect

| | **Relay** (recommended) | **Direct** |
|---|---|---|
| Who can create it | any user | staff only |
| Network | the GPU box dials out over HTTPS; works behind NAT and firewalls | the executor container calls `base_url`; the host must route to it (e.g. Tailscale on the host) |
| What runs on the GPU box | `relay/llmlab_relay.py` next to the model server | only the model server |
| Credentials | an endpoint-scoped relay token (rotatable, revocable) | optional API key, stored encrypted |

Both feed the same durable request queue, so caps, priorities and
cancellation behave identically.

## 2. Add a relay endpoint

1. **Endpoints → Add endpoint**: kind (Ollama / OpenAI-compatible), name,
   slug (immutable: it is part of every model id, `local-<slug>/<model>`),
   slots (`max_concurrency`), max resident models (K), request timeout,
   `num_ctx` cap and keep-alive.
2. The token is shown **once**. Store it in a file with mode 0600; it is
   never placed in a command line.
3. Follow the **Install** tab (Python, Docker or systemd). Every command
   verifies the script's sha256 shown in the UI. See `relay/README.md`.
4. The page turns green when the relay connects and lists the discovered
   models ("12 models · 9 with tools").

### Ollama settings on the GPU box

```ini
# sudo systemctl edit ollama
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"      # keep Ollama on localhost; the relay is local
Environment="OLLAMA_NUM_PARALLEL=<slots>"      # = the endpoint's max_concurrency and the relay's --parallel
Environment="OLLAMA_MAX_LOADED_MODELS=<K>"     # = the endpoint's max resident models
Environment="OLLAMA_KEEP_ALIVE=30m"
```

- **Slots.** `max_concurrency` is enforced by the server when a request is
  claimed, for scaffolding calls, agent calls, probes and AI-review calls
  together. Match it to `OLLAMA_NUM_PARALLEL`. For strict reproducibility
  use 1: Ollama batches parallel requests, which can break seed
  determinism.
- **Residency (K).** The scheduler never starts a model that would exceed K
  resident models on the endpoint. With the default `blocked` policy each
  model's cells run back to back, so a model loads once per repeat block.
  `interleaved` maximises temporal fairness but reloads models constantly
  when K is small (the composer warns `model_thrash_risk`).
- **Warm-up (prefetch).** For Ollama, before a lane's next model starts, the
  executor sends one `/api/generate` with an empty prompt and admits the job
  once the model is loaded, so load time never counts against an agent's
  wall clock (`execution.prefetch_load_s` records it).
- **`num_ctx` and VRAM.** Ollama's default context is small; LLM Lab always
  sends `options.num_ctx = min(model context, endpoint num_ctx cap)` (or a
  per-model override). Large contexts cost VRAM: if a load fails with
  "requires more system memory" the run gets `upstream_oom`; lower the cap
  or K. The relay's `--max-num-ctx` clamps it locally as a last resort.
  The effective `num_ctx` is part of the run fingerprint.
- **API flavor.** `native` (default for Ollama) uses `/api/chat`, which
  honours `num_ctx`, `keep_alive` and every sampling option. `openai` uses
  `/v1/chat/completions` and cannot set `num_ctx`; then set
  `OLLAMA_CONTEXT_LENGTH` on the server yourself.

### One endpoint per machine

Slots and K describe one machine, so create one endpoint per GPU box. A
second relay on the same endpoint (different `relay_id`, e.g. a second box
by mistake, or a replacement) connects as **standby**: it gets no work and
takes over when the active relay disconnects or times out (120 s). To spread
30 models over several boxes, create an endpoint per box and use "Balance
across endpoints" in the composer.

## 3. Direct endpoints (staff)

A direct endpoint is called from the executor container. `base_url` is the
server root (e.g. `http://100.101.102.103:11434`). It is validated against
SSRF rules:

- http/https only, no credentials, query or fragment in the URL;
- compose service names (`postgres`, `redis`, `django`, `apps-dind`, …),
  `localhost` and metadata hosts are rejected;
- every resolved address must not be loopback, link-local, multicast,
  reserved, inside a network the executor is attached to, the Docker host
  (default gateway) or one of `INFERENCE_HOST_PUBLIC_IPS`;
- private, CGNAT and LAN addresses (Tailscale is `100.64.0.0/10`) need an
  explicit `INFERENCE_DIRECT_ALLOWED_TARGETS` entry `cidr:port`, e.g.
  `INFERENCE_DIRECT_ALLOWED_TARGETS=100.64.0.0/10:11434`;
- public addresses are allowed on any port except 22, 25, 2375-2376, 5432
  and 6379.

At call time the host is re-resolved and re-validated on every connection,
the socket connects to the vetted IP (no DNS rebinding), redirects are never
followed and TLS verifies the original hostname. The executor has no
`extra_hosts`; `host.docker.internal` is not a supported target. Pumps in
the executor refresh the model list every 120 s (or on **Refresh models**).

## 4. Models

Discovered models appear in the model picker grouped by endpoint, as
`local-<endpoint>/<model>`, free and priced 0. Each row records the exact
served name (`qwen2.5-coder:32b`), the digest of its weights, family,
parameter size and quantization, and the capabilities Ollama reports
(`tools`, `vision`, `thinking`). Models with no `tools` capability cannot
drive tool-calling harnesses (OpenCode, mini-swe-agent); Aider works with
any model. On the endpoint's Models tab you can hide models, override
`num_ctx` or the tool flag (OpenAI-compatible servers report no
capabilities; the endpoint's "assume tools" sets the default).

**Digest changes.** When a model's weights change upstream (`ollama pull`),
the old digest is kept in the model's history and every running experiment
with pending runs that froze the old digest is paused (`digest_changed`).
Resume with "accept digest drift" (pending runs are re-frozen and the
results are flagged as mixed) or clone the experiment. Nothing ever runs on
different weights than its snapshot recorded: the relay is only given
requests for `(name, digest)` pairs it currently serves.

**Fingerprints.** A local run's fingerprint includes
`llm_source = {kind, api_flavor, served name, digest, num_ctx}` and the
model id embeds the endpoint slug, so the same weights on two machines are
two conditions (no cross-endpoint pooling in v1). OpenRouter runs are
unchanged.

## 5. How a call flows

- **Scaffolding / repairs / AI review:** the executor (or the analysis
  worker) creates an `LLMRequest` row and waits; the endpoint's active relay
  claims it on its next long-poll (`POST /api/relay/v1/poll`, 25 s), runs it
  against the local server and posts the result.
- **Agent runs:** the agent talks to its per-job gateway exactly as with
  OpenRouter. For local models the gateway is the `llm-gateway-local`
  overlay, which forwards to the executor's internal LLM proxy (an internal
  Docker network; a narrow firewall exception allows only the gateway egress
  network to reach it, on one IP and port). The proxy turns each call into
  an `LLMRequest`, built from the job's frozen snapshot, and answers when the
  relay delivers. Streaming harnesses get synthesized SSE with keep-alive
  pings; non-streaming harnesses ride a 504 re-attach chain, so a long
  queue never breaks a run.
- **Timeouts** form one chain derived from the endpoint's request timeout
  (default 900 s): request deadline 3300 s, gateway socket 1200 s, proxy wait
  1140 s, harness client 6060 s. The chain guarantees the harness never gives
  up before the endpoint does.
- **Priorities.** Experiment calls first; probes −20; AI-review analysis and
  inventory −40.

### Failure handling

| Situation | What happens |
|---|---|
| Relay offline | Jobs wait (status `endpoint_offline`); the launch preview warns. |
| Relay restarts mid-request | The request returns to the queue (attempt +1) and runs again; the run records a relay interruption. |
| A poll response is lost (proxy restart) | Claims carry a 60 s ack lease; unacknowledged claims return to the queue within a minute without using an attempt. |
| Cancel | The relay is told within one poll and closes the upstream socket; Ollama stops generating. |
| Network blip (< ~2 min) | The relay retries every few seconds while requests run; its session (120 s TTL) survives and running generations finish. |
| Endpoint disabled | Relays stay connected and idle (polling every 15 s); nothing is claimed. |

## 6. Tokens and sessions

- Format `llr_<prefix>.<secret>`; only a sha256 of the secret is stored.
  The prefix identifies the token in the UI.
- **Rotate** creates a new token; the old one keeps working for the grace
  period (default 24 h, up to 7 days). Replace the token file and send the
  relay `SIGHUP` (`systemctl reload llmlab-relay`), or just restart it.
- **Revoke** takes effect immediately: its sessions end, their requests
  return to the queue and the relay aborts its running generations and exits
  with code 3 (systemd will not
  restart it; Docker with `--on-unauthorized wait` idles).
- The endpoint page lists sessions with hostname, version, client IP,
  capacity and last-seen time. A new IP or hostname for a token is worth a
  look.

### Client IP trust

Relays reach Django through Caddy and the SvelteKit proxy, which appends to
`X-Forwarded-For`. Django takes the **right-most** hop that is not in
`TRUSTED_PROXY_CIDRS` (set it to the frontend container and Caddy
addresses). Requests that bypass Caddy (the directly published frontend
port) can only spoof hops to the left of the frontend's own entry, so the
recorded IP is trustworthy only for traffic through Caddy. Production
adapter-node needs `BODY_SIZE_LIMIT` ≥ 16M for agent-sized results.

## 7. Sharing

Endpoints are private. Staff can mark one **shared**; then every user can
run models on it. Non-owners see a consent banner in the composer ("Prompts
and generated code pass through a machine run by …") and never see
operational details: base URL, key prefix, sessions, IPs, hostnames and
errors are shown only to the owner and staff (REST and live updates).

## 8. Security model

- The relay holds no secrets except its own token and runs only allowlisted
  inference paths; the script is pinned by sha256 and never self-updates.
- A stolen token lets an attacker read prompts and answer requests for that
  endpoint's runs. Mitigations: endpoint-scoped tokens, 0600 token files,
  instant revocation, session visibility.
- The internal LLM proxy accepts only per-job signed tokens that name the
  job and attempt, valid only while that attempt holds a live lease.
- Direct endpoints are staff-only and SSRF-validated on every connection.

## 9. Troubleshooting

| Symptom | Check |
|---|---|
| `--check` fails on the server | URL (https), token file contents, `--ca-file` for a private CA |
| `401` / exit code 3 | token revoked, expired after rotation, or the wrong file |
| `409 kind_mismatch` | `--kind` differs from the endpoint's kind |
| `426` | the relay is older than `RELAY_MIN_VERSION`: fetch and verify the new script |
| Endpoint stays offline | relay logs; the relay must reach `https://<server>/api/relay/v1/hello` |
| Models listed but jobs wait (`model_not_served`) | the model was removed or re-pulled with other weights; see Digest changes |
| `upstream_oom` | lower the `num_ctx` cap or max resident models |
| Runs hit their wall clock | slow hardware: raise the agent wall clock or the request timeout; watch tokens/s on the endpoint page |
| Relay polls but never gets work | the session is `standby` (another relay holds the endpoint) or the endpoint is disabled |
