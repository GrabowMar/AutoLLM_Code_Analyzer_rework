"""Report orchestration: create, dispatch background generation, list."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.db import models
from django.db import transaction
from django.utils import timezone

from backend.common.threading import dispatch_in_thread
from backend.reports.models import Report

from .generators import GENERATORS
from .generators import USER_SCOPED

logger = logging.getLogger(__name__)

# A report's generation thread dies with its process (autoreload, restart, a
# lost dispatch); nothing would ever finish such a row, and the detail page
# would poll it forever. Rows still unfinished this long after creation are
# marked failed (``reap_stale_reports``). Generation normally takes seconds.
STALE_PENDING_AFTER = timedelta(minutes=10)
STALE_GENERATING_AFTER = timedelta(hours=1)
INTERRUPTED_MESSAGE = (
    "Interrupted: generation did not finish (the worker process restarted or the thread was lost). "
    "Generate the report again."
)


def create_and_dispatch(
    *,
    report_type: str,
    config: dict[str, Any],
    title: str | None = None,
    description: str = "",
    user=None,
    expires_in_days: int | None = 30,
) -> Report:
    """Create a Report row, then run the generator in a daemon thread once the row is committed.

    The thread reads the row on its own connection: started before the
    caller's transaction (the request's, under ATOMIC_REQUESTS) commits, it
    would not find it and the report would stay pending. Outside a
    transaction the callback runs at once.
    """

    report = _create(
        report_type=report_type,
        config=config,
        title=title,
        description=description,
        user=user,
        expires_in_days=expires_in_days,
    )
    report_id = report.id
    transaction.on_commit(lambda: _dispatch(report_id))
    return report


def create_and_run(
    *,
    report_type: str,
    config: dict[str, Any],
    title: str | None = None,
    description: str = "",
    user=None,
    expires_in_days: int | None = 30,
) -> Report:
    """Create a Report row and generate it synchronously in the caller's thread (study post-runs).

    A generator failure is recorded on the row (``failed``), never raised.
    """
    report = _create(
        report_type=report_type,
        config=config,
        title=title,
        description=description,
        user=user,
        expires_in_days=expires_in_days,
    )
    report.mark_generating()
    try:
        _generate(report)
    except Exception as exc:
        logger.exception("Report generation failed for %s", report.id)
        report.mark_failed(str(exc))
    report.refresh_from_db()
    return report


def _create(
    *,
    report_type: str,
    config: dict[str, Any],
    title: str | None,
    description: str,
    user,
    expires_in_days: int | None,
) -> Report:
    if report_type not in GENERATORS:
        msg = f"Unknown report_type: {report_type}"
        raise ValueError(msg)
    _validate_config(report_type, config, user)

    final_title = title or _default_title(report_type, config)
    expires_at = timezone.now() + timedelta(days=int(expires_in_days)) if expires_in_days else None

    return Report.objects.create(
        report_type=report_type,
        title=final_title,
        description=description,
        config=config,
        created_by=user if (user and user.is_authenticated) else None,
        expires_at=expires_at,
    )


def _dispatch(report_id) -> None:
    dispatch_in_thread(_run_generation, report_id, name=f"report-{report_id}")


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


def reap_stale_reports(now=None) -> int:
    """Mark reports stuck pending/generating past their deadline as failed; returns how many."""
    now = now or timezone.now()
    stale = Report.objects.filter(
        models.Q(status=Report.Status.PENDING, created_at__lt=now - STALE_PENDING_AFTER)
        | models.Q(status=Report.Status.GENERATING, created_at__lt=now - STALE_GENERATING_AFTER),
    )
    return stale.update(status=Report.Status.FAILED, error_message=INTERRUPTED_MESSAGE, completed_at=now)


def _generate(report: Report) -> None:
    generator = GENERATORS[report.report_type]
    if report.report_type in USER_SCOPED:
        # Owner-scoped generators read only what the report's creator may see (staff: everything).
        data = generator(report.config or {}, user=report.created_by)
    else:
        data = generator(report.config or {})
    report.report_data = data
    report.summary = _make_summary(report.report_type, data)
    report.save(update_fields=["report_data", "summary"])
    report.mark_completed()


def _validate_config(report_type: str, config: dict[str, Any], user=None) -> None:
    """Synchronously check required config keys (and ownership) before background dispatch."""

    required: dict[str, list[str]] = {
        "model_analysis": ["model_id"],
        "template_comparison": ["task_slug"],
        "experiment_report": ["study_id"],
    }
    for key in required.get(report_type, []):
        if not (config or {}).get(key):
            msg = f"Missing required config key for {report_type}: {key}"
            raise ValueError(msg)
    if report_type in ("experiment_report", "study_report") and (config or {}).get("study_id"):
        from .generators import owned_study

        # Raises ValueError("Study not found") for another user's study.
        owned_study(config["study_id"], user)
    if report_type == "study_report" and (config or {}).get("ranking_criteria"):
        from backend.statistics.services.mcdm import criteria_from_config

        criteria_from_config(config["ranking_criteria"])
    if report_type == "tool_analysis" and (config or {}).get("tool_name"):
        from backend.analysis.models import AnalyzerTool
        from backend.analysis.models import ToolResult

        # A typo would otherwise produce a completed report of zero findings.
        # Tools removed from the registry can still be reported on by their results.
        name = config["tool_name"]
        if not (AnalyzerTool.objects.filter(slug=name).exists() or ToolResult.objects.filter(tool_slug=name).exists()):
            msg = f"Unknown tool: {name}"
            raise ValueError(msg)


def _default_title(report_type: str, config: dict[str, Any]) -> str:  # noqa: PLR0911 - one branch per type
    if report_type == "model_analysis":
        slug = config.get("model_id") or config.get("model_slug", "?")
        return f"Model analysis: {slug}"
    if report_type == "template_comparison":
        return f"Task comparison: {config.get('task_slug', '?')}"
    if report_type == "tool_analysis":
        return f"Tool analysis: {config.get('tool_name') or 'all tools'}"
    if report_type == "generation_analytics":
        return f"Generation analytics ({config.get('days', 7)}d)"
    if report_type == "comprehensive":
        return f"Comprehensive report ({config.get('days', 30)}d)"
    if report_type == "experiment_report":
        return f"Study conditions: {config.get('study_id', '?')}"
    if report_type == "study_report":
        scope = config.get("study_id") or ("selected runs" if config.get("run_ids") else "all my apps")
        return f"Study report: {scope}"
    return "Report"


def _make_summary(report_type: str, data: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0911 - one branch per type
    """Compact denormalized summary for list rendering."""

    if report_type == "model_analysis":
        gen = data.get("generation", {})
        return {
            "model_id": (data.get("model") or {}).get("model_id"),
            "total_runs": gen.get("total_runs", 0),
            # None: nothing finished / nothing analyzed (unmeasured is not zero).
            "success_rate": gen.get("success_rate"),
            "runs_analyzed": data.get("runs_analyzed"),
            "total_findings": data.get("total_findings"),
            # None: no run's tree was collected (unknown, not zero).
            "total_loc": (data.get("loc") or {}).get("total_loc"),
        }
    if report_type == "template_comparison":
        return {
            "task_slug": (data.get("task") or {}).get("slug"),
            "model_count": data.get("total_models", 0),
        }
    if report_type == "tool_analysis":
        return {
            "tool": data.get("tool_filter"),
            "total_findings": data.get("total_findings"),
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
    if report_type == "experiment_report":
        results = data.get("results") or {}
        board = [r for r in results.get("leaderboard") or [] if r.get("pass_rate_mean") is not None]
        best = max((r["pass_rate_mean"] for r in board), default=None)
        # Conditions sharing the top mean (as the Results tab's findings decide a
        # tie): with more than one there is no leader, and with no graded
        # condition there is nothing to lead.
        top = [r for r in board if abs(r["pass_rate_mean"] - best) < 1e-9] if best is not None else []
        return {
            "study_id": (data.get("study") or {}).get("id"),
            "condition_count": data.get("total_conditions", 0),
            "leader": top[0].get("label") if len(top) == 1 else None,
            "leader_pass_rate": best,
            "tied_leaders": len(top) if len(top) > 1 else 0,
            "interim": results.get("interim"),
        }
    if report_type == "study_report":
        funnel = (data.get("funnel") or {}).get("overall") or {}
        ranking = (data.get("ranking") or {}).get("ranking") or []
        # TOPSIS ranks are competition ranks: every row ranked 1 shares the top,
        # and ranking[0] of a tie is only the alphabetically first. A tie names
        # no leader (as the study conditions report's summary decides it).
        if ranking and all("topsis_rank" in r for r in ranking):
            top = [r.get("model") for r in ranking if r.get("topsis_rank") == 1]
        else:
            top = [ranking[0].get("model")] if ranking else []
        return {
            "runs": funnel.get("runs", 0),
            "models": len((data.get("protocol") or {}).get("models") or []),
            "deploy_rate": (funnel.get("deploy_rate") or {}).get("rate"),
            "functional_rate": (funnel.get("functional_rate") or {}).get("rate"),
            # None: no app was measured (unmeasured is not zero).
            "unique_issues": ((data.get("issues") or {}).get("totals") or {}).get("unique_issues"),
            "leader": top[0] if len(top) == 1 else None,
            "tied_leaders": len(top) if len(top) > 1 else 0,
            "warnings": sum(1 for v in data.get("validity") or [] if v.get("level") == "warning"),
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
    reap_stale_reports()
    qs = Report.objects.all()
    if user and user.is_authenticated:
        qs = qs.filter(created_by=user)
    if report_type:
        qs = qs.filter(report_type=report_type)
    if status:
        qs = qs.filter(status=status)
    total = qs.count()
    return qs.order_by("-created_at")[offset : offset + limit], total
