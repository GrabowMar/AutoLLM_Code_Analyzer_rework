# Models reference

How the model catalog is populated and enriched, what the model page measures, and how OpenRouter keys are resolved when a job runs.

## The model catalog

`LLMModel` (`backend/llm_models/models.py`) holds two kinds of rows:

| Rows | `endpoint` | `model_id` | Populated by |
| --- | --- | --- | --- |
| OpenRouter catalog | empty (or the system OpenRouter endpoint) | the OpenRouter id, e.g. `openai/gpt-4o` | catalog sync / import |
| Models on your own endpoints (Ollama, OpenAI-compatible, cloud providers) | that `LLMEndpoint` | `local-<endpoint slug>/<served name>` | the endpoint's inventory, see [Local models](/docs/LOCAL_MODELS) |

Each row stores provider, display name, capability flags (function calling, vision, streaming, JSON mode), context window and max output tokens, per-token pricing, the raw payload (`capabilities_json`) and structured `metadata`. Endpoint models also carry `served_model_name`, digest, quantization, parameter size, family, an optional `num_ctx_override`, and `is_available` / `is_hidden`. A derived `cost_efficiency` score (0 to 1, free models score 1.0) feeds the rankings.

Visibility goes through `LLMModel.objects.visible_to(user)`: everyone sees the OpenRouter catalog plus models of endpoints they may use, and staff see every row. An invisible model is a 404 everywhere, including job and condition creation.

## Syncing from OpenRouter

`sync_models_from_openrouter` (`backend/llm_models/services/sync.py`) fetches the OpenRouter model list and upserts it: new models appear and existing ones update in place, keeping stored enrichment. Trigger it from the Models page or with `POST /api/models/sync/`.

- **Staff only.** The sync uses the staff user's **own** stored OpenRouter key. The global `OPENROUTER_API_KEY` fallback is deliberately not used here, so without a personal key the call answers `403` with a `remediation` hint.
- `POST /api/models/detail/<slug>/refresh/` (staff) re-fetches one catalog model under the same key rule and forces a Hugging Face refetch. Endpoint models refresh from their endpoint instead.
- `POST /api/models/import/` (staff) loads models from an exported JSON or a raw OpenRouter payload. `GET /api/models/export/?format=csv|json` exports what you can see.

An empty models page after a fresh install means nobody has synced yet.

## Enrichment beyond OpenRouter

The model page combines the catalog row with three public, keyless sources (`backend/llm_models/services/enrichment.py`):

| Source | Adds | Stored |
| --- | --- | --- |
| [models.dev](https://models.dev) | release / last-updated date, knowledge cutoff, family, open-weights flag, reasoning options, long-context price tiers | `metadata.models_dev`, refreshed on every catalog sync (download cached 6 h) |
| [Hugging Face Hub](https://huggingface.co) (models with a `hugging_face_id`) | license, parameter count, architecture, base model, downloads, likes, arXiv papers | `metadata.huggingface`, refetched after 7 days |
| OpenRouter endpoints API | every provider serving the model: price, quantization, limits, uptime, latency, throughput | not stored: fetched live, cached 10 min |

The OpenRouter payload also carries Artificial Analysis indices and Design Arena Elo scores (`capabilities_json.benchmarks`), shown on the Benchmarks tab with catalog percentiles.

A sync queues the Hugging Face pass as the Celery task `enrich_catalog_task` after commit. Without a Celery worker it never runs, so run it by hand with `just manage enrich_models`. Use `--no-hf` to do only models.dev, or `--force-hf` to refetch fresh entries.

## Model page data

| Endpoint | Returns |
| --- | --- |
| `GET /api/models/detail/<slug>/` | The catalog row |
| `GET /api/models/detail/<slug>/insights/` | What the platform measured for **your** jobs with this model: build success and conformance with Wilson intervals, tokens, cost, findings by severity and tool, per-template results, experiments, ranking positions, plus global catalog percentiles (price, context, Artificial Analysis indices) and local benchmark results (`backend/llm_models/services/insights.py`) |
| `GET /api/models/detail/<slug>/endpoints/?refresh=` | Live OpenRouter provider list (empty for endpoint models) |
| `GET /api/models/detail/<slug>/related/` | Other models from the same provider and source |
| `GET /api/models/comparison/?models=a,b,…` | Side-by-side data for up to 6 models |

The list endpoint `GET /api/models/` filters by `search`, `provider`, `capability`, `free_only`, `price_range` (`free`, `low`, `medium`, `high`), `context_range` (`small`, `medium`, `large`, `xlarge`), `used_in_apps`, and `endpoint` (a slug, `local` or `openrouter`). It sorts with `sort_by` / `sort_dir`. Hidden and unavailable endpoint models are left out unless `include_unavailable=true`.

## API key resolution

Every OpenRouter call made for a user resolves its key through `backend/credentials/services/resolver.py`. That covers generation jobs (including the key handed to an agent sandbox's gateway), experiment launch checks, and the `llm_review` analyzer.

1. The user's own key, stored encrypted under **Settings → API keys** (`PUT /api/credentials/openrouter/`), wins if set.
2. Otherwise the deployment-wide `OPENROUTER_API_KEY` env var is used, but only while `OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK` is `True` (the default).
3. Otherwise the call fails with "No OpenRouter API key is configured for this account." Experiment launch reports this as `missing_api_key`.

Catalog sync and refresh skip step 2 (see above). Models on your own endpoints need no OpenRouter key: their access is resolved per endpoint by `backend/inference/access.py`.

> [!TIP]
> On shared deployments set `OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=False` so each user's spend goes on their own key.

The credentials API also stores `huggingface` and `github` keys. The GitHub key powers [GitHub export](/docs/INTEGRATIONS#3-github).

## See also

- [API reference](/docs/api-reference) for the `/api/models/` router
- [Generation process](/docs/GENERATION_PROCESS) for where models are chosen
- [Local models](/docs/LOCAL_MODELS) for endpoints and their inventories
