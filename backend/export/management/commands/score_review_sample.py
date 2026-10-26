"""Score a filled-in review sample against the automated verdicts.

Usage:
    python manage.py score_review_sample path/to/sample.zip|folder [--json scores.json]
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.export.services.review_sample import score_review_labels


class Command(BaseCommand):
    help = "Precision of AI/static findings, kappa and Spearman vs conformance, from labelled sheets."

    def add_arguments(self, parser):
        parser.add_argument("source")
        parser.add_argument("--json", default=None, help="Also write the scores to this file")

    def handle(self, *args, **opts):
        source = Path(opts["source"])
        if not source.exists():
            msg = f"{source} does not exist"
            raise CommandError(msg)
        scores = score_review_labels(source)
        text = json.dumps(scores, indent=2)
        if opts["json"]:
            Path(opts["json"]).write_text(text, encoding="utf-8")
        self.stdout.write(text)
