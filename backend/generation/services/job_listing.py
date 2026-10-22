"""Job list rows without loading the big JSON columns.

``result_data`` (generated files, run evidence) and ``resolved_bundle``
(brief, prompts) can be megabytes per job, and the batch pages poll job
lists live. List querysets therefore defer both columns and pull the few
values a list row shows out of them with JSON-path annotations computed by
the database:

* ``harness``            -- ``agent_config.harness``, else the snapshot's harness name
* ``cost_usd``           -- ``metrics.cost``
* ``pass_rate``          -- ``metrics.functional.pass_rate`` (null on infra errors)
* ``eval_status``        -- ``result_data.evaluation.status``
* ``termination``        -- ``result_data.agent.termination``
* ``validity``           -- ``result_data.agent.validity`` as one level:
                            ``exclude`` (not a valid sample) > ``warn`` > ``valid``
* ``config_fingerprint`` -- ``resolved_bundle.config_fingerprint`` (agent snapshots)
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Case
from django.db.models import CharField
from django.db.models import FloatField
from django.db.models import Value
from django.db.models import When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.fields.json import KeyTransform
from django.db.models.functions import Cast
from django.db.models.functions import Coalesce

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from backend.generation.models import GenerationJob

# Never read by a list row; the first two are the large ones.
LIST_DEFERRED_FIELDS = (
    "result_data",
    "resolved_bundle",
    "custom_system_prompt",
    "custom_user_prompt",
    "task_description",
    "model__metadata",
    "model__description",
)


def harness_expression():
    # agent_config first: it is tiny, and COALESCE only reads the snapshot
    # for jobs whose harness came from the default (no harness key).
    return Coalesce(
        KeyTextTransform("harness", "agent_config"),
        KeyTextTransform("name", KeyTransform("harness", "resolved_bundle")),
    )


def cost_expression():
    return Cast(KeyTextTransform("cost", "metrics"), FloatField())


def pass_rate_expression():
    return Cast(KeyTextTransform("pass_rate", KeyTransform("functional", "metrics")), FloatField())


def validity_expression():
    # assess_validity: valid is false exactly when some note has severity
    # "exclude"; jobs without a validity block (not run, not agent) get null.
    return Case(
        When(result_data__agent__validity__valid=False, then=Value("exclude")),
        When(result_data__agent__validity__notes__contains=[{"severity": "warn"}], then=Value("warn")),
        When(result_data__agent__validity__valid=True, then=Value("valid")),
        default=Value(None),
        output_field=CharField(),
    )


def with_list_fields(qs: QuerySet[GenerationJob]) -> QuerySet[GenerationJob]:
    """*qs* shaped for :func:`job_list_item`: related rows joined, blobs deferred, JSON paths annotated."""
    return (
        qs.select_related("model", "app_requirement", "profile", "harness_preset")
        .defer(*LIST_DEFERRED_FIELDS)
        .annotate(
            harness=harness_expression(),
            cost_usd=cost_expression(),
            pass_rate=pass_rate_expression(),
            eval_status=KeyTextTransform("status", KeyTransform("evaluation", "result_data")),
            termination=KeyTextTransform("termination", KeyTransform("agent", "result_data")),
            validity=validity_expression(),
            config_fingerprint=KeyTextTransform("config_fingerprint", "resolved_bundle"),
        )
    )


def preset_ref(preset: Any) -> dict[str, Any] | None:
    if preset is None:
        return None
    return {"id": preset.id, "slug": preset.slug, "version": preset.version}


def job_list_item(job: GenerationJob) -> dict[str, Any]:
    """One ``GenerationJobListSchema`` row from a job loaded through :func:`with_list_fields`."""
    profile = job.profile
    return {
        "id": job.id,
        "mode": job.mode,
        "status": job.status,
        "phase": job.phase,
        "model_name": job.model.model_name if job.model else None,
        "model_id_str": job.model.model_id if job.model else None,
        "template_name": job.app_requirement.name if job.app_requirement else None,
        "app_slug": job.app_requirement.slug if job.app_requirement else None,
        "stack_slug": job.stack_slug,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "duration_seconds": job.duration_seconds,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "batch_id": job.batch_id,
        "harness": job.harness,
        "harness_preset": preset_ref(job.harness_preset),
        "condition_id": job.condition_id,
        "profile_ref": f"{profile.slug}@{profile.version}" if profile else None,
        "repeat_index": job.repeat_index,
        "experiment_seed": job.experiment_seed,
        "cost_usd": job.cost_usd,
        "eval_status": job.eval_status,
        "pass_rate": job.pass_rate,
        "termination": job.termination,
        "validity": job.validity,
        "config_fingerprint": job.config_fingerprint,
    }
