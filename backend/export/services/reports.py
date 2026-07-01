"""Report exporters."""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from django.db.models import QuerySet


_REPORT_HEADERS = ["id", "title", "type", "status", "created"]


def reports_csv(queryset: QuerySet[Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_REPORT_HEADERS)
    for report in queryset:
        writer.writerow(
            [
                str(report.id),
                report.title,
                report.report_type,
                report.status,
                report.created_at.isoformat(),
            ],
        )
    return buf.getvalue()


def reports_json(queryset: QuerySet[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(report.id),
            "title": report.title,
            "type": report.report_type,
            "status": report.status,
            "created": report.created_at.isoformat(),
        }
        for report in queryset
    ]
