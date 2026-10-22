"""Clone generation jobs for retry, exact replay, and additional trials."""

from __future__ import annotations

import copy
import random
from typing import TYPE_CHECKING

from django.db import transaction

from backend.generation.models import GenerationJob
from backend.generation.services.agent.snapshot import apply_agent_snapshot_to_job
from backend.generation.services.agent.snapshot import reseed_agent_snapshot
from backend.generation.services.profile_resolver import apply_snapshot_to_job
from backend.generation.services.profile_resolver import build_custom_snapshot
from backend.generation.services.profile_resolver import bundle_key_from_snapshot
from backend.generation.services.profile_resolver import run_fingerprint

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser


def _bundle_key(snapshot: dict) -> str:
    """The slicing key the original job got: agent snapshots carry it as ``method_key``."""
    if snapshot.get("mode") == "agent" and snapshot.get("method_key"):
        return str(snapshot["method_key"])[:220]
    return bundle_key_from_snapshot(snapshot)


def clone_job(
    original: GenerationJob,
    user: AbstractUser,
    *,
    reuse_snapshot: bool,
    new_seed: bool,
    keep_batch: bool = True,
) -> GenerationJob:
    """Create a new job from *original*.

    ``reuse_snapshot=True`` copies ``resolved_bundle`` verbatim (templates may
    have changed since — the copy preserves the original prompt material);
    ``False`` re-resolves the bundle from the job's own fields, which carry
    every applied preset value (llm_params, agent_config, harness_options),
    so a retry runs the same configuration (same ``config_fingerprint``).
    ``new_seed`` controls whether the run keeps the original sampling seed
    (exact replay) or draws a fresh one (an additional trial of the same
    configuration).

    The clone stays in the original's experiment cell (experiment,
    condition, repeat index) and keeps its preset provenance. It is created
    atomically: a snapshot that cannot be built leaves no orphaned job.
    """
    with transaction.atomic():
        new_job = GenerationJob.objects.create(
            mode=original.mode,
            created_by=user,
            # Keep the clone in the original batch so experiment trials stay whole.
            batch=original.batch if keep_batch else None,
            model=original.model,
            stack_slug=original.stack_slug,
            app_requirement=original.app_requirement,
            profile=original.profile,
            experiment=original.experiment,
            condition=original.condition,
            repeat_index=original.repeat_index,
            custom_system_prompt=original.custom_system_prompt,
            custom_user_prompt=original.custom_user_prompt,
            temperature=original.temperature,
            max_tokens=original.max_tokens,
            top_p=original.top_p,
            llm_params=copy.deepcopy(original.llm_params or {}),
            task_description=original.task_description,
            agent_config=copy.deepcopy(original.agent_config or {}),
            harness_options=copy.deepcopy(original.harness_options or {}),
            harness_preset=original.harness_preset,
            sampling_preset=original.sampling_preset,
            budget_preset=original.budget_preset,
            copilot_description=original.copilot_description,
            copilot_max_iterations=original.copilot_max_iterations,
            copilot_use_open_source=original.copilot_use_open_source,
        )

        snapshot = original.resolved_bundle if isinstance(original.resolved_bundle, dict) else {}
        if reuse_snapshot and snapshot:
            snapshot = copy.deepcopy(snapshot)
            if new_seed:
                seed = random.randint(0, 2_147_483_647)  # noqa: S311 - experiment seed
                if snapshot.get("mode") == "agent":
                    snapshot = reseed_agent_snapshot(snapshot, seed, original.model)
                else:
                    snapshot["seed"] = seed
                    snapshot["run_fingerprint"] = run_fingerprint(snapshot)
            new_job.experiment_seed = snapshot.get("seed")
            new_job.resolved_bundle = snapshot
            new_job.prompt_hash = snapshot.get("prompt_hash") or ""
            new_job.bundle_key = _bundle_key(snapshot)
            new_job.save(
                update_fields=[
                    "experiment_seed",
                    "resolved_bundle",
                    "prompt_hash",
                    "bundle_key",
                    "updated_at",
                ],
            )
        elif original.mode == GenerationJob.Mode.AGENT:
            apply_agent_snapshot_to_job(new_job)
        elif original.mode == GenerationJob.Mode.SCAFFOLDING and original.app_requirement:
            apply_snapshot_to_job(new_job)
        elif original.mode == GenerationJob.Mode.CUSTOM:
            # Execution reads the frozen llm section; without it a retry would
            # fall back to the legacy columns and drop llm_params.
            new_job.resolved_bundle = build_custom_snapshot(
                model=new_job.model,
                temperature=new_job.temperature,
                max_tokens=new_job.max_tokens,
                llm_overrides=new_job.llm_params,
                sampling_preset=new_job.sampling_preset,
            )
            new_job.save(update_fields=["resolved_bundle", "updated_at"])

    return new_job
