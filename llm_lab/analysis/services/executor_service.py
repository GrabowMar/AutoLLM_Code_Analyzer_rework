"""Manages parallel analyzer execution via thread pool."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from typing import TYPE_CHECKING
from typing import Any

from django.utils import timezone

from llm_lab.analysis.models import AnalysisResult

if TYPE_CHECKING:
    from llm_lab.analysis.services.base import AnalyzerOutput
    from llm_lab.analysis.services.base import BaseAnalyzer
    from llm_lab.analysis.services.result_service import ResultService

logger = logging.getLogger(__name__)


class ExecutorService:
    """Manages parallel analyzer execution via thread pool."""

    MAX_WORKERS = 4

    def __init__(self, result_service: ResultService) -> None:
        self.result_service = result_service

    def run_all(
        self,
        runnable: list[
            tuple[
                AnalysisResult,
                BaseAnalyzer,
                dict[str, Any] | None,
            ]
        ],
        code: dict[str, str],
    ) -> None:
        """Run all analyzers in parallel using ThreadPoolExecutor."""
        if not runnable:
            return
        workers = min(self.MAX_WORKERS, len(runnable))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    self.run_single,
                    result_obj,
                    analyzer,
                    code,
                    config,
                ): result_obj
                for result_obj, analyzer, config in runnable
            }
            for future in as_completed(futures):
                result_obj = futures[future]
                exc = future.exception()
                if exc:
                    logger.error(
                        "Unhandled error running analyzer %s: %s",
                        result_obj.analyzer_name,
                        exc,
                    )
                    self.result_service.mark_result_failed(
                        result_obj,
                        str(exc),
                    )

    def run_single(
        self,
        result: AnalysisResult,
        analyzer: BaseAnalyzer,
        code: dict[str, str],
        config: dict[str, Any] | None,
    ) -> None:
        """Run one analyzer and persist output via result_service."""
        result.status = AnalysisResult.Status.RUNNING
        result.started_at = timezone.now()
        result.save(update_fields=["status", "started_at"])

        try:
            output: AnalyzerOutput = analyzer.analyze(code, config)
        except Exception:
            logger.exception(
                "Analyzer %s raised an exception",
                analyzer.name,
            )
            self.result_service.mark_result_failed(
                result,
                f"Analyzer '{analyzer.name}' raised an unexpected error.",
            )
            return

        self.result_service.save_analyzer_output(result, output, analyzer)
