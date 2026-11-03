"""Detector validity audits as human-evaluation tasks (owned by WP-13; spec section 7.9 step 4).

``create_audit_task`` makes a **draft** single-app task whose pool is the sampled variant runs, hits and
non-hits mixed (the coder is blind to the detector verdict: positions are shuffled by the task seed, and
the snapshot carries no pattern data). Its design is one custom questionnaire block with a single-choice
question ``feature_present`` (``present`` / ``absent`` / ``unsure``) describing the feature. The researcher
opens the task (and invites coders) through the usual human-eval screens.

``collect_codes(task)`` reads the submitted, non-excluded answers back: one code per run, the majority of
its coders' ``present``/``absent`` answers (``unsure`` answers and ties give ``unsure``; runs nobody coded
are absent from the result, never coded as anything).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction

from backend.human_eval.models import EvalTask
from backend.human_eval.services import design as design_service
from backend.human_eval.services import pool

if TYPE_CHECKING:
    from collections.abc import Sequence

    from backend.generation.models import GenerationRun

BLOCK_ID = "detector_audit"
QUESTION_ID = "feature_present"
CODES = ("present", "absent", "unsure")


def audit_design(feature_key: str, description: str, *, instructions: str = "") -> dict[str, Any]:
    """The validated single-mode design: one custom questionnaire asking whether the feature is present."""
    raw = design_service.default_design("single")
    raw["blocks"] = [
        {
            "id": BLOCK_ID,
            "type": "questionnaire",
            "title": f"Is '{feature_key}' present?",
            "instructions": instructions
            or (
                "Read the regenerated part of this app (the slot) and decide whether the pattern below is "
                f"present.\n\n**{feature_key}**: {description}"
            ),
            "instrument": "custom",
            "questions": [
                {
                    "id": QUESTION_ID,
                    "kind": "single",
                    "label": f"Is the pattern '{feature_key}' present in the regenerated code?",
                    "help": description[:4000],
                    "required": True,
                    "options": [
                        {"value": "present", "label": "Present"},
                        {"value": "absent", "label": "Absent"},
                        {"value": "unsure", "label": "Cannot tell"},
                    ],
                },
            ],
        },
    ]
    # Precision/NPV are computed by the patterns app (patterns.audit), not by the human-eval analysis.
    raw.setdefault("analysis", {})["primary"] = None
    normalized, errors = design_service.validate_design("single", raw)
    if normalized is None:
        msg = f"the detector audit design is invalid: {errors}"
        raise ValueError(msg)
    return normalized


def create_audit_task(
    *,
    user: Any,
    study: Any,
    feature_key: str,
    description: str,
    runs: Sequence[GenerationRun],
    seed: int,
    title: str = "",
) -> EvalTask:
    """A draft EvalTask over ``runs`` coding ``feature_key`` (``seed`` fixes the item order)."""
    settings_doc, errors = design_service.validate_settings({})
    if settings_doc is None:
        msg = f"default human-eval settings are invalid: {errors}"
        raise ValueError(msg)
    with transaction.atomic():
        task = EvalTask(
            title=(title or f"Detector audit: {feature_key}")[:200],
            description=f"Blind coding of '{feature_key}' to estimate the detector's precision and NPV.",
            instructions="Code each app's regenerated part. You are not told what the detector reported.",
            mode=EvalTask.Mode.SINGLE,
            design=audit_design(feature_key, description),
            settings=settings_doc,
            seed=int(seed) % (2**31),
            study=study,
            created_by=user,
        )
        task.save()
        pool.create_task_apps(task, list(runs))
    return task


def collect_codes(task: EvalTask) -> dict[str, str]:
    """``{run id: present | absent | unsure}`` from submitted, non-excluded assignments."""
    from backend.human_eval.models import Response

    votes: dict[str, list[str]] = defaultdict(list)
    responses = Response.objects.filter(
        assignment__task=task,
        block_id=BLOCK_ID,
        assignment__status="submitted",
        assignment__excluded=False,
        assignment__participant__excluded=False,
    ).select_related("assignment__item__app_a")
    for response in responses:
        app = response.assignment.item.app_a
        if app is None or app.job_id is None:
            continue
        value = ((response.answers or {}).get("answers") or {}).get(QUESTION_ID)
        if value in CODES:
            votes[str(app.job_id)].append(value)
    codes: dict[str, str] = {}
    for run_id, values in votes.items():
        present, absent = values.count("present"), values.count("absent")
        codes[run_id] = "present" if present > absent else "absent" if absent > present else "unsure"
    return codes
