"""Clone generation jobs for retry, exact replay, and additional trials."""

from __future__ import annotations

import copy
import random
from typing import TYPE_CHECKING

from backend.generation.models import GenerationJob
from backend.generation.services.bundle_resolver import apply_snapshot_to_job
from backend.generation.services.bundle_resolver import bundle_key_from_snapshot
from backend.generation.services.bundle_resolver import run_fingerprint

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser


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
    ``False`` re-resolves the bundle from current templates. ``new_seed``
    controls whether the run keeps the original sampling seed (exact replay)
    or draws a fresh one (an additional trial of the same configuration).
    """
    new_job = GenerationJob.objects.create(
        mode=original.mode,
        created_by=user,
        # Keep the clone in the original batch so experiment trials stay whole.
        batch=original.batch if keep_batch else None,
        model=original.model,
        scaffolding_template=original.scaffolding_template,
        app_requirement=original.app_requirement,
        template_bundle=original.template_bundle,
        backend_prompt_template=original.backend_prompt_template,
        frontend_prompt_template=original.frontend_prompt_template,
        custom_system_prompt=original.custom_system_prompt,
        custom_user_prompt=original.custom_user_prompt,
        temperature=original.temperature,
        max_tokens=original.max_tokens,
        copilot_description=original.copilot_description,
        copilot_max_iterations=original.copilot_max_iterations,
        copilot_use_open_source=original.copilot_use_open_source,
    )

    snapshot = original.resolved_bundle if isinstance(original.resolved_bundle, dict) else {}
    if reuse_snapshot and snapshot:
        snapshot = copy.deepcopy(snapshot)
        if new_seed:
            snapshot["seed"] = random.randint(0, 2_147_483_647)  # noqa: S311 - experiment seed
            snapshot["run_fingerprint"] = run_fingerprint(snapshot)
        new_job.experiment_seed = snapshot.get("seed")
        new_job.resolved_bundle = snapshot
        new_job.prompt_hash = snapshot.get("prompt_hash") or ""
        new_job.bundle_key = bundle_key_from_snapshot(snapshot)
        new_job.save(
            update_fields=[
                "experiment_seed",
                "resolved_bundle",
                "prompt_hash",
                "bundle_key",
                "updated_at",
            ],
        )
    elif original.mode == GenerationJob.Mode.SCAFFOLDING and original.app_requirement:
        apply_snapshot_to_job(new_job)

    return new_job
