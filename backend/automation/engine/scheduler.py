"""Cron-based schedule executor.

``due_schedules(now)`` — return enabled schedules whose next_run_at <= now.
``tick()`` — trigger runs for due schedules, update timestamps.
"""

from __future__ import annotations

import logging
from datetime import UTC
from datetime import datetime

logger = logging.getLogger(__name__)


def due_schedules(now: datetime | None = None) -> list:
    """Return Schedule objects that are enabled and due to fire.

    Args:
        now: Override "current time" (useful for testing). Defaults to UTC now.

    Returns:
        List of Schedule model instances.
    """
    from backend.automation.models import Schedule

    if now is None:
        now = datetime.now(UTC)

    return list(
        Schedule.objects.filter(enabled=True, next_run_at__lte=now).select_related(
            "pipeline",
            "owner",
        ),
    )


def tick(now: datetime | None = None) -> int:
    """Fire all due schedules: trigger runs and advance next_run_at.

    Returns the number of schedules that fired.
    """
    from croniter import croniter

    from backend.automation import services as automation_services

    if now is None:
        now = datetime.now(UTC)

    schedules = due_schedules(now)
    fired = 0

    for schedule in schedules:
        try:
            # Trigger a new run
            automation_services.trigger_run(
                pipeline=schedule.pipeline,
                params={},
                user=schedule.owner,
            )

            # Advance timestamps
            schedule.last_run_at = now
            schedule.next_run_at = croniter(
                schedule.cron_expression,
                now,
            ).get_next(datetime)
            schedule.save(update_fields=["last_run_at", "next_run_at"])

            fired += 1
            logger.info(
                "Schedule %s fired for pipeline %s; next=%s",
                schedule.id,
                schedule.pipeline_id,
                schedule.next_run_at,
            )
        except Exception:
            logger.exception("Failed to fire schedule %s", schedule.id)

    return fired
