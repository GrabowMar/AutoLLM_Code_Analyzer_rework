# Models reference

How the model catalog is populated and enriched (models.dev, Hugging Face, Epoch AI, LMArena), what the model page measures, which research covariates it exports, and how OpenRouter keys are resolved when a run starts.

## The model catalog

`LLMModel` (`backend/llm_models/models.py`) holds two kinds of rows:

| Rows | `endpoint` | `model_id` | Populated by |
| --- | --- | --- | --- |
| OpenRouter catalog | empty (or the system OpenRouter endpoint) | the OpenRouter id, e.g. `openai/gpt-4o` | catalog sync / import |
| Models on your own endpoints (Ollama, OpenAI-compatible, cloud providers) | that `LLMEndpoint` | `local-<endpoint slug>/<served name>` | the endpoint's inventory, see [Local models](/docs/LOCAL_MODELS) |

Each row stores provider, display name, capability flags (function calling, vision, streaming, JSON mode), context window and max output tokens, per-token pricing, the raw payload (`capabilities_json`) and structured `metadata`. Endpoint models also carry `served_model_name`, digest, quantization, parameter size, family, an optional `num_ctx_override`, and `is_available` / `is_hidden`. A derived `cost_efficiency` score (0 to 1, free models score 1.0) feeds the rankings.

Visibility goes through `LLMModel.objects.visible_to(user)`: everyone sees the OpenRouter catalog plus models of endpoints they may use, and staff see every row. An invisible model is a 404 everywhere, including run and condition creation.

## Syncing from OpenRouter

`sync_models_from_openrouter` (`backend/llm_models/services/sync.py`) fetches the OpenRouter model list and upserts it: new models appear and existing ones update in place, keeping stored enrichment. Trigger it from the Models page or with `POST /api/models/sync/`.

- **Staff only.** The sync uses the staff user's **own** stored OpenRouter key. The global `OPENROUTER_API_KEY` fallback is deliberately not used here, so without a personal key the call answers `403` with a `remediation` hint.
- `POST /api/models/detail/<slug>/refresh/` (staff) re-fetches one catalog model under the same key rule and forces a Hugging Face refetch with the caller's Hugging Face token (waiting at most 15 s on a Hub 429). Endpoint models refresh from their endpoint instead.
- `POST /api/models/import/` (staff) loads models from an exported JSON or a raw OpenRouter payload. `GET /api/models/export/?format=csv|json` exports what you can see.

An empty models page after a fresh install means nobody has synced yet.

## Enrichment beyond OpenRouter

The model page combines the catalog row with these sources (`backend/llm_models/services/enrichment.py`):

| Source | Adds | Stored |
| --- | --- | --- |
| [models.dev](https://models.dev) | release / last-updated date, knowledge cutoff, family, open-weights flag, reasoning options, long-context price tiers, status | `metadata.models_dev`, refreshed on every catalog sync (download cached 6 h) |
| [Hugging Face Hub](https://huggingface.co) (models with a `hugging_face_id`) | the repo record, its `config.json`, `generation_config.json`, `tokenizer_config.json`, file tree, self-reported evaluations and papers ([below](#the-hugging-face-record)) | `metadata.huggingface` (schema 2), refetched after 7 days; parameter counts derived from it in `metadata.params` |
| OpenRouter endpoints API | every provider serving the model: price, quantization, limits, uptime, latency, throughput | not stored: fetched live, cached 10 min |
| Epoch AI, LMArena | reported size and training compute; arena ratings | `metadata.epoch`, `metadata.lmarena`, imported by hand ([Research covariates](#research-covariates)) |

The OpenRouter payload also carries Artificial Analysis indices and Design Arena Elo scores (`capabilities_json.benchmarks`), shown on the Benchmarks tab with catalog percentiles.

A sync queues the Hugging Face pass as the Celery task `enrich_catalog_task` after commit, with the syncing user's Hugging Face token. Without a Celery worker it never runs, so run it by hand:

```bash
python manage.py enrich_models [--no-hf] [--force-hf] [--hf-user ID] [--model PK_OR_MODEL_ID ...] [--no-link-local] [--dry-run]
```

| Flag | Effect |
| --- | --- |
| `--no-hf` | models.dev only |
| `--force-hf` | Refetch Hugging Face records that are still fresh |
| `--hf-user ID` | Use that user's stored Hugging Face token ([token precedence](#hugging-face-token)) |
| `--model` | Only this model, by pk or model id; repeatable |
| `--no-link-local` | Do not link local endpoint models to their [catalog twins](#local-endpoint-models-and-catalog-twins) |
| `--dry-run` | List the Hub fetches, the token source and an upper bound of Hub requests; no HTTP request, nothing written |

Records from before schema 2 count as stale, so the first sync after an upgrade refetches every repo: about 7 Hub requests per repo, roughly 8–10 minutes for 180 repos with a token (anonymous: about 13). Catalog rows that share one repo (`x`, `x:free`) are fetched once per run. Every write re-reads the row under a lock and merges only its own keys, so concurrent writers (a sync, the covariates import) do not overwrite each other.

### Hugging Face token

Hub requests use the first token available:

1. the stored `huggingface` credential (Settings → API keys) of the user the request runs for: the staff user who clicked **Sync** or **Refresh**, or `--hf-user`;
2. the deployment-wide `HUGGINGFACE_API_TOKEN` env var;
3. none (anonymous).

A token raises the Hub's API quota (observed: 1,000 requests per 300 s with a token, 500 without). It does not open gated repos (Llama, Gemma, Command A, …): until the token's account has accepted a repo's license on huggingface.co, that repo's `config.json`, `generation_config.json` and `tokenizer_config.json` are recorded as `gated_inaccessible` (the tokenizer facts then come from what the Hub record itself exposes), and its architecture and active parameters stay unknown. 401 and 403 are recorded this way, never raised.

The client sends the token only to `huggingface.co` hosts and follows redirects itself, so a CDN hop never receives it. It paces requests (at least 0.35 s apart with a token, 0.65 s without), backs off when the `RateLimit` header runs low and honours `429 Retry-After`. The token is never logged, stored with the data or returned by the API.

### The Hugging Face record

`metadata.huggingface` keeps the earlier keys (license, parameter count, base model, downloads, likes, arXiv ids, `fetched_at`, …) and adds:

| Key | Content |
| --- | --- |
| `sha`, `revision` | The repo commit; the repo files are read at that commit |
| `downloads_all_time`, `trending_score`, `used_storage_bytes`, `spaces_count_capped` | Adoption. The Hub truncates a repo's Spaces list at 100, shown as "100+" |
| `children`, `base_model_relation`, `base_models_expanded` | Derived repos (adapter, merge, quantized, finetune) and this repo's relation to its base model |
| `inference_providers` | Hugging Face inference providers serving it: provider, status, task, provider model id |
| `native_quant_method` | The quantization the weights ship in |
| `architecture` | Parsed from `config.json` (multimodal configs: the nested text config): model type, layers, hidden size, attention and KV heads, head dim, intermediate size, vocabulary, max position embeddings, native context, RoPE (theta, scaling type, factor, original max), attention variant (MLA, GQA, MQA, MHA; LoRA ranks, sliding window, layer-type counts), tied embeddings, dtype, quantization config, `is_moe` and the MoE layout (experts, experts per token, shared experts, expert FFN size, leading dense layers, MTP layers), and `uncovered_features` the parser does not model |
| `generation_config` | The authors' `generation_config.json`: `temperature`, `top_p`, `top_k`, `min_p`, `repetition_penalty`, `do_sample`, `max_new_tokens`, `from_model_config` (the file was generated from the model config by the library, not chosen by the authors) and the commit. It is what the authors **recommend**, never what a provider or a run used |
| `tokenizer` | Tokenizer class, `model_max_length`, whether there is a chat template, its sha256 and source, BOS and EOS tokens |
| `files` | Weight bytes, weight files, format (safetensors, gguf, …), shard count, other weight formats, repo file count, and the git oids of the config and template files. Weights a repo ships twice (consolidated and sharded) count once; a truncated tree listing gives no byte total |
| `eval_results` | Evaluations the repo publishes (`evalResults`, model-index): task, dataset, metric, value, date, `source: "model card (self-reported)"`, source name and URL, notes, `verified`. At most 60 are kept; `eval_results_total` gives the full count. They are self-reported: never normalized, ranked or verified here |
| `papers` | Up to 5 arXiv papers: id, title, date, upvotes, GitHub repo (cached 7 days) |
| `schema`, `authenticated`, `fetch_status`, `gated_inaccessible` | Record version, whether a token was used, and the status of each sub-fetch (`api`, `tree`, `config`, `generation_config`, `tokenizer_config`, `papers`) |

Unknown or unparseable values are null, never a guessed number. A transient failure of one sub-fetch keeps that part of the previous record, which can then describe an older commit than `revision`; `fetch_status` shows it.

### Parameter counts

`metadata.params` keeps three different counts apart, each with its source:

| Key | Meaning |
| --- | --- |
| `checkpoint_total` | The safetensors element count of the linked repo: every stored tensor, MTP heads, vision encoder and extra embeddings included (`checkpoint_note`; packed int dtypes make it differ from the logical count) |
| `reported_total` | The total the authors state |
| `active`, `active_source`, `active_note` | Parameters used per token, and where the figure comes from |
| `config_estimate` | The config-based estimate, kept as a cross-check whichever source wins |

`active` comes from the first source that applies:

1. `model_card_manual`: `CURATED_PARAMS` in `backend/llm_models/services/hf_metadata.py`, a hand-kept table that quotes each model card (DeepSeek-V4.1-Flash, DeepSeek-V4-Pro, GLM-5.3-Flash and Qwen3.8-Flash-Next so far). Add an entry by hand when a new model enters a study.
2. `name_pattern`: the name states it (`30B-A3B`), rounded as in the name.
3. `dense`: `config.json` was read and describes a dense model, so active = every checkpoint parameter. For a multimodal checkpoint that is an upper bound for text tokens, since the encoder runs only on images or audio.
4. `config_estimate`: checkpoint total minus the inactive routed experts, `L_moe × (E − k) × 3 × hidden × moe_intermediate` (assumes SwiGLU experts). It is an estimate and can differ from published figures (DeepSeek-V3-style models: about 40.1B against the stated 37B, because it counts the MTP layer).

Otherwise `active` is null and `active_note` says why (gated or unreadable config, packed integer weights, components the estimate does not cover, an unrecognised layout). When models.dev lists an API model as closed weights, `mapping_note` warns that the figures describe the linked repo, which may not be the weights being served.

### Local endpoint models and catalog twins

A model on your own endpoint has no catalog metadata of its own. Enrichment links it to the catalog row assumed to serve the same weights, stored as `metadata.catalog_twin`: `{model_id, source, canonical_model_id, served_name, note, provider_record, linked_at}`. Matching, first hit wins:

1. a models.dev provider whose id is the endpoint's slug and lists the served name (`source: "models.dev:<provider>"`);
2. a models.dev provider on the same API host, when those providers agree on one canonical model;
3. the one catalog model whose id ends in `/<served name>` (`source: "served_name"`), unless the endpoint reports a parameter size more than 1.5× away from that model's checkpoint (Ollama's `deepseek-r1` is an 8B distill, not the 671B model).

A link with `source: "manual"` is never overwritten. Linking runs in `enrich_models` (unless `--no-link-local`), in the catalog sync's models.dev pass, and when a local row is enriched. A link made earlier is kept when a later lookup finds nothing (for example while models.dev is unreachable).

The twin is an assumption, and its note says so: same name, assumed same weights; quantization and serving stack may differ. **Rows are never merged and statistics never pooled.** The model page shows the twin's model-intrinsic metadata (Hugging Face card, architecture, parameters, external covariates) marked "inherited from <twin>", while the context window, prices and every run statistic stay the route's own. `GET /api/models/detail/<slug>/` returns `twin` for local rows (the twin's id, slug and name, its `models_dev`, `huggingface`, `params`, `epoch` and `lmarena` metadata and its capabilities), and null for catalog rows.

### Sampling defaults

The Capabilities tab's **Sampling defaults** card puts three things side by side and never mixes them:

- **Authors · recommended**: `generation_config.json` at the recorded commit, or why there is none (gated, not published, not fetched). A file generated from the model config is flagged as boilerplate.
- **OpenRouter default**: the catalog's `default_parameters`, the reasoning default (on or off) and its effort; "not served via OpenRouter" for local models.
- **What your runs sent**: `GET /api/models/detail/<slug>/sampling/`.

The endpoint covers your runs of the model (the insights scope, every status; `beyond_plan` runs left out) and reads each run's frozen manifest: `sampling.forced_params` (what the gateway forced on every call), `sampling.policy` and `sampling.stripped_params`. Under `provider_default` the gateway strips every other sampling key, so a key that was not forced was not sent and the provider's default applied. Under `harness_native`, or for a run without a manifest (`not_recorded`), an unforced key may have come from the harness, so it counts as unknown, never as "not sent". It returns `n_jobs` (runs), `by_mode` (run kinds; both key names kept for the model page), `policies`, `sources`, `params` (per key: `n_sent`, `n_not_sent`, `n_unknown` and the values sent with their counts), `harness_sampling_stripped` (keys the gateway removed, with run counts) and the sampling `presets` used.

The authors' recommendation is stored, not applied: a run uses it only if a sampling preset sets those values.

## Research covariates

Two public, keyless sources add model-level covariates for analysis (`backend/llm_models/services/external_sources.py`). Both are licensed **CC-BY 4.0**: attribute Epoch AI and LMArena wherever you use their values. The license, source URL, fetch time and curator's match note are stored next to every value.

| Source | What | Stored |
| --- | --- | --- |
| [Epoch AI "All AI models"](https://epoch.ai/data/ai-models) (`all_ai_models.csv`) | Reported parameters with the notes they come from, active parameters parsed from those notes (only when exactly one value is stated), training compute (FLOP), training dataset size, accessibility, open weights, Epoch's confidence rating, publication date, reference | `metadata.epoch` |
| [LMArena leaderboard dataset](https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset) (split `latest`, read anonymously through the Hugging Face datasets-server) | Webdev arena (overall, HTML, React) and text arena (overall, coding): rating with its 95% interval, votes, rank of n ranked, the category's range. Agent arena: a score (not an Elo rating) with its interval, observations, sessions and rank | `metadata.lmarena.categories` |

Neither import writes benchmark rows, so these values never feed rankings or scores: they are descriptive covariates. The catalog sync and the endpoint inventory keep both keys.

**The name map.** Models are joined to the sources only through `backend/llm_models/data/external_model_map.yaml`, by exact name; nothing is fuzzy-matched at import. Each entry names the Epoch model (`epoch`), the arena names per dataset (`lmarena: {webdev, text, agent}`), a `match` and a `note` with the evidence, and for a local endpoint row its `twin`. `null` means no confident match. A name a source lists twice (in one arena category) is ambiguous and never matched. `match` is required:

| `match` | Meaning |
| --- | --- |
| `exact` | The same model in the default configuration we call |
| `variant` | The same weights or version, but the source measured another reasoning effort or release. Arena entries are per effort ("(Max)", "(High)", …); our runs send no effort and get the provider's default |
| `assumed` | The identity is an assumption: a subscription endpoint whose served model and effort are not disclosed, or a disputed catalog link. Read the note |

A record a local route inherits from its catalog twin is always reported as `assumed`.

**Curating and importing:**

1. `python manage.py import_external_covariates --suggest` prints the closest names in each source (difflib scores) for the models used in runs and the local models, or for `--model` / `--run`. It writes nothing.
2. Edit the map entry: the exact names, `match`, and a note with the evidence.
3. Run with `--dry-run`: it downloads, matches and prints each model's status, and warns about used models without an entry and twins missing from the database.
4. Run it for real.

```bash
python manage.py import_external_covariates [--epoch] [--lmarena] [--dry-run] [--model MODEL_ID ...] [--run RUN_UUID ...] [--suggest] [--map PATH]
```

| Flag | Effect |
| --- | --- |
| `--epoch`, `--lmarena` | Import only this source (default: both) |
| `--model` (repeatable) | Only this model id |
| `--run` (repeatable) | Only the model of this generation run |
| `--suggest` | Print candidate names for curation; no writes |
| `--map PATH` | Use another map file |
| `--dry-run` | Download and match; write nothing |

The import replaces only the `epoch` and `lmarena` keys, under a row lock. A `null` mapping removes a stale value; a mapped name missing from the download keeps the stored value and prints a warning. It takes about 10 s (public downloads only). Re-run it from time to time: Epoch's rows change continuously, and LMArena's `latest` split moves.

Values are copied as the sources have them, errors included; the map notes flag the ones found so far (Epoch lists Grok 4.7's parameters as 2.1e9 while its note names the 2.1T model). Most arena entries measured a high reasoning effort while our runs use the provider default, so keep the match columns in any analysis. With few models in a study, a covariate–outcome relationship is descriptive only: the unit is the model.

### Covariates export

`GET /api/models/covariates/?used_only=true|false&format=json|csv` (`backend/llm_models/services/model_covariates.py`) returns one row per model you can see. `used_only` (default `true`) keeps models with at least one of your generation runs. JSON adds the column order, the sources with their licenses and reading notes; `format=csv` downloads `model_covariates_<used|all>_<YYYYMMDD>.csv` with formula-safe cells, `true`/`false` booleans, `;`-joined lists and empty cells for unknown values. Another format is a 400. The **Covariates CSV** button on the Models page downloads the used-only CSV.

The 73 columns, in groups (exact order: `COLUMNS` in that module):

| Group | Columns |
| --- | --- |
| Identity | `id`, `model_id`, `model_name`, `provider`, `is_local`, `endpoint`, `served_model_name`, `catalog_twin`, `catalog_twin_source`, `inherited_from`, `inherited_fields` |
| Openness, license, dates | `open_weights` and `open_weights_source`, `hf_id`, `license`, `release_date` and `release_date_source`, `knowledge_cutoff`, `knowledge_cutoff_source`, `knowledge_cutoff_conflict` (models.dev, OpenRouter and the serving provider's record disagree on the year-month) |
| This route's limits and price | `context_window`, `max_output_tokens`, `input_usd_per_1m`, `output_usd_per_1m`: never inherited; prices are null for local routes |
| Size | `params_checkpoint_total`, `params_reported_total`, `params_reported_source`, `params_active`, `params_active_source` (falling back to Epoch, labelled `epoch` / `epoch_notes`) |
| Architecture | `arch_is_moe`, `arch_num_experts`, `arch_experts_per_token`, `arch_num_hidden_layers`, `arch_hidden_size`, `arch_native_context`, `arch_native_dtype`, `arch_quant_method`, `served_quantization` |
| Hugging Face popularity | `hf_downloads_30d`, `hf_downloads_all_time`, `hf_likes`, `hf_fetched_at` |
| Epoch AI | `epoch_model`, `epoch_publication_date`, `epoch_parameters`, `epoch_active_params`, `epoch_training_compute_flop`, `epoch_training_dataset_size`, `epoch_confidence`, `epoch_accessibility`, `epoch_match`, `epoch_fetched_at` |
| LMArena | `lmarena_webdev_name`, `lmarena_webdev_rating` with `_lower` / `_upper`, `lmarena_webdev_votes`, `lmarena_webdev_rank`, `lmarena_webdev_n_ranked`, `lmarena_text_coding_rating`, `lmarena_text_coding_votes`, `lmarena_agent_score` with `_lower` / `_upper`, `lmarena_publish_date`, `lmarena_match`, `lmarena_fetched_at` |
| Recommended sampling | `recommended_temperature`, `recommended_top_p`, `recommended_top_k`, `recommended_sampling_boilerplate` (`generation_config.json` was generated from the model config): the authors' recommendation, not what a run used |
| This platform | `n_jobs`, `n_completed_jobs` (your generation runs; key names kept) |

The unit is the model, never the run. Null means not known, never 0. A local route takes its model-intrinsic covariates (openness, license, parameters, architecture, popularity, recommended sampling, external scores) from its catalog twin and lists them in `inherited_fields`; `checkpoint` and `reported` parameter counts are different quantities ([Parameter counts](#parameter-counts)).

## Model page data

| Endpoint | Returns |
| --- | --- |
| `GET /api/models/detail/<slug>/` | The catalog row, with `twin` for local endpoint models ([catalog twins](#local-endpoint-models-and-catalog-twins)) |
| `GET /api/models/detail/<slug>/sampling/` | The sampling parameters your runs of this model sent ([Sampling defaults](#sampling-defaults)) |
| `GET /api/models/covariates/?used_only=&format=json\|csv` | One covariate row per model ([Covariates export](#covariates-export)) |
| `GET /api/models/detail/<slug>/insights/` | What the platform measured for **your** generation runs with this model: build success and conformance with Wilson intervals, tokens, cost (per kLOC of source code), findings of each run's latest result per tool (by tool, with the tools' own labels; level counts are the normalized heuristic, with unrated tools' findings counted as `unrated` and dependency advisories in their own group), per-template results, experiments, ranking positions, plus global catalog percentiles (price, context, Artificial Analysis indices) and local benchmark results (`backend/llm_models/services/insights.py`) |
| `GET /api/models/detail/<slug>/endpoints/?refresh=` | Live OpenRouter provider list (empty for endpoint models) |
| `GET /api/models/detail/<slug>/related/` | Other models from the same provider and source |
| `GET /api/models/comparison/?models=a,b,…` | Side-by-side data for up to 6 models |

The model page's **Generation data** tab (`?tab=generation`) shows what this platform recorded about how the model generated, over your generation runs (`model_id` is the model's numeric pk):

| Panel | Endpoint | Shows |
| --- | --- | --- |
| Agent behaviour | `GET /api/generation/models/<model_id>/behaviour/` | Trajectory summaries per harness: actions, builds, tests, servers, context growth ([Trajectory and behaviour metrics](/docs/GENERATION_PROCESS#trajectory-and-behaviour-metrics)) |
| Providers and calls | `GET /api/generation/models/<model_id>/call-telemetry/?mode=` | Per upstream provider: TTFT, decode and end-to-end speed, call duration, cache hits, reasoning share, retries, errors, stats coverage ([Per-call telemetry](/docs/GENERATION_PROCESS#per-call-telemetry)) |
| Same-setup variability | `GET /api/generation/models/<model_id>/variability/` | Spread between runs that share a setup, and whether seeded replays reproduced ([Run-to-run variation](/docs/STUDIES#run-to-run-variation)) |

All three are descriptive (n everywhere, no tests). For a local endpoint model they cover the route's own runs only: its catalog twin's runs are never pooled with them.

The list endpoint `GET /api/models/` filters by `search`, `provider`, `capability`, `free_only`, `price_range` (`free`, `low`, `medium`, `high`), `context_range` (`small`, `medium`, `large`, `xlarge`), `used_in_apps`, and `endpoint` (a slug, `local` or `openrouter`). It sorts with `sort_by` / `sort_dir`. Hidden and unavailable endpoint models are left out unless `include_unavailable=true`.

## API key resolution

Every OpenRouter call made for a user resolves its key through `backend/credentials/services/resolver.py`. That covers generation runs (including the key handed to an agent sandbox's gateway), study launch checks, and the `llm_review` analyzer.

1. The user's own key, stored encrypted under **Settings → API keys** (`PUT /api/credentials/openrouter/`), wins if set.
2. Otherwise the deployment-wide `OPENROUTER_API_KEY` env var is used, but only while `OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK` is `True` (the default).
3. Otherwise the call fails with "No OpenRouter API key is configured for this account." Study launch reports this as `missing_api_key`.

Catalog sync and refresh skip step 2 (see above). Models on your own endpoints need no OpenRouter key: their access is resolved per endpoint by `backend/inference/access.py`.

> [!TIP]
> On shared deployments set `OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=False` so each user's spend goes on their own key.

The credentials API also stores `huggingface` and `github` keys. The Hugging Face key is used for Hub metadata when its owner (staff) syncs or refreshes the catalog, or through `enrich_models --hf-user`, ahead of the `HUGGINGFACE_API_TOKEN` env var ([Hugging Face token](#hugging-face-token)); it is sent only to huggingface.co. The GitHub key powers [GitHub export](/docs/INTEGRATIONS#3-github).

## See also

- [API reference](/docs/api-reference) for the `/api/models/` router
- [Generation process](/docs/GENERATION_PROCESS) for where models are chosen
- [Local models](/docs/LOCAL_MODELS) for endpoints and their inventories
