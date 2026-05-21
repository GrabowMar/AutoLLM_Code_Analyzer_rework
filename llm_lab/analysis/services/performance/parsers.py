"""Parsers for Lighthouse JSON output."""

from __future__ import annotations

from typing import Any

from llm_lab.analysis.services.base import AnalyzerOutput
from llm_lab.analysis.services.base import FindingData

from ._common import _PASS_THRESHOLD
from ._common import LIGHTHOUSE_CATEGORY_MAP
from ._common import _average_score_to_grade
from ._common import _score_to_severity


def map_audit_to_category(
    audit_id: str,
    report: dict[str, Any],
) -> str:
    categories = report.get("categories", {})
    for cat_key, cat_data in categories.items():
        audit_refs = cat_data.get("auditRefs", [])
        for ref in audit_refs:
            if ref.get("id") == audit_id:
                return LIGHTHOUSE_CATEGORY_MAP.get(
                    cat_key,
                    "performance",
                )
    return "performance"


def parse_lighthouse_report(
    report: dict[str, Any],
) -> AnalyzerOutput:
    findings: list[FindingData] = []
    categories = report.get("categories", {})
    audits = report.get("audits", {})

    category_scores: dict[str, float] = {}
    for cat_key, cat_data in categories.items():
        score = cat_data.get("score")
        if score is not None:
            label = cat_data.get("title", cat_key)
            category_scores[label] = score

    passed = 0
    failed = 0

    for audit_id, audit_data in audits.items():
        score = audit_data.get("score")
        if score is None:
            continue
        if score >= _PASS_THRESHOLD:
            passed += 1
            continue

        failed += 1
        cat = map_audit_to_category(
            audit_id,
            report,
        )
        findings.append(
            FindingData(
                severity=_score_to_severity(score),
                category=cat,
                title=audit_data.get("title", audit_id),
                description=audit_data.get(
                    "description",
                    "",
                ),
                suggestion=audit_data.get(
                    "displayValue",
                    "",
                ),
                rule_id=audit_id,
                confidence="high",
                tool_specific_data={
                    "score": score,
                    "scoreDisplayMode": audit_data.get(
                        "scoreDisplayMode",
                    ),
                    "numericValue": audit_data.get(
                        "numericValue",
                    ),
                },
            ),
        )

    scores = list(category_scores.values())
    avg_score = sum(scores) / len(scores) if scores else 0.0

    summary: dict[str, Any] = {
        "category_scores": category_scores,
        "audits_passed": passed,
        "audits_failed": failed,
        "average_score": round(avg_score, 2),
        "grade": _average_score_to_grade(avg_score),
    }

    return AnalyzerOutput(
        findings=findings,
        summary=summary,
        raw_output=report,
    )
