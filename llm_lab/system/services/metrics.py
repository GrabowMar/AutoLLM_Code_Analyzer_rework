"""Host, database, and application-level metrics."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import psutil
from django.db import connection
from django.db.models import Count
from django.utils import timezone

from llm_lab.analysis.models import AnalysisTask
from llm_lab.generation.models import GenerationJob
from llm_lab.reports.models import Report
from llm_lab.runtime.models import ContainerInstance

_BYTES_UNITS = ("B", "KB", "MB", "GB", "TB")
_UNIT_STEP = 1024


def _bytes_human(b: int) -> str:
    val: float = float(b)
    for unit in _BYTES_UNITS:
        if val < _UNIT_STEP:
            return f"{val:.1f} {unit}"
        val /= _UNIT_STEP
    return f"{val:.1f} PB"


def host_metrics() -> dict[str, Any]:
    cpu_percent = psutil.cpu_percent(interval=0.2)
    mem = psutil.virtual_memory()
    load = psutil.getloadavg()
    boot_time = psutil.boot_time()
    uptime_seconds = time.time() - boot_time

    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append(
                {
                    "mountpoint": part.mountpoint,
                    "device": part.device,
                    "fstype": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": usage.percent,
                },
            )
        except PermissionError:
            pass

    return {
        "cpu_percent": cpu_percent,
        "cpu_count": psutil.cpu_count(logical=True),
        "memory": {
            "total": mem.total,
            "used": mem.used,
            "available": mem.available,
            "percent": mem.percent,
        },
        "disks": disks,
        "load_avg": {"1m": load[0], "5m": load[1], "15m": load[2]},
        "uptime_seconds": uptime_seconds,
        "boot_time": boot_time,
    }


def db_stats() -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                numbackends,
                xact_commit,
                xact_rollback,
                blks_hit,
                blks_read,
                tup_returned,
                tup_fetched,
                tup_inserted,
                tup_updated,
                tup_deleted,
                deadlocks
            FROM pg_stat_database
            WHERE datname = current_database()
            """,
        )
        row = cursor.fetchone()
        cols = [
            "numbackends",
            "xact_commit",
            "xact_rollback",
            "blks_hit",
            "blks_read",
            "tup_returned",
            "tup_fetched",
            "tup_inserted",
            "tup_updated",
            "tup_deleted",
            "deadlocks",
        ]
        stat = dict(zip(cols, row, strict=False)) if row else {}

        cursor.execute(
            """
            SELECT
                schemaname || '.' || tablename AS table_name,
                pg_total_relation_size(
                    (quote_ident(schemaname) || '.' || quote_ident(tablename))::regclass
                ) AS total_bytes
            FROM pg_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY total_bytes DESC
            LIMIT 10
            """,
        )
        top_tables = [
            {
                "table": r[0],
                "total_bytes": r[1],
                "total_human": _bytes_human(r[1]),
            }
            for r in cursor.fetchall()
        ]

    return {"stats": stat, "top_tables_by_size": top_tables}


def app_stats() -> dict[str, Any]:
    since = timezone.now() - timedelta(hours=24)

    def _counts(model):  # type: ignore[no-untyped-def]
        qs = model.objects.values("status").annotate(cnt=Count("pk")).order_by()
        by_status = {row["status"]: row["cnt"] for row in qs}
        recent = 0
        if hasattr(model, "created_at"):
            recent = model.objects.filter(created_at__gte=since).count()
        return {"by_status": by_status, "last_24h": recent}

    return {
        "analysis_tasks": _counts(AnalysisTask),
        "generation_jobs": _counts(GenerationJob),
        "reports": _counts(Report),
        "container_instances": _counts(ContainerInstance),
    }
