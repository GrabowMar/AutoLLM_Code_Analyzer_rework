# LLM Lab relay

`llmlab_relay.py` connects a model server on another network (a GPU box at
home, a lab workstation) to an LLM Lab server. It runs next to the model
server and only makes **outbound** HTTPS requests to
`<server>/api/relay/v1/`; nothing on the GPU box has to accept connections.

- Single file, Python ≥ 3.9 standard library only. No install step.
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

The full setup guide, including Ollama settings and troubleshooting, is
`docs/LOCAL_MODELS.md`. The endpoint's **Install** tab in LLM Lab shows the
commands below with your server URL, slot count and the script's sha256
filled in.

## Python

```bash
mkdir -p ~/.llmlab-relay && cd ~/.llmlab-relay
curl -fsSL https://lab.example.org/api/relay/v1/relay.py -o llmlab_relay.py
echo "<SHA256 from the Install tab>  llmlab_relay.py" | sha256sum -c -
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

Docker restart policies cannot exclude an exit code, so the container uses
`--on-unauthorized wait`: after a revoked token it idles and re-reads the
token file every 60 s instead of restarting in a loop.

## systemd

`llmlab-relay.service` is a hardened unit (`NoNewPrivileges`,
`ProtectSystem=strict`, its own user). The server serves it at
`/api/relay/v1/llmlab-relay.service` (with its sha256 in `X-Relay-Sha256`, also
shown on the Install tab). Its header comment lists the install steps; the
token lives in `/etc/llmlab/relay-token` (0600).

## Options

| Option / env | Default | Meaning |
|---|---|---|
| `--server` / `LLMLAB_SERVER` | (required) | LLM Lab URL; https only (http allowed for localhost) |
| `--token-file` / `LLMLAB_TOKEN_FILE`, or `LLMLAB_TOKEN` | | relay token |
| `--upstream` / `LLMLAB_UPSTREAM` | `http://127.0.0.1:11434` | model server URL |
| `--kind` / `LLMLAB_KIND` | `ollama` | `ollama` or `openai_compatible` |
| `--parallel` / `LLMLAB_PARALLEL` | `1` | concurrent upstream requests (match `OLLAMA_NUM_PARALLEL`) |
| `--state-dir` / `LLMLAB_STATE_DIR` | `~/.llmlab-relay` | keeps the relay id across restarts |
| `--upstream-api-key-env NAME` | | env var holding a key for the model server |
| `--ca-file` | | extra CA bundle for the LLM Lab server |
| `--max-num-ctx N` | | clamp `options.num_ctx` (protects VRAM) |
| `--on-unauthorized exit\|wait` | `exit` | behaviour after the server rejects the token |
| `--check` | | diagnose server, token and model server, then exit |

Exit codes: `0` ok, `1` failure (or `--check` found a problem), `2` usage,
`3` the token was rejected (systemd: `RestartPreventExitStatus=3`).

Signals: `SIGTERM` finishes in-flight requests (up to 30 s), aborts whatever is
still running (the server requeues it), says goodbye and exits 0; a second
`SIGTERM`/Ctrl-C aborts in-flight requests at once. `SIGHUP` re-reads the token file (use it after rotating the token;
the old token keeps working for its grace period, 24 h by default).

## What the server sees

Hostname, relay version, upstream kind and URL hint, the client IP, the
model list (`/api/tags`, `/api/show` capabilities and context length) and
what is loaded (`/api/ps`). Logs on the GPU box contain metadata only
(request id, model, timings), never prompts, responses or the token.
