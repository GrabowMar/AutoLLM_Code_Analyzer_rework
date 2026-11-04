"""Study links (owner A): the generation ``Study`` a task is linked to, its reference and its baseline condition.

``EvalTask.study`` is the foreign key to ``generation.Study``; services read and write it through
``study_id`` and the helpers here.

The baseline condition is the study's ``StudyCondition`` with ``is_baseline`` (of its active plan
when it has one; the lowest index when several are flagged).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import Study
    from backend.human_eval.models import EvalTask


def task_study(task: EvalTask) -> Study | None:
    """The task's linked study (None without one); uses the cached relation when it was selected."""
    if task.study_id is None:
        return None
    return task.study


def study_ref(task: EvalTask) -> dict[str, str] | None:
    """TS ``StudyRef`` ``{id, name}`` of the task's study, or None."""
    study = task_study(task)
    if study is None:
        return None
    return {"id": str(study.pk), "name": study.name}


def link_study(task: EvalTask, study: Study | None) -> None:
    """Point the task at ``study`` (not saved: pass ``"study"`` in ``update_fields``)."""
    task.study = study


def baseline_condition_id(study_id: Any) -> int | None:
    """The id of the study's baseline condition (``StudyCondition.is_baseline``), else None."""
    if study_id is None:
        return None
    from backend.generation.models import Study
    from backend.generation.models import StudyCondition

    active_plan = Study.objects.filter(pk=study_id).values_list("active_plan_id", flat=True).first()
    conditions = StudyCondition.objects.filter(study_id=study_id, is_baseline=True)
    if active_plan is not None:
        conditions = conditions.filter(plan_id=active_plan)
    return conditions.order_by("index", "pk").values_list("pk", flat=True).first()
