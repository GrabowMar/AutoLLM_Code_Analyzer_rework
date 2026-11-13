"""Export a seeded, stratified sample of apps for manual review.

Usage:
    python manage.py export_review_sample --out sample.zip [--study <uuid>] [--size 30]
        [--per-stratum 3] [--stratify model,task] [--seed 0] [--no-blind]

Without ``--study`` the population is every usable sample run (all users).
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.export.services.review_sample import build_review_sample
from backend.export.services.review_sample import review_population


class Command(BaseCommand):
    help = "Write a review-sample ZIP (code, specs, blank label sheets, sealed key)."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True)
        parser.add_argument("--study", default=None, help="Study id: its current runs (main stage)")
        parser.add_argument("--size", type=int, default=30)
        parser.add_argument("--per-stratum", type=int, default=None)
        parser.add_argument("--stratify", default="model", help="'', model, task or model,task")
        parser.add_argument("--seed", type=int, default=0)
        parser.add_argument("--no-blind", action="store_true", help="Name folders by run id, not S001…")

    def handle(self, *args, **opts):
        try:
            # No user: the staff view (every user's runs), as on the shell.
            runs = review_population(None, study_id=opts["study"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        try:
            data, manifest = build_review_sample(
                runs,
                size=opts["size"],
                per_stratum=opts["per_stratum"],
                stratify=tuple(k for k in opts["stratify"].split(",") if k),
                seed=opts["seed"],
                blind=not opts["no_blind"],
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
                f"{c['dependency_issues']} dependency advisories, {c['runtime_issues']} runtime issues, "
                f"{c['requirements']} requirements to label -> {out}",
            ),
        )
