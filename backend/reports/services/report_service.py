"""Report orchestration: create, dispatch background generation, list."""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from backend.reports.models import Report

from .generators import GENERATORS

logger = logging.getLogger(__name__)


def create_and_dispatch(
    *,
    report_type: str,
    config: dict[str, Any],
    title: str | None = None,
    description: str = "",
    user=None,
    expires_in_days: int | None = 30,
) -> Report:
    """Create a Report row, then run the generator in a daemon thread."""

    if report_type not in GENERATORS:
        msg = f"Unknown report_type: {report_type}"
        raise ValueError(msg)
    _validate_config(report_type, config)

    final_title = title or _default_title(report_type, config)
    expires_at = timezone.now() + timedelta(days=int(expires_in_days)) if expires_in_days else None

    report = Report.objects.create(
        report_type=report_type,
        title=final_title,
        description=description,
        config=config,
        created_by=user if (user and user.is_authenticated) else None,
        expires_at=expires_at,
    )
    _dispatch(report.id)
    return report


def _dispatch(report_id) -> None:
    thread = threading.Thread(
        target=_run_generation,
        args=(report_id,),
        daemon=True,
        name=f"report-{report_id}",
    )
    thread.start()


def _run_generation(report_id) -> None:
    from django.db import connection

    try:
        with transaction.atomic():
            report = Report.objects.select_for_update().get(id=report_id)
            if report.status != Report.Status.PENDING:
                return
            report.mark_generating()
        _generate(report)
    except Report.DoesNotExist:
        logger.warning("Report %s vanished before generation", report_id)
    except Exception as exc:
        logger.exception("Report generation failed for %s", report_id)
        try:
            r = Report.objects.get(id=report_id)
            r.mark_failed(str(exc))
        except Report.DoesNotExist:
            pass
    finally:
        connection.close()


def _generate(report: Report) -> None:
    generator = GENERATORS[report.report_type]
    data = generator(report.config or {})
    report.report_data = data
    report.summary = _make_summary(report.report_type, data)
    report.save(update_fields=["report_data", "summary"])
    report.mark_completed()


def _validate_config(report_type: str, config: dict[str, Any]) -> None:
    """Synchronously check required config keys before background dispatch."""

    required: dict[str, list[str]] = {
        "model_analysis": ["model_id"],
        "template_comparison": ["template_slug"],
    }
    for key in required.get(report_type, []):
        if not (config or {}).get(key):
            msg = f"Missing required config key for {report_type}: {key}"
            raise ValueError(msg)


def _default_title(report_type: str, config: dict[str, Any]) -> str:
    if report_type == "model_analysis":
        slug = config.get("model_id") or config.get("model_slug", "?")
        return f"Model analysis: {slug}"
    if report_type == "template_comparison":
        return f"Template comparison: {config.get('template_slug', '?')}"
    if report_type == "tool_analysis":
        return f"Tool analysis: {config.get('tool_name') or 'all tools'}"
    if report_type == "generation_analytics":
        return f"Generation analytics ({config.get('days', 7)}d)"
    if report_type == "comprehensive":
        return f"Comprehensive report ({config.get('days', 30)}d)"
    return "Report"


def _make_summary(report_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Compact denormalized summary for list rendering."""

    if report_type == "model_analysis":
        gen = data.get("generation", {})
        return {
            "model_id": (data.get("model") or {}).get("model_id"),
            "total_jobs": gen.get("total_jobs", 0),
            "success_rate": gen.get("success_rate", 0),
            "total_findings": data.get("total_findings", 0),
            "total_loc": (data.get("loc") or {}).get("total_loc", 0),
        }
    if report_type == "template_comparison":
        return {
            "template_slug": (data.get("template") or {}).get("slug"),
            "model_count": data.get("total_models", 0),
        }
    if report_type == "tool_analysis":
        return {
            "tool": data.get("tool_filter"),
            "total_findings": data.get("total_findings", 0),
            "tool_count": len(data.get("tools", []) or []),
        }
    if report_type == "generation_analytics":
        return {
            "window_days": data.get("window_days"),
            **(data.get("summary") or {}),
        }
    if report_type == "comprehensive":
        return {
            "platform": data.get("platform", {}),
        }
    return {}


def list_reports(
    *,
    user=None,
    report_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    qs = Report.objects.all()
    if user and user.is_authenticated:
        qs = qs.filter(created_by=user)
    if report_type:
        qs = qs.filter(report_type=report_type)
    if status:
        qs = qs.filter(status=status)
    total = qs.count()
    return qs.order_by("-created_at")[offset : offset + limit], total
