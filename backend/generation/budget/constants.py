"""Estimator and ledger constants (spec sections 4.4 and 4.5), calibrated from priors where noted.

Every number the estimator uses lives here, so a change is one reviewed diff. The heuristic seeds come
from live v2 data (OpenCode about 2.1M tokens over 64 calls, mini-swe-agent about 1.7M over 48, Aider
about 52k over 3); ``refresh_cost_priors`` replaces them with empirical priors once a key has ``n >= 5``
runs (``PRIOR_MIN_N``).
"""

from __future__ import annotations

from typing import Final

# --- 4.4 step 1: prior lookup -----------------------------------------------------------------------------
PRIOR_MIN_N: Final = 5  # a CostPrior key is used only with at least this many runs
PRIOR_WINDOW_DAYS: Final = 120  # history window of refresh_cost_priors
WILDCARD: Final = "*"

# --- 4.4 step 2: agentic heuristic --------------------------------------------------------------------------
# Expected main-class calls per run, per harness (R = min(run.max_llm_requests, r0[harness])).
R0: Final[dict[str, int]] = {
    "opencode": 45,
    "claude_code": 40,
    "codex": 40,
    "goose": 35,
    "qwen_code": 40,
    "gemini_cli": 40,
    "mini_swe_agent": 50,
    "aider": 6,
}
R0_DEFAULT: Final = 40  # an unknown or fake harness
# Tokens a harness adds to every prompt (native system prompt + tool schemas), per harness.
HARNESS_OVERHEAD: Final[dict[str, int]] = {
    "opencode": 9_000,
    "claude_code": 14_000,
    "codex": 8_000,
    "goose": 6_000,
    "qwen_code": 9_000,
    "gemini_cli": 9_000,
    "mini_swe_agent": 2_500,
    "aider": 3_000,
}
HARNESS_OVERHEAD_DEFAULT: Final = 6_000
PROMPT_GROWTH_PER_CALL: Final = 1_800  # g: history growth per call
OUTPUT_PER_CALL_CAP: Final = 900  # o_avg = min(call.max_output_tokens, 900)
# Expected reasoning tokens per call by effort (r_avg); a max_tokens budget uses REASONING_BUDGET_SHARE of it.
R_AVG: Final[dict[str, int]] = {
    "none": 0,
    "minimal": 100,
    "low": 400,
    "medium": 1_200,
    "high": 3_000,
    "xhigh": 5_000,
    "provider_default": 1_200,
}
REASONING_BUDGET_SHARE: Final = 0.6
CACHE_HIT_SHARE: Final = 0.8  # h when caching is on and the model caches
DEFAULT_CACHE_DISCOUNT: Final = 0.75  # d when the cached price is unknown
AGENTIC_P10: Final = 0.4
AGENTIC_P90: Final = 2.2
AUX_SHARE_OF_MAIN: Final = 0.1  # auxiliary calls (subagent/compaction) as a share of main-call cost

# --- 4.4 steps 3-4: slot ----------------------------------------------------------------------------------------
SLOT_OUTPUT_FACTOR: Final = 1.6  # OUT = min(slot cap, 1.6 x est_tokens(original) + 150) + r_avg
SLOT_OUTPUT_SLACK: Final = 150
# E2E-15: reasoning tokens to expect from a reasoning model whose reasoning the call does not bound. A
# single-shot slot call sends no reasoning parameter (``slot/executors/single_shot.build_request``), so the
# provider default applies whatever ``reasoning`` the policy says; the slot pilot saw qwen3.8-flash spend 5051
# reasoning tokens of 5457 and deepseek-v4.1-flash a full 8192 cap. ``slot_output_cap_tight`` uses at least this.
R_UNBOUNDED_REASONING: Final = 5_000
SLOT_P10: Final = 0.7
SLOT_P90: Final = 1.6
SLOT_AGENT_R0_FACTOR: Final = 0.5
BATCH_TIER_FACTOR: Final = 0.5  # serving_tier=batch halves the price
FLEX_TIER_FACTOR: Final = 1.0  # flex is the floor price; no discount is assumed

# --- token estimate -----------------------------------------------------------------------------------------------
CHARS_PER_TOKEN: Final = 3.6  # ceil(chars / 3.6) when tiktoken is unavailable

# --- time ---------------------------------------------------------------------------------------------------------
# Expected wall seconds of an agentic run by task size bucket (until priors exist); p90 = WALL_P90 x expected.
WALL_S_BY_SIZE: Final[dict[str, float]] = {"S": 420.0, "M": 780.0, "L": 1_380.0, "XL": 2_100.0}
WALL_S_DEFAULT: Final = 780.0
WALL_P90: Final = 1.8
SLOT_CALL_WALL_S: Final = 40.0
# Evaluation machine minutes per run by eval level (build, start + tester, probes, SAST), excluding reuse.
EVAL_MINUTES_BY_LEVEL: Final[dict[str, float]] = {
    "none": 0.0,
    "static": 1.0,
    "build": 3.0,
    "functional": 5.0,
    "security": 7.0,
    "full": 12.0,
}

# --- study cap "auto" (4.2): round-up(p90_total x factor) ------------------------------------------------------
AUTO_CAP_FACTOR: Final[dict[str, float]] = {"frugal": 1.1, "standard": 1.25, "generous": 1.5}
AUTO_CAP_FACTOR_CUSTOM: Final = 1.25
TOKENS_BASIS_RUN_CAP_FACTOR: Final = 1.5  # budget_basis=tokens: run cap = max(policy, p99 x 1.5)
P99_OVER_P90: Final = 1.6  # p99 from p90 when no prior gives it

# --- 4.5 ledger and live spend --------------------------------------------------------------------------------------
BUDGET_LIVE_POLL_S: Final = 10.0  # x ceil(running / 8)
LIVE_POLL_FANOUT: Final = 8
