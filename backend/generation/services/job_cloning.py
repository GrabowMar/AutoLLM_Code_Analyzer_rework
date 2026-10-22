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
from backend.generation.services.profile_resolver import batch_cell_seed
from backend.generation.services.profile_resolver import build_custom_snapshot
from backend.generation.services.profile_resolver import bundle_key_from_snapshot
from backend.generation.services.profile_resolver import experiment_cell_seed
from backend.generation.services.profile_resolver import run_fingerprint

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser


def _bundle_key(snapshot: dict) -> str:
    """The slicing key the original job got: agent snapshots carry it as ``method_key``."""
    if snapshot.get("mode") == "agent" and snapshot.get("method_key"):
        return str(snapshot["method_key"])[:220]
    return bundle_key_from_snapshot(snapshot)


def cell_seed(original: GenerationJob) -> int | None:
    """The seed of *original*'s cell, for a retry that takes the cell over.

    Every condition of a seeded (task, trial) block shares one derived seed
    (common random numbers), so the replacement must run under that seed,
    not a fresh one. The stored seed is what the cell ran with; a launch
    cell whose snapshot failed never stored it, so it is re-derived the way
    the experiment launch / ad-hoc batch derived it. None: the job never had
    a seed, and its retry draws a fresh one.
    """
    if original.experiment_seed is not None:
        return original.experiment_seed
    if original.repeat_index is None:
        return None
    if original.experiment_id is not None:
        return _experiment_cell_seed(original)
    return _batch_cell_seed(original)


def _experiment_cell_seed(original: GenerationJob) -> int | None:
    experiment = original.experiment
    agent = original.mode == GenerationJob.Mode.AGENT
    if experiment.base_seed is None or original.app_requirement_id is None:
        return None
    if not agent and original.condition_id is None:
        return None
    return experiment_cell_seed(
        experiment.base_seed,
        agent=agent,
        condition_id=original.condition_id,
        app_id=original.app_requirement_id,
        repeat_index=original.repeat_index,
    )


def _batch_cell_seed(original: GenerationJob) -> int | None:
    request = original.batch.request if original.batch_id and isinstance(original.batch.request, dict) else {}
    if original.mode != GenerationJob.Mode.AGENT or request.get("base_seed") is None:
        return None
    app = original.app_requirement
    return batch_cell_seed(request["base_seed"], app.slug if app else None, original.repeat_index)


def cell_taken_by(original: GenerationJob) -> GenerationJob | None:
    """A live or completed job already filling *original*'s experiment cell, if any.

    A relaunch tops failed cells up and an earlier retry takes the cell
    over; a second replacement would count as an extra trial of one
    condition and unbalance the design.
    """
    if original.experiment_id is None or original.condition_id is None or original.repeat_index is None:
        return None
    return (
        GenerationJob.objects.filter(
            experiment_id=original.experiment_id,
            condition_id=original.condition_id,
            app_requirement_id=original.app_requirement_id,
            repeat_index=original.repeat_index,
        )
        .exclude(id=original.id)
        .exclude(status__in=(GenerationJob.Status.FAILED, GenerationJob.Status.CANCELLED))
        .first()
    )


def clone_job(
    original: GenerationJob,
    user: AbstractUser,
    *,
    reuse_snapshot: bool,
    new_seed: bool,
    keep_batch: bool = True,
    keep_cell: bool = False,
) -> GenerationJob:
    """Create a new job from *original*.

    ``reuse_snapshot=True`` copies ``resolved_bundle`` verbatim (templates may
    have changed since — the copy preserves the original prompt material);
    ``False`` re-resolves the bundle from the job's own fields, which carry
    every applied preset value (llm_params, agent_config, harness_options),
    so a retry runs the same configuration (same ``config_fingerprint``).
    ``new_seed`` controls whether a reused snapshot keeps the original
    sampling seed (exact replay) or draws a fresh one (an additional trial
    of the same configuration).

    ``keep_cell`` (retry only) makes the clone the replacement of the
    original's trial: it takes over the experiment cell (experiment,
    condition, repeat index) and runs under the cell's seed (see
    :func:`cell_seed`). Replays and reruns are not part of the design: they
    join no experiment, so experiment reports, relaunch coverage and
    rankings never count them as trials. Preset provenance is always kept.
    The clone is created atomically: a snapshot that cannot be built leaves
    no orphaned job.
    """
    with transaction.atomic():
        new_job = GenerationJob.objects.create(
            mode=original.mode,
            created_by=user,
            # Keep the clone in the original batch so batch trials stay whole.
            batch=original.batch if keep_batch else None,
            model=original.model,
            stack_slug=original.stack_slug,
            app_requirement=original.app_requirement,
            profile=original.profile,
            experiment=original.experiment if keep_cell else None,
            condition=original.condition if keep_cell else None,
            repeat_index=original.repeat_index if keep_cell else None,
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
                    snapshot = reseed_agent_snapshot(snapshot, seed)
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
        else:
            if keep_cell:
                # Before the snapshot is built: both builders fall back to a
                # random seed when the job has none.
                new_job.experiment_seed = cell_seed(original)
                new_job.save(update_fields=["experiment_seed", "updated_at"])
            if original.mode == GenerationJob.Mode.AGENT:
                apply_agent_snapshot_to_job(new_job)
            elif original.mode == GenerationJob.Mode.SCAFFOLDING and original.app_requirement:
                apply_snapshot_to_job(new_job)
            elif original.mode == GenerationJob.Mode.CUSTOM:
                # Execution reads the frozen llm section; without it a retry
                # would fall back to the legacy columns and drop llm_params.
                new_job.resolved_bundle = build_custom_snapshot(
                    model=new_job.model,
                    temperature=new_job.temperature,
                    max_tokens=new_job.max_tokens,
                    llm_overrides=new_job.llm_params,
                    sampling_preset=new_job.sampling_preset,
                )
                new_job.save(update_fields=["resolved_bundle", "updated_at"])

    return new_job
