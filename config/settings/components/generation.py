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
# Read only by v2 code that WP-02a deletes or re-points. In v3 the harness comes from the design's
# HarnessPreset, evaluation from Design.evaluation and the network policy from the ResourcePolicy.
AGENT_DEFAULT_HARNESS = _env.str("AGENT_DEFAULT_HARNESS", default="opencode")
AGENT_AUTO_EVALUATE = _env.bool("AGENT_AUTO_EVALUATE", default=True)
AGENT_NETWORK_POLICY = _env.str("AGENT_NETWORK_POLICY", default="packages")
# v2 names of the machine limits (generation/services/agent/config.py, execution/capacity.py).
AGENT_SANDBOX_MEM_LIMIT = AGENT_SANDBOX_MEM_LIMIT_MAX
AGENT_SANDBOX_NANO_CPUS = AGENT_SANDBOX_NANO_CPUS_MAX

# --- WP-03 --- library services, prompt engine, seeding

# --- WP-05 --- stacks, starters, build context, dependency caches

# --- WP-06 --- gateway

# --- WP-07 --- harness adapters and images
# Harness releases baked into the sandbox images (images/agent-*). Changing one changes the image tag and
# every new run's fingerprint.
AGENT_OPENCODE_VERSION = _env.str("AGENT_OPENCODE_VERSION", default="1.18.35")
AGENT_MINI_SWE_AGENT_VERSION = _env.str("AGENT_MINI_SWE_AGENT_VERSION", default="2.4.6")
AGENT_AIDER_VERSION = _env.str("AGENT_AIDER_VERSION", default="0.86.2")

# --- WP-08 --- agentic run pipeline

# --- WP-09 --- evaluation

# --- WP-10 --- study engine

# --- WP-11 --- budget, ledger, stop rules, execution integration

# --- WP-12 --- slot mode

# --- WP-13 --- pattern analysis

# --- end of WP blocks ---
