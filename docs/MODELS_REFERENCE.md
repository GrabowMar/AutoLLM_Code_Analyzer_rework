# Models reference

How the model catalog is populated from OpenRouter and how API keys are resolved when a job runs.

## The model catalog

`LLMModel` (`backend/llm_models/models.py`) stores one row per OpenRouter model: the `model_id` (e.g. `openai/gpt-4o`), provider, display name, capability flags (function calling, vision, streaming, JSON mode), context window and max output tokens, per-token pricing, and the full raw OpenRouter payload for anything else. A derived `cost_efficiency` score (0–1, free models score 1.0) feeds the rankings.

## Syncing from OpenRouter

`sync_models_from_openrouter` fetches the OpenRouter model list and upserts it — new models appear, existing ones update in place. Trigger it from the Models page in the UI (or via the `/api/models/` sync endpoint). The sync itself needs a resolvable OpenRouter key for the requesting user, so set up a key first.

An empty models page after a fresh install just means nobody has synced yet.

## API key resolution

Every OpenRouter call — generation, model sync, the `llm_review` analyzer — resolves its key per user via `backend/credentials/services/resolver.py`:

1. The user's own key, stored encrypted via **Settings → API Credentials**, wins if set.
2. Otherwise the deployment-wide `OPENROUTER_API_KEY` env var is used, but only while `OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK` is `True` (the default).
3. Otherwise the call fails with a "No OpenRouter API key is configured for this account" error that points at the settings page.

> [!TIP]
> On shared deployments set `OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=False` so each user's spend goes on their own key.

## API

`/api/models/` lists and filters the catalog and exposes the sync action; see the [API reference](/docs/api-reference). Model choice happens at generation time — see [Generation process](/docs/GENERATION_PROCESS).
