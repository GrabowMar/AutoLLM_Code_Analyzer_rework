"""System monitoring and maintenance services.

Re-exports the public callables from the submodules so that existing imports
like ``from backend.system import services; services.host_metrics()`` keep
working.
"""

from backend.system.services.health import celery_status
from backend.system.services.health import container_health
from backend.system.services.health import redis_status
from backend.system.services.maintenance import clear_caches
from backend.system.services.maintenance import clear_stuck_analysis_tasks
from backend.system.services.maintenance import clear_stuck_generation_jobs
from backend.system.services.maintenance import purge_orphan_containers
from backend.system.services.metrics import app_stats
from backend.system.services.metrics import db_stats
from backend.system.services.metrics import host_metrics

__all__ = [
    "app_stats",
    "celery_status",
    "clear_caches",
    "clear_stuck_analysis_tasks",
    "clear_stuck_generation_jobs",
    "container_health",
    "db_stats",
    "host_metrics",
    "purge_orphan_containers",
    "redis_status",
]
