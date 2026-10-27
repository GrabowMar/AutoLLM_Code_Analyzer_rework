"""Clone generation jobs for retry, exact replay, and additional trials."""

from __future__ import annotations

import copy
import random
from typing import TYPE_CHECKING

from django.db import transaction

from backend.generation.models import Experiment
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.services.agent.snapshot import agent_stack_slug
from backend.generation.services.agent.snapshot import apply_agent_snapshot_to_job
from backend.generation.services.agent.snapshot import reseed_agent_snapshot
from backend.generation.services.profile_resolver import DEFAULT_CONTINUATION_LIMIT
from backend.generation.services.profile_resolver import DEFAULT_ENABLE_REPAIR
from backend.generation.services.profile_resolver import apply_snapshot_to_job
from backend.generation.services.profile_resolver import attach_rendered_backend_prompts
from backend.generation.services.profile_resolver import batch_cell_seed
from backend.generation.services.profile_resolver import build_custom_snapshot
from backend.generation.services.profile_resolver import bundle_key_from_snapshot
from backend.generation.services.profile_resolver import experiment_cell_seed
from backend.generation.services.profile_resolver import generation_options
from backend.generation.services.profile_resolver import run_fingerprint
from backend.runtime.services.scaffolding import canonical_stack_slug
from backend.runtime.services.scaffolding import get_stack_row

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser


def _bundle_key(snapshot: dict) -> str:
    """The slicing key the original job got: agent snapshots carry it as ``method_key``."""
    if snapshot.get("mode") == "agent" and snapshot.get("method_key"):
        return str(snapshot["method_key"])[:220]
    return bundle_key_from_snapshot(snapshot)


def _execution_policy(original: GenerationJob) -> dict:
    policy = (original.execution or {}).get("policy") if isinstance(original.execution, dict) else None
    return {"policy": policy} if policy else {}


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


def library_pins(source: GenerationJob, stack_slug: str, snapshot: dict | None = None) -> dict:
    """What a re-resolved snapshot keeps from *source*'s frozen one: spec content and stack version.

    Spec rows are updated in place and builtin stacks gain versions when the
    library changes, so re-resolving from the job's fields alone would run a
    retried experiment cell on newer spec text and a newer skeleton than its
    siblings, under the same ``app_requirement_id``. Everything else is
    re-resolved as before (the current harness, images and procedure).

    Returns ``{"app_requirement": dict | None, "stack": dict | None,
    "brief": dict | None, "record": dict}``. ``app_requirement`` is
    *source*'s frozen app dict, verbatim (a snapshot from before spec
    versions were embedded has no ``version``/``content_hash``; its siblings
    have none either). ``stack`` is *source*'s pinned ``{slug, version,
    content_hash}`` when that exact version still exists on *stack_slug*;
    otherwise None (the latest version is used) and ``record`` says why.
    ``brief`` (agent jobs) is *source*'s frozen brief section when both the
    task and the stack are kept: the brief describes the skeleton
    (``stacks.yaml`` is per stack, not per version), so one re-rendered over
    an older pinned skeleton would state the newer stack's runtimes.
    ``record`` is stored in the new snapshot as ``library_pin`` (provenance
    only; it enters no key). *snapshot* overrides ``source.resolved_bundle``
    (the frozen snapshot read before *source* itself is re-resolved).
    """
    if snapshot is None:
        snapshot = source.resolved_bundle
    if not isinstance(snapshot, dict):
        snapshot = {}
    record: dict = {"source_job_id": str(source.id)}
    pins: dict = {"app_requirement": None, "stack": None, "brief": None, "record": record}

    app = snapshot.get("app_requirement")
    if source.app_requirement_id is None:
        record["app_requirement"] = "none"
    elif isinstance(app, dict) and app and app.get("id") == source.app_requirement_id:
        pins["app_requirement"] = copy.deepcopy(app)
        record["app_requirement"] = "pinned"
        record["app_requirement_version"] = app.get("version")
        record["app_requirement_content_hash"] = app.get("content_hash")
    else:
        # The source never froze a snapshot (it failed before), so there is no
        # older content to keep.
        record["app_requirement"] = "live"
        record["app_requirement_reason"] = "the source job froze no spec"

    stack = snapshot.get("stack")
    if not (isinstance(stack, dict) and stack.get("slug") and stack.get("version") is not None):
        record["stack"] = "latest"
        record["stack_reason"] = "the source job pinned no stack version"
        return pins
    record["stack_pinned"] = {k: stack.get(k) for k in ("slug", "version", "content_hash")}
    row = get_stack_row(stack["slug"], version=stack["version"])
    if canonical_stack_slug(stack["slug"]) != stack_slug:
        record["stack"] = "latest"
        record["stack_reason"] = f"the job now resolves to the {stack_slug} stack"
    elif row is None or (stack.get("content_hash") and row.content_hash != stack["content_hash"]):
        record["stack"] = "latest"
        record["stack_reason"] = "the pinned stack version no longer exists with that content"
    else:
        pins["stack"] = {"slug": row.slug, "version": row.version, "content_hash": row.content_hash}
        record["stack"] = "pinned"
    brief = snapshot.get("brief")
    if (
        pins["stack"] is not None
        and record["app_requirement"] != "live"
        and isinstance(brief, dict)
        and brief.get("text")
        and brief.get("sha256")
    ):
        pins["brief"] = copy.deepcopy(brief)
        record["brief"] = "pinned"
    return pins


def apply_pinned_snapshot(
    job: GenerationJob,
    source: GenerationJob,
    *,
    continuation_limit: int = DEFAULT_CONTINUATION_LIMIT,
    enable_repair: bool = DEFAULT_ENABLE_REPAIR,
    save: bool = True,
    keep_brief: bool = True,
) -> GenerationJob:
    """Re-resolve *job*'s snapshot (agent or scaffolding mode) keeping *source*'s spec and stack.

    *source* is the job being replaced (a retry), *job* itself when a
    pending job is re-frozen, or a sibling cell's job when an experiment
    top-up fills a cell (see ``experiments.launch_experiment``). See
    :func:`library_pins`. ``keep_brief=False`` re-renders an agent brief
    instead of keeping *source*'s (a sibling of another condition, whose
    brief states that condition's budget). A scaffolding snapshot also keeps
    *source*'s frontend-context procedure: one frozen before it was versioned
    stays on version 1. ``save=False`` leaves *job* unsaved (bulk creation).
    """
    if job.mode == GenerationJob.Mode.AGENT:
        pins = library_pins(source, agent_stack_slug(job.stack_slug, job.profile))
        if not keep_brief and pins["brief"] is not None:
            pins["brief"] = None
            pins["record"].pop("brief", None)
        return apply_agent_snapshot_to_job(job, save=save, pins=pins)
    # Read the frozen snapshot first: when *source* is *job*, re-resolving
    # overwrites it.
    frozen = copy.deepcopy(source.resolved_bundle) if isinstance(source.resolved_bundle, dict) else {}
    apply_snapshot_to_job(job, save=False, continuation_limit=continuation_limit, enable_repair=enable_repair)
    snapshot = job.resolved_bundle
    pins = library_pins(source, snapshot["scaffolding_slug"], snapshot=frozen)
    if pins["app_requirement"] is not None:
        snapshot["app_requirement"] = pins["app_requirement"]
    if pins["stack"] is not None:
        snapshot["stack"] = pins["stack"]
    if frozen.get("app_requirement") and "frontend_context_version" not in frozen:
        snapshot.pop("frontend_context_version", None)
    snapshot["library_pin"] = pins["record"]
    # Re-render the backend prompts from the kept spec (pre-rendered ones
    # would be reused as they are); this also recomputes prompt_hash and
    # run_fingerprint (which cover the spec and the stack hash).
    snapshot["prompts"] = {k: v for k, v in (snapshot.get("prompts") or {}).items() if k != "backend"}
    snapshot = attach_rendered_backend_prompts(snapshot, None)
    job.resolved_bundle = snapshot
    job.prompt_hash = snapshot.get("prompt_hash") or ""
    if not save:
        return job
    job.save(
        update_fields=[
            "experiment_seed",
            "resolved_bundle",
            "prompt_hash",
            "bundle_key",
            "updated_at",
        ],
    )
    return job


class CellTakenError(Exception):
    """The trial cell a retry would take over already has a live or completed job."""

    def __init__(self, taken_by: GenerationJob) -> None:
        self.taken_by = taken_by
        super().__init__(
            "This trial cell already has a live or completed run (a relaunch or an earlier retry); "
            "another one would count as an extra trial",
        )


def cell_taken_by(original: GenerationJob) -> GenerationJob | None:
    """A live or completed job already filling *original*'s trial cell, if any.

    An experiment cell is (experiment, condition, app, repeat); an ad-hoc
    batch cell is (batch, model, app or free-form task, trial, harness
    preset for agent runs / profile for scaffolding). A relaunch tops
    failed experiment cells up and an earlier retry takes a cell over; a
    second replacement would count as an extra trial of one condition and
    unbalance the design. Reruns and replays carry no repeat index, so they
    never fill a cell.
    """
    if original.repeat_index is None:
        return None
    if original.experiment_id is not None:
        if original.condition_id is None:
            return None
        cell: dict = {"experiment_id": original.experiment_id, "condition_id": original.condition_id}
    elif original.batch_id is not None:
        cell = {"batch_id": original.batch_id, "experiment_id": None, "model_id": original.model_id}
        if original.mode == GenerationJob.Mode.AGENT:
            cell["harness_preset_id"] = original.harness_preset_id
            harness = (original.agent_config or {}).get("harness")
            if original.harness_preset_id is None and harness:
                # Pre-preset batches: the harness name is the condition.
                cell["agent_config__harness"] = harness
        elif original.mode == GenerationJob.Mode.SCAFFOLDING:
            cell["profile_id"] = original.profile_id
    else:
        return None
    return (
        GenerationJob.objects.filter(
            **cell,
            app_requirement_id=original.app_requirement_id,
            repeat_index=original.repeat_index,
        )
        .exclude(id=original.id)
        .exclude(status__in=(GenerationJob.Status.FAILED, GenerationJob.Status.CANCELLED))
        .first()
    )


def _lock_cell(original: GenerationJob) -> None:
    """Row-lock what owns *original*'s cell until the transaction ends, so concurrent retries serialize.

    The batch row covers ad-hoc cells (and retries of one experiment job);
    the experiment row covers an experiment cell whose earlier attempts sit
    in different launch batches. Always experiment first, then batch, so
    two retries cannot deadlock.
    """
    if original.experiment_id is not None:
        Experiment.objects.select_for_update().filter(id=original.experiment_id).values_list("id").first()
    if original.batch_id is not None:
        GenerationBatch.objects.select_for_update().filter(id=original.batch_id).values_list("id").first()


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
    so a retry runs the same configuration under the current procedure. The
    spec content and stack version are the exceptions: they are kept from
    the original's snapshot (:func:`library_pins`), since the library may
    have been updated in place since the original was frozen.
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
    no orphaned job. A cell takeover checks the cell under a row lock and
    raises :class:`CellTakenError` when it is already filled.
    """
    with transaction.atomic():
        if keep_cell:
            _lock_cell(original)
            taken_by = cell_taken_by(original)
            if taken_by is not None:
                raise CellTakenError(taken_by)
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
            build_healing=original.build_healing,
            harness_preset=original.harness_preset,
            sampling_preset=original.sampling_preset,
            budget_preset=original.budget_preset,
            copilot_description=original.copilot_description,
            copilot_max_iterations=original.copilot_max_iterations,
            copilot_use_open_source=original.copilot_use_open_source,
            # A cell takeover keeps the cell's frozen run position and priority, so
            # the executor runs the replacement where the design put the cell.
            sequence=original.sequence if keep_cell else None,
            priority=original.priority if keep_cell else 0,
            execution=_execution_policy(original) if keep_cell else {},
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
                apply_pinned_snapshot(new_job, original)
            elif original.mode == GenerationJob.Mode.SCAFFOLDING and original.app_requirement:
                # Keep the original's continuation/repair options (an experiment's).
                limit, repair = generation_options(original.resolved_bundle)
                apply_pinned_snapshot(new_job, original, continuation_limit=limit, enable_repair=repair)
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
