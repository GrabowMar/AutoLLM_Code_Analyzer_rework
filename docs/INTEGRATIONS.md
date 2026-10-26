# Integrations

LLM Lab connects outward in four ways, managed under **Settings → Connections** (`/users/settings`) and the API at `/api/integrations/`.

| Integration | What it gives you | Settings section |
|---|---|---|
| [MCP server](#1-mcp-server) | Claude, ChatGPT, Cursor, VS Code, Codex, Gemini CLI, Windsurf … drive LLM Lab with tools | `#mcp` |
| [Cloud LLM providers](#2-cloud-llm-providers) | Claude API, OpenAI, Gemini, Mistral, Groq, DeepSeek, xAI, Together, Fireworks, Cerebras, NVIDIA NIM as endpoints next to OpenRouter | `#providers` |
| [GitHub](#3-github) | Export a generated app to a repo (one commit) or Gist; findings as SARIF in code scanning | `#github` |
| [Notifications](#4-notifications-webhooks) | Slack, Discord, Teams, ntfy, Telegram or signed JSON webhooks when jobs, analyses, experiments and reports finish | `#notifications` |

The old `/integrations` page redirects to these sections (`?tab=github` → `#github`).

## 1. MCP server

`/api/mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server (Streamable HTTP, stateless JSON responses, no server-initiated stream). It speaks protocol versions `2025-11-25`, `2025-06-18`, `2025-03-26` and `2024-11-05`, and answers unknown versions with the latest. Code: `backend/integrations/mcp/`.

**Auth.** Send an LLM Lab API token: `Authorization: Bearer llml_…`. Every tool call is replayed in-process against the REST API *as that token's user* (`mcp/dispatch.py`), so permissions, ownership and validation are exactly the API's. Token minting (`/api/tokens/`), first-run bootstrap and `/api/mcp` itself are unreachable from MCP.

| Token scopes | Effect on MCP |
|---|---|
| *(none)* | full access, header auth only |
| `mcp` | may also be embedded in the URL: `/api/mcp/t/<token>`, for web connectors that cannot send headers (claude.ai, ChatGPT). A token without `mcp` is refused there. |
| `read` (or `mcp:read`) | read-only: write tools are hidden, `call_api` allows GET only |

Create a connection token in **Settings → MCP server** (it mints one via `POST /api/tokens/` and fills it into the snippets) or under **Settings → Personal tokens**. Treat URL tokens like passwords, since they appear in proxy logs. Prefer `["mcp", "read"]` for connectors you share.

> [!WARNING]
> Scopes only restrict the MCP endpoint. The same token sent as a Bearer header to the REST API has its user's full rights.

**Tools.** 52 in total (`mcp/tools.py`):

- *47 curated tools*, each mapped to one REST operation with its input schema taken from the OpenAPI definition, so tools never drift from the API. They cover account and catalog (`whoami`, `search_models`, `get_model`, `compare_models`, `list_app_templates`, `list_stacks`) and generation (`generate_apps`, `generate_custom`, `preview_agent_jobs`, `create_agent_jobs`, `list_generation_jobs`, `get_generation_job`, `get_job_artifacts`, `generation_stats`, `cancel_generation_job`, `retry_generation_job`). Runtime: `build_app`, `smoke_test_app`, `list_containers`, `container_logs`, `start_container`, `stop_container`. Analysis: `list_analyzers`, `analyze`, `list_analysis_runs`, `get_analysis_run`, `list_findings`, `cancel_analysis_run`. Results: `rankings`, `top_models`, `dashboard`, `top_findings`, `list_reports`, `generate_report`, `get_report_data`. Experiments: `list_experiments`, `preview_experiment`, `create_experiment`, `get_experiment`, `experiment_status`, `experiment_results`, `launch_experiment`, `pause_experiment`, `resume_experiment`, `cancel_experiment`. Infrastructure: `list_endpoints`, `execution_queue`.
- *3 generic tools*: `find_api_operations` → `describe_api_operation` → `call_api` reach every REST operation not excluded above (the tool descriptions do not hard-code a count).
- *2 docs tools*: `search_docs`, `read_doc`.

A `read` token sees 33 tools. `?toolset=core` (e.g. `/api/mcp?toolset=core`) lists only 20 core tools (about 13k characters of definitions instead of about 48k) for clients with a small tool budget. Combined with a `read` token, that leaves 18.

**Resources**: every docs page (`llmlab://docs/<slug>`) and `llmlab://openapi.json`.
**Prompts**: `compare_models` (`task`, `models`), `triage_findings` (`run_id`), `experiment_report` (`experiment_id`), `platform_health`.

**Connecting clients.** **Settings → MCP server** renders ready-to-paste snippets with your token filled in (`GET /api/integrations/mcp/config/`):

```bash
# Claude Code
claude mcp add --transport http llm-lab https://<host>/api/mcp --header "Authorization: Bearer <TOKEN>"
```

- **claude.ai / Claude Desktop / mobile**: Settings → Connectors → Add custom connector → `https://<host>/api/mcp/t/<MCP-SCOPED TOKEN>`.
- **ChatGPT**: Settings → Apps & Connectors → Advanced → Developer mode → Create. Use the URL-token URL with authentication set to "No authentication".
- **Cursor / VS Code / Gemini CLI / Windsurf / Codex CLI**: JSON/TOML snippets on the page, plus an MCP Inspector command.
- **Claude API** (MCP connector, beta `mcp-client-2025-11-20`) and **OpenAI Responses API** (`{"type": "mcp", …}` tool): code snippets on the page. These call the server from the provider's cloud, so the instance must be publicly reachable.

Generation and agent runs spend LLM credits. The server instructions ask the model to confirm with the user first and to prefer the free preview tools.

## 2. Cloud LLM providers

`GET /api/integrations/providers/` lists the presets (open to every user) with any endpoints already connected from them. `POST /{preset_id}/test/` checks a key without saving it. `POST /{preset_id}/connect/` validates the key against the provider's `/models`, creates a private direct `openai_compatible` endpoint (key encrypted at rest), runs the inventory and hides non-chat models (embeddings, TTS, image, moderation …). Test and connect are staff-only, like every direct endpoint. Models appear as `local-<endpoint slug>/<model>` and can be used in experiments and agent runs like any other endpoint (see [Local models](/docs/LOCAL_MODELS)).

Provider quirks handled by the upstream caller (`backend/inference/upstream.py`):

- **Claude API** (`api.anthropic.com`, OpenAI-compatible layer): `anthropic-version` is sent, and `temperature` / `top_p` / `top_k` are dropped because current Claude models reject them. Sampling settings therefore have no effect on Claude endpoints.
- **Gemini** (`…/v1beta/openai`): requests go to `/chat/completions` and `/models` without the `/v1` prefix.

Caveats: non-chat models are hidden only once, at connect time. `/v1/models` is fetched without pagination. Cloud endpoints rarely report context windows. Requests are executed by the **executor** service, which must be running for cloud endpoints to answer ([Execution](/docs/EXECUTION)).

## 3. GitHub

Store a token in **Settings → GitHub** or with `PUT /api/credentials/github/` (`{"api_key": "<PAT>"}`). A classic token needs scope `repo`, plus `gist` and `security_events` as needed. A fine-grained token needs Administration and Contents write. `GET /api/integrations/github/status/` shows the connected login, the token type and (classic tokens only) its scopes.

`POST /api/integrations/github/export/` takes `{application_id, target: "repo"|"gist", repo_name, private, description?, upload_sarif, include_scaffold}`. `application_id` is the generation job UUID. For a repo, it creates the repository and pushes the app (stack skeleton plus generated code) as one commit. Junk and files over 1 MB are skipped, with a 50 MB total cap. The commit adds an LLM Lab README (model, template, findings summary), written as `LLM_LAB_EXPORT.md` if the app has its own `README.md`. With `upload_sarif`, the app's findings go to GitHub code scanning, which needs a public repo or GitHub Advanced Security. Gists hold only the generated files, flattened, up to 50 files and 1 MB in total.

## 4. Notifications (webhooks)

`/api/integrations/webhooks/` allows up to 25 subscriptions per user. Each has a kind (`slack`, `discord`, `teams`, `ntfy`, `telegram`, `generic`), a target and a set of events (`GET /event-types/`): `generation.{completed,failed,cancelled}`, `analysis.{completed,failed,cancelled}`, `experiment.{completed,cancelled}`, `report.{completed,failed}`. Jobs inside an experiment only notify with `include_experiment_jobs`.

- URLs and secrets are encrypted and write-only. Telegram takes a bot token (as the secret) and `telegram_chat_id` instead of a URL. ntfy takes an optional access token. `generic` deliveries carry `X-LLMLab-Event`, `X-LLMLab-Delivery` and, with a secret, `X-LLMLab-Signature: sha256=<hex HMAC-SHA256 of the raw body>`.
- Delivery is a Celery task (`deliver_webhook`) with a 10 s timeout and 3 retries on 5xx, 429 or network errors (backoff 15 s, 60 s, 240 s). It is de-duplicated per `(subscription, event)`. Events are caught by `post_save` and by a 60 s sweep (`integrations: sweep webhook events` beat task, registered after `migrate`), since the executor finishes jobs with bulk updates.
- Targets must be public https (http only with `DEBUG`). Private, loopback and platform addresses are refused, connections go to the vetted IP, and redirects are not followed. Allow a LAN ntfy or Teams host with `INFERENCE_DIRECT_ALLOWED_TARGETS`.
- `POST /{id}/test/` sends a sample event. `GET /{id}/deliveries/` shows the last 50.

Without a running Celery worker, deliveries stay pending ([Background services](/docs/BACKGROUND_SERVICES)).
