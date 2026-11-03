"""Generation settings: sample generation, agent sandboxes, harnesses and the executor.

``config/settings/base.py`` star-imports this module. There is deliberately no ``__all__``: mypy and
django-stubs only see star-imported settings without one. Helpers that must not shadow names in
``base.py`` are private (read the environment through ``_env``).

Ownership (docs/design/SAMPLE_GEN_V3.md §12.2): each work package edits only its own block, which runs
from its ``# --- WP-xx ---`` marker to the next marker. A setting another WP needs is asked for through
``docs/design/SAMPLE_GEN_V3_CHANGES.md``.

Only deployment and machine facts live here. Study defaults (budgets, sampling, network policy,
provider routing) come from seeded library rows (ResourcePolicy, SamplingPreset), never from env.

Paths under MEDIA_ROOT are :class:`MediaPath` values that resolve on every use. Read them as
``Path(settings.X)`` inside the function that needs them, never at import time: ``backend/conftest.py``
points MEDIA_ROOT at a fresh tmpdir for every test.
"""

import os
from pathlib import Path

import environ

_env = environ.Env()


class MediaPath(os.PathLike[str]):
    """A directory under ``settings.MEDIA_ROOT``, resolved each time it is used.

    ``Path(settings.GENERATION_BLOBS_ROOT)``, ``os.fspath()`` and ``str()`` follow a MEDIA_ROOT changed
    after start-up (per test, or by ``override_settings``). Set the setting's environment variable to an
    absolute path to pin it elsewhere instead.
    """

    __slots__ = ("relative",)

    def __init__(self, relative: str) -> None:
        self.relative = relative

    def __fspath__(self) -> str:
        from django.conf import settings

        return str(Path(settings.MEDIA_ROOT) / self.relative)

    def __str__(self) -> str:
        return self.__fspath__()

    def __repr__(self) -> str:
        return f"MediaPath({self.relative!r})"


# --- WP-01 --- storage roots, machine limits, executor
# Content-addressed blob store (generation/blobs.py): <root>/sha256/<aa>/<bb>/<sha>.zst.
GENERATION_BLOBS_ROOT: str | MediaPath = _env.str("GENERATION_BLOBS_ROOT", default="") or MediaPath("blobs")
# Run evidence (gateway and harness logs, trajectory, workspace): <root>/<run>/a<attempt>/. Run rows keep
# summaries plus the files' sha256s.
GENERATION_RUNS_ROOT: str | MediaPath = _env.str("GENERATION_RUNS_ROOT", default="") or MediaPath(
    "generation_runs",
)

# Machine limits. A study's ResourcePolicy sets each run's sandbox memory and CPU (resolved per study to
# the maximum of the policy value and its stacks' minimums); these cap what any policy may ask for on this
# host. A sandbox may use AGENT_SANDBOX_MEM_LIMIT_MAX of the apps daemon's own cap (APPS_DIND_MEM_LIMIT,
# shared with the sample apps), so raise both together.
AGENT_SANDBOX_MEM_LIMIT_MAX = _env.str("AGENT_SANDBOX_MEM_LIMIT_MAX", default="3g")
AGENT_SANDBOX_NANO_CPUS_MAX = _env.int("AGENT_SANDBOX_NANO_CPUS_MAX", default=2_000_000_000)
# Initial ExecutorSettings.sandbox_slots (sandboxes running at once; staff edit it at runtime). The
# concurrency is never frozen into a run: runs stay comparable under any slot count.
AGENT_MAX_CONCURRENT_RUNS = _env.int("AGENT_MAX_CONCURRENT_RUNS", default=1)

# Execution: leases, heartbeats and the apps-daemon memory rule (backend/execution).
EXECUTOR_TICK_SECONDS = _env.float("EXECUTOR_TICK_SECONDS", default=2.0)
EXECUTOR_LEASE_SECONDS = _env.int("EXECUTOR_LEASE_SECONDS", default=90)
EXECUTOR_HEARTBEAT_SECONDS = _env.int("EXECUTOR_HEARTBEAT_SECONDS", default=20)
EXECUTOR_LEADER_LOCK_KEY = 0x6C6C6D6C6162  # "llmlab"
EXECUTOR_MAX_DB_THREADS = 48
EXECUTOR_LOST_RETRIES = 10  # executor_lost requeues; separate from infra_retries
EXECUTOR_STARVATION_S = 600  # residency starvation guard (spec §C.4 rule 4)
EVAL_MAX_REQUEUES = 2
# AUTHORITATIVE for the memory rule (docker info MemTotal inside apps-dind reports host RAM).
APPS_DIND_MEM_LIMIT = _env.str("APPS_DIND_MEM_LIMIT", default="4g")
APPS_DIND_APP_RESERVE = _env.str("APPS_DIND_APP_RESERVE", default="1g")
EVAL_SLOT_MEM = "1536m"  # app 1g + tester 256m + slack (evaluation/runner.py)
ANALYSIS_WORKSPACE_MEM = "1g"  # runtime/services/docker_manager.py
SCHEDULE_MIN_INTERVAL_S = 900

# --- WP-02a --- v2 compatibility (delete each setting together with its last reader)
# In v3 the harness comes from the design's HarnessPreset, evaluation from Design.evaluation and the
# network policy from the ResourcePolicy. AGENT_DEFAULT_HARNESS, AGENT_AUTO_EVALUATE and
# AGENT_NETWORK_POLICY were removed with their last readers (generation/_legacy/services/agent/).
# The worker registers the retained OpenRouter stats task from its service module (no generation/tasks.py).
CELERY_IMPORTS = ("backend.generation.services.openrouter_stats",)
# The v2 aliases AGENT_SANDBOX_MEM_LIMIT / AGENT_SANDBOX_NANO_CPUS were removed in wave 2 (no readers left;
# execution/capacity.py reads the *_MAX machine limits).

# --- WP-03 --- library services, prompt engine, seeding
# Seed content (spec 11.1): tasks/, prompts/, stacks/, starters/, ... read by `seed_library` / `lint_library`.
GENERATION_LIBRARY_DATA_DIR = _env.str(
    "GENERATION_LIBRARY_DATA_DIR",
    default=str(Path(__file__).resolve().parents[3] / "backend" / "generation" / "data"),
)
# Seed the library after `migrate` (apps.py post-migrate hook). Off unless the environment turns it on: the
# dev stack sets GENERATION_SEED_ON_MIGRATE=true; tests seed explicitly (a seeded test DB would collide with
# factory slugs in every other suite).
GENERATION_SEED_ON_MIGRATE = _env.bool("GENERATION_SEED_ON_MIGRATE", default=False)

# --- WP-05 --- stacks, starters, build context, dependency caches
# Free-choice FROM allowlist (build/dockerfiles.py; spec 6.10): a YAML file of {ref, digest} rows. Empty means
# the shipped generation/data/starters/allowed_bases.yaml. Its refs are rendered into the free-choice brief and
# hashed into treatment_hash (allowed_bases_sha), so pointing this elsewhere changes the treatment.
SAMPLE_BUILD_ALLOWED_BASES = _env.str("SAMPLE_BUILD_ALLOWED_BASES", default="")
# Read-only pull-through dependency caches (compose depcache block; build/depcache.py). URLs as reached from
# inside a sandbox (through the gateway's CONNECT allowlist). Empty = not deployed: runs whose policy asks for
# caches.dependency_cache=shared_ro then use the public registries and record depcache_unavailable.
DEPCACHE_NPM_URL = _env.str("DEPCACHE_NPM_URL", default="")
DEPCACHE_PYPI_URL = _env.str("DEPCACHE_PYPI_URL", default="")
DEPCACHE_GOPROXY_URL = _env.str("DEPCACHE_GOPROXY_URL", default="")
DEPCACHE_MAVEN_URL = _env.str("DEPCACHE_MAVEN_URL", default="")
# Health probe of the caches before a sandbox starts (seconds per endpoint; 0 disables the probe).
DEPCACHE_PROBE_TIMEOUT_S = _env.float("DEPCACHE_PROBE_TIMEOUT_S", default=2.0)
# Docker daemon for `manage.py build_starters --lock` and the docker-marked starter tests. Empty means the apps
# daemon (APPS_DOCKER_HOST, i.e. apps-dind), which is where the spec runs them.
STARTERS_DOCKER_HOST = _env.str("STARTERS_DOCKER_HOST", default="")

# --- WP-06 --- gateway
# Keepalive interval during upstream silence (seconds); the sandbox passes it as GATEWAY_PING_INTERVAL_S.
LLM_GATEWAY_PING_INTERVAL_S = _env.float("LLM_GATEWAY_PING_INTERVAL_S", default=10.0)
# Front protocols whose gateway translate mode is enabled (read by harnesses.registry and
# study.environment with getattr(..., ())). A protocol is listed only once its translator passes the
# golden fixtures (wave 3); the gateway refuses translate configs for unlisted ones at startup, so this
# is deliberately not env-driven.
GATEWAY_TRANSLATORS: list[str] = []

# --- WP-07 --- harness adapters and images
# Harness releases baked into the sandbox images (images/agent-*). Changing one changes the image tag and
# every new run's fingerprint.
AGENT_OPENCODE_VERSION = _env.str("AGENT_OPENCODE_VERSION", default="1.18.35")
AGENT_MINI_SWE_AGENT_VERSION = _env.str("AGENT_MINI_SWE_AGENT_VERSION", default="2.4.6")
AGENT_AIDER_VERSION = _env.str("AGENT_AIDER_VERSION", default="0.86.2")
AGENT_CLAUDE_CODE_VERSION = _env.str("AGENT_CLAUDE_CODE_VERSION", default="2.1.316")
AGENT_CODEX_VERSION = _env.str("AGENT_CODEX_VERSION", default="0.166.0")
AGENT_GOOSE_VERSION = _env.str("AGENT_GOOSE_VERSION", default="1.57.0")
AGENT_QWEN_CODE_VERSION = _env.str("AGENT_QWEN_CODE_VERSION", default="0.26.6")
AGENT_GEMINI_CLI_VERSION = _env.str("AGENT_GEMINI_CLI_VERSION", default="0.66.0")
# Operator kill switch: harness names listed here are reported enabled=False (blocked_reason "disabled by
# the operator") whatever their facts say, e.g. AGENT_HARNESSES_DISABLED=codex,claude-code.
AGENT_HARNESSES_DISABLED = _env.list("AGENT_HARNESSES_DISABLED", default=[])

# --- WP-08 --- agentic run pipeline
# Sandbox backend of the agentic stage (agentic/sandbox.py:get_backend). Only "docker" (the apps daemon) ships.
GENERATION_SANDBOX_BACKEND = _env.str("GENERATION_SANDBOX_BACKEND", default="docker")
# Supervisor tick (seconds) and idle detection: the idle clock advances only while no gateway call is in
# flight and the agent's CPU stays below AGENT_IDLE_CPU_FRACTION of one CPU over AGENT_IDLE_CPU_WINDOW_S
# (spec 6.5). The timeout itself is the policy knob time.idle_timeout_s.
AGENT_SUPERVISOR_TICK_S = _env.float("AGENT_SUPERVISOR_TICK_S", default=2.0)
AGENT_IDLE_CPU_FRACTION = _env.float("AGENT_IDLE_CPU_FRACTION", default=0.05)
AGENT_IDLE_CPU_WINDOW_S = _env.float("AGENT_IDLE_CPU_WINDOW_S", default=10.0)
# Backstop after wall clock + the 15 s kill grace, should the in-container timeout fail.
AGENT_SANDBOX_GRACE_S = _env.int("AGENT_SANDBOX_GRACE_S", default=60)
AGENT_GATEWAY_MEM_LIMIT = _env.str("AGENT_GATEWAY_MEM_LIMIT", default="256m")
# Informational static checks of collected samples (collector; never gating): the esbuild frontend validator
# image (images/frontend-validator) runs only when this is on and the apps daemon is reachable.
GENERATION_FRONTEND_VALIDATOR = _env.bool("GENERATION_FRONTEND_VALIDATOR", default=True)

# --- WP-09 --- evaluation
# Where evaluations run (evaluation/runtime.py): "docker" (production; apps daemon, internal eval network) or
# "local" (bundled stdlib apps only: the conformance kit and the WP-09 tests; refuses any other tree).
EVAL_RUNTIME = _env.str("EVAL_RUNTIME", default="docker")
# Tester and adherence-checker images; their digests must equal the ones pinned in the suite content
# (EvalSuiteContent.tester/adherence_checker.image_digest), else the evaluation is infra_error:image_digest_mismatch.
EVAL_TESTER_IMAGE = _env.str("EVAL_TESTER_IMAGE", default="llmlab-v3-wt/eval-tester:dev")
EVAL_ADHERENCE_IMAGE = _env.str("EVAL_ADHERENCE_IMAGE", default="llmlab-v3-wt/adherence-checker:dev")
# Resource caps of the evaluated app container.
EVAL_APP_MEM_LIMIT = _env.str("EVAL_APP_MEM_LIMIT", default="1g")
EVAL_APP_CPUS = _env.float("EVAL_APP_CPUS", default=1.0)

# --- WP-10 --- study engine

# --- WP-11 --- budget, ledger, stop rules, execution integration
# Live spend: the sandbox supervisor polls each gateway's state.json every BUDGET_LIVE_POLL_S seconds,
# scaled by ceil(running / 8) (spec 4.5). Budget presets, caps and stop rules are library rows, not env.
BUDGET_LIVE_POLL_S = _env.float("BUDGET_LIVE_POLL_S", default=10.0)

# --- WP-12 --- slot mode
# Batch serving of single-shot slot calls (slot/executors/batch_submitter.py, spec 7.6). OpenRouter's batch API
# (POST /api/beta/batches) is beta and only documented second-hand: keep False until its docs are verified.
# While False, routing.serving_tier=auto_batch resolves to standard (note batch_unsupported) and run_batch refuses.
BATCH_BACKENDS_VERIFIED = _env.bool("BATCH_BACKENDS_VERIFIED", default=False)
# Poll interval and expiry of a submitted batch (seconds). Unanswered cells of an expired batch end as
# infra_error:batch_expired and are not retried automatically (a standard-tier retry changes the condition).
BATCH_POLL_S = _env.int("BATCH_POLL_S", default=60)
BATCH_EXPIRY_S = _env.int("BATCH_EXPIRY_S", default=24 * 3600)

# --- WP-13 --- pattern analysis
# Where Semgrep detectors run (analysis/services/rulepack.py): "workspace" = the study owner's analyzer
# workspace container (semgrep from the tool shop), "local" = a semgrep binary on this host.
PATTERNS_SEMGREP_BACKEND = _env.str("PATTERNS_SEMGREP_BACKEND", default="workspace")
# Wall-clock limit of one rule-pack invocation (all semgrep detectors over all variants of a pattern run).
PATTERNS_SEMGREP_TIMEOUT_S = _env.int("PATTERNS_SEMGREP_TIMEOUT_S", default=600)
# Pooled permutation test: exact enumeration up to this many targets (2^T arrangements), else Monte Carlo.
PATTERNS_EXACT_PERMUTATION_MAX_TARGETS = _env.int("PATTERNS_EXACT_PERMUTATION_MAX_TARGETS", default=20)
PATTERNS_PERMUTATION_DRAWS = _env.int("PATTERNS_PERMUTATION_DRAWS", default=10_000)

# --- end of WP blocks ---
