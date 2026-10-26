# Integrations

LLM Lab connects outward in four ways, all managed under **Integrations** in the UI
(`/integrations`) and the API at `/api/integrations/`:

| Integration | What it gives you |
|---|---|
| [MCP server](#1-mcp-server) | Claude, ChatGPT, Cursor, VS Code, Codex, Gemini CLI … drive LLM Lab with tools |
| [Cloud LLM providers](#2-cloud-llm-providers) | Claude API, OpenAI, Gemini, Mistral, Groq, DeepSeek, xAI, Together, Fireworks, Cerebras, NVIDIA as endpoints next to OpenRouter |
| [GitHub](#3-github) | Export a generated app to a repo (one commit) or Gist; findings as SARIF in code scanning |
| [Notifications](#4-notifications-webhooks) | Slack, Discord, Teams, ntfy, Telegram or signed JSON webhooks when jobs, analyses, experiments and reports finish |

## 1. MCP server

`/api/mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server
(Streamable HTTP, stateless JSON responses; protocol versions 2024-11-05 → 2025-11-25).

**Auth.** Send an LLM Lab API token: `Authorization: Bearer llml_…`. Every tool call is
replayed in-process against the REST API *as that token's user*, so permissions, ownership
and validation are exactly the API's.

| Token scopes | Effect |
|---|---|
| *(none)* | full access, header auth only |
| `mcp` | may also be embedded in the URL: `/api/mcp/t/<token>` — for web connectors that cannot send headers (claude.ai, ChatGPT). A token without `mcp` is refused there. |
| `read` | read-only: write tools are hidden, `call_api` allows GET only |

Create tokens under **Integrations → MCP server**; treat URL tokens like passwords (they
appear in proxy logs) and prefer `["mcp", "read"]` for connectors you share.

**Tools.** ~50 tools:

- *curated* — `search_models`, `list_app_templates`, `generate_apps`, `create_agent_jobs`,
  `get_generation_job`, `build_app`, `analyze`, `list_findings`, `rankings`, `dashboard`,
  `preview_experiment`, `create_experiment`, `experiment_results`, `generate_report`, … Each
  maps to one REST operation and takes its input schema from the OpenAPI definition, so tools
  never drift from the API;
- *generic* — `find_api_operations` → `describe_api_operation` → `call_api` reach every one
  of the 225+ operations the UI uses;
- *docs* — `search_docs`, `read_doc`.

`?toolset=core` (e.g. `/api/mcp?toolset=core`) lists only 20 core tools (~3.5k tokens of
definitions instead of ~12k) for clients with a small tool budget.

**Resources**: every docs page (`llmlab://docs/<slug>`) and `llmlab://openapi.json`.
**Prompts**: `compare_models`, `triage_findings`, `experiment_report`, `platform_health`.

**Connecting clients** — *Integrations → MCP server* renders these with your token filled in
(`GET /api/integrations/mcp/config/`):

```bash
# Claude Code
claude mcp add --transport http llm-lab https://<host>/api/mcp --header "Authorization: Bearer <TOKEN>"
```

- **claude.ai / Claude Desktop / mobile**: Settings → Connectors → Add custom connector →
  `https://<host>/api/mcp/t/<MCP-SCOPED TOKEN>`.
- **ChatGPT**: Settings → Apps & Connectors → Advanced → Developer mode → Create; the URL-token
  URL, authentication "No authentication".
- **Cursor / VS Code / Gemini CLI / Windsurf / Codex CLI**: JSON/TOML snippets on the page.
- **Claude API** (MCP connector, beta `mcp-client-2025-11-20`) and **OpenAI Responses API**
  (`{"type": "mcp", …}` tool): code snippets on the page. These call the server from the
  provider's cloud, so the instance must be publicly reachable.

Generation and agent runs spend LLM credits; the server instructions ask the model to confirm
with the user and to prefer the free `preview_*` tools first.

## 2. Cloud LLM providers

`GET /api/integrations/providers/` lists presets; **Connect** (staff only, like every direct
endpoint) validates the key against the provider's `/models`, creates a private direct
`openai_compatible` endpoint (key encrypted at rest), runs the inventory and hides non-chat
models (embeddings, TTS, image, moderation …). Models appear as `local-<slug>/<model>` and can
be used in experiments and agent runs like any other endpoint (see `LOCAL_MODELS.md`).

Provider quirks handled by the executor's upstream caller:

- **Claude API** (`api.anthropic.com`, OpenAI-compatible layer): `anthropic-version` is sent,
  and `temperature` / `top_p` / `top_k` are dropped because current Claude models reject them.
  Sampling settings therefore have no effect on Claude endpoints.
- **Gemini** (`…/v1beta/openai`): requests go to `/chat/completions` and `/models` without the
  `/v1` prefix.

Caveats: hiding non-chat models happens once, at connect time; `/v1/models` is fetched without
pagination; cloud endpoints rarely report context windows. Requests are executed by the
**executor** service — it must be running for cloud endpoints to answer.

## 3. GitHub

Store a token with `PUT /api/credentials/github/` (`{"api_key": "<PAT>"}`; classic scopes
`repo`, plus `gist` and `security_events` as needed; fine-grained: Administration + Contents
write). `GET /api/integrations/github/status/` shows the connected login.

`POST /api/integrations/github/export/`
`{application_id, target: "repo"|"gist", repo_name, private, description?, upload_sarif, include_scaffold}`
creates the repo and pushes the app (stack skeleton + generated code, junk and files > 1 MB
skipped) as one commit with an LLM Lab README (model, template, findings summary). With
`upload_sarif`, the app's findings go to GitHub code scanning (needs a public repo or GitHub
Advanced Security). Gists hold only the generated files, flattened. The findings summary and the SARIF come from the app's
canonical analysis run: counted static findings plus one row per AI consensus cluster
(kept apart). When the static measurement is unavailable (a required tool failed),
the README says so instead of presenting it as a clean result.

## 4. Notifications (webhooks)

`/api/integrations/webhooks/` — up to 25 subscriptions per user, each a kind (`slack`,
`discord`, `teams`, `ntfy`, `telegram`, `generic`), a target and a set of events:
`generation.{completed,failed,cancelled}`, `analysis.{completed,failed,cancelled}`,
`experiment.{completed,cancelled}`, `report.{completed,failed}`. Jobs inside an experiment
only notify with `include_experiment_jobs`. The `analysis.*` payload follows the run summary
(schema 2): `static_measured`, counted findings and `issues`, `ai_findings`,
`is_complete`, `plan_hash`, `cost_usd` and `cost_complete`; an unscanned run says
"not measured" instead of reporting 0 findings. The `generation.*` payload's
`pass_rate` is the conformance rate only.

- URLs and secrets are encrypted and write-only. `generic` deliveries carry
  `X-LLMLab-Event`, `X-LLMLab-Delivery` and, with a secret,
  `X-LLMLab-Signature: sha256=<hex HMAC-SHA256 of the raw body>`.
- Delivery is a Celery task (10 s timeout, 3 retries with backoff), de-duplicated per
  `(subscription, event)`. Events are caught by `post_save` and by a 60 s sweep
  (`integrations: sweep webhook events` beat task), since the executor finishes jobs with bulk
  updates.
- Targets must be public https (http only with `DEBUG`); private, loopback and platform
  addresses are refused, connections go to the vetted IP and redirects are not followed.
  Allow a LAN ntfy/Teams host with `INFERENCE_DIRECT_ALLOWED_TARGETS`.
- `POST /{id}/test/` sends a sample event; `GET /{id}/deliveries/` shows the last 50.
