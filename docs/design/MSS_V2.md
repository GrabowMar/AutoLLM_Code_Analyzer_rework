# MSS v2: Model Selection Score and study model selection

Status: methodology documentation of the implementation in `backend/rankings/` (version `mss-2.0`, 2026-11-09). It is the design spec with **Amendment A1** (stratified quota selection, within-tier score, quality floor, hard budgets) folded in; where the two differ, A1 wins and is restated in §0.

Code map:
- `backend/rankings/data/mss_frame.yaml` — curated vendors (models.dev / OpenRouter provider ids, origin), OpenCode Go monthly limits, line overrides, twin overrides, include / exclude, open-weight overrides.
- `services/frame.py` — canonicalisation K1–K6 and eligibility E1–E7 with the PRISMA-style flow.
- `services/popularity.py`, `services/openrouter_api.py` — OpenRouter Data API fetchers (timeouts, retry, key only to openrouter.ai), popularity computation, `SourceSnapshot` storage.
- `services/pricing.py` — first-party list prices, reference run cost and planning cost.
- `services/mss.py` — component scores, aggregation, coverage, E8, the sensitivity variants.
- `services/selection.py` — pure stratified selection: tiers, MSS_tier, route choice, quality floor, exact look-ahead, sum-optimal diagnostic, infeasibility diagnosis.
- `services/study_selection.py` — inputs from the database, the pure replayable pipeline (`run_selection`), budgets.
- `services/sensitivity.py` — S1–S6, Jaccard, Kendall τ_b, RBO; also the empirical severity-weight sensitivity.
- `services/snapshot.py` — draft / confirmed / superseded selection snapshots and exact replay.
- Commands: `refresh_mss_inputs`, `import_list_prices`, `import_popularity`, `select_models`.

## 0. Amendment A1 in short

1. **Stratified quotas, N = 10:** exactly 3 frontier, 3 mid, 4 budget models. A tier is the price tertile of the reference run cost c_m over the eligible frame (§3.2 rule); cut points are reported and configurable (`tier_quotas`, `--tier-cuts`).
2. **MSS_tier** ranks inside a tier: popularity 0.45, capability 0.30, price 0.15 (re-normalised inside the tier band, the tier's own cut points as goalposts, cheaper higher), recency 0.10. Same missing-data rules. The global MSS (§2.6) stays for the rankings page and is reported next to MSS_tier.
3. **Quality floor (Q-quality):** capability ≥ the tier's median; a model without capability evidence only with top-quartile popularity (flag `capability_missing_admitted`); per-tier stepwise relaxation to the 40th, then 33rd percentile, each relaxation reported.
4. **Hard budgets, 15% contingency each:** B-anthropic ≤ 0.85 × $200; B-openrouter ≤ 0.85 × the live OpenRouter credit (`GET /api/v1/credits`, fallback `--openrouter-budget`); B-go per model π_m ≤ 0.85 × its Go monthly limit (weeks needed ⌈π/(0.5·limit)⌉ and runs per 5 h ⌊0.2·limit/run cost⌋ reported). DeepSeek's bound is its standard (peak) list price; models.dev lists no off-peak tier, so no off-peak discount is ever applied.
5. **Route choice is feasibility:** Claude → Anthropic key; else OpenCode Go when served and B-go holds; else OpenRouter within the shared budget; else not selectable (`budget:B-go` / `no_paid_route`).
6. **Algorithm:** stratified lexicographic greedy, tiers frontier → mid → budget, each in MSS_tier order; admit m if caps hold and the exact look-ahead finds a completion meeting the tier quotas, Q-quality and every other quota and budget. The branch-and-bound diagnostic maximises Σ MSS_tier.
7. **Sensitivity** reruns the stratified procedure: Dirichlet / simplex draws of the MSS_tier weights, specification draws, one-at-a-time changes, tier cut points ×0.9/×1.1, floor at the 40th percentile, no floor, and the floor tested on the WebDev rating's upper 95% bound.

**Review fixes (2026-11-09, after the methodology, correctness and UI reviews):**
- E4 now requires text to be the *only* output (image and audio generators such as `gemini-3-pro-image` that also emit text are removed).
- Speed editions of one checkpoint (`xiaomi/mimo-v2.6-pro-ultraspeed`, `z-ai/glm-5.3-flashx`) are curated exclusions (E5 `curated_exclude`), so one model cannot take two slots or count twice in the tier cuts and medians.
- Open/closed labels come from the pinned GA version first; preview and `:free` rows are consulted only when no GA row says anything, and a curated `open_weights:` entry wins (`tencent/hy4` closed, `minimax/minimax-m3.1-flash` open).
- E7 lets only a successor with the evidence E8 needs (list price, and popularity or LMArena interactions) supersede an older model, so a new, unscored release no longer removes its whole line.
- A model excluded at E1–E8 has no MSS and no composite (its component scores stay for the breakdown).
- Robustness is the minimum of the S1 and S3 selection frequencies; picks that leave the set under an S6 floor variant are flagged `floor_sensitive`. τ_b, top-k τ and RBO are computed within each nominal tier and averaged.
- Budgets are checked in whole cents with costs rounded up and caps rounded down; forced picks that break a cap are reported, never raised; the infeasibility diagnosis runs at the most relaxed floor; the Σ-optimal check keeps the forced picks.
- B-anthropic reports the cache-hit share it depends on (`break_even_cache_hit`, and the cost at 0% hits); `select_models --anthropic-cache-hit H` plans the Claude runs at H.
- Snapshots record `code_version` = git commit (`+dirty`) where git can read the tree, plus `src:<sha256>` of `backend/rankings/services/*.py` and `mss_frame.yaml`.

## 1. Target population and eligibility

### 1.1 Population and sampling frame
- **Target population:** LLMs that developers actually use for agentic software development, as of the snapshot date. These are the models whose generated web-app code reaches real projects.
- **Accessible population (the sampling frame):** every model reachable through our three inference routes on the snapshot date, after the canonicalisation and filters below.
- **Sampling design:** a purposive (non-probability) sample drawn by a pre-specified score, with quotas. It is deliberately not representative of all LLMs (Baltes & Ralph 2022, "quota sampling"). Generalisation is to widely used, API-accessible coding models at the snapshot date. See the threats in §7.

### 1.2 Canonicalisation: one row per real model
This step runs over all `LLMModel` rows and is implemented in `frame.py` (§6). Canonical ids are OpenRouter-style `vendor/slug`.

| Step | Rule | Rationale |
|---|---|---|
| K1 drop | Remove routers (`openrouter/*`), aliases (`~vendor/*`, `*-latest`) and `:batch` rows. | Routers and aliases have no fixed weights. The batch API is asynchronous and cannot drive an agent loop. |
| K2 variants | Merge `:free`, `:thinking`, `:nitro`, `:floor`, `:exacto` and any other `:variant` into the base id. | These are the same weights under a different billing or routing mode. A model whose only paid route is missing (free-only) is excluded at E5. |
| K3 dated snapshots | Merge suffixes `-MMDD`, `-YYYYMMDD` and `-YYYY-MM-DD` into the undated id. If no undated row exists, the id with the date stripped becomes the canonical id. The **pinned version** is the newest dated member, or the undated id if there is no dated member. Popularity is summed over all members of the canonical model. | Usage of one model line is split across checkpoints, for example `deepseek-v4.1-flash` and `-1017`. Pinning a version follows Sallou et al. 2024. |
| K4 prerelease markers | Tag ids containing `preview`, `exp`, `experimental`, `alpha`, `beta`, `contributor` or `stealth/`, and local rows named `*-free`, as `prerelease`. A prerelease member merges into its GA sibling when one exists (for example `hy4-preview` into `hy4`). Otherwise it stays its own canonical model and is excluded at E5. | Prerelease builds change silently and may be withdrawn mid-study. |
| K5 local rows | A local endpoint row (`local-opencode-go/*`, `local-anthropic/*`) maps to `metadata.catalog_twin.model_id`, then goes through K2–K3. A local row with no twin uses an override in `backend/rankings/data/mss_frame.yaml` (`twins:`). Otherwise it is reported as `unlinked_local` and excluded. | A route is a way to reach a model, not a separate model. Rows are never merged in the database: this is a selection-time mapping only. |
| K6 routes | Each canonical model lists its route rows (`go`, `anthropic`, `openrouter`) with availability. | Used by E1 and by route assignment after selection. |

Twin gaps found by the 2026-11-09 audit (covered by `mss_frame.yaml: twins:` or a fresh catalog sync):
- `qwen3.8-max` maps to `qwen/qwen3.8-max(-0902)`.
- `minimax-m3.1-flash` maps to `minimax/minimax-m3.1-flash`.
- `kimi-k3.1-pro` and `hy4` have no catalog row. A fresh OpenRouter catalog sync creates `moonshotai/kimi-k3.1-pro` and `tencent/hy4`.
- The Anthropic local rows map to `anthropic/claude-*`.

### 1.3 Hard eligibility filters
A model that fails a filter is excluded with a reason code. It is never given a low score.

| # | Filter | Data | Rationale |
|---|---|---|---|
| E1 route | At least one route row is available now: an OpenRouter catalog row with a paid endpoint (price > 0), an available opencode-go inventory row, or an available anthropic inventory row. | `is_available`, endpoint, inventory refresh | We can only study what we can call, version-pinned, today. |
| E2 tools | Tool calling: OpenRouter `supported_parameters` ∋ `tools`, or `models_dev.tool_call`, or the local row's capability flag. | `metadata.openrouter_*`, `models_dev` | The OpenCode harness drives the model only through tool calls. |
| E3 context | Context window ≥ 128k tokens, taken from the twin for local rows (local rows store 0). | `context_window`, `models_dev.limit.context` | Agentic runs carry 0.66–1.1M prompt tokens over many turns, and individual turns exceed 64k. |
| E4 modality | Text input, and text as the only output (chat). Image input is allowed. | OpenRouter architecture, `models_dev.modalities` (of the pinned GA version first) | Removes embedding, image and audio models, including image generators that also emit text. |
| E5 general availability | Not a curated exclusion (`mss_frame.yaml: exclude:`, each with a reason; e.g. speed editions of an already listed checkpoint). Not free-only. Not deprecated (`models_dev.status`, or an OpenRouter `expiration_date` within 60 days). Not prerelease (K4) unless `--include-previews` or a YAML `include:` entry with a written reason. | sync metadata | Results must refer to a stable, purchasable model. Free tiers are rate-limited (429) and promotional. |
| E6 recency | Released on or after snapshot − 365 days. The date is the release of the pinned version: models.dev first-party `release_date`, else OpenRouter `created`. | `models_dev`, `openrouter_created` | The study is about current practice. Older lines are covered by their successors. |
| E7 line dedup | Keep the newest model per (vendor, line). The line key is the slug with version tokens removed (`claude-opus-5.5` → `claude-opus`, `glm-5.3-flash` → `glm-flash`, `gpt-6-luna` → `gpt-luna`, `kimi-k3.1-pro` → `kimi-pro`). Size tokens such as `550b` and `a55b` are kept. Exceptions go in `mss_frame.yaml: lines:`. The computed key is shown in the output. Only a successor that has the evidence E8 needs (a list price, and popularity or LMArena interactions) supersedes; a newer release without it is kept to fail E8 on its own. | catalog ids | Otherwise successive versions of one product (`gpt-5.6-luna` and `gpt-6-luna`, `glm-5.2` and `glm-5.3`) compete for slots and dilute diversity. |
| E8 evidence | Popularity P and price C are both present (§2.6). Otherwise the model is listed as `insufficient_evidence`. | — | Popularity and price are the primary criteria. Selecting on secondary evidence alone would contradict the design. |

The output includes a PRISMA-style flow: the row count after each step K1–K6 and E1–E8, with the excluded ids and reasons per step.

---

## 2. MSS v2 components
Each component is a score s_k in [0, 1]. Higher always means "more preferred".

Notation:
- m is a canonical model.
- W is the window: the 28 complete UTC days ending the day before the snapshot (for 2026-11-09: 2026-10-12 to 2026-11-08).
- All prices are in USD per 1M tokens.

### 2.1 Popularity P (weight 0.40, primary)
**Source:** OpenRouter Data API (`https://openrouter.ai/api/v1/`, CC BY 4.0, cite as "Source: OpenRouter (openrouter.ai/rankings), as of <meta.as_of>"). Auth is any OpenRouter key: `UserApiCredential` provider `openrouter`, read with `.get_secret()`, never logged, and sent only to openrouter.ai.

**Fetching:**
- `datasets/rankings-daily?start_date&end_date`, fetched in 7-day chunks (4 calls per 28 days, well within 30/min and 500/day). Each response gives `{date, model_permaslug, total_tokens}` for the top 50 per day plus an `other` row.
- Permaslugs map to catalog ids through `/api/v1/models[].canonical_slug` (public), then to canonical m through K2–K4.
- `:free` permaslug rows are left out of the model's own numerator, because the study population is paid use. They stay in the daily market total.

**Exposure and censoring:**
- D_m is the set of days in W on or after the first OpenRouter `created` date of any member of m. Using it avoids penalising a model released mid-window.
- T_d is the total of all rows on day d, including `other`.
- L_d is the smallest listed (non-`other`) model's tokens on day d: the detection limit for that day.
- Let x_{m,d} be the tokens of model m on day d. It equals the observed value if m is listed that day. Otherwise it is **left-censored** and imputed as L_d/2 (substitution rule for skewed left-censored data; Hornung & Reed 1990).
- The output records `days_available`, `days_listed`, `days_censored`, and the flags `short_exposure` (|D_m| < 7) and `censored` (days_censored > 0).

**Volume share** (usage by tokens):

  vol_m = mean over d ∈ D_m of x_{m,d} / T_d

**Value share** (usage by spend):

  val_m = mean over d ∈ D_m of (x_{m,d} · u_m) / S_d

- u_m = c_m / 0.936M, the USD per token in the reference basket (§2.2).
- S_d = Σ_j t_{j,d}·u_j over the models listed on day d that have a price.
- The Data API has no spend data, so spend is estimated from list prices. The OpenRouter web spend ranking is ToS-restricted and is used only for a one-off manual cross-check, never in code.

**Raw value:**

  P_raw = ½·log10(vol_m) + ½·log10(val_m)

This is the geometric mean of the two standard market-share definitions (volume and value).

**Why the geometric mean:**
- Volume share favours cheap models: cheap, high-throughput pipelines such as a single agent app can dominate tokens. Value share favours expensive models.
- On today's 27-candidate frame:
  - Token volume falls with run cost (slope −0.28 decades per decade, r = −0.22).
  - The geometric mean has a tilt of +0.22 toward price, which is smaller in magnitude than the token-only tilt of −0.28.
  - Price enters the MSS explicitly and only once, through C.
- Both pure definitions are tested in the specification sensitivity analysis (S3).

**Transform:** P = clip((P_raw + 4) / 3, 0, 1). This sets fixed goalposts at a share of 0.01% (score 0) and 10% (score 1), on a log scale because usage is heavy-tailed. On 2026-11-09 the frame spans about 10^-4.6 to 10^-0.75 (deepseek-v4.1-flash and claude-opus-5.5 reach the top).

**Fallback (proxy):**
- This applies only when m has no OpenRouter presence at all (|D_m| = 0). Being off the top-50 list is not that case: it is censoring.
- P = quantile mapping of log10(LMArena interactions) to the P distribution among frame models that have both measures. LMArena interactions = webdev `vote_count` + text_coding `vote_count` + agent `session_count`, summed over effort variants.
- The model is flagged `popularity_source = "lmarena_proxy"`. No candidate on 2026-11-09 needs this.
- LMArena counts are not used in the main score. Arena battles assign models through the platform's sampling policy, which differs by provider (Singh et al. 2025), so the counts measure exposure on the platform, not developer demand.

**Convergent validity (reported, not scored):**
- Spearman ρ between P and log LMArena interactions.
- Spearman ρ between P and log Hugging Face 30-day downloads (open-weight models only, `metadata.huggingface.downloads`).
- Presence of m in the top 10 of each `classifications/task` `code:*` tag (7-day). The licensed API has per-model coding shares only for these top-10 lists, so they serve as a coding-relevance check.
- A low ρ is reported as a validity threat.

### 2.2 Price C (weight 0.30, primary)
**List price** p = (in, out, cache_read cr, cache_write cw), resolved in this order:
1. The **first-party provider record in models.dev** (`https://models.dev/api.json`), using the vendor's own provider key from `mss_frame.yaml: vendors:`:
   - anthropic → anthropic, openai → openai, google → google, x-ai → xai, deepseek → deepseek, z-ai → zai, moonshotai → moonshotai, qwen → alibaba, minimax → minimax, xiaomi → xiaomi, tencent → tencent-tokenhub, stepfun → stepfun, meituan → longcat, mistralai → mistral.
   - The record is matched on `canonical_model_id`, else on the served key.
2. The **vendor's own OpenRouter endpoint** (`/api/v1/models/{id}/endpoints`, `provider_name` from `vendors:`), with the discount undone (price / (1 − discount)).
3. The **median over OpenRouter endpoints** quantised at fp8 or better, with the discount undone.

**Never** use `/api/v1/models[].pricing`. It is the cheapest endpoint, often quantised or discounted:
- glm-5.3 shows 0.07/1.4 there against a list price of 1.4/4.4.
- kimi-k3 shows 0.19/16 against 3/15.
- hy4 shows a 50% discount.

**Further price rules:**
- For an OpenCode Go model, the Go price (models.dev provider `opencode-go`) is stored as well. It equals the first-party list price for every Go model checked. The score always uses the market (first-party) price.
- Tiered prices (long context, peak or off-peak) use the base tier. The tiers are stored.

**Reference run cost** (one OpenCode app run):

  c_m = P_ref·[(1−h)·max(in, cw) + h·cr'] + O_ref·out

- P_ref = 0.9 (M prompt tokens), O_ref = 0.036 (M output tokens), h = 0.8. cr' = cr if a cache-read price is listed, else in.
- max(in, cw): uncached prompt tokens are billed at the cache-write price where the provider bills writes explicitly (Anthropic at 1.25× input; qwen 0.2 > 0.15). A listed "cache_write" that is below input is a storage fee, not a write price (Gemini 0.1875 vs 2.0), so input applies.
- **Basket provenance.** The basket is the midpoint, rounded, of the measured OpenCode agentic profiles on this platform (2026-11 runs):
  - deepseek-v4.1-flash: 0.66M prompt / 27k output, n = 14 completed runs.
  - qwen3.8-flash: 1.10M prompt / 44.5k output, n = 12.
  - h = 0.8 is the middle of the 70–95% hit ratio implied by deepseek's measured cost ($0.047/run against $0.115 uncached).
- **Why one common basket.** One basket for every model works like a price index: it separates price from verbosity. Verbosity and cache behaviour are model behaviours the study measures, so they must not feed back into the selection (that would be circular).

**Transform:** C = clip(1 − (log10 c_m + 2) / 3, 0, 1). The fixed goalposts are $0.01 (score 1) and $10 (score 0) per run. The frame spans $0.037 to $4.8.

**Planning cost** (feasibility only, never part of the score):
- π_m = 30 runs × 1.25 retry factor × c_m(P = 1.1, O = 0.045, h = 0.8). This is the more verbose measured profile.
- π⁰_m uses h = 0 and is reported as a pessimistic bound.
- For OpenCode Go models, the Go monthly per-model limit comes from `mss_frame.yaml: go_limits:` (source https://opencode.ai/v2/docs/console/go/, fetched 2026-11-09). It is a feasibility flag, not a score input.

### 2.3 Capability Q (weight 0.20, secondary)
- **Source:** the LMArena HF dataset `lmarena-ai/leaderboard-dataset`, config `webdev`, category `overall`, split `latest` (CC BY 4.0). It is imported by the existing `import_external_covariates` into `metadata.lmarena.categories.webdev`.
- **Entry choice:** the entry that matches the configuration we call (the provider-default reasoning effort), using the existing map rules (`match: exact|variant|assumed`).
- **Transform:** Q = clip((rating − 1450) / 400, 0, 1). The rating is a Bradley–Terry log-odds scale, so it stays linear with no log. The goalposts are 1450 and 1850; the frame spans 1484 to 1818. A 400-point gap means 10:1 odds.
- `rating_lower` and `rating_upper` are stored and used in S3.
- **Why WebDev:** it is the only capability signal that is fresh (published 2026-11-08), covers every candidate once the map is extended, and matches the study task (web apps).
- **Why weight only 0.20:**
  - Arena ratings are skewed toward large providers (Singh et al. 2025).
  - Capability is a secondary criterion here: the empirical study itself measures quality.
- **Not used in the score:**
  - Artificial Analysis coding/agentic index (via the OpenRouter Data API `benchmarks`) covers only part of the frame.
  - Epoch ECI and CursorBench are stored as covariates. Spearman ρ between Q and each of them is reported as convergent validity.
  - SWE-bench Verified and Terminal-Bench are stale and not used.
- **Missing Q** (for example qwen3.7-plus, longcat-2.0, nemotron-3-ultra): the component is omitted and the weights renormalised, and the model is flagged `capability_missing`. S3 tests a pessimistic imputation (the frame's 25th percentile).

### 2.4 Recency R (weight 0.10, minor)
- R = clip(1 − age_days / 365, 0, 1). age = snapshot date − release date of the pinned version, using the same source as E6.
- Rationale:
  - The population is current practice.
  - Newer models stay available longer, both during the study and for a replication window.
  - R partly offsets popularity's lag: usage builds up after release, which the exposure adjustment only partly corrects.

### 2.5 Criteria deliberately not in the score
These are hard filters or quotas instead, because they are properties of the set (diversity) or of feasibility, not of a model's relevance:
- open versus closed weights
- vendor
- origin
- route
- budget
- context length
- tool calling

The old "accessibility" labels (license, stability, documentation) are dropped. They were guessed defaults, and their real content is now covered by E1–E5. Old cost-efficiency counted benchmarks twice and added a context bonus; plain price replaces it.

### 2.6 Aggregation, coverage and weights

  MSS_m = Σ_{k ∈ present(m)} w_k·s_k / Σ_{k ∈ present(m)} w_k

  coverage_m = Σ_{k ∈ present(m)} w_k

- **Weights:** w = (P 0.40, C 0.30, Q 0.20, R 0.10).
- A model needs P and C present, which by itself implies coverage ≥ 0.70. Otherwise it is excluded at E8 as insufficient evidence.
- Coverage, the missing components and flags (`censored`, `short_exposure`, `lmarena_proxy`, `capability_missing`) are shown and stored.

**Justification of the weights:**
- P + C = 0.70 follows the user's statement that popularity and price are the main criteria.
- P > C because relevance (what is used) defines the population. Price is also enforced through tier quotas and budget constraints, so it does not need the larger weight.
- Q = 0.20 keeps an independent capability signal, so the sample is not only cheap and popular models. It is modest because of arena bias.
- R = 0.10 is a tie-shaper.
- The weight choice is tested by SMAA with no stated preferences (S2).

**Variants used only in sensitivity:**
- Percentile normalisation: mid-rank percentile within the eligible frame, ties averaged.
- Popularity as volume share only or value share only.

The weighted sum with fixed bounds was chosen over TOPSIS because TOPSIS suffers rank reversal when the catalog changes (García-Cascales & Lamata 2012) (OECD/JRC 2008).

---

## 3. Selection procedure (N = 10 by default)

> **Amendment A1 applies throughout this section** (stratified quotas, within-tier MSS, quality floor, hard budgets, route choice inside feasibility). The pre-A1 text is kept where A1 does not change it; the rows and steps A1 replaces are marked.

### 3.1 Order
Models are ranked by the key (−round(MSS, 4), −P, −C, route_rank, canonical_id), with route_rank go=0, anthropic=1, openrouter=2. This is deterministic. The route rank is only a tie-break (the user prefers OpenCode Go). The ascending id gives the final order.

### 3.2 Constraints
Defaults are in `SELECTION_DEFAULTS` and every one can be overridden.

| Code | Constraint | Default | Rationale |
|---|---|---|---|
| Q-vendor | At most n models per vendor (OpenRouter author slug) | 2 | Diversity of vendors and training pipelines. |
| Q-vendors | At least n distinct vendors | 6 | Avoids clustering. |
| Q-open | At least n open-weight models | 3 | LLM guidelines (Baltes, Wagner et al. 2025) require at least one open model. Open-weight models are about a third of usage (Aubakirova et al. 2026). |
| Q-closed | At least n closed-weight models | 3 | Keeps the open vs closed contrast estimable. |
| Q-tier (A1) | **Exactly** n models per price tertile of c_m over the eligible frame (rank by c_m: budget = first ⌊n/3⌋, frontier = last ⌊n/3⌋, mid = the rest) | 3 frontier / 3 mid / 4 budget | Stratification by price; replaces the pre-A1 "at least 2 per tertile". |
| Q-claude | At least n Claude models (Anthropic-key route) | 1 | User request. Anthropic leads programming spend (Aubakirova et al. 2026). |
| B-anthropic (A1) | Σ π_m over Claude picks ≤ 0.85 × budget | 0.85 × $200 | User's Anthropic balance; 15% contingency. |
| Q-quality (A1) | Capability ≥ the tier's median; missing capability only with top-quartile popularity; relaxes to the 40th, then 33rd percentile per tier | on | "All picks good." |
| B-go (A1) | Per model: π_m at Go prices ≤ 0.85 × its Go monthly limit × study months | Go ($10) plan limits | Otherwise the model falls back to OpenRouter. |
| B-openrouter (A1) | Σ π_m over picks routed to OpenRouter ≤ 0.85 × available credit | live `GET /api/v1/credits` (fallback `--openrouter-budget`) | Shared knapsack, checked inside the look-ahead. |
| Q-origin (off) | At least n US-origin and at least n China-origin models | 0 / 0 | Optional; the mix is always reported. |

Under A1 the route is part of feasibility (§3.6) and still never affects the score.

### 3.3 Algorithm: lexicographic greedy with an exact feasibility look-ahead

```
S = []
for m in order:
    if len(S) == N:
        break
    if caps_ok(S + [m]) and feasible(S + [m], rest_after(m)):
        S.append(m)
```

- `caps_ok` checks the upper bounds: the vendor cap and the budgets.
- `feasible(S', R)` is exact: there exists T ⊆ R with |S'| + |T| = N such that S' ∪ T meets every cap and quota. It is a depth-first search with count-based pruning: deficit per quota ≤ remaining slots, and enough suppliers per quota in R. With 30–60 candidates it should take well under 5 ms per call. (The scratch prototype took about 13 ms per call without pruning.)
- **Property:** S is the lexicographically greatest feasible set with respect to the order. The best-ranked model is always in, unless no feasible set contains it, and so on down the order. This is easier to explain than maximising Σ MSS.
- **Diagnostic:** an exact branch-and-bound maximises Σ MSS under the same constraints. Its set and the difference from S are reported. Any difference is flagged for the researcher.
- **If no feasible set exists:** stop with an error that names the quotas making it infeasible. To find them, rerun with each quota relaxed in turn. The diagnosis (single relaxations, minimal budgets) is computed at the most relaxed quality floor (33rd percentile), because once a budget is raised the procedure relaxes the floor as far as needed; the level is reported as `diagnosed_at_floor_percentiles`.
- **Forced picks** (`--force`) that break a tier quota, the vendor cap or a budget are reported as infeasible with `forced_violation`; the Σ-optimal diagnostic includes the forced picks.
- **Budget arithmetic** is in whole cents: each pick's π is rounded up and each cap rounded down, so a hard budget is never overshot by rounding.

### 3.4 Rationale output
- **For each pick:**
  - its MSS rank
  - its components and coverage
  - `admitted_by`: `rank` if every higher-ranked model was taken or blocked only by caps, otherwise `quota:<code>`
  - its price tier, vendor, open-weight status and origin
  - the planned route and planning cost
- **For each eligible model ranked above the last pick but not selected:**
  - `blocked_by`: `cap:Q-vendor(<vendor>)`, `budget:B-anthropic`, or `lookahead:<quota codes>`
  - The look-ahead codes are found by rerunning `feasible` with each quota relaxed.
- **For each constraint:** `satisfied` and `binding`. A constraint is binding if relaxing it alone changes S.
- **Alternates:**
  - (a) The next 5 eligible models by MSS, each with its `blocked_by`.
  - (b) A per-pick replacement: rerun the selection with that pick excluded and report which model enters. This is the swap menu for confirmation.

### 3.5 Tie-breaks
These are fully determined by §3.1. The output says whether any two adjacent models in the order are tied on MSS at 4 decimal places.

### 3.6 Route assignment (A1: decided per model before the greedy, part of feasibility)
- Claude (vendor anthropic) → the Anthropic key. This is the user's instruction, and it applies even to claude-haiku-5-5, which Go also serves.
- Other models → OpenCode Go when served (stored twin) and π_m ≤ Go monthly limit × `study_months` (default 1).
  - If π_m exceeds that limit, the model is flagged `go_limit_exceeded`. Its options are OpenRouter or spreading the runs over more months.
  - The Go 5-hour and weekly caps (20% and 50% of the monthly limit) are scheduling constraints, reported as runs per 5 h.
- Otherwise → OpenRouter.
- The pinned version is recorded for each route:
  - OpenRouter: the dated permaslug (`canonical_slug`).
  - Anthropic: the dated API id.
  - Go: the served name, with a twin note saying the identity is assumed.

---

## 4. Sensitivity analysis
This lives in `backend/rankings/services/sensitivity.py`, which keeps the existing severity-weight functions. Seed 20261109 with `random.Random`; there is no numpy or scipy in the dependencies. Dirichlet draws are made with `rng.gammavariate`. Every draw reruns the full scoring and the §3 selection with the same eligible frame and constraints.

| Run | Draws | What varies |
|---|---|---|
| S1 (primary, pre-registered) | 10,000 | Weights ~ Dirichlet(α = 50·w). For w = 0.40 the SD is about 0.07. |
| S2 (SMAA, no preference) | 2,000 | Weights ~ Dirichlet(1, 1, 1, 1): uniform on the simplex (Lahdelma et al. 1998). |
| S3 (specification) | 2,000 | Nominal weights. Each draw picks independently: normalisation ∈ {goalposts, percentile}; popularity ∈ {geo, volume, value}; h ~ U(0.5, 0.95); censored-day imputation ∈ {L/10, L/2, L}; missing Q ∈ {renormalise, frame P25}; LMArena rating ~ N(rating, (upper − lower)/3.92). |
| S4 (one at a time) | 12 | Leave out each component (4 runs, renormalised). Each weight ×0.5 and ×1.5, renormalised (8 runs). Reports the picks that change. |
| S5 (tier cuts) | 4 | Cut points ×0.9/×1.1 (both, and each one alone). |
| S6 (floor) | 3 | Floor at the tier's 40th percentile; no floor; the floor tested on the WebDev rating's upper 95% bound (a model whose interval reaches the median passes). |

**Metrics, for each run:**
- Per model: selection frequency, an SMAA-style acceptability index. It covers every eligible model, not only the picks.
- Mean and 5th percentile of Jaccard |S ∩ S₀| / |S ∪ S₀| against the nominal set S₀.
- Kendall τ_b of MSS_tier between the nominal and each draw (reuse `backend.statistics.services.inference.kendall_tau_b`), τ over the nominal top 10 of each tier (reuse `rankings.services.sensitivity.kendall_tau`), and RBO with p = 0.9, extrapolated (Webber et al. 2010, eq. 32; `rank_biased_overlap`). All three are computed **within each nominal price tier and averaged over the tiers**: MSS_tier values are on different scales across tiers, and over the tier-major order RBO would put about 83% of its weight on the frontier tier. Report the mean and 5th percentile.

**Robustness classes** (from the minimum of the S1 and S3 frequencies; S1 alone when S3 did not run):
- A pick with frequency ≥ 0.70 is `robust`.
- 0.40 to 0.70 is `borderline`: the researcher decides and the decision is documented in the confirmation note.
- Below 0.40 is `fragile`, flagged.
- Non-picks with frequency ≥ 0.40 are listed as `contenders`.
- S1 perturbs only weights and cannot move the quality floor; S3 perturbs the WebDev ratings the floor is built on, so the class takes the weaker of the two. S2 is reported but does not change the class.
- A pick that leaves the set under any S6 floor variant is flagged `floor_sensitive`.

**Cost:** about 14,000 selections at ≤ 5 ms each takes about 1 minute. This runs inside `select_models` and when a snapshot is created. Interactive API previews cap the draws (S1 1,000, S2 500, S3 0) and label the result as such.

---

## 5. Reproducibility

### 5.1 Per-model inputs
Each input is stored in `LLMModel.metadata` on the catalog row of the canonical model.

**`metadata["popularity"]["openrouter"]`** is new and written by `import_popularity`:

```json
{"source": "OpenRouter Data API datasets/rankings-daily",
 "license": "CC BY 4.0",
 "citation": "Source: OpenRouter (openrouter.ai/rankings), as of 2026-11-09T09:22Z",
 "as_of": "...",
 "fetched_at": "...",
 "window": {"start": "2026-10-12", "end": "2026-11-08", "days": 28},
 "permaslugs": ["deepseek/deepseek-v4.1-flash-20260910", "..."],
 "days_available": 28, "days_listed": 28, "days_censored": 0,
 "tokens_listed": 0,
 "daily": [{"date": "...", "tokens": 0, "total": 0, "limit": 0, "censored": false}],
 "volume_share": 0.0,
 "code_tags": {"code:general_impl": 0.306},
 "source_snapshot_ids": [1, 2, 3, 4]}
```

**`metadata["list_price"]`** is new and written by `import_list_prices`:

```json
{"input": 0, "output": 0, "cache_read": 0, "cache_write": null,
 "unit": "USD per 1M tokens",
 "source": "models.dev:zai | openrouter-endpoint:Z.AI | openrouter-endpoints-median",
 "fetched_at": "...", "discount_undone": false, "tiers": null,
 "go": {"input": 0, "output": 0, "cache_read": 0,
        "monthly_limit_usd": 15, "limit_source": "...", "limit_fetched_at": "2026-11-09"},
 "openrouter_cheapest": {"input": 0, "output": 0}}
```

**Existing keys:**
- `metadata["lmarena"]` already exists. It needs a refresh to the 2026-11-08 publish and the extended map.
- `epoch`, `huggingface` and `models_dev` are unchanged.

Add `popularity` and `list_price` to `ENRICHMENT_KEYS` in `backend/llm_models/services/enrichment.py`, so OpenRouter re-syncs keep them.

### 5.2 Raw responses
New model **`rankings.SourceSnapshot`** with fields:
- `source`: `openrouter_rankings_daily`, `openrouter_task_classifications`, or `openrouter_models_index`
- `fetched_at`, `as_of`, `window_start`, `window_end`
- `params` JSON, `payload` JSON (the full response body), `sha256`
- `license`, `citation`

It is append-only.

### 5.3 Selection snapshot
**Extend `RankingSnapshot`** (it is currently unused):
- `kind` (`rankings` | `selection`)
- `version` = `"mss-2.0"`
- `params` JSON:
  - weights, goalposts, basket, planning profile
  - constraints, N, seed, draw counts
  - `include_previews`, overrides
- `inputs_sha256`
- `status` (`draft` | `confirmed` | `superseded`)
- `confirmed_at`, `confirmed_by` (FK user, null)
- `confirmation` JSON: final model list, overrides `[{replace, with, reason}]`, notes
- `created_by`
- `code_version`: `git:<sha12>[+dirty]` (env `GIT_SHA`, else `git rev-parse HEAD` and `git status`; the container cannot read the worktree's git dir) plus `src:<16 hex>`, the sha256 of `backend/rankings/services/*.py` and `data/mss_frame.yaml`, which pins the selection code even in an uncommitted tree.

**`payload`** contents:
- `as_of`
- `sources` (name, url, license, as_of, fetched_at, window)
- `flow` (PRISMA counts and excluded ids with reasons per step)
- `candidates`: one entry per canonical model, with:
  - members and routes
  - eligibility and reason
  - raw inputs copied with provenance
  - component scores, coverage, flags
  - MSS, rank, c_m, π_m, tier, vendor, open-weight status, origin
- `selection` (picks with rationale)
- `blocked`
- `alternates`
- `constraints` (satisfied and binding)
- `sum_optimal`
- `sensitivity` (S1–S4 tables)
- `validity` (the Spearman ρs)

**Replay:** `replay_selection(snapshot)` recomputes everything from the stored `payload.candidates` raw inputs and `params`, with no network or database inputs, and must reproduce the stored selection and scores exactly. This is unit-tested.

---

## 6. Methods paragraph (for the thesis)

> Models were selected by stratified purposive quota sampling (Baltes & Ralph, 2022) from a frame of large language models reachable through our three inference routes (OpenCode Go, the Anthropic API and OpenRouter) on 9 November 2026. Variants of one model (dated snapshots, billing variants, aliases, speed editions of the same checkpoint, route-specific copies) were collapsed to, or excluded in favour of, a single canonical model with a pinned version (Sallou et al., 2024). Hard eligibility filters required tool calling, a context window of at least 128k tokens, text input and text-only output, general availability (no free-only, deprecated or preview releases), release within the preceding 12 months, one model per vendor product line, and available popularity and price evidence. The frame was stratified into price tertiles of the cost of one reference agentic generation run (0.9M prompt and 36k output tokens, 80% cache hits, first-party list prices), and exactly three frontier, three mid-price and four budget models were drawn. Within each stratum, models were ranked by a weighted linear composite (OECD/JRC, 2008): popularity (0.45), coding capability (0.30), price re-normalised within the stratum (0.15) and recency (0.10). The weights and selection rules were set on 9 November 2026 after a provisional check on seven days of data and before the final 28-day data were imported; the provisional check is reported. Popularity was the geometric mean of a model's share of tokens and its estimated share of spend on OpenRouter over 28 days, adjusted for exposure and with days outside the published top-50 treated as left-censored (OpenRouter Data API, CC BY 4.0); combining volume and value shares limits the price bias of either measure (Aubakirova et al., 2026). Capability was the LMArena WebDev rating (Chiang et al., 2024), used with moderate weight given known arena biases (Singh et al., 2025). Popularity, global price, capability and recency were normalised against fixed bounds, which avoids rank reversal when the catalog changes (García-Cascales & Lamata, 2012); the stratum cut points, the within-stratum price band and the capability floor, by contrast, are relative to the frame, so adding or removing a model can move others between strata or across the floor, and we report how the selection changes when the cut points move by ±10% and under alternative floors. Missing criteria were omitted and the weights renormalised rather than scored as zero. Models were admitted in stratum order (frontier, mid, budget) and score order whenever an exact look-ahead showed that the remaining quotas could still be met: a capability floor (at least the stratum median, relaxed stepwise to the 40th and 33rd percentile only if a stratum could not be filled; a model without a capability rating passed only if its popularity was in the frame's top quartile, and we flag every such pick), at most two models per vendor, at least six vendors, at least three open-weight and three closed-weight models (Baltes, Wagner et al., 2025), at least one Anthropic model, and hard per-route budgets with a 15% contingency (the Anthropic balance, the OpenRouter credit and the per-model OpenCode Go limits). Selection stability was assessed by Monte Carlo perturbation of the within-stratum weights (Dirichlet draws centred on the nominal weights and uniform simplex draws; Saisana et al., 2005; Lahdelma et al., 1998), by specification draws that also resample each capability rating within its confidence interval, and by moving the stratum cut points (±10%) and the capability floor (40th percentile, none, and a floor tested on the rating's upper confidence bound). We report each model's selection frequency, taking a pick's robustness from the weaker of the weight and specification runs, the Jaccard overlap with the nominal set, and Kendall's τ_b (Kendall, 1938) and rank-biased overlap (Webber et al., 2010) computed within strata. Because the sample is purposive and OpenRouter traffic excludes first-party and self-hosted use, findings generalise to widely used, API-accessible coding models at the snapshot date rather than to all LLMs (Wohlin et al., 2012).

**Threats to record:**
- OpenRouter under-represents vendors with heavy first-party traffic (Anthropic, OpenAI, Google).
- Models without a WebDev rating enter the floor on popularity alone (`capability_missing_admitted`); evidence can then only hurt a model near the median. Import the LMArena rows of every pick before confirming.
- The Anthropic budget is planned at an 80% prompt-cache hit share measured on DeepSeek runs; no Anthropic run has been measured. `budget_use.anthropic.break_even_cache_hit` gives the share below which B-anthropic fails.
- Spend share is estimated from list prices, not observed.
- Arena ratings depend on the reasoning effort.
- OpenCode Go model identity is assumed through the twin mapping.
- Snapshot dependence: a different date can give a different frame.

### References
- Baltes & Ralph 2022, EMSE 27(4):94, arXiv 2002.07764
- Baltes, Wagner et al. 2025, arXiv 2508.15503 (llm-guidelines.org)
- Sallou, Durieux & Panichella 2024, ICSE-NIER, doi:10.1145/3639476.3639764
- OECD/JRC 2008, *Handbook on Constructing Composite Indicators*
- Saisana, Saltelli & Tarantola 2005, JRSS-A 168(2):307–323
- Lahdelma, Hokkanen & Salminen 1998, EJOR 106(1):137–143
- García-Cascales & Lamata 2012, Math. Comput. Modelling 56:123–132
- Webber, Moffat & Zobel 2010, ACM TOIS 28(4)
- Kendall 1938, Biometrika 30:81–93
- Aubakirova et al. 2026, arXiv 2601.10088
- Chiang et al. 2024, arXiv 2403.04132
- Singh et al. 2025, arXiv 2504.20879
- Wohlin et al. 2012, Springer, doi:10.1007/978-3-642-29044-2
- Added by this spec and not checked online this session:
  - Hornung & Reed 1990, "Estimation of average concentration in the presence of nondetectable values", Appl. Occup. Environ. Hyg. 5(1):46–51 (L/2 substitution).
  - UNDP Human Development Report technical notes (fixed "goalposts" normalisation; optional citation for §2).
