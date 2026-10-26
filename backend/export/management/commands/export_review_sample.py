"""Export a seeded, stratified sample of apps for manual review.

Usage:
    python manage.py export_review_sample --out sample.zip [--experiment <uuid>] [--size 30]
        [--per-stratum 3] [--stratify model,template] [--seed 0] [--no-blind]
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.export.services.review_sample import build_review_sample
from backend.statistics.services.study import study_jobs


class Command(BaseCommand):
    help = "Write a review-sample ZIP (code, specs, blank label sheets, sealed key)."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True)
        parser.add_argument("--experiment", default=None)
        parser.add_argument("--batch", default=None)
        parser.add_argument("--size", type=int, default=30)
        parser.add_argument("--per-stratum", type=int, default=None)
        parser.add_argument("--stratify", default="model", help="'', model, template or model,template")
        parser.add_argument("--seed", type=int, default=0)
        parser.add_argument("--no-blind", action="store_true", help="Name folders by job id, not S001…")
        parser.add_argument("--plan-hash", default=None, help="Analysis plan giving the automated verdicts")

    def handle(self, *args, **opts):
        jobs = study_jobs(experiment_id=opts["experiment"], batch_id=opts["batch"])
        try:
            data, manifest = build_review_sample(
                jobs,
                size=opts["size"],
                per_stratum=opts["per_stratum"],
                stratify=tuple(k for k in opts["stratify"].split(",") if k),
                seed=opts["seed"],
                blind=not opts["no_blind"],
                plan_hash=opts["plan_hash"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        out = Path(opts["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        c = manifest["counts"]
        self.stdout.write(
            self.style.SUCCESS(
                f"Sampled {manifest['size']} of {manifest['population']} apps ({manifest['method']}): "
                f"{c['ai_findings']} AI findings, {c['static_issues']} static issues, "
                f"{c['requirements']} requirements to label -> {out}",
            ),
        )
