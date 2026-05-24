"""Handles result persistence, finding storage, and aggregation."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from django.db import transaction
from django.utils import timezone

from llm_lab.analysis.models import AnalysisResult
from llm_lab.analysis.models import AnalysisTask
from llm_lab.analysis.models import Finding
from llm_lab.analysis.services.base import AnalyzerOutput
from llm_lab.analysis.services.base import AnalyzerRegistry
from llm_lab.analysis.services.base import BaseAnalyzer
from llm_lab.analysis.services.base import FindingData
from llm_lab.realtime import events as realtime

logger = logging.getLogger(__name__)


def _compute_duration(
    started_at: Any,
    completed_at: Any,
) -> float:
    return (completed_at - started_at).total_seconds()


_RESULT_SAVE_FIELDS = [
    "status",
    "error_message",
    "completed_at",
    "duration_seconds",
]


class ResultService:
    """Handles result persistence, finding storage, and aggregation."""

    def create_results(
        self,
        task: AnalysisTask,
        analyzer_names: list[str],
        settings: dict[str, Any],
    ) -> list[tuple[AnalysisResult, BaseAnalyzer, dict[str, Any] | None]]:
        """Create AnalysisResult records for each analyzer.

        Returns list of (result, analyzer, config) tuples for runnable analyzers.
        """
        runnable: list[
            tuple[
                AnalysisResult,
                BaseAnalyzer,
                dict[str, Any] | None,
            ]
        ] = []
        for name in analyzer_names:
            analyzer = AnalyzerRegistry.get_instance(name)
            result_obj = AnalysisResult.objects.create(
                task=task,
                analyzer_name=name,
                analyzer_type=(analyzer.analyzer_type if analyzer else ""),
                status=AnalysisResult.Status.PENDING,
            )
            if analyzer is None:
                logger.warning(
                    "Analyzer %r not found, skipping",
                    name,
                )
                result_obj.status = AnalysisResult.Status.SKIPPED
                result_obj.error_message = f"Analyzer '{name}' is not registered."
                result_obj.save(
                    update_fields=["status", "error_message"],
                )
            else:
                runnable.append(
                    (result_obj, analyzer, settings.get(name)),
                )
        return runnable

    def save_findings(
        self,
        result: AnalysisResult,
        findings: list[FindingData],
    ) -> int:
        """Bulk-create Finding records. Returns count created."""
        if not findings:
            return 0

        objects = [
            Finding(
                result=result,
                severity=f.severity,
                category=f.category,
                confidence=f.confidence,
                title=f.title,
                description=f.description,
                suggestion=f.suggestion,
                file_path=f.file_path,
                line_number=f.line_number,
                column_number=f.column_number,
                code_snippet=f.code_snippet,
                rule_id=f.rule_id,
                tool_specific_data=f.tool_specific_data,
            )
            for f in findings
        ]
        with transaction.atomic():
            Finding.objects.bulk_create(objects)
        return len(objects)

    def save_analyzer_output(
        self,
        result: AnalysisResult,
        output: AnalyzerOutput,
        analyzer: BaseAnalyzer,
    ) -> None:
        """Save a single analyzer's output to its AnalysisResult."""
        if output.has_error:
            result.status = AnalysisResult.Status.FAILED
            result.error_message = output.error or ""
        else:
            result.status = AnalysisResult.Status.COMPLETED
            count = self.save_findings(result, output.findings)
            logger.info(
                "Analyzer %s produced %d findings",
                analyzer.name,
                count,
            )

        result.raw_output = output.raw_output
        result.summary = output.summary
        result.completed_at = timezone.now()
        result.duration_seconds = _compute_duration(
            result.started_at,
            result.completed_at,
        )
        result.save(
            update_fields=[
                *_RESULT_SAVE_FIELDS,
                "raw_output",
                "summary",
            ],
        )
        # Publish per-result update for SSE clients
        finding_count = len(output.findings)
        severity_counts: dict[str, int] = Counter(f.severity for f in output.findings)
        realtime.publish(
            f"analysis:{result.task_id}",
            {
                "type": "result",
                "analyzer": result.analyzer_name,
                "status": result.status,
                "finding_count": finding_count,
                "severity_counts": dict(severity_counts),
                "updated_at": result.completed_at.isoformat() if result.completed_at else None,
            },
        )

    def mark_result_failed(
        self,
        result: AnalysisResult,
        error_msg: str,
    ) -> None:
        """Mark a result as failed with duration calculation."""
        result.status = AnalysisResult.Status.FAILED
        result.error_message = error_msg
        result.completed_at = timezone.now()
        if result.started_at:
            result.duration_seconds = _compute_duration(
                result.started_at,
                result.completed_at,
            )
        result.save(
            update_fields=_RESULT_SAVE_FIELDS,
        )

    def aggregate_results(
        self,
        task: AnalysisTask,
    ) -> dict[str, Any]:
        """Compute aggregate summary from all results."""
        results = task.results.all()
        active_findings = Finding.objects.filter(result__task=task, suppressed=False)
        suppressed_count = Finding.objects.filter(result__task=task, suppressed=True).count()

        severity_counts = Counter(
            active_findings.values_list("severity", flat=True),
        )
        category_counts = Counter(
            active_findings.values_list("category", flat=True),
        )

        by_severity: dict[str, int] = {s.value: severity_counts.get(s.value, 0) for s in Finding.Severity}
        by_category: dict[str, int] = {c.value: category_counts.get(c.value, 0) for c in Finding.Category}

        completed = 0
        failed = 0
        by_analyzer: dict[str, dict[str, Any]] = {}
        for r in results:
            finding_count = r.findings.filter(suppressed=False).count()
            by_analyzer[r.analyzer_name] = {
                "status": r.status,
                "findings": finding_count,
                "duration": r.duration_seconds,
            }
            if r.status == AnalysisResult.Status.COMPLETED:
                completed += 1
            elif r.status == AnalysisResult.Status.FAILED:
                failed += 1

        return {
            "total_findings": active_findings.count(),
            "suppressed_count": suppressed_count,
            "by_severity": by_severity,
            "by_category": by_category,
            "by_analyzer": by_analyzer,
            "analyzers_completed": completed,
            "analyzers_failed": failed,
            "analyzers_total": results.count(),
        }

    def finalize_task(self, task: AnalysisTask) -> None:
        """Set final task status and summary."""
        task.results_summary = self.aggregate_results(task)

        completed_count = task.results.filter(
            status=AnalysisResult.Status.COMPLETED,
        ).count()
        failed_count = task.results.filter(
            status=AnalysisResult.Status.FAILED,
        ).count()
        total = task.results.count()

        if completed_count == total:
            task.status = AnalysisTask.Status.COMPLETED
        elif failed_count == total:
            task.status = AnalysisTask.Status.FAILED
        else:
            task.status = AnalysisTask.Status.PARTIAL

        # Evaluate thresholds against active (non-suppressed) finding counts
        thresholds = task.configuration.get("thresholds", {})
        if thresholds:
            by_severity = task.results_summary.get("by_severity", {})
            exceeded = any(by_severity.get(sev, 0) > limit for sev, limit in thresholds.items() if limit is not None)
            task.threshold_status = (
                AnalysisTask.ThresholdStatus.EXCEEDED if exceeded else AnalysisTask.ThresholdStatus.PASSED
            )
        else:
            task.threshold_status = AnalysisTask.ThresholdStatus.NOT_CONFIGURED

        task.completed_at = timezone.now()
        if task.started_at:
            task.duration_seconds = _compute_duration(
                task.started_at,
                task.completed_at,
            )
        task.save(
            update_fields=[
                "status",
                "threshold_status",
                "results_summary",
                "completed_at",
                "duration_seconds",
            ],
        )
        logger.info(
            "Analysis task %s finished: %s (%d/%d completed)",
            task.id,
            task.status,
            completed_count,
            total,
        )
        realtime.publish(
            f"analysis:{task.id}",
            {
                "type": "status",
                "status": task.status,
                "threshold_status": task.threshold_status,
                "updated_at": task.completed_at.isoformat() if task.completed_at else None,
            },
        )
