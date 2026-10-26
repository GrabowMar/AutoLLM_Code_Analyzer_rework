# LLM Lab relay

`llmlab_relay.py` connects a model server on another network (a GPU box at
home, a lab workstation) to an LLM Lab server. It runs next to the model
server and only makes **outbound** HTTPS requests to
`<server>/api/relay/v1/`; nothing on the GPU box has to accept connections.

- Single file, Python ≥ 3.9 standard library only. No install step.
  Current version: 1.0.0 (`--version`).
- Ollama (`--kind ollama`, default) or any OpenAI-compatible server such as
  vLLM (`--kind openai_compatible`).
- The relay is deliberately dumb: the server builds each upstream request
  and the relay checks it against a hard-coded allowlist before running it:
  `POST /api/chat`, `POST /api/generate` (empty prompt only: warm-up and
  unload), `POST /api/show`, `GET /api/tags`, `GET /api/ps`,
  `GET /api/version`, `POST /v1/chat/completions`, `GET /v1/models`.
  Pull, delete, create, push and copy are never allowed, so even a
  compromised server can only run inference on models you already pulled.
- It never downloads or updates code. Upgrade by fetching a new version and
  verifying its sha256 again.

The full setup guide (endpoint settings, Ollama settings, how requests flow,
troubleshooting) is [docs/LOCAL_MODELS.md](../docs/LOCAL_MODELS.md). When you
add a relay endpoint in LLM Lab (**Endpoints → Add endpoint**), the
**Connect** step shows the commands below with your server URL, slot count
and the script's sha256 filled in; later they are on the endpoint's
**Tokens** tab.

## Python

```bash
mkdir -p ~/.llmlab-relay && cd ~/.llmlab-relay
curl -fsSL https://lab.example.org/api/relay/v1/relay.py -o llmlab_relay.py
echo "<SHA256 shown in LLM Lab>  llmlab_relay.py" | sha256sum -c -
install -m600 /dev/stdin ~/.llmlab-relay/token        # paste the token, then Ctrl-D
python3 llmlab_relay.py --server https://lab.example.org --token-file ~/.llmlab-relay/token --parallel 2 --check
python3 llmlab_relay.py --server https://lab.example.org --token-file ~/.llmlab-relay/token --parallel 2
```

The token is never accepted on the command line (it would show up in `ps`),
and the relay refuses to start when the token file is readable by group or
others.

## Docker (build once, never fetch code at runtime)

```bash
curl -fsSL https://lab.example.org/api/relay/v1/relay.py -o llmlab_relay.py
echo "<SHA256>  llmlab_relay.py" | sha256sum -c -
curl -fsSL https://lab.example.org/api/relay/v1/Dockerfile -o Dockerfile
echo "<DOCKERFILE SHA256>  Dockerfile" | sha256sum -c -
docker build -t llmlab-relay:1.0.0 .
docker run --rm -i -v llmlab-relay:/state alpine sh -c 'umask 077; cat > /state/token; chown 65534 /state /state/token'
docker run -d --name llmlab-relay --restart unless-stopped --read-only --network host -v llmlab-relay:/state \
  -e LLMLAB_SERVER=https://lab.example.org -e LLMLAB_TOKEN_FILE=/state/token -e LLMLAB_STATE_DIR=/state \
  llmlab-relay:1.0.0 --parallel 2 --on-unauthorized wait
```

The image (`python:3.12-alpine`, pinned by digest) runs the script as user
65534 and keeps its relay id in `/state`. Docker restart policies cannot
exclude an exit code, so the container uses `--on-unauthorized wait`: after
a revoked token it idles and re-reads the token file every 60 s instead of
restarting in a loop. Add `--kind openai_compatible --upstream <url>` for a
non-Ollama server.

## systemd

`llmlab-relay.service` is a hardened unit (`NoNewPrivileges`,
`ProtectSystem=strict`, its own `llmlab-relay` user, `Restart=always`,
`RestartPreventExitStatus=3`). The server serves it at
`/api/relay/v1/llmlab-relay.service` (with its sha256 in `X-Relay-Sha256`,
also shown in LLM Lab). Its header comment lists the install steps; the
script goes to `/usr/local/lib/llmlab_relay.py`, the token lives in
`/etc/llmlab/relay-token` (0600) and the state in `/var/lib/llmlab-relay`.

## Options

| Option / env | Default | Meaning |
|---|---|---|
| `--server` / `LLMLAB_SERVER` | (required) | LLM Lab URL; https only (http allowed for localhost) |
| `--token-file` / `LLMLAB_TOKEN_FILE`, or `LLMLAB_TOKEN` | | relay token |
| `--upstream` / `LLMLAB_UPSTREAM` | `http://127.0.0.1:11434` | model server URL |
| `--kind` / `LLMLAB_KIND` | `ollama` | `ollama` or `openai_compatible`; must match the endpoint's kind |
| `--parallel` / `LLMLAB_PARALLEL` | `1` | concurrent upstream requests (match `OLLAMA_NUM_PARALLEL`; the server uses the smaller of this and the endpoint's slots) |
| `--state-dir` / `LLMLAB_STATE_DIR` | `~/.llmlab-relay` | keeps the relay id across restarts |
| `--upstream-api-key-env NAME` | | env var holding a key for the model server |
| `--ca-file` | | extra CA bundle for the LLM Lab server |
| `--max-num-ctx N` | | clamp `options.num_ctx` (protects VRAM) |
| `--on-unauthorized exit\|wait` | `exit` | behaviour after the server rejects the token |
| `--check` | | diagnose server, token and model server, then exit |
| `--log-level` | `info` | log verbosity |
| `--version` | | print the version and exit |

Exit codes: `0` ok, `1` failure (or `--check` found a problem), `2` usage,
`3` the token was rejected (systemd: `RestartPreventExitStatus=3`).

Signals: `SIGTERM` finishes in-flight requests (up to 30 s), aborts whatever
is still running (the server requeues it), says goodbye and exits 0; a
second `SIGTERM`/Ctrl-C aborts in-flight requests at once. `SIGHUP` re-reads
the token file (use it after rotating the token; the old token keeps working
for its grace period, 24 h by default); in-flight requests survive it.

## Behaviour

- One relay per endpoint is **active**; a second relay with a different relay
  id connects as **standby** and takes over when the active one disconnects
  or times out (session TTL 120 s).
- While the endpoint is disabled the relay stays connected and polls every
  15 s. On network errors it backs off (up to 60 s; at most 5 s while
  requests are running, so its session survives short blips).

## What the server sees

Hostname, relay version, upstream kind and URL hint, the client IP, the
model list (`/api/tags`, `/api/show` capabilities and context length) and
what is loaded (`/api/ps`). Logs on the GPU box contain metadata only
(request id, model, timings), never prompts, responses or the token.
