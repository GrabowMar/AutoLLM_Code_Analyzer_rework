"""Findings exporters: CSV, JSON, and SARIF 2.1.0."""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from django.db.models import QuerySet


_SARIF_LEVEL: dict[str, str] = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}

FINDING_HEADERS = [
    "id",
    "task_id",
    "analyzer",
    "severity",
    "file_path",
    "line",
    "rule_id",
    "message",
    "cwe",
    "created_at",
]


def findings_csv(queryset: QuerySet[Any]) -> str:
    """Return CSV string of findings."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(FINDING_HEADERS)
    for f in queryset.select_related("result", "result__run"):
        writer.writerow(
            [
                str(f.id),
                str(f.result.run_id),
                f.result.tool_slug,
                f.severity,
                f.file_path,
                f.line_number or "",
                f.rule_id,
                f.title,
                f.tool_specific_data.get("cwe", "") if f.tool_specific_data else "",
                f.created_at.isoformat(),
            ],
        )
    return buf.getvalue()


def findings_json(queryset: QuerySet[Any]) -> list[dict[str, Any]]:
    """Return list of finding dicts."""
    return [
        {
            "id": f.id,
            "task_id": str(f.result.run_id),
            "analyzer": f.result.tool_slug,
            "severity": f.severity,
            "file_path": f.file_path,
            "line": f.line_number,
            "rule_id": f.rule_id,
            "message": f.title,
            "cwe": f.tool_specific_data.get("cwe", "") if f.tool_specific_data else "",
            "created_at": f.created_at.isoformat(),
        }
        for f in queryset.select_related("result", "result__run")
    ]


def findings_sarif(queryset: QuerySet[Any]) -> dict[str, Any]:
    """Return SARIF 2.1.0 dict grouped by analyzer name."""
    by_analyzer: dict[str, list[Any]] = {}
    for f in queryset.select_related("result", "result__run"):
        key = f.result.tool_slug
        by_analyzer.setdefault(key, []).append(f)

    runs = []
    for analyzer_name, findings in by_analyzer.items():
        results_list = []
        for f in findings:
            result_entry: dict[str, Any] = {
                "ruleId": f.rule_id or "unknown",
                "level": _SARIF_LEVEL.get(f.severity, "warning"),
                "message": {"text": f.title},
                "locations": [],
            }
            if f.file_path:
                location: dict[str, Any] = {
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.file_path},
                    },
                }
                if f.line_number:
                    location["physicalLocation"]["region"] = {
                        "startLine": f.line_number,
                    }
                result_entry["locations"].append(location)
            results_list.append(result_entry)

        runs.append(
            {
                "tool": {
                    "driver": {
                        "name": analyzer_name,
                        "version": "1.0.0",
                        "informationUri": "",
                    },
                },
                "results": results_list,
            },
        )

    return {
        "version": "2.1.0",
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "runs": runs,
    }
