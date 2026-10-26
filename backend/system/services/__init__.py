"""System monitoring and maintenance services.

Re-exports the public callables from the submodules so that existing imports
like ``from backend.system import services; services.host_metrics()`` keep
working.
"""

from backend.system.services.health import celery_status
from backend.system.services.health import container_health
from backend.system.services.health import redis_status
from backend.system.services.maintenance import action_count
from backend.system.services.maintenance import clear_caches
from backend.system.services.maintenance import clear_stuck_analysis_tasks
from backend.system.services.maintenance import clear_stuck_generation_jobs
from backend.system.services.maintenance import clear_stuck_reports
from backend.system.services.maintenance import purge_orphan_containers
from backend.system.services.maintenance import purge_orphans
from backend.system.services.maintenance import purged_total
from backend.system.services.maintenance import reconcile_analysis_runs
from backend.system.services.metrics import app_stats
from backend.system.services.metrics import db_stats
from backend.system.services.metrics import host_metrics

__all__ = [
    "action_count",
    "app_stats",
    "celery_status",
    "clear_caches",
    "clear_stuck_analysis_tasks",
    "clear_stuck_generation_jobs",
    "clear_stuck_reports",
    "container_health",
    "db_stats",
    "host_metrics",
    "purge_orphan_containers",
    "purge_orphans",
    "purged_total",
    "reconcile_analysis_runs",
    "redis_status",
]
